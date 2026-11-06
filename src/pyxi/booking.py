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
