"""Find Smithsonian 3D scans that are CC0, object by object.

The Open Access API (api.si.edu, the public DEMO_KEY) says which records are CC0 and links
each to its 3D package; the 3D API (3d-api.si.edu) lists that package's .glb files.

    python -I scripts/library/smithsonian.py "wright flyer" "triceratops" ...
"""

from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request

AGENT = {"User-Agent": "Pyxi-library-builder"}


def _get(url: str) -> dict:
    with urllib.request.urlopen(urllib.request.Request(url, headers=AGENT), timeout=60) as r:
        return json.loads(r.read())


def cc0_packages(term: str, rows: int = 30) -> list[tuple[str, str]]:
    """(title, package id) of records about ``term`` whose 3D media is CC0."""
    q = urllib.parse.quote(f"3d_package AND {term}")
    d = _get(f"https://api.si.edu/openaccess/api/v1.0/search?q={q}&rows={rows}&api_key=DEMO_KEY")
    out = []
    for row in d.get("response", {}).get("rows", []):
        dn = row.get("content", {}).get("descriptiveNonRepeating", {})
        for m in dn.get("online_media", {}).get("media", []):
            content = str(m.get("content", ""))
            if "3d_package:" in content and m.get("usage", {}).get("access") == "CC0":
                out.append((row.get("title", ""), content.split("3d_package:")[1].split("/")[0]))
    return out


def glb_for(package: str, title: str, quality: str = "Medium") -> str | None:
    """The package's web .glb at the given quality (Medium: about 2k textures)."""
    q = urllib.parse.quote(title[:60])
    d = _get(f"https://3d-api.si.edu/api/v1.0/content/file/search?q={q}&file_type=glb&rows=200")
    best = None
    for row in d.get("rows", []):
        c = row.get("content", {})
        if package in c.get("uri", "") and c.get("usage") == "Web3D":
            if c.get("quality") == quality:
                return c["uri"].split("/https://", 1)[-1].join(["https://", ""]) if "/https://" in c["uri"] else c["uri"]
            best = best or c["uri"]
    return best


if __name__ == "__main__":
    for term in sys.argv[1:]:
        hits = cc0_packages(term)
        print(f"{term}: {len(hits)} CC0")
        for title, pkg in hits[:4]:
            print(f"   {title[:70]} | {pkg} | {glb_for(pkg, title)}")
