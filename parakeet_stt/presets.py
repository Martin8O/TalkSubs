"""Subtitle rule presets and language word lists: data in `presets/*.toml`, typed here.

Adding a preset means adding a TOML file; its `id` must equal the file name. Every number the segmenter
and the rule checker use comes from a preset.

These models are part of the transcript contract: every subtitle track stores a snapshot of its preset.
A new preset field therefore needs a default (so stored snapshots still load), or a schema version bump
with a migration that fills it in.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from parakeet_stt.glossary import Glossary

import hashlib
import json
import re
import tomllib
from collections.abc import Sequence
from functools import cache
from pathlib import Path
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

PRESETS_DIR: Final = Path(__file__).resolve().parent.parent / "presets"
LANG_DIR: Final = PRESETS_DIR / "lang"
STANDARDS_DIR: Final = PRESETS_DIR / "standards"  # starting points for the user's own types, never used as they are
CUSTOM_ID: Final = r"^my-[a-z0-9]+(-[a-z0-9]+)*$"  # the user's own types; minted once, never from the name
MIN_SPAN_MS: Final = 200  # between a type's shortest and longest subtitle, and after a lead-in: a frame or more
DEFAULT: Final = "cz-template"
# Bump when the segmenter's behaviour changes, so stored tracks are recognised as stale.
SEGMENTER_VERSION: Final = 2  # 2: cue words tidied (fillers, window capitals), tidy.py

Severity = Literal["hard", "soft"]


class _Data(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Limits(_Data):
    # the bounds keep a type the user makes within what the segmenter and the checker were built for
    max_chars_per_line: int = Field(ge=10, le=100)
    max_lines: int = Field(ge=1, le=2)
    max_cps: float = Field(ge=5, le=40)
    cps_counts_spaces: bool
    min_duration_ms: int = Field(ge=0, le=10_000)
    max_duration_ms: int = Field(ge=1000, le=20_000)
    min_gap_frames: int = Field(ge=0, le=25)
    close_gaps_up_to_frames: int = Field(ge=0, le=50)
    default_fps: str = Field(pattern=r"^\d+(/\d+)?$", description="Frame grid for audio-only media, e.g. 25/1")

    @model_validator(mode="after")
    def _consistent(self) -> Limits:
        if self.min_duration_ms > self.max_duration_ms - MIN_SPAN_MS:
            raise ValueError("min_duration_ms must stay at least 0.2 s below max_duration_ms")
        if self.close_gaps_up_to_frames and self.close_gaps_up_to_frames <= self.min_gap_frames:
            raise ValueError("close_gaps_up_to_frames must be above min_gap_frames (or 0 to switch it off)")
        return self


class Timing(_Data):
    lead_in_ms: int = Field(ge=0, le=2000)
    end_hold_ms: int = Field(ge=0, le=5000)


class SegmenterWeights(_Data):
    block_pause_ms: int = Field(gt=0)
    pause_hint_ms: int = Field(gt=0)
    cost_sentence_end: float
    cost_clause: float
    cost_pause: float
    cost_before_conjunction: float
    cost_plain: float
    cost_forbidden: float
    cost_per_cue: float
    cost_cps_over: float
    cost_too_short: float
    cost_sentence_piece: float
    sentence_piece_chars: int = Field(ge=0)
    cost_line_imbalance: float
    cost_top_heavy: float
    cost_forced: float


class Severities(_Data):
    chars_per_line: Severity
    lines: Severity
    max_duration: Severity
    min_gap: Severity
    cps: Severity
    min_duration: Severity
    break_quality: Severity


class TextRules(_Data):
    """How the recognised words are tidied for the cues (tidy.py); the document keeps them as recognised."""

    drop_fillers: bool = True  # the language's fillers ("uh", "um") do not reach the cues
    window_capitals: bool = True  # a speech window's first word loses the model's capital mid-sentence


class Preset(_Data):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str = Field(min_length=1)
    description: str
    language: str = Field(pattern=r"^[a-z]{2,3}$")
    limits: Limits
    timing: Timing
    segmenter: SegmenterWeights
    severity: Severities
    text: TextRules = TextRules()  # a track saved before these rules existed reads as the defaults

    @model_validator(mode="after")
    def _room(self) -> Preset:
        if self.timing.lead_in_ms > self.limits.max_duration_ms - MIN_SPAN_MS:
            raise ValueError("lead_in_ms leaves no room for the words within max_duration_ms")
        return self


class Language(_Data):
    code: str
    break_before: frozenset[str]
    no_break_after: frozenset[str]
    not_names: frozenset[str]
    fillers: frozenset[str] = frozenset()  # dropped from cues when the preset says so
    capital_always: frozenset[str] = frozenset()  # words that keep their capital at a window start ("i", "i'm")

    @field_validator("break_before", "no_break_after", "not_names", "fillers", "capital_always")
    @classmethod
    def _lower(cls, v: frozenset[str]) -> frozenset[str]:
        bad = sorted(w for w in v if w != w.lower() or not w.strip())
        if bad:
            raise ValueError(f"words must be lower case and non-empty: {bad}")
        return v


class PresetError(Exception):
    """A preset or language file is missing or wrong; the message names the file and the problem."""


def _read(path: Path) -> dict[str, object]:
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except FileNotFoundError:
        raise PresetError(f"{path.name} not found in {path.parent}") from None
    except tomllib.TOMLDecodeError as exc:
        raise PresetError(f"{path.name}: not valid TOML ({exc})") from None


def _explain(path: Path, exc: ValidationError) -> PresetError:
    first = exc.errors()[0]
    where = ".".join(str(p) for p in first["loc"])
    return PresetError(f"{path.name}: {where}: {first['msg']}")


def load_preset(path: Path) -> Preset:
    try:
        preset = Preset.model_validate(_read(path))
    except ValidationError as exc:
        raise _explain(path, exc) from None
    if preset.id != path.stem:
        raise PresetError(f"{path.name}: id {preset.id!r} must equal the file name {path.stem!r}")
    return preset


@cache
def registry(directory: Path = PRESETS_DIR) -> dict[str, Preset]:
    found = {p.stem: load_preset(p) for p in sorted(directory.glob("*.toml"))}
    if not found:
        raise PresetError(f"no presets found in {directory}")
    return found


def names(directory: Path = PRESETS_DIR) -> list[str]:
    return list(registry(directory))


@cache
def standards(directory: Path = STANDARDS_DIR) -> dict[str, Preset]:
    """Published standards as starting points for the user's own types (not used as they are)."""
    return {p.stem: load_preset(p) for p in sorted(directory.glob("*.toml"))}


def book(custom: Sequence[Preset] = ()) -> dict[str, Preset]:
    """Every type a track can be made with: the built-in ones, then the user's own (a built-in id always wins).
    Passed to whatever builds tracks, like the glossary; nothing here remembers the user's types."""
    out = dict(registry())
    for p in custom:
        out.setdefault(p.id, p)
    return out


def get(preset_id: str, directory: Path = PRESETS_DIR) -> Preset:
    reg = registry(directory)
    if preset_id not in reg:
        raise PresetError(f"unknown preset {preset_id!r}; available: {', '.join(reg)}")
    return reg[preset_id]


@cache
def language(code: str, directory: Path = LANG_DIR) -> Language:
    path = directory / f"{code}.toml"
    try:
        return Language.model_validate({"code": code, **_read(path)})
    except ValidationError as exc:
        raise _explain(path, exc) from None


def digest(preset: Preset, lang: Language) -> str:
    """Fingerprint of the rules of a track: the rule values, the word lists, the segmenter version."""
    # sets are written sorted: their own order changes from run to run
    lists = ("break_before", "no_break_after", "not_names", "fillers", "capital_always")  # every list cues depend on
    words = {k: sorted(getattr(lang, k)) for k in lists}
    blob = json.dumps(
        {
            # a type's name and description are not rules: renaming it keeps its subtitles as they are
            "preset": preset.model_dump(mode="json", exclude={"name", "description"}),
            "lang": {"code": lang.code, **words},
            "segmenter": SEGMENTER_VERSION,
        },
        sort_keys=True,
    )
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def track_digest(preset: Preset, lang: Language, gl: Glossary | None = None) -> str:
    """What a stored track is compared with: its rules' fingerprint "+" the glossary's (the built-in one when none
    is given). Kept in two parts, so a glossary change remakes untouched tracks without calling an edited track's
    rules changed."""
    from parakeet_stt import glossary

    return f"{digest(preset, lang)}+{(gl or glossary.builtin()).digest()}"


def rules_part(track_digest: str) -> str:
    """The rules' fingerprint of a stored track's digest (a track made before the glossary has no second part)."""
    return track_digest.split("+", 1)[0]


_EDGE = re.compile(r"^[\W_]+|[\W_]+$")


def bare(word: str) -> str:
    """A word in lower case without surrounding punctuation ("Kyoto?" -> "kyoto")."""
    return _EDGE.sub("", word).lower()
