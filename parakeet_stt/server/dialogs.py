"""Windows' own Open dialogs, shown in front of the app window, in a child process of the server.

The page cannot learn where a file lives, so "Add files" and the output-folder picker ask Windows instead.
The server runs `python -m parakeet_stt dialog files|folder` and reads one JSON line: {"paths": [...]}
(empty when the user cancels). The dialog is owned by the app window, so Windows keeps it above that
window; the child also attaches to the foreground thread so the dialog gets the keyboard focus.

Plain ctypes COM (IFileOpenDialog), no extra package.
"""

from __future__ import annotations

import contextlib
import ctypes
import json
import subprocess
import sys
from ctypes import wintypes
from typing import Any, Literal

from parakeet_stt.server.uploads import MEDIA_EXTENSIONS

Kind = Literal["files", "folder"]
WINDOW_TITLE = "TalkSubs"  # the page title, which Chromium uses as the app window's title
MEDIA_PATTERNS = ";".join(f"*{ext}" for ext in MEDIA_EXTENSIONS)


class DialogError(Exception):
    """The dialog could not be shown; the message is for the user."""


def command(kind: Kind) -> list[str]:
    from parakeet_stt.server.runner import default_worker_cmd

    return [*default_worker_cmd()[:3], "-m", "parakeet_stt", "dialog", kind]


_open: set[subprocess.Popen[str]] = set()  # dialogs showing now; closed when the app stops


def close_all() -> None:
    """The app is stopping: close any dialog still open, so its waiting thread ends too."""
    for proc in list(_open):
        with contextlib.suppress(OSError):
            proc.kill()


def run(kind: Kind) -> list[str]:
    """Server side (call in a thread): show the dialog in a child process and return the chosen paths."""
    try:
        proc = subprocess.Popen(command(kind), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                                creationflags=subprocess.CREATE_NO_WINDOW)  # fmt: skip
    except OSError as exc:
        raise DialogError(f"The Windows dialog could not be opened: {exc}") from None
    _open.add(proc)
    try:
        out, err = proc.communicate()
    finally:
        _open.discard(proc)
    last = next((line for line in reversed(out.splitlines()) if line.strip()), "")
    try:
        answer = json.loads(last)
    except json.JSONDecodeError:
        raise DialogError(f"The Windows dialog failed: {err.strip()[-300:] or 'no answer'}") from None
    if "error" in answer:
        raise DialogError(f"The Windows dialog failed: {answer['error']}")
    return [str(p) for p in answer.get("paths", [])]


_ole32 = ctypes.WinDLL("ole32")
_user32 = ctypes.WinDLL("user32")
_kernel32 = ctypes.WinDLL("kernel32")


class _GUID(ctypes.Structure):
    _fields_ = [("d1", wintypes.DWORD), ("d2", wintypes.WORD), ("d3", wintypes.WORD), ("d4", ctypes.c_ubyte * 8)]

    @classmethod
    def parse(cls, text: str) -> _GUID:
        h = text.strip("{}").replace("-", "")
        return cls(int(h[0:8], 16), int(h[8:12], 16), int(h[12:16], 16),
                   (ctypes.c_ubyte * 8)(*bytes.fromhex(h[16:])))  # fmt: skip


class _FilterSpec(ctypes.Structure):
    _fields_ = [("name", wintypes.LPCWSTR), ("spec", wintypes.LPCWSTR)]


CLSID_FILE_OPEN_DIALOG = _GUID.parse("DC1C5A9C-E88A-4DDE-A5A1-60F82A20AEF7")
IID_IFILE_OPEN_DIALOG = _GUID.parse("D57C7288-D4AD-4768-BE02-9D969532D960")
CLSCTX_INPROC_SERVER = 1
COINIT_APARTMENTTHREADED = 2
FOS_NOCHANGEDIR = 0x8
FOS_PICKFOLDERS = 0x20
FOS_FORCEFILESYSTEM = 0x40
FOS_ALLOWMULTISELECT = 0x200
FOS_PATHMUSTEXIST = 0x800
FOS_FILEMUSTEXIST = 0x1000
SIGDN_FILESYSPATH = 0x80058000
HRESULT_CANCELLED = 0x800704C7
# vtable slots: IUnknown 0-2, IModalWindow::Show 3, IFileDialog 4-26, IFileOpenDialog 27-28
_SHOW, _SET_FILE_TYPES, _SET_OPTIONS, _GET_OPTIONS, _SET_TITLE, _GET_RESULT, _GET_RESULTS = 3, 4, 9, 10, 17, 20, 27
_RELEASE = 2
_ITEM_GET_DISPLAY_NAME = 5
_ARRAY_GET_COUNT, _ARRAY_GET_ITEM_AT = 7, 8
_ENUM_PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def _call(obj: ctypes.c_void_p, slot: int, *args: tuple[Any, Any]) -> int:
    """Call method `slot` of a COM object; args are (ctypes type, value) pairs. Returns the HRESULT (unsigned)."""
    vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    proto = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *(t for t, _ in args))
    return int(proto(vtbl[slot])(obj, *(v for _, v in args))) & 0xFFFFFFFF


def _release(obj: ctypes.c_void_p) -> None:
    if obj:
        _call(obj, _RELEASE)


def _item_path(item: ctypes.c_void_p) -> str:
    name = ctypes.c_wchar_p()
    hr = _call(
        item,
        _ITEM_GET_DISPLAY_NAME,
        (ctypes.c_ulong, SIGDN_FILESYSPATH),
        (ctypes.POINTER(ctypes.c_wchar_p), ctypes.byref(name)),
    )
    if hr != 0 or not name.value:
        return ""
    try:
        return name.value
    finally:
        _ole32.CoTaskMemFree(name)


def find_app_window(title: str = WINDOW_TITLE) -> int | None:
    """The visible Chromium app window whose title is the page title (the newest one if several)."""
    found: list[int] = []
    buf = ctypes.create_unicode_buffer(512)

    def visit(hwnd: int, _: int) -> bool:
        if _user32.IsWindowVisible(hwnd):
            _user32.GetClassNameW(hwnd, buf, 512)
            if buf.value == "Chrome_WidgetWin_1":
                _user32.GetWindowTextW(hwnd, buf, 512)
                if buf.value == title or buf.value.startswith(title + " "):
                    found.append(hwnd)
        return True

    _user32.EnumWindows(_ENUM_PROC(visit), 0)
    return found[0] if found else None  # EnumWindows lists top-down: the first is the one in front


def _take_focus(owner: int | None) -> None:
    """A process the user did not click cannot take the foreground; joining the foreground thread's input lets it."""
    fg = _user32.GetForegroundWindow()
    if not fg:
        return
    fg_thread = _user32.GetWindowThreadProcessId(fg, None)
    me = _kernel32.GetCurrentThreadId()
    if fg_thread and fg_thread != me and _user32.AttachThreadInput(me, fg_thread, True):
        try:
            if owner:
                _user32.SetForegroundWindow(owner)
        finally:
            _user32.AttachThreadInput(me, fg_thread, False)


def ask(kind: Kind, owner: int | None, title: str) -> list[str]:
    """Show the dialog and return the chosen paths ([] when cancelled). Raises OSError on a COM failure."""
    hr = _ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED) & 0xFFFFFFFF
    if hr not in (0, 1):
        raise OSError(f"COM could not start (0x{hr:08X})")
    dialog = ctypes.c_void_p()
    try:
        clsid, iid = ctypes.byref(CLSID_FILE_OPEN_DIALOG), ctypes.byref(IID_IFILE_OPEN_DIALOG)
        hr = _ole32.CoCreateInstance(clsid, None, CLSCTX_INPROC_SERVER, iid, ctypes.byref(dialog)) & 0xFFFFFFFF
        if hr != 0:
            raise OSError(f"the Open dialog is not available (0x{hr:08X})")
        opts = wintypes.DWORD()
        _call(dialog, _GET_OPTIONS, (ctypes.POINTER(wintypes.DWORD), ctypes.byref(opts)))
        flags = opts.value | FOS_NOCHANGEDIR | FOS_FORCEFILESYSTEM | FOS_PATHMUSTEXIST
        if kind == "folder":
            flags |= FOS_PICKFOLDERS
        else:
            flags |= FOS_ALLOWMULTISELECT | FOS_FILEMUSTEXIST
            specs = (_FilterSpec * 2)(_FilterSpec("Audio and video", MEDIA_PATTERNS), _FilterSpec("All files", "*.*"))
            _call(dialog, _SET_FILE_TYPES, (ctypes.c_uint, 2), (ctypes.POINTER(_FilterSpec), specs))
        _call(dialog, _SET_OPTIONS, (wintypes.DWORD, flags))
        _call(dialog, _SET_TITLE, (wintypes.LPCWSTR, title))
        _take_focus(owner)
        hr = _call(dialog, _SHOW, (wintypes.HWND, owner or 0))
        if hr == HRESULT_CANCELLED:
            return []
        if hr != 0:
            raise OSError(f"the Open dialog failed (0x{hr:08X})")
        return _results(dialog, kind)
    finally:
        _release(dialog)
        _ole32.CoUninitialize()


def _results(dialog: ctypes.c_void_p, kind: Kind) -> list[str]:
    if kind == "folder":
        item = ctypes.c_void_p()
        if _call(dialog, _GET_RESULT, (ctypes.POINTER(ctypes.c_void_p), ctypes.byref(item))) != 0:
            return []
        try:
            path = _item_path(item)
            return [path] if path else []
        finally:
            _release(item)
    array = ctypes.c_void_p()
    if _call(dialog, _GET_RESULTS, (ctypes.POINTER(ctypes.c_void_p), ctypes.byref(array))) != 0:
        return []
    try:
        count = wintypes.DWORD()
        _call(array, _ARRAY_GET_COUNT, (ctypes.POINTER(wintypes.DWORD), ctypes.byref(count)))
        paths = []
        for i in range(count.value):
            item = ctypes.c_void_p()
            if (
                _call(
                    array,
                    _ARRAY_GET_ITEM_AT,
                    (wintypes.DWORD, i),
                    (ctypes.POINTER(ctypes.c_void_p), ctypes.byref(item)),
                )
                == 0
            ):
                try:
                    path = _item_path(item)
                finally:
                    _release(item)
                if path:
                    paths.append(path)
        return paths
    finally:
        _release(array)


def main(argv: list[str]) -> int:
    """`dialog files|folder [title]`: print {"paths": [...]} or {"error": "..."} as one line."""
    kind = argv[0] if argv else ""
    if kind not in ("files", "folder"):
        print(json.dumps({"error": "usage: dialog files|folder [title]"}))
        return 2
    title = argv[1] if len(argv) > 1 else ("Add files" if kind == "files" else "Choose a folder")
    try:
        paths = ask(kind, find_app_window(), title)  # type: ignore[arg-type]
    except OSError as exc:
        print(json.dumps({"error": str(exc)}))
        return 1
    sys.stdout.write(json.dumps({"paths": paths}) + "\n")
    return 0
