"""The companion's side of the specialist teammates: their tools, and what happens around a job
(saving reminders, scheduling check-ins). Kept out of brain.py; the brain calls :func:`handle`.

Every tool here starts a background job and returns at once, so Pyxi says one short line and
the result arrives as a notification and a chip in the task tray.

* ``travel``: Atlas builds the itinerary of a trip from the confirmations in Gmail.
"""

from __future__ import annotations

import logging
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

ROLES = set(RUNNERS)  # their jobs' notifications come from job.data (title, body)
SPECS: list[ToolSpec] = [TRAVEL]
TOOLS = {s.name for s in SPECS}
INFO: set[str] = set()  # tools whose result goes back to the model for another round


async def handle(brain: Brain, name: str, args: dict[str, Any]) -> str | None:
    if name == "travel":
        return await _travel(brain, args)
    return None


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
        "atlas", f"Itinerary: {trip or 'your next trip'}", params={"trip": trip}
    )
    brain._spawn(after_trip(brain, job, bool(args.get("check_in"))))
    return (
        f"Started {job.id}: {job.agent_name} is reading their travel mail in the background. "
        "Say one short line ('On it.'); the itinerary comes as a notification."
    )


async def after_trip(brain: Brain, job: Job, check_in: bool) -> list[str]:
    """Save the trip's reminders (once each), and schedule check-ins if they asked."""
    job = await brain.jobs.wait(job.id)
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
    if check_in:
        try:  # the flight check-in (companion/paperwork.py) schedules Scout for when it opens
            from pyxi.companion import paperwork
        except ImportError:
            return added
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
