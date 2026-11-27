"""Atlas, after the audit: trips that vanished, landed on the wrong day or lost their way home.

Each test is a confirmation of the shape that went wrong, made up for the test (airline and
hotel names that don't exist, .example addresses). No network, no model, never the real
mailbox.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from pyxi.agents import atlas
from pyxi.companion import specialists
from test_audit_penny import PagedGmail
from test_mailwatch import connected, until

TZ = ZoneInfo("America/Vancouver")
NOW = datetime(2026, 11, 7, 9, 0)


def mail(subject: str, text: str, day: int = 20, month: int = 10, **more: Any) -> dict[str, Any]:
    """A mail as GoogleConnector.gmail_fetch hands it over."""
    return {
        "id": f"m-{month}-{day}-{len(text)}",
        "from": "Skyward Air <bookings@skyward.example>",
        "subject": subject,
        "text": text,
        "date": datetime(2026, month, day, 10, 0, tzinfo=TZ),
        "links": [],
        **more,
    }


def read(*mails: dict[str, Any]) -> tuple[list[atlas.Flight], list[atlas.Stay], list[atlas.Trip]]:
    flights, stays, _unread = atlas.read_mail(list(mails))
    return flights, stays, atlas.trips(flights, stays, NOW)


def flown(flights: list[atlas.Flight]) -> list[tuple[str, str, str]]:
    return [(f.flight, f"{f.depart:%d %b %H:%M}", f.ref) for f in flights]


BOOKED = """Thanks for booking with Skyward Air.
Booking reference: K7Q2ZP

Flight SK 870
Thu 19 Nov 2026
Depart: Vancouver (YVR) 18:45
Arrive: Paris (CDG) 13:05 +1

Flight SK 871
Mon 30 Nov 2026
Depart: Paris (CDG) 15:40
Arrive: Vancouver (YVR) 17:15
"""


# ---------------------------------------------------------------- finding 5: cancellations


def test_a_rebooking_under_the_same_reference_keeps_the_new_flight():
    """Found in the audit: "your flight has been cancelled, we have rebooked you", under the
    same booking reference, removed the new flight too. The answer was "I didn't find any
    upcoming flights"."""
    rebooked = mail(
        "Your flight has changed",
        "We're sorry: your flight SK 870 on Thu 19 Nov 2026 has been cancelled.\n"
        "We have rebooked you. Booking reference: K7Q2ZP\n\n"
        "Flight SK 872\nFri 20 Nov 2026\nDepart: Vancouver (YVR) 18:45\n"
        "Arrive: Paris (CDG) 13:05 +1\n",
        day=2,
        month=11,
    )
    replaced: list[atlas.Flight] = []
    flights, _stays, _ = atlas.read_mail([mail("Booking K7Q2ZP", BOOKED), rebooked], replaced)
    assert flown(flights) == [
        ("SK 872", "20 Nov 18:45", "K7Q2ZP"),
        ("SK 871", "30 Nov 15:40", "K7Q2ZP"),  # the way home was never cancelled
    ]
    assert [f.flight for f in replaced] == ["SK 870"]  # its reminders are stale now
    [trip] = atlas.trips(flights, [], NOW)
    assert trip.title() == "Paris trip, Fri 20 to Mon 30 Nov"


def test_one_cancelled_flight_leaves_the_rest_of_the_booking():
    notice = mail(
        "Flight cancelled",
        "Your flight SK 871 has been cancelled. Booking reference: K7Q2ZP\n"
        "Contact us to choose another flight.\n",
        day=3,
        month=11,
    )
    flights, _, _ = read(mail("Booking K7Q2ZP", BOOKED), notice)
    assert flown(flights) == [("SK 870", "19 Nov 18:45", "K7Q2ZP")]


def test_a_cancelled_booking_goes_and_a_later_one_under_its_reference_stays():
    """A cancellation takes away what was booked before it, not what was booked after."""
    cancelled = mail(
        "Booking cancelled",
        "Your booking has been cancelled.\nBooking reference: K7Q2ZP\n",
        day=22,
    )
    assert read(mail("Booking K7Q2ZP", BOOKED), cancelled)[0] == []
    again = mail("Booking K7Q2ZP", BOOKED, day=25)  # booked again three days later
    flights, _, trips = read(mail("Booking K7Q2ZP", BOOKED), cancelled, again)
    assert [f.flight for f in flights] == ["SK 870", "SK 871"] and len(trips) == 1


def test_a_cancelled_hotel_goes_by_its_name_when_the_notice_has_no_number():
    stay = "The Maple Hotel\nAddress: 12 Rue Cler, 75007 Paris, France\n" \
           "Check-in: Friday, November 20, 2026\nCheck-out: Monday, November 30, 2026\n"  # fmt: skip
    booked = mail("Your stay at The Maple Hotel", "Your reservation is confirmed\n" + stay)
    gone = mail("Cancelled", "Your reservation has been cancelled.\n" + stay, day=28)
    assert [s.name for s in read(booked)[1]] == ["The Maple Hotel"]
    assert read(booked, gone)[1] == []


def test_a_cancelled_seat_is_not_a_cancelled_trip():
    seat = mail(
        "Seat change",
        "Your seat selection has been cancelled. Booking reference: K7Q2ZP\n"
        "Choose a new seat when you check in.\n",
        day=1,
        month=11,
    )
    assert len(read(mail("Booking K7Q2ZP", BOOKED), seat)[0]) == 2


# ---------------------------------------------------------------- finding 7: when it lands

TOKYO = """Booking reference: QX7T2M

Flight SK 3
Departs: Thu, Nov 19, 2026 1:00 PM Vancouver (YVR)
Arrives: Fri, Nov 20, 2026 3:30 PM Tokyo (HND)

Flight SK 4
Departs: Sun, Nov 29, 2026 5:00 PM Tokyo (HND)
Arrives: Sun, Nov 29, 2026 9:30 AM Vancouver (YVR)
"""


def test_the_landing_has_its_own_date():
    """Found in the audit: "Arrives: Fri, Nov 20, 3:30 PM" was put on the 19th, the day it
    left; and a flight home across the date line (leaves 5 PM, lands 9:30 AM the same day)
    on the day after, which made the trip a day longer."""
    flights, _, [trip] = read(mail("Your itinerary", TOKYO))
    out, back = flights
    assert (out.depart, out.arrive) == (datetime(2026, 11, 19, 13, 0), datetime(2026, 11, 20, 15, 30))
    assert (back.depart, back.arrive) == (datetime(2026, 11, 29, 17, 0), datetime(2026, 11, 29, 9, 30))
    assert out.line().startswith("13:00 SK 3 Vancouver (YVR) to Tokyo (HND), lands 15:30 the next day")
    assert "next day" not in back.line()
    assert trip.title() == "Tokyo trip, Thu 19 to Sun 29 Nov"
    plan = atlas.days(trip)
    assert plan[1] == {"heading": "Fri 20 Nov", "lines": ["Land in Tokyo (HND) (SK 3)"]}
    assert plan[-1]["heading"] == "Sun 29 Nov"
    # A flight that lands "before" it left is still in the air until it has left.
    assert atlas.trips([back], [], datetime(2026, 11, 29, 12, 0))


def test_a_time_before_the_city_is_not_part_of_its_name():
    """Found in the audit: "1:00 PM Vancouver (YVR)" made the city "PM Vancouver" and the
    trip a "PM Tokyo trip"."""
    out = atlas.read_flights(mail("Your itinerary", TOKYO))[0]
    assert (out.origin_city, out.dest_city) == ("Vancouver", "Tokyo")


def test_an_overnight_flight_without_a_date_still_lands_the_next_day():
    red_eye = "Booking reference: QX7T2M\nFlight SK 9 on Thu 19 Nov 2026\n" \
              "Depart: Vancouver (YVR) 23:30\nArrive: Toronto (YYZ) 06:45\n"  # fmt: skip
    [f] = atlas.read_flights(mail("Your flight", red_eye))
    assert f.arrive == datetime(2026, 11, 20, 6, 45)


# ---------------------------------------------------------------- finding 8: one trip, both ways

RETURN = """Booking reference: K7Q2ZP

OUTBOUND
Flight SK 482
Thu 12 Nov 2026
Depart: Vancouver (YVR) 08:35
Arrive: Toronto Pearson (YYZ) 16:02

RETURN
Flight SK 489
Mon 23 Nov 2026
Depart: Toronto Pearson (YYZ) 18:10
Arrive: Vancouver (YVR) 20:25
"""


def test_a_return_ticket_is_one_trip_with_reminders_both_ways():
    """Found in the audit: out on the 12th, back on the 23rd, no hotel in the mail (staying
    with family). That was a "Toronto trip" and a "Vancouver trip", and the flight home got
    no reminders."""
    _, _, trips = read(mail("Your trip to Toronto: booking K7Q2ZP", RETURN))
    assert [t.title() for t in trips] == ["Toronto trip, Thu 12 to Mon 23 Nov"]
    rem = atlas.reminders(trips[0], NOW)
    assert [(r["on"], r["time"]) for r in rem] == [
        ("2026-11-11", "19:00"),
        ("2026-11-12", "05:35"),
        ("2026-11-22", "19:00"),
        ("2026-11-23", "15:10"),
    ]
    assert rem[-1]["title"] == "Leave for YYZ: SK 489"


def one_way(flight: str, day: int, origin: str, dest: str, ref: str) -> dict[str, Any]:
    return mail(
        f"Booking {ref}",
        f"Booking reference: {ref}\nFlight {flight} on {day} Nov 2026\n"
        f"Depart: {origin} 09:00\nArrive: {dest} 12:00\n",
    )


def test_two_one_way_tickets_there_and_back_are_one_trip_and_two_trips_stay_two():
    out = one_way("SK 482", 12, "Vancouver (YVR)", "Toronto (YYZ)", "AAA111")
    back = one_way("SK 489", 23, "Toronto (YYZ)", "Vancouver (YVR)", "BBB222")
    assert [t.title() for t in read(out, back)[2]] == ["Toronto trip, Thu 12 to Mon 23 Nov"]
    # Home and back, then away again: the second trip is its own.
    again = one_way("SK 700", 27, "Vancouver (YVR)", "Calgary (YYC)", "CCC333")
    assert [t.destination for t in read(out, back, again)[2]] == ["Toronto", "Calgary"]


# ---------------------------------------------------------------- the smaller ones


def test_a_change_of_planes_is_not_where_the_trip_goes():
    """Found in the audit: Vancouver to Paris through Toronto, with the hotel in Paris, was a
    "Toronto trip"."""
    flights = mail(
        "Booking confirmation K7Q2ZP",
        "Booking reference: K7Q2ZP\n\n"
        "Flight SK 118\nThu 19 Nov 2026\nDepart: Vancouver (YVR) 08:00\n"
        "Arrive: Toronto Pearson (YYZ) 15:30\n\n"
        "Flight SK 880\nThu 19 Nov 2026\nDepart: Toronto Pearson (YYZ) 18:30\n"
        "Arrive: Paris (CDG) 07:45 +1\n\n"
        "Flight SK 881\nThu 26 Nov 2026\nDepart: Paris (CDG) 12:00\n"
        "Arrive: Toronto Pearson (YYZ) 14:10\n\n"
        "Flight SK 119\nThu 26 Nov 2026\nDepart: Toronto Pearson (YYZ) 17:00\n"
        "Arrive: Vancouver (YVR) 19:05\n",
    )
    hotel = mail(
        "Your stay at Hotel Lutece",
        "Your reservation is confirmed\n\nHotel Lutece\n"
        "Address: 45 Boulevard Raspail, 75006 Paris, France\nConfirmation number: 88412093\n\n"
        "Check-in: Friday, November 20, 2026 (from 3:00 PM)\n"
        "Check-out: Thursday, November 26, 2026 (until 11:00 AM)\n",
    )
    assert [t.title() for t in read(flights, hotel)[2]] == ["Paris trip, Thu 19 to Thu 26 Nov"]
    assert [t.destination for t in read(flights)[2]] == ["Paris"]  # and without the hotel


def test_a_heading_in_capitals_is_not_the_booking_reference():
    """Found in the audit: "E-TICKET CONFIRMATION" over "PASSENGER: ..." made PASSENGER the
    booking reference, on the itinerary and for the online check-in."""
    caps = "E-TICKET CONFIRMATION\nPASSENGER: ALEX TESTER\nBOOKING REFERENCE\nFLIGHT DETAILS\n" \
           "Booking reference: K7Q2ZP\n\nFlight SK 870\nThu 19 Nov 2026\n" \
           "Depart: Vancouver (YVR) 18:45\nArrive: Paris (CDG) 13:05 +1\n"  # fmt: skip
    assert atlas._refs(caps) == ["K7Q2ZP"]
    assert atlas.read_flights(mail("Your e-ticket", caps))[0].ref == "K7Q2ZP"
    # The ways a real code is written still read.
    assert atlas._refs("Your booking confirmation TP9WQL") == ["TP9WQL"]
    assert atlas._refs("Reservation code: QWERTY") == ["QWERTY"]  # an airline's six letters
    assert atlas._refs("Confirmation number: 88412093, itinerary # 72910384451") == [
        "88412093",
        "72910384451",
    ]
    assert atlas._refs("Booking Confirmation\nThank you for flying with us") == []


def test_day_first_and_dotted_dates_are_read():
    """Found in the audit: "Date: 05/12/2026" (5 December) was read as 12 May, in the past,
    so the trip vanished; "25/11/2026" and "19.11.2026" weren't dates at all."""

    def depart(date: str, extra: str = "") -> datetime | None:
        text = f"Booking reference: K7Q2ZP\n\nFlight SK 84\nDate: {date}\n" \
               f"Depart: Vancouver (YVR) 20:35\nArrive: London (LHR) 13:50 +1\n{extra}"  # fmt: skip
        got = atlas.read_flights(mail("Your e-ticket receipt", text))
        return got[0].depart if got else None

    assert depart("25/11/2026") == datetime(2026, 11, 25, 20, 35)
    assert depart("05/12/2026") == datetime(2026, 12, 5, 20, 35)
    assert depart("19.11.2026") == datetime(2026, 11, 19, 20, 35)  # and "19.11" is no time
    # Both ways round could be meant: a day-first date elsewhere in the mail settles it.
    assert depart("11/12/2026") == datetime(2026, 11, 12, 20, 35)
    assert depart("11/12/2026", "Issued: 20/10/2026\n") == datetime(2026, 12, 11, 20, 35)
    stay = atlas.read_stays(
        mail(
            "Your stay at The Maple Hotel",
            "The Maple Hotel\nCheck-in: 20.11.2026\nCheck-out: 26.11.2026\n",
        )
    )[0]
    assert (stay.check_in.date(), stay.check_out.date()) == (
        datetime(2026, 11, 20).date(),
        datetime(2026, 11, 26).date(),
    )


# ---------------------------------------------------------------- finding 4: how much mail is read


def as_sent(m: dict[str, Any]) -> dict[str, Any]:
    return {"from": m["from"], "subject": m["subject"], "body": m["text"], "date": m["date"]}


def chatter(n: int) -> list[dict[str, Any]]:
    """Mail that matches Atlas's search by a word and is no booking, all newer than the booking."""
    first = datetime(2026, 11, 1, 8, 0, tzinfo=TZ)
    return [
        {
            "from": f"City Tables {i} <news{i}@citytables.example>",
            "subject": "Your table reservation and this week's hotel news",
            "body": "A reservation reminder for dinner, and flight news. Nothing is booked here.",
            "date": first + timedelta(minutes=30 * i),
        }
        for i in range(n)
    ]


def paged(settings, mails: list[dict[str, Any]]):
    brain, rec, _ = connected(settings, [])
    fake = PagedGmail(mails)
    brain.connectors.google._api = fake  # type: ignore[method-assign]
    return brain, rec, fake


async def test_a_booking_behind_a_hundred_newer_mails_is_still_found(settings, monkeypatch):
    """Found in the audit: Atlas read the 40 newest mails that matched its search. 40 newer
    mails that only mention a hotel or a reservation, and the trip was "not in your mail"."""
    monkeypatch.setattr(atlas, "clock", lambda: NOW)
    brain, rec, fake = paged(settings, [as_sent(mail("Booking K7Q2ZP", RETURN)), *chatter(100)])
    await specialists.handle(brain, "travel", {"trip": ""})
    [ready] = await until(rec, "job_ready")
    assert ready["ok"] and ready["title"] == "Toronto trip, Thu 12 to Mon 23 Nov"
    assert "read your travel mail back to" not in ready["body"] and len(fake.queries) == 2


async def test_a_scan_that_was_cut_says_how_far_back_it_read(settings, monkeypatch):
    monkeypatch.setattr(atlas, "clock", lambda: NOW)
    noise = chatter(150)
    brain, rec, _ = paged(settings, [as_sent(mail("Booking K7Q2ZP", RETURN)), *noise])
    await specialists.handle(brain, "travel", {"trip": ""})
    [ready] = await until(rec, "job_ready")
    oldest = sorted(m["date"] for m in noise)[30]  # the newest 120 of them were read
    assert not ready["ok"] and ready["title"] == "No upcoming trips in your mail"
    assert ready["body"].endswith(
        f"I could only read your travel mail back to {oldest.day} Nov, so an older booking "
        "may be missing."
    )


# ---------------------------------------------------------------- reminders of a flight that moved


async def test_a_flight_that_moved_lists_its_old_reminders_as_stale(settings, monkeypatch):
    """Found in the audit: after a schedule change, asking again set reminders for the new
    time and left the old ones, so "time to head to the airport" came twice. Atlas now names
    the old ones in ``stale_reminders``; the brain's side takes them off."""
    monkeypatch.setattr(atlas, "clock", lambda: NOW)
    change = mail(
        "Schedule change for booking K7Q2ZP",
        "Your flight time has changed.\n\nBooking reference: K7Q2ZP\n\n"
        "Flight SK 489 on Mon 23 Nov 2026 now departs Toronto (YYZ) at 19:05 and arrives "
        "Vancouver\n(YVR) at 21:20. No action is needed.\n",
        day=2,
        month=11,
    )
    brain, rec, _ = paged(settings, [as_sent(mail("Booking K7Q2ZP", RETURN)), as_sent(change)])
    await specialists.handle(brain, "travel", {"trip": ""})
    await until(rec, "job_ready")
    await brain.jobs.wait_all()
    job = next(j for j in brain.jobs.jobs.values() if j.agent_id == "atlas")
    assert job.data["stale_reminders"] == [
        "atlas:SK 489:2026-11-23T18:10:eve",
        "atlas:SK 489:2026-11-23T18:10:go",
    ]
    keys = [r["key"] for r in job.data["reminders"]]
    assert "atlas:SK 489:2026-11-23T19:05:go" in keys
    assert not set(keys) & set(job.data["stale_reminders"])


# ================================================================ after the review of the fix


def booking(ref: str = "MLQXVT") -> dict[str, Any]:
    return mail(
        "Skyward Air - Booking Confirmation",
        f"Booking Reference: {ref}\n\nThu, Nov 19\n"
        "SK 870  Vancouver (YVR) 6:45 PM -> Toronto (YYZ) 10:05 PM\n",
        day=2,
        month=11,
    )


def test_a_cancellation_that_does_not_name_the_booking_still_cancels_it():
    """After the first fix a cancellation had to say "flight" or "booking" in its own
    sentence. "Cancellation confirmed" over the same reference, and "Your purchase was
    cancelled and your refund is being processed", left the flight in the trip, with its
    reminders."""
    confirmed = mail(
        "Cancellation confirmed",
        "Hi Alex,\n\nYour cancellation confirmation is below. Keep this email for your "
        "records.\n\nBooking Reference: MLQXVT\n\nThu, Nov 19\n"
        "SK 870  Vancouver (YVR) 6:45 PM -> Toronto (YYZ) 10:05 PM\n",
        day=5,
        month=11,
    )
    refund = mail(
        "Your refund request has been received",
        "Booking Reference: MLQXVT\n\nYour purchase was cancelled and your refund is being "
        "processed.\n",
        day=5,
        month=11,
    )
    is_confirmed = mail(
        "Your cancellation is confirmed",
        "Booking Reference: MLQXVT\n\nHi Alex,\n\nAs requested, your itinerary below has been "
        "cancelled and a refund issued.\n",
        day=5,
        month=11,
    )
    for notice in (confirmed, refund, is_confirmed):
        replaced: list[atlas.Flight] = []
        flights, _stays, _ = atlas.read_mail([booking(), notice], replaced)
        assert flights == [] and [f.flight for f in replaced] == ["SK 870"], notice["subject"]


def test_a_hotel_cancelled_by_its_number_alone_is_gone():
    booked = mail(
        "Your Harbour Inn reservation is confirmed",
        "Confirmation: 3412345678\n\nHarbour Inn Toronto\n"
        "Check-in: Thu, Nov 19, 2026 3:00 PM\nCheck-out: Sun, Nov 22, 2026 11:00 AM\n",
        day=2,
        month=11,
    )
    cancelled = mail(
        "Cancellation confirmed",
        "Cancellation number: 99887766\nConfirmation: 3412345678\n\n"
        "We're sorry your plans changed. We hope to see you soon.\n",
        day=6,
        month=11,
    )
    assert [s.ref for s in read(booked)[1]] == ["3412345678"]
    assert read(booked, cancelled)[1] == []


def test_a_seat_cancelled_under_a_booking_reference_line_keeps_the_flight():
    """Before the first fix this removed the flight, and after it too: "Booking Reference:"
    on the line above was read as part of the sentence, so the seat was "a booking"."""
    seat = mail(
        "Update to your seat",
        "Booking Reference: MLQXVT\nYour seat selection has been cancelled due to an aircraft "
        "change. Please choose a new seat.\n",
        day=5,
        month=11,
    )
    small_print = mail(
        "Travel tips for your trip",
        "Booking Reference: MLQXVT\nIf your flight is cancelled, we will rebook you at no "
        "charge. Your meal order was cancelled at your request.\n",
        day=6,
        month=11,
    )
    flights, _, _ = read(booking(), seat, small_print)
    assert flown(flights) == [("SK 870", "19 Nov 18:45", "MLQXVT")]


def flight(no: str, a: str, b: str, month: int, day: int, hour: int, ref: str = "") -> atlas.Flight:
    return atlas.Flight(
        flight=no,
        airline="Skyward",
        origin=a,
        dest=b,
        depart=datetime(2026, month, day, hour, 0),
        arrive=datetime(2026, month, day, hour + 2, 30),
        origin_city=a,
        dest_city=b,
        ref=ref,
    )


def legs(found: list[atlas.Trip]) -> list[str]:
    return [" + ".join(f"{f.origin}-{f.dest} {f.depart:%d %b}" for f in t.flights) for t in found]


def test_two_trips_to_the_same_city_in_a_month_are_two_trips():
    """After the first fix an outbound was joined to the last way back within 45 days, not
    the nearest: two round trips to Toronto three weeks apart were one trip of four flights,
    and a weekly commuter's six flights one trip of three weeks."""
    for refs in (("AAA111", "AAA111", "BBB222", "BBB222"), ("A1AAAA", "B2BBBB", "C3CCCC", "D4DDDD")):
        twice = [
            flight("SK 259", "YVR", "YYZ", 11, 12, 8, refs[0]),
            flight("SK 260", "YYZ", "YVR", 11, 16, 17, refs[1]),
            flight("SK 259", "YVR", "YYZ", 12, 3, 8, refs[2]),
            flight("SK 260", "YYZ", "YVR", 12, 7, 17, refs[3]),
        ]
        assert legs(atlas.trips(twice, [], NOW)) == [
            "YVR-YYZ 12 Nov + YYZ-YVR 16 Nov",
            "YVR-YYZ 03 Dec + YYZ-YVR 07 Dec",
        ]
    commute = [
        flight(no, a, b, 11, day, hour)
        for day in (12, 19, 26)
        for no, a, b, day, hour in (("SK 1", "YVR", "YYC", day, 8), ("SK 2", "YYC", "YVR", day + 3, 17))
    ]
    assert legs(atlas.trips(commute, [], NOW)) == [
        "YVR-YYC 12 Nov + YYC-YVR 15 Nov",
        "YVR-YYC 19 Nov + YYC-YVR 22 Nov",
        "YVR-YYC 26 Nov + YYC-YVR 29 Nov",
    ]
    # One return ticket with eleven days between is still one trip.
    ticket = [flight("SK 259", "YVR", "YYZ", 11, 12, 8, "AAA111"), flight("SK 260", "YYZ", "YVR", 11, 23, 17, "AAA111")]
    assert legs(atlas.trips(ticket, [], NOW)) == ["YVR-YYZ 12 Nov + YYZ-YVR 23 Nov"]


def test_six_letters_after_the_word_confirmation_are_the_booking_reference():
    """After the first fix a code after the bare word "Confirmation" needed a digit, so
    "eTicket Itinerary and Receipt for Confirmation KLMNOP" lost its reference (and with it
    the online check-in)."""
    united = mail(
        "eTicket Itinerary and Receipt for Confirmation KLMNOP",
        "Confirmation:\nKLMNOP\n\nThu, Nov 19\nSK 482  Vancouver (YVR) 6:45 AM -> Denver (DEN) 8:05 AM\n",
    )
    assert atlas.read_flights(united)[0].ref == "KLMNOP"
    assert atlas._refs("Confirmation:\nKLMNOP") == ["KLMNOP"]
    # The fault before that one stays fixed: headings in capitals are not codes.
    assert atlas._refs("E-TICKET CONFIRMATION\nPASSENGER: ALEX TESTER") == []
    assert atlas._refs("BOOKING CONFIRMATION FLIGHT DETAILS") == []
    assert atlas._refs("CONFIRMATION NUMBER\nTICKET") == []
