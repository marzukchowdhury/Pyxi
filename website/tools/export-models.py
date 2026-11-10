"""Write the app's own gallery models out for the website's live 3D stage.

The home page plays these exactly as Pyxi does, so they are never edited by hand: when the
gallery in src/pyxi/scenes/object_gallery.py changes, run this again from the repo root:

    .venv/Scripts/python.exe website/tools/export-models.py
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from pyxi.scenes.object_gallery import OBJECT_GALLERY  # noqa: E402

OUT = ROOT / "website" / "src" / "build3d" / "models"
OUT.mkdir(parents=True, exist_ok=True)
for name, make in OBJECT_GALLERY.items():
    scene = make().model_dump(exclude_none=True, exclude_defaults=True)
    scene["id"] = name  # a stable id (the app gives each run a random one)
    scene.pop("notes", None)
    path = OUT / f"{name}.json"
    path.write_text(json.dumps(scene, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"{path.relative_to(ROOT)}: {len(scene['parts'])} parts, {len(scene.get('beats', []))} steps")
