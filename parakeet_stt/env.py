"""The one list of environment variables that keeps every cache, model store and temp folder in the project.

`scripts/env.ps1` and `scripts/env.sh` repeat this list for raw shell use; a test keeps all three identical.
Entry points call `apply()` before anything imports torch or onnxruntime. The doctor only judges against `VARS`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Rel:
    """A folder relative to the project root."""

    path: str


@dataclass(frozen=True)
class Fixed:
    """A literal value."""

    value: str


VARS: dict[str, Rel | Fixed] = {
    "PARAKEET_STT_ROOT": Rel("."),
    "HF_HOME": Rel(".cache/huggingface"),
    "HF_HUB_CACHE": Rel("models/hf"),
    "HF_XET_CACHE": Rel(".cache/hf-xet"),
    "HF_HUB_DISABLE_TELEMETRY": Fixed("1"),
    "HF_HUB_DISABLE_SYMLINKS_WARNING": Fixed("1"),
    "TORCH_HOME": Rel(".cache/torch"),
    "TORCHINDUCTOR_CACHE_DIR": Rel(".cache/torchinductor"),
    "TRITON_CACHE_DIR": Rel(".cache/triton"),
    "UV_CACHE_DIR": Rel(".cache/uv"),
    "UV_PYTHON_INSTALL_DIR": Rel(".cache/uv-python"),
    "UV_PYTHON_DOWNLOADS": Fixed("never"),
    "UV_TOOL_DIR": Rel(".cache/uv-tools"),
    "PIP_CACHE_DIR": Rel(".cache/pip"),
    "TEMP": Rel(".cache/tmp"),
    "TMP": Rel(".cache/tmp"),
    "TMPDIR": Rel(".cache/tmp"),  # Python's tempfile reads TMPDIR first; Git Bash sets it to the system drive
    "XDG_CACHE_HOME": Rel(".cache/xdg"),
    "CUDA_CACHE_PATH": Rel(".cache/cuda"),
    "CUDA_CACHE_MAXSIZE": Fixed("4294967296"),
    "ORT_TENSORRT_CACHE_PATH": Rel(".cache/ort-trt"),
    "ORT_TENSORRT_ENGINE_CACHE_PATH": Rel(".cache/ort-trt"),
    "NUMBA_CACHE_DIR": Rel(".cache/numba"),
    "MPLCONFIGDIR": Rel(".cache/matplotlib"),
    "npm_config_cache": Rel(".cache/npm"),
}


def expected(root: Path = ROOT) -> dict[str, str]:
    """Name -> value this project wants (folders as absolute paths)."""
    return {k: str((root / v.path).resolve()) if isinstance(v, Rel) else v.value for k, v in VARS.items()}


def apply(root: Path = ROOT) -> None:
    """Set the whole set in this process (inherited by children) and create the folders."""
    if not (root / "pyproject.toml").is_file():
        raise RuntimeError(f"{root} is not the project folder; run from the project checkout (editable install)")
    for name, value in expected(root).items():
        os.environ[name] = value
        if isinstance(VARS[name], Rel):
            Path(value).mkdir(parents=True, exist_ok=True)
    import tempfile

    tempfile.tempdir = None  # re-read TEMP on next use, in case something asked before apply()
    from parakeet_stt import cardpack

    cardpack.activate(root)  # the download package's graphics-card support, when it was fetched
