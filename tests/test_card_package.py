"""The download package's graphics-card support loads NVIDIA's own libraries itself (no torch): only on the card
path; a missing or damaged one is one clear sentence, the support is forgotten (offered again) and the run goes on the
processor."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from parakeet_stt import gpu


@pytest.fixture
def card_support(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """The graphics-card support's library folders, every file there but none of them a real library, no torch."""
    root = tmp_path / "nvidia"
    for lib, dll in gpu.NVIDIA_DLLS:
        (root / lib / "bin").mkdir(parents=True, exist_ok=True)
        (root / lib / "bin" / dll).write_bytes(b"not a library")
    monkeypatch.setattr(gpu, "_has_torch", lambda: False)
    monkeypatch.setattr(gpu, "nvidia_root", lambda: root)
    monkeypatch.setattr(gpu, "_loaded", [])
    monkeypatch.setattr(gpu, "_dirs", [])
    monkeypatch.setenv("PATH", r"C:\Windows\System32")
    gpu.cuda_libraries.cache_clear()
    yield root
    gpu.cuda_libraries.cache_clear()


def test_a_damaged_card_library_is_said_plainly_and_the_support_offered_again(
    card_support: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from parakeet_stt import cardpack

    forgotten: list[bool] = []
    monkeypatch.setattr(cardpack, "forget", lambda root=None: forgotten.append(True))
    with pytest.raises(gpu.CardFilesError) as caught:
        gpu.cuda_libraries()
    first = gpu.NVIDIA_DLLS[0][1]
    assert f"missing or damaged ({first})" in str(caught.value)
    assert "offers to download the support again" in str(caught.value)
    assert forgotten == [True]  # the pack no longer counts: the next look at the PC offers it again


FALLBACK = r"""
import sys
from pathlib import Path
card = Path(sys.argv[1])
(card / "onnxruntime").mkdir(parents=True)
(card / "onnxruntime" / "__init__.py").write_text("raise ImportError('DLL load failed')", encoding="utf-8")
sys.path.insert(0, str(card))
from parakeet_stt import cardpack, gpu
cardpack.present = lambda root=None: True
cardpack.forget = lambda root=None: None
cardpack.site = lambda root=None: card
gpu.nvidia_root = lambda: card / "nvidia"
gpu._has_torch = lambda: False
try:
    gpu.onnxruntime()
    print("no error")
except gpu.CardFilesError:
    ort = gpu.onnxruntime()  # the same process, as the engine's processor fallback calls it
    print("fallback", card not in Path(ort.__file__).parents)
"""


def test_a_damaged_card_onnxruntime_falls_back_to_the_processor_one_in_the_same_run(tmp_path: Path) -> None:
    import subprocess
    import sys

    root = Path(__file__).resolve().parent.parent
    run = subprocess.run([sys.executable, "-c", FALLBACK, str(tmp_path / "site")], cwd=root, capture_output=True,
                         text=True, timeout=120, check=False)  # fmt: skip
    assert run.stdout.strip().splitlines()[-1:] == ["fallback True"], run.stderr[-800:]


def test_the_libraries_load_only_on_the_card_path() -> None:
    """The processor path (the speech detector, the model on the processor) imports onnxruntime but never loads
    NVIDIA's libraries: a laptop without NVIDIA running the card package must not depend on them."""
    import inspect

    assert "cuda_libraries" not in inspect.getsource(gpu.onnxruntime)
    from parakeet_stt.engines import parakeet

    assert "cuda_libraries" in inspect.getsource(parakeet._load)
    assert "cuda_libraries" not in inspect.getsource(parakeet._load_cpu)
