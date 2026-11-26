"""The app with the Whisper engine chosen: its model is fetched on demand, a Whisper job waits for it, and the job
is told the spoken language and the user's terms. Parakeet jobs never wait for it."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from parakeet_stt import engines
from parakeet_stt.engines.whisper import WhisperEngine
from tests.conftest import MakeClip
from tests.test_server import ORIGIN, _Download, add, make_client, state_of, wait


@pytest.fixture
def data(tmp_path: Path) -> Path:
    d = tmp_path / "data"
    d.mkdir()
    return d


@pytest.fixture(autouse=True)
def whisper_offered(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """The engine is offered where faster-whisper is installed; these tests never load it (the fake worker runs)."""
    monkeypatch.setitem(engines.base._REGISTRY, WhisperEngine.name, WhisperEngine)
    yield


def _choose(c: TestClient, **changes: Any) -> dict[str, Any]:
    settings = c.get("/api/settings").json()["settings"]
    r = c.put("/api/settings", json={**settings, **changes}, headers=ORIGIN)
    return {"status": r.status_code, "body": r.json()}


def test_whisper_is_offered_and_the_spoken_language_is_checked(data: Path) -> None:
    with make_client(data, whisper_present=lambda: True) as c:
        assert "whisper" in c.get("/api/settings").json()["engines"]
        assert _choose(c, engine="whisper", speech_language="de")["status"] == 200
        assert c.get("/api/settings").json()["settings"]["speech_language"] == "de"
        assert _choose(c, speech_language="xx")["status"] >= 400


def test_a_whisper_job_waits_for_its_model_then_gets_the_language_and_the_terms(
    data: Path, make_clip: MakeClip, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    specs = tmp_path / "specs.jsonl"
    monkeypatch.setenv("FAKE_WORKER_SPECS", str(specs))
    dl = _Download()
    with make_client(data, whisper_present=lambda: dl.here, whisper_fetch=dl) as c:
        assert c.get("/api/status").json()["whisper_model"]["state"] == "absent"  # nothing starts by itself
        _choose(c, engine="whisper", speech_language="cs", glossary_text="Karmapa = Kamapa\nGampopa\n")
        a = add(c, make_clip("quick-a.wav", seconds=1))
        wait(lambda: c.get("/api/status").json()["whisper_model"]["done_bytes"] == 400)  # the job asked for it
        assert state_of(c, a["id"]) == "queued"  # and waits
        assert c.post("/api/whisper/fetch", json={}, headers=ORIGIN).status_code == 409  # already on its way
        dl.go.set()
        wait(lambda: state_of(c, a["id"]) == "done")
        assert c.get("/api/status").json()["whisper_model"]["state"] == "ready"
    sent = [json.loads(line) for line in specs.read_text(encoding="utf-8").splitlines()]
    job = next(s for s in sent if s.get("kind", "job") == "job")
    assert job["engine"] == "whisper" and job["speech_language"] == "cs"
    assert "Karmapa" in job["glossary_text"]
    assert dl.runs == 1


def test_parakeet_jobs_never_wait_for_the_whisper_model(
    data: Path, make_clip: MakeClip, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    specs = tmp_path / "specs.jsonl"
    monkeypatch.setenv("FAKE_WORKER_SPECS", str(specs))
    dl = _Download()
    with make_client(data, whisper_present=lambda: dl.here, whisper_fetch=dl) as c:
        a = add(c, make_clip("quick-a.wav", seconds=1))
        wait(lambda: state_of(c, a["id"]) == "done")
        assert c.get("/api/status").json()["whisper_model"]["state"] == "absent" and dl.runs == 0
    job = next(json.loads(s) for s in specs.read_text(encoding="utf-8").splitlines() if '"engine"' in s)
    assert job["engine"] == "parakeet" and job["speech_language"] is None


def test_the_user_can_fetch_the_whisper_model_from_settings(data: Path) -> None:
    dl = _Download()
    with make_client(data, whisper_present=lambda: dl.here, whisper_fetch=dl) as c:
        assert c.post("/api/whisper/fetch", json={}, headers=ORIGIN).status_code == 204
        wait(lambda: c.get("/api/status").json()["whisper_model"]["done_bytes"] == 400)
        dl.go.set()
        wait(lambda: c.get("/api/status").json()["whisper_model"]["state"] == "ready")
        assert c.post("/api/whisper/fetch", json={}, headers=ORIGIN).status_code == 409  # here already
