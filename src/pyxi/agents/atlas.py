"""Atlas, travel: trips built from the confirmations already in the user's mail.

Atlas reads airline and hotel confirmations (Gmail, fetched by the brain's Google connector),
pulls out the flights (number, airports, times, booking reference, check-in link) and the stays
(hotel or rental, address, check-in and check-out), groups them into trips, and lays each trip
out day by day. All of that is code: a scan costs nothing. Only a confirmation the code can't
read at all goes to the model, a few at a time, in one small call.

Atlas also works out reminders before each flight (the evening before, and three hours before
take-off). The brain saves them as routines (they need its routine store); online check-in is
handed to the flight check-in in :mod:`pyxi.companion.paperwork` when the user asks for it.

Times are the local times printed on the confirmation, like a paper ticket.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from pyxi import safety
from pyxi.mailwatch import DATE, merchant, parse_date, read_back_to, said_day

if TYPE_CHECKING:
    from pyxi.agents.jobs import Job, JobManager
    from pyxi.agents.registry import AgentProfile

QUERY = (
    'newer_than:180d -category:promotions (flight OR itinerary OR "boarding pass" OR '
    '"booking reference" OR "confirmation number" OR "booking confirmation" OR '
    'reservation OR hotel OR airbnb OR "check-in" OR "e-ticket")'
)

TRAVEL_WORDS = re.compile(
    r"\b(flight|itinerary|e-?ticket|boarding|airline|airways|air lines|depart|hotel|inn|suites|"
    r"resort|hostel|airbnb|vrbo|booking\.com|expedia|check-?in|your stay|reservation)\b",
    re.I,
)
CANCELLED = re.compile(
    r"\b(has been|was|is now|been) cancell?ed\b|\bcancell?ation (confirmed|confirmation)\b|"
    r"\byour (booking|reservation|trip|flight) (is|was) cancell?ed\b",
    re.I,
)
REF = re.compile(
    r"(?i:booking (?:reference|ref|code|number)|confirmation (?:code|number|no\.?|#)|"
    r"record locator|reservation (?:code|number|no\.?|#)|itinerary (?:number|no\.?|#)|\bPNR\b|booking id)"
    r"\s*(?i:is)?\s*[:#]?\s*#?\s*(?P<ref>[A-Z0-9]{5,14})\b"
)
# "Your booking confirmation TP9WQL": the word alone, so the code has to follow on the same
# line and have a digit in it. Live shape: "E-TICKET CONFIRMATION" over "PASSENGER: ..." made
# PASSENGER the booking reference, on the itinerary and for check-in.
REF_BARE = re.compile(r"(?i:confirmation)[ \t]*[:#-]?[ \t]*#?[ \t]*(?P<ref>[A-Z0-9]{5,14})\b")
# Words in capitals that follow a label in a table heading, not a code.
NOT_A_REF = {
    "FLIGHT", "FLIGHTS", "TICKET", "TICKETS", "NUMBER", "DETAIL", "DETAILS", "HOTEL", "HOTELS",
    "GUEST", "GUESTS", "EMAIL", "THANK", "THANKS", "PLEASE", "STATUS", "SUMMARY", "TRAVEL",
    "CHECK", "DATES", "BELOW", "ABOVE", "PRINT", "ADULT", "ADULTS", "NAMES", "TOTAL", "PRICE",
    "ROOMS", "NIGHT", "NIGHTS", "DEPART", "ARRIVE", "ARRIVAL", "RETURN", "BOOKING", "RECEIPT",
    "INVOICE", "PAYMENT", "ONLINE", "MANAGE", "CHANGE", "CHANGED", "UPDATE", "UPDATED",
    "CONFIRM", "PENDING", "ACTIVE", "ISSUED", "HOLDER", "CLIENT", "MEMBER", "FIRST", "TITLE",
    "WHERE", "WHICH", "ABOUT", "VALID", "ENTER", "CLICK", "HELLO",
}  # fmt: skip
# A notice that puts them on another flight, not one that only takes a booking away.
REBOOKED = re.compile(
    r"\b(?:re-?booked|re-?accommodated|new (?:flight|itinerary|booking|reservation)s?|"
    r"alternative flight|replacement flight|moved you|now (?:booked|confirmed) on)\b",
    re.I,
)
# What a cancellation has to be about to count: "your seat selection was cancelled" is not
# the trip.
BOOKED_THING = re.compile(
    r"\b(?:flights?|bookings?|reservations?|trips?|itinerar(?:y|ies)|stays?|hotels?|rooms?|"
    r"tickets?)\b",
    re.I,
)
# "SK 482", "AC123", "WS 702", "U2 8421". Two characters (letters, or a letter and a digit),
# then 1-4 digits, not part of a time or a longer word.
FLIGHT_NO = re.compile(
    r"(?<![A-Za-z0-9])(?P<code>[A-Z]{2}|[A-Z]\d|\d[A-Z])\s?(?P<num>\d{1,4})(?![\d:.,A-Za-z])"
)
IATA_PAREN = re.compile(r"\(([A-Z]{3})\)")
IATA_BARE = re.compile(r"(?<![A-Za-z])([A-Z]{3})(?![A-Za-z])")
NOT_AIRPORT = {
    "THE",
    "AND",
    "FOR",
    "YOU",
    "USD",
    "CAD",
    "EUR",
    "GBP",
    "AUD",
    "PNR",
    "ETA",
    "ETD",
    "REF",
    "NEW",
    "TAX",
    "FEE",
    "SEAT",
    "NON",
    "MON",
    "TUE",
    "WED",
    "THU",
    "FRI",
    "SAT",
    "SUN",
    "JAN",
    "FEB",
    "MAR",
    "APR",
    "MAY",
    "JUN",
    "JUL",
    "AUG",
    "SEP",
    "OCT",
    "NOV",
    "DEC",
    "AIR",
    "VIA",
    "PDF",
    "APP",
    "FAQ",
    "TBD",
    "TBA",
    "ONE",
    "WAY",
    "ALL",
    "GMT",
    "UTC",
    "PST",
    "EST",
    "PDT",
    "EDT",
    "CET",
    "BST",
    "AST",
    "MST",
    "CST",
    "MDT",
    "CDT",
    "AM",
    "PM",
}
TIME = re.compile(
    r"(?<![\d$€£.,:])(?P<h>[01]?\d|2[0-3])(?:[:h.](?P<m>[0-5]\d))?\s*(?P<ap>[ap]\.?\s?m\.?)?(?![\d%])",
    re.I,
)
DATE_RE = re.compile(DATE, re.I)
# The city of a big airport, for confirmations that only print the code.
CITIES = {
    "YVR": "Vancouver",
    "YYZ": "Toronto",
    "YTZ": "Toronto",
    "YUL": "Montreal",
    "YYC": "Calgary",
    "YEG": "Edmonton",
    "YOW": "Ottawa",
    "YHZ": "Halifax",
    "YWG": "Winnipeg",
    "YYJ": "Victoria",
    "SEA": "Seattle",
    "SFO": "San Francisco",
    "LAX": "Los Angeles",
    "JFK": "New York",
    "LGA": "New York",
    "EWR": "Newark",
    "ORD": "Chicago",
    "BOS": "Boston",
    "DFW": "Dallas",
    "ATL": "Atlanta",
    "MIA": "Miami",
    "DEN": "Denver",
    "LAS": "Las Vegas",
    "PHX": "Phoenix",
    "IAD": "Washington",
    "DCA": "Washington",
    "HNL": "Honolulu",
    "MEX": "Mexico City",
    "CUN": "Cancun",
    "LHR": "London",
    "LGW": "London",
    "CDG": "Paris",
    "ORY": "Paris",
    "AMS": "Amsterdam",
    "FRA": "Frankfurt",
    "MUC": "Munich",
    "MAD": "Madrid",
    "BCN": "Barcelona",
    "LIS": "Lisbon",
    "FCO": "Rome",
    "DUB": "Dublin",
    "ZRH": "Zurich",
    "HND": "Tokyo",
    "NRT": "Tokyo",
    "ICN": "Seoul",
    "HKG": "Hong Kong",
    "SIN": "Singapore",
    "DXB": "Dubai",
    "DOH": "Doha",
    "SYD": "Sydney",
    "MEL": "Melbourne",
    "DEL": "Delhi",
    "BOM": "Mumbai",
    "DAC": "Dhaka",
    "IST": "Istanbul",
}
# Airport names after the city: "Toronto Pearson" is Toronto, "London Heathrow" is London.
AIRPORT_WORDS = re.compile(
    r"\s+(Pearson|Heathrow|Gatwick|Stansted|Luton|Charles de Gaulle|Orly|O'Hare|Midway|JFK|"
    r"LaGuardia|Newark|Logan|Dulles|Reagan|Schiphol|Trudeau|Narita|Haneda|Changi|Barajas|"
    r"Fiumicino|Malpensa|Linate|Kastrup|Arlanda|Tegel|Brandenburg|Humberto Delgado|Portela|"
    r"Sea-Tac|Intercontinental|Hobby|Love Field)\b.*$",
    re.I,
)
STREET = re.compile(
    r"^\s*\d{1,6}[A-Za-z]?\s+[A-Za-z0-9 .'-]{2,40}?\b(?:St|Street|Ave|Avenue|Rd|Road|Blvd|"
    r"Boulevard|Dr|Drive|Way|Lane|Ln|Pl|Place|Pkwy|Parkway|Hwy|Highway|Cres|Crescent|Ct|Court|"
    r"Sq|Square|Terrace|Ter|Row|Quay|Strasse|Straße|Rua|Calle|Via)\b\.?.*$",
    re.I | re.M,
)
STAY_NAME = [
    re.compile(
        r"^(?:hotel|property|accommodation|where you're staying|staying at)(?: name)?\s*:\s*(.{3,80})$",
        re.I | re.M,
    ),
    re.compile(
        r"(?:reservation|booking|stay|room)\s+(?:is\s+)?(?:confirmed\s+)?(?:at|for)\s+(.{3,70}?)(?:[,.!:(]|\s+from\b|\s+on\b|\s+in\s+\w+\s*$|$)",
        re.I,
    ),
    re.compile(
        r"^((?:The\s+)?[A-Z][\w'&.-]*(?:\s+[A-Z&][\w'&.-]*){0,5}\s+(?:Hotel|Inn|Suites|Resort|Hostel|Lodge|Motel)(?:\s+[A-Z][\w'&.-]*){0,3})\s*$",
        re.M,
    ),
    re.compile(r"^((?:Hotel|The)\s+[A-Z][\w'&.-]*(?:\s+[A-Z][\w'&.-]*){0,4})\s*$", re.M),
]


@dataclass
class Flight:
    flight: str  # "SK 482"
    airline: str
    origin: str  # "YVR"
    dest: str
    depart: datetime  # local time at the origin, as printed
    arrive: datetime | None = None
    origin_city: str = ""
    dest_city: str = ""
    ref: str = ""
    checkin_url: str = ""
    email_id: str = ""
    subject: str = ""

    @property
    def start(self) -> datetime:
        return self.depart

    @property
    def end(self) -> datetime:
        # A flight home across the date line lands "before" it left, on the clock.
        return max(self.arrive, self.depart) if self.arrive else self.depart

    def place(self, which: str) -> str:
        city, code = (
            (self.origin_city, self.origin) if which == "from" else (self.dest_city, self.dest)
        )
        return f"{city} ({code})" if city else code

    def line(self) -> str:
        lands = ""
        if self.arrive:
            nxt = " the next day" if self.arrive.date() > self.depart.date() else ""
            lands = f", lands {self.arrive:%H:%M}{nxt}"
        ref = f". Booking {self.ref}" if self.ref else ""
        return f"{self.depart:%H:%M} {self.flight} {self.place('from')} to {self.place('to')}{lands}{ref}"


@dataclass
class Stay:
    name: str
    check_in: datetime
    check_out: datetime
    address: str = ""
    ref: str = ""
    email_id: str = ""
    subject: str = ""
    times: dict[str, bool] = field(default_factory=dict)  # which times the email gave

    @property
    def start(self) -> datetime:
        return self.check_in

    @property
    def end(self) -> datetime:
        return self.check_out

    @property
    def nights(self) -> int:
        return max(1, (self.check_out.date() - self.check_in.date()).days)


@dataclass
class Trip:
    flights: list[Flight]
    stays: list[Stay]

    @property
    def start(self) -> datetime:
        return min(x.start for x in [*self.flights, *self.stays])

    @property
    def end(self) -> datetime:
        return max(x.end for x in [*self.flights, *self.stays])

    def stay_city(self) -> str:
        for s in self.stays:
            for part in [p.strip() for p in s.address.split(",")][1:]:
                words = [w for w in part.split() if not re.search(r"\d", w)]
                if words and not (len(words) == 1 and len(words[0]) <= 3 and words[0].isupper()):
                    return " ".join(words)
        return ""

    def far_flight(self) -> Flight | None:
        """The flight that lands where the trip is going: the stop they stay at longest, or
        the last landing when there's no way back yet. Never home, and not a connection: a
        change of planes in Toronto on the way to Paris is a Paris trip."""
        fs = sorted(self.flights, key=lambda f: f.depart)
        if not fs:
            return None
        home = fs[0].origin
        best, longest = fs[0], timedelta(days=-1)
        for i, f in enumerate(fs):
            if f.dest == home:
                continue
            stop = fs[i + 1].depart - f.end if i + 1 < len(fs) else timedelta(days=9999)
            if stop > longest:
                best, longest = f, stop
        return best

    @property
    def destination(self) -> str:
        city = self.stay_city()
        f = self.far_flight()
        if f is not None:
            name = re.sub(AIRPORT_WORDS, "", f.dest_city).strip() or f.dest_city
            if city and name[:4].lower() == city[:4].lower():
                return city if len(city) < len(name) else name
            return name or f.dest
        return city or self.stays[0].name

    def title(self) -> str:
        a, b = self.start, self.end
        if a.date() == b.date():
            span = f"{a:%a} {a.day} {a:%b}"
        elif (a.year, a.month) == (b.year, b.month):
            span = f"{a:%a} {a.day} to {b:%a} {b.day} {b:%b}"
        else:
            span = f"{a:%a} {a.day} {a:%b} to {b:%a} {b.day} {b:%b}"
        return f"{self.destination} trip, {span}"

    def text(self) -> str:
        return " ".join(
            [
                f"{f.flight} {f.origin_city} {f.origin} {f.dest_city} {f.dest} {f.ref}"
                for f in self.flights
            ]
            + [f"{s.name} {s.address} {s.ref}" for s in self.stays]
        ).lower()


# ------------------------------------------------------------------------------- reading mail
def _time(m: re.Match[str]) -> tuple[int, int] | None:
    h, mins, ap = int(m.group("h")), int(m.group("m") or 0), (m.group("ap") or "").lower()
    if not m.group("m") and not ap:
        return None  # a bare number isn't a time
    if ap.startswith("p") and h < 12:
        h += 12
    if ap.startswith("a") and h == 12:
        h = 0
    if h > 23:
        return None
    return h, mins


def _date(text: str, ref: datetime, day_first: bool = False) -> datetime | None:
    m = DATE_RE.search(text)
    if not m:
        return None
    when = parse_date(m.group(0), ref, day_first)
    return when.replace(tzinfo=None) if when else None


def _day_first(text: str) -> bool:
    """Does this mail write its dates day first? One "25/11/2026" in it says so, and then its
    "05/12/2026" is 5 December too."""
    for m in re.finditer(r"(?<![\d/])(\d{1,2})/(\d{1,2})/(?:\d{4}|\d{2})(?![\d/])", text):
        if int(m.group(1)) > 12:
            return True
        if int(m.group(2)) > 12:
            return False
    return False


def _city(text: str, code: str) -> str:
    m = re.search(
        rf"([A-ZÀ-Þ][a-zA-ZÀ-ÿ.'-]+(?:[ -][A-ZÀ-Þ][a-zA-ZÀ-ÿ.'-]+){{0,3}})\s*(?:[,-]\s*[\w ]{{0,25}})?\(\s*{code}\s*\)",
        text,
    )
    if not m:
        return ""
    words = m.group(1).split()
    while words and words[0].lower() in {
        "from",
        "to",
        "depart",
        "departs",
        "arrive",
        "arrives",
        "departure",
        "arrival",
        "leaving",
        "landing",
        "in",
        "at",
        # "1:00 PM Vancouver (YVR)": the end of the time is not the start of the city
        "am",
        "pm",
        "a.m.",
        "p.m.",
    }:
        words = words[1:]
    city = " ".join(words)
    city = re.sub(r"\s+(International|Intl\.?|Airport|Terminal.*)$", "", city).strip()
    return city


def _refs(text: str) -> list[str]:
    """The booking references in a mail, in the order they appear, each once."""
    found = [(m.start(), m.group("ref"), False) for m in REF.finditer(text)]
    found += [(m.start(), m.group("ref"), True) for m in REF_BARE.finditer(text)]
    out: list[str] = []
    for _, ref, bare in sorted(found):
        if ref.isdigit():
            ok = len(ref) >= 6
        elif ref.isalpha():  # an airline's code of letters, or a heading in capitals
            ok = not bare and 5 <= len(ref) <= 7 and ref not in NOT_A_REF
        else:
            ok = True
        if ok and ref not in out:
            out.append(ref)
    return out


def _flight_numbers(text: str) -> list[re.Match[str]]:
    """ "SK 482", "AC123": where a mail names a flight."""
    return [
        m
        for m in FLIGHT_NO.finditer(text)
        if m.group("code") not in {"AM", "PM", "NO", "ID", "TO", "AT", "ON", "BC", "QC"}
        # a code that starts with a digit ("2E 51") only counts right after the word "flight",
        # so a postcode like "M6J 1J3" never reads as one
        and (
            not m.group("code")[0].isdigit()
            or re.search(r"flight\W{0,4}$", text[max(0, m.start() - 12) : m.start()], re.I)
        )
    ]


def _link(mail: dict[str, Any], pattern: str) -> str:
    for words, url in mail.get("links") or []:
        if re.search(pattern, f"{words} {url}", re.I) and str(url).startswith("http"):
            return str(url)
    return ""


def _mail_date(mail: dict[str, Any]) -> datetime:
    d = mail.get("date")
    if isinstance(d, datetime):
        return d
    try:
        return datetime.fromisoformat(str(d))
    except (TypeError, ValueError):
        return datetime.now().astimezone()


def _landing_day(
    window: str,
    dep_at: re.Match[str],
    arr_at: re.Match[str],
    sent: datetime,
    day_first: bool,
) -> datetime | None:
    """The date printed with the arrival time: on its line, after the departure time when
    that is on the same line. None when the mail gives the landing no date of its own."""
    a = window.rfind("\n", 0, arr_at.start()) + 1
    b = window.find("\n", arr_at.end())
    b = len(window) if b < 0 else b
    if a <= dep_at.start() < arr_at.start():
        a = dep_at.end()
    dates = list(DATE_RE.finditer(window, a, b))
    if not dates:
        return None
    near = min(
        dates, key=lambda d: min(abs(d.start() - arr_at.end()), abs(arr_at.start() - d.end()))
    )
    return _date(near.group(0), sent, day_first)


def read_flights(mail: dict[str, Any]) -> list[Flight]:
    text = f"{mail.get('subject', '')}\n{mail.get('text', '')}"
    sent = _mail_date(mail)
    refs = _refs(text)
    airline = merchant(str(mail.get("from") or ""))
    day_first = _day_first(text)
    hits = _flight_numbers(text)
    out: list[Flight] = []
    for i, m in enumerate(hits):
        stop = hits[i + 1].start() if i + 1 < len(hits) else len(text)
        window = text[m.end() : min(stop, m.end() + 420)]
        codes = [c for c in IATA_PAREN.findall(window)]
        if len(set(codes)) < 2:
            codes = [
                c
                for c in IATA_BARE.findall(window)
                if c not in NOT_AIRPORT and c != m.group("code")
            ]
        uniq: list[str] = []
        for c in codes:
            if c not in uniq:
                uniq.append(c)
        if len(uniq) < 2:
            continue
        times: dict[str, tuple[int, int]] = {}
        where: dict[str, re.Match[str]] = {}  # the same times, as found in the window
        order: list[tuple[int, int]] = []
        marks: list[re.Match[str]] = []
        ends: list[int] = []  # where each time ends in the window
        dated = [d.span() for d in DATE_RE.finditer(window)]
        for t in TIME.finditer(window):
            hm = _time(t)
            if hm is None or any(a <= t.start() < b for a, b in dated):
                continue  # not a time, or the "19.11" of "19.11.2026"
            before = window[max(0, t.start() - 28) : t.start()].lower()
            if (
                re.search(r"depart|leav|takes? off|departure|boarding", before)
                and "depart" not in times
            ):
                times["depart"], where["depart"] = hm, t
            elif re.search(r"arriv|land", before) and "arrive" not in times:
                times["arrive"], where["arrive"] = hm, t
            order.append(hm)
            marks.append(t)
            ends.append(t.end())
        dep = times.get("depart") or (order[0] if order else None)
        arr = times.get("arrive") or next((t for t in order if t != dep), None)
        if dep is None:
            continue
        dep_at = where.get("depart") or marks[0]
        arr_at = where.get("arrive") or next(
            (t for t, hm in zip(marks, order, strict=True) if hm != dep), None
        )
        # The date is on the flight's own lines, or on a heading line above the flight number
        # ("Thu, Nov 19" then "AC 870  YVR 6:45 PM -> CDG"). A date after this flight's times,
        # when it has such a heading, is the next flight's heading, not this one's.
        lead = text[max(hits[i - 1].end() if i else 0, m.start() - 220) : m.start()]
        dates = list(DATE_RE.finditer(lead))
        own = DATE_RE.search(window)
        if own and dates and ends and own.start() >= ends[min(1, len(ends) - 1)]:
            d = dates[-1]
            line = lead[lead.rfind("\n", 0, d.start()) + 1 : d.start()] + lead[d.end() :]
            if not any(_time(t) for t in TIME.finditer(line.split("\n")[0])):
                own = None  # a heading line of its own, with no times on it
        day = _date(own.group(0), sent, day_first) if own else None
        if day is None:
            day = _date(dates[-1].group(0), sent, day_first) if dates else None
        if day is None:
            continue
        depart = day.replace(hour=dep[0], minute=dep[1], second=0, microsecond=0)
        arrive = None
        if arr:
            arrive = day.replace(hour=arr[0], minute=arr[1], second=0, microsecond=0)
            # The landing's own date, when the mail prints one, beats any guess. Live shapes:
            # "Arrives: Fri, Nov 20 3:30 PM" was put on the 19th, and a flight home across
            # the date line (leaves 5 PM, lands 9:30 AM the same day) on the day after.
            landed = _landing_day(window, dep_at, arr_at, sent, day_first) if arr_at else None
            if landed is not None and 0 <= (landed.date() - day.date()).days <= 2:
                arrive = landed.replace(hour=arr[0], minute=arr[1], second=0, microsecond=0)
            elif arrive <= depart or re.search(r"\+1\b|next day", window[:200], re.I):
                arrive += timedelta(days=1)
        origin, dest = uniq[0], uniq[1]
        out.append(
            Flight(
                flight=f"{m.group('code')} {m.group('num')}",
                airline=airline,
                origin=origin,
                dest=dest,
                depart=depart,
                arrive=arrive,
                origin_city=_city(window, origin) or _city(text, origin) or CITIES.get(origin, ""),
                dest_city=_city(window, dest) or _city(text, dest) or CITIES.get(dest, ""),
                ref=refs[0] if refs else "",
                checkin_url=_link(mail, r"check.?in"),
                email_id=str(mail.get("id") or ""),
                subject=str(mail.get("subject") or ""),
            )
        )
    return out


def _stay_date(
    text: str, word: str, sent: datetime, day_first: bool = False
) -> tuple[datetime | None, tuple[int, int] | None]:
    m = re.search(
        rf"{word}(?:\s*date)?\b[^\n\d]{{0,24}}?(?P<d>{DATE})(?P<rest>[^\n]{{0,60}})", text, re.I
    )
    if not m:
        return None, None
    day = _date(m.group("d"), sent, day_first)
    hm = None
    for t in TIME.finditer(m.group("rest")):
        if (hm := _time(t)) is not None:
            break
    if hm is None:  # "Check-in: from 15:00" on the next line
        after = text[m.end() : m.end() + 80]
        t2 = re.search(rf"(?:from|after|by|before|until)\s+{TIME.pattern}", after, re.I)
        if t2:
            hm = _time(TIME.search(t2.group(0)))  # type: ignore[arg-type]
    return day, hm


def _stay_name(mail: dict[str, Any], text: str) -> str:
    subject = str(mail.get("subject") or "")
    for pat in STAY_NAME:
        for src in (text, subject) if pat is STAY_NAME[0] else (subject, text):
            m = pat.search(src)
            if m:
                name = m.group(1).strip(" .,:-")
                if (
                    3 <= len(name) <= 80
                    and not DATE_RE.match(name)
                    and not re.match(r"(?i)(your|a|an)\b", name)
                ):
                    return name
    sender = merchant(str(mail.get("from") or ""))
    return (
        sender
        if sender and sender.lower() not in {"booking.com", "expedia", "airbnb", "hotels.com"}
        else ""
    )


def read_stays(mail: dict[str, Any]) -> list[Stay]:
    text = f"{mail.get('subject', '')}\n{mail.get('text', '')}"
    sent = _mail_date(mail)
    day_first = _day_first(text)
    cin, tin = _stay_date(text, r"check[- ]?in", sent, day_first)
    cout, tout = _stay_date(text, r"check[- ]?out", sent, day_first)
    if cin is None or cout is None:
        arr = re.search(rf"\b(?:arrival|arriving)\b[^\n\d]{{0,12}}(?P<d>{DATE})", text, re.I)
        dep = re.search(
            rf"\b(?:departure|departing|leaving)\b[^\n\d]{{0,12}}(?P<d>{DATE})", text, re.I
        )
        if arr and dep and re.search(r"hotel|stay|room|nights?", text, re.I):
            cin = _date(arr.group("d"), sent, day_first)
            cout = _date(dep.group("d"), sent, day_first)
    if cin is None or cout is None or cout <= cin:
        return []
    name = _stay_name(mail, text)
    if not name:
        return []
    addr = ""
    m = re.search(r"^(?:address|location|where)\s*:\s*(.{6,140})$", text, re.I | re.M)
    if m:
        addr = m.group(1).strip()
    elif s := STREET.search(text):
        addr = s.group(0).strip()
    refs = _refs(text)
    cin = cin.replace(hour=tin[0], minute=tin[1]) if tin else cin.replace(hour=15)
    cout = cout.replace(hour=tout[0], minute=tout[1]) if tout else cout.replace(hour=11)
    return [
        Stay(
            name=name,
            check_in=cin,
            check_out=cout,
            address=addr,
            ref=refs[0] if refs else "",
            email_id=str(mail.get("id") or ""),
            subject=str(mail.get("subject") or ""),
            times={"in": bool(tin), "out": bool(tout)},
        )
    ]


PROMO = re.compile(
    r"\b(sale|deals?|save up to|fares from|newsletter)\b|% off|from \$\d|unsubscribe", re.I
)


def looks_like_travel(mail: dict[str, Any]) -> bool:
    """A booking the code couldn't read, worth one look by the model: flight or hotel words
    plus something only a real booking has (airport codes, check-in and check-out, a ticket)."""
    text = f"{mail.get('subject', '')}\n{str(mail.get('text', ''))[:2000]}"
    if not TRAVEL_WORDS.search(text) or PROMO.search(text[:400]):
        return False
    return bool(
        len(set(IATA_PAREN.findall(text))) >= 2
        or (re.search(r"check[- ]?in", text, re.I) and re.search(r"check[- ]?out", text, re.I))
        or re.search(r"e-?ticket|boarding pass|record locator|booking reference", text, re.I)
        # "Your reservation code is QX7T2M": a booking written as prose, with no airport codes
        or _refs(text)
    )


def _flight_key(f: Flight) -> tuple[str, str]:
    return f.flight, f.depart.date().isoformat()


def _stay_key(s: Stay) -> str:
    return s.ref or f"{s.name.lower()}:{s.check_in.date()}"


def _cancelled(text: str, sent: datetime, day_first: bool) -> tuple[bool, set[str], set[str]]:
    """What a mail cancels: (does it cancel a booking at all, the flight numbers its
    cancellation sentence names, the days it names for them). No flight named means the whole
    booking; "your seat selection was cancelled" is neither."""
    about, named, days = False, set(), set()
    for m in CANCELLED.finditer(text):
        head = text[max(0, m.start() - 200) : m.start()]
        cut = max((x.end() for x in re.finditer(r"[.!?]\s|\n\s*\n", head)), default=0)
        tail = text[m.end() : m.end() + 120]
        stop = re.search(r"[.!?]\s|\n\s*\n", tail)
        sentence = head[cut:] + m.group(0) + (tail[: stop.start()] if stop else tail)
        numbers = [f"{n.group('code')} {n.group('num')}" for n in _flight_numbers(sentence)]
        if not numbers and not BOOKED_THING.search(sentence):
            continue
        about = True
        named.update(numbers)
        for d in DATE_RE.finditer(sentence):
            when = _date(d.group(0), sent, day_first)
            if when and numbers:
                days.add(when.date().isoformat())
    return about, named, days


def read_mail(
    mails: list[dict[str, Any]], replaced: list[Flight] | None = None
) -> tuple[list[Flight], list[Stay], list[dict[str, Any]]]:
    """Every flight and stay in these emails, newest information winning, cancelled ones gone.
    Also the travel-looking emails the code couldn't read (for the model to try).

    A cancellation takes away what was booked before it, and only what it names: the flight
    in its sentence, or with no flight named the bookings under its reference. Live shape: "AC
    870 has been cancelled, we have rebooked you on AC 872", same reference, used to remove
    the new flight too, and the trip with it. ``replaced``: a list the flights that were
    cancelled or moved are added to (their reminders are stale)."""
    flights: dict[tuple[str, str], Flight] = {}
    stays: dict[str, Stay] = {}
    gone: list[Flight] = replaced if replaced is not None else []
    unread: list[dict[str, Any]] = []
    for mail in sorted(mails, key=_mail_date):  # oldest first, so a schedule change wins
        text = f"{mail.get('subject', '')}\n{mail.get('text', '')}"
        fs = read_flights(mail)
        ss = read_stays(mail)  # a package deal has both
        about, named, days = (
            _cancelled(text, _mail_date(mail), _day_first(text))
            if CANCELLED.search(text)
            else (False, set(), set())
        )
        if about:
            rebooked = bool(REBOOKED.search(text))
            dropped: set[tuple[str, str]] = set()
            refs = set() if named else set(_refs(text))
            # A plain cancellation that names no flight spells out again what it cancels.
            whole = not named and not rebooked
            restated_f = {_flight_key(f) for f in fs} if whole else set()
            restated_s = {_stay_key(s) for s in ss} if whole else set()
            for k, old in list(flights.items()):
                if (
                    (old.flight in named and (not days or k[1] in days))
                    or (old.ref and old.ref in refs)
                    or k in restated_f
                ):
                    gone.append(flights.pop(k))
                    dropped.add(k)
            for key, kept in list(stays.items()):
                if (kept.ref and kept.ref in refs) or key in restated_s:
                    del stays[key]
            if not rebooked:
                continue  # it only says what is gone
            # A rebooking: what it lists is the trip now, except the cancelled flight repeated.
            fs = [
                f
                for f in fs
                if not (
                    f.flight in named
                    and (_flight_key(f)[1] in days or (not days and _flight_key(f) in dropped))
                )
            ]
        for f in fs:
            key = _flight_key(f)
            before = flights.get(key)
            if before is not None and before.depart != f.depart:
                gone.append(before)  # the same flight that day, at a new time
            flights[key] = f
            if f.ref:  # a changed flight: the older times under the same booking go
                for k, old in list(flights.items()):
                    if (
                        old.ref == f.ref
                        and old.flight == f.flight
                        and k != key
                        and old.email_id != f.email_id
                    ):
                        gone.append(flights.pop(k))
        for s in ss:
            stays[_stay_key(s)] = s
        if not fs and not ss and looks_like_travel(mail):
            unread.append(mail)
    return (
        sorted(flights.values(), key=lambda f: f.depart),
        sorted(stays.values(), key=lambda s: s.check_in),
        unread,
    )


# ------------------------------------------------------------------------------- the model, rarely
MODEL_SYSTEM = (
    "You read travel confirmation emails and return JSON only: "
    '{"flights":[{"flight":"AC 123","airline":"","from":"YYZ","from_city":"","to":"YVR",'
    '"to_city":"","depart":"YYYY-MM-DDTHH:MM","arrive":"YYYY-MM-DDTHH:MM or empty","ref":""}],'
    '"stays":[{"name":"","address":"","check_in":"YYYY-MM-DDTHH:MM","check_out":"YYYY-MM-DDTHH:MM","ref":""}]}. '
    "Times are the local times printed in the email. Leave out anything that isn't a confirmed "
    "flight or stay (ads, price alerts, cancelled bookings). Never invent a value: leave it empty."
)


def from_model(raw: str, mails: list[dict[str, Any]]) -> tuple[list[Flight], list[Stay]]:
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        return [], []
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return [], []

    def when(v: Any) -> datetime | None:
        try:
            d = datetime.fromisoformat(str(v).strip())
        except ValueError:
            return None
        return d.replace(tzinfo=None)

    flights, stays = [], []
    for f in data.get("flights") or []:
        dep = when(f.get("depart"))
        if not dep or not f.get("from") or not f.get("to") or not f.get("flight"):
            continue
        flights.append(
            Flight(
                flight=str(f["flight"]).upper().strip(),
                airline=str(f.get("airline") or ""),
                origin=str(f["from"]).upper()[:3],
                dest=str(f["to"]).upper()[:3],
                depart=dep,
                arrive=when(f.get("arrive")),
                origin_city=str(f.get("from_city") or ""),
                dest_city=str(f.get("to_city") or ""),
                ref=str(f.get("ref") or "").upper(),
            )
        )
    for s in data.get("stays") or []:
        cin, cout = when(s.get("check_in")), when(s.get("check_out"))
        if not cin or not cout or cout <= cin or not s.get("name"):
            continue
        stays.append(
            Stay(
                str(s["name"]),
                cin,
                cout,
                str(s.get("address") or ""),
                str(s.get("ref") or "").upper(),
            )
        )
    return flights, stays


# ------------------------------------------------------------------------------- trips
RETURN_WITHIN = timedelta(days=45)  # a way back this soon after the way out is the same trip


def _one_trip(out: Trip, back: Trip) -> bool:
    """Is the later group the rest of the earlier one's trip? Flights on one booking are (a
    return ticket), and so is a flight home from where the earlier one landed."""
    refs = {f.ref for f in out.flights if f.ref}
    if any(f.ref in refs for f in back.flights):
        return True
    if not out.flights or not back.flights:
        return False
    home, away = out.flights[0].origin, out.flights[-1].dest
    first = back.flights[0]
    return (
        away != home
        and first.origin == away
        and back.flights[-1].dest == home
        and first.depart - out.end <= RETURN_WITHIN
    )


def trips(flights: list[Flight], stays: list[Stay], now: datetime, gap_days: int = 2) -> list[Trip]:
    """Bookings grouped into trips (a gap of more than ``gap_days`` starts a new one, unless
    the flights on both sides of it are one return ticket); only trips that haven't ended
    yet, soonest first.

    Live shape: out on the 12th, back on the 23rd, staying with family so no hotel in the
    mail. That was two trips, and the way home got no reminders."""
    items = sorted([*flights, *stays], key=lambda x: x.start)
    groups: list[Trip] = []
    end: datetime | None = None
    for x in items:
        if end is None or x.start.date() > (end + timedelta(days=gap_days)).date():
            groups.append(Trip([], []))
            end = x.end
        (groups[-1].flights if isinstance(x, Flight) else groups[-1].stays).append(x)
        end = max(end, x.end)
    joined = True
    while joined:
        joined = False
        for i in range(len(groups)):
            j = next(
                (j for j in range(len(groups) - 1, i, -1) if _one_trip(groups[i], groups[j])), 0
            )
            if j:  # everything between the way out and the way back happened on that trip
                for g in groups[i + 1 : j + 1]:
                    groups[i].flights += g.flights
                    groups[i].stays += g.stays
                del groups[i + 1 : j + 1]
                joined = True
                break
    return [t for t in groups if t.end >= now]


def _plain(s: str) -> str:
    """Lower case without accents, so "Zürich" finds "Zurich" and "hôtel" finds "hotel"."""
    import unicodedata

    return "".join(
        c for c in unicodedata.normalize("NFKD", s.lower()) if not unicodedata.combining(c)
    )


MONTH_WORDS = {
    m: i
    for i, names in enumerate(
        [
            ("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"),
            ("may",), ("jun", "june"), ("jul", "july"), ("aug", "august"),
            ("sep", "sept", "september"), ("oct", "october"), ("nov", "november"),
            ("dec", "december"),
        ],
        start=1,
    )
    for m in names
}  # fmt: skip
PICK_STOP = {
    "trip", "trips", "the", "next", "flight", "flights", "hotel", "hotels", "stay", "staying",
    "upcoming", "and", "for", "my", "our", "when", "where", "what", "which", "whats", "wheres",
    "itinerary", "plan", "this", "that", "coming", "soon", "from", "with", "back", "home", "there",
    "booking", "bookings",
}  # fmt: skip


RELATIVE = re.compile(r"\b(this|next|coming)\s+(week|weekend|month)\b")


def pick(
    found: list[Trip], want: str, *, strict: bool = False, now: datetime | None = None
) -> Trip | None:
    """The trip they named (a city, a hotel, a booking reference, a month, or "next month" /
    "this week"), else the next one. ``strict``: None when they named something no trip
    matches."""
    want = _plain(want.strip())
    if not found:
        return None
    if want:
        # "next month", "this weekend": a span of days from today, not a word to look for
        span: tuple[datetime, datetime] | None = None
        if m := RELATIVE.search(want):
            today = (now or clock()).replace(hour=0, minute=0, second=0, microsecond=0)
            ahead = m.group(1) != "this"
            if m.group(2) == "month":
                first = today.replace(day=1)
                if ahead:
                    first = (first + timedelta(days=32)).replace(day=1)
                span = (first, (first + timedelta(days=32)).replace(day=1))
            else:
                monday = today - timedelta(days=today.weekday()) + timedelta(weeks=int(ahead))
                start = monday + timedelta(days=5) if m.group(2) == "weekend" else monday
                span = (start, monday + timedelta(days=7))
            want = RELATIVE.sub(" ", want)
        words = [w for w in re.findall(r"[a-z0-9]{3,}", want) if w not in PICK_STOP]
        months = {MONTH_WORDS[w] for w in words if w in MONTH_WORDS}
        words = [w for w in words if w not in MONTH_WORDS]
        for t in found:
            text = _plain(f"{t.text()} {t.destination}")
            if words and not any(w in text for w in words):
                continue
            if months and not any(
                (t.start + timedelta(days=d)).month in months
                for d in range((t.end - t.start).days + 1)
            ):
                continue
            if span and not (t.start < span[1] and t.end >= span[0]):
                continue
            if words or months or span:
                return t
        if strict and span:
            return None
        if strict and (words or months):
            return None
    return found[0]


def days(trip: Trip) -> list[dict[str, Any]]:
    """The day-by-day itinerary: [{"heading": "Thu 12 Nov", "lines": [...]}]."""
    out: list[dict[str, Any]] = []
    day = trip.start.date()
    last = trip.end.date()
    while day <= last:
        lines: list[tuple[str, str]] = []
        for f in trip.flights:
            if f.depart.date() == day:
                lines.append((f"{f.depart:%H:%M}", f"Fly {f.line()}"))
            elif f.arrive and f.arrive.date() == day and f.depart.date() < day:
                lines.append((f"{f.arrive:%H:%M}", f"Land in {f.place('to')} ({f.flight})"))
        staying = None
        for s in trip.stays:
            where = f", {s.address}" if s.address else ""
            if s.check_in.date() == day:
                t = f"from {s.check_in:%H:%M}" if s.times.get("in") else "from the afternoon"
                ref = f". Booking {s.ref}" if s.ref else ""
                lines.append(
                    (
                        f"{s.check_in:%H:%M}",
                        f"Check in at {s.name}{where} ({t}, {s.nights} night{'s' if s.nights != 1 else ''}){ref}",
                    )
                )
            elif s.check_out.date() == day:
                t = f"by {s.check_out:%H:%M}" if s.times.get("out") else "in the morning"
                lines.append((f"{s.check_out:%H:%M}", f"Check out of {s.name} ({t})"))
            elif s.check_in.date() < day < s.check_out.date():
                staying = s
        if not lines and staying:
            lines.append(("12:00", f"Free day. Staying at {staying.name}"))
        if lines:
            dt = datetime.combine(day, datetime.min.time())
            out.append(
                {"heading": f"{dt:%a} {dt.day} {dt:%b}", "lines": [t for _, t in sorted(lines)]}
            )
        day += timedelta(days=1)
    return out


def reminder_keys(f: Flight) -> list[str]:
    """The keys of a flight's two reminders. The departure time is part of them, so a flight
    that moved gets new ones (and its old ones are listed as stale: see ``run``)."""
    stem = f"atlas:{f.flight}:{f.depart:%Y-%m-%dT%H:%M}"
    return [f"{stem}:eve", f"{stem}:go"]


def reminders(trip: Trip, now: datetime) -> list[dict[str, str]]:
    """Two reminders per flight still ahead: the evening before (when that's still ahead and
    not the same day) and three hours before take-off."""
    out = []
    for f in trip.flights:
        to = re.sub(AIRPORT_WORDS, "", f.dest_city).strip() or f.dest_city or f.dest
        eve = (f.depart - timedelta(days=1)).replace(hour=19, minute=0)
        if eve > now and f.depart - eve >= timedelta(hours=8):
            check = " Online check-in should be open." if f.ref else ""
            out.append(
                {
                    "key": reminder_keys(f)[0],
                    "title": f"Tomorrow: {f.flight} to {to}",
                    "on": eve.date().isoformat(),
                    "time": eve.strftime("%H:%M"),
                    "message": f"Tomorrow at {f.depart:%H:%M}: {f.flight} to {to} from {f.place('from')}.{check} Pack your ID.",
                }
            )
        go = f.depart - timedelta(hours=3)
        if go > now:
            out.append(
                {
                    "key": reminder_keys(f)[1],
                    "title": f"Leave for {f.origin}: {f.flight}",
                    "on": go.date().isoformat(),
                    "time": go.strftime("%H:%M"),
                    "message": f"{f.flight} to {to} leaves {f.place('from')} at {f.depart:%H:%M}. Time to head to the airport.",
                }
            )
    return out


def report(trip: Trip, others: list[Trip], n_reminders: int) -> dict[str, Any]:
    """The notification and the tray card for one trip."""
    plan = days(trip)
    nf, ns = len(trip.flights), len(trip.stays)
    bits = [
        x
        for x in (
            f"{nf} flight{'s' if nf != 1 else ''}" if nf else "",
            f"{ns} stay{'s' if ns != 1 else ''}" if ns else "",
        )
        if x
    ]
    body = " and ".join(bits) + "."
    if n_reminders:
        body += f" Reminders are set before {'each' if nf > 1 else 'the'} flight."
    if others:
        body += (
            " Also coming up: "
            + ", ".join(
                f"{t.destination} ({t.start:%a} {t.start.day} {t.start:%b})" for t in others[:2]
            )
            + "."
        )
    highlights = [
        f"{d['heading']}: {line}"
        for d in plan
        for line in d["lines"]
        if not line.startswith("Free day")
    ]
    summary = (
        trip.title()
        + ". "
        + " ".join(f"{d['heading']}: " + "; ".join(d["lines"]) + "." for d in plan)
    )
    return {
        "title": trip.title()[:70],
        "body": body,
        "changes": highlights[:8],
        "summary": summary,
        "card": {"title": trip.title(), "sections": plan},
    }


def flight_dict(f: Flight) -> dict[str, Any]:
    d = asdict(f)
    d["depart"] = f.depart.strftime("%Y-%m-%dT%H:%M")
    d["arrive"] = f.arrive.strftime("%Y-%m-%dT%H:%M") if f.arrive else ""
    return d


# ------------------------------------------------------------------------------- the job
clock = datetime.now  # tests move time
FETCH = 120  # travel mails read: half a year of bookings, and the mail that only sounds like one


async def run(manager: JobManager, job: Job, agent: AgentProfile) -> None:
    """Read the travel mail, build the itinerary, and leave reminders for the brain to save."""
    google = manager._google()
    if not google.connected():
        raise RuntimeError("Gmail isn't connected. Connect Google in Settings and ask again.")
    await manager._status(job, "Reading your travel confirmations")
    mails = await google.gmail_fetch(QUERY, FETCH)
    # Newest first: when there was more than this, a booking made long ago may be what's missing,
    # and the result says how far back it read (it used to stop at 40 without a word).
    now = clock()
    since = read_back_to(mails)
    older = (
        f" I could only read your travel mail back to {said_day(since, now)}, so an older "
        "booking may be missing."
        if since
        else ""
    )
    replaced: list[Flight] = []
    flights, stays, unread = read_mail(mails, replaced)
    if unread and job.params.get("model", True):
        await manager._status(job, f"Reading {min(len(unread), 4)} more confirmation(s)")
        llm = manager.llm_factory(agent)
        from otto.llm.base import Message

        sample = "\n\n---\n\n".join(
            f"From: {m.get('from')}\nSubject: {m.get('subject')}\nSent: {_mail_date(m):%Y-%m-%d}\n{str(m.get('text') or '')[:1500]}"
            for m in unread[:4]
        )
        try:
            resp = await llm.complete(
                system=MODEL_SYSTEM + safety.READ_ONLY_RULE,
                messages=[Message.user(safety.fence("the emails", sample))],
                max_tokens=900,
            )
            job.cost_usd += resp.usage.cost_usd
            more_f, more_s = from_model(resp.text, unread)
        except Exception:  # the code's reading is enough to go on
            more_f, more_s = [], []
        have = {(f.flight, f.depart.date()) for f in flights}
        flights += [f for f in more_f if (f.flight, f.depart.date()) not in have]
        stays += [s for s in more_s if all(s.name != o.name for o in stays)]
    found = trips(
        sorted(flights, key=lambda f: f.depart), sorted(stays, key=lambda s: s.check_in), now
    )
    # Reminders set on an earlier run for a flight that has since moved or been cancelled.
    current = {key for f in flights for key in reminder_keys(f)}
    stale = sorted({key for f in replaced for key in reminder_keys(f)} - current)
    want = str(job.params.get("trip") or "").strip()
    trip = pick(found, want, strict=True, now=now)
    missed = ""
    if trip is None and found:  # "Tokyo", and the only trips are Paris and Lisbon: say so
        trip = found[0]
        missed = f"I didn't find a trip matching {want!r} in your mail; this is your next one. "
    if trip is None:
        job.status = "failed"
        job.summary = f"I didn't find any upcoming flights or hotel bookings in your mail.{older}"
        job.data = {
            "title": "No upcoming trips in your mail",
            "body": job.summary,
            "stale_reminders": stale,
        }
        return
    rem = reminders(trip, now)
    rep = report(trip, [t for t in found if t is not trip], len(rem))
    job.summary = missed + rep["summary"] + older
    job.data = {
        "title": rep["title"],
        "body": missed + rep["body"] + older,
        "changes": rep["changes"],
        "card": rep["card"],
        "reminders": rem,
        "stale_reminders": stale,
        "flights": [flight_dict(f) for f in trip.flights],
    }
    job.status = "done"
