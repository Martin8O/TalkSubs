"""One ffmpeg pass whose output arrives on stdout: handed to a sink as it comes, progress from ffmpeg's own report.

Shared by the preview maker and the link fetch. A cancel kills ffmpeg at once, also while it waits for the network
and writes nothing, so a stop never hangs on a stalled download.
"""

from __future__ import annotations

import io
import re
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Final, Protocol, cast

from parakeet_stt.cancel import Cancelled, CancelToken

PIPE_READ: Final = 256 * 1024
PRIORITY: Final = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)


def spawn_options(*, own_group: bool = False, no_window: bool = False, low_priority: bool = False) -> dict[str, object]:
    """`Popen` options for a child ffmpeg/ffprobe, the same on Windows (creation flags) and elsewhere (a session of its
    own, so a console Ctrl+C reaches us and not the child; the priority is left alone)."""
    if sys.platform == "win32":
        flags = 0
        if own_group:
            flags |= subprocess.CREATE_NEW_PROCESS_GROUP
        if no_window:
            flags |= subprocess.CREATE_NO_WINDOW
        if low_priority:
            flags |= PRIORITY
        return {"creationflags": flags}
    return {"start_new_session": True} if own_group else {}


TIMEOUT_S: Final = 120
PROGRESS_ARGS: Final = ("-nostats", "-progress", "pipe:2")  # put right after the ffmpeg executable
_PROGRESS_LINE = re.compile(r"^[a-z0-9_]+=")  # ffmpeg's -progress output (values may be padded), not an error


class Sink(Protocol):
    def feed(self, data: bytes) -> float | None:
        """Take a piece of the output; may return seconds of media done (else ffmpeg's report is used)."""


class FfmpegFailed(Exception):
    """ffmpeg exited with an error; `.detail` holds its last lines (unscrubbed: the caller scrubs and words it)."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


def run(
    cmd: list[str],
    sink: Sink,
    on_progress: Callable[[float], None],
    cancel: CancelToken | None,
) -> int:
    """Run ffmpeg, feed its stdout to `sink`; the bytes it produced. Raises Cancelled or FfmpegFailed."""
    # own process group (a console Ctrl+C reaches us, not ffmpeg) and below-normal priority (the GPU job's
    # feeder keeps its CPU)
    proc = subprocess.Popen(
        cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        **spawn_options(own_group=True, no_window=True, low_priority=True),  # type: ignore[arg-type]
    )  # fmt: skip
    assert proc.stdout is not None and proc.stderr is not None
    err_tail: deque[str] = deque(maxlen=50)
    stderr = proc.stderr
    finished = threading.Event()

    def drain() -> None:
        for raw in stderr:
            line = raw.decode("utf-8", "replace").strip()
            if line.startswith("out_time_us=") and line[12:].isdigit():
                on_progress(int(line[12:]) / 1e6)
            elif line and not _PROGRESS_LINE.match(line):
                err_tail.append(line)

    def watch() -> None:
        while not finished.wait(0.2):
            if cancel is not None and cancel.cancelled:
                proc.kill()  # a read blocked on a silent pipe returns at once
                return

    drainer = threading.Thread(target=drain, daemon=True, name="ffmpeg-stderr")
    watcher = threading.Thread(target=watch, daemon=True, name="ffmpeg-cancel")
    drainer.start()
    watcher.start()
    stdout = cast(io.BufferedReader, proc.stdout)
    view = memoryview(bytearray(PIPE_READ))
    produced = 0
    try:
        while True:
            if cancel is not None and cancel.cancelled:
                raise Cancelled
            n = stdout.readinto(view)
            if not n:
                break
            produced += n
            done = sink.feed(bytes(view[:n]))
            if done is not None:
                on_progress(done)
        code = _wait(proc, cancel)
    except BaseException:
        proc.kill()
        proc.wait(timeout=TIMEOUT_S)
        raise
    finally:
        finished.set()
        drainer.join(timeout=5)
        watcher.join(timeout=5)
    if cancel is not None and cancel.cancelled:
        raise Cancelled
    if code != 0:
        raise FfmpegFailed("\n".join(err_tail).strip() or f"ffmpeg exit {code}")
    return produced


def _wait(proc: subprocess.Popen[bytes], cancel: CancelToken | None) -> int:
    """Wait for ffmpeg to finish writing, still answering a cancel."""
    deadline = time.monotonic() + TIMEOUT_S
    while True:
        try:
            return proc.wait(timeout=0.2)
        except subprocess.TimeoutExpired:
            if cancel is not None and cancel.cancelled:
                raise Cancelled from None
            if time.monotonic() > deadline:
                raise FfmpegFailed("ffmpeg did not finish writing.") from None
