"""Turning sub-word tokens with frame times into whole words."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

BOUNDARY = "▁"  # SentencePiece word-boundary mark


@dataclass(frozen=True)
class RawWord:
    text: str
    start_s: float  # first token time
    last_token_s: float  # last token time (the word's end is derived from it by a calibrated offset)
    conf: float | None  # geometric mean of the token probabilities, 0..1


def words_from_tokens(
    tokens: Sequence[str], times_s: Sequence[float], logprobs: Sequence[float] | None = None
) -> list[RawWord]:
    """A token starting with the boundary mark opens a new word; a lone boundary token opens the next one."""
    if len(tokens) != len(times_s) or (logprobs is not None and len(logprobs) != len(tokens)):
        raise ValueError("tokens, times and logprobs must have the same length")
    words: list[RawWord] = []
    text = ""
    start = last = 0.0
    lps: list[float] = []
    open_next = True

    def flush() -> None:
        if text:
            conf = math.exp(sum(lps) / len(lps)) if lps else None
            words.append(RawWord(text, start, last, conf))

    for i, (tok, t) in enumerate(zip(tokens, times_s, strict=True)):
        piece = tok.replace(BOUNDARY, " ")
        if not piece.strip():
            open_next = True
            continue
        if open_next or piece.startswith(" "):
            flush()
            text, start, lps = piece.strip(), t, []
            open_next = False
        else:
            text += piece
        last = t
        if logprobs is not None:
            lps.append(logprobs[i])
    flush()
    return words
