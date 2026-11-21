"""Media facts (ffprobe) and decoding to 16 kHz mono float32 (ffmpeg)."""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import cast

import numpy as np
import numpy.typing as npt

from parakeet_stt import ffpipe
from parakeet_stt.blobserve import MediaInput
from parakeet_stt.cancel import Cancelled, CancelToken

SAMPLE_RATE = 16_000
_CHUNK_SECONDS = 10  # one progress update per this many decoded seconds
_TOOL_TIMEOUT_S = 120


class MediaError(Exception):
    """The file cannot be used (no audio, unreadable, ffmpeg missing); the message is for the user."""


@dataclass(frozen=True)
class MediaInfo:
    path: Path
    size_bytes: int
    duration_s: float
    has_video: bool
    frame_rate: Fraction | None  # exact, e.g. 30000/1001; None for audio-only
    audio_streams: int
    sample_rate_hz: int
    channels: int
    audio_start_s: float = 0.0  # where the first audio sample sits on the container's own clock
    container_start_s: float = 0.0  # where that clock's earliest stream starts (ffmpeg moves it to 0 by itself)
    url: str | None = None  # read from this address (a sealed file's server, or a link), never from `path`
    url_options: tuple[str, ...] = ()  # how ffmpeg opens `url`
    audio_codec: str = ""  # ffprobe's name of the first audio track's codec
    audio_duration_s: float = 0.0  # the first audio track's own length, when the container says (else 0)
    audio_bit_rate: int = 0  # the first audio track's bits per second, when the container says (else 0)

    def _input(self) -> MediaInput:
        return MediaInput(self.path, self.url, self.size_bytes, options=self.url_options)

    def input_args(self) -> list[str]:
        """ffmpeg arguments that open this file (`-i` included)."""
        return self._input().input_args()

    def scrub(self, text: str) -> str:
        return self._input().scrub(text)


def _tool(name: str) -> str:
    """The ffmpeg tool shipped beside the app (download package) first, else the one on PATH."""
    from parakeet_stt import env

    bundled = env.ROOT / "ffmpeg" / (f"{name}.exe" if sys.platform == "win32" else name)
    exe = str(bundled) if bundled.is_file() else shutil.which(name)
    if not exe:
        raise MediaError(f"{name} was not found. Install ffmpeg and make sure {name} is on PATH.")
    return exe


def tool_version(name: str = "ffmpeg") -> str:
    out = subprocess.run(
        [_tool(name), "-version"], capture_output=True, text=True, timeout=_TOOL_TIMEOUT_S, check=False
    )
    first = out.stdout.splitlines()[0] if out.stdout else ""
    parts = first.split()
    return parts[2] if len(parts) > 2 and parts[1] == "version" else first


def _rate(text: str | None) -> Fraction | None:
    if not text or "/" not in text:
        return None
    num, den = text.split("/", 1)
    try:
        value = Fraction(int(num), int(den))
    except (ValueError, ZeroDivisionError):
        return None
    return value if value > 0 else None


def _seconds(text: object) -> float:
    try:
        return float(str(text))
    except ValueError:
        return 0.0


def _run_probe(cmd: list[str], cancel: CancelToken | None) -> tuple[int, str, str]:
    """ffprobe, killed at once on a cancel (a link far away can stall it for minutes)."""
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
        **ffpipe.spawn_options(no_window=True),  # type: ignore[arg-type] # the app has no console on Windows
    )  # fmt: skip
    deadline = time.monotonic() + _TOOL_TIMEOUT_S
    while True:
        try:
            out, err = proc.communicate(timeout=0.2)
            return proc.returncode, out, err
        except subprocess.TimeoutExpired:
            if (cancel is not None and cancel.cancelled) or time.monotonic() > deadline:
                proc.kill()
                proc.communicate()
                if cancel is not None and cancel.cancelled:
                    raise Cancelled from None
                raise MediaError("Reading the file took too long.") from None


def probe(src: Path | MediaInput, cancel: CancelToken | None = None) -> MediaInfo:
    """Facts of a media file: a plain path, a sealed file as served by `blobserve.media_input`, or a link."""
    if isinstance(src, Path):
        src = MediaInput(src, None, src.stat().st_size if src.is_file() else 0)
    if src.url is None and not src.path.is_file():
        raise MediaError(f"File not found: {src.path}")
    path = src.path
    cmd = [_tool("ffprobe"), "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
           *src.input_args()]  # fmt: skip
    code, stdout, stderr = _run_probe(cmd, cancel)
    if code != 0:
        raise MediaError(f"Cannot read {path.name}: {src.scrub(stderr.strip()) or 'ffprobe failed'}")
    data = json.loads(stdout or "{}")
    streams = data.get("streams", [])
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    video = [
        s for s in streams if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")
    ]  # cover art in audio files is not video
    if not audio:
        raise MediaError(f"{path.name} has no audio track.")
    duration = float(data.get("format", {}).get("duration") or audio[0].get("duration") or 0.0)
    if duration <= 0:
        raise MediaError(f"{path.name}: duration unknown or zero.")
    frame_rate = None
    if video:
        frame_rate = _rate(video[0].get("avg_frame_rate")) or _rate(video[0].get("r_frame_rate"))
    return MediaInfo(
        path=path,
        size_bytes=src.size_bytes,
        duration_s=duration,
        has_video=bool(video),
        frame_rate=frame_rate,
        audio_streams=len(audio),
        sample_rate_hz=int(audio[0].get("sample_rate") or 0),
        channels=int(audio[0].get("channels") or 0),
        audio_start_s=_seconds(audio[0].get("start_time")),
        container_start_s=_seconds(data.get("format", {}).get("start_time")),
        url=src.url,
        url_options=src.options if src.url else (),
        audio_codec=str(audio[0].get("codec_name") or ""),
        audio_duration_s=_seconds(audio[0].get("duration")),
        audio_bit_rate=_bit_rate(audio[0].get("bit_rate")),
    )


def _bit_rate(text: object) -> int:
    try:
        return max(int(str(text)), 0)
    except ValueError:
        return 0


def decode(
    info: MediaInfo,
    on_progress: Callable[[float], None] | None = None,
    cancel: CancelToken | None = None,
) -> npt.NDArray[np.float32]:
    """First audio track -> 16 kHz mono float32. Progress = seconds of audio actually decoded."""
    cmd = [
        _tool("ffmpeg"), "-nostdin", "-hide_banner", "-loglevel", "error", *info.input_args(),
        "-map", "0:a:0", "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "f32le", "pipe:1",
    ]  # fmt: skip
    # own process group: a console Ctrl+C reaches us (we cancel and kill ffmpeg), not ffmpeg directly
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **ffpipe.spawn_options(own_group=True)  # type: ignore[arg-type]
    )
    assert proc.stdout is not None and proc.stderr is not None
    err_tail: deque[bytes] = deque(maxlen=50)
    stderr = proc.stderr
    drain = threading.Thread(target=lambda: err_tail.extend(stderr), daemon=True)  # never let stderr block ffmpeg
    drain.start()

    capacity = int((info.duration_s + 5) * SAMPLE_RATE)
    buf = np.empty(capacity, dtype=np.float32)
    filled = 0  # samples
    view = memoryview(bytearray(_CHUNK_SECONDS * SAMPLE_RATE * 4))
    stdout = cast(io.BufferedReader, proc.stdout)
    pending = b""
    try:
        while True:
            if cancel is not None and cancel.cancelled:
                raise Cancelled
            n = stdout.readinto(view)
            if not n:
                break
            raw = pending + bytes(view[:n])
            usable = len(raw) - len(raw) % 4
            pending = raw[usable:]
            samples = np.frombuffer(raw[:usable], dtype=np.float32)
            if filled + len(samples) > len(buf):
                buf = np.concatenate([buf[:filled], np.empty(max(len(samples), len(buf) // 4), dtype=np.float32)])
            buf[filled : filled + len(samples)] = samples
            filled += len(samples)
            if on_progress is not None:
                on_progress(filled / SAMPLE_RATE)  # may run past the probed duration on some files
        code = proc.wait(timeout=_TOOL_TIMEOUT_S)
    except BaseException:
        proc.kill()
        proc.wait(timeout=_TOOL_TIMEOUT_S)
        raise
    drain.join(timeout=5)
    if cancel is not None and cancel.cancelled:
        raise Cancelled
    if code != 0:
        msg = info.scrub(b"".join(err_tail).decode("utf-8", "replace").strip())
        raise MediaError(f"Decoding {info.path.name} failed: {msg or f'ffmpeg exit {code}'}")
    if filled == 0:
        raise MediaError(f"{info.path.name}: the audio track decoded to nothing.")
    return buf[:filled].copy() if filled < len(buf) // 2 else buf[:filled]
