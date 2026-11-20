"""Frame grid and timecodes. Times are integer milliseconds everywhere; frames exist only here.

A cue time is "on the grid" when it is exactly `frame_to_ms(ms_to_frame(t))`, so converting back to frames
at export never moves it.
"""

from __future__ import annotations

from parakeet_stt.transcript import FrameRate


def ms_to_frame(ms: int, fps: FrameRate) -> int:
    """Nearest frame index (half rounds up)."""
    return (2 * ms * fps.num + fps.den * 1000) // (2 * fps.den * 1000)


def frame_to_ms(frame: int, fps: FrameRate) -> int:
    """Start time of a frame, rounded to the nearest millisecond."""
    return (2 * frame * fps.den * 1000 + fps.num) // (2 * fps.num)


def snap(ms: int, fps: FrameRate) -> int:
    return frame_to_ms(ms_to_frame(ms, fps), fps)


def frame_ms(fps: FrameRate) -> float:
    """Length of one frame in milliseconds."""
    return 1000 * fps.den / fps.num


def is_integer_rate(fps: FrameRate) -> bool:
    return fps.num % fps.den == 0


def timecode(ms: int, fps: FrameRate) -> str:
    """HH:MM:SS:FF of the frame nearest to `ms`, in real time.

    HH:MM:SS is the clock second the frame starts in, FF the frame's number inside that second. For integer
    rates (25, 50) this is the usual frame count; for fractional rates (29.97) it never drifts from the clock.
    It is not SMPTE drop-frame timecode.
    """
    frame = ms_to_frame(ms, fps)
    secs = frame * fps.den // fps.num
    first_of_second = -(-secs * fps.num // fps.den)  # ceil
    ff = frame - first_of_second
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}:{ff:02d}"


def parse_rate(text: str) -> FrameRate:
    """'25', '25/1' or '30000/1001'."""
    num, _, den = text.partition("/")
    return FrameRate(num=int(num), den=int(den or 1))
