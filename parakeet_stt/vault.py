"""The app's own encrypted store: every file the app keeps for itself under its data folder is sealed.

Key: one random 256-bit data key per data folder, wrapped by Windows DPAPI for the current user
(`key.dpapi`, an identical copy in `key.dpapi.bak`). Every process (app server, worker, CLI) unwraps it itself;
the key never crosses a pipe, a command line, a log or a file. Only the app server creates it.

File format "PSTE" v1 (the shape of Tink's streaming AES-GCM-HKDF):
- header, 48 bytes, plain, also the associated data of every chunk:
  b"PSTE" | version 1 | role | chunk_log2 (16) | 0 | salt (32) | nonce prefix (7) | 0
- per-file key = HKDF-SHA256(data key, salt, info = b"pstt-v1/" + role name)
- chunks of up to 64 KiB plain text + 16-byte tag; nonce = prefix | chunk number (u32, big-endian) | last flag.
  Only the final chunk carries the flag (an empty file is one empty final chunk), so a truncated, reordered or
  spliced file fails to open. The plain length follows from the file size.
A sealed file is never changed in place: every write is a new file with a new salt, swapped in whole.

The threat this answers: another Windows account, a copied, synced or backed-up data folder, and any program that
just reads files. Not malware running as the same Windows user - it could ask DPAPI as well.
"""

from __future__ import annotations

import contextlib
import ctypes
import enum
import os
import secrets
import struct
import threading
import time
from collections import OrderedDict
from collections.abc import Iterator
from functools import cache
from pathlib import Path
from typing import Final

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

MAGIC: Final = b"PSTE"
VERSION: Final = 1
CHUNK_LOG2: Final = 16
CHUNK: Final = 1 << CHUNK_LOG2  # plain bytes per chunk
TAG: Final = 16
SEALED_CHUNK: Final = CHUNK + TAG
HEADER: Final = 48
_SALT: Final = 32
_PREFIX: Final = 7
_HEADER_FMT: Final = f"<4sBBBB{_SALT}s{_PREFIX}sB"

KEY_FILE: Final = "key.dpapi"
KEY_BACKUP: Final = "key.dpapi.bak"
_KEY_MAGIC: Final = b"PSTK\x01"
_ENTROPY: Final = b"pstt-v1"
_DESCRIPTION: Final = "Parakeet STT data key"
_CACHED_CHUNKS: Final = 4
_REPLACE_TRIES: Final = 10
_REPLACE_WAIT_S: Final = 0.1

LOCKED_MESSAGE: Final = (
    "The work kept in this app was locked by another Windows account or on another computer, "
    "so it cannot be opened here."
)
DAMAGED_MESSAGE: Final = "This file of the app is damaged or was locked by another Windows account."


class Role(enum.IntEnum):
    """What a sealed file holds; part of its key, so one kind of file cannot be passed off as another."""

    QUEUE = 1
    SETTINGS = 2
    PROJECT = 3
    SOURCE = 4  # a copy of a dropped (later: downloaded) media file
    PREVIEW_MEDIA = 5
    PREVIEW_PEAKS = 6
    PREVIEW_INFO = 7
    LINK = 8  # the full link of a link job (its signature is a secret)
    CHECKPOINT = 9  # a running job's recognised words so far (checkpoint.py)
    SPEED = 10  # how fast this PC transcribes (speed.py)


class VaultError(Exception):
    """A sealed file cannot be opened; the message is for the user."""


class VaultLocked(VaultError):
    """The data key is missing or belongs to another Windows account or computer."""


# ---- DPAPI (current user) -----------------------------------------------------------------------------------


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_char))]


@cache
def _dpapi() -> tuple[ctypes.WinDLL, ctypes.WinDLL]:
    from ctypes import wintypes as wt

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    blob = ctypes.POINTER(_Blob)
    crypt32.CryptProtectData.argtypes = [blob, wt.LPCWSTR, blob, ctypes.c_void_p, ctypes.c_void_p, wt.DWORD, blob]
    crypt32.CryptProtectData.restype = wt.BOOL
    crypt32.CryptUnprotectData.argtypes = [blob, ctypes.c_void_p, blob, ctypes.c_void_p, ctypes.c_void_p,
                                           wt.DWORD, blob]  # fmt: skip
    crypt32.CryptUnprotectData.restype = wt.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    return crypt32, kernel32


_UI_FORBIDDEN: Final = 0x1


def _in(data: bytes) -> tuple[_Blob, ctypes.Array[ctypes.c_char]]:
    buf = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def _out(blob: _Blob) -> bytes:
    _, kernel32 = _dpapi()
    try:
        return ctypes.string_at(blob.pbData, blob.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(blob.pbData, ctypes.c_void_p))


def dpapi_protect(data: bytes, entropy: bytes = _ENTROPY) -> bytes:
    crypt32, _ = _dpapi()
    src, _keep1 = _in(data)
    ent, _keep2 = _in(entropy)
    dst = _Blob()
    if not crypt32.CryptProtectData(ctypes.byref(src), _DESCRIPTION, ctypes.byref(ent), None, None, _UI_FORBIDDEN,
                                    ctypes.byref(dst)):  # fmt: skip
        raise ctypes.WinError(ctypes.get_last_error())
    return _out(dst)


def dpapi_unprotect(blob: bytes, entropy: bytes = _ENTROPY) -> bytes:
    crypt32, _ = _dpapi()
    src, _keep1 = _in(blob)
    ent, _keep2 = _in(entropy)
    dst = _Blob()
    if not crypt32.CryptUnprotectData(ctypes.byref(src), None, ctypes.byref(ent), None, None, _UI_FORBIDDEN,
                                      ctypes.byref(dst)):  # fmt: skip
        raise ctypes.WinError(ctypes.get_last_error())
    return _out(dst)


# ---- the key file -------------------------------------------------------------------------------------------


def _unwrap(raw: bytes, entropy: bytes) -> bytes:
    if not raw.startswith(_KEY_MAGIC):
        raise VaultLocked(LOCKED_MESSAGE)
    try:
        key = dpapi_unprotect(raw[len(_KEY_MAGIC) :], entropy)
    except OSError:
        raise VaultLocked(LOCKED_MESSAGE) from None
    if len(key) != 32:
        raise VaultLocked(LOCKED_MESSAGE)
    return key


def _write_new(path: Path, data: bytes) -> None:
    """Create `path` with `data`, never replacing a file that is there (os.rename refuses on Windows)."""
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.new")
    try:
        with tmp.open("xb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.rename(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def key_exists(data_dir: Path) -> bool:
    return (data_dir / KEY_FILE).is_file() or (data_dir / KEY_BACKUP).is_file()


def _read_key(data_dir: Path, entropy: bytes, *, repair: bool) -> bytes:
    main, backup = data_dir / KEY_FILE, data_dir / KEY_BACKUP
    texts = []
    for p in (main, backup):
        with contextlib.suppress(FileNotFoundError):
            texts.append((p, p.read_bytes()))
    if not texts:
        raise VaultLocked(LOCKED_MESSAGE)
    last: VaultLocked | None = None
    for p, raw in texts:
        try:
            key = _unwrap(raw, entropy)
        except VaultLocked as exc:
            last = exc
            continue
        if repair:  # one of the two copies is missing (deleted by hand): make it again from the good one
            for other in (main, backup):
                if other != p and not other.is_file():
                    with contextlib.suppress(OSError):
                        _write_new(other, raw)
        return key
    assert last is not None
    raise last


# ---- the format ---------------------------------------------------------------------------------------------


def _header(role: Role) -> bytes:
    return struct.pack(_HEADER_FMT, MAGIC, VERSION, int(role), CHUNK_LOG2, 0, secrets.token_bytes(_SALT),
                       secrets.token_bytes(_PREFIX), 0)  # fmt: skip


def _parse_header(header: bytes, role: Role) -> tuple[bytes, bytes]:
    """(salt, nonce prefix) of a header for `role`; anything unexpected is a damaged file."""
    if len(header) != HEADER:
        raise VaultError(DAMAGED_MESSAGE)
    magic, version, got_role, chunk_log2, reserved, salt, prefix, pad = struct.unpack(_HEADER_FMT, header)
    if magic != MAGIC or version != VERSION or chunk_log2 != CHUNK_LOG2 or reserved or pad or got_role != role:
        raise VaultError(DAMAGED_MESSAGE)
    return salt, prefix


def _nonce(prefix: bytes, index: int, last: bool) -> bytes:
    return prefix + struct.pack(">IB", index, 1 if last else 0)


def _layout(sealed_size: int) -> tuple[int, int, int]:
    """(chunk count, sealed length of the final chunk, plain size) of a sealed file of this size."""
    body = sealed_size - HEADER
    if body < TAG:
        raise VaultError(DAMAGED_MESSAGE)
    count = max(1, -(-body // SEALED_CHUNK))
    last = body - (count - 1) * SEALED_CHUNK
    if not TAG <= last <= SEALED_CHUNK or count > 0xFFFFFFFF:
        raise VaultError(DAMAGED_MESSAGE)
    return count, last, body - count * TAG


def is_sealed(path: Path) -> bool:
    """Does the file start like a sealed file? (Says nothing about whether it opens.)"""
    try:
        with path.open("rb") as fh:
            return fh.read(len(MAGIC)) == MAGIC
    except OSError:
        return False


def sealed_bytes(raw: bytes) -> bool:
    return raw[: len(MAGIC)] == MAGIC


def replace_retrying(tmp: Path, path: Path) -> None:
    """os.replace, retried: on Windows a reader (indexer, antivirus, a player) can hold the target for a moment."""
    for attempt in range(_REPLACE_TRIES):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == _REPLACE_TRIES - 1:
                raise
            time.sleep(_REPLACE_WAIT_S)


class Vault:
    """The key of one data folder. Get one with `Vault.create_or_open` (app server) or `Vault.open` (others)."""

    def __init__(self, data_dir: Path, key: bytes) -> None:
        self.data_dir = data_dir
        self._key = key

    @classmethod
    def open(cls, data_dir: Path, *, entropy: bytes = _ENTROPY) -> Vault:
        """The existing key; VaultLocked when there is none or it is not this Windows account's."""
        return cls(data_dir, _read_key(data_dir, entropy, repair=False))

    @classmethod
    def create_or_open(cls, data_dir: Path, *, entropy: bytes = _ENTROPY) -> Vault:
        """The key of the folder, made now when the folder has none. Only the app server calls this."""
        data_dir.mkdir(parents=True, exist_ok=True)
        if not key_exists(data_dir):
            raw = _KEY_MAGIC + dpapi_protect(secrets.token_bytes(32), entropy)
            with contextlib.suppress(FileExistsError):  # another start won the race: use its key
                _write_new(data_dir / KEY_FILE, raw)
        return cls(data_dir, _read_key(data_dir, entropy, repair=True))

    def _file_key(self, salt: bytes, role: Role) -> AESGCM:
        hkdf = HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=b"pstt-v1/" + role.name.encode())
        return AESGCM(hkdf.derive(self._key))

    # ---- whole files in memory (small: queue, settings, project files, preview facts) ----

    def seal(self, data: bytes, role: Role) -> bytes:
        header = _header(role)
        salt, prefix = _parse_header(header, role)
        aead = self._file_key(salt, role)
        out = [header]
        count = max(1, -(-len(data) // CHUNK))
        for i in range(count):
            out.append(aead.encrypt(_nonce(prefix, i, i == count - 1), data[i * CHUNK : (i + 1) * CHUNK], header))
        return b"".join(out)

    def unseal(self, raw: bytes, role: Role) -> bytes:
        if not sealed_bytes(raw):
            raise VaultError(DAMAGED_MESSAGE)
        header = raw[:HEADER]
        salt, prefix = _parse_header(header, role)
        count, _, _ = _layout(len(raw))
        aead = self._file_key(salt, role)
        parts = []
        try:
            for i in range(count):
                start = HEADER + i * SEALED_CHUNK
                parts.append(aead.decrypt(_nonce(prefix, i, i == count - 1), raw[start : start + SEALED_CHUNK],
                                          header))  # fmt: skip
        except InvalidTag:
            raise VaultError(DAMAGED_MESSAGE) from None
        return b"".join(parts)

    def read(self, path: Path, role: Role) -> bytes:
        """The plain content of a sealed file. A plain file is refused: the app only ever writes sealed ones."""
        return self.unseal(path.read_bytes(), role)

    # ---- streams (large: media copies, the preview) ----

    @contextlib.contextmanager
    def writer(self, path: Path, role: Role) -> Iterator[SealedWriter]:
        """Write a sealed file in pieces; it appears under `path` only when the block ends without an error."""
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_name(path.name + ".part")
        w = SealedWriter(part, self, role)
        try:
            yield w
            w.close()
            replace_retrying(part, path)
        finally:
            w.abort()
            part.unlink(missing_ok=True)

    def reader(self, path: Path, role: Role) -> SealedReader:
        return SealedReader(path, self, role)

    def plain_size(self, path: Path) -> int:
        return _layout(path.stat().st_size)[2]


class SealedWriter:
    def __init__(self, path: Path, vault: Vault, role: Role) -> None:
        self._fh = path.open("wb")
        self._header = _header(role)
        salt, self._prefix = _parse_header(self._header, role)
        self._aead = vault._file_key(salt, role)
        self._buf = bytearray()
        self._index = 0
        self._closed = False
        self.size = 0  # plain bytes written so far
        self._fh.write(self._header)

    def write(self, data: bytes | bytearray | memoryview) -> int:
        self._buf += data
        self.size += len(data)
        # a chunk is written only once more data follows, so the final chunk can carry the last flag
        while len(self._buf) > CHUNK:
            self._emit(bytes(self._buf[:CHUNK]), last=False)
            del self._buf[:CHUNK]
        return len(data)

    def _emit(self, plain: bytes, *, last: bool) -> None:
        self._fh.write(self._aead.encrypt(_nonce(self._prefix, self._index, last), plain, self._header))
        self._index += 1

    def close(self) -> None:
        if self._closed:
            return
        self._emit(bytes(self._buf), last=True)
        self._buf.clear()
        self._fh.flush()
        os.fsync(self._fh.fileno())
        self._fh.close()
        self._closed = True

    def abort(self) -> None:
        if not self._closed:
            self._closed = True
            self._fh.close()


class SealedReader:
    """A read-only, seekable view of a sealed file's plain bytes; decrypts only the chunks it touches."""

    def __init__(self, path: Path, vault: Vault, role: Role) -> None:
        self._fh = path.open("rb")
        try:
            self._header = self._fh.read(HEADER)
            if not sealed_bytes(self._header):
                raise VaultError(DAMAGED_MESSAGE)
            salt, self._prefix = _parse_header(self._header, role)
            self._count, self._last_len, self.size = _layout(os.fstat(self._fh.fileno()).st_size)
            self._aead = vault._file_key(salt, role)
        except BaseException:
            self._fh.close()
            raise
        self._cache: OrderedDict[int, bytes] = OrderedDict()
        self._lock = threading.Lock()
        self._pos = 0

    def __enter__(self) -> SealedReader:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._fh.close()

    def _chunk(self, index: int) -> bytes:
        found = self._cache.get(index)
        if found is not None:
            self._cache.move_to_end(index)
            return found
        last = index == self._count - 1
        self._fh.seek(HEADER + index * SEALED_CHUNK)
        sealed = self._fh.read(self._last_len if last else SEALED_CHUNK)
        try:
            plain = self._aead.decrypt(_nonce(self._prefix, index, last), sealed, self._header)
        except InvalidTag:
            raise VaultError(DAMAGED_MESSAGE) from None
        self._cache[index] = plain
        if len(self._cache) > _CACHED_CHUNKS:
            self._cache.popitem(last=False)
        return plain

    def read_at(self, offset: int, n: int) -> bytes:
        """Up to `n` plain bytes from `offset` (fewer only at the end of the file)."""
        end = min(self.size, offset + max(n, 0))
        if offset < 0 or offset >= end:
            return b""
        out = bytearray()
        with self._lock:
            pos = offset
            while pos < end:
                index, inner = divmod(pos, CHUNK)
                piece = self._chunk(index)[inner : inner + (end - pos)]
                out += piece
                pos += len(piece)
        return bytes(out)

    def read_all(self) -> bytes:
        return self.read_at(0, self.size)

    # a minimal file-like face (read / seek / tell) for code that wants a stream
    def read(self, n: int = -1) -> bytes:
        data = self.read_at(self._pos, self.size - self._pos if n < 0 else n)
        self._pos += len(data)
        return data

    def seek(self, offset: int, whence: int = 0) -> int:
        base = {0: 0, 1: self._pos, 2: self.size}[whence]
        self._pos = max(0, base + offset)
        return self._pos

    def tell(self) -> int:
        return self._pos


PLAIN_EXPORT_DIR: Final = "subtitles"  # the one folder inside a data folder that holds plain files on purpose


def store_of(path: Path) -> Path | None:
    """The data folder (a folder with a key) that is `path` itself or holds it, or None."""
    target = path.resolve()
    for folder in (target, *target.parents):
        if key_exists(folder):
            return folder
    return None


def plain_allowed(folder: Path, data_dir: Path | None = None) -> bool:
    """May plain files (exports) be written into `folder`? Anywhere but inside a data folder of the app, where
    only its own export folder is plain on purpose; everything else there must stay sealed."""
    target = folder.resolve()
    for store in (data_dir.resolve() if data_dir is not None else None, store_of(target)):
        if store is None or not target.is_relative_to(store):
            continue
        if target == store or not target.is_relative_to(store / PLAIN_EXPORT_DIR):
            return False
    return True


def locate(path: Path) -> Vault | None:
    """The vault of the data folder that holds `path` (the CLI reading a project of the app), or None."""
    for folder in path.resolve().parents:
        if key_exists(folder):
            return Vault.open(folder)
    return None
