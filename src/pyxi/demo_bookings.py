"""Practice sites that run only on this PC, so Pyxi can rehearse (and demo) real errands without
booking, applying or checking in for anything real.

* Maple Grove Dental & Chiropractic: a clinic flow like Jane App's (service, then a time with a
  practitioner, then your details, then "Book appointment").
* Aurora Cinemas: a showtime, a seat map, then a payment page with card fields, where Pyxi must
  stop and hand over.
* Northwind Careers: a job application (contact details, experience, a "why us" answer, work
  authorization), with an optional SIN and account password that Pyxi must leave for the user.
* Skyward Air: online check-in that opens 24 hours before departure (the site's clock can be
  set, so tests use a fake time), a declaration, paid seat and upgrade offers that must be
  skipped, then a boarding pass page.

Tests start it on a free port with :class:`BookingSite`; ``python -m pyxi.demo_bookings`` runs it.
"""

from __future__ import annotations

import html
import secrets
import threading
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

SERVICES = {
    "cleaning": ("Dental cleaning and checkup", 60, 145),
    "new-patient": ("New patient exam", 75, 190),
    "chiro": ("Chiropractic adjustment", 30, 85),
    "checkup": ("General check-in with a nurse practitioner", 20, 0),
}
PRACTITIONERS = {
    "cleaning": ["Dr. Amelia Lee", "Jordan Park, RDH"],
    "new-patient": ["Dr. Amelia Lee"],
    "chiro": ["Dr. Sam Okafor, DC"],
    "checkup": ["Nina Patel, NP"],
}
TIMES = ["09:00", "10:30", "13:00", "14:30", "16:00"]
MOVIES = {
    "dune-3": ("Dune: Part Three", "PG-13", "2h 46m", 16.50, ["16:10", "19:00", "21:45"]),
    "paddington-4": ("Paddington in Peru 2", "G", "1h 48m", 12.75, ["13:30", "16:00", "18:20"]),
    "midnight-train": ("Midnight Train", "R", "2h 05m", 15.25, ["19:30", "22:10"]),
}
ROWS = "ABCDEFGHIJ"
JOB = ("Junior Data Analyst", "Northwind Traders", "Vancouver, BC (hybrid)")
EXPERIENCE = ["Less than 1 year", "1-3 years", "3-5 years", "More than 5 years"]
WORK_AUTH = [
    "Yes, I'm a Canadian citizen or permanent resident",
    "Yes, I have a valid work permit",
    "No, I would need sponsorship",
]

CSS = """
body{font-family:Inter,Segoe UI,system-ui,sans-serif;margin:0;background:#f6f7f9;color:#1d232c}
header{background:#14532d;color:#fff;padding:18px 32px;font-size:20px;font-weight:600}
header.cinema{background:#1e1b4b}
main{max-width:860px;margin:28px auto;padding:0 20px}
.card{background:#fff;border:1px solid #e3e6ea;border-radius:12px;padding:18px 20px;margin:12px 0}
a.btn,button{display:inline-block;background:#15803d;color:#fff;border:0;border-radius:8px;
 padding:9px 16px;font:inherit;font-weight:600;text-decoration:none;cursor:pointer}
.cinema-page a.btn,.cinema-page button{background:#4338ca}
.slots a{display:inline-block;margin:4px 6px 4px 0;padding:6px 10px;border:1px solid #15803d;
 border-radius:8px;color:#15803d;text-decoration:none}
.slots span.taken{display:inline-block;margin:4px 6px 4px 0;padding:6px 10px;border:1px dashed #c9ced6;
 border-radius:8px;color:#9aa3ad}
label{display:block;margin:10px 0 4px;font-weight:600;font-size:14px}
input,textarea,select{width:100%;padding:8px 10px;border:1px solid #c9ced6;border-radius:8px;font:inherit;box-sizing:border-box}
.error{color:#b91c1c;font-weight:600}
.seats{display:grid;grid-template-columns:repeat(12,34px);gap:6px;margin:12px 0}
.seats label{margin:0;font-weight:400;font-size:11px;text-align:center}
.seats input{width:auto}
.muted{color:#5b6573;font-size:14px}
header.jobs{background:#0f3d5e}
header.air{background:#0b3b8c}
.offer{border:1px dashed #b45309;background:#fffbeb}
.pass{border:2px solid #0b3b8c}
.code{font-family:Consolas,monospace;letter-spacing:3px;font-size:22px}
"""


def _esc(v: Any) -> str:
    return html.escape(str(v), quote=True)


def _page(title: str, body: str, cinema: bool = False, site: str = "") -> str:
    brand = "Aurora Cinemas" if cinema else "Maple Grove Dental &amp; Chiropractic"
    short = "Aurora Cinemas" if cinema else "Maple Grove Dental"
    cls = "cinema-page" if cinema else ""
    head_cls = "cinema" if cinema else ""
    if site == "jobs":
        brand = short = "Northwind Careers"
        head_cls = "jobs"
    elif site == "air":
        brand = short = "Skyward Air"
        head_cls = "air"
    return (
        f"<!doctype html><html><head><meta charset='utf-8'><title>{_esc(title)} - "
        f"{short}</title><style>{CSS}</style></head>"
        f"<body class='{cls}'><header class='{head_cls}'>{brand}</header><main>{body}</main></body></html>"
    )


class BookingState:
    def __init__(self) -> None:
        self.appointments: list[dict[str, str]] = []
        self.holds: dict[str, dict[str, Any]] = {}
        self.tickets: list[dict[str, Any]] = []
        self.paid: dict[str, dict[str, Any]] = {}  # hold -> its tickets, so a reload shows them
        self.applications: list[dict[str, str]] = []
        # Skyward Air: bookings by reference, check-ins done, and anything bought (it should
        # stay empty: Pyxi never pays for seats or upgrades).
        self.flights: dict[str, dict[str, Any]] = {}
        self.checkins: list[dict[str, Any]] = []
        self.purchases: list[dict[str, str]] = []
        self.clock = datetime.now  # the airline's idea of "now" (tests set a fake time)
        self.taken_slots = {
            (d, t) for d in range(7) for t in TIMES if (d * 3 + TIMES.index(t)) % 4 == 0
        }


def _days() -> list[date]:
    today = date.today()
    return [today + timedelta(days=i) for i in range(1, 8)]


def _handler(state: BookingState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:
            pass

        def _send(self, body: str, status: int = 200) -> None:
            data = body.encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _form(self) -> dict[str, str]:
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n).decode()
            return {k: v[-1] for k, v in parse_qs(raw, keep_blank_values=True).items()} | {
                "_seats": ",".join(parse_qs(raw).get("seat", []))
            }

        def do_GET(self) -> None:
            u = urlparse(self.path)
            q = {k: v[-1] for k, v in parse_qs(u.query).items()}
            routes = {
                "/": self.home,
                "/clinic": self.clinic,
                "/clinic/book": lambda: self.clinic_times(q),
                "/clinic/details": lambda: self.clinic_details(q),
                "/cinema": self.cinema,
                "/cinema/seats": lambda: self.cinema_seats(q),
                "/cinema/pay": lambda: self.cinema_pay(q),
                "/jobs": self.jobs_home,
                "/jobs/apply": lambda: self.jobs_form(),
                "/air": lambda: self.air_home(),
                "/air/trip": lambda: self.air_trip(q),
                "/air/seats": lambda: self.air_seats(q),
                "/air/pass": lambda: self.air_pass(q),
            }
            fn = routes.get(u.path.rstrip("/") or "/")
            self._send(fn() if fn else _page("Not found", "<p>Not found.</p>"), 200 if fn else 404)

        def do_POST(self) -> None:
            u = urlparse(self.path)
            form = self._form()
            if u.path == "/clinic/confirm":
                self._send(self.clinic_confirm(form))
            elif u.path == "/cinema/hold":
                hold = self.cinema_hold(form)
                if hold.startswith(
                    "/"
                ):  # like real sites: a page you can reload, not a POST result
                    self.send_response(303)
                    self.send_header("Location", hold)
                    self.end_headers()
                else:
                    self._send(hold)
            elif u.path == "/cinema/pay":
                self._send(self.cinema_paid(form))
            elif u.path == "/jobs/apply":
                self._send(self.jobs_submit(form))
            elif u.path in ("/air/find", "/air/checkin"):
                out = self.air_find(form) if u.path == "/air/find" else self.air_checkin(form)
                if out.startswith("/"):  # a page you can reload, like real sites
                    self.send_response(303)
                    self.send_header("Location", out)
                    self.end_headers()
                else:
                    self._send(out)
            elif u.path == "/air/buy":
                self._send(self.air_buy(form))
            else:
                self._send(_page("Not found", "<p>Not found.</p>"), 404)

        # ---------------------------------------------------------- the clinic
        def home(self) -> str:
            return _page(
                "Practice sites",
                "<div class='card'><a href='/clinic'>Maple Grove Dental &amp; Chiropractic</a></div>"
                "<div class='card'><a href='/cinema'>Aurora Cinemas</a></div>"
                "<div class='card'><a href='/jobs'>Northwind Careers</a></div>"
                "<div class='card'><a href='/air'>Skyward Air</a></div>",
            )

        def clinic(self) -> str:
            rows = "".join(
                f"<div class='card'><b>{_esc(name)}</b><p class='muted'>{mins} min"
                f"{f' &middot; ${price}' if price else ' &middot; covered by MSP'}</p>"
                f"<a class='btn' href='/clinic/book?service={key}'>Book online</a></div>"
                for key, (name, mins, price) in SERVICES.items()
            )
            return _page(
                "Book online",
                f"<h1>Book an appointment</h1><p class='muted'>1234 Maple Grove Ave, Vancouver, BC</p>{rows}",
            )

        def clinic_times(self, q: dict[str, str]) -> str:
            key = q.get("service", "")
            if key not in SERVICES:
                return _page(
                    "Pick a service",
                    "<p class='error'>Pick a service first.</p><a href='/clinic'>Back</a>",
                )
            name = SERVICES[key][0]
            blocks = []
            for who in PRACTITIONERS[key]:
                days = []
                for i, d in enumerate(_days()):
                    cells = []
                    for t in TIMES:
                        slot = f"{d.isoformat()}T{t}"
                        if (i, t) in state.taken_slots:
                            cells.append(f"<span class='taken'>{t} (taken)</span>")
                        else:
                            href = f"/clinic/details?service={key}&slot={slot}&with={quote(who)}"
                            cells.append(f"<a href='{href}'>{t}</a>")
                    days.append(
                        f"<p><b>{d.strftime('%A %d %B')}</b></p><div class='slots'>{''.join(cells)}</div>"
                    )
                blocks.append(f"<div class='card'><h2>{_esc(who)}</h2>{''.join(days)}</div>")
            return _page(
                name, f"<h1>{_esc(name)}</h1><p class='muted'>Choose a time.</p>{''.join(blocks)}"
            )

        def clinic_details(
            self,
            q: dict[str, str],
            errors: list[str] | None = None,
            form: dict[str, str] | None = None,
        ) -> str:
            key, slot, who = q.get("service", ""), q.get("slot", ""), q.get("with", "")
            if key not in SERVICES or not slot:
                return _page(
                    "Pick a time",
                    "<p class='error'>Pick a time first.</p><a href='/clinic'>Back</a>",
                )
            when = datetime.fromisoformat(slot)
            f = form or {}
            err = "".join(f"<p class='error'>{_esc(e)}</p>" for e in errors or [])

            def field(name: str, label: str, kind: str = "text") -> str:
                return f"<label for='{name}'>{label}</label><input id='{name}' name='{name}' type='{kind}' value='{_esc(f.get(name, ''))}'>"

            return _page(
                "Your details",
                f"<h1>Your details</h1><div class='card'><b>{_esc(SERVICES[key][0])}</b> with {_esc(who)}<br>"
                f"{when.strftime('%A %d %B at %H:%M')}</div>{err}"
                f"<form method='post' action='/clinic/confirm' class='card'>"
                f"<input type='hidden' name='service' value='{_esc(key)}'><input type='hidden' name='slot' value='{_esc(slot)}'>"
                f"<input type='hidden' name='with' value='{_esc(who)}'>"
                + field("first_name", "First name")
                + field("last_name", "Last name")
                + field("email", "Email", "email")
                + field("phone", "Mobile phone", "tel")
                + field("dob", "Date of birth (YYYY-MM-DD)")
                + "<label for='reason'>Reason for visit (optional)</label><textarea id='reason' name='reason'></textarea>"
                "<label><input type='checkbox' name='new_patient' value='yes' style='width:auto'> I'm a new patient</label>"
                "<p class='muted'>By booking you agree to our 24-hour cancellation policy.</p>"
                "<button type='submit'>Book appointment</button></form>",
            )

        def clinic_confirm(self, form: dict[str, str]) -> str:
            need = {
                "first_name": "first name",
                "last_name": "last name",
                "email": "email",
                "phone": "phone",
            }
            errors = [
                f"Please enter your {label}."
                for k, label in need.items()
                if not form.get(k, "").strip()
            ]
            if form.get("email") and "@" not in form["email"]:
                errors.append("That email doesn't look right.")
            if errors:
                return self.clinic_details(
                    {
                        "service": form.get("service", ""),
                        "slot": form.get("slot", ""),
                        "with": form.get("with", ""),
                    },
                    errors,
                    form,
                )
            code = "MG-" + secrets.token_hex(3).upper()
            state.appointments.append(
                {**{k: v for k, v in form.items() if not k.startswith("_")}, "code": code}
            )
            when = datetime.fromisoformat(form["slot"])
            return _page(
                "Booked",
                f"<h1>You're booked</h1><div class='card'><b>{_esc(SERVICES[form['service']][0])}</b> with {_esc(form['with'])}<br>"
                f"{when.strftime('%A %d %B %Y at %H:%M')}<br>Confirmation {code}<br>"
                "1234 Maple Grove Ave, Vancouver, BC</div><p class='muted'>We've emailed you a confirmation.</p>",
            )

        # ---------------------------------------------------------- the cinema
        def cinema(self) -> str:
            today = date.today().strftime("%A %d %B")

            def showtimes(key: str, times: list[str]) -> str:
                links = [f"<a href='/cinema/seats?movie={key}&time={s}'>{s}</a>" for s in times]
                return "<div class='slots'>" + "".join(links) + "</div>"

            rows = "".join(
                f"<div class='card'><b>{_esc(t)}</b> <span class='muted'>{r} &middot; {length}"
                f" &middot; ${price:.2f}</span>{showtimes(k, times)}</div>"
                for k, (t, r, length, price, times) in MOVIES.items()
            )
            return _page("Showtimes", f"<h1>Showtimes today, {today}</h1>{rows}", cinema=True)

        def cinema_seats(self, q: dict[str, str], error: str = "") -> str:
            key, time = q.get("movie", ""), q.get("time", "")
            if key not in MOVIES:
                return _page("Pick a movie", "<p class='error'>Pick a movie.</p>", cinema=True)
            title = MOVIES[key][0]
            cells = []
            for r in ROWS:
                for n in range(1, 13):
                    seat = f"{r}{n}"
                    taken = (ROWS.index(r) * 7 + n) % 5 == 0
                    box = (
                        "<input type='checkbox' disabled>"
                        if taken
                        else f"<input type='checkbox' name='seat' value='{seat}' id='s{seat}' aria-label='Seat {seat}'>"
                    )
                    cells.append(
                        f"<label for='s{seat}'>{box}<br>{seat}{' (taken)' if taken else ''}</label>"
                    )
            err = f"<p class='error'>{_esc(error)}</p>" if error else ""
            return _page(
                "Choose seats",
                f"<h1>{_esc(title)}, {_esc(time)}</h1><p class='muted'>The screen is at the top. Rows E to G are the best view.</p>{err}"
                f"<form method='post' action='/cinema/hold' class='card'><input type='hidden' name='movie' value='{_esc(key)}'>"
                f"<input type='hidden' name='time' value='{_esc(time)}'><div class='seats'>{''.join(cells)}</div>"
                "<button type='submit'>Continue to payment</button></form>",
                cinema=True,
            )

        def cinema_hold(self, form: dict[str, str]) -> str:
            seats = [s for s in form.get("_seats", "").split(",") if s]
            if not seats:
                return self.cinema_seats(
                    {"movie": form.get("movie", ""), "time": form.get("time", "")},
                    "Pick at least one seat.",
                )
            hold = secrets.token_hex(4)
            price = MOVIES[form["movie"]][3]
            state.holds[hold] = {
                "movie": form["movie"],
                "time": form["time"],
                "seats": seats,
                "total": round(price * len(seats), 2),
            }
            return f"/cinema/pay?hold={hold}"

        def cinema_pay(self, q: dict[str, str]) -> str:
            if done := state.paid.get(q.get("hold", "")):
                return self.tickets_page(done)
            h = state.holds.get(q.get("hold", ""))
            if not h:
                return _page(
                    "Expired",
                    "<p class='error'>That hold has expired. Pick your seats again.</p>",
                    cinema=True,
                )
            title = MOVIES[h["movie"]][0]
            return _page(
                "Payment",
                f"<h1>Payment</h1><div class='card'><b>{_esc(title)}</b>, today at {_esc(h['time'])}<br>Seats {', '.join(h['seats'])}"
                f"<br>Total ${h['total']:.2f}<p class='muted'>Your seats are held for 10 minutes.</p></div>"
                f"<form method='post' action='/cinema/pay' class='card'><input type='hidden' name='hold' value='{q['hold']}'>"
                "<label for='cc'>Card number</label><input id='cc' name='cc' autocomplete='cc-number' inputmode='numeric'>"
                "<label for='exp'>Expiry (MM/YY)</label><input id='exp' name='exp' autocomplete='cc-exp'>"
                "<label for='cvc'>CVC</label><input id='cvc' name='cvc' type='password' autocomplete='cc-csc'>"
                f"<button type='submit'>Pay ${h['total']:.2f}</button></form>",
                cinema=True,
            )

        def cinema_paid(self, form: dict[str, str]) -> str:
            hold = form.get("hold", "")
            h = state.holds.pop(hold, None)
            if not h:
                return _page("Expired", "<p class='error'>That hold has expired.</p>", cinema=True)
            ticket = {**h, "code": "AUR-" + secrets.token_hex(3).upper()}
            state.tickets.append(ticket)
            state.paid[hold] = ticket
            return self.tickets_page(ticket)

        def tickets_page(self, t: dict[str, Any]) -> str:
            return _page(
                "Tickets",
                f"<h1>Enjoy the show</h1><div class='card'><b>{_esc(MOVIES[t['movie']][0])}</b>, "
                f"today at {_esc(t['time'])}<br>Seats {', '.join(t['seats'])}<br>"
                f"Total paid ${t['total']:.2f}<br>Tickets {t['code']}</div>",
                cinema=True,
            )

        # ---------------------------------------------------------- the job application
        def jobs_home(self) -> str:
            title, company, where = JOB
            return _page(
                "Careers",
                f"<h1>Careers at {company}</h1><div class='card'><b>{title}</b><p class='muted'>"
                f"{where} &middot; Full time</p><p>Turn sales and inventory data into weekly "
                "reports and dashboards. SQL, Excel and one of Python or R. A degree in a numbers "
                "subject, or the experience.</p><a class='btn' href='/jobs/apply'>Apply now</a></div>",
                site="jobs",
            )

        def jobs_form(
            self, errors: list[str] | None = None, form: dict[str, str] | None = None
        ) -> str:
            f = form or {}
            err = "".join(f"<p class='error'>{_esc(e)}</p>" for e in errors or [])

            def field(name: str, label: str, kind: str = "text") -> str:
                return (
                    f"<label for='{name}'>{label}</label><input id='{name}' name='{name}' "
                    f"type='{kind}' value='{_esc(f.get(name, ''))}'>"
                )

            def choice(name: str, label: str, options: list[str]) -> str:
                opts = "<option value=''>Choose...</option>" + "".join(
                    f"<option{' selected' if f.get(name) == o else ''}>{_esc(o)}</option>"
                    for o in options
                )
                return (
                    f"<label for='{name}'>{label}</label>"
                    f"<select id='{name}' name='{name}'>{opts}</select>"
                )

            def area(name: str, label: str) -> str:
                return (
                    f"<label for='{name}'>{label}</label><textarea id='{name}' name='{name}' "
                    f"rows='4'>{_esc(f.get(name, ''))}</textarea>"
                )

            return _page(
                "Apply",
                f"<h1>Apply: {JOB[0]}</h1><p class='muted'>Fields marked * are required.</p>{err}"
                "<form method='post' action='/jobs/apply' class='card'><h2>About you</h2>"
                + field("first_name", "First name *")
                + field("last_name", "Last name *")
                + field("email", "Email *", "email")
                + field("phone", "Phone *", "tel")
                + field("city", "City")
                + "<h2>Experience</h2>"
                + field("current_title", "Current or most recent job title")
                + field("current_employer", "Current or most recent employer *")
                + choice("experience", "Years of relevant experience", EXPERIENCE)
                + field("education", "Highest education (school and degree)")
                + area("skills", "Key skills")
                + area("why", "Why do you want to work at Northwind? *")
                + choice("work_auth", "Are you legally able to work in Canada? *", WORK_AUTH)
                + field("start_date", "Earliest start date")
                + "<h2>Optional</h2>"
                + field("sin", "Social Insurance Number (optional now, needed if we hire you)")
                + field(
                    "password", "Create a password to track your application (optional)", "password"
                )
                + "<p class='muted'>By applying you agree to our candidate privacy notice.</p>"
                "<button type='submit'>Submit application</button></form>",
                site="jobs",
            )

        def jobs_submit(self, form: dict[str, str]) -> str:
            need = {
                "first_name": "first name",
                "last_name": "last name",
                "email": "email",
                "phone": "phone",
                "current_employer": "most recent employer",
                "why": "answer to why you want to work here",
                "work_auth": "work authorization",
            }
            errors = [
                f"Please enter your {label}."
                for k, label in need.items()
                if not form.get(k, "").strip()
            ]
            if form.get("email") and "@" not in form["email"]:
                errors.append("That email doesn't look right.")
            if errors:
                return self.jobs_form(errors, form)
            code = "NW-" + secrets.token_hex(3).upper()
            state.applications.append(
                {**{k: v for k, v in form.items() if not k.startswith("_")}, "code": code}
            )
            return _page(
                "Application received",
                f"<h1>Thanks, {_esc(form['first_name'])}</h1><div class='card'>We've received "
                f"your application for <b>{JOB[0]}</b>.<br>Application number {code}</div>"
                "<p class='muted'>We'll email you within two weeks.</p>",
                site="jobs",
            )

        # ---------------------------------------------------------- the airline
        def _trip(self, q: dict[str, str]) -> dict[str, Any] | None:
            t = state.flights.get(q.get("ref", "").strip().upper())
            if t and q.get("last", "").strip().lower() == t["last"].lower():
                return t
            return None

        def _keys(self, t: dict[str, Any]) -> str:
            return f"ref={t['ref']}&last={quote(t['last'])}"

        def air_home(self, error: str = "") -> str:
            err = f"<p class='error'>{_esc(error)}</p>" if error else ""
            return _page(
                "Check in",
                "<h1>Online check-in</h1><p class='muted'>Opens 24 hours before departure.</p>"
                f"{err}<form method='post' action='/air/find' class='card'>"
                "<label for='ref'>Booking reference</label><input id='ref' name='ref'>"
                "<label for='last'>Last name</label><input id='last' name='last'>"
                "<button type='submit'>Find my trip</button></form>",
                site="air",
            )

        def air_find(self, form: dict[str, str]) -> str:
            t = self._trip(form)
            if not t:
                return self.air_home("We couldn't find a booking with that reference and last name.")
            return f"/air/trip?{self._keys(t)}"

        def air_trip(self, q: dict[str, str], error: str = "") -> str:
            t = self._trip(q)
            if not t:
                return self.air_home("Find your trip first.")
            dep: datetime = t["departure"]
            opens = dep - timedelta(hours=24)
            head = (
                f"<h1>{t['flight']}: {t['route']}</h1><div class='card'>Departs "
                f"{dep.strftime('%A %d %B at %H:%M')}<br>Passengers: "
                f"{', '.join(t['passengers'])}</div>"
            )
            if t.get("checked_in"):
                link = f"<a href='/air/pass?{self._keys(t)}'>Boarding pass</a>"
                return _page("Your trip", head + f"<p>You're checked in. {link}</p>", site="air")
            if state.clock() < opens:
                when = opens.strftime("%A %d %B at %H:%M")
                return _page(
                    "Your trip", head + f"<p class='error'>Check-in opens {when}.</p>", site="air"
                )
            err = f"<p class='error'>{_esc(error)}</p>" if error else ""
            boxes = "".join(
                f"<label><input type='checkbox' name='pax' value='{_esc(p)}' checked "
                f"style='width:auto'> {_esc(p)}</label>"
                for p in t["passengers"]
            )
            return _page(
                "Check in",
                head
                + err
                + "<form method='post' action='/air/checkin' class='card'>"
                f"<input type='hidden' name='ref' value='{t['ref']}'>"
                f"<input type='hidden' name='last' value='{_esc(t['last'])}'>"
                f"<h2>Who's checking in</h2>{boxes}<h2>Dangerous goods</h2>"
                "<p class='muted'>Lithium batteries over 100 Wh, fireworks, flammable liquids and "
                "gas canisters are not allowed in your bags.</p>"
                "<label><input type='checkbox' name='dg' value='yes' style='width:auto'> I confirm "
                "I'm not carrying any of these items</label><button type='submit'>Check in</button>"
                "</form>",
                site="air",
            )

        def air_checkin(self, form: dict[str, str]) -> str:
            t = self._trip(form)
            if not t:
                return self.air_home("Find your trip first.")
            if state.clock() < t["departure"] - timedelta(hours=24):
                return self.air_trip(form)
            if form.get("dg") != "yes":
                return self.air_trip(form, "Please confirm the dangerous goods declaration.")
            t["checked_in"] = True
            state.checkins.append(
                {"ref": t["ref"], "at": state.clock().isoformat(timespec="minutes")}
            )
            return f"/air/seats?{self._keys(t)}"

        def air_seats(self, q: dict[str, str]) -> str:
            t = self._trip(q)
            if not t or not t.get("checked_in"):
                return self.air_home("Find your trip first.")
            hidden = (
                f"<input type='hidden' name='ref' value='{t['ref']}'>"
                f"<input type='hidden' name='last' value='{_esc(t['last'])}'>"
            )

            def offer(title: str, text: str, item: str, button: str) -> str:
                return (
                    f"<div class='card offer'><b>{title}</b><p>{text}</p>"
                    f"<form method='post' action='/air/buy'>{hidden}"
                    f"<input type='hidden' name='item' value='{item}'>"
                    f"<button type='submit'>{button}</button></form></div>"
                )

            return _page(
                "Seats and extras",
                f"<h1>You're checked in</h1><div class='card'>Your seat: <b>{t['seat']}</b> "
                "(assigned, free)</div>"
                + offer("Want more legroom?", "Move to seat 12A, extra legroom.", "seat 12A", "Choose seat 12A for $39")
                + offer("Upgrade to Premium", "Priority boarding, a bigger seat and a meal.", "premium", "Upgrade for $149")
                + f"<a class='btn' href='/air/pass?{self._keys(t)}'>No thanks, get my boarding pass</a>",
                site="air",
            )

        def air_buy(self, form: dict[str, str]) -> str:
            state.purchases.append({"ref": form.get("ref", ""), "item": form.get("item", "")})
            return _page(
                "Payment",
                f"<h1>Payment</h1><p>{_esc(form.get('item', ''))}</p>"
                "<label for='cc'>Card number</label><input id='cc' name='cc'>",
                site="air",
            )

        def air_pass(self, q: dict[str, str]) -> str:
            t = self._trip(q)
            if not t or not t.get("checked_in"):
                return self.air_home("Check in first.")
            dep: datetime = t["departure"]
            board = (dep - timedelta(minutes=40)).strftime("%H:%M")
            return _page(
                "Boarding pass",
                f"<h1>Boarding pass</h1><div class='card pass'><b>{_esc(t['passengers'][0])}</b>"
                f"<br>{t['flight']} &middot; {t['route']}<br>"
                f"{dep.strftime('%a %d %b, departs %H:%M')}<br>Seat <b>{t['seat']}</b> &middot; "
                f"Gate <b>{t['gate']}</b> &middot; Boarding <b>{board}</b>"
                f"<p class='code'>{t['ref']}-{t['seat']}</p></div>",
                site="air",
            )

    return Handler


class BookingSite:
    """The practice sites on a local port (``port=0`` picks a free one)."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self.state = BookingState()
        self.server = ThreadingHTTPServer((host, port), _handler(self.state))
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}"

    def pay(self, hold: str | None = None) -> bool:
        """Stand-in for the user paying in the browser (tests): pays the newest hold."""
        hold = hold or next(reversed(self.state.holds), None)
        h = self.state.holds.pop(hold, None) if hold else None
        if h and hold:
            ticket = {**h, "code": "AUR-TEST"}
            self.state.tickets.append(ticket)
            self.state.paid[hold] = ticket
        return h is not None

    def add_flight(
        self,
        ref: str,
        last: str,
        departure: datetime,
        *,
        flight: str = "SK 482",
        route: str = "Vancouver (YVR) to Toronto (YYZ)",
        passengers: list[str] | None = None,
        seat: str = "23C",
        gate: str = "B14",
    ) -> dict[str, Any]:
        """A Skyward Air booking to check in for."""
        trip = {
            "ref": ref.upper(),
            "last": last,
            "departure": departure,
            "flight": flight,
            "route": route,
            "passengers": passengers or [f"Alex {last}"],
            "seat": seat,
            "gate": gate,
        }
        self.state.flights[trip["ref"]] = trip
        return trip

    def start(self) -> BookingSite:
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def __enter__(self) -> BookingSite:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()


if __name__ == "__main__":
    site = BookingSite(port=8791).start()
    site.add_flight("K7Q2ZP", "Tester", datetime.now() + timedelta(hours=20))
    print(
        f"Practice sites on {site.url} (clinic: /clinic, cinema: /cinema, jobs: /jobs, airline: "
        "/air with booking K7Q2ZP, last name Tester). Ctrl+C stops."
    )
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        site.stop()
