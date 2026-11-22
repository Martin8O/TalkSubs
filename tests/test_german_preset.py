"""The German preset: German word lists decide where a cue may end."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from parakeet_stt import cli, presets
from parakeet_stt.segment import Segmenter
from parakeet_stt.transcript import FrameRate, Word


def _segmenter() -> Segmenter:
    preset = presets.get("de-template")
    return Segmenter(preset, presets.language(preset.language), FrameRate(num=25, den=1))


def test_the_german_preset_uses_the_german_lists_and_keeps_capitals() -> None:
    preset = presets.get("de-template")
    assert preset.language == "de"
    assert preset.text.window_capitals is False  # German nouns are capitalised
    lang = presets.language("de")
    assert {"die", "und", "mit", "ich", "ist"} <= set(lang.no_break_after)
    assert {"weil", "dass", "und"} <= set(lang.break_before)


def test_a_cue_never_ends_on_a_german_article_or_preposition_when_a_better_place_exists() -> None:
    seg = _segmenter()
    noun = seg._prep(Word(text="Menschen", start_ms=300, end_ms=700))  # 100 ms after "die": no pause
    die = seg._prep(Word(text="die", start_ms=0, end_ms=200))
    mit = seg._prep(Word(text="mit", start_ms=0, end_ms=200))
    und = seg._prep(Word(text="und", start_ms=800, end_ms=950))  # 100 ms after the noun
    assert seg.break_cost(die, noun) == seg.w.cost_forbidden
    assert seg.break_cost(mit, noun) == seg.w.cost_forbidden
    assert seg.break_cost(noun, und) < seg.break_cost(die, noun)  # ending before "und" is fine


def test_rechunk_with_the_german_preset_runs(tmp_path: Path) -> None:
    sentence = [
        "Das",
        "ist",
        "die",
        "Ursache",
        "und",
        "die",
        "Wirkung",
        "von",
        "allem",
        "was",
        "wir",
        "tun",
        "und",
        "lassen.",
    ]
    words = [{"word": w, "start": i * 0.4, "end": i * 0.4 + 0.3, "score": 0.9} for i, w in enumerate(sentence * 6)]
    wx = tmp_path / "talk.json"
    wx.write_text(json.dumps({"segments": [{"start": 0, "end": 40, "text": "x", "words": words}]}), encoding="utf-8")
    media_file = tmp_path / "talk.mp3"
    silence = ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono", "-t", "40", str(media_file)]
    subprocess.run(silence, check=True)
    out = tmp_path / "out"
    out.mkdir()
    args = ["rechunk", str(wx), str(media_file), "-o", str(out), "--preset", "de-template", "--layout", "plain"]
    assert cli.main(args) == cli.EXIT_OK
    srt = (out / "talk.srt").read_text(encoding="utf-8")
    assert "Ursache" in srt
