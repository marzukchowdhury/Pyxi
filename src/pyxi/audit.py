"""The activity log: everything Pyxi did on the user's behalf, for them to read, with undo.

Every email, post and invite sent, every booking started, every button Scout pressed after a
card, every mouse and keyboard step, every file moved, renamed, changed or saved goes here, with
the time and the teammate. Where an undo exists it's one click in the panel's Activity tab:

- a tidy: the files go back where they were (tidy.undo_log);
- Ditto's renames and moves: back to their old names (ditto.undo_files);
- a Patch fix: the file goes back to the backup Patch kept. The fixed version is kept beside it
  as ``name.pyxi-undone``, so nothing is deleted.

Stored on this PC only, in ``audit.jsonl`` in the data folder, and forgotten after the same 30
days as conversations. Nothing in it is sent anywhere.
"""

from __future__ import annotations

import json
import logging
import secrets
import shutil
import threading
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

KINDS = ("sent", "booked", "clicked", "moved", "wrote", "undid")


class AuditLog:
    def __init__(self, path: Path, retention_days: float = 30.0) -> None:
        self.path = Path(path)
        self.retention_s = retention_days * 86400
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ writing
    def add(
        self,
        kind: str,
        what: str,
        *,
        agent: str = "Pyxi",
        detail: str = "",
        undo: dict[str, Any] | None = None,
        now: float | None = None,
    ) -> dict[str, Any]:
        entry = {
            "id": "act_" + secrets.token_hex(5),
            "ts": time.time() if now is None else now,
            "kind": kind if kind in KINDS else "wrote",
            "agent": agent,
            "what": str(what)[:300],
            "detail": str(detail)[:600],
            "undo": undo,
            "undone": False,
        }
        try:
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(entry) + "\n")
        except OSError as exc:  # the log must never stop the work itself
            log.warning("couldn't write the activity log: %s", exc)
        return entry

    # ------------------------------------------------------------------ reading
    def _all(self, now: float | None = None) -> list[dict[str, Any]]:
        now = time.time() if now is None else now
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out = []
        for line in lines:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if isinstance(e, dict) and now - float(e.get("ts", 0)) < self.retention_s:
                out.append(e)
        if len(out) != len(lines):  # forget what's past the retention (and broken lines)
            self._rewrite(out)
        return out

    def _rewrite(self, entries: list[dict[str, Any]]) -> None:
        with self._lock:
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")
            tmp.replace(self.path)

    def recent(self, limit: int = 200, now: float | None = None) -> list[dict[str, Any]]:
        """Newest first, for the panel. ``can_undo`` says whether the button shows."""
        entries = self._all(now)[-limit:][::-1]
        return [{**e, "can_undo": bool(e.get("undo")) and not e.get("undone")} for e in entries]

    # ------------------------------------------------------------------ undo
    def undo(self, entry_id: str) -> str:
        entries = self._all()
        entry = next((e for e in entries if e.get("id") == entry_id), None)
        if entry is None:
            return "That's no longer in the activity log."
        if entry.get("undone"):
            return "That was already undone."
        how = entry.get("undo") or {}
        try:
            message = _undo(how)
        except Exception as exc:  # a file in use, moved by hand since: say so, change nothing
            log.warning("undo failed: %s", exc)
            return f"I couldn't undo that: {exc}"
        entry["undone"] = True
        self._rewrite(entries)
        self.add("undid", f"Undid: {entry['what']}", agent="Pyxi", detail=message)
        return message


def _undo(how: dict[str, Any]) -> str:
    kind = how.get("type")
    if kind == "tidy":
        from pyxi import tidy

        return tidy.undo_log(Path(how["log"]))
    if kind == "ditto":
        from pyxi.agents import ditto

        n = ditto.undo_files(Path(how["log"]))
        return f"Put {n} file{'s' if n != 1 else ''} back as they were."
    if kind == "patch":
        path, backup = Path(how["path"]), Path(how["backup"])
        if not backup.exists():
            raise RuntimeError(f"the backup {backup.name} isn't there any more")
        kept = path.with_name(path.name + ".pyxi-undone")
        n = 1
        while kept.exists():
            n += 1
            kept = path.with_name(f"{path.name}.pyxi-undone{n}")
        if path.exists():
            shutil.move(str(path), str(kept))
        shutil.move(str(backup), str(path))
        return f"{path.name} is back to how it was. The fixed version is kept as {kept.name}."
    raise RuntimeError("there's no undo for this")
