"""The sealed file format, the store key, and how ffmpeg reads a sealed file without a plain copy."""

from __future__ import annotations

import contextlib
import subprocess
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from parakeet_stt import blobserve, media, vault
from parakeet_stt.vault import CHUNK, HEADER, SEALED_CHUNK, Role, Vault, VaultError, VaultLocked
from tests.conftest import MakeClip

SIZES = [0, 1, CHUNK - 1, CHUNK, CHUNK + 1, 3 * CHUNK, 3 * CHUNK + 17]


@pytest.fixture
def key(tmp_path: Path) -> Vault:
    return Vault.create_or_open(tmp_path / "data")


def body(n: int) -> bytes:
    return bytes((i * 7 + i // 251) % 256 for i in range(n))


@pytest.mark.parametrize("n", SIZES)
def test_whole_and_streamed_files_round_trip_and_read_anywhere(key: Vault, tmp_path: Path, n: int) -> None:
    data = body(n)
    sealed = key.seal(data, Role.PROJECT)
    assert sealed[:4] == b"PSTE" and key.unseal(sealed, Role.PROJECT) == data
    path = tmp_path / "s.bin"
    with key.writer(path, Role.SOURCE) as w:
        for i in range(0, n, 10_000):  # pieces that do not line up with the chunks
            w.write(data[i : i + 10_000])
    assert not path.with_name("s.bin.part").exists()
    assert key.plain_size(path) == n and key.read(path, Role.SOURCE) == data
    with key.reader(path, Role.SOURCE) as r:
        assert r.size == n
        for start, length in [(0, 5), (CHUNK - 3, 10), (n // 2, CHUNK * 2), (n - 1, 9), (n + 4, 3)]:
            assert r.read_at(start, length) == data[start : start + length]


def test_the_same_content_never_seals_to_the_same_bytes(key: Vault) -> None:
    assert key.seal(b"same", Role.QUEUE) != key.seal(b"same", Role.QUEUE)


def _damaged(sealed: bytes, how: str) -> bytes:
    b = bytearray(sealed)
    if how == "flip":
        b[HEADER + 100] ^= 1
    elif how == "header":
        b[20] ^= 1  # the salt: part of every chunk's associated data
    elif how == "truncate":  # cut at a chunk boundary: the new last chunk was not written as the last one
        return bytes(b[: HEADER + 2 * SEALED_CHUNK])
    elif how == "reorder":
        first, second = HEADER, HEADER + SEALED_CHUNK
        b[first:second], b[second : second + SEALED_CHUNK] = b[second : second + SEALED_CHUNK], b[first:second]
    elif how == "short":
        return bytes(b[: HEADER + 5])
    return bytes(b)


@pytest.mark.parametrize("how", ["flip", "header", "truncate", "reorder", "short"])
def test_a_damaged_file_does_not_open(key: Vault, tmp_path: Path, how: str) -> None:
    sealed = key.seal(body(3 * CHUNK + 5), Role.PROJECT)
    with pytest.raises(VaultError):
        key.unseal(_damaged(sealed, how), Role.PROJECT)
    path = tmp_path / "f"
    path.write_bytes(_damaged(sealed, how))
    # the streaming reader finds it too, at the latest at the damaged chunk
    with pytest.raises(VaultError), key.reader(path, Role.PROJECT) as r:
        r.read_all()


def test_one_kind_of_file_cannot_be_passed_off_as_another(key: Vault) -> None:
    with pytest.raises(VaultError):
        key.unseal(key.seal(b'{"jobs": []}', Role.SETTINGS), Role.QUEUE)


def test_a_plain_file_is_refused_where_a_sealed_one_belongs(key: Vault, tmp_path: Path) -> None:
    plain = tmp_path / "queue.json"
    plain.write_text('{"schema_version": 1, "jobs": []}', encoding="utf-8")
    with pytest.raises(VaultError):
        key.read(plain, Role.QUEUE)
    assert not vault.is_sealed(plain)


def test_another_accounts_key_opens_nothing_and_says_so(tmp_path: Path) -> None:
    """A different DPAPI entropy stands in for another Windows account (or computer): its key cannot be unwrapped."""
    data = tmp_path / "data"
    mine = Vault.create_or_open(data)
    sealed = mine.seal(b"lecture text", Role.PROJECT)
    with pytest.raises(VaultLocked, match="another Windows account"):
        Vault.open(data, entropy=b"someone else")
    other = Vault.create_or_open(tmp_path / "other")  # a key of another data folder unwraps, but opens nothing
    with pytest.raises(VaultError):
        other.unseal(sealed, Role.PROJECT)


def test_the_key_is_made_once_with_a_second_copy_and_a_lost_copy_comes_back(tmp_path: Path) -> None:
    data = tmp_path / "data"
    with pytest.raises(VaultLocked):
        Vault.open(data)  # a worker or the CLI never makes a key
    first = Vault.create_or_open(data)
    main, backup = data / vault.KEY_FILE, data / vault.KEY_BACKUP
    assert main.read_bytes() == backup.read_bytes() and main.read_bytes()[:4] == b"PSTK"
    sealed = first.seal(b"x", Role.QUEUE)
    main.unlink()
    assert Vault.open(data).unseal(sealed, Role.QUEUE) == b"x"  # the copy is enough
    again = Vault.create_or_open(data)  # and the server puts the missing file back
    assert main.is_file() and again.unseal(sealed, Role.QUEUE) == b"x"
    backup.write_bytes(b"PSTK\x01garbage")
    assert Vault.open(data).unseal(sealed, Role.QUEUE) == b"x"  # one bad copy is not the end


def test_a_second_start_racing_the_first_uses_its_key(tmp_path: Path) -> None:
    data = tmp_path / "data"
    keys: list[Vault] = []
    threads = [threading.Thread(target=lambda: keys.append(Vault.create_or_open(data))) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    sealed = keys[0].seal(b"one key", Role.QUEUE)
    assert all(k.unseal(sealed, Role.QUEUE) == b"one key" for k in keys)


def test_a_writer_that_fails_leaves_nothing(key: Vault, tmp_path: Path) -> None:
    path = tmp_path / "x.bin"
    with pytest.raises(RuntimeError), key.writer(path, Role.SOURCE) as w:
        w.write(b"half")
        raise RuntimeError("stopped")
    assert list(tmp_path.iterdir()) == [tmp_path / "data"]


# ---- byte ranges ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("header", "want"),
    [(None, None), ("bytes=10-19", (10, 19)), ("bytes=10-", (10, 99)), ("bytes=-5", (95, 99)),
     ("bytes=90-500", (90, 99)), ("bytes=100-", False), ("bytes=-0", False), ("bytes=5-2", None),
     ("items=1-2", None), ("bytes=1-2,5-6", (1, 2))],
)  # fmt: skip
def test_range_headers(header: str | None, want: object) -> None:
    assert blobserve.parse_range(header, 100) == want


def test_no_range_of_an_empty_file_can_be_given() -> None:
    assert blobserve.parse_range("bytes=-5", 0) is False and blobserve.parse_range("bytes=0-", 0) is False


# ---- ffmpeg reading a sealed file -------------------------------------------------------------------------------


def _decoded(args: list[str]) -> bytes:
    cmd = ["ffmpeg", "-nostdin", "-v", "error", *args, "-map", "0:a:0", "-ac", "1", "-ar", "16000", "-f", "s16le",
           "pipe:1"]  # fmt: skip
    return subprocess.run(cmd, capture_output=True, check=True, timeout=60).stdout


@pytest.mark.parametrize("name", ["talk.mp4", "talk.m4a", "talk.mkv", "talk.webm", "talk.mp3", "talk.wav"])
def test_ffmpeg_reads_a_sealed_file_exactly_like_the_plain_one(
    key: Vault, tmp_path: Path, make_clip: MakeClip, name: str
) -> None:
    src = make_clip(name, seconds=3, video_rate="25" if name.endswith((".mp4", ".mkv", ".webm")) else None)
    sealed = tmp_path / "data" / "uploads" / "0123456789ab" / f"source{src.suffix}"
    with key.writer(sealed, Role.SOURCE) as w:
        w.write(src.read_bytes())
    with blobserve.media_input(sealed, key) as inp:
        assert inp.url is not None and inp.size_bytes == src.stat().st_size
        info = media.probe(inp)
        assert info.path == sealed and info.size_bytes == src.stat().st_size
        assert info.duration_s == pytest.approx(media.probe(src).duration_s, abs=0.05)
        assert _decoded(inp.input_args()) == _decoded(["-i", str(src)])
        assert len(media.decode(info)) == len(media.decode(media.probe(src)))
    with pytest.raises(urllib.error.URLError):
        urllib.request.urlopen(inp.url, timeout=2)  # gone once the run is over


def test_a_plain_file_is_read_directly(key: Vault, make_clip: MakeClip) -> None:
    src = make_clip("in-place.wav", seconds=1)
    with blobserve.media_input(src, key) as inp:
        assert inp.url is None and inp.input_args() == ["-i", str(src)]


def test_the_sealed_file_server_answers_only_its_address_and_its_own_user(key: Vault, tmp_path: Path) -> None:
    sealed = tmp_path / "data" / "s.bin"
    with key.writer(sealed, Role.SOURCE) as w:
        w.write(body(5000))
    with blobserve.serve(key, sealed) as url:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"Range": "bytes=100-199"}), timeout=5) as r:
            assert r.status == 206 and r.read() == body(5000)[100:200]
            assert r.headers["Content-Range"] == "bytes 100-199/5000"
        with pytest.raises(urllib.error.HTTPError) as e404:
            urllib.request.urlopen(url[:-3] + "xyz", timeout=5)
        assert e404.value.code == 404
    with blobserve.serve(key, sealed, peer_check=lambda _c, _s: False) as url:
        with pytest.raises(urllib.error.HTTPError) as e403:
            urllib.request.urlopen(url, timeout=5)
        assert e403.value.code == 403


def test_ffmpeg_errors_never_show_the_servers_address(key: Vault, tmp_path: Path) -> None:
    bad = tmp_path / "data" / "uploads" / "0123456789ab" / "source.mp4"
    with key.writer(bad, Role.SOURCE) as w:
        w.write(b"not media at all" * 100)
    with blobserve.media_input(bad, key) as inp, pytest.raises(media.MediaError) as caught:
        media.probe(inp)
    assert "source.mp4" in str(caught.value) and "127.0.0.1" not in str(caught.value)


def test_a_ranged_answer_is_one_window_never_the_rest_of_the_file(key: Vault, tmp_path: Path) -> None:
    """ffmpeg reads away the rest of an answer before it seeks: an open end would cost the rest of the file per seek."""
    sealed = tmp_path / "data" / "big.bin"
    data = body(2 * 1024 * 1024)
    with key.writer(sealed, Role.SOURCE) as w:
        w.write(data)
    with (
        blobserve.serve(key, sealed) as url,
        urllib.request.urlopen(urllib.request.Request(url, headers={"Range": "bytes=100-"}), timeout=5) as r,
    ):
        got = r.read()
        assert r.status == 206 and len(got) == blobserve.WINDOW and got == data[100 : 100 + blobserve.WINDOW]
        assert r.headers["Content-Range"] == f"bytes 100-{100 + blobserve.WINDOW - 1}/{len(data)}"


def test_ffmpeg_reads_a_large_sealed_file_without_decrypting_all_of_it_per_open(
    key: Vault, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "big.mp4"  # index at the end of the file, as most recordings have it
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc=size=640x360:rate=25:duration=20", "-f", "lavfi", "-i", "sine=duration=20",
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "5", "-c:a", "aac", str(src)],
                   check=True, timeout=120)  # fmt: skip
    sealed = tmp_path / "data" / "uploads" / "0123456789ab" / "source.mp4"
    with key.writer(sealed, Role.SOURCE) as w:
        w.write(src.read_bytes())
    served = [0]
    real = vault.SealedReader.read_at

    def counted(self: vault.SealedReader, offset: int, n: int) -> bytes:
        out = real(self, offset, n)
        served[0] += len(out)
        return out

    monkeypatch.setattr(vault.SealedReader, "read_at", counted)
    with blobserve.media_input(sealed, key) as inp:
        media.probe(inp)
        assert src.stat().st_size > 8 * blobserve.WINDOW and served[0] < src.stat().st_size / 4, served[0]
        # and across many windows it still reads exactly what the plain file holds
        assert _decoded(inp.input_args()) == _decoded(["-i", str(src)])


def test_one_connection_is_traced_to_its_owner_once(key: Vault, tmp_path: Path) -> None:
    import http.client

    sealed = tmp_path / "data" / "s.bin"
    with key.writer(sealed, Role.SOURCE) as w:
        w.write(body(3 * blobserve.WINDOW))
    checks: list[tuple[str, int]] = []

    def check(client: tuple[str, int], _server: tuple[str, int]) -> bool:
        checks.append(client)
        return True

    with blobserve.serve(key, sealed, peer_check=check) as url:
        host, rest = url.removeprefix("http://").split("/", 1)
        name, port = host.split(":")
        conn = http.client.HTTPConnection(name, int(port), timeout=5)
        for start in (0, blobserve.WINDOW, 2 * blobserve.WINDOW):
            conn.request("GET", "/" + rest, headers={"Range": f"bytes={start}-"})
            r = conn.getresponse()
            assert r.status == 206 and r.read() == body(3 * blobserve.WINDOW)[start : start + blobserve.WINDOW]
        conn.close()
    assert len(checks) == 1


def test_a_damaged_sealed_source_fails_instead_of_giving_a_short_result(
    key: Vault, tmp_path: Path, make_clip: MakeClip
) -> None:
    from parakeet_stt import preview

    clip = make_clip("long.wav", seconds=20)
    sealed = tmp_path / "data" / "uploads" / "0123456789ab" / "source.wav"
    with key.writer(sealed, Role.SOURCE) as w:
        w.write(clip.read_bytes())
    raw = bytearray(sealed.read_bytes())
    raw[len(raw) // 2] ^= 1  # one bad chunk half-way: ffmpeg would just see the sound end there
    sealed.write_bytes(bytes(raw))
    with blobserve.media_input(sealed, key) as inp:
        info = media.probe(inp)
        with contextlib.suppress(media.MediaError):  # ffmpeg may or may not notice the early end itself
            media.decode(info)
        assert inp.failed()
    with pytest.raises(preview.PreviewError, match=r"could not be read completely|could not be made"):
        preview.build(sealed, tmp_path / "data" / "jobs" / "aaaaaaaaaaaa", key)
    assert preview.read_ready(tmp_path / "data" / "jobs" / "aaaaaaaaaaaa", key) is None
