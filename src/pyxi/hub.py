"""The hub: one place that fans events out to every connected window (overlay + panel) and
turns "ask the user" moments into awaitable requests."""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from collections.abc import Awaitable, Callable
from typing import Any

log = logging.getLogger(__name__)

Sender = Callable[[str], Awaitable[None]]


class Hub:
    def __init__(self) -> None:
        self._clients: dict[str, Sender] = {}
        self._pending: dict[str, asyncio.Future[Any]] = {}
        self.history: list[dict[str, Any]] = []  # recent non-bulky events, replayed to new clients

    def connect(self, send: Sender) -> str:
        cid = secrets.token_hex(4)
        self._clients[cid] = send
        return cid

    def disconnect(self, cid: str) -> None:
        self._clients.pop(cid, None)

    @property
    def connected(self) -> int:
        return len(self._clients)

    async def emit(self, type: str, **data: Any) -> None:
        event = {"type": type, "ts": time.time(), **data}
        if type not in _BULKY:
            self.history = [*self.history[-199:], event]
        text = json.dumps(event, default=str)
        for cid, send in list(self._clients.items()):
            try:
                await send(text)
            except Exception as exc:
                log.warning("client %s went away (%s)", cid, exc)
                self.disconnect(cid)

    async def request(self, type: str, timeout: float = 300, **data: Any) -> Any:
        """Emit a request (approval, question) and wait for a client to answer it."""
        rid = "req_" + secrets.token_hex(4)
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[rid] = future
        await self.emit(type, request_id=rid, **data)
        try:
            return await asyncio.wait_for(future, timeout)
        except TimeoutError:
            return None
        finally:
            self._pending.pop(rid, None)
            # Answered here, in another window, or timed out: every screen drops the card.
            await self.emit("request_closed", request_id=rid)

    def resolve(self, request_id: str, value: Any) -> bool:
        future = self._pending.get(request_id)
        if future is None or future.done():
            return False
        future.set_result(value)
        return True

    def cancel_all(self) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_result(None)


_BULKY = {"say", "job_frame", "scene_audio", "screen_preview"}
