"""A line that says a job is under way is not said until the tool has answered: "Putting a 5 by
5 table into your blank doc now." and then "I can't reach that doc" is one sentence too many."""

from __future__ import annotations

import asyncio

from pyxi.companion.brain import CAN_REFUSE, claims_work
from test_brain_and_server import make_brain
from test_connectors_and_docs import Script
from test_errands import connect
from test_streaming import Streamed

CLAIM = "Putting a 5 by 5 table into your blank doc now."
TABLE = {"task": "Add a 5 by 5 table", "google": ["this doc"]}  # Google isn't connected: refused


def said(rec) -> list[str]:
    return [s["text"] for s in rec.of("say")]


def watch(brain) -> list[str]:
    """The order things really happened in: each tool as it runs, each line as it is spoken."""
    order: list[str] = []
    act, speak = brain._act, brain._speak

    async def acting(name: str, args: dict):
        if name != "say":
            order.append(name)
        return await act(name, args)

    def speaking(text: str, **kw):
        order.append("say")
        return speak(text, **kw)

    brain._act, brain._speak = acting, speaking  # type: ignore[method-assign]
    return order


def test_which_lines_claim_that_work_is_under_way() -> None:
    for line in [
        CLAIM,
        "Ledger's drawing the pivot now.",
        "Scout's on the flights, back with the cheapest.",
        "A five by five table, coming up.",
        "Drafting that email to Sam.",
        "I'll add it to the sheet.",
    ]:
        assert claims_work(line), line
    for line in [
        "Real. Only the B version can hover.",
        "It's a $107.80 phone bill, due November 28.",
        "Which doc do you mean?",
    ]:
        assert not claims_work(line), line
    assert {"edit_document", "convert_files", "delegate", "book", "errand", "use_computer"} <= CAN_REFUSE


async def test_a_claim_is_dropped_when_the_tool_refuses(settings):
    llm = Script(
        [("say", {"text": CLAIM}), ("edit_document", TABLE)],
        [("say", {"text": "Google isn't connected yet, so I can't reach that doc."})],
    )
    brain, rec = make_brain(settings, llm=llm)
    await brain.turn("put a 5 by 5 table in my doc")
    await asyncio.sleep(0.05)
    assert said(rec) == ["Google isn't connected yet, so I can't reach that doc."]
    assert "edit_document: Google isn't connected" in llm.prompts[-1]  # it speaks from the answer
    assert CLAIM not in [c["arguments"].get("text") for c in brain._calls]


async def test_a_claim_after_the_tool_in_the_reply_is_dropped_too(settings):
    llm = Script(
        [("edit_document", TABLE), ("say", {"text": CLAIM})],
        [("say", {"text": "Google isn't connected yet."})],
    )
    brain, rec = make_brain(settings, llm=llm)
    await brain.turn("put a 5 by 5 table in my doc")
    await asyncio.sleep(0.05)
    assert said(rec) == ["Google isn't connected yet."]


async def test_a_claim_is_said_once_the_tool_has_agreed(settings):
    llm = Script(
        [
            ("say", {"text": "Drafting that email to Sam now."}),
            ("errand", {"task": "Email Sam that I'm running late"}),
        ]
    )
    brain, rec = make_brain(settings, llm=llm)
    connect(brain)

    async def fake_errand(task: str, user_text: str, **kw) -> None:
        return None

    brain._run_errand = fake_errand  # type: ignore[method-assign]
    order = watch(brain)
    await brain.turn("email Sam that I'm running late")
    await asyncio.sleep(0.05)
    assert order == ["errand", "say"]  # the tool answered first, then the line
    assert said(rec) == ["Drafting that email to Sam now."]
    assert len(llm.prompts) == 1


async def test_a_tool_that_says_its_own_line_replaces_the_claim(settings):
    llm = Script([("say", {"text": CLAIM}), ("edit_document", TABLE)])
    brain, rec = make_brain(settings, llm=llm)
    act = brain._act

    async def acting(name: str, args: dict):
        if name == "edit_document":  # what filework does when the open doc is out of reach
            brain._speak("That doc is in another Google account, so I'm making a fresh one in yours.")
            return "Started job_1: Ledger is making a new doc."
        return await act(name, args)

    brain._act = acting  # type: ignore[method-assign]
    await brain.turn("put a 5 by 5 table in my doc")
    await asyncio.sleep(0.05)
    assert said(rec) == ["That doc is in another Google account, so I'm making a fresh one in yours."]


async def test_a_tool_that_breaks_is_reported_instead_of_the_claim(settings):
    llm = Script(
        [
            ("say", {"text": "Scout's on the flights, back with the cheapest."}),
            ("delegate", {"agent": "scout", "task": "Find the cheapest flight"}),
        ],
        [("say", {"text": "Scout couldn't get started just then."})],
    )
    brain, rec = make_brain(settings, llm=llm)
    act = brain._act

    async def acting(name: str, args: dict):
        if name == "delegate":
            raise RuntimeError("no browser")
        return await act(name, args)

    brain._act = acting  # type: ignore[method-assign]
    await brain.turn("find me the cheapest flight to Toronto")
    await asyncio.sleep(0.05)
    assert said(rec) == ["Scout couldn't get started just then."]
    assert "delegate failed: no browser" in llm.prompts[-1]


async def test_a_streamed_claim_waits_for_a_tool_that_can_refuse(settings):
    llm = Streamed([("say", {"text": CLAIM}), ("edit_document", TABLE)])
    brain, rec = make_brain(settings, llm=llm)
    await brain.turn("put a 5 by 5 table in my doc")
    await asyncio.sleep(0.05)
    assert said(rec) == ["Okay."]  # the next round's line, not the claim


async def test_a_streamed_claim_is_still_said_early_when_nothing_can_refuse(settings):
    llm = Streamed(
        [
            ("say", {"text": "Notepad, coming up."}),
            ("quick_action", {"action": "open_app", "value": "notepad"}),
        ]
    )
    brain, rec = make_brain(settings, llm=llm)

    async def fake_quick(action: str, value: str) -> None:
        return None

    brain._quick_action = fake_quick  # type: ignore[method-assign]
    order = watch(brain)
    await brain.turn("get me notepad please")
    await asyncio.sleep(0.05)
    assert order == ["say", "quick_action"]  # said while the rest of the reply was arriving
    assert said(rec) == ["Notepad, coming up."]


def test_the_prompt_says_a_claim_waits_for_the_tool() -> None:
    from pyxi.companion import prompts

    assert "Some tools can say no" in prompts.SYSTEM
    assert "spoken only once the tool has agreed" in " ".join(prompts.SYSTEM.split())
