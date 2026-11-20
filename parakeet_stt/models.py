"""The model files the default engine needs, pinned to exact revisions, and their one-time download.

Every file is named with its exact size and SHA-256, so "is it here?" is a few `stat` calls and a run never asks
the network. The download is our own, file by file over HTTPS from the model's Hugging Face repository: it writes
`<name>.part` beside the final file, goes on from a part left by an earlier try (a stop, a crash, a dropped
connection), reports every received chunk (the progress the window shows), checks the fingerprint, and only then
renames the part into place. (huggingface_hub 2.x starts every download afresh in a new temporary file.)
"""

from __future__ import annotations

import hashlib
import http.client
import os
import shutil
import socket
import ssl
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any, Literal, Protocol

from parakeet_stt import project
from parakeet_stt.cancel import CancelToken


@dataclass(frozen=True)
class Pin:
    size: int
    sha256: str


@dataclass(frozen=True)
class Repo:
    repo_id: str
    revision: str  # a commit: the same bytes on every PC, whatever the repository's later commits change
    files: Mapping[str, Pin]

    @property
    def size(self) -> int:
        return sum(p.size for p in self.files.values())

    @property
    def pinned(self) -> str:
        return f"{self.repo_id}@{self.revision}"


PARAKEET = Repo(
    "istupakov/parakeet-tdt-0.6b-v2-onnx",
    "0bbb45a3365852604aef28b538a8f066f4ccaa85",
    {
        "config.json": Pin(97, "666903c76b9798caf2c210afd4f6cd60b08a8dbf9800ec8d7a3bc0d2148ac466"),
        "vocab.txt": Pin(9384, "ec182b70dd42113aff6c5372c75cac58c952443eb22322f57bbd7f53977d497d"),
        "decoder_joint-model.onnx": Pin(35_792_059, "cbb52a07bd70ab5b67f8439d4b3cd8704b18467b4430bcacb5adabe154b8d191"),
        "encoder-model.onnx": Pin(41_770_866, "3987bcd28175d829d12888a996a84e8f62a0e374d9ffd640662c1515adc679d3"),
        "encoder-model.onnx.data": Pin(
            2_435_420_160, "4dab7362d4874d85965045b1e41b2d61dd2cc0fb25671a7f6b3dc47bf120cc41"
        ),
    },
)
VAD = Repo(
    "istupakov/silero-vad-onnx",
    "b3e3ee3cce4c11ceb63b1a0b229d916069c1ddf6",
    {
        "config.json": Pin(30, "1094039d370c82889582ba739a3d1caac5754c8b3a17a66a534200c9f72086e2"),
        "silero_vad.onnx": Pin(2_327_524, "1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3"),
    },
)
NEEDED: tuple[Repo, ...] = (PARAKEET, VAD)
SPACE_MARGIN = 512 * 1024 * 1024  # left free on the drive after the download
CHUNK = 1024 * 1024
TIMEOUT_S = 10.0  # connecting, and any wait for the next chunk: also how long a stop of the app may wait
PART = ".part"

ProgressFn = Callable[[int, int], None]  # bytes on disk, bytes in all


class Response(Protocol):
    status: int

    def read(self, n: int, /) -> bytes: ...


Opener = Callable[[str, int], AbstractContextManager[Response]]  # (url, bytes already here) -> response

NO_CONNECTION = (
    "The speech model could not be downloaded: there is no connection to the internet, or huggingface.co cannot be "
    "reached. It is downloaded once, on the first start. Connect to the internet and press Try again."
)
DROPPED = (
    "The download of the speech model was interrupted (the connection dropped). Press Try again: it goes on where it "
    "stopped."
)
DISK_FULL = (
    "The disk became full while the speech model was downloading. Free some space and press Try again: it goes on "
    "where it stopped."
)
SECURE = (
    "The speech model could not be downloaded: a secure connection to huggingface.co could not be set up (a firewall "
    "or a company proxy may be in the way). Press Try again, or try on another network."
)

# Why a download failed, as a fixed code: the window says it in its own language
ErrorKind = Literal["no_connection", "dropped", "secure", "site", "disk_full", "no_space", "damaged", "other"]


class ModelFetchError(Exception):
    """The model files cannot be downloaded; the message is for the user (in English; `kind` for the window)."""

    def __init__(self, message: str, kind: ErrorKind = "other", need: int | None = None, free: int | None = None):
        super().__init__(message)
        self.kind: ErrorKind = kind
        self.need = need  # no_space: bytes the download needs, and bytes free on the drive
        self.free = free


def cache_dir() -> Path:
    raw = os.environ.get("HF_HUB_CACHE")
    if not raw:
        raise ModelFetchError("The app's environment is not set (no model folder); start it with TalkSubs.bat.")
    return Path(raw)


def folder(repo: Repo, cache: Path | None = None) -> Path:
    """Where the pinned files of `repo` are (or will be): the Hugging Face cache layout, so a copy that an earlier
    version (or the library itself) put there counts."""
    base = cache if cache is not None else cache_dir()
    return base / f"models--{repo.repo_id.replace('/', '--')}" / "snapshots" / repo.revision


def missing(repos: Sequence[Repo] = NEEDED, cache: Path | None = None) -> list[tuple[Repo, str]]:
    """Every needed file that is absent or not its exact size."""
    out = []
    for repo in repos:
        base = folder(repo, cache)
        for name, pin in repo.files.items():
            if _size(base / name) != pin.size:
                out.append((repo, name))
    return out


def present(repos: Sequence[Repo] = NEEDED, cache: Path | None = None) -> bool:
    return not missing(repos, cache)


def total_size(repos: Sequence[Repo] = NEEDED) -> int:
    return sum(r.size for r in repos)


def fetch(
    on_progress: ProgressFn | None = None,
    cancel: CancelToken | None = None,
    *,
    repos: Sequence[Repo] = NEEDED,
    cache: Path | None = None,
    opener: Opener | None = None,
    free_space: Callable[[Path], int] | None = None,
) -> None:
    """Download whatever of `repos` is missing. Raises `ModelFetchError` (a sentence for the user) or `Cancelled`
    (the part file stays and the next call goes on from it)."""
    cache = cache if cache is not None else cache_dir()
    cancel = cancel or CancelToken()
    report = on_progress or (lambda _done, _total: None)
    todo = missing(repos, cache)
    total = total_size(repos)
    parts = {(r.repo_id, n): min(_size(_part(folder(r, cache) / n)) or 0, r.files[n].size) for r, n in todo}
    done = total - sum(repo.files[name].size for repo, name in todo) + sum(parts.values())
    report(done, total)
    if not todo:
        return
    _sweep_library_leftovers(repos, cache)
    cache.mkdir(parents=True, exist_ok=True)
    need = total - done + SPACE_MARGIN
    free = (free_space or _free)(cache)
    if free < need:
        raise ModelFetchError(
            f"There is not enough free disk space for the speech model: it needs {_gb(need)} GB, the drive that "
            f"holds the app has {_gb(free)} GB free. Free some space and press Try again.",
            "no_space", need, free,
        )  # fmt: skip
    get = opener or _open
    for repo, name in todo:
        base = done - parts[(repo.repo_id, name)]  # what every other file adds

        def seen(n: int, base: int = base) -> None:
            report(base + n, total)

        _fetch_one(repo, name, folder(repo, cache), get, seen, cancel)
        done = base + repo.files[name].size
        report(done, total)


def _fetch_one(  # noqa: PLR0917 - one call site
    repo: Repo, name: str, where: Path, get: Opener, seen: Callable[[int], None], cancel: CancelToken
) -> None:
    url = f"{_endpoint()}/{repo.repo_id}/resolve/{repo.revision}/{name}"
    download(url, repo.files[name], where / name, get, seen, cancel)


def download(  # noqa: PLR0917
    url: str,
    pin: Pin,
    final: Path,
    get: Opener,
    seen: Callable[[int], None],
    cancel: CancelToken,
    what: str = "the speech model",
) -> None:
    """One pinned file from `url` to `final`: through `<final>.part`, going on from a part an earlier try left,
    fingerprint-checked before it is renamed into place. Also used for the graphics-card files (cardpack.py)."""
    name = final.name
    part = _part(final)
    final.parent.mkdir(parents=True, exist_ok=True)
    have = _size(part) or 0
    if have > pin.size:  # longer than the file can be: not ours to trust
        part.unlink()
        have = 0
    digest = hashlib.sha256()
    if have:
        with part.open("rb") as fh:
            for chunk in iter(lambda: fh.read(CHUNK), b""):
                digest.update(chunk)
    if have < pin.size:
        try:
            with get(url, have) as resp:
                if have and resp.status != 206:  # the server sent the whole file: start the part over
                    have, digest = 0, hashlib.sha256()
                with part.open("ab" if have else "wb") as out:
                    have = _copy(resp, out, have, pin.size, digest, seen, cancel, what)
        except ModelFetchError:
            raise
        except urllib.error.HTTPError as exc:
            raise ModelFetchError(
                f"The model site answered with an error (HTTP {exc.code}). Press Try again later.", "site"
            ) from None
        except urllib.error.URLError as exc:
            raise _explain(exc.reason) from None
        except (OSError, http.client.HTTPException) as exc:
            raise _explain(exc) from None
    if have != pin.size or digest.hexdigest() != pin.sha256:
        part.unlink(missing_ok=True)
        raise ModelFetchError(f"A downloaded part of {what} ({name}) is damaged. Press Try again.", "damaged")
    os.replace(part, final)


def _copy(  # noqa: PLR0917 - one call site
    resp: Response,
    out: IO[bytes],
    have: int,
    size: int,
    digest: Any,  # hashlib's sha256 object
    seen: Callable[[int], None],
    cancel: CancelToken,
    what: str = "the speech model",
) -> int:
    seen(have)
    while have < size:
        cancel.raise_if_cancelled()  # the part stays: the next try goes on from it
        chunk = resp.read(min(CHUNK, size - have))
        if not chunk:
            raise ModelFetchError(DROPPED, "dropped")
        try:
            out.write(chunk)
        except OSError as exc:
            if project.disk_full(exc):
                raise ModelFetchError(DISK_FULL, "disk_full") from None
            raise ModelFetchError(_write_failed(exc, what)) from None
        digest.update(chunk)
        have += len(chunk)
        seen(have)
    out.flush()
    return have


def _open(url: str, have: int) -> AbstractContextManager[Response]:
    headers = {"User-Agent": "parakeet-stt"}
    if have:
        headers["Range"] = f"bytes={have}-"
    req = urllib.request.Request(url, headers=headers)
    resp: AbstractContextManager[Response] = urllib.request.urlopen(req, timeout=TIMEOUT_S)
    return resp


def _endpoint() -> str:
    """The Hugging Face site; `HF_ENDPOINT` (the library's own setting) points it elsewhere, e.g. a mirror."""
    return (os.environ.get("HF_ENDPOINT") or "https://huggingface.co").rstrip("/")


def _sweep_library_leftovers(repos: Sequence[Repo], cache: Path) -> None:
    """Temporary files the library's own download left (it never goes on from them): only in our models' folders."""
    for repo in repos:
        for left in _glob(folder(repo, cache).parent.parent / "blobs", "*.incomplete"):
            left.unlink(missing_ok=True)


def _glob(where: Path, pattern: str) -> Iterator[Path]:
    return where.glob(pattern) if where.is_dir() else iter(())


def _part(final: Path) -> Path:
    return final.with_name(final.name + PART)


def _size(path: Path) -> int | None:
    try:
        return path.stat().st_size
    except OSError:
        return None


def _free(path: Path) -> int:
    return shutil.disk_usage(path).free


def _gb(n: int) -> str:
    return f"{n / 1000**3:.1f}"  # as the window counts (1 GB = 1000 MB)


def _write_failed(exc: OSError, what: str = "the speech model") -> str:
    return f"{what.capitalize()} could not be saved ({exc.strerror or exc.__class__.__name__}). Press Try again."


def _explain(reason: object) -> ModelFetchError:
    """One sentence for a network failure (never the address: a redirect carries a signed one)."""
    if isinstance(reason, ssl.SSLError):
        return ModelFetchError(SECURE, "secure")
    if isinstance(reason, ConnectionRefusedError | socket.gaierror | str):
        return ModelFetchError(NO_CONNECTION, "no_connection")  # a str: urllib's words before any connection
    if isinstance(reason, OSError) and project.disk_full(reason):
        return ModelFetchError(DISK_FULL, "disk_full")
    return ModelFetchError(DROPPED, "dropped")
