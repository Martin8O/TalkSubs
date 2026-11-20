"""Which Windows account is on the other end of a connection to the app server?

Host and Origin checks stop web pages, but any program on this computer can send any header - including a
program of ANOTHER Windows account signed in at the same time (fast user switching). So every connection is
traced to the process that opened it (the system's TCP table names its process id) and that process must run as
the same Windows user as the server. Anything that cannot be traced - an unknown port, a process we may not open,
a lookup error - counts as a stranger.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import socket
from collections.abc import Mapping
from functools import cache
from typing import Any

_AF_INET = 2
_TCP_TABLE_OWNER_PID_LISTENER = 3
_TCP_TABLE_OWNER_PID_CONNECTIONS = 4
_NO_ERROR = 0
_ERROR_INSUFFICIENT_BUFFER = 122
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_TOKEN_QUERY = 0x0008
_TOKEN_USER = 1
_LOOPBACK = "127.0.0.1"


class _Row(ctypes.Structure):  # MIB_TCPROW_OWNER_PID
    _fields_ = [("state", wt.DWORD), ("local_addr", wt.DWORD), ("local_port", wt.DWORD),
                ("remote_addr", wt.DWORD), ("remote_port", wt.DWORD), ("pid", wt.DWORD)]  # fmt: skip


@cache
def _api() -> tuple[Any, Any, Any]:
    iphlpapi = ctypes.WinDLL("iphlpapi")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    iphlpapi.GetExtendedTcpTable.argtypes = [ctypes.c_void_p, ctypes.POINTER(wt.DWORD), wt.BOOL, wt.ULONG,
                                             ctypes.c_int, wt.ULONG]  # fmt: skip
    iphlpapi.GetExtendedTcpTable.restype = wt.DWORD
    kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    kernel32.OpenProcess.restype = wt.HANDLE
    kernel32.GetCurrentProcess.restype = wt.HANDLE
    kernel32.CloseHandle.argtypes = [wt.HANDLE]
    advapi32.OpenProcessToken.argtypes = [wt.HANDLE, wt.DWORD, ctypes.POINTER(wt.HANDLE)]
    advapi32.GetTokenInformation.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD,
                                             ctypes.POINTER(wt.DWORD)]  # fmt: skip
    advapi32.GetLengthSid.argtypes = [ctypes.c_void_p]
    advapi32.GetLengthSid.restype = wt.DWORD
    advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wt.LPWSTR)]
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    return iphlpapi, kernel32, advapi32


def _port(dw: int) -> int:
    return socket.ntohs(dw & 0xFFFF)


def _addr(dw: int) -> str:
    return socket.inet_ntoa(int(dw).to_bytes(4, "little"))


def _rows(table: int) -> list[_Row] | None:
    """The system's IPv4 TCP table of the given kind; None when it cannot be read."""
    iphlpapi, _, _ = _api()
    size = wt.DWORD(0)
    for _ in range(4):  # the table can grow between the size query and the read
        buf = ctypes.create_string_buffer(max(size.value, 4))
        rc = iphlpapi.GetExtendedTcpTable(buf, ctypes.byref(size), False, _AF_INET, table, 0)
        if rc == _NO_ERROR:
            break
        if rc != _ERROR_INSUFFICIENT_BUFFER:
            return None
    else:
        return None
    count = ctypes.cast(buf, ctypes.POINTER(wt.DWORD))[0]
    rows = ctypes.cast(ctypes.addressof(buf) + ctypes.sizeof(wt.DWORD), ctypes.POINTER(_Row * count)).contents
    return [_Row.from_buffer_copy(r) for r in rows]


def listener_pids(port: int) -> set[int] | None:
    """Every process listening on this port on 127.0.0.1 or on all addresses; None when the table cannot be read."""
    rows = _rows(_TCP_TABLE_OWNER_PID_LISTENER)
    if rows is None:
        return None
    return {int(r.pid) for r in rows if _port(r.local_port) == port and _addr(r.local_addr) in (_LOOPBACK, "0.0.0.0")}


def listener_is_ours(port: int) -> bool:
    """True only when everything listening on the port runs as this Windows user (another account signed in at the
    same time could take the port while the app is closed and serve its own page to our window)."""
    try:
        pids = listener_pids(port)
        if not pids:
            return False
        return all(user_sid_of(pid) == my_sid() for pid in pids)
    except Exception:  # noqa: BLE001 - when in doubt it is not ours
        return False


def owner_pid(client_port: int, server_port: int) -> int | None:
    """The process that holds the client end of 127.0.0.1:client_port -> 127.0.0.1:server_port, if any."""
    rows = _rows(_TCP_TABLE_OWNER_PID_CONNECTIONS)
    if rows is None:
        return None
    for r in rows:
        if (_port(r.local_port) == client_port and _port(r.remote_port) == server_port
                and _addr(r.local_addr) == _LOOPBACK and _addr(r.remote_addr) == _LOOPBACK):  # fmt: skip
            return int(r.pid)
    return None


def _token_sid(token: wt.HANDLE) -> bytes | None:
    _, _, advapi32 = _api()
    need = wt.DWORD(0)
    advapi32.GetTokenInformation(token, _TOKEN_USER, None, 0, ctypes.byref(need))
    if not need.value:
        return None
    buf = ctypes.create_string_buffer(need.value)
    if not advapi32.GetTokenInformation(token, _TOKEN_USER, buf, need, ctypes.byref(need)):
        return None
    psid = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]  # TOKEN_USER starts with the SID pointer
    return ctypes.string_at(psid, advapi32.GetLengthSid(psid))


def user_sid_of(pid: int) -> bytes | None:
    """The Windows user (SID bytes) a process runs as; None when we may not look (another account's process)."""
    _, kernel32, advapi32 = _api()
    process = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not process:
        return None
    try:
        token = wt.HANDLE()
        if not advapi32.OpenProcessToken(process, _TOKEN_QUERY, ctypes.byref(token)):
            return None
        try:
            return _token_sid(token)
        finally:
            kernel32.CloseHandle(token)
    finally:
        kernel32.CloseHandle(process)


@cache
def my_sid() -> bytes:
    _, kernel32, advapi32 = _api()
    token = wt.HANDLE()
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), _TOKEN_QUERY, ctypes.byref(token)):
        raise OSError(ctypes.get_last_error(), "cannot read this process's own user")
    try:
        sid = _token_sid(token)
    finally:
        kernel32.CloseHandle(token)
    if sid is None:
        raise OSError("cannot read this process's own user")
    return sid


def my_sid_string() -> str:
    """This process's user as "S-1-5-21-..." (for access lists)."""
    _, kernel32, advapi32 = _api()
    raw = ctypes.create_string_buffer(my_sid())
    out = wt.LPWSTR()
    if not advapi32.ConvertSidToStringSidW(raw, ctypes.byref(out)):
        raise OSError(ctypes.get_last_error(), "cannot name this process's own user")
    try:
        return str(out.value)
    finally:
        kernel32.LocalFree(out)


def connection_is_ours(scope: Mapping[str, Any]) -> bool:
    """True only when the ASGI connection comes from a process of this server's own Windows user."""
    try:
        client, server = scope.get("client"), scope.get("server")
        if not client or not server or client[0] != _LOOPBACK or server[0] != _LOOPBACK:
            return False
        pid = owner_pid(int(client[1]), int(server[1]))
        if pid is None:
            return False
        if pid == os.getpid():
            return True
        sid = user_sid_of(pid)
        # the same process must still hold the connection (a process id can be reused in between)
        return sid is not None and sid == my_sid() and owner_pid(int(client[1]), int(server[1])) == pid
    except Exception:  # noqa: BLE001 - whatever goes wrong, a stranger is refused, never let in
        return False
