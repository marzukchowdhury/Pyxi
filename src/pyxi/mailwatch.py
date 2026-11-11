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
    failed: int = 0  # mails Gmail listed that could not be fetched (one 429 among 300)


def read_back_to(mails: Any) -> datetime | None:
    """The date of the oldest mail a scan read, when it was cut short; None when it read
    everything there was."""
    if not getattr(mails, "cut", False):
        return None
    dates = [m["date"] for m in mails if isinstance(m.get("date"), datetime)]
    return min(dates) if dates else None


def not_read(mails: Any) -> str:
    """What a result says when some of the mail could not be fetched ("" when all of it was):
    the scan goes on without those mails, and says so."""
    n = int(getattr(mails, "failed", 0) or 0)
    if not n:
        return ""
    return (
        f"{n} mail{'s' if n != 1 else ''} couldn't be read just now, so something may be "
        "missing."
    )


# ------------------------------------------------------------------ reading money

# European receipts write "19,99 €" and "1.234,56 €": a comma and exactly two digits, last.
EU_NUMBER = r"\d{1,3}(?:\.\d{3})*,\d{2}(?![\d.,])"
# "$485 million", "$1.2 billion", "$3.5M", "$20k": a size, not a price. (Live: a newsletter's
# "$1.2 billion" deal was listed as a subscription of $1.00.)
NOT_A_PRICE = r"(?![\d]|[.,]\d|\s?(?i:k|m|mm|b|bn|million|billion|trillion|thousand)\b)"
# "CDN$ 11.19" and "CAD $11.00" are Canadian dollars, and so is "$15.81 CAD".
MONEY = re.compile(
    r"(?P<cur>US\$|CA\$|C\$|A\$|AU\$|NZ\$|(?:CDN|CAD|USD|AUD|NZD)\s?\$|\$|€|£|USD|CAD|AUD|EUR|GBP)\s?"
    rf"(?P<n>{EU_NUMBER}|\d{{1,5}}(?:,\d{{3}})*(?:\.\d{{2}})?){NOT_A_PRICE}"
    r"(?:\s?(?P<code>USD|CAD|AUD|NZD)\b)?"
    rf"|(?P<n2>{EU_NUMBER}|\d{{1,5}}(?:,\d{{3}})*\.\d{{2}})\s?(?P<cur2>USD|CAD|AUD|EUR|GBP|€|£)(?![A-Za-z])"
)


def parse_amount(text: str) -> float:
    """'1,234.56' or '1.234,56' -> 1234.56."""
    if re.fullmatch(EU_NUMBER, text):
        return float(text.replace(".", "").replace(",", "."))
    return float(text.replace(",", ""))
# A plain "$" and "US$" are added up together: receipts mix them for the same dollars.
CURRENCY = {"US$": "$", "USD": "$", "$": "$", "CA$": "CAD", "C$": "CAD", "A$": "AUD", "AU$": "AUD",
            "NZ$": "NZD", "€": "EUR", "£": "GBP", "CDN$": "CAD", "CAD$": "CAD", "USD$": "$",
            "AUD$": "AUD", "NZD$": "NZD"}  # fmt: skip


def money_of(m: re.Match[str]) -> tuple[float, str]:
    """The amount and currency of one MONEY match."""
    cur = (m.group("cur") or m.group("cur2")).replace(" ", "")
    cur = CURRENCY.get(cur, cur)
    code = m.group("code")
    if cur == "$" and code and code != "USD":  # "$15.81 CAD"
        cur = code
    return parse_amount(m.group("n") or m.group("n2")), cur
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
    r"received your payment|"
    r"you(?:'ve| have)? (?:been )?(?:charged|paid|billed)|we (?:charged|billed)|charged|billed|"
    r"debited|amount paid|total paid|thanks? (?:you )?for your (?:payment|purchase|order)|"
    r"renewed|renewal (?:confirmation|receipt)|automatic payment|order total)\b",
    re.I,
)
RECURRING = re.compile(
    r"\b(?:subscriptions?|subscrib(?:e|ed|ing)|renew(?:s|ed|al|als|ing)?|recurring|memberships?|"
    r"your plan|dues|billing (?:period|cycle)|next (?:billing|payment|charge)|"
    r"auto-?pay(?:ment)?|automatic payment|per month|a month|another month|monthly|"
    r"annual(?:ly)?|yearly|per year|a year|another year|weekly)\b|"
    r"/\s?(?:mo|month|yr|year)\b",
    re.I,
)
# The mail is about a subscription: said in its subject or in the receipt itself.
SUB_WORD = re.compile(
    r"\b(?:subscriptions?|subscrib(?:e|ed|ing)|memberships?|renew(?:s|ed|al|als|ing)?)\b", re.I
)
# A sentence about this very purchase coming again ("Your plan renews yearly", "Next billing
# date", "until you cancel"), wherever it stands. An ad under a ride's total ("try a Lyft Pink
# membership, billed monthly") is none of these.
RENEWS = re.compile(
    r"\byour (?:plan|subscription|membership|domain)\b[^.\n]{0,60}\b(?:renews?|will renew|"
    r"auto-?renews?|continues)\b|\bnext (?:billing|payment|renewal|charge) date\b|"
    r"\b(?:you(?:'ll| will) be|will be) (?:billed|charged)\b[^.\n]{0,40}\b(?:monthly|yearly|"
    r"annually|weekly|every|each|again|until)\b|\buntil you cancel\b|"
    r"\brenews? automatically\b|\bauto-?renew(?:s|al|ing)?\b|\bautomatic(?:ally)? renew",
    re.I,
)
# Bought each time, however regular: an order or a ride is a subscription only if it says so.
ONE_OFF = re.compile(
    r"\b(?:orders?|trips?|rides?|deliver(?:y|ed|ies)|tickets?|bookings?|reservations?|parking)\b",
    re.I,
)
RECEIPT_SUBJECT = re.compile(r"\b(?:invoices?|e-?receipts?|receipts?)\b", re.I)
ENDED = re.compile(
    r"\b(?:subscription|membership|plan)\b[^.\n]{0,40}?\b(?:has been |was |is )?(?:cancell?ed|ended)\b|"
    r"\b(?:cancell?ation|cancell?ed) (?:confirmed|confirmation)\b|\bwe(?:'re| are) sorry to see you go\b",
    re.I,
)
# A mail about money that changed hands says so where a receipt does: in who sent it
# ("Uber Receipts", billing@), in its subject, in its opening line ("Thanks for riding"), or
# on a line of its own that says what was paid ("Total: $141.60", "Amount charged: $20.00").
# A newsletter that mentions an invoice somewhere in its text does none of these.
MONEY_MAIL = re.compile(
    r"\b(?:e-?receipts?|receipts?|invoices?|payments?|paid|charged|charges?|billed|bills?|"
    r"e-?bills?|billing|statements?|renew(?:s|ed|al)?|subscriptions?|memberships?|dues|"
    r"orders?|purchases?|transactions?|donations?|pledges?|refund(?:s|ed)?)\b",
    re.I,
)
THANKED = re.compile(
    r"\bthanks?(?: you)? for (?:riding|ordering|shopping|dining|subscribing|renewing|"
    r"your (?:\w+ )?(?:payment|purchase|order|business|gift|donation|pledge))\b",
    re.I,
)
# A bill or a statement is money owed again and again, whatever else it says.
BILL = re.compile(r"\b(?:e-?bills?|bills?|statements?)\b", re.I)


def said_in_head(sender: str, subject: str, body: str) -> bool:
    """Does the mail say it is about money paid in its sender, subject or opening line?"""
    return bool(
        MONEY_MAIL.search(f"{sender}\n{subject}")
        or THANKED.search(subject)
        or THANKED.search(body[:300])
    )


def is_receipt(sender: str, subject: str, body: str) -> bool:
    """Is this mail a receipt, an invoice or a bill (and not a newsletter that talks about
    money)? Its sender, subject or first lines say so, or a line in it says what was paid.
    (The first gate read the sender and subject only, and a month of real spending vanished:
    "You Got Tickets To...", "Autoload Confirmation", "Your booking is confirmed".)"""
    if said_in_head(sender, subject, body):
        return True
    row = _paid_row(body)
    return bool(row and row.strong)
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
    r"\b(?:total|totale|amount|due|charged|billed|paid|payments?|debited|refund(?:ed)?|"
    r"renew(?:s|ed|al)?)\b",
    re.I,
)
TOTAL = re.compile(r"\btotale?\b", re.I)
# Money that is not what was charged: what a card statement shows, what was saved.
NOT_PAID = re.compile(
    r"\b(?:sub-?total|balance(?! due)|minimum (?:payment|due|amount)|credit limit|"
    r"available credit|you saved?|savings?|discounts?|points|rewards?|refundable)\b",
    re.I,
)
# A part of the total: never the amount itself when a line is read as "label: amount".
PART_OF = re.compile(
    r"\b(?:tax(?:es)?|gst|hst|pst|qst|vat|tva|shipping|handling|delivery|tips?|gratuity|"
    r"deposit|credit|(?:service|booking|processing|convenience|late|transaction) fees?)\b|"
    r"^\W*fees\b",
    re.I,
)
# A line that by itself proves a payment, whatever the subject says.
STRONG = re.compile(
    r"^\W*(?:\w+\W+){0,2}totale?\b|\b(?:amount|total) (?:paid|charged|due)\b|\bcharged to\b|"
    r"\bpaid with\b|\byou paid\b",
    re.I,
)
# What may follow the amount on a line that is a label and its amount: "/month (plus taxes)".
_AFTER_AMOUNT = re.compile(
    r"(?:\s*(?:/\s?\w+|per \w+|a (?:month|year|week)|each \w+|every \w+|\([^)]*\)|"
    r"plus [\w ]+|incl\w*(?: [\w ]+)?|for (?:\d+|one|a|an|another) \w+|\+ ?tax(?:es)?|[.,;*]))*\s*",
    re.I,
)
# Words that make "... $45" part of a sentence, not a row of a receipt: "Tickets start at $45".
_PROSE_BEFORE = re.compile(
    r"\b(?:at|from|for|of|to|by|just|only|is|was|are|were|than|over|under|about|around|save|"
    r"saved|worth|costs?|raised|earn(?:ed)?|win|won|get|off|and|or|with|pay|spend|spent|"
    r"another|extra|low as|starting)\W*$",
    re.I,
)


@dataclass
class _Row:
    amount: float
    currency: str
    end: int  # where the row ends in the text
    tier: int  # 1 a total, 2 says paid, 3 label and amount, 4 item and price, 5 amount alone, 6 a sentence
    strong: bool = False  # some line of the text proves a payment by itself


def _is_label(line: str) -> bool:
    """A short line that names what the next line's amount is: "Total", "Plan price"."""
    s = line.strip()
    return bool(
        s
        and len(s) <= 40
        and len(s.split()) <= 5
        and re.search(r"[A-Za-z]", s)
        and s[-1] not in ".!?"
        and not MONEY.search(s)
    )


def _paid_row(text: str) -> _Row | None:
    """The line of a receipt that carries the amount charged. In order: the last "Total" line;
    the first line that says paid, charged, billed or due; a label and its amount ("Price:
    $9.99/month", "Monthly dues: $49.99", or "Price" with the amount on the next line); an
    item and its price; an amount alone on a line; and last, a sentence that says so across a
    line break ("...has been charged the monthly fee:" / "CDN$ 11.19.").

    A price that is only mentioned ("Go Pro: $485/year gets you every event", "tickets from
    $45") is none of these. The second version of this read total and paid lines only, and a
    Netflix receipt that says "Price: $16.49/month" was not a charge."""
    rows: list[tuple[int, re.Match[str], int, bool]] = []
    pos = 0
    label = ""  # a label alone on its line, the amount on the next (mail laid out as a table)
    for ln in text.splitlines(keepends=True):
        pos += len(ln)
        s = ln.strip()
        if not s:
            continue
        m = MONEY.search(s)
        if m is None:
            label = s if _is_label(s) else ""
            continue
        totals = list(TOTAL.finditer(s))
        if totals:  # a whole receipt on one line: "Price $16.49 Tax $0.82 Total $17.31"
            m = MONEY.search(s, totals[-1].end()) or m
        before, after = s[: m.start()], s[m.end() :]
        alone = not (before + after).strip(" \t.,;:*()-")
        # what the amount is called: its label, or the words since the last "total"
        named = label if alone else before[totals[-1].start() :] if totals else before
        whole = f"{label} {s}" if alone and label else s
        label = ""
        if NOT_PAID.search(named):
            continue
        strong = bool(STRONG.search(whole))
        if PAID_LINE.search(whole):
            rows.append((1 if TOTAL.search(whole) else 2, m, pos, strong))
        elif PART_OF.search(named):
            continue
        elif whole is not s or (
            re.fullmatch(r"[^\d$€£][^:\n]{0,48}?(?::\s*|\t\s*|\s{2,}|\s[-–]\s)", before)
            and _AFTER_AMOUNT.fullmatch(after)
        ):
            rows.append((3, m, pos, False))
        elif (
            re.search(r"[A-Za-z]", before)
            and len(s.split()) <= 8
            and not after.strip(" .")
            and not _PROSE_BEFORE.search(before)
        ):
            rows.append((4, m, pos, False))
        elif alone:
            rows.append((5, m, pos, False))
    if not rows:
        # Mail wrapped at 72 columns: the word and the amount are on different lines. Each
        # paragraph is read as one line, a sentence at a time (the text keeps its length).
        joined = re.sub(r"(?<![\n])\n(?![\n])", " ", text)
        for sentence in re.finditer(r"[^\n]+?(?:[.!?](?=\s|$)|(?=\n)|$)", joined):
            words = sentence.group(0)
            m = MONEY.search(words)
            if m and PAID_LINE.search(words) and not NOT_PAID.search(words):
                rows.append((6, m, sentence.end(), False))
                break
    if not rows:
        return None
    best = min(r[0] for r in rows)
    picks = [r for r in rows if r[0] == best]
    tier, m, end, _ = picks[-1] if best == 1 else picks[0]
    amount, currency = money_of(m)
    return _Row(amount, currency, end, tier, any(r[3] for r in rows))


def paid_amount(text: str) -> tuple[float, str, int] | None:
    """The amount actually charged, its currency, and where its line ends in ``text`` (see
    :func:`_paid_row`). There is no "first price in the mail": live, that made a newsletter's
    "$485" a yearly subscription."""
    row = _paid_row(text)
    return (row.amount, row.currency, row.end) if row else None


def amount_in(text: str) -> tuple[float, str] | None:
    """The amount actually charged (see :func:`paid_amount`), or None when the text names no
    amount as paid."""
    got = paid_amount(text)
    return got[:2] if got else None


@dataclass
class Charge:
    """One mail read as a receipt: what was paid, and the receipt itself (the subject and the
    text down to the amount's line; what comes under the total is not the receipt)."""

    amount: float
    currency: str
    part: str
    in_head: bool  # the sender, subject or opening line says it is about money


def charge_in(sender: str, subject: str, body: str) -> Charge | None:
    """This mail as a charge, or None when it isn't a receipt, an invoice or a bill with an
    amount paid on it. The one gate for the subscription finder and for Penny's spending."""
    head = said_in_head(sender, subject, body)
    row = _paid_row(body)
    if not head and not (row and row.strong):
        return None
    text = f"{subject}\n{body}"
    if PROMO.search(text) and not re.search(r"\b(?:receipt|invoice)", text, re.I):
        return None
    part = f"{subject}\n{body[: row.end]}" if row else subject
    if row is None:  # "Your payment of $15.49 was received", and nothing in the text
        row = _paid_row(subject)
        if row is None or row.tier > 2:
            return None
    if row.amount <= 0:
        return None
    return Charge(row.amount, row.currency, part, head)


def via_processor(sender: str) -> bool:
    """Is this mail from someone who takes the money for someone else (PayPal, Stripe, Apple)?"""
    name, addr = parseaddr(sender)
    where = f"{name} {addr.split('@')[-1]}".lower().replace(" ", "")
    return any(p.replace(" ", "") in where for p in PROCESSORS)


def merchant(sender: str, subject: str = "", text: str = "") -> str:
    """Who the money went to: the sender's name without "Billing" or "no-reply", or the service
    named in a PayPal / Apple / Google Play receipt."""
    name, addr = parseaddr(sender)
    domain = addr.split("@")[-1].lower()
    if via_processor(sender):
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
    """How often, as the receipt itself says it ("billed yearly", "$7.99/month", "for 1 year")."""
    if re.search(
        r"\b(?:annual(?:ly)?|yearly|per year|a year|every year|another year|12 months|"
        r"12-month|1 year|one year)\b|/\s?(?:yr|year)\b",
        text,
        re.I,
    ):
        return "yearly"
    if re.search(r"\b(?:weekly|per week|a week|every week)\b|/\s?(?:wk|week)\b", text, re.I):
        return "weekly"
    if re.search(
        r"\b(?:monthly|per month|a month|every month|another month|1 month|one month|"
        r"billing period)\b|/\s?(?:mo|month)\b",
        text,
        re.I,
    ):
        return "monthly"
    return None


def _stated_cadence(text: str) -> str | None:
    """How often, read from the whole mail of a receipt that is about a subscription: first
    from the sentences about this purchase coming again ("Your plan renews yearly"), so "save
    with an annual plan" further down a monthly receipt doesn't make it yearly."""
    for m in RENEWS.finditer(text):
        a = text.rfind("\n", 0, m.start()) + 1
        b = text.find("\n", m.end())
        said = _cadence(text[a : len(text) if b < 0 else b])
        if said:
            return said
    return _cadence(text)


# How far apart two charges of a rhythm may be: a month is 28 to 31 days, and mail is late.
_BANDS = {"monthly": (25, 36), "yearly": (350, 381), "weekly": (6, 8)}
# The same amount from the same service this soon again is the same charge, mailed twice
# (PayPal's receipt and the service's own).
SAME_CHARGE = timedelta(days=3)


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


def _regular(dates: list[datetime], rhythm: str) -> bool:
    """Is every gap that rhythm, not just the average of them?"""
    low, high = _BANDS[rhythm]
    return all(low <= (a - b).days <= high for a, b in itertools.pairwise(dates))


# ------------------------------------------------------------------ subscriptions


@dataclass
class Subscription:
    service: str
    amount: float
    currency: str
    # monthly, yearly, weekly; "repeating" when it comes back at no rhythm with a name; "once"
    # for a subscription's first receipt that doesn't say how often it will come
    cadence: str
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
        if self.cadence == "once":
            return 0.0  # nobody knows how often yet: listed, never counted in the total
        if self.cadence != "monthly" and self.every_days > 0:
            return self.amount * 30.44 / self.every_days  # every three months is a third a month
        return self.amount

    def price(self) -> str:
        per = {
            "monthly": "a month",
            "yearly": "a year",
            "weekly": "a week",
            "once": "once so far",
        }.get(self.cadence, "each time")
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
    not_read: str = ""  # "3 mails couldn't be read just now, ..." when some fetches failed


@dataclass
class _Seen:
    """One charge of a service, as find_subscriptions weighs it."""

    mail: dict[str, Any]
    amount: float
    currency: str
    says: bool  # the receipt says it comes again (subscription, renews, monthly, a bill)
    cadence: str | None  # how often, as the mail says it
    in_head: bool  # sender, subject or opening line say it is about money
    about_sub: bool  # the subject itself says subscription, membership or renewal
    one_off: bool  # an order, a ride, a ticket: bought each time
    receipt: bool  # the subject says invoice or receipt


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

    * It's a charge when the mail is a receipt, invoice or bill and an amount on it was paid
      (:func:`charge_in`).
    * It recurs when the receipt says so (subscription, renews, monthly, a bill), or when about
      the same amount comes back at a steady rhythm (any amount, for three invoices or
      receipts a month apart). An order or a ride is never one by its rhythm alone.
    * How often is what the charges show, else what the receipt says; when the mail is about a
      subscription, that is read from all of it ("Your plan renews yearly" under the total).
    * One charge that says subscription or membership in its subject and never says how
      often is listed as "once so far" and left out of the monthly total. One charge that
      says nothing of the kind is not listed at all.

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
        got = charge_in(sender, subject, body)
        if got is None or not_spending(sender, text, got.amount):
            continue
        part = got.part  # the receipt itself
        about = bool(SUB_WORD.search(part) or RENEWS.search(text))
        bill = bool(BILL.search(subject))
        g["charges"].append(
            _Seen(
                mail=e,
                amount=got.amount,
                currency="USD" if got.currency == "$" and US_DOLLARS.search(part) else got.currency,
                says=about or bill or bool(RECURRING.search(part)),
                cadence=_cadence(part) or (_stated_cadence(text) if about else None),
                in_head=got.in_head,
                about_sub=bool(SUB_WORD.search(subject)),
                one_off=bool(ONE_OFF.search(subject)),
                receipt=bool(RECEIPT_SUBJECT.search(subject)),
            )
        )
    subs = []
    for g in groups.values():
        charges: list[_Seen] = []
        for c in sorted(g["charges"], key=lambda c: c.mail["date"], reverse=True):
            # PayPal's receipt and the service's own, a day apart, are one charge: nothing
            # charges the same amount twice within three days.
            again = any(
                abs(k.amount - c.amount) < 0.005 and k.mail["date"] - c.mail["date"] <= SAME_CHARGE
                for k in charges
            )
            if not again:
                charges.append(c)
        if not charges:
            continue
        dates = [c.mail["date"] for c in charges]
        amounts = [c.amount for c in charges]
        says_so = any(c.says for c in charges)
        rhythm = _from_gaps(dates)
        if rhythm and not says_so:
            # Nothing but the dates says it recurs, so the dates have to be convincing: every
            # gap that rhythm, about the same amount each time (or three invoices or receipts,
            # whose amounts may differ: a cloud bill), a receipt by its sender or subject,
            # and not an order or a ride.
            steady = max(amounts) <= 1.25 * min(amounts)
            billed = len(charges) >= 3 and all(c.receipt for c in charges)
            if (
                not _regular(dates, rhythm)
                or not (steady or billed)
                or any(c.one_off for c in charges)
                or not any(c.in_head for c in charges)
            ):
                rhythm = None
        said = next((c.cadence for c in charges if c.cadence), None)
        every = 0.0
        if rhythm:
            cadence = rhythm  # what really happened beats "annual plan, billed monthly"
        elif says_so and said:
            cadence = said
        elif says_so and len(charges) >= 2:
            cadence = "repeating"  # at no rhythm with a name: every 3 months, say
            every = max((dates[0] - dates[-1]).days / (len(dates) - 1), 7.0)
        elif charges[0].about_sub:
            cadence = "once"  # a subscription's receipt that doesn't say how often it comes
        else:
            continue  # a one-off purchase
        latest = charges[0].mail
        if g["ended"] and g["ended"] >= latest["date"]:
            continue  # cancelled since the last charge
        stale = {"weekly": 21, "monthly": 50, "yearly": 400}.get(cadence, 100)
        if (now - latest["date"]).days > stale:
            continue  # stopped coming: probably over already
        url, source = _cancel_link(g["name"], g["mails"])
        subs.append(
            Subscription(
                service=g["name"],
                amount=charges[0].amount,
                currency=charges[0].currency,
                cadence=cadence,
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
    found.not_read = not_read(emails)
    return found


def subscriptions_report(subs: list[Subscription]) -> tuple[str, str, list[str]]:
    """Title, body and lines for the notification card. The body says what the title's total
    leaves out (another currency, a first receipt that doesn't say how often), how far back
    the scan read when it couldn't read it all, and how many mails it couldn't fetch."""
    since = getattr(subs, "read_back_to", None)
    failed = getattr(subs, "not_read", "")
    if not subs:
        return (
            "No subscriptions found",
            (
                f"I didn't find any recurring charges in your receipts since {said_day(since)}. "
                "There was more mail than I could read, so older charges may be missing."
                if since
                else "I looked through a year of receipts and didn't find any recurring charges."
            )
            + (f" {failed}" if failed else ""),
            [],
        )
    by_currency: dict[str, float] = {}
    for s in subs:
        if s.cadence != "once":
            by_currency[s.currency] = by_currency.get(s.currency, 0.0) + s.monthly
    once = len([s for s in subs if s.cadence == "once"])
    n = len(subs)
    others: list[str] = []
    if by_currency:
        main = max(by_currency, key=by_currency.get)  # type: ignore[arg-type]
        others = [money(v, c) for c, v in by_currency.items() if c != main]
        title = f"{money(by_currency[main], main)} a month in subscriptions"
    else:  # nothing whose rhythm is known: no total to give
        title = f"{n} subscription{'s' if n != 1 else ''} charged once so far"
    body = (
        f"{n} recurring charge{'s' if n != 1 else ''}, biggest first. "
        + (f"Not in that total: {' and '.join(others)} a month in other currencies. " if others else "")
        + (
            f"{once} of them {'has' if once == 1 else 'have'} been charged once and "
            f"{'does' if once == 1 else 'do'}n't say how often, so "
            f"{'it is' if once == 1 else 'they are'} not in the total. "
            if once and by_currency
            else ""
        )
        + 'Say "cancel" and the name, and I\'ll open its cancel page.'
        + (
            f" I could only read back to {said_day(since)}, so older charges may be missing."
            if since
            else ""
        )
        + (f" {failed}" if failed else "")
    )
    return title, body, [s.line() for s in subs]


def subscriptions_query() -> str:
    """What the scan asks Gmail for: every word the gate accepts a receipt or a bill by. (It
    used to leave out "bill" and "amount due", so a bill that said nothing else was never
    fetched.)"""
    return (
        "newer_than:400d -category:promotions {receipt invoice bill subscription renewal renewed "
        'membership payment billed charged "amount due" "your plan" dues cancelled canceled}'
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
