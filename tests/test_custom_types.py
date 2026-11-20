"""The user's own subtitle types: made from a standard or a built-in type, checked, used for subtitles, and removed
without losing the subtitles already made with them."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from parakeet_stt import presets, subtitles
from parakeet_stt.server import worker
from parakeet_stt.server.messages import Settings
from parakeet_stt.server.settings import SettingsError, book_of, check, check_choice
from tests.conftest import MakeClip
from tests.test_cli_export import _project
from tests.test_server import ORIGIN, add, make_client, state_of, wait


def own(pid: str = "my-slow", name: str = "Slow lectures", **limits: Any) -> presets.Preset:
    base = presets.standards()["jtp-style-czech"]
    return base.model_copy(update={"id": pid, "name": name, "limits": base.limits.model_copy(update=limits)})


# ---- the data -------------------------------------------------------------------------------------------------


def test_every_standard_is_a_valid_type_with_a_source_and_none_is_used_as_it_is() -> None:
    found = presets.standards()
    assert len(found) >= 8 and "netflix-style-english" in found and "jtp-style-czech" in found
    assert not set(found) & set(presets.names())  # starting points only, never in the built-in list
    for p in found.values():
        assert p.language == "en" and p.name and p.description


def test_a_type_keeps_within_bounds_the_segmenter_was_built_for() -> None:
    base = presets.get("cz-template").limits.model_dump()
    for field, bad in [("max_cps", 100.0), ("max_chars_per_line", 3), ("min_gap_frames", 500),
                       ("max_duration_ms", 60_000)]:  # fmt: skip
        with pytest.raises(ValidationError):
            presets.Limits.model_validate({**base, field: bad})


def test_a_new_name_is_not_a_new_rule() -> None:
    p, lang = own(), presets.language("en")
    renamed = p.model_copy(update={"name": "Another name", "description": "Another description"})
    assert presets.digest(p, lang) == presets.digest(renamed, lang)
    assert presets.digest(p, lang) != presets.digest(own(max_cps=12.0), lang)


def test_own_types_are_checked_and_can_be_ticked_in_the_same_save() -> None:
    check(Settings(custom_presets=[own()], export_presets=["cz-template", "my-slow"]))
    for bad, why in [
        (Settings(custom_presets=[own("cz-template")]), "cannot use"),
        (Settings(custom_presets=[own("slow")]), "cannot use"),
        (Settings(custom_presets=[own(), own(name="Twin")]), "share"),
        (Settings(custom_presets=[own(name="  ")]), "name"),
    ]:
        with pytest.raises(SettingsError, match=why):
            check(bad)
    with pytest.raises(SettingsError, match="Unknown subtitle preset"):  # a removed type is no longer offered
        check(Settings(retired_presets=[own()], export_presets=["my-slow"]))
    check_choice(["my-slow"], ["srt"], book_of(Settings(retired_presets=[own()])))  # ... but its subtitles open


def test_subtitles_of_an_own_type_are_made_and_kept_after_the_type_is_removed(tmp_path: Path) -> None:
    from parakeet_stt import project

    doc = project.load(_project(tmp_path / "p"))
    doc, reports = subtitles.ensure_tracks(doc, ["my-slow"], None, presets.book([own()]))
    made = reports[0].track
    assert made.preset.limits.max_cps == 17.0 and not reports[0].reused
    again, reports = subtitles.ensure_tracks(doc, ["my-slow"], None, presets.book())  # the type is gone
    assert reports[0].reused and reports[0].track == made and again == doc
    with pytest.raises(presets.PresetError):
        subtitles.ensure_tracks(doc, ["my-gone"], None, presets.book())


def test_the_worker_makes_each_type_on_its_own_and_reports_the_one_that_fails(tmp_path: Path) -> None:
    from parakeet_stt import project

    doc = project.load(_project(tmp_path / "p"))
    broken = own("my-broken", name="Broken").model_copy(update={"language": "xx"})  # no rules for this language
    spec = worker.JobSpec(source="x", out_dir="x", stem="x", engine="parakeet", data_dir="x",
                          presets=[own(), broken])  # fmt: skip
    made, note = worker._make_tracks(doc, spec)
    assert {t.preset_id for t in made.subtitles} == {*presets.names(), "my-slow"}
    assert note is not None and "Broken" in note


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


def test_an_own_type_is_saved_used_for_subtitles_and_survives_its_removal(
    client: TestClient, make_clip: MakeClip, tmp_path: Path
) -> None:
    view = client.get("/api/settings").json()
    assert {p["id"] for p in view["standards"]} >= {"ted-style-talks", "bbc-style"}
    assert {p["id"] for p in view["builtin_presets"]} == set(presets.names())
    mine = own().model_dump(mode="json")
    new = {**view["settings"], "custom_presets": [mine], "export_presets": ["cz-template", "my-slow"]}
    r = client.put("/api/settings", json=new, headers=ORIGIN)
    assert r.status_code == 200, r.text
    assert {(p["id"], p["kind"]) for p in r.json()["presets"]} >= {("my-slow", "custom"), ("cz-template", "builtin")}

    job = add(client, make_clip("talk.wav", seconds=1))
    wait(lambda: state_of(client, job["id"]) == "done")
    opened = client.get(f"/api/jobs/{job['id']}/tracks/my-slow")
    assert opened.status_code == 200, opened.text
    assert opened.json()["track"]["preset"]["name"] == "Slow lectures"

    # removed: offered no more, its subtitles still open and save
    gone = {**new, "custom_presets": [], "retired_presets": [mine], "export_presets": ["cz-template"]}
    r = client.put("/api/settings", json=gone, headers=ORIGIN)
    assert r.status_code == 200, r.text
    assert ("my-slow", "retired") in {(p["id"], p["kind"]) for p in r.json()["presets"]}
    assert client.get(f"/api/jobs/{job['id']}/tracks/my-slow").status_code == 200
    out = client.post(f"/api/jobs/{job['id']}/export", json={"presets": ["my-slow"], "formats": ["srt"]},
                      headers=ORIGIN)  # fmt: skip
    assert out.status_code == 200, out.text
    assert client.get(f"/api/jobs/{job['id']}/tracks/my-other").status_code == 404


def test_a_type_leaves_the_segmenter_room() -> None:
    base = own()
    with pytest.raises(ValidationError):  # shortest and longest too close
        presets.Limits.model_validate({**base.limits.model_dump(), "min_duration_ms": 6900})
    with pytest.raises(ValidationError):  # the lead-in eats the whole subtitle
        presets.Preset.model_validate({**base.model_dump(), "timing": {"lead_in_ms": 2000, "end_hold_ms": 500},
                                       "limits": {**base.limits.model_dump(), "max_duration_ms": 2000}})  # fmt: skip


def test_files_of_an_own_type_carry_its_name() -> None:
    from parakeet_stt import export

    assert export.file_name("talk", "cz-template", "srt") == "talk.cz-template.srt"
    assert export.file_name("talk", "my-4n0k5n6a", "srt", "Pomalé přednášky!") == "talk.pomale-prednasky-5n6a.srt"
    assert export.file_name("talk", "my-4n0k5n6a", "srt", "!!!") == "talk.type-5n6a.srt"


def test_an_older_settings_file_is_saved_with_todays_version(tmp_path: Path) -> None:
    import json

    from parakeet_stt.server.messages import SETTINGS_SCHEMA_VERSION
    from parakeet_stt.server.settings import SettingsStore
    from parakeet_stt.vault import Role, Vault

    vault = Vault.create_or_open(tmp_path)
    store = SettingsStore(tmp_path, vault)
    old = store.load().model_copy(update={"schema_version": 1, "custom_presets": [own()]})
    store.save(old)
    assert (
        json.loads(vault.read(tmp_path / "settings.json", Role.SETTINGS))["schema_version"] == SETTINGS_SCHEMA_VERSION
    )


def test_a_removed_type_makes_no_new_subtitles(client: TestClient, make_clip: MakeClip) -> None:
    view = client.get("/api/settings").json()
    gone = {**view["settings"], "retired_presets": [own().model_dump(mode="json")]}
    assert client.put("/api/settings", json=gone, headers=ORIGIN).status_code == 200
    job = add(client, make_clip("talk.wav", seconds=1))
    wait(lambda: state_of(client, job["id"]) == "done")
    opened = client.get(f"/api/jobs/{job['id']}/tracks/my-slow")
    assert opened.status_code == 404 and "no subtitles made with it" in opened.json()["error"]
    out = client.post(f"/api/jobs/{job['id']}/export", json={"presets": ["my-slow"], "formats": ["srt"]},
                      headers=ORIGIN)  # fmt: skip
    assert out.status_code >= 400 and "no subtitles made with it" in out.json()["error"]


def test_the_lecture_templates_carry_the_czech_lecture_subtitlers_values() -> None:
    found = presets.standards()
    calm, fast = found["lecture-style-czech"], found["lecture-style-czech-fast"]
    for p in (calm, fast):
        assert (p.limits.max_chars_per_line, p.limits.max_lines, p.limits.max_duration_ms) == (40, 2, 6000)
        assert p.text.drop_fillers
    assert (calm.limits.max_cps, calm.limits.min_duration_ms) == (15.0, 1500)
    assert (fast.limits.max_cps, fast.limits.min_duration_ms) == (17.0, 1330)


def test_types_are_listed_in_the_users_order_everywhere(client: TestClient) -> None:
    view = client.get("/api/settings").json()
    new = {**view["settings"], "custom_presets": [own().model_dump(mode="json")],
           "type_order": ["my-slow", "en-subtitles"]}  # fmt: skip
    r = client.put("/api/settings", json=new, headers=ORIGIN)
    assert r.status_code == 200, r.text
    assert [p["id"] for p in r.json()["presets"]] == ["my-slow", "en-subtitles", "cz-template"]  # unnamed: after
