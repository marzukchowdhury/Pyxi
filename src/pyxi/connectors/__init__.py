"""Connected accounts: Gmail, Google Calendar, Google Docs, and (set up by a developer only)
Discord and LinkedIn.

Discord and LinkedIn need a bot token or a developer app that a customer cannot make, so they
are hidden everywhere (Settings, the model's tools, the prompt) unless that set-up is already
on this PC: see :meth:`Connector.available`.

Each connector offers tools to the model only once it is connected, so an unconnected service
costs nothing per turn. Reading is immediate. Anything that reaches other people (sending an
email, posting, messaging, inviting) comes back as an :class:`Outgoing`: Pyxi shows an approval
card with exactly what will be sent, and only acts if you press Allow.

Credentials live on this PC only, under ``.pyxi/connectors/`` (git-ignored), or in ``.env``.
"""

from __future__ import annotations

import re
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
    # What it counts as for the hourly limits (pyxi/limits.py): email, post or invite.
    channel: str = ""


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
    needs: str = ""  # connected, but one more OK is needed: the button's label ("Allow Sheets")


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

    def available(self) -> bool:
        """Can someone using Pyxi connect this by themselves? False hides it everywhere."""
        return True

    def status(self) -> Status:
        raise NotImplementedError

    def tools(self) -> list[Tool]:
        raise NotImplementedError


# What Pyxi cannot do yet for a customer, and what it offers instead. One plain line each:
# never a word about developer apps, bot tokens, keys or set-up files.
NOT_YET = {
    "linkedin": "I can't post to LinkedIn for you yet. I can write the post for you to paste in.",
    "discord": "I can't read or send Discord messages yet. I can write the message for you to paste in.",
}
_NAMES = {"linkedin": "LinkedIn", "discord": "Discord"}
# A request to post or message there (not one that only names the site: "email Sam the
# LinkedIn link" is Gmail's work).
_ASKS = {
    "linkedin": re.compile(
        r"(?i)\b(?:post|share|publish|put|announce)\w*\b[^.?!\n]{0,80}\b(?:on|to)\s+(?:my\s+)?linkedin\b"
        r"|\blinkedin\s+post\b"
    ),
    "discord": re.compile(
        r"(?i)\b(?:post|send|message|share|announce|read|check|reply|write)\w*\b[^.?!\n]{0,80}"
        r"\b(?:on|to|in|from)\s+(?:my\s+|the\s+)?discord\b|\bdiscord\s+(?:message|channel|server)s?\b"
    ),
}


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

    def shown(self) -> list[Connector]:
        """The accounts someone can connect on this PC (Settings lists these and no others)."""
        return [c for c in self.all if c.available()]

    def hidden(self) -> list[Connector]:
        return [c for c in self.all if not c.available()]

    def tools(self) -> list[Tool]:
        return [t for c in self.shown() if c.connected() for t in c.tools()]

    def find(self, name: str) -> Tool | None:
        return next((t for t in self.tools() if t.spec.name == name), None)

    def statuses(self, everything: bool = False) -> list[dict[str, Any]]:
        """The rows of Settings' Connected accounts. ``everything``: the hidden ones too (the
        developer's ``pyxi connect status``)."""
        return [vars(c.status()) for c in (self.all if everything else self.shown())]

    def not_yet(self, text: str) -> str:
        """The one plain line for a request that needs a hidden account ("post this on
        LinkedIn"), or "" when the request needs none of them."""
        return next(
            (NOT_YET[c.id] for c in self.hidden() if c.id in _ASKS and _ASKS[c.id].search(text or "")),
            "",
        )

    def not_yet_tool(self, name: str) -> str:
        """The same line for a tool of a hidden account that the model called anyway."""
        return next(
            (NOT_YET[c.id] for c in self.hidden() if name.startswith(c.id + "_") and c.id in NOT_YET),
            "",
        )

    def prompt_note(self) -> str:
        """Rules for the system prompt about the accounts that are, or are not, on offer here."""
        lines = []
        shown = [c.name for c in self.shown() if c.id in NOT_YET]
        if shown:
            lines.append(
                f"- {' and '.join(shown)} can be a connected account on this PC too: posting "
                "there is an `errand`, and they approve the exact text first."
            )
        for c in self.hidden():
            if c.id not in NOT_YET:
                continue
            lines.append(
                f"- {_NAMES.get(c.id, c.name)}: you have no way to reach it yet. If they ask "
                f'for it, say exactly this and nothing about set-up: "{NOT_YET[c.id]}" Then, if '
                "they want it, write the text in a note they can copy. Never start an `errand` "
                "for it, and never mention developer apps, bots, keys, tokens or files to edit."
            )
        return ("\n" + "\n".join(lines)) if lines else ""
