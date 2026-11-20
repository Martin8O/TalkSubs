"""Every test run keeps its caches and temp files in the project, like the app itself."""

from __future__ import annotations

import os
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from parakeet_stt import env

env.apply()
if not Path(tempfile.gettempdir()).resolve().is_relative_to(env.ROOT):
    raise RuntimeError(f"temp folder {tempfile.gettempdir()} is outside the project")

MakeClip = Callable[..., Path]


def key_of(data: Path) -> Any:
    """The store key of a test data folder (made on first use, like the app server does)."""
    from parakeet_stt.vault import Vault

    return Vault.create_or_open(data)


def unsealed(path: Path, role: Any) -> bytes:
    """The plain content of a file the app sealed (the key is found beside its data folder)."""
    from parakeet_stt import vault

    v = vault.locate(path)
    assert v is not None, f"no store key above {path}"
    return bytes(v.read(path, role))


@pytest.fixture
def make_clip(tmp_path: Path) -> MakeClip:
    """Generate a small media file with ffmpeg (tone + silence, optional video at a given frame rate)."""

    def make(name: str, *, seconds: float = 3.0, video_rate: str | None = None, audio: bool = True) -> Path:
        out = tmp_path / name
        cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y"]
        if audio:
            cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=44100:duration={seconds}"]
        if video_rate:
            cmd += ["-f", "lavfi", "-i", f"testsrc=size=64x48:rate={video_rate}:duration={seconds}"]
        if audio:
            cmd += ["-ac", "2"]
        cmd += [str(out)]
        subprocess.run(cmd, check=True, timeout=60)
        return out

    return make


EVAL = env.ROOT / "data" / "eval"  # the evaluation recordings and their checked reference text (not in git)
MODEL_DIR = env.ROOT / "models" / "hf" / "models--istupakov--parakeet-tdt-0.6b-v2-onnx"
REQUIRE_GPU = os.environ.get("PARAKEET_STT_REQUIRE_GPU_TESTS") == "1"  # set by the gate: a skip there is a failure


def gpu_ready() -> str | None:
    """Why GPU tests cannot run here, or None."""
    for need in (
        EVAL / "bait-noise.wav",
        EVAL / "lecture-a.wav",
        MODEL_DIR,
        EVAL / "lecture-a.600-780.ref.txt",
    ):
        if not need.exists():
            return f"missing {need.relative_to(env.ROOT)}"
    return None


def pytest_runtest_setup(item: pytest.Item) -> None:
    if "gpu" not in item.keywords:
        return
    reason = gpu_ready()
    if reason is None:
        return
    if REQUIRE_GPU:
        pytest.fail(f"the gate requires the GPU tests, but {reason}")
    pytest.skip(f"GPU test: {reason}")


@pytest.fixture(autouse=True)
def _test_client_counts_as_ours(monkeypatch: pytest.MonkeyPatch) -> None:
    """Starlette's test client has no real connection to trace; every real socket still gets the real check."""
    from parakeet_stt.server import owner

    real = owner.connection_is_ours

    def check(scope: dict[str, object]) -> bool:
        client = scope.get("client")
        return (isinstance(client, tuple) and client[:1] == ("testclient",)) or real(scope)

    monkeypatch.setattr(owner, "connection_is_ours", check)
