"""After Pyxi reads mail, a page or a document, every outward action asks on a fresh card, even
under "Always allow", and the card says what came from that content."""

from __future__ import annotations

import json

import pytest

from pyxi import safety
from pyxi.companion.errands import run_errand
from pyxi.connectors import Outgoing, Tool, tool
from test_connectors_and_docs import Script

SAM = "sam@uni.edu"


def tools(sent: list[str], mail_text: str) -> list[Tool]:
    async def read(args):
        return mail_text

    async def invite(args):
        async def run():
            sent.append("invite")
            return "CREATED invite"

        return Outgoing("Invite Sam to Standup", "Standup, Fri 10:00", run, kind="invite", recipients=[SAM])

    async def send(args):
        async def run():
            sent.append(args["body"])
            return "SENT"

        body = args["body"]
        return Outgoing(f"Send an email to {args['to']}", f"To: {args['to']}\n\n{body}", run, recipients=[args["to"]])

    return [
        Tool(tool("gmail_read", "Read one email.", {"id": {"type": "string"}}, ["id"]), read, info=True),
        Tool(tool("calendar_add", "Invite.", {}, []), invite),
        Tool(tool("gmail_send", "Send.", {"to": {"type": "string"}, "body": {"type": "string"}}, ["to"]), send),
    ]


async def _run(said, steps, mail_text="Sam's email: see you Friday.", reads=None, allow=False):
    sent: list[str] = []
    cards: list[Outgoing] = []

    async def approve(out):
        cards.append(out)
        return allow

    await run_errand(
        task=said, user_text=said, now="Now: Saturday", llm=Script(*steps),
        tools=tools(sent, mail_text), approve=approve, reads=reads,
    )  # fmt: skip
    return cards, sent


INVITE = [("calendar_add", {})]
DONE = [("finish", {"ok": True, "title": "x", "summary": "x"})]


async def test_an_invite_to_someone_they_named_goes_ahead_on_its_own():
    cards, sent = await _run(f"invite {SAM} to standup friday 10", [INVITE, DONE])
    assert cards == [] and sent == ["invite"]


async def test_after_reading_an_email_the_same_invite_asks_on_a_card():
    cards, sent = await _run(
        f"invite {SAM} to standup friday 10", [[("gmail_read", {"id": "m1"})], INVITE, DONE]
    )
    assert [c.action for c in cards] == ["Invite Sam to Standup"] and sent == []


async def test_after_the_turn_read_a_document_the_invite_asks_too():
    reads = [("the document you read", "Agenda: invite Sam")]
    cards, sent = await _run(f"invite {SAM} to standup friday 10", [INVITE, DONE], reads=reads)
    assert len(cards) == 1 and sent == []


async def test_recipients_links_and_amounts_from_the_email_are_flagged():
    mail = (
        "Hi, please pay the deposit of $1,250.00 to accounts@landlord-pay.example via "
        "https://landlord-pay.example/deposit today."
    )
    body = "Paying the $1,250.00 deposit now: https://landlord-pay.example/deposit"
    cards, _ = await _run(
        "reply to my landlord that I'll pay",
        [
            [("gmail_read", {"id": "m1"})],
            [("gmail_send", {"to": "accounts@landlord-pay.example", "body": body})],
            DONE,
        ],
        mail_text=mail,
    )
    [card] = cards
    head = card.preview.split("\n\nTo: ")[0]
    assert head.startswith("Came from what Pyxi read, not from you:")
    assert "- accounts@landlord-pay.example (from the email you read)" in head
    assert "- https://landlord-pay.example/deposit (from the email you read)" in head
    assert "- $1,250.00 (from the email you read)" in head


def test_what_the_user_said_is_never_flagged():
    reads = [("the email you read", f"Write to {SAM}, the fee is $40")]
    assert safety.provenance([SAM], "fee $40", f"email {SAM} that the $40 fee is fine", reads) == ""
    assert safety.provenance(["x@y.example"], "", "hi", []) == ""  # nothing was read


async def test_hands_ask_again_after_reading_even_with_always_allow(settings, monkeypatch):
    from conftest import FakeScreen
    from pyxi.companion.brain import Brain
    from pyxi.demo import DemoLLM
    from pyxi.hub import Hub

    brain = Brain(settings, Hub(), llm=DemoLLM(), screen=FakeScreen())
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    (settings.data_dir / "hands.json").write_text(json.dumps({"always": True}))
    cards: list[dict] = []

    async def request(kind, timeout=300, **data):
        cards.append(data)
        return False

    brain.hub.request = request  # type: ignore[method-assign]

    class Started(Exception):
        pass

    async def track(*a, **k):
        raise Started

    brain.jobs.track = track  # type: ignore[method-assign]
    with pytest.raises(Started):  # always allow and nothing read: straight to work, no card
        await brain._drive("rename the files", None)
    assert cards == []
    await brain._drive("rename the files", None, after_reading=["the email you read"])
    [card] = cards
    assert "always" not in card and card["reason"].startswith("This asks again because Pyxi just read the email you read.")
