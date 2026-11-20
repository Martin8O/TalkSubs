"""The job queue: one worker process at a time, state kept on the event loop, persisted on every change.

Threads only read the worker's pipes and wait for it to exit; they hand every line and the exit to the
event loop, which owns all job state (no locks).
"""

from __future__ import annotations

import asyncio
import collections
import contextlib
import json
import logging
import os
import shutil
import signal
import subprocess
import threading
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, TypeAdapter, ValidationError

from parakeet_stt import (
    __version__,
    blobserve,
    cardpack,
    engines,
    media,
    models,
    presets,
    preview,
    project,
    sound,
    speed,
    subtitles,
)
from parakeet_stt import checkpoint as ckpt
from parakeet_stt import glossary as gl_mod
from parakeet_stt import vault as vault_mod
from parakeet_stt.cancel import CancelToken
from parakeet_stt.glossary import Glossary
from parakeet_stt.rules import Violation
from parakeet_stt.server import editing, links, logscrub, uploads
from parakeet_stt.server.fetches import Fetches
from parakeet_stt.server.hub import Hub
from parakeet_stt.server.messages import (
    FINISHED,
    CardPackEvent,
    ExportRequest,
    ExportResult,
    FileSource,
    Job,
    JobEvent,
    JobPreview,
    JobProgress,
    LinkSource,
    MachineEvent,
    MachineInfo,
    ModelsEvent,
    ModelsInfo,
    PausedEvent,
    PausedKind,
    PreviewEvent,
    ProgressEvent,
    RemovedEvent,
    SaveTrackRequest,
    Status,
    UploadSource,
)
from parakeet_stt.server.modelfetch import ModelFetch
from parakeet_stt.server.previews import Previews
from parakeet_stt.server.store import Store
from parakeet_stt.server.worker import (
    BenchmarkDoneMsg,
    BenchmarkSpec,
    CancelledMsg,
    DiskFullMsg,
    DoneMsg,
    ErrorMsg,
    GpuBusyMsg,
    GpuMemoryMsg,
    Hello,
    JobSpec,
    ProgressMsg,
    WorkerMessage,
)
from parakeet_stt.transcript import Cue, Word
from parakeet_stt.vault import Role, Vault, VaultError

log = logging.getLogger("parakeet_stt.server")

MAX_ATTEMPTS = 2  # a job interrupted (crash, power cut) after this many starts is failed instead of run again
MEMORY_TRIES = 3  # a job that finds the GPU memory full this many times in a row is failed; the queue goes on
MEMORY_RETRY_S = 120.0  # how long the queue waits after the GPU memory was full
ETA_MIN_FRACTION = 0.02
ETA_MIN_ELAPSED_S = 10.0
STDERR_TAIL = 20
_messages: TypeAdapter[WorkerMessage] = TypeAdapter(WorkerMessage)
MEMORY_GAVE_UP = (
    "The graphics card ran out of memory {tries} times in a row. Close programs that use it "
    "(games, video or 3D programs) and add the file again."
)


class JobError(Exception):
    """A request about a job cannot be done; the message is for the user."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


class ExportExists(JobError):
    """Some subtitle files are already there; the user decides whether to replace them."""

    def __init__(self, existing: list[str]) -> None:
        n = len(existing)
        super().__init__(f"{n} subtitle file{'s' if n != 1 else ''} with these names already exist.", 409)
        self.existing = existing


@dataclass
class _Running:
    job_id: str  # "" for the speed test
    proc: subprocess.Popen[str]
    kind: Literal["job", "benchmark"] = "job"
    pid: int | None = None  # the worker's own process (the venv launcher may sit in between)
    outcome: BaseModel | None = None
    cancel_requested: bool = False
    requeue: bool = False  # cancelled because the app is closing: run it again next time
    eta_base: tuple[float, float] | None = None  # (time, fraction) when recognition began: the ETA's rate
    stderr_tail: collections.deque[str] = field(default_factory=lambda: collections.deque(maxlen=STDERR_TAIL))
    exited: asyncio.Event = field(default_factory=asyncio.Event)


def _hide_in_log(*jobs: Job) -> None:
    """The user's names and paths; an upload's own copy (`uploads/<id>/...`) is ours and says nothing."""
    texts: list[str | None] = []
    for job in jobs:
        texts.append(job.name)
        if isinstance(job.source, UploadSource):
            texts.append(job.source.original_name)
        elif isinstance(job.source, LinkSource):
            texts.append(job.source.shown)
        else:
            texts.append(job.source.path)
    logscrub.register(*texts)


def _own_folder(source: object) -> str | None:
    """The upload folder a job's source keeps in the app (a dropped copy, a link's sound), if any."""
    return source.upload_id if isinstance(source, (UploadSource, LinkSource)) else None


def gpu_in_use() -> str | None:
    """Why the GPU cannot be had right now (a CLI run holds it), or None. The worker checks again itself."""
    from parakeet_stt import gpu

    lock = gpu.GpuLock()
    try:
        lock.acquire()
    except gpu.GpuBusy as exc:
        return str(exc)
    lock.release()
    return None


def look_at_machine(worker_cmd: Sequence[str]) -> dict[str, Any] | None:
    """What this PC runs the model on (`machine.detect`), asked in a short-lived process: starting the NVIDIA
    driver in the long-running server could keep a laptop's graphics card awake. None if it cannot be told."""
    cmd = [*worker_cmd[:-1], "machine"]  # the worker's own interpreter and flags
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=15, check=False,
                             creationflags=subprocess.CREATE_NO_WINDOW)  # fmt: skip
        lines = out.stdout.strip().splitlines()
        raw = json.loads(lines[-1]) if out.returncode == 0 and lines else None
    except (OSError, subprocess.TimeoutExpired, ValueError):
        raw = None
    if not isinstance(raw, dict):
        log.warning("could not tell what this PC runs the model on")
        return None
    return raw


def default_worker_cmd() -> list[str]:
    import sys

    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe":  # the worker needs real pipes and a (hidden) console for ffmpeg
        exe = exe.with_name("python.exe")
    return [str(exe), "-X", "utf8", "-m", "parakeet_stt", "worker"]


class Runner:
    def __init__(
        self,
        data_dir: Path,
        hub: Hub,
        vault: Vault,
        worker_cmd: Sequence[str] | None = None,
        *,
        kill_after_s: float = 10.0,
        busy_retry_s: float = 5.0,
        memory_retry_s: float = MEMORY_RETRY_S,
        clock: Callable[[], float] = time.time,
        gpu_check: Callable[[], str | None] | None = None,
        allow_local_http_links: bool = False,  # tests only: links to 127.0.0.1 over plain http
        own_glossary: Callable[[], str] | None = None,  # the user's own list as typed (default: none)
        # every subtitle type (default: the built-in ones), and the user's own types a new job makes subtitles for
        preset_book: Callable[[], Mapping[str, presets.Preset]] | None = None,
        job_types: Callable[[], list[presets.Preset]] | None = None,
        # what this PC runs the model on (`machine.detect` as a dict); default: asked in a short-lived process,
        # unless a stand-in worker is used (tests), which has no machine to measure
        machine_check: Callable[[], dict[str, Any] | None] | None = None,
        # are the speech model's files here, and how to download them; default: the pinned files in the model
        # folder, unless a stand-in worker is used (tests), which needs no model
        models_present: Callable[[], bool] | None = None,
        models_fetch: Callable[[models.ProgressFn, CancelToken], None] | None = None,
        card_present: Callable[[], bool] | None = None,  # default: cardpack.present (a stand-in worker: present)
        card_fetch: Callable[[models.ProgressFn, CancelToken], None] | None = None,  # default: cardpack.fetch
    ) -> None:
        self.own_glossary = own_glossary or (lambda: "")
        self.preset_book = preset_book or presets.registry
        self.job_types = job_types or list
        self.allow_local_http_links = allow_local_http_links
        self.data_dir = data_dir
        self.hub = hub
        self.vault = vault
        self.worker_cmd = list(worker_cmd or default_worker_cmd())
        self.kill_after_s = kill_after_s
        self.busy_retry_s = busy_retry_s
        self.memory_retry_s = memory_retry_s
        self.clock = clock
        self.gpu_check = gpu_check or gpu_in_use
        if machine_check is None:
            cmd = self.worker_cmd
            machine_check = (lambda: look_at_machine(cmd)) if worker_cmd is None else (lambda: None)
        self.machine_check = machine_check
        if models_present is None and worker_cmd is not None:
            models_present = lambda: True  # noqa: E731
        self.models = ModelFetch(self._publish_models, self._kick, models_present, models_fetch)
        if card_present is None:
            card_present = cardpack.present if worker_cmd is None else (lambda: True)
        self.card_pack = ModelFetch(self._publish_card_pack, self._card_pack_ready, card_present,
                                    card_fetch or cardpack.fetch,
                                    total_bytes=cardpack.total_size(), auto=False,
                                    resume=(cardpack.resumable if worker_cmd is None else None),
                                    what="the graphics-card support")  # fmt: skip
        self.machine: dict[str, Any] | None = None  # known a moment after the start
        self.speed_store = speed.SpeedStore(data_dir, vault)
        self.speeds = speed.SpeedFile()
        self._measure_asked = False  # "Measure again" was pressed: runs before the next job
        self._measured_once = False  # the speed test starts by itself at most once per app session
        self.store = Store(data_dir, vault)
        self.jobs: list[Job] = []
        self.current: _Running | None = None
        self.paused_reason: str | None = None
        self.paused_kind: PausedKind | None = None
        self._unpause_timer: asyncio.TimerHandle | None = None
        self._memory_tries: dict[str, int] = {}  # job id -> GPU memory full in a row (this app session)
        self.closing = False
        self.busy: set[str] = set()  # jobs with an export running: remove and a second export wait
        self._project_locks: dict[str, asyncio.Lock] = {}  # one read-modify-write of a project file at a time
        self._checkers: dict[tuple[str, str], editing.Checker] = {}  # (job, preset) -> rules of the open track
        self._loop: asyncio.AbstractEventLoop | None = None
        self.previews = Previews(self._job_dir_of, lambda: self.jobs, self.set_preview, vault)
        self.fetches = Fetches(self._link_of, self._fetch_progress, self._fetched, vault)
        self._tasks: set[asyncio.Task[None]] = set()

    # ---- lifecycle -------------------------------------------------------------------------------------------

    def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.jobs = self.store.load()
        _hide_in_log(*self.jobs)
        for job in self.jobs:
            if job.state != "running":
                continue
            if job.attempts >= MAX_ATTEMPTS:
                self._remove_output(job)
                self._finish(job, "failed", error=f"Interrupted {job.attempts} times; not retried again.")
            else:  # a half-written file is not trusted; the sealed checkpoint is (it is whole or absent)
                self._remove_output(job, keep_checkpoint=True)
                self._requeue(job)
            log.info("job %s was interrupted by a stop; now %s", job.id, job.state)
        self.store.save(self.jobs)
        self.fetches.start()
        for job in self.jobs:
            if job.state == "fetching":  # a stop during the download: fetch again (the link may have expired)
                self.fetches.enroll(job.id)
        known = {j.id for j in self.jobs}
        jobs_root = self.data_dir / "jobs"
        for left in jobs_root.iterdir() if jobs_root.is_dir() else ():
            # only what a removal blocked by a playing file left: a folder holding nothing but preview files
            # (and a checkpoint, worthless without its job). A folder with a transcript stays, even when the queue
            # file was reset.
            if left.is_dir() and left.name not in known and all(_sweepable(c.name) for c in left.iterdir()):
                shutil.rmtree(left, ignore_errors=True)
        keep = [f for j in self.jobs if (f := _own_folder(j.source)) is not None]
        for gone in uploads.sweep(self.data_dir, keep):
            log.info("removed the unused upload %s (a stop during its upload, or its job is gone)", gone)
        self.previews.start()
        self.models.start()
        self.card_pack.start()
        self.speeds = self.speed_store.load()
        task = self._loop.create_task(self._look_at_machine())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        self._kick()

    async def _look_at_machine(self) -> None:
        raw = await asyncio.to_thread(self.machine_check)
        if raw is None or self.closing:
            return
        if self.machine is not None and speed.key(str(raw.get("device")), raw.get("card")) != self._speed_key():
            self._measured_once = False  # a new way of running (the card's support came in): its own speed test
        self.machine = raw
        self._publish_machine()
        self._kick()

    async def shutdown(self) -> None:
        """Stop the running job (it goes back to the queue) and wait until its worker has exited."""
        self.closing = True
        cur = self.current
        if cur is not None:
            cur.requeue = True
            self._request_cancel(cur)
        await self.fetches.shutdown()  # a download in flight starts again at the next start
        await self.models.shutdown()  # the model's partial file stays: the next start goes on from it
        await self.card_pack.shutdown()  # the same for the graphics-card support
        await self.previews.shutdown()
        if cur is None:
            return
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(cur.exited.wait(), self.kill_after_s + 5)

    # ---- requests (event loop) -------------------------------------------------------------------------------

    def status(self) -> Status:
        return Status(
            app_version=__version__, boot_id=self.hub.boot_id, seq=self.hub.seq,
            paused_reason=self.paused_reason, paused_kind=self.paused_kind, jobs=list(self.jobs),
            machine=self.machine_info(), models=self.models.info, card_pack=self.card_pack.info,
        )  # fmt: skip

    # ---- this PC's speed -----------------------------------------------------------------------------------

    def _speed_key(self) -> str:
        assert self.machine is not None
        return speed.key(str(self.machine.get("device")), self.machine.get("card"))

    def _measuring(self) -> bool:
        return self.current is not None and self.current.kind == "benchmark"

    def _unmeasured(self) -> bool:
        """No numbers for this PC's way of running yet (a new PC, or a new card): the test runs by itself."""
        entry = self.speeds.devices.get(self._speed_key())
        return entry is None or speed.estimate(entry) is None

    def machine_info(self) -> MachineInfo | None:
        if self.machine is None:
            return None
        entry = self.speeds.devices.get(self._speed_key())
        est = speed.estimate(entry) if entry is not None else None
        on_cpu = self.machine.get("device") == "cpu"
        try:
            return MachineInfo.model_validate({
                "device": self.machine.get("device"), "reason": self.machine.get("reason"),
                "card": self.machine.get("card"),
                "cpu_threads": entry.cpu_threads if on_cpu and entry is not None else None,
                "fixed_s": round(est.fixed_s, 2) if est else None, "per_s": round(est.per_s, 5) if est else None,
                "measured_by": ("jobs" if entry and speed.real_jobs(entry) else "test") if est else None,
                "measuring": self._measuring(),
                "measure_waiting": self._measure_asked and not self._measuring(),
            })  # fmt: skip
        except ValidationError:
            log.warning("this PC's description could not be shown: %r", self.machine)
            return None

    def measure_speed(self) -> None:
        """Run the speed test as soon as the running job (if any) has finished; asking twice runs it once."""
        if self.machine is None:
            raise JobError("The app is still looking at this PC; try again in a moment.", 409)
        if self._measuring() or self._measure_asked:
            return
        self._measure_asked = True
        self._publish_machine()
        self._kick()

    def fetch_models_again(self) -> None:
        if not self.models.retry():
            raise JobError("The speech model is already here or on its way.", 409)

    def fetch_card_pack(self) -> None:
        """The user asked for the graphics-card support (offered only where the PC has a usable card for it)."""
        if self.machine is None or self.machine.get("reason") != "card_pack_missing":
            raise JobError("This PC has no graphics card that the app could use, or it uses it already.", 409)
        self.card_pack.recheck()  # a support found damaged since is downloaded again
        if not self.card_pack.retry():
            raise JobError("The graphics-card support is already here or on its way.", 409)

    def _card_pack_ready(self) -> None:
        """The support came in, or turned out damaged: look at the PC again ("card", or the offer once more)."""
        if self.closing or self._loop is None:
            return
        task = self._loop.create_task(self._look_at_machine())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _publish_card_pack(self, info: ModelsInfo) -> None:
        self.hub.publish(lambda seq: CardPackEvent(seq=seq, card_pack=info))

    def _publish_models(self, info: ModelsInfo) -> None:
        self.hub.publish(lambda seq: ModelsEvent(seq=seq, models=info))

    def _publish_machine(self) -> None:
        info = self.machine_info()
        if info is not None:
            self.hub.publish(lambda seq: MachineEvent(seq=seq, machine=info))

    def _keep_speed(
        self, device: str, card: str | None, change: Callable[[speed.DeviceSpeed], speed.DeviceSpeed]
    ) -> None:
        k = speed.key(device, card)
        entry = change(self.speeds.devices.get(k) or speed.DeviceSpeed(device=device, card=card))
        self.speeds = self.speeds.model_copy(update={"devices": {**self.speeds.devices, k: entry}})
        try:
            self.speed_store.save(self.speeds)
        except OSError as exc:  # a full disk: the estimate is a convenience, no job depends on it
            log.warning("the speed measurements could not be saved: %s", exc)

    async def add(self, source: FileSource | UploadSource, engine: str = engines.DEFAULT) -> Job:
        """Queue a file (checked here) or an upload's copy (written by the upload route)."""
        if isinstance(source, FileSource):
            path = Path(source.path.strip().strip('"'))
            if not path.is_absolute():
                raise JobError("Give the full path of the file, starting with the drive letter.")
            if not path.is_file():
                raise JobError(f"There is no file at {path}.")
            source, name = FileSource(path=str(path)), path.name
        else:
            path, name = Path(source.path), source.original_name
        logscrub.register(name if isinstance(source, UploadSource) else str(path))
        try:
            info = await asyncio.to_thread(_probe, path, self.vault)  # refuse what ffmpeg cannot read before it waits
        except (media.MediaError, VaultError) as exc:
            raise JobError(str(exc)) from None
        job = Job(id=uuid.uuid4().hex[:12], name=name, source=source, created_at=self.clock(), engine=engine,
                  media_s=info.duration_s or None)  # fmt: skip
        self.jobs.append(job)
        self._saved(job)
        self._kick()
        self.previews.enroll(job)
        return job

    async def export(self, job_id: str, req: ExportRequest, out_dir: Path) -> ExportResult:
        """Write the chosen subtitle files of a finished job into `out_dir` (an existing folder)."""
        job = self._job(job_id)
        if job.state != "done" or job.project_file is None:
            raise JobError("Only a finished transcription can be exported.", 409)
        if job_id in self.busy:
            raise JobError("This job is being exported already.", 409)
        if not out_dir.is_dir():
            raise JobError(f"The folder {out_dir} does not exist any more.", 409)
        if not vault_mod.plain_allowed(out_dir, self.data_dir):  # e.g. a folder chosen before this rule existed
            raise JobError("Subtitles cannot be saved inside the app's own encrypted store; choose another folder.",
                           409)  # fmt: skip
        self.busy.add(job_id)
        try:
            async with self.project_lock(job_id):
                files, skipped = await asyncio.to_thread(
                    _write_export, self._job_dir(job) / job.project_file, req, out_dir, uploads.stem_of(job.name),
                    self.vault, self.glossary(), self.preset_book(),
                )  # fmt: skip
        finally:
            self.busy.discard(job_id)
        job = self._job(job_id)
        if files:  # an export that wrote nothing (nothing translated yet) keeps the list of the files saved before
            job = self._replace(job, exported=[str(f) for f in files])  # remove waits for busy jobs
            self._saved(job)
        return ExportResult(folder=str(out_dir), files=[str(f) for f in files], skipped=skipped)

    def exported_files(self, job_id: str) -> list[Path]:
        files = [Path(p) for p in self._job(job_id).exported]
        present = [f for f in files if f.is_file()]
        if not present:
            raise JobError("There are no saved subtitle files to show; export first.", 409)
        return present

    async def remove(self, job_id: str) -> None:
        """Drop a finished job and delete the app's own files for it (never the user's original or exports)."""
        job = self._job(job_id)
        if job.state not in FINISHED:
            raise JobError("Cancel the job before removing it.", 409)
        if job_id in self.busy:
            raise JobError("Wait until the export has finished.", 409)
        self.jobs = [j for j in self.jobs if j.id != job_id]
        self.store.save(self.jobs)
        self.hub.publish(lambda seq: RemovedEvent(seq=seq, job_id=job_id))
        folders = [self._job_dir(job)]
        if (own := _own_folder(job.source)) is not None:
            folders.append(uploads.folder(self.data_dir, own))
        await self.fetches.forget(job_id)
        await self.previews.forget(job_id)  # ffmpeg must be gone before its folder is deleted
        async with self.project_lock(job_id):  # an editor save in flight finishes first
            for f in folders:
                await asyncio.to_thread(_delete_folder, f)
        self._project_locks.pop(job_id, None)
        self._checkers = {k: v for k, v in self._checkers.items() if k[0] != job_id}

    def cancel(self, job_id: str) -> Job:
        job = self._job(job_id)
        if job.state in FINISHED:
            raise JobError("This job has already finished.", 409)
        if job.state in ("queued", "fetching"):
            job = self._finish(job, "cancelled")
            job = self._replace(job, progress=None)
            self._remove_output(job)  # a job sent back to the queue may have kept its checkpoint
            self._memory_tries.pop(job_id, None)
            self._saved(job)
            if self._loop is not None:  # a download in flight is stopped (it leaves nothing: sealed files appear whole)
                task = self._loop.create_task(self.fetches.forget(job_id))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
            return job
        cur = self.current
        if cur is not None and cur.job_id == job_id:
            self._request_cancel(cur)
        return job

    # ---- links ---------------------------------------------------------------------------------------------

    def add_link(self, text: str, engine: str = engines.DEFAULT) -> Job:
        """Queue a recording behind a link: its sound is fetched first (on the CPU), then it waits for the GPU."""
        try:
            link = links.parse(text, allow_local_http=self.allow_local_http_links)
        except links.LinkError as exc:
            raise JobError(str(exc)) from None
        logscrub.register(link.name, link.shown)
        upload_id = uuid.uuid4().hex[:12]
        folder = uploads.folder(self.data_dir, upload_id)
        folder.mkdir(parents=True)
        project.write_atomically({folder / links.LINK_FILE: self.vault.seal(link.url.encode("utf-8"), Role.LINK)})
        source = LinkSource(upload_id=upload_id, path=str(folder / sound.SOUND_FILE), shown=link.shown)
        job = Job(id=uuid.uuid4().hex[:12], name=link.name, source=source, created_at=self.clock(), engine=engine,
                  state="fetching")  # fmt: skip
        self.jobs.append(job)
        self._saved(job)
        self.fetches.enroll(job.id)
        return job

    def _link_of(self, job_id: str) -> tuple[links.Link, Path] | None:
        job = next((j for j in self.jobs if j.id == job_id), None)
        if job is None or job.state != "fetching" or not isinstance(job.source, LinkSource):
            return None
        folder = uploads.folder(self.data_dir, job.source.upload_id)
        try:
            url = self.vault.read(folder / links.LINK_FILE, Role.LINK).decode("utf-8")
            link = links.parse(url, allow_local_http=self.allow_local_http_links)
        except (OSError, VaultError, links.LinkError, UnicodeDecodeError):
            self._finish(job, "failed", error="The link of this job could not be read again; add it once more.")
            self._saved(self._job(job_id))
            return None
        self._replace(job, started_at=self.clock())
        return link, Path(job.source.path)

    def _fetch_progress(self, job_id: str, fraction: float, done: float, total: float) -> None:
        job = next((j for j in self.jobs if j.id == job_id), None)
        if job is None or job.state != "fetching":
            return
        elapsed = self.clock() - (job.started_at or self.clock())
        eta = None
        if fraction >= ETA_MIN_FRACTION and elapsed >= ETA_MIN_ELAPSED_S:
            eta = round(elapsed * (1 - fraction) / fraction, 1)
        progress = JobProgress(phase="fetch", fraction=fraction, media_done_s=done, media_total_s=total, eta_s=eta)
        self._replace(job, progress=progress)
        self.hub.publish(lambda seq: ProgressEvent(seq=seq, job_id=job_id, progress=progress))

    def _fetched(self, job_id: str, error: str | None, info: media.MediaInfo | None) -> None:
        job = next((j for j in self.jobs if j.id == job_id), None)
        if job is None or job.state != "fetching":  # cancelled or removed meanwhile
            return
        if error is not None:
            self._finish(job, "failed", error=error)
            self._saved(self._job(job_id))
            return
        assert isinstance(job.source, LinkSource)
        if info is not None:
            source = job.source.model_copy(update={"had_video": info.has_video, "frame_rate": _rate_text(info)})
            job = self._replace(job, source=source, media_s=info.duration_s or None)
        job = self._replace(job, state="queued", started_at=None, progress=None)
        self._saved(job)
        self.previews.enroll(job)
        self._kick()

    # ---- the worker ------------------------------------------------------------------------------------------

    def _kick(self) -> None:
        # nothing runs before the speech model is here (the first start downloads it)
        while not (self.closing or self.current is not None or self.paused_reason is not None) and self.models.ready:
            job = next((j for j in self.jobs if j.state == "queued"), None)
            # the speed test: when asked for, before the next job; by itself only with nothing waiting (a job
            # measures the PC too), and at most once per session (a failing test must not start again and again)
            test = self.machine is not None and (
                self._measure_asked or (job is None and not self._measured_once and self._unmeasured())
            )
            if job is None and not test:
                return
            busy = self.gpu_check()
            if busy is not None:  # a CLI run holds the GPU: wait without starting a worker each time
                self._pause(busy, "gpu_busy", self.busy_retry_s)
                return
            if test:
                if self._start_benchmark():
                    return
                continue
            assert job is not None
            if self._start(job):
                return
            # it could not even start (and is marked failed): go on with the next one

    def _start_benchmark(self) -> bool:
        self._measure_asked = False
        self._measured_once = True
        try:
            proc = self._spawn()
        except OSError as exc:
            log.warning("the speed test could not start: %s", exc)
            self._publish_machine()
            return False
        cur = _Running("", proc, kind="benchmark")
        self.current = cur
        self._publish_machine()
        self._send_spec(cur, BenchmarkSpec(engine=engines.DEFAULT).model_dump_json())
        return True

    def _spawn(self) -> subprocess.Popen[str]:
        return subprocess.Popen(
            self.worker_cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            creationflags=subprocess.CREATE_NO_WINDOW,  # a hidden console, inherited by ffmpeg: no flashing
        )  # fmt: skip

    def _send_spec(self, cur: _Running, spec_json: str) -> None:
        assert cur.proc.stdin is not None
        try:
            cur.proc.stdin.write(spec_json + "\n")
            cur.proc.stdin.flush()
        except OSError:
            pass  # it died at once; the exit handler reports it
        name = cur.job_id or "speed-test"
        threading.Thread(target=self._read_worker, args=(cur,), daemon=True, name=f"worker-{name}").start()

    def _start(self, job: Job) -> bool:
        out_dir = self._job_dir(job)
        job = self._replace(job, state="running", attempts=job.attempts + 1, started_at=self.clock(),
                            finished_at=None, error=None, note=None, project_file=None, progress=None)  # fmt: skip
        kept = job.source if isinstance(job.source, (UploadSource, LinkSource)) else None
        spec = JobSpec(source=job.source.path, out_dir=str(out_dir), stem=uploads.stem_of(job.name), engine=job.engine,
                       data_dir=str(self.data_dir), had_video=kept.had_video if kept and kept.had_video else None,
                       frame_rate=kept.frame_rate if kept else None,
                       glossary_text=self.own_glossary(), cpu_threads=self._cpu_threads(),
                       presets=self.job_types())  # fmt: skip
        try:
            _clear_job_dir(out_dir, keep_checkpoint=True)  # an interrupted run's progress: the worker decides
            out_dir.mkdir(parents=True, exist_ok=True)
            self.store.save(self.jobs)
            proc = self._spawn()
        except OSError as exc:
            log.exception("job %s could not start", job.id)
            self._remove_output(job)
            why = exc.strerror or exc.__class__.__name__  # not str(exc): it can name a folder of the user's
            self._finish(
                job, "failed", error=f"The transcription could not start ({why}). Close the app and start it again."
            )
            self._saved(job)
            return False
        cur = _Running(job.id, proc)
        self.current = cur
        self._publish_job(self._job(job.id))
        self._send_spec(cur, spec.model_dump_json())
        return True

    def _cpu_threads(self) -> int | None:
        if self.machine is None or self.machine.get("device") != "cpu":
            return None
        entry = self.speeds.devices.get(self._speed_key())
        return entry.cpu_threads if entry is not None else None

    def _read_worker(self, cur: _Running) -> None:
        """Worker thread: forward stdout lines, keep the stderr tail, then report the exit (in that order)."""
        assert self._loop is not None and cur.proc.stdout is not None and cur.proc.stderr is not None
        loop = self._loop
        err = cur.proc.stderr

        def drain_stderr() -> None:
            for line in err:
                if line.strip():
                    cur.stderr_tail.append(line.rstrip())
                    log.info("worker: %s", line.rstrip())

        t = threading.Thread(target=drain_stderr, daemon=True, name=f"worker-err-{cur.job_id or 'speed-test'}")
        t.start()
        for line in cur.proc.stdout:
            loop.call_soon_threadsafe(self._on_line, cur, line)
        code = cur.proc.wait()
        t.join(timeout=5)
        loop.call_soon_threadsafe(self._worker_gone, cur, code)

    def _on_line(self, cur: _Running, line: str) -> None:
        try:
            msg = _messages.validate_json(line)
        except ValidationError:
            log.warning("worker sent a line that is not a message: %r", line[:200])
            return
        if isinstance(msg, Hello):
            cur.pid = msg.pid
        elif isinstance(msg, ProgressMsg):
            if cur.kind == "job":
                self._on_progress(cur, msg)
        else:
            cur.outcome = msg

    def _on_progress(self, cur: _Running, msg: ProgressMsg) -> None:
        job = self._job(cur.job_id)
        before = job.progress.fraction if job.progress else 0.0
        fraction = max(before, min(max(msg.fraction, 0.0), 1.0))
        now = self.clock()
        if cur.eta_base is None and msg.phase == "recognise":  # a resumed job jumps here: its rate from now on
            cur.eta_base = (now, fraction)
        t0, f0 = cur.eta_base or (job.started_at or now, 0.0)
        elapsed, gained = now - t0, fraction - f0
        eta = None
        if gained >= ETA_MIN_FRACTION and elapsed >= ETA_MIN_ELAPSED_S:
            eta = round(elapsed * (1 - fraction) / gained, 1)
        try:
            progress = JobProgress(phase=msg.phase, fraction=fraction, media_done_s=msg.media_done_s,
                                   media_total_s=msg.media_total_s, eta_s=eta)  # fmt: skip
        except ValidationError:
            log.warning("worker sent progress the window cannot show: %r", msg)
            return
        self._replace(job, progress=progress)
        self.hub.publish(lambda seq: ProgressEvent(seq=seq, job_id=job.id, progress=progress))

    def _worker_gone(self, cur: _Running, code: int) -> None:
        if self.card_pack.recheck():  # the worker found the graphics-card support damaged: offer it again
            self._card_pack_ready()
        self._on_exit(cur, code)

    def _on_exit(self, cur: _Running, code: int) -> None:
        if cur.kind == "benchmark":
            self._benchmark_exit(cur, code)
            return
        self.current = None
        job = self._job(cur.job_id)
        out = cur.outcome
        if not isinstance(out, GpuMemoryMsg) or cur.requeue or cur.cancel_requested:
            self._memory_tries.pop(job.id, None)
        if isinstance(out, DoneMsg):
            ckpt.clear(self._job_dir(job))  # the worker deletes it; a stray one must not outlive the job
            job = self._finish(job, "done")
            job = self._replace(job, project_file=out.project_file, note=out.note)
            timing = out.timing
            if timing is not None and out.device is not None and not timing.resumed:
                self._keep_speed(out.device, out.card, lambda e: speed.with_job(e, timing))
                self._publish_machine()
        elif cur.requeue:  # the app is closing: run it again next time, from its checkpoint
            self._remove_output(job, keep_checkpoint=True)
            self._requeue(job, count_attempt=False)
        elif isinstance(out, CancelledMsg) or cur.cancel_requested:
            self._remove_output(job)
            self._finish(job, "cancelled")
        elif isinstance(out, GpuBusyMsg):
            self._remove_output(job, keep_checkpoint=True)
            self._requeue(job, count_attempt=False)
            self._pause(out.message, "gpu_busy", self.busy_retry_s)
        elif isinstance(out, GpuMemoryMsg):
            self._memory_full(job, out)
        elif isinstance(out, DiskFullMsg):
            # never given up: the recognition is finished and kept; it is saved as soon as there is room
            self._remove_output(job, keep_checkpoint=True)
            self._requeue(job, count_attempt=False)
            self._pause(out.message, "disk_full", self.memory_retry_s)
        elif isinstance(out, ErrorMsg):
            self._remove_output(job)
            self._finish(job, "failed", error=out.message)
        elif job.attempts < MAX_ATTEMPTS:  # it died on its own (a native crash): once more, from its checkpoint
            log.warning("job %s: the worker stopped unexpectedly (exit %s); starting it again", job.id, code)
            self._remove_output(job, keep_checkpoint=True)
            self._requeue(job)
        else:
            self._remove_output(job)
            last = cur.stderr_tail[-1] if cur.stderr_tail else "no further detail"
            self._finish(job, "failed", error=f"The transcription stopped unexpectedly (exit code {code}): {last}")
        self._saved(self._job(job.id))
        log.info("job %s ended: %s (worker exit %s)", job.id, self._job(job.id).state, code)
        cur.exited.set()
        self._kick()

    def _benchmark_exit(self, cur: _Running, code: int) -> None:
        self.current = None
        out = cur.outcome
        if isinstance(out, BenchmarkDoneMsg) and not self.closing:
            res = out

            def change(e: speed.DeviceSpeed) -> speed.DeviceSpeed:
                # a new test starts the estimate over: earlier jobs may have run with another thread count
                return e.model_copy(update={"benchmark": res.benchmark, "cpu_threads": res.cpu_threads, "jobs": []})

            self._keep_speed(res.device, res.card, change)
            log.info("speed test: %s, threads %s (tried %s), %s", res.device, res.cpu_threads, res.tried,
                     res.benchmark.model_dump())  # fmt: skip
        elif isinstance(out, GpuBusyMsg):  # a CLI run took the GPU: the test waits and runs after it
            self._measure_asked = True
            self._pause(out.message, "gpu_busy", self.busy_retry_s)
        elif not self.closing:
            last = cur.stderr_tail[-1] if cur.stderr_tail else "no further detail"
            detail = out.message if isinstance(out, (ErrorMsg, GpuMemoryMsg)) else last
            log.warning("the speed test did not finish (exit %s): %s", code, detail)
        self._publish_machine()
        cur.exited.set()
        self._kick()

    def _request_cancel(self, cur: _Running) -> None:
        if cur.cancel_requested:
            return
        cur.cancel_requested = True
        stdin = cur.proc.stdin
        if stdin is not None:
            with contextlib.suppress(OSError, ValueError):
                stdin.write("cancel\n")
                stdin.flush()
        assert self._loop is not None
        self._loop.call_later(self.kill_after_s, self._kill_if_alive, cur)

    def _kill_if_alive(self, cur: _Running) -> None:
        if cur.exited.is_set() or cur.proc.poll() is not None:
            return
        log.warning("worker for %s did not stop within %.0f s; killing it", cur.job_id or "the speed test",
                    self.kill_after_s)  # fmt: skip
        if cur.pid is not None:
            with contextlib.suppress(OSError):
                os.kill(cur.pid, signal.SIGTERM)  # TerminateProcess; its job object takes ffmpeg along
        with contextlib.suppress(OSError):
            cur.proc.kill()

    def _memory_full(self, job: Job, out: GpuMemoryMsg) -> None:
        """The GPU memory was full: the whole queue waits (it is the machine's memory) and the job keeps its place
        and its checkpoint. The same job meeting it again and again fails, so the queue never stays stuck."""
        tries = self._memory_tries.get(job.id, 0) + 1
        if tries >= MEMORY_TRIES:
            self._memory_tries.pop(job.id, None)
            self._remove_output(job)
            self._finish(job, "failed", error=MEMORY_GAVE_UP.format(tries=tries))
            return
        self._memory_tries[job.id] = tries
        self._remove_output(job, keep_checkpoint=True)
        self._requeue(job, count_attempt=False)
        self._pause(out.message, "gpu_memory", self.memory_retry_s)

    def _pause(self, reason: str, kind: PausedKind, after_s: float) -> None:
        self.paused_reason, self.paused_kind = reason, kind
        self.hub.publish(lambda seq: PausedEvent(seq=seq, reason=reason, kind=kind))
        assert self._loop is not None
        if self._unpause_timer is not None:
            self._unpause_timer.cancel()
        self._unpause_timer = self._loop.call_later(after_s, self._unpause)

    def _unpause(self) -> None:
        self._unpause_timer = None
        self.paused_reason = self.paused_kind = None
        self.hub.publish(lambda seq: PausedEvent(seq=seq, reason=None, kind=None))
        self._kick()

    # ---- the subtitle editor ---------------------------------------------------------------------------------

    def glossary(self) -> Glossary:
        """The built-in glossary plus the user's own list; the built-in one alone if that list cannot be used."""
        text = self.own_glossary()
        try:
            return gl_mod.current(text)
        except gl_mod.GlossaryError:
            log.warning("the user's own list of terms cannot be used; the built-in one is")
            return gl_mod.builtin()

    def project_lock(self, job_id: str) -> asyncio.Lock:
        return self._project_locks.setdefault(job_id, asyncio.Lock())

    def _project_file(self, job_id: str) -> Path:
        job = self._job(job_id)
        if job.state != "done" or job.project_file is None:
            raise JobError("Only a finished transcription can be edited.", 409)
        return self._job_dir(job) / job.project_file

    async def _edit(
        self, job_id: str, preset_id: str, fn: Callable[..., editing.Opened], *args: object
    ) -> editing.Opened:
        path = self._project_file(job_id)
        async with self.project_lock(job_id):
            self._job(job_id)  # removed while waiting for the lock: 404
            opened = await asyncio.to_thread(
                fn, self.vault, path, preset_id, *args, gl=self.glossary(), book=self.preset_book()
            )
        self._checkers[(job_id, preset_id)] = editing.Checker(opened.track, opened.media_end_ms)
        return opened

    async def open_track(self, job_id: str, preset_id: str) -> editing.Opened:
        return await self._edit(job_id, preset_id, editing.open_track)

    async def save_track(self, job_id: str, preset_id: str, req: SaveTrackRequest) -> editing.Opened:
        return await self._edit(
            job_id, preset_id, editing.save_track, req.base_revision, req.cues, req.next_cue_id,
            req.translation_language,
        )  # fmt: skip

    async def resegment(self, job_id: str, preset_id: str, base_revision: str) -> editing.Opened:
        return await self._edit(job_id, preset_id, editing.resegment, base_revision)

    async def check_cues(self, job_id: str, preset_id: str, cues: list[Cue]) -> list[Violation]:
        path = self._project_file(job_id)
        found = self._checkers.get((job_id, preset_id))
        if found is None:
            async with self.project_lock(job_id):
                found = await asyncio.to_thread(editing.checker, self.vault, path, preset_id)
            self._checkers[(job_id, preset_id)] = found
        return await asyncio.to_thread(found.check, cues)

    async def words(self, job_id: str, preset_id: str | None = None) -> list[Word]:
        path = self._project_file(job_id)
        async with self.project_lock(job_id):
            return await asyncio.to_thread(editing.words, self.vault, path, preset_id, gl=self.glossary(),
                                           book=self.preset_book())  # fmt: skip

    # ---- state helpers ---------------------------------------------------------------------------------------

    def job(self, job_id: str) -> Job:
        return self._job(job_id)

    def _job(self, job_id: str) -> Job:
        for j in self.jobs:
            if j.id == job_id:
                return j
        raise JobError("No such job.", 404)

    def _replace(self, job: Job, **changes: object) -> Job:
        new = job.model_copy(update=changes)
        i = next((i for i, j in enumerate(self.jobs) if j.id == job.id), None)
        if i is not None:  # a job removed meanwhile stays removed
            self.jobs[i] = new
        return new

    def _finish(self, job: Job, state: str, *, error: str | None = None) -> Job:
        return self._replace(job, state=state, finished_at=self.clock(), error=error)

    def _requeue(self, job: Job, *, count_attempt: bool = True) -> Job:
        attempts = job.attempts if count_attempt else max(job.attempts - 1, 0)
        return self._replace(job, state="queued", started_at=None, progress=None, attempts=attempts)

    def _saved(self, job: Job) -> None:
        self.store.save(self.jobs)
        self._publish_job(self._job(job.id))

    def _publish_job(self, job: Job) -> None:
        self.hub.publish(lambda seq: JobEvent(seq=seq, job=job))

    def _job_dir(self, job: Job) -> Path:
        return self.data_dir / "jobs" / job.id  # the id is ours (12 hex digits), never request text

    def _job_dir_of(self, job_id: str) -> Path:
        return self.data_dir / "jobs" / job_id

    def _remove_output(self, job: Job, *, keep_checkpoint: bool = False) -> None:
        _clear_job_dir(self._job_dir(job), keep_checkpoint=keep_checkpoint)

    # ---- the preview -----------------------------------------------------------------------------------------

    def set_preview(self, job_id: str, state: JobPreview | None) -> None:
        """Record the preview's state (live only) and tell the window; a job removed meanwhile stays removed."""
        job = next((j for j in self.jobs if j.id == job_id), None)
        if job is None:
            return
        self._replace(job, preview=state)
        self.hub.publish(lambda seq: PreviewEvent(seq=seq, job_id=job_id, preview=state))

    def preview_files(self, job_id: str) -> tuple[Path, Path, preview.PreviewInfo]:
        """(media file, peaks file, facts) of a job whose preview is ready; the path comes from the job id only."""
        job = self._job(job_id)
        info = preview.read_ready(self._job_dir(job), self.vault)
        if job.preview is None or job.preview.state != "ready" or info is None:
            raise JobError("The preview is not ready yet.", 404)
        root = preview.folder(self._job_dir(job))
        return root / info.media_file, root / info.peaks_file, info


def _delete_folder(folder: Path) -> None:
    """Delete a folder a player may still hold open for a moment; what stays is swept at the next start."""
    for attempt in range(5):
        try:
            shutil.rmtree(folder)
            return
        except FileNotFoundError:
            return
        except OSError as exc:
            if attempt == 4:
                log.warning("could not delete %s now (%s); it is removed at the next start", folder, exc)
                return
            time.sleep(0.3)


def _sweepable(name: str) -> bool:
    return name.startswith("preview") or name in (ckpt.FILE, f".{ckpt.FILE}.tmp")


def _clear_job_dir(folder: Path, *, keep_checkpoint: bool = False) -> None:
    """Empty a job's folder but leave its preview: that is made from the source, not from the transcription.
    `keep_checkpoint`: also leave an interrupted run's progress (sealed, whole or absent) for its next run."""
    if not folder.is_dir():
        return
    for child in folder.iterdir():
        if child.name in (preview.PREVIEW_DIR, preview.BUILD_DIR):
            continue
        if keep_checkpoint and child.name == ckpt.FILE:
            continue
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)


def _rate_text(info: media.MediaInfo) -> str | None:
    rate = info.frame_rate
    return f"{rate.numerator}/{rate.denominator}" if rate else None


def _probe(path: Path, vault: Vault) -> media.MediaInfo:
    with blobserve.media_input(path, vault) as source:
        return media.probe(source)


def _write_export(  # noqa: PLR0917 - one call site, in a thread
    project_file: Path,
    req: ExportRequest,
    out_dir: Path,
    stem: str,
    vault: Vault,
    gl: Glossary,
    book: Mapping[str, presets.Preset] | None = None,
) -> tuple[list[Path], list[str]]:
    """Worker thread: build any missing tracks, refuse to overwrite unless asked, write everything atomically.
    The subtitle files are plain (that is what an export is for); the project file stays sealed."""
    try:
        doc = project.load(project_file, vault)
        new_doc, reports = subtitles.ensure_tracks(doc, req.presets, gl, book)
        made, skipped = subtitles.files_for(
            new_doc, [r.track.preset_id for r in reports], req.formats, req.texts, out_dir, stem, book
        )
    except (project.ProjectError, presets.PresetError) as exc:
        raise JobError(str(exc)) from None
    except subtitles.SubtitleError as exc:
        raise JobError(f"The subtitles cannot be made: {exc}", 409) from None
    existing = sorted(str(p) for p in made if p.exists())
    if existing and not req.replace:
        raise ExportExists(existing)
    outputs: dict[Path, str | bytes] = dict(made)
    if any(not r.reused for r in reports):
        outputs[project_file] = project.stored(new_doc, vault)  # keep the new tracks with the project
    try:
        project.write_atomically(outputs)
    except OSError as exc:  # a read-only folder, a full disk, a file open in another program
        raise JobError(f"The subtitles could not be written to {out_dir}: {exc.strerror or exc}", 409) from None
    return list(made), skipped
