"""Standing permissions ("Always allow"), which expire.

A card that offers "Always allow" saves a grant here. A grant lasts ``days`` (30 by default,
``PYXI_GRANT_DAYS`` to change it), Settings lists the live ones with a Revoke button, and an
expired one simply asks again. Kept in ``grants.json`` in the data folder.

Even a live grant never covers a task that comes after Pyxi read mail, a page or a document
(that asks on a fresh card), nor anything that sends, buys, books, deletes or posts.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

FILE = "grants.json"
LEGACY_HANDS = "hands.json"  # before grants expired: {"always": true}, forever
DAY = 86400

LABELS = {
    "hands": "Use your mouse and keyboard without asking first",
}


def days() -> float:
    try:
        return max(0.01, float(os.environ.get("PYXI_GRANT_DAYS", "30")))
    except ValueError:
        return 30.0


def _load(data_dir: Path) -> dict[str, dict[str, float]]:
    data_dir = Path(data_dir)
    try:
        raw = json.loads((data_dir / FILE).read_text(encoding="utf-8"))
        grants = {k: v for k, v in raw.items() if k in LABELS and isinstance(v, dict)}
    except (OSError, ValueError, AttributeError):
        grants = {}
    legacy = data_dir / LEGACY_HANDS
    if legacy.exists():  # an old forever-grant becomes a 30-day one, from now
        try:
            if json.loads(legacy.read_text(encoding="utf-8")).get("always"):
                grants.setdefault("hands", _new())
        except (OSError, ValueError, AttributeError):
            pass
        _save(data_dir, grants)
        legacy.unlink(missing_ok=True)
    return grants


def _new(now: float | None = None) -> dict[str, float]:
    now = time.time() if now is None else now
    return {"granted": now, "expires": now + days() * DAY}


def _save(data_dir: Path, grants: dict[str, dict[str, float]]) -> None:
    path = Path(data_dir) / FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(grants, indent=1), encoding="utf-8")


def active(data_dir: Path, kind: str, now: float | None = None) -> bool:
    g = _load(data_dir).get(kind)
    return bool(g) and float(g.get("expires", 0)) > (time.time() if now is None else now)


def grant(data_dir: Path, kind: str, now: float | None = None) -> dict[str, float]:
    if kind not in LABELS:
        raise ValueError(f"unknown grant {kind!r}")
    grants = _load(data_dir)
    grants[kind] = _new(now)
    _save(data_dir, grants)
    return grants[kind]


def revoke(data_dir: Path, kind: str) -> bool:
    grants = _load(data_dir)
    gone = grants.pop(kind, None) is not None
    _save(data_dir, grants)
    return gone


def public(data_dir: Path, now: float | None = None) -> list[dict[str, Any]]:
    """The live grants, for Settings (expired ones are dropped)."""
    now = time.time() if now is None else now
    grants = _load(data_dir)
    live = {k: v for k, v in grants.items() if float(v.get("expires", 0)) > now}
    if live != grants:
        _save(data_dir, live)
    return [
        {"kind": k, "label": LABELS[k], "granted": v["granted"], "expires": v["expires"]}
        for k, v in sorted(live.items())
    ]
