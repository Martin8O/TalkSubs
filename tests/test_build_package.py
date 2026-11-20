"""The download package's own checks: they must see what they claim to check (a check that matches nothing passes)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("build_package", ROOT / "scripts" / "build_package.py")
assert _spec and _spec.loader
build_package = importlib.util.module_from_spec(_spec)
sys.modules["build_package"] = build_package  # its dataclasses look their module up
_spec.loader.exec_module(build_package)


def test_the_dll_reader_sees_what_a_real_windows_library_loads() -> None:
    python_dll = next(Path(sys.base_prefix).glob("python3?*.dll"))
    names = {n.lower() for n in build_package.pe_imports(python_dll.read_bytes())}
    assert "kernel32.dll" in names and any(n.startswith("vcruntime140") for n in names)
    assert build_package.pe_imports(b"not a program") == set()


def test_the_runtime_check_names_the_vc_redistributable_and_a_missing_library(tmp_path: Path) -> None:
    pyd = next(Path(sys.base_prefix, "DLLs").glob("_ctypes*.pyd"))
    (tmp_path / "a.pyd").write_bytes(pyd.read_bytes())
    redist, problems = build_package.runtime_dlls(tmp_path)
    assert "vcruntime140.dll" in redist  # Microsoft's runtime: reported for the launcher to check
    assert any("python3" in p for p in problems)  # python3x.dll is neither shipped here nor part of Windows
    assert not any("kernel32" in p.lower() for p in problems)  # Windows' own libraries are fine


def test_the_launcher_runs_from_folders_with_brackets_and_ampersands(tmp_path: Path) -> None:
    import subprocess

    text = build_package.LAUNCHER.format(redist_checks="")
    text = text.replace('start "" "python\\pythonw.exe" -m parakeet_stt app', "echo REACHED-START")
    for name in ("TalkSubs-1.1.0 (1)", "Tom & Jerry (x86)"):
        folder = tmp_path / name
        (folder / "python").mkdir(parents=True)
        (folder / "python" / "pythonw.exe").write_bytes(b"")
        (folder / "TalkSubs.bat").write_text(text.replace("\n", "\r\n"), encoding="ascii")
        # started the way a double-click does (the whole quoted path kept as one, "&" included)
        run = subprocess.run(f'cmd /s /c ""{folder / "TalkSubs.bat"}""', capture_output=True, text=True,
                             timeout=30, stdin=subprocess.DEVNULL, check=False)  # fmt: skip
        assert "REACHED-START" in run.stdout, (name, run.stdout, run.stderr)


def test_the_launcher_says_to_unpack_when_started_inside_the_zip(tmp_path: Path) -> None:
    import subprocess

    (tmp_path / "TalkSubs.bat").write_text(build_package.LAUNCHER.format(redist_checks="").replace("\n", "\r\n"),
                                           encoding="ascii")  # fmt: skip
    run = subprocess.run(["cmd", "/c", str(tmp_path / "TalkSubs.bat")], capture_output=True, text=True, timeout=30,
                         input="\n", check=False)  # fmt: skip
    assert "still inside the zip" in run.stdout and run.returncode == 1


def test_every_library_is_installed_with_the_locks_hashes(tmp_path: Path) -> None:
    import pytest

    export = (
        "idna==3.20 \\\n    --hash=sha256:" + "a" * 64 + " \\\n    --hash=sha256:" + "b" * 64 + "\n"
        "numpy==2.4.6 ; sys_platform == 'win32' \\\n    --hash=sha256:" + "c" * 64 + "\n"
    )
    hashes = build_package.locked_hashes(export)
    assert hashes["idna"] == ("3.20", ["sha256:" + "a" * 64, "sha256:" + "b" * 64])
    wheel = tmp_path / "onnxruntime-1.23.2-cp311-cp311-win_amd64.whl"
    out = build_package.hashed_requirements(f"idna==3.20\nnumpy==2.4.6\nonnxruntime @ file:///{wheel}\n", hashes,
                                            wheel)  # fmt: skip
    lines = out.splitlines()
    assert lines[0].startswith("idna==3.20 --hash=sha256:aaaa") and lines[1].startswith("numpy==2.4.6 --hash=")
    assert lines[2] == f"{wheel} --hash=sha256:{build_package.ONNXRUNTIME.sha256}"
    with pytest.raises(build_package.BuildError, match=r"not in uv\.lock"):
        build_package.hashed_requirements("idna==9.9\n", hashes, wheel)  # another version than the lock's


def test_the_machine_path_scan_finds_the_project_folder_in_any_encoding(tmp_path: Path) -> None:
    (tmp_path / "plain.txt").write_text(f"built at {build_package.ROOT}", encoding="utf-8")
    (tmp_path / "wide.bin").write_bytes(str(build_package.ROOT).upper().encode("utf-16-le"))
    (tmp_path / "clean.txt").write_text("nothing here", encoding="utf-8")
    found = build_package.machine_paths(tmp_path, sorted(tmp_path.iterdir()))
    assert len(found) == 2 and not any("clean" in f for f in found)
