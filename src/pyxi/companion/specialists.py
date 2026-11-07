"""The companion's side of the specialist teammates: their tools, and what happens around a job
(saving reminders, scheduling check-ins). Kept out of brain.py; the brain calls :func:`handle`.

Every tool here starts a background job and returns at once, so Pyxi says one short line and
the result arrives as a notification and a chip in the task tray.

* ``travel``: Atlas builds the itinerary of a trip from the confirmations in Gmail.
* ``money``: Penny's monthly spending summary from receipts, budgets, and receipts into a
  spreadsheet (a new one, or theirs through Ledger).
* ``meeting_notes``: Scribe transcribes a recording (local faster-whisper) into notes,
  decisions and action items, and drafts a follow-up email that goes only through a card.
* ``make_flashcards`` and ``quiz``: Sage makes flashcards and a quiz from slides, PDFs or notes, and quizzes them out
  loud (the quiz lives on the brain as ``brain.study_quiz``, one question per voice turn).
* ``watch_me``: Ditto watches them do one item, then does the rest the quietest way it can
  (files, Pixel, Ledger, Scout; the mouse only as a last resort), after a card.
* ``find_again``: Trace finds where they saw something (screen history, files, mail) and
  points at the exact place.
* ``fix_code``: Patch finds the failing line from an error and proposes a small fix; the
  file is written only after the approval card, with a backup beside it.

:func:`after_job` runs when any specialist's job ends (the brain calls it from ``_job_done``),
so a job started by a routine or a hand-off gets the same follow-up as one started here.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from otto.llm.base import ToolSpec

from pyxi.agents.specialists import RUNNERS

if TYPE_CHECKING:
    from pyxi.agents.jobs import Job
    from pyxi.companion.brain import Brain

log = logging.getLogger(__name__)

TRAVEL = ToolSpec(
    "travel",
    "Atlas, the travel teammate: builds a day-by-day itinerary of their trip from the "
    "confirmations already in their Gmail (flights, hotels and rentals, times, addresses, "
    "booking references) and sets reminders before each flight. Runs in the background; the "
    "itinerary arrives as a notification. Use it for 'plan my trip', 'what's my itinerary?', "
    "'when's my flight to Toronto?', 'where am I staying in Lisbon?', 'remind me before my "
    "flight'. Set check_in=true only when they also ask to be checked in. Say one short line "
    "('On it.') and nothing more.",
    {
        "type": "object",
        "properties": {
            "trip": {
                "type": "string",
                "description": "Which trip, in their words: a city, a hotel or dates. Empty for the next one.",
            },
            "check_in": {
                "type": "boolean",
                "description": "Also check them in online for each flight when check-in opens.",
            },
        },
    },
)

MONEY = ToolSpec(
    "money",
    "Penny, the money teammate. action 'summary': what they spent in a month, from the "
    "receipts in their Gmail, by category and against their budget ('how much did I spend last "
    "month?', 'where's my money going?', 'am I over budget?'); monthly=true also sends that "
    "summary on the 1st of every month. action 'sheet': that month's receipts into a "
    "spreadsheet (a new Excel file, or `target`: their own .xlsx path or a Google Sheet link "
    "or name, which Ledger adds the rows to). action 'budget': set a monthly budget (`amount`, "
    "optional `category` like 'groceries' or 'eating out'; amount 0 removes it). Summary and "
    "sheet run in the background: say one short line ('On it.'). Subscriptions, bills and "
    "price checks have their own tools.",
    {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["summary", "sheet", "budget", "monthly_off"]},
            "month": {
                "type": "string",
                "description": "In their words: 'this month', 'last month', 'October', '2026-09'.",
            },
            "monthly": {"type": "boolean"},
            "amount": {"type": "number"},
            "category": {"type": "string"},
            "target": {"type": "string"},
        },
        "required": ["action"],
    },
)

MEETING = ToolSpec(
    "meeting_notes",
    "Scribe, the meetings teammate: transcribes a recording of a meeting, lecture or call on "
    "this PC (audio or video: .m4a, .mp3, .wav, .mp4, .webm...) right here, then writes notes, "
    "decisions and action items, and drafts a follow-up email. Runs in the background; the "
    "notes arrive as a notification. `file` is the recording's full path (search_files first "
    "if they only named it). `to`: the other people's email addresses, only if they want the "
    "follow-up sent (look them up with gmail_people first); they approve it on a card before "
    "anything is sent. Say one short line ('On it.').",
    {
        "type": "object",
        "properties": {
            "file": {"type": "string"},
            "to": {"type": "array", "items": {"type": "string"}},
            "about": {"type": "string", "description": "What the meeting was about, if they said."},
            "language": {"type": "string", "description": "'en', or 'auto' for another language."},
        },
        "required": ["file"],
    },
)

FLASHCARDS = ToolSpec(
    "make_flashcards",
    "Sage, the study teammate: turns lecture slides, a PDF or notes on this PC into flashcards "
    "(a CSV that Anki and Quizlet import) and a multiple-choice quiz, in the background. "
    "`files` are full paths (search_files first if they only named them). Say one short line "
    "('On it.'); afterwards they can say 'quiz me'.",
    {
        "type": "object",
        "properties": {
            "files": {"type": "array", "items": {"type": "string"}},
            "count": {"type": "integer", "description": "How many flashcards (default 20)."},
            "focus": {"type": "string", "description": "A part to focus on, if they said."},
        },
        "required": ["files"],
    },
)

QUIZ = ToolSpec(
    "quiz",
    "Quiz them out loud from their flashcards, one question per turn. action 'start' (from the "
    "latest deck, or `deck`: a topic or file name), 'again' (only the ones they missed), "
    "'answer' (after they reply: `right` true if it means the same as the card's answer, "
    "generous with wording, strict on facts; `said` is what they said), 'stop'. The result "
    "tells you what to say next. Never say the answer before they've tried.",
    {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["start", "again", "answer", "stop"]},
            "deck": {"type": "string"},
            "questions": {"type": "integer", "description": "How many questions (default 10)."},
            "right": {"type": "boolean"},
            "said": {"type": "string"},
        },
        "required": ["action"],
    },
)

FIX_CODE = ToolSpec(
    "fix_code",
    "Patch, the coding teammate: works out why a script on this PC fails and proposes the "
    "smallest fix, shown as a diff on a card; the file changes only if they press Allow (a "
    "backup is kept). `error`: the full error text as it is on screen (stack trace, compiler "
    "message, the squiggle's tooltip), copied exactly. `file`: the script's full path when the "
    "error doesn't include it (from the editor's title bar or search_files), with `line` if "
    "you know it. Runs in the background: say one short line.",
    {
        "type": "object",
        "properties": {
            "error": {"type": "string"},
            "file": {"type": "string"},
            "line": {"type": "integer"},
            "ask": {"type": "string", "description": "What they asked, in their words."},
        },
    },
)

WATCH = ToolSpec(
    "watch_me",
    "Ditto, 'watch me once, then do the rest': for a repetitive job (renaming a folder of "
    "invoices, filing downloads, filling a form per spreadsheet row). action 'start' when they "
    "offer to show you: Ditto watches the screen (which window, button and field, what they "
    "typed; never passwords) while they do ONE item; `folder` is where the items are, if "
    "they said. action 'stop' when they say they're done: Ditto works out the pattern and "
    "shows the plan for the rest on a card, choosing files, Pixel, Ledger or Scout in the "
    "background before ever using the mouse. `items` only if they listed what's left. "
    "'cancel' stops watching; 'undo' puts back the last batch of renamed files.",
    {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["start", "stop", "cancel", "undo"]},
            "task": {"type": "string", "description": "The job, in their words."},
            "folder": {
                "type": "string",
                "description": "Where the items are (a path, or Desktop/Downloads/Documents).",
            },
            "items": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["action"],
    },
)

FIND = ToolSpec(
    "find_again",
    "Trace, the memory teammate: 'where did I see that pricing page last week?', 'which email "
    "had the Acme quote?', 'that PDF about the lease I opened on Tuesday'. Looks through their "
    "screen history (the Memory Palace), their files (if file search is on) and their mail at "
    "once, and answers with the exact place: the window and the moment, the file's path, or "
    "the email with its link. Runs in the background: say one short line ('Looking.').",
    {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "What they're looking for, in their words."},
            "when": {
                "type": "string",
                "description": "Time words if they said: 'last week', 'on Tuesday'.",
            },
        },
        "required": ["query"],
    },
)

ROLES = set(RUNNERS)  # their jobs' notifications come from job.data (title, body)
SPECS: list[ToolSpec] = [TRAVEL, MONEY, MEETING, FLASHCARDS, QUIZ, FIX_CODE, WATCH, FIND]
TOOLS = {s.name for s in SPECS}
INFO: set[str] = {"quiz"}  # tools whose result goes back to the model for another round


async def handle(brain: Brain, name: str, args: dict[str, Any]) -> str | None:
    if name == "travel":
        return await _travel(brain, args)
    if name == "money":
        return await _money(brain, args)
    if name == "meeting_notes":
        return await _meeting(brain, args)
    if name == "find_again":
        return await _find(brain, args)
    if name == "watch_me":
        return await _watch(brain, args)
    if name == "fix_code":
        return await _fix_code(brain, args)
    if name == "make_flashcards":
        return await _study(brain, {**args, "action": "make"})
    if name == "quiz":
        action = str(args.get("action") or "start")
        return await _study(brain, {**args, "action": "quiz" if action == "start" else action})
    return None


async def after_job(brain: Brain, job: Job) -> None:
    """Follow-ups when a specialist's job ends. A problem here never breaks the card."""
    try:
        if job.role == "travel":
            await after_trip(brain, job)
        elif job.role == "money":
            await after_money(brain, job)
        elif job.role == "meetings":  # the card can wait half an hour: not inside the job
            brain._spawn(after_meeting(brain, job))
        elif job.role == "coder":  # the same: the card waits for them
            brain._spawn(after_fix(brain, job))
        elif job.role == "automation":
            brain._spawn(after_repeat(brain, job))
    except Exception:
        log.exception("after %s's job", job.agent_name)


def _share_google(brain: Brain) -> bool:
    """The teammates' jobs read mail through the brain's own Google connection."""
    if not brain.connectors.google.connected():
        return False
    brain.jobs.google = brain.connectors.google
    return True


# ---------------------------------------------------------------------------------- Atlas
async def _travel(brain: Brain, args: dict[str, Any]) -> str:
    if not _share_google(brain):
        return (
            "travel: Gmail isn't connected, so Atlas can't read their bookings. Tell them to "
            "connect Google in Settings, in one sentence."
        )
    trip = str(args.get("trip") or "").strip()
    job = await brain.jobs.start(
        "atlas",
        f"Itinerary: {trip or 'your next trip'}",
        params={"trip": trip, "check_in": bool(args.get("check_in"))},
    )
    return (
        f"Started {job.id}: {job.agent_name} is reading their travel mail in the background. "
        "Say one short line ('On it.'); the itinerary comes as a notification."
    )


async def after_trip(brain: Brain, job: Job) -> list[str]:
    """Save the trip's reminders (once each), and schedule check-ins if they asked."""
    data = job.data if isinstance(job.data, dict) else {}
    if job.status != "done":
        return []
    added = []
    for r in data.get("reminders") or []:
        if brain.routines.has_key(r["key"]):
            continue
        brain.routines.add(
            r["title"],
            on=r["on"],
            time=r["time"],
            kind="remind",
            message=r["message"],
            key=r["key"],
        )
        added.append(r["key"])
    if added:
        await brain.hub.emit("routines", routines=brain.routines.public())
    if job.params.get("check_in"):
        from pyxi.companion import paperwork  # the flight check-in: Scout, when it opens

        for f in data.get("flights") or []:
            if not f.get("ref"):
                continue
            note = await paperwork.handle(
                brain,
                "check_in_flight",
                {
                    "confirmation": f["ref"],
                    "departure": f["depart"],
                    "airline": f.get("airline") or "",
                    "flight": f["flight"],
                    "from": f.get("origin_city") or f.get("origin") or "",
                    "url": f.get("checkin_url") or "",
                },
            )
            log.info("atlas check-in %s: %s", f["flight"], (note or "")[:120])
            if (note or "").startswith("check_in_flight:") and "already" not in note:
                need = "the last name on the booking" if "last name" in note else "a few details"
                await brain._notify(
                    job.id,
                    ok=False,
                    title=f"Check-in for {f['flight']} isn't set up",
                    body=f"I need {need}. Tell me and I'll check you in when it opens.",
                )
                break
    return added


# ---------------------------------------------------------------------------------- Penny
def schedule_monthly(brain: Brain, now: datetime | None = None) -> str | None:
    """Next month's summary of the month before, on the 1st at 09:00 (once)."""
    now = now or datetime.now()
    when = (now.replace(day=1) + timedelta(days=32)).replace(day=1, hour=9, minute=0)
    key = f"penny:monthly:{when:%Y-%m}"
    if brain.routines.has_key(key):
        return None
    brain.routines.add(
        "Penny: last month's spending",
        on=when.date().isoformat(),
        time="09:00",
        kind="job",
        key=key,
        job={
            "agent": "penny",
            "task": "Spending: last month",
            "params": {"action": "summary", "month": "last month", "monthly": True},
        },
        now=now,
    )
    return f"{when:%a} {when.day} {when:%b}"


async def _money(brain: Brain, args: dict[str, Any]) -> str:
    from pyxi.agents import penny

    action = str(args.get("action") or "summary")
    if action == "budget":
        try:
            amount = float(args.get("amount") or 0)
        except (TypeError, ValueError):
            return "money: the budget needs an amount, as a number."
        said = penny.set_budget(brain.settings.data_dir, amount, str(args.get("category") or ""))
        return f"money: {said} Say that in a few words."
    if action == "monthly_off":
        for r in [r for r in brain.routines.items if (r.key or "").startswith("penny:monthly")]:
            brain.routines.remove(r.id)
        await brain.hub.emit("routines", routines=brain.routines.public())
        return "money: the monthly summary is off. Say so in a few words."
    if not _share_google(brain):
        return (
            "money: Gmail isn't connected, so Penny can't read their receipts. Tell them to "
            "connect Google in Settings, in one sentence."
        )
    month = str(args.get("month") or "this month").strip()
    target = str(args.get("target") or "").strip().strip('"')
    own_file = action == "sheet" and target.lower().endswith((".xlsx", ".xlsm"))
    if own_file and not Path(target).expanduser().is_file():
        return f"money: there's no spreadsheet at {target}. Find it with search_files first."
    params: dict[str, Any] = {"action": "sheet" if action == "sheet" else "summary", "month": month}
    if action == "sheet" and target:
        params["target"] = target
    if args.get("monthly"):
        params["monthly"] = True
    what = "Receipts to a spreadsheet" if action == "sheet" else "Spending"
    job = await brain.jobs.start("penny", f"{what}: {month}", params=params)
    return (
        f"Started {job.id}: {job.agent_name} is reading their receipts in the background. "
        "Say one short line ('On it.'); the result comes as a notification."
    )


async def after_money(brain: Brain, job: Job) -> None:
    """A monthly summary books the next one (it also starts the series the first time)."""
    if job.params.get("monthly") and schedule_monthly(brain):
        await brain.hub.emit("routines", routines=brain.routines.public())


# ---------------------------------------------------------------------------------- Scribe
async def _meeting(brain: Brain, args: dict[str, Any]) -> str:
    from pyxi.agents import scribe

    raw = str(args.get("file") or "").strip().strip('"')
    path = Path(raw).expanduser() if raw else None
    if path is None or not path.is_file():
        return (
            f"meeting_notes: there's no recording at {raw or '(no path)'}. Find it with "
            "search_files (kind audio or video) first, or ask them where it is."
        )
    if path.suffix.lower() not in scribe.AUDIO:
        return f"meeting_notes: {path.name} isn't an audio or video file."
    to = args.get("to") or []
    to = [str(t).strip() for t in (to if isinstance(to, list) else [to]) if str(t).strip()]
    brain.jobs.stt = brain.stt  # the warm model push-to-talk already uses
    job = await brain.jobs.start(
        "scribe",
        f"Meeting notes: {path.name}",
        files=[str(path)],
        params={
            "to": to,
            "about": str(args.get("about") or ""),
            "language": str(args.get("language") or "en"),
        },
    )
    return (
        f"Started {job.id}: {job.agent_name} is listening to {path.name} in the background. "
        "Say one short line ('On it.'); the notes come as a notification."
    )


async def after_meeting(brain: Brain, job: Job) -> bool:
    """The follow-up email, on an approval card. Sent only on Allow; True if it was sent."""
    data = job.data if isinstance(job.data, dict) else {}
    draft = data.get("follow_up") or {}
    to = [str(t) for t in job.params.get("to") or []]
    if job.status != "done" or not draft.get("body") or not to:
        return False
    if not brain.connectors.google.connected():
        return False
    out = await brain.connectors.google.gmail_send(
        {"to": ", ".join(to), "subject": draft.get("subject") or "Follow-up", "body": draft["body"]}
    )
    if isinstance(out, str):  # a problem with the addresses: say so on the card's place
        await brain._notify(job.id, ok=False, title="The follow-up wasn't sent", body=out[:200])
        return False
    if not await brain._within_limits(out.channel, out.action):  # the hourly email limit
        return False
    ok = await brain.hub.request(
        "confirm",
        timeout=1800,
        job_id=job.id,
        agent=job.agent_name,
        action=out.action,
        reason=out.preview,
    )
    if not ok:
        return False
    try:
        line = await out.run()
        await brain._count_sent(out)
    except Exception as exc:
        log.exception("follow-up email failed")
        await brain._notify(
            job.id, ok=False, title="The follow-up didn't send", body=str(exc)[:200]
        )
        return False
    await brain._notify(job.id, ok=True, title="Follow-up sent", body=line[:200])
    return True


# ---------------------------------------------------------------------------------- Sage
async def _study(brain: Brain, args: dict[str, Any]) -> str:
    from pyxi.agents import sage

    action = str(args.get("action") or "make")
    quiz: sage.Quiz | None = getattr(brain, "study_quiz", None)
    if action == "make":
        raw = args.get("files") or []
        paths = [
            Path(str(f).strip().strip('"')).expanduser()
            for f in (raw if isinstance(raw, list) else [raw])
            if str(f).strip()
        ]
        missing = [p for p in paths if not p.is_file()]
        if not paths or missing:
            names = ", ".join(p.name for p in missing) or "the notes"
            return f"make_flashcards: I can't find {names}. Find the exact path with search_files first."
        odd = [p for p in paths if p.suffix.lower() not in sage.READABLE]
        if odd:
            return f"make_flashcards: Sage can't read {odd[0].name} (slides, PDFs, Word and text files work)."
        job = await brain.jobs.start(
            "sage",
            f"Flashcards: {', '.join(p.name for p in paths[:3])}",
            files=[str(p) for p in paths],
            params={"count": int(args.get("count") or 20), "focus": str(args.get("focus") or "")},
        )
        return (
            f"Started {job.id}: {job.agent_name} is making the cards in the background. Say one "
            "short line ('On it.'); they get a notification, then can say 'quiz me'."
        )
    if action in ("quiz", "again"):
        if action == "again" and quiz and quiz.missed:
            quiz = sage.Quiz.start(quiz.deck, len(quiz.missed), only=list(quiz.missed))
        else:
            deck = sage.find_deck(brain.settings.data_dir, str(args.get("deck") or ""))
            if deck is None or not deck.cards:
                return (
                    "quiz: there are no flashcards yet. Offer to make some from their slides or "
                    "notes (action 'make' with the file)."
                )
            quiz = sage.Quiz.start(deck, int(args.get("questions") or 10))
        brain.study_quiz = quiz
        return (
            f"quiz: on {quiz.deck.title}, {len(quiz.order)} questions. Say one short line to "
            f"start, then ask the first question out loud, word for word, and stop there.\n{quiz.ask()}"
        )
    if action == "stop":
        brain.study_quiz = None
        if quiz is None:
            return "quiz: there's no quiz going."
        return f"quiz: quiz stopped at {quiz.score()}. Say so in a few words."
    if action == "answer":
        if quiz is None or quiz.done:
            return "quiz: there's no question waiting. Offer a quiz."
        card = quiz.current()
        right = bool(args.get("right"))
        quiz.mark(right)
        verdict = (
            "They got it: say so in a few words."
            if right
            else f"Not quite: kindly give the answer in one sentence ({card.a})."
        )
        if quiz.done:
            brain.study_quiz = quiz  # kept for "again"
            missed = len(quiz.missed)
            return (
                f"quiz: {verdict} That was the last one: {quiz.score()} right. Tell them the "
                "score warmly"
                + (f" and offer to go again on the {missed} they missed." if missed else ".")
            )
        return (
            f"quiz: {verdict} Then ask the next one, word for word, and stop there.\n{quiz.ask()}"
        )
    return f"quiz: unknown action {action!r}."


# ---------------------------------------------------------------------------------- Patch
async def _fix_code(brain: Brain, args: dict[str, Any]) -> str:
    from pyxi.agents import patch

    error = str(args.get("error") or "").strip()
    raw = str(args.get("file") or "").strip().strip('"')
    if raw:
        path = Path(raw).expanduser()
        if not path.is_file():
            return f"fix_code: there's no file at {raw}. Find it with search_files, or ask them."
        loc = patch.Location(str(path), int(args.get("line") or 0))
    else:
        found = patch.locations(error)
        if not found:
            return (
                "fix_code: the error doesn't name a file on this PC. Ask them which script it "
                "is (or read the editor's title bar), then call fix_code with `file`."
            )
        loc = found[0]
    path = Path(loc.path)
    if path.suffix.lower() not in patch.CODE:
        return f"fix_code: {path.name} isn't a script Patch edits."
    job = await brain.jobs.start(
        "patch",
        f"Fix: {path.name}" + (f" line {loc.line}" if loc.line else ""),
        files=[str(path)],
        params={"error": error[-6000:], "line": loc.line, "ask": str(args.get("ask") or "")},
    )
    return (
        f"Started {job.id}: {job.agent_name} is looking at {path.name} in the background. "
        "Say one short line; the fix comes on a card they approve before anything changes."
    )


async def after_fix(brain: Brain, job: Job) -> bool:
    """The diff on an approval card; the file changes only on Allow. True if it was applied."""
    from pyxi.agents import patch

    data = job.data if isinstance(job.data, dict) else {}
    if job.status != "done" or not data.get("fix"):
        return False
    fix = patch.Fix(**data["fix"])
    name = Path(fix.path).name
    ok = await brain.hub.request(
        "confirm",
        timeout=1800,
        job_id=job.id,
        agent=job.agent_name,
        action=f"Apply this fix to {name}?",
        reason=(f"{fix.why}\n\n" if fix.why else "")
        + fix.diff  # the whole change, exactly as it will be written (the card scrolls)
        + "\n\nA backup of the file is kept beside it.",
    )
    if not ok:
        return False
    try:
        backup = await asyncio.to_thread(patch.apply, fix)
        brain.audit.add(
            "wrote",
            f"Changed line {fix.line} of {name}",
            agent=job.agent_name,
            detail=fix.diff,
            undo={"type": "patch", "path": fix.path, "backup": str(backup)},
        )
    except (OSError, RuntimeError, UnicodeDecodeError) as exc:
        await brain._notify(job.id, ok=False, title=f"{name} wasn't changed", body=str(exc)[:200])
        return False
    await brain._notify(
        job.id,
        ok=True,
        title=f"Fixed {name}",
        body=f"Line {fix.line} changed. The old version is saved as {backup.name}.",
        files=[fix.path],
    )
    brain.history = [
        *brain.history[-11:],
        (
            f"(applied {job.agent_name}'s fix to {fix.path})",
            f"(notified: fixed {name}, backup {backup.name})",
        ),
    ]
    return True


# ---------------------------------------------------------------------------------- Ditto
async def _watch(brain: Brain, args: dict[str, Any]) -> str:
    from pyxi.agents import ditto

    action = str(args.get("action") or "start")
    rec: ditto.Recorder | None = getattr(brain, "ditto_recorder", None)
    out_dir = brain.settings.data_dir / "watch"
    if action == "start":
        if rec is not None:
            return "watch_me: already watching. Tell them to say 'done' when they've finished one."
        folder = str(args.get("folder") or "").strip().strip('"')
        if folder and not Path(folder).expanduser().is_dir():
            from pyxi import tidy

            known = tidy.known_folder(folder)
            if known is None:
                return f"watch_me: there's no folder {folder!r}. Ask them which folder the items are in."
            folder = str(known)

        async def snap() -> Any:
            ctx = await brain.screen.capture(pixels=True)
            if ctx.excluded or ctx.screenshot is None:
                return None
            img = ctx.screenshot.copy()
            img.thumbnail((960, 540))
            return img

        def log_step(step: ditto.Step) -> None:
            brain.activity.log(
                "watch",
                app=step.app,
                title=step.title,
                name=step.name,
                role=step.role,
                detail=step.line(),
            )

        rec = ditto.Recorder(
            str(args.get("task") or ""),
            str(Path(folder).expanduser()) if folder else "",
            out_dir=out_dir,
            snap=snap,
            log=log_step,
            excluded=lambda app, title: bool(brain.screen.is_excluded(_window(app, title))),
        )
        rec.start()
        brain.ditto_recorder = rec
        brain.ditto_job = await brain.jobs.track(
            "Ditto", watching_task(rec.rec.task), role="watching"
        )
        # Not a "watch_me:" note: those go back to the model for another round, and the reply
        # that started this already told them to go ahead.
        return "Watching now. They do one item the normal way and say 'done'."
    if action == "cancel":
        if rec is None:
            return "watch_me: nothing is being watched."
        await rec.stop()
        brain.ditto_recorder = None
        if chip := getattr(brain, "ditto_job", None):
            await brain.jobs.settle(chip, ok=False, summary="Stopped watching.")
        return "watch_me: stopped watching, nothing will be repeated. Say so in a few words."
    if action == "undo":
        logs = sorted((out_dir / "undo").glob("ditto-*.json"))
        if not logs:
            return "watch_me: there's nothing of mine to undo."
        n = await asyncio.to_thread(ditto.undo_files, logs[-1])
        logs[-1].unlink(missing_ok=True)
        return f"watch_me: put {n} files back the way they were. Say so in a few words."
    if action == "stop":
        if rec is None:
            return "watch_me: I wasn't watching. Offer to watch them do it once (action start)."
        recording = await rec.stop()
        brain.ditto_recorder = None
        if chip := getattr(brain, "ditto_job", None):
            await brain.jobs.settle(chip, ok=True, summary=f"Saw {len(recording.steps)} steps.")
        raw = args.get("items")
        items = [str(i) for i in raw] if isinstance(raw, list) else None
        job = await brain.jobs.start(
            "ditto",
            f"Repeat: {recording.task or 'what you showed me'}",
            params={"recording": str(out_dir / f"{recording.id}.json"), "items": items},
        )
        return (
            f"Started {job.id}: saw {len(recording.steps)} steps; {job.agent_name} is working out "
            "the rest in the background. Say one short line; the plan comes on a card first."
        )
    return f"watch_me: unknown action {action!r}."


def watching_task(task: str) -> str:
    """The tray chip's text, and what the model sees under "Teammates' jobs" next turn."""
    return f"Watching: {(task or 'your task')[:40]}\non 'done': watch_me stop"


def _window(app: str, title: str) -> Any:
    from pyxi.screen import WindowInfo

    return WindowInfo(title, app)


ROUTE_NOTE = {
    "files": "Renames and moves the files right here (you can undo it).",
    "convert": "Pixel converts each file in the background; the originals stay.",
    "sheet": "Ledger edits the spreadsheet (a copy is saved beside it).",
    "web": "Scout does each one in a background browser; anything that submits or pays asks you.",
    "mouse": "This one needs your mouse and keyboard: don't touch them while it runs. Move the "
    "mouse or press Esc to stop.",
}


async def after_repeat(brain: Brain, job: Job) -> str:
    """Ditto's plan on a card, then done the quietest way. Returns what happened."""
    from pyxi.agents import ditto

    data = job.data if isinstance(job.data, dict) else {}
    if job.status != "done" or not data.get("plan"):
        return "no plan"
    plan = ditto.Plan(**data["plan"])
    folder = Path(data.get("folder") or ".")
    preview = "\n".join(
        f"{it['item']}  ->  {it['to']}" if it.get("to") else it["item"] for it in plan.items[:6]
    )
    more = f"\n... and {len(plan.items) - 6} more" if len(plan.items) > 6 else ""
    ok = await brain.hub.request(
        "confirm",
        timeout=1800,
        job_id=job.id,
        agent=job.agent_name,
        action=f"Do the other {len(plan.items)} the same way?",
        reason=f"{plan.summary}\n{ROUTE_NOTE[plan.route]}\n\n{preview}{more}",
    )
    if not ok:
        return "declined"
    items = [it["item"] for it in plan.items]
    if plan.route == "files":
        moves = ditto.check_files(plan, folder)
        undo_dir = brain.settings.data_dir / "watch" / "undo"
        done, undo_log = await asyncio.to_thread(ditto.do_files, moves, undo_dir)
        if done:
            brain.audit.add(
                "moved",
                f"Renamed or moved {done} files in {folder.name}",
                agent=job.agent_name,
                detail=str(folder),
                undo={"type": "ditto", "log": str(undo_log)},
            )
        skipped = len(plan.items) - done
        body = f"{plan.summary} " if done else ""
        if skipped:
            body += f"{skipped} skipped (a name clash or a missing file). "
        if done:
            body += "Say 'undo that' to put them back."
        await brain._notify(
            job.id,
            ok=done > 0,
            title=f"Done {done} more" if done else "Nothing was changed",
            body=body.strip(),
            changes=[f"{s.name} -> {d.relative_to(folder)}" for s, d in moves[:6]],
        )
        return f"files:{done}"
    if plan.route == "convert":
        from pyxi.agents.pixel import OPERATIONS

        if plan.operation not in OPERATIONS:
            await brain._notify(
                job.id,
                ok=False,
                title="I couldn't repeat that",
                body="Pixel can't do that conversion.",
            )
            return "bad operation"
        files = [str(folder / i) for i in items if (folder / i).is_file()]
        await brain.jobs.start(
            "pixel",
            f"{plan.operation.replace('_', ' ')}: {len(files)} files",
            files=files,
            params={"operation": plan.operation, "options": {}},
        )
        return "convert"
    if plan.route == "sheet":
        target = [
            str(folder / i) for i in items if (folder / i).suffix.lower() in (".xlsx", ".xlsm")
        ]
        await brain.jobs.start("ledger", plan.sheet_task or plan.summary, files=target[:1])
        return "sheet"
    steps = "\n".join(f"{n + 1}. {s}" for n, s in enumerate(plan.steps))
    if plan.route == "web":
        for item in items:
            task = (
                f"Repeat what the user did once, for this item: {item}\n"
                f"Steps:\n{steps.replace('{item}', item)}"
            )
            await brain.jobs.start("scout", task, start_url=plan.url or None)
        return f"web:{len(items)}"
    # mouse: the last resort, one run of the hands for a few items at a time
    batch = items[: ditto.MAX_MOUSE_ITEMS]
    task = (
        f"Do this for each of these {len(batch)} items, one after another: {', '.join(batch)}. "
        f"For each one: {steps}"
    )
    brain._spawn(brain._drive(task, None))
    return f"mouse:{len(batch)}"


# ---------------------------------------------------------------------------------- Trace
async def _find(brain: Brain, args: dict[str, Any]) -> str:
    from pyxi import files_access

    query = str(args.get("query") or "").strip()
    if not query:
        return "find_again: what are they looking for? Ask for a word or two from it."
    _share_google(brain)
    brain.jobs.spatial = brain.spatial  # the Memory Palace's screen history
    access = files_access.load(brain.settings.data_dir)
    job = await brain.jobs.start(
        "trace",
        f"Finding: {query[:60]}",
        params={"query": query, "when": str(args.get("when") or "")},
    )
    files = (
        "" if access.enabled else " (Their files aren't searched: file search is off in Settings.)"
    )
    return (
        f"Started {job.id}: {job.agent_name} is looking through their screen history, files and "
        f"mail. Say one short line ('Looking.'); the answer comes as a notification.{files}"
    )
