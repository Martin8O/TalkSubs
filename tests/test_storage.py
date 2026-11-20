"""The app's sealed store end to end: nothing it keeps is readable as plain text, older plain data is sealed once,
and a store whose key is missing or foreign is locked (and can be started fresh) instead of guessed at."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest

from parakeet_stt import cli, preview, project, vault
from parakeet_stt.server import storage
from parakeet_stt.server.messages import FileSource, Job, QueueFile, UploadSource
from parakeet_stt.transcript import Document, Segment
from parakeet_stt.vault import Role, Vault
from tests import factories
from tests.conftest import MakeClip, unsealed
from tests.test_segment import stream
from tests.test_server import ORIGIN, jobs, make_client, state_of, wait

PHRASE = "Quokka Marmalade Zeppelin"


def assert_all_sealed(data: Path) -> None:
    """The privacy claim, machine-checked: every file in the data folder is sealed unless it is plain by design."""
    left = storage.plain_files(data)
    assert left == [], f"plain files in the app's store: {[str(p.relative_to(data)) for p in left]}"


def assert_nowhere(data: Path, needles: list[bytes]) -> None:
    for p in data.rglob("*"):
        if p.is_file() and p.relative_to(data).parts[0] not in ("subtitles",):
            raw = p.read_bytes()
            for n in needles:
                assert n not in raw, f"{n[:30]!r} readable in {p.relative_to(data)}"


def secret_project(folder: Path) -> Path:
    words = stream(3, n=200)
    words = [w.model_copy(update={"text": t}) for w, t in zip(words, PHRASE.split(), strict=False)] + words[3:]
    seg = Segment(start_ms=words[0].start_ms, end_ms=words[-1].end_ms, words=words)
    end = words[-1].end_ms + 5000
    doc = Document(app_version="0.1.0", media=factories.media(duration_ms=end, decoded_ms=end),
                   run_config=factories.run_config(), segments=[seg])  # fmt: skip
    path = folder / "secret.pstt.json"
    folder.mkdir(parents=True, exist_ok=True)
    project.save(doc, path)
    assert b"Marmalade" in path.read_bytes()  # each word is its own entry in the file
    return path


@pytest.fixture
def data(tmp_path: Path) -> Path:
    d = tmp_path / "data"
    d.mkdir()
    return d


def test_a_whole_session_leaves_nothing_readable_in_the_store(
    data: Path, tmp_path: Path, make_clip: MakeClip, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Drop a file, transcribe, preview, edit, export, restart: the phrase of the transcript and a run of the
    dropped file's bytes are nowhere in the data folder; the export (plain on purpose) has the phrase."""
    monkeypatch.setenv("FAKE_WORKER_PROJECT", str(secret_project(tmp_path / "ref")))
    clip = make_clip("lecture.mp4", seconds=3, video_rate="25")
    body = clip.read_bytes()
    out = tmp_path / "exports"
    out.mkdir()
    with make_client(data, kill_after_s=1.0) as c:
        r = c.put("/api/uploads", content=body, headers={**ORIGIN, "content-type": "application/octet-stream",
                                                         "x-file-name": quote("lecture.mp4")})  # fmt: skip
        assert r.status_code == 201, r.text
        job = r.json()
        wait(lambda: state_of(c, job["id"]) == "done")
        wait(lambda: (next(j for j in jobs(c) if j["id"] == job["id"])["preview"] or {}).get("state") == "ready",
             timeout=60)  # fmt: skip
        assert len(c.get(f"/api/jobs/{job['id']}/media", headers={"range": "bytes=0-99"}).content) == 100
        view = c.get(f"/api/jobs/{job['id']}/tracks/cz-template").json()
        cues = view["track"]["cues"]
        cues[0]["lines"] = [PHRASE + " edited"]
        r = c.put(
            f"/api/jobs/{job['id']}/tracks/cz-template",
            headers=ORIGIN,
            json={"base_revision": view["revision"], "cues": cues, "next_cue_id": view["track"]["next_cue_id"]},
        )
        assert r.status_code == 200, r.text
        settings = c.get("/api/settings").json()["settings"]
        r = c.put("/api/settings", json={**settings, "output": "folder", "output_dir": str(out)}, headers=ORIGIN)
        assert r.status_code == 200, r.text
        r = c.post(f"/api/jobs/{job['id']}/export", json={"presets": ["cz-template"], "formats": ["srt", "txt"]},
                   headers=ORIGIN)  # fmt: skip
        assert r.status_code == 200, r.text
    with make_client(data, kill_after_s=1.0) as c:  # a restart opens everything again
        assert state_of(c, job["id"]) == "done"
        assert PHRASE in json.dumps(c.get(f"/api/jobs/{job['id']}/tracks/cz-template").json())
    assert_all_sealed(data)
    assert_nowhere(data, [PHRASE.encode(), b"Marmalade", body[1000:1064], str(out).encode()])
    srt = next(out.glob("*.srt")).read_text(encoding="utf-8")
    assert PHRASE + " edited" in srt


def test_a_plain_file_planted_anywhere_in_the_store_is_found(data: Path) -> None:
    Vault.create_or_open(data)
    (data / "logs").mkdir()
    (data / "logs" / "app.log").write_text("plain by design", encoding="utf-8")
    (data / "subtitles").mkdir()
    (data / "subtitles" / "talk.srt").write_text("plain by design", encoding="utf-8")
    (data / ".parakeet-stt").touch()  # the folder's own (empty) marker
    assert storage.plain_files(data) == []
    planted = data / "jobs" / "0123456789ab" / "talk.pstt.json"
    planted.parent.mkdir(parents=True)
    planted.write_text("{}", encoding="utf-8")
    stray = data / "notes.txt"
    stray.write_text("x", encoding="utf-8")
    assert storage.plain_files(data) == [planted, stray]


# ---- one-time sealing of what an older version left plain -------------------------------------------------------


def _old_plain_folder(data: Path, tmp_path: Path, make_clip: MakeClip) -> dict[str, Any]:
    """A data folder as the version before encryption left it."""
    up_id, job_id, done_id = "aaaaaaaaaaaa", "bbbbbbbbbbbb", "cccccccccccc"
    clip = make_clip("old.wav", seconds=2)
    upload = data / "uploads" / up_id / "source.wav"
    upload.parent.mkdir(parents=True)
    shutil.copyfile(clip, upload)
    (data / "uploads" / up_id / "source.wav.part").write_bytes(b"half a sealing")
    proj = secret_project(data / "jobs" / done_id)
    old_preview = data / "jobs" / done_id / "preview"
    old_preview.mkdir()
    (old_preview / "preview.json").write_text('{"version": 1}', encoding="utf-8")
    (old_preview / "audio.m4a").write_bytes(b"plain proxy")
    queue = QueueFile(jobs=[
        Job(id=done_id, name="secret.wav", source=FileSource(path=str(clip)), created_at=1.0, state="done",
            project_file=proj.name),
        Job(id=job_id, name="old.wav", source=UploadSource(upload_id=up_id, path=str(upload), original_name="old.wav"),
            created_at=2.0, state="cancelled"),
    ])  # fmt: skip
    (data / "queue.json").write_text(queue.model_dump_json(indent=1), encoding="utf-8")
    (data / "settings.json").write_text('{"schema_version": 1, "look": "dark"}', encoding="utf-8")
    return {"upload": upload, "clip": clip, "project": proj, "proj_bytes": proj.read_bytes(), "done": done_id,
            "queue": queue}  # fmt: skip


def test_an_older_plain_store_is_sealed_once_and_keeps_everything(
    data: Path, tmp_path: Path, make_clip: MakeClip
) -> None:
    old = _old_plain_folder(data, tmp_path, make_clip)
    opened = storage.open_store(data)
    assert opened.vault is not None and opened.left_plain == 0
    assert_all_sealed(data)
    assert unsealed(old["upload"], Role.SOURCE) == old["clip"].read_bytes()
    assert unsealed(old["project"], Role.PROJECT) == old["proj_bytes"]
    assert QueueFile.model_validate_json(unsealed(data / "queue.json", Role.QUEUE)) == old["queue"]
    assert json.loads(unsealed(data / "settings.json", Role.SETTINGS))["look"] == "dark"
    assert not (data / "jobs" / old["done"] / "preview").exists()  # made again, sealed, by the app
    assert not (old["upload"].parent / "source.wav.part").exists()
    stamps = {p: p.stat().st_mtime_ns for p in data.rglob("*") if p.is_file()}
    storage.open_store(data)  # a second start changes nothing
    assert {p: p.stat().st_mtime_ns for p in data.rglob("*") if p.is_file()} == stamps
    with make_client(data, kill_after_s=1.0) as c:  # and the app works on it
        assert state_of(c, old["done"]) == "done"
        assert c.get(f"/api/jobs/{old['done']}/words").status_code == 200
        wait(lambda: (next(j for j in jobs(c) if j["id"] == old["done"])["preview"] or {}).get("state") == "ready",
             timeout=60)  # fmt: skip
    assert_all_sealed(data)


def test_a_dropped_file_waits_plain_when_the_disk_is_too_full_to_seal_it(
    data: Path, tmp_path: Path, make_clip: MakeClip, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = _old_plain_folder(data, tmp_path, make_clip)
    monkeypatch.setattr(storage, "_free", lambda _p: 1024)
    opened = storage.open_store(data)
    assert opened.vault is not None and opened.left_plain == 1
    assert storage.plain_files(data) == [old["upload"]]  # everything small is sealed already
    monkeypatch.undo()
    assert storage.open_store(data).left_plain == 0
    assert_all_sealed(data)


# ---- locked ------------------------------------------------------------------------------------------------------


def test_another_accounts_store_is_locked_and_start_fresh_keeps_the_old_work(data: Path, tmp_path: Path) -> None:
    secret_project(tmp_path / "ref")
    mine = Vault.create_or_open(data)
    (data / "queue.json").write_bytes(mine.seal(QueueFile(jobs=[]).model_dump_json().encode(), Role.QUEUE))
    before = (data / "queue.json").read_bytes()
    assert storage.open_store(data, entropy=b"another account").vault is None
    with make_client(data, key_entropy=b"another account") as c:
        st = c.get("/api/status").json()
        assert st["storage_locked"] and st["jobs"] == []
        for path in ("/api/jobs/0123456789ab/media", "/api/jobs/0123456789ab/words"):
            r = c.get(path)
            assert r.status_code == 503 and "another Windows account" in r.json()["error"]
        assert c.get("/api/settings").status_code == 200  # the page still shows (defaults)
        assert c.post("/api/storage/start-fresh", json={}, headers=ORIGIN).status_code == 204
        assert not c.get("/api/status").json()["storage_locked"]
    old = next(p for p in data.iterdir() if p.name.startswith("old-"))
    assert (old / "queue.json").read_bytes() == before and (old / "key.dpapi").is_file()
    assert Vault.open(old).read(old / "queue.json", Role.QUEUE)  # the old work still opens with its own key


def test_a_missing_key_with_sealed_work_is_locked_not_replaced(data: Path) -> None:
    mine = Vault.create_or_open(data)
    (data / "queue.json").write_bytes(mine.seal(b'{"schema_version": 1, "jobs": []}', Role.QUEUE))
    (data / vault.KEY_FILE).unlink()
    (data / vault.KEY_BACKUP).unlink()
    assert storage.open_store(data).vault is None
    assert not vault.key_exists(data)  # no new key was made over the old work


def test_a_key_from_another_data_folder_is_locked(data: Path, tmp_path: Path) -> None:
    mine = Vault.create_or_open(data)
    (data / "queue.json").write_bytes(mine.seal(b'{"schema_version": 1, "jobs": []}', Role.QUEUE))
    Vault.create_or_open(tmp_path / "other")
    for name in (vault.KEY_FILE, vault.KEY_BACKUP):
        shutil.copyfile(tmp_path / "other" / name, data / name)
    assert storage.open_store(data).vault is None


# ---- the command line on the app's own project files -------------------------------------------------------------


def test_the_cli_exports_a_sealed_project_and_keeps_it_sealed(data: Path, tmp_path: Path) -> None:
    key = Vault.create_or_open(data)
    folder = data / "jobs" / "0123456789ab"
    plain = secret_project(tmp_path / "ref")
    sealed = folder / "talk.pstt.json"
    project.save(project.load(plain), sealed, key)
    out = tmp_path / "out"
    out.mkdir()
    assert cli.main(["export", str(sealed), "--preset", "all", "--format", "srt", "-o", str(out)]) == cli.EXIT_OK
    assert PHRASE in next(out.glob("*.cz-template.srt")).read_text(encoding="utf-8")
    assert vault.is_sealed(sealed) and [t.preset_id for t in project.load(sealed, key).subtitles] == [
        "cz-template", "en-subtitles"]  # fmt: skip
    # plain subtitle files never go into the app's own store - only into its own export folder
    for inside in (folder, data, data / "uploads"):
        assert cli.main(["export", str(sealed), "--format", "srt", "-o", str(inside)]) == cli.EXIT_USAGE, inside
    assert cli.main(["export", str(sealed), "--format", "srt"]) == cli.EXIT_USAGE  # beside it = inside jobs/
    (data / "subtitles").mkdir()
    assert cli.main(["export", str(sealed), "--format", "srt", "-o", str(data / "subtitles")]) == cli.EXIT_OK
    assert_all_sealed(data)


def test_the_cli_reseals_a_plain_project_an_older_version_left_in_the_store(data: Path, tmp_path: Path) -> None:
    key = Vault.create_or_open(data)
    left = data / "jobs" / "0123456789ab" / "talk.pstt.json"
    left.parent.mkdir(parents=True)
    shutil.copyfile(secret_project(tmp_path / "ref"), left)
    out = tmp_path / "out"
    out.mkdir()
    assert cli.main(["export", str(left), "--preset", "all", "--format", "srt", "-o", str(out)]) == cli.EXIT_OK
    assert vault.is_sealed(left) and project.load(left, key).subtitles


def test_cli_transcribe_refuses_the_store_as_output_and_names_a_missing_file(
    data: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    Vault.create_or_open(data)
    (data / "jobs").mkdir()
    clip = tmp_path / "x.wav"
    clip.write_bytes(b"refused before it is read")
    for inside in (data, data / "jobs"):
        assert cli.main(["transcribe", str(clip), "-o", str(inside)]) == cli.EXIT_USAGE
        assert "inside the app's own store" in capsys.readouterr().err
    assert cli.main(["transcribe", str(tmp_path / "gone.wav"), "-o", str(tmp_path)]) == cli.EXIT_USAGE
    assert "not found" in capsys.readouterr().err.lower()


def test_settings_refuse_an_output_folder_inside_the_store(data: Path, tmp_path: Path) -> None:
    jobs_dir = data / "jobs" / "0123456789ab"
    jobs_dir.mkdir(parents=True)
    (jobs_dir / "talk.pstt.json").write_bytes(Vault.create_or_open(data).seal(b"{}", Role.PROJECT))
    (data / "subtitles").mkdir()
    with make_client(data) as c:
        s = c.get("/api/settings").json()["settings"]
        for inside in (data, data / "jobs", jobs_dir):
            r = c.put("/api/settings", json={**s, "output": "folder", "output_dir": str(inside)}, headers=ORIGIN)
            assert r.status_code == 400 and "encrypted store" in r.json()["error"], inside
        for fine in (data / "subtitles", tmp_path):
            r = c.put("/api/settings", json={**s, "output": "folder", "output_dir": str(fine)}, headers=ORIGIN)
            assert r.status_code == 200, (fine, r.text)


def test_the_preview_of_a_sealed_source_is_sealed_and_plays(data: Path, make_clip: MakeClip) -> None:
    key = Vault.create_or_open(data)
    clip = make_clip("v.mp4", seconds=2, video_rate="25")
    src = data / "uploads" / "0123456789ab" / "source.mp4"
    with key.writer(src, Role.SOURCE) as w:
        w.write(clip.read_bytes())
    info = preview.build(src, data / "jobs" / "aaaaaaaaaaaa", key)
    assert info.kind == "audio" and preview.read_ready(data / "jobs" / "aaaaaaaaaaaa", key) == info
    assert_all_sealed(data)
