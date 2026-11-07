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
]


def install(brain: Any, background: list[str]) -> None:
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
    return out
