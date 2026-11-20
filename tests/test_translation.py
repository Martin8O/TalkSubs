"""Translations typed in the editor: kept with the cues, written as their own subtitle files, never thrown away by
"start over"."""

from __future__ import annotations

import io
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from parakeet_stt import export, presets, project, subtitles
from parakeet_stt.transcript import Cue
from tests.conftest import MakeClip
from tests.test_cli_export import _project
from tests.test_server import ORIGIN, add, make_client, state_of, wait


def test_a_cleared_translation_is_no_translation() -> None:
    assert Cue(id=1, start_ms=0, end_ms=1000, lines=["a"], translation=["", "  "]).translation is None
    assert Cue(id=1, start_ms=0, end_ms=1000, lines=["a"], translation=["jedna", ""]).translation == ["jedna", ""]


def test_a_version_2_file_opens_without_translations(tmp_path: Path) -> None:
    path = _project(tmp_path / "p")
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["schema_version"] = 2
    path.write_text(json.dumps(raw), encoding="utf-8")
    doc = project.load(path)
    assert doc.schema_version == 3 and all(c.translation is None for t in doc.subtitles for c in t.cues)


def _translated_doc(tmp_path: Path) -> Any:
    doc = project.load(_project(tmp_path / "p"))
    doc, _ = subtitles.ensure_tracks(doc, ["cz-template", "en-subtitles"])
    track = doc.track("cz-template")
    assert track is not None and len(track.cues) >= 2
    cues = [track.cues[0].model_copy(update={"translation": ["První titulek"]}), *track.cues[1:]]
    return doc.with_track(track.model_copy(update={"cues": cues, "translation_language": "cs", "edited": True}))


def test_the_translation_is_written_as_its_own_files_with_untranslated_cues_left_out(tmp_path: Path) -> None:
    doc = _translated_doc(tmp_path)
    made, skipped = subtitles.files_for(doc, ["cz-template", "en-subtitles"], ["srt", "xlsx"],
                                        ["original", "translation"], tmp_path, "talk")  # fmt: skip
    names = sorted(p.name for p in made)
    assert "talk.cz-template.cs.srt" in names and "talk.cz-template.srt" in names
    assert not any(n.endswith(".cs.xlsx") for n in names)  # the spreadsheet holds both texts, once
    srt = made[tmp_path / "talk.cz-template.cs.srt"].decode("utf-8-sig")
    assert "První titulek" in srt and srt.count("-->") == 1  # the untranslated cues are not shown in English
    assert skipped == ["en-subtitles"]  # nothing of it is translated
    sheet = load_workbook(io.BytesIO(made[tmp_path / "talk.cz-template.xlsx"])).active
    assert sheet is not None and sheet.cell(row=2, column=7).value == "První titulek"
    assert sheet.cell(row=3, column=7).value in (None, "")


def test_a_translation_file_of_an_own_type_names_type_and_language() -> None:
    assert export.file_name("talk", "my-4n0k5n6a", "srt", "Pomalé", "cs") == "talk.pomale-5n6a.cs.srt"
    assert export.file_name("talk", "cz-template", "vtt", lang="sk") == "talk.cz-template.sk.vtt"


# ---- through the app ----------------------------------------------------------------------------------------


@pytest.fixture
def data(tmp_path: Path) -> Path:
    d = tmp_path / "data"
    d.mkdir()
    return d


@pytest.fixture
def client(data: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("FAKE_WORKER_PROJECT", str(_project(tmp_path / "proj")))
    with make_client(data, kill_after_s=1.0) as c:
        yield c


def test_a_typed_translation_is_saved_exported_and_protected_from_start_over(
    client: TestClient, make_clip: MakeClip, tmp_path: Path
) -> None:
    job = add(client, make_clip("talk.wav", seconds=1))
    wait(lambda: state_of(client, job["id"]) == "done")
    view = client.get(f"/api/jobs/{job['id']}/tracks/cz-template").json()
    cues = view["track"]["cues"]
    cues[0]["translation"] = ["Dobrý den"]
    body = {"base_revision": view["revision"], "cues": cues, "next_cue_id": 1, "translation_language": "cs"}
    saved = client.put(f"/api/jobs/{job['id']}/tracks/cz-template", json=body, headers=ORIGIN)
    assert saved.status_code == 200, saved.text
    stored = client.get(f"/api/jobs/{job['id']}/tracks/cz-template").json()["track"]
    assert stored["cues"][0]["translation"] == ["Dobrý den"] and stored["translation_language"] == "cs"

    out = client.post(f"/api/jobs/{job['id']}/export", headers=ORIGIN,
                      json={"presets": ["cz-template", "en-subtitles"], "formats": ["srt"],
                            "texts": ["translation"]})  # fmt: skip
    assert out.status_code == 200, out.text
    files = [Path(f).name for f in out.json()["files"]]
    assert any(f.endswith(".cz-template.cs.srt") for f in files) and out.json()["skipped"] == ["en-subtitles"]

    again = client.post(f"/api/jobs/{job['id']}/tracks/cz-template/resegment", headers=ORIGIN,
                        json={"base_revision": saved.json()["revision"]})  # fmt: skip
    assert again.status_code == 409 and "translation" in again.json()["error"]
    assert presets.names()  # the built-in types are untouched


def test_blank_lines_inside_a_translation_never_break_a_subtitle_file(tmp_path: Path) -> None:
    doc = _translated_doc(tmp_path)
    track = doc.track("cz-template")
    cues = [track.cues[0].model_copy(update={"translation": ["Dobrý", "", "den", ""]}), *track.cues[1:]]
    doc = doc.with_track(track.model_copy(update={"cues": cues}))
    made, _ = subtitles.files_for(doc, ["cz-template"], ["srt"], ["translation"], tmp_path, "talk")
    srt = made[tmp_path / "talk.cz-template.cs.srt"].decode("utf-8-sig")
    assert "Dobrý\r\nden\r\n" in srt and "\r\n\r\n" not in srt.strip()  # one block, its lines together


def test_a_spreadsheet_alone_reports_no_skipped_type(tmp_path: Path) -> None:
    doc = _translated_doc(tmp_path)
    _, skipped = subtitles.files_for(doc, ["en-subtitles"], ["xlsx"], ["original", "translation"], tmp_path, "t")
    assert skipped == []
    made, skipped = subtitles.files_for(doc, ["en-subtitles"], ["xlsx"], ["translation"], tmp_path, "t")
    assert made == {} and skipped == ["en-subtitles"]  # nothing written: said so
