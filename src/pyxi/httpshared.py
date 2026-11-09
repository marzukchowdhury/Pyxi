"""One kept-open HTTP client per purpose (and event loop), instead of a new one per request.

Creating an httpx client loads the certificate store, about 0.25 s of CPU on the event loop, so
every web search, page read and weather check paid it again (and 25 'parallel' Gmail reads ran
one after another). ``async with shared("web", headers=...) as c:`` reads like before.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

_clients: dict[tuple[str, int], httpx.AsyncClient] = {}


class _Keep:
    """Hands out the shared client; leaving the block keeps it open."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client

    async def __aenter__(self) -> httpx.AsyncClient:
        return self.client

    async def __aexit__(self, *exc: object) -> bool:
        return False


def shared(purpose: str, **kw: Any) -> _Keep:
    key = (purpose, id(asyncio.get_running_loop()))
    client = _clients.get(key)
    if client is None or client.is_closed:
        client = httpx.AsyncClient(**kw)
        _clients[key] = client
    return _Keep(client)
