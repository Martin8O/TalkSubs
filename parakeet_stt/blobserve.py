"""Let ffmpeg and ffprobe read a sealed media file without a plain copy ever touching the disk.

The process that runs ffmpeg (worker, preview maker, the app checking a dropped file) serves that one file
over HTTP on 127.0.0.1, at a fresh random address, for as long as the run lasts, decrypting the byte ranges
ffmpeg asks for. Only a process of this Windows account may connect; nothing is logged.
"""

from __future__ import annotations

import contextlib
import hmac
import re
import secrets
import socket
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Final

from parakeet_stt.vault import Role, Vault, VaultError, is_sealed

WINDOW: Final = 256 * 1024  # the most one ranged answer carries (see _Handler._answer)
_HOST: Final = "127.0.0.1"
PeerCheck = Callable[[tuple[str, int], tuple[str, int]], bool]
_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")
# what ffmpeg needs to read an HTTP input (it keeps one connection open and asks for ranges on it)
FFMPEG_INPUT_OPTIONS: Final = ("-multiple_requests", "1", "-protocol_whitelist", "http,tcp")


# single-file containers a lecture link may hold (the endings uploads accept); playlists (HLS, DASH, concat) are
# left out so a link cannot make ffmpeg fetch further addresses of the sender's choosing
REMOTE_FORMATS: Final = "mov,matroska,webm,mp3,wav,flac,ogg,aac,asf,avi,mpegts,mpeg"


def remote_input_options(*, allow_http: bool = False) -> tuple[str, ...]:
    """How ffmpeg opens a download link far away: survive a dropped connection or a stall; https only (a redirect to
    plain http is refused) with the site's certificate checked against the Windows store (ffmpeg's TLS accepts any
    certificate unless told otherwise), one media file only; plain http only for the tests' own local server."""
    protocols = "https,tls,tcp,http" if allow_http else "https,tls,tcp"
    # (ffmpeg refuses an option no part of the input used: the tests' plain-http server has no TLS to verify)
    verify = () if allow_http else ("-tls_verify", "1")
    return ("-reconnect", "1", "-reconnect_on_network_error", "1", "-reconnect_delay_max", "30",
            "-rw_timeout", "60000000", "-protocol_whitelist", protocols, *verify,
            "-format_whitelist", REMOTE_FORMATS)  # fmt: skip


def parse_range(header: str | None, size: int) -> tuple[int, int] | bool | None:  # noqa: PLR0911 - one return per case
    """The first byte range of a Range header as (start, end inclusive); None = the whole file;
    False = the range starts past the end (416). A header we do not understand means the whole file."""
    if not header:
        return None
    m = _RANGE.match(header.split(",", 1)[0].strip())
    if not m or (not m.group(1) and not m.group(2)):
        return None
    if size == 0:
        return False  # nothing to give: every range of an empty file is unsatisfiable
    if not m.group(1):  # the last n bytes
        n = int(m.group(2))
        if n == 0:
            return False
        return max(0, size - n), size - 1
    start = int(m.group(1))
    end = int(m.group(2)) if m.group(2) else size - 1
    if start >= size:
        return False
    if end < start:
        return None
    return start, min(end, size - 1)


def _never() -> bool:
    return False


@dataclass(frozen=True)
class MediaInput:
    """Where ffmpeg reads a media file from: a plain path, or the address of the sealed file's server."""

    path: Path  # the file itself (its name is what messages show)
    url: str | None  # set when the file is sealed
    size_bytes: int  # plain length
    # true once the server could not hand out a part of the file (damage): ffmpeg then sees an early end and may
    # exit cleanly with a shortened result, so whoever reads through the server asks this when ffmpeg is done
    failed: Callable[[], bool] = field(default=_never, compare=False)
    options: tuple[str, ...] = FFMPEG_INPUT_OPTIONS  # how ffmpeg opens `url` (REMOTE_INPUT_OPTIONS for a link)

    @property
    def ref(self) -> str:
        return self.url or str(self.path)

    def input_args(self) -> list[str]:
        """ffmpeg/ffprobe arguments that open this input (`-i` included)."""
        return [*self.options, "-i", self.url] if self.url else ["-i", str(self.path)]

    def scrub(self, text: str) -> str:
        """An ffmpeg message with the server's address replaced by the file's name."""
        return text.replace(self.url, self.path.name) if self.url else text


def _peer_is_ours(client: tuple[str, int], server: tuple[str, int]) -> bool:
    from parakeet_stt.server import owner

    return owner.connection_is_ours({"client": client, "server": server})


class _Handler(BaseHTTPRequestHandler):
    """One instance per connection; ffmpeg keeps one connection and asks for range after range on it."""

    protocol_version = "HTTP/1.1"
    server: _BlobServer
    timeout = 120
    _ours: bool | None = None  # the owner of a connection cannot change: checked once, on its first request

    def log_message(self, format: str, *args: object) -> None:
        pass  # the address is a secret for the run's duration: never logged

    def do_HEAD(self) -> None:
        self._answer(body=False)

    def do_GET(self) -> None:
        self._answer(body=True)

    def _plain(self, status: int, extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _answer(self, *, body: bool) -> None:
        srv = self.server
        if not hmac.compare_digest(self.path.encode(), srv.wanted_path.encode()):
            self._plain(404)
            return
        if self._ours is None:
            self._ours = srv.peer_check((str(self.client_address[0]), int(self.client_address[1])), (_HOST, srv.port))
        if not self._ours:
            self._plain(403)
            self.close_connection = True
            return
        size = srv.reader.size
        rng = parse_range(self.headers.get("Range"), size)
        if rng is False:
            self._plain(416, {"Content-Range": f"bytes */{size}"})
            return
        if isinstance(rng, tuple):
            # every ranged answer is one window, never "to the end": ffmpeg keeps its connection and, to seek,
            # reads away the rest of the current answer first, so an open end would cost the rest of the file per
            # seek (thousands of seeks where sound and picture lie far apart). It asks again where a window ends.
            self._send(rng[0], min(rng[1], rng[0] + WINDOW - 1), ranged=True, body=body)
        else:
            self._send(0, size - 1, ranged=False, body=body)

    def _send(self, start: int, end: int, *, ranged: bool, body: bool) -> None:
        srv = self.server
        reader, size = srv.reader, srv.reader.size
        length = max(0, end - start + 1)
        try:
            first = reader.read_at(start, min(length, WINDOW)) if body else b""
        except VaultError:
            srv.failures += 1
            self._plain(500)
            self.close_connection = True
            return
        self.send_response(206 if ranged else 200)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(length))
        if ranged:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if not body:
            return
        try:
            self.wfile.write(first)
            pos = start + len(first)
            while pos <= end:
                piece = reader.read_at(pos, min(WINDOW, end - pos + 1))
                if not piece:
                    break
                self.wfile.write(piece)
                pos += len(piece)
        except ConnectionError:  # ffmpeg closed this one itself
            self.close_connection = True
        except Exception:  # noqa: BLE001 - damage, a stall, the run ending: ffmpeg must not take it for the end
            srv.failures += 1
            self.close_connection = True


class _BlobServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False  # nobody else may bind this port beside us (and receive ffmpeg's connection)

    def server_bind(self) -> None:
        self.socket.setsockopt(socket.SOL_SOCKET, getattr(socket, "SO_EXCLUSIVEADDRUSE", socket.SO_REUSEADDR), 1)
        super().server_bind()

    def handle_error(self, request: object, client_address: object) -> None:
        pass  # a request cut off by ffmpeg or the run ending is normal; tracebacks would only fill the log

    def __init__(self, vault: Vault, file: Path, role: Role, peer_check: PeerCheck) -> None:
        self.reader = vault.reader(file, role)  # one for every request: read_at is thread-safe
        try:
            super().__init__((_HOST, 0), _Handler)
        except BaseException:
            self.reader.close()
            raise
        self.peer_check = peer_check
        self.port = int(self.server_address[1])
        self.wanted_path = "/" + secrets.token_urlsafe(32)
        self.failures = 0


@contextlib.contextmanager
def _running(vault: Vault, file: Path, role: Role, peer_check: PeerCheck | None) -> Iterator[_BlobServer]:
    srv = _BlobServer(vault, file, role, peer_check or _peer_is_ours)
    thread = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True,
                              name="blob-server")  # fmt: skip
    thread.start()
    try:
        yield srv
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=5)
        srv.reader.close()


def _url(srv: _BlobServer) -> str:
    return f"http://{_HOST}:{srv.port}{srv.wanted_path}"  # an address, never "localhost" (IPv6 first)


@contextlib.contextmanager
def serve(vault: Vault, file: Path, role: Role = Role.SOURCE, *, peer_check: PeerCheck | None = None) -> Iterator[str]:
    """The address ffmpeg reads the sealed `file` from, valid until the block ends."""
    with _running(vault, file, role, peer_check) as srv:
        yield _url(srv)


@contextlib.contextmanager
def media_input(path: Path, vault: Vault | None) -> Iterator[MediaInput]:
    """How ffmpeg should read `path`: directly when it is a plain file, through `serve` when it is sealed."""
    if vault is None or not is_sealed(path):
        size = path.stat().st_size if path.is_file() else 0
        yield MediaInput(path, None, size)
        return
    with _running(vault, path, Role.SOURCE, None) as srv:
        yield MediaInput(path, _url(srv), srv.reader.size, failed=lambda: srv.failures > 0)
