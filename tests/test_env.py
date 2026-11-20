"""The environment list exists three times (Python, PowerShell, Bash); the values they produce must agree."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from parakeet_stt.env import ROOT, VARS, Fixed, Rel, expected

SCRIPTS = ROOT / "scripts"


def _git_bash() -> str:
    """Git's own bash (a bare `bash` on Windows may be the WSL launcher)."""
    git = shutil.which("git")
    assert git, "git not found"
    exec_path = subprocess.run([git, "--exec-path"], capture_output=True, text=True, check=True).stdout.strip()
    for cand in (Path(exec_path).parents[2] / "bin" / "bash.exe", Path(exec_path).parents[1] / "bin" / "bash.exe"):
        if cand.is_file():
            return str(cand)
    raise AssertionError(f"Git bash not found near {exec_path}")


def _same(name: str, got: str | None, want: str) -> bool:
    if got is None:
        return False
    if isinstance(VARS[name], Fixed):
        return got == want
    return Path(got).resolve() == Path(want).resolve()


def _check(got: dict[str, str | None]) -> None:
    want = expected()
    bad = {k: (got.get(k), want[k]) for k in VARS if not _same(k, got.get(k), want[k])}
    assert bad == {}


def test_powershell_script_produces_the_same_values() -> None:
    names = ",".join(f"'{k}'" for k in VARS)
    cmd = (
        f". '{SCRIPTS / 'env.ps1'}'; $o=@{{}}; foreach($n in @({names})){{ "
        "$o[$n]=[Environment]::GetEnvironmentVariable($n) }; $o | ConvertTo-Json -Compress"
    )
    out = subprocess.run(
        ["pwsh", "-NoProfile", "-Command", cmd], capture_output=True, text=True, check=True, timeout=60
    )
    _check(json.loads(out.stdout))


def test_bash_script_produces_the_same_values() -> None:
    script = (SCRIPTS / "env.sh").as_posix()
    out = subprocess.run(
        [_git_bash(), "-c", f"source '{script}' && env"], capture_output=True, text=True, check=True, timeout=60
    )
    got: dict[str, str | None] = {}
    for line in out.stdout.splitlines():
        name, _, value = line.partition("=")
        if name in VARS:
            got[name] = value
    _check(got)


def test_every_folder_is_inside_the_project() -> None:
    for name, v in VARS.items():
        if isinstance(v, Rel):
            assert (ROOT / v.path).resolve().is_relative_to(ROOT), name


def test_temp_files_stay_in_the_project() -> None:
    assert Path(tempfile.gettempdir()).resolve().is_relative_to(ROOT)


def _fresh(code: str) -> str:
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True, timeout=120).stdout


def test_torch_is_loaded_before_onnxruntime() -> None:
    code = (
        "import sys, pkgutil, importlib, parakeet_stt\n"
        "for m in pkgutil.walk_packages(parakeet_stt.__path__, 'parakeet_stt.'): importlib.import_module(m.name)\n"
        "from parakeet_stt import gpu; gpu.onnx_asr()\n"
        "mods = list(sys.modules); print(mods.index('torch') < mods.index('onnxruntime'))"
    )
    assert _fresh(code).strip() == "True"


def test_every_module_imports_without_the_gpu_stack() -> None:
    # the later web server needs the contract and the project file without loading torch / onnxruntime
    code = (
        "import sys, pkgutil, importlib, parakeet_stt\n"
        "for m in pkgutil.walk_packages(parakeet_stt.__path__, 'parakeet_stt.'): importlib.import_module(m.name)\n"
        "print(sorted(m for m in ('torch', 'onnxruntime', 'onnx_asr') if m in sys.modules))"
    )
    assert _fresh(code).strip() == "[]"
