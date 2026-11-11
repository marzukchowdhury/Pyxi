"""Errands: 'On it', quiet work with checks, one result in the notch."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from pyxi.companion.errands import said_by_user
from pyxi.timecontext import now_block
from test_brain_and_server import make_brain
from test_connectors_and_docs import Script

TZ = "America/Vancouver"


def tomorrow_at(hour: int) -> datetime:
    now = datetime.now(ZoneInfo(TZ))
    return (now + timedelta(days=1)).replace(hour=hour, minute=0, second=0, microsecond=0)


class FakeCalendar:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.params: list[dict[str, Any]] = []

    async def __call__(self, method: str, url: str, **kw: Any) -> Any:
        if url.endswith("/settings/timezone"):
            return {"value": TZ}
        if method == "GET" and url.endswith("/events"):
            return {"items": []}
        if method == "POST" and url.endswith("/events"):
            self.created.append(kw["json"])
            self.params.append(kw.get("params", {}))
            return {"id": "e1"}
        if method == "GET" and url.endswith("/events/e1"):
            ev = self.created[-1]
            return {
                "summary": ev["summary"],
                "start": {"dateTime": ev["start"]["dateTime"] + "-07:00"},
                "end": {"dateTime": ev["end"]["dateTime"] + "-07:00"},
                "attendees": ev.get("attendees", []),
                "hangoutLink": "https://meet.google.com/abc-defg-hij",
                "htmlLink": "https://calendar.google.com/event?eid=e1",
            }
        raise AssertionError(f"unexpected {method} {url}")


def connect(brain) -> FakeCalendar:
    g = brain.connectors.google
    g.token_path.parent.mkdir(parents=True, exist_ok=True)
    g.token_path.write_text("{}", encoding="utf-8")
    fake = FakeCalendar()
    g._api = fake  # type: ignore[method-assign]
    return fake


def test_only_addresses_the_user_said():
    assert said_by_user(["NabihaTR21@gmail.com"], "set it up with nabihatr21@gmail.com please")
    assert not said_by_user(["nabiha@gmail.com"], "set it up with nabihatr21@gmail.com")
    assert not said_by_user([], "anything")


def test_the_model_always_knows_the_date():
    block = now_block(TZ, datetime(2026, 11, 4, 13, 6, tzinfo=ZoneInfo(TZ)))
    assert block.startswith(
        "Now: Wednesday 4 November 2026, 1:06 PM (America/Vancouver, UTC-07:00)."
    )
    assert "tomorrow is Thu 5 Nov" in block and "Fri 6 Nov" in block


async def test_meeting_errand_is_quiet_checked_and_lands_in_the_notch(settings):
    start = tomorrow_at(15)
    said = "set up a google calendar meeting with nabihatr21@gmail.com tomorrow at 3pm"
    llm = Script(
        [
            (
                "errand",
                {
                    "task": "Create a 30-minute Google Calendar meeting 'Catch up' tomorrow at 3pm with nabihatr21@gmail.com, with a Meet link."
                },
            ),
            ("say", {"text": "On it, I'll let you know when it's set."}),
        ],
        [("calendar_list", {"days": 2})],
        [
            (
                "calendar_add",
                {
                    "title": "Catch up",
                    "start": start.strftime("%Y-%m-%dT%H:%M"),
                    "minutes": 30,
                    "attendees": ["nabihatr21@gmail.com"],
                },
            )
        ],
        [
            (
                "finish",
                {
                    "ok": True,
                    "title": "Meeting set with Nabiha",
                    "summary": f"Catch up is on {start:%A} at 3:00 PM with a Meet link.",
                },
            )
        ],
    )
    brain, rec = make_brain(settings, llm=llm)
    cal = connect(brain)
    await brain.turn(said)
    for _ in range(100):
        if rec.of("job_ready"):
            break
        await asyncio.sleep(0.02)
    says = rec.of("say")
    from pyxi.companion.brain import ACKS

    [said_back] = [s["text"] for s in says]  # and nothing else
    ack, rest = said_back.split(",", 1)  # a fresh acknowledgement each time, then the same line
    assert ack in ACKS and rest == " I'll let you know when it's set."
    assert not rec.of("confirm")  # the user named this address: no approval card needed
    [made] = cal.created
    # Google wants full RFC 3339 times, seconds included (without them it answers 400).
    assert made["start"] == {"dateTime": start.strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": TZ}
    assert made["attendees"] == [{"email": "nabihatr21@gmail.com"}]
    assert "conferenceData" in made and cal.params[-1]["conferenceDataVersion"] == 1
    [ready] = rec.of("job_ready")
    assert ready["ok"] and ready["title"] == "Meeting set with Nabiha"
    assert ready["link"] == "https://meet.google.com/abc-defg-hij" and ready["actions"] == ["link"]
    assert (
        "(Now: " in llm.prompts[0] or "Now: " in llm.prompts[0]
    )  # the conversation knows the date too
    assert "Now: " in llm.prompts[1]  # and so does every errand step


async def test_an_address_the_user_never_said_needs_approval(settings):
    start = tomorrow_at(10)
    llm = Script(
        [
            ("errand", {"task": "Invite Nabiha to coffee tomorrow at 10"}),
            ("say", {"text": "On it."}),
        ],
        [
            (
                "calendar_add",
                {
                    "title": "Coffee",
                    "start": start.strftime("%Y-%m-%dT%H:%M"),
                    "attendees": ["nabiha@example.com"],
                },
            )
        ],
        [("finish", {"ok": True, "title": "Coffee booked", "summary": "Done."})],
    )
    brain, rec = make_brain(settings, llm=llm)
    cal = connect(brain)
    await brain.turn("invite Nabiha to coffee tomorrow at 10")
    for _ in range(100):
        if rec.of("confirm"):
            break
        await asyncio.sleep(0.02)
    [ask] = rec.of("confirm")
    assert "nabiha@example.com" in ask["action"] and not cal.created  # nothing sent yet
    brain.hub.resolve(ask["request_id"], True)
    for _ in range(100):
        if rec.of("job_ready"):
            break
        await asyncio.sleep(0.02)
    assert cal.created and rec.of("job_ready")[0]["ok"]


async def test_calendar_rejects_the_past_and_bad_addresses(settings):
    brain, _ = make_brain(settings)
    connect(brain)
    g = brain.connectors.google
    past = (datetime.now(ZoneInfo(TZ)) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")
    out = await g.calendar_add({"title": "x", "start": past})
    assert "in the past" in out
    out = await g.calendar_add(
        {
            "title": "x",
            "start": tomorrow_at(9).strftime("%Y-%m-%dT%H:%M"),
            "attendees": ["nabiha at gmail"],
        }
    )
    assert "aren't valid email addresses" in out


async def test_the_conversation_can_read_accounts_but_actions_are_errands(settings):
    class Seeing(Script):
        async def complete(self, *, system, messages, tools=None, **kw):
            self.offered = {t.name for t in tools or []}
            return await super().complete(system=system, messages=messages, tools=tools, **kw)

    llm = Seeing([("say", {"text": "On it."})])
    brain, _ = make_brain(settings, llm=llm)
    connect(brain)
    await brain.turn("what's on my calendar?")
    # Reading happens in the conversation; anything that books, sends or creates is an errand.
    assert {"calendar_list", "gmail_search", "errand"} <= llm.offered
    assert not {"calendar_add", "gmail_send", "docs_create"} & llm.offered


async def test_lookups_dont_chatter_and_errors_are_human(settings):
    from pyxi.companion.brain import friendly_error, strip_filler

    llm = Script(
        [("say", {"text": "Let me check your calendar."}), ("calendar_list", {"days": 2})],
        [("say", {"text": "Still checking."}), ("calendar_list", {"days": 7})],
        [("say", {"text": "Nothing on Saturday yet."})],
    )
    brain, rec = make_brain(settings, llm=llm)
    connect(brain)
    await brain.turn("is my Saturday meeting saved?")
    await asyncio.sleep(0.1)
    said = " ".join(s["text"] for s in rec.of("say"))
    assert "Still checking" not in said and "Nothing on Saturday yet." in said
    raw = 'Google said 400: { "error": { "message": "Bad Request" } }'
    assert friendly_error(Exception(raw)) == "Google rejected the request: Bad Request."
    assert strip_filler("There you go, I have done this. It's saved.") == "It's saved."


async def test_a_sent_email_finishes_the_errand_without_another_model_round():
    from otto.llm.base import ToolSpec
    from otto.types import ToolCall, Usage

    from pyxi.companion.errands import run_errand
    from pyxi.connectors import Outgoing, Tool

    calls = 0

    class OneShot:
        async def complete(self, **kw):
            nonlocal calls
            calls += 1
            args = {"to": "sam@example.com", "subject": "Late", "body": "Ten minutes late, sorry."}
            return type(
                "R",
                (),
                {
                    "text": "",
                    "usage": Usage(),
                    "tool_calls": [ToolCall(id="t1", name="gmail_send", arguments=args)],
                },
            )()

    async def send(args):
        async def run():
            return "sent, subject 'Late'"

        return Outgoing(
            action="Send email to sam@example.com",
            preview="",
            run=run,
            recipients=["sam@example.com"],
        )

    spec = ToolSpec("gmail_send", "send", {"type": "object", "properties": {}})

    async def yes(_o):
        return True

    result = await run_errand(
        task="email sam",
        user_text="email sam@example.com",
        now="Now",
        llm=OneShot(),
        tools=[Tool(spec, send)],
        approve=yes,
    )
    assert result.ok and result.title == "Email sent" and calls == 1


def test_other_time_zones_are_measured_from_their_own_clock():
    """Live: the model said 10 AM Eastern was 7 AM in Vancouver, from its memory of the zones;
    British Columbia had stopped changing its clocks, and the answer was 8 AM. The differences
    come from the zone data on the PC, whatever it says that year."""
    from datetime import timedelta

    from pyxi.timecontext import elsewhere

    now = datetime(2026, 11, 10, 20, 0, tzinfo=ZoneInfo("America/Toronto"))
    text = elsewhere(now)
    assert "Eastern (New York, Toronto) the same time" in text
    assert "UK 5h ahead" in text and "India 10h30 ahead" in text and "Central (Chicago) 1h behind" in text
    # Whatever the data says for Vancouver is what the model is told (not what it remembers).
    van = datetime(2026, 11, 10, 20, 0, tzinfo=ZoneInfo("America/Vancouver"))
    gap = ZoneInfo("America/New_York").utcoffset(van.replace(tzinfo=None)) - van.utcoffset()
    hours = int(gap / timedelta(hours=1))
    assert f"Eastern (New York, Toronto) {hours}h ahead" in elsewhere(van)
    assert "never your own memory" in now_block("America/Vancouver", van)
