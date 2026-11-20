"""The subtitle editor's routes: open a track, check unsaved cues, save, start over - on a finished job."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from parakeet_stt import project, rules
from parakeet_stt.transcript import Cue
from parakeet_stt.vault import Vault
from tests.conftest import MakeClip
from tests.test_cli_export import _project
from tests.test_server import ORIGIN, add, make_client, state_of, wait


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


@pytest.fixture
def job(client: TestClient, make_clip: MakeClip) -> dict[str, Any]:
    j = add(client, make_clip("talk.wav", seconds=1))
    wait(lambda: state_of(client, j["id"]) == "done")
    return j


def _file(data: Path, job: dict[str, Any]) -> Path:
    return next((data / "jobs" / str(job["id"])).glob("*.pstt.json"))


def _open(c: TestClient, job: dict[str, Any], preset: str = "cz-template") -> dict[str, Any]:
    r = c.get(f"/api/jobs/{job['id']}/tracks/{preset}")
    assert r.status_code == 200, r.text
    return dict(r.json())


def _save(c: TestClient, job: dict[str, Any], body: dict[str, Any], preset: str = "cz-template") -> Any:
    return c.put(f"/api/jobs/{job['id']}/tracks/{preset}", json=body, headers=ORIGIN)


def test_open_makes_the_track_once_and_keeps_it(client: TestClient, job: dict[str, Any], data: Path) -> None:
    view = _open(client, job)
    track = view["track"]
    assert track["preset_id"] == "cz-template" and not track["edited"] and len(track["cues"]) > 3
    stored = project.load(_file(data, job)).track("cz-template")
    assert stored is not None and [c.model_dump() for c in stored.cues] == track["cues"]
    assert _open(client, job)["revision"] == view["revision"]  # nothing changed: same fingerprint
    assert view["media_end_ms"] == project.load(_file(data, job)).media.decoded_ms
    expected = rules.check_track(stored, view["media_end_ms"])
    assert view["violations"] == [v.model_dump() for v in expected]


def test_save_marks_edited_refuses_a_stale_base_and_never_reuses_ids(
    client: TestClient, job: dict[str, Any], data: Path
) -> None:
    view = _open(client, job)
    cues = view["track"]["cues"]
    first, rest = cues[0], cues[2:]  # the second cue is deleted, the first gets new text
    edited = [{**first, "lines": ["Hello there,", "edited line"]}, *rest]
    r = _save(client, job, {"base_revision": view["revision"], "cues": edited, "next_cue_id": 1})
    assert r.status_code == 200, r.text
    saved = r.json()
    assert saved["revision"] != view["revision"] and saved["next_cue_id"] == view["track"]["next_cue_id"]
    stored = project.load(_file(data, job)).track("cz-template")
    assert stored is not None and stored.edited and stored.cues[0].lines == ["Hello there,", "edited line"]
    assert [c.id for c in stored.cues] == [c["id"] for c in edited]

    stale = _save(client, job, {"base_revision": view["revision"], "cues": edited, "next_cue_id": 1})
    assert stale.status_code == 409 and "changed elsewhere" in stale.json()["error"]

    again = _open(client, job)
    assert again["revision"] == saved["revision"] and again["track"]["edited"]


def test_a_save_that_breaks_the_contract_is_refused_with_a_sentence(client: TestClient, job: dict[str, Any]) -> None:
    view = _open(client, job)
    cues = view["track"]["cues"]
    late = [*cues[:-1], {**cues[-1], "end_ms": view["media_end_ms"] + 1000}]
    r = _save(client, job, {"base_revision": view["revision"], "cues": late, "next_cue_id": 1})
    assert r.status_code == 422 and "past the media" in r.json()["error"]
    backwards = [{**cues[0], "end_ms": cues[0]["start_ms"]}, *cues[1:]]
    r = _save(client, job, {"base_revision": view["revision"], "cues": backwards, "next_cue_id": 1})
    assert r.status_code == 422 and r.json()["error"].startswith("Invalid request")
    assert _open(client, job)["revision"] == view["revision"]  # nothing was written


def test_check_judges_unsaved_cues_exactly_like_the_rule_checker(client: TestClient, job: dict[str, Any]) -> None:
    view = _open(client, job)
    cues = view["track"]["cues"]
    a, b = cues[0], cues[1]
    long_line = "x" * 60
    changed = [{**a, "lines": [long_line]}, {**b, "start_ms": a["end_ms"] - 200}, *cues[2:]]
    r = client.post(f"/api/jobs/{job['id']}/tracks/cz-template/check", json={"cues": changed}, headers=ORIGIN)
    assert r.status_code == 200, r.text
    got = r.json()["violations"]
    track = view["track"]
    from parakeet_stt.presets import Preset
    from parakeet_stt.transcript import FrameRate

    expected = rules.check(
        [Cue.model_validate(c) for c in changed], Preset.model_validate(track["preset"]),
        FrameRate.model_validate(track["frame_rate"]), view["media_end_ms"],
    )  # fmt: skip
    assert got == [v.model_dump() for v in expected]
    assert {v["rule"] for v in got} >= {"chars_per_line", "overlap"}
    assert _open(client, job)["revision"] == view["revision"]  # a check saves nothing


def test_start_over_gives_new_ids_and_an_untouched_track(client: TestClient, job: dict[str, Any], data: Path) -> None:
    view = _open(client, job)
    cues = view["track"]["cues"]
    saved = _save(client, job, {"base_revision": view["revision"], "cues": cues[1:], "next_cue_id": 1}).json()
    r = client.post(
        f"/api/jobs/{job['id']}/tracks/cz-template/resegment", json={"base_revision": saved["revision"]}, headers=ORIGIN
    )
    assert r.status_code == 200, r.text
    fresh = r.json()["track"]
    start = view["track"]["next_cue_id"]
    assert [c["id"] for c in fresh["cues"]] == list(range(start, start + len(cues)))
    assert [(c["start_ms"], c["lines"]) for c in fresh["cues"]] == [(c["start_ms"], c["lines"]) for c in cues]
    assert not fresh["edited"] and fresh["next_cue_id"] == start + len(cues)
    stale = client.post(
        f"/api/jobs/{job['id']}/tracks/cz-template/resegment", json={"base_revision": saved["revision"]}, headers=ORIGIN
    )
    assert stale.status_code == 409


def test_export_after_an_edit_writes_the_edit(client: TestClient, job: dict[str, Any], tmp_path: Path) -> None:
    view = _open(client, job)
    cues = view["track"]["cues"]
    edited = [{**cues[0], "lines": ["A hand-made line"]}, *cues[1:]]
    assert _save(client, job, {"base_revision": view["revision"], "cues": edited, "next_cue_id": 1}).status_code == 200
    r = client.post(f"/api/jobs/{job['id']}/export", json={"presets": ["cz-template"], "formats": ["srt"]},
                    headers=ORIGIN)  # fmt: skip
    assert r.status_code == 200, r.text
    text = Path(r.json()["files"][0]).read_text(encoding="utf-8")
    assert "A hand-made line" in text
    assert _open(client, job)["track"]["edited"]  # the export kept the edited track as it was


def test_words_and_refusals(client: TestClient, job: dict[str, Any], make_clip: MakeClip) -> None:
    words = client.get(f"/api/jobs/{job['id']}/words").json()["words"]
    assert len(words) == 200 and all(w["end_ms"] >= w["start_ms"] for w in words)
    typed = client.get(f"/api/jobs/{job['id']}/tracks/cz-template/words").json()["words"]
    cue_words = " ".join(" ".join(c["lines"]) for c in _open(client, job)["track"]["cues"]).split()
    assert [w["text"] for w in typed] == cue_words  # word for word what the cues say, so splits keep exact times
    assert client.get(f"/api/jobs/{job['id']}/tracks/no-such").status_code == 404
    assert client.get("/api/jobs/000000000000/tracks/cz-template").status_code == 404
    big = {"base_revision": "x", "cues": [{"id": i + 1, "start_ms": i * 10, "end_ms": i * 10 + 5,
                                           "lines": ["y" * 40]} for i in range(3000)], "next_cue_id": 1}  # fmt: skip
    r = _save(client, job, big)
    assert r.status_code == 409  # large enough to pass the size check, refused for its stale base
    assert client.post(f"/api/jobs/{job['id']}/remove", json={}, headers=ORIGIN).status_code == 204
    assert client.get(f"/api/jobs/{job['id']}/tracks/cz-template").status_code == 404


def test_an_edited_track_whose_preset_changed_is_kept_and_said_so(
    client: TestClient, job: dict[str, Any], data: Path
) -> None:
    view = _open(client, job)
    cues = view["track"]["cues"]
    assert not view["preset_changed"]
    saved = _save(client, job, {"base_revision": view["revision"], "cues": cues[1:], "next_cue_id": 1}).json()
    path = _file(data, job)
    key = Vault.open(data)
    doc = project.load(path, key)
    track = doc.track("cz-template")
    assert track is not None
    project.save(doc.with_track(track.model_copy(update={"digest": "an older preset"})), path, key)
    again = _open(client, job)
    assert again["preset_changed"] and again["track"]["cues"] == cues[1:]
    assert again["revision"] != saved["revision"]  # the fingerprint covers the digest
    r = client.post(f"/api/jobs/{job['id']}/export", json={"presets": ["cz-template"], "formats": ["srt"]},
                    headers=ORIGIN)  # fmt: skip
    assert r.status_code == 200, r.text  # still exportable, with its own rules


def test_remove_waits_for_a_save_in_flight(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The project lock: a removal never deletes the folder under a save (nor does the save re-create it)."""
    import asyncio
    import threading

    from parakeet_stt.server import editing
    from parakeet_stt.server.hub import Hub
    from parakeet_stt.server.messages import Job, SaveTrackRequest
    from parakeet_stt.server.runner import Runner

    data = tmp_path / "data"
    folder = data / "jobs" / "0123456789ab"
    folder.mkdir(parents=True)
    path = folder / "talk.pstt.json"
    key = Vault.create_or_open(data)
    project.save(project.load(_project(tmp_path / "proj")), path, key)
    order: list[str] = []
    release = threading.Event()
    real_save = editing.save_track

    def slow_save(*args: Any, **kw: Any) -> editing.Opened:
        order.append("save start")
        release.wait(5)
        out = real_save(*args, **kw)
        order.append("save end")
        return out

    async def main() -> None:
        runner = Runner(data, Hub(), key, gpu_check=lambda: None)
        record = {"id": "0123456789ab", "name": "talk.wav", "created_at": 0.0, "engine": "parakeet", "state": "done",
                  "project_file": path.name, "source": {"kind": "file", "path": str(tmp_path / "t.wav")}}  # fmt: skip
        runner.jobs = [Job.model_validate(record)]
        opened = await runner.open_track("0123456789ab", "cz-template")
        monkeypatch.setattr(editing, "save_track", slow_save)
        req = SaveTrackRequest(base_revision=opened.revision, cues=list(opened.track.cues[1:]), next_cue_id=1)
        save = asyncio.create_task(runner.save_track("0123456789ab", "cz-template", req))
        while not order:
            await asyncio.sleep(0.01)
        gone = asyncio.create_task(runner.remove("0123456789ab"))
        await asyncio.sleep(0.2)
        order.append("folder still there" if folder.is_dir() else "folder deleted early")
        release.set()
        await save
        await gone

    asyncio.run(main())
    assert order == ["save start", "folder still there", "save end"]
    assert not folder.exists()  # deleted after the save, not re-created by it
