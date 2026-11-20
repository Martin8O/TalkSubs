"""The speed test: how fast this PC finds speech and runs the model, in under a minute (docs/adr.md 0036).

Made on generated sound (no recording travels with the app): its length, not its content, decides the cost of
finding speech and of the model's encoder, which is most of the work. What real speech adds on top is the fitted
`speed.SPEECH_FACTOR`, and real transcriptions replace the test's numbers as soon as they exist.
"""

from __future__ import annotations

import ctypes
import gc
import statistics
import time
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from parakeet_stt.cancel import CancelToken
from parakeet_stt.engines.base import Engine
from parakeet_stt.media import SAMPLE_RATE
from parakeet_stt.speed import Benchmark
from parakeet_stt.vad import Vad, Window

VAD_SECONDS = 60
WINDOW_S = 20  # a typical speech window (they are cut at 25 s at most)
WINDOWS = 4  # timed one by one after a warm-up; the median is kept (one slow window does not count)


@dataclass(frozen=True)
class Result:
    benchmark: Benchmark
    cpu_threads: int | None  # the fastest thread count tried (processor only)
    tried: dict[int, float]  # thread count -> model rate (processor only)


def process_age_s() -> float:
    """Seconds since this process was created (so the interpreter's own start and every import are counted)."""

    class FileTime(ctypes.Structure):
        _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]

    kernel = ctypes.WinDLL("kernel32")
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    created, exited, k, u, now = FileTime(), FileTime(), FileTime(), FileTime(), FileTime()
    kernel.GetProcessTimes(ctypes.c_void_p(kernel.GetCurrentProcess()), ctypes.byref(created), ctypes.byref(exited),
                           ctypes.byref(k), ctypes.byref(u))  # fmt: skip
    kernel.GetSystemTimeAsFileTime(ctypes.byref(now))

    def ticks(t: FileTime) -> int:
        return int((t.high << 32) | t.low)

    return max(0.0, (ticks(now) - ticks(created)) / 1e7)


def test_sound(seconds: float, seed: int = 0) -> npt.NDArray[np.float32]:
    """Pink-ish noise at speech loudness: the same every time."""
    rng = np.random.default_rng(seed)
    white = rng.standard_normal(int(seconds * SAMPLE_RATE)).astype(np.float32)
    spectrum = np.fft.rfft(white)
    spectrum /= np.sqrt(np.arange(1, len(spectrum) + 1, dtype=np.float32))
    pink = np.fft.irfft(spectrum, n=len(white)).astype(np.float32)
    out: npt.NDArray[np.float32] = (0.1 * pink / (np.abs(pink).max() + 1e-9)).astype(np.float32)
    return out


def _model_rate(eng: Engine, cancel: CancelToken) -> float:
    n = WINDOW_S * SAMPLE_RATE
    audio = test_sound(WINDOW_S * (WINDOWS + 1), seed=1)
    windows = [Window(i * n, (i + 1) * n, i * n, (i + 1) * n) for i in range(WINDOWS + 1)]
    eng.transcribe(audio, windows[:1], lambda _s: None, cancel)  # warm-up: the first run sets up its buffers
    times = []
    for w in windows[1:]:
        t0 = time.perf_counter()
        eng.transcribe(audio, [w], lambda _s: None, cancel)
        times.append(time.perf_counter() - t0)
    return statistics.median(times) / WINDOW_S


def _close(eng: Engine) -> None:
    """Close and really free it now: a closed model kept alive by a reference cycle keeps its processor threads,
    and the next model measured beside them ran at half speed."""
    eng.close()
    gc.collect()


def run(
    make_engine: Callable[[int | None], Engine], candidates: list[int] | None, cancel: CancelToken, started_s: float
) -> Result:
    """`make_engine(threads)` gives an unloaded engine; `candidates` are the processor's thread counts to compare
    (None on the graphics card). `started_s`: seconds from process creation until now (the interpreter's start and
    the imports), counted into the fixed cost together with one load of the model from disk."""
    tries: dict[int, float] = {}
    fixed_s = 0.0
    best: tuple[float, int | None] = (float("inf"), None)
    for i, threads in enumerate(candidates or [None]):
        eng = make_engine(threads)
        eng.load()  # a missing model is downloaded here, before anything is timed
        if i == 0:
            _close(eng)
            t0 = time.perf_counter()
            eng = make_engine(threads)
            eng.load()
            fixed_s = started_s + time.perf_counter() - t0
        try:
            cancel.raise_if_cancelled()
            rate = _model_rate(eng, cancel)
        finally:
            _close(eng)
        if threads is not None:
            tries[threads] = rate
        if rate < best[0]:
            best = (rate, threads)
        cancel.raise_if_cancelled()
    t0 = time.perf_counter()
    vad = Vad()
    list(vad.windows(test_sound(VAD_SECONDS)))
    vad_rate = (time.perf_counter() - t0) / VAD_SECONDS
    return Result(Benchmark(fixed_s=fixed_s, vad_rate=vad_rate, model_rate=best[0]), best[1], tries)
