"""Scenarios for the specialist teammates (companion/specialists.py), plugged into
scripts/scenarios.py: the requests, the fakes that keep them off real accounts, and the checks.

Every specialist tool is replaced by a recorder, so a scenario only checks the routing (the
right tool with the right arguments, and one short line), never touches mail, files or apps.
"""

from __future__ import annotations

import json
import re
from typing import Any

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
]


def install(brain: Any, background: list[str]) -> None:
    """Replace each specialist's work with a recorder."""
    from pyxi.companion import specialists

    async def fake_travel(brain_: Any, args: dict[str, Any]) -> str:
        background.append("travel: " + json.dumps(args)[:200])
        return "Started job_x: Atlas is reading their travel mail. Say one short line ('On it.')."

    specialists._travel = fake_travel  # type: ignore[assignment]


def sentences(said: str) -> int:
    return len(re.findall(r"[.!?](\s|$)", said))


def problems(expect: str, tools: list[str], said: str, background: list[str]) -> list[str]:
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
    return out
