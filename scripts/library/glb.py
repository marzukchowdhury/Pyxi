"""Read a .glb's scene graph without any 3D package: node names, which meshes they draw, and
each node's world-space bounding box (from the accessors' min/max, so no vertex data is read).
Used to build the realistic-model library: to see how a model is split, and to work out where
each named piece sits once the model is scaled to Pyxi's stage.

The files come from outside, so they're treated as data: only the JSON chunk is parsed, and
nothing in a file is ever executed.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np


def read_json(path: Path) -> dict:
    data = path.read_bytes()
    magic, version, _length = struct.unpack_from("<4sII", data, 0)
    if magic != b"glTF" or version != 2:
        raise ValueError(f"{path.name}: not a glTF 2 binary")
    chunk_len, chunk_type = struct.unpack_from("<I4s", data, 12)
    if chunk_type != b"JSON":
        raise ValueError(f"{path.name}: first chunk isn't JSON")
    return json.loads(data[20 : 20 + chunk_len])


def _local(node: dict) -> np.ndarray:
    if "matrix" in node:
        return np.array(node["matrix"], dtype=float).reshape(4, 4).T
    t = np.array(node.get("translation", [0, 0, 0]), dtype=float)
    x, y, z, w = node.get("rotation", [0, 0, 0, 1])
    s = np.array(node.get("scale", [1, 1, 1]), dtype=float)
    r = np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ]
    )
    m = np.eye(4)
    m[:3, :3] = r * s
    m[:3, 3] = t
    return m


@dataclass
class Node:
    index: int
    name: str
    parent: int | None
    world: np.ndarray
    box: tuple[np.ndarray, np.ndarray] | None  # own mesh, world space
    children: list[int] = field(default_factory=list)
    triangles: int = 0


def nodes(gltf: dict) -> list[Node]:
    """Every node of the default scene, with world matrices and its own mesh's world box."""
    raw = gltf.get("nodes", [])
    meshes = gltf.get("meshes", [])
    accessors = gltf.get("accessors", [])
    out: dict[int, Node] = {}

    def mesh_box(mi: int, world: np.ndarray):
        lo, hi, tris = None, None, 0
        for prim in meshes[mi].get("primitives", []):
            acc = accessors[prim["attributes"]["POSITION"]]
            if "min" not in acc or "max" not in acc:
                continue
            a, b = np.array(acc["min"][:3]), np.array(acc["max"][:3])
            corners = np.array([[x, y, z, 1] for x in (a[0], b[0]) for y in (a[1], b[1]) for z in (a[2], b[2])])
            pts = (world @ corners.T).T[:, :3]
            lo = pts.min(0) if lo is None else np.minimum(lo, pts.min(0))
            hi = pts.max(0) if hi is None else np.maximum(hi, pts.max(0))
            idx = prim.get("indices")
            count = accessors[idx]["count"] if idx is not None else acc["count"]
            tris += count // 3
        return (lo, hi) if lo is not None else None, tris

    def walk(i: int, parent: int | None, parent_world: np.ndarray):
        n = raw[i]
        world = parent_world @ _local(n)
        box, tris = mesh_box(n["mesh"], world) if "mesh" in n else (None, 0)
        out[i] = Node(i, n.get("name") or f"node{i}", parent, world, box, list(n.get("children", [])), tris)
        for c in n.get("children", []):
            walk(c, i, world)

    scenes = gltf.get("scenes") or [{"nodes": list(range(len(raw)))}]
    for root in scenes[gltf.get("scene", 0)].get("nodes", []):
        walk(root, None, np.eye(4))
    return list(out.values())


def subtree_box(all_nodes: list[Node], index: int):
    """World box of a node and everything under it."""
    by = {n.index: n for n in all_nodes}
    lo = hi = None
    stack = [index]
    while stack:
        n = by[stack.pop()]
        if n.box is not None:
            lo = n.box[0] if lo is None else np.minimum(lo, n.box[0])
            hi = n.box[1] if hi is None else np.maximum(hi, n.box[1])
        stack.extend(n.children)
    return (lo, hi) if lo is not None else None


def describe(path: Path, depth: int = 3) -> str:
    g = read_json(path)
    ns = nodes(g)
    by = {n.index: n for n in ns}
    lines = [f"{path.name}: {len(ns)} nodes, {len(g.get('meshes', []))} meshes, {len(g.get('materials', []))} materials, "
             f"{sum(n.triangles for n in ns)} triangles"]

    def show(i: int, d: int):
        n = by[i]
        box = subtree_box(ns, i)
        size = "" if box is None else " size " + " ".join(f"{v:.2f}" for v in box[1] - box[0])
        lines.append(f"{'  ' * d}- {n.name} ({n.triangles} tris){size}")
        if d < depth:
            for c in n.children:
                show(c, d + 1)

    for n in ns:
        if n.parent is None:
            show(n.index, 0)
    return "\n".join(lines)


if __name__ == "__main__":
    import sys

    for p in sys.argv[1:]:
        print(describe(Path(p), depth=int(__import__("os").environ.get("DEPTH", "3"))))
        print()


def materials(path: Path) -> list[tuple[str, int, np.ndarray, np.ndarray]]:
    """(material name, triangles, world min, world max) for every material in use."""
    g = read_json(path)
    names = [m.get("name") or f"material{i}" for i, m in enumerate(g.get("materials", []))]
    meshes, accessors = g.get("meshes", []), g.get("accessors", [])
    acc: dict[str, list] = {}
    for n in nodes(g):
        mi = g["nodes"][n.index].get("mesh")
        if mi is None:
            continue
        for prim in meshes[mi].get("primitives", []):
            a = accessors[prim["attributes"]["POSITION"]]
            if "min" not in a:
                continue
            lo, hi = np.array(a["min"][:3]), np.array(a["max"][:3])
            pts = np.array([[x, y, z, 1] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
            w = (n.world @ pts.T).T[:, :3]
            name = names[prim["material"]] if "material" in prim else "(none)"
            idx = prim.get("indices")
            tris = (accessors[idx]["count"] if idx is not None else a["count"]) // 3
            e = acc.setdefault(name, [0, w.min(0), w.max(0)])
            e[0] += tris
            e[1], e[2] = np.minimum(e[1], w.min(0)), np.maximum(e[2], w.max(0))
    return [(k, v[0], v[1], v[2]) for k, v in acc.items()]
