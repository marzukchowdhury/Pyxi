"""Scenarios for the specialist teammates (companion/specialists.py), plugged into
scripts/scenarios.py: the requests, the fakes that keep them off real accounts, and the checks.

Every specialist tool is replaced by a recorder, so a scenario only checks the routing (the
right tool with the right arguments, and one short line), never touches mail, files or apps.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from pyxi.agents.penny import month_range

HOME = "turn_49b75541"  # an ordinary desktop

SCENARIOS = [
    # Atlas, travel: itineraries from the confirmations in their mail.
    (
        "trip plan",
        HOME,
        "Can you put together my itinerary for the Toronto trip from my emails?",
        "travel",
    ),
    ("trip flight time", HOME, "When's my flight to Toronto?", "travel"),
    ("trip hotel", HOME, "Where am I staying in Lisbon next month?", "travel"),
    (
        "trip remind",
        HOME,
        "Remind me before my flight next week, and check me in when it opens.",
        "travel_checkin",
    ),
    # Penny, money: spending from receipts, budgets, receipts into a spreadsheet.
    ("money month", HOME, "How much did I spend last month?", "money:summary"),
    ("money over", HOME, "Am I over budget this month?", "money:summary"),
    ("money budget", HOME, "Set my monthly budget to $1,500.", "money:budget"),
    ("money sheet", HOME, "Put my October receipts in a spreadsheet.", "money:sheet"),
    # Scribe, meetings: a recording into notes, a follow-up only through the card.
    (
        "meeting notes",
        HOME,
        "Write up the notes from the team standup recording on my desktop.",
        "meeting",
    ),
    (
        "meeting follow up",
        HOME,
        "Get the action items from the team standup recording on my desktop and email the "
        "follow-up to sam.rahman@uni.edu.",
        "meeting_to",
    ),
    # Sage, study: flashcards from slides, a quiz out loud, diagrams explained on the picture.
    (
        "study make",
        "turn_f05f1ae6",
        "Make flashcards from my Lecture 4 slides on the desktop.",
        "study:make",
    ),
    ("study quiz", HOME, "Quiz me on the heart.", "study:quiz"),
    (
        "study diagram",
        "turn_480a31d9",
        "Explain this diagram, I have an exam on it tomorrow.",
        "study:diagram",
    ),
    # Patch, code: an error in a terminal, explained on screen, the fix on a card.
    ("code error", HOME, "What's this error? Can you fix it?", "code"),
    ("code why", HOME, "Why does my script keep crashing?", "code"),
    # Ditto, watch me once: start watching, then "done" hands it over (never the mouse here).
    (
        "watch start",
        HOME,
        "I have to rename all the invoices in my Downloads by date and company. Let me show "
        "you one and then you do the rest.",
        "watch:start",
    ),
    ("watch done", HOME, "Okay, done, that's the first one.", "watch:stop"),
    # Trace, memory: screen history, files and mail at once, with the exact place.
    ("find pricing", HOME, "Where did I see that pricing page last week?", "find"),
    ("find lease", HOME, "Find that PDF about the lease I had open on Tuesday.", "find"),
    ("find quote", HOME, "Which email had the Acme quote in it?", "find_or_mail"),
]

TRACE = [
    "PS C:\\Users\\alex\\Desktop> python report.py",
    "Traceback (most recent call last):",
    '  File "{folder}\\report.py", line 14, in <module>',
    '    print("Total:", total(rows))',
    '  File "{folder}\\report.py", line 10, in total',
    '    return sum(r["amount"] for r in rows)',
    "TypeError: unsupported operand type(s) for +: 'float' and 'str'",
    "PS C:\\Users\\alex\\Desktop>",
]


def terminal_screen(folder: str) -> Any:
    """Windows Terminal showing a real-looking Python traceback from report.py in ``folder``."""
    import time

    from PIL import Image, ImageDraw

    from pyxi.screen.context import ScreenContext, WindowInfo
    from pyxi.screen.ocr import TextLine

    w, h = 1920, 1080
    shot = Image.new("RGB", (w, h), (12, 12, 12))
    draw = ImageDraw.Draw(shot)
    ocr = []
    for i, line in enumerate(TRACE):
        text = line.format(folder=folder)
        x, y, per = 40, 80 + i * 34, 11
        draw.text((x, y), text, fill=(255, 110, 110) if "Error" in text else (230, 230, 230))
        words, cx = [], x
        for word in text.split():
            words.append((word, (cx, y, per * len(word), 24)))
            cx += per * (len(word) + 1)
        ocr.append(TextLine(text, (x, y, per * len(text), 24), words, i + 1))
    window = WindowInfo("Windows PowerShell", "WindowsTerminal.exe", (0, 0, w, h))
    ctx = ScreenContext(time.time(), window, [], shot)
    ctx.text = ocr
    return ctx


def install(brain: Any, background: list[str], name: str = "") -> None:
    """Replace each specialist's work with a recorder."""
    from pyxi.companion import specialists

    async def fake_travel(brain_: Any, args: dict[str, Any]) -> str:
        background.append("travel: " + json.dumps(args)[:200])
        return "Started job_x: Atlas is reading their travel mail. Say one short line ('On it.')."

    async def fake_money(brain_: Any, args: dict[str, Any]) -> str:
        background.append("money: " + json.dumps(args)[:200])
        if args.get("action") == "budget":
            return "money: Monthly budget set. Say that in a few words."
        return "Started job_x: Penny is reading their receipts. Say one short line ('On it.')."

    async def fake_meeting(brain_: Any, args: dict[str, Any]) -> str:
        background.append("meeting: " + json.dumps(args)[:240])
        return "Started job_x: Scribe is listening to it. Say one short line ('On it.')."

    # A recording on the sample Desktop (the only folder file search may look in).
    from pyxi import files_access

    for folder in files_access.load(brain.settings.data_dir).folders[:1]:
        (Path(folder) / "Team standup Nov 6.m4a").write_bytes(b"\x00" * 64)

    # A deck from last week's lecture, for "quiz me" (the real quiz code runs: it's all local).
    from pyxi.agents import sage

    cards = [
        ("How many chambers does the heart have?", "Four: two atria and two ventricles."),
        ("What does the mitral valve stop?", "Blood flowing back into the left atrium."),
        ("Where does the right ventricle pump blood?", "To the lungs."),
    ]
    sage.save_deck(
        brain.settings.data_dir,
        sage.Deck("The heart", "Lecture 4 slides.pptx", [sage.Card(q, a) for q, a in cards]),
    )

    async def fake_fix(brain_: Any, args: dict[str, Any]) -> str:
        background.append("fix: " + json.dumps(args))
        return "Started job_x: Patch is looking at report.py. Say one short line; the fix comes on a card."

    allowed = files_access.load(brain.settings.data_dir).folders
    if name.startswith("code ") and allowed:
        (Path(allowed[0]) / "report.py").write_text("rows = []\n", encoding="utf-8")
        brain.screen.ctx = terminal_screen(allowed[0])
    specialists._fix_code = fake_fix  # type: ignore[assignment]

    async def fake_watch(brain_: Any, args: dict[str, Any]) -> str:
        background.append("watch: " + json.dumps(args)[:200])
        if args.get("action") == "start":
            return "Watching now. They do one item the normal way and say 'done'."
        return "Started job_x: saw 6 steps; Ditto is working out the rest. Say one short line."

    if name == "watch done":  # they asked to be watched a minute ago, and Ditto is watching
        from pyxi.agents.jobs import Job

        brain.history = [
            (
                "I have to rename all the invoices in my Downloads by date and company. Let me "
                "show you one and then you do the rest.",
                "Go ahead, rename one the normal way and say done.",
            )
        ]
        chip = Job(
            agent_id="ditto",
            agent_name="Ditto",
            role="watching",
            task=specialists.watching_task("rename the invoices by date and company"),
            status="running",
        )
        brain.jobs.jobs[chip.id] = chip
    specialists._watch = fake_watch  # type: ignore[assignment]

    async def fake_find(brain_: Any, args: dict[str, Any]) -> str:
        background.append("find: " + json.dumps(args)[:200])
        return "Started job_x: Trace is looking. Say one short line ('Looking.')."

    specialists._find = fake_find  # type: ignore[assignment]
    specialists._travel = fake_travel  # type: ignore[assignment]
    specialists._meeting = fake_meeting  # type: ignore[assignment]
    specialists._money = fake_money  # type: ignore[assignment]


def sentences(said: str) -> int:
    return len(re.findall(r"[.!?](\s|$)", said))


def problems(
    expect: str,
    tools: list[str],
    said: str,
    background: list[str],
    tool_calls: list[dict] | None = None,
) -> list[str]:
    out: list[str] = []
    travel = [b for b in background if b.startswith("travel: ")]
    if expect in ("travel", "travel_checkin"):
        checkins = [b for b in background if b.startswith("checkin: ")]
        if expect == "travel" and not travel:
            out.append("didn't hand the trip to Atlas")
        if expect == "travel_checkin":
            if not travel and not checkins:
                out.append("neither Atlas nor the check-in")
            if travel and not checkins and '"check_in": true' not in travel[0]:
                out.append("asked to check in, but check_in wasn't set")
        if (
            travel
            and expect == "travel"
            and re.search(r"toronto|lisbon", said + " ".join(travel), re.I) is None
        ):
            out.append("lost which trip")
        if "use_computer" in tools:
            out.append("drove the mouse for a trip")
        if sentences(said) > 2:
            out.append(f"talks too much ({len(said)} chars)")
    if expect.startswith("money:"):
        want = expect.split(":", 1)[1]
        calls = [json.loads(b[len("money: ") :]) for b in background if b.startswith("money: ")]
        if not calls:
            out.append("didn't hand it to Penny")
        elif calls[0].get("action") != want:
            out.append(f"Penny got action {calls[0].get('action')!r}, not {want!r}")
        elif want == "budget" and float(calls[0].get("amount") or 0) != 1500:
            out.append(f"wrong budget: {calls[0].get('amount')}")
        elif (
            want == "sheet"
            and month_range(str(calls[0].get("month", "")), datetime.now())[0].month != 10
        ):
            out.append("lost the month")
        if "use_computer" in tools:
            out.append("drove the mouse for money")
        if sentences(said) > 2:
            out.append(f"talks too much ({len(said)} chars)")
    if expect in ("meeting", "meeting_to"):
        calls = [json.loads(b[len("meeting: ") :]) for b in background if b.startswith("meeting: ")]
        if not calls:
            out.append("didn't hand the recording to Scribe")
        elif not str(calls[0].get("file", "")).endswith("Team standup Nov 6.m4a"):
            out.append(f"wrong file: {calls[0].get('file')}")
        elif expect == "meeting_to" and "sam.rahman@uni.edu" not in json.dumps(calls[0].get("to")):
            out.append("lost who the follow-up goes to")
        elif expect == "meeting" and calls[0].get("to"):
            out.append("set a follow-up nobody asked to send")
        if re.search(r"\b(sent|emailed)\b", said, re.I):
            out.append("claimed the follow-up was sent")
        if "use_computer" in tools or "gmail_send" in tools:
            out.append("sent or clicked instead of the card")
        if sentences(said) > 2:
            out.append(f"talks too much ({len(said)} chars)")
    if expect.startswith("study:"):
        want = expect.split(":", 1)[1]
        if want == "make" and not any(
            b.startswith("delegate sage") and "Lecture 4 slides.pptx" in b for b in background
        ):
            out.append("didn't hand the slides to Sage")
        if want == "quiz":
            if "quiz" not in tools:
                out.append("didn't start the quiz")
            elif "?" not in said:
                out.append("didn't ask a question")
            if re.search(r"two atria|left atrium|to the lungs", said, re.I):
                out.append("gave the answer away")
        if want == "diagram":
            pics = [c for c in tool_calls or [] if c["tool"] == "explain_on_screen"]
            if not pics or pics[0]["arguments"].get("style") != "on_picture":
                out.append("didn't explain it on the picture")
        if "use_computer" in tools:
            out.append("drove the mouse for studying")
        if want == "make" and sentences(said) > 2:
            out.append(f"talks too much ({len(said)} chars)")
    if expect == "code":
        fixes = [json.loads(b[len("fix: ") :]) for b in background if b.startswith("fix: ")]
        if not fixes:
            out.append("didn't hand it to Patch")
        elif "TypeError" not in fixes[0].get("error", "") or "report.py" not in json.dumps(
            fixes[0]
        ):
            out.append(f"lost the error text: {json.dumps(fixes[0])[:120]}")
        if "explain_on_screen" not in tools and not ({"point", "circle", "highlight"} & set(tools)):
            out.append("didn't point at the line")
        if re.search(r"\b(fixed it|it's fixed|all fixed)\b", said, re.I):
            out.append("claimed it was fixed before the card")
        if "use_computer" in tools:
            out.append("drove the mouse for code")
    if expect.startswith("watch:"):
        want = expect.split(":", 1)[1]
        watches = [json.loads(b[len("watch: ") :]) for b in background if b.startswith("watch: ")]
        if not watches or watches[0].get("action") != want:
            out.append(f"didn't {want} watching: {watches}")
        elif want == "start" and "download" not in str(watches[0].get("folder", "")).lower():
            out.append("lost the folder")
        if "use_computer" in tools or any(b.startswith("hands") for b in background):
            out.append("drove the mouse instead of watching")
        if sentences(said) > 2:
            out.append(f"talks too much ({len(said)} chars)")
    if expect in ("find", "find_or_mail"):
        finds = [json.loads(b[len("find: ") :]) for b in background if b.startswith("find: ")]
        if not finds and not (expect == "find_or_mail" and "gmail_search" in tools):
            out.append("didn't hand it to Trace")
        if finds and not re.search(r"pricing|lease|acme|quote", finds[0].get("query", ""), re.I):
            out.append(f"lost what to look for: {finds[0]}")
        if "use_computer" in tools:
            out.append("drove the mouse to look")
        if finds and sentences(said) > 2:
            out.append(f"talks too much ({len(said)} chars)")
    return out
