"""Pyxi's library of real 3D models: ready-made, realistic models (NASA's, CC0 scans and
product models) split into named parts, so an explanation can run on the real thing instead
of a model built from simple shapes.

The catalog (library.json, written by scripts/library/build.py) ships with the app; the .glb
files are downloaded the first time they're needed, into the data folder, from
``PyxiSettings.library_url``. A library model becomes an ObjectScene whose parts are
``asset`` parts (positioned at their real centres), so motions, callouts, cutaways and the
camera all work on it exactly as on a built model; the explanation itself (beats, motions,
extra parts like an arrow of air or a parachute) is a small edit written on top.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.request
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from pyxi.scenes.objects import Beat, ObjectScene, Part

log = logging.getLogger(__name__)
CATALOG = Path(__file__).with_name("library.json")
_ID = re.compile(r"^[a-z0-9_]+$")


@dataclass(frozen=True)
class LibraryPart:
    id: str
    label: str | None
    note: str | None
    casing: bool
    center: tuple[float, float, float]
    size: tuple[float, float, float]


@dataclass(frozen=True)
class LibraryModel:
    id: str
    title: str
    file: str
    bytes: int
    about: str
    keywords: tuple[str, ...]
    credit: str
    license: str
    source: str
    parts: tuple[LibraryPart, ...]

    def scene(self) -> ObjectScene:
        """The model at rest: every part in place, labelled, one quiet beat."""
        parts = [
            Part(
                id=p.id,
                shape="asset",
                asset=self.id,
                node=p.id,
                position=list(p.center),
                size=[max(0.005, v) for v in p.size],
                label=p.label,
                note=p.note,
                casing=p.casing,
            )
            for p in self.parts
        ]
        return few_labels(
            ObjectScene(
                title=self.title,
                parts=parts,
                beats=[Beat(title=self.title, narration="", camera="iso")],
                credit=self.credit,
            )
        )

    def summary(self) -> str:
        """What the explaining model needs: what it is, which way it faces, and its parts."""
        lines = [f"{self.title} ({self.about})" if self.about else self.title, "Parts (id: centre, size):"]
        for p in self.parts:
            c = ", ".join(f"{v:.2f}" for v in p.center)
            s = ", ".join(f"{v:.2f}" for v in p.size)
            name = f" '{p.label}'" if p.label else ""
            note = f" ({p.note})" if p.note else ""
            lines.append(f"- {p.id}{name}{note}: [{c}], size [{s}]" + (" (outer shell)" if p.casing else ""))
        return "\n".join(lines)


@cache
def models() -> dict[str, LibraryModel]:
    try:
        data = json.loads(CATALOG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out: dict[str, LibraryModel] = {}
    for m in data.get("models", []):
        if not _ID.match(str(m.get("id", ""))):
            continue
        parts = tuple(
            LibraryPart(
                id=p["id"],
                label=p.get("label"),
                note=p.get("note"),
                casing=bool(p.get("casing")),
                center=tuple(p["center"]),
                size=tuple(p["size"]),
            )
            for p in m.get("parts", [])
        )
        out[m["id"]] = LibraryModel(
            id=m["id"],
            title=m["title"],
            file=m["file"],
            bytes=int(m.get("bytes", 0)),
            about=m.get("about", ""),
            keywords=tuple(m.get("keywords", [])),
            credit=m.get("credit", ""),
            license=m.get("license", ""),
            source=m.get("source", ""),
            parts=parts,
        )
    return out


def get(model_id: str) -> LibraryModel | None:
    return models().get(model_id)


def menu() -> str:
    """One line per model, for the chat model to pick from: 'id: title (keywords)'."""
    return "; ".join(f"{m.id}: {m.title}" for m in models().values())


def local_path(library_dir: Path, model: LibraryModel) -> Path:
    return library_dir / model.file


def ensure(library_dir: Path, model: LibraryModel, base_url: str | None) -> Path | None:
    """The model's file on disk, downloading it the first time. None if it can't be had."""
    path = local_path(library_dir, model)
    if path.is_file() and path.stat().st_size == model.bytes:
        return path  # the size tells an old copy of a since-rebuilt model from the right one
    if not base_url:
        return None
    url = base_url.rstrip("/") + "/" + model.file
    tmp = path.with_suffix(".part")
    try:
        library_dir.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(url, headers={"User-Agent": "Pyxi"})
        with urllib.request.urlopen(req, timeout=60) as r, tmp.open("wb") as f:
            while chunk := r.read(1 << 16):
                f.write(chunk)
        if tmp.read_bytes()[:4] != b"glTF":
            raise ValueError("not a .glb")
        tmp.replace(path)
        return path
    except Exception as exc:
        log.warning("library model %s unavailable: %s", model.id, exc)
        tmp.unlink(missing_ok=True)
        return None


MOST_LABELS = 7


def few_labels(scene: ObjectScene, most: int = MOST_LABELS) -> ObjectScene:
    """At most ``most`` tags, each saying something different: a car tagged on all twelve of its
    parts (two "Door"s, two "Front wheel"s) is a cloud of stickers, not an explanation. Tags the
    story talks about stay first: markers (they exist only for their tag), then the parts the
    beats focus on, then the rest in order."""
    focus = {pid for b in scene.beats for pid in b.focus}
    ranked = sorted(
        (p for p in scene.parts if p.label),
        key=lambda p: (p.shape != "marker", p.id not in focus),
    )
    keep: set[str] = set()
    said: set[str] = set()
    for p in ranked:
        text = (p.label or "").strip().lower()
        if len(keep) < most and text not in said:
            keep.add(p.id)
            said.add(text)
    for p in scene.parts:
        if p.label and p.id not in keep:
            p.label = None
    return scene


def scene_json(model_id: str) -> dict[str, Any] | None:
    m = get(model_id)
    return m.scene().model_dump(mode="json") if m else None


@cache
def _surface(path_str: str, mtime: float) -> Any:
    """Up to 40,000 points on the model's surface (its vertices, in stage units)."""
    import struct

    import numpy as np

    data = Path(path_str).read_bytes()
    n = struct.unpack_from("<I", data, 12)[0]
    doc = json.loads(data[20 : 20 + n])
    bin_start = 20 + n + 8
    pts = []
    for node in doc.get("nodes", []):
        if "mesh" not in node:
            continue
        t = np.array(node.get("translation", [0, 0, 0]), dtype=np.float32)
        for prim in doc["meshes"][node["mesh"]]["primitives"]:
            a = doc["accessors"][prim["attributes"]["POSITION"]]
            bv = doc["bufferViews"][a["bufferView"]]
            arr = np.frombuffer(data, dtype=np.float32, count=a["count"] * 3, offset=bin_start + bv.get("byteOffset", 0) + a.get("byteOffset", 0))
            pts.append(arr.reshape(-1, 3) + t)
    if not pts:
        return None
    allp = np.concatenate(pts)
    if len(allp) > 40_000:
        allp = allp[np.random.default_rng(0).choice(len(allp), 40_000, replace=False)]
    return allp


def snap_markers(scene: ObjectScene, library_dir: Path) -> ObjectScene:
    """Pin every free-standing marker onto the real model's surface (the explaining model only
    knows parts' boxes, so it places tags near things, not on them)."""
    import numpy as np

    asset = next((p.asset for p in scene.parts if p.shape == "asset" and p.asset), None)
    m = get(asset) if asset else None
    if m is None:
        return scene
    path = local_path(library_dir, m)
    try:
        pts = _surface(str(path), path.stat().st_mtime)
    except (OSError, ValueError, KeyError):
        return scene
    if pts is None:
        return scene
    for p in scene.parts:
        if p.shape != "marker" or p.parent:
            continue
        q = np.array(p.position, dtype=np.float32)
        d = np.linalg.norm(pts - q, axis=1)
        i = int(d.argmin())
        if d[i] > 0.02:
            p.position = [round(float(v), 3) for v in pts[i]]
    return scene
