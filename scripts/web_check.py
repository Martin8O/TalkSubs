"""The web half of the quality gate, and the one way to build the page the app serves.

    python scripts/web_check.py lint | types | test | fresh | build

Runs the tools from web/node_modules with node directly (never npx), with every cache inside the project.
`fresh` rebuilds into .cache/web-check and fails when the committed page in parakeet_stt/server/static differs.
"""

from __future__ import annotations

import filecmp
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
STATIC = ROOT / "parakeet_stt" / "server" / "static"
CHECK_OUT = ROOT / ".cache" / "web-check"
NODE_MAJOR = 24
BIN = {
    "eslint": WEB / "node_modules" / "eslint" / "bin" / "eslint.js",
    "tsc": WEB / "node_modules" / "typescript" / "bin" / "tsc",
    "vitest": WEB / "node_modules" / "vitest" / "vitest.mjs",
    "vite": WEB / "node_modules" / "vite" / "bin" / "vite.js",
}


def _env(**extra: str) -> dict[str, str]:
    sys.path.insert(0, str(ROOT))
    from parakeet_stt import env as project_env

    out = dict(os.environ)
    out.update(project_env.expected(ROOT))
    out.update(extra)
    return out


def _run(tool: str, *args: str, **extra_env: str) -> int:
    node = shutil.which("node")
    if node is None:
        print("node is not installed (needed for the web part)")
        return 1
    version = subprocess.run([node, "--version"], capture_output=True, text=True, check=False).stdout.strip()
    if not version.startswith(f"v{NODE_MAJOR}."):  # the committed page is only byte-identical on one Node line
        print(f"node {version or '?'} found; this project builds with Node {NODE_MAJOR} (see web/package.json)")
        return 1
    if not BIN[tool].is_file():
        print(f"{tool} is missing: run `npm ci` in web/ (through scripts/env.ps1)")
        return 1
    return subprocess.call([node, str(BIN[tool]), *args], cwd=WEB, env=_env(**extra_env), stdin=subprocess.DEVNULL)


def _c_drive_untouched(since: float) -> list[str]:
    """Places on C: where node tools are known to write anyway; none may have changed during the run."""
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return []
    moved = []
    for sub in ("vitest", "npm-cache"):
        base = Path(local) / sub
        if base.exists():
            for p in base.rglob("*"):
                if p.stat().st_mtime >= since:
                    moved.append(str(p))
                    break
    return moved


def _diff(a: Path, b: Path) -> list[str]:
    cmp = filecmp.dircmp(a, b)
    out = [f"only in the committed page: {n}" for n in cmp.left_only]
    out += [f"only in a fresh build: {n}" for n in cmp.right_only]
    _, mismatch, errors = filecmp.cmpfiles(a, b, cmp.common_files, shallow=False)
    out += [f"differs: {n}" for n in mismatch + errors]
    for d in cmp.common_dirs:
        out += [f"{d}/{line}" for line in _diff(a / d, b / d)]
    return out


def main(argv: list[str]) -> int:
    what = argv[0] if argv else ""
    t0 = time.time() - 2  # file times are coarse
    if what == "lint":
        code = _run("eslint", "--max-warnings=0", ".")
    elif what == "types":
        code = _run("tsc", "-b")
    elif what == "test":
        token_home = ROOT / ".cache" / "tmp" / "vitest-localappdata"
        token_home.mkdir(parents=True, exist_ok=True)
        code = _run("vitest", "run", LOCALAPPDATA=str(token_home))  # vitest writes a token there, else to C:
    elif what == "build":
        code = _run("vite", "build", "--logLevel", "warn")
    elif what == "fresh":
        shutil.rmtree(CHECK_OUT, ignore_errors=True)
        code = _run("vite", "build", "--logLevel", "warn", "--outDir", str(CHECK_OUT), "--emptyOutDir")
        if code == 0:
            problems = _diff(STATIC, CHECK_OUT)
            if problems:
                print("The committed page is stale; run `python scripts/web_check.py build`:")
                print("\n".join(f"  {p}" for p in problems))
                code = 1
            else:
                print("committed page matches a fresh build")
    else:
        print(__doc__)
        return 2
    moved = _c_drive_untouched(t0)
    if moved:
        print(f"a tool wrote to C: during the run: {moved[0]}")
        return 1
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
