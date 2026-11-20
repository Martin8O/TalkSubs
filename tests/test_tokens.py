from __future__ import annotations

import math

import pytest

from parakeet_stt.engines.tokens import words_from_tokens


def test_boundary_marks_open_words() -> None:
    words = words_from_tokens(["▁The", "▁ess", "ence", "."], [0.0, 0.32, 0.4, 0.48])
    assert [w.text for w in words] == ["The", "essence."]
    assert words[1].start_s == 0.32
    assert words[1].last_token_s == 0.48


def test_lone_boundary_token_opens_the_next_word() -> None:
    # ignoring a lone boundary token glues words together ("theessence", "his16th")
    words = words_from_tokens(["▁his", "▁", "1", "6", "th"], [1.0, 1.2, 1.2, 1.28, 1.36])
    assert [w.text for w in words] == ["his", "16th"]
    assert words[1].start_s == 1.2


def test_first_token_without_mark_still_starts_a_word() -> None:
    assert [w.text for w in words_from_tokens(["ok", "ay"], [0.0, 0.08])] == ["okay"]


def test_confidence_is_geometric_mean_of_token_probabilities() -> None:
    (w,) = words_from_tokens(["▁ka", "gyu"], [0.0, 0.08], [math.log(0.9), math.log(0.4)])
    assert w.conf == pytest.approx(0.6)


def test_no_tokens_no_words() -> None:
    assert words_from_tokens([], []) == []


def test_lengths_must_match() -> None:
    with pytest.raises(ValueError):
        words_from_tokens(["▁a"], [0.0, 1.0])
