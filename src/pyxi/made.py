"""Files Pyxi's teammates made, in the data dir as ``made.json``.

The desktop app opens a file, shows it in Explorer or lets you drag it out of a result card only
when Pyxi made it: inside Pyxi's own folders, or listed here. Pixel and Ledger save new files
beside the original (on the Desktop, in Downloads...), so without this list "Open file" on their
results would do nothing, and with a looser rule a compromised page could open any file.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from pathlib import Path

FILE = "made.json"
MAX = 500  # newest kept; old entries for files long gone don't matter


def load(data_dir: Path) -> list[str]:
    try:
        items = json.loads((Path(data_dir) / FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [str(p) for p in items if isinstance(p, str)] if isinstance(items, list) else []


def record(data_dir: Path, paths: Iterable[str | Path]) -> list[str]:
    """Add files (absolute, existing ones only), newest last. Folders count too (a split's)."""
    new = []
    for p in paths:
        try:
            full = Path(p).resolve()
        except OSError:
            continue
        if full.exists():
            new.append(str(full))
    if not new:
        return load(data_dir)
    keep = [p for p in load(data_dir) if p.lower() not in {n.lower() for n in new}] + new
    keep = keep[-MAX:]
    folder = Path(data_dir)
    folder.mkdir(parents=True, exist_ok=True)
    tmp = folder / f"{FILE}.tmp"
    tmp.write_text(json.dumps(keep, indent=0), encoding="utf-8")
    os.replace(tmp, folder / FILE)  # the app reads it at any moment: never half-written
    return keep
