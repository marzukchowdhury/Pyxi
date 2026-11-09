"""They kept talking, or paused mid-sentence and the request arrived in two parts: the turn that
had started on the first part is cut off, and nothing they said is lost."""

from __future__ import annotations

import asyncio

from test_brain_and_server import make_brain
from test_connectors_and_docs import Script

FIRST = "Compare these two phones for me"
SECOND = "and tell me which has the better camera."


class Slow(Script):
    """The model takes a moment, as the real one does: long enough to be talked over."""

    async def complete(self, **kw):
        reply = await super().complete(**kw)
        await asyncio.sleep(0.2)
        return reply


async def thinking(llm: Script, calls: int = 1) -> None:
    """Wait until the model has been asked (it is now mid-thought)."""
    for _ in range(200):
        if len(llm.prompts) >= calls:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the model was never asked")


def asked(llm: Script, i: int = -1) -> str:
    """What the model was given as the user's words in one of its calls."""
    return llm.prompts[i].split("# The user says\n")[-1]


async def test_a_request_said_in_two_parts_is_answered_as_one(settings):
    llm = Slow([("say", {"text": "The first half's answer."})], [("say", {"text": "Both."})])
    brain, rec = make_brain(settings, llm=llm)
    first = asyncio.create_task(brain.turn(FIRST))
    await thinking(llm)
    brain.begin_listening()  # they press talk and go on
    await brain.turn(SECOND)
    await first
    await asyncio.sleep(0.05)
    assert asked(llm) == f"{FIRST} {SECOND}"  # both halves, in order, once
    said = [s["text"] for s in rec.of("say")]
    assert said == ["Both."]
    # The conversation and the turn log hold one request and its answer, not an orphan half.
    assert brain.history == [(f"{FIRST} {SECOND}", "Both.")]
    [turn] = brain.turns.recent()
    assert turn["user_text"] == f"{FIRST} {SECOND}" and turn["error"] is None
    # On screen they see what they said each time, not the first half twice.
    assert [u["text"] for u in rec.of("user")] == [FIRST, SECOND]


async def test_typing_again_while_it_thinks_carries_the_words_too(settings):
    llm = Slow([("say", {"text": "Dropped."})], [("say", {"text": "Both."})])
    brain, _ = make_brain(settings, llm=llm)
    first = asyncio.create_task(brain.turn(FIRST))
    await thinking(llm)
    brain.interrupt()  # the text box sends an interrupt, then the new words
    await brain.turn(SECOND)
    await first
    assert asked(llm) == f"{FIRST} {SECOND}"
    assert brain.history == [(f"{FIRST} {SECOND}", "Both.")]


async def test_three_parts_in_a_row_all_arrive(settings):
    llm = Slow([("say", {"text": "One."})], [("say", {"text": "Two."})], [("say", {"text": "All."})])
    brain, _ = make_brain(settings, llm=llm)
    first = asyncio.create_task(brain.turn(FIRST))
    await thinking(llm)
    brain.begin_listening()
    second = asyncio.create_task(brain.turn(SECOND))
    await first
    await thinking(llm, 2)  # the second part is being answered now
    brain.begin_listening()
    await brain.turn("Under 500 dollars.")
    await second
    assert asked(llm) == f"{FIRST} {SECOND} Under 500 dollars."
    assert [u for u, _ in brain.history] == [f"{FIRST} {SECOND} Under 500 dollars."]


async def test_escape_alone_does_not_carry_the_words_on(settings):
    llm = Slow([("say", {"text": "Dropped."})], [("say", {"text": "Everest."})])
    brain, _ = make_brain(settings, llm=llm)
    first = asyncio.create_task(brain.turn(FIRST))
    await thinking(llm)
    brain.interrupt()  # dismissed, with nothing more said
    await first
    await brain.turn("What is the tallest mountain?")
    assert asked(llm) == "What is the tallest mountain?"
    assert brain.history == [(FIRST, ""), ("What is the tallest mountain?", "Everest.")]
    assert [t["error"] for t in brain.turns.recent()] == [None, "interrupted"]


async def test_dismissed_words_are_not_picked_up_while_waiting_behind_a_routine(settings):
    llm = Slow(
        [("say", {"text": "Dropped."})],
        [("say", {"text": "Sunny all day."})],
        [("say", {"text": "Everest."})],
    )
    brain, _ = make_brain(settings, llm=llm)
    first = asyncio.create_task(brain.turn(FIRST))
    await thinking(llm)
    brain.interrupt()  # dismissed with Escape
    await first
    routine = asyncio.create_task(brain.turn("(Routine: say the forecast)", synthetic=True))
    await thinking(llm, 2)
    await brain.turn("What is the tallest mountain?")  # sent while the routine is still running
    await routine
    assert asked(llm) == "What is the tallest mountain?"


async def test_old_words_are_not_carried_into_a_much_later_turn(settings, monkeypatch):
    from pyxi.companion import brain as brain_module

    llm = Slow([("say", {"text": "Dropped."})], [("say", {"text": "Everest."})])
    brain, _ = make_brain(settings, llm=llm)
    first = asyncio.create_task(brain.turn(FIRST))
    await thinking(llm)
    brain.begin_listening()  # pressed talk, then let go without saying anything
    await first
    monkeypatch.setattr(brain_module, "_CARRY_S", -1.0)
    await brain.turn("What is the tallest mountain?")
    assert asked(llm) == "What is the tallest mountain?"


async def test_the_stop_button_ends_it_and_nothing_is_carried_on(settings):
    llm = Slow([("say", {"text": "Dropped."})], [("say", {"text": "Everest."})])
    brain, _ = make_brain(settings, llm=llm)
    brain._spawn(brain.turn(FIRST))  # as the server starts it
    await thinking(llm)
    brain.begin_listening()
    await brain.stop_everything()  # the stop button, after they had started to speak
    await asyncio.sleep(0.05)
    assert brain._dropped is None
    await brain.turn("What is the tallest mountain?")
    assert asked(llm) == "What is the tallest mountain?"
    # as before: the stopped turn is cancelled and stays in the log as "stopped"
    stopped = brain.turns.recent()[-1]
    assert stopped["user_text"] == FIRST and stopped["error"] == "stopped"
    assert brain.history[0] == (FIRST, "")


async def test_a_turn_ended_by_stop_without_being_cancelled_is_not_carried_on_either(settings):
    llm = Slow([("say", {"text": "Dropped."})], [("say", {"text": "Everest."})])
    brain, _ = make_brain(settings, llm=llm)
    first = asyncio.create_task(brain.turn(FIRST))  # not one of the brain's own tasks
    await thinking(llm)
    brain.begin_listening()
    await brain.stop_everything()
    await first
    assert brain._dropped is None
    await brain.turn("What is the tallest mountain?")
    assert asked(llm) == "What is the tallest mountain?"


async def test_saying_stop_over_a_turn_stops_and_does_not_run_it_again(settings):
    llm = Slow([("say", {"text": "Dropped."})], [("say", {"text": "Everest."})])
    brain, _ = make_brain(settings, llm=llm)
    first = asyncio.create_task(brain.turn(FIRST))
    await thinking(llm)
    brain.begin_listening()
    await brain.turn("Stop.")
    await first
    assert [c["tool"] for c in brain._calls] == ["stop_everything"]
    assert len(llm.prompts) == 1  # the model was never asked to "compare ... Stop."
    await brain.turn("What is the tallest mountain?")
    assert asked(llm) == "What is the tallest mountain?"


async def test_a_turn_that_had_already_acted_says_what_it_did(settings):
    """They speak again while Pyxi is mid-action: the rest of that round is not carried out
    behind their back, and the next turn is told what was already done."""
    llm = Script(
        [
            ("quick_action", {"action": "open_url", "value": "https://www.amazon.com"}),
            ("errand", {"task": "Email the order to Sam"}),
        ],
        [("say", {"text": "The table results are up."})],
    )
    brain, _ = make_brain(settings, llm=llm)
    opened: list[str] = []

    async def fake_quick(action: str, value: str) -> None:
        opened.append(value)
        brain.begin_listening()  # they start talking while the page opens

    brain._quick_action = fake_quick  # type: ignore[method-assign]
    await brain.turn("Get Amazon up for my order")
    assert opened == ["https://www.amazon.com"]
    assert [c["tool"] for c in brain._calls] == ["quick_action"]  # the errand never started
    assert len(llm.prompts) == 1  # and no model round for an answer that would be dropped
    await brain.turn("and search it for an IKEA table.")
    prompt = llm.prompts[-1]
    assert asked(llm) == "and search it for an IKEA table."  # not asked to open it again
    assert "Your last turn was cut off" in prompt and '"Get Amazon up for my order"' in prompt
    assert "quick_action (action: open_url, value: https://www.amazon.com)" in prompt
    first, second = brain.history
    assert first[0] == "Get Amazon up for my order" and "Cut off here" in first[1]
    assert second == ("and search it for an IKEA table.", "The table results are up.")
    assert [t["error"] for t in brain.turns.recent()] == [None, "interrupted"]


async def test_no_model_round_is_spent_once_they_have_spoken_again(settings):
    llm = Script(
        [("quick_action", {"action": "open_url", "value": "https://example.com"})],
        [("say", {"text": "Never said."})],
    )
    brain, rec = make_brain(settings, llm=llm)

    async def fake_quick(action: str, value: str) -> str:
        brain.begin_listening()
        return "quick_action: that didn't open."  # a problem: normally worth another round

    brain._quick_action = fake_quick  # type: ignore[method-assign]
    await brain.turn("Open the example page")
    await asyncio.sleep(0.05)
    assert len(llm.prompts) == 1 and not rec.of("say")
