"""Recordings behind a link: only their sound is fetched and kept (sealed), the link's secret never shows, and a
dropped video keeps only its sound too."""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from parakeet_stt import blobserve, media, sound, vault
from parakeet_stt.server import links, storage, uploads
from parakeet_stt.vault import Role, Vault
from tests.conftest import MakeClip, key_of, unsealed
from tests.test_server import ORIGIN, jobs, make_client, state_of, wait

SECRET = "X-Amz-Signature=d0c0ffeed0c0ffee"


class _Site:
    """A stand-in for the platform's object storage: byte ranges, expiry, a slow mode."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.delay_s = 0.0
        self.requests: list[str] = []
        self.stall_after: int | None = None  # bytes of a body; then nothing more (until the test ends)
        self.cut_after: int | None = None  # bytes of a body; then the connection is closed mid-way
        self.dies_after_cut = False  # after a cut, every further request is refused (the link expired meanwhile)
        self.cuts = 0
        self.released = threading.Event()
        site = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format: str, *args: object) -> None:
                pass

            def do_HEAD(self) -> None:  # a presigned GET link is not signed for HEAD
                self.send_response(403)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_GET(self) -> None:
                site.requests.append(self.path)
                route, _, query = self.path.partition("?")
                if route.startswith("/expired/") or SECRET not in query or (site.dies_after_cut and site.cuts):
                    return self._empty(403)
                file = site.folder / route.rsplit("/", 1)[-1]
                if not file.is_file():
                    return self._empty(404)
                data = file.read_bytes()
                rng = blobserve.parse_range(self.headers.get("Range"), len(data))
                start, end = rng if isinstance(rng, tuple) else (0, len(data) - 1)
                self.send_response(206 if isinstance(rng, tuple) else 200)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(end - start + 1))
                if isinstance(rng, tuple):
                    self.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
                self.end_headers()
                sent = 0
                try:
                    for i in range(start, end + 1, 64 * 1024):
                        if site.delay_s:
                            time.sleep(site.delay_s)
                        if site.stall_after is not None and sent >= site.stall_after:
                            site.released.wait(60)  # silence: headers sent, the body stops coming
                            return
                        if site.cut_after is not None and sent >= site.cut_after:
                            site.cuts += 1
                            self.close_connection = True
                            return
                        piece = data[i : min(i + 64 * 1024, end + 1)]
                        self.wfile.write(piece)
                        sent += len(piece)
                except ConnectionError:
                    pass

            def _empty(self, code: int) -> None:
                self.send_response(code)
                self.send_header("Content-Length", "0")
                self.end_headers()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def link(self, name: str, route: str = "vod") -> str:
        return f"{self.base}/{route}/lectures/{name}?X-Amz-Expires=604800&{SECRET}"

    def close(self) -> None:
        self.released.set()
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def site(tmp_path: Path, make_clip: MakeClip) -> Iterator[_Site]:
    folder = tmp_path / "site"
    folder.mkdir()
    shutil.move(make_clip("lecture.mp4", seconds=6, video_rate="25"), folder / "lecture.mp4")
    # a longer one, several fragments long, with its index first and a heavy picture: ffprobe needs only its start
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc=size=640x360:rate=30000/1001:duration=40", "-f", "lavfi", "-i", "sine=duration=40",
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "8", "-c:a", "aac", "-movflags", "+faststart",
                    str(folder / "long.mp4")], check=True, timeout=120)  # fmt: skip
    s = _Site(folder)
    yield s
    s.close()


@pytest.fixture
def data(tmp_path: Path) -> Path:
    d = tmp_path / "data"
    d.mkdir()
    return d


@pytest.fixture
def client(data: Path) -> Iterator[TestClient]:
    with make_client(data, kill_after_s=1.0, allow_local_http_links=True) as c:
        yield c


def add_link(c: TestClient, url: str) -> Any:
    return c.post("/api/links", json={"url": url}, headers=ORIGIN)


def job_of(c: TestClient, job_id: str) -> dict[str, Any]:
    return next(j for j in jobs(c) if j["id"] == job_id)


# ---- what a link may be ----------------------------------------------------------------------------------------


def test_a_link_is_shown_without_its_secret_parts() -> None:
    link = links.parse(f"https://storage.example.com/bucket/talks/2024/the%20talk.mp4?{SECRET}")
    assert link.name == "the talk.mp4" and link.shown == "storage.example.com/…/the talk.mp4"
    assert "Signature" not in link.shown and "bucket" not in link.shown
    assert links.parse("https://host.example/x?a=1").name == "x.mp4"  # a name without a media ending gets one


@pytest.mark.parametrize(
    ("text", "why"),
    [("http://host.example/a.mp4", "secure links"), ("ftp://host.example/a.mp4", "secure links"),
     ("https://user:pw@host.example/a.mp4", "not a link"), ("https://host.example/a b.mp4", "one whole link"),
     ("", "one whole link"), ("https://" + "a" * 9000, "one whole link"), ("http://127.0.0.1:8/a.mp4", "secure"),
     ("https://host.example:port/a.mp4", "not a link")],
)  # fmt: skip
def test_links_the_app_refuses(text: str, why: str) -> None:
    with pytest.raises(links.LinkError, match=why):
        links.parse(text)


def test_a_redirect_is_followed_only_to_a_secure_link() -> None:
    handler = links._SecureRedirects(allow_http=False)
    with pytest.raises(links.LinkError, match="insecure"):
        handler.redirect_request(None, None, 302, "Found", {}, "http://storage.example.com/a.mp4")  # type: ignore[no-untyped-call]
    opts = blobserve.remote_input_options()
    protocols = opts[opts.index("-protocol_whitelist") + 1]
    assert protocols == "https,tls,tcp"


def test_a_site_that_answers_a_byte_at_a_time_cannot_hold_the_link_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    from parakeet_stt.cancel import Cancelled, CancelToken

    monkeypatch.setattr(links, "_one_byte", lambda _link: time.sleep(5))  # every read inside its own timeout
    link = links.parse("https://example.com/talk.mp4")
    began = time.monotonic()
    with pytest.raises(links.LinkError, match="did not answer in time"):
        links.reachable(link, deadline_s=0.5)
    assert time.monotonic() - began < 2
    stop = CancelToken()
    stop.cancel()
    with pytest.raises(Cancelled):
        links.reachable(link, stop)


def test_ffmpeg_checks_the_certificate_and_opens_one_media_file_only() -> None:
    opts = blobserve.remote_input_options()
    assert opts[opts.index("-tls_verify") + 1] == "1"  # ffmpeg's TLS accepts any certificate unless told
    formats = opts[opts.index("-format_whitelist") + 1].split(",")
    assert "hls" not in formats and "dash" not in formats and "concat" not in formats and "mov" in formats


# ---- fetching -------------------------------------------------------------------------------------------------


def test_a_link_gives_a_job_that_keeps_only_the_sealed_sound(
    client: TestClient, data: Path, site: _Site, caplog: pytest.LogCaptureFixture
) -> None:
    r = add_link(client, site.link("lecture.mp4"))
    assert r.status_code == 201, r.text
    job = r.json()
    assert job["state"] == "fetching" and job["name"] == "lecture.mp4" and job["source"]["kind"] == "link"
    wait(lambda: state_of(client, job["id"]) == "done", timeout=60)
    wait(lambda: (job_of(client, job["id"])["preview"] or {}).get("state") == "ready", timeout=60)
    folder = data / "uploads" / job["source"]["upload_id"]
    assert sorted(p.name for p in folder.iterdir()) == ["link", "sound.m4a"]
    assert unsealed(folder / "link", Role.LINK).decode() == site.link("lecture.mp4")
    plain = data.parent / "check.m4a"
    plain.write_bytes(unsealed(folder / "sound.m4a", Role.SOURCE))
    got = media.probe(plain)
    assert not got.has_video and got.duration_s == pytest.approx(6.0, abs=0.1)
    storage_check(data)
    # the secret is nowhere: not in what the window gets, not in the queue, not in the log
    assert SECRET not in json.dumps(client.get("/api/status").json())
    assert SECRET not in unsealed(data / "queue.json", Role.QUEUE).decode()
    assert SECRET not in caplog.text and "d0c0ffee" not in caplog.text
    assert all(not r.startswith("HEAD") for r in site.requests)


def storage_check(data: Path) -> None:
    left = storage.plain_files(data)
    assert left == [], left


def test_an_expired_link_fails_with_one_sentence(client: TestClient, site: _Site) -> None:
    job = add_link(client, site.link("lecture.mp4", route="expired")).json()
    wait(lambda: state_of(client, job["id"]) == "failed", timeout=30)
    assert job_of(client, job["id"])["error"] == links.EXPIRED
    gone = add_link(client, site.link("nothing.mp4")).json()
    wait(lambda: state_of(client, gone["id"]) == "failed", timeout=30)
    assert "Nothing is at this link" in job_of(client, gone["id"])["error"]


def test_a_link_to_something_that_is_not_a_recording_fails_readably(client: TestClient, site: _Site) -> None:
    (site.folder / "notes.mp4").write_bytes(b"this is not media" * 100)
    job = add_link(client, site.link("notes.mp4")).json()
    wait(lambda: state_of(client, job["id"]) == "failed", timeout=30)
    error = job_of(client, job["id"])["error"]
    assert error == "The recording behind this link could not be read; it may not be audio or video."


def test_a_silent_download_stops_at_once_when_cancelled_and_leaves_nothing(
    client: TestClient, data: Path, site: _Site
) -> None:
    """Headers came, then the body stopped: only killing ffmpeg ends that. The next link must not wait for it."""
    site.stall_after = 2 * 1024 * 1024  # ffprobe reads the index at the start; the download falls silent later
    job = add_link(client, site.link("long.mp4")).json()
    folder = data / "uploads" / job["source"]["upload_id"]
    part = folder / "sound.m4a.part"
    wait(part.exists, timeout=30)  # ffmpeg is writing: the stall is in the download
    time.sleep(1.5)  # really silent now
    t0 = time.monotonic()
    assert client.post(f"/api/jobs/{job['id']}/cancel", json={}, headers=ORIGIN).status_code == 200
    site.stall_after = None
    nxt = add_link(client, site.link("lecture.mp4")).json()
    wait(lambda: state_of(client, nxt["id"]) in ("queued", "running", "done"), timeout=10)
    assert time.monotonic() - t0 < 4  # the stalled one was let go at once, not after a network timeout
    assert sorted(p.name for p in folder.iterdir()) == ["link"]
    assert client.post(f"/api/jobs/{job['id']}/remove", json={}, headers=ORIGIN).status_code == 204
    time.sleep(0.5)
    assert not folder.exists()


def test_a_dropped_connection_is_picked_up_again(client: TestClient, data: Path, site: _Site) -> None:
    site.cut_after = 3 * 1024 * 1024  # the connection drops every 3 MB; ffmpeg asks again from where it was
    job = add_link(client, site.link("long.mp4")).json()
    wait(lambda: state_of(client, job["id"]) in ("failed", "done"), timeout=120)
    assert state_of(client, job["id"]) == "done" and site.cuts >= 1
    plain = data.parent / "resumed.m4a"
    plain.write_bytes(unsealed(data / "uploads" / job["source"]["upload_id"] / "sound.m4a", Role.SOURCE))
    assert media.probe(plain).duration_s == pytest.approx(40.0, abs=0.2)


def test_a_download_cut_short_is_an_error_not_a_shorter_lecture(client: TestClient, data: Path, site: _Site) -> None:
    site.cut_after = 3 * 1024 * 1024  # the connection drops part of the way in ...
    site.dies_after_cut = True  # ... and the link has expired meanwhile: the rest never comes
    job = add_link(client, site.link("long.mp4")).json()
    wait(lambda: state_of(client, job["id"]) in ("failed", "done"), timeout=120)
    j = job_of(client, job["id"])
    assert j["state"] == "failed" and ("cut" in j["error"] or "completely" in j["error"]), j
    assert sorted(p.name for p in (data / "uploads" / job["source"]["upload_id"]).iterdir()) == ["link"]


def test_a_long_link_keeps_its_frame_rate_and_whole_length(client: TestClient, data: Path, site: _Site) -> None:
    job = add_link(client, site.link("long.mp4")).json()
    wait(lambda: state_of(client, job["id"]) == "done", timeout=120)
    src = job_of(client, job["id"])["source"]
    assert src["had_video"] and src["frame_rate"] == "30000/1001"
    plain = data.parent / "long-sound.m4a"
    plain.write_bytes(unsealed(data / "uploads" / src["upload_id"] / "sound.m4a", Role.SOURCE))
    assert media.probe(plain).duration_s == pytest.approx(40.0, abs=0.2)  # all fragments, not only the first


def test_a_download_cut_by_closing_the_app_starts_again(data: Path, site: _Site) -> None:
    site.delay_s = 0.5
    with make_client(data, kill_after_s=1.0, allow_local_http_links=True) as c1:
        job = add_link(c1, site.link("lecture.mp4")).json()
        wait(lambda: len(site.requests) > 1)
    site.delay_s = 0.0
    with make_client(data, kill_after_s=1.0, allow_local_http_links=True) as c2:
        wait(lambda: state_of(c2, job["id"]) == "done", timeout=60)


# ---- a dropped video keeps only its sound -------------------------------------------------------------------------


def test_a_dropped_video_keeps_only_its_sound(client: TestClient, data: Path, make_clip: MakeClip) -> None:
    clip = make_clip("talk.mp4", seconds=3, video_rate="25")
    headers = {**ORIGIN, "content-type": "application/octet-stream", "x-file-name": quote("Talk 1.mp4")}
    r = client.put("/api/uploads", content=clip.read_bytes(), headers=headers)
    assert r.status_code == 201, r.text
    src = r.json()["source"]
    folder = data / "uploads" / src["upload_id"]
    assert [p.name for p in folder.iterdir()] == ["sound.m4a"] and src["path"] == str(folder / "sound.m4a")
    assert r.json()["name"] == "Talk 1.mp4" and src["original_name"] == "Talk 1.mp4"
    assert src["had_video"] and src["frame_rate"] == "25/1"
    wait(lambda: state_of(client, r.json()["id"]) == "done")


def test_keeping_the_sound_changes_no_sample(tmp_path: Path, make_clip: MakeClip) -> None:
    key = key_of(tmp_path / "data")

    def pcm(p: Path) -> bytes:
        return subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(p), "-map", "0:a:0", "-ac", "1", "-ar",
                               "16000", "-f", "s16le", "pipe:1"], capture_output=True, check=True).stdout  # fmt: skip

    for name, audio in (("aac.mp4", ["-c:a", "aac"]), ("opus.mkv", ["-c:a", "libopus"])):
        src = tmp_path / name
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
                        "testsrc=size=64x48:rate=25:d=4", "-itsoffset", "0.3", "-f", "lavfi", "-i",
                        "anoisesrc=d=4:seed=2:sample_rate=48000", "-c:v", "libx264", *audio, str(src)],
                       check=True)  # fmt: skip
        target = tmp_path / "data" / f"{name}.m4a"
        sound.keep(media.probe(src), target, key)
        plain = tmp_path / f"plain-{name}.m4a"
        plain.write_bytes(key.read(target, Role.SOURCE))
        if name == "aac.mp4":  # copied as it is: every sample the same
            assert pcm(plain) == pcm(src)
        else:  # opus cannot be copied exactly: lossless FLAC of its decoded sound, the same length
            assert media.probe(plain).audio_codec == "flac" and len(pcm(plain)) == len(pcm(src))


def test_a_sound_file_dropped_as_it_is_stays_as_it_is(tmp_path: Path, make_clip: MakeClip) -> None:
    key = Vault.create_or_open(tmp_path / "data")
    clip = make_clip("talk.wav", seconds=2)
    sealed = tmp_path / "data" / "uploads" / "0123456789ab" / "source.wav"
    with key.writer(sealed, Role.SOURCE) as w:
        w.write(clip.read_bytes())
    assert uploads.keep_sound_only(sealed, key) == (sealed, None) and vault.is_sealed(sealed)


# ---- free disk space -------------------------------------------------------------------------------------------


def test_the_kept_sound_is_estimated_from_its_bit_rate() -> None:
    base = media.MediaInfo(path=Path("x.mp4"), size_bytes=0, duration_s=3600.0, has_video=True, frame_rate=None,
                           audio_streams=1, sample_rate_hz=48_000, channels=2, audio_codec="aac")  # fmt: skip
    hour_at = {128_000: 57_600_000, 0: 230_400_000}  # an unknown rate counts as 512 kbit/s
    for rate, size in hour_at.items():
        assert sound.estimate_bytes(media.MediaInfo(**{**base.__dict__, "audio_bit_rate": rate})) == size
    opus = media.MediaInfo(**{**base.__dict__, "audio_codec": "opus"})  # made into FLAC: at most 4 bytes a sample
    assert sound.estimate_bytes(opus) == 48_000 * 2 * 4 * 3600


def test_a_link_is_refused_when_its_sound_would_fill_the_disk(
    client: TestClient, data: Path, site: _Site, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sound, "free_bytes", lambda _folder: uploads.SPARE_BYTES + 1024)
    job = add_link(client, site.link("long.mp4")).json()
    wait(lambda: state_of(client, job["id"]) in ("failed", "done"), timeout=60)
    j = job_of(client, job["id"])
    assert j["state"] == "failed" and j["error"].startswith("Not enough free disk space for this recording"), j
    assert "MB must stay free" in j["error"]
    assert sorted(p.name for p in (data / "uploads" / job["source"]["upload_id"]).iterdir()) == ["link"]


def test_a_download_stops_cleanly_when_the_disk_fills_up_meanwhile(
    client: TestClient, data: Path, site: _Site, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = [0]

    def free(_folder: Path) -> int:  # plenty when it starts, then another program fills the disk
        calls[0] += 1
        return 100 * 1024**3 if calls[0] == 1 else uploads.SPARE_BYTES - 1

    monkeypatch.setattr(sound, "free_bytes", free)
    monkeypatch.setattr(sound, "GUARD_EVERY", 64 * 1024)
    job = add_link(client, site.link("long.mp4")).json()
    wait(lambda: state_of(client, job["id"]) in ("failed", "done"), timeout=60)
    j = job_of(client, job["id"])
    assert j["state"] == "failed" and "the disk is almost full" in j["error"], j
    assert calls[0] >= 2
    assert sorted(p.name for p in (data / "uploads" / job["source"]["upload_id"]).iterdir()) == ["link"]


def test_a_full_disk_while_writing_is_said_plainly(
    client: TestClient, data: Path, site: _Site, monkeypatch: pytest.MonkeyPatch
) -> None:
    def full(self: object, data: bytes) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(vault.SealedWriter, "write", full)
    job = add_link(client, site.link("lecture.mp4")).json()
    wait(lambda: state_of(client, job["id"]) in ("failed", "done"), timeout=60)
    j = job_of(client, job["id"])
    assert j["state"] == "failed" and j["error"] == sound.DISK_FULL, j
    assert sorted(p.name for p in (data / "uploads" / job["source"]["upload_id"]).iterdir()) == ["link"]
