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

from pyxi.mailwatch import DATE, merchant, parse_date

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
    r"record locator|reservation (?:code|number|no\.?|#)|itinerary (?:number|no\.?|#)|\bPNR\b|booking id|confirmation)"
    r"\s*(?i:is)?\s*[:#]?\s*#?\s*(?P<ref>[A-Z0-9]{5,14})\b"
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
        return self.arrive or self.depart

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

    @property
    def destination(self) -> str:
        city = self.stay_city()
        if self.flights:
            f = self.flights[0]
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


def _date(text: str, ref: datetime) -> datetime | None:
    m = DATE_RE.search(text)
    if not m:
        return None
    when = parse_date(m.group(0), ref)
    return when.replace(tzinfo=None) if when else None


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
    }:
        words = words[1:]
    city = " ".join(words)
    city = re.sub(r"\s+(International|Intl\.?|Airport|Terminal.*)$", "", city).strip()
    return city


def _refs(text: str) -> list[str]:
    return [
        m.group("ref")
        for m in REF.finditer(text)
        if not m.group("ref").isdigit() or len(m.group("ref")) >= 6
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


def read_flights(mail: dict[str, Any]) -> list[Flight]:
    text = f"{mail.get('subject', '')}\n{mail.get('text', '')}"
    sent = _mail_date(mail)
    refs = _refs(text)
    airline = merchant(str(mail.get("from") or ""))
    hits = [
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
        order: list[tuple[int, int]] = []
        ends: list[int] = []  # where each time ends in the window
        for t in TIME.finditer(window):
            hm = _time(t)
            if hm is None:
                continue
            before = window[max(0, t.start() - 28) : t.start()].lower()
            if (
                re.search(r"depart|leav|takes? off|departure|boarding", before)
                and "depart" not in times
            ):
                times["depart"] = hm
            elif re.search(r"arriv|land", before) and "arrive" not in times:
                times["arrive"] = hm
            order.append(hm)
            ends.append(t.end())
        dep = times.get("depart") or (order[0] if order else None)
        arr = times.get("arrive") or next((t for t in order if t != dep), None)
        if dep is None:
            continue
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
        day = _date(own.group(0), sent) if own else None
        if day is None:
            day = _date(dates[-1].group(0), sent) if dates else None
        if day is None:
            continue
        depart = day.replace(hour=dep[0], minute=dep[1], second=0, microsecond=0)
        arrive = None
        if arr:
            arrive = day.replace(hour=arr[0], minute=arr[1], second=0, microsecond=0)
            if arrive <= depart or re.search(r"\+1\b|next day", window[:200], re.I):
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
    text: str, word: str, sent: datetime
) -> tuple[datetime | None, tuple[int, int] | None]:
    m = re.search(
        rf"{word}(?:\s*date)?\b[^\n\d]{{0,24}}?(?P<d>{DATE})(?P<rest>[^\n]{{0,60}})", text, re.I
    )
    if not m:
        return None, None
    day = _date(m.group("d"), sent)
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
    cin, tin = _stay_date(text, r"check[- ]?in", sent)
    cout, tout = _stay_date(text, r"check[- ]?out", sent)
    if cin is None or cout is None:
        arr = re.search(rf"\b(?:arrival|arriving)\b[^\n\d]{{0,12}}(?P<d>{DATE})", text, re.I)
        dep = re.search(
            rf"\b(?:departure|departing|leaving)\b[^\n\d]{{0,12}}(?P<d>{DATE})", text, re.I
        )
        if arr and dep and re.search(r"hotel|stay|room|nights?", text, re.I):
            cin, cout = _date(arr.group("d"), sent), _date(dep.group("d"), sent)
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
    )


def read_mail(mails: list[dict[str, Any]]) -> tuple[list[Flight], list[Stay], list[dict[str, Any]]]:
    """Every flight and stay in these emails, newest information winning, cancelled ones gone.
    Also the travel-looking emails the code couldn't read (for the model to try)."""
    flights: dict[tuple[str, str], Flight] = {}
    stays: dict[str, Stay] = {}
    cancelled: set[str] = set()
    unread: list[dict[str, Any]] = []
    for mail in sorted(mails, key=_mail_date):  # oldest first, so a schedule change wins
        text = f"{mail.get('subject', '')}\n{mail.get('text', '')}"
        if CANCELLED.search(text):
            cancelled.update(_refs(text))
            continue
        fs = read_flights(mail)
        ss = read_stays(mail)  # a package deal has both
        for f in fs:
            flights[(f.flight, f.depart.date().isoformat())] = f
            if f.ref:  # a changed flight: the older times under the same booking go
                for k, old in list(flights.items()):
                    if (
                        old.ref == f.ref
                        and old.flight == f.flight
                        and k != (f.flight, f.depart.date().isoformat())
                        and old.email_id != f.email_id
                    ):
                        del flights[k]
        for s in ss:
            stays[s.ref or f"{s.name.lower()}:{s.check_in.date()}"] = s
        if not fs and not ss and looks_like_travel(mail):
            unread.append(mail)
    keep_f = [f for f in flights.values() if not (f.ref and f.ref in cancelled)]
    keep_s = [s for s in stays.values() if not (s.ref and s.ref in cancelled)]
    return sorted(keep_f, key=lambda f: f.depart), sorted(keep_s, key=lambda s: s.check_in), unread


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
def trips(flights: list[Flight], stays: list[Stay], now: datetime, gap_days: int = 2) -> list[Trip]:
    """Bookings grouped into trips (a gap of more than ``gap_days`` starts a new one); only
    trips that haven't ended yet, soonest first."""
    items = sorted([*flights, *stays], key=lambda x: x.start)
    groups: list[Trip] = []
    end: datetime | None = None
    for x in items:
        if end is None or x.start.date() > (end + timedelta(days=gap_days)).date():
            groups.append(Trip([], []))
            end = x.end
        (groups[-1].flights if isinstance(x, Flight) else groups[-1].stays).append(x)
        end = max(end, x.end)
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


def pick(found: list[Trip], want: str, *, strict: bool = False) -> Trip | None:
    """The trip they named (a city, a hotel, a booking reference or a month), else the next
    one. ``strict``: None when they named something no trip matches."""
    want = _plain(want.strip())
    if not found:
        return None
    if want:
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
            if words or months:
                return t
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
                    "key": f"atlas:{f.flight}:{f.depart:%Y-%m-%dT%H:%M}:eve",
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
                    "key": f"atlas:{f.flight}:{f.depart:%Y-%m-%dT%H:%M}:go",
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
        body += " Reminders are set before each flight."
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


async def run(manager: JobManager, job: Job, agent: AgentProfile) -> None:
    """Read the travel mail, build the itinerary, and leave reminders for the brain to save."""
    google = manager._google()
    if not google.connected():
        raise RuntimeError("Gmail isn't connected. Connect Google in Settings and ask again.")
    await manager._status(job, "Reading your travel confirmations")
    mails = await google.gmail_fetch(QUERY, 40)
    flights, stays, unread = read_mail(mails)
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
                system=MODEL_SYSTEM, messages=[Message.user(sample)], max_tokens=900
            )
            job.cost_usd += resp.usage.cost_usd
            more_f, more_s = from_model(resp.text, unread)
        except Exception:  # the code's reading is enough to go on
            more_f, more_s = [], []
        have = {(f.flight, f.depart.date()) for f in flights}
        flights += [f for f in more_f if (f.flight, f.depart.date()) not in have]
        stays += [s for s in more_s if all(s.name != o.name for o in stays)]
    now = clock()
    found = trips(
        sorted(flights, key=lambda f: f.depart), sorted(stays, key=lambda s: s.check_in), now
    )
    want = str(job.params.get("trip") or "").strip()
    trip = pick(found, want, strict=True)
    missed = ""
    if trip is None and found:  # "Tokyo", and the only trips are Paris and Lisbon: say so
        trip = found[0]
        missed = f"I didn't find a trip matching {want!r} in your mail; this is your next one. "
    if trip is None:
        job.status = "failed"
        job.summary = "I didn't find any upcoming flights or hotel bookings in your mail."
        job.data = {"title": "No upcoming trips in your mail", "body": job.summary}
        return
    rem = reminders(trip, now)
    rep = report(trip, [t for t in found if t is not trip], len(rem))
    job.summary = missed + rep["summary"]
    job.data = {
        "title": rep["title"],
        "body": missed + rep["body"],
        "changes": rep["changes"],
        "card": rep["card"],
        "reminders": rem,
        "flights": [flight_dict(f) for f in trip.flights],
    }
    job.status = "done"
