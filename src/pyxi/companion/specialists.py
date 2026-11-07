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

:func:`after_job` runs when any specialist's job ends (the brain calls it from ``_job_done``),
so a job started by a routine or a hand-off gets the same follow-up as one started here.
"""

from __future__ import annotations

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

ROLES = set(RUNNERS)  # their jobs' notifications come from job.data (title, body)
SPECS: list[ToolSpec] = [TRAVEL, MONEY, MEETING, FLASHCARDS, QUIZ]
TOOLS = {s.name for s in SPECS}
INFO: set[str] = {"quiz"}  # tools whose result goes back to the model for another round


async def handle(brain: Brain, name: str, args: dict[str, Any]) -> str | None:
    if name == "travel":
        return await _travel(brain, args)
    if name == "money":
        return await _money(brain, args)
    if name == "meeting_notes":
        return await _meeting(brain, args)
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
