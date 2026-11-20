from __future__ import annotations

from parakeet_stt import presets, rules
from parakeet_stt.transcript import Cue, FrameRate

PAL = FrameRate(num=25, den=1)
CZ = presets.get("cz-template")


def cue(i: int, start: int, end: int, *lines: str) -> Cue:
    return Cue(id=i, start_ms=start, end_ms=end, lines=list(lines))


def found(cues: list[Cue], media_end: int = 600_000) -> set[tuple[int, str, str]]:
    return {(v.cue_id, v.rule, v.severity) for v in rules.check(cues, CZ, PAL, media_end)}


def test_a_clean_track_has_no_violations() -> None:
    assert found([cue(1, 1000, 3000, "We sit down,", "and we meditate."), cue(2, 3080, 5000, "Then we rest.")]) == set()


def test_every_rule_is_reported() -> None:
    cues = [
        cue(1, 1000, 2000, "x" * 43),  # too long a line
        cue(2, 2080, 4000, "one", "two", "three"),  # three lines
        cue(3, 4080, 12_000, "Lasting far too long."),  # over 7 s
        cue(4, 12_040, 12_400, "Short."),  # 360 ms, and a 1-frame gap before it
        cue(5, 12_480, 13_480, "This is far too much text to read in a sec"),  # CPS
        cue(6, 13_560, 15_000, "we walk towards the", "house."),  # line ends on an article
        cue(7, 14_000, 16_000, "Overlapping."),  # starts inside the previous cue
        cue(8, 13_900, 17_000, "Before the previous start."),
        cue(9, 17_013, 18_000, "Off the grid."),
        cue(10, 18_080, 19_000, ""),
        cue(11, 19_080, 700_000, "Past the end."),
    ]
    got = found(cues)
    assert {
        (1, "chars_per_line", "hard"), (2, "lines", "hard"), (3, "max_duration", "hard"), (4, "min_gap", "hard"),
        (4, "min_duration", "soft"), (5, "cps", "soft"), (6, "break_quality", "soft"), (7, "overlap", "hard"),
        (8, "order", "hard"), (9, "off_grid", "hard"), (10, "empty", "hard"), (11, "past_media_end", "hard"),
    } <= got  # fmt: skip


def test_a_name_split_across_cues_is_a_bad_break() -> None:
    assert (1, "break_quality", "soft") in found([cue(1, 1000, 3000, "I met Tim"), cue(2, 3080, 5000, "Testor there.")])
    assert found([cue(1, 1000, 3000, "I met Tim."), cue(2, 3080, 5000, "Testor was there.")]) == set()


def test_severity_comes_from_the_preset() -> None:
    raw = CZ.model_dump()
    raw["severity"]["cps"] = "hard"
    strict = presets.Preset.model_validate(raw)
    (v,) = rules.check([cue(1, 1000, 2000, "This is far too much text to read in a sec")], strict, PAL, 600_000)
    assert (v.rule, v.severity) == ("cps", "hard")


def test_summary_counts_by_severity_and_rule() -> None:
    vs = rules.check([cue(1, 1000, 1200, "x" * 43)], CZ, PAL, 600_000)
    assert rules.summary(vs) == {"hard": {"chars_per_line": 1}, "soft": {"cps": 1, "min_duration": 1}}
