"""The worker: `python -m parakeet_stt worker`, one process per job (or speed test), started by the server.

Protocol, one JSON object per line. stdin: the spec (a job or the speed test), then optionally "cancel"; end of
stdin (the server is gone) also cancels. stdout: `WorkerMessage`s. Everything else any library prints goes to
stderr, whose last lines the server shows if the worker dies.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import threading
import time
from fractions import Fraction
from pathlib import Path
from typing import Annotated, Literal, TextIO

from pydantic import BaseModel, Field, ValidationError

from parakeet_stt import machine
from parakeet_stt.cancel import CancelToken
from parakeet_stt.pipeline import Phase, Progress
from parakeet_stt.presets import Preset
from parakeet_stt.speed import Benchmark, JobTiming
from parakeet_stt.transcript import Document

PROGRESS_MIN_INTERVAL_S = 0.25  # at most 4 progress lines per second (phase changes always go through)
DISK_FULL = (
    "The disk is full, so the finished transcript cannot be saved. Free some space on the drive that holds the app; "
    "the queue tries again by itself and goes on where it stopped."
)


def save_failed(exc: OSError) -> str:
    """A sentence for any other failure to write the result (never the path: it names the user's file)."""
    why = exc.strerror or exc.__class__.__name__
    return f"The finished transcript could not be saved ({why}). Check the app's folder, then add the file again."


class JobSpec(BaseModel):
    kind: Literal["job"] = "job"
    source: str
    out_dir: str
    stem: str
    engine: str  # the registry name; checked by the engine registry when the job runs
    data_dir: str  # whose key seals the result (each process unwraps the key itself; it never travels here)
    had_video: bool | None = None  # set when only the sound of a video was kept: its picture's facts
    frame_rate: str | None = None
    glossary_text: str = ""  # the user's own list of terms, as typed (the built-in list comes with the app)
    cpu_threads: int | None = None  # on the processor: the thread count the speed test found fastest
    speech_language: str | None = None  # the Whisper engine: the spoken language ("auto" or None: detected)
    presets: list[Preset] = []  # the user's own subtitle types to make subtitles for, besides the built-in ones


class BenchmarkSpec(BaseModel):
    """Measure this PC (speed.py): nothing is read or written but the answer."""

    kind: Literal["benchmark"] = "benchmark"
    engine: str


def parse_spec(line: str) -> JobSpec | BenchmarkSpec:
    """The first stdin line: the speed test says so; anything else is a job (older servers send no kind)."""
    try:
        kind = json.loads(line).get("kind")
    except (ValueError, AttributeError):
        kind = None
    return (BenchmarkSpec if kind == "benchmark" else JobSpec).model_validate_json(line)


class Hello(BaseModel):
    t: Literal["hello"] = "hello"
    pid: int


class ProgressMsg(BaseModel):
    t: Literal["progress"] = "progress"
    phase: Phase
    fraction: float
    media_done_s: float
    media_total_s: float


class DoneMsg(BaseModel):
    t: Literal["done"] = "done"
    project_file: str
    note: str | None = None
    device: str | None = None  # where it ran ("cuda" / "cpu") and on which card: the speed it is filed under
    card: str | None = None
    timing: JobTiming | None = None


class BenchmarkDoneMsg(BaseModel):
    t: Literal["benchmark_done"] = "benchmark_done"
    device: str
    card: str | None = None
    cpu_threads: int | None = None
    benchmark: Benchmark
    tried: dict[int, float] = {}  # processor: thread count -> model rate


class ErrorMsg(BaseModel):
    t: Literal["error"] = "error"
    message: str


class GpuBusyMsg(BaseModel):
    t: Literal["gpu_busy"] = "gpu_busy"
    message: str


class GpuMemoryMsg(BaseModel):
    """The graphics card ran out of memory; the progress so far is kept for the next try."""

    t: Literal["gpu_memory"] = "gpu_memory"
    message: str


class DiskFullMsg(BaseModel):
    """The finished result could not be saved: the disk is full. The progress so far is kept for the next try."""

    t: Literal["disk_full"] = "disk_full"
    message: str


class CancelledMsg(BaseModel):
    t: Literal["cancelled"] = "cancelled"


WorkerMessage = Annotated[
    Hello
    | ProgressMsg
    | DoneMsg
    | BenchmarkDoneMsg
    | ErrorMsg
    | GpuBusyMsg
    | GpuMemoryMsg
    | DiskFullMsg
    | CancelledMsg,
    Field(discriminator="t"),
]


class _Out:
    def __init__(self, stream: TextIO) -> None:
        self.stream = stream
        self.lock = threading.Lock()

    def send(self, msg: BaseModel) -> None:
        with self.lock:
            self.stream.write(msg.model_dump_json() + "\n")
            self.stream.flush()


def _watch_stdin(stdin: TextIO, cancel: CancelToken) -> None:
    for line in stdin:
        if line.strip() == "cancel":
            cancel.cancel()
    cancel.cancel()  # the server closed the pipe (or died): stop


def _make_tracks(doc: Document, spec: JobSpec) -> tuple[Document, str | None]:
    """The subtitles of every built-in type and of the user's ticked own types, one type at a time: a type that cannot
    be made never costs the others, nor the recognition, which took minutes on the GPU. Returns a note for the user."""
    from parakeet_stt import glossary, presets, subtitles

    try:
        gl = glossary.current(spec.glossary_text)
    except glossary.GlossaryError:
        gl = glossary.builtin()  # the user's own list cannot be used: the built-in one (if any) still applies
    book = presets.book(spec.presets)
    failed = []
    for pid in [*presets.names(), *(p.id for p in spec.presets)]:
        try:
            doc, _ = subtitles.ensure_tracks(doc, [pid], gl, book)
        except (subtitles.SubtitleError, presets.PresetError, ValidationError) as exc:
            failed.append(f"{book[pid].name if pid in book else pid}: {exc}")
    return doc, ("Some subtitles could not be made: " + "; ".join(failed)) if failed else None


def run(  # noqa: PLR0911 - one outcome message for each way a job can end
    spec: JobSpec, out: _Out, cancel: CancelToken, here: machine.Machine | None = None
) -> None:
    """Transcribe one file into `<out_dir>/<stem>.pstt.json` with a track for every preset. `here`: this PC
    (`machine.detect`), asked once per worker."""
    here = here or machine.detect()
    from parakeet_stt import blobserve, gpu, media, project, vault
    from parakeet_stt import checkpoint as ckpt
    from parakeet_stt.cancel import Cancelled
    from parakeet_stt.engines.base import EngineBusy, EngineError, EngineOutOfMemory
    from parakeet_stt.pipeline import transcribe_file

    probe_lock = gpu.GpuLock()  # a CLI run may hold the GPU; say so instead of failing the job
    try:
        probe_lock.acquire()
    except gpu.GpuBusy as exc:
        out.send(GpuBusyMsg(message=str(exc)))
        return
    probe_lock.release()

    on_progress = _Reporter(out)
    out_path = Path(spec.out_dir) / f"{spec.stem}{project.SUFFIX}"
    source = Path(spec.source)
    try:
        v = vault.Vault.open(Path(spec.data_dir))
        progress_file = ckpt.Checkpoint(Path(spec.out_dir) / ckpt.FILE, v)
        with blobserve.media_input(source, v) as src:
            picture = None
            if spec.had_video is not None:
                picture = (spec.had_video, Fraction(spec.frame_rate) if spec.frame_rate else None)
            result = transcribe_file(source, engine_name=spec.engine, on_progress=on_progress, cancel=cancel,
                                     source=src, picture=picture, checkpoint=progress_file,
                                     engine_options=_engine_options(spec, here))  # fmt: skip
        cancel.raise_if_cancelled()
        doc, note = _make_tracks(result.document, spec)
        cancel.raise_if_cancelled()
        try:
            project.save(doc, out_path, v)
        except OSError as exc:
            out.send(DiskFullMsg(message=DISK_FULL) if project.disk_full(exc) else ErrorMsg(message=save_failed(exc)))
            return
        with contextlib.suppress(OSError):  # e.g. held by a virus scanner: the runner removes it after "done"
            progress_file.clear()  # the project holds it all now
    except Cancelled:
        out.send(CancelledMsg())
        return
    except (media.MediaError, project.ProjectError, vault.VaultError) as exc:
        out.send(ErrorMsg(message=str(exc)))
        return
    except EngineBusy as exc:  # a CLI run took the GPU between the check above and loading the model
        out.send(GpuBusyMsg(message=str(exc)))
        return
    except EngineOutOfMemory as exc:  # the checkpoint stays: the next try goes on from it
        out.send(GpuMemoryMsg(message=str(exc)))
        return
    except EngineError as exc:
        out.send(ErrorMsg(message=str(exc)))
        return
    timing = _timing(on_progress, result.document.media.decoded_ms / 1000, result.stats.resumed)
    out.send(DoneMsg(project_file=out_path.name, note=note, device=here.device, card=here.card, timing=timing))


class _Reporter:
    """Progress to the server (at most 4 lines a second; phase changes always), and when the model was ready."""

    def __init__(self, out: _Out) -> None:
        self.out = out
        self.last_t, self.last_phase = 0.0, ""
        self.ready_s: list[float] = []  # process age when the model was ready (the job's fixed cost)
        self.ready_at = 0.0  # the same moment on the monotonic clock: the work is timed from it

    def __call__(self, p: Progress) -> None:
        from parakeet_stt import benchmark

        if p.phase == "decode" and not self.ready_s:
            self.ready_s.append(benchmark.process_age_s())
            self.ready_at = time.perf_counter()
        now = time.monotonic()
        if p.phase == self.last_phase and now - self.last_t < PROGRESS_MIN_INTERVAL_S and p.phase != "done":
            return
        self.last_t, self.last_phase = now, p.phase
        self.out.send(ProgressMsg(phase=p.phase, fraction=p.fraction, media_done_s=p.media_done_s,
                                  media_total_s=p.media_total_s))  # fmt: skip


def _engine_options(spec: JobSpec, here: machine.Machine) -> dict[str, object] | None:
    """On the processor: the thread count the speed test found fastest (the engine's own choice without one).
    Whisper also gets the spoken language and the user's own terms as its prompt (it then prefers those spellings)."""
    from parakeet_stt import glossary
    from parakeet_stt.engines import whisper

    options: dict[str, object] = {}
    if spec.cpu_threads and here.device == "cpu":
        options["cpu_threads"] = spec.cpu_threads
    if spec.engine == whisper.WhisperEngine.name:
        try:
            terms = [e.term for e in glossary.parse_text(spec.glossary_text)]
        except glossary.GlossaryError:
            terms = []
        options |= {"language": spec.speech_language, "prompt": whisper.prompt_from_terms(terms)}
    return options or None


def _timing(progress: _Reporter, media_s: float, resumed: bool) -> JobTiming | None:
    """What the job took: process start -> model ready, then the rest until now (the project is saved), the
    latter on the monotonic clock so a clock correction during a long job does not count."""
    if not progress.ready_s:
        return None
    work = max(time.perf_counter() - progress.ready_at, 0.0)
    return JobTiming(fixed_s=progress.ready_s[0], work_s=work, media_s=media_s, resumed=resumed)


def run_benchmark(spec: BenchmarkSpec, out: _Out, cancel: CancelToken, here: machine.Machine) -> None:
    """The speed test (benchmark.py) for the device this PC runs the model on."""
    from parakeet_stt import benchmark, engines
    from parakeet_stt.cancel import Cancelled
    from parakeet_stt.engines.base import EngineBusy, EngineError, EngineOutOfMemory

    started = benchmark.process_age_s()
    candidates = machine.thread_candidates(here) if here.device == "cpu" else None

    def make(threads: int | None) -> engines.Engine:
        return engines.create(spec.engine, **({"cpu_threads": threads} if threads else {}))

    try:
        res = benchmark.run(make, candidates, cancel, started)
    except Cancelled:
        out.send(CancelledMsg())
        return
    except EngineBusy as exc:
        out.send(GpuBusyMsg(message=str(exc)))
        return
    except EngineOutOfMemory as exc:
        out.send(GpuMemoryMsg(message=str(exc)))
        return
    except EngineError as exc:
        out.send(ErrorMsg(message=str(exc)))
        return
    out.send(BenchmarkDoneMsg(device=here.device, card=here.card, cpu_threads=res.cpu_threads,
                              benchmark=res.benchmark, tried=res.tried))  # fmt: skip


def detach_std_handles() -> tuple[TextIO, TextIO]:
    """Keep the pipes to the server private to this process and return (protocol in, protocol out).

    os.dup handles are not inherited, so children never see the pipes:
    - stdout carries the protocol only; fd 1 (C libraries too) goes to stderr instead.
    - stdin is read by a thread for "cancel"; children (ffmpeg) get NUL as stdin, because on Windows a
      child that inspects a pipe our thread is blocked reading from hangs until that read returns.
    """
    proto_out = os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8", newline="\n")
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    sys.stdout = sys.stderr
    proto_in = os.fdopen(os.dup(sys.stdin.fileno()), "r", encoding="utf-8")
    nul = os.open(os.devnull, os.O_RDONLY)
    os.dup2(nul, sys.stdin.fileno())
    os.close(nul)
    return proto_in, proto_out


def main() -> int:
    from parakeet_stt.server import winjob

    winjob.contain_self()  # killing this worker also kills its ffmpeg
    stdin, proto = detach_std_handles()
    out = _Out(proto)
    out.send(Hello(pid=os.getpid()))
    line = stdin.readline()
    try:
        spec = parse_spec(line)
    except ValidationError as exc:
        out.send(ErrorMsg(message=f"Internal error: bad job spec ({exc.error_count()} problems)."))
        return 2
    cancel = CancelToken()
    threading.Thread(target=_watch_stdin, args=(stdin, cancel), daemon=True, name="stdin").start()
    here = machine.detect()
    if here.device == "cuda":
        from dataclasses import replace

        from parakeet_stt import gpu

        try:
            gpu.cuda_libraries()  # the graphics-card support's files: a damaged one makes this a processor run
        except gpu.CardFilesError as exc:
            print(f"the graphics-card support is damaged, this run is on the processor: {exc}", file=sys.stderr)
            here = replace(here, device="cpu", reason="no_card_support")
    if here.device == "cpu":  # the processor does all the work: keep the PC usable, and never slowed as background
        machine.run_in_background_mode()
    if isinstance(spec, BenchmarkSpec):
        run_benchmark(spec, out, cancel, here)
    else:
        run(spec, out, cancel, here)
    return 0
