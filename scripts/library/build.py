"""Build Pyxi's realistic-model library.

    python -I scripts/library/build.py              build every recipe (downloads sources once)
    python -I scripts/library/build.py jet saturn_v build some
    python -I scripts/library/build.py --probe saturn_v
                                                    where its nodes and materials sit, in stage
                                                    units, to write a recipe's part rules

Sources go to .pyxi/library_src/, built models to .pyxi/library/ (the dev data folder, so a
local brain serves them straight away), and the catalog the app ships with to
src/pyxi/scenes/library.json. Publishing: upload .pyxi/library/*.glb to the release the app
downloads from (PyxiSettings.library_url); the catalog travels inside the app.
"""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
ROOT = HERE.parents[1]

import numpy as np  # noqa: E402
from pack import flatten, normalise, pack, read_glb  # noqa: E402
from recipes import RECIPES  # noqa: E402

SRC = ROOT / ".pyxi" / "library_src"
OUT = ROOT / ".pyxi" / "library"
CATALOG = ROOT / "src" / "pyxi" / "scenes" / "library.json"
AGENT = {"User-Agent": "Pyxi-library-builder"}
KHRONOS = "https://raw.githubusercontent.com/KhronosGroup/glTF-Sample-Assets/main/Models/"


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers=AGENT)
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def _polyhaven(recipe: dict) -> Path:
    """A Poly Haven model at 1k textures: its .gltf, .bin and textures, in a folder of its own."""
    folder = SRC / recipe["id"]
    have = sorted(folder.glob("*.gltf")) if folder.exists() else []
    if have:
        return have[0]
    files = json.loads(_get(f"https://api.polyhaven.com/files/{recipe['polyhaven']}"))
    g = files["gltf"].get("1k") or next(iter(files["gltf"].values()))
    g = g["gltf"]
    main = folder / Path(g["url"]).name
    if main.exists():
        return main
    for rel, inc in g.get("include", {}).items():
        target = (folder / rel).resolve()
        if folder.resolve() not in target.parents:
            raise ValueError(f"{rel}: outside the model's folder")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(_get(inc["url"]))
    main.parent.mkdir(parents=True, exist_ok=True)
    main.write_bytes(_get(g["url"]))
    return main


def _khronos_legal(recipe: dict) -> None:
    """Khronos sample models each carry their own licence and artist: record them exactly."""
    meta = json.loads(_get(f"{KHRONOS}{recipe['khronos']}/metadata.json"))
    legal = [x for x in meta.get("legal", []) if not str(x.get("license", "")).startswith("LicenseRef")]
    if not legal:
        raise ValueError("no licence in its metadata")
    lic = sorted({x["license"] for x in legal})
    if not set(lic) <= {"CC0-1.0", "CC-BY-4.0"}:
        raise ValueError(f"licence {lic} isn't one the library takes")
    artists = ", ".join(sorted({x.get("artist", "") for x in legal if x.get("artist")}))
    recipe["license"] = " + ".join(lic)
    recipe["credit"] = f"{meta.get('name', recipe['khronos'])} by {artists} (Khronos glTF Sample Assets, {recipe['license']})"


def source(recipe: dict) -> Path:
    if "polyhaven" in recipe:
        return _polyhaven(recipe)
    if "khronos" in recipe:
        _khronos_legal(recipe)
    path = SRC / f"{recipe['id']}{Path(recipe.get('file_name', 'x.glb')).suffix or '.glb'}"
    if path.exists():
        return path
    url = recipe["url"]
    if url.startswith("file:"):
        return Path(url[5:])
    SRC.mkdir(parents=True, exist_ok=True)
    data = _get(url)
    if data[:4] != b"glTF":
        raise ValueError(f"{recipe['id']}: {url} isn't a .glb")
    path.write_bytes(data)
    return path


def probe(recipe: dict) -> None:
    gltf, binary = read_glb(source(recipe))
    tri, names = flatten(gltf, binary)
    normalise(tri, recipe.get("rotate", [0, 0, 0]), recipe.get("size", 10.0))
    flat = tri.pos.reshape(-1, 3)
    print(f"{recipe['id']}: {len(tri.pos)} triangles, box {flat.min(0).round(2)} .. {flat.max(0).round(2)}")
    mats = [m.get("name") or f"material{i}" for i, m in enumerate(gltf.get("materials", []))]
    for label, key, table in (("material", tri.material, mats), ("node", tri.node, names)):
        print(f"by {label}:")
        for v in np.unique(key):
            s = key == v
            p = tri.pos[s].reshape(-1, 3)
            lo, hi = p.min(0), p.max(0)
            name = table[v] if v >= 0 else "(none)"
            print(f"  {str(name)[-48:]:48s} {int(s.sum()):7d}  center {((lo + hi) / 2).round(2)}  size {(hi - lo).round(2)}")


def main() -> None:
    args = sys.argv[1:]
    if args and args[0] == "--probe":
        for rid in args[1:]:
            probe(next(r for r in RECIPES if r["id"] == rid))
        return
    catalog = json.loads(CATALOG.read_text(encoding="utf-8")) if CATALOG.exists() else {"models": []}
    have = {m["id"]: m for m in catalog["models"]}
    for recipe in RECIPES:
        if args and recipe["id"] not in args:
            continue
        try:
            entry = pack(source(recipe), OUT / f"{recipe['id']}.glb", recipe)
        except Exception as exc:  # one broken source mustn't stop the rest
            print(f"FAILED {recipe['id']}: {exc}")
            continue
        have[recipe["id"]] = entry
        print(f"{recipe['id']:22s} {entry['bytes'] / 1e6:5.1f} MB  {entry['triangles']:7d} tris  "
              f"parts: {', '.join(p['id'] for p in entry['parts'])}")
    order = [r["id"] for r in RECIPES]
    catalog["models"] = sorted(have.values(), key=lambda m: order.index(m["id"]) if m["id"] in order else 999)
    CATALOG.write_text(json.dumps(catalog, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
