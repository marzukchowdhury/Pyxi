"""Field notes (cards that frame what they explain) and click-by-click guides."""

from __future__ import annotations

import asyncio

from otto.types import LLMResponse, ToolCall, Usage

from conftest import fake_context
from test_brain_and_server import make_brain
from wisp.companion.notes import build_card


def test_card_points_frame_the_exact_elements():
    ctx = fake_context()
    card = build_card(
        ctx,
        {
            "title": "What this page does",
            "items": [
                {"text": "Dark mode flips it all", "element_id": 3},
                {"text": "Export saves a file", "element_id": 4, "mark": "circle"},
                {"text": "No target, just a joke", "kind": "aside"},
                {"text": "  "},  # empty points are dropped
            ],
            "sketches": [{"kind": "tick", "element_id": 3}, {"kind": "bogus", "points": [[1, 2]]}],
        },
    )
    assert [i.n for i in card.items] == [1, 2, 3]
    assert card.items[0].rect == (140, 170, 200, 30) and card.items[1].mark == "circle"
    assert card.items[2].mark == "none" and card.items[2].kind == "aside"
    assert len(card.sketches) == 1 and card.sketches[0].points == [(367, 185)]  # just after it
    assert card.region == (140, 170, 680, 520)  # union of what it explains, to sit beside it


class Scripted:
    """Replies with whatever tool calls the test queues up for each turn."""

    def __init__(self, *turns):
        self.turns = list(turns)
        self.prompts: list[str] = []

    async def complete(self, *, system, messages, tools=None, **_):
        self.prompts.append(messages[-1].parts[0].text)
        calls = self.turns.pop(0) if self.turns else [("say", {"text": "Okay."})]
        return LLMResponse(
            tool_calls=[ToolCall(id=str(i), name=n, arguments=a) for i, (n, a) in enumerate(calls)],
            usage=Usage(),
            stop_reason="tool_use",
        )


async def test_note_markers_reveal_points_with_the_sentence(settings):
    llm = Scripted(
        [
            (
                "explain_on_screen",
                {
                    "title": "Settings",
                    "items": [
                        {"text": "Dark mode", "element_id": 3},
                        {"text": "Export", "element_id": 4},
                    ],
                },
            ),
            ("say", {"text": "This one [note:1] flips the theme. And this [note:2] saves it."}),
        ]
    )
    brain, rec = make_brain(settings, llm=llm)
    await brain.turn("explain this page")
    [notes] = rec.of("notes")
    assert notes["card"]["items"][1]["rect"] == [700, 650, 120, 40]
    says = rec.of("say")
    assert [s["notes"] for s in says] == [[1], [2]]
    assert "[note" not in " ".join(s["text"] for s in says)


async def test_guides_wait_for_the_real_click_then_continue(settings):
    llm = Scripted(
        [
            ("guide", {"element_id": 4, "label": "Export", "goal": "export my settings"}),
            ("say", {"text": "Click Export."}),
        ],
        [("guide", {"goal": "export my settings", "done": True}), ("say", {"text": "Done!"})],
    )
    brain, rec = make_brain(settings, llm=llm)
    await brain.turn("how do I export my settings?")
    target = rec.of("annotate")[-1]["annotations"][0]
    assert target["kind"] == "click" and target["guide"] and target["rect"] == [700, 650, 120, 40]
    g = rec.of("guide")[-1]
    assert (g["active"], g["step"], g["goal"], g["label"]) == (
        True,
        1,
        "export my settings",
        "Export",
    )

    await brain.guide_clicked()
    assert rec.of("user")[-1]["text"] == "Clicked Export"  # what the chat shows
    assert "I clicked 'Export'" in llm.prompts[-1]  # what the model is told
    assert rec.of("guide")[-1]["active"] is False and brain._guide is None


async def test_a_card_without_speech_narrates_itself(settings):
    llm = Scripted(
        [
            (
                "explain_on_screen",
                {
                    "title": "Gist",
                    "items": [
                        {"text": "Dark mode flips it", "element_id": 3},
                        {"text": "Export saves", "element_id": 4},
                    ],
                },
            )
        ]
    )
    brain, rec = make_brain(settings, llm=llm)
    await brain.turn("explain this")
    says = rec.of("say")
    assert says[0]["text"] == "Dark mode flips it."  # straight into the points, no filler
    assert [n for s in says for n in s["notes"]] == [1, 2]


async def test_drawings_clear_when_the_user_switches_window(settings):
    import asyncio

    llm = Scripted(
        [
            (
                "explain_on_screen",
                {"title": "Gist", "items": [{"text": "Dark mode", "element_id": 3}]},
            ),
            ("say", {"text": "This one [note:1] flips the theme."}),
        ]
    )
    brain, rec = make_brain(settings, llm=llm)
    titles = ["Settings - App"]
    brain.screen.foreground_title = lambda: titles[-1]  # type: ignore[attr-defined]
    await brain.turn("explain this")
    await asyncio.sleep(1.4)
    assert not rec.of("clear")  # still on the same window: the card stays
    titles.append("Facebook - Microsoft Edge")
    await asyncio.sleep(1.4)
    assert len(rec.of("clear")) == 1  # switched away: everything that points at pixels goes


async def test_the_card_talks_through_its_points_after_the_opener(settings):
    from test_connectors_and_docs import Script

    card = {
        "title": "Is this loss real?",
        "closing": "Amazon lost money for years too. Want me to sketch where it all goes?",
        "items": [
            {
                "text": "Headline says 'reportedly'",
                "say": "Notice the word reportedly.",
                "element_id": 3,
            },
            {
                "text": "Labelled LEAKED",
                "say": "And LEAKED is there to make you click.",
                "element_id": 4,
            },
        ],
    }
    llm = Script(
        [("say", {"text": "Big if true, but read it twice. [note:1]"}), ("explain_on_screen", card)]
    )
    brain, rec = make_brain(settings, llm=llm)
    await brain.turn("explain what's on my screen")
    for _ in range(100):
        if any("LEAKED" in s["text"] for s in rec.of("say")):
            break
        await asyncio.sleep(0.02)
    spoken = [(s["text"], s["notes"]) for s in rec.of("say")]
    assert spoken[0][0].startswith("Big if true")
    # Point 1 was named in the opener; point 2 gets its own sentence, revealed as it's said.
    assert ("And LEAKED is there to make you click.", [2]) in spoken
    assert not any("reportedly" in t for t, _ in spoken)  # no point is said twice
    # It lands with a related fact and an offer instead of just stopping.
    assert spoken[-1][0].endswith("Want me to sketch where it all goes?")
