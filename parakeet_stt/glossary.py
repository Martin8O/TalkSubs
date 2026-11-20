"""Terms and names the speech model gets wrong, put right where subtitle text is made.

The engine's words in the project file stay exactly as recognised (ADR 0007); the glossary is applied to a copy
when cues are built (and when the editor is given the words), and its fingerprint is a separate part of a track's
digest, so untouched tracks are made again when the glossary changes while edited tracks stay - and are not
reported as having changed rules.

An entry: the term as it should be written, other spellings that are also right, and the wrong forms the model
writes instead (each one or more words). A wrong form is replaced by the term; a plural of a wrong form ("…s")
by the term's plural, a possessive ("…'s") likewise. A term (or other right spelling) written with a capital is
also given its capital where the model wrote it in lower case, unless the entry says not to. A term listed by the
user is right as written: a built-in rule that would replace it is dropped. Later entries win (the user's come last).
A multi-word form is not matched across a sentence end or a long pause. Times: one word for one keeps its time;
otherwise the span of the replaced words is shared out by letters, so every new word lies inside the old span.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import re
import tomllib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Final

from parakeet_stt.transcript import Word

BUILTIN_FILE: Final = Path(__file__).resolve().parent.parent / "presets" / "glossary" / "en.toml"
VERSION: Final = 1  # of the matching rules below: part of the fingerprint, so a change remakes untouched tracks
MAX_WORDS: Final = 6  # the longest form looked for, in words
MAX_INNER_PAUSE_MS: Final = 1500  # a multi-word form is not matched across a longer pause
MAX_TEXT_CHARS: Final = 200_000
_EDGE_LEAD = re.compile(r"^[\W_]+")
_EDGE_TAIL = re.compile(r"[\W_]+$")
_POSSESSIVE = ("'s", "’s")  # noqa: RUF001 - the typographic apostrophe on purpose
_SENTENCE_END = (".", "?", "!", "…")
_CLOSERS = "\"'”’)]"  # noqa: RUF001 - typographic quotes on purpose


class GlossaryError(Exception):
    """The glossary is not usable; the message is for the user."""


@dataclass(frozen=True)
class Entry:
    term: str
    also: tuple[str, ...] = ()
    wrong: tuple[str, ...] = ()
    fix_case: bool = True  # give a capitalised term its capital where the model wrote it in lower case


def _core(text: str) -> str:
    return _EDGE_TAIL.sub("", _EDGE_LEAD.sub("", text))


def _key(text: str) -> tuple[str, ...]:
    return tuple(c.lower() for c in (_core(t) for t in text.split()) if c)


def _ends_sentence(text: str) -> bool:
    return text.rstrip(_CLOSERS).endswith(_SENTENCE_END)


@dataclass(frozen=True)
class _Rule:
    replace: tuple[str, ...] | None  # the words written instead; None: right as it is (a user's term)
    fix_case_only: bool  # a right spelling in the wrong case: replaced only when the case differs


@dataclass(frozen=True)
class Glossary:
    entries: tuple[Entry, ...]
    _rules: dict[tuple[str, ...], _Rule] = field(default_factory=dict, compare=False, repr=False)
    _longest: int = field(default=0, compare=False, repr=False)

    @classmethod
    def of(cls, entries: Iterable[Entry], *, user_from: int | None = None) -> Glossary:
        """`user_from`: entries from this index on are the user's (their terms are right as they are)."""
        es = tuple(entries)
        rules: dict[tuple[str, ...], _Rule] = {}
        for i, e in enumerate(es):
            term = tuple(e.term.split())
            case_rules: dict[tuple[str, ...], _Rule] = {}
            if e.fix_case:
                for spelling in (e.term, *e.also):
                    if any(c.isupper() for c in spelling):
                        case_rules[_key(spelling)] = _Rule(tuple(spelling.split()), fix_case_only=True)
            own = _key(e.term)
            if user_from is not None and i >= user_from and own and own not in case_rules:
                case_rules[own] = _Rule(None, fix_case_only=True)  # the user says this is right as written
            rules.update({k: r for k, r in case_rules.items() if k and len(k) <= MAX_WORDS})
            for form in e.wrong:  # an entry's wrong forms win over its own case rules
                k = _key(form)
                if k and len(k) <= MAX_WORDS:
                    rules[k] = _Rule(term, fix_case_only=False)
        return cls(es, rules, max((len(k) for k in rules), default=0))

    def with_user(self, entries: Iterable[Entry]) -> Glossary:
        """The built-in entries plus the user's own, which come last and so win."""
        return Glossary.of((*self.entries, *entries), user_from=len(self.entries))

    def digest(self) -> str:
        rules = sorted(
            [list(k), list(r.replace) if r.replace else None, r.fix_case_only] for k, r in self._rules.items()
        )
        blob = json.dumps({"v": VERSION, "rules": rules}, sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()[:16]

    def apply(self, words: Sequence[Word]) -> list[Word]:
        """The words with every wrong form put right (a new list; the input is not changed)."""
        if not self._rules:
            return list(words)
        cores = [_core(w.text) for w in words]
        out: list[Word] = []
        i = 0
        while i < len(words):
            hit = self._match(words, cores, i)
            if hit is None:
                out.append(words[i])
                i += 1
                continue
            n, replace, suffix = hit
            starts_sentence = not out or _ends_sentence(out[-1].text)
            out.extend(_rewrite(words[i : i + n], replace, suffix, starts_sentence))
            i += n
        return out

    def _match(self, words: Sequence[Word], cores: list[str], i: int) -> tuple[int, tuple[str, ...], str] | None:
        for n in range(min(self._longest, len(words) - i), 0, -1):
            span = cores[i : i + n]
            if any(not c for c in span) or not _together(words[i : i + n]):
                continue
            key = tuple(c.lower() for c in span)
            rule, suffix = self._rules.get(key), ""
            if rule is None:
                last = span[-1]
                for end in (*_POSSESSIVE, "s"):
                    if last.lower().endswith(end) and len(last) > len(end):
                        found = self._rules.get((*key[:-1], last[: -len(end)].lower()))
                        if found is not None and not found.fix_case_only:
                            rule, suffix = found, last[-len(end) :]
                            break
                if rule is None:
                    continue
            if rule.replace is None or (rule.fix_case_only and list(span) == list(rule.replace)):
                return None  # right as it is
            return n, rule.replace, suffix
        return None


def _together(span: Sequence[Word]) -> bool:
    """Words of one multi-word form: no sentence end inside and no long pause between them."""
    return all(
        not _ends_sentence(a.text) and b.start_ms - a.end_ms <= MAX_INNER_PAUSE_MS for a, b in itertools.pairwise(span)
    )


def _rewrite(span: Sequence[Word], replace: tuple[str, ...], suffix: str, starts_sentence: bool) -> list[Word]:
    first, last = span[0], span[-1]
    lead = first.text[: len(first.text) - len(_EDGE_LEAD.sub("", first.text))]
    tail = last.text[len(_EDGE_TAIL.sub("", last.text)) :]
    texts = list(replace)
    if starts_sentence and texts[0][:1].islower():
        texts[0] = texts[0][:1].upper() + texts[0][1:]
    texts[-1] += suffix
    texts[0] = lead + texts[0]
    texts[-1] += tail
    if len(texts) == len(span):
        return [
            Word(text=t, start_ms=w.start_ms, end_ms=w.end_ms, conf=w.conf) for t, w in zip(texts, span, strict=True)
        ]
    confs = [w.conf for w in span]
    conf = None if any(c is None for c in confs) else min(c for c in confs if c is not None)
    start, end = first.start_ms, last.end_ms
    letters = [max(1, len(_core(t))) for t in texts]
    total = sum(letters)
    out, at = [], start
    for k, t in enumerate(texts):
        stop = end if k == len(texts) - 1 else max(at, start + round((end - start) * sum(letters[: k + 1]) / total))
        out.append(Word(text=t, start_ms=at, end_ms=max(stop, at), conf=conf))
        at = stop
    return out


# ---- data --------------------------------------------------------------------------------------------------------


def _entry(raw: object, where: str) -> Entry:
    if not isinstance(raw, dict) or not isinstance(raw.get("write"), str) or not raw["write"].strip():
        raise GlossaryError(f"{where}: every entry needs `write` (the term as it should be written).")
    lists = []
    for name in ("also", "wrong"):
        value = raw.get(name, [])
        if not isinstance(value, list) or not all(isinstance(v, str) and _key(v) for v in value):
            raise GlossaryError(f"{where}: `{name}` of {raw['write']!r} must be a list of words.")
        lists.append(tuple(v.strip() for v in value))
    fix_case = raw.get("fix_case", True)
    if not isinstance(fix_case, bool):
        raise GlossaryError(f"{where}: `fix_case` of {raw['write']!r} must be true or false.")
    return Entry(raw["write"].strip(), lists[0], lists[1], fix_case)


@cache
def builtin(path: Path = BUILTIN_FILE) -> Glossary:
    """The glossary that comes with the app (`presets/glossary/en.toml`; empty: users list their own terms)."""
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise GlossaryError(f"The glossary {path.name} cannot be read: {exc}") from None
    return Glossary.of(_entry(r, path.name) for r in data.get("term", []))


def parse_text(text: str) -> list[Entry]:
    """The user's own list, as typed in Settings - one term per line:

    Nietzsche = Neechee, Nichee      (what the speech model writes instead, comma separated)
    Dostoevsky / Dostoyevsky = Dusty Effsky   (other right spellings after "/")
    Schrödinger                      (a term alone: right as written; it gets its capital where written small)
    ~Big Bang                        (a leading ~: its case is left alone elsewhere)
    # a note                         (kept with the text, not used)
    """
    if len(text) > MAX_TEXT_CHARS:
        raise GlossaryError("The list of terms is too long.")
    entries = []
    for n, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        fix_case = not line.startswith("~")
        left, eq, right = line.lstrip("~").partition("=")
        names = [x.strip() for x in left.split("/") if x.strip()]
        wrong = [x.strip() for x in right.split(",") if x.strip()]
        what = f"Line {n} ({line[:60]})"
        if not names or (eq and not wrong):
            raise GlossaryError(f"{what}: write the right term, then = and what the model writes instead.")
        for form in (*names, *wrong):
            if not _key(form):
                raise GlossaryError(f"{what}: {form!r} has no letters.")
            if len(form.split()) > MAX_WORDS:
                raise GlossaryError(f"{what}: {form!r} is longer than {MAX_WORDS} words.")
        entries.append(Entry(names[0], tuple(names[1:]), tuple(wrong), fix_case))
    return entries


def as_text(entries: Iterable[Entry]) -> str:
    """Entries in the Settings format (for showing the built-in list)."""
    lines = []
    for e in entries:
        left = ("" if e.fix_case else "~") + " / ".join((e.term, *e.also))
        lines.append(f"{left} = {', '.join(e.wrong)}" if e.wrong else left)
    return "\n".join(lines)


@cache
def current(user_text: str) -> Glossary:
    """The built-in glossary plus the user's own list (cached by its text). Raises GlossaryError."""
    return builtin().with_user(parse_text(user_text))
