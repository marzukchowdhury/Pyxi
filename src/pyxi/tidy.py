"""Tidying a folder (the Desktop, Downloads): look, plan, ask once, move, and keep an undo.

Only ever moves files into folders inside the same folder. Never deletes, never overwrites
(a name that's taken becomes "name (2).ext"), never touches hidden or system files, and writes
every move to an undo log first, so "undo that" puts everything back exactly where it was.
"""

from __future__ import annotations

import ctypes
import json
import os
import re
import shutil
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

KNOWN = {  # Windows known-folder ids: the real place, even when OneDrive has moved it
    "desktop": "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}",
    "downloads": "{374DE290-123F-4565-9164-39C4925E467B}",
    "documents": "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}",
    "pictures": "{33E28130-4E1E-4676-835A-98395C3BC3BB}",
}
SKIP = {"desktop.ini", "thumbs.db", ".ds_store"}
BAD_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]|^\.+$')


def known_folder(name: str) -> Path | None:
    key = name.strip().lower()
    if sys.platform == "win32" and key in KNOWN:
        try:
            buf = ctypes.c_wchar_p()
            guid = uuid.UUID(KNOWN[key])
            raw = (ctypes.c_byte * 16).from_buffer_copy(guid.bytes_le)
            if ctypes.windll.shell32.SHGetKnownFolderPath(raw, 0, None, ctypes.byref(buf)) == 0:  # type: ignore[attr-defined]
                path = Path(buf.value)
                ctypes.windll.ole32.CoTaskMemFree(buf)  # type: ignore[attr-defined]
                return path
        except OSError:
            pass
    fallback = Path.home() / key.capitalize()
    return fallback if key in KNOWN and fallback.is_dir() else None


def _hidden(p: Path) -> bool:
    if p.name.lower() in SKIP or p.name.startswith("~$"):
        return True
    try:
        return bool(os.stat(p).st_file_attributes & (0x2 | 0x4))  # hidden or system
    except (AttributeError, OSError):
        return p.name.startswith(".")


def _size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} B"


def listing(folder: Path, limit: int = 250) -> str:
    """What's in it, for planning: loose files (name, size, date) and the folders already there."""
    files, dirs = [], []
    for p in sorted(folder.iterdir(), key=lambda q: q.name.lower()):
        if _hidden(p):
            continue
        (dirs if p.is_dir() else files).append(p)
    lines = [f"{folder} has {len(files)} loose files and {len(dirs)} folders."]
    if dirs:
        lines.append("Folders already there: " + ", ".join(d.name for d in dirs[:60]))
    for p in files[:limit]:
        st = p.stat()
        when = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d")
        lines.append(f"- {p.name} ({_size(st.st_size)}, {when})")
    if len(files) > limit:
        lines.append(f"(and {len(files) - limit} more)")
    return "\n".join(lines)


@dataclass
class Plan:
    folder: Path
    moves: list[tuple[str, str]] = field(default_factory=list)  # (file name, into folder)
    problems: list[str] = field(default_factory=list)

    def summary(self) -> str:
        groups: dict[str, int] = {}
        for _, into in self.moves:
            groups[into] = groups.get(into, 0) + 1
        parts = ", ".join(f"{k} ({v})" for k, v in sorted(groups.items(), key=lambda kv: -kv[1]))
        return f"Move {len(self.moves)} files into {len(groups)} folders: {parts}."


def make_plan(folder: Path, groups: list[dict]) -> Plan:
    """Check the model's plan against what's really there before anything moves."""
    plan = Plan(folder)
    seen: set[str] = set()
    for g in groups:
        into = str(g.get("folder") or "").strip().rstrip(". ")
        if not into or BAD_NAME.search(into) or len(into) > 60:
            plan.problems.append(f"skipped a group with a bad folder name {into!r}")
            continue
        for name in g.get("files") or []:
            name = str(name)
            src = folder / name
            if name in seen or "/" in name or "\\" in name:
                continue
            if not src.exists() or src.is_dir() or _hidden(src):
                plan.problems.append(f"{name!r} isn't a loose file there")
                continue
            seen.add(name)
            plan.moves.append((name, into))
    return plan


def _free(target: Path) -> Path:
    if not target.exists():
        return target
    for i in range(2, 1000):
        cand = target.with_name(f"{target.stem} ({i}){target.suffix}")
        if not cand.exists():
            return cand
    raise FileExistsError(target)


def apply(plan: Plan, log_dir: Path) -> tuple[int, list[str], Path]:
    """Move the files, logging each move before it happens. Returns (moved, failures, log)."""
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"tidy-{time.strftime('%Y%m%d-%H%M%S')}.json"
    record: dict = {"folder": str(plan.folder), "made": [], "moves": []}
    failed: list[str] = []
    for name, into in plan.moves:
        dest_dir = plan.folder / into
        if not dest_dir.exists():
            dest_dir.mkdir()
            record["made"].append(str(dest_dir))
        src, dst = plan.folder / name, _free(dest_dir / name)
        record["moves"].append([str(src), str(dst)])
        log_path.write_text(json.dumps(record, indent=1))
        try:
            shutil.move(str(src), str(dst))
        except OSError as exc:  # open in another app, permissions
            record["moves"].pop()
            failed.append(f"{name}: {exc.strerror or exc}")
    log_path.write_text(json.dumps(record, indent=1))
    return len(record["moves"]), failed, log_path


def undo_last(log_dir: Path) -> str:
    logs = sorted(log_dir.glob("tidy-*.json"))
    if not logs:
        return "There's nothing of mine to undo."
    record = json.loads(logs[-1].read_text())
    back, failed = 0, []
    for src, dst in reversed(record["moves"]):
        if not Path(dst).exists():
            continue
        try:
            shutil.move(dst, _free(Path(src)))
            back += 1
        except OSError as exc:
            failed.append(f"{Path(dst).name}: {exc.strerror or exc}")
    for d in record["made"]:
        try:
            Path(d).rmdir()  # only if it's empty again: anything they added stays
        except OSError:
            pass
    logs[-1].rename(logs[-1].with_suffix(".undone"))
    tail = f" {len(failed)} couldn't move back: {'; '.join(failed[:3])}." if failed else ""
    return f"Put {back} files back where they were.{tail}"
