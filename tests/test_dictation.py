"""Dictation: hold Ctrl + Windows, talk, let go: the words are typed where the cursor is."""

from __future__ import annotations

import json

from conftest import FakeScreen
from pyxi.companion.brain import Brain
from pyxi.demo import DemoLLM
from pyxi.dictation import clean
from pyxi.hub import Hub


def test_hesitations_go_and_commas_stay_tidy() -> None:
    assert clean("um so I think we should, uh, ship it on Friday.") == "So I think we should ship it on Friday. "
    assert clean("I mean the summary, um.") == "I mean the summary. "
    assert clean("So, um I think yes.") == "So, I think yes. "
    # real words that start like fillers stay
    assert clean("Remind me about the umbrella and the error.") == "Remind me about the umbrella and the error. "


def test_spoken_line_breaks() -> None:
    assert clean("Dear Sam, new paragraph thanks for the notes. New line Best, Marzuk") == (
        "Dear Sam,\n\nThanks for the notes.\nBest, Marzuk "
    )
    assert clean("new line") == "\n"
    assert clean("New paragraph.") == "\n\n"


def test_silence_types_nothing_but_thank_you_is_real() -> None:
    for heard in ("", "   ", "um", "Thanks for watching!", "[BLANK_AUDIO]", "..."):
        assert clean(heard) == "", heard
    assert clean("Thank you.") == "Thank you. "


async def test_dictating_types_into_the_app_and_never_asks_the_model(settings) -> None:
    hub = Hub()
    events: list[dict] = []

    async def record(text: str) -> None:
        events.append(json.loads(text))

    hub.connect(record)

    class Ears:
        async def transcribe(self, audio, language="en"):
            return "um the meeting moved to Thursday, new line bring the slides", 0.4, "en"

    class NoModel(DemoLLM):
        async def complete(self, *a, **k):  # pragma: no cover - must never run
            raise AssertionError("dictation called the model")

    brain = Brain(settings, hub, llm=NoModel(), screen=FakeScreen())
    brain.stt = Ears()  # type: ignore[assignment]
    typed = await brain.dictate(b"fake audio")
    assert typed == "The meeting moved to Thursday,\nBring the slides "
    inserts = [{k: v for k, v in e.items() if k != "ts"} for e in events if e["type"] == "action"]
    assert inserts == [{"type": "action", "kind": "insert", "value": typed}]
    assert not [e for e in events if e["type"] == "user"]  # not a question for Pyxi
