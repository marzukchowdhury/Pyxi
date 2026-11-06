"""Acting on a reply while it streams: the first sentence, and the cue card point by point."""

from __future__ import annotations

import asyncio
import json

from otto.types import LLMResponse, ToolCall, Usage

from pyxi.partial_json import complete_items, finished_sentences, partial_string
from pyxi.stream_llm import StreamingChat
from test_brain_and_server import make_brain


def test_reading_json_that_is_still_arriving():
    assert partial_string('{"text": "Real. The B ver', "text") == ("Real. The B ver", False)
    assert partial_string('{"text": "a \\"b\\" c"}', "text") == ('a "b" c', True)
    assert complete_items('{"items": [{"t": "x {y}"}, {"t": "z"') == [{"t": "x {y}"}]
    assert (
        finished_sentences("Real. The B version has a fan. And") == "Real. The B version has a fan."
    )
    assert finished_sentences("Yes, e.g. this") == ""


class Streamed(StreamingChat):
    """Plays a reply back in small pieces, the way OpenRouter streams it."""

    def __init__(self, calls: list[tuple[str, dict]]) -> None:
        self.calls = calls
        self.events: list[str] = []  # what happened, in order

    async def complete(
        self, *, system, messages, tools=None, max_tokens=None, on_call=None, on_partial=None
    ):
        done = []
        for i, (name, args) in enumerate(self.calls):
            raw = json.dumps(args)
            for cut in range(20, len(raw), 20):
                if on_partial:
                    await on_partial(i, name, raw[:cut])
                await asyncio.sleep(0)
            call = ToolCall(id=f"c{i}", name=name, arguments=args)
            done.append(call)
            if on_call:
                await on_call(call)
        self.calls = [("say", {"text": "Okay."})]
        return LLMResponse(tool_calls=done, usage=Usage(), stop_reason="tool_use")


CARD = {
    "title": "What this post claims",
    "items": [
        {
            "text": "It's a repost",
            "say": "First, this is a repost of a screenshot.",
            "element_id": 3,
        },
        {"text": "No source", "say": "And nobody links the original.", "element_id": 4},
        {"text": "Plausible topic", "say": "The topic itself is real, though.", "element_id": 3},
    ],
    "closing": "Want me to look for the original?",
}


async def test_first_sentence_and_points_are_said_as_they_arrive(settings):
    llm = Streamed(
        [
            (
                "say",
                {"text": "Short version: it's a repost. Here's what to look at, point by point."},
            ),
            ("explain_on_screen", CARD),
        ]
    )
    brain, rec = make_brain(settings, llm=llm)
    await brain.turn("what is this post about?")
    for _ in range(100):
        if any("original?" in s["text"] for s in rec.of("say")):
            break
        await asyncio.sleep(0.02)
    said = [s["text"] for s in rec.of("say")]
    # The first sentence went out on its own, before the rest of the line was even finished.
    assert said[0] == "Short version: it's a repost."
    joined = " ".join(said)
    for line in (
        "Here's what to look at",
        "repost of a screenshot",
        "nobody links",
        "topic itself",
        "look for the original",
    ):
        assert joined.count(line) == 1, line  # each said exactly once
    assert (
        joined.index("repost of a screenshot")
        < joined.index("nobody links")
        < joined.index("look for the original")
    )
    cards = rec.of("notes")
    assert len({c["card"]["id"] for c in cards}) == 1  # one card, filling in
    assert len(cards[0]["card"]["items"]) < 3 and len(cards[-1]["card"]["items"]) == 3
    assert len({tuple(c["card"]["place"] or ()) for c in cards}) == 1  # it never jumps around


def test_a_long_first_sentence_starts_at_its_first_pause():
    from pyxi.partial_json import first_chunk

    long = "This is a Yahoo Finance post claiming a leaked filing shows, honestly, a huge loss"
    assert first_chunk(long) == ""  # a normal sentence is said whole, so wait for its end
    huge = long + " that nobody saw coming because the numbers were buried, deep in the notes"
    assert first_chunk(huge) == "This is a Yahoo Finance post claiming a leaked filing shows,"
    # The bug: the tail of a sentence said on its own sounded like an order to Scout.
    coffee = "On it. I'll have Scout check Google Maps for coffee spots near you, filtered to open now, ranked by what the reviews say."
    assert first_chunk(coffee) == coffee
    assert (
        first_chunk("Real. The B version has a giant fan. More")
        == "Real. The B version has a giant fan."
    )
    assert first_chunk("Short, then") == ""  # too short to start on
