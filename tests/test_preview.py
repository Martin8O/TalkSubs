"""The preview: the proxy and waveform peaks made from a media file, and how the server hands them out."""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient

from parakeet_stt import media, preview
from parakeet_stt.cancel import Cancelled, CancelToken
from parakeet_stt.server import runner as runner_mod
from parakeet_stt.server.store import Store
from parakeet_stt.vault import Role, Vault
from tests.conftest import MakeClip, key_of, unsealed
from tests.test_server import ORIGIN, add, jobs, make_client, wait


def reference_peaks(path: Path) -> np.ndarray:
    """An independent decode of the same audio, reduced block by block the slow, obvious way."""
    raw = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-i", str(path), "-map", "0:a:0", "-ac", "1", "-ar", "8000",
         "-f", "s16le", "pipe:1"], capture_output=True, check=True, timeout=60,
    ).stdout  # fmt: skip
    s = np.frombuffer(raw, dtype="<i2")
    rows = [(s[i : i + 80].min() >> 8, s[i : i + 80].max() >> 8) for i in range(0, len(s), 80)]
    return np.array(rows, dtype=np.int8)


def plain(path: Path, role: Role = Role.PREVIEW_MEDIA) -> Path:
    """A plain copy of a sealed preview file beside it, for ffprobe/ffmpeg checks in a test only."""
    out = path.with_name("plain-" + path.name)
    out.write_bytes(unsealed(path, role))
    return out


def stream_starts(path: Path) -> dict[str, float]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,start_time", "-of", "json", str(path)],
        capture_output=True, text=True, check=True, timeout=60,
    )  # fmt: skip
    return {s["codec_type"]: float(s["start_time"]) for s in json.loads(out.stdout)["streams"]}


def test_a_video_gets_a_sound_only_preview_and_exact_peaks(make_clip: MakeClip, tmp_path: Path) -> None:
    """The app keeps no pictures: the preview of a video is its sound."""
    src = make_clip("lecture.mp4", seconds=4, video_rate="25")
    job = tmp_path / "job"
    seen: list[float] = []
    info = preview.build(src, job, key_of(tmp_path), lambda f, _d, _t: seen.append(f))
    root = preview.folder(job)
    assert info.kind == "audio" and (root / "audio.m4a").is_file() and not (job / preview.BUILD_DIR).exists()
    assert sorted(p.name for p in root.iterdir()) == ["audio.m4a", "peaks.bin", "preview.json"]
    got = np.frombuffer(unsealed(root / "peaks.bin", Role.PREVIEW_PEAKS), dtype=np.int8).reshape(-1, 2)
    assert got.shape == reference_peaks(src).shape == (info.peak_count, 2)
    assert (got == reference_peaks(src)).all()
    assert seen == sorted(seen) and all(0 <= f < 1 for f in seen)
    assert preview.read_ready(job, key_of(tmp_path)) == info
    assert not media.probe(plain(root / "audio.m4a")).has_video
    assert b"ftyp" not in (root / "audio.m4a").read_bytes()[:64]  # sealed, not a plain MP4


def test_an_audio_file_gets_an_audio_only_proxy(make_clip: MakeClip, tmp_path: Path) -> None:
    src = make_clip("talk.wav", seconds=2)
    info = preview.build(src, tmp_path / "job", key_of(tmp_path))
    assert info.kind == "audio" and info.media_file == "audio.m4a"
    assert not media.probe(plain(preview.folder(tmp_path / "job") / "audio.m4a")).has_video


def test_the_proxy_clock_starts_at_the_first_audio_sample(tmp_path: Path) -> None:
    """The transcript's time 0 is the first audio sample; a source whose audio starts late must not shift the player."""
    src = tmp_path / "late.mp4"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=64x48:rate=25:duration=4",
         "-itsoffset", "0.4", "-f", "lavfi", "-i", "sine=frequency=440:duration=4", "-c:v", "libx264", "-c:a", "aac",
         str(src)], check=True, timeout=60,
    )  # fmt: skip
    assert media.probe(src).audio_start_s == pytest.approx(0.376, abs=0.05)
    info = preview.build(src, tmp_path / "job", key_of(tmp_path))
    starts = stream_starts(plain(preview.folder(tmp_path / "job") / "audio.m4a"))
    assert starts == {"audio": pytest.approx(0.0, abs=0.003)}  # the sound starts exactly at 0, no picture
    assert info.peak_count / preview.PEAKS_PER_S == pytest.approx(media.probe(src).duration_s - 0.4, abs=0.1)


def decoded(path: Path) -> np.ndarray:
    raw = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-i", str(path), "-map", "0:a:0", "-ac", "1", "-ar", "8000",
         "-f", "f32le", "pipe:1"], capture_output=True, check=True, timeout=60,
    ).stdout  # fmt: skip
    return np.frombuffer(raw, dtype="<f4")


def lag_ms(a: np.ndarray, b: np.ndarray) -> float:
    n = min(len(a), len(b))
    a, b = a[:n] - a[:n].mean(), b[:n] - b[:n].mean()
    size = 1 << (2 * n - 1).bit_length()
    corr = np.fft.irfft(np.fft.rfft(a, size) * np.conj(np.fft.rfft(b, size)))
    shifts = np.concatenate([corr[-400:], corr[:400]])
    return float((int(np.argmax(shifts)) - 400) / 8)


@pytest.mark.parametrize("container", ["late.mp4", "late.ts"])
def test_the_proxy_audio_lines_up_with_the_source_audio(container: str, tmp_path: Path) -> None:
    """Noise has one alignment only: the proxy's sound must sit exactly where the transcript's sound does, also
    for a transport stream whose clock starts well after 0."""
    src = tmp_path / container
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=64x48:rate=25:duration=5",
         "-itsoffset", "0.4", "-f", "lavfi", "-i", "anoisesrc=d=5:c=white:seed=7:sample_rate=44100",
         "-c:v", "libx264", "-c:a", "aac", str(src)], check=True, timeout=60,
    )  # fmt: skip
    info = media.probe(src)
    assert info.audio_start_s > info.container_start_s  # the audio really starts late
    preview.build(src, tmp_path / "job", key_of(tmp_path))
    proxy = plain(preview.folder(tmp_path / "job") / "audio.m4a")
    assert abs(lag_ms(decoded(src), decoded(proxy))) <= 5
    assert len(decoded(proxy)) == pytest.approx(len(decoded(src)), abs=8000 * 0.05)  # nothing cut off at the start


def test_a_picture_with_an_odd_height_still_gets_a_proxy(tmp_path: Path) -> None:
    src = tmp_path / "odd.mkv"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=66x47:rate=25:duration=2",
         "-f", "lavfi", "-i", "sine=duration=2", "-c:v", "libx264", "-pix_fmt", "yuv444p", str(src)],
        check=True, timeout=60,
    )  # fmt: skip
    preview.build(src, tmp_path / "job", key_of(tmp_path))
    assert preview.read_ready(tmp_path / "job", key_of(tmp_path)) is not None


def test_a_cancelled_build_leaves_nothing(make_clip: MakeClip, tmp_path: Path) -> None:
    src = make_clip("c.mp4", seconds=3, video_rate="25")
    token = CancelToken()
    token.cancel()
    with pytest.raises(Cancelled):
        preview.build(src, tmp_path / "job", key_of(tmp_path), cancel=token)
    assert (
        not (tmp_path / "job" / preview.BUILD_DIR).exists()
        and preview.read_ready(tmp_path / "job", key_of(tmp_path)) is None
    )


def test_no_space_is_a_readable_failure(make_clip: MakeClip, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = make_clip("s.wav", seconds=2)
    monkeypatch.setattr(shutil, "disk_usage", lambda _p: SimpleNamespace(free=1024))
    with pytest.raises(preview.PreviewError, match="Not enough free disk space"):
        preview.build(src, tmp_path / "job", key_of(tmp_path))
    assert not (tmp_path / "job" / preview.BUILD_DIR).exists()


def test_a_preview_that_does_not_hold_together_is_not_ready(make_clip: MakeClip, tmp_path: Path) -> None:
    preview.build(make_clip("p.wav", seconds=2), tmp_path / "job", key_of(tmp_path))
    root = preview.folder(tmp_path / "job")
    assert preview.read_ready(tmp_path / "job", key_of(tmp_path)) is not None
    peaks = root / "peaks.bin"
    peaks.write_bytes(peaks.read_bytes()[:-2])  # a crash between the files
    assert preview.read_ready(tmp_path / "job", key_of(tmp_path)) is None
    (root / "preview.json").write_text("{", encoding="utf-8")
    assert preview.read_ready(tmp_path / "job", key_of(tmp_path)) is None
    assert preview.read_ready(tmp_path / "nothing", key_of(tmp_path)) is None


def test_a_missing_source_fails_with_a_sentence(tmp_path: Path) -> None:
    with pytest.raises(preview.PreviewError, match="File not found"):
        preview.build(tmp_path / "gone.mp4", tmp_path / "job", key_of(tmp_path))


# ---- in the server ---------------------------------------------------------------------------------------------


@pytest.fixture
def data(tmp_path: Path) -> Path:
    d = tmp_path / "data"
    d.mkdir()
    return d


@pytest.fixture
def client(data: Path) -> Iterator[TestClient]:
    with make_client(data, kill_after_s=1.0, busy_retry_s=0.3) as c:
        yield c


def preview_of(c: TestClient, job_id: str) -> dict[str, object] | None:
    return next(j["preview"] for j in jobs(c) if j["id"] == job_id)  # type: ignore[no-any-return]


def ready(c: TestClient, job_id: str) -> None:
    wait(lambda: (preview_of(c, job_id) or {}).get("state") in ("ready", "failed"), timeout=60)
    assert (preview_of(c, job_id) or {}).get("state") == "ready", preview_of(c, job_id)


def test_the_proxy_is_served_with_byte_ranges(client: TestClient, make_clip: MakeClip) -> None:
    job = add(client, make_clip("r.mp4", seconds=3, video_rate="25"))
    assert client.get(f"/api/jobs/{job['id']}/media").status_code in (404, 200)  # 404 until it is ready
    ready(client, job["id"])
    p = preview_of(client, job["id"])
    assert p is not None and p["kind"] == "audio" and p["peaks_per_s"] == 100 and p["fraction"] == 1.0
    url = f"/api/jobs/{job['id']}/media"
    full = client.get(url)
    assert full.status_code == 200 and full.headers["content-type"] == "audio/mp4"
    assert full.headers["accept-ranges"] == "bytes" and full.headers["cache-control"] == "no-store"
    part = client.get(url, headers={"range": "bytes=10-19"})
    assert part.status_code == 206 and part.content == full.content[10:20]
    assert part.headers["content-range"] == f"bytes 10-19/{len(full.content)}"
    assert client.get(url, headers={"range": f"bytes={len(full.content) + 5}-"}).status_code == 416
    head = client.head(url)
    assert head.status_code == 200 and int(head.headers["content-length"]) == len(full.content)
    peaks = client.get(f"/api/jobs/{job['id']}/peaks")
    assert peaks.status_code == 200 and len(peaks.content) % 2 == 0 and len(peaks.content) > 0


def test_a_proxy_damaged_after_its_first_block_ends_the_stream_early(
    data: Path, client: TestClient, make_clip: MakeClip
) -> None:
    job = add(client, make_clip("dmg.mp4", seconds=20, video_rate="25"))
    ready(client, job["id"])
    proxy = data / "jobs" / job["id"] / "preview" / "audio.m4a"
    whole = len(client.get(f"/api/jobs/{job['id']}/media").content)
    assert whole > 3 * 64 * 1024
    raw = bytearray(proxy.read_bytes())
    raw[-100] ^= 1  # the last chunk: the answer starts, then stops - never a full-length answer with garbage
    proxy.write_bytes(bytes(raw))
    try:
        got = client.get(f"/api/jobs/{job['id']}/media").content
    except Exception:  # noqa: BLE001 - the test client may raise on the cut-off stream instead
        got = b""
    assert len(got) < whole


def test_an_audio_job_is_served_as_audio(client: TestClient, make_clip: MakeClip) -> None:
    job = add(client, make_clip("a.wav", seconds=2))
    ready(client, job["id"])
    assert client.get(f"/api/jobs/{job['id']}/media").headers["content-type"] == "audio/mp4"


def test_the_preview_routes_say_so_when_there_is_nothing(client: TestClient) -> None:
    for route in ("media", "peaks"):
        r = client.get(f"/api/jobs/aaaaaaaaaaaa/{route}")
        assert r.status_code == 404 and r.json() == {"error": "No such job."}


def test_a_preview_that_cannot_be_made_is_shown_as_failed(
    client: TestClient, make_clip: MakeClip, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_a: object, **_k: object) -> preview.PreviewInfo:
        raise preview.PreviewError("The preview of x.wav could not be made: boom")

    monkeypatch.setattr(preview, "build", boom)
    job = add(client, make_clip("f.wav", seconds=2))
    wait(lambda: (preview_of(client, job["id"]) or {}).get("state") == "failed", timeout=30)
    assert "boom" in str((preview_of(client, job["id"]) or {})["error"])
    nothing = client.get(f"/api/jobs/{job['id']}/media")  # nothing to play
    assert nothing.status_code == 404 and nothing.json() == {"error": "The preview is not ready yet."}


def test_removing_a_job_deletes_its_preview(data: Path, client: TestClient, make_clip: MakeClip) -> None:
    job = add(client, make_clip("rm.mp4", seconds=2, video_rate="25"))
    ready(client, job["id"])
    wait(lambda: next(j["state"] for j in jobs(client) if j["id"] == job["id"]) == "done", timeout=30)
    assert client.post(f"/api/jobs/{job['id']}/remove", json={}, headers=ORIGIN).status_code == 204
    assert not (data / "jobs" / job["id"]).exists()


def test_removing_a_job_while_its_preview_is_made_stops_the_maker_first(
    data: Path, client: TestClient, make_clip: MakeClip, monkeypatch: pytest.MonkeyPatch
) -> None:
    started, order = threading.Event(), []

    def slow(
        _src: Path, _job_dir: Path, _vault: object, _progress: object = None, cancel: CancelToken | None = None
    ) -> preview.PreviewInfo:
        started.set()
        assert cancel is not None
        while not cancel.cancelled:
            time.sleep(0.02)
        order.append("stopped")
        raise Cancelled

    monkeypatch.setattr(preview, "build", slow)
    job = add(client, make_clip("slow.mp4", seconds=2, video_rate="25"))
    assert started.wait(10)
    wait(lambda: next(j["state"] for j in jobs(client) if j["id"] == job["id"]) == "done", timeout=30)
    assert client.post(f"/api/jobs/{job['id']}/remove", json={}, headers=ORIGIN).status_code == 204
    assert order == ["stopped"] and not (data / "jobs" / job["id"]).exists() and jobs(client) == []


def test_a_restart_makes_a_missing_preview_again_and_keeps_a_good_one(data: Path, make_clip: MakeClip) -> None:
    src = make_clip("again.mp4", seconds=2, video_rate="25")
    with make_client(data, kill_after_s=1.0) as c1:
        job = add(c1, src)
        ready(c1, job["id"])
        wait(lambda: next(j["state"] for j in jobs(c1) if j["id"] == job["id"]) == "done", timeout=30)
    folder = data / "jobs" / job["id"] / "preview"
    before = (folder / "audio.m4a").stat().st_mtime_ns
    with make_client(data, kill_after_s=1.0) as c2:
        wait(lambda: (preview_of(c2, job["id"]) or {}).get("state") == "ready", timeout=30)  # found on disk
        assert (folder / "audio.m4a").stat().st_mtime_ns == before
    shutil.rmtree(folder)
    with make_client(data, kill_after_s=1.0) as c3:
        ready(c3, job["id"])  # made again
    assert (folder / "audio.m4a").is_file()


def test_the_queue_file_never_holds_the_preview_state(data: Path, client: TestClient, make_clip: MakeClip) -> None:
    job = add(client, make_clip("q.wav", seconds=2))
    ready(client, job["id"])
    wait(lambda: next(j["state"] for j in jobs(client) if j["id"] == job["id"]) == "done", timeout=30)
    assert all(j.preview is None for j in Store(data, Vault.open(data)).load())
    assert "preview" in json.loads(unsealed(data / "queue.json", Role.QUEUE))["jobs"][0]


def test_a_new_run_clears_the_transcript_but_keeps_the_preview(tmp_path: Path) -> None:
    folder = tmp_path / "job"
    (folder / "preview").mkdir(parents=True)
    (folder / "preview.part").mkdir()
    (folder / "preview" / "video.mp4").write_bytes(b"x")
    (folder / "a.pstt.json").write_text("x", encoding="utf-8")
    (folder / "sub").mkdir()
    runner_mod._clear_job_dir(folder)
    assert sorted(p.name for p in folder.iterdir()) == ["preview", "preview.part"]
    runner_mod._clear_job_dir(tmp_path / "nothing")


def test_a_folder_whose_job_is_gone_is_swept_only_when_it_holds_just_preview_files(data: Path) -> None:
    stray = data / "jobs" / "0123456789ab"
    (stray / "preview").mkdir(parents=True)
    (stray / "preview" / "video.mp4").write_bytes(b"x")
    kept = data / "jobs" / "ba9876543210"  # a transcript: the queue file may have been reset, the work stays
    kept.mkdir(parents=True)
    (kept / "talk.pstt.json").write_text("{}", encoding="utf-8")
    with make_client(data, kill_after_s=1.0):
        pass
    assert not stray.exists() and (kept / "talk.pstt.json").is_file()


def _cut_after(seconds: float, times: int) -> tuple[object, list[int]]:
    """A sound pass that, for its first `times` runs, stops by itself after `seconds` (as when the reading of the
    source ends early without ffmpeg reporting an error)."""
    original, runs = preview._sound_command, [0]

    def command(info: media.MediaInfo) -> list[str]:
        runs[0] += 1
        cmd = original(info)
        return [*cmd[:-1], "-t", str(seconds), cmd[-1]] if runs[0] <= times else cmd

    return command, runs


def test_a_preview_whose_source_ends_early_is_not_ready(
    make_clip: MakeClip, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = make_clip("talk.wav", seconds=30)
    command, runs = _cut_after(5, times=99)
    monkeypatch.setattr(preview, "_sound_command", command)
    with pytest.raises(preview.PreviewError, match="ended after"):
        preview.build(src, tmp_path / "job", key_of(tmp_path))
    assert runs[0] == preview.CUT_SHORT_TRIES  # made again before it gave up
    assert preview.read_ready(tmp_path / "job", key_of(tmp_path)) is None  # nothing short is kept as ready
    assert not (tmp_path / "job" / preview.BUILD_DIR).exists()


def test_a_preview_cut_short_once_is_made_again_whole(
    make_clip: MakeClip, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = make_clip("talk.wav", seconds=30)
    command, runs = _cut_after(5, times=1)
    monkeypatch.setattr(preview, "_sound_command", command)
    info = preview.build(src, tmp_path / "job", key_of(tmp_path))
    assert runs[0] == 2
    proxy = media.probe(plain(preview.folder(tmp_path / "job") / "audio.m4a"))
    assert abs(proxy.duration_s - 30) < 0.1 and abs(info.duration_s - 30) < 0.1
