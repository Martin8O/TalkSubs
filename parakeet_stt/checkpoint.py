"""Recognition kept while a job runs, so an interrupted job goes on where it stopped instead of starting over.

One sealed file in the job's folder: the speech windows and the words of every window recognised so far. It is
used again only when everything that decides those words is the same - the code, the engine and its libraries,
the speech detector and its options, and the decoded sound itself (a digest of every sample). Anything else, or a
file that is damaged, foreign or does not hold together, is ignored and the job starts over. A resumed run gives
the same document as an uninterrupted one: every window is recognised on its own, and the saved times are the
very floats the engine gave (JSON keeps a float exactly).
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any, Final

import numpy as np
import numpy.typing as npt

from parakeet_stt import project
from parakeet_stt.engines.base import TimedSegment, TimedWord
from parakeet_stt.vad import JoinStats, Window
from parakeet_stt.vault import Role, Vault, VaultError

FILE: Final = "checkpoint.bin"
FORMAT: Final = 1
_PACKAGE = Path(__file__).resolve().parent


@cache
def code_digest() -> str:
    """Every Python file of the app: a changed line anywhere means no resume (subtitles are made later)."""
    h = hashlib.sha256()
    files = sorted(p for p in _PACKAGE.rglob("*.py") if "__pycache__" not in p.parts)
    for p in files:
        h.update(p.relative_to(_PACKAGE).as_posix().encode("utf-8") + b"\0")
        h.update(p.read_bytes() + b"\0")
    return h.hexdigest()


def audio_digest(audio: npt.NDArray[np.float32]) -> str:
    flat = np.ascontiguousarray(audio)
    return hashlib.sha256(memoryview(flat.view(np.uint8))).hexdigest()  # type: ignore[arg-type]  # no copy


@dataclass(frozen=True)
class Saved:
    windows: list[Window]
    joins: JoinStats
    done: list[TimedSegment]  # the first len(done) windows, recognised


def _warn(text: str) -> None:
    print(f"checkpoint: {text}", file=sys.stderr, flush=True)  # the worker's stderr goes to the app's log


class Checkpoint:
    def __init__(self, path: Path, vault: Vault) -> None:
        self.path = path
        self.vault = vault

    def load(self, key: dict[str, Any], samples: int) -> Saved | None:
        """What an earlier run of this very job left, or None (no file, another key, or not trustworthy)."""
        try:
            raw = self.vault.read(self.path, Role.CHECKPOINT)
        except FileNotFoundError:
            return None
        except (OSError, VaultError):
            _warn("the saved progress cannot be read; starting over")
            return None
        try:
            data = json.loads(raw.decode("utf-8"))
            if data.get("format") != FORMAT or data.get("key") != key:
                _warn("the saved progress belongs to another run; starting over")
                return None
            saved = _decode(data, samples)
        except (ValueError, TypeError, KeyError, AttributeError):  # json / shape errors: never trusted
            _warn("the saved progress does not hold together; starting over")
            return None
        _warn(f"going on after {len(saved.done)} of {len(saved.windows)} recognised windows")
        return saved

    def save(self, key: dict[str, Any], windows: list[Window], joins: JoinStats, done: list[TimedSegment]) -> None:
        """Write the progress so far; a failure (a full disk) is logged and the job goes on without it."""
        data = {
            "format": FORMAT,
            "key": key,
            "windows": [[w.start, w.end, w.own_start, w.own_end] for w in windows],
            "joins": [joins.windows, joins.forced_joins, joins.hard_splits],
            "done": [[[w.text, w.start_s, w.end_s, w.conf] for w in ts.words] for ts in done],
        }
        try:
            body = json.dumps(data, separators=(",", ":"), allow_nan=False).encode("utf-8")
            project.write_atomically({self.path: self.vault.seal(body, Role.CHECKPOINT)})
        except (OSError, VaultError, ValueError) as exc:
            _warn(f"the progress could not be saved ({exc.__class__.__name__}); the job goes on without it")

    def clear(self) -> None:
        clear(self.path.parent)


def clear(job_dir: Path) -> None:
    for name in (FILE, f".{FILE}.tmp"):
        (job_dir / name).unlink(missing_ok=True)


def _decode(data: dict[str, Any], samples: int) -> Saved:
    windows = [Window(*(_int(v) for v in w)) for w in data["windows"]]  # a wrong count raises TypeError
    joins = JoinStats(*(_int(v) for v in data["joins"]))
    if joins.windows != len(windows) or not windows:
        raise ValueError("window count")
    prev_own_end = 0
    for w in windows:
        if not (0 <= w.start <= w.own_start < w.own_end <= w.end <= samples) or w.own_start < prev_own_end:
            raise ValueError("window order")
        prev_own_end = w.own_end
    done_raw = data["done"]
    if not isinstance(done_raw, list) or len(done_raw) > len(windows):
        raise ValueError("done count")
    done = [TimedSegment(win, [_word(x) for x in words]) for win, words in zip(windows, done_raw, strict=False)]
    return Saved(windows, joins, done)


def _int(v: object) -> int:
    if not isinstance(v, int) or isinstance(v, bool):
        raise TypeError("not an int")
    return v


def _float(v: object) -> float:
    if not isinstance(v, float) or not math.isfinite(v):
        raise TypeError("not a float")
    return v


def _word(x: object) -> TimedWord:
    if not isinstance(x, list) or len(x) != 4 or not isinstance(x[0], str):
        raise TypeError("not a word")
    conf = None if x[3] is None else _float(x[3])
    return TimedWord(x[0], _float(x[1]), _float(x[2]), conf)
