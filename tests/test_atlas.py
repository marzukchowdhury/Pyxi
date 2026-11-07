"""Atlas, the travel teammate: itineraries from the confirmations in a fake Gmail.

Real-looking airline, hotel and rental emails (one with two flights, a schedule change, a
cancelled booking, an ad and a past trip). No network and never the real mailbox.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from pyxi.agents import atlas
from pyxi.companion import specialists
from test_mailwatch import connected, fetched, until

TZ = ZoneInfo("America/Vancouver")
NOW = datetime(2026, 11, 7, 9, 0)


def sent(month: int, day: int) -> datetime:
    return datetime(2026, month, day, 10, 0, tzinfo=TZ)


SKYWARD = """Hi Alex,

Thanks for booking with Skyward Air. Your trip to Toronto is confirmed.

Booking reference: K7Q2ZP

OUTBOUND
Flight SK 482
Thu 12 Nov 2026
Depart: Vancouver (YVR) 08:35
Arrive: Toronto Pearson (YYZ) 16:02

RETURN
Flight SK 489
Sun 15 Nov 2026
Depart: Toronto Pearson (YYZ) 18:10
Arrive: Vancouver (YVR) 20:25

Passenger: Alex Tester
Total paid: $612.40 CAD

Check in online from 24 hours before departure.
"""

SCHEDULE_CHANGE = """Your flight time has changed.

Booking reference: K7Q2ZP

Flight SK 489 on Sun 15 Nov 2026 now departs Toronto (YYZ) at 19:05 and arrives Vancouver
(YVR) at 21:20. No action is needed.
"""

DRAKE = """Your reservation is confirmed

The Drake Hotel
Address: 1150 Queen St W, Toronto, ON M6J 1J3
Confirmation number: 88412093

Check-in: Thursday, November 12, 2026 (from 3:00 PM)
Check-out: Sunday, November 15, 2026 (until 11:00 AM)
1 room, 3 nights, 2 adults
Total: $894.00 CAD
"""

AIRBNB = """Reservation confirmed for Sunny loft near Alfama

Check-in   Thu, Dec 3, 2026   after 4:00 PM
Checkout   Mon, Dec 7, 2026   by 11:00 AM
Address: Rua dos Remedios 42, 1100-445 Lisboa, Portugal
Confirmation code: HMQ4X2ZB9P
"""

LISBON_FLIGHT = """Booking confirmation  -  Record locator: TP9WQL

TP 258  Thu, Dec 3, 2026
Toronto (YYZ) 21:40  ->  Lisbon (LIS) 09:55 +1
"""

CANCELLED_HOTEL = """Your booking has been cancelled.

Hotel Le Germain, Toronto
Confirmation number: 55120987
Check-in: Thursday, November 12, 2026
Check-out: Saturday, November 14, 2026
"""

AD = """Fall sale! Flights from Vancouver (YVR) to Paris (CDG) from $399. Book by Nov 30.
"""

PAST = """Booking reference: QQ7Y2M
Flight AC 117 on Sat 12 Sep 2026: Vancouver (YVR) 07:00 to Montreal (YUL) 14:35.
"""


def mails() -> list[dict[str, Any]]:
    return [
        {
            "from": "Skyward Air <bookings@skyward.example>",
            "subject": "Your trip to Toronto: booking K7Q2ZP",
            "body": SKYWARD,
            "date": sent(10, 20),
            "html": '<a href="https://skyward.example/checkin?ref=K7Q2ZP">Check in online</a>',
        },
        {
            "from": "Skyward Air <noreply@skyward.example>",
            "subject": "Schedule change for booking K7Q2ZP",
            "body": SCHEDULE_CHANGE,
            "date": sent(11, 2),
        },
        {
            "from": "The Drake Hotel <stay@thedrake.example>",
            "subject": "Your stay at The Drake Hotel",
            "body": DRAKE,
            "date": sent(10, 21),
        },
        {
            "from": "Airbnb <automated@airbnb.example>",
            "subject": "Reservation confirmed for Sunny loft near Alfama",
            "body": AIRBNB,
            "date": sent(10, 30),
        },
        {
            "from": "TAP Air Portugal <booking@flytap.example>",
            "subject": "Your booking confirmation TP9WQL",
            "body": LISBON_FLIGHT,
            "date": sent(10, 30),
        },
        {
            "from": "Booking.com <noreply@booking.example>",
            "subject": "Cancelled: Hotel Le Germain",
            "body": CANCELLED_HOTEL,
            "date": sent(10, 22),
        },
        {
            "from": "Skyward Air Deals <deals@skyward.example>",
            "subject": "Fall flight sale",
            "body": AD,
            "date": sent(11, 1),
        },
        {
            "from": "Air Canada <confirmation@aircanada.example>",
            "subject": "Booking confirmation QQ7Y2M",
            "body": PAST,
            "date": sent(8, 1),
        },
    ]


async def test_flights_and_stays_are_read_from_real_looking_confirmations():
    flights, stays, unread = atlas.read_mail(await fetched(mails()))
    got = {
        (f.flight, f.depart.strftime("%Y-%m-%d %H:%M"), f.origin, f.dest, f.ref) for f in flights
    }
    assert ("SK 482", "2026-11-12 08:35", "YVR", "YYZ", "K7Q2ZP") in got
    # The schedule change wins over the first email's 18:10.
    assert ("SK 489", "2026-11-15 19:05", "YYZ", "YVR", "K7Q2ZP") in got
    assert not any(f.flight == "SK 489" and f.depart.hour == 18 for f in flights)
    lis = next(f for f in flights if f.flight == "TP 258")
    assert lis.arrive == datetime(2026, 12, 4, 9, 55) and lis.ref == "TP9WQL"
    sk = next(f for f in flights if f.flight == "SK 482")
    assert sk.origin_city == "Vancouver" and sk.dest_city == "Toronto Pearson"
    assert sk.arrive == datetime(2026, 11, 12, 16, 2)
    assert sk.checkin_url.startswith("https://skyward.example/checkin")
    names = {s.name for s in stays}
    assert names == {"The Drake Hotel", "Sunny loft near Alfama"}  # the cancelled one is gone
    drake = next(s for s in stays if s.name == "The Drake Hotel")
    assert drake.address.startswith("1150 Queen St W") and drake.ref == "88412093"
    assert drake.check_in == datetime(2026, 11, 12, 15, 0) and drake.check_out == datetime(
        2026, 11, 15, 11, 0
    )
    assert not any("CDG" in (f.origin, f.dest) for f in flights)  # an ad is not a booking
    assert not unread or all("sale" in m["subject"].lower() for m in unread)


async def test_the_next_trip_is_laid_out_day_by_day_with_reminders():
    flights, stays, _ = atlas.read_mail(await fetched(mails()))
    found = atlas.trips(flights, stays, NOW)
    assert [t.destination for t in found] == ["Toronto", "Lisbon"]  # the past trip is gone
    trip = found[0]
    assert trip.title() == "Toronto trip, Thu 12 to Sun 15 Nov"
    plan = atlas.days(trip)
    assert [d["heading"] for d in plan] == ["Thu 12 Nov", "Fri 13 Nov", "Sat 14 Nov", "Sun 15 Nov"]
    assert plan[0]["lines"][0].startswith(
        "Fly 08:35 SK 482 Vancouver (YVR) to Toronto Pearson (YYZ), lands 16:02"
    )
    assert "Check in at The Drake Hotel, 1150 Queen St W" in plan[0]["lines"][1]
    assert plan[1]["lines"] == ["Free day. Staying at The Drake Hotel"]
    assert plan[3]["lines"][0].startswith("Check out of The Drake Hotel (by 11:00)")
    assert plan[3]["lines"][1].startswith("Fly 19:05 SK 489")
    rem = atlas.reminders(trip, NOW)
    assert [(r["on"], r["time"]) for r in rem] == [
        ("2026-11-11", "19:00"),
        ("2026-11-12", "05:35"),
        ("2026-11-14", "19:00"),
        ("2026-11-15", "16:05"),
    ]
    assert "—" not in " ".join(r["message"] for r in rem)  # no em dashes in what they read
    assert atlas.pick(found, "lisbon").destination == "Lisbon"
    lis = atlas.days(found[1])
    assert lis[0]["heading"] == "Thu 3 Dec" and lis[1]["lines"][0].startswith(
        "Land in Lisbon (LIS)"
    )


def test_reminders_never_land_in_the_past():
    f = atlas.Flight("SK 1", "Skyward", "YVR", "YYZ", datetime(2026, 11, 7, 11, 0))
    assert atlas.reminders(atlas.Trip([f], []), NOW) == []  # 3 hours before is already gone
    f2 = atlas.Flight("SK 2", "Skyward", "YVR", "YYZ", datetime(2026, 11, 8, 6, 0))
    keys = [r["key"].rsplit(":", 1)[1] for r in atlas.reminders(atlas.Trip([f2], []), NOW)]
    assert keys == ["eve", "go"]


def test_odd_text_is_not_mistaken_for_a_flight():
    mail = {
        "subject": "Receipt",
        "text": "Order AB 12 shipped. Total $15.49 at 10:30. Ref YYZ",
        "date": sent(11, 1),
    }
    assert atlas.read_flights(mail) == []
    assert atlas.read_stays({"subject": "Hi", "text": "Check-in: soon", "date": sent(11, 1)}) == []


def test_the_model_reading_never_invents_bad_rows():
    raw = (
        'Here you go: {"flights": [{"flight": "WS 702", "from": "YYC", "to": "YVR", "depart": '
        '"2026-11-20T07:10", "arrive": "2026-11-20T07:45", "ref": "abc123"}, {"flight": "", '
        '"from": "YYC", "to": "YVR", "depart": "2026-11-20T07:10"}], "stays": [{"name": "X", '
        '"check_in": "2026-11-21T15:00", "check_out": "2026-11-20T11:00"}]}'
    )
    flights, stays = atlas.from_model(raw, [])
    assert [(f.flight, f.ref) for f in flights] == [("WS 702", "ABC123")] and stays == []
    assert atlas.from_model("not json", []) == ([], [])


async def test_pyxi_hands_the_trip_to_atlas_and_saves_the_reminders(settings, monkeypatch):
    monkeypatch.setattr(atlas, "clock", lambda: NOW)
    brain, rec, fake = connected(settings, mails())
    out = await specialists.handle(brain, "travel", {"trip": ""})
    assert out.startswith("Started job_") and "Atlas" in out
    [ready] = await until(rec, "job_ready")
    assert ready["agent"] == "Atlas" and ready["ok"]
    assert ready["title"] == "Toronto trip, Thu 12 to Sun 15 Nov"
    assert ready["body"].startswith("2 flights and 1 stay.") and "Lisbon" in ready["body"]
    assert ready["changes"][0].startswith("Thu 12 Nov: Fly 08:35 SK 482")
    for _ in range(50):
        if len(brain.routines.items) >= 4:
            break
        await asyncio.sleep(0.02)
    keys = sorted(r.key for r in brain.routines.items if r.key and r.key.startswith("atlas:"))
    assert len(keys) == 4 and all(r.kind == "remind" for r in brain.routines.items)
    # Asking again doesn't set the same reminders twice.
    await specialists.handle(brain, "travel", {"trip": "toronto"})
    await asyncio.sleep(0.3)
    await brain.jobs.wait_all()
    await asyncio.sleep(0.1)
    assert len([r for r in brain.routines.items if r.key and r.key.startswith("atlas:")]) == 4
    assert fake.sent == [] and fake.queries  # read only, nothing sent
    job = next(j for j in brain.jobs.jobs.values() if j.agent_id == "atlas")
    assert job.data["card"]["sections"][0]["heading"] == "Thu 12 Nov"


async def test_check_in_goes_to_the_flight_check_in(settings, monkeypatch):
    from pyxi.companion import paperwork

    monkeypatch.setattr(atlas, "clock", lambda: NOW)
    calls = []

    async def fake_handle(brain, name, args):
        calls.append((name, args))
        return "Saved"

    monkeypatch.setattr(paperwork, "handle", fake_handle)
    brain, _rec, _ = connected(settings, mails())
    out = await specialists.handle(brain, "travel", {"trip": "toronto", "check_in": True})
    job_id = out.split()[1].rstrip(":")
    await brain.jobs.wait(job_id)
    for _ in range(50):
        if len(calls) >= 2:
            break
        await asyncio.sleep(0.02)
    assert [(n, a["confirmation"], a["departure"]) for n, a in calls] == [
        ("check_in_flight", "K7Q2ZP", "2026-11-12T08:35"),
        ("check_in_flight", "K7Q2ZP", "2026-11-15T19:05"),
    ]
    assert calls[0][1]["url"].startswith("https://skyward.example/checkin")


async def test_no_gmail_means_one_plain_sentence(settings):
    from test_brain_and_server import make_brain

    brain, _rec = make_brain(settings)
    out = await specialists.handle(brain, "travel", {})
    assert "isn't connected" in out and not brain.jobs.jobs


async def test_no_trips_says_so(settings, monkeypatch):
    monkeypatch.setattr(atlas, "clock", lambda: datetime(2027, 3, 1))
    brain, rec, _ = connected(settings, mails())
    await specialists.handle(brain, "travel", {})
    [ready] = await until(rec, "job_ready")
    assert not ready["ok"] and ready["title"] == "No upcoming trips in your mail"


def test_other_airline_and_hotel_formats():
    d = sent(10, 20)
    ac = {
        "from": "Air Canada <info@aircanada.example>",
        "subject": "Booking Confirmation",
        "date": d,
        "text": "Booking Reference: X4YT9P\nMon, Nov 16\nAC 123   YYZ 6:10 PM → YVR 8:25 PM\nSeat 14C.\nTotal: $412.33",
    }
    [f] = atlas.read_flights(ac)
    assert (f.flight, f.depart, f.arrive, f.ref, f.dest_city) == (
        "AC 123",
        datetime(2026, 11, 16, 18, 10),
        datetime(2026, 11, 16, 20, 25),
        "X4YT9P",
        "Vancouver",
    )
    ws = {
        "from": "WestJet <noreply@westjet.example>",
        "subject": "Your WestJet itinerary",
        "date": d,
        "text": "Reservation code: QWERTY\nFlight WS 702 departing Calgary (YYC) on November 20, 2026 at "
        "7:10 a.m., arriving Vancouver (YVR) at 7:45 a.m.",
    }
    [w] = atlas.read_flights(ws)
    assert (w.origin_city, w.depart.hour, w.depart.minute, w.ref) == ("Calgary", 7, 10, "QWERTY")
    ex = {
        "from": "Expedia <travel@expedia.example>",
        "date": d,
        "subject": "Expedia travel confirmation - Hilton Garden Inn Calgary Downtown",
        "text": "Hilton Garden Inn Calgary Downtown\n711 4th St SE, Calgary, AB T2G 1N3\n"
        "Check-in: Fri, Nov 20\nCheck-out: Sun, Nov 22\nItinerary # 72910384451",
    }
    [s] = atlas.read_stays(ex)
    assert (s.name, s.ref, s.check_in.day, s.check_out.day) == (
        "Hilton Garden Inn Calgary Downtown",
        "72910384451",
        20,
        22,
    )
    trip = atlas.Trip([w], [s])
    assert trip.destination == "Vancouver" and trip.stay_city() == "Calgary"


async def test_a_check_in_that_needs_a_name_says_so(settings, monkeypatch):
    from pyxi.companion import paperwork

    monkeypatch.setattr(atlas, "clock", lambda: NOW)
    monkeypatch.setattr(paperwork, "clock", lambda: NOW)
    brain, rec, _ = connected(settings, mails())  # no saved last name
    out = await specialists.handle(brain, "travel", {"trip": "toronto", "check_in": True})
    await brain.jobs.wait(out.split()[1].rstrip(":"))
    notes = await until(rec, "job_ready", 2)
    assert (
        notes[-1]["title"] == "Check-in for SK 482 isn't set up"
        and "last name" in notes[-1]["body"]
    )
    assert not any(r.kind == "job" for r in brain.routines.items)


def test_promotions_and_postcodes_are_not_bookings():
    ad = {
        "subject": "Fall sale: fares from $99",
        "text": "Fly Vancouver (YVR) to Paris (CDG). Check-in online.",
        "date": sent(11, 1),
    }
    assert not atlas.looks_like_travel(ad)
    real = {
        "subject": "Your e-ticket",
        "text": "E-ticket 0142155. Passenger A TESTER.",
        "date": sent(11, 1),
    }
    assert atlas.looks_like_travel(real)
    addr = {
        "subject": "Your stay",
        "text": "Address: 1150 Queen St W, Toronto, ON M6J 1J3\nCheck in 10:30 YYZ YVR",
        "date": sent(11, 1),
    }
    assert atlas.read_flights(addr) == []
