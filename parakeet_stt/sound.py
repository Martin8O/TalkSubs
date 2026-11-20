"""Keep only the sound of a recording: its first audio track, sealed, as an M4A file. The app keeps no pictures.

Used for a dropped video (right after it was copied in) and for a link (straight from the download). The track is
copied unchanged when MP4 can hold it - every sample stays as it was, time 0 included - and otherwise made into
FLAC (lossless from the decoded sound). Fragmented MP4 on a pipe with `delay_moov`, so the encoder delay of the
original stays in the file's edit list and the sound is not shifted.
"""

from __future__ import annotations

import errno
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Final

from parakeet_stt import ffpipe, media
from parakeet_stt.cancel import CancelToken
from parakeet_stt.vault import Role, SealedWriter, Vault

SOUND_FILE: Final = "sound.m4a"  # never a dropped copy's name (those are source.<ext>)
# codecs MP4 holds as they are and ffmpeg decodes back to the very same samples (opus is not: its pre-skip moves)
_COPY: Final = frozenset({"aac", "mp3", "alac", "flac"})
UNKNOWN_BIT_RATE: Final = 512_000  # bits per second assumed for a copied track whose rate the container hides
GUARD_EVERY: Final = 32 * 1024**2  # bytes written between two free-space checks
DISK_FULL: Final = "The disk is full, so the sound could not be kept. Free some space and try again."


def _copyable(codec: str) -> bool:
    return codec in _COPY or codec in ("pcm_s16le", "pcm_s24le", "pcm_f32le")


def command(info: media.MediaInfo) -> list[str]:
    codec = ["-c:a", "copy"] if _copyable(info.audio_codec) else ["-c:a", "flac", "-sample_fmt", "s32"]
    return [
        # -xerror: a read error (a dropped connection) ends ffmpeg with an error, not as if the file had ended there
        media._tool("ffmpeg"), *ffpipe.PROGRESS_ARGS, "-nostdin", "-hide_banner", "-loglevel", "error", "-xerror",
        *info.input_args(), "-map", "0:a:0", "-vn", "-sn", "-dn", *codec,
        "-movflags", "delay_moov+default_base_moof", "-frag_duration", "10000000", "-f", "mp4", "pipe:1",
    ]  # fmt: skip


def estimate_bytes(info: media.MediaInfo) -> int:
    """About how large the kept sound will be: a copied track at its bit rate, FLAC at most 4 bytes a sample."""
    seconds = info.audio_duration_s or info.duration_s
    if _copyable(info.audio_codec):
        return int((info.audio_bit_rate or UNKNOWN_BIT_RATE) / 8 * seconds)
    return int(max(info.sample_rate_hz, 1) * max(info.channels, 1) * 4 * seconds)


def free_bytes(folder: Path) -> int | None:
    """Free space on the drive of `folder` (or of its nearest existing parent); None when it cannot be told."""
    probe = folder
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    try:
        return shutil.disk_usage(probe).free
    except OSError:
        return None


def _disk_full(exc: OSError) -> bool:
    return exc.errno == errno.ENOSPC or getattr(exc, "winerror", None) in (39, 112)  # handle-disk-full, disk-full


class _Sealing:
    def __init__(self, out: SealedWriter, guard: Callable[[], None] | None) -> None:
        self.out = out
        self.guard = guard
        self.since_check = 0

    def feed(self, data: bytes) -> float | None:
        self.out.write(data)
        self.since_check += len(data)
        if self.guard is not None and self.since_check >= GUARD_EVERY:
            self.since_check = 0
            self.guard()  # raises media.MediaError: ffmpeg is stopped and the partial file removed
        return None


def keep(
    info: media.MediaInfo,
    target: Path,
    vault: Vault,
    on_progress: Callable[[float], None] | None = None,
    cancel: CancelToken | None = None,
    *,
    guard: Callable[[], None] | None = None,
) -> int:
    """Write the sound of `info` to `target`, sealed; its plain size. Appears only when complete - and complete
    means as long as the recording says its sound is: a download cut short never passes for the whole lecture.
    `guard` is called every GUARD_EVERY bytes and may stop the writing by raising media.MediaError.
    Raises media.MediaError (one sentence, nothing of the input's address) or Cancelled."""
    reached = [0.0]

    def progress(done_s: float) -> None:
        reached[0] = max(reached[0], done_s)
        if on_progress is not None:
            on_progress(done_s)

    expected = info.audio_duration_s or info.duration_s
    if cancel is not None:
        cancel.raise_if_cancelled()
    try:
        with vault.writer(target, Role.SOURCE) as out:
            size = ffpipe.run(command(info), _Sealing(out, guard), progress, cancel)
            if size == 0:
                raise media.MediaError(f"No sound came out of {info.path.name}.")
            if reached[0] < expected - max(2.0, expected * 0.005):
                raise media.MediaError(
                    f"The sound of {info.path.name} ended after {reached[0] / 60:.0f} of {expected / 60:.0f} minutes: "
                    "the connection was cut. Try again."
                )
    except ffpipe.FfmpegFailed:
        raise media.MediaError(f"The sound of {info.path.name} could not be read completely.") from None
    except OSError as exc:
        if _disk_full(exc):
            raise media.MediaError(DISK_FULL) from None
        raise
    return size
