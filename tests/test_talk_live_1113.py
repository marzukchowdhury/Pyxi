"""How Pyxi talks, from a live session on 13 November.

Three things heard that night:
- "Just stop the work." was answered "Stopping. I won't start anything else." and the hands
  carried on three seconds later. Only the bare word "stop" stopped anything.
- "Bold the heading and make it red" got "Already on it." with a highlight drawn on the screen
  and nothing started; half a minute later: "Are you doing the work? I don't see you doing it."
- Asked for a table, Pyxi began with the last job's whole story ("I clicked the AI Sheets AI
  .xlsx button in GenOffice, and the AI Sheets spreadsheet workspace is now open, still
  streaming some data in"), because that job ended while the new request was being answered.
"""

from __future__ import annotations

import asyncio

from pyxi.companion.brain import _RESERVATION, says_on_it
from pyxi.companion.instant import is_stop
from test_brain_and_server import make_brain
from test_connectors_and_docs import Script


def test_stopping_the_work_is_stop_however_it_is_put():
    for words in (
        "Just stop the work.",
        "stop what you're doing",
        "Pyxi, stop working",
        "can you stop doing that please",
        "cancel the task",
        "stop the job now",
        "Okay, stop everything you are doing.",
        "Stop.",
        "never mind",
    ):
        assert is_stop(words), words
    # Stopping something else is a request of its own, and a question is a question.
    for words in (
        "stop the music",
        "stop sharing my screen",
        "just stop the video",
        "when should I stop working today?",
        "cancel my subscription",
        "stop at the shop on the way",
    ):
        assert not is_stop(words), words


def test_what_counts_as_saying_it_is_on_it():
    for line in ("Stopped, as you asked. Already on it.", "On it.", "Making the headers bold and red now.", "Doing that now.", "I'll get that done right away."):
        assert says_on_it(line), line
    for line in ("I am not on it yet.", "The header row is bold.", "Now for the fun part: it's 4.", "Want me to make them red?"):
        assert not says_on_it(line), line


def test_what_is_still_worth_saying_after_a_job_that_worked():
    # A story of what was clicked is not: they watched it.
    assert not _RESERVATION.search("I clicked the AI Sheets button, and the workspace is now open, still streaming some data in.")
    assert not _RESERVATION.search("The brightness is at 40 percent now.")
    # A real "but", or a question, is.
    assert _RESERVATION.search("The header row is bold and red, but the data rows also look red.")
    assert _RESERVATION.search("Renamed it. Should I rename the tab too?")
    assert _RESERVATION.search("Saved, though I couldn't set the colour.")


ASK = "No, just bold the heading for me, bold it and make it red"


async def test_on_it_with_only_a_highlight_is_sent_back_to_start_the_job(settings):
    llm = Script(
        [("say", {"text": "Already on it."}), ("highlight", {"element_id": 1})],
        [("use_computer", {"task": "Make the headings in row 1 bold and red"})],
    )
    brain, _rec = make_brain(settings, llm=llm)
    did: list[str] = []

    async def fake_hands(args: dict) -> None:
        did.append(args["task"])

    brain._use_computer = fake_hands  # type: ignore[method-assign]
    await brain.turn(ASK)
    await asyncio.sleep(0.05)
    assert did == ["Make the headings in row 1 bold and red"]
    assert "started nothing" in llm.prompts[1] and "use_computer" in llm.prompts[1]


async def test_it_is_told_once_and_a_job_that_did_start_is_left_alone(settings):
    llm = Script(
        [("say", {"text": "On it."})],
        [("say", {"text": "On it, really."})],  # says it again: not told twice
        [("say", {"text": "Never asked for."})],
    )
    brain, _ = make_brain(settings, llm=llm)
    await brain.turn(ASK)
    await asyncio.sleep(0.05)
    assert sum("started nothing" in p for p in llm.prompts) == 1

    # "On it." beside the tool that does the work is just what it is.
    llm = Script([("say", {"text": "On it."}), ("use_computer", {"task": "Make the headings bold"})])
    brain, _ = make_brain(settings, llm=llm)

    async def fake_hands(args: dict) -> None:
        return None

    brain._use_computer = fake_hands  # type: ignore[method-assign]
    await brain.turn(ASK)
    await asyncio.sleep(0.05)
    assert not any("started nothing" in p for p in llm.prompts)
