"""The `export` command end to end on a saved project (no GPU)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from parakeet_stt import cli, project
from parakeet_stt.transcript import Document, Segment, Word
from tests import factories
from tests.test_segment import stream


def _project(tmp_path: Path) -> Path:
    words = stream(3, n=200)
    seg = Segment(start_ms=words[0].start_ms, end_ms=words[-1].end_ms, words=words)
    end = words[-1].end_ms + 5000
    doc = Document(app_version="0.1.0", media=factories.media(duration_ms=end, decoded_ms=end),
                   run_config=factories.run_config(), segments=[seg])  # fmt: skip
    path = tmp_path / "talk.pstt.json"
    project.save(doc, path)
    return path


def test_export_makes_every_format_for_every_preset_and_keeps_the_tracks(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _project(tmp_path)
    assert cli.main(["export", str(path), "--preset", "all", "--format", "all"]) == cli.EXIT_OK
    names = sorted(p.name for p in tmp_path.iterdir())
    for preset in ("cz-template", "de-template", "en-subtitles"):
        for fmt in ("srt", "vtt", "ass", "txt", "xlsx"):
            assert f"talk.{preset}.{fmt}" in names
    doc = project.load(path)
    assert [t.preset_id for t in doc.subtitles] == ["cz-template", "de-template", "en-subtitles"]
    assert "hard: none" in capsys.readouterr().err

    before = path.read_bytes()
    assert cli.main(["export", str(path), "--format", "srt"]) == cli.EXIT_OK
    assert path.read_bytes() == before  # the stored track still matches: reused, project untouched
    assert "(kept)" in capsys.readouterr().err


def test_a_stale_track_is_remade_but_an_edited_one_is_kept(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _project(tmp_path)
    assert cli.main(["export", str(path)]) == cli.EXIT_OK
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["subtitles"][0]["digest"] = "stale"
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert cli.main(["export", str(path)]) == cli.EXIT_OK
    assert "(made)" in capsys.readouterr().err

    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["subtitles"][0]["digest"], raw["subtitles"][0]["edited"] = "stale", True
    path.write_text(json.dumps(raw), encoding="utf-8")
    before = path.read_bytes()
    assert cli.main(["export", str(path)]) == cli.EXIT_OK
    assert "(kept, edited)" in capsys.readouterr().err
    assert path.read_bytes() == before  # nothing remade, nothing rewritten


def test_unknown_format_is_a_usage_error(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        cli.main(["export", str(_project(tmp_path)), "--format", "docx"])


def test_glossary_files_put_wrong_forms_right_in_the_files_and_the_stored_track(tmp_path: Path) -> None:
    texts = "Dnes mluvíme o Kamapa a o Diamond way a pak o bardo".split()
    words = [Word(text=t, start_ms=i * 400, end_ms=i * 400 + 300) for i, t in enumerate(texts)]
    seg = Segment(start_ms=0, end_ms=words[-1].end_ms, words=words)
    end = words[-1].end_ms + 2000
    doc = Document(app_version="0.1.0", media=factories.media(duration_ms=end, decoded_ms=end),
                   run_config=factories.run_config(), segments=[seg])  # fmt: skip
    path = tmp_path / "talk.pstt.json"
    project.save(doc, path)
    first, second = tmp_path / "a.txt", tmp_path / "b.txt"
    first.write_text("# notes\nKarmapa = Kamapa\n", encoding="utf-8-sig")  # a BOM, as Windows editors write
    second.write_text("Diamond Way\n", encoding="utf-8")  # a term alone: gets its capital
    args = ["export", str(path), "--glossary", str(first), "--glossary", str(second)]
    assert cli.main([*args, "--preset", "en-subtitles"]) == cli.EXIT_OK
    srt = (tmp_path / "talk.en-subtitles.srt").read_text(encoding="utf-8")
    assert "Karmapa" in srt and "Kamapa" not in srt
    assert "Diamond Way" in srt


def test_a_bad_or_missing_glossary_file_is_a_usage_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _project(tmp_path)
    assert cli.main(["export", str(path), "--glossary", str(tmp_path / "nope.txt")]) == cli.EXIT_USAGE
    assert "cannot be read" in capsys.readouterr().err
    bad = tmp_path / "bad.txt"
    bad.write_text("Karmapa =\n", encoding="utf-8")
    assert cli.main(["export", str(path), "--glossary", str(bad)]) == cli.EXIT_USAGE
    assert "Line 1" in capsys.readouterr().err


def test_plain_layout_writes_stem_srt_and_a_whisperx_shaped_json(tmp_path: Path) -> None:
    path = _project(tmp_path)
    gl = tmp_path / "gl.txt"
    gl.write_text("Karmapa = " + stream(3, n=200)[0].text + "\n", encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()
    assert cli.main(["export", str(path), "-o", str(out), "--layout", "plain", "--glossary", str(gl)]) == cli.EXIT_OK
    assert sorted(p.name for p in out.iterdir()) == ["talk.json", "talk.srt", "talk.txt", "talk.vtt"]
    for name in ("talk.srt", "talk.vtt", "talk.txt"):  # WhisperX style: no BOM, LF line ends
        raw = (out / name).read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf") and b"\r" not in raw, name
    data = json.loads((out / "talk.json").read_text(encoding="utf-8"))
    assert data["segments"]
    first = data["segments"][0]
    assert first["start"] < first["end"] and first["text"]
    assert {"word", "start", "end"} <= set(first["words"][0])
    assert data["segments"][0]["words"][0]["word"].startswith("Karmapa")
    assert sum(len(s["words"]) for s in data["segments"]) == 200  # every word lands in exactly one cue
