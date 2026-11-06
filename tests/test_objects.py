"""Object scenes: repairs, follow-up edits, the gallery, and the brain's 3D model tools."""

from __future__ import annotations

from otto.types import LLMResponse, ToolCall, Usage

from blip.scenes.object_gallery import OBJECT_GALLERY
from blip.scenes.objects import ObjectEdit, ObjectScene, apply_edit, build_tool_schema
from test_brain_and_server import make_brain


def _model(**extra) -> dict:
    return {
        "title": "Desk fan",
        "parts": [
            {"id": "base", "shape": "cylinder", "size": [2, 0.3, 2]},
            {
                "id": "pole",
                "shape": "cylinder",
                "size": [0.2, 2, 0.2],
                "position": [0, 1.2, 0],
                "parent": "base",
            },
            {
                "id": "blades",
                "shape": "gear",
                "teeth": 3,
                "axis": "z",
                "size": [2, 2, 0.1],
                "parent": "pole",
                "label": "Blades",
            },
            {
                "id": "cage",
                "shape": "sphere",
                "size": [2.4, 2.4, 0.8],
                "parent": "pole",
                "casing": True,
            },
        ],
        "motions": [{"part": "blades", "kind": "spin", "axis": "z", "speed": 2}],
        "beats": [{"title": "Spin", "narration": "The blades push air.", "at": 0}],
        **extra,
    }


def test_slightly_wrong_models_are_repaired_not_rejected():
    m = ObjectScene.model_validate(
        _model(
            parts=[
                {"id": "a b", "shape": "tube"},  # no path: becomes a cylinder
                {"id": "a b", "shape": "extrude"},  # duplicate id, no profile: becomes a box
                {"id": "c", "parent": "nope", "size": 2, "color": "red"},
                {"id": "d", "parent": "e"},
                {"id": "e", "parent": "d"},  # a parent loop
            ],
            motions=[{"part": "ghost", "kind": "spin"}, {"part": "c", "keys": []}],
        )
    )
    ids = [p.id for p in m.parts]
    assert ids[:2] == ["a_b", "a_b_1"]
    assert [p.shape for p in m.parts[:2]] == ["cylinder", "box"]
    c = m.parts[2]
    assert c.parent is None and c.size == [2.0, 2.0, 2.0] and c.color is None
    assert sum(p.parent is not None for p in m.parts[3:]) == 1  # the loop is broken
    assert m.motions == [] and m.beats  # unknown/empty motions dropped, a default beat added
    assert m.notes


def test_edits_change_the_same_model():
    m = ObjectScene.model_validate(_model())
    e = apply_edit(
        m,
        ObjectEdit(
            cutaway=True,
            hide=["cage"],
            update=[{"id": "blades", "color": "#ff0000"}],
            add_motions=[{"part": "pole", "kind": "oscillate", "prop": "rotation", "amount": 30}],
        ),
    )
    assert e.id == m.id and e.cutaway
    parts = {p.id: p for p in e.parts}
    assert parts["cage"].hidden and parts["blades"].color == "#ff0000"
    assert len(e.motions) == 2
    gone = apply_edit(e, ObjectEdit(remove=["blades"]))
    assert "blades" not in {p.id for p in gone.parts} and all(
        mo.part != "blades" for mo in gone.motions
    )


def test_gallery_models_are_clean():
    for name, build in OBJECT_GALLERY.items():
        m = build()
        assert not m.notes, (name, m.notes)
        assert len(m.beats) >= 4 and m.duration > 0
        assert sum(1 for p in m.parts if p.label) >= 4


def test_tool_schema_is_flat():
    schema = build_tool_schema()
    assert "$defs" not in str(schema) and "id" not in schema["properties"]
    assert schema["properties"]["parts"]["items"]["properties"]["axis"]["enum"] == ["x", "y", "z"]


class OneModel:
    """Builds a model on the first turn and edits it on the second."""

    def __init__(self) -> None:
        self.turn = 0
        self.prompts: list[str] = []

    async def complete(self, *, system, messages, tools=None, **_):
        self.prompts.append(messages[-1].parts[0].text)
        self.turn += 1
        calls = (
            [
                ToolCall(id="1", name="build_3d_model", arguments=_model()),
                ToolCall(id="2", name="say", arguments={"text": "Here's a fan."}),
            ]
            if self.turn == 1
            else [
                ToolCall(id="3", name="edit_3d_model", arguments={"cutaway": True}),
                ToolCall(id="4", name="say", arguments={"text": "Inside!"}),
            ]
        )
        return LLMResponse(text="", tool_calls=calls, usage=Usage(), stop_reason="tool_use")


async def test_brain_builds_then_edits_a_model(settings):
    llm = OneModel()
    brain, rec = make_brain(settings, llm=llm)
    await brain.turn("how does a fan work?")
    [first] = rec.of("object")
    assert first["restart"] and first["scene"]["title"] == "Desk fan"
    await brain.turn("show me the inside")
    second = rec.of("object")[1]
    assert second["scene"]["id"] == first["scene"]["id"] and second["scene"]["cutaway"]
    assert not second["restart"] and second["audio_key"] == first["audio_key"]
    assert "3D model on screen: 'Desk fan'" in llm.prompts[1]


async def test_demo_plays_the_ejection_seat(settings):
    brain, rec = make_brain(settings)
    await brain.turn("how does a pilot eject?")
    [obj] = rec.of("object")
    assert obj["scene"]["title"] == "How a pilot ejects"
