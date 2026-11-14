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
    "limb",
    "blob",
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
    "silicon",
    "pcb",
    "solder",
    "copper",
    "anodized",
]
# Fine detail the renderer draws on a part's surface (a generated texture: nothing to model).
Surface = Literal[
    "pins",
    "traces",
    "cells",
    "fins",
    "brushed",
    "vents",
    "grille",
    "knurl",
    "panel_lines",
]
SURFACES: tuple[str, ...] = Surface.__args__  # type: ignore[attr-defined]
# What a builder writes when it reaches for a material that is not on the list: the nearest
# real one, never a failed model (live: one "copper" cost a whole second build).
MATERIAL_ALIASES = {
    "aluminium": "brushed_metal",
    "aluminum": "brushed_metal",
    "brushed_aluminium": "brushed_metal",
    "brushed_aluminum": "brushed_metal",
    "steel": "metal",
    "stainless_steel": "metal",
    "chrome": "metal",
    "silver": "metal",
    "iron": "dark_metal",
    "titanium": "brushed_metal",
    "brass": "gold",
    "bronze": "copper",
    "black_plastic": "plastic",
    "abs": "plastic",
    "carbon": "rubber",
    "graphite": "rubber",
    "circuit_board": "pcb",
    "fr4": "pcb",
    "die": "silicon",
    "wafer": "silicon",
    "tin": "solder",
    "anodised": "anodized",
    "anodized_aluminium": "anodized",
    "anodized_aluminum": "anodized",
    "leather": "fabric",
    "cloth": "fabric",
    "paint": "painted",
    "porcelain": "ceramic",
    "light": "emissive",
    "glow": "emissive",
}
CameraView = Literal["iso", "front", "back", "side", "left", "top", "below", "close", "wide"]
Style = Literal["studio", "sticker", "blueprint", "xray"]
Ease = Literal["linear", "in", "out", "in_out", "spring"]

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
_ID = re.compile(r"[^a-zA-Z0-9_\-]")
MAX_PARTS, MAX_MOTIONS, MAX_BEATS, MAX_POINTS, MAX_LUMPS = 90, 60, 10, 64, 24
# Shapes with hard edges, and what a living thing gets instead of each.
SOFTER = {"box": "sphere", "rounded_box": "sphere", "cylinder": "capsule"}


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


_MATERIALS: tuple[str, ...] = Material.__args__  # type: ignore[attr-defined]


def _known_look(data: Any) -> Any:
    """A material or surface that is not on the list becomes the nearest one that is (or none),
    so one wrong word never fails a whole model."""
    if not isinstance(data, dict):
        return data
    out = data
    material = data.get("material")
    if isinstance(material, str) and material not in _MATERIALS:
        key = material.strip().lower().replace(" ", "_").replace("-", "_")
        out = {**out, "material": key if key in _MATERIALS else MATERIAL_ALIASES.get(key, "plastic")}
    surface = data.get("surface")
    if surface is not None and surface not in SURFACES:
        key = str(surface).strip().lower().replace(" ", "_").replace("-", "_")
        out = {**out, "surface": key if key in SURFACES else None}
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
    surface: Surface | None = Field(
        None,
        description="Fine detail drawn on the part for free (never model these from boxes): "
        "pins (a pin or solder-ball grid, gold contacts), traces (circuit traces on a board), "
        "cells (a chip die's grid of blocks), fins (heat-sink fins), brushed (brushed metal), "
        "vents (cooling slots), grille (a mesh of holes: speaker, intake), knurl (a grip), "
        "panel_lines (seams and access panels on a casing or hull).",
    )
    density: float | None = Field(
        None,
        ge=0.25,
        le=4.0,
        description="surface: how fine the detail is (1 normal, 2 twice as fine, 0.5 coarser).",
    )
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
        description="tube and limb: [[x,y,z], ...] centerline points (limb: the spine, 3-8 "
        "points). arrow: [from, to] (or a curved run). Relative to position; an arrow's head "
        "is at the last point.",
    )
    radius: float | None = Field(None, gt=0, le=10, description="tube: pipe radius.")
    radii: list[float] | None = Field(
        None,
        description="limb: thickness (radius) at each `path` point, so it swells and tapers: a "
        "leg [0.5,0.35,0.3,0.2], a tail [0.3,0.2,0.1,0.04], a fish body [0.2,0.9,1.0,0.6,0.15]. "
        "Ends are rounded off. Any body, neck, limb, tail, trunk, branch, artery or horn.",
    )
    squash: float | None = Field(
        None,
        ge=0.15,
        le=1.0,
        description="limb: flatten the round cross-section side to side (1 round, 0.4 a fish "
        "body or a leaf, 0.2 a fin).",
    )
    lumps: list[list[float]] | None = Field(
        None,
        description="blob: [[x, y, z, rx, ry, rz], ...] rounded lumps (centre and RADII, half its "
        "width each way: a lump 2 wide has rx 1; relative "
        "to position) melted into ONE smooth skin: a torso is chest + belly + hips, a head is "
        "skull + muzzle + cheeks, a heart is two ventricles + two atria. 2-10 lumps that "
        "overlap. A `path` with `radii` on the same part is melted in too (a neck into the body).",
    )
    blend: float | None = Field(
        None, ge=0.0, le=1.0, description="blob: how far lumps melt together (default 0.5)."
    )
    organic: bool = Field(
        False,
        description="True on every part of a living thing (animal, person, organ, plant): it is "
        "then never drawn hard-edged. Decide this before choosing the shape.",
    )
    mirror: bool = Field(
        False,
        description="Also make this part's twin on the other side (x mirrored): model the left "
        "leg, ear, wing, eye or lung once.",
    )
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
        data = _known_look(data)
        return _clamp_fields(
            data,
            {
                "opacity": (0, 1),
                "density": (0.25, 4),
                "taper": (0, 4),
                "radius": (0.001, 10),
                "teeth": (3, 80),
                "turns": (1, 40),
                "squash": (0.15, 1),
                "blend": (0, 1),
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

    @field_validator("radii", mode="before")
    @classmethod
    def _radii(cls, v: Any) -> list[float] | None:
        if isinstance(v, int | float) and not isinstance(v, bool):
            v = [v]
        if not isinstance(v, list):
            return None
        out = [max(0.005, min(10.0, float(x))) for x in v[:MAX_POINTS] if isinstance(x, int | float) and not isinstance(x, bool)]
        return out or None

    @field_validator("lumps", mode="before")
    @classmethod
    def _lumps(cls, v: Any) -> list[list[float]] | None:
        if not isinstance(v, list):
            return None
        out: list[list[float]] = []
        for item in v[:MAX_LUMPS]:
            if not isinstance(item, list | tuple) or len(item) < 4:
                continue
            centre = _vec(item, 3, -200.0, 200.0)
            # [x, y, z, r] is a ball; [x, y, z, rx, ry, rz] an egg.
            radii = _vec(list(item[3:6]) if len(item) >= 6 else [item[3]] * 3, 3, 0.01, 30.0)
            if centre and radii:
                out.append([*centre, *radii])
        return out or None

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
            if p.shape == "limb" and not p.path:
                notes.append(f"{pid}: limb without a spine became a capsule")
                p = p.model_copy(update={"shape": "capsule"})
            if p.shape == "blob" and not p.lumps and not p.path:
                notes.append(f"{pid}: blob without lumps became a sphere")
                p = p.model_copy(update={"shape": "sphere"})
            if p.organic and p.shape in SOFTER:
                notes.append(f"{pid}: a living part is never a {p.shape}; drawn as a {SOFTER[p.shape]}")
                p = p.model_copy(update={"shape": SOFTER[p.shape]})
            if p.shape in ("limb", "blob"):
                p = _fit_box(p)
            if p.organic and p.shape == "extrude" and p.profile:
                # A fin, an ear, a leaf, a wing: an outline with almost no thickness. Written
                # as thick as it is wide, it is a block of wood stuck on a fish.
                deep = "xyz".index(p.axis)
                span = max(max(pt[i] for pt in p.profile) - min(pt[i] for pt in p.profile) for i in range(2))
                thin = max(0.03, 0.08 * span)
                if p.size[deep] > thin:
                    notes.append(f"{pid}: a flat living part is thin; {p.size[deep]:.2f} thick became {thin:.2f}")
                    p = p.model_copy(update={"size": [thin if i == deep else v for i, v in enumerate(p.size)]})
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
            if p.mirror:
                parts[-1].mirror = False
                twin = _twin(parts[-1], {q.id for q in parts})
                if twin is not None:
                    parts.append(twin)
        if len(self.parts) > MAX_PARTS or len(parts) > MAX_PARTS:
            notes.append(f"trimmed to {MAX_PARTS} parts")
            parts = parts[:MAX_PARTS]
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


def shape_points(p: Part) -> list[tuple[list[float], list[float]]]:
    """Where a limb or blob really is: (centre, radii) of each lump and each spine point,
    relative to the part's position."""
    out: list[tuple[list[float], list[float]]] = []
    if p.shape == "blob":
        out += [(lump[:3], lump[3:6]) for lump in p.lumps or []]
    if p.path:
        radii = p.radii or [p.radius or max(0.02, min(p.size) / 2)]
        last = len(p.path) - 1
        for i, pt in enumerate(p.path):
            # Fewer radii than points: they are spread along the spine.
            at = i / last * (len(radii) - 1) if last else 0.0
            lo = int(at)
            hi = min(len(radii) - 1, lo + 1)
            r = radii[lo] + (radii[hi] - radii[lo]) * (at - lo)
            out.append((pt, [r, r, r]))
    return out


def _fit_box(p: Part) -> Part:
    """A limb or blob is drawn from its spine and lumps, so its `size` is worked out from them:
    everything that reads a part's box (the camera, the checks, the labels) then sees where it
    really is. Unrotated parts are also re-centred on their box."""
    pts = shape_points(p)
    if not pts:
        return p
    lo = [min(c[i] - r[i] for c, r in pts) for i in range(3)]
    hi = [max(c[i] + r[i] for c, r in pts) for i in range(3)]
    size = [max(0.005, hi[i] - lo[i]) for i in range(3)]
    mid = [(lo[i] + hi[i]) / 2 for i in range(3)]
    if any(p.rotation) or all(abs(m) < 1e-6 for m in mid):
        return p.model_copy(update={"size": size})
    shift = lambda v: [v[i] - mid[i] for i in range(3)]  # noqa: E731
    return p.model_copy(
        update={
            "size": size,
            "position": [p.position[i] + mid[i] for i in range(3)],
            "path": [shift(pt) for pt in p.path] if p.path else None,
            "lumps": [[*shift(lump), *lump[3:6]] for lump in p.lumps] if p.lumps else None,
        }
    )


def _twin(p: Part, taken: set[str]) -> Part | None:
    """The same part on the other side: mirrored across x (left and right)."""
    if p.shape in ("asset", "kit", "gear", "spring", "wedge"):
        return None  # a ready-made piece is not mirrored
    if abs(p.position[0]) < 1e-6 and not p.path and not p.lumps:
        return None  # on the middle line it is its own twin
    tid = f"{p.id}_twin"
    if tid in taken:
        return None
    flip = lambda v: [-v[0], *v[1:]]  # noqa: E731
    return p.model_copy(
        update={
            "id": tid,
            "position": flip(p.position),
            "rotation": [p.rotation[0], -p.rotation[1], -p.rotation[2]],
            "path": [flip(pt) for pt in p.path] if p.path else None,
            "lumps": [[*flip(lump[:3]), *lump[3:6]] for lump in p.lumps] if p.lumps else None,
            # An outline drawn across the body (not extruded across it) is drawn the other way round.
            "profile": [[-pt[0], pt[1]] for pt in p.profile] if p.profile and p.shape == "extrude" and p.axis != "x" else p.profile,
            "label": None,
            "note": None,
        }
    )


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
        return _clamp_fields(_known_look(data), {"opacity": (0.05, 1), "density": (0.25, 4)})

    id: str
    position: list[float] | None = None
    rotation: list[float] | None = None
    size: list[float] | None = None
    material: Material | None = None
    color: str | None = None
    surface: Surface | None = None
    density: float | None = Field(None, ge=0.25, le=4.0)
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
