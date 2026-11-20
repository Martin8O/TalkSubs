"""The rule checker: every way a track breaks its preset, one entry per problem.

Integrity problems (order, overlap, off the frame grid, empty text, past the media end) are always hard.
Style rules take their severity from the preset. The same function serves the command line now and the
editor's live warnings later.
"""

from __future__ import annotations

from collections import Counter
from typing import Literal

from pydantic import BaseModel, ConfigDict

from parakeet_stt import presets, timecode
from parakeet_stt.presets import Language, Preset
from parakeet_stt.transcript import Cue, FrameRate, SubtitleTrack

Rule = Literal[
    "order", "overlap", "off_grid", "empty", "past_media_end",
    "chars_per_line", "lines", "max_duration", "min_duration", "cps", "min_gap", "break_quality",
]  # fmt: skip
INTEGRITY: frozenset[Rule] = frozenset({"order", "overlap", "off_grid", "empty", "past_media_end"})


class Violation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cue_id: int
    cue_index: int  # 0-based position in the track
    rule: Rule
    severity: presets.Severity
    value: float
    limit: float
    message: str


def cps(cue: Cue, preset: Preset) -> float:
    text = " ".join(cue.lines)
    chars = len(text) if preset.limits.cps_counts_spaces else len(text.replace(" ", ""))
    return chars / max((cue.end_ms - cue.start_ms) / 1000, 0.001)


def check_track(track: SubtitleTrack, media_end_ms: int, lang: Language | None = None) -> list[Violation]:
    return check(track.cues, track.preset, track.frame_rate, media_end_ms, lang)


_Found = tuple[Rule, float, float, str]


def check(
    cues: list[Cue], preset: Preset, fps: FrameRate, media_end_ms: int, lang: Language | None = None
) -> list[Violation]:
    lang = lang or presets.language(preset.language)
    out: list[Violation] = []
    for i, c in enumerate(cues):
        prev = cues[i - 1] if i else None
        nxt = cues[i + 1] if i + 1 < len(cues) else None
        found = [*_integrity(c, prev, fps, media_end_ms), *_style(c, preset), *_spacing(c, prev, preset, fps)]
        found += [("break_quality", 0, 0, f"breaks between {a!r} and {b!r}")
                  for a, b in _boundaries(c, nxt) if _bad_break(a, b, lang)]  # fmt: skip
        for rule, value, limit, message in found:
            severity: presets.Severity = "hard" if rule in INTEGRITY else getattr(preset.severity, rule)
            out.append(Violation(cue_id=c.id, cue_index=i, rule=rule, severity=severity, value=value,
                                 limit=limit, message=message))  # fmt: skip
    return out


def _integrity(c: Cue, prev: Cue | None, fps: FrameRate, media_end_ms: int) -> list[_Found]:
    found: list[_Found] = []
    for t in (c.start_ms, c.end_ms):
        if timecode.snap(t, fps) != t:
            found.append(("off_grid", t, timecode.snap(t, fps), f"{t} ms is not on the {fps.num}/{fps.den} frame grid"))
    if not c.lines or any(not ln.strip() for ln in c.lines):
        found.append(("empty", len(c.lines), 1, "empty line or no text"))
    if c.end_ms > media_end_ms:
        found.append(("past_media_end", c.end_ms, media_end_ms, "ends after the media"))
    if prev is not None and c.start_ms < prev.start_ms:
        found.append(("order", c.start_ms, prev.start_ms, "starts before the previous cue"))
    elif prev is not None and c.start_ms < prev.end_ms:
        found.append(
            ("overlap", c.start_ms, prev.end_ms, f"overlaps the previous cue by {prev.end_ms - c.start_ms} ms")
        )
    return found


def _style(c: Cue, preset: Preset) -> list[_Found]:
    lim = preset.limits
    dur = c.end_ms - c.start_ms
    found: list[_Found] = []
    if len(c.lines) > lim.max_lines:
        found.append(("lines", len(c.lines), lim.max_lines, f"{len(c.lines)} lines"))
    found += [("chars_per_line", len(ln), lim.max_chars_per_line, f"line of {len(ln)} characters: {ln!r}")
              for ln in c.lines if len(ln) > lim.max_chars_per_line]  # fmt: skip
    if dur > lim.max_duration_ms:
        found.append(("max_duration", dur, lim.max_duration_ms, f"lasts {dur} ms"))
    if dur < lim.min_duration_ms:
        found.append(("min_duration", dur, lim.min_duration_ms, f"lasts only {dur} ms"))
    rate = cps(c, preset)
    if rate > lim.max_cps:
        found.append(("cps", round(rate, 2), lim.max_cps, f"{rate:.1f} characters per second"))
    return found


def _spacing(c: Cue, prev: Cue | None, preset: Preset, fps: FrameRate) -> list[_Found]:
    if prev is None or c.start_ms < prev.end_ms:  # overlap and order are integrity problems
        return []
    gap_frames = round((c.start_ms - prev.end_ms) / timecode.frame_ms(fps))
    if gap_frames < preset.limits.min_gap_frames:
        return [("min_gap", gap_frames, preset.limits.min_gap_frames, f"gap of {gap_frames} frames before it")]
    return []


def _boundaries(c: Cue, nxt: Cue | None) -> list[tuple[str, str]]:
    """(last word, next word) at every line break inside the cue and at its end."""
    pairs = []
    for top, bottom in zip(c.lines, c.lines[1:], strict=False):
        if top.split() and bottom.split():
            pairs.append((top.split()[-1], bottom.split()[0]))
    if nxt is not None and c.lines and nxt.lines and c.lines[-1].split() and nxt.lines[0].split():
        pairs.append((c.lines[-1].split()[-1], nxt.lines[0].split()[0]))
    return pairs


def _bad_break(a: str, b: str, lang: Language) -> bool:
    if not a[-1].isalnum():  # the word carries punctuation: any break after it is fine
        return False
    if presets.bare(a) in lang.no_break_after:
        return True
    return (
        a[:1].isupper()
        and b[:1].isupper()
        and presets.bare(a) not in lang.not_names
        and (presets.bare(b) not in lang.not_names)
    )


def summary(violations: list[Violation]) -> dict[str, dict[str, int]]:
    """{"hard": {rule: count}, "soft": {rule: count}}."""
    out: dict[str, Counter[str]] = {"hard": Counter(), "soft": Counter()}
    for v in violations:
        out[v.severity][v.rule] += 1
    return {k: dict(sorted(v.items())) for k, v in out.items()}
