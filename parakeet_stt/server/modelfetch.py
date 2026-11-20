"""The speech model's one-time download, on the first start, before the queue may run anything. The download
package's graphics-card support (cardpack.py) uses the same machinery, but waits until the user asks for it.

The event loop owns the state; the download runs in a thread and reports back through `call_soon_threadsafe`.
A stop of the app interrupts it; its partial file stays, and the next start goes on from it.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable

from parakeet_stt import models
from parakeet_stt.cancel import Cancelled, CancelToken
from parakeet_stt.server.messages import ModelsInfo

log = logging.getLogger("parakeet_stt.server")

PROGRESS_MIN_INTERVAL_S = 0.5
ETA_MIN_ELAPSED_S = 3.0  # the first seconds (connecting, a slow start) say nothing about the speed


class ModelFetch:
    def __init__(
        self,
        publish: Callable[[ModelsInfo], None],
        on_ready: Callable[[], None],
        present: Callable[[], bool] | None = None,  # default: every pinned file is here at its size
        fetch: Callable[[models.ProgressFn, CancelToken], None] | None = None,  # default: models.fetch
        clock: Callable[[], float] = time.monotonic,
        *,
        total_bytes: int | None = None,  # default: the model's files
        auto: bool = True,  # False: a missing download waits ("absent") until retry() starts it ...
        resume: Callable[[], bool] | None = None,  # ... unless this says one the user started is unfinished
        what: str = "the speech model",  # for the log
    ) -> None:
        self._publish = publish
        self._on_ready = on_ready
        self._present = present or models.present
        self._fetch = fetch or models.fetch
        self._clock = clock
        self._auto = auto
        self._resume = resume or (lambda: False)
        self._what = what
        self.info = ModelsInfo(
            state="checking", total_bytes=models.total_size() if total_bytes is None else total_bytes
        )
        self._cancel: CancelToken | None = None
        self._task: asyncio.Task[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._last_sent = -PROGRESS_MIN_INTERVAL_S
        self._flush: asyncio.TimerHandle | None = None
        self._closing = False

    @property
    def ready(self) -> bool:
        return self.info.state == "ready"

    def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        if self._present():  # the usual start: a few file sizes, no network
            self._set(ModelsInfo(state="ready", done_bytes=self.info.total_bytes, total_bytes=self.info.total_bytes))
            return
        if not self._auto and not self._resume():
            self._set(self.info.model_copy(update={"state": "absent"}))
            return
        self._begin()

    def retry(self) -> bool:
        """Start (again) after a failure, or the first time when it waits for the user; False when there is nothing
        to start (running or done)."""
        if self.info.state not in ("failed", "absent") or self._closing:
            return False
        self._begin()
        return True

    def recheck(self) -> bool:
        """A download counted as done whose files are gone or damaged since (found by loading them): waiting again.
        True when that is what it found."""
        if self.info.state == "ready" and not self._auto and not self._present():
            self._set(ModelsInfo(state="absent", total_bytes=self.info.total_bytes))
            return True
        return False

    async def shutdown(self) -> None:
        self._closing = True
        if self._cancel is not None:
            self._cancel.cancel()
        if self._task is not None:
            with contextlib.suppress(asyncio.CancelledError, TimeoutError):
                await asyncio.wait_for(self._task, 15)

    def _begin(self) -> None:
        assert self._loop is not None
        self._set(self.info.model_copy(update={
            "state": "downloading", "error": None, "error_kind": None, "need_bytes": None, "free_bytes": None,
            "eta_s": None,
        }))  # fmt: skip
        self._cancel = CancelToken()  # here, not in the task: a stop in the same tick must still reach it
        self._task = self._loop.create_task(self._run(self._cancel), name="model-download")

    async def _run(self, token: CancelToken) -> None:
        assert self._loop is not None
        loop = self._loop
        started = self._clock()
        first: int | None = None  # bytes on disk when this run began: the speed counts only what arrives now

        def report(done: int, total: int) -> None:
            nonlocal first
            if first is None:
                first = done
            elapsed = self._clock() - started
            got = done - first
            eta = (total - done) / (got / elapsed) if elapsed >= ETA_MIN_ELAPSED_S and got > 0 else None
            loop.call_soon_threadsafe(self._progress, token, done, total, eta)

        log.info("%s is not here yet: downloading it", self._what)
        failure: models.ModelFetchError | None = None
        try:
            await asyncio.to_thread(self._fetch, report, token)
        except Cancelled:
            log.info("the download of %s was stopped; it goes on at the next try", self._what)
            return
        except models.ModelFetchError as exc:
            failure = exc
        except Exception as exc:  # the loop must outlive any surprise here; the user gets a sentence and a retry
            log.exception("the download of %s crashed", self._what)
            failure = models.ModelFetchError(
                f"{self._what.capitalize()} could not be downloaded ({exc.__class__.__name__}). Press Try again."
            )
        finally:
            self._cancel = None
        if self._closing:
            return
        if failure is None and not self._present():
            failure = models.ModelFetchError(
                f"{self._what.capitalize()} is still incomplete after its download. Press Try again."
            )
        if failure is not None:
            log.warning("the download of %s failed: %s", self._what, failure)
            self._set(self.info.model_copy(update={
                "state": "failed", "error": str(failure), "error_kind": failure.kind, "eta_s": None,
                "need_bytes": failure.need, "free_bytes": failure.free,
            }))  # fmt: skip
            return
        log.info("%s is in", self._what)
        total = self.info.total_bytes
        self._set(ModelsInfo(state="ready", done_bytes=total, total_bytes=total))
        self._on_ready()

    def _progress(self, token: CancelToken, done: int, total: int, eta: float | None) -> None:
        if token.cancelled or self.info.state != "downloading":
            return
        eta_s = round(eta, 1) if eta is not None else None
        # the state is always exact; the window hears at most twice a second, and always the latest figure
        self.info = self.info.model_copy(update={"done_bytes": done, "total_bytes": total, "eta_s": eta_s})
        wait = self._last_sent + PROGRESS_MIN_INTERVAL_S - self._clock()
        if wait <= 0:
            self._send_progress()
        elif self._flush is None:
            assert self._loop is not None
            self._flush = self._loop.call_later(wait, self._send_progress)

    def _send_progress(self) -> None:
        self._flush = None
        if self.info.state == "downloading":
            self._last_sent = self._clock()
            self._publish(self.info)

    def _set(self, info: ModelsInfo) -> None:
        self.info = info
        self._publish(info)
