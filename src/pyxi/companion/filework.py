"""The companion's file tools: hand conversions to Pixel and edits to Ledger, and (once the user
allows it) search and read their files. Kept out of brain.py; the brain calls :func:`handle`.

Everything here works on the files directly in the background. Nothing opens an app window or
moves the user's mouse.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pyxi import files_access
from pyxi.agents.pixel import NEEDS_PASSWORD, OPERATIONS

if TYPE_CHECKING:
    from pyxi.companion.brain import Brain

FILE_TOOLS = {"convert_files", "edit_document", "search_files", "read_file"}

CONSENT_ACTION = "Let Pyxi search your files?"


def _paths(raw: Any) -> list[str]:
    items = raw if isinstance(raw, list) else [raw] if raw else []
    return [str(Path(str(f).strip().strip('"')).expanduser()) for f in items if str(f).strip()]


async def _emit_access(brain: Brain, access: files_access.FileAccess) -> None:
    await brain.hub.emit("file_access", **access.public())


async def consent(brain: Brain) -> files_access.FileAccess | str:
    """File access if they allow it, or a note for the model saying why not. The card is shown
    once; after a "no", Pyxi asks for paths instead and points at Settings."""
    data_dir = brain.settings.data_dir
    access = files_access.load(data_dir)
    if access.enabled:
        return access
    if access.asked:
        return (
            "file search is off. Ask them for the file (they can drag it into the chat box or "
            "say where it is), and mention they can turn on 'Let Pyxi search my files' in Settings."
        )
    folders = ", ".join(Path(f).name for f in access.folders)
    ok = await brain.hub.request(
        "confirm",
        timeout=180,
        job_id="",
        agent="Pyxi",
        action=CONSENT_ACTION,
        reason=f"Only in {folders}. Pyxi finds files by name and reads them when you ask; "
        "nothing leaves this PC except what it reads for an answer. Change the folders or turn "
        "it off in Settings.",
    )
    access.asked = True
    access.enabled = bool(ok)
    files_access.save(data_dir, access)
    await _emit_access(brain, access)
    if not ok:
        return "they said no to file search. Ask them where the file is instead."
    return access


_GOOGLE_LINK = re.compile(r"/(spreadsheets|document)/d/[\w-]+")
_THIS = re.compile(r"(?i)^\W*(this|the open|the current|current|open|my open)\b")


async def _open_google_file(brain: Brain, ref: str) -> str:
    """'this sheet', 'Untitled spreadsheet': the Sheet or Doc open in their browser, by its real
    link from the address bar. A name alone fails for a new file (they're all called Untitled)
    and the screen shows the link cut short."""
    if _GOOGLE_LINK.search(ref) or ref.lower().startswith("new "):
        return ref
    w = brain._ctx.window if brain._ctx else None
    if not w or not w.hwnd:
        return ref
    title = w.title.lower()
    if not (_THIS.match(ref) or (ref.strip() and ref.strip().lower() in title)):
        return ref
    from pyxi.screen import uia

    url = await asyncio.to_thread(uia.browser_url, w.hwnd)
    return url if url and _GOOGLE_LINK.search(url) else ref


async def handle(brain: Brain, name: str, args: dict[str, Any]) -> str | None:
    if name == "convert_files":
        op = str(args.get("operation") or "")
        if op not in OPERATIONS:
            return f"convert_files: operation must be one of {', '.join(OPERATIONS)}."
        files = _paths(args.get("files"))
        missing = [f for f in files if not Path(f).is_file()]
        if not files or missing:
            return (
                f"convert_files: I can't find {', '.join(Path(f).name for f in missing) or 'the file'}. "
                "Find the exact path with search_files first, or ask them."
            )
        raw = args.get("options") if isinstance(args.get("options"), dict) else {}
        # A password never travels through the model or the job record: Pixel asks for it on a
        # masked card. Saved details are read on this PC by the job itself.
        options = {k: v for k, v in raw.items() if k not in ("password", "details", "out_dir")}
        if op == "compare_documents" and len(files) != 2:
            return "convert_files: compare_documents needs exactly two files, the old one first."
        names = ", ".join(Path(f).name for f in files[:3])
        job = await brain.jobs.start(
            "pixel",
            f"{op.replace('_', ' ')}: {names}",
            files=files,
            params={"operation": op, "options": options},
        )
        if op in NEEDS_PASSWORD:
            return (
                f"Started {job.id}: a card is asking them to type the password (never ask for it "
                "yourself). Say one short line, like 'Type the password on the card.'"
            )
        return (
            f"Started {job.id}: {job.agent_name} is on it in the background. Say one short line "
            "('On it.'); they get a notification with the new file."
        )
    if name == "edit_document":
        task = str(args.get("task") or "").strip()
        files = _paths(args.get("files"))
        raw = args.get("google")
        google = [
            str(g).strip()
            for g in (raw if isinstance(raw, list) else [raw])
            if g and str(g).strip()
        ]
        missing = [f for f in files if not Path(f).is_file()]
        if not task:
            return "edit_document: say what to change."
        if missing:
            return (
                f"edit_document: I can't find {', '.join(Path(f).name for f in missing)}. Find the "
                "exact path with search_files first, or ask them."
            )
        if not files and not google:
            return "edit_document: which file? Give files (paths on this PC) or google (a link or the file's name)."
        if google and not brain.connectors.google.connected():
            return (
                "edit_document: Google isn't connected. Tell them to connect it in Settings first."
            )
        google = [await _open_google_file(brain, g) for g in google]
        job = await brain.jobs.start("ledger", task, files=files, params={"google": google})
        return (
            f"Started {job.id}: {job.agent_name} is on it in the background. Say one short line "
            "('On it.'); they get a notification when it's done."
        )
    if name in ("search_files", "read_file"):
        access = await consent(brain)
        if isinstance(access, str):
            return f"{name}: {access}"
        if name == "search_files":
            query = str(args.get("query") or "").strip()
            hits = await asyncio.to_thread(
                files_access.search, query, access, str(args.get("kind") or "")
            )
            if not hits:
                return f"search_files: nothing matching {query!r} in {', '.join(Path(f).name for f in access.folders)}."
            return (
                "search_files: in the folders they allowed (their real files), newest first:\n"
                + "\n".join(f"- {h.line()}" for h in hits)
                + "\nIf one clearly fits, hand it over now with one short `say`; don't mention "
                "the search. If several fit, ask which."
            )
        path = Path(str(args.get("path") or "").strip().strip('"')).expanduser()
        if not path.is_file():
            return f"read_file: there's no file at {path}. Use search_files to find it."
        if not files_access.allowed(path, access):
            return "read_file: that file is outside the folders they allowed (Settings > Let Pyxi search my files)."
        try:
            text = await asyncio.to_thread(files_access.read_text, path)
        except Exception as exc:
            return f"read_file: couldn't read {path.name}: {str(exc)[:160]}"
        return f"read_file {path.name}:\n{text}"
    return None
