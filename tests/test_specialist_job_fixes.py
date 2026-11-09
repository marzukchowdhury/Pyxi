"""Bugs found by running each specialist's real job end to end with the real model on fake
data (.pyxi/specialist_e2e.py), one regression test each. No network, no real accounts.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from openpyxl import Workbook, load_workbook

from pyxi.agents import atlas, ditto, penny, sage, scribe, trace
from pyxi.agents.jobs import Job, plain_markdown
from pyxi.agents.ledger import Workbook as LedgerBook
from pyxi.companion import specialists
from pyxi.voice.tts import split_sentences
from test_brain_and_server import make_brain
from test_connectors_and_docs import Script
from test_files_agents import budget
from test_mailwatch import connected, fetched, until

TZ = ZoneInfo("America/Vancouver")
NOW = datetime(2026, 11, 8, 10, 0)

WESTJET = {
    "from": "WestJet <noreply@westjet.example>",
    "subject": "Get ready for your trip to Calgary",
    "body": "Hi Alex! You're flying with us out of Vancouver on Friday the 20th of November 2026, "
    "leaving at ten past seven in the morning on flight WS 702, and you'll touch down in "
    "Calgary at 9:45 local time. Your reservation code is QX7T2M. See you soon!",
    "date": datetime(2026, 11, 4, 10, 0, tzinfo=TZ),
}


# ---------------------------------------------------------------- Atlas
async def test_a_booking_written_as_prose_goes_to_the_model(settings, monkeypatch):
    """A confirmation with a reservation code but no airport codes, labels or check-in words
    was dropped: never read by the code, and never shown to the model either."""
    [mail] = await fetched([dict(WESTJET)])
    assert atlas.looks_like_travel(mail)
    ad = {"subject": "Fall flight sale", "text": "Flights from $99! Reservation code SALE99 at checkout."}
    assert not atlas.looks_like_travel(ad)  # an ad still isn't a booking
    monkeypatch.setattr(atlas, "clock", lambda: NOW)
    reply = json.dumps(
        {
            "flights": [
                {"flight": "WS 702", "from": "YVR", "from_city": "Vancouver", "to": "YYC",
                 "to_city": "Calgary", "depart": "2026-11-20T07:10", "arrive": "2026-11-20T09:45",
                 "ref": "QX7T2M"}
            ],
            "stays": [],
        }
    )  # fmt: skip
    brain, rec, _ = connected(settings, [dict(WESTJET)], llm=Script(reply))
    await specialists.handle(brain, "travel", {"trip": "Calgary"})
    [ready] = await until(rec, "job_ready")
    assert ready["ok"] and ready["title"] == "Calgary trip, Fri 20 Nov", ready
    assert ready["changes"][0].startswith("Fri 20 Nov: Fly 07:10 WS 702")
    assert "before the flight" in ready["body"]  # one flight: not "each flight"
    await brain.jobs.wait_all()


def _trip(city: str, day: datetime) -> atlas.Trip:
    return atlas.Trip([atlas.Flight("XX 1", "", "YVR", city[:3].upper(), day, dest_city=city)], [])


def test_next_month_and_this_week_pick_the_trip_by_its_dates():
    """'Where am I staying next month?' showed the November trip and said there was no trip
    matching 'next month'."""
    toronto = _trip("Toronto", datetime(2026, 11, 12, 8, 35))
    lisbon = _trip("Lisbon", datetime(2026, 12, 3, 21, 40))
    found = [toronto, lisbon]
    assert atlas.pick(found, "next month", strict=True, now=NOW) is lisbon
    assert atlas.pick(found, "this month", strict=True, now=NOW) is toronto
    assert atlas.pick(found, "next week", strict=True, now=NOW) is toronto  # Mon 9 to Sun 15
    assert atlas.pick(found, "this weekend", strict=True, now=NOW) is None
    assert atlas.pick(found, "Lisbon next month", strict=True, now=NOW) is lisbon
    assert atlas.pick(found, "Toronto next month", strict=True, now=NOW) is None
    assert atlas.pick(found, "", strict=True, now=NOW) is toronto


# ---------------------------------------------------------------- Scribe
def test_action_dates_read_naturally():
    """'(by This week)' in the action items."""
    n = scribe.Notes("x")
    line = n.action_line
    assert line({"who": "Jordan", "what": "Book the photographer", "when": "This week"}) == (
        "Jordan: Book the photographer (by this week)"
    )
    assert line({"who": "Priya", "what": "Release notes", "when": "Thursday noon"}).endswith(
        "(by Thursday noon)"
    )
    assert line({"who": "", "what": "Ship it", "when": "By Friday"}).endswith("(by Friday)")
    assert line({"who": "Sam", "what": "Demo", "when": "before the launch"}).endswith(
        "(before the launch)"
    )
    assert line({"who": "Mei", "what": "Wireframes", "when": ""}) == "Mei: Wireframes"


# ---------------------------------------------------------------- Sage
async def test_one_reply_answers_one_question(settings):
    """After 'I don't know', the model called quiz 'answer' three times in one turn: it marked
    questions they never heard and ended the quiz ('That was the last question')."""
    brain, _ = make_brain(settings)
    cards = [sage.Card(f"Q{i}?", f"A{i}.") for i in range(3)]
    sage.save_deck(settings.data_dir, sage.Deck("Heart", "heart.pptx", cards))
    await specialists.handle(brain, "quiz", {"action": "start"})
    quiz = brain.study_quiz
    # The turn where they reply: the first answer is marked...
    brain._calls = [{"round": 1, "tool": "quiz", "arguments": {"action": "answer"}}]
    out = await specialists.handle(brain, "quiz", {"action": "answer", "right": False})
    assert out.startswith("quiz: Not quite") and quiz.at == 1
    # ...and a second one in the same turn is refused: that question was only just asked.
    brain._calls.append({"round": 2, "tool": "quiz", "arguments": {"action": "answer"}})
    out = await specialists.handle(brain, "quiz", {"action": "answer", "right": False})
    assert "haven't answered" in out and quiz.at == 1 and len(quiz.missed) == 1
    # Next turn, their next reply counts again.
    brain._calls = [{"round": 1, "tool": "quiz", "arguments": {"action": "answer"}}]
    out = await specialists.handle(brain, "quiz", {"action": "answer", "right": True})
    assert out.startswith("quiz: They got it") and quiz.at == 2


# ---------------------------------------------------------------- Penny
def _mail(sender: str, subject: str, body: str, day: int) -> dict:
    return {"from": sender, "subject": subject, "body": body, "date": datetime(2026, 11, day, 10, tzinfo=TZ)}


async def test_us_dollars_are_never_added_to_canadian_dollars():
    """An Apple receipt in US$ next to CA$ receipts was counted as Canadian dollars."""
    mails = [
        _mail("Uber <uber.canada@uber.com>", "Your trip with Uber", "Thanks for riding.\nTotal CA$22.00", 2),
        _mail("Apple <no_reply@email.apple.com>", "Your receipt from Apple", "Receipt\niCloud+\nTotal US$0.99", 4),
        _mail("Amazon.ca <auto-confirm@amazon.ca>", "Your order", "Thanks for your order.\nOrder Total: $10.00", 5),
    ]
    start = datetime(2026, 11, 1)
    rs = penny.receipts(await fetched(mails), start, datetime(2026, 12, 1))
    assert sorted((r.amount, r.currency) for r in rs) == [(0.99, "USD"), (10.0, "CAD"), (22.0, "CAD")]
    rep = penny.summarise(rs, {"total": 60.0}, start, NOW)
    assert rep["title"] == "November so far: CA$32.00 spent"
    assert "CA$28.00 left" in rep["body"] and "Also US$0.99 in other currencies." in rep["body"]
    # With no Canadian dollars around, US$ and $ are the same dollars.
    us = [mails[1], _mail("Netflix <info@netflix.com>", "Your payment", "We charged $15.49 monthly.", 6)]
    rs = penny.receipts(await fetched(us), start, datetime(2026, 12, 1))
    assert {r.currency for r in rs} == {"$"}
    assert penny.summarise(rs, {}, start, NOW)["title"] == "November so far: $16.48 spent"


# ---------------------------------------------------------------- Ledger
def test_dates_the_model_writes_show_as_dates(tmp_path):
    """Penny's receipts went into their sheet as =DATE(2026,10,3) in a General cell: Excel
    shows 46298, not a date."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Spending"
    ws.append(["Date", "Merchant", "Amount"])
    ws.append([datetime(2026, 9, 12), "Costco", 143.2])
    ws["A2"].number_format = "dd/mm/yyyy"
    src = tmp_path / "spending.xlsx"
    wb.save(src)
    book = LedgerBook(src)
    book.apply({"op": "append_row", "values": ["=DATE(2026,10,3)", "Amazon", 24.84]})
    book.apply({"op": "append_row", "values": ["2026-10-09", "Uber Eats", 32.4]})
    book.apply({"op": "set_cells", "cells": {"E1": "=DATE(2026,11,1)", "F1": 7}})
    out = load_workbook(book.save())["Spending"]
    assert out["A3"].value == "=DATE(2026,10,3)" and out["A3"].number_format == "dd/mm/yyyy"
    assert out["A4"].value == datetime(2026, 10, 9) and out["A4"].number_format == "dd/mm/yyyy"
    assert out["E1"].number_format == "yyyy-mm-dd"  # no date above it: a plain date format
    assert out["F1"].number_format == "General" and out["C3"].value == 24.84
    assert out["B4"].value == "Uber Eats"


async def test_a_failed_edit_and_the_saved_copy_reach_the_notification(settings, tmp_path):
    """The notification shows the summary's first two sentences, and every edit was its own
    sentence: with two edits, "Saved as ..." and "1 edit(s) didn't work" were cut off."""
    reply = json.dumps(
        {
            "edits": [
                {"file": 0, "op": "totals_row"},
                {"file": 0, "op": "format", "range": "A1:D1", "bold": True},
                {"file": 0, "op": "pivot", "group_by": "Nope", "value": "Amount"},
            ]
        }
    )
    brain, _ = make_brain(settings, llm=Script(reply, json.dumps({"edits": []})))
    src = budget(tmp_path / "budget.xlsx")
    job = await brain.jobs.start("ledger", "totals, bold header, pivot", files=[str(src)])
    job = await brain.jobs.wait(job.id)
    assert job.status == "done", job.summary
    shown = " ".join(split_sentences(job.summary)[:2])  # what brain._job_done puts in the body
    assert "1 edit didn't work" in shown and "Saved as budget (edited by Pyxi).xlsx" in shown
    assert "; formatted" in shown or "; " in shown.split(", but")[0]


def test_summaries_reach_the_notification_without_markdown():
    """Quill's document summary showed **bold** markers in the notch, which is plain text."""
    md = "## Key dates\n* **Lease term:** Starts January 1, 2027\n- Notice by `November 1`, *in writing*"
    assert plain_markdown(md) == (
        "Key dates\n- Lease term: Starts January 1, 2027\n- Notice by November 1, in writing"
    )
    assert plain_markdown("2 * 3 = 6, file_name__v2") == "2 * 3 = 6, file_name__v2"


# ---------------------------------------------------------------- Ditto
async def test_the_done_notice_says_what_happened(settings, tmp_path):
    """'Done 4 more | Rename the four remaining invoices...': the plan again, as if still to do."""
    folder = tmp_path / "Invoices"
    folder.mkdir()
    for n in ("a.pdf", "b.pdf", "c.pdf"):
        (folder / n).write_bytes(b"%PDF")
    brain, rec, _ = connected(settings, [])
    plan = ditto.Plan(
        "Rename the remaining invoices like the first one.",
        "files",
        [{"item": "a.pdf", "to": "2026 A.pdf"}, {"item": "b.pdf", "to": "Done/b.pdf"}],
    )
    job = Job(agent_id="ditto", agent_name="Ditto", role="automation", task="Repeat", status="done")
    job.data = {"plan": plan.__dict__, "folder": str(folder)}

    async def allow():
        [card] = await until(rec, "confirm")
        brain.hub.resolve(card["request_id"], True)

    answer = asyncio.ensure_future(allow())
    assert await specialists.after_repeat(brain, job) == "files:2"
    await answer
    [ready] = rec.of("job_ready")
    assert ready["title"] == "Done 2 more"
    assert ready["body"] == "Renamed and moved 2 files in Invoices. Say 'undo that' to put them back."
    assert (folder / "Done" / "b.pdf").is_file() and (folder / "2026 A.pdf").is_file()


# ---------------------------------------------------------------- Trace
def test_the_found_file_is_named_not_spelled_out_as_a_path(tmp_path):
    p = tmp_path / "Documents" / "Lease renewal.pdf"
    screen = trace.Hit("screen", "Lease renewal.pdf", 'On screen in Adobe Acrobat: "Lease renewal.pdf"', 1.0e9, 1.2)
    file = trace.Hit("file", p.name, f"In your files: {p}", 1.0e9, 0.9, path=str(p))
    rep = trace.report(trace.best_first([screen, file]), "lease", ["screen history", "files"])
    assert str(p) not in rep["body"] and rep["body"].startswith('On screen in Adobe Acrobat: "Lease renewal.pdf",')
    assert str(p) in rep["summary"]  # the model still has the path


async def test_quiz_on_a_topic_without_notes_makes_a_deck(settings):
    """Night run: 'Quiz me on the heart' answered 'no flashcards yet, point me at your slides'."""
    from conftest import FakeScreen

    from pyxi.companion import specialists
    from pyxi.companion.brain import Brain
    from pyxi.demo import DemoLLM
    from pyxi.hub import Hub

    class Cards(DemoLLM):
        async def complete(self, *a, **k):
            from types import SimpleNamespace

            text = '{"title": "The heart", "cards": [{"q": "How many chambers?", "a": "Four."}, {"q": "Which side pumps to the lungs?", "a": "The right."}, {"q": "What stops backflow?", "a": "Valves."}]}'
            return SimpleNamespace(text=text, usage=SimpleNamespace(cost_usd=0.0), tool_calls=[])

    b = Brain(settings, Hub(), llm=Cards(), screen=FakeScreen())
    out = await specialists.handle(b, "quiz", {"action": "start", "deck": "the heart", "questions": 3})
    assert out.startswith("quiz: on The heart, 3 questions") and "How many chambers?" in out
