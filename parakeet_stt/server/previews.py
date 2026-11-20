"""The preview maker: one at a time, on the CPU, beside the GPU queue.

Jobs are enrolled when they are added (so a job waiting for the GPU already gets its preview) and again at every
server start (a job whose preview is missing or incomplete is made again). The event loop owns all state; the
ffmpeg pass runs in a thread and reports back through `call_soon_threadsafe`.
"""

from __future__ import annotations

import asyncio
import collections
import contextlib
import logging
import time
from collections.abc import Callable
from pathlib import Path

from parakeet_stt import preview
from parakeet_stt.cancel import Cancelled, CancelToken
from parakeet_stt.server.messages import Job, JobPreview
from parakeet_stt.vault import Vault

log = logging.getLogger("parakeet_stt.server")

PROGRESS_MIN_INTERVAL_S = 0.5
FORGET_WAIT_S = 20.0
_WANTED = frozenset({"queued", "running", "done"})  # a failed or cancelled job is never opened in the editor


class Previews:
    def __init__(
        self,
        job_dir: Callable[[str], Path],
        jobs: Callable[[], list[Job]],
        update: Callable[[str, JobPreview | None], None],
        vault: Vault,
        build: Callable[..., preview.PreviewInfo] | None = None,  # default: preview.build, looked up when used
    ) -> None:
        self._vault = vault
        self._job_dir = job_dir
        self._jobs = jobs
        self._update = update
        self._build = build
        self._queue: collections.deque[str] = collections.deque()
        self._current: str | None = None
        self._cancel: CancelToken | None = None
        self._idle = asyncio.Event()
        self._idle.set()
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._last_sent = 0.0
        self._closing = False

    def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._task = self._loop.create_task(self._run(), name="previews")
        for job in self._jobs():
            self.enroll(job)

    async def shutdown(self) -> None:
        self._closing = True
        if self._cancel is not None:
            self._cancel.cancel()
        self._wake.set()
        if self._task is not None:
            with contextlib.suppress(asyncio.CancelledError, TimeoutError):
                await asyncio.wait_for(self._task, 15)

    def enroll(self, job: Job) -> None:
        """Make sure the job has a preview: use the one on disk, or queue it to be made."""
        if job.state not in _WANTED or job.id == self._current or job.id in self._queue:
            return
        info = preview.read_ready(self._job_dir(job.id), self._vault)
        if info is not None:
            self._update(job.id, _ready(info))
            return
        self._update(job.id, JobPreview(state="waiting"))
        self._queue.append(job.id)
        self._wake.set()

    async def forget(self, job_id: str) -> None:
        """A job is being removed: stop making its preview (ffmpeg ended) before its folder is deleted."""
        with contextlib.suppress(ValueError):
            self._queue.remove(job_id)
        if self._current == job_id and self._cancel is not None:
            self._cancel.cancel()
            with contextlib.suppress(TimeoutError):  # ffmpeg stalled on a vanished drive: the delete retries later
                await asyncio.wait_for(self._idle.wait(), FORGET_WAIT_S)

    # ---- the loop --------------------------------------------------------------------------------------------

    async def _run(self) -> None:
        while not self._closing:
            if not self._queue:
                self._wake.clear()
                await self._wake.wait()
                continue
            job_id = self._queue.popleft()
            job = next((j for j in self._jobs() if j.id == job_id), None)
            if job is None or job.state not in _WANTED:  # cancelled or failed while it waited
                continue
            await self._make(job)

    async def _make(self, job: Job) -> None:
        assert self._loop is not None
        loop, job_id = self._loop, job.id
        token = CancelToken()
        self._current, self._cancel = job_id, token
        self._idle.clear()
        self._update(job_id, JobPreview(state="making"))
        src = Path(job.source.path)

        def report(fraction: float, done: float, total: float) -> None:
            now = time.monotonic()
            if now - self._last_sent < PROGRESS_MIN_INTERVAL_S:
                return
            self._last_sent = now
            loop.call_soon_threadsafe(self._progress, job_id, fraction, done, total)

        try:
            info = await asyncio.to_thread(
                self._build or preview.build, src, self._job_dir(job_id), self._vault, report, token
            )
            self._update(job_id, _ready(info))
        except Cancelled:
            log.info("the preview of job %s was stopped", job_id)
        except preview.PreviewError as exc:
            log.warning("preview of job %s failed: %s", job_id, exc)
            self._update(job_id, JobPreview(state="failed", error=str(exc)))
        except Exception as exc:
            log.exception("preview of job %s crashed", job_id)
            self._update(
                job_id, JobPreview(state="failed", error=f"The preview could not be made ({exc.__class__.__name__}).")
            )
        finally:
            self._current, self._cancel = None, None
            self._idle.set()

    def _progress(self, job_id: str, fraction: float, done: float, total: float) -> None:
        if self._current != job_id or (self._cancel is not None and self._cancel.cancelled):
            return
        self._update(job_id, JobPreview(state="making", fraction=fraction, media_done_s=done, media_total_s=total))


def _ready(info: preview.PreviewInfo) -> JobPreview:
    return JobPreview(state="ready", kind=info.kind, fraction=1.0, media_done_s=info.duration_s,
                      media_total_s=info.duration_s, peaks_per_s=info.peaks_per_s)  # fmt: skip
