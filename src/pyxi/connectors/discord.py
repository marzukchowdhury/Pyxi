"""Discord through a bot you add to your own servers.

Setup (once): discord.com/developers > New Application > Bot > Reset Token, turn on "Message
Content Intent", invite the bot to your server (OAuth2 > URL Generator > bot, with Read
Messages and Send Messages), then put ``DISCORD_BOT_TOKEN=...`` in ``pyxi/.env``.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from pyxi.connectors import Connector, Outgoing, Result, Status, Tool, tool

API = "https://discord.com/api/v10"


class DiscordConnector(Connector):
    id = "discord"
    name = "Discord"

    def __init__(self) -> None:
        self._channels: list[dict[str, Any]] | None = None

    @property
    def token(self) -> str | None:
        return os.environ.get("DISCORD_BOT_TOKEN")

    def connected(self) -> bool:
        return bool(self.token)

    def status(self) -> Status:
        if self.connected():
            return Status(self.id, self.name, True, "Bot connected")
        return Status(
            self.id,
            self.name,
            False,
            "Needs a bot token",
            [
                "discord.com/developers > New Application > Bot > Reset Token.",
                "Turn on Message Content Intent on the same page.",
                "OAuth2 > URL Generator: scope 'bot', permissions Read Messages + Send Messages; open the URL to add it to your server.",
                "Add DISCORD_BOT_TOKEN=your-token to pyxi/.env and restart Pyxi.",
            ],
        )

    async def _api(self, method: str, path: str, **kw: Any) -> Any:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.request(
                method, API + path, headers={"Authorization": f"Bot {self.token}"}, **kw
            )
        if r.status_code >= 400:
            raise RuntimeError(f"Discord said {r.status_code}: {r.text[:200]}")
        return r.json() if r.content else {}

    async def channels(self) -> list[dict[str, Any]]:
        if self._channels is None:
            out = []
            for g in await self._api("GET", "/users/@me/guilds"):
                for c in await self._api("GET", f"/guilds/{g['id']}/channels"):
                    if c.get("type") in (0, 5):  # text and announcement channels
                        out.append({"id": c["id"], "name": c["name"], "server": g["name"]})
            self._channels = out
        return self._channels

    async def _resolve(self, ref: str) -> dict[str, Any] | None:
        ref = ref.strip().lstrip("#").lower()
        chans = await self.channels()
        return (
            next((c for c in chans if c["id"] == ref), None)
            or next((c for c in chans if c["name"].lower() == ref), None)
            or next((c for c in chans if ref in c["name"].lower()), None)
        )

    async def list_channels(self, args: dict[str, Any]) -> Result:
        chans = await self.channels()
        if not chans:
            return "Discord: the bot isn't in any server yet."
        return "Discord channels:\n" + "\n".join(
            f"- #{c['name']} ({c['server']})" for c in chans[:40]
        )

    async def read(self, args: dict[str, Any]) -> Result:
        chan = await self._resolve(str(args.get("channel", "")))
        if chan is None:
            return "discord_read: no channel like that; call discord_channels."
        msgs = await self._api(
            "GET",
            f"/channels/{chan['id']}/messages",
            params={"limit": min(int(args.get("limit", 20) or 20), 50)},
        )
        lines = [
            f"{m['author'].get('global_name') or m['author']['username']}: {m.get('content') or '(attachment)'}"
            for m in reversed(msgs)
        ]
        return f"#{chan['name']} ({chan['server']}), latest:\n" + "\n".join(lines)

    async def send(self, args: dict[str, Any]) -> Result:
        chan = await self._resolve(str(args.get("channel", "")))
        text = str(args.get("text", "")).strip()
        if chan is None or not text:
            return "discord_send: which channel, and what should it say?"

        async def post() -> str:
            await self._api(
                "POST", f"/channels/{chan['id']}/messages", json={"content": text[:2000]}
            )
            return f"Posted in #{chan['name']}."

        return Outgoing(
            action=f"Post in #{chan['name']} ({chan['server']})", preview=text[:800], run=post
        )

    def tools(self) -> list[Tool]:
        return [
            Tool(
                tool("discord_channels", "List the Discord channels Pyxi's bot can see.", {}),
                self.list_channels,
                info=True,
            ),
            Tool(
                tool(
                    "discord_read",
                    "Read recent messages in a Discord channel (name or id).",
                    {"channel": {"type": "string"}, "limit": {"type": "integer"}},
                    ["channel"],
                ),
                self.read,
                info=True,
            ),
            Tool(
                tool(
                    "discord_send",
                    "Post a message in a Discord channel. The user approves it first.",
                    {"channel": {"type": "string"}, "text": {"type": "string"}},
                    ["channel", "text"],
                ),
                self.send,
            ),
        ]
