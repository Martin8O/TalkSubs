"""File in, transcript document out: load engine -> decode -> speech windows -> recognition -> document.

Speech windows are found completely before recognition starts: interleaving the CPU speech detector with the
GPU model made both ~3x slower (measured: 40 s vs 12 s for a 22-minute lecture).

Recognition runs in slices of windows, always (the CLI and the app take the same path). With a checkpoint, the
windows and every finished slice are saved, and a run of the same job that was interrupted goes on from there.
"""

from __future__ import annotations

import dataclasses
import platform
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from fractions import Fraction
from importlib import metadata
from pathlib import Path
from typing import Literal

import numpy as np
import numpy.typing as npt

from parakeet_stt import __version__, engines, media, models
from parakeet_stt import checkpoint as ckpt
from parakeet_stt.blobserve import MediaInput
from parakeet_stt.cancel import Cancelled, CancelToken
from parakeet_stt.engines.base import Engine, EngineOutOfMemory, TimedSegment, TimedWord
from parakeet_stt.transcript import Document, FrameRate, Media, RunConfig, Segment, VadConfig, Word
from parakeet_stt.vad import VAD_MODEL, JoinStats, Vad, VadOptions, Window

Phase = Literal["load", "decode", "speech", "recognise", "done"]
# Share of the whole job per phase, from measured cost on a 2.2 h lecture.
PHASE_SHARE: dict[Phase, tuple[float, float]] = {
    "load": (0.0, 0.05),
    "decode": (0.05, 0.10),
    "speech": (0.10, 0.30),
    "recognise": (0.30, 0.99),  # writing the document is the last 1 %
    "done": (1.0, 1.0),
}
# Windows per slice: even, so batch-2 pairs are the same as in one long run. ~32 windows = a few minutes of audio.
SLICE_WINDOWS = 32
# Save the progress at most this often (seconds of work). On the GPU 20 s were a quarter of a 3 h lecture; one
# save of a 5 h lecture's words takes well under a second.
CHECKPOINT_EVERY_S = 5.0
MIN_WORD_MS = 80  # one Parakeet frame: a word is never shorter than the grid it was heard on
_PHASE_CAP = 0.999  # a phase reaches 1.0 only when it has really finished


@dataclass(frozen=True)
class Progress:
    phase: Phase
    fraction: float  # of the whole job, 0..1, never decreasing
    media_done_s: float
    media_total_s: float


ProgressCallback = Callable[[Progress], None]


@dataclass
class RunStats:
    joins: JoinStats = field(default_factory=JoinStats)
    words_dropped_at_joins: int = 0  # heard by both windows of a join, kept once
    resumed: bool = False  # went on from an earlier run's progress


@dataclass(frozen=True)
class Result:
    document: Document
    stats: RunStats


class _Reporter:
    def __init__(self, on_progress: ProgressCallback | None, total_s: float) -> None:
        self.on_progress = on_progress
        self.total_s = total_s

    def __call__(self, phase: Phase, phase_fraction: float, media_done_s: float = 0.0) -> None:
        if self.on_progress is None:
            return
        lo, hi = PHASE_SHARE[phase]
        f = min(max(phase_fraction, 0.0), 1.0)
        self.on_progress(Progress(phase, lo + (hi - lo) * f, min(media_done_s, self.total_s), self.total_s))


def transcribe_file(
    path: Path,
    *,
    engine: Engine | None = None,
    engine_name: str = engines.DEFAULT,
    engine_options: dict[str, object] | None = None,
    vad_options: VadOptions | None = None,
    on_progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    source: MediaInput | None = None,
    picture: tuple[bool, Fraction | None] | None = None,
    checkpoint: ckpt.Checkpoint | None = None,
) -> Result:
    """Transcribe one file. A passed-in `engine` is used as is and left loaded; otherwise one is created and closed.
    `source` says how to read a sealed file (`blobserve.media_input`); it must stay open for the whole call.
    `picture` = (had video, frame rate) of the original when only its sound was kept.
    `checkpoint`: where this job's progress is kept; an earlier run's progress is used when it matches."""
    cancel = cancel or CancelToken()
    info = media.probe(source or path.resolve())
    if picture is not None:  # the sound of a video, kept alone: the subtitles keep the video's frame grid
        info = dataclasses.replace(info, has_video=picture[0], frame_rate=picture[1])
    total = info.duration_s
    report = _Reporter(on_progress, total)
    report("load", 0.0)
    own_engine = engine is None
    eng = engine or engines.create(engine_name, **(engine_options or {}))
    try:
        eng.load()
        cancel.raise_if_cancelled()
        report("load", 1.0)
        report("decode", 0.0)
        audio = media.decode(info, lambda s: report("decode", min(s / total, _PHASE_CAP), s), cancel)
        if source is not None and source.failed():  # an early end of a damaged sealed file is not the end
            raise media.MediaError(f"{path.name} could not be read completely: the app's copy of it is damaged.")
        decoded_s = len(audio) / media.SAMPLE_RATE
        report("decode", 1.0, decoded_s)
        vad = Vad(vad_options)
        key = resume_key(eng, vad.options, audio) if checkpoint is not None else {}
        saved = checkpoint.load(key, len(audio)) if checkpoint is not None else None
        windows, joins = _speech(vad, audio, decoded_s, report, cancel, saved)
        timed = list(saved.done) if saved is not None else []
        if checkpoint is not None and saved is None:
            checkpoint.save(key, windows, joins, timed)
        stats = RunStats(joins=joins, resumed=saved is not None)
        if timed:  # only the resume point: a first report at 0 would make it the base of the time estimate
            done_s = timed[-1].window.end_s
            report("recognise", min(done_s / decoded_s, _PHASE_CAP), done_s)
        else:
            report("recognise", 0.0)
        timed = _recognise(eng, audio, windows, timed, decoded_s, report, cancel, checkpoint, key, joins)
        cancel.raise_if_cancelled()
        segments = build_segments(timed, stats)
        doc = Document(
            app_version=__version__,
            media=_media(info, decoded_s),
            run_config=_run_config(eng, vad.options),
            segments=segments,
        )
        report("recognise", 1.0, total)
        report("done", 1.0, total)
        return Result(doc, stats)
    finally:
        if own_engine:
            eng.close()


def _speech(  # noqa: PLR0917 - the one call site above
    vad: Vad,
    audio: npt.NDArray[np.float32],
    decoded_s: float,
    report: _Reporter,
    cancel: CancelToken,
    saved: ckpt.Saved | None,
) -> tuple[list[Window], JoinStats]:
    """The speech windows: found now, or those of the interrupted run (the same sound, code and settings)."""
    report("speech", 0.0)
    if saved is not None:
        windows, joins = saved.windows, saved.joins
    else:
        windows = []
        for w in vad.windows(audio):
            windows.append(w)
            report("speech", min(w.end_s / decoded_s, _PHASE_CAP), w.end_s)
            cancel.raise_if_cancelled()
        joins = vad.stats
    report("speech", 1.0, decoded_s)
    return windows, joins


def resume_key(eng: Engine, vad: VadOptions, audio: npt.NDArray[np.float32]) -> dict[str, object]:
    """Everything that decides the windows and the words: a checkpoint is used only when all of it is equal."""
    d = eng.describe()
    return {
        "app": __version__,
        "code": ckpt.code_digest(),
        "engine": [d.engine, d.model, d.provider],
        "engine_options": dict(d.options),
        "engine_versions": dict(d.versions),
        "vad_model": models.VAD.pinned,
        "vad": vad.as_dict(),
        "samples": len(audio),
        "audio": ckpt.audio_digest(audio),
    }


def _recognise(  # noqa: PLR0917 - the one call site above
    eng: Engine,
    audio: npt.NDArray[np.float32],
    windows: list[Window],
    timed: list[TimedSegment],
    decoded_s: float,
    report: _Reporter,
    cancel: CancelToken,
    checkpoint: ckpt.Checkpoint | None,
    key: dict[str, object],
    joins: JoinStats,
) -> list[TimedSegment]:
    """Recognise the windows not in `timed` yet, a slice at a time; keep the progress after a slice, and once more
    when the run is stopped (closing the app, a cancel, a full GPU memory), so the next run loses nothing."""
    check_device = getattr(eng, "check_device", None)  # engines that can be moved off the GPU say so
    last_save, saved = time.monotonic(), len(timed)
    try:
        for i in range(len(timed), len(windows), SLICE_WINDOWS):
            part = eng.transcribe(
                audio,
                windows[i : i + SLICE_WINDOWS],
                lambda s: report("recognise", min(s / decoded_s, _PHASE_CAP), s),
                cancel,
            )
            if check_device is not None:  # a guard: a slice made off the GPU would not be kept
                check_device()
            timed.extend(part)  # whole slices only: a slice cut short is recognised again
            cancel.raise_if_cancelled()
            last = i + SLICE_WINDOWS >= len(windows)
            if checkpoint is not None and not last and time.monotonic() - last_save >= CHECKPOINT_EVERY_S:
                checkpoint.save(key, windows, joins, timed)
                last_save, saved = time.monotonic(), len(timed)
    except (Cancelled, EngineOutOfMemory):
        if checkpoint is not None and len(timed) > saved:
            checkpoint.save(key, windows, joins, timed)
        raise
    return timed


def _ms(seconds: float) -> int:
    return round(seconds * 1000)


def _midpoint_ms(w: TimedWord) -> int:
    """Where a word sits, for deciding which window owns it (its end is never taken as before its start)."""
    start = _ms(w.start_s)
    return (start + max(_ms(w.end_s), start + MIN_WORD_MS)) // 2


def build_segments(timed: list[TimedSegment], stats: RunStats | None = None) -> list[Segment]:
    """Engine output -> contract segments: each window keeps the words it owns, times clamped to its span."""
    out: list[Segment] = []
    kept = [[w for w in ts.words if _owns(ts.window, w)] for ts in timed]
    dropped = sum(len(ts.words) - len(k) for ts, k in zip(timed, kept, strict=True))
    for i in range(1, len(timed)):
        dropped += _drop_edge_echo(timed[i - 1].window, kept[i - 1], timed[i].window, kept[i])
    if stats is not None:
        stats.words_dropped_at_joins += dropped
    for ts, owned in zip(timed, kept, strict=True):
        lo, hi = _ms(ts.window.own_start_s), _ms(ts.window.own_end_s)
        words: list[Word] = []
        for i, w in enumerate(owned):
            start = min(max(_ms(w.start_s), lo), hi)
            if words and start < words[-1].start_ms:
                start = words[-1].start_ms
            nxt = _ms(owned[i + 1].start_s) if i + 1 < len(owned) else hi
            end = min(max(_ms(w.end_s), start + MIN_WORD_MS), max(nxt, start), hi)
            words.append(Word(text=w.text, start_ms=start, end_ms=max(end, start), conf=w.conf))
        out.append(Segment(start_ms=lo, end_ms=hi, words=words))
    return out


def _owns(window: Window, w: TimedWord) -> bool:
    return _ms(window.own_start_s) <= _midpoint_ms(w) < _ms(window.own_end_s)


def _norm(text: str) -> str:
    return "".join(ch for ch in text.lower() if ch.isalnum())


def _drop_edge_echo(a: Window, a_words: list[TimedWord], b: Window, b_words: list[TimedWord]) -> int:
    """At a join, a window that hears only the first moment of a word at its very edge may still emit the whole
    word, timed just before the boundary, while the next window hears it fully. Keep the copy heard fully."""
    if a.own_end != b.own_start or not a_words or not b_words:
        return 0
    x, y = a_words[-1], b_words[0]
    if _norm(x.text) != _norm(y.text):
        return 0
    reach = a.end_s - a.own_end_s  # how far each window hears past the boundary
    x_at_edge = a.end_s - x.end_s < reach
    y_at_edge = y.start_s - b.start_s < reach
    if x_at_edge and not y_at_edge:
        a_words.pop()
        return 1
    if y_at_edge and not x_at_edge:
        b_words.pop(0)
        return 1
    return 0


def _media(info: media.MediaInfo, decoded_s: float) -> Media:
    rate = info.frame_rate
    return Media(
        source=str(info.path.resolve()),
        name=info.path.name,
        size_bytes=info.size_bytes,
        duration_ms=_ms(info.duration_s),
        decoded_ms=_ms(decoded_s),
        has_video=info.has_video,
        frame_rate=FrameRate(num=rate.numerator, den=rate.denominator) if rate else None,
        audio_stream_index=0,
        audio_streams=info.audio_streams,
        sample_rate_hz=info.sample_rate_hz,
        channels=info.channels,
    )


def _run_config(eng: Engine, vad: VadOptions) -> RunConfig:
    d = eng.describe()
    versions = {
        "app": __version__,
        "git": _git_commit(),
        "python": platform.python_version(),
        "numpy": metadata.version("numpy"),
        "pydantic": metadata.version("pydantic"),
        "ffmpeg": media.tool_version("ffmpeg"),
        "vad_model": models.VAD.pinned,
        **d.versions,
    }
    return RunConfig(
        engine=d.engine,
        model=d.model,
        provider=d.provider,
        engine_options=dict(d.options),
        vad=VadConfig(
            model=VAD_MODEL,
            threshold=vad.threshold,
            min_speech_duration_ms=vad.min_speech_duration_ms,
            min_silence_duration_ms=vad.min_silence_duration_ms,
            speech_pad_ms=vad.speech_pad_ms,
            max_speech_duration_s=vad.max_speech_duration_s,
        ),
        decode_sample_rate_hz=media.SAMPLE_RATE,
        decode_channels=1,
        versions=versions,
        created_utc=datetime.now(UTC),
    )


def _git_commit() -> str:
    root = Path(__file__).resolve().parent.parent
    if not (root / ".git").exists():  # a download package names the commit it was built from
        build = root / "BUILD"
        return build.read_text(encoding="utf-8").strip() or "none" if build.is_file() else "none"
    try:
        head = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"],
            capture_output=True, text=True, timeout=20, check=False,
        ).stdout.strip()  # fmt: skip
    except OSError:
        return "unknown"
    return f"{head}{'-dirty' if dirty else ''}" if head else "unknown"
