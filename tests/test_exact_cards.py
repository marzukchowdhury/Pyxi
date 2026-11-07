"""Cards show exactly what will happen: every address, the whole message, the real link. What
the card shows is what gets sent, byte for byte."""

from __future__ import annotations

import base64
import email
import email.policy
from typing import Any

import httpx

from pyxi.connectors.discord import DiscordConnector
from pyxi.connectors.linkedin import LinkedInConnector
from test_email_errands import connector, mailbox

BODY = (
    "Hi Sam,\n\nHere's everything from Tuesday, in full:\n"
    + "\n".join(f"{i}. Point number {i}: café ☕, déjà vu, 12,50 € and $1,234.56" for i in range(1, 60))
    + "\n\n  indented line, trailing spaces   \nLast line, no newline"
)


async def test_an_email_card_shows_the_whole_email_and_sends_exactly_that(tmp_path):
    mail = mailbox()
    g = connector(tmp_path, mail)
    out = await g.gmail_send(
        {"to": "Sam Rahman <sam.rahman@uni.edu>, alex.kim@work.com", "subject": "Notes ☕ (full)", "body": BODY, "gist": "The notes"}
    )
    # The card: every address in full, the subject, and the body exactly (no "...").
    assert out.preview.startswith("To: Sam Rahman (sam.rahman@uni.edu), alex.kim@work.com\n")
    assert "Subject: Notes ☕ (full)" in out.preview
    assert out.preview.endswith("\n\n" + BODY) and out.payload == BODY and len(BODY) > 2000
    await out.run()
    [sent] = mail.sent
    msg = email.message_from_bytes(base64.urlsafe_b64decode(sent["raw"]), policy=email.policy.default)
    assert msg.get_body().get_content().removesuffix("\n") == BODY  # byte for byte
    assert str(msg["Subject"]) == "Notes ☕ (full)"
    assert "sam.rahman@uni.edu" in str(msg["To"]) and "alex.kim@work.com" in str(msg["To"])


async def test_an_invite_card_shows_everything_the_guests_get(tmp_path, monkeypatch):
    g = connector(tmp_path, mailbox())
    posted: list[dict[str, Any]] = []

    async def api(method: str, url: str, **kw: Any) -> Any:
        if url.endswith("/settings/timezone"):
            return {"value": "America/Vancouver"}
        if method == "POST":
            posted.append(kw["json"])
            return {"id": "e1"}
        ev = posted[-1]
        return {**ev, "htmlLink": "https://calendar.example/e1", "attendees": ev.get("attendees", [])}

    g._api = api  # type: ignore[method-assign]
    note = "Agenda:\n1. Budget\n2. Launch date\nBring the Q4 numbers."
    out = await g.calendar_add(
        {
            "title": "Planning",
            "start": "2099-01-05T10:00",
            "minutes": 45,
            "attendees": ["sam.rahman@uni.edu", "alex.kim@work.com"],
            "location": "Room 4, 12 Main St",
            "description": note,
            "meet": False,
        }
    )
    lines = out.preview.split("\n")
    assert lines[0] == "Guests: sam.rahman@uni.edu, alex.kim@work.com" and lines[1] == "Title: Planning"
    assert "Where: Room 4, 12 Main St" in out.preview and out.preview.endswith("\n\n" + note)
    await out.run()
    assert posted[0]["description"] == note == out.payload
    assert [a["email"] for a in posted[0]["attendees"]] == ["sam.rahman@uni.edu", "alex.kim@work.com"]


async def test_a_discord_post_is_shown_and_sent_whole_or_refused(monkeypatch):
    d = DiscordConnector()
    sent: list[dict[str, Any]] = []

    async def resolve(ref):
        return {"id": "c1", "name": "general", "server": "Team"}

    async def api(method, path, **kw):
        sent.append(kw["json"])
        return {}

    monkeypatch.setattr(d, "_resolve", resolve)
    monkeypatch.setattr(d, "_api", api)
    text = "x" * 1900 + "\nthe end ✓"
    out = await d.send({"channel": "general", "text": text})
    assert out.preview == text == out.payload
    await out.run()
    assert sent == [{"content": text}]
    too_long = await d.send({"channel": "general", "text": "y" * 2001})
    assert isinstance(too_long, str) and "2000" in too_long  # never silently cut


async def test_a_linkedin_post_is_shown_and_sent_whole_or_refused(tmp_path, monkeypatch):
    import json
    import time

    li = LinkedInConnector(tmp_path)
    from pyxi import secretstore

    secretstore.write_json(li.token_path, {"access_token": "t", "expires_at": time.time() + 999, "sub": "abc"})
    posted: list[dict[str, Any]] = []

    def reply(request: httpx.Request) -> httpx.Response:
        posted.append(json.loads(request.content))
        return httpx.Response(201, json={})

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(reply), **kw))
    text = "Proud of the team 🎉\n" + "z" * 2900
    out = await li.post({"text": text})
    assert out.preview == text == out.payload
    await out.run()
    assert posted[0]["specificContent"]["com.linkedin.ugc.ShareContent"]["shareCommentary"]["text"] == text
    assert "3000" in await li.post({"text": "w" * 3001})
