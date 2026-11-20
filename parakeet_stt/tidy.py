"""The words a cue is made of, tidied from the recognised ones; the document keeps its words as recognised (ADR 0007).

Two things the speech model does that a subtitle should not show, each switched by the preset's [text] rules:
- fillers ("uh", "um": the language's list) are dropped; a sentence end they carried moves to the word before, and
  a sentence they opened starts with a capital on the next word;
- every speech window is recognised on its own, and the model writes the first word of each window with a capital
  as if a sentence began there. Where the word before ends no sentence, that capital is taken back, but only from a
  word known to be an ordinary one: the same lecture also has it in lower case, or it is one of the language's small
  words ("and", "the") and never written with a capital mid-sentence inside a window (which would make it a name:
  "Will", "May"). A word that always has a capital ("I") and a word seen only with a capital keep it. Glossary terms
  get their own capital back afterwards (the glossary fixes the case of its terms).
"""

from __future__ import annotations

from typing import Final

from parakeet_stt import presets
from parakeet_stt.presets import Language, Preset
from parakeet_stt.transcript import Document, Word

SENTENCE_END: Final = (".", "?", "!", "…")
CLOSERS: Final = "\"'”’)]"  # noqa: RUF001 - typographic quotes on purpose
_APOSTROPHES: Final = ("'", "’")  # noqa: RUF001


def ends_sentence(text: str) -> bool:
    return text.rstrip(CLOSERS).endswith(SENTENCE_END)


def words_for_cues(doc: Document, preset: Preset, lang: Language) -> list[Word]:
    # (word, whether it opens a speech window after the first one)
    flat = [
        (w, i == 0 and k > 0) for k, s in enumerate(s for s in doc.segments if s.words) for i, w in enumerate(s.words)
    ]
    words = [w for w, _ in flat]
    if preset.text.window_capitals:
        small = (lang.break_before | lang.no_break_after) - _names(flat)
        ordinary = {presets.bare(w.text) for w in words if w.text[:1].islower()} | small
        words = [
            _lower_first(w)
            if window_start and k > 0 and not ends_sentence(flat[k - 1][0].text) and _takes_lower(w, lang, ordinary)
            else w
            for k, (w, window_start) in enumerate(flat)
        ]
    if preset.text.drop_fillers:
        words = _without_fillers(words, lang)
    return words


def _names(flat: list[tuple[Word, bool]]) -> set[str]:
    """Words the model wrote with a capital inside a window and mid-sentence (names, unless also seen in lower case)."""
    return {
        presets.bare(w.text)
        for k, (w, window_start) in enumerate(flat)
        if k > 0 and not window_start and w.text[:1].isupper() and not ends_sentence(flat[k - 1][0].text)
    }


def _takes_lower(w: Word, lang: Language, ordinary: set[str]) -> bool:
    text, bare = w.text, presets.bare(w.text)
    if not text[:1].isupper() or text[1:2].isupper():  # no capital, or an abbreviation ("USA")
        return False
    head = bare
    for mark in _APOSTROPHES:
        head = head.split(mark)[0]
    return head not in lang.capital_always and bare in ordinary


def _lower_first(w: Word) -> Word:
    return w.model_copy(update={"text": w.text[0].lower() + w.text[1:]})


def _without_fillers(words: list[Word], lang: Language) -> list[Word]:
    out: list[Word] = []
    capital_next = False
    for w in words:
        if presets.bare(w.text) in lang.fillers:
            opens = not out or ends_sentence(out[-1].text)
            core = w.text.rstrip(CLOSERS)
            if core.endswith(SENTENCE_END) and out and not ends_sentence(out[-1].text):
                # "because uh." -> "because."; "right, uh?" -> "right?"; 'said "yes" uh.' -> 'said "yes."'
                prev = out[-1].text
                body = prev.rstrip(CLOSERS)
                closers, body = prev[len(body) :], body.rstrip(",;:")
                if body[-1:].isalnum():
                    out[-1] = out[-1].model_copy(update={"text": body + core[-1] + closers})
            elif opens:  # "Uh, the point is" -> "The point is"
                capital_next = True
            continue
        opens_sentence = capital_next and w.text[:1].islower()
        out.append(w.model_copy(update={"text": w.text[0].upper() + w.text[1:]}) if opens_sentence else w)
        capital_next = False
    return out
