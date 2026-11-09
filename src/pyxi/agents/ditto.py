"""Ditto, "watch me once, then do the rest".

The user does a repetitive task once while Ditto watches; Ditto then does it for the remaining
items, the quiet way when there is one.

* **Watching.** :class:`Recorder` polls the screen reader's accessibility focus (the same UI
  Automation read the Memory Palace uses, :mod:`pyxi.screen.uia`): every window they switch to,
  every button, field, tab or menu that takes focus, and what ended up in each field when they
  left it (never a password field). A small screenshot is kept at each window change. When
  they say which folder the items are in, the folder is listed before and after, so renames,
  moves and new files are seen exactly (by file id, so a rename is a rename). Each step also
  goes into the activity log (:mod:`pyxi.palace.activity`).
* **Understanding.** One model call reads the recording plus the remaining items and picks the
  route, in this order of preference:
  ``files`` (rename or move each file, done right here in code), ``convert`` (Pixel converts
  each file), ``sheet`` (Ledger edits the spreadsheet), ``web`` (Scout fills a website for each
  row, in a background browser), and only when nothing else can do it, ``mouse`` (Pyxi's hands
  repeat the clicks, after a card that says it will use the mouse).
* **Doing.** Nothing runs until they approve the plan on a card, with the first few items
  shown before and after. File changes keep an undo log.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pyxi import safety

if TYPE_CHECKING:
    from pyxi.agents.jobs import Job, JobManager
    from pyxi.agents.registry import AgentProfile

ROUTES = ("files", "convert", "sheet", "web", "mouse")
MAX_ITEMS = 60
MAX_MOUSE_ITEMS = 10
FILLED = {"Edit", "ComboBox", "Document", "Spinner"}
BAD_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


@dataclass
class Step:
    ts: float
    kind: str  # "window", "focus", "typed", "file"
    app: str = ""
    title: str = ""
    name: str = ""
    role: str = ""
    value: str = ""
    shot: str = ""

    def line(self) -> str:
        if self.kind == "window":
            return f"switched to {self.app}: {self.title[:80]}"
        if self.kind == "typed":
            return f'typed "{self.value[:120]}" into {self.role.lower()} "{self.name[:60]}"'
        if self.kind == "file":
            return self.value
        return f'{"clicked" if self.role in ("Button", "MenuItem", "Hyperlink", "ListItem", "TreeItem", "TabItem") else "went to"} {self.role.lower()} "{self.name[:60]}"'


@dataclass
class Focus:
    """What has focus now: like ``uia.RawFocus`` plus the field's text when it's a field."""

    app: str
    title: str
    name: str
    role: str
    value: str = ""
    password: bool = False


def read_focus() -> Focus | None:
    """The focused element and, for a field, its current text (UI Automation ValuePattern).
    Password fields are never read."""
    from pyxi.screen import uia

    if not uia.available():
        return None
    if __import__("sys").platform == "darwin":  # Accessibility's focused element
        f = uia.focused_field()  # type: ignore[attr-defined]
        if f is None:
            return None
        password = bool(f["password"])
        value = f["value"] if f["role"] in FILLED and not password else ""
        name = "(password field)" if password else f["name"]
        return Focus(f["process"], f["title"], name, f["role"], value, password)
    try:
        import uiautomation as auto

        with auto.UIAutomationInitializerInThread(debug=False):
            c = auto.GetFocusedControl()
            if c is None:
                return None
            top = c.GetTopLevelControl() or c
            role = c.ControlTypeName.removesuffix("Control")
            try:
                password = bool(c.GetPropertyValue(auto.PropertyId.IsPasswordProperty))
            except Exception:
                password = False
            value = ""
            if role in FILLED and not password:
                try:
                    value = str(c.GetValuePattern().Value or "")[:300]
                except Exception:
                    value = ""
            return Focus(
                uia.process_name(top.ProcessId),
                top.Name or "",
                "(password field)" if password else (c.Name or "").strip()[:160],
                role,
                value,
                password,
            )
    except Exception:
        return None


# ------------------------------------------------------------------------------ folders
def listing(folder: Path, depth: int = 2) -> dict[int, str]:
    """File id -> path relative to ``folder``, a couple of levels deep. Windows keeps a file's
    id through a rename or a move on the same drive, so a rename shows up as one."""
    out: dict[int, str] = {}
    base = len(folder.parts)
    for root, dirs, files in os.walk(folder):
        if len(Path(root).parts) - base >= depth:
            dirs[:] = []
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for f in files:
            p = Path(root) / f
            try:
                out[p.stat().st_ino or hash(str(p))] = str(p.relative_to(folder))
            except OSError:
                continue
    return out


def folder_changes(before: dict[int, str], after: dict[int, str]) -> list[dict[str, str]]:
    changes = []
    for fid, old in before.items():
        new = after.get(fid)
        if new is None:
            changes.append({"op": "deleted", "from": old})
        elif new != old:
            same_dir = Path(new).parent == Path(old).parent
            changes.append({"op": "renamed" if same_dir else "moved", "from": old, "to": new})
    for fid, new in after.items():
        if fid not in before:
            changes.append({"op": "created", "to": new})
    return changes


# ------------------------------------------------------------------------------ recording
@dataclass
class Recording:
    id: str
    task: str
    folder: str
    started: float
    ended: float = 0.0
    steps: list[Step] = field(default_factory=list)
    changes: list[dict[str, str]] = field(default_factory=list)

    def text(self, limit: int = 80) -> str:
        lines = [s.line() for s in self.steps[:limit]]
        if len(self.steps) > limit:
            lines.append(f"... and {len(self.steps) - limit} more steps")
        return "\n".join(f"{i + 1}. {ln}" for i, ln in enumerate(lines))

    def save(self, folder: Path) -> Path:
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{self.id}.json"
        path.write_text(json.dumps(asdict(self), indent=1), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: Path) -> Recording:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        d["steps"] = [Step(**s) for s in d.get("steps") or []]
        return cls(**d)


class Recorder:
    """Polls the focused element while they show Pyxi the task once."""

    def __init__(
        self,
        task: str,
        folder: str = "",
        *,
        out_dir: Path,
        reader: Callable[[], Focus | None] | None = None,
        snap: Callable[[], Any] | None = None,
        log: Callable[[Step], None] | None = None,
        poll_s: float = 0.4,
        excluded: Callable[[str, str], bool] | None = None,
    ) -> None:
        self.rec = Recording("watch_" + secrets.token_hex(3), task.strip(), folder, time.time())
        self.out_dir = out_dir
        self.reader = reader or (lambda: read_focus())  # looked up when called (tests swap it)
        self.snap, self.log, self.poll_s = snap, log, poll_s
        self.excluded = excluded or (lambda app, title: False)
        self._before = listing(Path(folder)) if folder and Path(folder).is_dir() else {}
        self._last: Focus | None = None
        self._window: tuple[str, str] | None = None
        self._task: asyncio.Task[None] | None = None
        self.own_apps = {"pyxi.exe", "electron.exe", "pyxi", "electron"}  # Pyxi's own (Windows, Mac)

    def start(self) -> None:
        self._task = asyncio.ensure_future(self._run())

    async def _run(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:  # a hiccup in UI Automation must not end the recording
                pass
            await asyncio.sleep(self.poll_s)

    def _add(self, step: Step) -> None:
        self.rec.steps.append(step)
        if self.log:
            self.log(step)

    async def tick(self) -> None:
        f = await asyncio.to_thread(self.reader)
        if f is None or f.app.lower() in self.own_apps or self.excluded(f.app, f.title):
            return
        last = self._last
        # What they left in the field they were in (read now that they've moved on).
        if (
            last
            and last.role in FILLED
            and not last.password
            and last.value
            and ((f.app, f.name, f.role) != (last.app, last.name, last.role))
        ):
            self._add(
                Step(time.time(), "typed", last.app, last.title, last.name, last.role, last.value)
            )
        if (f.app, f.title) != self._window:
            self._window = (f.app, f.title)
            shot = ""
            if self.snap:
                try:
                    img = await self.snap()
                    if img is not None:
                        p = self.out_dir / self.rec.id / f"step{len(self.rec.steps) + 1}.jpg"
                        p.parent.mkdir(parents=True, exist_ok=True)
                        img.convert("RGB").save(p, quality=60)
                        shot = str(p)
                except Exception:
                    shot = ""
            self._add(Step(time.time(), "window", f.app, f.title, shot=shot))
        moved = last is None or (f.app, f.name, f.role) != (last.app, last.name, last.role)
        if moved and (f.name or f.role in FILLED):
            self._add(Step(time.time(), "focus", f.app, f.title, f.name, f.role))
        self._last = f

    async def stop(self) -> Recording:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        last = self._last
        if last and last.role in FILLED and last.value and not last.password:
            self._add(
                Step(time.time(), "typed", last.app, last.title, last.name, last.role, last.value)
            )
        if self.rec.folder and Path(self.rec.folder).is_dir():
            self.rec.changes = folder_changes(self._before, listing(Path(self.rec.folder)))
            for c in self.rec.changes:
                what = f"{c['op']} {c.get('from', '')}" + (f" -> {c['to']}" if c.get("to") else "")
                self._add(Step(time.time(), "file", value=f"file {what.strip()}"))
        self.rec.ended = time.time()
        self.rec.save(self.out_dir)
        return self.rec


# ------------------------------------------------------------------------------ the plan
SYSTEM = """The user showed you a repetitive task once; you plan doing it for the rest of the
items. Reply with JSON only:
{"summary": "what you'll do, one sentence, in plain words",
 "route": "files" | "convert" | "sheet" | "web" | "mouse",
 "items": [{"item": "<an item from the list, exactly>", "to": "<new name or relative path, files only>"}],
 "operation": "<convert only: one of the conversions listed>",
 "steps": ["<web or mouse: the steps for one item, with {item} where the item goes>"],
 "url": "<web: the page they used>",
 "sheet_task": "<sheet: the edit to make, in words>",
 "why": "why this route, one line"}
Prefer a route that doesn't need their mouse, in this order: files (they renamed or moved
files: give every remaining item's new name or path, following exactly the pattern they
used, from the item's name and what's in it), convert, sheet, web, and mouse only when nothing
else can do it. Only items from the list; leave out ones already done or that don't fit the
pattern. Never invent data you can't see in the item. Plain words, no em dashes."""


@dataclass
class Plan:
    summary: str
    route: str
    items: list[dict[str, str]]
    operation: str = ""
    steps: list[str] = field(default_factory=list)
    url: str = ""
    sheet_task: str = ""
    why: str = ""


def parse_plan(raw: str, allowed: list[str]) -> Plan | None:
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return None
    route = str(data.get("route") or "")
    if route not in ROUTES:
        return None
    ok = set(allowed)
    items = []
    for it in data.get("items") or []:
        if isinstance(it, str):
            it = {"item": it}
        if not isinstance(it, dict) or str(it.get("item")) not in ok:
            continue  # the model made one up
        items.append({"item": str(it["item"]), "to": str(it.get("to") or "")})
    clean = lambda s: re.sub(r"\s*[—–]\s*", ", ", str(s or "")).strip()  # noqa: E731
    return Plan(
        clean(data.get("summary")),
        route,
        items[:MAX_ITEMS],
        str(data.get("operation") or ""),
        [clean(s) for s in data.get("steps") or [] if str(s).strip()][:20],
        str(data.get("url") or ""),
        clean(data.get("sheet_task")),
        clean(data.get("why")),
    )


def remaining(rec: Recording, items: list[str] | None = None) -> list[str]:
    """The items still to do: given ones, or the files in their folder they didn't touch yet
    (same kind of file as the ones they worked on)."""
    if items:
        return [str(i).strip() for i in items if str(i).strip()][:MAX_ITEMS]
    if not rec.folder or not Path(rec.folder).is_dir():
        return []
    touched = {c.get("to") for c in rec.changes} | {c.get("from") for c in rec.changes}
    exts = {
        Path(str(c.get("from") or c.get("to"))).suffix.lower()
        for c in rec.changes
        if c.get("from") or c.get("to")
    }
    out = []
    for p in sorted(Path(rec.folder).iterdir()):
        fresh = p.is_file() and p.name not in touched and not p.name.startswith((".", "~$"))
        if fresh and (not exts or p.suffix.lower() in exts):
            out.append(p.name)
    return out[:MAX_ITEMS]


def check_files(plan: Plan, folder: Path) -> list[tuple[Path, Path]]:
    """The renames and moves to make, safe ones only: inside the folder, a real new name, no
    clashes, nothing overwritten."""
    moves, taken = [], set()
    for it in plan.items:
        src = folder / it["item"]
        to = it.get("to", "").strip().replace("\\", "/")
        if not to or not src.is_file():
            continue
        parts = [p for p in to.split("/") if p not in ("", ".")]
        if not parts or any(p == ".." or BAD_NAME.search(p) for p in parts):
            continue
        dst = folder.joinpath(*parts)
        if Path(parts[-1]).suffix == "" and src.suffix:
            dst = dst.with_name(dst.name + src.suffix)  # they kept the extension
        key = str(dst).lower()
        if dst == src or dst.exists() or key in taken:
            continue
        taken.add(key)
        moves.append((src, dst))
    return moves


def do_files(moves: list[tuple[Path, Path]], log_dir: Path) -> tuple[int, Path]:
    """Rename and move, writing an undo log first."""
    log_dir.mkdir(parents=True, exist_ok=True)
    undo = log_dir / f"ditto-{time.strftime('%Y%m%d-%H%M%S')}.json"
    undo.write_text(json.dumps([[str(d), str(s)] for s, d in moves], indent=1), encoding="utf-8")
    done = 0
    for src, dst in moves:
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists():
                continue
            src.rename(dst)
            done += 1
        except OSError:
            continue
    return done, undo


def undo_files(undo: Path) -> int:
    n = 0
    for now, was in json.loads(undo.read_text(encoding="utf-8")):
        if Path(now).exists() and not Path(was).exists():
            Path(now).rename(was)
            n += 1
    return n


# ------------------------------------------------------------------------------ the job
def flat(text: str, n: int = 300) -> str:
    return re.sub(r"\s+", " ", text)[:n]


async def run(manager: JobManager, job: Job, agent: AgentProfile) -> None:
    from otto.llm.base import Message

    from pyxi import files_access
    from pyxi.agents.pixel import OPERATIONS

    rec = Recording.load(Path(job.params["recording"]))
    items = remaining(rec, job.params.get("items"))
    if not rec.steps:
        job.status, job.summary = "failed", "I didn't see anything happen while I was watching."
        job.data = {"title": "Nothing to repeat", "body": job.summary}
        return
    if not items:
        job.status = "failed"
        job.summary = (
            "I saw what you did, but not what to do it to next. Tell me the folder or the list."
        )
        job.data = {"title": "What should I do it to?", "body": job.summary}
        return
    await manager._status(job, f"Working out the pattern, {len(items)} to go")
    folder = Path(rec.folder) if rec.folder else None
    peeks = []
    for name in items[:30]:  # a little of what's inside, for names that come from the content
        p = folder / name if folder else None
        if p and p.is_file() and p.suffix.lower() in {".pdf", ".txt", ".md", ".docx", ".csv"}:
            try:
                text = await asyncio.to_thread(files_access.read_text, p, 500)
                peeks.append(f"- {name}: {flat(text)}")
            except Exception:
                pass
    done_example = []
    for c in rec.changes[:5]:
        p = folder / c["to"] if folder and c.get("to") else None
        if p and p.is_file() and p.suffix.lower() in {".pdf", ".txt", ".md", ".docx", ".csv"}:
            try:
                text = await asyncio.to_thread(files_access.read_text, p, 500)
                done_example.append(f"- {c.get('from')} -> {c.get('to')}: {flat(text)}")
            except Exception:
                pass
    prompt = (
        f"The task, in their words: {rec.task or '(they did not say)'}\n"
        + (f"Folder: {rec.folder}\n" if rec.folder else "")
        + f"\nWhat they did once:\n{rec.text()}\n"
        + (
            "\nThe file they did, and what's in it:\n" + "\n".join(done_example) + "\n"
            if done_example
            else ""
        )
        + "\nItems still to do:\n"
        + "\n".join(f"- {i}" for i in items)
        + ("\n\nWhat's in them:\n" + "\n".join(peeks) if peeks else "")
        + f"\n\nConversions Pixel can do: {', '.join(OPERATIONS)}"
    )
    llm = manager.llm_factory(agent)
    resp = await llm.complete(system=SYSTEM + safety.RULE, messages=[Message.user(prompt)], max_tokens=3000)
    job.cost_usd = resp.usage.cost_usd
    plan = parse_plan(resp.text, items)
    if plan is not None and plan.route == "files" and folder is not None:
        # The card shows (and counts) only what will really happen: a name that clashes, or
        # one outside the folder, is left out now, and "Initech" shows as "Initech.pdf".
        safe = dict(check_files(plan, folder))
        plan.items = [
            {"item": it["item"], "to": safe[folder / it["item"]].relative_to(folder).as_posix()}
            for it in plan.items
            if folder / it["item"] in safe
        ]
    if plan is None or not plan.items:
        job.status = "failed"
        job.summary = "I couldn't work out how to repeat that. Show me once more, a little slower?"
        job.data = {"title": "I didn't get the pattern", "body": job.summary}
        return
    preview = [
        f"{it['item']} -> {it['to']}" if it.get("to") else it["item"] for it in plan.items[:5]
    ]
    job.summary = (
        f"{plan.summary} ({len(plan.items)} items, route {plan.route}; waiting for their OK)"
    )
    job.data = {
        "title": f"Ready to do {len(plan.items)} more"[:70],
        "body": plan.summary[:200] + " Check the plan on the card first.",
        "changes": preview,
        "card": {"title": plan.summary, "sections": [{"heading": "First ones", "lines": preview}]},
        "plan": asdict(plan),
        "folder": rec.folder,
        "recording": rec.id,
    }
    job.status = "done"
