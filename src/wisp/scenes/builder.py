"""The 3D builder: a dedicated modelling call, separate from the conversation.

The chat model decides *what* to show ("an F-35B cut open, the lift fan and swivel nozzle,
air going down") and talks; this builder, which can be a stronger model, does the modelling
with one job and two worked examples in front of it. Quality of the shape no longer depends on
the fast model that keeps the conversation snappy.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from otto.llm.base import LLMProvider, Message, ToolSpec
from pydantic import ValidationError

from wisp.scenes.model_examples import EXAMPLES
from wisp.scenes.objects import ObjectScene, build_tool_schema

SYSTEM = """\
You are Wisp's 3D modeller. You build one model per request with the build_3d_model tool. It \
floats on the user's screen with no background, with sticky-note labels and narrated beats, \
so the object itself has to look right at a glance: someone should recognise it before any \
label appears.

How good models are built:
- Real silhouettes. Any flat outline (fuselage side view, wing or fin from above, hull, blade, \
circuit board, phone body, key, gun frame) is ONE extrude with its true outline as `profile` \
(8-16 points, traced like a technical drawing). Never capsules or boxes for bodies or wings.
- Scale and proportion: the whole thing 8-12 units across, centred near the origin, y up; parts \
in realistic proportion to each other (a jet's wingspan is about its length; a heart is taller \
than wide).
- Inside and outside: outer shells get casing=true and the model gets cutaway=true when the \
inside is the point, so the shell becomes a see-through ghost and the workings show.
- 10-24 parts: enough to be convincing, each one meaningful. Group assemblies with `parent`.
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
description of the picture; the last one lands the "so that's why".

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


async def build_model(llm: LLMProvider, brief: str, context: str = "") -> BuildResult:
    """Build a model from a brief; one repair attempt if the first try doesn't validate."""
    system = SYSTEM.format(
        examples="\n".join(json.dumps(e, separators=(",", ":")) for e in EXAMPLES)
    )
    ask = f"Build this:\n{brief}"
    if context:
        ask += f"\n\nWhat the user is looking at (use it so the model is about their exact thing):\n{context[:1500]}"
    result = BuildResult(model=None)
    started = time.monotonic()
    messages = [Message.user(ask)]
    for _ in range(2):
        result.attempts += 1
        response = await llm.complete(
            system=system, messages=messages, tools=[builder_tool()], max_tokens=12000
        )
        result.cost_usd += response.usage.cost_usd
        call = next((c for c in response.tool_calls if c.name == "build_3d_model"), None)
        if call is None:
            result.errors.append("no tool call")
            messages = [Message.user(ask + "\n\nCall build_3d_model now.")]
            continue
        try:
            result.model = ObjectScene.model_validate(dict(call.arguments))
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
