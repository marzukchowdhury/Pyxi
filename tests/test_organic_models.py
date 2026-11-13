"""Living things are not built from boxes, and a model is looked over before it is shown.

Built models read as machines whatever they were of: a cat was boxes and cylinders. Two shapes
were added for what grows (a `limb` that swells and tapers along a spine, a `blob` of lumps in
one skin), and a look at the numbers of a finished model catches what nobody was looking for:
a part left hanging in the air, the same part twice in one place, a labelled part sealed inside
a solid one.
"""

from __future__ import annotations

from otto.types import LLMResponse, ToolCall, Usage

from pyxi.scenes.builder import BuildResult, build_model, is_living, look_over
from pyxi.scenes.check import boxes, floating, tidy
from pyxi.scenes.model_examples import CAT, EXAMPLES, HEART, JET, LIVING_EXAMPLES
from pyxi.scenes.objects import ObjectScene, Part, build_tool_schema


def scene(*parts: dict, **extra) -> ObjectScene:
    return ObjectScene.model_validate({"title": "Test", "parts": list(parts), **extra})


# ------------------------------------------------------------------ the shapes
def test_a_limb_and_a_blob_know_how_big_they_are():
    m = scene(
        {"id": "leg", "shape": "limb", "position": [1, 2, 0], "path": [[0, 1.2, 0], [0, 0, 0.1], [0, -1.3, 0]], "radii": [0.4, 0.25, 0.2]},
        {"id": "body", "shape": "blob", "position": [0, 3, 0], "lumps": [[0, 0, -1.5, 1.2, 1.1, 1.3], [0, 0.1, 1.2, 1.1, 1.0, 1.2]]},
    )
    leg, body = m.parts
    # The box is worked out from the spine and its thickness: 1.2 + 0.4 above, 1.3 + 0.2 below.
    assert [round(v, 2) for v in leg.size] == [0.8, 3.1, 0.8]
    assert round(leg.position[1], 2) == 2.05  # re-centred on its box, and the spine moved to match
    assert round(leg.path[0][1] + leg.position[1], 2) == 3.2  # the top of the spine is where it was written
    assert [round(v, 2) for v in body.size] == [2.4, 2.2, 5.2]
    assert not m.notes


def test_fewer_radii_than_spine_points_are_spread_along_it():
    m = scene({"id": "tail", "shape": "limb", "path": [[0, 0, 0], [0, 0, 1], [0, 0, 2], [0, 0, 3]], "radii": [0.4, 0.1]})
    # 0.4 thick at the start, 0.1 at the tip: the box is as wide as the thick end.
    assert round(m.parts[0].size[0], 2) == 0.8 and round(m.parts[0].size[2], 2) == 3.5


def test_a_lump_can_be_a_ball_or_an_egg_and_rubbish_is_dropped():
    part = Part.model_validate({"id": "b", "shape": "blob", "lumps": [[0, 0, 0, 1], [2, 0, 0, 1, 0.5, 0.5], "x", [1, 2], [0, 0, 0, -5]]})
    assert part.lumps == [[0, 0, 0, 1, 1, 1], [2, 0, 0, 1, 0.5, 0.5], [0, 0, 0, 0.01, 0.01, 0.01]]


def test_a_limb_with_no_spine_and_a_blob_with_no_lumps_are_still_drawn():
    m = scene({"id": "a", "shape": "limb"}, {"id": "b", "shape": "blob"})
    assert [p.shape for p in m.parts] == ["capsule", "sphere"]
    assert len(m.notes) == 2


def test_a_living_part_is_never_hard_edged():
    m = scene(
        {"id": "torso", "shape": "box", "organic": True},
        {"id": "arm", "shape": "cylinder", "organic": True},
        {"id": "beak", "shape": "cone", "organic": True},  # a point is fine on a living thing
        {"id": "collar", "shape": "box"},  # and a made thing on it stays as made
    )
    assert [p.shape for p in m.parts] == ["sphere", "capsule", "cone", "box"]
    assert "a living part is never a box" in m.notes[0]


def test_a_flat_living_part_is_thin():
    fin = {"id": "fin", "shape": "extrude", "axis": "x", "size": [1.2, 1.5, 2], "profile": [[0, 0], [2, 1], [2, -1]]}
    m = scene({**fin, "organic": True}, {**fin, "id": "plate"}, {**fin, "id": "leaf", "organic": True, "size": [0.05, 1.5, 2]})
    # 8% of its 2-unit outline; the steel plate and the leaf that was already thin are left alone.
    assert [round(p.size[0], 2) for p in m.parts] == [0.16, 1.2, 0.05]
    assert len(m.notes) == 1 and "a flat living part is thin" in m.notes[0]


def test_a_mirrored_part_gets_its_twin_on_the_other_side():
    m = scene(
        {"id": "body", "shape": "sphere", "size": 2},
        {"id": "leg", "shape": "limb", "mirror": True, "position": [0.7, -1, 0.2], "path": [[0, 1, 0], [0.2, -1, 0]], "radii": [0.3], "label": "Leg"},
        {"id": "nose", "shape": "sphere", "mirror": True, "size": 0.2, "position": [0, 0, 1]},  # on the middle line: one nose
        motions=[{"part": "leg", "kind": "spin"}],
    )
    ids = [p.id for p in m.parts]
    assert ids == ["body", "leg", "leg_twin", "nose"]
    leg, twin = m.parts[1], m.parts[2]
    assert round(twin.position[0] + leg.position[0], 6) == 0 and twin.position[1:] == leg.position[1:]
    assert [pt[0] for pt in twin.path] == [-pt[0] for pt in leg.path]
    assert twin.label is None and leg.label == "Leg"  # one tag for the pair
    assert not any(p.mirror for p in m.parts)  # done once: an edit later does not make more
    again = ObjectScene.model_validate(m.model_dump())
    assert [p.id for p in again.parts] == ids
    # A fin drawn as an outline is mirrored too, its outline turned round.
    fin = {"id": "fin", "shape": "extrude", "axis": "z", "mirror": True, "organic": True, "position": [1, 0, 0], "size": [1, 1, 0.05], "profile": [[0, 0], [1, 0.4], [1, -0.4]]}
    twin = scene(fin).parts[1]
    assert twin.id == "fin_twin" and twin.position[0] == -1 and twin.profile == [[0, 0], [-1, 0.4], [-1, -0.4]]


def test_the_builder_is_told_about_them_and_never_about_what_pyxi_sets():
    schema = build_tool_schema()
    part = schema["properties"]["parts"]["items"]["properties"]
    assert {"radii", "squash", "lumps", "blend", "organic", "mirror"} <= set(part)
    assert {"limb", "blob"} <= set(part["shape"]["enum"]) and "asset" not in part["shape"]["enum"]


def test_the_worked_examples_are_sound():
    for example in (JET, HEART, CAT):
        m = ObjectScene.model_validate(example)
        assert not m.notes, (m.title, m.notes)
        fixed, done = tidy(m)
        assert not done and not floating(fixed), m.title
    cat = ObjectScene.model_validate(CAT)
    assert {"front_leg_twin", "hind_leg_twin", "ear_twin", "eye_twin"} <= {p.id for p in cat.parts}
    assert all(p.organic for p in cat.parts if p.shape != "arrow")


def test_living_subjects_get_living_examples():
    for words in ("how does a cat land on its feet", "show me the human heart", "a tree's roots", "how neurons fire"):
        assert is_living(words), words
    for words in ("how a jet engine works", "the inside of a hard drive", "category theory", "a catapult"):
        assert not is_living(words), words
    assert CAT in LIVING_EXAMPLES and CAT not in EXAMPLES and JET in EXAMPLES


# ------------------------------------------------------------------ the look before showing
CAR = [
    {"id": "body", "shape": "box", "size": [4, 1.2, 9], "position": [0, 1.2, 0]},
    {"id": "cabin", "shape": "box", "size": [3.4, 1, 4], "position": [0, 2.3, -0.5]},
    {"id": "wheel_fl", "shape": "kit", "kit": "wheel", "size": [1.4, 1.4, 0.5], "position": [2, 0.7, 3]},
    {"id": "wheel_fr", "shape": "kit", "kit": "wheel", "size": [1.4, 1.4, 0.5], "position": [-2, 0.7, 3]},
]


def test_a_small_part_left_hanging_in_the_air_is_noticed():
    m = scene(*CAR, {"id": "headlight", "shape": "sphere", "size": 0.5, "position": [1.2, 1.4, 6.6], "label": "Headlight"})
    [f] = floating(m)
    assert f.ids == ["headlight"] and f.near == "body" and 1.8 < f.gap < 1.9
    # Set into the body's front face, it is not.
    m = scene(*CAR, {"id": "headlight", "shape": "sphere", "size": 0.5, "position": [1.2, 1.4, 4.5]})
    assert not floating(m)


def test_what_is_meant_to_be_apart_is_left_alone():
    planet = {"id": "earth", "shape": "sphere", "size": 6}
    core = {"id": "core", "shape": "sphere", "size": 2, "casing": False}
    # A moon as big as this is a second thing, not a stray part.
    assert not floating(scene(planet, core, {"id": "moon", "shape": "sphere", "size": 3.4, "position": [9, 0, 0]}))
    # Light, flows and tags are not matter.
    assert not floating(scene(planet, core, {"id": "sun", "shape": "sphere", "size": 1, "position": [12, 0, 0], "material": "emissive"}))
    assert not floating(scene(planet, core, {"id": "ray", "shape": "arrow", "path": [[12, 0, 0], [9, 0, 0]]}))
    # Someone standing beside it for scale.
    assert not floating(scene(planet, core, {"id": "you", "shape": "kit", "kit": "person", "size": [0.5, 1.8, 0.5], "position": [8, 0, 0]}))
    # A part that flies off in the story starts wherever the story needs it.
    seat = {"id": "seat", "shape": "box", "size": 0.6, "position": [0, 9, 0]}
    flies = [{"part": "seat", "kind": "keyframes", "keys": [{"t": 0, "position": [0, 3, 0]}, {"t": 2, "position": [0, 9, 0]}]}]
    assert not floating(scene(planet, core, seat, motions=flies))
    # A child of a turned part is not where the sums say: not judged.
    arm = {"id": "arm", "shape": "box", "size": [0.4, 0.4, 5], "position": [0, 0, 0], "rotation": [0, 45, 0]}
    hand = {"id": "hand", "shape": "sphere", "size": 0.5, "position": [0, 0, 6], "parent": "arm"}
    assert not floating(scene(planet, core, arm, hand))


def test_a_child_is_judged_where_it_really_is():
    m = scene(*CAR, {"id": "mirror", "shape": "box", "size": 0.3, "position": [2.1, 1.0, 1.5], "parent": "cabin"})
    b = boxes(m)["mirror"]
    assert [round((b.lo[i] + b.hi[i]) / 2, 2) for i in range(3)] == [2.1, 3.3, 1.0]  # cabin's place + its own
    assert not floating(m)


def test_the_same_part_twice_in_one_place_goes_and_takes_its_motion():
    m = scene(*CAR, {"id": "wheel_again", "shape": "kit", "kit": "wheel", "size": [1.4, 1.4, 0.5], "position": [2, 0.7, 3]}, motions=[{"part": "wheel_again", "kind": "keyframes", "keys": [{"t": 1, "opacity": 0.5}]}])
    # It moves (even if only by fading), so someone meant it: left alone.
    assert tidy(m)[1] == []
    m = scene(*CAR, {"id": "wheel_again", "shape": "kit", "kit": "wheel", "size": [1.4, 1.4, 0.5], "position": [2, 0.7, 3.001]})
    fixed, done = tidy(m)
    assert [p.id for p in fixed.parts] == ["body", "cabin", "wheel_fl", "wheel_fr"]
    assert "wheel_again was wheel_fl again" in done[0] and done[0] in fixed.notes
    # A different shape in the same box is two things (a ring round a disc).
    m = scene(*CAR, {"id": "hub", "shape": "cylinder", "size": [1.4, 1.4, 0.5], "position": [2, 0.7, 3]})
    assert tidy(m)[1] == []


def test_a_labelled_part_sealed_in_a_solid_one_gets_a_shell_it_can_be_seen_through():
    engine = {"id": "engine", "shape": "box", "size": [1.5, 0.8, 2], "position": [0, 1.2, 2.5], "label": "Engine"}
    fixed, done = tidy(scene(*CAR, engine))
    assert fixed.cutaway and next(p for p in fixed.parts if p.id == "body").casing
    assert "engine was sealed inside body" in done[0]
    # Already a shell, or see-through, or the part pokes out: nothing to do.
    shell = [{**CAR[0], "casing": True}, *CAR[1:]]
    assert tidy(scene(*shell, engine))[1] == []
    assert tidy(scene({**CAR[0], "material": "glass"}, *CAR[1:], engine))[1] == []
    assert tidy(scene(*CAR, {**engine, "position": [0, 1.2, 4.2]}))[1] == []
    # An unlabelled part inside (a frame, a bulkhead) is nobody's business.
    assert tidy(scene(*CAR, {**engine, "label": None}))[1] == []
    # A ball does not fill its box: a part near its corner is not hidden by it.
    ball = {"id": "ball", "shape": "sphere", "size": 6}
    pip = {"id": "pip", "shape": "sphere", "size": 0.4, "position": [2.4, 2.4, 2.4], "label": "Pip"}
    assert tidy(scene(ball, pip))[1] == []
    assert tidy(scene(ball, {**pip, "position": [0.5, 0, 0]}))[1] != []


# ------------------------------------------------------------------ in the builder
class Modeller:
    """A builder that answers each call with the next scripted tool call."""

    def __init__(self, *calls: tuple[str, dict]) -> None:
        self.calls, self.prompts, self.systems = list(calls), [], []

    async def complete(self, *, system, messages, tools, max_tokens=0, **kw) -> LLMResponse:
        self.prompts.append("".join(getattr(part, "text", "") for part in messages[-1].parts))
        self.systems.append(system)
        name, args = self.calls.pop(0)
        return LLMResponse(text="", tool_calls=[ToolCall(id="c", name=name, arguments=args)], usage=Usage(cost_usd=0.01))


CAR_MODEL = {"title": "A car", "parts": [*CAR, {"id": "headlight", "shape": "sphere", "size": 0.5, "position": [1.2, 1.4, 6.6]}]}


async def test_the_builder_is_asked_once_about_a_part_left_in_the_air_and_it_is_put_back():
    llm = Modeller(("build_3d_model", CAR_MODEL), ("move_parts", {"moves": [{"id": "headlight", "position": [1.2, 1.4, 4.5]}, {"id": "body", "position": [0, 50, 0]}]}))
    result = await build_model(llm, "a car")
    assert next(p for p in result.model.parts if p.id == "headlight").position == [1.2, 1.4, 4.5]
    assert next(p for p in result.model.parts if p.id == "body").position == [0, 1.2, 0]  # only what was asked about moves
    assert "headlight" in llm.prompts[1] and "Nearest is body" in llm.prompts[1]
    assert result.checks == ["hanging apart: headlight", "put back on the model: headlight"]
    assert round(result.cost_usd, 2) == 0.02 and not llm.calls


async def test_a_move_that_does_not_help_or_none_at_all_leaves_the_model_as_built():
    for answer, said in (
        ({"moves": [{"id": "headlight", "position": [1.2, 9, 6.6]}]}, "the move did not help: kept as built"),
        ({"moves": []}, "left as they are: meant to be apart"),
    ):
        result = await build_model(Modeller(("build_3d_model", CAR_MODEL), ("move_parts", answer)), "a car")
        assert next(p for p in result.model.parts if p.id == "headlight").position == [1.2, 1.4, 6.6]
        assert result.checks[-1] == said


async def test_a_sound_model_costs_no_second_call_and_a_failed_ask_still_shows_the_model():
    llm = Modeller(("build_3d_model", CAT))
    result = await build_model(llm, "how a cat lands on its feet")
    assert result.model is not None and result.checks == [] and len(llm.prompts) == 1
    assert '"shape":"blob"' in llm.systems[0] and "F-35B" not in llm.systems[0]  # shown the living examples

    class Broken(Modeller):
        async def complete(self, **kw):
            if not self.calls:
                raise RuntimeError("down")
            return await super().complete(**kw)

    model = ObjectScene.model_validate(CAR_MODEL)
    result = BuildResult(model=None)
    assert (await look_over(Broken(), model, result)) is model
    assert result.checks[-1] == "could not ask about them: RuntimeError"
