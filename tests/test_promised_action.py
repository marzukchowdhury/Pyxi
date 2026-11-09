"""Says it, then does it: a turn that promised an action no tool call carried out gets one
nudge to do it or say plainly that it can't ("You haven't completed the work")."""

from __future__ import annotations

import asyncio

from pyxi.companion.brain import undone_promise
from test_brain_and_server import make_brain
from test_connectors_and_docs import Script

PROMISE = "Heading to Display to lower the brightness."
ASK = "Go to display and lower the brightness"
OPENED = {"tool": "quick_action", "arguments": {"action": "open_app", "value": "settings"}}
SAID = {"tool": "say", "arguments": {"text": PROMISE}}


def test_opening_the_app_is_not_doing_the_thing_in_it() -> None:
    assert undone_promise([PROMISE], [SAID, OPENED], ASK) == (PROMISE, True)
    line = "Opening Settings and turning the brightness down."
    assert undone_promise([line], [OPENED], ASK) == (line, True)
    # it only said it would open it, and it did
    assert undone_promise(["Opening Display settings."], [OPENED], ASK) is None
    # opening it was the whole request
    assert undone_promise([PROMISE], [OPENED], "open settings") is None


def test_a_promise_with_nothing_done_at_all() -> None:
    for line in [
        "I'll lower the brightness now.",
        "Okay, let me turn that down.",
        "Lowering the brightness now.",
        "I'm going to add a totals row.",
    ]:
        assert undone_promise([line], [{"tool": "say", "arguments": {}}], ASK) == (line, False), line
    # a lookup is not the action either
    looked = [{"tool": "look_again", "arguments": {}}]
    assert undone_promise(["I'll lower the brightness now."], looked, ASK) is not None


def test_what_is_not_a_promise_or_was_kept() -> None:
    nothing: list[dict] = []
    for line in [
        "Want me to lower the brightness?",  # an offer
        "I can't change the brightness from here.",
        "I'll lower it if you allow the mouse.",
        "Open Display, then drag the brightness slider down.",  # telling them how
        "The brightness is at 40 percent.",
        "I'll put it this way: it's bright.",
    ]:
        assert undone_promise([line], nothing, ASK) is None, line
    # a chat, not a job
    assert undone_promise(["I'll be honest, forty tabs is a lot."], nothing, "what about my tabs?") is None
    assert undone_promise(["I'll change it for you."], nothing, "how do I change the brightness?") is None
    # something did carry it out: the mouse, a teammate, an errand
    for tool in ("use_computer", "delegate", "errand", "edit_document", "play_music"):
        done = [OPENED, {"tool": tool, "arguments": {"task": "x"}}]
        assert undone_promise([PROMISE], done, ASK) is None, tool


async def test_a_turn_that_only_opened_the_app_is_sent_back_to_finish(settings):
    llm = Script(
        [("say", {"text": PROMISE}), ("quick_action", {"action": "open_app", "value": "settings"})],
        [("use_computer", {"task": "In Display settings, lower the brightness"})],
    )
    brain, rec = make_brain(settings, llm=llm)
    did: list[str] = []

    async def fake_quick(action: str, value: str) -> None:
        did.append(f"{action} {value}")

    async def fake_hands(args: dict) -> None:
        did.append(f"hands: {args['task']}")

    brain._quick_action = fake_quick  # type: ignore[method-assign]
    brain._use_computer = fake_hands  # type: ignore[method-assign]
    await brain.turn(ASK)
    await asyncio.sleep(0.05)
    assert did == ["open_app settings", "hands: In Display settings, lower the brightness"]
    nudge = llm.prompts[1]
    assert f'You told them: "{PROMISE}"' in nudge and "opening it is not doing it" in nudge
    assert "say plainly" in nudge or "tell them plainly" in nudge
    assert [s["text"] for s in rec.of("say")] == [PROMISE]  # not said again


async def test_the_nudge_comes_once_a_turn_at_most(settings):
    llm = Script(
        [("say", {"text": PROMISE}), ("quick_action", {"action": "open_app", "value": "settings"})],
        [("say", {"text": "I'll set the slider lower now."})],  # promises again, does nothing
        [("say", {"text": "Never asked for."})],
    )
    brain, _ = make_brain(settings, llm=llm)

    async def fake_quick(action: str, value: str) -> None:
        return None

    brain._quick_action = fake_quick  # type: ignore[method-assign]
    await brain.turn(ASK)
    await asyncio.sleep(0.05)
    assert len(llm.prompts) == 2
    assert sum("You told them:" in p for p in llm.prompts) == 1


async def test_a_plain_reply_that_only_promises_is_nudged_too(settings):
    llm = Script(
        "I'll lower the brightness now.",  # no tool call at all
        [("say", {"text": "That one's out of my reach: the slider is in Display settings."})],
    )
    brain, rec = make_brain(settings, llm=llm)
    await brain.turn("lower the brightness")
    await asyncio.sleep(0.05)
    assert len(llm.prompts) == 2 and "You told them:" in llm.prompts[1]
    assert "out of my reach" in " ".join(s["text"] for s in rec.of("say"))
    # nothing was opened, so the words may have been the job ("Writing one now." and the poem)
    assert "If what you said was itself the whole job, add nothing." in llm.prompts[1]
    assert "opening it is not doing it" not in llm.prompts[1]


async def test_no_nudge_when_it_was_done_or_was_only_talk(settings):
    llm = Script(
        [
            ("say", {"text": "Lowering the brightness now."}),
            ("use_computer", {"task": "Lower the brightness"}),
        ]
    )
    brain, _ = make_brain(settings, llm=llm)

    async def fake_hands(args: dict) -> None:
        return None

    brain._use_computer = fake_hands  # type: ignore[method-assign]
    await brain.turn("lower the brightness")
    assert len(llm.prompts) == 1

    chat = Script([("say", {"text": "I'll be honest, forty tabs is a lot for one small firefly."})])
    brain, _ = make_brain(settings, llm=chat)
    await brain.turn("what do you make of my tabs?")
    assert len(chat.prompts) == 1


def test_the_prompt_says_opening_it_is_not_doing_it() -> None:
    from pyxi.companion import prompts

    assert "the tool call that does it goes in the same reply" in prompts.SYSTEM
