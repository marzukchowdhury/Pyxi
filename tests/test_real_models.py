"""Made things look made, a model taken apart stays apart, and a model never lands on a diagram.

Seen live: an AI chip came out as flat pastel blocks seen from straight above; a refrigerator
asked for "taken apart" had two of its parts put back on the model by the look before showing,
and was a spring, a ball and some tubes with no cabinet; and the model was drawn on top of the
diagram drawn a moment before it.
"""

from __future__ import annotations

import json
import time

from otto.types import LLMResponse, ToolCall, Usage
from PIL import Image

from pyxi.companion.brain import _overlap
from pyxi.scenes.builder import SYSTEM, build_model, examples_for, is_electronic, is_living
from pyxi.scenes.check import apart_on_purpose, asked_apart, floating, spread_out, tidy
from pyxi.scenes.diagram import parse_diagram
from pyxi.scenes.model_examples import CAT, CHIP, ELECTRONIC_EXAMPLES, EXAMPLES, HEART, JET, LIVING_EXAMPLES
from pyxi.scenes.objects import SURFACES, ObjectEdit, ObjectScene, apply_edit, build_tool_schema
from pyxi.screen import ScreenContext
from pyxi.screen.context import WindowInfo
from test_brain_and_server import make_brain
from test_diagram import REFUND


def scene(*parts: dict, **extra) -> ObjectScene:
    return ObjectScene.model_validate({"title": "Test", "parts": list(parts), **extra})


class Modeller:
    """A builder that answers each call with the next scripted tool call."""

    def __init__(self, *calls: tuple[str, dict]) -> None:
        self.calls, self.prompts, self.systems = list(calls), [], []

    async def complete(self, *, system, messages, tools, max_tokens=0, **kw) -> LLMResponse:
        self.prompts.append("".join(getattr(part, "text", "") for part in messages[-1].parts))
        self.systems.append(system)
        name, args = self.calls.pop(0)
        return LLMResponse(text="", tool_calls=[ToolCall(id="c", name=name, arguments=args)], usage=Usage(cost_usd=0.01))


# ------------------------------------------------------------------ the spec
def test_a_part_can_ask_for_surface_detail_and_real_electronics_materials():
    m = scene(
        {"id": "board", "size": [9, 0.3, 9], "material": "pcb", "surface": "traces"},
        {"id": "die", "size": [3, 0.2, 3], "position": [0, 0.25, 0], "material": "silicon", "surface": "cells", "density": 2},
        {"id": "balls", "size": [8, 0.1, 8], "position": [0, -0.2, 0], "material": "solder", "surface": "pins"},
        {"id": "pipe", "shape": "cylinder", "size": [0.3, 0.3, 6], "position": [0, 0.5, 0], "material": "copper"},
        {"id": "sink", "size": [6, 1, 4], "position": [0, 1.2, 0], "material": "anodized", "surface": "fins"},
    )
    assert [p.surface for p in m.parts] == ["traces", "cells", "pins", None, "fins"]
    assert m.parts[1].density == 2 and m.parts[0].density is None and not m.notes
    assert set(SURFACES) == {"pins", "traces", "cells", "fins", "brushed", "vents", "grille", "knurl", "panel_lines"}
    # It survives the trip to the renderer, and an edit can set it.
    sent = m.model_dump(mode="json")["parts"][1]
    assert sent["surface"] == "cells" and sent["density"] == 2 and sent["material"] == "silicon"
    edited = apply_edit(m, ObjectEdit.model_validate({"update": [{"id": "pipe", "surface": "brushed", "material": "brushed_metal"}]}))
    assert edited.parts[3].surface == "brushed"


def test_a_wrong_word_for_a_material_or_surface_never_fails_the_model():
    # Live: one part written as "copper" (not then a material) cost a whole second build.
    m = scene(
        {"id": "a", "material": "aluminium", "surface": "Panel Lines", "density": 9},
        {"id": "b", "material": "Stainless Steel", "surface": "sparkles"},
        {"id": "c", "material": "unobtainium", "position": [3, 0, 0]},
        {"id": "d", "material": "circuit-board", "position": [6, 0, 0]},
    )
    assert [p.material for p in m.parts] == ["brushed_metal", "metal", "plastic", "pcb"]
    assert [p.surface for p in m.parts] == ["panel_lines", None, None, None]
    assert m.parts[0].density == 4  # pulled into range, like every other number


def test_the_builder_is_told_about_surfaces_and_the_new_materials():
    part = build_tool_schema()["properties"]["parts"]["items"]["properties"]
    assert part["surface"]["enum"] == list(SURFACES) and "density" in part
    assert {"silicon", "pcb", "solder", "copper", "anodized"} <= set(part["material"]["enum"])
    # The rules: real materials and colours, pastel only for flows; the thing itself is there.
    for words in ("Pastel and bright", "Surface detail is free", "The thing itself is always there", "Taken apart", "opens on iso"):
        assert words in SYSTEM, words


# ------------------------------------------------------------------ which examples
def test_electronics_are_shown_the_machine_and_the_chip():
    for words in (
        "An AI chip. Show the die, the memory and the contacts",
        "A graphics card. Show how it stays cool",
        "A smartphone cut open. Show the screen, the battery, the main board, the body",
        "A hard drive. Show the platters and the read-write head on its arm",
        "how does a cell phone find a tower",
        "a motherboard",
        "inside a laptop",
    ):
        assert is_electronic(words), words
        assert examples_for(words) is ELECTRONIC_EXAMPLES
    for words in ("A jet engine cut open", "A bicycle", "a refrigerator", "how a cat lands on its feet", "the human eye, like a camera", "a neuron firing"):
        assert not is_electronic(words), words
    assert examples_for("a jet engine") is EXAMPLES
    assert examples_for("A domestic cat. Show the inner ear") is LIVING_EXAMPLES
    assert is_living("the human eye, like a camera") and examples_for("the human eye, like a camera") is LIVING_EXAMPLES
    # Two examples, as ever: the prompt is no bigger than it was.
    assert ELECTRONIC_EXAMPLES == (JET, CHIP) and len(ELECTRONIC_EXAMPLES) == len(EXAMPLES) == 2
    size = lambda ex: sum(len(json.dumps(e, separators=(",", ":"))) for e in ex)  # noqa: E731
    assert size(ELECTRONIC_EXAMPLES) <= size(EXAMPLES) + 600


def test_the_chip_example_is_sound_and_shows_what_it_teaches():
    m = ObjectScene.model_validate(CHIP)
    assert not m.notes, m.notes
    fixed, done = tidy(m)
    assert not done and not floating(fixed)
    by_id = {p.id: p for p in m.parts}
    assert by_id["board"].material == "pcb" and by_id["board"].surface == "traces"
    assert by_id["die"].material == "silicon" and by_id["die"].surface == "cells"
    assert by_id["contacts"].surface == "pins" and by_id["pads"].material == "gold"
    assert by_id["lid"].casing and by_id["lid"].material == "brushed_metal"
    assert "memory_twin" in by_id  # memory beside the die, on both sides
    # Only the flows are bright: every solid part is a real material in a real, dark colour.
    for p in m.parts:
        if p.shape != "arrow":
            assert p.material != "emissive" and p.material != "painted", p.id
    # It opens whole, from three quarters, and only then turns the lid to glass.
    assert m.beats[0].camera == "iso" and not m.cutaway and m.beats[1].cutaway


async def test_the_builder_sees_the_chip_for_electronics_and_not_otherwise():
    llm = Modeller(("build_3d_model", CHIP))
    result = await build_model(llm, "An AI chip. Show the die and the memory beside it")
    assert result.model is not None and result.checks == [] and len(llm.prompts) == 1
    assert '"surface":"cells"' in llm.systems[0] and "F-35B" in llm.systems[0]
    assert "How your heart beats" not in llm.systems[0]
    llm = Modeller(("build_3d_model", JET))
    await build_model(llm, "how the F-35B hovers")
    assert '"surface":"cells"' not in llm.systems[0] and "How your heart beats" in llm.systems[0]
    # What is on screen behind the ask never outvotes the ask itself.
    llm = Modeller(("build_3d_model", CAT))
    await build_model(llm, "how a cat lands on its feet", context="Chrome - laptop reviews, best GPU 2026")
    assert '"shape":"blob"' in llm.systems[0] and '"surface":"cells"' not in llm.systems[0]
    assert HEART in LIVING_EXAMPLES


# ------------------------------------------------------------------ taken apart
# A refrigerator's working parts in a row, the cabinet first: an exploded view.
FRIDGE = [
    {"id": "cabinet", "shape": "rounded_box", "size": [3, 6, 2.6], "position": [-6, 0, 0], "material": "painted", "color": "#e8eaed", "label": "Cabinet"},
    {"id": "evap", "size": [2.2, 1.6, 0.3], "position": [-2.4, 1.5, 0], "material": "brushed_metal", "label": "Evaporator"},
    {"id": "comp", "shape": "sphere", "size": 1.4, "position": [0.6, -2, 0], "material": "dark_metal", "label": "Compressor"},
    {"id": "cond", "size": [0.3, 3.4, 2.2], "position": [3.4, 0, 0], "material": "copper", "label": "Condenser"},
    {"id": "valve", "shape": "cone", "size": 0.4, "position": [5.6, 1, 0], "material": "gold", "label": "Valve"},
]
BRIEF = "Household refrigerator refrigeration cycle, taken apart into its parts. Show the compressor, condenser, valve and evaporator."


def test_words_that_ask_for_it_taken_apart():
    for words in (
        BRIEF,
        "build it in 3D and take it apart",
        "an exploded view of a watch",
        "A gearbox, disassembled",
        "show me a laptop in pieces",
        "pull the engine apart for me",
        "a teardown of the phone",
        "Engine, dismantled into its components",
    ):
        assert asked_apart(words), words
    for words in ("how a refrigerator keeps things cold", "a jet engine cut open", "the parts of a flower", "an apartment block", "department store"):
        assert not asked_apart(words), words


def test_parts_apart_are_the_point_of_an_exploded_view():
    m = scene(*FRIDGE)
    # By the numbers alone the small ones look like mistakes: this is what was "put back".
    assert {pid for f in floating(m) for pid in f.ids} == {"comp", "cond", "valve"}
    assert spread_out(m)  # five groups in a row along x, air between each
    assert apart_on_purpose(m) == "laid out as an exploded view"
    assert apart_on_purpose(m, BRIEF) == "asked for taken apart"
    # Asked for by its title alone.
    car = scene(
        {"id": "body", "size": [4, 1.2, 9], "position": [0, 1.2, 0]},
        {"id": "cabin", "size": [3.4, 1, 4], "position": [0, 2.3, -0.5]},
        {"id": "headlight", "shape": "sphere", "size": 0.5, "position": [1.2, 1.4, 6.6]},
    )
    assert apart_on_purpose(car) is None and not spread_out(car)  # one stray part is still a mistake
    apart = car.model_copy(update={"title": "A car, exploded view"})
    assert apart_on_purpose(apart) == "asked for taken apart"
    # Parts scattered every which way are not an exploded view.
    mess = scene(
        {"id": "a", "size": 3, "position": [0, 0, 0]},
        {"id": "b", "size": 0.5, "position": [4, 4, 0]},
        {"id": "c", "size": 0.5, "position": [4, -4, 0]},
        {"id": "d", "size": 0.5, "position": [0.5, 4, 4]},
    )
    assert not spread_out(mess)


async def test_the_look_over_never_puts_an_exploded_view_back_together():
    model = {"title": "Refrigerator, taken apart", "parts": FRIDGE}
    # The builder would move them if asked (the second scripted call): it must never be asked.
    llm = Modeller(("build_3d_model", model), ("move_parts", {"moves": [{"id": "valve", "position": [3.4, 1, 0]}, {"id": "cond", "position": [1.2, 0, 0]}]}))
    result = await build_model(llm, BRIEF)
    assert len(llm.prompts) == 1 and len(llm.calls) == 1  # no second call
    assert {p.id: p.position for p in result.model.parts}["valve"] == [5.6, 1, 0]
    assert {p.id: p.position for p in result.model.parts}["cond"] == [3.4, 0, 0]
    assert result.checks == ["asked for taken apart: parts left where they are"]
    # Laid out as one, with no such words anywhere: the same.
    llm = Modeller(("build_3d_model", {"title": "Refrigerator", "parts": FRIDGE}), ("move_parts", {"moves": []}))
    result = await build_model(llm, "a refrigerator's refrigeration cycle")
    assert len(llm.prompts) == 1 and result.checks == ["laid out as an exploded view: parts left where they are"]
    # The mending that has one right answer still happens: the same part twice goes.
    twice = [*FRIDGE, {**FRIDGE[2], "id": "comp_again", "label": None}]
    result = await build_model(Modeller(("build_3d_model", {"title": "Fridge", "parts": twice})), BRIEF)
    assert "comp_again" not in {p.id for p in result.model.parts}
    assert result.checks[0].startswith("comp_again was comp again") and result.checks[-1].endswith("parts left where they are")


# ------------------------------------------------------------------ on screen, clear of the rest
def empty_screen(w: int = 1920, h: int = 1080) -> ScreenContext:
    return ScreenContext(time.time(), WindowInfo("Desktop", "explorer.exe", (0, 0, w, h)), [], Image.new("RGB", (w, h), (245, 245, 248)))


MODEL = ObjectScene.model_validate({"title": "Refrigerator", "parts": FRIDGE})


def brain_on(settings, ctx: ScreenContext):
    brain, rec = make_brain(settings)
    brain._ctx = ctx
    brain._watch_window = lambda: None  # no window to watch in a test
    return brain, rec


async def test_a_model_shown_while_a_diagram_is_up_does_not_cover_it(settings):
    brain, rec = brain_on(settings, empty_screen())
    await brain.show_sketch(parse_diagram(REFUND))
    await brain.show_object(MODEL, restart=False)
    diagram, model = rec.of("sketch")[0]["place"], rec.of("object")[0]["place"]
    assert diagram and model and _overlap(tuple(diagram), tuple(model)) == 0
    assert not rec.of("clear")  # there was room for both: nothing was put away
    # The model has its own entry, so what comes next keeps clear of it: a card...
    assert tuple(model) in brain._placed.others("card") and tuple(diagram) in brain._placed.others("card")
    # ...and a second diagram, which replaces the first, does not land on the model.
    await brain.show_sketch(parse_diagram(REFUND))
    assert _overlap(tuple(rec.of("sketch")[1]["place"]), tuple(model)) == 0
    # The model as it assembles and when it is finished stays put; shown again it still avoids the diagram.
    await brain.show_object(MODEL, restart=False, keep_place=True)
    assert rec.of("object")[1]["place"] == model
    await brain.show_object(MODEL, restart=False)
    assert _overlap(tuple(rec.of("object")[2]["place"]), tuple(rec.of("sketch")[1]["place"])) == 0
    # Closed: its spot is free again.
    brain.object_closed(by_them=False)
    assert tuple(rec.of("object")[2]["place"]) not in brain._placed.others("card")


async def test_with_no_room_for_both_the_diagram_is_put_away_first(settings):
    brain, rec = brain_on(settings, empty_screen(1100, 760))
    await brain.show_sketch(parse_diagram(REFUND))
    assert rec.of("sketch")[0]["place"]
    await brain.show_object(MODEL, restart=False)
    assert rec.of("clear")  # the overlay drops the diagram: the model was asked for last
    assert rec.of("object")[0]["place"] and brain._placed.others("model") == []


async def test_a_card_on_screen_is_kept_clear_of_too(settings):
    brain, rec = brain_on(settings, empty_screen())
    card = (700, 300, 420, 260)
    brain._placed.put("card", card, 30.0)
    await brain.show_object(MODEL, restart=False)
    assert _overlap(tuple(rec.of("object")[0]["place"]), card) == 0 and not rec.of("clear")
