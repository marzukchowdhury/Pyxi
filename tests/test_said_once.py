"""One thing is said once a turn: a second `say` with the same content, or the same start line
in other words, is dropped. What came of the work is news, and always stays."""

from __future__ import annotations

import asyncio

from pyxi.companion.brain import drop_repeats, said_before
from test_brain_and_server import make_brain
from test_connectors_and_docs import Script


def test_the_start_line_again_in_other_words_is_dropped() -> None:
    said = ["Putting the 5 by 5 table in now."]
    assert drop_repeats("The table's going in now.", said) == ""
    assert said_before("The table's going in now.", said)
    assert said_before("Okay, the table is going in.", said)
    assert said_before("The table, coming up.", said)


def test_the_same_short_line_is_dropped_however_it_is_written() -> None:
    assert said_before("Opening Settings.", ["Opening Settings."])
    assert said_before("  opening settings!  ", ["Opening Settings."])
    assert said_before("Opening   Settings", ["On to the display. Opening Settings."])
    # a longer one too, with different case and punctuation
    said = ["Putting a 5 by 5 table into your blank doc now."]
    assert said_before("putting a 5 by 5 table, into your blank doc now!", said)


def test_what_came_of_it_and_new_things_are_kept() -> None:
    said = ["Putting the 5 by 5 table in now."]
    for line in [
        "The table is in.",  # the result
        "The table's in now.",
        "The table isn't going in: that doc is read only.",  # it went wrong
        "The table's going into a new doc instead.",
        "Lowering the brightness now.",  # another job
        "It's five rows by five columns.",
    ]:
        assert drop_repeats(line, said) == line, line
        assert not said_before(line, said), line
    # a word that only looks like work under way
    assert drop_repeats("It's a string.", ["Reading the string on line 4."]) == "It's a string."
    # nothing said yet: nothing is a repeat
    assert not said_before("Opening Settings.", [])
    # a line with a mark keeps its mark, so the call is not dropped
    assert not said_before("[point:4] Opening Settings.", ["Opening Settings."])


async def test_a_repeated_say_is_dropped_from_the_turn(settings):
    llm = Script(
        [
            ("say", {"text": "Putting the 5 by 5 table in now."}),
            ("look_closer", {"element_id": 4}),  # a lookup: the model gets another round
        ],
        [
            ("say", {"text": "The table's going in now."}),
            ("say", {"text": "putting the 5 by 5 table in, now."}),
            ("say", {"text": "It has a header row."}),
            ("say", {"text": "It has a header row."}),
        ],
    )
    brain, rec = make_brain(settings, llm=llm)
    await brain.turn("what is happening with the table?")
    await asyncio.sleep(0.05)
    assert [s["text"] for s in rec.of("say")] == [
        "Putting the 5 by 5 table in now.",
        "It has a header row.",
    ]
    says = [c["arguments"]["text"] for c in brain._calls if c["tool"] == "say"]
    assert says == ["Putting the 5 by 5 table in now.", "It has a header row."]


def test_the_prompt_says_each_thing_is_said_once() -> None:
    from pyxi.companion import prompts

    assert "Say each thing once in a turn" in prompts.SYSTEM


def test_the_result_of_a_job_is_not_a_repeat_of_the_line_that_started_it() -> None:
    """Almost the same words, and the one that differs is the news: it is done."""
    for start, result in [
        ("Lowering the brightness to 40 percent now.", "Lowered the brightness to 40 percent."),
        ("Setting the brightness to 40 percent now.", "Set the brightness to 40 percent."),
        ("Changing the wallpaper to the blue one.", "Changed the wallpaper to the blue one."),
        ("Sending the report to Sam now.", "Sent the report to Sam."),
        ("Moving the three files to Archive.", "I've moved the three files to Archive."),
    ]:
        assert drop_repeats(result, [start]) == result, result
        assert not said_before(result, [start]), result
    # still only on its way, in other words: that is the start line again
    said = ["Lowering the brightness to 40 percent now."]
    assert said_before("I'm lowering the brightness to 40 percent.", said)
    assert said_before("Lowering the brightness to 40 percent.", said)


async def test_the_hands_say_what_came_of_it_however_it_is_worded(settings, monkeypatch):
    """The hands' closing line is the result. Said after "Setting the brightness to 40
    percent now.", it shares every word that matters with it, and was dropped unspoken."""
    from otto.types import Usage

    from pyxi.companion import brain as brain_module
    from pyxi.companion.operator import Outcome

    class Hands:
        usage = Usage()
        cancelled = False

        def __init__(self, *a, **k) -> None:
            pass

        async def run(self, task, window, come_back):
            return Outcome(True, "The brightness is at 40 percent now. It was at 70.")

    monkeypatch.setattr(brain_module, "Operator", Hands)
    brain, rec = make_brain(settings)

    async def allow(kind, timeout=300, **data):
        return True

    brain.hub.request = allow  # type: ignore[method-assign]
    brain._hands_llm = brain.llm
    brain._said = ["Setting the brightness to 40 percent now."]  # the turn's starting line
    await brain._drive("set the brightness to 40 percent", None)
    await asyncio.sleep(0.05)
    assert [s["text"] for s in rec.of("say")] == ["The brightness is at 40 percent now."]
