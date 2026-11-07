"""Hourly limits on outward actions: at 10 emails (5 posts, 10 invites, 3 bookings) Pyxi stops and
asks before the next one. The limits are settings."""

from __future__ import annotations

from pyxi.companion.errands import run_errand
from pyxi.config import ActionLimits, PyxiSettings
from pyxi.connectors import Outgoing, Tool, tool
from pyxi.limits import Limits
from test_connectors_and_docs import Script


def test_counts_last_an_hour_and_survive_a_restart(tmp_path):
    lim = Limits(tmp_path, ActionLimits(emails_per_hour=2))
    lim.record("email", now=1000)
    lim.record("email", now=2000)
    assert lim.reached("email", now=2001).startswith("Pyxi has already sent 2 emails in the last hour")
    assert Limits(tmp_path, ActionLimits(emails_per_hour=2)).reached("email", now=2001)  # on disk
    assert lim.reached("email", now=1000 + 3601) == ""  # the first one aged out
    assert lim.reached("post", now=2001) == "" and lim.reached("unknown") == ""


def test_the_defaults_and_settings(monkeypatch):
    d = ActionLimits()
    assert (d.emails_per_hour, d.posts_per_hour, d.invites_per_hour, d.bookings_per_hour) == (10, 5, 10, 3)
    monkeypatch.setenv("PYXI_LIMITS__EMAILS_PER_HOUR", "4")
    assert PyxiSettings(_env_file=None).limits.emails_per_hour == 4


def _send_tool(sent: list[str]) -> list[Tool]:
    async def send(args):
        async def run():
            sent.append(args["to"])
            return "Sent."

        return Outgoing(f"Send an email to {args['to']}", "To: ...", run, recipients=[args["to"]], channel="email")

    return [Tool(tool("gmail_send", "Send.", {"to": {"type": "string"}}, ["to"]), send)]


async def test_an_errand_at_the_limit_stops_and_asks(settings):
    from conftest import FakeScreen
    from pyxi.companion.brain import Brain
    from pyxi.demo import DemoLLM
    from pyxi.hub import Hub

    settings = settings.model_copy(update={"limits": ActionLimits(emails_per_hour=1)})
    brain = Brain(settings, Hub(), llm=DemoLLM(), screen=FakeScreen())
    asked: list[dict] = []
    answer = {"Keep going past the hourly limit?": False}

    async def request(kind, timeout=300, **data):
        asked.append(data)
        return answer.get(data["action"], True)

    brain.hub.request = request  # type: ignore[method-assign]
    sent: list[str] = []
    said = "email sam@uni.edu and alex@work.com"

    async def errand(script):
        return await run_errand(
            task=said, user_text=said, now="Now", llm=Script(*script), tools=_send_tool(sent),
            approve=lambda out: request("confirm", action=out.action),
            gate=lambda out: brain._within_limits(out.channel, out.action), on_sent=brain._count_sent,
        )  # fmt: skip

    first = await errand([[("gmail_send", {"to": "sam@uni.edu"})]])
    assert first.ok and sent == ["sam@uni.edu"]
    second = await errand([[("gmail_send", {"to": "alex@work.com"})]])
    assert not second.ok and second.title == "Stopped at the hourly limit" and sent == ["sam@uni.edu"]
    stop = [a for a in asked if a["action"] == "Keep going past the hourly limit?"]
    assert len(stop) == 1 and "already sent 1 emails" in stop[0]["reason"]
    assert "Next: Send an email to alex@work.com" in stop[0]["reason"]
    # They choose to carry on: it goes, after its own card.
    answer["Keep going past the hourly limit?"] = True
    third = await errand([[("gmail_send", {"to": "alex@work.com"})]])
    assert third.ok and sent == ["sam@uni.edu", "alex@work.com"]


async def test_bookings_stop_at_the_limit(settings, monkeypatch):
    from conftest import FakeScreen
    from pyxi import booking
    from pyxi.companion.brain import Brain
    from pyxi.demo import DemoLLM
    from pyxi.hub import Hub

    settings = settings.model_copy(update={"limits": ActionLimits(bookings_per_hour=0)})
    brain = Brain(settings, Hub(), llm=DemoLLM(), screen=FakeScreen())
    monkeypatch.setattr(booking, "ready", lambda p: True)
    asked: list[str] = []

    async def request(kind, timeout=300, **data):
        asked.append(data["action"])
        return False

    brain.hub.request = request  # type: ignore[method-assign]
    out = await brain._book({"what": "a haircut", "where": "Clip Joint"})
    assert "hourly limit" in out and asked == ["Keep going past the hourly limit?"]
