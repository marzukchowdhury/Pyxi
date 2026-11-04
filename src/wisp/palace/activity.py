"""The activity log: a timeline of what happened, for causal explanations.

Events come from four places:

* ``window``: the user switched to another app or document;
* ``focus``: keyboard focus moved to a button, field, tab or menu (a proxy for clicks);
* ``turn``: the user asked Wisp something;
* ``job``: a teammate did something in its browser.

Each event points at the spatial-memory snapshot that was current when it happened, so a
causal chain can be rebuilt in 3D from the actual interface pieces involved.

Pins (persistent notes) live here too: a note attached to an element in an app reappears
on that element whenever the app is open again.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, kind TEXT NOT NULL,
    app TEXT, title TEXT, name TEXT, role TEXT, x INTEGER, y INTEGER, w INTEGER, h INTEGER,
    snapshot_id INTEGER, detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_activity_ts ON activity(ts DESC);
CREATE TABLE IF NOT EXISTS pins (
    id INTEGER PRIMARY KEY AUTOINCREMENT, created_at REAL, app TEXT, element_name TEXT, element_role TEXT,
    note TEXT, color TEXT
);
"""


@dataclass
class Event:
    id: int
    ts: float
    kind: str
    app: str
    title: str
    name: str
    role: str
    rect: tuple[int, int, int, int] | None
    snapshot_id: int | None
    detail: str

    def headline(self) -> str:
        if self.kind == "window":
            return f"Opened {self.app}: {self.title[:60]}"
        if self.kind == "focus":
            verb = {
                "Edit": "Typed in",
                "Document": "Worked in",
                "TabItem": "Switched to tab",
                "MenuItem": "Opened menu",
            }.get(self.role, "Clicked")
            return f'{verb} "{self.name[:50]}"' if self.name else f"{verb} a {self.role.lower()}"
        if self.kind == "turn":
            return f'Asked Wisp: "{self.detail[:60]}"'
        return self.detail[:80] or self.kind

    def describe(self) -> str:
        clock = datetime.fromtimestamp(self.ts).strftime("%H:%M:%S")
        return f"#{self.id} {clock} [{self.app}] {self.headline()}"


@dataclass
class Pin:
    id: int
    app: str
    element_name: str
    element_role: str
    note: str
    color: str


class ActivityLog:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)
        self._last_focus: tuple[str, str, str] | None = None
        self._last_window: tuple[str, str] | None = None

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------ writing
    def log(
        self,
        kind: str,
        *,
        app: str = "",
        title: str = "",
        name: str = "",
        role: str = "",
        rect: tuple[int, int, int, int] | None = None,
        snapshot_id: int | None = None,
        detail: str = "",
        ts: float | None = None,
    ) -> int:
        x, y, w, h = rect if rect else (None, None, None, None)
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO activity (ts, kind, app, title, name, role, x, y, w, h, snapshot_id, detail) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    ts or time.time(),
                    kind,
                    app,
                    title,
                    name,
                    role,
                    x,
                    y,
                    w,
                    h,
                    snapshot_id,
                    detail[:500],
                ),
            )
            return int(cur.lastrowid)

    def focus_changed(
        self,
        app: str,
        title: str,
        name: str,
        role: str,
        rect: tuple[int, int, int, int],
        snapshot_id: int | None,
    ) -> int | None:
        """Log a focus event only when focus really moved (polling sees the same element a lot)."""
        key = (app, name, role)
        if key == self._last_focus or not (name or role in {"Edit", "Document"}):
            return None
        self._last_focus = key
        return self.log(
            "focus", app=app, title=title, name=name, role=role, rect=rect, snapshot_id=snapshot_id
        )

    def window_changed(self, app: str, title: str, snapshot_id: int | None) -> int | None:
        key = (app, title)
        if key == self._last_window or not (app or title):
            return None
        self._last_window = key
        return self.log("window", app=app, title=title, snapshot_id=snapshot_id)

    # ------------------------------------------------------------------ reading
    def recent(self, minutes: float = 30, limit: int = 60) -> list[Event]:
        since = time.time() - minutes * 60
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM activity WHERE ts >= ? ORDER BY ts DESC LIMIT ?", (since, limit)
            ).fetchall()
        return [_event(r) for r in reversed(rows)]

    def get(self, ids: list[int]) -> list[Event]:
        if not ids:
            return []
        with self._conn() as c:
            rows = c.execute(
                f"SELECT * FROM activity WHERE id IN ({','.join('?' * len(ids))})", ids
            ).fetchall()
        by_id = {r["id"]: _event(r) for r in rows}
        return [by_id[i] for i in ids if i in by_id]

    def forget_all(self) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM activity")
        self._last_focus = self._last_window = None

    # ------------------------------------------------------------------ pins
    def add_pin(self, app: str, name: str, role: str, note: str, color: str = "#fbbf24") -> Pin:
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO pins (created_at, app, element_name, element_role, note, color) VALUES (?,?,?,?,?,?)",
                (time.time(), app, name, role, note[:200], color),
            )
        return Pin(int(cur.lastrowid), app, name, role, note[:200], color)

    def pins_for(self, app: str) -> list[Pin]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM pins WHERE lower(app) = lower(?)", (app,)).fetchall()
        return [
            Pin(r["id"], r["app"], r["element_name"], r["element_role"], r["note"], r["color"])
            for r in rows
        ]

    def remove_pin(self, pin_id: int) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM pins WHERE id = ?", (pin_id,))


def _event(r: sqlite3.Row) -> Event:
    rect = (r["x"], r["y"], r["w"], r["h"]) if r["x"] is not None else None
    return Event(
        r["id"],
        r["ts"],
        r["kind"],
        r["app"] or "",
        r["title"] or "",
        r["name"] or "",
        r["role"] or "",
        rect,
        r["snapshot_id"],
        r["detail"] or "",
    )
