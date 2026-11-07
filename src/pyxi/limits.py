"""Limits on outward actions per hour: emails, posts, invites and bookings.

However the request came about (a routine, a long errand, a model talked into something by what
it read), Pyxi never sends more than a handful of things an hour on its own. When one limit is
reached it stops and asks the user on a card before the next one; they can carry on, or not.

The limits are settings (config.ActionLimits): ``PYXI_LIMITS__EMAILS_PER_HOUR=10`` and so on.
What was sent in the last hour is kept in ``action_counts.json``, so a restart doesn't reset it.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

FILE = "action_counts.json"
HOUR = 3600

# Outgoing.channel -> (config field, how the card says it)
KINDS = {
    "email": ("emails_per_hour", "emails"),
    "post": ("posts_per_hour", "posts"),
    "invite": ("invites_per_hour", "invites"),
    "booking": ("bookings_per_hour", "bookings"),
}


class Limits:
    def __init__(self, data_dir: Path, config: object) -> None:
        self.path = Path(data_dir) / FILE
        self.config = config

    def _load(self, now: float) -> dict[str, list[float]]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        return {
            k: [t for t in v if now - float(t) < HOUR]
            for k, v in (raw.items() if isinstance(raw, dict) else [])
            if k in KINDS and isinstance(v, list)
        }

    def limit(self, kind: str) -> int | None:
        field = KINDS.get(kind, (None, ""))[0]
        return int(getattr(self.config, field)) if field else None

    def count(self, kind: str, now: float | None = None) -> int:
        now = time.time() if now is None else now
        return len(self._load(now).get(kind, []))

    def reached(self, kind: str, now: float | None = None) -> str:
        """The card's sentence when this kind is at its limit for the hour, else ""."""
        limit = self.limit(kind)
        if limit is None:
            return ""
        n = self.count(kind, now)
        if n < limit:
            return ""
        what = KINDS[kind][1]
        return (
            f"Pyxi has already sent {n} {what} in the last hour, its limit "
            f"({limit} an hour). It stopped to check with you before doing more."
        )

    def record(self, kind: str, now: float | None = None) -> None:
        if kind not in KINDS:
            return
        now = time.time() if now is None else now
        data = self._load(now)
        data.setdefault(kind, []).append(now)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data), encoding="utf-8")
