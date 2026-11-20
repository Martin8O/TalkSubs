"""A browser-friendly preview of a recording: its sound to play, and waveform peaks to draw. Both sealed.

Sound only, also for a video: the app keeps no pictures. Two ffmpeg passes, each on its own pipe, so nothing
plain ever reaches the disk: mono AAC as fragmented MP4 on stdout, sealed as it arrives (a pipe cannot be rewound
for a normal MP4's index); then 8 kHz mono PCM, made into min/max peaks per 10 ms. A sealed source is read through
`blobserve`. Everything is built in a sibling folder and swapped in whole, so a stop or a crash never leaves a
half-made preview that looks finished.

The audio stream is the same one `media.decode` reads (the first), so the preview and the transcript share a
timeline: time 0 is the first audio sample.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

import numpy as np

from parakeet_stt import blobserve, ffpipe, media
from parakeet_stt.cancel import Cancelled, CancelToken
from parakeet_stt.project import write_atomically
from parakeet_stt.vault import Role, SealedWriter, Vault, VaultError

PREVIEW_DIR: Final = "preview"
BUILD_DIR: Final = "preview.part"
INFO_FILE: Final = "preview.json"
PEAKS_FILE: Final = "peaks.bin"
AUDIO_FILE: Final = "audio.m4a"
INFO_VERSION: Final = 3  # 2: sealed, fragmented; 3: sound only (older video previews are made again)

PEAK_RATE_HZ: Final = 8000  # sample rate of the pipe the peaks are made from
PEAKS_PER_S: Final = 100  # one min/max pair per 10 ms
_BLOCK: Final = PEAK_RATE_HZ // PEAKS_PER_S
AUDIO_KBPS: Final = 96
_BYTES_PER_S: Final = (
    AUDIO_KBPS * 1000 // 8 + 1000
)  # with room for the container, so the disk check never under-estimates
_SPARE_BYTES: Final = 1024**3
CUT_SHORT_TRIES: Final = 3  # a preview pass that ends early is made again this often before it fails
CUT_SHORT_SLACK_S: Final = 2.0  # how much shorter than the recording a pass may end (as when keeping a link's sound)
CUT_SHORT_SLACK_SHARE: Final = 0.005
SOUND_SHARE: Final = 0.7  # of the progress bar: the sound pass takes most of the time

Kind = Literal["audio"]


class PreviewError(Exception):
    """The preview cannot be made; the message is for the user."""


class CutShortError(PreviewError):
    """A pass ended before the end of the recording although ffmpeg reported no error."""


@dataclass(frozen=True)
class PreviewInfo:
    kind: Kind
    duration_s: float
    peaks_per_s: int
    media_file: str  # inside the preview folder
    peaks_file: str
    peak_count: int


def folder(job_dir: Path) -> Path:
    return job_dir / PREVIEW_DIR


def read_ready(job_dir: Path, vault: Vault) -> PreviewInfo | None:
    """The finished preview of a job, or None when it is missing or does not hold together."""
    root = folder(job_dir)
    try:
        raw = json.loads(vault.read(root / INFO_FILE, Role.PREVIEW_INFO).decode("utf-8"))
        if raw.get("version") != INFO_VERSION:
            return None
        info = PreviewInfo(
            kind=raw["kind"], duration_s=float(raw["duration_s"]), peaks_per_s=int(raw["peaks_per_s"]),
            media_file=raw["media_file"], peaks_file=raw["peaks_file"], peak_count=int(raw["peak_count"]),
        )  # fmt: skip
        if info.kind != "audio" or info.media_file != AUDIO_FILE or info.peaks_file != PEAKS_FILE:
            return None
        if not (root / info.media_file).is_file() or vault.plain_size(root / info.peaks_file) != info.peak_count * 2:
            return None
    except (OSError, ValueError, KeyError, TypeError, AttributeError, VaultError):
        return None
    return info


def estimate_bytes(duration_s: float) -> int:
    return int(duration_s * _BYTES_PER_S)


def _sound_command(info: media.MediaInfo) -> list[str]:
    """The sound as fragmented MP4 on stdout; progress lines on stderr."""
    return [
        media._tool("ffmpeg"), *ffpipe.PROGRESS_ARGS, "-nostdin", "-hide_banner", "-loglevel", "error",
        *info.input_args(), "-map", "0:a:0", "-vn", "-sn", "-dn",
        # transcript time 0 is the first decoded audio sample: the preview's clock starts there too, whatever the
        # container's own clock says (a transport stream's starts late, and ffmpeg shifts sound-only output itself)
        "-af", "asetpts=PTS-STARTPTS",
        "-c:a", "aac", "-b:a", f"{AUDIO_KBPS}k", "-ac", "1",
        # delay_moov: the header waits for the first fragment, so it carries the encoder's start delay (AAC
        # priming) and the sound is not shifted by it
        "-movflags", "delay_moov+default_base_moof", "-frag_duration", "10000000", "-f", "mp4", "pipe:1",
    ]  # fmt: skip


def _peaks_command(info: media.MediaInfo) -> list[str]:
    return [media._tool("ffmpeg"), "-nostdin", "-hide_banner", "-loglevel", "error", *info.input_args(),
            "-map", "0:a:0", "-vn", "-ac", "1", "-ar", str(PEAK_RATE_HZ), "-f", "s16le", "pipe:1"]  # fmt: skip


def peaks_of(samples: np.ndarray) -> np.ndarray:
    """int16 samples (a whole number of blocks) -> (blocks, 2) int8 [min, max] per block."""
    blocks = samples.reshape(-1, _BLOCK)
    out = np.empty((blocks.shape[0], 2), dtype=np.int8)
    out[:, 0] = blocks.min(axis=1) >> 8
    out[:, 1] = blocks.max(axis=1) >> 8
    return out


def build(
    src: Path,
    job_dir: Path,
    vault: Vault,
    on_progress: Callable[[float, float, float], None] | None = None,
    cancel: CancelToken | None = None,
) -> PreviewInfo:
    """Make `<job_dir>/preview/` for `src`. on_progress(fraction, seconds_done, seconds_total)."""
    if not src.is_file():
        raise PreviewError(f"File not found: {src.name}")
    for attempt in range(1, CUT_SHORT_TRIES + 1):  # a read cut short is made again, from the start
        try:
            with blobserve.media_input(src, vault) as source:
                return _build(media.probe(source), job_dir, vault, on_progress, cancel, source_failed=source.failed)
        except CutShortError:
            if attempt == CUT_SHORT_TRIES:
                raise
        except media.MediaError as exc:
            raise PreviewError(str(exc)) from None
        except VaultError as exc:
            raise PreviewError(f"{src.name}: {exc}") from None
    raise AssertionError("unreachable")


def _build(
    info: media.MediaInfo,
    job_dir: Path,
    vault: Vault,
    on_progress: Callable[[float, float, float], None] | None,
    cancel: CancelToken | None,
    *,
    source_failed: Callable[[], bool],
) -> PreviewInfo:
    root, build_dir = folder(job_dir), job_dir / BUILD_DIR
    _check_space(job_dir, info.duration_s)
    shutil.rmtree(build_dir, ignore_errors=True)
    build_dir.mkdir(parents=True)
    total = info.duration_s
    reached = [0.0]

    def sound_progress(done_s: float) -> None:
        reached[0] = max(reached[0], done_s)
        if on_progress is not None:
            done = min(max(done_s, 0.0), total)
            on_progress(min(SOUND_SHARE * done / total, 0.999), done, total)

    def peaks_progress(done_s: float) -> None:
        if on_progress is not None:
            done = min(done_s, total)
            on_progress(min(SOUND_SHARE + (1 - SOUND_SHARE) * done / total, 0.999), total, total)

    try:
        with vault.writer(build_dir / AUDIO_FILE, Role.PREVIEW_MEDIA) as out:
            size = _pass(_sound_command(info), info, _Copy(out), sound_progress, cancel)
            _whole(info, source_failed)
            _all_of_it(info, reached[0], "listening copy")
        if size == 0:
            raise PreviewError(f"The preview of {info.path.name} could not be made.")
        peaks = _Peaks()
        with vault.writer(build_dir / PEAKS_FILE, Role.PREVIEW_PEAKS) as out:
            peaks.out = out
            _pass(_peaks_command(info), info, peaks, peaks_progress, cancel)
            _whole(info, source_failed)
            peaks.finish()
            _all_of_it(info, peaks.samples / PEAK_RATE_HZ, "waveform")
        if peaks.blocks == 0:
            raise PreviewError(f"{info.path.name}: the audio track decoded to nothing.")
        meta = PreviewInfo("audio", info.duration_s, PEAKS_PER_S, AUDIO_FILE, PEAKS_FILE, peaks.blocks)
        text = json.dumps({
            "version": INFO_VERSION, "kind": meta.kind, "duration_s": meta.duration_s,
            "peaks_per_s": meta.peaks_per_s, "media_file": meta.media_file,
            "peaks_file": meta.peaks_file, "peak_count": meta.peak_count,
        }, indent=1) + "\n"  # fmt: skip
        write_atomically({build_dir / INFO_FILE: vault.seal(text.encode("utf-8"), Role.PREVIEW_INFO)})
        if cancel is not None and cancel.cancelled:
            raise Cancelled
        shutil.rmtree(root, ignore_errors=True)
        _swap_in(build_dir, root)
    except BaseException:
        shutil.rmtree(build_dir, ignore_errors=True)
        raise
    return meta


def _pass(
    cmd: list[str],
    info: media.MediaInfo,
    sink: ffpipe.Sink,
    on_progress: Callable[[float], None],
    cancel: CancelToken | None,
) -> int:
    try:
        return ffpipe.run(cmd, sink, on_progress, cancel)
    except ffpipe.FfmpegFailed as exc:
        raise PreviewError(f"The preview of {info.path.name} could not be made: {info.scrub(exc.detail)}") from None


def _all_of_it(info: media.MediaInfo, reached_s: float, what: str) -> None:
    """A pass that ended early is refused, never kept: the player would take the short length while the waveform
    and the subtitles use the whole one, and the play cursor would run ahead of the sound."""
    expected = info.audio_duration_s or info.duration_s
    if reached_s < expected - max(CUT_SHORT_SLACK_S, expected * CUT_SHORT_SLACK_SHARE):
        raise CutShortError(
            f"The {what} of {info.path.name} ended after {reached_s / 60:.0f} of {expected / 60:.0f} minutes."
        )


def _whole(info: media.MediaInfo, source_failed: Callable[[], bool]) -> None:
    if source_failed():  # ffmpeg saw an early end of a damaged sealed file and may have exited "cleanly"
        raise PreviewError(f"{info.path.name} could not be read completely: the app's copy of it is damaged.")


class _Copy:
    """The sound pass's sink: its bytes, sealed as they come."""

    def __init__(self, out: SealedWriter) -> None:
        self.out = out

    def feed(self, data: bytes) -> float | None:
        self.out.write(data)
        return None  # its progress comes from ffmpeg's own report


class _Peaks:
    """The peaks pass's sink: 8 kHz int16 PCM -> min/max peaks per block, sealed as they come."""

    def __init__(self) -> None:
        self.out: SealedWriter | None = None
        self.pending = b""  # bytes of a sample or of a block not complete yet
        self.samples = 0
        self.blocks = 0

    def feed(self, data: bytes) -> float:
        assert self.out is not None
        raw = self.pending + data
        whole = len(raw) - len(raw) % (_BLOCK * 2)
        self.pending = raw[whole:]
        self.samples += len(data) // 2
        if whole:
            chunk = peaks_of(np.frombuffer(raw[:whole], dtype="<i2"))
            self.out.write(chunk.tobytes())
            self.blocks += chunk.shape[0]
        return self.samples / PEAK_RATE_HZ

    def finish(self) -> None:
        assert self.out is not None
        tail = self.pending[: len(self.pending) - len(self.pending) % 2]
        if tail:  # the last, shorter block
            last = np.frombuffer(tail, dtype="<i2")
            self.out.write(np.array([[last.min() >> 8, last.max() >> 8]], dtype=np.int8).tobytes())
            self.blocks += 1


def _swap_in(build_dir: Path, root: Path) -> None:
    """Rename the finished folder into place; a virus scanner or indexer holding a file for a moment is retried."""
    last: OSError | None = None
    for _ in range(10):
        shutil.rmtree(root, ignore_errors=True)
        try:
            os.replace(build_dir, root)
            return
        except OSError as exc:
            last = exc
            time.sleep(0.5)
    raise PreviewError(f"The preview could not be saved: {last}")


def _check_space(job_dir: Path, duration_s: float) -> None:
    probe = job_dir
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    try:
        free = shutil.disk_usage(probe).free
    except OSError:
        return
    need = estimate_bytes(duration_s) + _SPARE_BYTES
    if free < need:
        raise PreviewError(
            f"Not enough free disk space for the preview: about {need // 1024**2} MB needed, {free // 1024**2} MB free."
        )
