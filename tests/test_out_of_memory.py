"""Telling a full graphics card apart from other engine trouble (no GPU needed: onnxruntime's texts as measured)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from parakeet_stt import gpu
from parakeet_stt.engines import parakeet
from parakeet_stt.engines.base import EngineError, EngineOutOfMemory

AT_LOAD = (
    "[ONNXRuntimeError] : 6 : RUNTIME_EXCEPTION : Exception during initialization: E:\\_work\1\\s\\onnxruntime\\core"
    "\framework\bfc_arena.cc:359 onnxruntime::BFCArena::AllocateRawInternal Available memory of 4024576 is smaller "
    "than requested bytes of 4194304"
)
WHILE_RUNNING = (
    "[ONNXRuntimeError] : 6 : RUNTIME_EXCEPTION : Non-zero status code returned while running Conv node. "
    "Name:'/pre_encode/conv/conv.0/Conv' Status Message: E:\\_work\1\\s\\onnxruntime\\core\framework\bfc_arena.cc"
    ":359 onnxruntime::BFCArena::AllocateRawInternal Available memory of 43268864 is smaller than requested bytes of "
    "78970880"
)


@pytest.mark.parametrize("text", [AT_LOAD, WHILE_RUNNING, "CUDNN_STATUS_ALLOC_FAILED", "CUDA failure 2: out of memory"])
def test_memory_errors_are_recognised(text: str) -> None:
    assert parakeet._out_of_memory(RuntimeError(text))


@pytest.mark.parametrize(
    "text",
    [
        "Non-zero status code returned while running Conv node. Invalid input shape",
        "CUDA failure 700: an illegal memory access was encountered",
        "bad allocation",
    ],
)
def test_other_errors_are_not_called_a_full_memory(text: str) -> None:
    assert not parakeet._out_of_memory(RuntimeError(text))


def _model(*providers: str) -> SimpleNamespace:
    def session(p: str) -> SimpleNamespace:
        return SimpleNamespace(get_providers=lambda: [p, "CPUExecutionProvider"] if p == parakeet.CUDA else [p])

    pre, enc, dec = (session(p) for p in providers)
    return SimpleNamespace(asr=SimpleNamespace(_preprocessor=SimpleNamespace(_preprocessor=pre), _encoder=enc,
                                               _decoder_joint=dec))  # fmt: skip


def test_a_part_left_on_the_cpu_is_a_full_memory_only_when_little_is_free(monkeypatch: pytest.MonkeyPatch) -> None:
    cuda, cpu = parakeet.CUDA, "CPUExecutionProvider"
    parakeet._check_on_gpu(_model(cuda, cuda, cuda))  # all on the GPU: fine
    monkeypatch.setattr(gpu, "free_memory_mb", lambda: 900)
    with pytest.raises(EngineOutOfMemory):
        parakeet._check_on_gpu(_model(cuda, cuda, cpu))  # even only the decoder: every session is checked
    monkeypatch.setattr(gpu, "free_memory_mb", lambda: 9000)
    with pytest.raises(EngineError) as plenty:
        parakeet._check_on_gpu(_model(cuda, cpu, cuda))
    assert not isinstance(plenty.value, EngineOutOfMemory) and "GPU drivers" in str(plenty.value)
    monkeypatch.setattr(gpu, "free_memory_mb", lambda: None)  # cannot be told: not called a full memory
    with pytest.raises(EngineError) as unknown:
        parakeet._check_on_gpu(_model(cpu, cuda, cuda))
    assert not isinstance(unknown.value, EngineOutOfMemory)
