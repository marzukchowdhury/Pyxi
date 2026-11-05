"""Spatial memory: what was on screen, where, and when.

Every capture (on each turn, plus ambient captures when the foreground window changes)
stores a *snapshot*: app, window title and the named UI elements with their rectangles.
By default no pixels are kept, only metadata. That lets Blip answer questions like
"where was that export button?" or "what was I looking at in Figma before lunch?", and
point straight at the element again if the same app is open.

Search uses SQLite FTS5 (BM25) over element names, window titles and app names, with a
recency boost.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from blip.screen import ScreenContext

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    app TEXT, title TEXT,
    fingerprint TEXT,
    element_count INTEGER,
    thumb_path TEXT
);
CREATE TABLE IF NOT EXISTS snap_elements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
    name TEXT, role TEXT, x INTEGER, y INTEGER, w INTEGER, h INTEGER,
    depth INTEGER DEFAULT 0, parent_id INTEGER
);
CREATE INDEX IF NOT EXISTS idx_snap_ts ON snapshots(ts DESC);
CREATE VIRTUAL TABLE IF NOT EXISTS snap_fts USING fts5(name, role, app, title, element_id UNINDEXED, snapshot_id UNINDEXED);
"""


@dataclass
class Recall:
    snapshot_id: int
    ts: float
    app: str
    title: str
    name: str
    role: str
    rect: tuple[int, int, int, int]

    def describe(self, now: float | None = None) -> str:
        ago = _ago((now or time.time()) - self.ts)
        x, y, w, h = self.rect
        return f'{self.role} "{self.name}" in {self.app} ({self.title[:60]!r}), {ago}, at ({x},{y},{w}x{h})'


def _ago(seconds: float) -> str:
    if seconds < 90:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)} min ago"
    if seconds < 86400:
        return f"{seconds / 3600:.1f} h ago"
    return f"{seconds / 86400:.1f} days ago"


class SpatialMemory:
    def __init__(
        self, db_path: Path, *, retention_hours: float = 24.0, thumbs_dir: Path | None = None
    ) -> None:
        self.thumbs_dir = Path(thumbs_dir) if thumbs_dir else None
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.retention_s = retention_hours * 3600
        with self._conn() as c:
            c.executescript(SCHEMA)
        self._last_fingerprint: str | None = None

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def fingerprint(ctx: ScreenContext) -> str:
        h = hashlib.sha1(f"{ctx.window.app}|{ctx.window.title}".encode())
        for e in ctx.elements[:80]:
            h.update(f"{e.name}|{e.role}".encode())
        return h.hexdigest()[:16]

    def record(
        self, ctx: ScreenContext, *, thumb_path: str | None = None, force: bool = False
    ) -> int | None:
        """Store a snapshot unless it's private, empty or identical to the previous one."""
        if ctx.excluded or not ctx.elements:
            return None
        fp = self.fingerprint(ctx)
        if fp == self._last_fingerprint and not force:
            return None
        self._last_fingerprint = fp
        # Keep named elements plus big unnamed containers (they give the 3D reconstruction its shape).
        kept = [
            e
            for e in ctx.elements
            if not e.is_password and (e.name or e.rect[2] * e.rect[3] > 40_000)
        ]
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO snapshots (ts, app, title, fingerprint, element_count, thumb_path) VALUES (?,?,?,?,?,?)",
                (ctx.captured_at, ctx.window.app, ctx.window.title, fp, len(kept), None),
            )
            sid = cur.lastrowid
            if thumb_path is None and self.thumbs_dir is not None and ctx.screenshot is not None:
                thumb_path = self._save_thumb(ctx, sid)
                c.execute("UPDATE snapshots SET thumb_path = ? WHERE id = ?", (thumb_path, sid))
            row_ids: dict[int, int] = {}
            for e in kept:
                x, y, w, h = e.rect
                ecur = c.execute(
                    "INSERT INTO snap_elements (snapshot_id, name, role, x, y, w, h, depth, parent_id) VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        sid,
                        e.name,
                        e.role,
                        x,
                        y,
                        w,
                        h,
                        e.depth,
                        row_ids.get(e.parent) if e.parent is not None else None,
                    ),
                )
                row_ids[e.id] = ecur.lastrowid
                if e.name:
                    c.execute(
                        "INSERT INTO snap_fts (name, role, app, title, element_id, snapshot_id) VALUES (?,?,?,?,?,?)",
                        (e.name, e.role, ctx.window.app, ctx.window.title, ecur.lastrowid, sid),
                    )
        return sid

    def _save_thumb(self, ctx: ScreenContext, sid: int) -> str | None:
        """A downscaled screenshot (opt-in) so time travel can texture its ghosts with real pixels."""
        assert self.thumbs_dir is not None and ctx.screenshot is not None
        self.thumbs_dir.mkdir(parents=True, exist_ok=True)
        img = ctx.screenshot.copy()
        for e in ctx.elements:  # never keep pixels of password fields
            if e.is_password:
                x, y, w, h = (
                    e.rect[0] - ctx.origin[0],
                    e.rect[1] - ctx.origin[1],
                    e.rect[2],
                    e.rect[3],
                )
                img.paste((20, 20, 20), (x, y, x + w, y + h))
        scale = min(1.0, 1280 / img.width)
        img = img.resize((int(img.width * scale), int(img.height * scale)))
        path = self.thumbs_dir / f"snap_{sid}.jpg"
        img.convert("RGB").save(path, quality=70)
        # The thumbnail's coordinate frame: origin + scale, so crops can be cut from it later.
        meta = path.with_suffix(".json")
        meta.write_text(json.dumps({"origin": list(ctx.origin), "scale": scale}))
        return str(path)

    def snapshot(self, sid: int) -> dict | None:
        with self._conn() as c:
            snap = c.execute("SELECT * FROM snapshots WHERE id = ?", (sid,)).fetchone()
            if not snap:
                return None
            elements = c.execute(
                "SELECT * FROM snap_elements WHERE snapshot_id = ? ORDER BY id", (sid,)
            ).fetchall()
        return {**dict(snap), "elements": [dict(e) for e in elements]}

    def neighbours(self, sid: int, n: int = 12) -> list[dict]:
        """Snapshots around ``sid`` in time, for the time-travel scrubber."""
        with self._conn() as c:
            ts = c.execute("SELECT ts FROM snapshots WHERE id = ?", (sid,)).fetchone()
            if not ts:
                return []
            before = c.execute(
                "SELECT id, ts, app, title FROM snapshots WHERE ts < ? ORDER BY ts DESC LIMIT ?",
                (ts[0], n),
            ).fetchall()
            after = c.execute(
                "SELECT id, ts, app, title FROM snapshots WHERE ts >= ? ORDER BY ts ASC LIMIT ?",
                (ts[0], n + 1),
            ).fetchall()
        return [dict(r) for r in reversed(before)] + [dict(r) for r in after]

    def find_snapshots(
        self, query: str, *, start: float | None = None, end: float | None = None, k: int = 5
    ) -> list[dict]:
        """Best-matching snapshots for "the pricing page" within an optional time window."""
        terms = [
            t
            for t in "".join(ch if ch.isalnum() else " " for ch in query.lower()).split()
            if len(t) > 2
        ][:12]
        where, params = ["1=1"], []
        if start is not None:
            where.append("s.ts >= ?")
            params.append(start)
        if end is not None:
            where.append("s.ts <= ?")
            params.append(end)
        with self._conn() as c:
            if not terms:
                rows = c.execute(
                    f"SELECT s.id, s.ts, s.app, s.title FROM snapshots s WHERE {' AND '.join(where)} ORDER BY s.ts DESC LIMIT ?",
                    [*params, k],
                ).fetchall()
                return [dict(r) for r in rows]
            match = " OR ".join(f'"{t}"*' for t in terms)
            matches = c.execute(
                f"""SELECT f.snapshot_id AS sid, bm25(snap_fts) AS score FROM snap_fts f
                    JOIN snapshots s ON s.id = f.snapshot_id
                    WHERE snap_fts MATCH ? AND {" AND ".join(where)} LIMIT 2000""",
                [match, *params],
            ).fetchall()
            # Rank snapshots by how many of their elements matched, then by best BM25, then recency.
            agg: dict[int, list[float]] = {}
            for m in matches:
                agg.setdefault(m["sid"], []).append(m["score"])
            ranked = sorted(agg, key=lambda sid: (-len(agg[sid]), min(agg[sid]), -sid))[:k]
            if not ranked:
                return []
            snaps = {
                r["id"]: r
                for r in c.execute(
                    f"SELECT id, ts, app, title FROM snapshots WHERE id IN ({','.join('?' * len(ranked))})",
                    ranked,
                )
            }
        rows = [snaps[sid] for sid in ranked]
        return [dict(r) for r in rows]

    def recall(self, query: str, k: int = 5, *, app: str | None = None) -> list[Recall]:
        terms = [
            t
            for t in "".join(ch if ch.isalnum() else " " for ch in query.lower()).split()
            if len(t) > 1
        ][:12]
        if not terms:
            return []
        match = " OR ".join(f'"{t}"*' for t in terms)
        sql = """SELECT e.name, e.role, e.x, e.y, e.w, e.h, s.id AS sid, s.ts, s.app, s.title, bm25(snap_fts) AS score
                 FROM snap_fts f JOIN snap_elements e ON e.id = f.element_id JOIN snapshots s ON s.id = f.snapshot_id
                 WHERE snap_fts MATCH ?"""
        params: list[object] = [match]
        if app:
            sql += " AND lower(s.app) = lower(?)"
            params.append(app)
        sql += " ORDER BY score LIMIT 200"
        with self._conn() as c:
            rows = c.execute(sql, params).fetchall()
        now = time.time()
        # BM25 is lower-is-better; add a gentle recency boost and dedupe same element across snapshots.
        ranked = sorted(rows, key=lambda r: r["score"] - 2.0 / (1 + (now - r["ts"]) / 600))
        seen: set[tuple[str, str, str]] = set()
        out: list[Recall] = []
        for r in ranked:
            key = (r["app"], r["name"], r["role"])
            if key in seen:
                continue
            seen.add(key)
            out.append(
                Recall(
                    r["sid"],
                    r["ts"],
                    r["app"],
                    r["title"],
                    r["name"],
                    r["role"],
                    (r["x"], r["y"], r["w"], r["h"]),
                )
            )
            if len(out) >= k:
                break
        return out

    def timeline(self, limit: int = 30) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT id, ts, app, title, element_count FROM snapshots ORDER BY ts DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def prune(self) -> int:
        cutoff = time.time() - self.retention_s
        with self._conn() as c:
            ids = [r[0] for r in c.execute("SELECT id FROM snapshots WHERE ts < ?", (cutoff,))]
            for sid in ids:
                c.execute("DELETE FROM snap_fts WHERE snapshot_id = ?", (sid,))
                c.execute("DELETE FROM snap_elements WHERE snapshot_id = ?", (sid,))
            c.execute("DELETE FROM snapshots WHERE ts < ?", (cutoff,))
        return len(ids)

    def forget_all(self) -> None:
        with self._conn() as c:
            c.executescript(
                "DELETE FROM snap_fts; DELETE FROM snap_elements; DELETE FROM snapshots;"
            )
        self._last_fingerprint = None
