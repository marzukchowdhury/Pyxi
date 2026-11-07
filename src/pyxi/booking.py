"""Booking: appointments, clinics, movies and tables, done by Scout in a browser you can watch.

"Book me a dentist cleaning Saturday morning" or "two tickets for Dune tonight around 7" becomes
a Scout job that finds the slot, fills the form from the details you saved once (Settings, kept
only on this PC), and stops at the two moments that are yours:

* **the final confirm** ("Book appointment", "Reserve"): you approve it on an Allow card first;
* **payment**: Scout never types card numbers. It holds the seats, then hands the browser
  window to you ("Pay here, then press Done") and reads the confirmation once you have.

Government ID numbers (health card, SIN, passport) are never saved or typed by Pyxi: if a form
needs one, Scout pauses and asks you to type it yourself.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

FILE = "profile.json"
FIELDS = {
    "first_name": "First name",
    "last_name": "Last name",
    "email": "Email",
    "phone": "Phone",
    "date_of_birth": "Date of birth",
    "city": "City or postal code",
    "preferences": "Preferences (e.g. mornings, aisle seats, Dr. Lee)",
}
# Approving these buttons is approving the booking: they always get an Allow card.
CONFIRM_PATTERNS = [
    r"\bbook appointment\b",
    r"\bbook (?:my |this |the )?(?:visit|table|seats?|tickets?|now)\b",
    r"\bconfirm (?:booking|appointment|reservation)\b",
    r"\bcomplete (?:booking|reservation)\b",
    r"\breserve\b",
    r"\bschedule (?:appointment|visit)\b",
]
HANDOVER = "HANDOVER:"

RULES = """You're booking this for the user, in a browser they can watch. Rules:
- Use only the user's details below, and only the ones the form asks for. Never invent a detail.
  If the form needs something that isn't below (a health card or insurance number, an address,
  a referral), call ask_human with one short question, and type exactly what they answer.
- Never type card numbers, CVCs, passwords or government ID numbers yourself; the guardrails
  will stop you anyway.
- Pick what best matches what they asked (day, time of day, who, how many). If nothing fits,
  take the closest and say so in your summary, or call ask_human when it's a real tradeoff
  (for example Saturday 4pm, or Monday 9am?).
- Cinemas: seats side by side, middle rows (not the first three), unless they said otherwise.
- The final button (Book appointment, Confirm, Reserve): click it. The user is asked to approve
  that click first, so don't ask them separately.
- Payment: when you reach a page asking for card details, stop. Call ask_human with
  "HANDOVER: <what's held, the total, how long it's held>. Pay in this browser window, then
  press Done." When they answer, look at the page (reload it if needed): if it shows a
  confirmation, finish with it; if not, finish saying it isn't paid yet.
- Finish with success=true only when it's booked (or paid for by them), and data:
  {"booked": true, "what": ..., "where": ..., "when": "YYYY-MM-DDTHH:MM", "who": ...,
   "confirmation": ..., "total": ..., "address": ...} (leave out what you don't know).
  If it couldn't be booked, success=false and say exactly why."""


# Forms filled from their documents (a job application, a government form). Same two moments
# are theirs: the final Submit (an Allow card), and anything secret, which they type themselves.
FORM_RULES = """You're filling in this form for the user, in a browser they can watch. Rules:
- Fill each field from the user's details and documents below, in the form's own format (dates,
  phone numbers, a country picked from the list). Copy facts exactly: names, employers, dates,
  schools, titles. Never invent or round a fact. A field nothing below answers: leave it empty
  if it's optional; if it's required, call ask_human with one short question and type exactly
  what they answer.
- Free-text answers ("Why do you want this job?", a cover note): write 2-4 plain sentences from
  their documents and what they asked for. Nothing that isn't true of them.
- NEVER type passwords, card numbers, CVCs, or government ID numbers (SIN, SSN, passport,
  driver's licence, health card, tax number), not even if they appear in a document. Leave
  those fields empty when optional. When one is required, call ask_human with "HANDOVER: Type
  your <which one> into the form in this browser window, then press Done." and carry on once
  they have.
- Uploads (attach a CV): if the form has a file field and the user named a file, upload that
  file; otherwise skip it and say so in your summary.
- Before the final Submit, check every field once against the details below. Then click
  Submit (or Send application, Apply): the user is asked to approve that click, so don't ask
  them separately.
- Finish with success=true only when the site confirms it went in, and data:
  {"submitted": true, "what": ..., "where": ..., "confirmation": ..., "left_for_you": [fields
  you left empty that they may want to fill]} (leave out what you don't know). If it couldn't
  be sent, success=false and say exactly why."""

# Flight check-in, done at the moment it opens. Free, so no approval, but never anything paid.
CHECKIN_RULES = """You're checking the user in for their flight on the airline's website. Rules:
- Find online check-in (Check in, Manage booking), enter the booking reference and last name
  below, and check in every passenger on the booking unless told otherwise.
- Accept the required declarations (dangerous goods, health or travel rules) and keep the seat
  you're given. Decline every paid extra: seat selection that costs money, upgrades, priority
  boarding, bags, insurance, lounge passes. Pick "No thanks", "Skip" or "Continue without".
  Never click anything that pays or adds a charge; if the only way forward costs money, stop
  and say so.
- Never type passwords, card numbers or passport or ID numbers. If check-in needs one, stop
  and finish with success=false, data {"needs_you": "<what they must add>", "link": <the
  check-in page address>}.
- When it says you're checked in, open or find the boarding pass and finish with success=true
  and data: {"checked_in": true, "flight": ..., "seat": ..., "gate": ..., "boarding": ...,
  "boarding_pass": <the boarding pass page address, from the address bar>} (leave out what
  you don't know)."""
# Paid extras on a check-in always stop for an Allow card (the default patterns cover pay and buy).
CHECKIN_PATTERNS = [r"\bupgrade\b", r"\badd to (?:booking|trip|cart)\b", r"\$\s?\d"]
# Free check-in steps are not final actions: "Confirm" or "Submit" here costs nothing.
CHECKIN_SAFE = {r"\bsubmit\b", r"\bconfirm\b", r"\bsend\b"}

SECRET_FIELD = re.compile(
    r"\b(?:sin|ssn|social (?:insurance|security)|passport|driver'?s? licen[cs]e|health card|"
    r"password|card number|cvc|cvv|tax (?:id|number)|national id)\b",
    re.IGNORECASE,
)


def load(data_dir: Path) -> dict[str, str]:
    try:
        raw = json.loads((data_dir / FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: str(v).strip() for k, v in raw.items() if k in FIELDS and str(v).strip()}


def save(data_dir: Path, profile: dict[str, Any]) -> dict[str, str]:
    clean = {k: str(v).strip()[:200] for k, v in profile.items() if k in FIELDS and str(v).strip()}
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / FILE).write_text(json.dumps(clean, indent=2), encoding="utf-8")
    return clean


def ready(profile: dict[str, str]) -> bool:
    """Enough to book almost anything: a name and a way to reach them."""
    return bool(profile.get("first_name") and (profile.get("email") or profile.get("phone")))


def shared_labels(profile: dict[str, str]) -> str:
    """'your name, email and phone', for the consent card."""
    labels = []
    if profile.get("first_name") or profile.get("last_name"):
        labels.append("name")
    labels += [
        FIELDS[k].lower().split(" (")[0]
        for k in ("email", "phone", "date_of_birth", "city")
        if profile.get(k)
    ]
    if len(labels) > 1:
        return "your " + ", ".join(labels[:-1]) + " and " + labels[-1]
    return "your " + (labels[0] if labels else "details")


def brief(args: dict[str, Any], profile: dict[str, str], now: str) -> str:
    """The task Scout gets."""
    want = [f"Book: {args.get('what', '').strip()}"]
    for key, label in (
        ("where", "Where"),
        ("when", "When"),
        ("people", "How many"),
        ("notes", "Also"),
    ):
        if str(args.get(key) or "").strip():
            want.append(f"{label}: {str(args[key]).strip()}")
    details = "\n".join(f"- {FIELDS[k].split(' (')[0]}: {v}" for k, v in profile.items())
    return f"{now}\n\n" + "\n".join(want) + f"\n\nThe user's details:\n{details}\n\n{RULES}"


def redact_secrets(text: str) -> str:
    """A document's ID numbers and passwords never reach Scout: 'SIN: 046 454 286' becomes
    'SIN: [left out: the user types it]'. Scout can't type what it never saw."""
    return re.sub(
        SECRET_FIELD.pattern + r"(\s*(?:number|no\.?|#)?\s*[:#-]?\s*)([A-Z0-9][A-Z0-9 -]{4,24}\d)",
        lambda m: m.group(0)[: m.start(2) - m.start(0)] + "[left out: the user types it]",
        text,
        flags=re.IGNORECASE,
    )


def form_brief(
    args: dict[str, Any], profile: dict[str, str], documents: dict[str, str], now: str
) -> str:
    """The task Scout gets for "fill this form from my CV". Its first line names the job (the
    task tray shows it). ``documents``: file path -> its text."""
    want = [f"Fill: {str(args.get('what') or 'the form').strip()}"]
    for key, label in (("where", "Where"), ("notes", "Also")):
        if str(args.get(key) or "").strip():
            want.append(f"{label}: {str(args[key]).strip()}")
    details = "\n".join(f"- {FIELDS[k].split(' (')[0]}: {v}" for k, v in profile.items())
    docs = "".join(
        f"\n\n--- {Path(name).name} ---\n{redact_secrets(text)[:6000]}"
        for name, text in documents.items()
    )
    upload = f"\nFiles you may upload if the form asks: {', '.join(documents)}" if documents else ""
    return (
        "\n".join(want)
        + f"\n{now}\n\nThe user's details:\n{details or '- (none saved)'}"
        + (f"\n\nTheir documents:{docs}" if docs else "")
        + upload
        + f"\n\n{FORM_RULES}"
    )


def parse_departure(text: str) -> datetime | None:
    """'2026-11-12T08:35' or '2026-11-12 08:35' (local time), or with an offset."""
    try:
        when = datetime.fromisoformat(str(text).strip().replace(" ", "T"))
    except ValueError:
        return None
    return when.astimezone().replace(tzinfo=None) if when.tzinfo else when


def checkin_opens(departure: datetime, hours: int = 24) -> datetime:
    return departure - timedelta(hours=hours)


def checkin_brief(trip: dict[str, Any], profile: dict[str, str]) -> str:
    """The task Scout gets when check-in opens. Its first line is for the task tray."""
    name = " ".join(str(trip.get(k) or "").strip() for k in ("airline", "flight")).strip()
    last = str(trip.get("last_name") or profile.get("last_name") or "").strip()
    lines = [
        f"Check in: {name or 'your flight'}",
        f"Booking reference: {str(trip.get('confirmation') or '').strip().upper()}",
        f"Last name: {last}",
    ]
    if first := str(trip.get("first_name") or profile.get("first_name") or "").strip():
        lines.append(f"First name: {first}")
    if trip.get("departure"):
        lines.append(f"Departs: {trip['departure']} {trip.get('from') or ''}".rstrip())
    if trip.get("notes"):
        lines.append(f"Also: {trip['notes']}")
    return "\n".join(lines) + f"\n\n{CHECKIN_RULES}"

