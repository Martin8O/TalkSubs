"""Live events to every open window: numbered frames, a snapshot first, bounded per-client buffers.

Everything here runs on the server's event loop; other threads hand work to it with call_soon_threadsafe.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import Callable

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from parakeet_stt.server.messages import SnapshotEvent, Status

log = logging.getLogger("parakeet_stt.server")
CLIENT_BUFFER = 512  # frames a slow window may lag behind before it is dropped (it reconnects and resyncs)


class _Client:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[str] = asyncio.Queue(CLIENT_BUFFER)
        self.overflow = asyncio.Event()

    def offer(self, frame: str) -> None:
        try:
            self.queue.put_nowait(frame)
        except asyncio.QueueFull:
            self.overflow.set()


class Hub:
    def __init__(self) -> None:
        self.boot_id = uuid.uuid4().hex
        self.seq = 0
        self._clients: set[_Client] = set()
        self.on_count: Callable[[int], None] | None = None  # told whenever the number of windows changes

    @property
    def count(self) -> int:
        return len(self._clients)

    def publish(self, make: Callable[[int], BaseModel]) -> None:
        """Number the next event and queue it for every window. Event-loop thread only."""
        self.seq += 1
        frame = make(self.seq).model_dump_json()
        for c in self._clients:
            c.offer(frame)

    async def serve(self, ws: WebSocket, snapshot: Callable[[], Status]) -> None:
        await ws.accept()
        client = _Client()
        status = snapshot()  # taken in the same loop step as registering: no event can fall in between
        client.offer(SnapshotEvent(seq=status.seq, status=status).model_dump_json())
        self._clients.add(client)
        self._changed()
        try:
            await self._pump(ws, client)
        finally:
            self._clients.discard(client)
            self._changed()

    async def _pump(self, ws: WebSocket, client: _Client) -> None:
        receive = asyncio.ensure_future(ws.receive())
        overflow = asyncio.ensure_future(client.overflow.wait())
        try:
            while True:
                send = asyncio.ensure_future(client.queue.get())
                done, _ = await asyncio.wait({send, receive, overflow}, return_when=asyncio.FIRST_COMPLETED)
                if send in done:
                    try:
                        await ws.send_text(send.result())
                    except (WebSocketDisconnect, RuntimeError, OSError):
                        return
                else:
                    send.cancel()
                if overflow in done:
                    log.warning("a window fell %d events behind; closing it so it resyncs", CLIENT_BUFFER)
                    with contextlib.suppress(Exception):
                        await ws.close(code=1013)
                    return
                if receive in done:
                    msg = receive.result()
                    if msg.get("type") == "websocket.disconnect":
                        return
                    receive = asyncio.ensure_future(ws.receive())  # windows send nothing we act on
        finally:
            for f in (receive, overflow):
                f.cancel()

    def _changed(self) -> None:
        if self.on_count is not None:
            self.on_count(self.count)


class Presence:
    """Stops the app when its window is gone: no window for `grace_s`, or none ever within `first_s`."""

    def __init__(self, on_gone: Callable[[], None], grace_s: float = 8.0, first_s: float = 60.0) -> None:
        self.on_gone = on_gone
        self.grace_s, self.first_s = grace_s, first_s
        self._timer: asyncio.TimerHandle | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._timer = loop.call_later(self.first_s, self._fire, "no window connected")

    def changed(self, count: int) -> None:
        if self._loop is None:
            return
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        if count == 0:
            self._timer = self._loop.call_later(self.grace_s, self._fire, "the window was closed")

    def _fire(self, why: str) -> None:
        log.info("stopping: %s", why)
        self.on_gone()
