"""The transcript document: the one contract shared by the pipeline, the project file, the exporters,
the server and the editor (whose TypeScript types are generated from these models).

Rules for changing it:
- Every change of shape, additive ones included, bumps SCHEMA_VERSION and adds a dict-level migration
  from the previous version to `parakeet_stt.project.MIGRATIONS`, so older project files keep opening.
- Field names are snake_case with no aliases; times are integer milliseconds of media time. Subtitle cue
  times lie on their track's frame grid (`parakeet_stt.timecode`), still stored as milliseconds.
- `segments` and their `words` are what the engine heard. They are never edited: user edits live in the
  subtitle tracks, whose cues carry their own text and times. A word belongs to the cue with the latest
  start at or before the word's start - no stored links, so editing a cue never breaks a reference.
- The contract checks integrity only (times ordered and inside the media, unique ids). Subtitle rules -
  overlap, gaps, lengths, empty text - belong to the rule checker, so an editor can save a half-done edit.
"""

from __future__ import annotations

from typing import Final, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from parakeet_stt.presets import Preset

SCHEMA_VERSION: Final = 3  # 3: a cue may carry its translation; a track names the translation's language


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Word(_Model):
    text: str = Field(min_length=1)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    conf: float | None = Field(default=None, ge=0.0, le=1.0, description="Engine confidence 0..1, if it gives one")

    @model_validator(mode="after")
    def _ordered(self) -> Word:
        if self.end_ms < self.start_ms:
            raise ValueError(f"word {self.text!r} ends ({self.end_ms}) before it starts ({self.start_ms})")
        return self


class Segment(_Model):
    """One stretch of speech as the engine recognised it (a pause or the window length bounds it)."""

    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    words: list[Word]

    @model_validator(mode="after")
    def _words_inside(self) -> Segment:
        if self.end_ms < self.start_ms:
            raise ValueError(f"segment ends ({self.end_ms}) before it starts ({self.start_ms})")
        prev = self.start_ms
        for w in self.words:
            if w.start_ms < prev:
                raise ValueError(f"word {w.text!r} at {w.start_ms} is out of order or before its segment")
            if w.end_ms > self.end_ms:
                raise ValueError(f"word {w.text!r} ends at {w.end_ms}, after its segment ({self.end_ms})")
            prev = w.start_ms
        return self


class FrameRate(_Model):
    """Exact video frame rate as a fraction, e.g. 30000/1001."""

    num: int = Field(gt=0)
    den: int = Field(gt=0)


class Media(_Model):
    source: str = Field(description="Absolute path of the media file when it was transcribed")
    name: str
    size_bytes: int = Field(ge=0)
    duration_ms: int = Field(ge=0, description="Container duration reported by ffprobe")
    decoded_ms: int = Field(ge=0, description="Length of the decoded audio actually recognised")
    has_video: bool
    frame_rate: FrameRate | None = Field(description="None for audio-only files")
    audio_stream_index: int = Field(ge=0, description="Which audio track was used (0 = first)")
    audio_streams: int = Field(ge=1)
    sample_rate_hz: int = Field(ge=0, description="Original sample rate of that track")
    channels: int = Field(ge=0, description="Original channel count of that track")


class VadConfig(_Model):
    model: str
    threshold: float
    min_speech_duration_ms: int
    min_silence_duration_ms: int
    speech_pad_ms: int
    max_speech_duration_s: float


class RunConfig(_Model):
    engine: str
    model: str = Field(description="Model repository and exact revision")
    provider: str = Field(description="Execution provider the model actually ran on")
    engine_options: dict[str, str | int | float | bool] = Field(description="Engine-specific settings that were used")
    vad: VadConfig
    decode_sample_rate_hz: int
    decode_channels: int
    versions: dict[str, str] = Field(description="App, git commit, libraries, tools, GPU and driver")
    created_utc: AwareDatetime


class Cue(_Model):
    """One subtitle: its own text (one string per line) and times on the track's frame grid."""

    id: int = Field(ge=1, description="Stable within its track; new cues take the track's next_cue_id")
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    lines: list[str]
    translation: list[str] | None = Field(
        default=None, description="The cue's translation, one string per line; None while not translated"
    )

    @field_validator("translation")
    @classmethod
    def _blank_is_none(cls, v: list[str] | None) -> list[str] | None:
        # a cleared translation is no translation (it must not count as translated, nor change the fingerprint)
        return v if v is not None and any(line.strip() for line in v) else None

    @model_validator(mode="after")
    def _ordered(self) -> Cue:
        if self.end_ms <= self.start_ms:
            raise ValueError(f"cue {self.id} ends ({self.end_ms}) at or before its start ({self.start_ms})")
        return self


class SubtitleTrack(_Model):
    """The cues one preset produced (and the user may have edited)."""

    preset_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    preset: Preset = Field(description="The rule values used, kept so the track reads the same if the preset changes")
    digest: str = Field(description="Fingerprint of the preset, its word lists and the segmenter version")
    segmenter_version: int = Field(ge=1)
    frame_rate: FrameRate = Field(description="The frame grid of the cue times")
    created_utc: AwareDatetime
    edited: bool = Field(default=False, description="True once a person changed a cue; never re-segmented silently")
    next_cue_id: int = Field(ge=1, description="The id the next new cue gets; ids are never reused after a delete")
    translation_language: str | None = Field(
        default=None, pattern=r"^[a-z]{2,3}$", description="Language code of the cues' translations (e.g. cs)"
    )
    cues: list[Cue]

    @model_validator(mode="after")
    def _cues_ordered(self) -> SubtitleTrack:
        prev, ids = 0, set()
        for c in self.cues:
            if c.start_ms < prev:
                raise ValueError(f"cue {c.id} starts at {c.start_ms}, before the previous cue's start ({prev})")
            if c.id in ids:
                raise ValueError(f"cue id {c.id} is used twice")
            prev = c.start_ms
            ids.add(c.id)
        if ids and self.next_cue_id <= max(ids):
            raise ValueError(f"next_cue_id {self.next_cue_id} is not above the highest cue id {max(ids)}")
        if self.preset_id != self.preset.id:
            raise ValueError(f"track preset_id {self.preset_id!r} differs from its preset snapshot {self.preset.id!r}")
        return self


class Document(_Model):
    schema_version: Literal[3] = SCHEMA_VERSION
    app_version: str
    media: Media
    run_config: RunConfig
    segments: list[Segment]
    subtitles: list[SubtitleTrack] = Field(default_factory=list, description="At most one track per preset")

    @model_validator(mode="after")
    def _segments_ordered(self) -> Document:
        prev_end = 0
        for s in self.segments:
            if s.start_ms < prev_end:
                raise ValueError(f"segment at {s.start_ms} overlaps the previous one (ends {prev_end})")
            prev_end = s.end_ms
        if prev_end > self.media.decoded_ms:
            raise ValueError(f"segments run to {prev_end}, past the decoded audio ({self.media.decoded_ms})")
        seen = set()
        for track in self.subtitles:
            if track.preset_id in seen:
                raise ValueError(f"two subtitle tracks for preset {track.preset_id!r}")
            seen.add(track.preset_id)
            for c in track.cues:
                if c.end_ms > self.media.decoded_ms:
                    raise ValueError(f"cue {c.id} ends at {c.end_ms}, past the media ({self.media.decoded_ms})")
        return self

    def words(self) -> list[Word]:
        return [w for s in self.segments for w in s.words]

    def track(self, preset_id: str) -> SubtitleTrack | None:
        return next((t for t in self.subtitles if t.preset_id == preset_id), None)

    def with_track(self, track: SubtitleTrack) -> Document:
        """This document with `track` added, or replacing the one of the same preset."""
        others = [t for t in self.subtitles if t.preset_id != track.preset_id]
        fields = {name: getattr(self, name) for name in type(self).model_fields}
        return type(self)(**{**fields, "subtitles": [*others, track]})  # validated, unlike model_copy
