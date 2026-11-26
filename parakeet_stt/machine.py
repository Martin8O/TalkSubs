"""What this PC can run the model on: the graphics card or the processor, and why (docs/adr.md 0035).

Asked of the NVIDIA driver and of Windows directly (ctypes), so it imports neither torch nor onnxruntime: the
import order of docs/adr.md 0006 stays with `gpu.py`. Run it in a short-lived process (the worker, the doctor,
`python -m parakeet_stt machine`): on some laptops starting the driver keeps the graphics card awake.
"""

from __future__ import annotations

import contextlib
import ctypes
import importlib.util
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

Device = Literal["cuda", "cpu"]
# Why that device: "card" = a usable NVIDIA card; the rest say why the processor runs the model.
# "card_pack_missing": a usable card, but the download package's graphics-card support is not fetched yet (the window
# offers it, cardpack.py); "driver_too_old": the card's driver predates CUDA 12 (onnxruntime-gpu 1.23 needs it).
Reason = Literal[
    "card",
    "no_driver",
    "no_card",
    "card_too_old",
    "card_too_small",
    "no_card_support",
    "card_pack_missing",
    "driver_too_old",
]

CUDA_ERROR_NO_DEVICE = 100  # also what the driver says when the card is hidden (CUDA_VISIBLE_DEVICES=-1)
# onnxruntime-gpu 1.23 (CUDA 12) runs on Maxwell (compute capability 5.2) and newer.
MIN_COMPUTE = (5, 2)
# The model needs ~3.8 GB at batch 1 (docs/adr.md 0006); a smaller card fails or crawls, the processor does not.
MIN_CARD_MB = 5632  # 5.5 GiB: a "6 GB" card reports a little under 6144
MIN_DRIVER_CUDA = 12000  # cuDriverGetVersion of the oldest driver that runs CUDA 12 (R525 and newer)
_CC_MAJOR, _CC_MINOR = 75, 76  # CU_DEVICE_ATTRIBUTE_COMPUTE_CAPABILITY_MAJOR / _MINOR
WINDOWS = sys.platform == "win32"
_RELATION_PROCESSOR_CORE = 0
_ERROR_INSUFFICIENT_BUFFER = 122


@dataclass(frozen=True)
class Machine:
    device: Device
    reason: Reason
    card: str | None  # the NVIDIA card's name, when one was found
    card_mb: int | None
    performance_cores: int  # cores of the fastest kind (all cores on a CPU without efficiency cores)
    physical_cores: int

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


class DriverError(Exception):
    """The NVIDIA driver is there but does not work: the graphics-card path must show the real error."""


def detect(*, probe: bool = False) -> Machine:  # noqa: PLR0911 - one answer per reason
    """Where the model runs. Without the graphics-card provider (the download package before its graphics-card
    support is fetched) only the `probe` (`python -m parakeet_stt machine`, a short-lived process) asks the driver
    whether a card could use it: a job's worker on the processor must not wake a laptop's card for nothing."""
    perf, phys = cpu_cores()
    provider = _cuda_provider_installed()
    if not provider and not probe:
        return Machine("cpu", "no_card_support", None, None, perf, phys)
    try:
        card = _card()
    except DriverError:
        if not provider:  # the processor package: nothing to offer while the driver does not answer
            return Machine("cpu", "no_card_support", None, None, perf, phys)
        return Machine("cuda", "card", None, None, perf, phys)  # loading the model says what is wrong
    if card is None:
        reason: Reason = "no_driver" if not _driver_present() else "no_card"
        return Machine("cpu", reason, None, None, perf, phys)
    name, cc, mb = card
    if cc < MIN_COMPUTE:
        return Machine("cpu", "card_too_old", name, mb, perf, phys)
    if mb < MIN_CARD_MB:
        return Machine("cpu", "card_too_small", name, mb, perf, phys)
    if 0 < _driver_cuda() < MIN_DRIVER_CUDA:  # (0: it would not say; loading the model then shows what is wrong)
        return Machine("cpu", "driver_too_old", name, mb, perf, phys)
    if not provider:
        return Machine("cpu", "card_pack_missing", name, mb, perf, phys)
    return Machine("cuda", "card", name, mb, perf, phys)


def cpu_threads(m: Machine) -> int:
    """Threads for the model on the processor when nothing better was measured: the performance cores. Efficiency
    cores make the parallel steps wait for the slowest core (20 threads were 2.6x slower than 8 here); with very
    few performance cores all cores are used."""
    return m.performance_cores if m.performance_cores >= 4 else m.physical_cores


def thread_candidates(m: Machine) -> list[int]:
    """The thread counts the speed test compares on this processor."""
    return sorted({m.performance_cores, m.physical_cores} - {0}) or [max(1, (os.cpu_count() or 2) // 2)]


def _cuda_provider_installed() -> bool:
    """Whether the installed onnxruntime has the graphics-card provider - read from disk, never imported (a
    dotted find_spec would import the package, before torch)."""
    spec = importlib.util.find_spec("onnxruntime")
    if spec is None or spec.origin is None:
        return False
    provider = "onnxruntime_providers_cuda.dll" if WINDOWS else "libonnxruntime_providers_cuda.so"
    return (Path(spec.origin).parent / "capi" / provider).is_file()


def _cuda_driver() -> ctypes.CDLL:
    """The driver's CUDA library: nvcuda.dll on Windows, libcuda.so.1 on Linux (OSError when there is none)."""
    return ctypes.WinDLL("nvcuda.dll") if WINDOWS else ctypes.CDLL("libcuda.so.1")


def _driver_cuda() -> int:
    """The newest CUDA version the installed driver runs (12080 = 12.8); 0 when it cannot say."""
    try:
        cu = _cuda_driver()
    except OSError:
        return 0
    version = ctypes.c_int()
    return version.value if cu.cuDriverGetVersion(ctypes.byref(version)) == 0 else 0


def _driver_present() -> bool:
    try:
        _cuda_driver()
    except OSError:
        return False
    return True


def _card() -> tuple[str, tuple[int, int], int] | None:
    """(name, compute capability, memory MB) of the first NVIDIA card, None when there is none (or it is
    hidden); DriverError when the driver answers with anything else."""
    try:
        cu = _cuda_driver()
    except OSError:
        return None
    rc = cu.cuInit(0)
    if rc == CUDA_ERROR_NO_DEVICE:
        return None
    if rc:
        raise DriverError(f"cuInit {rc}")
    count = ctypes.c_int()
    if cu.cuDeviceGetCount(ctypes.byref(count)):
        raise DriverError("cuDeviceGetCount")
    if count.value == 0:
        return None
    dev = ctypes.c_int()
    if cu.cuDeviceGet(ctypes.byref(dev), 0):
        raise DriverError("cuDeviceGet")
    name = ctypes.create_string_buffer(256)
    major, minor, total = ctypes.c_int(), ctypes.c_int(), ctypes.c_size_t()
    if (
        cu.cuDeviceGetName(name, 256, dev)
        or cu.cuDeviceGetAttribute(ctypes.byref(major), _CC_MAJOR, dev)
        or cu.cuDeviceGetAttribute(ctypes.byref(minor), _CC_MINOR, dev)
        or cu.cuDeviceTotalMem_v2(ctypes.byref(total), dev)
    ):
        raise DriverError("device query")
    return name.value.decode("utf-8", "replace"), (major.value, minor.value), int(total.value // 2**20)


def cpu_cores() -> tuple[int, int]:
    """(performance cores, physical cores) from Windows' processor topology; (0, 0) if it cannot be read. Elsewhere
    the processors this process may use (a container's CPU limit counts), all taken as performance cores."""
    if not WINDOWS:
        n = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else (os.cpu_count() or 0)
        return n, n
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    size = ctypes.c_uint32(0)
    kernel.GetLogicalProcessorInformationEx(_RELATION_PROCESSOR_CORE, None, ctypes.byref(size))
    if ctypes.get_last_error() != _ERROR_INSUFFICIENT_BUFFER or size.value == 0:
        return 0, 0
    buf = ctypes.create_string_buffer(size.value)
    if not kernel.GetLogicalProcessorInformationEx(_RELATION_PROCESSOR_CORE, buf, ctypes.byref(size)):
        return 0, 0
    classes: list[int] = []
    off = 0
    while off < size.value:  # SYSTEM_LOGICAL_PROCESSOR_INFORMATION_EX: Relationship, Size, then PROCESSOR_RELATIONSHIP
        rel = int.from_bytes(buf.raw[off : off + 4], "little")
        step = int.from_bytes(buf.raw[off + 4 : off + 8], "little")
        if step == 0:
            break
        if rel == _RELATION_PROCESSOR_CORE:
            classes.append(buf.raw[off + 9])  # Flags at +8, EfficiencyClass at +9 (higher = faster)
        off += step
    if not classes:
        return 0, 0
    return classes.count(max(classes)), len(classes)


def run_in_background_mode() -> None:
    """This process (and the ffmpeg it starts) works at below-normal priority so the PC stays usable, and Windows
    may not slow it down as background work (it runs without a window and could be moved to efficiency cores)."""
    if sys.platform != "win32":
        with contextlib.suppress(OSError):
            os.nice(10)
        return
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    proc = ctypes.c_void_p(kernel.GetCurrentProcess())
    if not kernel.SetPriorityClass(proc, 0x00004000):  # BELOW_NORMAL_PRIORITY_CLASS
        print(f"could not lower this process's priority (error {ctypes.get_last_error()})", file=sys.stderr)

    class _PowerThrottling(ctypes.Structure):
        _fields_ = [("Version", ctypes.c_uint32), ("ControlMask", ctypes.c_uint32), ("StateMask", ctypes.c_uint32)]

    state = _PowerThrottling(1, 0x1, 0x0)  # PROCESS_POWER_THROTTLING_EXECUTION_SPEED: controlled, and off
    if not kernel.SetProcessInformation(proc, 4, ctypes.byref(state), ctypes.sizeof(state)):  # ProcessPowerThrottling
        print(f"could not keep Windows from slowing this process (error {ctypes.get_last_error()})", file=sys.stderr)
