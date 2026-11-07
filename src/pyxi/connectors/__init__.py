"""Connected accounts: Gmail, Google Calendar, Google Docs, Discord and LinkedIn.

Each connector offers tools to the model only once it is connected, so an unconnected service
costs nothing per turn. Reading is immediate. Anything that reaches other people (sending an
email, posting, messaging, inviting) comes back as an :class:`Outgoing`: Pyxi shows an approval
card with exactly what will be sent, and only acts if you press Allow.

Credentials live on this PC only, under ``.pyxi/connectors/`` (git-ignored), or in ``.env``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from otto.llm.base import ToolSpec


@dataclass
class Outgoing:
    """Something that would leave this PC. Nothing happens until the user approves it."""

    action: str  # "Send email to sam@example.com"
    preview: str  # what exactly goes out
    run: Callable[[], Awaitable[str]]  # does it, returns a short result line
    agent: str = "Pyxi"
    kind: str = "message"  # "invite" (a calendar invite) or "message" (email, post, chat)
    recipients: list[str] = field(default_factory=list)
    # The exact text that goes out (an email's body, a post). The card shows it in full, byte for
    # byte, inside ``preview``: nothing is cut on the card that would go out anyway.
    payload: str = ""


Result = str | Outgoing


@dataclass
class Tool:
    spec: ToolSpec
    handler: Callable[[dict[str, Any]], Awaitable[Result]]
    info: bool = False  # the result goes back to the model for another round


@dataclass
class Status:
    id: str
    name: str
    connected: bool
    detail: str = ""
    setup: list[str] = field(default_factory=list)


def tool(
    name: str, description: str, props: dict[str, Any], required: list[str] | None = None
) -> ToolSpec:
    return ToolSpec(
        name, description, {"type": "object", "properties": props, "required": required or []}
    )


class Connector:
    id = ""
    name = ""

    def connected(self) -> bool:
        raise NotImplementedError

    def status(self) -> Status:
        raise NotImplementedError

    def tools(self) -> list[Tool]:
        raise NotImplementedError


class Connectors:
    def __init__(self, data_dir: Path, downloads: Path) -> None:
        from pyxi.connectors.discord import DiscordConnector
        from pyxi.connectors.google import GoogleConnector
        from pyxi.connectors.linkedin import LinkedInConnector

        self.dir = data_dir / "connectors"
        self.downloads = downloads
        self.google = GoogleConnector(self.dir, downloads)
        self.discord = DiscordConnector()
        self.linkedin = LinkedInConnector(self.dir)
        self.all: list[Connector] = [self.google, self.discord, self.linkedin]

    def tools(self) -> list[Tool]:
        return [t for c in self.all if c.connected() for t in c.tools()]

    def find(self, name: str) -> Tool | None:
        return next((t for t in self.tools() if t.spec.name == name), None)

    def statuses(self) -> list[dict[str, Any]]:
        return [vars(c.status()) for c in self.all]
