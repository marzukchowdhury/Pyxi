"""Turn a source .glb into a library model Pyxi can explain with: real geometry and textures,
split into named parts that can move on their own.

Each recipe (scripts/library/recipes.py) says how to rotate the model upright and which
triangles make up which part: by node name, by material name, and/or by a box of space
(in stage units, after the model is scaled to its stage size and centred). Triangles are
claimed by the first part that matches; whatever is left goes to the part marked ``rest``.

The output .glb has one node per part, named ``part_<id>``, placed at the part's centre with
its geometry around it, so a part can turn about its own middle. Materials and textures are
kept as they were (textures larger than 1024 px are scaled down; opaque PNGs become JPEGs).
The manifest records each part's centre and size, which the explanation's motions need.

Source files are untrusted data: only the JSON chunk and the binary buffer are read.
"""

from __future__ import annotations

import io
import json
import re
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from glb import _local

COMPONENT = {5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}
WIDTH = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}
NORMALIZE = {np.int8: 127.0, np.uint8: 255.0, np.int16: 32767.0, np.uint16: 65535.0}
UNSUPPORTED = {"KHR_draco_mesh_compression", "EXT_meshopt_compression", "KHR_mesh_quantization"}
TEXTURE_MAX = 1024


def _inside(base: Path, uri: str) -> Path:
    """A file a .gltf refers to, which must sit in its own folder (no ../ escapes)."""
    from urllib.parse import unquote

    target = (base / unquote(uri)).resolve()
    if base.resolve() not in target.parents:
        raise ValueError(f"{uri}: outside the model's folder")
    return target


def read_glb(path: Path) -> tuple[dict, bytes]:
    if path.suffix.lower() == ".gltf":
        gltf = json.loads(path.read_text(encoding="utf-8"))
        bufs = gltf.get("buffers", [])
        if len(bufs) > 1:
            raise ValueError("only one buffer is supported")
        binary = _inside(path.parent, bufs[0]["uri"]).read_bytes() if bufs and "uri" in bufs[0] else b""
        gltf["__base"] = str(path.parent)
        bad = UNSUPPORTED & set(gltf.get("extensionsUsed", []))
        if bad:
            raise ValueError(f"{path.name}: compressed with {sorted(bad)}; decompress it first")
        return gltf, binary
    data = path.read_bytes()
    magic, version, _ = struct.unpack_from("<4sII", data, 0)
    if magic != b"glTF" or version != 2:
        raise ValueError(f"{path.name}: not a glTF 2 binary")
    off, gltf, binary = 12, None, b""
    while off < len(data):
        n, kind = struct.unpack_from("<I4s", data, off)
        chunk = data[off + 8 : off + 8 + n]
        if kind == b"JSON":
            gltf = json.loads(chunk)
        elif kind == b"BIN\x00":
            binary = bytes(chunk)
        off += 8 + n
    if gltf is None:
        raise ValueError(f"{path.name}: no JSON chunk")
    bad = UNSUPPORTED & set(gltf.get("extensionsUsed", []))
    if bad:
        raise ValueError(f"{path.name}: compressed with {sorted(bad)}; decompress it first")
    return gltf, binary


def accessor(gltf: dict, binary: bytes, i: int) -> np.ndarray:
    a = gltf["accessors"][i]
    dtype = COMPONENT[a["componentType"]]
    width = WIDTH[a["type"]]
    count = a["count"]
    if "bufferView" not in a:
        out = np.zeros((count, width), dtype=np.float32)
    else:
        bv = gltf["bufferViews"][a["bufferView"]]
        if bv.get("buffer", 0) != 0:
            raise ValueError("only the glb's own buffer is supported")
        start = bv.get("byteOffset", 0) + a.get("byteOffset", 0)
        item = np.dtype(dtype).itemsize * width
        stride = bv.get("byteStride") or item
        raw = np.frombuffer(binary, dtype=np.uint8, count=stride * (count - 1) + item, offset=start)
        rows = np.lib.stride_tricks.as_strided(raw, shape=(count, item), strides=(stride, 1))
        out = np.ascontiguousarray(rows).view(dtype).reshape(count, width)
    if a.get("normalized") and dtype in NORMALIZE:
        out = out.astype(np.float32) / NORMALIZE[dtype]
    if "sparse" in a:
        raise ValueError("sparse accessors aren't supported")
    return out


@dataclass
class Tri:
    """All triangles of the model, flattened: three vertices each, in stage space."""

    pos: np.ndarray  # (n, 3, 3)
    nrm: np.ndarray  # (n, 3, 3)
    uv: np.ndarray | None  # (n, 3, 2) or None per batch -> filled with zeros
    material: np.ndarray  # (n,) material index (-1 for none)
    node: np.ndarray  # (n,) index into node_names
    has_uv: np.ndarray  # (n,) bool


def _rotation(deg: list[float]) -> np.ndarray:
    rx, ry, rz = np.radians(deg)
    cx, sx, cy, sy, cz, sz = np.cos(rx), np.sin(rx), np.cos(ry), np.sin(ry), np.cos(rz), np.sin(rz)
    x = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    y = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    z = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    return z @ y @ x


def flatten(gltf: dict, binary: bytes) -> tuple[Tri, list[str]]:
    """Every triangle of the default scene in world space, with its material and node path."""
    batches: list[tuple] = []
    node_names: list[str] = []

    def walk(i: int, parent: np.ndarray, path: str) -> None:
        n = gltf["nodes"][i]
        world = parent @ _local(n)
        name = n.get("name") or f"node{i}"
        full = f"{path}/{name}" if path else name
        if "mesh" in n:
            node_names.append(full)
            ni = len(node_names) - 1
            normal_m = np.linalg.inv(world[:3, :3]).T
            flip = np.linalg.det(world[:3, :3]) < 0
            for prim in gltf["meshes"][n["mesh"]].get("primitives", []):
                if prim.get("mode", 4) != 4:
                    continue  # lines and points: not part of a solid model
                attrs = prim["attributes"]
                p = accessor(gltf, binary, attrs["POSITION"])[:, :3].astype(np.float64)
                p = (world[:3, :3] @ p.T).T + world[:3, 3]
                nm = accessor(gltf, binary, attrs["NORMAL"])[:, :3].astype(np.float64) if "NORMAL" in attrs else None
                if nm is not None:
                    nm = (normal_m @ nm.T).T
                    nm /= np.maximum(np.linalg.norm(nm, axis=1, keepdims=True), 1e-12)
                uv = accessor(gltf, binary, attrs["TEXCOORD_0"])[:, :2].astype(np.float32) if "TEXCOORD_0" in attrs else None
                idx = accessor(gltf, binary, prim["indices"]).reshape(-1).astype(np.int64) if "indices" in prim else np.arange(len(p))
                idx = idx[: len(idx) // 3 * 3].reshape(-1, 3)
                if flip:
                    idx = idx[:, ::-1]
                tp = p[idx]
                if nm is None:  # flat normals from the faces
                    fn = np.cross(tp[:, 1] - tp[:, 0], tp[:, 2] - tp[:, 0])
                    fn /= np.maximum(np.linalg.norm(fn, axis=1, keepdims=True), 1e-12)
                    tn = np.repeat(fn[:, None, :], 3, axis=1)
                else:
                    tn = nm[idx]
                tuv = uv[idx] if uv is not None else np.zeros((len(idx), 3, 2), np.float32)
                batches.append((tp, tn, tuv, prim.get("material", -1), ni, uv is not None))
        for c in n.get("children", []):
            walk(c, world, full)

    scenes = gltf.get("scenes") or [{"nodes": list(range(len(gltf.get("nodes", []))))}]
    for root in scenes[gltf.get("scene", 0)].get("nodes", []):
        walk(root, np.eye(4), "")
    if not batches:
        raise ValueError("no triangles")
    tri = Tri(
        pos=np.concatenate([b[0] for b in batches]),
        nrm=np.concatenate([b[1] for b in batches]),
        uv=np.concatenate([b[2] for b in batches]),
        material=np.concatenate([np.full(len(b[0]), b[3]) for b in batches]),
        node=np.concatenate([np.full(len(b[0]), b[4]) for b in batches]),
        has_uv=np.concatenate([np.full(len(b[0]), b[5]) for b in batches]),
    )
    return tri, node_names


def normalise(tri: Tri, rotate: list[float], size: float) -> float:
    """Upright, centred, the longest side ``size`` units. Returns the scale used."""
    r = _rotation(rotate)
    tri.pos = (tri.pos.reshape(-1, 3) @ r.T).reshape(tri.pos.shape)
    tri.nrm = (tri.nrm.reshape(-1, 3) @ r.T).reshape(tri.nrm.shape)
    flat = tri.pos.reshape(-1, 3)
    lo, hi = flat.min(0), flat.max(0)
    scale = size / max(1e-9, float((hi - lo).max()))
    tri.pos = (tri.pos - (lo + hi) / 2) * scale
    return scale


def assign(tri: Tri, node_names: list[str], material_names: list[str], parts: list[dict]) -> np.ndarray:
    """Which part each triangle belongs to (index into parts; -1 = nobody's)."""
    owner = np.full(len(tri.pos), -1)
    centroid = tri.pos.mean(axis=1)
    node_name = np.array(node_names, dtype=object)[tri.node]
    mat_name = np.array([material_names[m] if m >= 0 else "" for m in range(len(material_names))] + [""], dtype=object)[tri.material]
    rest = None
    for k, part in enumerate(parts):
        if part.get("rest"):
            rest = k
        m = part.get("match")
        if not m:
            continue
        mask = owner == -1
        if "node" in m:
            rx = re.compile(m["node"], re.I)
            mask &= np.array([bool(rx.search(n)) for n in node_name])
        if "not_node" in m:
            rx = re.compile(m["not_node"], re.I)
            mask &= ~np.array([bool(rx.search(n)) for n in node_name])
        if "material" in m:
            rx = re.compile(m["material"], re.I)
            mask &= np.array([bool(rx.search(n)) for n in mat_name])
        if "region" in m:
            x0, y0, z0, x1, y1, z1 = m["region"]
            c = centroid
            mask &= (c[:, 0] >= x0) & (c[:, 0] <= x1) & (c[:, 1] >= y0) & (c[:, 1] <= y1) & (c[:, 2] >= z0) & (c[:, 2] <= z1)
        owner[mask] = k
    if rest is not None:
        owner[owner == -1] = rest
    return owner


class Writer:
    """Builds the output glTF: buffer views appended to one binary, accessors with bounds."""

    def __init__(self) -> None:
        self.bin = bytearray()
        self.views: list[dict] = []
        self.accessors: list[dict] = []

    def view(self, data: bytes, target: int | None = None) -> int:
        while len(self.bin) % 4:
            self.bin += b"\0"
        v = {"buffer": 0, "byteOffset": len(self.bin), "byteLength": len(data)}
        if target:
            v["target"] = target
        self.bin += data
        self.views.append(v)
        return len(self.views) - 1

    def accessor(self, arr: np.ndarray, kind: str, indices: bool = False) -> int:
        if indices:
            arr = arr.astype(np.uint32 if arr.max(initial=0) > 65535 else np.uint16)
            comp = 5125 if arr.dtype == np.uint32 else 5123
        else:
            arr = arr.astype(np.float32)
            comp = 5126
        a: dict[str, Any] = {
            "bufferView": self.view(arr.tobytes(), 34963 if indices else 34962),
            "componentType": comp,
            "count": int(len(arr) if indices else arr.shape[0]),
            "type": kind,
        }
        if kind == "VEC3" and not indices:
            a["min"] = arr.min(0).tolist()
            a["max"] = arr.max(0).tolist()
        self.accessors.append(a)
        return len(self.accessors) - 1


def _image_bytes(gltf: dict, binary: bytes, img: dict) -> tuple[bytes, str]:
    if "bufferView" in img:
        bv = gltf["bufferViews"][img["bufferView"]]
        start = bv.get("byteOffset", 0)
        data = binary[start : start + bv["byteLength"]]
    elif img.get("uri", "").startswith("data:"):
        import base64

        data = base64.b64decode(img["uri"].split(",", 1)[1])
    elif "uri" in img and "__base" in gltf:
        data = _inside(Path(gltf["__base"]), img["uri"]).read_bytes()
    else:
        raise ValueError("external image files aren't supported")
    mime = img.get("mimeType") or ("image/png" if data[:4] == b"\x89PNG" else "image/jpeg")
    return data, mime


def _shrink(data: bytes, mime: str) -> tuple[bytes, str]:
    """Textures at most 1024 px; opaque PNGs become JPEGs (a tenth of the size)."""
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
    except Exception:
        return data, mime
    changed = False
    if max(im.size) > TEXTURE_MAX:
        im.thumbnail((TEXTURE_MAX, TEXTURE_MAX), Image.LANCZOS)
        changed = True
    alpha = im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info)
    if alpha and im.mode != "RGBA":
        im = im.convert("RGBA")
    if alpha and im.getchannel("A").getextrema()[0] >= 250:
        alpha = False
    out = io.BytesIO()
    if not alpha and (mime == "image/png" or changed):
        im.convert("RGB").save(out, "JPEG", quality=86, optimize=True)
        new = (out.getvalue(), "image/jpeg")
    elif changed:
        im.save(out, "PNG", optimize=True)
        new = (out.getvalue(), "image/png")
    else:
        return data, mime
    return new if len(new[0]) < len(data) else (data, mime)


def pack(src: Path, out: Path, recipe: dict) -> dict:
    """Write the library .glb; return its manifest entry (parts with centres and sizes)."""
    gltf, binary = read_glb(src)
    tri, node_names = flatten(gltf, binary)
    normalise(tri, recipe.get("rotate", [0, 0, 0]), recipe.get("size", 10.0))
    materials = gltf.get("materials", [])
    material_names = [m.get("name") or f"material{i}" for i, m in enumerate(materials)]
    parts = recipe["parts"]
    owner = assign(tri, node_names, material_names, parts)

    w = Writer()
    out_nodes: list[dict] = []
    out_meshes: list[dict] = []
    entries: list[dict] = []
    for k, part in enumerate(parts):
        sel = owner == k
        if not sel.any():
            if part.get("optional"):
                continue
            raise ValueError(f"{recipe['id']}: part {part['id']!r} matched no triangles")
        pts = tri.pos[sel].reshape(-1, 3)
        lo, hi = pts.min(0), pts.max(0)
        center = (lo + hi) / 2
        prims = []
        for mat in np.unique(tri.material[sel]):
            s = sel & (tri.material == mat)
            p = (tri.pos[s] - center).reshape(-1, 3)
            n = tri.nrm[s].reshape(-1, 3)
            uv = tri.uv[s].reshape(-1, 2)
            # Weld identical corners back together (shared vertices), so the file stays small.
            key = np.concatenate([np.round(p, 5), np.round(n, 3), np.round(uv, 5)], axis=1)
            uniq, inverse = np.unique(key, axis=0, return_inverse=True)
            attrs = {"POSITION": w.accessor(uniq[:, :3], "VEC3"), "NORMAL": w.accessor(uniq[:, 3:6], "VEC3")}
            if tri.has_uv[s].any():
                attrs["TEXCOORD_0"] = w.accessor(uniq[:, 6:8], "VEC2")
            prim = {"attributes": attrs, "indices": w.accessor(inverse.reshape(-1), "SCALAR", indices=True)}
            if mat >= 0:
                prim["material"] = int(mat)
            prims.append(prim)
        out_meshes.append({"name": part["id"], "primitives": prims})
        out_nodes.append({"name": f"part_{part['id']}", "mesh": len(out_meshes) - 1, "translation": center.tolist()})
        entries.append(
            {
                "id": part["id"],
                "label": part.get("label"),
                "note": part.get("note"),
                "casing": bool(part.get("casing")),
                "center": [round(float(v), 3) for v in center],
                "size": [round(float(v), 3) for v in hi - lo],
                "triangles": int(sel.sum()),
            }
        )

    # Materials, textures, samplers and images carry over as they were (images re-encoded).
    images = []
    for img in gltf.get("images", []):
        data, mime = _shrink(*_image_bytes(gltf, binary, img))
        images.append({"bufferView": w.view(data), "mimeType": mime, **({"name": img["name"]} if "name" in img else {})})
    doc: dict[str, Any] = {
        "asset": {"version": "2.0", "generator": "Pyxi library pack", "copyright": recipe["credit"]},
        "scene": 0,
        "scenes": [{"nodes": [len(out_nodes)]}],
        "nodes": [*out_nodes, {"name": recipe["id"], "children": list(range(len(out_nodes)))}],
        "meshes": out_meshes,
        "materials": materials,
        "textures": gltf.get("textures", []),
        "samplers": gltf.get("samplers", []),
        "images": images,
        "accessors": w.accessors,
        "bufferViews": w.views,
        "buffers": [{"byteLength": len(w.bin)}],
    }
    used = [e for e in gltf.get("extensionsUsed", []) if e not in UNSUPPORTED]
    if used:
        doc["extensionsUsed"] = used
    for k in ("textures", "samplers", "images"):
        if not doc[k]:
            del doc[k]
    js = json.dumps(doc, separators=(",", ":")).encode()
    js += b" " * (-len(js) % 4)
    body = bytes(w.bin) + b"\0" * (-len(w.bin) % 4)
    blob = struct.pack("<4sII", b"glTF", 2, 12 + 8 + len(js) + 8 + len(body))
    blob += struct.pack("<I4s", len(js), b"JSON") + js + struct.pack("<I4s", len(body), b"BIN\x00") + body
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(blob)
    return {
        "id": recipe["id"],
        "title": recipe["title"],
        "file": out.name,
        "bytes": len(blob),
        "triangles": int(len(tri.pos)),
        "about": recipe.get("about", ""),
        "keywords": recipe.get("keywords", []),
        "credit": recipe["credit"],
        "license": recipe["license"],
        "source": recipe["source"],
        "parts": entries,
    }
