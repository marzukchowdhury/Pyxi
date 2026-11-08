"""Add a model you have (downloaded, bought, or generated once with a 3D service) to the
library, so every user gets it for free from then on: "generate once, share with everyone".

    python -I scripts/library/add.py <file.glb|file.gltf> <id> "<Title>" --license CC-BY-4.0 \
        --credit "F-16 by Someone (Sketchfab)" --source https://... [--keywords "f-16,fighter jet"]

It is recorded in scripts/library/extra.json and built straight away (into .pyxi/library and
the app's catalog). Only add models whose licence allows use in Pyxi: CC0, CC-BY (credited),
your own, or a generation service's commercial licence. Split it into parts afterwards by
editing its entry's "parts" (see recipes.py for the rules) and building it again.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXTRA = HERE / "extra.json"
SRC = HERE.parents[1] / ".pyxi" / "library_src"
LICENCES = {"CC0-1.0", "CC-BY-4.0", "Owned", "Commercial (generated)"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("file", type=Path)
    ap.add_argument("id")
    ap.add_argument("title")
    ap.add_argument("--license", required=True, choices=sorted(LICENCES))
    ap.add_argument("--credit", required=True)
    ap.add_argument("--source", required=True)
    ap.add_argument("--keywords", default="")
    ap.add_argument("--rotate", default="0,0,0", help="degrees x,y,z to stand it upright")
    a = ap.parse_args()
    if not re.fullmatch(r"[a-z0-9_]{1,60}", a.id):
        sys.exit("id: lowercase letters, digits and _ only")
    if not a.file.is_file():
        sys.exit(f"{a.file}: no such file")
    SRC.mkdir(parents=True, exist_ok=True)
    if a.file.suffix.lower() == ".gltf":
        # A .gltf comes with its .bin and textures: copy the whole folder.
        dest = SRC / a.id
        shutil.copytree(a.file.parent, dest, dirs_exist_ok=True)
        url = "file:" + str(dest / a.file.name)
    else:
        dest = SRC / f"{a.id}.glb"
        shutil.copyfile(a.file, dest)
        url = "file:" + str(dest)
    entries = json.loads(EXTRA.read_text(encoding="utf-8")) if EXTRA.exists() else []
    entries = [e for e in entries if e["id"] != a.id]
    entries.append(
        {
            "id": a.id,
            "title": a.title,
            "url": url,
            "credit": a.credit,
            "license": a.license,
            "source": a.source,
            "keywords": [k.strip() for k in a.keywords.split(",") if k.strip()],
            "rotate": [float(v) for v in a.rotate.split(",")],
            "parts": [{"id": "body", "rest": True}],
        }
    )
    EXTRA.write_text(json.dumps(entries, indent=1) + "\n", encoding="utf-8")
    print(f"added {a.id}; building it")
    # Draco-compressed files are decompressed through the browser preview first, if needed.
    subprocess.run([sys.executable, "-I", str(HERE / "build.py"), a.id], check=False)


if __name__ == "__main__":
    main()
