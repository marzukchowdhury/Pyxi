"""Object scenes: real-looking 3D models the model assembles from parts, then animates.

Where a :class:`~pyxi.scenes.spec.SceneSpec` explains *ideas* (boxes and arrows), an
:class:`ObjectScene` explains *things*: a jet ejection seat, an engine, a lock, a heart. The
model describes parts (boxes, cylinders, tubes, gears, springs, extruded and turned profiles...) with
materials, callouts that point at the parts that matter, motions on a timeline, and narrated
beats that play like a short video. Follow-ups ("show the inside", "remove the casing",
"make that part move") are :class:`ObjectEdit` patches applied to the same model.

As with SceneSpec, nothing the model writes is executed: the renderer only draws known
primitives, and slightly wrong input is repaired, not rejected. The geometry is explanatory,
not an engineering drawing.
"""

from __future__ import annotations

import re
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

PartShape = Literal[
    "box",
    "rounded_box",
    "sphere",
    "cylinder",
    "cone",
    "capsule",
    "torus",
    "ring",
    "wedge",
    "tube",
    "extrude",
    "lathe",
    "gear",
    "spring",
    "plane",
    "arrow",
    "marker",
    "kit",
    "asset",
]
Kit = Literal[
    "wheel",
    "missile",
    "propeller",
    "jet_engine",
    "ram_stick",
    "gpu",
    "chip",
    "pc_fan",
    "screen",
    "seat",
    "person",
    "bolt",
    "house",
]
Material = Literal[
    "metal",
    "brushed_metal",
    "dark_metal",
    "gold",
    "plastic",
    "rubber",
    "glass",
    "painted",
    "fabric",
    "wood",
    "ceramic",
    "skin",
    "emissive",
]
CameraView = Literal["iso", "front", "back", "side", "left", "top", "below", "close", "wide"]
Style = Literal["studio", "sticker", "blueprint", "xray"]
Ease = Literal["linear", "in", "out", "in_out", "spring"]

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
_ID = re.compile(r"[^a-zA-Z0-9_\-]")
MAX_PARTS, MAX_MOTIONS, MAX_BEATS, MAX_POINTS = 90, 60, 10, 64


def _vec(v: Any, n: int, lo: float, hi: float) -> list[float] | None:
    if not isinstance(v, list | tuple) or len(v) < n:
        return None
    try:
        return [max(lo, min(hi, float(x))) for x in v[:n]]
    except (TypeError, ValueError):
        return None


def _clamp_fields(data: Any, limits: dict[str, tuple[float, float]]) -> Any:
    """Pull out-of-range numbers into range before validation: a 3-tooth gear or a 700 s key
    should become a 4-tooth gear or a 600 s key, not a failed scene."""
    if not isinstance(data, dict):
        return data
    out = dict(data)
    for key, (lo, hi) in limits.items():
        v = out.get(key)
        if isinstance(v, int | float) and not isinstance(v, bool):
            out[key] = (
                type(v)(max(lo, min(hi, v))) if isinstance(v, float) else int(max(lo, min(hi, v)))
            )
    return out


class Part(BaseModel):
    id: str = Field(description="Short unique id, e.g. 'seat'.")
    shape: PartShape = "box"
    size: list[float] = Field(
        default_factory=lambda: [1.0, 1.0, 1.0],
        description="Bounding box [x, y, z] in meters-ish units; the shape fills it.",
    )
    axis: Literal["x", "y", "z"] = Field(
        "y",
        description="Long axis of cylinder/cone/capsule/spring/gear/torus (cone tip points +axis); "
        "extrude direction for extrude. Pick it instead of rotating.",
    )
    position: list[float] = Field(
        default_factory=lambda: [0.0, 0.0, 0.0], description="[x, y, z], y is up."
    )
    rotation: list[float] = Field(
        default_factory=lambda: [0.0, 0.0, 0.0], description="Degrees [x, y, z]."
    )
    parent: str | None = Field(
        None,
        description="Attach to another part: position/rotation become relative and it moves with it.",
    )
    material: Material = "plastic"
    color: str | None = Field(None, description="Hex like '#c0c4cc'.")
    opacity: float = Field(
        1.0, ge=0.0, le=1.0, description="0 = invisible until a motion fades it in."
    )
    label: str | None = Field(
        None, description="Callout text pointing at this part (only for parts that matter)."
    )
    note: str | None = Field(None, description="Optional second callout line.")
    casing: bool = Field(
        False, description="Outer shell: hidden in cutaway view to show the inside."
    )
    hidden: bool = False
    taper: float | None = Field(
        None, ge=0.0, le=4.0, description="Cylinder top/bottom radius ratio."
    )
    path: list[list[float]] | None = Field(
        None,
        description="tube: [[x,y,z], ...] centerline points. arrow: [from, to] (or a curved "
        "run), relative to position; the head is at the last point.",
    )
    radius: float | None = Field(None, gt=0, le=10, description="tube: pipe radius.")
    profile: list[list[float]] | None = Field(
        None,
        description="extrude: [[x,y], ...] outline in the x-y plane, extruded along z by size[2]. "
        "lathe: [[along, radius], ...] from one end to the other, spun round `axis` (a fuselage, "
        "rocket, bottle, piston, nose cone, vase: anything round in cross-section).",
    )
    teeth: int | None = Field(
        None, ge=3, le=80, description="gear: number of teeth (or fan blades)."
    )
    turns: float | None = Field(None, ge=1, le=40, description="spring: number of coils.")
    # A piece of a real, ready-made model from Pyxi's library (set by Pyxi, never by the model):
    # the library model's id and the part's name in it. Its geometry and textures come from the
    # file; position is its centre, size its extent, and it moves like any other part.
    asset: str | None = None
    node: str | None = None
    kit: Kit | None = Field(
        None,
        description="shape kit: a ready-made detailed piece that fills the size box: wheel (tyre "
        "+ spoked rim; axis = axle), missile, propeller (teeth = blades), jet_engine, ram_stick, "
        "gpu (graphics card), chip (processor / AI chip), pc_fan, screen, seat, person, bolt, "
        "house. Far more real than building these from boxes.",
    )

    @model_validator(mode="before")
    @classmethod
    def _clamp(cls, data: Any) -> Any:
        return _clamp_fields(
            data,
            {
                "opacity": (0, 1),
                "taper": (0, 4),
                "radius": (0.001, 10),
                "teeth": (3, 80),
                "turns": (1, 40),
            },
        )

    @field_validator("color")
    @classmethod
    def _color(cls, v: str | None) -> str | None:
        return v if v is None or _HEX.match(v) else None

    @field_validator("size", mode="before")
    @classmethod
    def _size(cls, v: Any) -> list[float]:
        if isinstance(v, int | float):
            return [float(v)] * 3
        return _vec(v, 3, 0.005, 60.0) or ([*(_vec(v, 1, 0.005, 60.0) or [1.0])] * 3)

    @field_validator("position", mode="before")
    @classmethod
    def _position(cls, v: Any) -> list[float]:
        return _vec(v, 3, -200.0, 200.0) or [0.0, 0.0, 0.0]

    @field_validator("rotation", mode="before")
    @classmethod
    def _rotation(cls, v: Any) -> list[float]:
        return _vec(v, 3, -3600.0, 3600.0) or [0.0, 0.0, 0.0]

    @field_validator("path", mode="before")
    @classmethod
    def _path(cls, v: Any) -> list[list[float]] | None:
        if not isinstance(v, list):
            return None
        pts = [p for p in (_vec(x, 3, -200.0, 200.0) for x in v[:MAX_POINTS]) if p]
        return pts if len(pts) >= 2 else None

    @field_validator("profile", mode="before")
    @classmethod
    def _profile(cls, v: Any) -> list[list[float]] | None:
        if not isinstance(v, list):
            return None
        pts = [p for p in (_vec(x, 2, -200.0, 200.0) for x in v[:MAX_POINTS]) if p]
        return pts if len(pts) >= 3 else None

    @field_validator("label", "note")
    @classmethod
    def _text(cls, v: str | None) -> str | None:
        return " ".join(v.split())[:48] or None if v else None


class Key(BaseModel):
    t: float = Field(ge=0, le=600, description="Seconds on the scene timeline.")
    position: list[float] | None = None
    rotation: list[float] | None = Field(None, description="Degrees.")
    scale: list[float] | None = None
    opacity: float | None = Field(None, ge=0, le=1)

    @model_validator(mode="before")
    @classmethod
    def _clamp(cls, data: Any) -> Any:
        return _clamp_fields(data, {"t": (0, 600), "opacity": (0, 1)})

    @field_validator("position", "rotation", "scale", mode="before")
    @classmethod
    def _v(cls, v: Any) -> list[float] | None:
        return _vec(v, 3, -3600.0, 3600.0) if v is not None else None


class Motion(BaseModel):
    @model_validator(mode="before")
    @classmethod
    def _clamp(cls, data: Any) -> Any:
        return _clamp_fields(
            data,
            {
                "amount": (-100, 100),
                "speed": (-20, 20),
                "period": (0.06, 60),
                "phase": (-1, 1),
                "start": (0, 600),
                "end": (0, 600),
            },
        )

    part: str
    kind: Literal["keyframes", "spin", "oscillate", "pulse"] = Field(
        "keyframes",
        description="keyframes: move/rotate/scale/fade through keys. spin: rotate around axis. "
        "oscillate: back-and-forth (pistons, springs). pulse: glow and breathe (attention, heat, flow).",
    )
    keys: list[Key] = Field(default_factory=list, description="keyframes only.")
    axis: Literal["x", "y", "z"] = "y"
    prop: Literal["position", "rotation", "scale"] = Field(
        "position", description="oscillate only."
    )
    amount: float = Field(
        0.3, ge=-100, le=100, description="oscillate: distance/degrees/scale delta."
    )
    speed: float = Field(0.5, ge=-20, le=20, description="spin: turns per second.")
    period: float = Field(1.2, gt=0.05, le=60, description="oscillate/pulse: seconds per cycle.")
    phase: float = Field(
        0, ge=-1, le=1, description="oscillate/pulse/spin: cycle offset (0..1), to sync parts."
    )
    loop: bool = Field(False, description="keyframes: repeat forever (engines, valves, blinking).")
    start: float = Field(0, ge=0, le=600)
    end: float | None = Field(None, ge=0, le=600, description="Omit to keep going.")
    ease: Ease = "in_out"


class Beat(BaseModel):
    @model_validator(mode="before")
    @classmethod
    def _clamp(cls, data: Any) -> Any:
        data = _clamp_fields(data, {"at": (0, 600)})
        # A beat without a title takes the first words of what it says (not worth a retry).
        if isinstance(data, dict) and not str(data.get("title") or "").strip():
            words = str(data.get("narration") or "").split()[:4]
            data = {**data, "title": " ".join(words).rstrip(".,;:") or "Step"}
        return data

    title: str = Field("", description="A 2-4 word heading for this step ('First stage drops').")
    narration: str = Field(
        description="One or two short spoken sentences in your voice: what is happening and why it matters, "
        "with a concrete fact or vivid analogy, never a description of the picture."
    )
    at: float = Field(0, ge=0, le=600, description="Timeline second this beat starts.")
    focus: list[str] = Field(
        default_factory=list, description="Part ids to spotlight (others dim)."
    )
    camera: CameraView = "iso"
    cutaway: bool | None = Field(None, description="Hide casing parts during this beat.")


class ObjectScene(BaseModel):
    id: str = Field(default_factory=lambda: "obj_" + uuid.uuid4().hex[:8])
    title: str
    subtitle: str | None = None
    style: Style = Field(
        "studio",
        description="studio: realistic materials; sticker: bold outlines, playful; blueprint: technical lines; xray: see-through.",
    )
    parts: list[Part] = Field(min_length=1)
    motions: list[Motion] = Field(default_factory=list)
    beats: list[Beat] = Field(default_factory=list)
    cutaway: bool = False
    auto_play: bool = True
    credit: str | None = Field(None, description="Set by Pyxi: who made the real model it uses.")
    notes: list[str] = Field(
        default_factory=list, description="Repairs made during validation (set by Pyxi)."
    )

    @property
    def duration(self) -> float:
        ends = [k.t for m in self.motions for k in m.keys]
        ends += [m.end for m in self.motions if m.end is not None]
        ends += [b.at for b in self.beats]
        return max([0.0, *ends])

    @model_validator(mode="after")
    def _repair(self) -> ObjectScene:
        notes: list[str] = []
        seen: dict[str, str] = {}
        parts: list[Part] = []
        for i, p in enumerate(self.parts[:MAX_PARTS]):
            pid = _ID.sub("_", p.id) or f"p{i}"
            if pid in seen.values():
                pid = f"{pid}_{i}"
                notes.append(f"renamed duplicate part id {p.id!r}")
            seen[p.id] = pid
            if p.shape == "tube" and not p.path:
                notes.append(f"{pid}: tube without path became a cylinder")
                p = p.model_copy(update={"shape": "cylinder"})
            if p.shape == "arrow" and p.color is None:
                # Flows read best as one glowing colour, set apart from the machine.
                p = p.model_copy(update={"color": "#7fbaff", "material": "emissive"})
            if p.shape == "asset" and not (p.asset and _ID.sub("", p.asset) == p.asset):
                notes.append(f"{pid}: asset without a library model became a box")
                p = p.model_copy(update={"shape": "box", "asset": None})
            if p.shape == "kit" and not p.kit:
                notes.append(f"{pid}: kit without a piece became a box")
                p = p.model_copy(update={"shape": "box"})
            if p.shape == "lathe" and not p.profile:
                notes.append(f"{pid}: lathe without profile became a cylinder")
                p = p.model_copy(update={"shape": "cylinder"})
            if p.shape == "extrude" and not p.profile:
                notes.append(f"{pid}: extrude without profile became a box")
                p = p.model_copy(update={"shape": "box"})
            parts.append(p.model_copy(update={"id": pid}))
        if len(self.parts) > MAX_PARTS:
            notes.append(f"trimmed to {MAX_PARTS} parts")
        ids = {p.id for p in parts}
        for p in parts:
            if p.parent is not None:
                p.parent = seen.get(p.parent, p.parent)
                if p.parent not in ids or p.parent == p.id:
                    notes.append(f"dropped unknown parent of {p.id!r}")
                    p.parent = None
        _break_cycles(parts, notes)
        motions = []
        for m in self.motions[:MAX_MOTIONS]:
            m.part = seen.get(m.part, m.part)
            if m.part not in ids:
                notes.append(f"dropped motion for unknown part {m.part!r}")
                continue
            if m.kind == "keyframes":
                m.keys = sorted(m.keys, key=lambda k: k.t)
                if not m.keys:
                    continue
            motions.append(m)
        beats = []
        for b in sorted(self.beats[:MAX_BEATS], key=lambda b: b.at):
            b.focus = [seen.get(f, f) for f in b.focus if seen.get(f, f) in ids]
            beats.append(b)
        if not beats:
            beats = [Beat(title=self.title, narration=self.subtitle or "", at=0, camera="iso")]
        self.parts, self.motions, self.beats = parts, motions, beats
        self.notes = [*self.notes, *notes]
        return self


def _break_cycles(parts: list[Part], notes: list[str]) -> None:
    by_id = {p.id: p for p in parts}
    for p in parts:
        seen = {p.id}
        cur = p
        while cur.parent:
            if cur.parent in seen:
                notes.append(f"broke a parent loop at {cur.id!r}")
                cur.parent = None
                break
            seen.add(cur.parent)
            cur = by_id[cur.parent]


class PartPatch(BaseModel):
    """Fields to change on an existing part; anything omitted stays as it is."""

    @model_validator(mode="before")
    @classmethod
    def _clamp(cls, data: Any) -> Any:
        return _clamp_fields(data, {"opacity": (0.05, 1)})

    id: str
    position: list[float] | None = None
    rotation: list[float] | None = None
    size: list[float] | None = None
    material: Material | None = None
    color: str | None = None
    opacity: float | None = Field(None, ge=0.05, le=1.0)
    label: str | None = None
    note: str | None = None
    casing: bool | None = None
    hidden: bool | None = None


class ObjectEdit(BaseModel):
    """A follow-up change to the model on screen ("show the inside", "make the fan spin")."""

    cutaway: bool | None = Field(None, description="True hides casing parts to show the inside.")
    hide: list[str] = Field(default_factory=list, description="Part ids to hide.")
    show: list[str] = Field(default_factory=list, description="Part ids to show again.")
    remove: list[str] = Field(default_factory=list)
    update: list[PartPatch] = Field(default_factory=list)
    add_parts: list[Part] = Field(default_factory=list)
    add_motions: list[Motion] = Field(default_factory=list)
    clear_motions: bool = False
    beats: list[Beat] | None = Field(
        None, description="Replace the narrated beats (new explanation)."
    )
    style: Style | None = None
    title: str | None = None


def apply_edit(scene: ObjectScene, edit: ObjectEdit) -> ObjectScene:
    """Return a new scene (same id, so the view keeps its camera) with the edit applied."""
    data = scene.model_dump()
    parts: list[dict[str, Any]] = [p for p in data["parts"] if p["id"] not in set(edit.remove)]
    by_id = {p["id"]: p for p in parts}
    for pid in edit.hide:
        if pid in by_id:
            by_id[pid]["hidden"] = True
    for pid in edit.show:
        if pid in by_id:
            by_id[pid]["hidden"] = False
    for patch in edit.update:
        if patch.id in by_id:
            by_id[patch.id].update(patch.model_dump(exclude_none=True, exclude={"id"}))
    parts += [p.model_dump() for p in edit.add_parts]
    motions = [] if edit.clear_motions else data["motions"]
    motions = [m for m in motions if m["part"] not in set(edit.remove)]
    motions += [m.model_dump() for m in edit.add_motions]
    data.update(
        parts=parts,
        motions=motions,
        beats=[b.model_dump() for b in edit.beats] if edit.beats is not None else data["beats"],
        notes=[],
    )
    if edit.cutaway is not None:
        data["cutaway"] = edit.cutaway
    if edit.style:
        data["style"] = edit.style
    if edit.title:
        data["title"] = edit.title
    return ObjectScene.model_validate(data)


def object_summary(scene: ObjectScene) -> str:
    """What the model needs to edit the scene on screen next turn (ids, not geometry)."""
    labelled = [f"{p.id}({p.label})" if p.label else p.id for p in scene.parts]
    moving = sorted({m.part for m in scene.motions})
    return (
        f"3D model on screen: '{scene.title}' style={scene.style} cutaway={scene.cutaway}. "
        f"Parts: {', '.join(labelled)}. Moving: {', '.join(moving) or 'none'}. "
        f"Casing: {', '.join(p.id for p in scene.parts if p.casing) or 'none'}. "
        "Use edit_3d_model to change it."
    )


def _inline(schema: dict[str, Any]) -> dict[str, Any]:
    defs = schema.pop("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return walk(dict(defs[node["$ref"].split("/")[-1]]))
            # Drop pydantic's "title" labels, but never a field that is called title (a model's
            # or a beat's): those the model has to be able to see and fill in.
            props = node.get("properties")
            node = {
                k: ({pk: walk(pv) for pk, pv in v.items()} if k == "properties" and v is props else walk(v))
                for k, v in node.items()
                if k != "title" or not isinstance(v, str)
            }
            # Optional fields come out as anyOf[T, null]; a flat T is friendlier to every provider.
            any_of = node.get("anyOf")
            if isinstance(any_of, list) and len(any_of) == 2 and {"type": "null"} in any_of:
                rest = next(x for x in any_of if x != {"type": "null"})
                node = {**{k: v for k, v in node.items() if k != "anyOf"}, **rest}
            return node
        if isinstance(node, list):
            return [walk(x) for x in node]
        return node

    return walk(schema)


def _pyxi_only(node: Any) -> Any:
    """Drop what only Pyxi sets (library parts and credits) from a schema the model sees."""
    if isinstance(node, dict):
        props = node.get("properties")
        if isinstance(props, dict):
            for key in ("asset", "node", "credit"):
                props.pop(key, None)
        if isinstance(node.get("enum"), list) and "arrow" in node["enum"]:
            node["enum"] = [v for v in node["enum"] if v != "asset"]
        for v in node.values():
            _pyxi_only(v)
    elif isinstance(node, list):
        for v in node:
            _pyxi_only(v)
    return node


def build_tool_schema() -> dict[str, Any]:
    schema = _inline(ObjectScene.model_json_schema())
    for key in ("id", "notes"):
        schema["properties"].pop(key, None)
    return _pyxi_only(schema)


def edit_tool_schema() -> dict[str, Any]:
    return _pyxi_only(_inline(ObjectEdit.model_json_schema()))
