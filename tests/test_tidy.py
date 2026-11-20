"""Cue words tidied from the recognised ones: fillers out, the model's window-start capitals taken back mid-sentence."""

from __future__ import annotations

from parakeet_stt import presets, tidy
from parakeet_stt.segment import build_track
from parakeet_stt.transcript import Document, Segment, Word
from tests import factories

EN = presets.language("en")
PRESET = presets.get("cz-template")


def doc(*windows: str) -> Document:
    """One segment (speech window) per string, words 500 ms apart, windows 1 s apart."""
    segments, t = [], 0
    for text in windows:
        ws = []
        for word in text.split():
            ws.append(Word(text=word, start_ms=t, end_ms=t + 400, conf=0.9))
            t += 500
        segments.append(Segment(start_ms=ws[0].start_ms, end_ms=ws[-1].end_ms, words=ws))
        t += 1000
    return factories.document(segments).model_copy(update={"media": factories.media(duration_ms=t, decoded_ms=t)})


def text(d: Document, preset: presets.Preset = PRESET) -> str:
    return " ".join(w.text for w in tidy.words_for_cues(d, preset, EN))


def test_a_window_that_starts_mid_sentence_loses_the_models_capital() -> None:
    d = doc("I was asked to", "Talk about our", "Experiences.", "With him", "to talk of experiences")
    assert text(d) == "I was asked to talk about our experiences. With him to talk of experiences"
    assert text(doc("we sat", "And the", "rest")) == "we sat and the rest"  # small words are known ordinary words


def test_a_word_seen_only_with_a_capital_keeps_it() -> None:
    assert text(doc("we went to", "Basel and", "talked")) == "we went to Basel and talked"


def test_i_names_and_abbreviations_keep_their_capital() -> None:
    d = doc("we met", "I think", "and then", "Hannah came", "with Hannah and", "USA people")
    assert text(d) == "we met I think and then Hannah came with Hannah and USA people"
    assert text(doc("so", "I'm here")) == "so I'm here"


def test_fillers_are_dropped_and_a_sentence_they_open_or_close_stays_right() -> None:
    assert text(doc("Uh, the point is", "uh simple. Um I know")) == "The point is simple. I know"
    assert text(doc("to tell because uh.", "So we")) == "to tell because. So we"
    assert text(doc("with uh Nietzsche, the young")) == "with Nietzsche, the young"
    assert text(doc("is that right, uh?", "So we go on")) == "is that right? So we go on"
    assert text(doc('he said "yes" uh.', "Then we")) == 'he said "yes." Then we'
    assert text(doc("it ends here. uh.", "Then")) == "it ends here. Then"  # an end already there is not doubled


def test_the_rules_follow_the_preset_and_the_document_keeps_its_words() -> None:
    d = doc("Uh I was asked to", "Talk about", "a talk")
    off = PRESET.model_copy(update={"text": presets.TextRules(drop_fillers=False, window_capitals=False)})
    assert text(d, off) == "Uh I was asked to Talk about a talk"
    assert text(d) == "I was asked to talk about a talk"
    assert [w.text for w in d.words()][:6] == ["Uh", "I", "was", "asked", "to", "Talk"]  # as recognised


def test_cues_are_made_from_the_tidied_words() -> None:
    cues = build_track(doc("we met the", "Nietzsche uh in", "Basel."), PRESET).cues
    assert " ".join(" ".join(c.lines) for c in cues) == "we met the Nietzsche in Basel."


def test_a_preset_file_without_text_rules_reads_as_the_defaults() -> None:
    data = PRESET.model_dump(exclude={"text"})
    assert presets.Preset.model_validate(data).text == presets.TextRules()


def test_a_word_also_written_in_lower_case_is_no_name_and_a_small_word_used_as_a_name_is() -> None:
    assert text(doc("he said", "Okay then", "and Okay again", "it was okay")) == (
        "he said okay then and Okay again it was okay"  # only a window's first word is touched
    )
    assert text(doc("I met", "Will today", "and Will said")) == "I met Will today and Will said"
