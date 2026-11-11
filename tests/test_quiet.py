"""When they say "don't talk", Pyxi doesn't; thanks get one word; nothing is said for the sake of it.

The founder's trial of 10 November, 11 PM, in his words: "Stop, stop. Don't talk. You're still
talking too much." "It's done. Just don't talk." "Just stop. Why are you talking? I don't want
you to talk." The last was answered "Hmm, I'm not sure what to say to that."
"""

from __future__ import annotations

import pytest
from otto.types import ToolCall

from pyxi.companion import brain as brain_mod
from pyxi.companion.brain import Brain, only_polite
from test_answer_card import Scripted, make_brain, settle


@pytest.fixture(autouse=True)
def no_wait_for_silence(monkeypatch):
    monkeypatch.setattr(Brain, "QUIET_BEFORE_SPEAKING_S", 0.0)


def say(text: str) -> list[ToolCall]:
    return [ToolCall(id="s", name="say", arguments={"text": text})]


def spoken(rec) -> list[str]:
    """What was said aloud (a line with sound), as opposed to shown."""
    return [s["text"] for s in rec.of("say") if s.get("audio")]


def shown(rec) -> list[str]:
    return [s["text"] for s in rec.of("say")]


@pytest.mark.parametrize(
    "words",
    [
        "Stop, stop. Don't talk. You're still talking too much.",
        "It's done. Just don't talk.",
        "Just stop. Why are you talking? I don't want you to talk.",
        "Be quiet please.",
    ],
)
async def test_dont_talk_is_answered_with_silence_and_no_model_call(settings, words):
    llm = Scripted(say("Hmm, I'm not sure what to say to that."))
    brain, rec = make_brain(settings, llm=llm)
    await brain.turn(words)
    await settle(brain)
    assert llm.prompts == []  # nothing is asked of the model
    assert shown(rec) == []  # and not a word comes back, spoken or shown
    assert brain._quiet()
    assert [r["title"] for r in rec.of("job_ready")] == ["Okay, not a word"]  # a notice, to be seen


async def test_while_quiet_her_words_are_shown_not_spoken_and_the_model_is_told(settings):
    llm = Scripted(say("Four."))
    brain, rec = make_brain(settings, llm=llm)
    await brain.turn("Don't talk.")
    await brain.turn("What's two plus two?")
    await settle(brain)
    assert shown(rec) == ["Four."] and spoken(rec) == []
    assert "They told you not to talk" in llm.prompts[-1]
    # A background line (a teammate finishing) is not spoken either.
    await brain._say_when_free("Ledger's done.")
    assert shown(rec) == ["Four."]


async def test_you_can_talk_again_gives_her_voice_back(settings):
    llm = Scripted(say("Four."))
    brain, rec = make_brain(settings, llm=llm)
    await brain.turn("Stop talking.")
    await brain.turn("Okay, you can talk again.")
    assert not brain._quiet() and shown(rec) == ["Okay."]
    await brain.turn("What's two plus two?")
    assert "They told you not to talk" not in llm.prompts[-1]


async def test_not_every_talk_is_a_request_for_quiet(settings):
    for words in ("Talk me through it.", "Stop the music.", "Can you talk to Sam about it?"):
        assert not brain_mod._ASKS_QUIET.search(words), words


async def test_thanks_get_one_word_and_no_model_call(settings):
    """Live: "Thanks." got "Anytime. The earbud cutaway is still up if you want to poke around
    it. Good luck with the hunt." """
    llm = Scripted(say("Anytime. The earbud cutaway is still up. Good luck with the hunt."))
    brain, rec = make_brain(settings, llm=llm)
    await brain.turn("Okay. Thank you. Thank you for your help.")
    assert llm.prompts == []
    [line] = shown(rec)
    assert line in ("Anytime.", "You're welcome.", "Any time.")


async def test_okay_got_it_is_met_with_nothing_unless_it_answers_her_question(settings):
    llm = Scripted(say("Glad it works. If the shade is off, tell me and I'll adjust it."))
    brain, rec = make_brain(settings, llm=llm)
    await brain.turn("Yeah, perfect.")
    assert llm.prompts == [] and shown(rec) == []
    # After a question of hers, "okay" is a yes: the model takes it.
    brain.history = [("draft a reply", "Want me to send it now?")]
    await brain.turn("Okay.")
    assert len(llm.prompts) == 1


def test_what_is_only_polite():
    assert only_polite("Thanks.") == "thanks"
    assert only_polite("Okay. Thank you. Thank you for your help.") == "thanks"
    assert only_polite("Okay, got it.") == "heard" and only_polite("Yeah, perfect.") == "heard"
    for asks in (
        "Beautiful. Good job. Can you insert a chart?",
        "Okay, create a pivot table now",
        "Thanks, open YouTube",
        "Is it done?",
    ):
        assert only_polite(asks) is None, asks


async def test_nothing_to_say_is_silence_not_a_filler_line(settings):
    """A reply with no words used to be filled with "Hmm, I'm not sure what to say to that." A
    statement gets silence; a question gets one plain line."""
    llm = Scripted([])  # the model says and does nothing
    brain, rec = make_brain(settings, llm=llm)
    await brain.turn("That one looks nicer than the other.")
    assert shown(rec) == []
    await brain.turn("What do you think of this one?")
    assert shown(rec) == ["Sorry, I didn't get that.", "Say it another way?"] or shown(rec) == [
        "Sorry, I didn't get that. Say it another way?"
    ]
