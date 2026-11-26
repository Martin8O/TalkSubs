"""WhisperX words cut by this app's subtitle rules (`rechunk`)."""

from __future__ import annotations

import json
import subprocess
from itertools import pairwise
from pathlib import Path
from typing import Any

from parakeet_stt import cli, whisperx_import
from tests import factories


def _whisperx(seconds: int = 60) -> dict[str, Any]:
    """One 20 s chunk per WhisperX segment, as WhisperX writes them, with unaligned numbers."""
    words: list[dict[str, Any]] = []
    for i in range(seconds * 3):  # 3 words a second
        number = i % 50 == 7
        word: dict[str, Any] = {"word": ("zwölf" if number else "Wort") + ("." if i % 12 == 11 else "")}
        if not number:  # WhisperX leaves numbers unaligned: no times
            word.update(start=i / 3, end=i / 3 + 0.28, score=0.9)
        words.append(word)
    chunks = [words[i : i + 60] for i in range(0, len(words), 60)]
    return {
        "segments": [
            {"start": c[0].get("start", 0), "end": 20.0, "text": " ".join(str(x["word"]) for x in c), "words": c}
            for c in chunks
        ]
    }


def test_unaligned_words_take_the_gap_and_times_never_go_backwards() -> None:
    words = whisperx_import.words_from(_whisperx())
    assert len(words) == 180
    assert all(b.start_ms >= a.start_ms for a, b in pairwise(words))
    assert all(w.end_ms >= w.start_ms for w in words)


def test_rechunk_cuts_short_cues_and_keeps_the_whisperx_words(tmp_path: Path) -> None:
    doc = whisperx_import.document_from_whisperx(_whisperx(), factories.media(duration_ms=65_000, decoded_ms=65_000))
    assert sum(len(s.words) for s in doc.segments) == 180
    wx = tmp_path / "talk.whisperx.json"
    wx.write_text(json.dumps(_whisperx()), encoding="utf-8")
    media_file = tmp_path / "talk.mp3"
    out = tmp_path / "out"
    out.mkdir()
    silence = ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono", "-t", "65", str(media_file)]
    subprocess.run(silence, check=True)
    assert cli.main(["rechunk", str(wx), str(media_file), "-o", str(out), "--layout", "plain"]) == cli.EXIT_OK
    assert sorted(p.name for p in out.iterdir()) == ["talk.json", "talk.srt", "talk.txt", "talk.vtt"]
    segments = json.loads((out / "talk.json").read_text(encoding="utf-8"))["segments"]
    assert len(segments) > 3  # the 20 s sentence chunks are cut into cues
    assert max(s["end"] - s["start"] for s in segments) <= 7.0
    assert sum(len(s["words"]) for s in segments) == 180


def test_a_word_stretched_over_a_pause_is_capped() -> None:
    stretched = [
        {"word": "Hallo", "start": 0.0, "end": 0.4, "score": 0.9},
        {"word": "La.", "start": 0.5, "end": 11.5, "score": 0.9},
    ]
    words = whisperx_import.words_from({"segments": [{"start": 0, "end": 12, "text": "Hallo La.", "words": stretched}]})
    assert words[1].end_ms - words[1].start_ms <= 1200


def test_words_past_the_end_of_the_audio_are_clamped() -> None:
    data = {
        "segments": [
            {
                "start": 0,
                "end": 11,
                "text": "a b c",
                "words": [
                    {"word": "a", "start": 0.0, "end": 0.4},
                    {"word": "b", "start": 9.9, "end": 10.013},  # 13 ms past the audio
                    {"word": "c", "start": 10.5, "end": 10.8},  # entirely past it
                ],
            }
        ]
    }
    doc = whisperx_import.document_from_whisperx(data, factories.media(duration_ms=10_000, decoded_ms=10_000))
    assert [w.text for w in doc.words()] == ["a", "b"]
    assert max(w.end_ms for w in doc.words()) <= 10_000
