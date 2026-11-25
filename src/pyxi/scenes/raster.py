"""A plain picture of a model, worked out here from its numbers.

The overlay draws the real thing (three.js, lights, materials). Before a model is shown, the
brain sometimes has to see it too: to hold it against the photo it was built from, from the
photo's own angle. Asking the overlay for a frame would put an unchecked model on screen first,
so this draws one itself: every part's surface as a cloud of points, turned and placed the way
the renderer does it (desktop/src/renderer/src/scene/parts.ts), dropped onto a grid nearest
point first, with flat shading. It is a silhouette and the colours, nothing more: no textures,
no shine, kit pieces as plain blocks. Good for an outline, a colour and a count of parts, and
never shown to the user.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from pyxi.scenes.objects import ObjectScene, Part, loft_rings

# The colour each material has when the part gives none (parts.ts MATERIALS).
MATERIAL_COLORS = {
    "metal": "#c9ced6",
    "brushed_metal": "#b4b9c1",
    "dark_metal": "#474c56",
    "gold": "#e0b25a",
    "plastic": "#dde1e7",
    "rubber": "#2a2d33",
    "glass": "#d3e8ff",
    "painted": "#9aa5b2",
    "fabric": "#8b8f7a",
    "wood": "#a4743f",
    "ceramic": "#f2f0ea",
    "skin": "#e2b08e",
    "emissive": "#ffb347",
    "silicon": "#2b303c",
    "pcb": "#1f5a36",
    "solder": "#b9bcc0",
    "copper": "#c47a4a",
    "anodized": "#3a3d44",
}
_NOT_DRAWN = {"arrow", "marker", "plane"}  # flows, tags and floors are not the thing itself
_MAX_POINTS = 400_000  # for any one part


def hex_rgb(color: str | None, fallback: str = "#dde1e7") -> tuple[int, int, int]:
    c = (color or fallback).lstrip("#")
    try:
        return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    except (ValueError, IndexError):
        return 221, 225, 231


def part_rgb(p: Part) -> tuple[int, int, int]:
    return hex_rgb(p.color, MATERIAL_COLORS.get(p.material, "#dde1e7"))


# ------------------------------------------------------------------ turning and placing
def _rotation(deg: list[float]) -> np.ndarray:
    """Degrees [x, y, z] as the renderer turns a part (three.js Euler XYZ)."""
    (cx, sx), (cy, sy), (cz, sz) = ((math.cos(math.radians(d)), math.sin(math.radians(d))) for d in deg)
    return np.array(
        [
            [cy * cz, -cy * sz, sy],
            [cx * sz + sx * sy * cz, cx * cz - sx * sy * sz, -sx * cy],
            [sx * sz - cx * sy * cz, sx * cz + cx * sy * sz, cx * cy],
        ]
    )


def placements(scene: ObjectScene) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """Each part's turn and position in the model's space, through its parents."""
    by_id = {p.id: p for p in scene.parts}
    out: dict[str, tuple[np.ndarray, np.ndarray]] = {}

    def of(p: Part, depth: int = 0) -> tuple[np.ndarray, np.ndarray]:
        if p.id in out:
            return out[p.id]
        turn, at = _rotation(p.rotation), np.array(p.position, dtype=float)
        parent = by_id.get(p.parent or "")
        if parent is not None and depth < 32:
            pt, pa = of(parent, depth + 1)
            turn, at = pt @ turn, pt @ at + pa
        out[p.id] = (turn, at)
        return out[p.id]

    for p in scene.parts:
        of(p)
    return out


def _along(points: np.ndarray, axis: str) -> np.ndarray:
    """Something built with its long axis up (+y), laid along ``axis`` (parts.ts alongAxis)."""
    x, y, z = points[:, 0], points[:, 1], points[:, 2]
    if axis == "x":
        return np.stack([y, -x, z], axis=1)
    if axis == "z":
        return np.stack([x, -z, y], axis=1)
    return points


def _from_z(points: np.ndarray, axis: str) -> np.ndarray:
    """Something extruded along +z, laid along ``axis`` (parts.ts fromZ)."""
    x, y, z = points[:, 0], points[:, 1], points[:, 2]
    if axis == "y":
        return np.stack([x, -z, y], axis=1)
    if axis == "x":
        return np.stack([-z, y, x], axis=1)
    return points


def _unit(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v, axis=1, keepdims=True)
    return v / np.maximum(n, 1e-9)


def _steps(length: float, spacing: float, least: int = 2, most: int = 1200) -> int:
    return int(max(least, min(most, math.ceil(length / max(spacing, 1e-6)))))


# ------------------------------------------------------------------ surfaces, as points
Cloud = tuple[np.ndarray, np.ndarray]  # points (n, 3) and their normals (n, 3)
_EMPTY: Cloud = (np.zeros((0, 3)), np.zeros((0, 3)))


def _join(pieces: list[Cloud]) -> Cloud:
    pieces = [c for c in pieces if len(c[0])]
    if not pieces:
        return _EMPTY
    return np.concatenate([c[0] for c in pieces]), np.concatenate([c[1] for c in pieces])


def _revolve(profile: list[tuple[float, float]], spacing: float) -> Cloud:
    """A [(radius, along), ...] outline turned round the y axis."""
    pts, nrm = [], []
    for (r0, a0), (r1, a1) in zip(profile, profile[1:]):
        length = math.hypot(r1 - r0, a1 - a0)
        if length < 1e-9:
            continue
        n_r, n_a = (a1 - a0) / length, -(r1 - r0) / length
        for f in (np.arange(_steps(length, spacing, 1)) + 0.5) / _steps(length, spacing, 1):
            r, a = r0 + (r1 - r0) * f, a0 + (a1 - a0) * f
            k = _steps(2 * math.pi * abs(r), spacing, 6, 1500)
            ang = (np.arange(k) + 0.5 * (len(pts) % 2)) * (2 * math.pi / k)
            c, s = np.cos(ang), np.sin(ang)
            pts.append(np.stack([r * c, np.full(k, a), r * s], axis=1))
            nrm.append(np.stack([n_r * c, np.full(k, n_a), n_r * s], axis=1))
    return (np.concatenate(pts), np.concatenate(nrm)) if pts else _EMPTY


def _box(size: list[float], spacing: float) -> Cloud:
    out: list[Cloud] = []
    for axis in range(3):
        u, v = [i for i in range(3) if i != axis]
        nu, nv = _steps(size[u], spacing), _steps(size[v], spacing)
        gu, gv = np.meshgrid((np.arange(nu) + 0.5) / nu - 0.5, (np.arange(nv) + 0.5) / nv - 0.5)
        for side in (-0.5, 0.5):
            p = np.zeros((nu * nv, 3))
            p[:, u], p[:, v], p[:, axis] = gu.ravel() * size[u], gv.ravel() * size[v], side * size[axis]
            n = np.zeros((nu * nv, 3))
            n[:, axis] = 2 * side
            out.append((p, n))
    return _join(out)


def _inside(poly: np.ndarray, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Which points lie inside an outline (even-odd)."""
    inside = np.zeros(x.shape, dtype=bool)
    for (x0, y0), (x1, y1) in zip(poly, np.roll(poly, -1, axis=0)):
        if y0 == y1:
            continue
        crosses = (y0 > y) != (y1 > y)
        at = x0 + (y - y0) * (x1 - x0) / (y1 - y0)
        inside ^= crosses & (x < at)
    return inside


def _prism(outline: list[list[float]], depth: float, spacing: float) -> Cloud:
    """An outline in x-y, ``depth`` thick along z, centred on z."""
    poly = np.array(outline, dtype=float)
    lo, hi = poly.min(axis=0), poly.max(axis=0)
    nx, ny = _steps(hi[0] - lo[0], spacing), _steps(hi[1] - lo[1], spacing)
    gx, gy = np.meshgrid(lo[0] + (np.arange(nx) + 0.5) * (hi[0] - lo[0]) / nx, lo[1] + (np.arange(ny) + 0.5) * (hi[1] - lo[1]) / ny)
    keep = _inside(poly, gx.ravel(), gy.ravel())
    fx, fy = gx.ravel()[keep], gy.ravel()[keep]
    out: list[Cloud] = []
    for side in (-0.5, 0.5):
        p = np.stack([fx, fy, np.full(fx.shape, side * depth)], axis=1)
        n = np.zeros_like(p)
        n[:, 2] = 2 * side
        out.append((p, n))
    nz = _steps(depth, spacing)
    zs = ((np.arange(nz) + 0.5) / nz - 0.5) * depth
    # Outward is to the right of an edge when the outline runs anticlockwise.
    area = 0.5 * float(np.sum(poly[:, 0] * np.roll(poly[:, 1], -1) - np.roll(poly[:, 0], -1) * poly[:, 1]))
    turn = 1.0 if area >= 0 else -1.0
    for (x0, y0), (x1, y1) in zip(poly, np.roll(poly, -1, axis=0)):
        length = math.hypot(x1 - x0, y1 - y0)
        if length < 1e-9:
            continue
        k = _steps(length, spacing, 1)
        f = (np.arange(k) + 0.5) / k
        ex, ey = np.meshgrid(x0 + (x1 - x0) * f, zs)
        why = np.meshgrid(y0 + (y1 - y0) * f, zs)[0]
        p = np.stack([ex.ravel(), why.ravel(), ey.ravel()], axis=1)
        n = np.tile([turn * (y1 - y0) / length, -turn * (x1 - x0) / length, 0.0], (len(p), 1))
        out.append((p, n))
    return _join(out)


def _curve(path: list[list[float]], spacing: float) -> np.ndarray:
    """A smooth run through the points (Catmull-Rom), about ``spacing`` between samples."""
    pts = np.array(path, dtype=float)
    if len(pts) < 3:
        length = float(np.linalg.norm(pts[-1] - pts[0]))
        f = np.linspace(0, 1, _steps(length, spacing, 2, 600) + 1)[:, None]
        return pts[0] + (pts[-1] - pts[0]) * f
    out = []
    for i in range(len(pts) - 1):
        p0, p1, p2, p3 = pts[max(0, i - 1)], pts[i], pts[i + 1], pts[min(len(pts) - 1, i + 2)]
        k = _steps(float(np.linalg.norm(p2 - p1)) * 1.2, spacing, 4, 400)
        f = (np.arange(k) / k)[:, None]
        out.append(0.5 * (2 * p1 + (p2 - p0) * f + (2 * p0 - 5 * p1 + 4 * p2 - p3) * f**2 + (3 * p1 - p0 - 3 * p2 + p3) * f**3))
    out.append(pts[-1][None, :])
    return np.concatenate(out)


def _radius_at(radii: list[float], u: np.ndarray) -> np.ndarray:
    if len(radii) == 1:
        return np.full(u.shape, radii[0])
    at = np.clip(u, 0, 1) * (len(radii) - 1)
    return np.interp(at, np.arange(len(radii)), radii)


def _sweep(path: list[list[float]], radii: list[float], spacing: float, squash: float = 1.0, round_ends: bool = False) -> Cloud:
    """A round body along a path, ``radii`` thick along it (a tube, a limb)."""
    centre = _curve(path, spacing)
    if len(centre) < 2:
        return _EMPTY
    tangent = _unit(np.gradient(centre, axis=0))
    u = np.linspace(0, 1, len(centre))
    r = _radius_at(radii, u)
    # "Side to side" is the world's x wherever the path allows it (organic.ts), carried along.
    side = np.array([1.0, 0.0, 0.0]) - tangent[0] * tangent[0][0]
    if float(side @ side) < 0.01:
        side = np.array([0.0, 0.0, 1.0]) - tangent[0] * tangent[0][2]
    side /= np.linalg.norm(side)
    pts, nrm = [], []
    for i, (c, t) in enumerate(zip(centre, tangent)):
        side = side - t * float(side @ t)
        side /= max(float(np.linalg.norm(side)), 1e-9)
        other = np.cross(t, side)
        k = _steps(2 * math.pi * r[i], spacing, 6, 800)
        ang = np.arange(k) * (2 * math.pi / k)
        ring = np.outer(np.cos(ang) * squash, side) + np.outer(np.sin(ang), other)
        pts.append(c + ring * r[i])
        nrm.append(np.outer(np.cos(ang), side) + np.outer(np.sin(ang), other))
    out: list[Cloud] = [(np.concatenate(pts), np.concatenate(nrm))]
    for end, sign in ((0, -1.0), (len(centre) - 1, 1.0)):
        ball = _revolve([(0.0, 0.0)] + [(r[end] * math.cos(a), r[end] * math.sin(a) * (1.0 if round_ends else 0.0)) for a in np.linspace(math.pi / 2, 0, 6)], spacing)
        # A half ball (or a flat disc) on the end, facing along the path.
        t = tangent[end] * sign
        side = side - t * float(side @ t)
        side = side / max(float(np.linalg.norm(side)), 1e-9) if float(side @ side) > 1e-9 else np.cross(t, [0.0, 1.0, 0.0])
        frame = np.stack([side * squash, t, np.cross(t, side)], axis=1)
        out.append((centre[end] + ball[0] @ frame.T, np.tile(t, (len(ball[0]), 1))))
    return _join(out)


def _ellipsoid(centre: list[float], radii: list[float], spacing: float) -> Cloud:
    small = max(min(radii), 1e-6)
    unit = _revolve([(math.cos(a), math.sin(a)) for a in np.linspace(-math.pi / 2, math.pi / 2, 25)], spacing / max(radii))
    scale = np.array(radii, dtype=float)
    return unit[0] * scale + np.array(centre, dtype=float), _unit(unit[1] / (scale / small))


def _loft(sections: list[list[float]], axis: str, spacing: float) -> Cloud:
    rings = np.array(loft_rings(sections), dtype=float)
    if len(rings) < 2:
        return _EMPTY

    def outline(row: np.ndarray, k: int, shrink: float = 1.0) -> tuple[np.ndarray, np.ndarray]:
        ang = (np.arange(k) + 0.5) * (2 * math.pi / k)
        e = float(np.clip(row[4], 0.15, 1.0))
        c, s = np.cos(ang), np.sin(ang)
        return shrink * row[1] / 2 * np.sign(c) * np.abs(c) ** e, shrink * row[2] / 2 * np.sign(s) * np.abs(s) ** e + row[3]

    def place(along: np.ndarray, across: np.ndarray, up: np.ndarray) -> np.ndarray:
        if axis == "x":
            return np.stack([along, up, across], axis=1)
        if axis == "z":
            return np.stack([across, up, along], axis=1)
        return np.stack([across, along, up], axis=1)

    pts, nrm = [], []
    for a, b in zip(rings, rings[1:]):
        for f in np.arange(_steps(b[0] - a[0], spacing, 1)) / _steps(b[0] - a[0], spacing, 1):
            row = a + (b - a) * f
            k = _steps(math.pi * (row[1] + row[2]), spacing, 8, 1500)
            x, y = outline(row, k)
            # Outward: a quarter turn from the way the outline runs.
            tx, ty = np.roll(x, -1) - np.roll(x, 1), np.roll(y, -1) - np.roll(y, 1)
            pts.append(place(np.full(k, row[0]), x, y))
            nrm.append(place(np.zeros(k), ty, -tx))
    for row, sign in ((rings[0], -1.0), (rings[-1], 1.0)):
        for shrink in np.linspace(0.02, 0.98, _steps(max(row[1], row[2]) / 2, spacing, 2)):
            k = _steps(math.pi * (row[1] + row[2]) * shrink, spacing, 6, 1500)
            x, y = outline(np.array([row[0], row[1], row[2], 0.0, row[4]]), k, shrink)
            pts.append(place(np.full(k, row[0]), x, y + row[3]))
            nrm.append(place(np.full(k, sign), np.zeros(k), np.zeros(k)))
    return np.concatenate(pts), _unit(np.concatenate(nrm))


def hollow_profile(outside: list[tuple[float, float]], wall: float) -> list[tuple[float, float]]:
    """The outline a hollow vessel is turned from (the same sum as hollowProfile in loft.ts)."""
    pts = [(max(0.0, r), a) for r, a in outside]
    if len(pts) < 2:
        return pts
    rim = pts[-1]
    length = abs(rim[1] - pts[0][1])
    t = min(wall, max(r for r, _ in pts) * 0.45, length * 0.45)
    way = 1.0 if rim[1] >= pts[0][1] else -1.0
    floor = pts[0][1] + way * t
    inside: list[tuple[float, float]] = []
    for r, a in reversed(pts):
        if (a - floor) * way <= 0:
            break
        inside.append((max(0.001, r - t), a))
    last = inside[-1] if inside else (max(0.001, rim[0] - t), rim[1])
    return [*pts, *inside, (last[0], floor), (0.0, floor)]


def part_cloud(p: Part, spacing: float) -> Cloud:
    """The part's surface as points, in its own space (before it is turned and placed)."""
    size = [max(1e-4, s) for s in p.size]
    a = "xyz".index(p.axis)
    others = [size[i] for i in range(3) if i != a]
    across = min(others)
    scale = np.array(size)
    fine = spacing / max(size)  # for shapes built one unit big, then stretched to the box
    shape = p.shape
    if shape == "kit":
        shape = "cylinder" if p.kit in ("wheel", "pc_fan", "bolt", "jet_engine", "missile") else "capsule" if p.kit == "person" else "box"
    if shape == "sphere":
        pts, nrm = _ellipsoid([0, 0, 0], [s / 2 for s in size], spacing)
        return pts, nrm
    if shape in ("cylinder", "gear", "spring"):
        t = p.taper if (p.taper is not None and shape == "cylinder") else 1.0
        top, bottom = 0.5 * min(1.0, t), 0.5 * min(1.0, 1 / t if t > 0 else 1.0)
        pts, nrm = _revolve([(0, -0.5), (bottom, -0.5), (top, 0.5), (0, 0.5)], fine)
        pts, nrm = _along(pts, p.axis), _along(nrm, p.axis)
        return pts * scale, _unit(nrm / scale)
    if shape == "cone":
        pts, nrm = _revolve([(0, -0.5), (0.5, -0.5), (0, 0.5)], fine)
        pts, nrm = _along(pts, p.axis), _along(nrm, p.axis)
        return pts * scale, _unit(nrm / scale)
    if shape == "capsule":
        r = across / 2
        half = max(0.0005, size[a] / 2 - r)
        arc = [(r * math.cos(t), r * math.sin(t)) for t in np.linspace(-math.pi / 2, 0, 9)]
        prof = [(x, y - half) for x, y in arc] + [(x, -y + half) for x, y in reversed(arc)]
        pts, nrm = _revolve(prof, spacing)
        return _along(pts, p.axis), _along(nrm, p.axis)
    if shape == "torus":
        thick = size[a]
        big = max(0.001, (across - thick) / 2)
        ring = [(big + thick / 2 * math.cos(t), thick / 2 * math.sin(t)) for t in np.linspace(-math.pi, math.pi, 25)]
        pts, nrm = _revolve(ring, spacing)
        return _along(pts, p.axis), _along(nrm, p.axis)
    if shape == "ring":
        h, r = size[a], across / 2
        pts, nrm = _revolve([(r * 0.62, -h / 2), (r, -h / 2), (r, h / 2), (r * 0.62, h / 2), (r * 0.62, -h / 2)], spacing)
        return _along(pts, p.axis), _along(nrm, p.axis)
    if shape == "wedge":
        pts, nrm = _prism([[-0.5, -0.5], [0.5, -0.5], [-0.5, 0.5]], 1.0, fine)
        return pts * scale, _unit(nrm / scale)
    if shape == "tube" and p.path:
        return _sweep(p.path, [p.radius or max(0.01, across / 2)], spacing)
    if shape == "limb":
        half = size[a] / 2
        path = p.path or [[half * (-1 if i == 0 else 1) * (j == a) for j in range(3)] for i in range(2)]
        radii = p.radii or [p.radius or max(0.01, across / 2)]
        return _sweep(path, radii, spacing, p.squash or 1.0, round_ends=True)
    if shape == "blob":
        lumps = p.lumps or ([] if p.path else [[0, 0, 0, size[0] / 2, size[1] / 2, size[2] / 2]])
        pieces = [_ellipsoid(lump[:3], lump[3:6], spacing) for lump in lumps]
        if p.path:
            pieces.append(_sweep(p.path, p.radii or [p.radius or 0.2], spacing, round_ends=True))
        return _join(pieces)
    if shape == "extrude" and p.profile:
        pts, nrm = _prism(p.profile, size[a], spacing)
        return _from_z(pts, p.axis), _from_z(nrm, p.axis)
    if shape == "lathe" and p.profile:
        prof = [(max(0.0, r), along) for along, r in p.profile]
        if p.wall:
            if prof[0][1] > prof[-1][1]:
                prof.reverse()
            prof = hollow_profile(prof, p.wall)
        if prof[0][0] > 0:
            prof.insert(0, (0.0, prof[0][1]))
        if prof[-1][0] > 0:
            prof.append((0.0, prof[-1][1]))
        pts, nrm = _revolve(prof, spacing)
        return _along(pts, p.axis), _along(nrm, p.axis)
    if shape == "loft" and p.sections:
        return _loft(p.sections, p.axis, spacing)
    return _box(size, spacing)


# ------------------------------------------------------------------ the picture
@dataclass
class Points:
    """A whole model as points in its own space: where, facing which way, of which part."""

    at: np.ndarray
    normal: np.ndarray
    part: np.ndarray  # index into the scene's parts
    color: np.ndarray  # (parts, 3) 0..255
    lit: np.ndarray  # (parts,) False for a part that glows

    @property
    def extent(self) -> float:
        if not len(self.at):
            return 1.0
        return float(np.linalg.norm(self.at.max(axis=0) - self.at.min(axis=0)))


def drawn(p: Part) -> bool:
    return p.shape not in _NOT_DRAWN and not p.hidden and p.opacity > 0.05


def points(scene: ObjectScene, spacing: float | None = None, *, detail: int = 256) -> Points:
    """The model's surface as points close enough together for a picture ``detail`` px across."""
    if spacing is None:
        rough = points(scene, spacing=1e9)
        spacing = max(1e-4, 0.5 * rough.extent / detail)
    where = placements(scene)
    at, normal, index = [], [], []
    for i, p in enumerate(scene.parts):
        if not drawn(p):
            continue
        pts, nrm = part_cloud(p, spacing)
        if len(pts) > _MAX_POINTS:
            keep = np.linspace(0, len(pts) - 1, _MAX_POINTS).astype(int)
            pts, nrm = pts[keep], nrm[keep]
        turn, pos = where[p.id]
        at.append(pts @ turn.T + pos)
        normal.append(nrm @ turn.T)
        index.append(np.full(len(pts), i, dtype=np.int32))
    color = np.array([part_rgb(p) for p in scene.parts], dtype=float).reshape(-1, 3)
    lit = np.array([p.material != "emissive" for p in scene.parts], dtype=bool)
    if not at:
        return Points(np.zeros((0, 3)), np.zeros((0, 3)), np.zeros(0, dtype=np.int32), color, lit)
    return Points(np.concatenate(at), np.concatenate(normal), np.concatenate(index), color, lit)


def view_axes(azimuth: float, elevation: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(right, up, toward the camera) for a camera ``azimuth`` degrees round from the front
    (+z; 90 is from the model's +x side) and ``elevation`` degrees above level."""
    az, el = math.radians(azimuth), math.radians(max(-89.0, min(89.0, elevation)))
    toward = np.array([math.sin(az) * math.cos(el), math.sin(el), math.cos(az) * math.cos(el)])
    right = np.cross([0.0, 1.0, 0.0], toward)
    right /= np.linalg.norm(right)
    return right, np.cross(toward, right), toward


@dataclass
class Shot:
    rgb: np.ndarray  # (size, size, 3) uint8, on ``background``
    mask: np.ndarray  # (size, size) bool: where the model is
    part: np.ndarray  # (size, size) int: which part is in front there, -1 for none


def shoot(
    cloud: Points,
    azimuth: float = 32.0,
    elevation: float = 22.0,
    size: int = 256,
    *,
    margin: float = 0.06,
    background: tuple[int, int, int] = (245, 246, 248),
) -> Shot:
    """The model from one side: no perspective, fitted to the square with a margin."""
    rgb = np.empty((size, size, 3), dtype=np.uint8)
    rgb[:] = background
    part = np.full((size, size), -1, dtype=np.int32)
    if not len(cloud.at):
        return Shot(rgb, part >= 0, part)
    right, up, toward = view_axes(azimuth, elevation)
    u, v, depth = cloud.at @ right, cloud.at @ up, cloud.at @ toward
    span = max(float(u.max() - u.min()), float(v.max() - v.min()), 1e-6)
    scale = size * (1 - 2 * margin) / span
    x = np.round((u - (u.max() + u.min()) / 2) * scale + size / 2 - 0.5).astype(np.int64)
    y = np.round(size / 2 - 0.5 - (v - (v.max() + v.min()) / 2) * scale).astype(np.int64)
    ok = (x >= 0) & (x < size) & (y >= 0) & (y < size)
    order = np.argsort(depth[ok], kind="stable")  # farthest first: the nearest is written last
    flat = (y[ok] * size + x[ok])[order]
    index = cloud.part[ok][order]
    light = _unit((toward + 0.55 * up - 0.35 * right)[None, :])[0]
    facing = np.abs(cloud.normal[ok][order] @ toward)
    shade = 0.42 + 0.5 * np.clip(cloud.normal[ok][order] @ light, 0, 1) + 0.08 * facing
    shade = np.where(cloud.lit[index], shade, 1.0)
    colour = np.clip(cloud.color[index] * shade[:, None], 0, 255).astype(np.uint8)
    part.ravel()[flat] = index
    rgb.reshape(-1, 3)[flat] = colour
    return Shot(rgb, part >= 0, part)


def render(scene: ObjectScene, azimuth: float = 32.0, elevation: float = 22.0, size: int = 256) -> Shot:
    return shoot(points(scene, detail=size), azimuth, elevation, size)
