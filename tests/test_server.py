"""The app server: queue order, live progress, cancel, crashes, restarts and the localhost checks."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from parakeet_stt.server.app import ServerConfig, create
from parakeet_stt.server.messages import FileSource, Job, QueueFile
from parakeet_stt.server.store import Store, StoreError
from parakeet_stt.vault import Role, Vault
from tests.conftest import MakeClip, key_of, unsealed
from tests.factories import document as make_document

PORT = 8754
BASE = f"http://127.0.0.1:{PORT}"
ORIGIN = {"origin": BASE}
WS = f"ws://127.0.0.1:{PORT}/api/events"  # the test client would otherwise call itself testserver
FAKE = [sys.executable, "-X", "utf8", str(Path(__file__).with_name("fake_worker.py"))]


@pytest.fixture
def data(tmp_path: Path) -> Path:
    d = tmp_path / "data"
    d.mkdir()
    return d


def make_client(data: Path, **cfg: Any) -> TestClient:
    cfg.setdefault("gpu_check", lambda: None)  # the fake worker needs no GPU
    cfg.setdefault("worker_cmd", FAKE)
    srv = create(ServerConfig(data_dir=data, port=PORT, **cfg))
    return TestClient(srv.asgi, base_url=BASE)


@pytest.fixture
def client(data: Path) -> Iterator[TestClient]:
    with make_client(data, kill_after_s=1.0, busy_retry_s=0.3) as c:
        yield c


def add(c: TestClient, path: Path) -> dict[str, Any]:
    r = c.post("/api/jobs", json={"path": str(path)}, headers=ORIGIN)
    assert r.status_code == 201, r.text
    return dict(r.json())


def jobs(c: TestClient) -> list[dict[str, Any]]:
    return list(c.get("/api/status").json()["jobs"])


def wait(cond: Callable[[], bool], timeout: float = 20.0) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return
        time.sleep(0.05)
    raise AssertionError("condition not met in time")


def left_by_run(data: Path, job_id: str) -> list[str]:
    """What a transcription run left in the job's folder (its preview is made from the source and stays)."""
    folder = data / "jobs" / job_id
    return [p.name for p in folder.iterdir() if not p.name.startswith("preview")] if folder.is_dir() else []


def state_of(c: TestClient, job_id: str) -> str:
    return next(str(j["state"]) for j in jobs(c) if j["id"] == job_id)


# ---- the queue -------------------------------------------------------------------------------------------------


def test_two_jobs_run_in_order_with_progress_over_websocket(
    client: TestClient, make_clip: MakeClip, data: Path
) -> None:
    a, b = make_clip("slow-a.wav", seconds=1), make_clip("quick-b.wav", seconds=1)
    frames: list[dict[str, Any]] = []
    with client.websocket_connect(WS, headers=ORIGIN) as ws:
        first = ws.receive_json()
        assert first["type"] == "snapshot" and first["status"]["app"] == "parakeet-stt"
        ja, jb = add(client, a), add(client, b)
        while True:
            ev = ws.receive_json()
            frames.append(ev)
            if ev["type"] == "job" and ev["job"]["id"] == jb["id"] and ev["job"]["state"] == "done":
                break
    seqs = [f["seq"] for f in frames]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    order = [(f["job"]["id"], f["job"]["state"]) for f in frames if f["type"] == "job"]
    assert order.index((ja["id"], "done")) < order.index((jb["id"], "running"))  # one at a time, in order
    progress = [f["progress"]["fraction"] for f in frames if f["type"] == "progress" and f["job_id"] == ja["id"]]
    assert len(progress) >= 10 and progress == sorted(progress) and progress[-1] > 0.9
    done = {j["id"]: j for j in jobs(client)}
    assert done[ja["id"]]["project_file"] == "slow-a.pstt.json"
    assert (data / "jobs" / ja["id"] / "slow-a.pstt.json").is_file()
    stored = QueueFile.model_validate_json(unsealed(data / "queue.json", Role.QUEUE))
    assert [j.state for j in stored.jobs] == ["done", "done"]
    assert all(j.progress is None for j in stored.jobs)  # live progress is never written


def test_cancel_mid_job_stops_it_and_the_next_one_starts(client: TestClient, make_clip: MakeClip, data: Path) -> None:
    clips = make_clip("hold-a.wav", seconds=1), make_clip("quick-b.wav", seconds=1)  # both made before any job
    a, b = add(client, clips[0]), add(client, clips[1])
    wait(lambda: state_of(client, a["id"]) == "running")
    time.sleep(0.3)
    t0 = time.monotonic()
    r = client.post(f"/api/jobs/{a['id']}/cancel", json={}, headers=ORIGIN)
    assert r.status_code == 200
    wait(lambda: state_of(client, a["id"]) == "cancelled", timeout=5)
    assert time.monotonic() - t0 < 2
    assert not left_by_run(data, a["id"])  # nothing half-written stays behind
    wait(lambda: state_of(client, b["id"]) == "done")


def test_cancel_a_waiting_job_and_a_finished_one(client: TestClient, make_clip: MakeClip) -> None:
    clips = make_clip("hold-a.wav", seconds=1), make_clip("quick-b.wav", seconds=1)  # both made before any job
    a, b = add(client, clips[0]), add(client, clips[1])
    r = client.post(f"/api/jobs/{b['id']}/cancel", json={}, headers=ORIGIN)  # b waits while a holds the worker
    assert r.json()["state"] == "cancelled", r.json()
    assert state_of(client, a["id"]) == "running"
    client.post(f"/api/jobs/{a['id']}/cancel", json={}, headers=ORIGIN)
    wait(lambda: state_of(client, a["id"]) == "cancelled", timeout=5)
    r = client.post(f"/api/jobs/{a['id']}/cancel", json={}, headers=ORIGIN)
    assert r.status_code == 409 and "already finished" in r.json()["error"]
    r = client.post("/api/jobs/0123456789ab/cancel", json={}, headers=ORIGIN)
    assert r.status_code == 404


def test_a_worker_that_will_not_stop_is_killed(client: TestClient, make_clip: MakeClip) -> None:
    a = add(client, make_clip("hang-a.wav", seconds=1))
    wait(lambda: state_of(client, a["id"]) == "running")
    t0 = time.monotonic()
    client.post(f"/api/jobs/{a['id']}/cancel", json={}, headers=ORIGIN)
    wait(lambda: state_of(client, a["id"]) == "cancelled", timeout=10)
    assert 0.8 < time.monotonic() - t0 < 5  # kill_after_s = 1


def test_a_crashing_worker_gives_a_readable_error(client: TestClient, make_clip: MakeClip, data: Path) -> None:
    a = add(client, make_clip("crash-a.wav", seconds=1))
    b = add(client, make_clip("quick-b.wav", seconds=1))
    wait(lambda: state_of(client, b["id"]) == "done")
    failed = next(j for j in jobs(client) if j["id"] == a["id"])
    assert failed["state"] == "failed"
    assert "stopped unexpectedly (exit code 3)" in failed["error"] and "pretend CUDA failure" in failed["error"]
    assert failed["attempts"] == 2  # run once more first: a native crash may not come again
    assert not left_by_run(data, a["id"])  # given up: its checkpoint goes too


def test_a_worker_error_is_shown(client: TestClient, make_clip: MakeClip, data: Path) -> None:
    a = add(client, make_clip("error-a.wav", seconds=1))
    wait(lambda: state_of(client, a["id"]) == "failed")
    assert next(j for j in jobs(client) if j["id"] == a["id"])["error"] == "pretend media error"
    assert not left_by_run(data, a["id"])


def test_gpu_used_elsewhere_pauses_the_queue_instead_of_failing(
    client: TestClient, make_clip: MakeClip, data: Path
) -> None:
    with client.websocket_connect(WS, headers=ORIGIN) as ws:
        ws.receive_json()
        a = add(client, make_clip("busy-a.wav", seconds=1))
        paused: list[tuple[str | None, str | None]] = []
        while len(paused) < 2:
            ev = ws.receive_json()
            if ev["type"] == "paused":
                paused.append((ev["reason"], ev["kind"]))
                if ev["kind"]:
                    assert left_by_run(data, a["id"]) == ["checkpoint.bin"]  # kept while it waits
    (reason, kind), after = paused
    assert reason and "GPU" in reason and kind == "gpu_busy" and after == (None, None)  # paused, then retried
    job = next(j for j in jobs(client) if j["id"] == a["id"])
    assert job["state"] in ("queued", "running") and job["attempts"] <= 1


def test_bad_inputs_are_refused_readably(client: TestClient, tmp_path: Path) -> None:
    text = tmp_path / "notes.txt"
    text.write_text("not media", encoding="utf-8")
    for path, words in (("relative.wav", "full path"), (str(tmp_path / "missing.wav"), "no file"),
                        (str(text), "")):  # fmt: skip
        r = client.post("/api/jobs", json={"path": path}, headers=ORIGIN)
        assert r.status_code == 400 and words in r.json()["error"], r.text
    r = client.post("/api/jobs", json={"nope": 1}, headers=ORIGIN)
    assert r.status_code == 422 and r.json()["error"].startswith("Invalid request")


def test_reconnect_gets_the_current_state(client: TestClient, make_clip: MakeClip) -> None:
    a = add(client, make_clip("hold-a.wav", seconds=1))
    with client.websocket_connect(WS, headers=ORIGIN) as ws:
        ws.receive_json()
        while ws.receive_json()["type"] != "progress":
            pass
    with client.websocket_connect(WS, headers=ORIGIN) as ws:  # the "reload"
        snap = ws.receive_json()
        assert snap["type"] == "snapshot"
        job = next(j for j in snap["status"]["jobs"] if j["id"] == a["id"])
        assert job["state"] == "running" and job["progress"]["fraction"] > 0
        nxt = ws.receive_json()
        assert nxt["seq"] == snap["seq"] + 1  # nothing lost between the snapshot and the stream


def test_gpu_held_by_another_program_pauses_before_any_worker_starts(data: Path, make_clip: MakeClip) -> None:
    answers = iter(["Another transcription is using the GPU; wait until it finishes."] * 2)
    with make_client(data, busy_retry_s=0.2, gpu_check=lambda: next(answers, None)) as c:
        a = add(c, make_clip("quick-a.wav", seconds=1))
        status = c.get("/api/status").json()
        assert status["paused_reason"] and "GPU" in status["paused_reason"]
        assert state_of(c, a["id"]) == "queued" and not left_by_run(data, a["id"])  # no worker was started
        wait(lambda: state_of(c, a["id"]) == "done")  # the GPU came free: it ran
        job = next(j for j in jobs(c) if j["id"] == a["id"])
        assert job["attempts"] == 1


def test_a_worker_that_cannot_start_fails_its_job_and_the_queue_moves_on(data: Path, make_clip: MakeClip) -> None:
    clips = make_clip("quick-a.wav", seconds=1), make_clip("quick-b.wav", seconds=1)
    with make_client(data, worker_cmd=[str(data / "no-such-program.exe")]) as c:
        a, b = add(c, clips[0]), add(c, clips[1])
        wait(lambda: state_of(c, b["id"]) == "failed", timeout=5)
        for job in jobs(c):
            assert job["state"] == "failed" and "could not start" in job["error"]
        assert not left_by_run(data, a["id"])


def test_a_cancel_that_arrives_before_a_gpu_busy_answer_wins(client: TestClient, make_clip: MakeClip) -> None:
    a = add(client, make_clip("busy-a.wav", seconds=1))
    client.post(f"/api/jobs/{a['id']}/cancel", json={}, headers=ORIGIN)
    wait(lambda: state_of(client, a["id"]) in ("cancelled", "done", "failed"), timeout=5)
    assert state_of(client, a["id"]) == "cancelled"


# ---- restarts -------------------------------------------------------------------------------------------------


def _stored_running(data: Path, path: Path, attempts: int) -> str:
    job = Job(id="0123456789ab", name=path.name, source=FileSource(path=str(path)), state="running",
              created_at=1.0, started_at=2.0, attempts=attempts)  # fmt: skip
    Store(data, key_of(data)).save([job])
    (data / "jobs" / job.id).mkdir(parents=True)
    (data / "jobs" / job.id / ".half.pstt.json.tmp").write_text("x", encoding="utf-8")
    return job.id


def test_a_job_interrupted_by_a_crash_runs_again(data: Path, make_clip: MakeClip) -> None:
    job_id = _stored_running(data, make_clip("quick-a.wav", seconds=1), attempts=1)
    with make_client(data) as c:
        wait(lambda: state_of(c, job_id) == "done")
        assert next(j for j in jobs(c) if j["id"] == job_id)["attempts"] == 2
        assert not (data / "jobs" / job_id / ".half.pstt.json.tmp").exists()


def test_a_job_interrupted_again_and_again_is_not_retried(data: Path, make_clip: MakeClip) -> None:
    job_id = _stored_running(data, make_clip("quick-a.wav", seconds=1), attempts=2)
    with make_client(data) as c:
        job = next(j for j in jobs(c) if j["id"] == job_id)
        assert job["state"] == "failed" and "Interrupted 2 times" in job["error"]


def test_closing_the_app_mid_job_puts_it_back_in_the_queue(data: Path, make_clip: MakeClip) -> None:
    with make_client(data) as c:
        a = add(c, make_clip("hold-a.wav", seconds=1))
        wait(lambda: state_of(c, a["id"]) == "running")
    stored = Store(data, Vault.open(data)).load()
    assert stored[0].state == "queued" and stored[0].attempts == 0
    assert left_by_run(data, a["id"]) == ["checkpoint.bin"]  # the next start goes on from it


# ---- going on after an interruption (checkpoints) ---------------------------------------------------------------


def test_a_worker_that_dies_goes_on_from_its_checkpoint(client: TestClient, make_clip: MakeClip, data: Path) -> None:
    a = add(client, make_clip("crashonce-a.wav", seconds=1))
    wait(lambda: state_of(client, a["id"]) in ("done", "failed"))
    job = next(j for j in jobs(client) if j["id"] == a["id"])
    assert job["state"] == "done" and job["attempts"] == 2  # counted: a native crash is not the user's doing
    assert "checkpoint.bin" not in left_by_run(data, a["id"])  # a finished job never keeps it


def test_full_gpu_memory_pauses_the_queue_and_the_job_goes_on(data: Path, make_clip: MakeClip) -> None:
    with make_client(data, memory_retry_s=0.3) as c, c.websocket_connect(WS, headers=ORIGIN) as ws:
        assert ws.receive_json()["status"]["paused_kind"] is None
        a = add(c, make_clip("memoryonce-a.wav", seconds=1))
        kinds: list[str | None] = []
        while len(kinds) < 2:
            ev = ws.receive_json()
            if ev["type"] == "paused":
                kinds.append(ev["kind"])
                if ev["kind"] == "gpu_memory":
                    assert "memory" in ev["reason"]
                    assert c.get("/api/status").json()["paused_kind"] == "gpu_memory"
        wait(lambda: state_of(c, a["id"]) == "done")
        job = next(j for j in jobs(c) if j["id"] == a["id"])
    assert kinds == ["gpu_memory", None] and job["attempts"] == 1  # a full memory is not counted as an attempt
    assert "checkpoint.bin" not in left_by_run(data, a["id"])


def test_gpu_memory_full_again_and_again_fails_the_job_and_the_queue_goes_on(data: Path, make_clip: MakeClip) -> None:
    clips = make_clip("memory-a.wav", seconds=1), make_clip("quick-b.wav", seconds=1)
    with make_client(data, memory_retry_s=0.2) as c:
        a, b = add(c, clips[0]), add(c, clips[1])
        wait(lambda: state_of(c, b["id"]) == "done")
        job = next(j for j in jobs(c) if j["id"] == a["id"])
    assert job["state"] == "failed" and "ran out of memory 3 times" in job["error"]
    assert not left_by_run(data, a["id"])


def test_a_full_disk_at_the_final_save_pauses_the_queue_and_the_job_goes_on(data: Path, make_clip: MakeClip) -> None:
    with make_client(data, memory_retry_s=0.3) as c, c.websocket_connect(WS, headers=ORIGIN) as ws:
        ws.receive_json()
        a = add(c, make_clip("diskonce-a.wav", seconds=1))
        kinds: list[str | None] = []
        while len(kinds) < 2:
            ev = ws.receive_json()
            if ev["type"] == "paused":
                kinds.append(ev["kind"])
                if ev["kind"] == "disk_full":
                    assert state_of(c, a["id"]) == "queued" and left_by_run(data, a["id"]) == ["checkpoint.bin"]
        wait(lambda: state_of(c, a["id"]) == "done")
        job = next(j for j in jobs(c) if j["id"] == a["id"])
    assert kinds == ["disk_full", None] and job["attempts"] == 1 and job["error"] is None


def test_a_disk_that_stays_full_never_throws_the_finished_recognition_away(data: Path, make_clip: MakeClip) -> None:
    with make_client(data, memory_retry_s=0.1) as c:
        a = add(c, make_clip("disk-a.wav", seconds=1))
        time.sleep(2)  # many more tries than a full graphics card gets
        assert state_of(c, a["id"]) in ("queued", "running")
        assert c.get("/api/status").json()["paused_kind"] in ("disk_full", None)
    assert "checkpoint.bin" in left_by_run(data, a["id"])


def test_cancelling_a_job_that_waits_with_a_checkpoint_deletes_it(data: Path, make_clip: MakeClip) -> None:
    with make_client(data, memory_retry_s=30) as c:
        a = add(c, make_clip("memory-a.wav", seconds=1))
        wait(lambda: c.get("/api/status").json()["paused_kind"] == "gpu_memory")
        assert state_of(c, a["id"]) == "queued" and left_by_run(data, a["id"]) == ["checkpoint.bin"]
        r = c.post(f"/api/jobs/{a['id']}/cancel", json={}, headers=ORIGIN)
        assert r.json()["state"] == "cancelled"
        assert not left_by_run(data, a["id"])


def test_an_interrupted_job_keeps_its_checkpoint_unless_it_is_given_up(data: Path, make_clip: MakeClip) -> None:
    job_id = _stored_running(data, make_clip("crashonce-a.wav", seconds=1), attempts=1)
    (data / "jobs" / job_id / "checkpoint.bin").write_bytes(key_of(data).seal(b"crashonce", Role.CHECKPOINT))
    orphan = data / "jobs" / "ffffffffffff"
    orphan.mkdir()
    (orphan / "checkpoint.bin").write_bytes(key_of(data).seal(b"x", Role.CHECKPOINT))
    with make_client(data) as c:
        wait(lambda: state_of(c, job_id) in ("done", "failed"))
        assert state_of(c, job_id) == "done"  # it finishes only when its checkpoint survived the start
        assert next(j for j in jobs(c) if j["id"] == job_id)["attempts"] == 2
    assert not orphan.exists()  # a checkpoint without its job is swept


def test_a_job_given_up_after_interruptions_loses_its_checkpoint(data: Path, make_clip: MakeClip) -> None:
    job_id = _stored_running(data, make_clip("quick-a.wav", seconds=1), attempts=2)
    (data / "jobs" / job_id / "checkpoint.bin").write_bytes(key_of(data).seal(b"x", Role.CHECKPOINT))
    with make_client(data) as c:
        assert state_of(c, job_id) == "failed"
    assert not left_by_run(data, job_id)


def test_queue_file_problems_are_readable(data: Path) -> None:
    key = key_of(data)
    (data / "queue.json").write_bytes(key.seal(b'{"schema_version": 99, "jobs": []}', Role.QUEUE))
    with pytest.raises(StoreError, match="newer version"):
        Store(data, key).load()
    (data / "queue.json").write_bytes(key.seal(b"{nope", Role.QUEUE))
    with pytest.raises(StoreError, match="damaged"):
        Store(data, key).load()
    (data / "queue.json").write_text('{"schema_version": 1, "jobs": []}', encoding="utf-8")
    with pytest.raises(StoreError, match="cannot be opened"):  # a plain queue file is not the app's
        Store(data, key).load()


# ---- the localhost checks -------------------------------------------------------------------------------------


def test_only_its_own_window_may_talk_to_it(client: TestClient) -> None:
    def get(**h: str) -> int:
        return client.get("/api/status", headers=h).status_code

    assert get() == 200
    assert get(host="evil.example:8754") == 403  # DNS rebinding
    assert get(host="127.0.0.1:9999") == 403
    assert get(origin="http://evil.example") == 403
    assert get(origin="null") == 403
    assert get(**{"sec-fetch-site": "cross-site"}) == 403
    assert get(**{"sec-fetch-site": "same-site"}) == 403
    assert get(**{"sec-fetch-site": "same-origin"}, origin=BASE) == 200
    assert client.get("/api/status", headers=[("host", f"127.0.0.1:{PORT}"), ("host", "evil")]).status_code == 403


def test_changes_need_the_window_origin_and_json(client: TestClient) -> None:
    body = json.dumps({"path": "x"})
    r = client.post("/api/jobs", content=body, headers={"content-type": "application/json"})
    assert r.status_code == 403  # no Origin: not from the window
    r = client.post(
        "/api/jobs", content="path=x", headers={**ORIGIN, "content-type": "application/x-www-form-urlencoded"}
    )
    assert r.status_code == 415  # what a plain HTML form could send
    r = client.post(
        "/api/jobs", content=json.dumps({"path": "x" * 20000}), headers={**ORIGIN, "content-type": "application/json"}
    )
    assert r.status_code == 413


def test_websocket_checks(client: TestClient) -> None:
    from starlette.websockets import WebSocketDisconnect

    for headers in ({}, {"origin": "http://evil.example"}, {"origin": BASE, "host": "evil.example:8754"}):
        with pytest.raises(WebSocketDisconnect), client.websocket_connect(WS, headers=headers) as ws:
            ws.receive_json()


def test_paths_are_checked_before_anything_else(client: TestClient) -> None:
    for path in ("/..%2f..%2fwindows", "/a%5cb", "/c:%2fwindows"):
        assert client.get(path).status_code == 400, path
    assert client.get("/secret.txt").status_code == 404


def test_security_headers_and_no_cors(client: TestClient) -> None:
    for r in (client.get("/"), client.get("/api/status"), client.get("/missing"),
              client.options("/api/jobs", headers={**ORIGIN, "access-control-request-method": "POST"})):  # fmt: skip
        assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
        assert r.headers["x-frame-options"] == "DENY" and r.headers["x-content-type-options"] == "nosniff"
        assert not any(k.lower().startswith("access-control-") for k in r.headers)
    page = client.get("/")
    assert page.status_code == 200 and "<title>TalkSubs</title>" in page.text
    assert page.headers["cache-control"] == "no-store" and "<script>" not in page.text  # no inline script
    import re

    for ref, kind in ((r'src="(/assets/[^"]+\.js)"', "text/javascript"), (r'href="(/assets/[^"]+\.css)"', "text/css")):
        m = re.search(ref, page.text)
        assert m, ref
        r = client.get(m.group(1))
        assert r.status_code == 200 and r.headers["content-type"].startswith(kind)
        assert "immutable" in r.headers["cache-control"]
    assert client.get("/api/unknown").json() == {"error": "Not found."}


# ---- closing the window ----------------------------------------------------------------------------------------


def test_presence_stops_only_when_no_window_comes_back() -> None:
    import asyncio

    from parakeet_stt.server.hub import Presence

    async def scenario() -> list[float]:
        fired: list[float] = []
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        p = Presence(lambda: fired.append(loop.time() - t0), grace_s=0.3, first_s=0.5)
        p.start(loop)
        p.changed(1)  # the window connects
        p.changed(0)  # a reload drops it ...
        await asyncio.sleep(0.1)
        p.changed(1)  # ... and it is back well within the grace period
        await asyncio.sleep(0.6)
        assert fired == []
        p.changed(0)  # closed for good
        await asyncio.sleep(0.5)
        return fired

    fired = asyncio.run(scenario())
    assert len(fired) == 1 and 0.9 < fired[0] < 1.3


def test_presence_stops_when_no_window_ever_connects() -> None:
    import asyncio

    from parakeet_stt.server.hub import Presence

    async def scenario() -> int:
        fired: list[int] = []
        p = Presence(lambda: fired.append(1), grace_s=5, first_s=0.2)
        p.start(asyncio.get_running_loop())
        await asyncio.sleep(0.4)
        return len(fired)

    assert asyncio.run(scenario()) == 1


# ---- the real server stack (uvicorn + websockets, not the test client) ------------------------------------------


def test_real_server_stack_checks_websockets_before_accepting(data: Path) -> None:
    import threading

    import uvicorn
    from websockets.exceptions import InvalidStatus
    from websockets.sync.client import connect
    from websockets.typing import Origin

    from parakeet_stt.server.launcher import _bind

    sock = socket_on_free_port()
    port = sock.getsockname()[1]
    sock.close()
    sock = _bind(port)
    srv = create(ServerConfig(data_dir=data, port=port, worker_cmd=FAKE, gpu_check=lambda: None))
    server = uvicorn.Server(uvicorn.Config(srv.asgi, log_config=None, ws="websockets-sansio", lifespan="on"))
    t = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    t.start()
    try:
        wait(lambda: server.started, timeout=20)
        url, own = f"ws://127.0.0.1:{port}/api/events", f"http://127.0.0.1:{port}"
        with connect(url, origin=Origin(own)) as ws:
            first = json.loads(ws.recv(timeout=5))
            assert first["type"] == "snapshot" and first["status"]["boot_id"] == srv.hub.boot_id
        for origin in ("http://evil.example", None):
            with pytest.raises(InvalidStatus) as e, connect(url, origin=Origin(origin) if origin else None):
                pass
            assert e.value.response.status_code == 403
    finally:
        server.should_exit = True
        t.join(15)
    assert not t.is_alive()


def socket_on_free_port() -> Any:
    import socket

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    return s


def test_a_damaged_queue_file_stops_the_start_with_a_readable_message(tmp_path: Path) -> None:
    """A queue that opens with the key but does not hold together is not a key problem: the start says so."""
    data = tmp_path / "data"
    key_of(data)
    (data / "queue.json").write_bytes(key_of(data).seal(b"{nope", Role.QUEUE))
    sock = socket_on_free_port()
    port = sock.getsockname()[1]
    sock.close()
    import os

    env = {**os.environ, "PARAKEET_STT_DATA": str(data), "PARAKEET_STT_PORT": str(port)}
    out = subprocess.run([sys.executable, "-X", "utf8", "-m", "parakeet_stt", "app", "--no-window"], env=env,
                         capture_output=True, text=True, timeout=120, check=False)  # fmt: skip
    assert out.returncode == 1
    assert "queue file" in out.stderr and "damaged" in out.stderr and "Move it away" in out.stderr, out.stderr


def test_a_locked_store_refuses_work_until_start_fresh_moves_it_aside(data: Path) -> None:
    """A store whose queue does not open with the key is never guessed at; starting fresh never deletes it."""
    key = key_of(data)
    (data / "queue.json").write_bytes(key.seal(b'{"schema_version": 1, "jobs": []}', Role.QUEUE))
    with make_client(data, key_entropy=b"another account") as c:
        st = c.get("/api/status").json()
        assert st["storage_locked"] and st["jobs"] == []
        r = c.post("/api/jobs", json={"path": "C:/x.wav"}, headers=ORIGIN)
        assert r.status_code == 503 and "cannot be opened" in r.json()["error"]
        assert c.put("/api/settings", json={}, headers=ORIGIN).status_code == 400  # nothing is saved meanwhile
        assert c.post("/api/storage/start-fresh", json={}, headers=ORIGIN).status_code == 204
        assert not c.get("/api/status").json()["storage_locked"]
        assert c.post("/api/storage/start-fresh", json={}, headers=ORIGIN).status_code == 409  # only while locked
    old = [p for p in data.iterdir() if p.name.startswith("old-")]
    assert len(old) == 1 and (old[0] / "queue.json").read_bytes()[:4] == b"PSTE"
    assert (old[0] / "key.dpapi").is_file() and (data / "key.dpapi").is_file()


# ---- the real worker and the process guard ---------------------------------------------------------------------


def test_worker_reports_a_gpu_taken_while_it_was_loading_as_busy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import io

    from parakeet_stt import pipeline
    from parakeet_stt.cancel import CancelToken
    from parakeet_stt.engines.base import EngineBusy
    from parakeet_stt.server import worker

    def taken(*_: object, **__: object) -> None:
        raise EngineBusy("Another transcription is using the GPU; wait until it finishes.")

    monkeypatch.setattr(pipeline, "transcribe_file", taken)
    key_of(tmp_path)
    buf = io.StringIO()
    spec = worker.JobSpec(source=str(tmp_path / "x.wav"), out_dir=str(tmp_path), stem="x", engine="parakeet",
                          data_dir=str(tmp_path))  # fmt: skip
    worker.run(spec, worker._Out(buf), CancelToken())
    assert json.loads(buf.getvalue().splitlines()[-1])["t"] == "gpu_busy"


def _winerror(code: int, text: str) -> OSError:
    exc = OSError(None, text)
    exc.winerror = code  # what Windows sets for its own error codes
    return exc


@pytest.mark.parametrize(
    ("error", "kind", "words"),
    [
        (OSError(28, "No space left on device"), "disk_full", "The disk is full"),
        (_winerror(112, "There is not enough space on the disk"), "disk_full", "The disk is full"),
        (PermissionError(13, "Permission denied", "C:/secret/lecture name.pstt.json"), "error", "(Permission denied)"),
    ],
)
def test_worker_says_plainly_when_the_finished_transcript_cannot_be_saved(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, error: OSError, kind: str, words: str
) -> None:
    import io

    from parakeet_stt import pipeline, project
    from parakeet_stt.cancel import CancelToken
    from parakeet_stt.server import worker

    class Result:
        document = make_document()
        stats = None

    def save(*_: object, **__: object) -> None:
        raise error

    monkeypatch.setattr(pipeline, "transcribe_file", lambda *_, **__: Result())
    monkeypatch.setattr(project, "save", save)
    key_of(tmp_path)
    buf = io.StringIO()
    spec = worker.JobSpec(source=str(tmp_path / "x.wav"), out_dir=str(tmp_path), stem="x", engine="parakeet",
                          data_dir=str(tmp_path))  # fmt: skip
    worker.run(spec, worker._Out(buf), CancelToken())
    last = json.loads(buf.getvalue().splitlines()[-1])
    assert last["t"] == kind and words in last["message"]
    assert "secret" not in last["message"] and "Errno" not in last["message"]


def test_real_worker_waits_politely_when_the_gpu_is_held(tmp_path: Path, make_clip: MakeClip) -> None:
    from parakeet_stt.gpu import GpuLock

    clip = make_clip("tone.wav", seconds=1)
    lock = GpuLock()
    lock.acquire()  # this test process plays the CLI run that holds the GPU
    try:
        spec = json.dumps({"source": str(clip), "out_dir": str(tmp_path / "out"), "stem": "tone", "engine": "parakeet",
                           "data_dir": str(tmp_path)})  # fmt: skip
        out = subprocess.run([sys.executable, "-X", "utf8", "-m", "parakeet_stt", "worker"], input=spec + "\n",
                             capture_output=True, text=True, timeout=120, check=False)  # fmt: skip
    finally:
        lock.release()
    last = json.loads(out.stdout.splitlines()[-1])
    assert last["t"] == "gpu_busy" and "Another transcription is running" in last["message"], out.stdout + out.stderr


def test_worker_reports_a_full_gpu_memory_and_keeps_its_checkpoint(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import io

    from parakeet_stt import pipeline
    from parakeet_stt.cancel import CancelToken
    from parakeet_stt.engines.base import EngineOutOfMemory
    from parakeet_stt.server import worker

    seen: list[object] = []

    def full(*_: object, **kw: object) -> None:
        seen.append(kw.get("checkpoint"))
        raise EngineOutOfMemory("The graphics card has no memory left for the model.")

    monkeypatch.setattr(pipeline, "transcribe_file", full)
    key = key_of(tmp_path)
    (tmp_path / "checkpoint.bin").write_bytes(key.seal(b"so far", Role.CHECKPOINT))
    (tmp_path / "x.wav").write_bytes(b"")
    buf = io.StringIO()
    spec = worker.JobSpec(source=str(tmp_path / "x.wav"), out_dir=str(tmp_path), stem="x", engine="parakeet",
                          data_dir=str(tmp_path))  # fmt: skip
    worker.run(spec, worker._Out(buf), CancelToken())
    last = json.loads(buf.getvalue().splitlines()[-1])
    assert last["t"] == "gpu_memory" and "no memory" in last["message"]
    assert seen and getattr(seen[0], "path", None) == tmp_path / "checkpoint.bin"  # the worker passes it on
    assert (tmp_path / "checkpoint.bin").is_file()


def test_children_of_the_worker_never_see_its_pipes() -> None:
    code = (
        "import subprocess, sys\n"
        "from parakeet_stt.server.worker import detach_std_handles\n"
        "proto_in, proto_out = detach_std_handles()\n"
        "spec = proto_in.readline().strip()\n"
        "child = subprocess.run([sys.executable, '-c', 'import sys; print(repr(sys.stdin.read()))'],\n"
        "                       capture_output=True, text=True, timeout=10)\n"
        "proto_out.write(spec + '|' + child.stdout.strip() + '\\n'); proto_out.flush()\n"
        "print('library noise')\n"  # must land on stderr, not in the protocol
    )
    p = subprocess.Popen([sys.executable, "-X", "utf8", "-c", code], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True)  # fmt: skip
    assert p.stdin is not None and p.stdout is not None and p.stderr is not None
    try:
        p.stdin.write("the-spec\n")
        p.stdin.flush()  # the pipe stays open: a grandchild holding it would block (and time out) instead
        line = p.stdout.readline()
        p.stdin.close()
        err = p.stderr.read()
    finally:
        p.kill()
    assert line.strip() == "the-spec|''", err  # the grandchild read NUL at once instead of our pipe
    assert "library noise" in err


def test_real_worker_reports_unreadable_media(tmp_path: Path) -> None:
    bad = tmp_path / "notes.wav"
    bad.write_text("not media", encoding="utf-8")
    key_of(tmp_path)
    spec = json.dumps({"source": str(bad), "out_dir": str(tmp_path / "out"), "stem": "notes", "engine": "parakeet",
                       "data_dir": str(tmp_path)})  # fmt: skip
    out = subprocess.run([sys.executable, "-X", "utf8", "-m", "parakeet_stt", "worker"], input=spec + "\n",
                         capture_output=True, text=True, timeout=120, check=False)  # fmt: skip
    lines = [json.loads(x) for x in out.stdout.splitlines()]
    assert lines[0]["t"] == "hello"
    assert lines[-1]["t"] == "error" and "notes.wav" in lines[-1]["message"], out.stdout + out.stderr
    assert not (tmp_path / "out").exists()


def test_killing_a_contained_process_kills_its_children(tmp_path: Path) -> None:
    marker = tmp_path / "pids.txt"
    code = (
        "import os, subprocess, sys, time\n"
        "from parakeet_stt.server import winjob\n"
        "winjob.contain_self()\n"
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "time.sleep(1)\n"  # let the grandchild's own launcher (if any) start the real interpreter
        f"open(r'{marker}', 'w').write(f'{{os.getpid()}} {{p.pid}}')\n"
        "time.sleep(60)\n"
    )
    child = subprocess.Popen([sys.executable, "-c", code])
    try:
        wait(marker.exists, timeout=30)
        time.sleep(0.2)
        me, grandchild = (int(x) for x in marker.read_text().split())
        assert _alive(grandchild)
        _terminate(me)  # only the contained process itself, exactly as a crash would end it
        wait(lambda: not _alive(grandchild), timeout=10)
    finally:
        child.kill()


def _terminate(pid: int) -> None:
    import ctypes

    h = ctypes.windll.kernel32.OpenProcess(0x0001, False, pid)  # PROCESS_TERMINATE
    assert h, f"cannot open process {pid}"
    ctypes.windll.kernel32.TerminateProcess(h, 1)
    ctypes.windll.kernel32.CloseHandle(h)


def _alive(pid: int) -> bool:
    import ctypes

    h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    code = ctypes.c_ulong()
    ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
    ctypes.windll.kernel32.CloseHandle(h)
    return code.value == 259  # STILL_ACTIVE


# ---- the speech model's first download ---------------------------------------------------------------------------


class _Download:
    """Stands in for the model download: reports progress, then waits until the test lets it finish or fail."""

    def __init__(self) -> None:
        import threading

        self.go = threading.Event()
        self.error: str | None = None
        self.runs = 0
        self.here = False

    def __call__(self, report: Callable[[int, int], None], cancel: Any) -> None:
        from parakeet_stt import models

        self.runs += 1
        report(100, 1000)
        report(400, 1000)
        while not self.go.wait(0.02):
            cancel.raise_if_cancelled()
        self.go.clear()
        if self.error is not None:
            raise models.ModelFetchError(self.error)
        report(1000, 1000)
        self.here = True


def test_the_queue_waits_for_the_first_model_download_and_shows_its_progress(data: Path, make_clip: MakeClip) -> None:
    dl = _Download()
    with make_client(data, models_present=lambda: dl.here, models_fetch=dl) as c:
        wait(lambda: (c.get("/api/status").json()["models"] or {}).get("done_bytes") == 400)
        models = c.get("/api/status").json()["models"]
        assert models["state"] == "downloading" and models["total_bytes"] == 1000
        a = add(c, make_clip("quick-a.wav", seconds=1))  # a lecture added meanwhile waits for the model
        time.sleep(0.5)
        assert state_of(c, a["id"]) == "queued"
        assert c.post("/api/models/fetch", json={}, headers=ORIGIN).status_code == 409  # already on its way
        dl.go.set()
        wait(lambda: state_of(c, a["id"]) == "done")
        assert c.get("/api/status").json()["models"]["state"] == "ready" and dl.runs == 1


def test_a_failed_model_download_says_why_and_try_again_goes_on(data: Path, make_clip: MakeClip) -> None:
    dl = _Download()
    dl.error = "The speech model could not be downloaded: there is no connection to the internet."
    with make_client(data, models_present=lambda: dl.here, models_fetch=dl) as c, \
            c.websocket_connect(WS, headers=ORIGIN) as ws:  # fmt: skip
        assert ws.receive_json()["status"]["models"]["state"] in ("checking", "downloading")
        a = add(c, make_clip("quick-a.wav", seconds=1))
        dl.go.set()
        wait(lambda: c.get("/api/status").json()["models"]["state"] == "failed")
        failed = c.get("/api/status").json()["models"]
        assert "no connection" in failed["error"] and state_of(c, a["id"]) == "queued"
        seen: list[dict[str, Any]] = []
        while not seen or seen[-1]["state"] != "failed":
            ev = ws.receive_json()
            if ev["type"] == "models":
                seen.append(ev["models"])
        assert {m["state"] for m in seen} <= {"downloading", "failed"} and seen[-1]["error"] == failed["error"]
        dl.error = None
        assert c.post("/api/models/fetch", json={}, headers=ORIGIN).status_code == 204
        dl.go.set()
        wait(lambda: state_of(c, a["id"]) == "done")
    assert dl.runs == 2


def test_the_model_download_stops_with_the_app_and_nothing_runs(data: Path, make_clip: MakeClip) -> None:
    dl = _Download()
    with make_client(data, models_present=lambda: dl.here, models_fetch=dl) as c:
        wait(lambda: (c.get("/api/status").json()["models"] or {}).get("done_bytes") == 400)
        a = add(c, make_clip("quick-a.wav", seconds=1))
    stored = Store(data, key_of(data)).load()
    assert [j.state for j in stored if j.id == a["id"]] == ["queued"] and not dl.here


# ---- the download package's graphics-card support -----------------------------------------------------------------

CARDLESS_PC = {"device": "cpu", "reason": "no_card", "card": None, "card_mb": None, "performance_cores": 8,
               "physical_cores": 8}  # fmt: skip
OFFER_PC = {**CARDLESS_PC, "reason": "card_pack_missing", "card": "RTX", "card_mb": 12000}


def test_the_card_support_waits_for_the_user_and_is_offered_only_with_a_card(data: Path) -> None:
    dl = _Download()
    with make_client(data, card_present=lambda: dl.here, card_fetch=dl, machine_check=lambda: CARDLESS_PC) as c:
        wait(lambda: c.get("/api/status").json()["machine"] is not None)
        assert c.get("/api/status").json()["card_pack"]["state"] == "absent"  # nothing starts by itself
        assert c.post("/api/card/fetch", json={}, headers=ORIGIN).status_code == 409  # no card to use it with
        assert dl.runs == 0


def test_the_card_support_downloads_on_request_then_the_pc_is_looked_at_again(data: Path) -> None:
    dl = _Download()
    looks: list[int] = []

    def look() -> dict[str, Any]:
        looks.append(1)
        return {**OFFER_PC, "device": "cuda", "reason": "card"} if dl.here else OFFER_PC

    with (
        make_client(data, card_present=lambda: dl.here, card_fetch=dl, machine_check=look) as c,
        c.websocket_connect(WS, headers=ORIGIN) as ws,
    ):
        wait(lambda: (c.get("/api/status").json()["machine"] or {}).get("reason") == "card_pack_missing")
        assert c.post("/api/card/fetch", json={}, headers=ORIGIN).status_code == 204
        wait(lambda: c.get("/api/status").json()["card_pack"]["done_bytes"] == 400)
        assert c.get("/api/status").json()["card_pack"]["state"] == "downloading"
        assert c.post("/api/card/fetch", json={}, headers=ORIGIN).status_code == 409  # already on its way
        dl.go.set()
        wait(lambda: (c.get("/api/status").json()["machine"] or {}).get("reason") == "card")
        assert c.get("/api/status").json()["card_pack"]["state"] == "ready" and len(looks) == 2
        kinds = set()
        while "card_pack" not in kinds or "machine" not in kinds:
            kinds.add(ws.receive_json()["type"])
