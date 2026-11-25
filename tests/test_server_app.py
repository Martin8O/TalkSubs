"""The window's app routes: settings, Windows dialogs, dropped-file uploads, export, reveal and remove."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from parakeet_stt import cli
from parakeet_stt.server import uploads
from parakeet_stt.vault import Role
from tests.conftest import MakeClip, key_of, unsealed
from tests.test_cli_export import _project
from tests.test_server import ORIGIN, add, jobs, make_client, state_of, wait

RAW = {**ORIGIN, "content-type": "application/octet-stream"}


def upload(c: TestClient, name: str, body: bytes, **headers: str) -> Any:
    return c.put("/api/uploads", content=body, headers={**RAW, "x-file-name": quote(name), **headers})


@pytest.fixture
def project_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = _project(tmp_path / "proj")
    monkeypatch.setenv("FAKE_WORKER_PROJECT", str(path))  # the fake worker "transcribes" into this document
    return path


@pytest.fixture
def shown() -> list[list[Path]]:
    return []


@pytest.fixture
def client(data: Path, shown: list[list[Path]]) -> Iterator[TestClient]:
    with make_client(data, kill_after_s=1.0, busy_retry_s=0.3, dialog=lambda kind: [f"C:/picked/{kind}.mp4"],
                     reveal=shown.append, upload_limit=lambda: 10_000_000) as c:  # fmt: skip
        yield c


@pytest.fixture
def data(tmp_path: Path) -> Path:
    d = tmp_path / "data"
    d.mkdir()
    return d


# ---- settings --------------------------------------------------------------------------------------------------


def test_settings_have_defaults_and_are_kept_on_the_server(client: TestClient, data: Path, tmp_path: Path) -> None:
    view = client.get("/api/settings").json()
    s = view["settings"]
    assert s["engine"] == "parakeet" and s["output"] == "beside_source" and s["output_dir"] is None
    assert s["export_presets"] == ["cz-template", "en-subtitles"] and s["export_formats"] == ["srt"]
    assert view["output_dir_default"] == str(data / "subtitles")
    assert {p["id"] for p in view["presets"]} == {"cz-template", "de-template", "en-subtitles"}
    assert s["language"] == "en"
    assert s["look"] == "light"
    cz = next(p for p in view["presets"] if p["id"] == "cz-template")
    assert (cz["max_chars_per_line"], cz["max_lines"], cz["max_cps"], cz["max_duration_ms"]) == (42, 2, 17.0, 7000)
    assert view["formats"] == ["srt", "vtt", "ass", "txt", "xlsx"] and view["engines"] == ["parakeet"]

    out = tmp_path / "subs"
    out.mkdir()
    new = {**s, "export_formats": ["srt", "xlsx"], "output": "folder", "output_dir": str(out), "look": "dark"}
    r = client.put("/api/settings", json=new, headers=ORIGIN)
    assert r.status_code == 200, r.text
    assert r.json()["settings"]["output_dir"] == str(out)
    assert json.loads(unsealed(data / "settings.json", Role.SETTINGS))["export_formats"] == ["srt", "xlsx"]
    assert client.get("/api/settings").json()["settings"] == new


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"engine": "nope"}, "Unknown engine"),
        ({"language": "de"}, "Invalid request"),
        ({"look": "pink"}, "Invalid request"),
        ({"export_presets": ["nope"]}, "Unknown subtitle preset"),
        ({"export_formats": ["doc"]}, "Unknown file type"),
        ({"export_formats": []}, "at least one"),
        ({"export_formats": ["srt", "srt"]}, "twice"),
        ({"output_dir": "relative\\folder"}, "drive of this computer"),
        ({"output_dir": "\\\\?\\C:\\Windows"}, "drive of this computer"),
        ({"output_dir": "\\\\server\\share"}, "drive of this computer"),
        ({"output_dir": "C:\\there\\is\\no\\such\\folder"}, "There is no folder"),
    ],
)
def test_bad_settings_are_refused(client: TestClient, change: dict[str, Any], message: str) -> None:
    s = client.get("/api/settings").json()["settings"]
    r = client.put("/api/settings", json={**s, **change}, headers=ORIGIN)
    assert r.status_code in (400, 422) and message in r.json()["error"], r.text


def test_settings_at_their_allowed_size_can_be_saved(client: TestClient) -> None:
    s = client.get("/api/settings").json()["settings"]
    terms = "\n".join(f"Ťermín{i} = Termín{i}" for i in range(8000))[:190_000]  # well past the 16 KiB default
    r = client.put("/api/settings", json={**s, "glossary_text": terms}, headers=ORIGIN)
    assert r.status_code == 200, r.text


def test_an_engine_an_earlier_version_offered_falls_back_to_the_default(data: Path) -> None:
    from parakeet_stt.server.settings import SettingsStore
    from parakeet_stt.vault import Vault

    vault = Vault.create_or_open(data)
    store = SettingsStore(data, vault)
    store.save(store.load())
    raw = json.loads(unsealed(data / "settings.json", Role.SETTINGS))
    old = json.dumps({**raw, "engine": "whisper"}).encode()  # what 1.0.0 could save
    (data / "settings.json").write_bytes(vault.seal(old, Role.SETTINGS))
    loaded = store.load()
    assert loaded.engine == "parakeet"
    store.save(loaded)  # and saving works again


def test_a_damaged_settings_file_says_so(data: Path) -> None:
    (data / "settings.json").write_text("{broken", encoding="utf-8")
    with make_client(data) as c:
        r = c.get("/api/settings")
    assert r.status_code == 400 and "damaged" in r.json()["error"]


# ---- dialogs ---------------------------------------------------------------------------------------------------


def test_dialog_returns_the_paths_and_only_one_opens_at_a_time(data: Path) -> None:
    entered, release = threading.Event(), threading.Event()

    def slow(kind: str) -> list[str]:
        entered.set()
        release.wait(5)
        return [f"D:/{kind}"]

    with make_client(data, dialog=slow) as c:
        answers: list[Any] = []
        t = threading.Thread(
            target=lambda: answers.append(c.post("/api/dialog", json={"kind": "files"}, headers=ORIGIN))
        )
        t.start()
        assert entered.wait(5)
        second = c.post("/api/dialog", json={"kind": "folder"}, headers=ORIGIN)
        release.set()
        t.join(5)
    assert second.status_code == 409 and "already open" in second.json()["error"]
    assert answers[0].status_code == 200 and answers[0].json() == {"paths": ["D:/files"]}


# ---- uploads ---------------------------------------------------------------------------------------------------


def test_a_dropped_file_is_copied_and_queued(client: TestClient, make_clip: MakeClip, data: Path) -> None:
    clip = make_clip("quick.wav", seconds=1)
    body = clip.read_bytes()
    r = upload(client, "My lecture — part 1.wav", body)
    assert r.status_code == 201, r.text
    job = r.json()
    src = job["source"]
    assert job["name"] == "My lecture — part 1.wav" and src["kind"] == "upload"
    copy = Path(src["path"])
    assert copy == data / "uploads" / src["upload_id"] / "source.wav" and unsealed(copy, Role.SOURCE) == body
    assert body[:64] not in copy.read_bytes()
    wait(lambda: state_of(client, job["id"]) == "done")


@pytest.mark.parametrize(
    ("headers", "body", "status"),
    [
        ({"origin": None}, b"x" * 10, 403),
        ({"content-type": "application/json"}, b"x" * 10, 415),
        ({"content-type": "text/plain"}, b"x" * 10, 415),
        ({}, b"", 400),
        ({}, b"x" * 10_000_001, 413),
    ],
)
def test_upload_refusals(client: TestClient, data: Path, headers: dict[str, Any], body: bytes, status: int) -> None:
    h = {**RAW, "x-file-name": "talk.wav", **headers}
    r = client.put("/api/uploads", content=body, headers={k: v for k, v in h.items() if v is not None})
    assert r.status_code == status, r.text
    assert not (data / "uploads").exists() or not any((data / "uploads").iterdir())


def test_a_no_room_refusal_says_how_much_is_free(client: TestClient) -> None:
    r = client.put("/api/uploads", content=b"x" * 10_000_001, headers={**RAW, "x-file-name": "talk.wav"})
    assert r.status_code == 413
    body = r.json()
    assert body["spare_bytes"] == uploads.SPARE_BYTES and isinstance(body["free_bytes"], int)


def test_the_upload_margin_lets_a_small_file_in_with_two_gb_free(tmp_path: Path, monkeypatch: Any) -> None:
    # about 2 GB free (Explorer's "2 GB") must still take a small lecture
    monkeypatch.setattr(uploads, "free_bytes", lambda _d: 2 * 1024**3 - 50 * 1024**2)
    assert uploads.limit(tmp_path) > 500 * 1024**2


def test_chunked_bodies_are_refused_everywhere(client: TestClient) -> None:
    def gen() -> Iterator[bytes]:
        yield b"x" * 100

    up = client.put("/api/uploads", content=gen(), headers={**RAW, "x-file-name": "a.wav"})
    js = client.post("/api/jobs", content=gen(), headers={**ORIGIN, "content-type": "application/json"})
    assert up.status_code == 411 and js.status_code == 411


def test_upload_names_are_checked(client: TestClient, make_clip: MakeClip) -> None:
    body = make_clip("quick.wav", seconds=1).read_bytes()
    assert upload(client, "notes.txt", body).status_code == 415
    assert client.put("/api/uploads", content=body, headers=RAW).status_code == 400  # no name
    r = upload(client, "..\\..\\CON.wav", body)
    assert r.status_code == 201 and r.json()["name"] == "_CON.wav"
    junk = upload(client, "fake.wav", b"this is not audio")
    assert junk.status_code == 422 and junk.json() == {"error": "fake.wav could not be read as audio or video."}


def test_safe_names() -> None:
    assert uploads.safe_name("a/b\\c:d?.mp4") == "c_d_.mp4"
    assert uploads.safe_name("evil\u202egnp.mp4") == "evilgnp.mp4"
    assert uploads.safe_name("trailing. . ") == "trailing"
    assert uploads.stem_of("com\u00b9.mp4") == "_com\u00b9"
    assert uploads.stem_of("LPT1 .part2.mp4") == "_LPT1 .part2"
    assert uploads.stem_of("conin$.wav") == "_conin$"
    assert uploads.stem_of("Talk.v2.mp4") == "Talk.v2"
    assert len(uploads.safe_name("x" * 400 + ".mp4")) <= uploads.MAX_NAME_CHARS


def test_leftover_uploads_are_swept_at_start(data: Path) -> None:
    orphan = data / "uploads" / "0123456789ab"
    orphan.mkdir(parents=True)
    (orphan / "source.part").write_bytes(b"half")
    with make_client(data):
        pass
    assert not orphan.exists()


# ---- export, reveal, remove ------------------------------------------------------------------------------------


def test_export_beside_the_source_matches_the_cli(
    client: TestClient, make_clip: MakeClip, project_file: Path, tmp_path: Path, shown: list[list[Path]]
) -> None:
    clip = make_clip("quick-talk.wav", seconds=1)
    job = add(client, clip)
    wait(lambda: state_of(client, job["id"]) == "done")
    req = {"presets": ["cz-template", "en-subtitles"], "formats": ["srt", "vtt", "ass", "txt", "xlsx"]}
    r = client.post(f"/api/jobs/{job['id']}/export", json=req, headers=ORIGIN)
    assert r.status_code == 200, r.text
    files = [Path(f) for f in r.json()["files"]]
    assert r.json()["folder"] == str(clip.parent) and len(files) == 10
    assert all(f.parent == clip.parent and f.name.startswith("quick-talk.") for f in files)

    ref = tmp_path / "cli"
    assert cli.main(["export", str(project_file), "--preset", "all", "--format", "all", "-o", str(ref)]) == 0
    for f in files:
        other = ref / f.name.replace("quick-talk.", "talk.", 1)
        if f.suffix == ".xlsx":  # the workbook records when it was saved (its zip entries and docProps/core.xml)
            assert _xlsx_parts(f) == _xlsx_parts(other), f.name
        else:
            assert f.read_bytes() == other.read_bytes(), f.name

    again = client.post(f"/api/jobs/{job['id']}/export", json=req, headers=ORIGIN)
    assert again.status_code == 409 and len(again.json()["existing"]) == 10
    assert (
        client.post(f"/api/jobs/{job['id']}/export", json={**req, "replace": True}, headers=ORIGIN).status_code == 200
    )
    assert next(j for j in jobs(client) if j["id"] == job["id"])["exported"] == [str(f) for f in files]

    assert client.post(f"/api/jobs/{job['id']}/reveal", json={}, headers=ORIGIN).status_code == 204
    assert shown == [files]


def _xlsx_parts(path: Path) -> dict[str, bytes]:
    import zipfile

    with zipfile.ZipFile(path) as z:
        return {n: z.read(n) for n in z.namelist() if n != "docProps/core.xml"}


def test_export_of_a_dropped_file_goes_to_the_output_folder(
    client: TestClient, make_clip: MakeClip, project_file: Path, data: Path
) -> None:
    job = upload(client, "Dropped.wav", make_clip("quick.wav", seconds=1).read_bytes()).json()
    wait(lambda: state_of(client, job["id"]) == "done")
    r = client.post(
        f"/api/jobs/{job['id']}/export", json={"presets": ["cz-template"], "formats": ["srt"]}, headers=ORIGIN
    )
    assert r.status_code == 200, r.text
    assert r.json()["files"] == [str(data / "subtitles" / "Dropped.cz-template.srt")]


def test_export_checks(client: TestClient, make_clip: MakeClip, project_file: Path) -> None:
    hold = add(client, make_clip("hold.wav", seconds=1))
    r = client.post(
        f"/api/jobs/{hold['id']}/export", json={"presets": ["cz-template"], "formats": ["srt"]}, headers=ORIGIN
    )
    assert r.status_code == 409
    for bad in ({"presets": ["nope"], "formats": ["srt"]}, {"presets": ["cz-template"], "formats": ["pdf"]}):
        r = client.post(f"/api/jobs/{hold['id']}/export", json=bad, headers=ORIGIN)
        assert r.status_code == 400, r.text
    assert client.post(f"/api/jobs/{hold['id']}/reveal", json={}, headers=ORIGIN).status_code == 409
    client.post(f"/api/jobs/{hold['id']}/cancel", json={}, headers=ORIGIN)


def test_remove_deletes_only_the_apps_own_files(
    client: TestClient, make_clip: MakeClip, project_file: Path, data: Path
) -> None:
    clip = make_clip("quick.wav", seconds=1)
    a = add(client, clip)
    b = upload(client, "dropped.wav", clip.read_bytes()).json()
    wait(lambda: state_of(client, a["id"]) == "done" and state_of(client, b["id"]) == "done")
    exported = client.post(f"/api/jobs/{a['id']}/export", json={"presets": ["cz-template"], "formats": ["srt"]},
                           headers=ORIGIN).json()["files"]  # fmt: skip
    upload_dir = data / "uploads" / b["source"]["upload_id"]
    assert upload_dir.is_dir() and (data / "jobs" / a["id"]).is_dir()

    with client.websocket_connect("ws://127.0.0.1:8754/api/events", headers=ORIGIN) as ws:
        ws.receive_json()
        for job in (a, b):
            assert client.post(f"/api/jobs/{job['id']}/remove", json={}, headers=ORIGIN).status_code == 204
        frames = [ws.receive_json(), ws.receive_json()]
    assert [f["type"] for f in frames] == ["removed", "removed"] and frames[0]["job_id"] == a["id"]
    assert jobs(client) == []
    assert not (data / "jobs" / a["id"]).exists() and not upload_dir.exists()
    assert clip.is_file() and Path(exported[0]).is_file()


def test_remove_waits_for_running_jobs_and_exports(client: TestClient, make_clip: MakeClip, data: Path) -> None:
    hold = add(client, make_clip("hold.wav", seconds=1))
    wait(lambda: state_of(client, hold["id"]) == "running")
    r = client.post(f"/api/jobs/{hold['id']}/remove", json={}, headers=ORIGIN)
    assert r.status_code == 409 and "Cancel" in r.json()["error"]
    client.post(f"/api/jobs/{hold['id']}/cancel", json={}, headers=ORIGIN)
    wait(lambda: state_of(client, hold["id"]) == "cancelled")


def test_remove_and_a_second_export_wait_for_an_export(
    client: TestClient, make_clip: MakeClip, project_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from parakeet_stt.server import runner as runner_mod

    job = add(client, make_clip("quick.wav", seconds=1))
    wait(lambda: state_of(client, job["id"]) == "done")
    entered, release = threading.Event(), threading.Event()
    real = runner_mod._write_export

    def slow(*args: Any) -> tuple[list[Path], list[str]]:
        entered.set()
        release.wait(5)
        return real(*args)

    monkeypatch.setattr(runner_mod, "_write_export", slow)
    req = {"presets": ["cz-template"], "formats": ["srt"]}
    answers: list[Any] = []
    t = threading.Thread(
        target=lambda: answers.append(client.post(f"/api/jobs/{job['id']}/export", json=req, headers=ORIGIN))
    )
    t.start()
    assert entered.wait(5)
    second = client.post(f"/api/jobs/{job['id']}/export", json=req, headers=ORIGIN)
    removed = client.post(f"/api/jobs/{job['id']}/remove", json={}, headers=ORIGIN)
    release.set()
    t.join(5)
    assert second.status_code == 409 and "already" in second.json()["error"]
    assert removed.status_code == 409 and "export" in removed.json()["error"]
    assert answers[0].status_code == 200
    assert client.post(f"/api/jobs/{job['id']}/remove", json={}, headers=ORIGIN).status_code == 204


def test_an_unwritable_folder_is_a_readable_error(
    client: TestClient, make_clip: MakeClip, project_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from parakeet_stt import project

    job = add(client, make_clip("quick.wav", seconds=1))
    wait(lambda: state_of(client, job["id"]) == "done")

    def refuse(_: Any) -> None:
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(project, "write_atomically", refuse)
    r = client.post(
        f"/api/jobs/{job['id']}/export", json={"presets": ["cz-template"], "formats": ["srt"]}, headers=ORIGIN
    )
    assert (
        r.status_code == 409 and "could not be written" in r.json()["error"] and "Access is denied" in r.json()["error"]
    )


# ---- receiving an upload ---------------------------------------------------------------------------------------


async def _chunks(parts: list[bytes], fail: BaseException | None = None) -> Any:
    for p in parts:
        yield p
    if fail is not None:
        raise fail


@pytest.mark.parametrize(
    ("parts", "length", "fail", "error"),
    [
        ([b"abc", b"def"], 4, None, "longer than its stated size"),
        ([b"abc"], 10, None, "did not arrive completely"),
        ([b"abc"], 10, ConnectionError("gone"), None),  # the page cancelled: the client disconnected
    ],
)
def test_a_broken_upload_leaves_nothing_behind(
    tmp_path: Path, parts: list[bytes], length: int, fail: BaseException | None, error: str | None
) -> None:
    import asyncio

    with pytest.raises((uploads.UploadError, ConnectionError)) as caught:
        asyncio.run(uploads.receive(tmp_path, _chunks(parts, fail), length, ".wav", key_of(tmp_path)))
    if error:
        assert error in str(caught.value)
    assert not any((tmp_path / "uploads").iterdir())


def test_a_whole_upload_is_kept(tmp_path: Path) -> None:
    import asyncio

    key = key_of(tmp_path)
    upload_id, path = asyncio.run(
        uploads.receive(tmp_path, _chunks([b"RIFF", b"data" * 300_000]), 1_200_004, ".wav", key)
    )
    assert path == tmp_path / "uploads" / upload_id / "source.wav" and key.plain_size(path) == 1_200_004
    assert unsealed(path, Role.SOURCE) == b"RIFF" + b"data" * 300_000
    assert [p.name for p in path.parent.iterdir()] == ["source.wav"]
