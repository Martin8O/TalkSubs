"""The pinned model files: is everything here, and the first download with true byte progress and resume."""

from __future__ import annotations

import contextlib
import hashlib
import socket
import ssl
import urllib.error
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from parakeet_stt import models
from parakeet_stt.cancel import Cancelled, CancelToken

CONFIG = b'{"model_type": "x"}'
BIG = bytes(range(256)) * 12  # 3072 bytes
OTHER_DATA = b"v" * 50


def pin(data: bytes) -> models.Pin:
    return models.Pin(len(data), hashlib.sha256(data).hexdigest())


REPO = models.Repo("someone/model", "a" * 40, {"config.json": pin(CONFIG), "big.onnx": pin(BIG)})
OTHER = models.Repo("someone/vad", "b" * 40, {"vad.onnx": pin(OTHER_DATA)})
CONTENT = {"config.json": CONFIG, "big.onnx": BIG, "vad.onnx": OTHER_DATA}
TOTAL = len(CONFIG) + len(BIG)
PLENTY = 10**12


def put(cache: Path, repo: models.Repo, name: str, data: bytes | None = None, suffix: str = "") -> Path:
    path = models.folder(repo, cache) / (name + suffix)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(CONTENT[name] if data is None else data)
    return path


class _Resp:
    def __init__(self, status: int, body: bytes, cut_after: int | None) -> None:
        self.status = status
        self._body = body
        self._at = 0
        self._cut = cut_after

    def read(self, n: int) -> bytes:
        if self._cut is not None and self._at >= self._cut:
            raise ConnectionResetError(10054, "reset")
        end = self._at + n if self._cut is None else min(self._at + n, self._cut)
        out = self._body[self._at : end]
        self._at += len(out)
        return out


class FakeSite:
    """Stands in for the model site: answers ranges (or ignores them), can drop the connection or be away."""

    def __init__(self, *, ranges: bool = True, cut_after: int | None = None, away: BaseException | None = None):
        self.ranges = ranges
        self.cut_after = cut_after
        self.away = away
        self.asked: list[tuple[str, int]] = []

    @contextlib.contextmanager
    def __call__(self, url: str, have: int) -> Iterator[_Resp]:
        self.asked.append((url, have))
        if self.away is not None:
            raise self.away
        body = CONTENT[url.rsplit("/", 1)[1]]
        if have and self.ranges:
            yield _Resp(206, body[have:], self.cut_after)
        else:
            yield _Resp(200, body, self.cut_after)


def fetch(
    cache: Path,
    site: FakeSite,
    seen: list[int] | None = None,
    repos: tuple[models.Repo, ...] = (REPO,),
    free_space: Callable[[Path], int] = lambda _: PLENTY,
) -> None:
    report = (lambda d, _t: seen.append(d)) if seen is not None else None
    models.fetch(report, repos=repos, cache=cache, opener=site, free_space=free_space)


def test_missing_lists_absent_and_wrong_sized_files(tmp_path: Path) -> None:
    assert models.missing([REPO], tmp_path) == [(REPO, "config.json"), (REPO, "big.onnx")]
    put(tmp_path, REPO, "config.json")
    put(tmp_path, REPO, "big.onnx", BIG[:-1])  # a cut-off copy is not "here"
    assert models.missing([REPO], tmp_path) == [(REPO, "big.onnx")]
    put(tmp_path, REPO, "big.onnx")
    assert models.present([REPO], tmp_path)


def test_fetch_asks_for_the_pinned_revision_and_reports_every_chunk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(models, "CHUNK", 1000)
    put(tmp_path, OTHER, "vad.onnx")
    site, seen = FakeSite(), list[int]()
    fetch(tmp_path, site, seen, repos=(REPO, OTHER))
    at = f"https://huggingface.co/someone/model/resolve/{'a' * 40}"
    assert [u for u, _ in site.asked] == [f"{at}/config.json", f"{at}/big.onnx"]
    assert seen == sorted(seen) and seen[0] == 50 and seen[-1] == 50 + TOTAL  # never backwards, ends at all
    assert {50 + len(CONFIG) + n for n in (1000, 2000, 3000)} <= set(seen)  # every chunk shows
    assert models.present([REPO, OTHER], tmp_path)
    assert not list(models.folder(REPO, tmp_path).glob("*.part"))


def test_an_interrupted_download_goes_on_where_it_stopped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(models, "CHUNK", 500)
    put(tmp_path, REPO, "config.json")
    with pytest.raises(models.ModelFetchError, match="connection dropped"):
        fetch(tmp_path, FakeSite(cut_after=1500))
    part = models.folder(REPO, tmp_path) / "big.onnx.part"
    assert part.stat().st_size == 1500
    site, seen = FakeSite(), list[int]()
    fetch(tmp_path, site, seen)
    assert site.asked[-1][1] == 1500  # asked only for the rest
    assert seen[0] == len(CONFIG) + 1500 and seen[-1] == TOTAL
    assert (models.folder(REPO, tmp_path) / "big.onnx").read_bytes() == BIG


def test_a_site_that_ignores_the_range_starts_the_part_over(tmp_path: Path) -> None:
    put(tmp_path, REPO, "config.json")
    put(tmp_path, REPO, "big.onnx", BIG[:1000], suffix=".part")
    fetch(tmp_path, FakeSite(ranges=False))
    assert (models.folder(REPO, tmp_path) / "big.onnx").read_bytes() == BIG


def test_a_damaged_part_is_refused_and_removed(tmp_path: Path) -> None:
    put(tmp_path, REPO, "config.json")
    put(tmp_path, REPO, "big.onnx", b"\0" * 1000, suffix=".part")  # not the first 1000 bytes of the real file
    with pytest.raises(models.ModelFetchError, match=r"\(big\.onnx\) is damaged"):
        fetch(tmp_path, FakeSite())
    assert not models.folder(REPO, tmp_path).joinpath("big.onnx.part").exists()
    assert not models.folder(REPO, tmp_path).joinpath("big.onnx").exists()
    fetch(tmp_path, FakeSite())  # Try again: from scratch, and right
    assert models.present([REPO], tmp_path)


def test_nothing_missing_needs_no_site(tmp_path: Path) -> None:
    put(tmp_path, REPO, "config.json")
    put(tmp_path, REPO, "big.onnx")
    site = FakeSite()
    fetch(tmp_path, site, free_space=lambda _: 0)
    assert site.asked == []


def test_too_little_disk_space_is_said_before_anything_downloads(tmp_path: Path) -> None:
    site = FakeSite()
    with pytest.raises(
        models.ModelFetchError, match=r"not enough free disk space.*needs 0\.5 GB.*has 0\.1 GB free"
    ) as e:
        fetch(tmp_path, site, free_space=lambda _: 100 * 1000**2)
    assert site.asked == [] and e.value.kind == "no_space" and e.value.free == 100 * 1000**2


@pytest.mark.parametrize(
    ("away", "words"),
    [
        (urllib.error.URLError(ConnectionRefusedError(10061, "refused")), "no_connection"),
        (urllib.error.URLError(socket.gaierror(11001, "getaddrinfo failed")), "no_connection"),
        (urllib.error.URLError(TimeoutError("timed out")), "dropped"),
        (urllib.error.URLError(ssl.SSLCertVerificationError(1, "certificate verify failed")), "secure"),
        (urllib.error.HTTPError("https://x", 503, "busy", {}, None), "site"),  # type: ignore[arg-type]
    ],
)
def test_a_failed_download_is_one_plain_sentence_with_its_code(tmp_path: Path, away: BaseException, words: str) -> None:
    with pytest.raises(models.ModelFetchError) as caught:
        fetch(tmp_path, FakeSite(away=away))
    assert caught.value.kind == words and "https://" not in str(caught.value)
    assert str(caught.value).endswith(("Try again.", "Try again later.", "another network.", "where it stopped."))


def test_a_full_disk_while_downloading_is_said_plainly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    put(tmp_path, REPO, "config.json")
    real_open = Path.open

    def full(self: Path, mode: str = "r", *a: object, **kw: object):  # type: ignore[no-untyped-def]
        fh = real_open(self, mode, *a, **kw)  # type: ignore[call-overload]
        if self.name.endswith(".part") and "b" in mode and ("w" in mode or "a" in mode):

            def write(_data: bytes) -> int:
                raise OSError(28, "No space left on device")

            fh.write = write
        return fh

    monkeypatch.setattr(Path, "open", full)
    with pytest.raises(models.ModelFetchError, match="The disk became full"):
        fetch(tmp_path, FakeSite())


def test_cancel_stops_inside_the_download_and_keeps_the_part(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(models, "CHUNK", 500)
    put(tmp_path, REPO, "config.json")
    token = CancelToken()

    def report(done: int, _total: int) -> None:
        if done > len(CONFIG) + 1000:
            token.cancel()

    with pytest.raises(Cancelled):
        models.fetch(report, token, repos=[REPO], cache=tmp_path, opener=FakeSite(), free_space=lambda _: PLENTY)
    assert (models.folder(REPO, tmp_path) / "big.onnx.part").stat().st_size == 1500
    assert not models.present([REPO], tmp_path)


def test_the_librarys_own_leftovers_are_swept(tmp_path: Path) -> None:
    blobs = models.folder(REPO, tmp_path).parent.parent / "blobs"
    blobs.mkdir(parents=True)
    (blobs / "abc.1234.incomplete").write_bytes(b"x" * 10)
    elsewhere = tmp_path / "models--someone--else" / "blobs"
    elsewhere.mkdir(parents=True)
    (elsewhere / "def.incomplete").write_bytes(b"x")
    fetch(tmp_path, FakeSite())
    assert not list(blobs.glob("*.incomplete")) and (elsewhere / "def.incomplete").exists()


def test_the_pins_describe_the_files_of_this_checkout() -> None:
    """On a PC that has the model, every pinned name, size and fingerprint matches it."""
    if not models.folder(models.PARAKEET).is_dir():
        pytest.skip("the speech model is not downloaded here")
    assert models.missing() == []
    for repo in models.NEEDED:
        for name, p in repo.files.items():
            if p.size < 50_000_000:  # the big one is checked by the end-to-end download (it takes seconds)
                assert hashlib.sha256((models.folder(repo) / name).read_bytes()).hexdigest() == p.sha256, name


def test_speech_models_are_pinned_and_default_is_v2() -> None:
    assert list(models.SPEECH_MODELS)[0] == "v2"
    for repo, onnx_asr_name in models.SPEECH_MODELS.values():
        assert len(repo.revision) == 40
        assert onnx_asr_name.startswith("nemo-parakeet-tdt-0.6b-")
        for name, pin in repo.files.items():
            assert pin.size > 0, name
            assert len(pin.sha256) == 64, name
