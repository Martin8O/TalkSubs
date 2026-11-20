"""`python -m parakeet_stt app`: start the server, open the window, stop when the window is closed.

Started by `TalkSubs.bat` through pythonw (no console window), so everything is logged to
`<data>/logs/app.log` and every problem that stops the start is also shown in a message box.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import logging
import os
import shutil
import socket
import sys
import urllib.request
from pathlib import Path
from typing import TextIO

from parakeet_stt.env import ROOT
from parakeet_stt.server.messages import APP_ID

LOG_MAX_BYTES = 5 * 2**20
WINDOW_PROFILE = ROOT / ".cache" / "app-window"
CACHE_WIPED = "pstt-cache-wiped-v1"
log = logging.getLogger("parakeet_stt.server")


class StartError(Exception):
    """The app cannot start; the message is for the user."""


def data_dir() -> Path:
    return Path(os.environ.get("PARAKEET_STT_DATA") or ROOT / "data")


def port() -> int:
    from parakeet_stt.server.app import DEFAULT_PORT

    raw = os.environ.get("PARAKEET_STT_PORT") or str(DEFAULT_PORT)
    if not raw.isdigit() or not 1024 <= int(raw) <= 65535:
        raise StartError(f"PARAKEET_STT_PORT must be a port number from 1024 to 65535, not {raw!r}.")
    return int(raw)


def _message_box(text: str) -> None:
    ctypes.windll.user32.MessageBoxW(None, text, "TalkSubs", 0x10)


def _open_log(path: Path) -> TextIO:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > LOG_MAX_BYTES:
        path.replace(path.with_suffix(".old.log"))
    return path.open("a", encoding="utf-8", buffering=1)


def _private_folders(data: Path) -> None:
    """The data folder and the window's profile (its cache holds what the window showed) are for this user only."""
    from parakeet_stt.server import access

    for folder in (data, WINDOW_PROFILE):
        try:
            access.lock_app_folder(folder, known=folder.resolve().is_relative_to(ROOT.resolve()))
        except OSError as exc:  # e.g. a drive without access lists, or a folder that is not the app's
            log.warning("could not limit access to %s: %s", folder, exc)
    marker = WINDOW_PROFILE / CACHE_WIPED
    if not marker.exists():  # copies a browser may have kept before /api answers carried no-store
        try:
            shutil.rmtree(WINDOW_PROFILE / "Default" / "Cache")
        except FileNotFoundError:
            pass
        except OSError as exc:  # a window of an earlier run still open: try again at the next start
            log.warning("could not empty the window's cache yet: %s", exc)
            return
        marker.write_bytes(b"1")


def _bind(p: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # without this a second process could bind the same port on Windows and steal connections
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    try:
        sock.bind(("127.0.0.1", p))
    except OSError:
        sock.close()
        raise
    sock.listen(64)  # connections wait in the backlog until the server accepts: the window may open at once
    sock.set_inheritable(False)
    return sock


def _ours_running(p: int) -> bool:
    """Our own app of this Windows user holds the port (an answer alone proves nothing: anyone can send it)."""
    from parakeet_stt.server import owner

    if not owner.listener_is_ours(p):
        return False
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{p}/api/status", timeout=3) as r:  # our own localhost server
            return bool(json.loads(r.read()).get("app") == APP_ID)
    except (OSError, ValueError):
        return False


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="parakeet-stt app")
    ap.add_argument("--no-window", action="store_true", help="serve only; stop with Ctrl+C (diagnostics)")
    args = ap.parse_args(argv)
    try:
        return _run(window=not args.no_window)
    except StartError as exc:
        message = str(exc)
    except Exception as exc:  # anything at all: under pythonw an unshown error is an app that silently never opens
        message = f"TalkSubs stopped with an error: {exc.__class__.__name__}: {exc}"
        log.exception("start failed")
    log.error("%s", message)
    if args.no_window:
        print(message, file=sys.stderr or sys.__stderr__)
    else:
        _message_box(message)
    return 1


def _start_log(data: Path) -> None:
    """Everything goes to `<data>/logs/app.log`, with the user's file names and link secrets hidden."""
    from parakeet_stt.server import logscrub

    try:
        logfile = _open_log(data / "logs" / "app.log")
    except OSError as exc:
        raise StartError(f"TalkSubs cannot write to its data folder {data}: {exc}") from None
    if sys.stderr is None or sys.stdout is None:  # pythonw: no console at all
        sys.stdout = sys.stderr = logscrub.RedactingStream(logfile)
    handler = logging.StreamHandler(logfile)
    handler.setFormatter(logscrub.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    logging.captureWarnings(True)


def _run(*, window: bool) -> int:
    data = data_dir()
    _start_log(data)
    p = port()
    url = f"http://127.0.0.1:{p}/"
    try:
        sock = _bind(p)
    except OSError:
        if not _ours_running(p):
            msg = f"TalkSubs cannot start: port {p} on this computer is used by another program."
            raise StartError(msg) from None
        log.info("already running on %s; opening another window", url)
        if window:  # this process has no job object yet, so the window outlives it
            from parakeet_stt.server.browser import open_window

            open_window(url, WINDOW_PROFILE)
        return 0

    from parakeet_stt.server import winjob

    winjob.contain_self()  # from here on, however this process ends, the worker and the window end with it
    _private_folders(data)  # before the store is opened and sealed: nobody else may read it meanwhile

    import uvicorn

    from parakeet_stt.server.app import ServerConfig, create
    from parakeet_stt.server.hub import Presence

    server_box: list[uvicorn.Server] = []

    def stop() -> None:
        if server_box:
            server_box[0].should_exit = True

    presence = Presence(stop) if window else None
    from parakeet_stt.server.store import StoreError

    try:
        srv = create(ServerConfig(data_dir=data, port=p, presence=presence))
    except StoreError as exc:  # reported now, before a window opens onto nothing
        raise StartError(f"{exc} Move it away (the app then starts with an empty queue) and start again.") from None
    config = uvicorn.Config(
        srv.asgi, log_level="info", proxy_headers=False, server_header=False, timeout_graceful_shutdown=5,
        log_config=None, ws="websockets-sansio", lifespan="on",
    )  # fmt: skip
    server = uvicorn.Server(config)
    server_box.append(server)
    if window:
        from parakeet_stt.server.browser import open_window

        open_window(url, WINDOW_PROFILE)
    log.info("serving %s (data in %s)", url, data)
    server.run(sockets=[sock])
    if not server.started:  # uvicorn returns quietly when the app's start-up fails; the log has the reason
        raise StartError(f"TalkSubs could not start. Details are in {data / 'logs' / 'app.log'}.")
    log.info("stopped")
    return 0
