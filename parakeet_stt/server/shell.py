"""Show files in Explorer with all of them selected (`explorer /select,` takes only one, and misreads commas)."""

from __future__ import annotations

import ctypes
import os
from collections.abc import Sequence
from ctypes import wintypes
from pathlib import Path

_shell32 = ctypes.WinDLL("shell32")
_ole32 = ctypes.WinDLL("ole32")
_shell32.SHParseDisplayName.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
                                        wintypes.ULONG, ctypes.POINTER(wintypes.ULONG)]  # fmt: skip
_shell32.ILFindLastID.argtypes = [ctypes.c_void_p]
_shell32.ILFindLastID.restype = ctypes.c_void_p
_shell32.SHOpenFolderAndSelectItems.argtypes = [ctypes.c_void_p, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p),
                                                wintypes.DWORD]  # fmt: skip
_ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]


def _pidl(path: str) -> ctypes.c_void_p:
    out = ctypes.c_void_p()
    hr = _shell32.SHParseDisplayName(path, None, ctypes.byref(out), 0, None) & 0xFFFFFFFF
    if hr != 0 or not out:
        raise OSError(f"Explorer cannot find {path} (0x{hr:08X})")
    return out


def reveal(files: Sequence[Path]) -> None:
    """Open the folder of the files (they share one) with every one of them selected."""
    if not files:
        raise OSError("nothing to show")
    parent = str(files[0].parent)
    _ole32.CoInitializeEx(None, 2)
    owned: list[ctypes.c_void_p] = []
    try:
        folder = _pidl(parent)
        owned.append(folder)
        children = []
        for f in files:
            if str(f.parent) != parent:
                continue
            item = _pidl(os.fspath(f))
            owned.append(item)
            children.append(_shell32.ILFindLastID(item))
        array = (ctypes.c_void_p * len(children))(*children)
        hr = _shell32.SHOpenFolderAndSelectItems(folder, len(children), array, 0) & 0xFFFFFFFF
        if hr != 0:
            raise OSError(f"Explorer could not open {parent} (0x{hr:08X})")
    finally:
        for p in owned:
            _ole32.CoTaskMemFree(p)
        _ole32.CoUninitialize()
