"""Fill a form from a CV, and check in for a flight at a fake time, with the real Scout, end to end.

Starts the local practice sites (pyxi.demo_bookings) and runs real jobs through Pyxi's job
manager with the real model and a real browser:

* ``form``: "fill in the Northwind job application from my CV". A made-up CV (a Word file with
  a SIN in it, which Scout must never see) and the saved booking details. This script plays the
  user and approves the Submit.
* ``checkin``: "check me in when it opens" for a Skyward Air booking. The routine is scheduled
  24 hours before departure on a fake clock; the script moves the clock to that moment, the
  routine starts Scout, and any paid extra Scout asks about is declined.

Made-up details only (Alex Tester, a 555 number). Nothing real is sent, booked or checked in.

usage: python scripts/paperwork_check.py [form|checkin]
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

from otto.config import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from conftest import FakeScreen
from pyxi import booking, files_access
from pyxi.companion import paperwork
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
    "city": "Vancouver, BC",
}
CV = [
    ("Alex Tester", "Title"),
    ("Vancouver, BC . alex.tester@example.com . 604-555-0100", None),
    ("Experience", "Heading 1"),
    ("Data Analyst, Lumen Retail (Mar 2023 - present)", "Heading 2"),
    ("Weekly sales and inventory reports for 40 stores in SQL and Excel; cut stockouts 18%.", None),
    ("Built the store dashboard in Power BI used by every regional manager.", None),
    ("Analyst Intern, Harbour Credit Union (May 2022 - Dec 2022)", "Heading 2"),
    ("Cleaned and joined loan data in Python (pandas) for the risk team.", None),
    ("Education", "Heading 1"),
    ("BSc Statistics, University of British Columbia, 2022", None),
    ("Skills", "Heading 1"),
    ("SQL, Excel (pivot tables, Power Query), Python (pandas), Power BI, R basics", None),
    ("Work authorization", "Heading 1"),
    ("Canadian citizen. SIN: 046 454 286", None),
    ("Available from January 2027.", None),
]


def write_cv(folder: Path) -> Path:
    from docx import Document

    doc = Document()
    for text, style in CV:
        doc.add_paragraph(text, style=style) if style else doc.add_paragraph(text)
    path = folder / "Alex Tester CV.docx"
    doc.save(path)
    return path


def make(data_dir: Path) -> tuple[Brain, Hub]:
    load_dotenv()
    s = PyxiSettings(_env_file=None, data_dir=data_dir, voice={"tts": "client"})
    hub = Hub()
    return Brain(s, hub, screen=FakeScreen(), tts=make_tts(s.voice, s.models_dir)), hub


async def run_form(site: BookingSite) -> dict:
    data_dir = Path(tempfile.mkdtemp(prefix="pyxi_form_"))
    brain, hub = make(data_dir)
    booking.save(data_dir, ALEX)
    docs = data_dir / "Documents"
    docs.mkdir()
    cv = write_cv(docs)
    files_access.save(data_dir, files_access.FileAccess(True, [str(docs)], True))
    log: list[str] = []
    ready: list[dict] = []

    async def as_the_user(text: str) -> None:
        e = json.loads(text)
        if e["type"] == "confirm":
            log.append(f"approve: {e.get('action')}")
            hub.resolve(e["request_id"], True)
        elif e["type"] == "ask":
            log.append(f"{'hand-over' if e.get('handover') else 'question'}: {e.get('question')}")
            hub.resolve(e["request_id"], "done" if e.get("handover") else "Leave it empty.")
        elif e["type"] == "job_ready":
            ready.append(e)

    hub.connect(as_the_user)
    t0 = time.monotonic()
    note = await paperwork.handle(
        brain,
        "fill_form",
        {
            "what": "job application for Junior Data Analyst at Northwind",
            "url": site.url + "/jobs/apply",
            "documents": [str(cv)],
            "notes": "I can start in January.",
        },
    )
    job = next(iter(brain.jobs.jobs.values()))
    job = await brain.jobs.wait(job.id)
    await asyncio.sleep(0.2)
    app = site.state.applications[-1] if site.state.applications else {}
    return {
        "case": "form",
        "tool": note,
        "status": job.status,
        "secs": round(time.monotonic() - t0, 1),
        "cost": round(job.cost_usd, 4),
        "steps": job.steps,
        "summary": job.summary,
        "log": log,
        "notification": {k: ready[-1].get(k) for k in ("title", "body")} if ready else None,
        "application": app,
        "checks": {
            "submitted": bool(app),
            "employer from CV": "lumen" in app.get("current_employer", "").lower(),
            "SIN left empty": not app.get("sin"),
            "password left empty": not app.get("password"),
            "SIN never in Scout's task": "046 454" not in job.task,
            "approved before submit": any(x.startswith("approve: Click") for x in log),
        },
    }


async def run_checkin(site: BookingSite) -> dict:
    data_dir = Path(tempfile.mkdtemp(prefix="pyxi_checkin_"))
    brain, hub = make(data_dir)
    booking.save(data_dir, ALEX)
    departs = (datetime.now() + timedelta(days=2)).replace(second=0, microsecond=0)
    site.add_flight("K7Q2ZP", "Tester", departs)
    log: list[str] = []
    ready: list[dict] = []

    async def as_the_user(text: str) -> None:
        e = json.loads(text)
        if e["type"] == "confirm":  # a paid extra: the user says no
            log.append(f"declined: {e.get('action')} ({e.get('reason', '')[:60]})")
            hub.resolve(e["request_id"], False)
        elif e["type"] == "ask":
            log.append(f"question: {e.get('question')}")
            hub.resolve(e["request_id"], "No thanks.")
        elif e["type"] == "job_ready":
            ready.append(e)

    hub.connect(as_the_user)
    paperwork.clock = lambda: departs - timedelta(days=3)  # three days out: schedule it
    note = await paperwork.handle(
        brain,
        "check_in_flight",
        {
            "airline": "Skyward Air",
            "flight": "SK 482",
            "confirmation": "K7Q2ZP",
            "departure": departs.strftime("%Y-%m-%dT%H:%M"),
            "from": "YVR",
            "url": site.url + "/air",
        },
    )
    opens = departs - timedelta(hours=24)
    early = await brain.run_due_routines(opens - timedelta(minutes=1))
    # The clock reaches the moment check-in opens: for Pyxi and for the airline.
    site.state.clock = lambda: opens + timedelta(seconds=30)
    t0 = time.monotonic()
    ran = await brain.run_due_routines(opens + timedelta(seconds=30))
    job = next((j for j in brain.jobs.jobs.values() if j.kind == "checkin"), None)
    if job:
        job = await brain.jobs.wait(job.id)
    await asyncio.sleep(0.2)
    data = job.data if job and isinstance(job.data, dict) else {}
    return {
        "case": "checkin",
        "tool": note,
        "ran_early": early,
        "ran_on_time": ran,
        "status": job.status if job else None,
        "secs": round(time.monotonic() - t0, 1),
        "cost": round(job.cost_usd, 4) if job else 0,
        "steps": job.steps if job else 0,
        "summary": job.summary if job else "",
        "data": data,
        "log": log,
        "notification": {k: ready[-1].get(k) for k in ("title", "body", "link")} if ready else None,
        "checks": {
            "not before it opened": early == 0,
            "started on time": ran == 1,
            "checked in": bool(site.state.checkins),
            "nothing bought": not site.state.purchases,
            "boarding pass link": "/air/pass" in str((ready[-1].get("link") if ready else "") or ""),
        },
    }


async def main() -> None:
    which = sys.argv[1:] or ["form", "checkin"]
    with BookingSite() as site:
        for name in which:
            r = await (run_form(site) if name == "form" else run_checkin(site))
            print(json.dumps(r, indent=2, default=str))


asyncio.run(main())
