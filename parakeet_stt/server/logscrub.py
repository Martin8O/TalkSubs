"""Keep the user's file names, folders and link secrets out of the app's log.

The log (`<data>/logs/app.log`) is for finding faults; what a lecture is called, where it lies and the signed part
of a download link are none of its business. Log records pass through `Formatter`; anything written straight to
the log file (under pythonw, stray prints, warnings and thread tracebacks) passes through `RedactingStream`. Both
replace each registered text - as a whole word, also in its doubled-backslash (repr) form - and the query part of
every web address.
"""

from __future__ import annotations

import logging
import re
import threading
from pathlib import PureWindowsPath
from typing import TextIO

HIDDEN = "<hidden>"
_MIN_LEN = 4  # shorter texts ("the", "art") would blank out ordinary words
_URL_QUERY = re.compile(r"(https?://[^\s?#'\"<>]+)\?[^\s'\"<>]*", re.IGNORECASE)
_lock = threading.Lock()
_texts: set[str] = set()
_pattern: re.Pattern[str] | None = None
_dirty = False


def _forms(text: str) -> set[str]:
    p = PureWindowsPath(text)
    full = {text, str(p), str(p).replace("\\", "/")}
    full |= {t.replace("\\", "\\\\") for t in full if "\\" in t}  # how repr() and %r show a Windows path
    return full | {p.name, p.stem}


def register(*texts: str | None) -> None:
    """Hide these texts in every later log line; a path also hides its file name and its name without extension.

    Cheap to call often: the combined pattern is built once, at the next line that is written.
    """
    global _dirty  # noqa: PLW0603 - one registry for the whole process
    found: set[str] = set()
    for text in texts:
        if text:
            found |= _forms(text)
    found = {t for t in found if len(t.strip()) >= _MIN_LEN}
    with _lock:
        if not found <= _texts:
            _texts.update(found)
            _dirty = True


def registered() -> frozenset[str]:
    with _lock:
        return frozenset(_texts)


def _current() -> re.Pattern[str] | None:
    global _pattern, _dirty  # noqa: PLW0603
    with _lock:
        if _dirty:
            alternatives = "|".join(re.escape(t) for t in sorted(_texts, key=len, reverse=True))  # longest first
            _pattern = re.compile(rf"(?<!\w)(?:{alternatives})(?!\w)", re.IGNORECASE) if _texts else None
            _dirty = False
        return _pattern


def redact(text: str) -> str:
    text = _URL_QUERY.sub(rf"\1?{HIDDEN}", text)
    pattern = _current()
    return pattern.sub(HIDDEN, text) if pattern is not None else text


def forget_all() -> None:
    """For tests: start with an empty registry."""
    global _pattern, _dirty
    with _lock:
        _texts.clear()
        _pattern, _dirty = None, False


class Formatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


class RedactingStream:
    """A text stream that hides registered texts in whatever is written to it (used for stdout/stderr)."""

    def __init__(self, target: TextIO) -> None:
        self.target = target
        self._buf = ""

    def write(self, s: str) -> int:
        self._buf += s
        *lines, self._buf = self._buf.split("\n")  # whole lines only, so a name is never split across writes
        for line in lines:
            self.target.write(redact(line) + "\n")
        return len(s)

    def flush(self) -> None:
        if self._buf:
            self.target.write(redact(self._buf))
            self._buf = ""
        self.target.flush()

    def isatty(self) -> bool:
        return False

    @property
    def encoding(self) -> str:
        return self.target.encoding
