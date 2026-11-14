"""The companion's file tools: hand conversions to Pixel and edits to Ledger, and (once the user
allows it) search and read their files. Kept out of brain.py; the brain calls :func:`handle`.

Everything here works on the files directly in the background. Nothing opens an app window or
moves the user's mouse.
"""

from __future__ import annotations

import asyncio
import re
import time
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


_GOOGLE_LINK = re.compile(r"/(spreadsheets|document|presentation)/d/[\w-]+")
_KIND = {"spreadsheets": "sheet", "document": "doc", "presentation": "deck"}
_NEW = {"sheet": "new sheet", "doc": "new doc", "deck": "new slides"}
_THIS = re.compile(r"(?i)^\W*(this|the open|the current|current|open|my open)\b")


_UNTITLED = re.compile(r"(?i)^\W*untitled (spreadsheet|document|presentation)\W*$")
_GOOGLE_TITLE = re.compile(r"(?i) - Google (Sheets|Docs|Slides)\b")


def _google_windows(brain: Brain) -> list[tuple[int, str]]:
    """Browser windows showing a Google Sheet or Doc, on any screen, the window under the mouse
    first, then front to back."""
    from pyxi import windows

    found = [(h, t) for h, t, _ in windows.open_windows() if _GOOGLE_TITLE.search(t)]
    w = brain._ctx.window if brain._ctx else None
    if w and w.hwnd:
        found = [(w.hwnd, w.title), *[(h, t) for h, t in found if h != w.hwnd]]
    return found


async def _open_google_file(brain: Brain, ref: str) -> str:
    """'this sheet', 'Untitled spreadsheet': the Sheet or Doc open in their browser, by its real
    link from the address bar. A name alone fails for a new file (they're all called Untitled:
    one evening Ledger edited the morning's 'Untitled spreadsheet' instead of the blank one on
    screen) and the screen shows the link cut short. Any screen counts, not just the one under
    the mouse. Returns "" for an Untitled file that isn't open anywhere: never guess by name."""
    if _GOOGLE_LINK.search(ref) or ref.lower().startswith("new "):
        return ref
    from pyxi.screen import uia

    want = ref.strip().lower()
    generic = bool(_THIS.match(ref)) or not want
    kind = (
        "Docs"
        if re.search(r"(?i)\bdoc", ref)
        else "Sheets"
        if re.search(r"(?i)sheet|spreadsheet", ref)
        else "Slides"
        if re.search(r"(?i)slide|deck|presentation", ref)
        else ""
    )
    for hwnd, title in _google_windows(brain):
        t = title.lower()
        if kind and f"google {kind.lower()}" not in t and _GOOGLE_TITLE.search(title):
            continue  # a Doc when they said sheet (a window whose title doesn't say is still tried)
        if generic or (want and want in t):
            url = await asyncio.to_thread(uia.browser_url, hwnd)
            if url and _GOOGLE_LINK.search(url):
                return url
    if generic:
        # "This sheet", and no window gave up its link (the address bar can't always be read):
        # the file Ledger made or edited a moment ago is the one they mean. Live: Ledger made
        # a new sheet, and the next request, "make the header bold" on "this sheet", ended
        # "I couldn't find a Google Sheet called 'this sheet'".
        return _just_worked_on(brain, kind)
    return "" if _UNTITLED.match(ref) else ref


_RECENT_S = 30 * 60  # how long "this sheet" still means the file Ledger last touched
_KIND_IN_LINK = {"Sheets": "spreadsheets", "Docs": "document", "Slides": "presentation"}


def _just_worked_on(brain: Brain, kind: str) -> str:
    """The link of the Google file Ledger last made or edited, if that was recent ('' if not).
    ``kind``: 'Sheets', 'Docs', 'Slides', or '' for whichever was last."""
    now = time.time()
    for job in reversed(brain.jobs.recent(12)):
        if job.agent_name != "Ledger" or job.status != "done":
            continue
        if job.ended_at is None or now - job.ended_at > _RECENT_S:
            continue
        for m in re.finditer(r"https://docs\.google\.com/(spreadsheets|document|presentation)/d/[\w-]+", job.summary or ""):
            if not kind or m.group(1) == _KIND_IN_LINK.get(kind):
                return m.group(0) + "/edit"
    return ""


# Ledger can't open the file, but the file is open in front of them: cells can still go in.
_PASTE_INSTEAD = (
    " BUT if what they asked for is cells put in (data, rows, a table, an empty table of some "
    "size), don't stop or offer anything: call paste_table now, which pastes it into the file "
    "that's open in front of them whatever account it's in."
)

_SAMPLE = re.compile(r"(?i)\b(sample|example|dummy|made[- ]up|fake|test|demo|practice)\b")


async def _reachable(brain: Brain, ref: str) -> bool:
    """Can the connected Google account open this link? A Sheet open in their browser may
    belong to another of their Google accounts, and Google then says only "The caller does
    not have permission"."""
    m = _GOOGLE_LINK.search(ref)
    if not m:
        return True  # a name or "new sheet:": Ledger finds or makes it in the connected account
    file_id = ref[m.start() :].split("/")[3]
    g = brain.connectors.google
    try:
        if m.group(1) == "spreadsheets":
            await g.sheets_meta(file_id)
        elif m.group(1) == "presentation":
            await g._api(
                "GET",
                f"https://slides.googleapis.com/v1/presentations/{file_id}",
                params={"fields": "title"},
            )
        else:
            await g._api("GET", f"https://docs.googleapis.com/v1/documents/{file_id}", params={"fields": "title"})
    except Exception as exc:
        return not re.search(r"\b(403|404)\b", str(exc))
    return True


_APP_WORDS = {"ppt": "powerpoint", "power point": "powerpoint", "slides": "powerpoint", "xl": "excel"}


async def _open_app(brain: Brain, app: str) -> tuple[dict[str, Any] | None, str | None]:
    """The document open in Excel, Word or PowerPoint, for a direct edit: (what the job needs,
    None), or (None, a note for the model) when the app can't be reached right now, or
    (None, None) when no such app was named and none is in front.

    The look changes nothing and comes back within three seconds whatever the app is doing."""
    from pyxi import windows
    from pyxi.agents import ledger_live
    from pyxi.screen.context import WindowInfo

    app = " ".join(app.lower().split())
    app = _APP_WORDS.get(app, app)
    w = brain._ctx.window if brain._ctx else None
    front = ledger_live.app_of(w.process) if w else ""
    if not app:
        app = front  # they're looking at it, and named no file
    if not app:
        return None, None
    if app not in ledger_live.APPS:
        return None, "edit_document: app is excel, word or powerpoint."
    label = ledger_live.APPS[app]["label"]
    hwnd, title, exe = (w.hwnd, w.title, w.process) if w and front == app else (0, "", "")
    if not hwnd:  # named, not in front: its topmost window
        for h, t, e in await asyncio.to_thread(windows.open_windows):
            if ledger_live.app_of(e) == app:
                hwnd, title, exe = h, t, e
                break
    is_excluded = getattr(brain.screen, "is_excluded", None)
    if is_excluded and title and is_excluded(WindowInfo(title, exe)):
        return None, f"edit_document: that {label} window is one of their private apps, so Pyxi leaves it alone."
    why, name = await asyncio.to_thread(ledger_live.probe, app, hwnd or None)
    if why:
        return None, (
            f"edit_document: {why} So Pyxi can't change it directly right now. If {label} is open "
            "on their screen, do this with use_computer now through the app's own menus (or "
            "paste_table when it's only cells to put in), and don't mention this note; if it "
            f"isn't open, say in one short line that there's nothing open in {label}."
        )
    return {"app": app, "hwnd": int(hwnd or 0), "name": name, "label": label}, None


async def _open_when_done(brain: Brain, job_id: str) -> None:
    """A sheet or doc Pyxi made (asked for, or because the open one was out of reach): put it
    on their screen when it's ready."""
    try:
        job = await brain.jobs.wait(job_id)
    except KeyError:
        return  # no such job (it was never started): nothing to open
    data = job.data if isinstance(job.data, dict) else {}
    links = data.get("links") or []
    if job.status == "done" and links:
        await brain._quick_action("open_url", links[0])
    elif job.status == "done" and job.params.get("new") and job.outputs:
        # A workbook Ledger built from nothing: it opens in Excel (or whatever opens .xlsx).
        try:
            await asyncio.to_thread(open_path, Path(job.outputs[0]))
        except OSError:  # no app for it: the notice still has the file and its Open button
            pass


def open_path(path: Path) -> None:
    """Open a file in its usual app (Excel for a workbook). Replaced in tests."""
    import os

    os.startfile(str(path))  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------- one job, then another
# Words that say a second teammate works on the first one's result.
_AFTER = (
    r"(?:and then|then|after that|afterwards?|next|"
    r"once (?:that|it|she|he|they)(?:'s| is| has|'ve| are)? (?:done|finished|through)|"
    r"when (?:that|it)(?:'s| is) (?:done|ready))"
)


def promised_next(spoken: str, starting: str, names: list[str]) -> str:
    """The teammate a spoken line says will take the result next, or "".

    Live, 13 November: "Pixel's turning your statement into Word first, then Quill gets the
    grammar". Pixel finished in 3.7 seconds, no Quill job was ever started, and nothing said
    so: the model had promised the second step and passed only the first."""
    for name in names:
        if not name or name.lower() == starting.lower():
            continue
        n = re.escape(name)
        if (
            re.search(rf"(?i)\b{_AFTER}\b[^.!?\n]{{0,60}}\b{n}\b", spoken)
            or re.search(rf"(?i)\b{n}\b[^.!?\n]{{0,60}}\b(?:after(?:wards)?|next|then)\b", spoken)
            or re.search(rf"(?i)\bfirst\b[^.!?\n]{{0,80}}\b{n}\b", spoken)
            or re.search(
                rf"(?i)\b(?:hand(?:s|ing)?|pass(?:es|ing)?|send(?:s|ing)?|giv(?:e|es|ing))\s+"
                rf"(?:it|that|them|the \w+)\s+(?:on |over |off )?to\s+{n}\b",
                spoken,
            )
        ):
            return name
    return ""


def handoff(brain: Brain, tool: str, starting: str, args: dict[str, Any]) -> tuple[Any, str | None]:
    """The ``then`` of a call that starts a teammate: (the hand-off or None, a note that sends
    the call back to the model or None).

    The call goes back, once a turn, when the line Pyxi is saying promises a second teammate
    and the call names none. A teammate that doesn't exist is said too: it used to be dropped
    without a word, which is the same broken promise."""
    from pyxi.agents.jobs import Handoff

    who = str(args.get("then_agent") or "").strip()
    if getattr(brain, "registry", None) is None:
        return None, None
    if who:
        agent = brain.registry.get(who)
        if agent is None or agent.role == "companion":
            names = ", ".join(a.name for a in brain.registry.all() if a.role != "companion")
            return None, f"{tool}: there's no teammate called {who!r} for then_agent. The team: {names}."
        what = str(args.get("then_task") or "").strip()
        return Handoff(agent=who, task=what or "Use the result."), None
    calls = getattr(brain, "_calls", None)
    if calls is not None and getattr(brain, "_handoff_sent_back", None) is calls:
        return None, None  # said once this turn already: the call goes ahead as it is
    spoken = " ".join([*getattr(brain, "_said", []), getattr(brain, "_round_say", "") or ""])
    names = [a.name for a in brain.registry.all() if a.role != "companion"]
    promised = promised_next(spoken, starting, names)
    if not promised:
        return None, None
    for call in calls or []:  # started in this same turn: the promise is kept another way
        a = call.get("arguments") or {}
        if promised.lower() in (str(a.get("agent", "")).lower(), str(a.get("then_agent", "")).lower()):
            return None, None
    brain._handoff_sent_back = calls
    return None, (
        f"{tool}: not started yet. You are telling them {promised} takes it next, but this call "
        f"has no then_agent, so {promised} would never start and nobody would tell them. Call "
        f'{tool} again now with then_agent "{promised}" and then_task (what {promised} does with '
        "the result, in full: the new file is handed over by itself). Or, when one teammate can "
        "do the whole thing (Quill fixes the grammar of a PDF and returns a Word file on its "
        "own), start only that one. No new line of your own first."
    )


_NEW_FILE = re.compile(
    r"(?i)^\s*new\s+(xlsx|excel(?: file| workbook)?|workbook|spreadsheet)\s*[:\-]\s*(.+?)\s*$"
)


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
        options = {k: v for k, v in raw.items() if k not in ("password", "details", "out_dir", "placed", "labels")}
        if op == "compare_documents" and len(files) != 2:
            return "convert_files: compare_documents needs exactly two files, the old one first."
        names = ", ".join(Path(f).name for f in files[:3])
        then, note = handoff(brain, "convert_files", "Pixel", args)
        if note:
            return note
        job = await brain.jobs.start(
            "pixel",
            f"{op.replace('_', ' ')}: {names}",
            files=files,
            params={"operation": op, "options": options},
            then=then,
        )
        if then is not None:
            return (
                f"Started {job.id}: {job.agent_name} is on it, and the new file goes straight to "
                f"{then.agent} when it's done ({then.task[:120]}). Say one short line that names "
                "both steps; they're told when the last one is finished."
            )
        if op == "fill_form":
            return (
                f"Started {job.id}: {job.agent_name} is reading the form and will put a card on "
                "their screen with every question it asks; their name, email, phone and the like "
                "are typed in already, and they type the rest. Don't ask them for the answers "
                "yourself. Say one short line, like 'I'll ask you for the answers on a card.'"
            )
        if op in NEEDS_PASSWORD:
            return (
                f"Started {job.id}: a card is asking them to type the password (never ask for it "
                "yourself). Say one short line, like 'Type the password on the card.'"
            )
        return (
            f"Started {job.id}: {job.agent_name} is on it in the background. Say one short line "
            "that names the job; they're told when the new file is ready."
        )
    if name == "edit_document":
        task = str(args.get("task") or "").strip()
        given = args.get("files")
        given = given if isinstance(given, list) else [given] if given else []
        # "new xlsx: NEXORA": a workbook that doesn't exist yet, built from the brief.
        new = [m.group(2).strip().strip('"')[:80] for f in given if (m := _NEW_FILE.match(str(f)))]
        files = _paths([f for f in given if not _NEW_FILE.match(str(f))])
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
        then, note = handoff(brain, "edit_document", "Ledger", args)
        if note:
            return note
        if new:
            if files or google or len(new) > 1:
                return (
                    "edit_document: a new workbook is made on its own: one 'new xlsx: <title>' "
                    "in files, and nothing else in files or google."
                )
            job = await brain.jobs.start(
                "ledger", task, files=[], params={"new": [{"kind": "xlsx", "title": new[0]}]}, then=then
            )
            if then is None:
                brain._spawn(_open_when_done(brain, job.id))
            return (
                f"Started {job.id}: {job.agent_name} is building {new[0]}.xlsx in the background, "
                "sheet by sheet (a big workbook takes a minute or two), and it opens on their "
                "screen when it's ready. Say one short line that names the job."
            )
        if not files and not google:
            # The workbook, document or deck open in Excel, Word or PowerPoint: edited where it
            # is (agents/ledger_live.py), when the app can be reached right now.
            ref, note = await _open_app(brain, str(args.get("app") or ""))
            if note:
                return note
            if ref:
                job = await brain.jobs.start(
                    "ledger", task, files=[], params={"live": [ref]}, then=then
                )
                return (
                    f"Started {job.id}: {job.agent_name} is changing {ref['name']} right in "
                    f"{ref['label']}, where it's open; it takes a few seconds and nothing is saved. "
                    "Say one short line that names the job; they're told when it's done."
                )
            return (
                "edit_document: which file? Give files (paths on this PC), google (a link or the "
                "file's name), or app (excel, word or powerpoint) for the one open in that app."
            )
        if google and not brain.connectors.google.connected():
            return (
                "edit_document: Google isn't connected. Tell them to connect it in Settings first."
            )
        google = [await _open_google_file(brain, g) for g in google]
        if "" in google:
            return (
                "edit_document: I can't tell which file they mean: none is open in a browser "
                "window I can read, and there is none I worked on in the last half hour. Ask "
                "them to bring it to the front (or say its name or paste its link), and never "
                "guess by the name Untitled. Don't call edit_document again in this reply: say "
                "that in one short line and stop."
            )
        out_of_reach = [g for g in google if not await _reachable(brain, g)]
        made_instead = False
        if out_of_reach:
            who = brain.connectors.google._saved_email() or "the connected Google account"
            kind = _KIND[_GOOGLE_LINK.search(out_of_reach[0]).group(1)]
            needs_ok = getattr(brain.connectors.google, "needs_ok", None)
            if label := (needs_ok(kind) if needs_ok else ""):
                # Not another account: Google hasn't been asked for this app yet, so Pyxi can
                # open only the files it made itself. (One test: "that doc belongs to a
                # different account", about a doc in the very account Pyxi was connected to.)
                return (
                    f"edit_document: Pyxi can't open that {kind} yet. Google has only let Pyxi "
                    f"into {kind}s it made itself; one more OK lets it edit theirs. Tell them in "
                    f"one short line: open Settings and press {label} next to Google, then ask "
                    f"again. Or offer to make a new {kind} now instead. Don't say it's a "
                    "different account." + _PASTE_INSTEAD
                )
            if not _SAMPLE.search(task):
                return (
                    f"edit_document: the {kind} open on their screen is in a different Google "
                    f"account; Pyxi is connected as {who}, which can't open it. Tell them in one "
                    f"short line, and offer to make a new {kind} in {who} instead (or they can "
                    "connect the other account in Settings)." + _PASTE_INSTEAD
                )
            # Sample data doesn't need that file: make a new one where Pyxi can, and show it.
            title = "Sample data" if kind == "sheet" else "Sample document"
            google = [f"{_NEW[kind]}: {title}" if g in out_of_reach else g for g in google]
            made_instead = True
        job = await brain.jobs.start(
            "ledger", task, files=files, params={"google": google}, **({"then": then} if then else {})
        )
        if not made_instead and any(g.lower().startswith("new ") for g in google):
            # "Open a new Google Doc and write...": a file made in their Drive goes on screen.
            brain._spawn(_open_when_done(brain, job.id))
        if made_instead:
            brain._spawn(_open_when_done(brain, job.id))
            brain._speak(
                f"That {kind} is in a different Google account, so I'm making a fresh one in "
                "yours and opening it."
            )
            return (
                f"Started {job.id}: the {kind} on their screen is in another Google account, so "
                f"{job.agent_name} is making a new {kind} in {who} and it opens on their screen "
                "in a few seconds. Say that in one short line."
            )
        return (
            f"Started {job.id}: {job.agent_name} is on it in the background. Say one short line "
            "that names the job; they're told when it's done."
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
