"""Routines: things Pyxi does on a schedule while it's running ("every weekday at 9, check my
email for anything from my professor and tell me").

A routine is a task and a schedule. When it's due and Pyxi isn't busy, Pyxi runs the task as
an ordinary turn, so it uses the same tools, approvals and daily budget as anything you ask.
A routine can also be a one-off on a date (``on``), a plain reminder that shows a notification
without a model call (``kind="remind"``), or one of Pyxi's own quiet checks (``kind="returns"``,
the daily look for new orders). Routines are saved in ``.pyxi/routines.json``. They only run while Pyxi is open; this is not a
cloud service.
"""

from __future__ import annotations

import json
import re
import secrets
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

DAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
Days = Literal["every day", "weekdays", "weekends", "mon", "tue", "wed", "thu", "fri", "sat", "sun"]


class Routine(BaseModel):
    id: str = Field(default_factory=lambda: "rt_" + secrets.token_hex(3))
    task: str
    days: list[str] = Field(default_factory=lambda: ["every day"])
    time: str | None = None  # "HH:MM", local time
    every_minutes: int | None = None  # instead of a time of day
    enabled: bool = True
    created_at: float = Field(default_factory=lambda: datetime.now().timestamp())
    last_run: float | None = None
    next_run: float = 0.0
    on: str | None = None  # "YYYY-MM-DD": runs once on that day, then goes away
    kind: Literal["turn", "remind", "returns", "job"] = "turn"
    message: str | None = None  # what a "remind" routine shows
    key: str | None = None  # so the same reminder is never set twice

    def day_set(self) -> set[int]:
        out: set[int] = set()
        for d in self.days:
            d = d.lower().strip()
            if d == "every day":
                out |= set(range(7))
            elif d == "weekdays":
                out |= set(range(5))
            elif d == "weekends":
                out |= {5, 6}
            elif d[:3] in DAY_NAMES:
                out.add(DAY_NAMES.index(d[:3]))
        return out or set(range(7))

    def describe(self) -> str:
        if self.on:
            day = datetime.fromisoformat(self.on)
            return f"on {day:%a} {day.day} {day:%b} at {self.time or '09:00'}"
        if self.every_minutes:
            m = self.every_minutes
            when = (
                f"every {m // 60} hours"
                if m % 60 == 0 and m >= 120
                else "every hour"
                if m == 60
                else f"every {m} minutes"
            )
        else:
            days = ", ".join(self.days) if self.days != ["every day"] else "every day"
            when = f"{days} at {self.time}"
        return when

    def compute_next(self, now: datetime) -> float:
        if self.every_minutes:
            base = datetime.fromtimestamp(self.last_run) if self.last_run else now
            nxt = base + timedelta(minutes=self.every_minutes)
            return max(nxt, now + timedelta(seconds=30)).timestamp()
        hh, mm = (int(x) for x in (self.time or "09:00").split(":"))
        if self.on:
            at = datetime.fromisoformat(self.on).replace(hour=hh, minute=mm)
            return max(at, now + timedelta(seconds=30)).timestamp()
        days = self.day_set()
        for offset in range(0, 8):
            day = (now + timedelta(days=offset)).replace(
                hour=hh, minute=mm, second=0, microsecond=0
            )
            if day.weekday() in days and day > now:
                return day.timestamp()
        return (now + timedelta(days=1)).timestamp()


def parse_time(text: str) -> str | None:
    """'9', '9:30', '9am', '6:15 pm', '18:00' -> 'HH:MM'."""
    m = re.match(r"^\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*$", text.lower())
    if not m:
        return None
    h, mins, ampm = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if ampm == "pm" and h < 12:
        h += 12
    if ampm == "am" and h == 12:
        h = 0
    if not (0 <= h < 24 and 0 <= mins < 60):
        return None
    return f"{h:02d}:{mins:02d}"


class RoutineStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.items: list[Routine] = self._load()

    def _load(self) -> list[Routine]:
        try:
            return [
                Routine.model_validate(r) for r in json.loads(self.path.read_text(encoding="utf-8"))
            ]
        except (OSError, ValueError):
            return []

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps([r.model_dump() for r in self.items], indent=2), encoding="utf-8"
        )

    def add(
        self,
        task: str,
        *,
        days: list[str] | None = None,
        time: str | None = None,
        every_minutes: int | None = None,
        now: datetime | None = None,
        on: str | None = None,
        kind: str = "turn",
        message: str | None = None,
        key: str | None = None,
    ) -> Routine:
        now = now or datetime.now()
        if every_minutes is not None:
            every_minutes = max(
                15, min(int(every_minutes), 7 * 24 * 60)
            )  # no faster than every 15 minutes
            time = None
        else:
            time = parse_time(time or "") or "09:00"
        r = Routine(
            task=task.strip()[:500],
            days=days or ["every day"],
            time=time,
            every_minutes=every_minutes,
            on=on,
            kind=kind,  # type: ignore[arg-type]
            message=message,
            key=key,
        )
        r.next_run = r.compute_next(now)
        self.items.append(r)
        self.save()
        return r

    def remove(self, ref: str) -> Routine | None:
        ref = ref.strip().lower()
        hit = next((r for r in self.items if r.id == ref), None) or next(
            (r for r in self.items if ref and ref in r.task.lower()), None
        )
        if hit:
            self.items.remove(hit)
            self.save()
        return hit

    def due(self, now: datetime | None = None) -> list[Routine]:
        ts = (now or datetime.now()).timestamp()
        return [r for r in self.items if r.enabled and r.next_run and r.next_run <= ts]

    def has_key(self, key: str) -> bool:
        return any(r.key == key for r in self.items)

    def mark_ran(self, r: Routine, now: datetime | None = None) -> None:
        now = now or datetime.now()
        if r.on:  # a one-off is done once it has run
            if r in self.items:
                self.items.remove(r)
            self.save()
            return
        r.last_run = now.timestamp()
        r.next_run = r.compute_next(now)
        self.save()

    def public(self) -> list[dict]:
        return [
            {
                "id": r.id,
                "task": r.task,
                "when": r.describe(),
                "next_run": r.next_run,
                "last_run": r.last_run,
                "enabled": r.enabled,
                "kind": r.kind,
            }
            for r in sorted(self.items, key=lambda r: r.next_run)
        ]
