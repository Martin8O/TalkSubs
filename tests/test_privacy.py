"""Only the signed-in user's own programs reach the server; nothing is cached; folders and the log stay private."""

from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from parakeet_stt.server import access, logscrub, owner
from parakeet_stt.server.app import ServerConfig, create
from parakeet_stt.server.owner import connection_is_ours as real_check  # the conftest stand-in is not this one
from parakeet_stt.server.security import STRANGER
from tests.conftest import MakeClip

FAKE = [sys.executable, "-X", "utf8", str(Path(__file__).with_name("fake_worker.py"))]
ORIGIN = {"origin": "http://127.0.0.1:8754"}


@pytest.fixture
def data(tmp_path: Path) -> Path:
    d = tmp_path / "data"
    d.mkdir()
    return d


def _scope(client_port: int, server_port: int, host: str = "127.0.0.1") -> dict[str, Any]:
    return {"type": "http", "client": (host, client_port), "server": ("127.0.0.1", server_port)}


@pytest.fixture
def listener() -> Iterator[socket.socket]:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(4)
    yield s
    s.close()


# ---- who is on the other end ----------------------------------------------------------------------------------


def test_a_connection_from_this_process_is_ours(listener: socket.socket) -> None:
    with socket.create_connection(listener.getsockname()) as cli:
        conn, _ = listener.accept()
        with conn:
            assert real_check(_scope(cli.getsockname()[1], listener.getsockname()[1]))


def test_a_connection_from_another_process_of_this_user_is_ours(listener: socket.socket) -> None:
    host, port = listener.getsockname()
    code = (f"import socket, sys; s = socket.create_connection(({host!r}, {port})); "
            "print(s.getsockname()[1], flush=True); sys.stdin.read()")  # fmt: skip
    child = subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout is not None
        client_port = int(child.stdout.readline())
        conn, _ = listener.accept()
        with conn:
            assert owner.owner_pid(client_port, port) not in (None, os.getpid())  # the venv starter runs a child
            assert real_check(_scope(client_port, port))
    finally:
        assert child.stdin is not None
        child.stdin.close()
        child.wait(10)


def test_a_process_of_another_user_is_a_stranger(listener: socket.socket, monkeypatch: pytest.MonkeyPatch) -> None:
    host, port = listener.getsockname()
    code = (f"import socket, sys; s = socket.create_connection(({host!r}, {port})); "
            "print(s.getsockname()[1], flush=True); sys.stdin.read()")  # fmt: skip
    child = subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout is not None
        client_port = int(child.stdout.readline())
        conn, _ = listener.accept()
        with conn:
            monkeypatch.setattr(owner, "user_sid_of", lambda pid: b"\x01\x01" + b"\x00" * 10)  # someone else
            assert not real_check(_scope(client_port, port))
            monkeypatch.setattr(owner, "user_sid_of", lambda pid: None)  # a process we may not even look at
            assert not real_check(_scope(client_port, port))
    finally:
        assert child.stdin is not None
        child.stdin.close()
        child.wait(10)


def test_a_port_held_by_another_user_is_not_taken_for_the_app(
    listener: socket.socket, monkeypatch: pytest.MonkeyPatch
) -> None:
    from parakeet_stt.server import launcher

    port = listener.getsockname()[1]
    assert owner.listener_is_ours(port)
    assert not owner.listener_is_ours(1)  # nobody listens there
    monkeypatch.setattr(owner, "user_sid_of", lambda pid: b"\x01\x01" + b"\x00" * 10)  # another account's server
    assert not owner.listener_is_ours(port)
    assert not launcher._ours_running(port)  # its answer is never asked for, let alone believed


def test_a_system_process_cannot_be_looked_at() -> None:
    assert owner.user_sid_of(4) is None  # the System process: no access, so never "ours"


def test_anything_untraceable_is_a_stranger(listener: socket.socket, monkeypatch: pytest.MonkeyPatch) -> None:
    port = listener.getsockname()[1]
    assert not real_check(_scope(1, port))  # no such connection
    assert not real_check(_scope(50000, port, host="testclient"))
    assert not real_check({"type": "http"})
    monkeypatch.setattr(owner, "owner_pid", lambda *_: 1 / 0)
    assert not real_check(_scope(5, port))  # a failing lookup refuses instead of letting in


# ---- the server refuses strangers before anything else --------------------------------------------------------


def test_a_stranger_gets_one_sentence_and_no_live_updates(data: Path) -> None:
    srv = create(ServerConfig(data_dir=data, worker_cmd=FAKE, gpu_check=lambda: None, peer_check=lambda _: False))
    with TestClient(srv.asgi, base_url="http://127.0.0.1:8754") as c:
        r = c.get("/api/status")
        assert r.status_code == 403 and r.json() == {"error": STRANGER}
        assert c.get("/").status_code == 403
        with pytest.raises(WebSocketDisconnect), c.websocket_connect("/api/events", headers=ORIGIN):
            pass


def test_api_answers_are_never_stored_but_page_files_may_be(data: Path, make_clip: MakeClip) -> None:
    srv = create(ServerConfig(data_dir=data, worker_cmd=FAKE, gpu_check=lambda: None))
    with TestClient(srv.asgi, base_url="http://127.0.0.1:8754") as c:
        status = c.get("/api/status")
        assert status.headers.get_list("cache-control") == ["no-store"]
        missing = c.get("/api/jobs/000000000000/media")
        assert missing.status_code == 404 and missing.headers.get_list("cache-control") == ["no-store"]
        asset = srv_assets()[0]
        assert "immutable" in c.get(f"/assets/{asset}").headers["cache-control"]  # page code, not user data


def srv_assets() -> list[str]:
    from importlib import resources

    root = Path(str(resources.files("parakeet_stt.server").joinpath("static", "assets")))
    return sorted(p.name for p in root.iterdir() if p.suffix in (".js", ".css"))


def test_the_real_stack_lets_in_this_user_and_refuses_a_stranger(data: Path) -> None:
    import uvicorn

    from parakeet_stt.server.launcher import _bind

    for refuse in (False, True):
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        sock = _bind(port)
        check = (lambda _: False) if refuse else None  # None: the real Windows check
        srv = create(ServerConfig(data_dir=data, port=port, worker_cmd=FAKE, gpu_check=lambda: None, peer_check=check))
        server = uvicorn.Server(uvicorn.Config(srv.asgi, log_config=None, ws="websockets-sansio", lifespan="on"))
        t = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        t.start()
        try:
            for _ in range(200):
                if server.started:
                    break
                threading.Event().wait(0.1)
            req = urllib.request.Request(f"http://127.0.0.1:{port}/api/status")
            if refuse:
                with pytest.raises(urllib.error.HTTPError) as e:
                    urllib.request.urlopen(req, timeout=5)
                assert e.value.code == 403 and json.loads(e.value.read()) == {"error": STRANGER}
            else:
                with urllib.request.urlopen(req, timeout=5) as r:
                    assert r.status == 200 and r.headers["cache-control"] == "no-store"
        finally:
            server.should_exit = True
            t.join(15)
        assert not t.is_alive()


def test_a_request_that_fails_on_checked_text_leaves_the_text_out_of_the_log(
    data: Path, caplog: pytest.LogCaptureFixture
) -> None:
    import uvicorn
    from pydantic import BaseModel

    from parakeet_stt.server.launcher import _bind

    class Cue(BaseModel):
        start_ms: int

    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    sock = _bind(port)
    srv = create(ServerConfig(data_dir=data, port=port, worker_cmd=FAKE, gpu_check=lambda: None))

    def boom() -> None:
        Cue.model_validate({"start_ms": "SECRET TRANSCRIPT WORDS"})

    srv.app.add_api_route("/api/boom", boom, methods=["GET"])
    srv.app.router.routes.insert(0, srv.app.router.routes.pop())  # before the page's catch-all route
    server = uvicorn.Server(uvicorn.Config(srv.asgi, log_config=None, ws="websockets-sansio", lifespan="on"))
    t = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    caplog.set_level(logging.INFO)
    t.start()
    try:
        for _ in range(200):
            if server.started:
                break
            threading.Event().wait(0.1)
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/boom", timeout=5)
        assert e.value.code == 500
    finally:
        server.should_exit = True
        t.join(15)
    logged = "\n".join(f"{r.getMessage()} {r.exc_text or ''} {r.exc_info or ''}" for r in caplog.records)
    assert "request failed: ValidationError with 1 errors" in logged
    assert "SECRET TRANSCRIPT" not in logged


# ---- folders only this user can open ---------------------------------------------------------------------------


def test_a_locked_folder_and_its_contents_are_for_this_user_system_and_admins(tmp_path: Path) -> None:
    folder = tmp_path / "data"
    (folder / "jobs").mkdir(parents=True)
    (folder / "jobs" / "old.txt").write_text("x", encoding="utf-8")
    before = access.trustees(access.current_sddl(folder))
    access.lock_to_user(folder)
    (folder / "new.txt").write_text("y", encoding="utf-8")
    wanted = {owner.my_sid_string(), access.SYSTEM, access.ADMINISTRATORS}
    top = access.current_sddl(folder)
    assert top.startswith("D:P")  # nothing inherited from the drive any more
    for path in (folder, folder / "jobs", folder / "jobs" / "old.txt", folder / "new.txt"):
        assert access.trustees(access.current_sddl(path)) == wanted, path
    assert before != wanted  # the test folder really started with the drive's wider list


# ---- the log never names the user's files ---------------------------------------------------------------------


@pytest.fixture
def clean_registry() -> Iterator[None]:
    logscrub.forget_all()
    yield
    logscrub.forget_all()


def _log_line(text: str, exc: BaseException | None = None) -> str:
    record = logging.LogRecord("parakeet_stt.server", logging.INFO, __file__, 1, text, None,
                               (type(exc), exc, exc.__traceback__) if exc else None)  # fmt: skip
    return logscrub.Formatter("%(levelname)s %(message)s").format(record)


def test_names_paths_and_link_secrets_are_hidden(clean_registry: None) -> None:
    logscrub.register(r"D:\Lectures\Evening Talk on Patience.mp4", "Evening Talk on Patience.mp4")
    line = _log_line(r"ffmpeg failed on D:\Lectures\Evening Talk on Patience.mp4 (evening talk on patience) "
                     "from https://store.example/v/a.mp4?X-Amz-Signature=abc&X-Amz-Expires=1 done")  # fmt: skip
    assert "Patience" not in line and "patience" not in line and "Lectures" not in line
    assert "X-Amz" not in line and "abc" not in line and "https://store.example/v/a.mp4?<hidden>" in line
    try:
        raise ValueError("cannot open Evening Talk on Patience.mp4")
    except ValueError as exc:
        assert "Patience" not in _log_line("crashed", exc)


def test_the_queue_registers_what_it_must_hide(data: Path, make_clip: MakeClip, clean_registry: None) -> None:
    clip = make_clip("secret-lecture-title.wav", seconds=2.0)
    srv = create(ServerConfig(data_dir=data, worker_cmd=FAKE, gpu_check=lambda: None))
    with TestClient(srv.asgi, base_url="http://127.0.0.1:8754") as c:
        r = c.post("/api/jobs", json={"path": str(clip)}, headers=ORIGIN)
        assert r.status_code in (200, 201), r.text
    assert "secret-lecture-title" not in _log_line(f"worker: Traceback ... {clip} secret-lecture-title")
    logscrub.forget_all()
    srv = create(ServerConfig(data_dir=data, worker_cmd=FAKE, gpu_check=lambda: None))
    with TestClient(srv.asgi, base_url="http://127.0.0.1:8754"):
        pass  # a restart registers the saved jobs again
    assert "secret-lecture-title" not in _log_line(f"job ended: {clip}")


def test_hiding_is_by_whole_words_and_covers_repr_paths(clean_registry: None) -> None:
    path = r"D:\Lectures\Weekend course\art.wav"
    logscrub.register(path, "the.mp4", "part.wav")
    line = _log_line(f"job abc123 started; the partial preview; open {path!r}")  # repr doubles the backslashes
    assert "\\\\" in repr(path) and "started; the partial preview" in line  # short, or only inside another word
    assert "Weekend" not in line and "Lectures" not in line


def test_an_upload_copy_is_not_hidden_but_its_original_name_is(data: Path, make_clip: MakeClip,
                                                              clean_registry: None) -> None:  # fmt: skip
    clip = make_clip("my-private-lecture.wav", seconds=2.0)
    srv = create(ServerConfig(data_dir=data, worker_cmd=FAKE, gpu_check=lambda: None))
    with TestClient(srv.asgi, base_url="http://127.0.0.1:8754") as c:
        body = clip.read_bytes()
        r = c.put("/api/uploads", content=body, headers={**ORIGIN, "content-type": "application/octet-stream",
                                                         "x-file-name": "my-private-lecture.wav"})  # fmt: skip
        assert r.status_code == 201, r.text
        upload_id = r.json()["source"]["upload_id"]
    line = _log_line(f"removed the unused upload {upload_id} (my-private-lecture.wav)")
    assert upload_id in line and "my-private-lecture" not in line


def test_many_jobs_register_quickly(clean_registry: None) -> None:
    import time

    t0 = time.perf_counter()
    for i in range(1000):
        logscrub.register(rf"D:\Lectures\talk number {i:04d}.mp4")
    line = _log_line(r"failed: D:\Lectures\talk number 0999.mp4")
    assert time.perf_counter() - t0 < 2.0 and "0999" not in line


def test_text_written_straight_to_the_log_is_hidden_too(tmp_path: Path, clean_registry: None) -> None:
    logscrub.register("Secret Course Title.mp4")
    path = tmp_path / "app.log"
    with path.open("w", encoding="utf-8") as fh:
        stream = logscrub.RedactingStream(fh)
        stream.write("Traceback: cannot open Secret Cou")  # a name split across two writes
        stream.write("rse Title.mp4\nnext line\n")
        stream.write("tail without newline Secret Course Title")
        stream.flush()
    text = path.read_text(encoding="utf-8")
    assert "Secret" not in text and "next line" in text


def test_the_app_log_file_never_names_a_lecture(tmp_path: Path, make_clip: MakeClip, clean_registry: None) -> None:
    from parakeet_stt.server import launcher

    data = tmp_path / "appdata"
    clip = make_clip("planted-title-for-the-log.wav", seconds=2.0)
    root = logging.getLogger()
    before = list(root.handlers)
    launcher._start_log(data)
    added = [h for h in root.handlers if h not in before]
    try:
        srv = create(ServerConfig(data_dir=data, worker_cmd=FAKE, gpu_check=lambda: None))
        with TestClient(srv.asgi, base_url="http://127.0.0.1:8754") as c:
            assert c.post("/api/jobs", json={"path": str(clip)}, headers=ORIGIN).status_code == 201
        logging.getLogger("parakeet_stt.server").warning("planted: %s and %r", clip, str(clip))
        for h in added:
            h.flush()
    finally:
        for h in added:
            root.removeHandler(h)
            h.close()
    text = (data / "logs" / "app.log").read_text(encoding="utf-8")
    assert "planted:" in text and "planted-title" not in text and "<hidden>" in text


def test_a_proxy_header_is_refused(data: Path) -> None:
    srv = create(ServerConfig(data_dir=data, worker_cmd=FAKE, gpu_check=lambda: None))
    with TestClient(srv.asgi, base_url="http://127.0.0.1:8754") as c:
        for name in ("x-forwarded-for", "forwarded", "x-real-ip"):
            r = c.get("/api/status", headers={name: "127.0.0.1:50000"})
            assert r.status_code == 403, name


def test_only_the_apps_own_folders_are_locked(tmp_path: Path) -> None:
    foreign = tmp_path / "documents"
    foreign.mkdir()
    (foreign / "letter.docx").write_text("x", encoding="utf-8")
    with pytest.raises(access.AccessError):
        access.lock_app_folder(foreign)
    assert not access.is_locked(foreign)
    with pytest.raises(access.AccessError):
        access.lock_app_folder(Path(tmp_path.anchor))  # a whole drive, never
    fresh = tmp_path / "new-data"
    assert access.lock_app_folder(fresh) is True and access.is_locked(fresh)
    assert access.lock_app_folder(fresh) is False  # already right: nothing is rewritten
    assert access.lock_app_folder(foreign, known=True) is True
