"""A look at a built model before it is shown.

The builder writes a model in one go and nobody looks at it: a headlight left hanging in front
of the car, the same wheel written twice in one place (which flickers), a labelled valve sealed
inside a solid block where no one can see it. This looks, from the numbers alone (each part's
box in the model's space), and does two things:

* ``tidy`` mends what has one right answer: a doubled part goes, and a solid part that hides a
  labelled one becomes a see-through shell.
* ``floating`` lists small parts that hang apart from everything else. Those have no one right
  answer (a moon is meant to be apart from its planet), so the builder is asked once.

It is deliberately only about boxes. An outline match was tried elsewhere and scored a model
with its face missing at 0.88, so nothing here pretends to judge the look.
"""

from __future__ import annotations

from dataclasses import dataclass

from pyxi.scenes.objects import ObjectScene, Part, shape_points

# Never judged: light, flows and tags are not matter, and a floor is under everything.
_EFFECTS = {"arrow", "marker", "plane"}
# Solid enough to hide what is inside them.
_SOLID = {"box", "rounded_box", "sphere", "cylinder", "capsule", "lathe", "extrude", "blob", "cone", "wedge"}
_ROUND = {"sphere", "capsule", "lathe", "blob", "cone", "cylinder"}
# Standing beside the model for scale, on purpose.
_BESIDE = {"person", "house"}


@dataclass
class Box:
    part: Part
    lo: list[float]
    hi: list[float]
    judged: bool  # False: an effect, hidden, moving away, or under a turned parent

    @property
    def volume(self) -> float:
        return max(1e-9, (self.hi[0] - self.lo[0]) * (self.hi[1] - self.lo[1]) * (self.hi[2] - self.lo[2]))


def _gap(a: Box, b: Box) -> float:
    """How far apart two boxes are (0 when they touch or overlap)."""
    return sum(max(0.0, a.lo[i] - b.hi[i], b.lo[i] - a.hi[i]) ** 2 for i in range(3)) ** 0.5


def boxes(scene: ObjectScene) -> dict[str, Box]:
    """Each part's box in the model's space. Positions add up through parents; a turned part
    takes a box as wide as its longest side, so a wrong guess errs towards "touching"."""
    by_id = {p.id: p for p in scene.parts}
    travels = {m.part for m in scene.motions if m.kind == "keyframes" and any(k.position for k in m.keys)}
    travels |= {m.part for m in scene.motions if m.kind == "oscillate" and m.prop == "position"}
    out: dict[str, Box] = {}
    for p in scene.parts:
        at = list(p.position)
        judged = p.shape not in _EFFECTS and not p.hidden and p.opacity > 0.05 and p.material != "emissive"
        judged = judged and p.kit not in _BESIDE and p.id not in travels
        cur, hops = p, 0
        while cur.parent and cur.parent in by_id and hops < 32:
            cur, hops = by_id[cur.parent], hops + 1
            at = [at[i] + cur.position[i] for i in range(3)]
            # Under a turned or travelling parent the sums above are not where it is.
            if any(cur.rotation) or cur.id in travels or any(m.part == cur.id and m.kind == "spin" for m in scene.motions):
                judged = False
        pts = shape_points(p) if p.shape in ("tube", "arrow", "limb", "blob") else []
        if pts and not any(p.rotation):
            lo = [at[i] + min(c[i] - r[i] for c, r in pts) for i in range(3)]
            hi = [at[i] + max(c[i] + r[i] for c, r in pts) for i in range(3)]
        else:
            half = [max(p.size) / 2] * 3 if any(p.rotation) else [s / 2 for s in p.size]
            lo, hi = [at[i] - half[i] for i in range(3)], [at[i] + half[i] for i in range(3)]
        out[p.id] = Box(p, lo, hi, judged)
    return out


def _extent(found: dict[str, Box]) -> float:
    judged = [b for b in found.values() if b.judged] or list(found.values())
    return max(max(b.hi[i] for b in judged) - min(b.lo[i] for b in judged) for i in range(3))


@dataclass
class Floater:
    ids: list[str]  # the parts that hang together, apart from the rest
    near: str  # the nearest part of the main model
    gap: float


def floating(scene: ObjectScene) -> list[Floater]:
    """Small groups of parts that touch each other and nothing else."""
    found = boxes(scene)
    judged = [b for b in found.values() if b.judged]
    if len(judged) < 3:
        return []
    extent = _extent(found)
    touch = 0.04 * extent
    group = {b.part.id: b.part.id for b in judged}

    def root(i: str) -> str:
        while group[i] != i:
            group[i] = group[group[i]]
            i = group[i]
        return i

    for n, a in enumerate(judged):
        for b in judged[n + 1 :]:
            if _gap(a, b) <= touch:
                group[root(a.part.id)] = root(b.part.id)
    groups: dict[str, list[Box]] = {}
    for b in judged:
        groups.setdefault(root(b.part.id), []).append(b)
    main = max(groups.values(), key=lambda g: sum(b.volume for b in g))
    main_volume = sum(b.volume for b in main)
    out: list[Floater] = []
    for g in groups.values():
        if g is main or sum(b.volume for b in g) > 0.15 * main_volume:
            continue  # two big things apart are two things (a planet and its moon), not a mistake
        gap, near = min(((_gap(a, b), b.part.id) for a in g for b in main), key=lambda x: x[0])
        if gap > 0.1 * extent:
            out.append(Floater([b.part.id for b in g], near, gap))
    return out


def tidy(scene: ObjectScene) -> tuple[ObjectScene, list[str]]:
    """Mend what has one right answer. Returns the model (the same one if nothing was wrong)
    and what was done, in words."""
    found = boxes(scene)
    extent = _extent(found)
    done: list[str] = []
    moving = {m.part for m in scene.motions}
    parents = {p.parent for p in scene.parts if p.parent}

    # The same piece twice in one place: the two surfaces fight and flicker.
    drop: set[str] = set()
    judged = [b for b in found.values() if b.judged]
    for n, a in enumerate(judged):
        for b in judged[n + 1 :]:
            if a.part.id in drop or b.part.id in drop or a.part.shape != b.part.shape:
                continue
            same = all(abs(a.lo[i] - b.lo[i]) < 0.01 * extent and abs(a.hi[i] - b.hi[i]) < 0.01 * extent for i in range(3))
            if same and not b.part.label and b.part.id not in moving and b.part.id not in parents:
                drop.add(b.part.id)
                done.append(f"{b.part.id} was {a.part.id} again in the same place: removed")

    # A labelled part sealed inside a solid one is a tag pointing at nothing you can see.
    shells: set[str] = set()
    for inner in judged:
        if not inner.part.label or inner.part.id in drop:
            continue
        for outer in judged:
            o = outer.part
            if o.id == inner.part.id or o.id in drop or o.casing or o.shape not in _SOLID:
                continue
            if o.opacity < 0.9 or o.material == "glass" or outer.volume < 3 * inner.volume:
                continue
            # A round shape does not fill its box: only its middle surely hides things.
            inset = 0.15 if o.shape in _ROUND else 0.0
            lo = [outer.lo[i] + inset * (outer.hi[i] - outer.lo[i]) for i in range(3)]
            hi = [outer.hi[i] - inset * (outer.hi[i] - outer.lo[i]) for i in range(3)]
            if all(lo[i] <= inner.lo[i] and inner.hi[i] <= hi[i] for i in range(3)):
                shells.add(o.id)
                done.append(f"{inner.part.id} was sealed inside {o.id}: {o.id} is now a see-through shell")
    if not drop and not shells:
        return scene, done
    data = scene.model_dump()
    data["parts"] = [{**p, "casing": True} if p["id"] in shells else p for p in data["parts"] if p["id"] not in drop]
    data["motions"] = [m for m in data["motions"] if m["part"] not in drop]
    if shells:
        data["cutaway"] = True
    data["notes"] = [*scene.notes, *done]
    return ObjectScene.model_validate(data), done


def describe(scene: ObjectScene, floaters: list[Floater]) -> str:
    """The floaters, in words the builder can act on: where each is and what it is nearest."""
    found = boxes(scene)
    fmt = lambda v: "[" + ", ".join(f"{x:.2f}" for x in v) + "]"  # noqa: E731
    lines = []
    for f in floaters:
        for pid in f.ids:
            b, p = found[pid], found[pid].part
            n = found[f.near]
            under = f", a child of {p.parent} (its position is relative to that)" if p.parent else ""
            lines.append(
                f"- {pid} ({p.shape}{under}): position {fmt(p.position)}, fills {fmt(b.lo)} to {fmt(b.hi)}. "
                f"Nearest is {f.near}, filling {fmt(n.lo)} to {fmt(n.hi)}: {f.gap:.2f} away."
            )
    return "\n".join(lines)
