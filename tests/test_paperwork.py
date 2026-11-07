"""Paperwork: bills explained on the bill, forms filled from documents, flight check-in."""

from __future__ import annotations

import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

import httpx

from pyxi import booking, files_access
from pyxi.agents.jobs import Job
from pyxi.companion import paperwork
from pyxi.companion.notes import build_card
from pyxi.demo_bookings import BookingSite
from pyxi.partial_json import picture_head
from test_brain_and_server import make_brain

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from sample_bills import render

ALEX = {"first_name": "Alex", "last_name": "Tester", "email": "alex.tester@example.com", "phone": "604-555-0100"}
DEPARTS = datetime(2026, 11, 12, 8, 35)


def bill_ctx(name: str):
    from pyxi.screen.context import ScreenContext, WindowInfo
    from pyxi.screen.ocr import TextLine

    shot, lines, title = render(name)
    ctx = ScreenContext(0.0, WindowInfo(title, "msedge.exe", (0, 0, *shot.size)), [], shot)
    ctx.text = [TextLine(t, r, w, i) for i, (t, r, w) in enumerate(lines, start=1)]
    ctx.model_scale = 1.0
    return ctx


# ------------------------------------------------------------------------------ bills
def test_bill_tags_pin_to_the_quoted_lines():
    ctx = bill_ctx("phone")
    args = {
        "style": "on_picture",
        "bill": {"owe": "$107.80", "due": "Nov 28"},
        "picture": [560, 64, 800, 1016],
        "items": [
            {"text": "Due 28 Nov", "say": "Pay by the 28th.", "quote": "Please pay by Nov 28, 2026"},
            {"text": "Charged twice?", "say": "Same day, twice.", "quote": "US Roam Pass, Oct 18 (1 day)"},
            {"text": "+$10 since Oct", "say": "Up ten.", "quote": "Your plan price changed"},
        ],
    }
    card = build_card(ctx, args)
    assert card.style == "picture" and all(it.at for it in card.items)
    due = next(t for t in ctx.text if t.text.startswith("Please pay"))
    x, y = card.items[0].at
    assert due.rect[0] <= x <= due.rect[0] + due.rect[2] and due.rect[1] <= y <= due.rect[1] + due.rect[3]
    assert all(it.tag for it in card.items)  # each tag got a spot of its own


def test_a_bill_skips_the_picture_survey_and_says_its_verdict():
    assert picture_head('{"style": "on_picture", "bill": {"owe": "$5"}, "picture": [1, 2, 300, 400]')["bill"]
    title, summary = paperwork.bill_summary(
        {"from": "Maplewave", "owe": "$107.80", "due": "Nov 28", "flags": ["Roaming charged twice"]}
    )
    assert title == "Explaining your Maplewave bill"
    assert summary == "You owe $107.80 by Nov 28. 1 thing to check: Roaming charged twice."
    assert paperwork.bill_summary({"owe": "$0"})[1] == "You owe $0. Nothing looks wrong."


async def test_explaining_a_bill_puts_a_chip_in_the_task_tray(settings):
    brain, rec = make_brain(settings)
    brain._ctx = bill_ctx("phone")
    note = await brain._act(
        "explain_on_screen",
        {
            "style": "on_picture",
            "bill": {"from": "Maplewave", "owe": "$107.80", "due": "Nov 28", "flags": ["Roaming twice"]},
            "picture": [560, 64, 800, 1016],
            "items": [{"text": "Due 28 Nov", "say": "Pay by the 28th.", "quote": "Please pay by Nov 28, 2026"}],
        },
    )
    assert note is None
    [job] = [e["job"] for e in rec.of("job") if e["job"]["status"] == "done"]
    assert job["task"] == "Explaining your Maplewave bill" and "$107.80 by Nov 28" in job["summary"]


async def test_a_pdf_bill_is_opened_on_screen_then_looked_at(settings, tmp_path):
    brain, _ = make_brain(settings)
    shown = bill_ctx("phone")

    async def capture(**_):
        return shown

    brain.screen.capture = capture  # type: ignore[method-assign]
    pdf = tmp_path / "bill.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    files_access.save(settings.data_dir, files_access.FileAccess(True, [str(tmp_path)], True))
    opened = []
    paperwork.open_path, real = opened.append, paperwork.open_path
    try:
        note = await paperwork.handle(brain, "show_file", {"path": str(pdf)})
    finally:
        paperwork.open_path = real
    assert opened == [pdf] and "this is the screen now" in note and "explain_on_screen" in note
    assert brain._ctx is shown


# ------------------------------------------------------------------------------ forms
def test_id_numbers_never_reach_scout():
    cv = "Alex Tester\nSIN: 046 454 286\nPassport number GA123456\nPhone 604-555-0100\nI'm good with passwords."
    clean = booking.redact_secrets(cv)
    assert "046" not in clean and "GA123456" not in clean
    assert "604-555-0100" in clean and "passwords" in clean
    text = booking.form_brief({"what": "job application"}, ALEX, {"C:/cv.pdf": cv}, "Now: Sat")
    assert text.startswith("Fill: job application") and "046 454" not in text
    assert "NEVER type passwords" in text and "HANDOVER:" in text and "C:/cv.pdf" in text


async def test_fill_form_reads_the_cv_asks_first_and_starts_a_booking_job(settings, tmp_path):
    brain, rec = make_brain(settings)
    booking.save(settings.data_dir, ALEX)
    cv = tmp_path / "cv.txt"
    cv.write_text("Alex Tester. Data analyst at Lumen Retail since 2023. SIN 046 454 286.", encoding="utf-8")
    files_access.save(settings.data_dir, files_access.FileAccess(True, [str(tmp_path)], True))
    started = {}

    async def fake_start(agent, task, **kw):
        started.update(agent=agent, task=task, **kw)
        return Job(agent_id="scout", agent_name="Scout", role="browser", task=task)

    async def answer(kind, timeout=300, **data):
        rec.events.append({"type": kind, **data})
        return True

    brain.jobs.start = fake_start  # type: ignore[method-assign]
    brain.hub.request = answer  # type: ignore[method-assign]
    assert "which page" in await paperwork.handle(brain, "fill_form", {"what": "x"})
    note = await paperwork.handle(
        brain,
        "fill_form",
        {"what": "job application at Northwind", "url": "http://127.0.0.1/jobs/apply", "documents": [str(cv)]},
    )
    assert note.startswith("Started")
    card = rec.of("confirm")[-1]
    assert card["action"] == "Fill in job application at Northwind"
    assert "cv.txt" in card["reason"] and "never types passwords" in card["reason"]
    assert started["kind"] == "booking" and started["params"] == {"form": True}
    assert "Lumen Retail" in started["task"] and "046 454" not in started["task"]


# ------------------------------------------------------------------------------ check-in
async def test_check_in_is_scheduled_24_hours_before_and_runs_at_that_moment(settings):
    brain, rec = make_brain(settings)
    booking.save(settings.data_dir, ALEX)
    paperwork.clock = lambda: DEPARTS - timedelta(days=3)
    started = []

    async def fake_start(agent, task, **kw):
        started.append((agent, task, kw))
        return Job(agent_id="scout", agent_name="Scout", role="browser", task=task)

    brain.jobs.start = fake_start  # type: ignore[method-assign]
    try:
        args = {"airline": "Skyward Air", "flight": "SK 482", "confirmation": "k7q2zp",
                "departure": "2026-11-12T08:35", "url": "http://127.0.0.1/air"}
        note = await paperwork.handle(brain, "check_in_flight", args)
        assert "Wed 11 Nov at 08:35" in note and not started
        assert "already scheduled" in await paperwork.handle(brain, "check_in_flight", args)
        [r] = brain.routines.items
        assert r.kind == "job" and r.task == "Check in: Skyward Air SK 482"
        assert datetime.fromtimestamp(r.next_run) == DEPARTS - timedelta(hours=24)
        tray = [e["job"] for e in rec.of("job")][-1]
        assert tray["task"] == "Check in: Skyward Air SK 482 when it opens"
        # A minute early: nothing. At the moment it opens: Scout, straight away, no model turn.
        assert await brain.run_due_routines(DEPARTS - timedelta(hours=24, minutes=1)) == 0
        assert await brain.run_due_routines(DEPARTS - timedelta(hours=24) + timedelta(seconds=5)) == 1
        [(agent, task, kw)] = started
        assert agent == "scout" and kw["kind"] == "checkin" and kw["start_url"] == "http://127.0.0.1/air"
        assert "Booking reference: K7Q2ZP" in task and "Last name: Tester" in task
        assert "Decline every paid extra" in task
        assert not brain.routines.items  # a one-off: gone once it has run
        assert not rec.of("user")  # no turn, no model call
        # Already open (less than 24 hours to go): check in now.
        paperwork.clock = lambda: DEPARTS - timedelta(hours=3)
        note = await paperwork.handle(brain, "check_in_flight", {**args, "confirmation": "ZZ9ZZ9"})
        assert "checking them in now" in note and len(started) == 2
        paperwork.clock = lambda: DEPARTS + timedelta(hours=1)
        assert "already left" in await paperwork.handle(brain, "check_in_flight", args)
    finally:
        paperwork.clock = datetime.now


def test_check_in_goes_ahead_but_paid_extras_stop():
    from otto.config import DEFAULT_SENSITIVE_PATTERNS

    patterns = [p for p in DEFAULT_SENSITIVE_PATTERNS if p not in booking.CHECKIN_SAFE]
    patterns += booking.CHECKIN_PATTERNS

    def stops(label: str) -> bool:
        return any(re.search(p, label, re.I) for p in patterns)

    assert not any(stops(x) for x in ("Check in", "Confirm", "Submit", "No thanks, get my boarding pass"))
    assert all(stops(x) for x in ("Upgrade for $149", "Choose seat 12A for $39", "Pay now", "Buy bags"))


def test_notifications_carry_the_boarding_pass_and_what_was_left():
    job = Job(agent_id="scout", agent_name="Scout", role="browser", task="Check in: Skyward Air SK 482\n...",
              kind="checkin", status="done")
    note = paperwork.notice(job, {"checked_in": True, "seat": "23C", "gate": "B14", "boarding_pass": "http://x/pass"})
    assert note["title"] == "You're checked in: Skyward Air SK 482" and note["link"] == "http://x/pass"
    assert note["body"] == "Seat 23C. Gate B14. Your boarding pass is ready." and note["actions"] == ["link"]
    job.status = "failed"
    assert paperwork.notice(job, {"needs_you": "your passport number"})["title"].startswith("Check-in needs you")
    form = Job(agent_id="scout", agent_name="Scout", role="browser", task="Fill: job application\n...",
               kind="booking", params={"form": True}, status="done")
    note = paperwork.notice(form, {"submitted": True, "confirmation": "NW-1A2B3C", "left_for_you": ["SIN"]})
    assert note["title"] == "Sent: job application" and note["body"] == "Confirmation NW-1A2B3C. Left for you: SIN."
    assert paperwork.notice(Job(agent_id="q", agent_name="Q", role="writer", task="t"), {}) is None


def test_practice_job_form_and_airline():
    with BookingSite() as site:
        c = httpx.Client(base_url=site.url, follow_redirects=True)
        assert "Please enter your most recent employer" in c.post("/jobs/apply", data={"first_name": "A"}).text
        ok = c.post("/jobs/apply", data={**ALEX, "current_employer": "Lumen", "why": "Data.",
                                         "work_auth": "Yes, I have a valid work permit"}).text
        assert "Application number NW-" in ok and not site.state.applications[0].get("sin")
        site.add_flight("K7Q2ZP", "Tester", DEPARTS)
        site.state.clock = lambda: DEPARTS - timedelta(hours=25)
        trip = c.post("/air/find", data={"ref": "k7q2zp", "last": "tester"}).text
        assert "Check-in opens Wednesday 11 November at 08:35" in trip
        site.state.clock = lambda: DEPARTS - timedelta(hours=23)
        assert "dangerous goods" in c.post("/air/checkin", data={"ref": "K7Q2ZP", "last": "Tester"}).text
        seats = c.post("/air/checkin", data={"ref": "K7Q2ZP", "last": "Tester", "dg": "yes"}).text
        assert "Upgrade for $149" in seats and site.state.checkins
        assert "Gate <b>B14</b>" in c.get("/air/pass?ref=K7Q2ZP&last=Tester").text
        assert not site.state.purchases
