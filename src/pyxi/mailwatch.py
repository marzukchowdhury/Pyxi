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
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from email.utils import parseaddr
from pathlib import Path
from typing import Any

# ------------------------------------------------------------------ reading money

# European receipts write "19,99 €" and "1.234,56 €": a comma and exactly two digits, last.
EU_NUMBER = r"\d{1,3}(?:\.\d{3})*,\d{2}(?![\d.,])"
MONEY = re.compile(
    r"(?P<cur>US\$|CA\$|C\$|A\$|AU\$|NZ\$|\$|€|£|USD|CAD|AUD|EUR|GBP)\s?"
    rf"(?P<n>{EU_NUMBER}|\d{{1,5}}(?:,\d{{3}})*(?:\.\d{{2}})?)"
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
SYMBOL = {"USD": "$", "CAD": "CA$", "AUD": "A$", "NZD": "NZ$", "EUR": "€", "GBP": "£", "$": "$"}

# A charge really happened (not an offer to subscribe).
CHARGED = re.compile(
    r"receipt|invoice|payment (?:received|confirmation|confirmed|successful|processed)|"
    r"you(?:'ve| have)? (?:been )?(?:charged|paid|billed)|we (?:charged|billed)|charged|billed|"
    r"amount paid|total paid|thanks? (?:you )?for your (?:payment|purchase|order)|renewed|"
    r"renewal (?:confirmation|receipt)|automatic payment|order total",
    re.I,
)
RECURRING = re.compile(
    r"subscription|subscribed|renew|recurring|membership|your plan|billing (?:period|cycle)|"
    r"next (?:billing|payment|charge)|auto-?pay|automatic payment|per month|a month|/\s?mo\b|"
    r"monthly|annual(?:ly)?|yearly|per year|/\s?yr\b|weekly",
    re.I,
)
ENDED = re.compile(
    r"(?:subscription|membership|plan)[^.\n]{0,40}(?:has been |was |is )?(?:cancell?ed|ended)|"
    r"(?:cancell?ation|cancell?ed) (?:confirmed|confirmation)|we(?:'re| are) sorry to see you go",
    re.I,
)
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
    r"(?:total|amount)\s+(?:paid|charged)|you (?:paid|were charged|have been charged)|payment of|charged (?:to|on) your",
    re.I,
)


def not_spending(sender: str, text: str, amount: float) -> bool:
    """A job alert or recruiter's mail, or a big number with nothing saying it was paid."""
    who = sender.lower()
    if any(j in who for j in JOB_SENDERS) or JOB_TEXT.search(text):
        return True
    return amount > 5000 and not PAID_CLEARLY.search(text)


def amount_in(text: str) -> tuple[float, str] | None:
    """The amount actually charged: a "Total" line beats a subtotal or tax line, a "charged"
    line beats any other price, and the first price is the last resort."""
    lines = [ln for ln in text.splitlines() if MONEY.search(ln)]
    if not lines:
        return None

    def pick(line: str) -> tuple[float, str]:
        m = MONEY.search(line)
        assert m
        cur = m.group("cur") or m.group("cur2")
        return parse_amount(m.group("n") or m.group("n2")), CURRENCY.get(cur, cur)

    totals = [
        ln
        for ln in lines
        if re.search(r"\btotal\b", ln, re.I) and not re.search(r"sub-?total", ln, re.I)
    ]
    if totals:
        return pick(totals[-1])
    charged = [
        ln for ln in lines if re.search(r"charged|billed|paid|amount|price|renew|payment", ln, re.I)
    ]
    return pick(charged[0] if charged else lines[0])


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
    if re.search(r"annual|yearly|per year|a year|/\s?yr\b|12 months", text, re.I):
        return "yearly"
    if re.search(r"weekly|per week|a week|/\s?wk\b", text, re.I):
        return "weekly"
    if re.search(r"monthly|per month|a month|/\s?mo\b|every month|billing period", text, re.I):
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

    @property
    def monthly(self) -> float:
        return {"yearly": self.amount / 12, "weekly": self.amount * 52 / 12}.get(
            self.cadence, self.amount
        )

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


def find_subscriptions(emails: list[dict[str, Any]], now: datetime) -> list[Subscription]:
    """Recurring charges, biggest monthly cost first. A charge counts when the email shows money
    really changing hands; it's recurring when the email says so (subscription, renews,
    monthly...) or when the same amount comes back at a steady rhythm. Ones that were cancelled
    afterwards, or stopped coming, are left out."""
    groups: dict[str, dict[str, Any]] = {}
    for e in emails:
        if not e.get("date"):
            continue
        text = f"{e['subject']}\n{e['text']}"
        service = merchant(e["from"], e["subject"], e["text"])
        g = groups.setdefault(
            _key(service), {"name": service, "charges": [], "ended": None, "mails": []}
        )
        g["mails"].append(e)
        if ENDED.search(text):
            g["ended"] = max(g["ended"] or e["date"], e["date"])
            continue
        if not CHARGED.search(text) or (
            PROMO.search(text) and not re.search(r"receipt|invoice", text, re.I)
        ):
            continue
        got = amount_in(e["text"]) or amount_in(e["subject"])
        if got and not_spending(str(e.get("from") or ""), text, got[0]):
            continue
        if not got or got[0] <= 0:
            continue
        g["charges"].append((e, got, bool(RECURRING.search(text)), _cadence(text)))
    subs = []
    for g in groups.values():
        charges = sorted(g["charges"], key=lambda c: c[0]["date"], reverse=True)
        if not charges:
            continue
        dates = [c[0]["date"] for c in charges]
        said = next((c[3] for c in charges if c[3]), None)
        cadence = said or _from_gaps(dates)
        recurring = any(c[2] for c in charges) or (cadence is not None and len(charges) >= 2)
        if not recurring:
            continue  # a one-off purchase
        latest, (amount, currency), *_ = charges[0]
        if g["ended"] and g["ended"] >= latest["date"]:
            continue  # cancelled since the last charge
        stale = {"weekly": 21, "monthly": 50, "yearly": 400}.get(cadence or "", 100)
        if (now - latest["date"]).days > stale:
            continue  # stopped coming: probably over already
        url, source = _cancel_link(g["name"], g["mails"])
        subs.append(
            Subscription(
                service=g["name"],
                amount=amount,
                currency=currency,
                cadence=cadence or "repeating",
                last_charged=latest["date"].date().isoformat(),
                charges=len(charges),
                email_id=latest["id"],
                email_subject=latest["subject"],
                cancel_url=url,
                cancel_from=source,
            )
        )
    return sorted(subs, key=lambda s: s.monthly, reverse=True)


def subscriptions_report(subs: list[Subscription]) -> tuple[str, str, list[str]]:
    """Title, body and lines for the notification card."""
    if not subs:
        return (
            "No subscriptions found",
            "I looked through a year of receipts and didn't find any recurring charges.",
            [],
        )
    by_currency: dict[str, float] = {}
    for s in subs:
        by_currency[s.currency] = by_currency.get(s.currency, 0.0) + s.monthly
    main = max(by_currency, key=by_currency.get)  # type: ignore[arg-type]
    total = money(by_currency[main], main)
    n = len(subs)
    title = f"{total} a month in subscriptions"
    body = (
        f"{n} recurring charge{'s' if n != 1 else ''}, biggest first. "
        'Say "cancel" and the name, and I\'ll open its cancel page.'
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
    rf"|\d{{4}}-\d{{2}}-\d{{2}}|\d{{1,2}}/\d{{1,2}}/\d{{2,4}})"
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


def parse_date(text: str, ref: datetime) -> datetime | None:
    """'Nov 30, 2026', '30 November', 'Monday, December 7', '2026-11-30', '11/30/2026', at
    midnight in ref's zone. A date without a year is the one closest after ref minus a month."""
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
