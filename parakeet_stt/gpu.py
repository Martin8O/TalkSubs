"""The only place that imports onnxruntime (and onnx-asr, which imports it).

The CUDA 12 / cuDNN 9 libraries onnxruntime-gpu 1.23 needs must be loaded before a session opens on the card
(docs/adr.md 0006): in the development checkout `import torch` (first, before onnxruntime) loads them; the download
package has no torch, and once its graphics-card support is fetched (cardpack.py) NVIDIA's own runtime libraries are
in `card/site/nvidia/*/bin`, which `cuda_libraries()` loads by full path just before the model is opened on the card -
never on the processor path. Importing onnxruntime anywhere else risks the wrong order. A torch that is installed but
broken still fails here.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from functools import cache
from pathlib import Path
from types import ModuleType
from typing import IO, cast

WINDOWS = sys.platform == "win32"
if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

# NVIDIA's runtime libraries in the card package: every one the CUDA provider imports, and every one cuDNN and NVRTC
# load later by name (cuDNN's sub-libraries, NVRTC and its builtins), which then finds the copy already loaded. Order
# does not matter (their own imports stay inside one folder); the graphics-card support keeps exactly these
# (cardpack.kept).
NVIDIA_DLLS = (
    ("cuda_runtime", "cudart64_12.dll"),
    ("cublas", "cublasLt64_12.dll"),
    ("cublas", "cublas64_12.dll"),
    ("cufft", "cufft64_11.dll"),
    ("cuda_nvrtc", "nvrtc-builtins64_128.dll"),
    ("cuda_nvrtc", "nvrtc64_120_0.dll"),
    ("cudnn", "cudnn_graph64_9.dll"),
    ("cudnn", "cudnn_ops64_9.dll"),
    ("cudnn", "cudnn_cnn64_9.dll"),
    ("cudnn", "cudnn_adv64_9.dll"),
    ("cudnn", "cudnn_heuristic64_9.dll"),
    ("cudnn", "cudnn_engines_precompiled64_9.dll"),
    ("cudnn", "cudnn_engines_runtime_compiled64_9.dll"),
    ("cudnn", "cudnn64_9.dll"),
)
_loaded: list[object] = []  # the NVIDIA libraries loaded by cuda_libraries(), kept alive for the process
_dirs: list[object] = []  # their folders, added to the DLL search
CARD_FILES_DAMAGED = (
    "A file of the app's graphics-card support is missing or damaged ({name}). The app offers to download the "
    "support again; until then it runs on the processor."
)


class CardFilesError(Exception):
    """A file of the graphics-card support could not be loaded; the message is for the user."""


def nvidia_root() -> Path | None:
    """`nvidia/` beside the onnxruntime in use (the graphics-card support's `card/site/nvidia`), found without importing
    anything; None where there is none (the development checkout, the processor package)."""
    spec = importlib.util.find_spec("onnxruntime")
    if spec is None or spec.origin is None:
        return None
    root = Path(spec.origin).parent.parent / "nvidia"
    return root if root.is_dir() else None


@cache
def cuda_libraries() -> bool:
    """Before a model opens on the card: in the card package, load NVIDIA's libraries by full path (and make their
    folders findable for the loads by name), then onnxruntime's CUDA provider itself, so a damaged one is caught
    here too. True when it did; False where torch or nothing supplies them. A file that does not load is a
    CardFilesError: the support is forgotten (offered again) and the caller runs on the processor, saying why."""
    if not WINDOWS or _has_torch():  # Linux: torch or the system's CUDA libraries (LD_LIBRARY_PATH) supply them
        return False
    root = nvidia_root()
    if root is None:
        return False
    import ctypes

    k32 = ctypes.windll.kernel32
    k32.SetErrorMode(k32.GetErrorMode() | 0x0001)  # SEM_FAILCRITICALERRORS: a missing file is an error, not a dialog
    folders = sorted({root / lib / "bin" for lib, _ in NVIDIA_DLLS})
    for folder in folders:
        if folder.is_dir():
            _dirs.append(os.add_dll_directory(str(folder)))
    # a plain load by name (cuDNN, NVRTC) searches PATH, not the folders added above
    os.environ["PATH"] = os.pathsep.join([*map(str, folders), os.environ.get("PATH", "")])
    # onnxruntime's own CUDA provider is loaded by onnxruntime (it needs onnxruntime set up first): here only whether
    # it still looks like a program, so a file emptied or zeroed since is caught before the model opens
    capi = root.parent / "onnxruntime" / "capi"
    providers = [capi / "onnxruntime_providers_shared.dll", capi / "onnxruntime_providers_cuda.dll"]
    files = [root / lib / "bin" / dll for lib, dll in NVIDIA_DLLS] + providers
    for path in files:
        lib, dll = path.parent.parent.name, path.name
        try:
            if path in providers:
                if not _looks_like_a_program(path):
                    raise OSError(f"{dll} is not a program")
            else:
                _loaded.append(ctypes.WinDLL(str(path)))
        except OSError as exc:
            print(f"could not load {lib}/{dll}: {exc!r}", file=sys.stderr, flush=True)  # the log keeps the detail
            from parakeet_stt import cardpack

            cardpack.forget()  # the pack no longer counts: the window offers it again, the processor runs meanwhile
            raise CardFilesError(CARD_FILES_DAMAGED.format(name=dll)) from None
    return True


def _looks_like_a_program(path: Path) -> bool:
    """A Windows program file starts with "MZ" and points at a "PE" header (a few bytes read)."""
    with path.open("rb") as fh:
        head = fh.read(4096)
    if head[:2] != b"MZ" or len(head) < 64:
        return False
    at = int.from_bytes(head[60:64], "little")
    return at + 4 <= len(head) and head[at : at + 4] == b"PE\0\0"


def _drop_card_onnxruntime(card_site: Path) -> None:
    """Take the graphics-card support off the module path and forget its half-imported onnxruntime."""
    import importlib

    sys.path[:] = [p for p in sys.path if Path(p) != card_site]
    for name in [n for n in sys.modules if n == "onnxruntime" or n.startswith("onnxruntime.")]:
        del sys.modules[name]
    importlib.invalidate_caches()


@cache
def onnxruntime() -> ModuleType:
    if _has_torch():
        import torch  # noqa: F401 - loads the CUDA 12 / cuDNN 9 DLLs onnxruntime needs

    # isort: split
    try:
        import onnxruntime as ort
    except (ImportError, OSError) as exc:
        from parakeet_stt import cardpack

        if not cardpack.present() or nvidia_root() is None:
            raise
        print(f"onnxruntime of the graphics-card support did not load: {exc!r}", file=sys.stderr, flush=True)
        cardpack.forget()  # the window offers the support again
        _drop_card_onnxruntime(cardpack.site())  # so the caller's next import, in this process, is the processor one
        raise CardFilesError(CARD_FILES_DAMAGED.format(name="onnxruntime")) from None

    ort.set_default_logger_severity(3)  # torch or the card path loads the CUDA libraries, not ort
    return cast(ModuleType, ort)


@cache
def onnx_asr() -> ModuleType:
    """onnx-asr imports onnxruntime itself, so it too may only be imported after `onnxruntime()`."""
    onnxruntime()
    import onnx_asr as module

    return module


def _has_torch() -> bool:
    return importlib.util.find_spec("torch") is not None


def versions() -> dict[str, str]:
    """Libraries and hardware that change the numbers (recorded in every run's configuration)."""
    ort = onnxruntime()
    out = {"onnxruntime": str(ort.__version__)}
    if not _has_torch():
        if _loaded:  # the card package, on the card (cuda_libraries() loaded them): NVIDIA's wheel versions
            from importlib.metadata import PackageNotFoundError, version

            for name in ("nvidia-cudnn-cu12", "nvidia-cublas-cu12", "nvidia-cuda-runtime-cu12"):
                try:
                    out[name] = version(name)
                except PackageNotFoundError:
                    out[name] = "unknown"
            out["gpu"] = _card_name()
            out["driver"] = _driver_version()
        return out
    import torch

    out = {"torch": str(torch.__version__), **out}
    if torch.cuda.is_available():
        out["gpu"] = torch.cuda.get_device_name(0)
        out["cuda"] = str(torch.version.cuda)
        out["cudnn"] = str(torch.backends.cudnn.version())  # type: ignore[no-untyped-call]
        out["driver"] = _driver_version()
    return out


def _card_name() -> str:
    from parakeet_stt import machine  # asks the driver directly, imports nothing heavy

    try:
        return machine.detect().card or "unknown"
    except machine.DriverError:
        return "unknown"


def _nvml() -> tuple[str, int | None] | None:
    """(driver version, free card memory in MB) from NVIDIA's management library, which comes with every driver;
    None when it is not there or does not answer."""
    import ctypes

    class Memory(ctypes.Structure):
        _fields_ = (("total", ctypes.c_ulonglong), ("free", ctypes.c_ulonglong), ("used", ctypes.c_ulonglong))

    try:
        nvml = ctypes.WinDLL("nvml.dll") if WINDOWS else ctypes.CDLL("libnvidia-ml.so.1")
    except OSError:
        return None
    if nvml.nvmlInit_v2() != 0:
        return None
    try:
        buf = ctypes.create_string_buffer(96)
        if nvml.nvmlSystemGetDriverVersion(buf, 96) != 0:
            return None
        handle, mem = ctypes.c_void_p(), Memory()
        ok = nvml.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(handle)) == 0
        free = mem.free // 2**20 if ok and nvml.nvmlDeviceGetMemoryInfo(handle, ctypes.byref(mem)) == 0 else None
        return buf.value.decode("ascii", "replace"), free
    finally:
        nvml.nvmlShutdown()


def _driver_version() -> str:
    import subprocess

    if (asked := _nvml()) is not None:
        return asked[0]
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=20, check=False,
        )  # fmt: skip
    except OSError:
        return "unknown"
    return out.stdout.strip().splitlines()[0] if out.returncode == 0 and out.stdout.strip() else "unknown"


class GpuBusy(Exception):
    """Another job on this machine holds the GPU."""


class GpuLock:
    """One GPU job at a time across processes (CLI, tests, later the server). The OS frees it if we crash."""

    def __init__(self) -> None:
        self._fh: IO[bytes] | None = None

    def acquire(self) -> None:
        if self._fh is not None:
            return
        path = Path(os.environ.get("PARAKEET_STT_ROOT", Path(__file__).resolve().parent.parent)) / ".cache" / "gpu.lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        fh = path.open("a+b")
        fh.seek(0)  # the lock covers the byte at the current position
        try:
            if sys.platform == "win32":
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            raise GpuBusy("Another transcription is running on this PC; wait until it finishes.") from None
        self._fh = fh

    def release(self) -> None:
        if self._fh is None:
            return
        try:
            if sys.platform == "win32":
                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        finally:
            self._fh.close()
            self._fh = None


def free_memory_mb() -> int | None:
    """Free memory on the graphics card now, or None when it cannot be told (asked only when something failed).
    Asked of the driver, not of CUDA: with the memory full, CUDA itself may not even start."""
    import subprocess

    if (asked := _nvml()) is not None and asked[1] is not None:
        return asked[1]
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=20, check=False,
        )  # fmt: skip
        return int(out.stdout.strip().splitlines()[0]) if out.returncode == 0 else None
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
        return None
