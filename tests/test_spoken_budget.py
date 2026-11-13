"""How much Pyxi says in a turn is a limit in code, not a request to the model.

Live, 12 November, after an evening of four, five and nine sentences a turn: "Pyxi, stop, stop,
don't talk. You're talking too much." The lines here are the shapes of that evening's.
"""

from __future__ import annotations

import pytest
from otto.types import ToolCall

from pyxi import web
from pyxi.companion import brain as brain_mod
from pyxi.companion.brain import Brain, asks_long, fit_to_say
from test_answer_card import RESULTS, Scripted, make_brain, say, settle


@pytest.fixture(autouse=True)
def no_wait_for_silence(monkeypatch):
    monkeypatch.setattr(Brain, "QUIET_BEFORE_SPEAKING_S", 0.0)


def fresh() -> dict[str, int]:
    return {"all": 0, "answer": 0}


THOUGHT = "For $30 earbuds I'm looking for a tuned set with real reviews, not just a cheap shell with a big bass boost."
ANSWER = (
    "My pick is the Wren Two at about $25. A test of 46 cheap sets ranks it best under $30. "
    "It's bass-heavy, so the Lark Three is the flatter choice."
)


# ---------------------------------------------------------------- the rule
def test_a_thought_before_a_search_is_on_it_and_a_short_line_is_kept():
    assert fit_to_say(THOUGHT, "lookup", fresh(), False) == ("On it.", 1)
    short = "Checking Canadian prices for the Wren Two."
    assert fit_to_say(short, "lookup", fresh(), False) == (short, 1)
    # Its second sentence, about the thing itself, is not what was asked.
    more = f"{short} The Wren Two is a wired in-ear monitor with a 10mm driver."
    assert fit_to_say(more, "lookup", fresh(), False) == (short, 1)


def test_one_line_while_it_looks_things_up_and_never_a_second():
    assert fit_to_say("Right, those are all over thirty. I'm sorting by lowest price.", "lookup", {"all": 1, "answer": 0}, False) == ("", 0)


def test_an_answer_is_two_sentences_and_its_first_is_always_said():
    said, n = fit_to_say(ANSWER, "answer", {"all": 1, "answer": 0}, False)
    assert n == 2 and said == "My pick is the Wren Two at about $25. A test of 46 cheap sets ranks it best under $30."
    # Even a turn that has used up its sentences says what it found.
    said, n = fit_to_say(ANSWER, "answer", {"all": 3, "answer": 0}, False)
    assert n == 1 and said == "My pick is the Wren Two at about $25."
    # Two long sentences are one.
    long = " ".join(["The first one runs on and on with a great many words in it, far more than a spoken line needs to carry at all."] * 2)
    assert fit_to_say(long, "answer", fresh(), False)[1] == 1


def test_a_job_being_started_gets_its_first_sentence():
    line = "A pivot of total sales by region, coming up. I'll build it in the Data menu."
    assert fit_to_say(line, "action", fresh(), False) == ("A pivot of total sales by region, coming up.", 1)
    # Also when something was said before it: it may be the only word on what was done.
    assert fit_to_say("Your dentist is in at 3 pm. I named it Check-up.", "action", {"all": 1, "answer": 0}, False)[0] == "Your dentist is in at 3 pm."


def test_an_explanation_they_asked_for_may_run_to_five():
    six = "One. Two. Three. Four. Five. Six."
    assert fit_to_say(six, "answer", fresh(), True) == ("One. Two. Three. Four. Five.", 5)
    for words in ("Can you explain this diagram?", "Why is the sky blue?", "Walk me through it.", "Summarise this email."):
        assert asks_long(words), words
    for words in ("Pyxi, how are you?", "Can you open Gmail?", "Find me the best earbuds under $30.", "Can you see?"):
        assert not asks_long(words), words


def test_a_sentence_that_draws_a_mark_always_stays():
    text = "The fan is here. [point:3] The heat goes out this way. It gets loud under load. That's normal."
    said, n = fit_to_say(text, "answer", fresh(), False)
    assert "[point:3]" in said and n == 2 and "That's normal" not in said
    only_marks = "[note:1] First. [note:2] Second. [note:3] Third."
    assert fit_to_say(only_marks, "answer", {"all": 9, "answer": 9}, False) == (only_marks, 0)


# ---------------------------------------------------------------- in a turn
def shown(rec) -> str:
    """Everything said in the turn, as one string (a line goes out a sentence or two at a time)."""
    return " ".join(s["text"] for s in rec.of("say"))


async def test_a_lookup_turn_says_on_it_then_two_sentences(settings, monkeypatch):
    """The evening's worst: a thought, a search, a remark, another search, and three sentences
    at the end. 67 words then; these are what is said now."""

    async def fake_search(query, max_results=6):
        return RESULTS

    monkeypatch.setattr(web, "search_text", fake_search)
    llm = Scripted(
        [say(THOUGHT), ToolCall(id="w", name="web_search", arguments={"query": "best earbuds under $30"})],
        [say("Right, those are all over thirty. I'm sorting by lowest price.", "s2"),
         ToolCall(id="w2", name="web_search", arguments={"query": "earbuds under $30 lowest price"})],
        [say(ANSWER, "s3")],
    )  # fmt: skip
    brain, rec = make_brain(settings, llm=llm)
    await brain.turn("Can you find me the best earbuds? My budget is $30.")
    await settle(brain)
    opener, _, rest = shown(rec).partition(". ")
    assert opener in brain_mod.ACKS, shown(rec)
    assert rest == "My pick is the Wren Two at about $25. A test of 46 cheap sets ranks it best under $30."


async def test_a_plain_answer_is_cut_to_two_sentences_and_a_short_one_is_untouched(settings):
    brain, rec = make_brain(settings, llm=Scripted([say(ANSWER)]))
    await brain.turn("Which earbuds should I get?")
    await settle(brain)
    assert shown(rec) == "My pick is the Wren Two at about $25. A test of 46 cheap sets ranks it best under $30."
    brain, rec = make_brain(settings, llm=Scripted([say("Good, thanks. Your desktop is tidier than mine.")]))
    await brain.turn("Pyxi, how are you?")
    await settle(brain)
    assert shown(rec) == "Good, thanks. Your desktop is tidier than mine."


async def test_an_explanation_keeps_its_length(settings):
    five = "Air goes in at the nose. It is warmed there. The windpipe carries it down. It splits in two. The sacs pass oxygen to the blood."
    brain, rec = make_brain(settings, llm=Scripted([say(five + " And that's breathing.")]))
    await brain.turn("Can you explain how breathing works?")
    await settle(brain)
    assert shown(rec) == five
