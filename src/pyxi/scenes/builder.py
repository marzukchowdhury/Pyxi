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
from pyxi.scenes.model_examples import EXAMPLES
from pyxi.scenes.objects import Beat, ObjectScene, Part, build_tool_schema

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
of these on the hero part do more than ten more big pieces.
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
