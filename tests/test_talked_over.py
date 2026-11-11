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
    # The conversation holds one request and its answer, not an orphan half. The turn log
    # keeps the half that was talked over, marked, so the history still shows it happened.
    assert brain.history == [(f"{FIRST} {SECOND}", "Both.")]
    turn, half = brain.turns.recent()
    assert turn["user_text"] == f"{FIRST} {SECOND}" and turn["error"] is None
    assert half["user_text"] == FIRST and half["error"] == "carried into the next turn"
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


async def test_a_tool_that_said_no_is_not_passed_on_as_done(settings):
    """The call is noted before it runs. A refusal used to reach the next turn as "already
    started or done ... never redo", so the thing was never done at all."""
    llm = Script(
        [("quick_action", {"action": "open_url", "value": "https://example.com"})],
        [("say", {"text": "Full screen it is."})],
    )
    brain, _ = make_brain(settings, llm=llm)

    async def fake_quick(action: str, value: str) -> str:
        brain.begin_listening()  # they speak again as it fails
        return "quick_action: that address didn't open."

    brain._quick_action = fake_quick  # type: ignore[method-assign]
    await brain.turn("Open the example page")
    assert brain._calls[0]["outcome"] == "refused" and brain._done_so_far() == []
    await brain.turn("and make it full screen.")
    prompt = llm.prompts[-1]
    assert "Already started or done" not in prompt
    # Nothing was done, so both halves are asked as one, and the refusal goes with them.
    assert asked(llm) == "Open the example page and make it full screen."
    assert "did not go through (it is NOT done)" in prompt
    assert "quick_action: that address didn't open." in prompt


async def test_a_refusal_beside_something_done_is_told_apart(settings):
    llm = Script(
        [
            ("quick_action", {"action": "open_url", "value": "https://www.amazon.com"}),
            ("paste_table", {"rows": []}),
        ],
        [("say", {"text": "Okay."})],
    )
    brain, _ = make_brain(settings, llm=llm)

    async def fake_quick(action: str, value: str) -> None:
        return None

    real = brain._paste_table

    async def paste(args: dict):
        note = await real(args)
        brain.begin_listening()  # they speak again as the paste is refused
        return note

    brain._quick_action = fake_quick  # type: ignore[method-assign]
    brain._paste_table = paste  # type: ignore[method-assign]
    await brain.turn("Get Amazon up and paste my list in")
    assert brain._done_so_far() == ["quick_action (action: open_url, value: https://www.amazon.com)"]
    await brain.turn("the short list, I mean.")
    prompt = llm.prompts[-1]
    done = prompt.split("Already started or done before the cut: ")[1].split(". Their new")[0]
    assert "quick_action" in done and "paste_table" not in done
    assert "did not go through (it is NOT done): paste_table: give rows" in prompt


async def test_what_was_already_said_goes_with_the_carried_words(settings, monkeypatch):
    """It had spoken and looked something up, but done nothing, when they went on: the turn
    left no trace, and the one that carried its words on said the same line again."""
    from pyxi import web

    async def fake_search(query, max_results=6):
        return "Results: the first phone has the better camera."

    monkeypatch.setattr(web, "search_text", fake_search)
    llm = Slow(
        [
            ("say", {"text": "Comparing the two on battery and screen."}),
            ("web_search", {"query": "phone one vs phone two"}),
        ],
        [("say", {"text": "Never said."})],
        [("say", {"text": "The first one, and its camera is better too."})],
    )
    brain, rec = make_brain(settings, llm=llm)
    first = asyncio.create_task(brain.turn(FIRST))
    await thinking(llm, 2)  # it has spoken and searched, and is thinking about the answer
    brain.begin_listening()
    await brain.turn(SECOND)
    await first
    await asyncio.sleep(0.05)
    assert asked(llm) == f"{FIRST} {SECOND}"
    assert (
        'you had already said aloud: "Comparing the two on battery and screen."' in llm.prompts[-1]
    )
    assert [s["text"] for s in rec.of("say")][-1] == "The first one, and its camera is better too."


async def test_the_same_request_said_again_is_one_request(settings):
    """Live: nothing had happened three seconds after "Create a sample sales data.", so they
    said it again, and the model was handed the sentence twice in a row."""
    llm = Slow([("say", {"text": "Dropped."})], [("say", {"text": "Once."})])
    brain, _ = make_brain(settings, llm=llm)
    first = asyncio.create_task(brain.turn("Create a sample sales data."))
    await thinking(llm)
    brain.begin_listening()
    await brain.turn("Create a sample sales data, please.")
    await first
    assert asked(llm) == "Create a sample sales data, please."


async def test_a_sentence_that_stops_mid_way_waits_for_its_other_half(settings, monkeypatch):
    """Live: "Can you open another tab on" (they let go of the key to think), and Pyxi opened
    a tab at once. It waits a moment; when the key goes down again, both halves are one
    request, and when it doesn't, the half is answered as it is."""
    from pyxi.companion import brain as brain_mod

    monkeypatch.setattr(brain_mod, "HALF_SAID_WAIT_S", 0.3)
    llm = Script([("say", {"text": "Both."})], [("say", {"text": "Half."})], [("say", {"text": "Whole."})])
    brain, _ = make_brain(settings, llm=llm)
    heard = iter(["Can you open another tab on", "Chrome with YouTube in it.", "Open another tab on", "Turn it on."])

    async def transcribe(audio, language="en"):
        return next(heard), 0.1, "en"

    monkeypatch.setattr(brain.stt, "transcribe", transcribe)
    first = asyncio.create_task(brain.hear(b"clip"))
    await asyncio.sleep(0.1)
    assert llm.prompts == []  # nothing is done on the half
    brain.begin_listening()
    await first
    await brain.hear(b"clip")
    assert asked(llm) == "Can you open another tab on Chrome with YouTube in it."
    # They don't go on: the half is the request after all.
    await brain.hear(b"clip")
    assert asked(llm) == "Open another tab on"
    # A whole sentence that happens to end on such a word is not held up.
    await brain.hear(b"clip")
    assert asked(llm) == "Turn it on."
