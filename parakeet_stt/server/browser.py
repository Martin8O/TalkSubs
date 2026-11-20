"""Open the app in its own browser window (Chromium `--app` mode with a profile inside the project)."""

from __future__ import annotations

import os
import subprocess
import winreg
from pathlib import Path

CHROMIUM_EXES = ("msedge.exe", "chrome.exe", "brave.exe", "vivaldi.exe")
WINDOW_SIZE = "1280,860"


def _reg(root: int, key: str, value: str = "") -> str | None:
    try:
        with winreg.OpenKey(root, key) as k:
            v, _ = winreg.QueryValueEx(k, value)
    except OSError:
        return None
    return str(v) if v else None


def _exe_from_command(command: str) -> Path | None:
    command = command.strip()
    exe = command[1:].split('"', 1)[0] if command.startswith('"') else command.split(" ", 1)[0]
    path = Path(os.path.expandvars(exe))
    return path if path.is_file() else None


def _app_path(exe: str) -> Path | None:
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        found = _reg(root, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}")
        if found and Path(found).is_file():
            return Path(found)
    return None


def find_chromium() -> Path | None:
    """The default browser if it can do app windows, else Edge, else Chrome (Edge can be absent)."""
    prog_id = _reg(
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\http\UserChoice", "ProgId",
    )  # fmt: skip
    if prog_id:
        command = _reg(winreg.HKEY_CLASSES_ROOT, rf"{prog_id}\shell\open\command")
        exe = _exe_from_command(command) if command else None
        if exe is not None and exe.name.lower() in CHROMIUM_EXES:
            return exe
    for name in ("msedge.exe", "chrome.exe"):
        found = _app_path(name)
        if found is not None:
            return found
    return None


def open_window(url: str, profile_dir: Path) -> subprocess.Popen[bytes] | None:
    """Start the app window; without a Chromium browser, open the default browser (a normal tab)."""
    exe = find_chromium()
    if exe is None:
        os.startfile(url)  # our own localhost URL in the user's default browser
        return None
    profile_dir.mkdir(parents=True, exist_ok=True)
    return subprocess.Popen(
        [str(exe), f"--app={url}", f"--user-data-dir={profile_dir}", f"--window-size={WINDOW_SIZE}",
         "--no-first-run", "--no-default-browser-check", "--disable-sync"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )  # fmt: skip
