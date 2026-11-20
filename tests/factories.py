"""Small valid documents for tests."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from parakeet_stt import presets
from parakeet_stt.transcript import (
    Cue,
    Document,
    FrameRate,
    Media,
    RunConfig,
    Segment,
    SubtitleTrack,
    VadConfig,
    Word,
)


def media(**over: Any) -> Media:
    base: dict[str, Any] = dict(
        source="X:/lectures/talk.mp4", name="talk.mp4", size_bytes=1000, duration_ms=60_000, decoded_ms=60_000,
        has_video=True, frame_rate=FrameRate(num=30000, den=1001), audio_stream_index=0, audio_streams=1,
        sample_rate_hz=48000, channels=2,
    )  # fmt: skip
    base.update(over)
    return Media(**base)


def run_config() -> RunConfig:
    return RunConfig(
        engine="parakeet", model="repo@abc", provider="CUDAExecutionProvider",
        engine_options={"batch_size": 1, "word_end_offset_ms": 80},
        vad=VadConfig(model="silero", threshold=0.5, min_speech_duration_ms=250, min_silence_duration_ms=300,
                      speech_pad_ms=200, max_speech_duration_s=25.0),
        decode_sample_rate_hz=16000, decode_channels=1, versions={"app": "0.1.0"},
        created_utc=datetime(2026, 11, 17, 12, 0, tzinfo=UTC),
    )  # fmt: skip


def document(segments: list[Segment] | None = None) -> Document:
    if segments is None:
        segments = [
            Segment(start_ms=1000, end_ms=4000, words=[
                Word(text="Of", start_ms=1100, end_ms=1200, conf=0.9),
                Word(text="course.", start_ms=1250, end_ms=1700, conf=1.0),
                Word(text="Yes", start_ms=2000, end_ms=2300, conf=None),
            ]),
            Segment(start_ms=4000, end_ms=9000, words=[Word(text="Kyoto?", start_ms=4200, end_ms=4800, conf=0.5)]),
        ]  # fmt: skip
    return Document(app_version="0.1.0", media=media(), run_config=run_config(), segments=segments)


def track(cues: list[Cue] | None = None, fps: FrameRate | None = None, preset_id: str = "cz-template") -> SubtitleTrack:
    """A hand-made track with fixed facts (for golden files: nothing here may depend on the run)."""
    if cues is None:
        cues = [
            Cue(id=1, start_ms=1000, end_ms=3000, lines=["Of course, the mind is open", "like space – naïve café."]),  # noqa: RUF001 - en dash on purpose
            Cue(id=2, start_ms=3080, end_ms=4000, lines=["- Yes."]),
            Cue(id=3, start_ms=4080, end_ms=6040, lines=["=1+1 & <b>bold</b> {brace}"]),
        ]  # fmt: skip
    return SubtitleTrack(
        preset_id=preset_id, preset=presets.get(preset_id), digest="0123456789abcdef", segmenter_version=1,
        frame_rate=fps or FrameRate(num=25, den=1), created_utc=datetime(2026, 11, 17, 12, 0, tzinfo=UTC),
        next_cue_id=max((c.id for c in cues), default=0) + 1, cues=cues,
    )  # fmt: skip
