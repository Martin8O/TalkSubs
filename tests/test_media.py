from __future__ import annotations

from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest

from parakeet_stt import media
from parakeet_stt.cancel import Cancelled, CancelToken
from tests.conftest import MakeClip


def test_probe_video_keeps_exact_frame_rate(make_clip: MakeClip) -> None:
    info = media.probe(make_clip("v.mp4", video_rate="30000/1001"))
    assert info.has_video
    assert info.frame_rate == Fraction(30000, 1001)
    assert info.audio_streams == 1
    assert info.channels == 2
    assert abs(info.duration_s - 3.0) < 0.1


def test_probe_audio_only_has_no_frame_rate(make_clip: MakeClip) -> None:
    info = media.probe(make_clip("a.m4a"))
    assert not info.has_video
    assert info.frame_rate is None


def test_no_audio_track_is_a_readable_error(make_clip: MakeClip) -> None:
    with pytest.raises(media.MediaError, match="no audio track"):
        media.probe(make_clip("silent.mp4", video_rate="25", audio=False))


def test_missing_and_unreadable_files(tmp_path: Path) -> None:
    with pytest.raises(media.MediaError, match="not found"):
        media.probe(tmp_path / "nope.wav")
    junk = tmp_path / "junk.mp4"
    junk.write_bytes(b"not a media file")
    with pytest.raises(media.MediaError, match="Cannot read"):
        media.probe(junk)


def test_decode_is_16k_mono_with_true_progress(make_clip: MakeClip) -> None:
    info = media.probe(make_clip("long.wav", seconds=25.0))
    seen: list[float] = []
    audio = media.decode(info, on_progress=seen.append)
    assert audio.dtype == np.float32
    assert abs(len(audio) / media.SAMPLE_RATE - 25.0) < 0.05
    assert 0.1 < float(np.abs(audio).max()) < 0.2  # lavfi sine amplitude is 1/8
    assert seen == sorted(seen) and len(seen) >= 3
    assert abs(seen[-1] - len(audio) / media.SAMPLE_RATE) < 1e-9


def test_decode_cancel_stops(make_clip: MakeClip) -> None:
    info = media.probe(make_clip("c.wav", seconds=25.0))
    token = CancelToken()

    def stop_after_first(_: float) -> None:
        token.cancel()

    with pytest.raises(Cancelled):
        media.decode(info, on_progress=stop_after_first, cancel=token)


def test_the_ffmpeg_shipped_beside_the_app_comes_before_the_one_on_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from parakeet_stt import env

    monkeypatch.setattr(env, "ROOT", tmp_path)
    assert media._tool("ffprobe") != str(tmp_path / "ffmpeg" / "ffprobe.exe")  # nothing shipped: PATH
    (tmp_path / "ffmpeg").mkdir()
    (tmp_path / "ffmpeg" / "ffprobe.exe").write_bytes(b"")
    assert media._tool("ffprobe") == str(tmp_path / "ffmpeg" / "ffprobe.exe")
