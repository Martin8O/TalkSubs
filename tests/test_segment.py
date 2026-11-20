from __future__ import annotations

import random

import pytest

from parakeet_stt import presets, rules, timecode
from parakeet_stt.presets import Preset
from parakeet_stt.segment import Segmenter
from parakeet_stt.transcript import Cue, FrameRate, Word

PAL = FrameRate(num=25, den=1)
RATES = [PAL, FrameRate(num=50, den=1), FrameRate(num=30000, den=1001)]
LANG = presets.language("en")
VOCAB = [
    "the",
    "a",
    "of",
    "and",
    "to",
    "we",
    "mind",
    "is",
    "very",
    "really",
    "science",
    "meditation",
    "open",
    "space",
    "awareness",
    "teacher",
    "Toronto",
    "practice",
    "we",
    "they",
    "you",
    "it",
    "has",
    "some",
    "many",
    "people",
    "experience",
    "nature",
    "joy",
    "because",
    "when",
]
ENDINGS = ("", "", "", "", "", ",", ".", "?")


def stream(seed: int, n: int = 600) -> list[Word]:
    """Speech-like words: 80-600 ms long, mostly tight, sometimes a pause, punctuation now and then."""
    rng = random.Random(seed)
    t, out = 500, []
    for _ in range(n):
        text = rng.choice(VOCAB) + rng.choice(ENDINGS)
        if rng.random() < 0.1:
            text = text.capitalize()
        dur = rng.randint(80, 600)
        out.append(Word(text=text, start_ms=t, end_ms=t + dur, conf=None))
        gap = rng.choice((0, 0, 0, 40, 80, 120, 300, 700, 2000))
        t += dur + gap
    return out


def timed(text: str, start: int = 1000, word_ms: int = 300, gap_ms: int = 20) -> list[Word]:
    out, t = [], start
    for w in text.split():
        out.append(Word(text=w, start_ms=t, end_ms=t + word_ms, conf=None))
        t += word_ms + gap_ms
    return out


def seg(preset_id: str = "cz-template", fps: FrameRate = PAL) -> Segmenter:
    return Segmenter(presets.get(preset_id), LANG, fps)


def end_of(words: list[Word]) -> int:
    return words[-1].end_ms + 60_000


def owner(cues: list[Cue], w: Word) -> int:
    """The contract's rule: the cue with the latest start at or before the word's start."""
    return max(i for i, c in enumerate(cues) if c.start_ms <= w.start_ms)


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
@pytest.mark.parametrize("fps", RATES, ids=lambda f: f"{f.num}/{f.den}")
@pytest.mark.parametrize("preset_id", presets.names())
def test_random_speech_keeps_every_word_in_its_own_cue(seed: int, fps: FrameRate, preset_id: str) -> None:
    words = stream(seed)
    cues = seg(preset_id, fps).cues(words, end_of(words))
    assert " ".join(" ".join(c.lines) for c in cues).split() == [w.text for w in words]  # each once, in order
    groups: dict[int, list[str]] = {}
    for w in words:
        groups.setdefault(owner(cues, w), []).append(w.text)
    assert [groups.get(i, []) for i in range(len(cues))] == [" ".join(c.lines).split() for c in cues]
    for c in cues:
        assert timecode.snap(c.start_ms, fps) == c.start_ms and timecode.snap(c.end_ms, fps) == c.end_ms
    preset = presets.get(preset_id)
    hard = [v for v in rules.check(cues, preset, fps, end_of(words), LANG) if v.severity == "hard"]
    assert hard == []


def test_no_words_no_cues() -> None:
    assert seg().cues([], 10_000) == []


def test_a_long_breathless_sentence_is_cut_into_legal_cues() -> None:
    words = timed(" ".join(["people really experience the open space of mind"] * 6), word_ms=250, gap_ms=10)
    cues = seg().cues(words, end_of(words))
    assert len(cues) >= 4
    assert [v for v in rules.check(cues, presets.get("cz-template"), PAL, end_of(words), LANG)
            if v.severity == "hard"] == []  # fmt: skip


def test_slow_speech_is_split_by_duration_not_length() -> None:
    words = timed("so this is very slow speech indeed", word_ms=900, gap_ms=200)  # 7.5 s, 34 characters
    cues = seg().cues(words, end_of(words))
    assert len(cues) == 2
    assert all(c.end_ms - c.start_ms <= 7000 for c in cues)


def test_a_word_too_long_for_a_line_goes_alone_and_is_reported() -> None:
    long = "Constantinople-Thessaloniki-Alexandria-Rome-x"  # 45 characters
    words = timed(f"we meet {long} today.")
    cues = seg().cues(words, end_of(words))
    assert [long] in [c.lines for c in cues]
    found = rules.check(cues, presets.get("cz-template"), PAL, end_of(words), LANG)
    assert [(v.rule, v.severity) for v in found if v.rule == "chars_per_line"] == [("chars_per_line", "hard")]
    assert " ".join(" ".join(c.lines) for c in cues).split() == [w.text for w in words]


def test_a_word_longer_than_the_maximum_duration_goes_alone_and_is_reported() -> None:
    words = [*timed("first we chant"), Word(text="Om", start_ms=2000, end_ms=11_000, conf=None),
             *timed("and then we rest.", start=11_020)]  # fmt: skip
    cues = seg().cues(words, end_of(words))
    assert ["Om"] in [c.lines for c in cues]
    found = rules.check(cues, presets.get("cz-template"), PAL, end_of(words), LANG)
    assert [v.rule for v in found if v.severity == "hard"] == ["max_duration"]


def _layout(text: str) -> list[str]:
    ws = [seg()._prep(w) for w in timed(text)]
    fit = seg().layout(ws)
    assert fit is not None
    return fit[0]


@pytest.mark.parametrize(
    ("text", "bad_line_end"),
    [
        ("and now we look at the nature of the mind itself very calmly", "the"),  # article + noun
        ("this morning I spoke with Tim Testor about the meditation", "Tim"),  # first + last name
        ("in the end after many years of practice we understand it all", "we"),  # pronoun + verb
    ],
)
def test_lines_never_break_in_the_wrong_place(text: str, bad_line_end: str) -> None:
    lines = _layout(text)
    assert len(lines) == 2 and all(len(ln) <= 42 for ln in lines)
    assert lines[0].split()[-1] != bad_line_end


def test_lines_prefer_punctuation_and_a_longer_bottom_line() -> None:
    assert _layout("When the mind is relaxed, it can see what it really is") == [
        "When the mind is relaxed,", "it can see what it really is"]  # fmt: skip
    top, bottom = _layout("so we sit down and we meditate on the breath every single morning")
    assert len(bottom) >= len(top)


def test_short_text_stays_on_one_line() -> None:
    assert _layout("We meditate every morning.") == ["We meditate every morning."]


def _two_cues(gap_frames: int, first_ms: int = 4000) -> list[Cue]:
    """Two sentences too long to share a cue, `gap_frames` (25 fps) apart."""
    text = "we sit together in the quiet hall and we watch the mind."  # 56 characters, 12 words
    a = timed(text, start=1000, word_ms=first_ms // 12 - 20, gap_ms=20)
    a[-1] = Word(text=a[-1].text, start_ms=a[-1].start_ms, end_ms=1000 + first_ms, conf=None)
    b = timed(text.capitalize(), start=1000 + first_ms + gap_frames * 40, word_ms=300)
    return seg().cues([*a, *b], 60_000)


@pytest.mark.parametrize(("gap", "expected"), [(0, 2), (1, 2), (2, 2), (3, 2), (11, 2), (12, 12), (20, 20)])
def test_gaps_are_kept_at_two_frames_and_small_ones_closed(gap: int, expected: int) -> None:
    a, b = _two_cues(gap)
    assert round((b.start_ms - a.end_ms) / 40) == expected


def test_closing_a_gap_never_goes_past_the_maximum_duration() -> None:
    a, b = _two_cues(10, first_ms=6900)
    assert a.end_ms - a.start_ms <= 7000
    assert b.start_ms - a.end_ms >= 80  # left open rather than made too long


def test_a_short_answer_gets_the_minimum_duration_from_the_pause_after_it() -> None:
    words = [Word(text="Yes.", start_ms=1000, end_ms=1300, conf=None), *timed("Then we go on.", start=4300)]
    first = seg().cues(words, end_of(words))[0]
    assert first.lines == ["Yes."] and first.end_ms - first.start_ms >= 1000


def test_reading_time_extends_into_silence_but_only_by_the_hold() -> None:
    # 40 characters spoken in 1.2 s, then a long pause: the cue stays at most 500 ms longer than the speech
    words = timed("Meditation brings us joy every morning.", word_ms=180, gap_ms=24)
    (cue,) = seg().cues(words, end_of(words))
    assert words[-1].end_ms < cue.end_ms <= words[-1].end_ms + 500 + 40


def test_a_cue_does_not_end_on_the_first_words_of_a_new_sentence() -> None:
    text = ("you know, there are many spiritual offers. We meet all kinds of offers from hopping up and down "
            "and breathing hard, and so on.")  # fmt: skip
    cues = seg().cues(timed(text, word_ms=230, gap_ms=10), 120_000)
    for c in cues:
        joined = " ".join(c.lines)
        assert not joined.endswith(("offers. We", "offers. We meet"))


def test_lead_in_starts_the_cue_early_without_breaking_the_maximum() -> None:
    raw = presets.get("cz-template").model_dump()
    raw["timing"]["lead_in_ms"] = 200
    preset = Preset.model_validate(raw)
    words = timed("people really experience the open space of mind", word_ms=850, gap_ms=0)  # 6.8 s of speech
    cues = Segmenter(preset, LANG, PAL).cues(words, 120_000)
    assert cues[0].start_ms == 800
    assert all(c.end_ms - c.start_ms <= 7000 for c in cues)


def test_rounding_never_tips_a_cue_over_the_maximum_at_fractional_rates() -> None:
    fps = FrameRate(num=30000, den=1001)  # the span fits the budget at 29.97 fps (at 23.976 it is split)
    # the word span is exactly the budget and the first word starts late in its frame: rounding the last
    # word's end to the nearest frame used to give a 7007 ms cue
    spans = [("so", 33, 1000), ("we", 1000, 3000), ("sit", 3000, 5000), ("down", 5000, 6999)]
    words = [Word(text=w, start_ms=s, end_ms=e, conf=None) for w, s, e in spans]
    cues = seg(fps=fps).cues(words, 60_000)
    assert len(cues) == 1 and cues[0].end_ms - cues[0].start_ms <= 7000


def test_a_word_in_the_last_partial_frame_still_ends_inside_the_media() -> None:
    words = [Word(text="Hello.", start_ms=1000, end_ms=2000, conf=None),
             Word(text="Bye.", start_ms=10_005, end_ms=10_030, conf=None)]  # fmt: skip
    cues = seg().cues(words, 10_030)
    assert cues[-1].end_ms <= 10_030


@pytest.mark.parametrize("fps", RATES, ids=lambda f: f"{f.num}/{f.den}")
def test_gap_closing_works_at_every_frame_rate(fps: FrameRate) -> None:
    frame = timecode.frame_ms(fps)
    text = "we sit together in the quiet hall and we watch the mind."
    a = timed(text, start=1000, word_ms=300, gap_ms=20)
    for gap_frames, closed in ((1, True), (5, True), (11, True), (16, False)):
        b = timed(text.capitalize(), start=round(a[-1].end_ms + gap_frames * frame), word_ms=300)
        first, second = seg(fps=fps).cues([*a, *b], 120_000)[:2]
        gap = round((second.start_ms - first.end_ms) / frame)  # word edges are off the grid: +-1 frame
        assert gap == 2 if closed else gap > 11, (gap_frames, gap)
