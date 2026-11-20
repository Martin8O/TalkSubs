"""The link fetcher: one download at a time, on the CPU, before the GPU queue.

A link job waits in "fetching" until its sound is in; the runner then queues it for the GPU. The event loop owns
all state; the download runs in a thread and reports back through `call_soon_threadsafe`.
"""

from __future__ import annotations

import asyncio
import collections
import contextlib
import logging
import time
from collections.abc import Callable
from pathlib import Path

from parakeet_stt import media
from parakeet_stt.cancel import Cancelled, CancelToken
from parakeet_stt.server import links
from parakeet_stt.vault import Vault

log = logging.getLogger("parakeet_stt.server")

PROGRESS_MIN_INTERVAL_S = 0.5
FORGET_WAIT_S = 10.0


class Fetches:
    def __init__(
        self,
        target_of: Callable[[str], tuple[links.Link, Path] | None],  # job id -> (link, where its sound goes)
        progress: Callable[[str, float, float, float], None],
        finished: Callable[[str, str | None, media.MediaInfo | None], None],  # job id, error, the recording's facts
        vault: Vault,
        fetch: Callable[..., media.MediaInfo]
        | None = None,  # default: links.fetch, looked up when used (tests replace it)
    ) -> None:
        self._target_of = target_of
        self._progress = progress
        self._finished = finished
        self._fetch = fetch
        self._vault = vault
        self._queue: collections.deque[str] = collections.deque()
        self._current: str | None = None
        self._cancel: CancelToken | None = None
        self._idle = asyncio.Event()
        self._idle.set()
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._last_sent = 0.0
        self._closing = False

    def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._task = self._loop.create_task(self._run(), name="fetches")

    async def shutdown(self) -> None:
        self._closing = True
        if self._cancel is not None:
            self._cancel.cancel()
        self._wake.set()
        if self._task is not None:
            with contextlib.suppress(asyncio.CancelledError, TimeoutError):
                await asyncio.wait_for(self._task, 15)

    def enroll(self, job_id: str) -> None:
        if job_id == self._current or job_id in self._queue:
            return
        self._queue.append(job_id)
        self._wake.set()

    async def forget(self, job_id: str) -> None:
        """Stop fetching a job (cancelled or removed); returns once its download has ended."""
        with contextlib.suppress(ValueError):
            self._queue.remove(job_id)
        if self._current == job_id and self._cancel is not None:
            self._cancel.cancel()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._idle.wait(), FORGET_WAIT_S)

    def busy_with(self, job_id: str) -> bool:
        return job_id == self._current

    async def _run(self) -> None:
        while not self._closing:
            if not self._queue:
                self._wake.clear()
                await self._wake.wait()
                continue
            job_id = self._queue.popleft()
            try:
                await self._one(job_id)
            except Exception:  # the loop must outlive one job's trouble (e.g. a full disk while saving its state)
                log.exception("the download of job %s could not be finished", job_id)

    async def _one(self, job_id: str) -> None:
        assert self._loop is not None
        loop = self._loop
        target = self._target_of(job_id)
        if target is None:  # removed or cancelled while it waited
            return
        link, path = target
        token = CancelToken()
        self._current, self._cancel = job_id, token
        self._idle.clear()

        def report(fraction: float, done: float, total: float) -> None:
            now = time.monotonic()
            if now - self._last_sent < PROGRESS_MIN_INTERVAL_S:
                return
            self._last_sent = now
            loop.call_soon_threadsafe(self._report, job_id, token, fraction, done, total)

        error: str | None = None
        info: media.MediaInfo | None = None
        try:
            info = await asyncio.to_thread(self._fetch or links.fetch, link, path, self._vault, report, token)
            log.info("job %s: the sound of its link is in", job_id)
        except Cancelled:
            log.info("the download of job %s was stopped", job_id)
            error = "cancelled"
        except links.LinkError as exc:
            log.warning("job %s: its link could not be fetched", job_id)  # the sentence may name the file: not logged
            error = str(exc)
        except Exception as exc:
            log.exception("the download of job %s crashed", job_id)
            error = f"The recording could not be downloaded ({exc.__class__.__name__})."
        finally:
            self._current, self._cancel = None, None
            self._idle.set()
        if error != "cancelled" and (error is None or not self._closing):  # a download that made it is kept
            self._finished(job_id, error, info)

    def _report(self, job_id: str, token: CancelToken, fraction: float, done: float, total: float) -> None:
        if self._current == job_id and not token.cancelled:
            self._progress(job_id, fraction, done, total)
