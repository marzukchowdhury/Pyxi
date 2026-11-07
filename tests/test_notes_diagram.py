"""Cue cards: the bigger card and its optional inline diagram."""

from pyxi.companion.notes import DIAGRAM_SCHEMA, build_card, build_diagram, card_size
from pyxi.companion.tools import COMPANION_TOOLS
from pyxi.scenes.objects import ObjectScene, Part

ITEMS = [{"text": "Your browser says hello", "say": "x"}, {"text": "The server shows its ID", "say": "y"}]


def test_a_process_card_carries_its_diagram_with_shapes_tied_to_points():
    card = build_card(
        None,
        {
            "title": "How HTTPS starts",
            "items": ITEMS,
            "diagram": {
                "caption": "The handshake",
                "shapes": [
                    {"kind": "rect", "at": [6, 20], "size": [22, 16], "step": 0},
                    {"kind": "text", "at": [9, 30], "text": "You", "step": 0},
                    {"kind": "arrow", "points": [[30, 28], [60, 28]], "flow": True, "step": 1},
                    {"kind": "ellipse", "at": [62, 18], "size": [24, 20], "step": 9},  # past the last point
                ],
            },
        },
    )
    assert card.diagram is not None
    assert card.diagram.caption == "The handshake"
    assert [s.step for s in card.diagram.shapes] == [0, 0, 1, 2]
    assert card.model_dump(mode="json")["diagram"]["shapes"][2]["flow"] is True


def test_broken_or_missing_diagrams_are_left_out_not_fatal():
    assert build_card(None, {"title": "t", "items": ITEMS}).diagram is None
    assert build_diagram({"shapes": "nope"}, 2) is None
    assert build_diagram({"shapes": [{"kind": "line", "points": [[1, 1]], "step": 0}]}, 2) is None
    kept = build_diagram(
        {"shapes": [{"kind": "rect", "step": 1}, {"kind": "rect", "at": [5, 5], "size": [10, 8], "step": 1}]}, 2
    )
    assert kept is not None and len(kept.shapes) == 1


def test_the_card_is_bigger_and_makes_room_for_a_diagram():
    w, h = card_size(["one point", "another point"], "Title")
    w2, h2 = card_size(["one point", "another point"], "Title", diagram=True)
    assert w >= 360  # was 300: about a quarter larger
    assert w2 == w and h2 > h + 150


def test_explain_on_screen_offers_the_diagram():
    tool = next(t for t in COMPANION_TOOLS if t.name == "explain_on_screen")
    assert tool.parameters["properties"]["diagram"] is DIAGRAM_SCHEMA
    shape = DIAGRAM_SCHEMA["properties"]["shapes"]["items"]
    assert "point number" in shape["properties"]["step"]["description"]


def test_lathe_parts_need_a_profile():
    scene = ObjectScene(
        title="Rocket",
        parts=[
            Part(id="body", shape="lathe", axis="y", profile=[[-2, 0.4], [1.5, 0.4], [2.5, 0]]),
            Part(id="bare", shape="lathe"),
        ],
    )
    shapes = {p.id: p.shape for p in scene.parts}
    assert shapes == {"body": "lathe", "bare": "cylinder"}
    assert any("lathe without profile" in n for n in scene.notes)
