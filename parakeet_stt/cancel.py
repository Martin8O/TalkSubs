"""Cooperative cancellation shared by every long step (decode, VAD, recognition)."""

from __future__ import annotations

import threading


class Cancelled(Exception):
    """The job was cancelled by the user; nothing was written."""


class CancelToken:
    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self._event.is_set():
            raise Cancelled
