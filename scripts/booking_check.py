"""Book on the practice sites with the real Scout, end to end, and report what happened.

Starts the local practice sites (pyxi.demo_bookings), then runs two real booking jobs through
Pyxi's job manager with the real model and a real browser window: a dental cleaning at Maple
Grove Dental, and two cinema tickets at Aurora Cinemas. This script plays the user: it approves
the final booking click and, at the hand-over, "pays" on the practice site and presses Done.
Uses made-up details (Alex Tester, a 555 number). Nothing real is booked.

usage: python scripts/booking_check.py [clinic|cinema]
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path

from otto.config import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from conftest import FakeScreen
from pyxi import booking
from pyxi.companion.brain import Brain
from pyxi.config import PyxiSettings
from pyxi.demo_bookings import BookingSite
from pyxi.hub import Hub
from pyxi.voice import make_tts

ALEX = {
    "first_name": "Alex",
    "last_name": "Tester",
    "email": "alex.tester@example.com",
    "phone": "604-555-0100",
    "date_of_birth": "1994-05-17",
}
CASES = {
    "clinic": (
        {
            "what": "a dental cleaning and checkup",
            "where": "Maple Grove Dental",
            "when": "next Saturday, morning",
        },
        "/clinic",
    ),
    "cinema": (
        {
            "what": "2 tickets for Dune: Part Three",
            "where": "Aurora Cinemas",
            "when": "tonight around 7pm",
            "people": "2",
        },
        "/cinema",
    ),
}


async def run(name: str, site: BookingSite) -> dict:
    load_dotenv()
    data_dir = Path(tempfile.mkdtemp(prefix="pyxi_book_"))
    s = PyxiSettings(_env_file=None, data_dir=data_dir, voice={"tts": "client"})
    hub = Hub()
    brain = Brain(s, hub, screen=FakeScreen(), tts=make_tts(s.voice, s.models_dir))
    log: list[str] = []

    async def as_the_user(text: str) -> None:  # answer cards the way the user would
        e = json.loads(text)
        if e["type"] == "confirm":
            log.append(f"approve: {e.get('action')} ({e.get('reason', '')[:70]})")
            hub.resolve(e["request_id"], True)
        elif e["type"] == "ask":
            if e.get("handover"):
                log.append(f"hand-over: {e.get('question')}")
                site.pay()
                hub.resolve(e["request_id"], "done")
            else:
                log.append(f"question: {e.get('question')}")
                hub.resolve(e["request_id"], "Whatever is closest works.")
        elif e["type"] == "job_event" and e.get("event") == "step_finished":
            log.append(f"  step {e['data'].get('step')}")

    hub.connect(as_the_user)
    args, path = CASES[name]
    t0 = time.monotonic()
    job = await brain.jobs.start(
        "scout",
        booking.brief(args, ALEX, await brain._now()),
        start_url=site.url + path,
        kind="booking",
    )
    job = await brain.jobs.wait(job.id)
    return {
        "case": name,
        "status": job.status,
        "secs": round(time.monotonic() - t0, 1),
        "cost": round(job.cost_usd, 4),
        "steps": job.steps,
        "summary": job.summary,
        "data": job.data,
        "log": [x for x in log if not x.startswith("  step")],
    }


async def main() -> None:
    which = sys.argv[1:] or list(CASES)
    with BookingSite() as site:
        for name in which:
            r = await run(name, site)
            print(json.dumps(r, indent=2, default=str))
        print(
            "site now has:",
            json.dumps(
                {"appointments": site.state.appointments, "tickets": site.state.tickets},
                default=str,
            ),
        )


asyncio.run(main())
