"""Sketchboards: a diagram Blip draws by hand while it talks.

"Can a jet really hover?" gets a side view of the jet, then an arrow of air pushed down, then
lift pushing up, then "lift = weight", each stroke inking itself in as Blip says that part.
The board is a small spec the model writes in one tool call: shapes on a 100 x 64 grid, each
revealed at a step, plus one short line of narration per step.

What makes it Blip's own:
* a thread ties the board to the real thing on screen it's about (the video, the photo);
* flows move: arrows marked ``flow`` carry travelling dashes (air, current, data, blood);
* every label can be clicked to ask about it, and the board can become a 3D model;
* it settles where it hides nothing and leaves on its own once it's done.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

GRID_W, GRID_H = 100.0, 64.0
MAX_SHAPES = 48
MAX_STEPS = 6

Kind = Literal[
    "line", "polyline", "polygon", "curve", "arrow", "rect", "ellipse", "text", "equation"
]
Ink = Literal["ink", "ice", "amber", "rose", "mint", "violet", "muted"]
Weight = Literal["thin", "normal", "bold"]

INKS: tuple[str, ...] = ("ink", "ice", "amber", "rose", "mint", "violet", "muted")
_POINTS_NEEDED = {"line": 2, "arrow": 2, "polyline": 2, "curve": 3, "polygon": 3}


def _xy(p: Any) -> list[float] | None:
    try:
        x, y = float(p[0]), float(p[1])
    except (TypeError, ValueError, IndexError):
        return None
    return [round(min(GRID_W, max(0.0, x)), 2), round(min(GRID_H, max(0.0, y)), 2)]


class Shape(BaseModel):
    kind: Kind
    points: list[list[float]] = Field(default_factory=list)
    at: list[float] | None = None  # rect/ellipse top-left, text/equation anchor (left baseline)
    size: list[float] | None = None  # rect/ellipse [w, h]
    text: str | None = None
    color: Ink = "ink"
    fill: bool = False
    weight: Weight = "normal"
    dashed: bool = False
    flow: bool = False  # travelling dashes along the stroke
    step: int = 0
    ask: str | None = None  # what clicking this label asks (defaults to its text)

    @field_validator("color", mode="before")
    @classmethod
    def _ink(cls, v: Any) -> str:
        return v if v in INKS else "ink"

    @field_validator("points", mode="before")
    @classmethod
    def _points(cls, v: Any) -> list[list[float]]:
        if not isinstance(v, list):
            return []
        return [p for p in (_xy(p) for p in v[:40]) if p]

    @field_validator("at", mode="before")
    @classmethod
    def _at(cls, v: Any) -> list[float] | None:
        return _xy(v) if v is not None else None

    @field_validator("size", mode="before")
    @classmethod
    def _size(cls, v: Any) -> list[float] | None:
        try:
            return [min(GRID_W, max(0.5, float(v[0]))), min(GRID_H, max(0.5, float(v[1])))]
        except (TypeError, ValueError, IndexError):
            return None

    @field_validator("text", "ask", mode="before")
    @classmethod
    def _text(cls, v: Any) -> str | None:
        return str(v).strip()[:60] or None if v is not None else None


class Step(BaseModel):
    narration: str = Field(description="One short sentence Blip says while this step draws.")
    title: str | None = None

    @field_validator("narration", mode="before")
    @classmethod
    def _narr(cls, v: Any) -> str:
        return str(v or "").strip()[:220]


class SketchAnchor(BaseModel):
    element_id: int | None = None
    quote: str | None = None
    rect: list[int] | None = None  # filled in by Blip: what the thread points at, screen px


class Sketchboard(BaseModel):
    id: str = Field(default_factory=lambda: "sketch_" + uuid.uuid4().hex[:8])
    title: str
    subtitle: str | None = None
    shapes: list[Shape] = Field(min_length=1)
    steps: list[Step] = Field(min_length=1)
    anchor: SketchAnchor | None = None
    notes: list[str] = Field(default_factory=list)

    @field_validator("title", "subtitle", mode="before")
    @classmethod
    def _short(cls, v: Any) -> str | None:
        return str(v).strip()[:48] if v else None

    @model_validator(mode="before")
    @classmethod
    def _drop_broken(cls, data: Any) -> Any:
        """One malformed shape shouldn't sink the whole board: drop it and say so."""
        if not isinstance(data, dict) or not isinstance(data.get("shapes"), list):
            return data
        kept, dropped = [], 0
        for raw in data["shapes"][: MAX_SHAPES + 10]:
            try:
                kept.append(Shape.model_validate(raw))
            except Exception:
                dropped += 1
        data = dict(data)
        data["shapes"] = kept
        if dropped:
            data["notes"] = [*data.get("notes", []), f"dropped {dropped} malformed shapes"]
        return data

    @model_validator(mode="after")
    def _repair(self) -> Sketchboard:
        notes: list[str] = []
        self.steps = [s for s in self.steps if s.narration][:MAX_STEPS] or [
            Step(narration=self.subtitle or self.title)
        ]
        last = len(self.steps) - 1
        shapes: list[Shape] = []
        for s in self.shapes:
            need = _POINTS_NEEDED.get(s.kind)
            if need and len(s.points) < need:
                notes.append(f"dropped a {s.kind} with {len(s.points)} points")
                continue
            if s.kind in ("rect", "ellipse") and (s.at is None or s.size is None):
                if len(s.points) >= 2:  # accept two corners instead
                    (x0, y0), (x1, y1) = s.points[0], s.points[1]
                    s.at = [min(x0, x1), min(y0, y1)]
                    s.size = [max(0.5, abs(x1 - x0)), max(0.5, abs(y1 - y0))]
                else:
                    notes.append(f"dropped a {s.kind} with no position")
                    continue
            if s.kind in ("text", "equation"):
                if not s.text:
                    continue
                if s.at is None:
                    if not s.points:
                        notes.append(f"dropped text {s.text!r} with no position")
                        continue
                    s.at = s.points[0]
            s.step = min(max(0, int(s.step)), last)
            shapes.append(s)
        if len(shapes) > MAX_SHAPES:
            notes.append(f"trimmed to {MAX_SHAPES} shapes")
            shapes = shapes[:MAX_SHAPES]
        if not shapes:
            raise ValueError("no drawable shapes: give points/at/size on the 100x64 grid")
        self.shapes = shapes
        self.notes = [*self.notes, *notes]
        return self


NARRATION = (
    "One spoken sentence, under 20 words, in your voice: a concrete fact, a vivid analogy or a "
    "punchline, never a description of the picture ('Here is', 'This shows'). The last one "
    "lands the 'so that's why' moment, adds a related fun fact, and offers one thing to show next."
)


def steps_from_speech(sentences: list[str], shapes: list[Any], title: str) -> list[dict[str, str]]:
    """Steps for a board the model sent without any: its spoken reply, one sentence per step,
    so the drawing still builds in time with the words (extra sentences join the last step)."""
    n = 1
    for s in shapes:
        try:
            n = max(n, int(s.get("step", 0)) + 1)
        except (AttributeError, TypeError, ValueError):
            continue
    n = min(n, MAX_STEPS)
    lines = [s.strip() for s in sentences if s.strip()]
    if not lines:
        return [{"narration": title}]
    if len(lines) <= n:
        return [{"narration": line} for line in lines]
    return [{"narration": line} for line in lines[: n - 1]] + [
        {"narration": " ".join(lines[n - 1 :])}
    ]


_POINT = {"type": "array", "items": {"type": "number"}, "description": "[x, y]"}

SKETCH_TOOL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {
            "type": "string",
            "description": "The idea in 2-5 words, e.g. 'Push air down, lift up'.",
        },
        "subtitle": {"type": "string", "description": "Optional, e.g. 'Side view, nose left'."},
        "anchor": {
            "type": "object",
            "description": "What on screen this is about (a video, photo, chart). A thread joins them.",
            "properties": {"element_id": {"type": "integer"}, "quote": {"type": "string"}},
        },
        "steps": {
            "type": "array",
            "description": "2-5 steps. Each draws its shapes while Blip says the narration.",
            "items": {
                "type": "object",
                "properties": {
                    "narration": {"type": "string", "description": NARRATION},
                    "title": {"type": "string"},
                },
                "required": ["narration"],
            },
        },
        "shapes": {
            "type": "array",
            "description": "On a 100 wide x 64 tall grid, y down. Leave ~6 units of margin.",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": list(Kind.__args__),  # type: ignore[attr-defined]
                    },
                    "points": {"type": "array", "items": _POINT},
                    "at": {**_POINT, "description": "rect/ellipse top-left; text/equation start"},
                    "size": {"type": "array", "items": {"type": "number"}, "description": "[w, h]"},
                    "text": {"type": "string", "description": "text/equation content, short"},
                    "color": {"type": "string", "enum": list(INKS)},
                    "fill": {"type": "boolean"},
                    "weight": {"type": "string", "enum": ["thin", "normal", "bold"]},
                    "dashed": {"type": "boolean"},
                    "flow": {"type": "boolean", "description": "animate movement along it"},
                    "step": {"type": "integer", "description": "0-based step that draws it"},
                },
                "required": ["kind", "step"],
            },
        },
    },
    "required": ["title", "steps", "shapes"],
}
