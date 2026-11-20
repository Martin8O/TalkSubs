from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from parakeet_stt import project
from parakeet_stt.transcript import SCHEMA_VERSION, Cue, Segment, Word
from tests import factories


def test_round_trip_is_lossless(tmp_path: Path) -> None:
    doc = factories.document()
    path = tmp_path / f"talk{project.SUFFIX}"
    project.save(doc, path)
    again = project.load(path)
    assert again == doc
    assert project.dumps(again) == path.read_text(encoding="utf-8")


def test_committed_schema_matches_the_model() -> None:
    assert project.SCHEMA_FILE.read_text(encoding="utf-8") == project.schema_text(), (
        "contract changed: run `python -m parakeet_stt schema --write` and commit the schema with the change"
    )


def test_unknown_fields_are_refused() -> None:
    data = json.loads(project.dumps(factories.document()))
    data["media"]["speaker"] = "x"
    with pytest.raises(ValidationError):
        type(factories.document()).model_validate(data)


def _write(tmp_path: Path, data: object) -> Path:
    p = tmp_path / "x.pstt.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def test_newer_file_is_refused_readably(tmp_path: Path) -> None:
    data = json.loads(project.dumps(factories.document()))
    data["schema_version"] = SCHEMA_VERSION + 1
    data["cues"] = []
    with pytest.raises(project.ProjectError, match="newer version"):
        project.load(_write(tmp_path, data))


@pytest.mark.parametrize("bad", [None, "1", 0, True])
def test_missing_or_bad_version_is_refused_readably(tmp_path: Path, bad: object) -> None:
    data = json.loads(project.dumps(factories.document()))
    data["schema_version"] = bad
    with pytest.raises(project.ProjectError, match="no valid schema_version"):
        project.load(_write(tmp_path, data))


def test_not_json_and_damaged_files(tmp_path: Path) -> None:
    p = tmp_path / "a.pstt.json"
    p.write_text("{oops", encoding="utf-8")
    with pytest.raises(project.ProjectError, match="not valid JSON"):
        project.load(p)
    data = json.loads(project.dumps(factories.document()))
    data["segments"][0]["words"][0]["end_ms"] = 1
    with pytest.raises(project.ProjectError, match="damaged"):
        project.load(_write(tmp_path, data))


def test_words_must_stay_inside_their_segment_and_in_order() -> None:
    with pytest.raises(ValidationError, match="after its segment"):
        Segment(start_ms=0, end_ms=100, words=[Word(text="a", start_ms=50, end_ms=150)])
    with pytest.raises(ValidationError, match="out of order"):
        Segment(
            start_ms=0,
            end_ms=500,
            words=[Word(text="b", start_ms=300, end_ms=350), Word(text="a", start_ms=100, end_ms=200)],
        )
    with pytest.raises(ValidationError, match="ends"):
        Word(text="a", start_ms=10, end_ms=5)


def test_segments_must_not_overlap() -> None:
    a = Segment(start_ms=0, end_ms=1000, words=[])
    b = Segment(start_ms=900, end_ms=2000, words=[])
    with pytest.raises(ValidationError, match="overlaps"):
        factories.document([a, b])
    factories.document([a, Segment(start_ms=1000, end_ms=2000, words=[])])  # touching is fine


def test_atomic_write_leaves_no_temp_files(tmp_path: Path) -> None:
    a, b = tmp_path / "a.txt", tmp_path / "sub" / "b.txt"
    project.write_atomically({a: "one", b: "two"})
    assert a.read_text(encoding="utf-8") == "one" and b.read_text(encoding="utf-8") == "two"
    assert sorted(p.name for p in tmp_path.rglob("*")) == ["a.txt", "b.txt", "sub"]


def test_a_version_1_file_opens_with_no_subtitles(tmp_path: Path) -> None:
    raw = json.loads(project.dumps(factories.document()))
    raw["schema_version"] = 1
    del raw["subtitles"]
    path = tmp_path / "old.pstt.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    doc = project.load(path)
    assert doc.schema_version == SCHEMA_VERSION == 3 and doc.subtitles == []
    assert doc.segments == factories.document().segments


def test_tracks_round_trip_and_replace_by_preset(tmp_path: Path) -> None:
    doc = factories.document().with_track(factories.track()).with_track(factories.track(preset_id="en-subtitles"))
    path = tmp_path / "t.pstt.json"
    project.save(doc, path)
    assert project.load(path) == doc
    again = doc.with_track(factories.track(cues=[Cue(id=7, start_ms=0, end_ms=40, lines=["Hi."])]))
    assert [t.preset_id for t in again.subtitles] == ["en-subtitles", "cz-template"]
    track = again.track("cz-template")
    assert track is not None and [c.id for c in track.cues] == [7]


def test_the_contract_accepts_half_done_edits_but_not_broken_order() -> None:
    # overlap and empty text are the checker's business: an editor must be able to save them
    loose = [Cue(id=1, start_ms=1000, end_ms=3000, lines=[""]), Cue(id=2, start_ms=2000, end_ms=4000, lines=[])]
    factories.document().with_track(factories.track(cues=loose))
    with pytest.raises(ValidationError, match="before the previous cue's start"):
        factories.track(cues=[Cue(id=1, start_ms=2000, end_ms=3000, lines=["a"]),
                              Cue(id=2, start_ms=1000, end_ms=4000, lines=["b"])])  # fmt: skip
    with pytest.raises(ValidationError, match="used twice"):
        factories.track(
            cues=[Cue(id=1, start_ms=0, end_ms=40, lines=["a"]), Cue(id=1, start_ms=80, end_ms=120, lines=["b"])]
        )
    with pytest.raises(ValidationError, match="at or before its start"):
        Cue(id=1, start_ms=100, end_ms=100, lines=["a"])
    with pytest.raises(ValidationError, match="past the media"):
        factories.document().with_track(factories.track(cues=[Cue(id=1, start_ms=0, end_ms=70_000, lines=["a"])]))
    doc = factories.document().with_track(factories.track())
    with pytest.raises(ValidationError, match="two subtitle tracks"):
        type(doc).model_validate({**doc.model_dump(), "subtitles": [doc.subtitles[0].model_dump()] * 2})


def test_next_cue_id_stays_above_every_cue_and_preset_ids_agree() -> None:
    track = factories.track()
    assert track.next_cue_id == 4
    raw = track.model_dump()
    with pytest.raises(ValidationError, match="next_cue_id"):
        type(track).model_validate({**raw, "next_cue_id": 3})
    with pytest.raises(ValidationError, match="differs from its preset"):
        type(track).model_validate({**raw, "preset_id": "en-subtitles"})
    with pytest.raises(ValidationError, match="pattern"):
        type(track).model_validate({**raw, "preset_id": "../evil"})
