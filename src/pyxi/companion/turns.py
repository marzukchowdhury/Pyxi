"""Turn log: every conversation turn with what Pyxi saw, did and spent (the companion's trajectory)."""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS turns (
    id TEXT PRIMARY KEY, ts REAL, user_text TEXT, app TEXT, window TEXT, screen_text TEXT,
    image_path TEXT, tool_calls TEXT, said TEXT, rounds INTEGER, input_tokens INTEGER,
    output_tokens INTEGER, cost_usd REAL, latency_ms INTEGER, error TEXT
);
CREATE TABLE IF NOT EXISTS spend (ts REAL, source TEXT, cost_usd REAL);
"""


class TurnStore:
    def __init__(self, db_path: Path, turns_dir: Path) -> None:
        self.db_path, self.turns_dir = Path(db_path), Path(turns_dir)
        self.turns_dir.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def prune(self, days: float) -> int:
        """Forget turns older than ``days``, with their screenshots. Returns how many went."""
        import time

        cutoff = time.time() - days * 86400
        with self._conn() as c:
            old = c.execute("SELECT id, image_path FROM turns WHERE ts < ?", (cutoff,)).fetchall()
            c.execute("DELETE FROM turns WHERE ts < ?", (cutoff,))
        for _id, image in old:
            if image:
                Path(image).unlink(missing_ok=True)
        return len(old)

    def save_image(self, turn_id: str, jpeg: bytes) -> str:
        path = self.turns_dir / f"{turn_id}.jpg"
        path.write_bytes(jpeg)
        return str(path)

    def record(self, turn: dict[str, Any]) -> None:
        cols = [
            "id",
            "ts",
            "user_text",
            "app",
            "window",
            "screen_text",
            "image_path",
            "tool_calls",
            "said",
            "rounds",
            "input_tokens",
            "output_tokens",
            "cost_usd",
            "latency_ms",
            "error",
        ]
        row = {**{c: None for c in cols}, **turn}
        row["tool_calls"] = json.dumps(row["tool_calls"] or [], default=str)
        with self._conn() as c:
            c.execute(
                f"INSERT OR REPLACE INTO turns ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                [row[c] for c in cols],
            )
        self.add_spend("turn", row["cost_usd"] or 0.0)

    def add_spend(self, source: str, cost: float) -> None:
        if cost:
            with self._conn() as c:
                c.execute(
                    "INSERT INTO spend (ts, source, cost_usd) VALUES (?,?,?)",
                    (time.time(), source, cost),
                )

    def spent_today(self) -> float:
        midnight = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        with self._conn() as c:
            return float(
                c.execute(
                    "SELECT COALESCE(SUM(cost_usd), 0) FROM spend WHERE ts >= ?", (midnight,)
                ).fetchone()[0]
            )

    def recent(self, limit: int = 30) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM turns ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
        return [{**dict(r), "tool_calls": json.loads(r["tool_calls"] or "[]")} for r in rows]
