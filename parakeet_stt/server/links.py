"""Recordings behind a download link (a time-limited, presigned download link): only the sound is fetched.

The link's query (its signature) is a secret: it is never logged, never in a message, never in the window - the
window sees `shown` ("host/…/file.mp4"). The full link is kept sealed beside the fetched sound and dropped with
the job. ffmpeg reads the link and copies the sound out of it while it downloads; the picture passes through
memory only (an interleaved MP4 has its sound spread over the whole file) and never reaches the disk.
"""

from __future__ import annotations

import http.client
import re
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final
from urllib.parse import unquote, urlsplit

from parakeet_stt import blobserve, media, sound
from parakeet_stt.cancel import Cancelled, CancelToken
from parakeet_stt.server.uploads import MEDIA_EXTENSIONS, SPARE_BYTES, safe_name
from parakeet_stt.vault import Vault

LINK_FILE: Final = "link"
MAX_LINK_CHARS: Final = 8192
_TIMEOUT_S: Final = 10  # the longest pause between two parts of the first answer
_DEADLINE_S: Final = 60.0  # the longest the whole first answer may take
_ANY_LINK = re.compile(r"[a-zA-Z][a-zA-Z0-9+.-]*://\S+")
EXPIRED: Final = "This link has expired or is not valid any more. Get a fresh link and add it again."


class LinkError(Exception):
    """A link cannot be used; the message is for the user and never contains the link's secret part."""


@dataclass(frozen=True)
class Link:
    url: str
    shown: str  # without the query, the user name/password part and the middle of the path
    name: str  # the recording's file name, made safe
    allow_http: bool = False  # tests only


def parse(text: str, *, allow_local_http: bool = False) -> Link:
    """Check a pasted link: https only (plain http on 127.0.0.1 for tests), a host, a file name of a recording."""
    url = text.strip()
    if not url or len(url) > MAX_LINK_CHARS or any(c.isspace() for c in url):
        raise LinkError("Paste one whole link (it starts with https://).")
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        _ = parts.port  # a port that is not a number
    except ValueError:
        raise LinkError("This is not a link the app can read.") from None
    local = parts.scheme == "http" and host == "127.0.0.1" and allow_local_http
    if parts.scheme != "https" and not local:
        raise LinkError("Only secure links (https://) can be added.")
    if not host or parts.username or parts.password:
        raise LinkError("This is not a link the app can read.")
    raw_name = unquote(parts.path.rsplit("/", 1)[-1])
    name = safe_name(raw_name) if raw_name else "recording.mp4"
    if Path(name).suffix.lower() not in MEDIA_EXTENSIONS:
        name = f"{name}.mp4"
    shown = f"{host}/…/{name}" if parts.path.count("/") > 1 else f"{host}/{name}"
    return Link(url, shown, name, allow_http=local)


class _SecureRedirects(urllib.request.HTTPRedirectHandler):
    """Follow a redirect only to another secure link."""

    def __init__(self, allow_http: bool) -> None:
        self.allow_http = allow_http

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]  # noqa: PLR0917 - the stdlib's
        if not newurl.startswith("https://") and not (self.allow_http and newurl.startswith("http://127.0.0.1")):
            raise LinkError("The link leads on to an insecure address; it was not followed.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def reachable(link: Link, cancel: CancelToken | None = None, deadline_s: float = _DEADLINE_S) -> None:
    """One byte, to tell an expired link from a working one before ffmpeg is started (HEAD is not signed: 403).
    Asked in a thread of its own with a deadline for the whole answer: a site that sends its answer a byte at a time
    restarts the per-read timeout forever, and would hold the queue of links with it (a cancel is seen at once)."""
    done = threading.Event()
    failed: list[BaseException] = []

    def ask() -> None:
        try:
            _one_byte(link)
        except BaseException as exc:  # noqa: BLE001 - handed to the waiting thread
            failed.append(exc)
        finally:
            done.set()

    threading.Thread(target=ask, daemon=True, name="link-check").start()
    end = time.monotonic() + deadline_s
    while not done.wait(0.2):
        if cancel is not None:
            cancel.raise_if_cancelled()
        if time.monotonic() > end:
            raise LinkError("The link did not answer in time. Check the internet connection and try again.")
    if failed:
        raise failed[0]


def _one_byte(link: Link) -> None:
    req = urllib.request.Request(link.url, headers={"Range": "bytes=0-0", "User-Agent": "parakeet-stt"})
    opener = urllib.request.build_opener(_SecureRedirects(link.allow_http))
    try:
        with opener.open(req, timeout=_TIMEOUT_S) as resp:
            resp.read(1)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise LinkError(EXPIRED) from None
        if exc.code in (404, 410):
            raise LinkError("Nothing is at this link (any more).") from None
        raise LinkError(f"The link answered with an error (HTTP {exc.code}).") from None
    except (urllib.error.URLError, OSError, http.client.HTTPException, ValueError):
        raise LinkError("The link could not be reached. Check the internet connection and try again.") from None


def fetch(
    link: Link,
    target: Path,
    vault: Vault,
    on_progress: Callable[[float, float, float], None] | None = None,
    cancel: CancelToken | None = None,
) -> media.MediaInfo:
    """Download the sound of `link` into `target`, sealed. on_progress(fraction, seconds_done, seconds_total).
    Returns the facts of the recording behind the link (its picture's frame rate matters for the subtitles).
    Raises LinkError (one sentence) or Cancelled."""
    reachable(link, cancel)
    if cancel is not None:
        cancel.raise_if_cancelled()
    options = blobserve.remote_input_options(allow_http=link.allow_http)
    source = blobserve.MediaInput(target.with_name(link.name), link.url, 0, options=options)
    try:
        info = media.probe(source, cancel)
    except media.MediaError:
        raise LinkError("The recording behind this link could not be read; it may not be audio or video.") from None
    total = info.audio_duration_s or info.duration_s
    _check_space(target.parent, sound.estimate_bytes(info))

    def progress(done_s: float) -> None:
        if on_progress is not None:
            done = min(max(done_s, 0.0), total)
            on_progress(min(done / total, 0.999), done, total)

    try:
        sound.keep(info, target, vault, progress, cancel, guard=lambda: _keep_space(target.parent))
    except media.MediaError as exc:
        raise LinkError(_plain(str(exc), link)) from None
    except Cancelled:
        raise
    return info


def _check_space(folder: Path, need: int) -> None:
    free = sound.free_bytes(folder)
    if free is not None and free - need < SPARE_BYTES:
        raise LinkError(
            f"Not enough free disk space for this recording: its sound needs about {_mb(need)} MB and "
            f"{_mb(SPARE_BYTES)} MB must stay free, but only {_mb(free)} MB are free. Free some space and try again."
        )


def _keep_space(folder: Path) -> None:
    free = sound.free_bytes(folder)
    if free is not None and free < SPARE_BYTES:
        raise media.MediaError(
            f"The download was stopped: the disk is almost full (less than {_mb(SPARE_BYTES)} MB free). "
            "Free some space and try again."
        )


def _mb(n: int) -> int:
    return n // 1024**2


def _plain(text: str, link: Link) -> str:
    """A message with every trace of a link replaced by the shown form (ffmpeg likes to quote what it opened)."""
    text = text.replace(link.url, link.shown).replace(link.url.split("?", 1)[0], link.shown)
    return _ANY_LINK.sub(link.shown, text)
