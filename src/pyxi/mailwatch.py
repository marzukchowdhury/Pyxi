"""Money Pyxi finds in your email: forgotten subscriptions, and return windows about to close.

Both read receipts, billing and order emails in Gmail and work everything out in code (no model
calls, so a scan costs nothing and gives the same answer every time):

* **Subscriptions.** Every recurring charge: the service, the amount, how often, when it was last
  charged and the email it came from, plus where to cancel it (a link in the email, the
  service's own cancel page, or Scout finds it). Pyxi never cancels anything: it opens the page
  after an approval card, and the user presses the final button.
* **Returns.** Orders and deliveries, with the return deadline the email states, or the store's
  usual policy when the store is clear about one. Reminders land a few days before a window
  closes.
"""

from __future__ import annotations

import itertools
import json
import re
from collections.abc import Iterable, Set
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from email.utils import parseaddr
from pathlib import Path
from typing import Any


class Fetched(list):  # type: ignore[type-arg]
    """What a Gmail scan read. ``cut``: Gmail had more matching mail than was asked for, so
    the oldest of it wasn't read (the result then says how far back it got)."""

    cut: bool = False


def read_back_to(mails: Any) -> datetime | None:
    """The date of the oldest mail a scan read, when it was cut short; None when it read
    everything there was."""
    if not getattr(mails, "cut", False):
        return None
    dates = [m["date"] for m in mails if isinstance(m.get("date"), datetime)]
    return min(dates) if dates else None


# ------------------------------------------------------------------ reading money

# European receipts write "19,99 €" and "1.234,56 €": a comma and exactly two digits, last.
EU_NUMBER = r"\d{1,3}(?:\.\d{3})*,\d{2}(?![\d.,])"
# "$485 million", "$1.2 billion", "$3.5M", "$20k": a size, not a price. (Live: a newsletter's
# "$1.2 billion" deal was listed as a subscription of $1.00.)
NOT_A_PRICE = r"(?![\d]|[.,]\d|\s?(?i:k|m|mm|b|bn|million|billion|trillion|thousand)\b)"
MONEY = re.compile(
    r"(?P<cur>US\$|CA\$|C\$|A\$|AU\$|NZ\$|\$|€|£|USD|CAD|AUD|EUR|GBP)\s?"
    rf"(?P<n>{EU_NUMBER}|\d{{1,5}}(?:,\d{{3}})*(?:\.\d{{2}})?){NOT_A_PRICE}"
    rf"|(?P<n2>{EU_NUMBER}|\d{{1,5}}(?:,\d{{3}})*\.\d{{2}})\s?(?P<cur2>USD|CAD|AUD|EUR|GBP|€|£)(?![A-Za-z])"
)


def parse_amount(text: str) -> float:
    """'1,234.56' or '1.234,56' -> 1234.56."""
    if re.fullmatch(EU_NUMBER, text):
        return float(text.replace(".", "").replace(",", "."))
    return float(text.replace(",", ""))
# A plain "$" and "US$" are added up together: receipts mix them for the same dollars.
CURRENCY = {"US$": "$", "USD": "$", "$": "$", "CA$": "CAD", "C$": "CAD", "A$": "AUD", "AU$": "AUD",
            "NZ$": "NZD", "€": "EUR", "£": "GBP"}  # fmt: skip
# "USD" only shows next to another dollar (a Canadian's CA$ receipts), so it reads "US$".
SYMBOL = {"USD": "US$", "CAD": "CA$", "AUD": "A$", "NZD": "NZ$", "EUR": "€", "GBP": "£", "$": "$"}
# "US$0.99", "Total: 0.99 USD": a receipt that says it's in US dollars
US_DOLLARS = re.compile(r"\bUS\s?\$|\$\s?\d[\d,.]*\s?USD\b|\bUSD\s?\$?\s?\d")


def own_dollar(currencies: Iterable[str]) -> str | None:
    """The user's own dollar, when exactly one of CA$, A$ or NZ$ shows up in their receipts: a
    plain "$" beside it is the same money (Amazon.ca and Uber in Canada print just "$")."""
    local = set(currencies) & {"CAD", "AUD", "NZD"}
    return next(iter(local)) if len(local) == 1 else None


# The patterns that read a mail's words match whole words. Live: "supercharged" in a
# newsletter counted as "charged", and a plan that was "extended" would count as "ended".

# A charge really happened (not an offer to subscribe).
CHARGED = re.compile(
    r"\b(?:e-?receipts?|receipts?|invoices?|invoiced|"
    r"payment (?:was |has been |is )?(?:received|confirmation|confirmed|successful|processed)|"
    r"you(?:'ve| have)? (?:been )?(?:charged|paid|billed)|we (?:charged|billed)|charged|billed|"
    r"amount paid|total paid|thanks? (?:you )?for your (?:payment|purchase|order)|renewed|"
    r"renewal (?:confirmation|receipt)|automatic payment|order total)\b",
    re.I,
)
RECURRING = re.compile(
    r"\b(?:subscriptions?|subscribed|renew(?:s|ed|al|als|ing)?|recurring|memberships?|your plan|"
    r"billing (?:period|cycle)|next (?:billing|payment|charge)|auto-?pay(?:ment)?|"
    r"automatic payment|per month|a month|monthly|annual(?:ly)?|yearly|per year|a year|weekly)\b|"
    r"/\s?(?:mo|month|yr|year)\b",
    re.I,
)
ENDED = re.compile(
    r"\b(?:subscription|membership|plan)\b[^.\n]{0,40}?\b(?:has been |was |is )?(?:cancell?ed|ended)\b|"
    r"\b(?:cancell?ation|cancell?ed) (?:confirmed|confirmation)\b|\bwe(?:'re| are) sorry to see you go\b",
    re.I,
)
# A mail about money that changed hands says so where a receipt does: in who sent it
# ("Uber Receipts", billing@), in its subject, or in its opening line ("Thanks for riding").
# A newsletter that mentions an invoice somewhere in its text does none of these.
MONEY_MAIL = re.compile(
    r"\b(?:e-?receipts?|receipts?|invoices?|payments?|paid|charged|billed|bills?|billing|"
    r"statements?|renew(?:s|ed|al)?|subscriptions?|memberships?|orders?|purchases?|"
    r"refund(?:s|ed)?)\b",
    re.I,
)
THANKED = re.compile(
    r"\bthanks?(?: you)? for (?:riding|ordering|shopping|dining|"
    r"your (?:payment|purchase|order|business))\b",
    re.I,
)
# A bill or a statement is money owed again and again, whatever else it says.
BILL = re.compile(r"\b(?:bill|statement)\b", re.I)


def is_receipt(sender: str, subject: str, body: str) -> bool:
    """Is this mail a receipt, an invoice or a bill (and not a newsletter that talks about
    money)? Its sender, subject or first lines have to say so."""
    return bool(MONEY_MAIL.search(f"{sender}\n{subject}") or THANKED.search(body[:300]))
PROMO = re.compile(
    r"\b(?:\d+% off|limited time|special offer|subscribe now|try .{0,20}free)\b", re.I
)

# Senders that bill on someone else's behalf: the real service is named in the email.
PROCESSORS = (
    "paypal",
    "stripe",
    "googleplay",
    "google play",
    "apple",
    "itunes",
    "paddle",
    "gumroad",
)
SERVICE_IN_TEXT = re.compile(
    r"(?:automatic payment to|payment to|subscription to|subscribed to|your subscription for|"
    r"receipt from|charge from)\s+(?P<s>[A-Z][\w&+.' ]{1,40}?)(?=[.,:;!\n]| for | has | is | was | \(|$)",
)
SENDER_FILLER = re.compile(
    r"\b(?:billing|receipts?|payments?|invoices?|accounts?|team|support|no-?reply|notifications?|"
    r"orders?|shipping|info|hello|help|customer service|via)\b",
    re.I,
)

# Where well-known services keep their cancel page. Anything else: a link in the email, or Scout.
CANCEL_PAGES = {
    "netflix": "https://www.netflix.com/cancelplan",
    "spotify": "https://www.spotify.com/account/subscription/",
    "youtube": "https://www.youtube.com/paid_memberships",
    "disney": "https://www.disneyplus.com/account/subscription",
    "hulu": "https://secure.hulu.com/account",
    "amazon prime": "https://www.amazon.com/mc",
    "audible": "https://www.audible.com/account/overview",
    "apple": "https://apps.apple.com/account/subscriptions",
    "google play": "https://play.google.com/store/account/subscriptions",
    "google one": "https://one.google.com/settings",
    "microsoft": "https://account.microsoft.com/services",
    "xbox": "https://account.microsoft.com/services",
    "adobe": "https://account.adobe.com/plans",
    "dropbox": "https://www.dropbox.com/account/plan",
    "openai": "https://chatgpt.com/#settings/Subscription",
    "chatgpt": "https://chatgpt.com/#settings/Subscription",
    "linkedin": "https://www.linkedin.com/premium/manage/",
    "canva": "https://www.canva.com/settings/billing-and-teams",
}
CANCEL_LINK = [
    re.compile(r"cancel", re.I),
    re.compile(r"manage (?:your )?(?:subscription|membership|plan)|subscription settings", re.I),
    re.compile(
        r"billing|payment settings|account settings|manage (?:your )?account|my account", re.I
    ),
]
NOT_CANCEL = re.compile(
    r"unsubscribe|email preferences|mailing list|privacy|terms|help center", re.I
)


# Mail that mentions money without anyone paying it: job alerts and recruiters (a salary of
# $85,000.00 a year was counted as a charge), so it's never spending or a subscription.
JOB_SENDERS = ("indeed", "linkedin", "glassdoor", "ziprecruiter", "workopolis", "monster", "jobbank",
               "simplyhired", "wellfound", "hired.com", "talent.com", "eluta")
JOB_TEXT = re.compile(
    # Only words a job ad uses: "$100/year" for a membership is a real charge.
    r"\b(salary|job alerts?|jobs? for you|apply now|easy apply|is hiring|new jobs?\b|recruit(?:er|ing)|"
    r"job posting|jobs? (?:near|matching)|per annum)",
    re.I,
)
PAID_CLEARLY = re.compile(
    r"\b(?:(?:total|amount)\s+(?:paid|charged)|you (?:paid|were charged|have been charged)|"
    r"payment of|charged (?:to|on) your)\b",
    re.I,
)


def not_spending(sender: str, text: str, amount: float) -> bool:
    """A job alert or recruiter's mail, or a big number with nothing saying it was paid."""
    who = sender.lower()
    if any(j in who for j in JOB_SENDERS) or JOB_TEXT.search(text):
        return True
    return amount > 5000 and not PAID_CLEARLY.search(text)


# A line that says its amount was paid (or is owed): "Total: $34.20", "You paid $8.52",
# "Amount due: $271.07". A price that is only mentioned ("Go Pro: $485/year") is not one.
PAID_LINE = re.compile(
    r"\b(?:total|amount|balance|due|charged|billed|paid|payments?|refund(?:ed)?|"
    r"renew(?:s|ed|al)?)\b",
    re.I,
)


def paid_amount(text: str) -> tuple[float, str, int] | None:
    """The amount actually charged, its currency, and where its line ends in ``text``: a
    "Total" line beats a subtotal or tax line, else the first line that says paid, charged,
    billed or due. There is no "first price in the mail" any more: live, that made a
    newsletter's "$485" a yearly subscription."""
    rows: list[tuple[str, re.Match[str], int]] = []
    pos = 0
    label = ""  # "Total" alone on its line, with the amount on the next (mail laid out as a table)
    for ln in text.splitlines(keepends=True):
        pos += len(ln)
        if not ln.strip():
            continue
        m = MONEY.search(ln)
        said = bool(PAID_LINE.search(ln))
        if m and (said or (label and len(ln.strip()) <= 24)):
            whole = ln if said else f"{label} {ln}"
            if not re.search(r"sub-?total", whole, re.I):
                rows.append((whole, m, pos))
        label = ln.strip() if said and not m and len(ln.strip()) <= 40 else ""
    if not rows:
        return None
    totals = [r for r in rows if re.search(r"\btotal\b", r[0], re.I)]
    _, m, end = totals[-1] if totals else rows[0]
    cur = m.group("cur") or m.group("cur2")
    return parse_amount(m.group("n") or m.group("n2")), CURRENCY.get(cur, cur), end


def amount_in(text: str) -> tuple[float, str] | None:
    """The amount actually charged (see :func:`paid_amount`), or None when the text names no
    amount as paid."""
    got = paid_amount(text)
    return got[:2] if got else None


def merchant(sender: str, subject: str = "", text: str = "") -> str:
    """Who the money went to: the sender's name without "Billing" or "no-reply", or the service
    named in a PayPal / Apple / Google Play receipt."""
    name, addr = parseaddr(sender)
    domain = addr.split("@")[-1].lower()
    if any(
        p in f"{name} {domain}".lower().replace(" ", "")
        for p in (p.replace(" ", "") for p in PROCESSORS)
    ):
        m = SERVICE_IN_TEXT.search(f"{subject}\n{text}")
        if m:  # "Spotify USA Inc" -> "Spotify"
            found = re.sub(
                r"(?:,?\s+(?:inc|llc|ltd|usa|us|corp|ab)\.?)+$", "", m.group("s"), flags=re.I
            )
            return found.strip(" .")
    clean = SENDER_FILLER.sub(" ", name).strip(" -|,.:")
    clean = re.sub(r"\.(?:com|co\.uk|ca|net|io)(?![a-z])", "", clean)  # "Amazon.com" -> "Amazon"
    clean = re.sub(r"\s{2,}", " ", clean)
    if clean:
        return clean
    parts = [
        p
        for p in domain.split(".")
        if p
        not in ("com", "co", "uk", "ca", "net", "org", "io", "app", "email", "mail", "e", "info")
    ]
    return (parts[-1] if parts else domain).capitalize()


def _key(name: str) -> str:
    """'Netflix', 'NETFLIX.COM' and 'Netflix, Inc.' are the same service."""
    name = re.sub(r"\.(com|net|io|co|app)\b|,? inc\.?|,? llc|\bltd\b", "", name.lower())
    return re.sub(r"[^a-z0-9]+", " ", name).strip()


def _cadence(text: str) -> str | None:
    """How often, as the receipt itself says it ("billed yearly", "$7.99/month")."""
    if re.search(
        r"\b(?:annual(?:ly)?|yearly|per year|a year|every year|12 months)\b|/\s?(?:yr|year)\b",
        text,
        re.I,
    ):
        return "yearly"
    if re.search(r"\b(?:weekly|per week|a week|every week)\b|/\s?(?:wk|week)\b", text, re.I):
        return "weekly"
    if re.search(
        r"\b(?:monthly|per month|a month|every month|billing period)\b|/\s?(?:mo|month)\b",
        text,
        re.I,
    ):
        return "monthly"
    return None


def _from_gaps(dates: list[datetime]) -> str | None:
    gaps = [(a - b).days for a, b in itertools.pairwise(dates)]
    if not gaps:
        return None
    avg = sum(gaps) / len(gaps)
    if 25 <= avg <= 35:
        return "monthly"
    if 330 <= avg <= 400:
        return "yearly"
    if 6 <= avg <= 8:
        return "weekly"
    return None


# ------------------------------------------------------------------ subscriptions


@dataclass
class Subscription:
    service: str
    amount: float
    currency: str
    cadence: str  # monthly, yearly, weekly, or "repeating" when the emails don't say
    last_charged: str  # ISO date
    charges: int
    email_id: str
    email_subject: str
    cancel_url: str | None = None
    cancel_from: str | None = None  # "email" (a link in their receipt) or "known" (our list)
    every_days: float = 0.0  # for "repeating": the usual gap between its charges

    @property
    def monthly(self) -> float:
        if self.cadence == "yearly":
            return self.amount / 12
        if self.cadence == "weekly":
            return self.amount * 52 / 12
        if self.cadence != "monthly" and self.every_days > 0:
            return self.amount * 30.44 / self.every_days  # every three months is a third a month
        return self.amount

    def price(self) -> str:
        per = {"monthly": "a month", "yearly": "a year", "weekly": "a week"}.get(
            self.cadence, "each time"
        )
        return f"{money(self.amount, self.currency)} {per}"

    def line(self) -> str:
        last = datetime.fromisoformat(self.last_charged)
        return f"{self.service}: {self.price()}, last charged {last.day} {last:%b}"


def money(amount: float, currency: str) -> str:
    sym = SYMBOL.get(currency, currency + " ")
    return f"{sym}{amount:,.2f}"


def _cancel_link(service: str, emails: list[dict[str, Any]]) -> tuple[str | None, str | None]:
    for rule in CANCEL_LINK:
        for e in emails:
            for text, url in e.get("links", []):
                if not url.startswith("https://") or NOT_CANCEL.search(f"{text} {url}"):
                    continue
                if rule.search(text) or rule.search(url):
                    return url, "email"
    k = _key(service)
    for name, url in CANCEL_PAGES.items():
        if name in k or (k and k in name):
            return url, "known"
    return None, None


class Found(list):  # type: ignore[type-arg]
    """The subscriptions a scan found. ``read_back_to``: set when Gmail had more receipts than
    the scan read, to the date of the oldest one it did read."""

    read_back_to: datetime | None = None


def said_day(d: datetime, now: datetime | None = None) -> str:
    """ "28 Oct", with the year when it isn't this one."""
    return f"{d.day} {d:%b}" + ("" if d.year == (now or datetime.now()).year else f" {d.year}")


def find_subscriptions(
    emails: list[dict[str, Any]], now: datetime, ignore: Set[str] = frozenset()
) -> list[Subscription]:
    """Recurring charges, biggest monthly cost first.

    Only the receipt itself is read: who sent it, its subject, and its text down to the line
    the amount is on. What comes under the total (an ad for a membership, "manage your
    subscription") is not the receipt. Live, reading whole mails listed a free newsletter as
    "$485.00 a year" and a single ride as a subscription.

    * It's a charge when the mail is a receipt, invoice or bill (:func:`is_receipt`) and the
      amount is on a line that says it was paid (:func:`paid_amount`).
    * It recurs when the receipt says so (subscription, renews, monthly, a bill), or when about
      the same amount comes back at a steady rhythm. One charge that doesn't say how often it
      comes is never a subscription.

    Ones that were cancelled afterwards, or stopped coming, are left out, and so is every
    service in ``ignore`` (the ones the user said aren't subscriptions)."""
    skip = {_key(name) for name in ignore}
    groups: dict[str, dict[str, Any]] = {}
    for e in emails:
        if not e.get("date"):
            continue
        sender, subject = str(e.get("from") or ""), str(e.get("subject") or "")
        body = str(e.get("text") or "")
        text = f"{subject}\n{body}"
        service = merchant(sender, subject, body)
        if _key(service) in skip:
            continue
        g = groups.setdefault(
            _key(service), {"name": service, "charges": [], "ended": None, "mails": []}
        )
        g["mails"].append(e)
        if ENDED.search(text):
            g["ended"] = max(g["ended"] or e["date"], e["date"])
            continue
        if not is_receipt(sender, subject, body) or (
            PROMO.search(text) and not re.search(r"\b(?:receipt|invoice)", text, re.I)
        ):
            continue
        in_body = paid_amount(body)
        got = in_body or paid_amount(subject)
        if not got or got[0] <= 0:
            continue
        part = f"{subject}\n{body[: in_body[2]]}" if in_body else subject  # the receipt itself
        bill = bool(BILL.search(subject))
        if not (CHARGED.search(part) or bill) or not_spending(sender, text, got[0]):
            continue
        currency = "USD" if got[1] == "$" and US_DOLLARS.search(part) else got[1]
        g["charges"].append(
            (e, (got[0], currency), bool(RECURRING.search(part)) or bill, _cadence(part))
        )
    subs = []
    for g in groups.values():
        charges, seen = [], set()
        for c in sorted(g["charges"], key=lambda c: c[0]["date"], reverse=True):
            once = (c[0]["date"].date(), round(c[1][0], 2))
            if once not in seen:  # PayPal's receipt and the service's own are one charge
                seen.add(once)
                charges.append(c)
        if not charges:
            continue
        dates = [c[0]["date"] for c in charges]
        said = next((c[3] for c in charges if c[3]), None)
        says_so = any(c[2] for c in charges)
        rhythm = _from_gaps(dates)
        amounts = [c[1][0] for c in charges]
        if rhythm and not says_so and max(amounts) > 1.25 * min(amounts):
            rhythm = None  # two rides a month apart: a subscription charges about the same again
        if not (rhythm or (says_so and (said or len(charges) >= 2))):
            continue  # a one-off purchase, or one charge that doesn't say how often it comes
        cadence = rhythm or said  # what really happened beats "annual plan, billed monthly"
        latest, (amount, currency), *_ = charges[0]
        if g["ended"] and g["ended"] >= latest["date"]:
            continue  # cancelled since the last charge
        stale = {"weekly": 21, "monthly": 50, "yearly": 400}.get(cadence or "", 100)
        if (now - latest["date"]).days > stale:
            continue  # stopped coming: probably over already
        every = 0.0
        if cadence is None:  # it repeats, at no rhythm with a name: every 3 months, say
            every = max((dates[0] - dates[-1]).days / (len(dates) - 1), 7.0)
        url, source = _cancel_link(g["name"], g["mails"])
        subs.append(
            Subscription(
                service=g["name"],
                amount=amount,
                currency=currency,
                cadence=cadence or "repeating",
                last_charged=latest["date"].date().isoformat(),
                charges=len(charges),
                email_id=str(latest.get("id") or ""),
                email_subject=str(latest.get("subject") or ""),
                cancel_url=url,
                cancel_from=source,
                every_days=round(every, 1),
            )
        )
    # A Canadian's receipts print "$" and "CA$" for the same dollars: one currency, one total.
    # Only a receipt that says US$ or USD stays apart from those.
    own = own_dollar(s.currency for s in subs)
    for s in subs:
        if s.currency == "$" and own:
            s.currency = own
        elif s.currency == "USD" and not own:
            s.currency = "$"
    found = Found(sorted(subs, key=lambda s: s.monthly, reverse=True))
    found.read_back_to = read_back_to(emails)
    return found


def subscriptions_report(subs: list[Subscription]) -> tuple[str, str, list[str]]:
    """Title, body and lines for the notification card. The body says what the title's total
    leaves out (another currency), and how far back the scan read when it couldn't read it all."""
    since = getattr(subs, "read_back_to", None)
    if not subs:
        return (
            "No subscriptions found",
            (
                f"I didn't find any recurring charges in your receipts since {said_day(since)}. "
                "There was more mail than I could read, so older charges may be missing."
                if since
                else "I looked through a year of receipts and didn't find any recurring charges."
            ),
            [],
        )
    by_currency: dict[str, float] = {}
    for s in subs:
        by_currency[s.currency] = by_currency.get(s.currency, 0.0) + s.monthly
    main = max(by_currency, key=by_currency.get)  # type: ignore[arg-type]
    total = money(by_currency[main], main)
    others = [money(v, c) for c, v in by_currency.items() if c != main]
    n = len(subs)
    title = f"{total} a month in subscriptions"
    body = (
        f"{n} recurring charge{'s' if n != 1 else ''}, biggest first. "
        + (f"Not in that total: {' and '.join(others)} a month in other currencies. " if others else "")
        + 'Say "cancel" and the name, and I\'ll open its cancel page.'
        + (
            f" I could only read back to {said_day(since)}, so older charges may be missing."
            if since
            else ""
        )
    )
    return title, body, [s.line() for s in subs]


def subscriptions_query() -> str:
    return (
        "newer_than:400d -category:promotions {receipt invoice subscription renewal renewed "
        'membership billed charged "payment received" "your plan" "automatic payment" '
        "cancelled canceled}"
    )


def match_subscription(subs: list[Subscription], said: str) -> Subscription | None:
    k = _key(said)
    words = [w for w in k.split() if w not in ("my", "the", "subscription", "membership", "plan")]
    if not words:
        return None
    for s in subs:
        if all(w in _key(s.service) for w in words):
            return s
    return next((s for s in subs if any(w in _key(s.service) for w in words)), None)


# ------------------------------------------------------------------ returns

MONTHS = "jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec"
DATE = (
    rf"(?:(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*,?\s+)?"
    rf"(?:(?:{MONTHS})[a-z]*\.?\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s+\d{{4}})?"
    rf"|\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{MONTHS})[a-z]*\.?(?:,?\s+\d{{4}})?"
    rf"|\d{{4}}-\d{{2}}-\d{{2}}|\d{{1,2}}/\d{{1,2}}/\d{{2,4}}"
    # "19.11.2026", as much of Europe writes it (four-digit year only: "1.2.34" is a version)
    rf"|(?<![\d.])\d{{1,2}}\.\d{{1,2}}\.\d{{4}}(?![\d.]))"
)
RETURN_BY = [
    re.compile(
        rf"return(?:ed|s)?\b[^.\n]{{0,40}}?\b(?:by|until|through|thru|before|no later than):?\s+(?P<d>{DATE})",
        re.I,
    ),
    re.compile(rf"return window (?:closes|ends|is open until)\s+(?:on\s+)?(?P<d>{DATE})", re.I),
    re.compile(
        rf"eligible for (?:a )?(?:return|refund)[^.\n]{{0,20}}?(?:until|through|by)\s+(?P<d>{DATE})",
        re.I,
    ),
    re.compile(rf"returnable (?:until|through|by)\s+(?P<d>{DATE})", re.I),
]
# The usual return windows of big stores, from purchase or from delivery. Only used when the
# order email doesn't say, and always shown as "usual policy" so the user checks the order.
POLICIES = {
    "amazon": (30, "delivery"),
    "walmart": (90, "purchase"),
    "target": (90, "purchase"),
    "best buy": (15, "purchase"),
    "apple": (14, "delivery"),
    "nike": (60, "purchase"),
}
ORDER_NO = re.compile(
    # One run of separators, not three neighbouring \s* (they backtracked for minutes on a
    # newsletter full of spaces).
    r"order[ \t]{0,3}(?:#|no\.?|number)?[\s:#]{0,6}(?P<n>(?=[A-Z0-9-]{0,40}[0-9])[A-Z0-9][A-Z0-9-]{4,40})", re.I
)
ORDER_MAIL = re.compile(
    r"order|shipped|has shipped|on (?:its|the) way|out for delivery|delivered|your package|purchase",
    re.I,
)
RETURNED = re.compile(
    r"return (?:received|complete|processed|started)|refund (?:issued|processed|complete)|your refund",
    re.I,
)


def _numbers_date(text: str, ref: datetime, day_first: bool) -> datetime | None:
    """'11/30/2026', '25/11/2026', '19.11.2026'. Dots are always day first. Slashes are month
    first (as the stores here write them) unless that isn't a date, ``day_first`` says the
    mail writes them the other way, or it would be long before the mail was sent while day
    first is not: "05/12/2026" in an October mail is 5 December, not last May."""
    m = re.fullmatch(r"(\d{1,2})([/.])(\d{1,2})\2(\d{2}|\d{4})", text)
    if not m or (m.group(2) == "." and len(m.group(4)) == 2):  # "1.2.34" is a version
        return None
    a, b, year = int(m.group(1)), int(m.group(3)), int(m.group(4))
    year += 2000 if year < 100 else 0

    def on(day: int, month: int) -> datetime | None:
        try:
            return datetime(year, month, day, tzinfo=ref.tzinfo)
        except ValueError:
            return None

    month_first, day_1st = on(b, a), on(a, b)
    if m.group(2) == "." or month_first is None:
        return day_1st
    if day_1st is None:
        return month_first
    if day_first:
        return day_1st
    early = ref - timedelta(days=31)
    return day_1st if month_first < early <= day_1st else month_first


def parse_date(text: str, ref: datetime, day_first: bool = False) -> datetime | None:
    """'Nov 30, 2026', '30 November', 'Monday, December 7', '2026-11-30', '11/30/2026',
    '19.11.2026', at midnight in ref's zone. A date without a year is the one closest after ref
    minus a month. ``day_first``: this mail writes 25/11/2026, so 05/12 is 5 December."""
    numbers = _numbers_date(text.strip().rstrip(".,"), ref, day_first)
    if numbers is not None:
        return numbers
    t = re.sub(r"(?<=\d)(st|nd|rd|th)\b", "", text.strip().rstrip(".,"), flags=re.I)
    t = re.sub(r"^(mon|tue|wed|thu|fri|sat|sun)[a-z]*,?\s+", "", t, flags=re.I)
    t = re.sub(r"\bsept\b", "sep", t, flags=re.I).replace(".", "").replace(",", "")
    for fmt in ("%b %d %Y", "%B %d %Y", "%d %b %Y", "%d %B %Y", "%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(t, fmt).replace(tzinfo=ref.tzinfo)
        except ValueError:
            pass
    for fmt in ("%b %d", "%B %d", "%d %b", "%d %B"):
        try:
            d = datetime.strptime(t, fmt)
        except ValueError:
            continue
        best = None
        for year in (ref.year - 1, ref.year, ref.year + 1):
            try:
                cand = d.replace(year=year, tzinfo=ref.tzinfo)
            except ValueError:
                continue
            if cand >= ref - timedelta(days=31) and (best is None or cand < best):
                best = cand
        return best
    return None


@dataclass
class Order:
    store: str
    item: str
    order_no: str | None
    ordered: str  # ISO date of the first email
    delivered: str | None = None
    return_by: str | None = None
    basis: str = ""  # "the email says so" / "Amazon's usual 30 days from delivery" / ""
    email_id: str = ""
    returned: bool = False
    emails: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{_key(self.store)}:{self.order_no or _key(self.item)[:40]}"

    def days_left(self, now: datetime) -> int | None:
        if not self.return_by:
            return None
        end = datetime.fromisoformat(self.return_by).date()
        return (end - now.date()).days

    def line(self, now: datetime) -> str:
        what = f"{self.store}, {self.item}" if self.item else self.store
        left = self.days_left(now)
        if left is None:
            return f"{what}: no return date yet" + (
                " (the window starts when it arrives)" if self.basis == "on delivery" else ""
            )
        end = datetime.fromisoformat(self.return_by)  # type: ignore[arg-type]
        when = "today" if left == 0 else "tomorrow" if left == 1 else f"in {left} days"
        usual = " (usual policy)" if self.basis.startswith("usual") else ""
        return f"{what}: return by {end:%a} {end.day} {end:%b}, {when}{usual}"


def _item(subject: str, text: str) -> str:
    m = re.search(r"[\"“'‘](.{3,80}?)[\"”'’]", subject)
    if m:
        return m.group(1).strip()
    m = re.search(r"^\s*(?:item|product|items? ordered)\s*:\s*(.{3,80})$", text, re.I | re.M)
    if m:
        return m.group(1).strip()
    m = re.search(r"your order of (.{3,60}?)(?: has| is| was|$)", subject, re.I)
    return m.group(1).strip() if m else ""


def find_orders(emails: list[dict[str, Any]], now: datetime) -> list[Order]:
    """Orders with their return deadlines, soonest first. Shipping, delivery and return emails
    for the same order (same store and order number) are read together."""
    orders: dict[str, Order] = {}
    for e in sorted((e for e in emails if e.get("date")), key=lambda e: e["date"]):
        text = f"{e['subject']}\n{e['text']}"
        if not ORDER_MAIL.search(text) and not RETURNED.search(text):
            continue
        store = merchant(e["from"], e["subject"], e["text"])
        number = next(
            (
                m.group("n").upper()
                for m in ORDER_NO.finditer(text)
                if re.search(r"\d", m.group("n"))
            ),
            None,
        )
        item = _item(e["subject"], e["text"])
        key = f"{_key(store)}:{number or _key(item)[:40] or e['id']}"
        o = orders.get(key)
        if o is None:
            if RETURNED.search(text):
                continue  # a refund for an order we never saw
            o = orders[key] = Order(
                store=store,
                item=item,
                order_no=number,
                ordered=e["date"].date().isoformat(),
                email_id=e["id"],
            )
        o.emails.append(e["id"])
        o.item = o.item or item
        if RETURNED.search(text):
            o.returned = True
            continue
        if re.search(r"\bdelivered\b", e["subject"], re.I):
            o.delivered = e["date"].date().isoformat()
        for rule in RETURN_BY:
            hit = rule.search(text)
            if hit and (d := parse_date(hit.group("d"), e["date"])):
                o.return_by, o.basis = d.date().isoformat(), "the email says so"
                break
    out = []
    for o in orders.values():
        if o.returned:
            continue
        if o.basis != "the email says so":
            policy = next((p for name, p in POLICIES.items() if name in _key(o.store)), None)
            if policy:
                days, start = policy
                base = o.delivered if start == "delivery" else o.ordered
                if base:
                    end = datetime.fromisoformat(base) + timedelta(days=days)
                    o.return_by = end.date().isoformat()
                    o.basis = f"usual: {o.store}'s {days} days from {start}"
                else:
                    o.basis = "on delivery"
        out.append(o)
    open_ = [o for o in out if o.days_left(now) is None or o.days_left(now) >= 0]  # type: ignore[operator]
    return sorted(open_, key=lambda o: (o.return_by is None, o.return_by or ""))


def orders_query() -> str:
    return (
        'newer_than:120d {"order confirmation" "your order" "has shipped" shipped delivered '
        '"out for delivery" "order #" "return by" refund}'
    )


def reminder_day(o: Order, now: datetime, ahead: int = 3) -> datetime | None:
    """When to remind them: a few days before the window closes, at 10 am. None if there's no
    deadline, or it's so close the card they're seeing now is the reminder."""
    if not o.return_by:
        return None
    end = datetime.fromisoformat(o.return_by).replace(hour=10, tzinfo=now.tzinfo)
    for back in (ahead, 1):
        when = end - timedelta(days=back)
        if when > now:
            return when
    return None


def returns_report(orders: list[Order], now: datetime) -> tuple[str, str, list[str]]:
    dated = [o for o in orders if o.return_by]
    if not orders:
        return "Nothing to return", "No recent orders with a return window open.", []
    first = dated[0] if dated else None
    if first:
        left = first.days_left(now)
        title = f"{len(dated)} order{'s' if len(dated) != 1 else ''} you can still return"
        body = (
            f"Soonest: {first.store}"
            + (f", {first.item}" if first.item else "")
            + (f", {left} days left." if left and left > 1 else ", closing very soon.")
            + " I'll remind you a few days before each one closes."
        )
    else:
        title, body = "Orders on the way", "Their return windows start when they arrive."
    return title, body, [o.line(now) for o in orders]


# ------------------------------------------------------------------ saved state


class MailWatchStore:
    """The last scan's results (so "cancel Netflix" knows where its page is) and the orders
    already reminded about (so a daily scan only speaks up about new ones)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        try:
            self.data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.data = {}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")

    def subscriptions(self) -> list[Subscription]:
        return [Subscription(**s) for s in self.data.get("subscriptions", [])]

    def set_subscriptions(self, subs: list[Subscription]) -> None:
        self.data["subscriptions"] = [asdict(s) for s in subs]
        self.save()

    def ignored(self) -> set[str]:
        """The services the user said aren't subscriptions; scans leave them out."""
        return set(self.data.get("ignored", []))

    def not_a_subscription(self, name: str) -> str | None:
        """ "Lyft isn't a subscription": remember that, and take it off the last list. Returns
        the service's name as saved, or None when nothing in the last list matches."""
        sub = match_subscription(self.subscriptions(), name)
        if sub is None:
            return None
        self.data["ignored"] = sorted(self.ignored() | {sub.service})
        self.data["subscriptions"] = [
            s for s in self.data.get("subscriptions", []) if s.get("service") != sub.service
        ]
        self.save()
        return sub.service

    def seen_orders(self) -> set[str]:
        return set(self.data.get("orders_seen", []))

    def reminded(self) -> set[str]:
        return set(self.data.get("reminded", []))

    def mark_reminded(self, key: str) -> None:
        self.data["reminded"] = sorted(self.reminded() | {key})[-500:]
        self.save()

    def mark_orders(self, keys: set[str]) -> None:
        self.data["orders_seen"] = sorted(self.seen_orders() | keys)[-500:]
        self.save()
