"""Build the download-and-run package for Windows: unzip, double-click TalkSubs.bat, done.

    .venv\\Scripts\\python.exe scripts\\build_package.py [--ref HEAD] [--worktree] [--out dist]

The package runs the speech model on the processor (no NVIDIA libraries inside) and holds no model: the first
start downloads the pinned files (models.py). The app is read from a git COMMIT, never from the folder, so a
half-done edit cannot ship; `--worktree` builds a trial package from uncommitted work through a throwaway commit.
The direct downloads are pinned by size and SHA-256: the official embeddable Python, the processor-only
onnxruntime wheel and an LGPL ffmpeg build. The other libraries come from PyPI at the exact versions of uv.lock,
each checked against the lock's SHA-256 hashes.
The finished folder is checked before it is zipped, and the build stops on any problem.
The smoke runs the packaged app as a user's PC would: a spoken sample transcribed on the processor (the package's
own onnxruntime, no NVIDIA library loaded) and its server started and asked for its status.
On a build PC with a usable NVIDIA card the smoke also proves the graphics-card support the app downloads on request
(cardpack.py): the packaged app unpacks it from the same pinned wheels (cached here, no network), transcribes a
spoken sample on the card with every NVIDIA library traced to the package, then with the card hidden; nothing of it
stays in the package.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # the app's own pins of the graphics-card support (cardpack.py)
sys.path.insert(0, str(ROOT / "scripts"))  # the spoken sample of the README pictures
DOWNLOADS = ROOT / ".cache" / "package-downloads"
NAME = "TalkSubs"


@dataclass(frozen=True)
class Download:
    url: str
    size: int
    sha256: str

    @property
    def file(self) -> Path:
        return DOWNLOADS / self.url.rsplit("/", 1)[1]


PYTHON = Download(
    "https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip",
    11249023, "009d6bf7e3b2ddca3d784fa09f90fe54336d5b60f0e0f305c37f400bf83cfd3b",
)  # fmt: skip
ONNXRUNTIME = Download(
    "https://files.pythonhosted.org/packages/3a/db/1b4a62e23183a0c3fe441782462c0ede9a2a65c6bbffb9582fab7c7a0d38/"
    "onnxruntime-1.23.2-cp311-cp311-win_amd64.whl",
    13468349, "902c756d8b633ce0dedd889b7c08459433fbcf35e9c38d1c03ddc020f0648c6e",
)  # fmt: skip
# BtbN FFmpeg-Builds keep the last build of each month for two years: re-pin about once a year
FFMPEG = Download(
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/autobuild-2026-10-31-12-59/"
    "ffmpeg-n8.1.3-27-g8dca1586f4-win64-lgpl-shared-8.1.zip",
    80356594, "5cef7bc48e513f3cee7c881e8b2999d835541edb1846c3e89443a67f8f8556ab",
)  # fmt: skip
GPL3 = Download("https://www.gnu.org/licenses/gpl-3.0.txt", 35149,
                "3972dc9744f6499f0f9b2dbf76696f2ae7ad8af9b23dde66d6af86c9dfb36986")  # fmt: skip
# flatbuffers (an onnxruntime library, Apache-2.0) ships its wheel without the licence text the licence asks for
APACHE2 = Download("https://www.apache.org/licenses/LICENSE-2.0.txt", 11358,
                   "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30")  # fmt: skip
FFMPEG_NOTICE = """\
ffmpeg and ffprobe in this folder are unmodified builds of FFmpeg 8.1.3 (n8.1.3-27-g8dca1586f4), made by
BtbN/FFmpeg-Builds (release autobuild-2026-10-31-12-59, LGPL shared build), licensed under the GNU Lesser
General Public License version 3 (LICENSE.txt), which builds on the GNU General Public License version 3
(GPL-3.0.txt). TalkSubs runs them as separate programs.

Source code:
- FFmpeg at this version: https://github.com/FFmpeg/FFmpeg/tree/8dca1586f4
- The build scripts, which name every library inside and its version:
  https://github.com/BtbN/FFmpeg-Builds/tree/autobuild-2026-10-31-12-59
- The archive these files come from: {url}
"""

# what goes into the package from the commit
APP_PATHS = ("parakeet_stt/", "presets/", "docs/images/")
APP_FILES = ("pyproject.toml", "LICENSE", "README.md", "CHANGELOG.md", "THIRD-PARTY-NOTICES.txt")
LEFT_OUT_DEPS = {"torch", "onnxruntime-gpu"}  # the graphics-card libraries; onnxruntime (processor) replaces them
# runtime libraries Windows itself does not have; the launcher checks for them (Microsoft's VC++ Redistributable)
REDIST = re.compile(r"^(msvcp140.*|vcruntime140.*|concrt140|vcomp140|vccorlib140)\.dll$", re.I)
VC_REDIST_URL = "https://aka.ms/vs/17/release/vc_redist.x64.exe"

LAUNCHER = r"""@echo off
rem Starts TalkSubs: the local server and its window. Closing the window stops everything.
rem Everything the app keeps (work, settings, the speech model, caches) stays inside this folder.
cd /d "%~dp0"
if not exist "python\pythonw.exe" (
  echo TalkSubs is still inside the zip file. Unpack the whole zip first ^(right-click it, Extract All^),
  echo then start TalkSubs.bat from the unpacked folder.
  pause
  exit /b 1
)
{redist_checks}copy /y nul ".write-test" >nul 2>&1
if errorlevel 1 (
  echo TalkSubs cannot write to this folder:
  echo   "%~dp0"
  echo Move the whole TalkSubs folder to a place you can write to, for example C:\TalkSubs or another drive
  echo ^(not Program Files^), then start it again.
  pause
  exit /b 1
)
del ".write-test" >nul 2>&1
start "" "python\pythonw.exe" -m parakeet_stt app
"""
REDIST_CHECK = r"""if not exist "%SystemRoot%\System32\{dll}" (
  echo TalkSubs needs the Microsoft Visual C++ Redistributable ^(x64^), which this PC does not have yet.
  echo Install it from {url} and start TalkSubs again.
  pause
  exit /b 1
)
"""
PTH = "python311.zip\n.\n..\nLib\\site-packages\nimport site\n"


class BuildError(Exception):
    pass


def git(*args: str, env: dict[str, str] | None = None) -> bytes:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, env=env).stdout


def fetch(d: Download) -> Path:
    """Download once into the project cache; any size or SHA-256 mismatch stops the build."""
    DOWNLOADS.mkdir(parents=True, exist_ok=True)
    if not d.file.is_file():
        part = d.file.with_suffix(d.file.suffix + ".part")
        with urllib.request.urlopen(d.url, timeout=120) as r, part.open("wb") as f:
            shutil.copyfileobj(r, f, 1 << 20)
        part.replace(d.file)
    data = d.file.read_bytes()
    if len(data) != d.size or hashlib.sha256(data).hexdigest() != d.sha256:
        d.file.unlink()
        raise BuildError(f"{d.file.name}: size or SHA-256 differs from the pin; deleted, nothing built")
    return d.file


def worktree_commit() -> str:
    """A throwaway commit of the working tree (ignored files left out); no branch moves."""
    (ROOT / ".cache").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=ROOT / ".cache") as tmp:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(tmp) / "index")}
        git("read-tree", "HEAD", env=env)
        git("add", "-A", env=env)
        tree = git("write-tree", env=env).decode().strip()
        return git("commit-tree", tree, "-p", "HEAD", "-m", "trial package", env=env).decode().strip()


def app_files(ref: str, dest: Path) -> list[str]:
    names = [n for n in git("ls-tree", "-r", "-z", "--name-only", ref).decode().split("\0") if n]
    chosen = [n for n in names if n in APP_FILES or n.startswith(APP_PATHS)]
    for n in chosen:
        target = dest / n
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(git("cat-file", "blob", f"{ref}:{n}"))
    (dest / "BUILD").write_text(git("rev-parse", "--short", ref).decode().strip() + "\n", encoding="utf-8")
    return chosen


def python_runtime(dest: Path) -> None:
    py = dest / "python"
    with zipfile.ZipFile(fetch(PYTHON)) as z:
        z.extractall(py)
    (py / "python311._pth").write_text(PTH, encoding="utf-8")


def libraries(ref: str, dest: Path) -> None:
    """The app's libraries without the graphics-card ones, at uv.lock's exact versions, binary wheels only."""
    project = tomllib.loads(git("cat-file", "blob", f"{ref}:pyproject.toml").decode())
    wanted = [d for d in project["project"]["dependencies"]
              if re.split(r"[\s=<>!~\[;]", d, maxsplit=1)[0].lower() not in LEFT_OUT_DEPS]  # fmt: skip
    with tempfile.TemporaryDirectory(dir=ROOT / ".cache") as tmp:
        lock = Path(tmp) / "uv.lock"
        lock.write_bytes(git("cat-file", "blob", f"{ref}:uv.lock"))
        (Path(tmp) / "pyproject.toml").write_text(git("cat-file", "blob", f"{ref}:pyproject.toml").decode(),
                                                 encoding="utf-8")  # fmt: skip
        locked = Path(tmp) / "locked.txt"
        subprocess.run(["uv", "export", "--frozen", "--no-dev", "--no-emit-project",
                        "--format", "requirements-txt", "-o", str(locked)], cwd=tmp, check=True,
                       capture_output=True)  # fmt: skip
        hashes = locked_hashes(locked.read_text(encoding="utf-8"))
        constraints = Path(tmp) / "constraints.txt"
        constraints.write_text("".join(f"{n}=={v}\n" for n, (v, _) in hashes.items()), encoding="utf-8")
        wheel = fetch(ONNXRUNTIME)
        wanted_file = Path(tmp) / "wanted.in"
        wanted_file.write_text("\n".join([*wanted, str(wheel)]) + "\n", encoding="utf-8")
        pinned = Path(tmp) / "pinned.txt"  # exactly the libraries the package needs, at the lock's versions
        subprocess.run(["uv", "pip", "compile", "--quiet", "--python", sys.executable, "--only-binary", ":all:",
                        "--no-header", "--no-annotate", "-c", str(constraints), str(wanted_file), "-o", str(pinned)],
                       check=True)  # fmt: skip
        reqs = Path(tmp) / "requirements.txt"  # each one with the lock's hashes: a swapped file on PyPI is refused
        reqs.write_text(hashed_requirements(pinned.read_text(encoding="utf-8"), hashes, wheel), encoding="utf-8")
        site = dest / "python" / "Lib" / "site-packages"
        subprocess.run(["uv", "pip", "install", "--quiet", "--target", str(site), "--python", sys.executable,
                        "--only-binary", ":all:", "--require-hashes", "--no-deps", "-r", str(reqs)],
                       check=True)  # fmt: skip
    for scripts in ("bin", "Scripts"):  # console launchers carry the build machine's interpreter path
        shutil.rmtree(site / scripts, ignore_errors=True)
    for origin in site.glob("*.dist-info/direct_url.json"):  # where a wheel was installed from: a local path
        origin.unlink()
    for info in site.glob("flatbuffers-*.dist-info"):
        shutil.copyfile(fetch(APACHE2), info / "LICENSE")


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def locked_hashes(export: str) -> dict[str, tuple[str, list[str]]]:
    """name -> (version, sha256 hashes) from `uv export` (requirements format, hashes included)."""
    out: dict[str, tuple[str, list[str]]] = {}
    for entry in re.split(r"\n(?=\S)", export):
        head = entry.split("\n", 1)[0]
        m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;\\]+)", head)
        if m:
            out[_norm(m.group(1))] = (m.group(2), re.findall(r"--hash=(sha256:[0-9a-f]{64})", entry))
    return out


def hashed_requirements(pinned: str, hashes: dict[str, tuple[str, list[str]]], wheel: Path) -> str:
    """The resolved list again, every line with the lock's hashes; the local onnxruntime wheel with its own pin."""
    lines: list[str] = []
    for raw in pinned.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==(\S+)", line)
        if (m and _norm(m.group(1)) == "onnxruntime") or (line.lower().endswith(".whl") and "onnxruntime" in line):
            continue  # the pinned processor wheel, below
        if not m:
            raise BuildError(f"unexpected line in the resolved libraries: {line}")
        name, version = _norm(m.group(1)), m.group(2)
        locked = hashes.get(name)
        if locked is None or locked[0] != version or not locked[1]:
            raise BuildError(f"{name}=={version} is not in uv.lock with hashes: nothing built")
        lines.append(f"{name}=={version} " + " ".join(f"--hash={h}" for h in locked[1]))
    lines.append(f"{wheel} --hash=sha256:{ONNXRUNTIME.sha256}")
    return "\n".join(lines) + "\n"


def ffmpeg(dest: Path) -> None:
    out = dest / "ffmpeg"
    out.mkdir()
    with zipfile.ZipFile(fetch(FFMPEG)) as z:
        for info in z.infolist():
            parts = info.filename.split("/")
            name = parts[-1]
            program = len(parts) == 3 and parts[1] == "bin" and (name in ("ffmpeg.exe", "ffprobe.exe")
                                                                 or name.endswith(".dll"))  # fmt: skip
            if program or (len(parts) == 2 and name == "LICENSE.txt"):
                (out / name).write_bytes(z.read(info))
    shutil.copyfile(fetch(GPL3), out / "GPL-3.0.txt")
    (out / "NOTICE.txt").write_text(FFMPEG_NOTICE.format(url=FFMPEG.url), encoding="utf-8")


# --- checks on the finished folder --------------------------------------------------------------------------------


def pe_imports(data: bytes) -> set[str]:
    """DLL names a Windows program or library loads at start (import + delay-import tables)."""

    def u16(o: int) -> int:
        return int.from_bytes(data[o : o + 2], "little")

    def u32(o: int) -> int:
        return int.from_bytes(data[o : o + 4], "little")

    pe = u32(0x3C)
    if data[:2] != b"MZ" or data[pe : pe + 4] != b"PE\0\0":
        return set()
    coff = pe + 4
    opt = coff + 20
    dirs = opt + (112 if u16(opt) == 0x20B else 96)
    first = opt + u16(coff + 16)
    table = range(first, first + 40 * u16(coff + 2), 40)
    sections = [(u32(s + 12), max(u32(s + 8), u32(s + 16)), u32(s + 20)) for s in table]

    def off(rva: int) -> int | None:
        return next((rva - va + raw for va, size, raw in sections if va <= rva < va + size), None)

    def cstr(o: int) -> str:
        return data[o : data.index(b"\0", o)].decode("ascii", "replace")

    names: set[str] = set()
    for index, width, name_at in ((1, 20, 12), (13, 32, 4)):
        o = off(u32(dirs + 8 * index)) if u32(dirs + 8 * index) else None
        while o is not None and any(data[o : o + width]):
            at = off(u32(o + name_at))
            if at is not None:
                names.add(cstr(at))
            o += width
    return names


def check(pkg: Path, chosen: list[str]) -> list[str]:
    """Problems with the finished folder (empty = fit to zip). Also returns the redistributable DLLs needed."""
    problems: list[str] = []
    files = [p for p in pkg.rglob("*") if p.is_file()]
    rel = {p.relative_to(pkg).as_posix().lower() for p in files}
    site = pkg / "python" / "Lib" / "site-packages"
    for bad in ("torch", "nvidia"):
        if (site / bad).exists():
            problems.append(f"graphics-card library inside: {bad}")
    if (pkg / "card").exists():  # the graphics-card support is downloaded by the app, never shipped
        problems.append("the graphics-card support is inside (card/)")
    for r in rel:
        if "providers_cuda" in r or "onnxruntime_gpu" in r or r.startswith("models/"):
            problems.append(f"must not ship: {r}")
    for p in files:  # the speech model is downloaded at the first start; libraries carry only tiny helper graphs
        if p.suffix.lower() in (".onnx", ".data", ".safetensors") and p.stat().st_size > 20_000_000:
            problems.append(f"a model file inside: {p.relative_to(pkg)}")
    for need in ("python/pythonw.exe", "python/python.exe", "ffmpeg/ffmpeg.exe", "ffmpeg/ffprobe.exe",
                 "ffmpeg/license.txt", "ffmpeg/gpl-3.0.txt", "ffmpeg/notice.txt", "python/license.txt", "license",
                 "talksubs.bat", "build", "parakeet_stt/__main__.py", "third-party-notices.txt",
                 "python/lib/site-packages/onnxruntime/capi/onnxruntime.dll"):  # fmt: skip
        if need not in rel:
            problems.append(f"missing: {need}")
    readme = (pkg / "README.md").read_text(encoding="utf-8")
    pictures = re.findall(r"\]\((docs/images/[^)]+)\)", readme)
    if not pictures:
        problems.append("README links no picture: the picture check matched nothing")
    problems += [f"README picture missing: {p}" for p in pictures if p.lower() not in rel]
    page = pkg / "parakeet_stt" / "server" / "static" / "index.html"
    assets = re.findall(r'(?:src|href)="/(assets/[^"]+)"', page.read_text(encoding="utf-8"))
    if not assets:
        problems.append("the built page references no asset: the asset check matched nothing")
    problems += [f"page asset missing: {a}" for a in assets
                 if f"parakeet_stt/server/static/{a}".lower() not in rel]  # fmt: skip
    if any("__pycache__" in r for r in rel):
        problems.append("compiled files of the build machine inside (__pycache__)")
    return problems + machine_paths(pkg, files)


def machine_paths(pkg: Path, files: list[Path]) -> list[str]:
    """This machine's project folder and user folder must appear in no file (programs included)."""
    home = Path(os.environ.get("USERPROFILE", "~")).expanduser()
    secrets = {str(ROOT), ROOT.as_posix(), str(home), home.as_posix()}
    needles = [s.lower().encode() for s in secrets] + [s.lower().encode("utf-16-le") for s in secrets]
    found = []
    for p in files:  # each file read once
        data = p.read_bytes().lower()
        if any(n in data for n in needles):
            found.append(f"a path of the build machine inside: {p.relative_to(pkg)}")
    return found


def runtime_dlls(pkg: Path) -> tuple[set[str], list[str]]:
    """Every DLL a shipped program or library imports must be shipped or part of Windows; the VC++ runtime ones are
    reported, so the launcher can check for them."""
    shipped = {p.name.lower() for p in pkg.rglob("*") if p.suffix.lower() in (".dll", ".pyd", ".exe")}
    system = Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32"
    redist: set[str] = set()
    problems: list[str] = []
    for p in pkg.rglob("*"):
        if p.suffix.lower() not in (".dll", ".pyd", ".exe"):
            continue
        for dll in pe_imports(p.read_bytes()):
            low = dll.lower()
            if low in shipped:
                continue
            if REDIST.match(low):
                redist.add(low)
            elif not (low.startswith(("api-ms-win-", "ext-ms-")) or (system / dll).is_file()):
                problems.append(f"{p.relative_to(pkg)} needs {dll}, which is neither shipped nor part of Windows")
    return redist, problems


def smoke(pkg: Path) -> list[str]:
    """Start the packaged Python with an environment that knows nothing of the project: it must say 'processor'."""
    system = os.environ.get("SYSTEMROOT", r"C:\Windows")
    keep = ("SystemRoot", "windir", "SystemDrive", "USERPROFILE", "USERNAME", "USERDOMAIN", "APPDATA",
            "LOCALAPPDATA", "COMPUTERNAME", "PROCESSOR_ARCHITECTURE", "NUMBER_OF_PROCESSORS", "ComSpec")  # fmt: skip
    env = {k: os.environ[k] for k in keep if k in os.environ}
    env["PATH"] = f"{system}\\System32;{system}"
    out = subprocess.run([str(pkg / "python" / "python.exe"), "-m", "parakeet_stt", "machine"], cwd=pkg, env=env,
                         capture_output=True, text=True, timeout=300, check=False)  # fmt: skip
    try:
        found = json.loads(out.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return [f"the packaged app did not start: {out.stderr.strip()[-400:]}"]
    # the processor; on a build PC with a usable card, the offer of the graphics-card support
    if found.get("device") != "cpu" or found.get("reason") not in CPU_REASONS:
        return [f"the packaged app should run on the processor, it says {found}"]
    print(f"smoke: packaged app starts and says {found['device']} / {found['reason']}")
    problems: list[str] = []
    try:
        problems = cpu_smoke(pkg, env)  # what every user gets: the processor, before any graphics-card support
        if problems:
            return problems
        if found["reason"] == "card_pack_missing":
            problems = card_smoke(pkg, env)
        else:
            print("smoke: no usable NVIDIA card here, the graphics-card support is not proven by this build")
    finally:
        for p in pkg.rglob("__pycache__"):  # compiled by this start; the user's first start makes its own
            shutil.rmtree(p)
        for leftover in (".cache", "data", "models", "card", "smoke"):
            shutil.rmtree(pkg / leftover, ignore_errors=True)
    return problems


CPU_REASONS = (
    "card_pack_missing",
    "no_driver",
    "no_card",
    "card_too_old",
    "card_too_small",
    "driver_too_old",
    "no_card_support",
)

# run inside the packaged Python: the graphics-card support unpacked by the app's own code (its wheels are already
# in card/downloads, so nothing is downloaded), then whether the machine probe now says "card"
CARD_UNPACK = r"""
import json, subprocess, sys
from parakeet_stt import env
env.apply()
from parakeet_stt import cardpack
cardpack.fetch()
probe = subprocess.run([sys.executable, "-m", "parakeet_stt", "machine"], capture_output=True, text=True)
print(json.dumps({"present": cardpack.present(), "machine": json.loads(probe.stdout.strip().splitlines()[-1])}))
"""
# run inside the packaged Python: a transcription through the command line, then every NVIDIA library the process
# has loaded, by full path (Windows' module list: what really runs, whatever any search order would have found)
CARD_PROBE = r"""
import ctypes, ctypes.wintypes as w, json, os, re, sys
from parakeet_stt import env
env.apply()
from parakeet_stt import cli
rc = cli.main(["transcribe", sys.argv[1], "-o", sys.argv[2], "--format", "srt"])
k32, psapi = ctypes.WinDLL("kernel32"), ctypes.WinDLL("psapi")
k32.GetCurrentProcess.restype = w.HANDLE
psapi.EnumProcessModules.argtypes = [w.HANDLE, ctypes.POINTER(w.HMODULE), w.DWORD, ctypes.POINTER(w.DWORD)]
psapi.GetModuleFileNameExW.argtypes = [w.HANDLE, w.HMODULE, w.LPWSTR, w.DWORD]
me, mods, need = k32.GetCurrentProcess(), (w.HMODULE * 4096)(), w.DWORD()
psapi.EnumProcessModules(me, mods, ctypes.sizeof(mods), ctypes.byref(need))
buf, paths = ctypes.create_unicode_buffer(1024), []
for m in mods[: need.value // ctypes.sizeof(w.HMODULE)]:
    psapi.GetModuleFileNameExW(me, m, buf, 1024)
    paths.append(buf.value)
nv_name = re.compile(r"(cudart|cublas|cudnn|cufft|nvrtc|providers_cuda).*\.dll$", re.I)
nv = [p for p in paths if nv_name.search(os.path.basename(p))]
print(json.dumps({"rc": rc, "nvidia": nv}))
"""
# the libraries a run on the card cannot do without
CARD_MUST_LOAD = ("onnxruntime_providers_cuda.dll", "cudart64_12.dll", "cublas64_12.dll", "cudnn64_9.dll")
# words of the spoken sample (scripts/readme_pictures.py) a working run must find
SAMPLE_WORDS = ("evening", "listen", "curiosity")


def _last_json(run: subprocess.CompletedProcess[str]) -> dict[str, object] | None:
    try:
        found = json.loads(run.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None
    return found if isinstance(found, dict) else None


def _lend_models(pkg: Path) -> str | None:
    """This checkout's speech model, lent to the package by hard links (removed with the smoke's leftovers)."""
    dev_models, models = ROOT / "models" / "hf", pkg / "models" / "hf"
    if not dev_models.is_dir():
        return "the speech model is not in this checkout (models/hf): the package cannot be proven without it"
    for f in dev_models.rglob("*"):
        target = models / f.relative_to(dev_models)
        if f.is_file() and not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            os.link(f, target)
    return None


def _free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def cpu_smoke(pkg: Path, env: dict[str, str]) -> list[str]:
    """The processor path users get, proven on the packaged app: the spoken sample transcribed with the card hidden
    (the package's own onnxruntime, no NVIDIA library), then the app's server started and asked for its status."""
    import readme_pictures  # the neutral talk the README pictures use, spoken by Windows

    missing = _lend_models(pkg)
    if missing:
        return [missing]
    problems = _card_run(pkg, {**env, "CUDA_VISIBLE_DEVICES": "-1"}, "processor", readme_pictures.sample())
    if problems:
        return problems
    port = _free_port()
    status: dict[str, object] = {}
    server = subprocess.Popen([str(pkg / "python" / "python.exe"), "-m", "parakeet_stt", "app", "--no-window"],
                              cwd=pkg, env={**env, "PARAKEET_STT_PORT": str(port)}, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL)  # fmt: skip
    try:
        for _ in range(180):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=2) as r:  # its own server
                    status = json.loads(r.read())
                break
            except OSError:
                if server.poll() is not None:
                    return [f"the packaged app's server stopped at its start (exit {server.returncode})"]
                time.sleep(0.5)
        else:
            return ["the packaged app's server did not answer within 90 s"]
    finally:
        server.kill()
        server.wait(30)
    if status.get("app") != "parakeet-stt":
        return [f"the packaged app's server answered something else: {status}"]
    print(f"smoke: the packaged app's server starts and answers (version {status.get('version', '?')})")
    return []


def card_smoke(pkg: Path, env: dict[str, str]) -> list[str]:
    """The graphics-card support proven on this PC's card: unpacked by the packaged app from the pinned wheels, then
    a spoken sample transcribed on the card (every NVIDIA library from the package, the sample's words found) and with
    the card hidden (none loaded). Holds this checkout's card lock; the model is lent by hard links."""
    import readme_pictures  # the neutral talk the README pictures use, spoken by Windows

    from parakeet_stt import cardpack, gpu

    shipped = (pkg / "parakeet_stt" / "cardpack.py").read_text(encoding="utf-8")
    if shipped != (ROOT / "parakeet_stt" / "cardpack.py").read_text(
        encoding="utf-8"
    ):  # the pins proven must be its own
        return ["the package's cardpack.py differs from this checkout's: build from the checkout's state (--worktree)"]
    downloads = pkg / "card" / "downloads"
    downloads.mkdir(parents=True)
    for w in cardpack.WHEELS:
        os.link(fetch(Download(w.url, w.pin.size, w.pin.sha256)), downloads / w.name)
    missing = _lend_models(pkg)
    if missing:
        return [missing]
    wav = readme_pictures.sample()
    python = str(pkg / "python" / "python.exe")
    lock = gpu.GpuLock()
    lock.acquire()  # one job on the card at a time, also against this checkout's own runs
    try:
        unpacked = _last_json(subprocess.run([python, "-c", CARD_UNPACK], cwd=pkg, env=env, capture_output=True,
                                             text=True, timeout=900, check=False))  # fmt: skip
        if not unpacked or not unpacked.get("present"):
            return [f"the packaged app did not unpack the graphics-card support: {unpacked}"]
        machine = unpacked.get("machine")
        if not isinstance(machine, dict) or (machine.get("device"), machine.get("reason")) != ("cuda", "card"):
            return [f"with the graphics-card support the packaged app should say cuda / card, it says {machine}"]
        print("smoke: the packaged app unpacked the graphics-card support and now says cuda / card")
        problems: list[str] = []
        for label, extra in (("card", {}), ("hidden card", {"CUDA_VISIBLE_DEVICES": "-1"})):
            problems += _card_run(pkg, {**env, **extra}, label, wav)
        return problems
    except subprocess.TimeoutExpired:
        return ["the graphics-card proof took more than 15 minutes"]
    finally:
        lock.release()


def _card_run(pkg: Path, env: dict[str, str], label: str, wav: Path) -> list[str]:
    out = pkg / "smoke" / label.replace(" ", "-")
    run = subprocess.run([str(pkg / "python" / "python.exe"), "-c", CARD_PROBE, str(wav), str(out)], cwd=pkg, env=env,
                         capture_output=True, text=True, timeout=900, check=False)  # fmt: skip
    got = _last_json(run)
    if got is None or got.get("rc") != 0:
        return [f"{label}: the packaged app did not transcribe: {run.stderr.strip()[-600:]}"]
    nvidia = got.get("nvidia")
    loaded = [str(p) for p in nvidia] if isinstance(nvidia, list) else []
    names = {Path(p).name.lower() for p in loaded}
    problems = [f"{label}: an NVIDIA library from outside the package: {p}" for p in loaded
                if not Path(p).resolve().is_relative_to(pkg.resolve())]  # fmt: skip
    if label == "card":
        problems += [f"card: {n} was not loaded" for n in CARD_MUST_LOAD if n not in names]
    elif loaded:
        problems.append(f"{label}: NVIDIA libraries loaded anyway: {sorted(names)}")
    text = " ".join(p.read_text(encoding="utf-8") for p in out.glob("*.srt")).lower()
    problems += [f"{label}: the transcript lacks the spoken word '{w}'" for w in SAMPLE_WORDS if w not in text]
    if not problems:
        print(f"smoke ({label}): transcribed, sample words found, {len(loaded)} NVIDIA libraries (from the package)")
    return problems


def build(ref: str, out: Path, trial_of: str | None = None) -> Path:
    version = tomllib.loads(git("cat-file", "blob", f"{ref}:pyproject.toml").decode())["project"]["version"]
    stale = out / f"{NAME}-{version}-windows-x64.zip"
    if trial_of is None:
        stale.unlink(missing_ok=True)  # a failed rebuild must not leave the previous package looking current
    stage = ROOT / "build" / NAME
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True)
    chosen = app_files(ref, stage)
    python_runtime(stage)
    libraries(ref, stage)
    ffmpeg(stage)
    redist, problems = runtime_dlls(stage)
    checks = "".join(REDIST_CHECK.format(dll=d, url=VC_REDIST_URL) for d in sorted(redist))
    (stage / f"{NAME}.bat").write_text(LAUNCHER.format(redist_checks=checks).replace("\n", "\r\n"), encoding="ascii")
    problems += smoke(stage) + check(stage, chosen)
    if problems:
        raise BuildError("the package is not fit to ship:\n  " + "\n  ".join(problems))
    out.mkdir(parents=True, exist_ok=True)
    trial = "" if trial_of is None else f"-trial-{trial_of[:9]}"  # never mistaken for the release package
    target = out / f"{NAME}-{version}{trial}-windows-x64.zip"
    part = target.with_name(target.name + ".part")
    with zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in sorted(stage.rglob("*")):
            if p.is_file():
                z.write(p, f"{NAME}/{p.relative_to(stage).as_posix()}")
    part.replace(target)  # whole or not at all: a stopped build leaves no zip under the package's name
    count = sum(1 for p in stage.rglob("*") if p.is_file())
    size = sum(p.stat().st_size for p in stage.rglob("*") if p.is_file())
    print(f"{target.name}: {count} files, {size / 1e6:.0f} MB unpacked, {target.stat().st_size / 1e6:.0f} MB zipped")
    needs = ", ".join(sorted(redist)) or "nothing beyond Windows itself"
    print(f"needs from Windows: {needs} (checked by the launcher)")
    return target


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ref", default="HEAD")
    ap.add_argument("--worktree", action="store_true", help="trial package from uncommitted work")
    ap.add_argument("--out", default=str(ROOT / "dist"))
    a = ap.parse_args()
    ref = worktree_commit() if a.worktree else git("rev-parse", a.ref).decode().strip()
    try:
        build(ref, Path(a.out), trial_of=ref if a.worktree else None)
    except BuildError as exc:
        print(f"BUILD STOPPED: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
