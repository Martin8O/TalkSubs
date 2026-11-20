from __future__ import annotations

import pytest

from parakeet_stt.timecode import frame_to_ms, ms_to_frame, parse_rate, snap, timecode
from parakeet_stt.transcript import FrameRate

RATES = [FrameRate(num=25, den=1), FrameRate(num=50, den=1), FrameRate(num=30000, den=1001),
         FrameRate(num=24000, den=1001)]  # fmt: skip


@pytest.mark.parametrize("fps", RATES, ids=lambda f: f"{f.num}/{f.den}")
def test_frame_grid_round_trips(fps: FrameRate) -> None:
    for k in range(0, 400_000, 13):
        assert ms_to_frame(frame_to_ms(k, fps), fps) == k
    for ms in range(0, 200_000, 7):
        assert snap(snap(ms, fps), fps) == snap(ms, fps)
        assert abs(snap(ms, fps) - ms) <= 1000 * fps.den / fps.num / 2 + 1


def test_timecodes_count_frames_at_integer_rates() -> None:
    pal = parse_rate("25")
    assert timecode(0, pal) == "00:00:00:00"
    assert timecode(999, pal) == "00:00:01:00"  # nearest frame is the next second's first
    assert timecode(960, pal) == "00:00:00:24"
    assert timecode(3_723_040, pal) == "01:02:03:01"
    assert timecode(1_020, parse_rate("50/1")) == "00:00:01:01"


def test_fractional_rates_follow_the_clock() -> None:
    ntsc = parse_rate("30000/1001")
    assert timecode(frame_to_ms(29, ntsc), ntsc) == "00:00:00:29"
    assert timecode(frame_to_ms(30, ntsc), ntsc) == "00:00:01:00"
    # after an hour the clock part still says one hour (no drift), the frame part stays below 30
    tc = timecode(3_600_000, ntsc)
    assert tc.startswith(("00:59:59", "01:00:00")) and int(tc[-2:]) < 30
    assert timecode(3_601_100, ntsc).startswith("01:00:01")
