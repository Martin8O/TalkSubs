"""Folders the app keeps work in are readable only by the user who runs it (plus the system and administrators).

A folder made on a data drive inherits that drive's access list - on a typical second drive that lets every
signed-in account read and change it. The folder's list is replaced by exactly three full-control entries that
its contents inherit, and inheritance from above is cut. Set from a fixed description (SDDL) with SIDs, so it
does not depend on the language Windows uses for group names.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
from functools import cache
from pathlib import Path
from typing import Any

from parakeet_stt.server import owner

_SE_FILE_OBJECT = 1
_DACL_SECURITY_INFORMATION = 0x4
_PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000
_SDDL_REVISION_1 = 1
SYSTEM, ADMINISTRATORS = "SY", "BA"  # SDDL names for S-1-5-18 and S-1-5-32-544


class AccessError(OSError):
    """The folder's access list could not be set; the message is for the log."""


@cache
def _api() -> tuple[Any, Any]:
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wt.LPCWSTR,
        wt.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wt.ULONG),
    ]
    advapi32.GetSecurityDescriptorDacl.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(wt.BOOL),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wt.BOOL),
    ]
    advapi32.SetNamedSecurityInfoW.argtypes = [
        wt.LPWSTR,
        ctypes.c_int,
        wt.DWORD,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    advapi32.SetNamedSecurityInfoW.restype = wt.DWORD
    advapi32.GetNamedSecurityInfoW.argtypes = [
        wt.LPCWSTR,
        ctypes.c_int,
        wt.DWORD,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi32.GetNamedSecurityInfoW.restype = wt.DWORD
    advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [
        ctypes.c_void_p,
        wt.DWORD,
        wt.DWORD,
        ctypes.POINTER(wt.LPWSTR),
        ctypes.POINTER(wt.ULONG),
    ]
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    return advapi32, kernel32


def wanted_sddl() -> str:
    """Protected list: full control for this user, the system and administrators, inherited by everything inside."""
    return f"D:P(A;OICI;FA;;;{owner.my_sid_string()})(A;OICI;FA;;;{SYSTEM})(A;OICI;FA;;;{ADMINISTRATORS})"


def lock_to_user(folder: Path) -> None:
    """Give `folder` (created if missing) exactly the wanted list; its contents follow by inheritance."""
    folder.mkdir(parents=True, exist_ok=True)
    advapi32, kernel32 = _api()
    sd = ctypes.c_void_p()
    if not advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        wanted_sddl(), _SDDL_REVISION_1, ctypes.byref(sd), None
    ):
        raise AccessError(ctypes.get_last_error(), "the access description was not accepted")
    try:
        present, defaulted = wt.BOOL(), wt.BOOL()
        dacl = ctypes.c_void_p()
        if not advapi32.GetSecurityDescriptorDacl(
            sd, ctypes.byref(present), ctypes.byref(dacl), ctypes.byref(defaulted)
        ):
            raise AccessError(ctypes.get_last_error(), "the access list could not be read back")
        rc = advapi32.SetNamedSecurityInfoW(
            str(folder),
            _SE_FILE_OBJECT,
            _DACL_SECURITY_INFORMATION | _PROTECTED_DACL_SECURITY_INFORMATION,
            None,
            None,
            dacl,
            None,
        )
        if rc != 0:
            raise AccessError(rc, f"the access list of {folder} could not be set")
    finally:
        kernel32.LocalFree(sd)


MARKER = ".parakeet-stt"
_APP_ENTRIES = frozenset({"queue.json", "settings.json", "jobs", "uploads", "logs", "subtitles", MARKER,
                          "key.dpapi", "key.dpapi.bak"})  # fmt: skip


def lock_app_folder(folder: Path, *, known: bool = False) -> bool:
    """Lock a folder the app owns; refuse a drive root or a folder full of things that are not the app's.

    `known`: the folder is the app's by where it lies (inside the application folder), whatever it holds.

    Returns whether the list was (re)set; an already correct folder is left alone, so nothing is rewritten at
    every start.
    """
    folder = folder.resolve()
    if folder.parent == folder:
        raise AccessError(f"{folder} is a whole drive; give the app a folder of its own")
    folder.mkdir(parents=True, exist_ok=True)
    present = {p.name for p in folder.iterdir()}
    if not known and present - _APP_ENTRIES and not (folder / MARKER).exists() and not present & {"queue.json", "jobs"}:
        raise AccessError(f"{folder} holds files that are not the app's; its access list is left as it is")
    (folder / MARKER).touch()
    if is_locked(folder):
        return False
    lock_to_user(folder)
    return True


def is_locked(folder: Path) -> bool:
    sddl = current_sddl(folder)
    return sddl.startswith("D:P") and trustees(sddl) == {owner.my_sid_string(), SYSTEM, ADMINISTRATORS}


def current_sddl(path: Path) -> str:
    """The access list of a file or folder as SDDL text (for checks and tests)."""
    advapi32, kernel32 = _api()
    sd = ctypes.c_void_p()
    rc = advapi32.GetNamedSecurityInfoW(
        str(path), _SE_FILE_OBJECT, _DACL_SECURITY_INFORMATION, None, None, None, None, ctypes.byref(sd)
    )
    if rc != 0:
        raise AccessError(rc, f"the access list of {path} could not be read")
    try:
        text = wt.LPWSTR()
        if not advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW(
            sd, _SDDL_REVISION_1, _DACL_SECURITY_INFORMATION, ctypes.byref(text), None
        ):
            raise AccessError(ctypes.get_last_error(), f"the access list of {path} could not be described")
        try:
            return str(text.value)
        finally:
            kernel32.LocalFree(text)
    finally:
        kernel32.LocalFree(sd)


def trustees(sddl: str) -> set[str]:
    """Who an SDDL access list grants anything to (the last field of every ACE)."""
    return {ace.rstrip(")").split(";")[-1] for ace in sddl.split("(")[1:]}
