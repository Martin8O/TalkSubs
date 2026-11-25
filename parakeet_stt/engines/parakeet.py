"""Parakeet TDT 0.6B v2 through onnx-asr (the default engine, docs/adr.md 0005): on the CUDA execution provider when
this PC has a usable NVIDIA card, otherwise on the processor (docs/adr.md 0035). Decided once, when the model loads."""

from __future__ import annotations

import sys
from collections.abc import Iterable, Iterator
from itertools import islice
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from parakeet_stt import gpu, machine, models
from parakeet_stt.cancel import CancelToken
from parakeet_stt.engines.base import (
    EngineBusy,
    EngineError,
    EngineInfo,
    EngineOutOfMemory,
    ProgressFn,
    TimedSegment,
    TimedWord,
)
from parakeet_stt.engines.tokens import words_from_tokens
from parakeet_stt.vad import Window

DEFAULT_MODEL = "v2"  # the keys of `models.SPEECH_MODELS`
CUDA = "CUDAExecutionProvider"
CPU = "CPUExecutionProvider"
# Lean settings: 3.8 GB at batch 1 instead of ~10 GB with the library defaults (docs/adr.md 0006).
CUDA_OPTIONS = {
    "cudnn_conv_algo_search": "HEURISTIC",
    "cudnn_conv_use_max_workspace": "0",
    "arena_extend_strategy": "kSameAsRequested",
}
# Word end = its last token's time + this offset. The model's last-token times run late; -60 ms puts word ends
# on the acoustic speech edges (median error ~0 ms on three lectures, tuned on two and checked on the third).
WORD_END_OFFSET_MS = -60
BATCH_SIZES = (1, 2)
# What onnxruntime says when CUDA memory runs out: its arena cannot grow (seen at load and while running), a plain
# CUDA allocation fails, or cuBLAS / cuDNN cannot get their workspace.
_OUT_OF_MEMORY = ("BFCArena::AllocateRawInternal", "Failed to allocate memory", "CUDA failure 2:", "ALLOC_FAILED")
# A model part that came up on the CPU although CUDA was asked: a shortage of memory when this little is free.
LOAD_NEEDS_MB = 4096
OUT_OF_MEMORY_MESSAGE = (
    "The graphics card has no memory left for the model; another program is probably using it. "
    "Close it, or wait: the queue tries again by itself."
)
# On the processor the model uses the computer's own memory (about 3.3 GB); running out of it is no pause-and-retry
# case: the same programs are still open a minute later.
RAM_MESSAGE = "The computer ran out of memory for the model. Close other programs and add the file again."


class ParakeetEngine:
    name = "parakeet"

    def __init__(
        self,
        batch_size: int = 1,
        word_end_offset_ms: int = WORD_END_OFFSET_MS,
        cpu_threads: int | None = None,
        model: str = DEFAULT_MODEL,
    ) -> None:
        if model not in models.SPEECH_MODELS:
            raise EngineError(f"Unknown speech model {model!r}; use one of {', '.join(models.SPEECH_MODELS)}.")
        self.model = model
        if batch_size not in BATCH_SIZES:
            raise EngineError(f"Batch size must be one of {BATCH_SIZES} (VRAM budget), not {batch_size}.")
        if cpu_threads is not None and cpu_threads < 1:
            raise EngineError(f"The processor needs at least one thread, not {cpu_threads}.")
        self.batch_size = batch_size
        self.word_end_offset_ms = word_end_offset_ms
        self.cpu_threads = cpu_threads  # None: the performance cores (`machine.cpu_threads`)
        self._asr: Any = None
        self._provider = ""
        self._threads = 0
        self._lock = gpu.GpuLock()

    def load(self) -> None:
        if self._asr is not None:
            return
        try:
            self._lock.acquire()
        except gpu.GpuBusy as exc:
            raise EngineBusy(str(exc)) from None
        try:
            m = machine.detect()
            if m.device == "cuda":
                try:
                    self._asr, self._provider = _load(self.model), CUDA
                except gpu.CardFilesError as exc:  # forgotten (offered again); this run: the processor (the worker
                    # normally finds it first, so the run is counted as the processor's)
                    print(f"the graphics-card support is damaged, running on the processor: {exc}", file=sys.stderr)
                    self._threads = self.cpu_threads or machine.cpu_threads(m)
                    self._asr, self._provider = _load_cpu(self._threads, self.model), CPU
            else:
                self._threads = self.cpu_threads or machine.cpu_threads(m)
                self._asr, self._provider = _load_cpu(self._threads, self.model), CPU
        except BaseException:
            self._lock.release()
            raise

    def close(self) -> None:
        self._asr = None  # the sessions and their GPU memory go with the last reference
        self._provider = ""
        self._threads = 0
        self._lock.release()

    @property
    def on_cpu(self) -> bool:
        return self._provider == CPU

    def describe(self) -> EngineInfo:
        device: dict[str, str | int] = {"cpu_threads": self._threads} if self.on_cpu else dict(CUDA_OPTIONS)
        return EngineInfo(
            engine=self.name,
            model=models.SPEECH_MODELS[self.model][0].pinned,
            provider=self._provider,
            options={"batch_size": self.batch_size, "word_end_offset_ms": self.word_end_offset_ms, **device},
            versions={"onnx-asr": str(gpu.onnx_asr().__version__), **gpu.versions()},
        )

    def transcribe(
        self,
        audio: npt.NDArray[np.float32],
        windows: Iterable[Window],
        on_progress: ProgressFn,
        cancel: CancelToken,
    ) -> list[TimedSegment]:
        if self._asr is None:
            raise EngineError("Engine not loaded.")
        out: list[TimedSegment] = []
        for batch in _batched(iter(windows), self.batch_size):
            cancel.raise_if_cancelled()
            try:
                results = self._asr.recognize([audio[w.start : w.end] for w in batch], sample_rate=16_000)
            except Exception as exc:  # onnxruntime's own types; only a memory shortage is told apart
                if _out_of_memory(exc):
                    if self.on_cpu:
                        raise EngineError(RAM_MESSAGE) from exc
                    raise EngineOutOfMemory(OUT_OF_MEMORY_MESSAGE) from exc
                raise
            for w, res in zip(batch, results, strict=True):
                out.append(self._segment(w, res))
            on_progress(batch[-1].end_s)
        return out

    def check_device(self) -> None:
        """Raise unless every part of the model still runs on the GPU. With onnxruntime's fallback turned off this
        should never fire; it guards against a library change moving a session to the CPU without a word."""
        if self._asr is not None and not self.on_cpu:
            _check_on_gpu(self._asr)

    def _segment(self, window: Window, res: Any) -> TimedSegment:
        offset = window.start_s
        raw = words_from_tokens(res.tokens or [], res.timestamps or [], res.logprobs)
        end_pad = self.word_end_offset_ms / 1000
        words = [TimedWord(w.text, offset + w.start_s, offset + w.last_token_s + end_pad, w.conf) for w in raw]
        return TimedSegment(window, words)


def _model_folder(model: str) -> Path:
    """The pinned model files, downloaded first if they are not here (the app does that before any job, with
    progress in the window; a command-line run on a new PC does it here)."""
    try:
        repo = models.SPEECH_MODELS[model][0]
        models.fetch(repos=(repo, models.VAD))
        return models.folder(repo)
    except models.ModelFetchError as exc:
        raise EngineError(str(exc)) from None


def _load(model_key: str = DEFAULT_MODEL) -> Any:
    gpu.cuda_libraries()  # the graphics-card support's NVIDIA libraries, before the model opens on the card
    onnx_asr = gpu.onnx_asr()
    path = _model_folder(model_key)
    try:
        model = onnx_asr.load_model(models.SPEECH_MODELS[model_key][1], path, providers=[(CUDA, CUDA_OPTIONS)])
    except Exception as exc:  # the library raises several types; the user needs the message
        if _out_of_memory(exc):
            raise EngineOutOfMemory(OUT_OF_MEMORY_MESSAGE) from exc
        raise EngineError(_load_failed(exc, model_key)) from exc
    for session in _sessions(model):
        session.disable_fallback()  # a CUDA failure while running must surface, not go on quietly on the CPU
    _check_on_gpu(model)
    return model.with_timestamps()


def _load_cpu(threads: int, model_key: str = DEFAULT_MODEL) -> Any:
    """The same model on the processor. onnx-asr then makes the sound features with numpy instead of its own
    onnxruntime session, so `_sessions` does not apply here (and there is no other device to fall back from)."""
    ort = gpu.onnxruntime()
    options = ort.SessionOptions()
    options.intra_op_num_threads = threads
    path = _model_folder(model_key)
    try:
        onnx_name = models.SPEECH_MODELS[model_key][1]
        model = gpu.onnx_asr().load_model(onnx_name, path, providers=[CPU], sess_options=options)
    except Exception as exc:  # the library raises several types; the user needs the message
        if _out_of_memory(exc):
            raise EngineError(RAM_MESSAGE) from exc
        raise EngineError(_load_failed(exc, model_key)) from exc
    return model.with_timestamps()


def _load_failed(exc: BaseException, model_key: str = DEFAULT_MODEL) -> str:
    """A sentence for the window; the library's own text (it can name folders) goes only to the log."""
    print(f"model load failed: {exc!r}", file=sys.stderr, flush=True)  # the worker's stderr is the app's log
    folder = models.SPEECH_MODELS[model_key][0].repo_id.replace("/", "--")
    return (
        f"The speech model could not be loaded ({exc.__class__.__name__}). Its files may be damaged: delete the "
        f"folder models\\hf\\models--{folder} in the app's folder and start the app again to download it anew."
    )


def _sessions(model: Any) -> list[Any]:
    """Every onnxruntime session of the model (pinned onnx-asr 0.12 internals)."""
    asr = model.asr
    return [asr._preprocessor._preprocessor, asr._encoder, asr._decoder_joint]


def _check_on_gpu(model: Any) -> None:
    on_cpu = [s for s in _sessions(model) if not s.get_providers() or s.get_providers()[0] != CUDA]
    if not on_cpu:
        return
    free_mb = gpu.free_memory_mb()
    if free_mb is not None and free_mb < LOAD_NEEDS_MB:  # onnxruntime fell back to the CPU for want of memory
        raise EngineOutOfMemory(OUT_OF_MEMORY_MESSAGE)
    raise EngineError(
        f"The model did not start on the GPU (running on {on_cpu[0].get_providers()}); check the GPU drivers."
    )


def _out_of_memory(exc: BaseException) -> bool:
    text = str(exc)
    return any(mark in text for mark in _OUT_OF_MEMORY)


def _batched(it: Iterator[Window], n: int) -> Iterator[list[Window]]:
    while batch := list(islice(it, n)):
        yield batch
