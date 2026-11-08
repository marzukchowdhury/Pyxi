"""The 3D builder: a dedicated modelling call, separate from the conversation.

The chat model decides *what* to show ("an F-35B cut open, the lift fan and swivel nozzle,
air going down") and talks; this builder, which can be a stronger model, does the modelling
with one job and two worked examples in front of it. Quality of the shape no longer depends on
the fast model that keeps the conversation snappy.
"""

from __future__ import annotations

import inspect
import json
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from otto.llm.base import LLMProvider, Message, ToolSpec
from pydantic import ValidationError

from pyxi.partial_json import complete_items, partial_string
from pyxi.scenes.library import few_labels
from pyxi.scenes.model_examples import EXAMPLES
from pyxi.scenes.objects import (
    Beat,
    ObjectEdit,
    ObjectScene,
    Part,
    apply_edit,
    build_tool_schema,
    edit_tool_schema,
)

SYSTEM = """\
You are Pyxi's 3D modeller. You build one model per request with the build_3d_model tool. It \
floats on the user's screen with no background, with sticky-note labels and narrated beats, \
so the object itself has to look right at a glance: someone should recognise it before any \
label appears.

How good models are built:
- Real silhouettes. Anything round in cross-section (fuselage, rocket, missile, bottle, \
piston, nose cone, lens barrel, vase, bullet) is ONE lathe: `profile` is [[along, radius], ...] \
from one end to the other along `axis` (6-12 points, a smooth outline). Any flat outline (wing \
or fin from above, hull side, blade, circuit board, phone body, key, gun frame) is ONE extrude \
with its true outline as `profile` (8-16 points, traced like a technical drawing). Never \
capsules or boxes for bodies or wings.
- Scale and proportion: the whole thing 8-12 units across, centred near the origin, y up. Think \
of the real dimensions first (an F-16 is 15 m long with a 10 m span; a piston is about as tall \
as it is wide) and keep every part in that ratio; a heart is taller than wide.
- Detail sells it: after the main parts, add the small things that make it read as real, as \
children of the part they sit on: bolts and rivets, rims, lips and flanges, vents and intakes, \
rails, handles, seams, a hex nut, a nozzle ring, wingtip missiles, a pitot tube. Three or four \
of these on the hero part do more than ten more big pieces. Every small piece touches what it \
sits on: a headlight or grille is set into the body's front face, never floating in front of \
it; wheels tuck up into the body, their tops inside the wheel arches.
- Ready-made pieces: wheels, missiles, propellers, jet engines, RAM sticks, graphics cards, \
chips (CPUs, AI chips), PC fans, screens, seats, people, bolts and houses are `kit` parts \
(shape kit, kit=<name>, sized and placed like any part): detailed and real-looking, never \
rebuild them from boxes. A computer is boards, chips and fans from the kit; a vehicle is a body \
extrude or lathe plus kit wheels (a wheel's size is [diameter, diameter, width] with its thin \
side across the vehicle); a fighter is its outline plus kit missiles and a seat.
- Materials: real ones, in contrast (painted body, dark_metal workings, brushed_metal moving \
parts, rubber seals, glass); give painted and plastic parts a real colour, not default grey.
- Inside and outside: outer shells get casing=true and the model gets cutaway=true when the \
inside is the point, so the shell becomes a see-through ghost and the workings show.
- 16-36 parts: enough to be convincing, each one meaningful. Group assemblies with `parent`.
- Alive: fans, rotors, turbines, wheels and gears spin; beating or pumping parts oscillate on \
scale; latches, pistons, triggers and slides move with keyframes; whatever travels (air, \
blood, fuel, water, current, data) gets `arrow` parts along its route.
- Labels: the 4-7 parts that matter, 1-3 words each, no notes.
- Be brief in the JSON: leave out every field that is at its default (rotation [0,0,0], \
opacity 1, hidden, casing false, plastic material, axis y, style studio, auto_play). The \
model is drawn while the user waits, so every extra field is time they wait.
- Beats: 3-5, rising `at` times (2-3 s apart), each focusing the parts it is about, with the \
camera that shows them best (side for vehicles, iso for depth, front for faces, close for a \
detail). Narration: one vivid spoken sentence each, a concrete fact or analogy, never a \
description of the picture; the last one lands the "so that's why", adds one related fun \
fact, and offers one specific thing to show next (inside a part, how it's assembled, a diagram).

Two complete examples of the quality expected (different subjects; follow the rules, not the \
shapes):
{examples}
"""


@dataclass
class BuildResult:
    model: ObjectScene | None
    cost_usd: float = 0.0
    seconds: float = 0.0
    attempts: int = 0
    errors: list[str] = field(default_factory=list)


def builder_tool() -> ToolSpec:
    return ToolSpec(
        "build_3d_model",
        "Build the 3D model: parts, motions and narrated beats.",
        build_tool_schema(),
    )


OnParts = Callable[[ObjectScene], Awaitable[None]]
_CUTAWAY = re.compile(r'"cutaway"\s*:\s*true')
PARTIAL_EVERY_S = 0.3  # at most ~3 partial models a second: each one is the whole model so far


class PartialModel:
    """Builds the model so far from the builder's streaming JSON: every part that has fully
    arrived, standing still and silent (motions and beats come last), so the model can be on
    screen assembling itself a few seconds in instead of appearing all at once a minute later.
    A part whose parent hasn't arrived yet waits for it, so nothing lands in the wrong place."""

    def __init__(self, scene_id: str, fallback_title: str) -> None:
        self.scene_id = scene_id
        self.fallback_title = fallback_title
        self.shown = 0
        self.last = 0.0

    def update(self, raw: str, *, force: bool = False) -> ObjectScene | None:
        items = complete_items(raw, "parts")
        if len(items) <= self.shown:
            return None
        now = time.monotonic()
        if not force and now - self.last < PARTIAL_EVERY_S and self.shown:
            return None
        parts: list[Part] = []
        ids: set[str] = set()
        for raw_part in items:
            try:
                part = Part.model_validate(raw_part)
            except ValidationError:
                continue
            if part.parent and part.parent not in ids:
                continue
            parts.append(part)
            ids.add(part.id)
        if not parts or len(parts) <= self.shown:
            return None
        title = partial_string(raw, "title")
        try:
            scene = ObjectScene(
                id=self.scene_id,
                title=(title[0] if title and title[1] else "") or self.fallback_title,
                parts=parts,
                cutaway=bool(_CUTAWAY.search(raw.split('"parts"', 1)[0])),
                beats=[Beat(title="Building", narration="", camera="iso")],
                # Paused: a story with nothing to tell counts as finished, and a finished model
                # tidies itself away; the narrated model that replaces it plays.
                auto_play=False,
            )
        except ValidationError:
            return None
        self.shown, self.last = len(parts), now
        return scene


async def build_model(
    llm: LLMProvider, brief: str, context: str = "", on_parts: OnParts | None = None
) -> BuildResult:
    """Build a model from a brief; one repair attempt if the first try doesn't validate.

    ``on_parts``: called with the model so far as its parts stream in (only for a streaming
    model). The finished model keeps the same id, so the view carries straight on from it."""
    system = SYSTEM.format(
        examples="\n".join(json.dumps(e, separators=(",", ":")) for e in EXAMPLES)
    )
    ask = f"Build this:\n{brief}"
    if context:
        ask += f"\n\nWhat the user is looking at (use it so the model is about their exact thing):\n{context[:1500]}"
    result = BuildResult(model=None)
    started = time.monotonic()
    messages = [Message.user(ask)]
    scene_id = ObjectScene(title="x", parts=[Part(id="x")]).id
    params = inspect.signature(llm.complete).parameters
    streams = on_parts is not None and "on_partial" in params
    for _ in range(2):
        result.attempts += 1
        extra: dict[str, Any] = {}
        if streams:
            partial = PartialModel(scene_id, brief.split(".")[0][:60] or "Model")

            async def as_it_streams(i: int, name: str, raw: str, partial=partial) -> None:
                if name == "build_3d_model" and (scene := partial.update(raw)) is not None:
                    await on_parts(scene)  # type: ignore[misc]

            extra["on_partial"] = as_it_streams
        response = await llm.complete(
            system=system, messages=messages, tools=[builder_tool()], max_tokens=12000, **extra
        )
        result.cost_usd += response.usage.cost_usd
        call = next((c for c in response.tool_calls if c.name == "build_3d_model"), None)
        if call is None:
            result.errors.append("no tool call")
            messages = [Message.user(ask + "\n\nCall build_3d_model now.")]
            continue
        try:
            result.model = ObjectScene.model_validate({**dict(call.arguments), "id": scene_id})
            break
        except ValidationError as exc:
            errors = exc.errors()[:8]
            result.errors.append(str(errors)[:400])
            messages = [
                Message.user(
                    f"{ask}\n\nYour last attempt was rejected: {errors}. Fix those and call "
                    "build_3d_model again with the complete model."
                )
            ]
    result.seconds = time.monotonic() - started
    return result


EXPLAIN_SYSTEM = (
    "You are Pyxi's 3D explainer. A real, detailed 3D model of the subject is already on the "
    "user's screen (listed below with every part's id, centre and size, in stage units, y up). "
    "You make the explanation on it with ONE explain_on_model call, the same format as an edit:\n"
    "- update: give the 4-7 parts that matter a label (1-3 words each); never rename ids. Where the "
    "model is one piece, add `marker` parts (invisible; just a label) on the spots that "
    "matter, positioned on the real part's surface.\n"
    "- add_motions: move real parts the way the real thing moves (a canopy lifts and flies off, "
    "a rotor spins, a stage separates and falls away, a door opens). Keyframe positions are "
    "absolute: start from the part's centre as listed. Spins and oscillations for anything "
    "that turns.\n"
    "- The model may be a stand-in for something more specific (a generic sports car for a BMW "
    "M3): tag and focus only what it really has, its listed parts or markers on its real "
    "surface. Never tag a feature it lacks (a kidney grille, a spoiler, red calipers) and never "
    "build one from shapes; talk about the real one in the narration instead, and say once, "
    "lightly, that this is a stand-in shape.\n"
    "- add_parts: only effects the story needs, never pieces of the object itself: thin arrows "
    "for anything that flows (air, exhaust, blood, current, light, data), at most a third of the "
    "model's length; a flame or plume (emissive cone); a parachute, person or stand away from "
    "the model. Few.\n"
    "- title: what the user asked about, 2-5 words ('BMW M3 CS'), not the model's own name.\n"
    "- cutaway true (with casing) only when the inside is the point and the shell is a part.\n"
    "- beats: 3-5, each with a 2-4 word title ('First stage drops'), rising `at` times 2-3 s "
    "apart, each focusing the parts it is about, camera "
    "side/front/iso/close/top to show them best. Narration: one vivid spoken sentence each, a "
    "concrete fact or analogy, never a description of the picture; the last lands the 'so "
    "that's why', adds one related fun fact, and offers one specific thing to show next.\n"
    "Be brief: leave out fields at their defaults."
)


_EFFECTS = {"arrow", "marker", "kit"}


def _effects_only(model: ObjectScene, added: list[Part]) -> list[Part]:
    """What an explanation may add to a real model: arrows, tags, glowing flames and plumes, kit
    pieces, and props standing clear of it. A plain block on the model (a "spoiler" on a car that
    has none, a box for a brake caliper) only makes a real model look fake, so it goes; and
    nothing added may dwarf the model (a giant airflow beam across the screen)."""
    lo = [min(p.position[i] - p.size[i] / 2 for p in model.parts) for i in range(3)]
    hi = [max(p.position[i] + p.size[i] / 2 for p in model.parts) for i in range(3)]
    extent = max(h - l for l, h in zip(lo, hi))
    near = [0.08 * (h - l) for l, h in zip(lo, hi)]
    real = {p.id: p for p in model.parts}
    keep: list[Part] = []
    for p in added:
        if p.parent and p.parent not in real and p.parent not in {k.id for k in keep}:
            continue  # its parent was dropped
        # Positions under a parent are relative to it.
        base = real[p.parent].position if p.parent in real else [0.0, 0.0, 0.0]
        at = [base[i] + p.position[i] for i in range(3)]
        on_model = all(lo[i] - near[i] <= at[i] <= hi[i] + near[i] for i in range(3))
        added_child = p.parent is not None and p.parent not in real
        if on_model and not added_child and p.shape not in _EFFECTS and p.material != "emissive":
            continue
        biggest = max(p.size)
        if biggest > extent / 3:
            p.size = [v * extent / 3 / biggest for v in p.size]
        keep.append(p)
    return keep


def explain_tool() -> ToolSpec:
    return ToolSpec(
        "explain_on_model",
        "Explain on the real model: labels, motions, the few extra parts the story needs, beats.",
        edit_tool_schema(),
    )


async def explain_library_model(
    llm: LLMProvider, model: ObjectScene, summary: str, brief: str, context: str = ""
) -> BuildResult:
    """Write the explanation for a real library model (an edit on it, so the real parts stay)."""
    ask = f"{summary}\n\nExplain this on it:\n{brief}"
    if context:
        ask += f"\n\nWhat the user is looking at:\n{context[:1200]}"
    result = BuildResult(model=None)
    started = time.monotonic()
    messages = [Message.user(ask)]
    for _ in range(2):
        result.attempts += 1
        response = await llm.complete(
            system=EXPLAIN_SYSTEM, messages=messages, tools=[explain_tool()], max_tokens=6000
        )
        result.cost_usd += response.usage.cost_usd
        call = next((c for c in response.tool_calls if c.name == "explain_on_model"), None)
        if call is None:
            result.errors.append("no tool call")
            messages = [Message.user(ask + "\n\nCall explain_on_model now.")]
            continue
        try:
            edit = ObjectEdit.model_validate(dict(call.arguments))
            # The real model's parts are never removed or reshaped by the explanation.
            edit.remove = []
            real = {p.id for p in model.parts if p.shape == "asset"}
            for patch in edit.update:
                if patch.id in real:
                    patch.size = patch.material = patch.color = None
            edit.add_parts = _effects_only(model, edit.add_parts)
            result.model = few_labels(apply_edit(model, edit))
            break
        except ValidationError as exc:
            errors = exc.errors()[:8]
            result.errors.append(str(errors)[:400])
            messages = [
                Message.user(
                    f"{ask}\n\nYour last attempt was rejected: {errors}. Fix those and call "
                    "explain_on_model again."
                )
            ]
    result.seconds = time.monotonic() - started
    return result
