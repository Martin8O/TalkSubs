"""Only this machine's own window may talk to the server.

Raw ASGI middleware, so the same checks cover HTTP and WebSocket (Starlette's HTTP middleware never sees a
WebSocket). A refusal happens before the request reaches the app or the connection is accepted.

- The connection must come from a program of the server's own Windows user (`owner.py`): headers can be forged
  by any local program, including one of another account signed in at the same time.
- Host must be exactly 127.0.0.1:<port> or localhost:<port> (defeats DNS rebinding).
- Origin, when sent, must be the app's own origin; "null" is refused (sandboxed frames, file pages).
- Sec-Fetch-Site, when sent, must be same-origin or none (another site cannot drive the app from a tab).
- Writes need the app's own Origin and a JSON body (a plain HTML form cannot send one); WebSockets need the Origin.
  The one exception is the upload route (PUT, exact path): raw bytes, still with the own Origin and a stated
  length within a limit measured on each request (free disk space).
- Transfer-Encoding is refused on every request: the stated length is the only framing, so the size checks hold.
- No CORS headers, ever. Every HTTP response carries headers that forbid framing and sniffing; every /api/
  answer also forbids storing it (the window's browser cache lives outside the app's data folder).
- Paths with a backslash, a colon or a ".." segment are refused before anything looks at the disk.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from parakeet_stt.server import owner

# The waveform component writes one fixed <style> block into its own shadow root; this hash allows exactly that
# text and nothing else inline (tests/test_page_csp.py recomputes it from the built page).
WAVEFORM_STYLE_HASH = "sha256-/wNXXQM7c2ofBcjrAABoSS5Ibbo1K6dLnomtT2ZNGIA="
DEFAULT_BODY_LIMIT = 16 * 1024  # bytes; routes that take documents get their own limit
_SAFE_METHODS = frozenset({"GET", "HEAD"})
_SITES_OK = frozenset({"same-origin", "none"})
_NO_STORE = (b"cache-control", b"no-store")
STRANGER = "This app only answers to programs of the Windows user who started it."


class LocalOnly:
    def __init__(
        self,
        app: ASGIApp,
        port: int,
        body_limits: Sequence[tuple[re.Pattern[str], int]] = (),
        *,
        upload_path: str | None = None,
        upload_limit: Callable[[], int] | None = None,
        upload_room: Callable[[], dict[str, int | None]] | None = None,
        peer_check: Callable[[Scope], bool] | None = None,
    ) -> None:
        self.app = app
        self.peer_check = peer_check  # None: the real Windows check (looked up per call, so tests can stand in)
        self.hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        self.body_limits = list(body_limits)  # (path pattern, limit); the first full match wins
        self.upload_path = upload_path
        self.upload_limit = upload_limit or (lambda: 0)
        self.upload_room = upload_room  # numbers a "no room" refusal carries, so the window can say how much
        csp = (
            "default-src 'self'; "
            f"style-src 'self' '{WAVEFORM_STYLE_HASH}'; "
            f"connect-src 'self' ws://127.0.0.1:{port} ws://localhost:{port}; "
            "img-src 'self' data: blob:; media-src 'self' blob:; object-src 'none'; "
            "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
        )
        self.headers = [
            (b"content-security-policy", csp.encode()),
            (b"x-frame-options", b"DENY"),
            (b"x-content-type-options", b"nosniff"),
            (b"referrer-policy", b"no-referrer"),
            (b"cross-origin-opener-policy", b"same-origin"),
            (b"cross-origin-resource-policy", b"same-origin"),
        ]

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        kind = scope["type"]
        if kind not in ("http", "websocket"):
            await self.app(scope, receive, send)  # lifespan
            return
        ours = self.peer_check(scope) if self.peer_check is not None else owner.connection_is_ours(scope)
        why, status = self._refusal(scope) if ours else (STRANGER, 403)
        if why is not None:
            if kind == "websocket":
                await send({"type": "websocket.close", "code": 1008, "reason": why})
            else:
                room = status == 413 and self.upload_room is not None and scope.get("path") == self.upload_path
                await self._refuse(send, status, why, self.upload_room() if room and self.upload_room else None)
            return
        if kind == "websocket":
            await self.app(scope, receive, send)
            return

        api = str(scope.get("path", "")).startswith("/api/")

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                if api:  # transcripts, media and settings must never land in the browser's disk cache
                    headers = [(k, v) for k, v in headers if k.lower() != b"cache-control"] + [_NO_STORE]
                message = {**message, "headers": [*headers, *self.headers]}
            await send(message)

        await self.app(scope, receive, send_with_headers)

    def _refusal(self, scope: Scope) -> tuple[str | None, int]:  # noqa: PLR0911, PLR0912 - one return per check reads best
        h = _headers(scope)
        if _FORWARDED & h.keys():  # a proxy header could rewrite who the connection seems to come from
            return "Requests through a proxy are not accepted.", 403
        host = h.get("host", "").lower()
        if host not in self.hosts:
            return "This server only answers to its own address.", 403
        origin = h.get("origin")
        own = f"http://{host}"
        if origin is not None and origin.lower() != own:
            return "Requests from other pages are not allowed.", 403
        site = h.get("sec-fetch-site")
        if site is not None and site.lower() not in _SITES_OK:
            return "Requests from other pages are not allowed.", 403
        if "transfer-encoding" in h:
            return "The request must state its length.", 411
        path: str = scope.get("path", "")
        if "\\" in path or ":" in path or ".." in path.split("/"):
            return "Bad path.", 400
        if scope["type"] == "websocket":
            if origin is None:
                return "Live updates are only for the app window.", 403
            return None, 0
        method = scope.get("method", "GET")
        if method in _SAFE_METHODS:
            return None, 0
        if origin is None:
            return "Changes are only accepted from the app window.", 403
        kind = h.get("content-type", "").split(";")[0].strip().lower()
        length = h.get("content-length")
        if length is None or not length.isdigit():
            return "The request must state its length.", 411
        if self.upload_path is not None and method == "PUT" and path == self.upload_path:
            return self._upload_refusal(kind, int(length))
        if kind != "application/json":
            return "Changes must be sent as JSON.", 415
        limit = next((n for pattern, n in self.body_limits if pattern.fullmatch(path)), DEFAULT_BODY_LIMIT)
        if int(length) > limit:
            return "The request is too large.", 413
        return None, 0

    def _upload_refusal(self, kind: str, length: int) -> tuple[str | None, int]:
        if kind != "application/octet-stream":
            return "Files must be sent as raw bytes.", 415
        if length == 0:
            return "The file is empty.", 400
        if length > self.upload_limit():
            return "There is not enough free disk space for a copy of this file.", 413
        return None, 0

    async def _refuse(self, send: Send, status: int, why: str, extra: dict[str, int | None] | None = None) -> None:
        body = json.dumps({"error": why, **(extra or {})}).encode()
        headers = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
                   _NO_STORE, *self.headers]  # fmt: skip
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})


_FORWARDED = frozenset({"forwarded", "x-forwarded-for", "x-forwarded-host", "x-forwarded-proto", "x-real-ip"})
_CHECKED = frozenset({"host", "origin", "sec-fetch-site", "content-type", "content-length", "transfer-encoding"})


def _headers(scope: Scope) -> dict[str, str]:
    out: dict[str, str] = {}
    raw: list[tuple[bytes, bytes]] = scope.get("headers", [])
    for k, v in raw:
        name = k.decode("latin-1").lower()
        if name in _CHECKED and name in out:  # a repeated checked header: keep a value that fails every check
            out[name] = "\x00"
        else:
            out[name] = v.decode("latin-1")
    return out
