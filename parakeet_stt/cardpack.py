"""The graphics-card support of the download package, fetched once inside the app ("card pack").

The download package runs the model on the processor (onnxruntime without its CUDA provider, no NVIDIA libraries).
On a PC with a usable NVIDIA card the window offers this pack instead of a second, bigger package: onnxruntime 1.23.2
with its CUDA provider and NVIDIA's own CUDA 12 / cuDNN 9 runtime libraries, each from its official PyPI wheel, pinned
by size and SHA-256 (the CUDA 12.8 / cuDNN 9.19 generation the development checkout runs, docs/adr.md 0006).

The wheels are downloaded with the model's resumable, fingerprint-checked downloader into `card/downloads/`; then the
marker is removed, `card/site/` cleared, only what the app uses unpacked into it, and the marker written last: it names
the pins and every unpacked file with its size, and only a marker whose every file is there at its size counts
(`present()`, a few `stat` calls). The wheels are deleted. `activate()` (from `env.apply()`, first in every entry
point) puts `card/site` on `sys.path` just before the processor onnxruntime's site-packages, so the card one shadows
it and `gpu.cuda_libraries()` loads NVIDIA's libraries from `card/site/nvidia`. The development checkout has no
`card/`. `card/` gets the app folder's access list (docs/adr.md 0026): it holds code every process runs.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import stat
import sys
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from parakeet_stt import models
from parakeet_stt.cancel import CancelToken
from parakeet_stt.gpu import NVIDIA_DLLS

FOLDER = "card"
MARKER = "READY.json"
WHAT = "the graphics-card support"
UNPACKED_BYTES = 2_700_000_000  # what the unpacked pack takes (measured 2.6 GB), for the free-space check


@dataclass(frozen=True)
class Wheel:
    url: str
    pin: models.Pin

    @property
    def name(self) -> str:
        return self.url.rsplit("/", 1)[1]


_PYPI = "https://files.pythonhosted.org/packages/"
ONNXRUNTIME_GPU = Wheel(
    _PYPI + "a9/5c/dba7c009e73dcce02e7f714574345b5e607c5c75510eb8d7bef682b45e5d/"
    "onnxruntime_gpu-1.23.2-cp311-cp311-win_amd64.whl",
    models.Pin(244_506_823, "054282614c2fc9a4a27d74242afbae706a410f1f63cc35bc72f99709029a5ba4"),
)
NVIDIA = (
    Wheel(
        _PYPI + "30/a5/a515b7600ad361ea14bfa13fb4d6687abf500adc270f19e89849c0590492/"
        "nvidia_cuda_runtime_cu12-12.8.90-py3-none-win_amd64.whl",
        models.Pin(944_318, "c0c6027f01505bfed6c3b21ec546f69c687689aad5f1a377554bc6ca4aa993a8"),
    ),
    Wheel(
        _PYPI + "70/61/7d7b3c70186fb651d0fbd35b01dbfc8e755f69fd58f817f3d0f642df20c3/"
        "nvidia_cublas_cu12-12.8.4.1-py3-none-win_amd64.whl",
        models.Pin(567_544_208, "47e9b82132fa8d2b4944e708049229601448aaad7e6f296f630f2d1a32de35af"),
    ),
    Wheel(
        _PYPI + "7d/ec/ce1629f1e478bb5ccd208986b5f9e0316a78538dd6ab1d0484f012f8e2a1/"
        "nvidia_cufft_cu12-11.3.3.83-py3-none-win_amd64.whl",
        models.Pin(192_216_559, "7a64a98ef2a7c47f905aaf8931b69a3a43f27c55530c698bb2ed7c75c0b42cb7"),
    ),
    Wheel(
        _PYPI + "45/51/52a3d84baa2136cc8df15500ad731d74d3a1114d4c123e043cb608d4a32b/"
        "nvidia_cuda_nvrtc_cu12-12.8.93-py3-none-win_amd64.whl",
        models.Pin(73_586_838, "7a4b6b2904850fe78e0bd179c4b655c404d4bb799ef03ddc60804247099ae909"),
    ),
    Wheel(
        _PYPI + "a7/a5/48f07449fc9c6cc146dcafe6149fa5d69630137d2ec5b7d9e09f255fadd7/"
        "nvidia_cudnn_cu12-9.19.0.56-py3-none-win_amd64.whl",
        models.Pin(644_003_014, "cec70596b9ce878fab83810c3f5a2e606d35f510e5fee579759e4cbc68a23750"),
    ),
)
WHEELS = (ONNXRUNTIME_GPU, *NVIDIA)
# the TensorRT provider is never used and needs NVIDIA's TensorRT, which is not part of the pack
LEFT_OUT = "onnxruntime/capi/onnxruntime_providers_tensorrt.dll"


def total_size() -> int:
    return sum(w.pin.size for w in WHEELS)


def pins() -> str:
    """The pins the pack is of: a marker of other versions never counts."""
    return hashlib.sha256("\n".join(f"{w.name} {w.pin.sha256}" for w in WHEELS).encode()).hexdigest()


def folder(root: Path | None = None) -> Path:
    from parakeet_stt.env import ROOT

    return (root or ROOT) / FOLDER


def site(root: Path | None = None) -> Path:
    return folder(root) / "site"


def present(root: Path | None = None) -> bool:
    """The whole pack is unpacked, of these pins, and every file of it is there at its size (no network)."""
    where = site(root)
    try:
        marker = json.loads((where / MARKER).read_text(encoding="utf-8"))
        files: dict[str, int] = marker["files"]
        return marker["pins"] == pins() and bool(files) and all(_size(where / n) == s for n, s in files.items())
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False


def forget(root: Path | None = None) -> None:
    """A file of the pack turned out damaged: the pack no longer counts (the window offers it again)."""
    (site(root) / MARKER).unlink(missing_ok=True)


def resumable(root: Path | None = None) -> bool:
    """A download the user started is not finished yet (it goes on by itself at the next start)."""
    downloads = folder(root) / "downloads"
    return downloads.is_dir() and any(downloads.iterdir()) and not present(root)


def activate(root: Path | None = None) -> bool:
    """Put the pack on `sys.path` just before the site-packages that hold the processor onnxruntime (never before the
    standard library); False when there is no whole pack."""
    if not present(root):
        return False
    where = str(site(root))
    if where in sys.path:
        return True
    at = next((i for i, p in enumerate(sys.path) if Path(p).name.lower() == "site-packages"), len(sys.path))
    sys.path.insert(at, where)
    return True


def kept(member: str) -> bool:
    """Whether a wheel member belongs in the pack: onnxruntime (without TensorRT) and its metadata; of NVIDIA's
    wheels the DLLs the app loads and the metadata with the licence texts. Odd names are never kept."""
    parts = member.split("/")
    if member.endswith("/") or any(p in ("", ".", "..") or ":" in p or "\\" in p for p in parts):
        return False
    if parts[0].endswith(".dist-info"):
        return parts[-1] != "RECORD"
    if parts[0] == "onnxruntime":
        return member != LEFT_OUT
    return member in {f"nvidia/{lib}/bin/{dll}" for lib, dll in NVIDIA_DLLS}


def fetch(
    on_progress: models.ProgressFn | None = None,
    cancel: CancelToken | None = None,
    *,
    root: Path | None = None,
    opener: models.Opener | None = None,
    free_space: Callable[[Path], int] | None = None,
) -> None:
    """Download what is missing, then unpack and mark the pack. Raises `models.ModelFetchError` (a sentence for the
    user, with its code) or `Cancelled` (the parts stay; the next call goes on from them). While it unpacks, the
    progress stays at the whole size (the window says it is unpacking)."""
    cancel = cancel or CancelToken()
    report = on_progress or (lambda _done, _total: None)
    total = total_size()
    if present(root):
        report(total, total)
        return
    base = folder(root)
    if _is_link(base):  # its access list would be set on, and its files cleared in, another folder
        raise models.ModelFetchError(f"The folder for {WHAT} is a link to another place; delete it and press Try "
                                     "again.", "other")  # fmt: skip
    downloads = base / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    _lock(base)
    shutil.rmtree(base / "site", ignore_errors=True)  # a half-unpacked try: its space counts as free again
    have = {w.name: _size(downloads / w.name) or min(_size(downloads / (w.name + models.PART)) or 0, w.pin.size)
            for w in WHEELS}  # fmt: skip
    done = sum(have.values())
    report(done, total)
    need = total - done + UNPACKED_BYTES + models.SPACE_MARGIN
    free = (free_space or _free)(base)
    if free < need:
        raise models.ModelFetchError(
            f"There is not enough free disk space for {WHAT}: it needs {need / 1e9:.1f} GB, the drive that holds the "
            f"app has {free / 1e9:.1f} GB free. Free some space and press Try again.",
            "no_space", need, free,
        )  # fmt: skip
    get = opener or models._open
    for w in WHEELS:
        if _size(downloads / w.name) == w.pin.size:
            continue
        start = done - have[w.name]

        def seen(n: int, start: int = start) -> None:
            report(start + n, total)

        models.download(w.url, w.pin, downloads / w.name, get, seen, cancel, WHAT)
        done = start + w.pin.size
        report(done, total)
    _unpack(base, downloads, cancel)


def _unpack(base: Path, downloads: Path, cancel: CancelToken) -> None:
    """The marker goes first, the old files next, then every wheel's kept members, and the marker last (no folder is
    renamed: a virus scanner holding new program files makes that fail on Windows). The wheels go at the end."""
    where = base / "site"
    forget(base.parent)
    shutil.rmtree(where, ignore_errors=True)
    files: dict[str, int] = {}
    try:
        for w in WHEELS:
            # a wheel left from an earlier run was only measured by size: its code runs, so its fingerprint is checked
            if _sha256(downloads / w.name, cancel) != w.pin.sha256:
                (downloads / w.name).unlink(missing_ok=True)
                raise zipfile.BadZipFile(w.name)
            with zipfile.ZipFile(downloads / w.name) as z:
                for info in z.infolist():
                    if not kept(info.filename):
                        continue
                    target = where / info.filename
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with z.open(info) as src, target.open("wb") as out:
                        while chunk := src.read(models.CHUNK):
                            cancel.raise_if_cancelled()  # a stop of the app does not wait for a whole library
                            out.write(chunk)
                        out.flush()
                        os.fsync(out.fileno())  # on the disk before the marker says so (a power cut, a crash)
                    files[info.filename] = info.file_size
        marker = {"pins": pins(), "files": files}
        (where / MARKER).write_text(json.dumps(marker, indent=0), encoding="utf-8")
    except OSError as exc:
        raise models.ModelFetchError(
            f"{WHAT.capitalize()} could not be unpacked ({exc.strerror or exc.__class__.__name__}). Press Try again.",
            "disk_full" if _disk_full(exc) else "other",
        ) from None
    except zipfile.BadZipFile:
        shutil.rmtree(downloads, ignore_errors=True)
        raise models.ModelFetchError(f"A downloaded part of {WHAT} is damaged. Press Try again.", "damaged") from None
    shutil.rmtree(downloads, ignore_errors=True)


def _lock(base: Path) -> None:
    """The app folder's access list on `card/` (it is inside the application folder: the app's by where it lies)."""
    from parakeet_stt.server import access

    # (it fails only where the rest of the application folder has the same list: the download need not stop)
    with contextlib.suppress(OSError):  # AccessError is an OSError
        access.lock_app_folder(base, known=True)


def _disk_full(exc: OSError) -> bool:
    from parakeet_stt import project

    return project.disk_full(exc)


def _is_link(path: Path) -> bool:
    """A symbolic link or a junction (Python 3.11 has no is_junction)."""
    try:
        attrs = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        return False
    return path.is_symlink() or bool(attrs & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _sha256(path: Path, cancel: CancelToken) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(models.CHUNK):
            cancel.raise_if_cancelled()
            h.update(chunk)
    return h.hexdigest()


def _size(path: Path) -> int | None:
    try:
        return path.stat().st_size
    except OSError:
        return None


def _free(path: Path) -> int:
    return shutil.disk_usage(path).free
