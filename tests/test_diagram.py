"""Typed diagrams: Pyxi names a type and gives the content; the board lays it out."""

from __future__ import annotations

import asyncio

import pytest

from pyxi.companion.prompts import SYSTEM
from pyxi.companion.tools import COMPANION_TOOLS
from pyxi.scenes import diagram as dg
from pyxi.scenes.diagram import DiagramError, parse_diagram
from test_brain_and_server import make_brain
from test_connectors_and_docs import Script

STEPS = [
    {"narration": "It starts when someone asks for their money back."},
    {"narration": "The shop checks how long ago they bought it."},
    {"narration": "A manager signs it off."},
    {"narration": "Then the money goes back to the card."},
]
REFUND = {
    "type": "flowchart",
    "title": "How a refund gets approved",
    "items": [
        {"id": "a", "label": "Refund asked", "step": 0},
        {"id": "b", "label": "Within 30 days?", "step": 1},
        {"id": "c", "label": "Declined", "step": 1},
        {"id": "d", "label": "Manager signs", "step": 2},
        {"id": "e", "label": "Money sent", "step": 3, "focus": True},
    ],
    "links": [
        {"from": "a", "to": "b"},
        {"from": "b", "to": "c", "label": "no"},
        {"from": "b", "to": "d", "label": "yes"},
        {"from": "d", "to": "e"},
    ],
    "steps": STEPS,
}


def items(n: int, **extra) -> list[dict]:
    return [{"id": f"n{i}", "label": f"Item {i}", "step": i, **extra} for i in range(n)]


def problems(args: dict) -> str:
    with pytest.raises(DiagramError) as caught:
        parse_diagram({"title": "A title", "steps": STEPS, **args})
    return " | ".join(caught.value.problems)


def test_the_tool_asks_for_content_and_never_for_positions():
    spec = next(t for t in COMPANION_TOOLS if t.name == "draw_diagram")
    props = spec.parameters["properties"]
    assert props["type"]["enum"] == list(dg.TYPES)
    assert set(spec.parameters["required"]) == {"type", "title", "items", "steps"}
    item = props["items"]["items"]["properties"]
    assert {"id", "label", "step", "focus"} <= set(item)
    # Nothing in it places a shape: no points, no sizes, no colours.
    assert not {"points", "at", "size", "color", "shapes"} & (set(props) | set(item))
    # The freehand tool is still there, and each names the other for what it is not for.
    sketch = next(t for t in COMPANION_TOOLS if t.name == "sketch_diagram")
    assert "draw_diagram" in sketch.description and "sketch_diagram" in spec.description
    assert "draw_diagram" in SYSTEM and "sketch_diagram" in SYSTEM


def test_a_good_diagram_goes_to_the_board_as_content_with_no_shapes():
    d = parse_diagram(REFUND)
    board = d.as_board()
    assert board["shapes"] == [] and board["id"].startswith("sketch_")
    assert [s["narration"] for s in board["steps"]] == [s["narration"] for s in STEPS]
    content = board["diagram"]
    assert content["type"] == "flowchart"
    assert [i["label"] for i in content["items"]] == [i["label"] for i in REFUND["items"]]
    assert content["links"][1] == {"from": "b", "to": "c", "label": "no"}
    assert [i["focus"] for i in content["items"]] == [False, False, False, False, True]


def test_small_slips_are_repaired_not_refused():
    raw = {
        "type": "Process",  # what a model may call steps
        "title": "  Getting   a passport ",
        "items": [
            {"label": "Fill in the form.", "step": 0, "focus": True},
            {"label": "Photo", "step": 1, "focus": True},
            "junk",
            {"label": "", "step": 1},
            {"label": "Pay the fee", "step": 9, "focus": True},
        ],
        "links": [{"from": "nobody", "to": "nowhere"}],  # steps are joined by their order
        "steps": STEPS[:2],
    }
    d = parse_diagram(raw)
    assert d.type == "steps" and d.title == "Getting a passport"
    assert [i.label for i in d.items] == ["Fill in the form", "Photo", "Pay the fee"]
    assert [i.id for i in d.items] == ["1", "2", "5"]  # a missing id is its place in the list
    assert [i.step for i in d.items] == [0, 1, 1]  # never past the last spoken step
    assert [i.focus for i in d.items] == [True, True, False]  # one accent, on two at most
    assert d.links == [] and "accent" in d.notes[0]


def test_too_much_is_turned_away_with_what_to_change():
    assert "11 items: the limit is 9" in problems({"type": "steps", "items": items(11)})
    assert "at least 2 items" in problems({"type": "steps", "items": items(1)})
    assert "limit is 8" in problems({"type": "loop", "items": items(9)})
    long = items(3)
    long[1]["label"] = "Wait for the regional office to answer"
    long[2]["label"] = "Antidisestablishmentarianism"
    said = problems({"type": "timeline", "items": long})
    assert "2 labels too long" in said and "'Wait for the regional office to answer'" in said
    assert "1 to 4 words" in said
    assert "`when` too long" in problems(
        {"type": "timeline", "items": items(2, when="the twenty-first century")}
    )
    assert "type must be one of" in problems({"type": "pie chart", "items": items(3)})
    with pytest.raises(DiagramError, match="title"):
        parse_diagram({"type": "steps", "items": items(3), "steps": STEPS})


def test_a_flowchart_runs_one_way_and_stays_small():
    def flow(n: int, *links: str) -> dict:
        pairs = [x.split(">") for x in links]
        return {
            "type": "flowchart",
            "items": items(n),
            "links": [{"from": f"n{a}", "to": f"n{b}"} for a, b in pairs],
        }

    assert "needs `links`" in problems(flow(3))
    assert "no item has the id 'n7'" in problems(flow(3, "0>1", "1>7"))
    assert "loop back" in problems(flow(3, "0>1", "1>2", "2>0"))
    assert "6 levels deep: the limit is 5" in problems(flow(6, "0>1", "1>2", "2>3", "3>4", "4>5"))
    wide = problems(flow(6, "0>1", "0>2", "0>3", "1>4", "2>4", "3>4", "0>5", "5>4"))
    assert "4 ways out of 'Item 0'" in wide and "4 items side by side" in wide
    label = {**flow(2, "0>1")}
    label["links"][0]["label"] = "only if the manager agrees"
    assert "link label too long" in problems(label)
    parse_diagram({"title": "T", "steps": STEPS, **flow(5, "0>1", "1>2", "1>3", "2>4", "3>4")})


def test_a_tree_has_one_top_and_one_parent_each():
    def tree(n: int, *links: str) -> dict:
        pairs = [x.split(">") for x in links]
        return {
            "type": "tree",
            "items": items(n),
            "links": [{"from": f"n{a}", "to": f"n{b}"} for a, b in pairs],
        }

    assert "needs `links`" in problems(tree(3))
    assert "2 have no parent" in problems(tree(4, "0>1", "2>3"))
    assert "two parents" in problems(tree(3, "0>2", "1>2"))
    assert "circle" in problems(tree(3, "0>1", "1>2", "2>0"))
    assert "5 levels deep: the limit is 4" in problems(tree(5, "0>1", "1>2", "2>3", "3>4"))
    d = parse_diagram({"title": "T", "steps": STEPS, **tree(4, "0>1", "0>2", "2>3")})
    assert [(link.source, link.to) for link in d.links] == [
        ("n0", "n1"),
        ("n0", "n2"),
        ("n2", "n3"),
    ]


def test_a_comparison_is_two_columns_or_two_axes():
    assert "needs `columns`" in problems({"type": "comparison", "items": items(4)})
    cols = {"type": "comparison", "columns": ["Renting", "Buying"]}
    assert "needs side" in problems({**cols, "items": items(4)})
    six = items(6, side="left") + items(1, side="right")
    six[-1]["id"] = "r"
    assert "6 items on the left: the limit is 5" in problems({**cols, "items": six})
    assert "column name too long" in problems(
        {
            **cols,
            "columns": ["Renting", "Buying a home of your own"],
            "items": items(2, side="left"),
        }
    )
    sides = [{**it, "side": "left" if i % 2 else "right"} for i, it in enumerate(items(4))]
    d = parse_diagram({"title": "Rent or buy", "steps": STEPS, **cols, "items": sides})
    assert d.columns == ("Renting", "Buying") and d.axes is None
    # A column is nearly half the board wide: a line in it may be a short phrase.
    phrase = "Selling costs eat the gains if you leave"
    wide = [{**it, "label": phrase} for it in sides]
    assert (
        parse_diagram({"title": "T", "steps": STEPS, **cols, "items": wide}).items[0].label
        == phrase
    )
    longer = [{**it, "label": phrase + " too early"} for it in sides]
    assert "limit 44" in problems({**cols, "items": longer})
    assert "limit 28" in problems({"type": "steps", "items": wide})

    axes = {"type": "comparison", "axes": {"x": ["Cheap", "Costly"], "y": ["Slow", "Fast"]}}
    assert "needs x and y" in problems({**axes, "items": items(3)})
    assert "axes needs x" in problems(
        {"type": "comparison", "axes": {"x": ["Cost"]}, "items": items(3)}
    )
    crowded = items(5, x=80, y=90)
    assert "more than 4 items in one quarter" in problems({**axes, "items": crowded})
    placed = [{**it, "x": 20 + 20 * i, "y": 500} for i, it in enumerate(items(4))]
    d = parse_diagram({"title": "Which to do first", "steps": STEPS, **axes, "items": placed})
    assert d.axes is not None and d.columns is None
    assert [it.y for it in d.items] == [100.0] * 4  # kept on the chart


async def test_a_typed_diagram_draws_first_and_is_narrated_step_by_step(settings):
    llm = Script(
        [
            ("say", {"text": "It turns on one question. Watch where the money goes. And more."}),
            ("draw_diagram", REFUND),
        ]
    )
    brain, rec = make_brain(settings, llm=llm)
    await brain.turn("how does a refund get approved?")
    for _ in range(100):
        if len(rec.of("scene_audio")) >= 4:
            break
        await asyncio.sleep(0.02)
    [ev] = rec.of("sketch")
    names = [e["type"] for e in rec.events if e["type"] in ("sketch", "say")]
    assert names[0] == "sketch"  # the diagram goes up before Pyxi starts talking
    assert ev["board"]["shapes"] == [] and ev["board"]["diagram"]["type"] == "flowchart"
    assert len(ev["board"]["diagram"]["items"]) == 5
    assert [a["step"] for a in rec.of("scene_audio")] == [0, 1, 2, 3]
    assert all(a["scene_id"] == ev["audio_key"] for a in rec.of("scene_audio"))
    # The verdict and the hook are said; the rest is left to the diagram.
    assert " ".join(s["text"] for s in rec.of("say")) == (
        "It turns on one question. Watch where the money goes."
    )


async def test_a_diagram_that_does_not_fit_is_sent_back_once_with_the_reasons(settings):
    too_many = {**REFUND, "type": "steps", "items": items(12), "links": []}
    llm = Script(
        [("say", {"text": "Here is how it goes."}), ("draw_diagram", too_many)],
        [("say", {"text": "Here is how it goes."}), ("draw_diagram", REFUND)],
    )
    brain, rec = make_brain(settings, llm=llm)
    await brain.turn("how does a refund get approved?")
    await asyncio.sleep(0.1)
    [ev] = rec.of("sketch")  # the second try drew it
    assert ev["board"]["diagram"]["type"] == "flowchart"
    assert " ".join(s["text"] for s in rec.of("say")) == "Here is how it goes."  # said once
    # The model was told what to change, in words it can act on.
    assert "DIAGRAM_INVALID" in llm.prompts[1] and "12 items: the limit is 9" in llm.prompts[1]


async def test_a_diagram_without_steps_is_told_by_the_reply(settings):
    board = {k: v for k, v in REFUND.items() if k != "steps"}
    reply = "A refund turns on one check. Was it within thirty days? If so a manager signs. Then it is paid."
    brain, rec = make_brain(
        settings, llm=Script([("say", {"text": reply}), ("draw_diagram", board)])
    )
    await brain.turn("how does a refund get approved?")
    for _ in range(100):
        if len(rec.of("scene_audio")) >= 4:
            break
        await asyncio.sleep(0.02)
    [ev] = rec.of("sketch")
    assert [s["narration"] for s in ev["board"]["steps"]] == [
        "A refund turns on one check.",
        "Was it within thirty days?",
        "If so a manager signs.",
        "Then it is paid.",
    ]
    assert not rec.of("say")  # said once, by the diagram
