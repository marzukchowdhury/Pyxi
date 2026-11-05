"""Turn "last Tuesday afternoon" or "20 minutes ago" into a time range."""

from __future__ import annotations

import re
from datetime import datetime, timedelta

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
PARTS = {
    "morning": (5, 12),
    "afternoon": (12, 17),
    "evening": (17, 22),
    "night": (20, 24),
    "lunch": (11, 14),
}
UNITS = {"minute": 60, "min": 60, "hour": 3600, "day": 86400, "week": 604800}


def parse_when(text: str, now: datetime | None = None) -> tuple[float, float] | None:
    """Return (start_ts, end_ts) for the time expression in ``text``, or None if there isn't one."""
    now = now or datetime.now()
    t = text.lower()
    day: datetime | None = None

    if m := re.search(r"(\d+|an?|a few|couple of)\s*(minute|min|hour|day|week)s?\s+ago", t):
        qty = {"a": 1, "an": 1, "a few": 3, "couple of": 2}.get(m.group(1)) or int(m.group(1))
        center = now - timedelta(seconds=qty * UNITS[m.group(2)])
        spread = max(300, qty * UNITS[m.group(2)] * 0.35)
        return (center - timedelta(seconds=spread)).timestamp(), (
            center + timedelta(seconds=spread)
        ).timestamp()
    if "just now" in t or "a moment ago" in t:
        return (now - timedelta(minutes=5)).timestamp(), now.timestamp()
    if "earlier today" in t or "earlier" in t.split():
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return start.timestamp(), (now - timedelta(minutes=5)).timestamp()

    if "yesterday" in t:
        day = now - timedelta(days=1)
    elif "today" in t or re.search(r"\bthis (morning|afternoon|evening|night)\b", t):
        day = now
    else:
        for i, name in enumerate(WEEKDAYS):
            if re.search(rf"\b{name}\b", t):
                back = (now.weekday() - i) % 7 or (7 if "last" in t else 0)
                day = now - timedelta(days=back)
                break
    if day is None:
        return None
    start_h, end_h = next((PARTS[p] for p in PARTS if p in t), (0, 24))
    start = day.replace(hour=start_h, minute=0, second=0, microsecond=0)
    end = day.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(hours=end_h)
    if day.date() == now.date():
        end = min(end, now)
    return start.timestamp(), end.timestamp()


def strip_when(text: str) -> str:
    """Remove time words so the rest can be used as a search query."""
    t = re.sub(
        r"(\d+|an?|a few|couple of)\s*(minute|min|hour|day|week)s?\s+ago", " ", text, flags=re.I
    )
    words = [
        "yesterday",
        "today",
        "earlier",
        "last",
        "this",
        "on",
        "at",
        "just now",
        "ago",
        *WEEKDAYS,
        *PARTS,
    ]
    t = re.sub(r"\b(" + "|".join(words) + r")\b", " ", t, flags=re.I)
    return " ".join(t.split())
