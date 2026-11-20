"""The `export` command end to end on a saved project (no GPU)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from parakeet_stt import cli, project
from parakeet_stt.transcript import Document, Segment
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


def test_export_makes_every_format_for_both_presets_and_keeps_the_tracks(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _project(tmp_path)
    assert cli.main(["export", str(path), "--preset", "all", "--format", "all"]) == cli.EXIT_OK
    names = sorted(p.name for p in tmp_path.iterdir())
    for preset in ("cz-template", "en-subtitles"):
        for fmt in ("srt", "vtt", "ass", "txt", "xlsx"):
            assert f"talk.{preset}.{fmt}" in names
    doc = project.load(path)
    assert [t.preset_id for t in doc.subtitles] == ["cz-template", "en-subtitles"]
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
