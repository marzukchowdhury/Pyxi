"""3D on screen sooner: the builder streams parts in as they arrive, and a tool call that comes
back glued to another one (or with junk after it) is kept instead of costing a whole round."""

from __future__ import annotations

import json
from types import SimpleNamespace as NS

from otto.types import LLMResponse, ToolCall, Usage

from pyxi.scenes.builder import PartialModel, build_model
from pyxi.scenes.object_gallery import OBJECT_GALLERY
from pyxi.stream_llm import StreamingChat

MODEL = OBJECT_GALLERY["engine"]().model_dump(mode="json", exclude={"id", "notes"})


def _delta(index, id=None, name=None, args=""):
    fn = NS(name=name, arguments=args)
    return NS(usage=None, choices=[NS(finish_reason=None, delta=NS(content=None, tool_calls=[NS(index=index, id=id, function=fn)]))])


class FakeClient:
    def __init__(self, chunks):
        self.chunks = chunks
        self.chat = NS(completions=NS(create=self.create))

    async def create(self, **_):
        async def gen():
            for c in self.chunks:
                yield c

        return gen()


def _chat(chunks) -> StreamingChat:
    chat = StreamingChat.__new__(StreamingChat)
    chat.model = "test"
    chat.client = FakeClient(chunks)
    return chat


async def test_two_calls_under_one_index_stay_two_calls():
    a, b = json.dumps({"cutaway": True}), json.dumps({"recolor": {"fan": "#d8ae55"}})
    chunks = [
        _delta(0, "c1", "edit_3d_model", a[:10]),
        _delta(0, None, None, a[10:]),
        _delta(0, "c2", "edit_3d_model", b[:7]),  # same index, new id: a new call
        _delta(0, None, None, b[7:]),
    ]
    r = await _chat(chunks).complete(system="s", messages=[])
    assert [c.arguments for c in r.tool_calls] == [{"cutaway": True}, {"recolor": {"fan": "#d8ae55"}}]


async def test_a_complete_call_with_junk_after_it_is_kept():
    chunks = [_delta(0, "c1", "edit_3d_model", '{"cutaway": true}}')]
    r = await _chat(chunks).complete(system="s", messages=[])
    assert r.tool_calls[0].arguments == {"cutaway": True}


async def test_a_cut_off_call_is_still_flagged_for_a_retry():
    chunks = [_delta(0, "c1", "edit_3d_model", '{"add_parts": [{"id": "x", "size"')]
    r = await _chat(chunks).complete(system="s", messages=[])
    assert r.tool_calls[0].arguments == {"__unparseable__": True}


def test_the_model_so_far_has_every_finished_part_and_no_narration():
    raw = json.dumps(MODEL)
    cut = raw.index('"id": "piston"')  # partway through the parts
    p = PartialModel("obj_fixed", "Engine")
    scene = p.update(raw[:cut], force=True)
    assert scene is not None and scene.id == "obj_fixed"
    ids = [x.id for x in scene.parts]
    assert ids and "piston" not in ids
    assert all(x.parent is None or x.parent in ids for x in scene.parts)  # children wait for parents
    assert [b.narration for b in scene.beats] == [""]  # nothing is said while it assembles
    assert scene.auto_play is False  # paused, so the panel doesn't take it for finished and close it
    assert p.update(raw[:cut], force=True) is None  # nothing new, nothing sent
    more = p.update(raw, force=True)
    assert more is not None and len(more.parts) > len(scene.parts)


class StreamedBuilder:
    """Plays the builder's tool call back in pieces, the way StreamingChat delivers it."""

    async def complete(self, *, system, messages, tools=None, max_tokens=None, on_partial=None, on_call=None):
        raw = json.dumps(MODEL)
        for cut in range(200, len(raw), 200):
            if on_partial:
                await on_partial(0, "build_3d_model", raw[:cut])
        return LLMResponse(tool_calls=[ToolCall(id="b", name="build_3d_model", arguments=MODEL)], usage=Usage(), stop_reason="tool_use")


async def test_the_builder_shows_parts_as_they_stream_then_the_same_model_finished(monkeypatch):
    import pyxi.scenes.builder as b

    monkeypatch.setattr(b, "PARTIAL_EVERY_S", 0.0)
    shown = []

    async def on_parts(scene):
        shown.append(scene)

    r = await build_model(StreamedBuilder(), "A four-stroke engine", on_parts=on_parts)
    assert r.model is not None and len(shown) >= 3
    counts = [len(s.parts) for s in shown]
    assert counts == sorted(counts) and counts[0] < len(r.model.parts)
    assert {s.id for s in shown} == {r.model.id}  # one model growing, not many models
    assert r.model.motions and r.model.beats[0].narration


async def test_an_explain_in_3d_scene_shows_before_its_narration_has_arrived(settings):
    from pyxi.scenes import get_scene
    from test_brain_and_server import make_brain
    from test_streaming import Streamed

    spec = get_scene("https").model_dump(by_alias=True, mode="json", exclude={"id"})
    brain, rec = make_brain(settings, llm=Streamed([("say", {"text": "Here's how."}), ("explain_in_3d", spec)]))
    await brain.turn("explain https in 3d")
    scenes = [e["scene"] for e in rec.of("scene")]
    assert len(scenes) == 2
    preview, final = scenes
    assert preview["id"].endswith("_preview") and preview["id"] != final["id"]
    assert [n["id"] for n in preview["nodes"]] == [n["id"] for n in final["nodes"]]
    assert [s["narration"] for s in preview["steps"]] == [""]  # silent until the real steps
    assert len(final["steps"]) == len(spec["steps"])
