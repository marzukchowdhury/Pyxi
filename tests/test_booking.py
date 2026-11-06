"""Booking: saved details, the consent card, Scout's rules, and the hand-over at payment."""

from __future__ import annotations

import httpx

from pyxi import booking
from pyxi.demo_bookings import BookingSite
from test_brain_and_server import make_brain

ALEX = {
    "first_name": "Alex",
    "last_name": "Tester",
    "email": "alex.tester@example.com",
    "phone": "604-555-0100",
}


def test_details_are_saved_cleanly_and_never_hold_id_numbers(tmp_path):
    saved = booking.save(tmp_path, {**ALEX, "health_card": "9876 543 210", "city": "  ", "x": "y"})
    assert saved == ALEX and booking.load(tmp_path) == ALEX
    assert booking.ready(saved) and not booking.ready({"first_name": "Alex"})
    assert booking.shared_labels(saved) == "your name, email and phone"


def test_scout_gets_the_request_the_details_and_the_rules():
    text = booking.brief(
        {"what": "dental cleaning", "when": "Saturday morning"}, ALEX, "Now: Friday"
    )
    assert "Book: dental cleaning" in text and "When: Saturday morning" in text
    assert "Email: alex.tester@example.com" in text
    assert "HANDOVER:" in text and "Never type card numbers" in text


async def test_booking_asks_first_and_starts_a_booking_job(settings):
    brain, rec = make_brain(settings)
    assert "booking details" in (
        await brain._book({"what": "a cleaning"}) or ""
    )  # nothing saved yet
    booking.save(settings.data_dir, ALEX)
    started = {}

    async def fake_start(agent, task, **kw):
        started.update(agent=agent, task=task, **kw)

    brain.jobs.start = fake_start  # type: ignore[method-assign]

    async def answer(kind, timeout=300, **data):
        rec.events.append({"type": kind, **data})
        return answer.value

    brain.hub.request = answer  # type: ignore[method-assign]
    answer.value = False
    assert "nothing was booked" in (
        await brain._book({"what": "a cleaning", "where": "Maple Grove Dental"}) or ""
    )
    assert not started
    answer.value = True
    assert (
        await brain._book(
            {
                "what": "a cleaning",
                "where": "Maple Grove Dental",
                "url": "https://maplegrove.example/book",
            }
        )
        is None
    )
    card = [e for e in rec.events if e["type"] == "confirm"][-1]
    assert card["action"] == "Book a cleaning at Maple Grove Dental"
    assert "never pays" in card["reason"] and "name, email and phone" in card["reason"]
    assert started["agent"] == "scout" and started["kind"] == "booking"
    assert (
        started["start_url"] == "https://maplegrove.example/book"
        and "alex.tester@example.com" in started["task"]
    )


def test_the_final_buttons_need_approval_but_the_first_ones_dont():
    import re

    final = [re.compile(p, re.I) for p in booking.CONFIRM_PATTERNS]
    assert any(p.search("Book appointment") for p in final)
    assert any(p.search("Reserve") for p in final)
    assert not any(p.search("Book online") for p in final)  # just opens the booking page


def test_the_practice_sites_book_and_show_tickets_after_paying():
    with BookingSite() as site:
        c = httpx.Client(base_url=site.url, follow_redirects=True)
        page = c.post(
            "/cinema/hold", data={"movie": "dune-3", "time": "19:00", "seat": ["F6", "F7"]}
        ).text
        assert "Pay $33.00" in page and "card number" in page.lower()
        hold = next(iter(site.state.holds))
        assert site.pay()
        assert (
            "Enjoy the show" in c.get(f"/cinema/pay?hold={hold}").text
        )  # a reload shows the tickets
