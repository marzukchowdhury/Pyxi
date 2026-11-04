"""The Scene spec: a small, typed language for 3D explanations.

Why a spec instead of letting the model write Three.js code?

* **Safe.** Nothing the model writes is executed; the overlay only renders known primitives.
* **Reliable.** A schema-validated tool call is far easier for a model to get right than a
  working WebGL program, and invalid parts are repaired or dropped, not crashed on.
* **Beautiful by default.** Layout engines, materials, lighting, animation and narration
  timing live in the renderer, so every scene looks designed even when the model is terse.

A scene is a set of ``nodes`` (things), ``edges`` (relationships or flows) and ``steps``
(a narrated tour: each step focuses some nodes and moves the camera). ``layout`` picks the
arrangement; ``exploded`` scenes are built from real crops of the user's screen.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

log = logging.getLogger(__name__)

Shape = Literal[
    "box",
    "sphere",
    "cylinder",
    "cone",
    "torus",
    "capsule",
    "card",
    "chip",
    "screen",
    "server",
    "user",
    "database",
    "cloud",
]
Layout = Literal["flow", "tree", "orbit", "stack", "grid", "timeline", "exploded", "free"]
EdgeKind = Literal["flow", "arrow", "link", "dashed"]
Camera = Literal["overview", "focus", "top", "side", "front"]
Theme = Literal["aurora", "blueprint", "candy", "mono"]

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
MAX_NODES, MAX_EDGES, MAX_STEPS = 40, 60, 12


class SceneNode(BaseModel):
    id: str = Field(description="Short unique id, e.g. 'browser'.")
    label: str = Field(description="Name shown on the object (max ~24 chars).")
    detail: str | None = Field(None, description="One short line shown under the label.")
    shape: Shape = Field(
        "box",
        description="'card' = flat panel with text; 'chip' = small pill; themed icons: server, user, database, cloud.",
    )
    color: str | None = Field(
        None, description="Hex like '#8b5cf6'. Omit to use the theme palette."
    )
    size: float = Field(1.0, ge=0.3, le=3.0, description="Relative size.")
    parent: str | None = Field(None, description="Parent node id (tree, stack, exploded layouts).")
    group: str | None = Field(
        None, description="Nodes sharing a group are clustered (grid/flow lanes)."
    )
    position: list[float] | None = Field(
        None, description="[x, y, z] in scene units (about -6..6). Only for layout 'free'."
    )
    screen_rect: list[int] | None = Field(
        None, description="[x, y, w, h] of a real on-screen element (exploded views)."
    )
    image: str | None = Field(
        None, description="Crop id served by the backend (set by Wisp, not the model)."
    )
    depth: int | None = Field(None, description="Layer index for stack/exploded layouts.")
    glow: bool = False

    @field_validator("color")
    @classmethod
    def _color(cls, v: str | None) -> str | None:
        return v if v is None or _HEX.match(v) else None

    @field_validator("position")
    @classmethod
    def _pos(cls, v: list[float] | None) -> list[float] | None:
        return [max(-50.0, min(50.0, float(x))) for x in v[:3]] if v and len(v) >= 3 else None

    @field_validator("label")
    @classmethod
    def _label(cls, v: str) -> str:
        return " ".join(v.split())[:40] or "?"


class SceneEdge(BaseModel):
    source: str = Field(alias="from", description="Node id.")
    target: str = Field(alias="to", description="Node id.")
    label: str | None = None
    kind: EdgeKind = Field(
        "arrow", description="'flow' = animated particles travelling along the edge."
    )
    bidirectional: bool = False
    color: str | None = None

    model_config = {"populate_by_name": True}

    @property
    def key(self) -> str:
        return f"{self.source}->{self.target}"


class SceneStep(BaseModel):
    title: str
    narration: str = Field(description="What Wisp says during this step (1-2 short sentences).")
    focus: list[str] = Field(default_factory=list, description="Node ids to highlight; others dim.")
    reveal: list[str] | None = Field(
        None, description="Node ids that appear at this step (progressive disclosure)."
    )
    edges: list[str] | None = Field(None, description="Edge keys 'a->b' to animate in this step.")
    camera: Camera = "focus"


class SceneAnchor(BaseModel):
    element_id: int | None = Field(
        None, description="Screen element the scene explains; the card floats beside it."
    )
    rect: list[int] | None = Field(None, description="[x, y, w, h] in screen px, if no element id.")


class SceneSpec(BaseModel):
    id: str = Field(default_factory=lambda: "scene_" + uuid.uuid4().hex[:8])
    title: str
    subtitle: str | None = None
    layout: Layout = "flow"
    mode: Literal["card", "hologram"] = Field(
        "card", description="'card' floats beside the anchor; 'hologram' fills the screen."
    )
    theme: Theme = "aurora"
    anchor: SceneAnchor | None = None
    nodes: list[SceneNode] = Field(min_length=1)
    edges: list[SceneEdge] = Field(default_factory=list)
    steps: list[SceneStep] = Field(default_factory=list)
    auto_play: bool = True
    notes: list[str] = Field(
        default_factory=list, description="Repairs made during validation (set by Wisp)."
    )

    @model_validator(mode="after")
    def _repair(self) -> SceneSpec:
        """Make a slightly-wrong scene renderable instead of rejecting it outright."""
        notes: list[str] = []
        seen: set[str] = set()
        nodes = []
        for i, n in enumerate(self.nodes[:MAX_NODES]):
            nid = re.sub(r"[^a-zA-Z0-9_\-]", "_", n.id) or f"n{i}"
            if nid in seen:
                nid = f"{nid}_{i}"
                notes.append(f"renamed duplicate node id {n.id!r}")
            seen.add(nid)
            nodes.append(n.model_copy(update={"id": nid}))
        if len(self.nodes) > MAX_NODES:
            notes.append(f"trimmed to {MAX_NODES} nodes")
        ids = {n.id for n in nodes}
        for n in nodes:
            if n.parent and n.parent not in ids:
                notes.append(f"dropped unknown parent {n.parent!r} of {n.id!r}")
                n.parent = None
            if n.parent == n.id:
                n.parent = None
        edges = [
            e
            for e in self.edges[:MAX_EDGES]
            if e.source in ids and e.target in ids and e.source != e.target
        ]
        if len(edges) != len(self.edges):
            notes.append(f"dropped {len(self.edges) - len(edges)} invalid edge(s)")
        keys = {e.key for e in edges} | {f"{e.target}->{e.source}" for e in edges}
        steps = []
        for s in self.steps[:MAX_STEPS]:
            s.focus = [f for f in s.focus if f in ids]
            s.reveal = [r for r in s.reveal if r in ids] if s.reveal else None
            s.edges = [k for k in s.edges if k in keys] if s.edges else None
            steps.append(s)
        if not steps:
            steps = [
                SceneStep(
                    title=self.title, narration=self.subtitle or "", focus=[], camera="overview"
                )
            ]
        self.nodes, self.edges, self.steps = nodes, edges, steps
        self.notes = [*self.notes, *notes]
        return self


def tool_schema() -> dict[str, Any]:
    """JSON schema for the explain_in_3d tool, with $refs inlined (friendlier to every provider)."""
    schema = SceneSpec.model_json_schema(by_alias=True)
    defs = schema.pop("$defs", {})

    def inline(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return inline(defs[node["$ref"].split("/")[-1]])
            # Drop pydantic's auto-generated "title" strings; keep a *property* named "title".
            return {
                k: inline(v) for k, v in node.items() if not (k == "title" and isinstance(v, str))
            }
        if isinstance(node, list):
            return [inline(x) for x in node]
        return node

    schema = inline(schema)
    props = schema["properties"]
    for internal in ("id", "notes"):
        props.pop(internal, None)
    for node_prop in ("image", "screen_rect"):
        props["nodes"]["items"]["properties"].pop(node_prop, None)
    schema["required"] = ["title", "layout", "nodes", "steps"]
    return schema


def parse_scene(data: dict[str, Any]) -> SceneSpec:
    """Validate model output into a SceneSpec (raises pydantic.ValidationError if hopeless)."""
    data = dict(data)
    data.pop("id", None)
    return SceneSpec.model_validate(data)
