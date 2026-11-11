"""Penny, money: where the money features live.

* **Monthly spending from receipts.** Penny reads one month of receipts in Gmail (Gmail's own
  ``after:``/``before:`` search, so it's exactly that month), keeps only real charges (no ads,
  no "your order shipped" repeats of the same order), sorts them into categories, and compares
  them with the budget. All in code: a summary costs nothing.
* **Receipts into a spreadsheet.** The same receipts as rows. A new file is written here with
  real formulas (a SUM total, SUMIF by category next to the budget); adding them to a
  spreadsheet or Google Sheet the user already has goes through Ledger
  (:mod:`pyxi.agents.ledger`), which edits it in place or saves a copy beside it.
* **Budgets.** Saved in ``.pyxi/money.json``: a monthly total and optional per-category limits.
* Subscriptions (``mailwatch.find_subscriptions``), bills explained on the bill
  (``companion/paperwork.py``) and cheaper elsewhere (``companion/pricecheck.py``) already exist;
  Penny's prompt routes to them instead of doing them twice.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pyxi.mailwatch import (
    CHARGED,
    ORDER_NO,
    PROMO,
    RECURRING,
    US_DOLLARS,
    is_receipt,
    merchant,
    not_spending,
    own_dollar,
    paid_amount,
    read_back_to,
    said_day,
)
from pyxi.mailwatch import money as _money

if TYPE_CHECKING:
    from pyxi.agents.jobs import Job, JobManager
    from pyxi.agents.registry import AgentProfile

FILE = "money.json"
NOT_A_CHARGE = re.compile(
    r"\b(has shipped|shipped|on (?:its|the) way|out for delivery|delivered|tracking number|"
    r"arriving|your package|ready for pickup|price drop|wish ?list|cart|left something)\b",
    re.I,
)
# Receipts that don't use the usual words: "Thanks for riding", "Total: CA$18.75".
PAID = re.compile(
    r"\bthanks? (?:you )?for (?:riding|ordering|shopping|dining)\b|"
    r"\btotal\b\s*:?\s*(?:CA|US|C|A|AU|NZ)?\$\s?\d",
    re.I,
)
# Money coming back. Read in the subject only: live, an order whose footer explained "your
# refund goes to the card you paid with" was booked as a refund of the whole order.
REFUND = re.compile(r"\brefund(?:ed|s)?\b", re.I)
# How long after a charge the same merchant's same amount is still that charge mailed again
# (a confirmation, then an invoice). Two coffees at 8 and at 3 are two charges.
SAME_CHARGE = timedelta(hours=2)
# First match wins: a phone bill is a bill even though it repeats, Uber Eats is food not a ride.
# Whole words only: "lessons" is not Esso and "retargeting" is not Target.
CATEGORIES: list[tuple[str, re.Pattern[str]]] = [
    (
        "Bills and utilities",
        re.compile(
            r"\b(?:hydro|electric\w*|energy|power|water bill|internet|wireless|mobile|telus|"
            r"rogers|bell|fido|koodo|freedom mobile|verizon|at&t|t-mobile|comcast|xfinity|shaw|"
            r"insurance|rent|property tax|utilit(?:y|ies)|maplewave)\b",
            re.I,
        ),
    ),
    (
        "Eating out",
        re.compile(
            r"\b(?:uber ?eats|doordash|skip ?the ?dishes|grubhub|deliveroo|just eat|"
            r"restaurants?|caf[eé]s?|coffee|espresso|starbucks|tim hortons|mcdonald'?s?|"
            r"pizza\w*|pizzeria|sushi|burgers?|chipotle)\b",
            re.I,
        ),
    ),
    (
        "Groceries",
        re.compile(
            r"\b(?:grocer\w*|supermarkets?|instacart|costco|safeway|loblaws?|whole foods|"
            r"trader joe'?s?|save-on|no frills|kroger|sobeys|metro|aldi|lidl|tesco|"
            r"walmart grocery|real canadian superstore|t&t|pc express|voila|fresh ?co|"
            r"food basics)\b",
            re.I,
        ),
    ),
    (
        "Transport",
        re.compile(
            r"\b(?:uber|lyft|transit|presto|compass card|gas|fuel|shell|esso|petro\w*|chevron|"
            r"parking|evo car|modo|translink|ttc)\b",
            re.I,
        ),
    ),
    (
        "Travel",
        re.compile(
            r"\b(?:airlines?|airways|air|flights?|hotels?|airbnb|booking\.com|expedia|westjet|"
            r"air canada|skyward|via rail|amtrak)\b",
            re.I,
        ),
    ),
    (
        "Health",
        re.compile(
            r"\b(?:pharmac\w+|shoppers drug\w*|cvs|walgreens|dental|dentists?|clinics?|"
            r"physio\w*|optometr\w*)\b",
            re.I,
        ),
    ),
    (
        "Entertainment",
        re.compile(
            r"\b(?:steam|playstation|nintendo|ticketmaster|cineplex|cinemas?|concerts?|"
            r"eventbrite)\b",
            re.I,
        ),
    ),
    ("Subscriptions", RECURRING),
    (
        "Shopping",
        re.compile(
            r"\b(?:amazon|best ?buy|apple|ikea|target|ebay|etsy|shein|walmart|uniqlo|h&m|zara|"
            r"staples|canadian tire|home depot|lowe'?s|sephora)\b",
            re.I,
        ),
    ),
]


def money(amount: float, currency: str) -> str:
    """mailwatch's money(): US dollars next to other dollars read "US$"."""
    return _money(amount, currency)


MONTHS = {
    m: i + 1
    for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
    )
}
MONTH_NAME = re.compile(
    r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
    r"sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b"
)
COUNTS = {
    w: i
    for i, w in enumerate(
        ["a", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve"],
        start=1,
    )
} | {"one": 1, "a couple of": 2, "couple of": 2}  # fmt: skip
HOW_MANY = r"(\d{1,2}|a couple of|couple of|" + "|".join(w for w in COUNTS if " " not in w) + ")"


@dataclass
class Receipt:
    day: str  # ISO date of the email
    merchant: str
    amount: float  # negative for a refund
    currency: str
    category: str
    subject: str
    email_id: str = ""
    order_no: str = ""

    def line(self) -> str:
        d = datetime.fromisoformat(self.day)
        what = "Refund from " if self.amount < 0 else ""
        return f"{d.day} {d:%b}: {what}{self.merchant} {money(abs(self.amount), self.currency)}"


# ------------------------------------------------------------------------------ budgets
def load(data_dir: Path) -> dict[str, Any]:
    try:
        data = json.loads((data_dir / FILE).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save(data_dir: Path, prefs: dict[str, Any]) -> dict[str, Any]:
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / FILE).write_text(json.dumps(prefs, indent=2), encoding="utf-8")
    return prefs


def category_named(said: str) -> str | None:
    """'food' -> Eating out, 'groceries' -> Groceries, '' / 'total' -> None (the whole month)."""
    s = said.strip().lower()
    if not s or s in {"total", "overall", "everything", "all", "month", "monthly"}:
        return None
    aliases = {
        "groceries|grocery": "Groceries",
        "food|eating out|takeout|take-out|restaurants?|dining|coffee": "Eating out",
        "bills?|utilit(?:y|ies)|rent|phone|internet|insurance": "Bills and utilities",
        "subscriptions?": "Subscriptions",
        "transport|transportation|transit|rides?|gas|fuel": "Transport",
        "travel|trips?|flights?|hotels?": "Travel",
        "shopping": "Shopping",
        "health|pharmacy": "Health",
        "fun|entertainment|games?": "Entertainment",
    }
    for words, category in aliases.items():
        if re.search(rf"\b(?:{words})\b", s):  # whole words: "refunds" is not "fun"
            return category
    return next((c for c, _ in CATEGORIES if c.lower().startswith(s[:5])), "Other")


def set_budget(data_dir: Path, amount: float, category: str = "") -> str:
    prefs = load(data_dir)
    budgets = prefs.setdefault("budgets", {})
    cat = category_named(category)
    key = cat or "total"
    if amount <= 0:
        budgets.pop(key, None)
        save(data_dir, prefs)
        return f"Removed the {cat or 'monthly'} budget."
    budgets[key] = round(float(amount), 2)
    save(data_dir, prefs)
    return (
        f"{cat or 'Monthly'} budget set to {money(amount, prefs.get('currency') or '$')} a month."
    )


# ------------------------------------------------------------------------------ months
def shift(first: datetime, months: int) -> datetime:
    """The first of the month, ``months`` later (or earlier, when negative)."""
    n = first.year * 12 + first.month - 1 + months
    return first.replace(year=n // 12, month=n % 12 + 1)


def months_in(start: datetime, end: datetime) -> int:
    return max(1, (end.year - start.year) * 12 + end.month - start.month)


def span_name(start: datetime, end: datetime, now: datetime) -> str:
    """ "October", "October 2025", "August to October": the months from start up to end."""
    last = end - timedelta(days=1)
    year = "" if last.year == now.year else f" {last.year}"
    if months_in(start, end) == 1:
        return f"{start:%B}{year}"
    return f"{start:%B}{'' if start.year == last.year else f' {start.year}'} to {last:%B}{year}"


def month_range(said: str, now: datetime) -> tuple[datetime, datetime]:
    """'this month', 'last month', 'October', 'Oct 2025', '2026-10', '2 months ago' -> [first
    day, next first). 'last 3 months' is the three whole months before this one, 'this year'
    is January up to now, 'last year' the twelve months before that. (Live: "2 months ago"
    came back as this month and "last 3 months" as last month, without a word.)"""
    s = said.strip().lower()
    first = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    def count(word: str) -> int:
        return max(1, int(word) if word.isdigit() else COUNTS[word])

    if m := re.search(r"(\d{4})-(\d{1,2})", s):
        if not 1 <= int(m.group(2)) <= 12:
            raise ValueError(f"There's no month {m.group(2)}. Say a month like October.")
        first = first.replace(year=int(m.group(1)), month=int(m.group(2)))
    elif m := re.search(rf"\b{HOW_MANY} months? (?:ago|back)\b", s):
        first = shift(first, -count(m.group(1)))
    elif m := re.search(rf"\b(?:last|past|previous) {HOW_MANY} months\b", s):
        return shift(first, -count(m.group(1))), first
    elif re.search(r"\b(?:this year|year to date|so far this year)\b", s):
        return first.replace(month=1), shift(first, 1)
    elif re.search(r"\b(?:last|previous) year\b", s):
        start = first.replace(year=first.year - 1, month=1)
        return start, shift(start, 12)
    elif m := MONTH_NAME.search(s):
        n = MONTHS[m.group(1)[:3]]
        y = re.search(r"\b(20\d\d)\b", s)
        year = int(y.group(1)) if y else now.year if n <= now.month else now.year - 1
        if not y and n == now.month and re.search(r"\b(?:last|previous)\b", s):
            year -= 1  # "last November", said in November
        first = first.replace(year=year, month=n)
    elif "last" in s or "previous" in s:
        first = shift(first, -1)
    elif "next" in s:  # nothing is spent yet, but it's that month they asked about
        first = shift(first, 1)
    return first, shift(first, 1)


def query(start: datetime, end: datetime) -> str:
    return (
        f"after:{start:%Y/%m/%d} before:{end:%Y/%m/%d} -category:promotions "
        "{receipt invoice order payment charged billed paid total purchase refund}"
    )


# ------------------------------------------------------------------------------ receipts
def categorise(name: str, text: str) -> str:
    head = f"{name} {text.splitlines()[0] if text else ''}"  # the merchant and the subject
    for cat, pat in CATEGORIES:
        if cat != "Subscriptions" and pat.search(head):
            return cat
    for cat, pat in CATEGORIES:
        if cat not in ("Shopping", "Travel") and pat.search(text[:600]):
            return cat
    return "Shopping" if re.search(r"\border\b", text[:600], re.I) else "Other"


def _date(mail: dict[str, Any]) -> datetime | None:
    d = mail.get("date")
    if isinstance(d, datetime):
        return d
    try:
        return datetime.fromisoformat(str(d))
    except (TypeError, ValueError):
        return None


def receipts(
    mails: list[dict[str, Any]], start: datetime | None = None, end: datetime | None = None
) -> list[Receipt]:
    """Real charges (and refunds) in these emails, one per order, oldest first.

    A mail counts when it is a receipt (its sender, subject or first lines say so: the gate
    the subscription finder uses, :func:`pyxi.mailwatch.is_receipt`) and its amount is on a
    line that says it was paid. Live, a free newsletter that mentioned an invoice and
    "$485 million" was $485.00 of spending."""
    out: list[Receipt] = []
    seen: set[tuple[str, str]] = set()
    last_at: dict[tuple[str, str], datetime] = {}
    for mail in sorted(mails, key=lambda m: d.timestamp() if (d := _date(m)) else 0.0):
        when = _date(mail)
        if when is None:
            continue
        local = when.replace(tzinfo=None)
        if (start and local < start.replace(tzinfo=None)) or (
            end and local >= end.replace(tzinfo=None)
        ):
            continue
        subject, body = str(mail.get("subject") or ""), str(mail.get("text") or "")
        sender = str(mail.get("from") or "")
        text = f"{subject}\n{body}"
        refund = bool(REFUND.search(subject))
        paid = is_receipt(sender, subject, body) and (CHARGED.search(text) or PAID.search(text))
        if not refund and (not paid or NOT_A_CHARGE.search(subject)):
            continue
        if PROMO.search(text) and not re.search(r"\b(?:receipt|invoice)", text, re.I):
            continue
        found = paid_amount(body) or paid_amount(subject)
        if not found or found[0] <= 0:
            continue
        got = found[:2]
        if not_spending(sender, text, got[0]):
            continue  # a job alert's salary, or a big number nobody paid
        if got[1] == "$" and US_DOLLARS.search(text):
            got = (got[0], "USD")  # said so: never mixed into Canadian dollars below
        name = merchant(sender, subject, body)
        if name.lower() == "uber" and re.search(r"uber ?eats", text, re.I):
            name = "Uber Eats"
        m = ORDER_NO.search(text)
        order = m.group("n") if m else ""
        back = ":r" if refund else ""
        if order:
            key = (name.lower(), order + back)
            if key in seen:
                continue  # the same order's confirmation and invoice
            seen.add(key)
        else:
            # No order number to tell by: the same amount from the same place within a couple
            # of hours is one charge mailed twice. (It used to be "on the same day", and two
            # coffees for the same price counted once.)
            same = (name.lower(), f"{got[0]:.2f}{back}")
            before = last_at.get(same)
            last_at[same] = local
            if before is not None and local - before < SAME_CHARGE:
                continue
        out.append(
            Receipt(
                day=local.date().isoformat(),
                merchant=name,
                amount=-got[0] if refund else got[0],
                currency=got[1],
                category="Refunds" if refund else categorise(name, text),
                subject=subject[:120],
                email_id=str(mail.get("id") or ""),
                order_no=order,
            )
        )
    # A plain "$" next to CA$ receipts is the same Canadian dollars (Amazon.ca, Uber in Canada
    # print just "$"), so they add up together. Same for A$ and NZ$.
    # A receipt that says US$ or USD stays apart from those; with no other dollars around it's
    # the same "$" as the rest.
    own = own_dollar(r.currency for r in out)
    for r in out:
        if r.currency == "$" and own:
            r.currency = own
        elif r.currency == "USD" and not own:
            r.currency = "$"
    return out


def signed(amount: float, currency: str) -> str:
    return ("-" if amount < 0 else "") + money(abs(amount), currency)


def summarise(
    rs: list[Receipt],
    budgets: dict[str, float],
    start: datetime,
    now: datetime,
    end: datetime | None = None,
    since: datetime | None = None,
) -> dict[str, Any]:
    """The notification and card: total, top categories, budget left (and the pace for a month
    that isn't over yet). ``end``: the first day after the months asked for (one month when
    left out); budgets are monthly, so three months get three of them. ``since``: the scan
    was cut short and only read back to this day, which the title and the body then say."""
    end = end or shift(start, 1)
    n_months = months_in(start, end)
    month = span_name(start, end, now)
    budget = "budget" if n_months == 1 else f"{n_months}-month budget"
    if n_months > 1:
        budgets = {k: v * n_months for k, v in budgets.items()}
    missing = (
        f"I could only read back to {said_day(since, now)}, so earlier receipts are missing."
        if since
        else ""
    )
    if not rs:
        return {
            "title": f"No receipts in {month}",
            "body": " ".join(
                x
                for x in (
                    "I didn't find any purchases or bills in your mail for "
                    + ("that month." if n_months == 1 else "those months."),
                    missing,
                )
                if x
            ),
            "changes": [],
            "card": None,
            "summary": f"No receipts found for {month}." + (f" {missing}" if missing else ""),
        }
    by_cur: dict[str, float] = {}
    for r in rs:
        by_cur[r.currency] = by_cur.get(r.currency, 0.0) + r.amount
    cur = max(by_cur, key=lambda c: abs(by_cur[c]))
    main = [r for r in rs if r.currency == cur]
    total = sum(r.amount for r in main)
    cats: dict[str, list[Receipt]] = {}
    for r in main:
        cats.setdefault(r.category, []).append(r)
    order = sorted(cats, key=lambda c: -sum(r.amount for r in cats[c]))
    spent = {c: sum(r.amount for r in cats[c]) for c in order}
    lines = []
    for c in order:
        n = len(cats[c])
        kind = ("refund" if c == "Refunds" else "charge") + ("" if n == 1 else "s")
        line = f"{c}: {signed(spent[c], cur)} ({n} {kind})"
        if c in budgets:
            left = budgets[c] - spent[c]
            line += (
                f", {money(left, cur)} left of {money(budgets[c], cur)}"
                if left >= 0
                else f", {money(-left, cur)} over the {money(budgets[c], cur)} {budget}"
            )
        lines.append(line)
    ongoing = start <= now < end
    bits = [f"{len(main)} receipt{'s' if len(main) != 1 else ''}."]
    top = [c for c in order if c != "Refunds"][:3]
    if top:
        bits.append(
            "Most on " + ", ".join(f"{c.lower()} ({money(spent[c], cur)})" for c in top) + "."
        )
    if "total" in budgets:
        b = budgets["total"]
        if total > b:
            bits.append(f"That's {money(total - b, cur)} over your {money(b, cur)} {budget}.")
        else:
            bits.append(f"{money(b - total, cur)} left of your {money(b, cur)} {budget}.")
            if ongoing and not since:
                days_in = (end - start).days
                pace = total / max(1, (now - start).days + 1) * days_in
                if pace > b * 1.05:
                    last = end - timedelta(days=1)
                    bits.append(
                        f"At this pace you'd spend about {money(pace, cur)} by the end of {last:%B}."
                    )
    over = [c for c in order if c in budgets and spent[c] > budgets[c]]
    if over:
        bits.append("Over budget: " + ", ".join(c.lower() for c in over) + ".")
    others = [f"{money(v, c)}" for c, v in by_cur.items() if c != cur]
    if others:
        bits.append("Also " + " and ".join(others) + " in other currencies.")
    if missing:
        bits.append(missing)
    # A total that leaves out the start of the month says so itself, where it's read first.
    when = f"Since {said_day(since, now)}" if since else f"{month}{' so far' if ongoing else ''}"
    title = f"{when}: {money(total, cur)} spent"
    card = {
        "title": title,
        "sections": [
            {"heading": f"{c} {signed(spent[c], cur)}", "lines": [r.line() for r in cats[c]]}
            for c in order
        ],
    }
    summary = f"{title}. " + " ".join(bits) + " " + "; ".join(lines) + "."
    return {
        "title": title,
        "body": " ".join(bits),
        "changes": lines,
        "card": card,
        "summary": summary,
    }


# ------------------------------------------------------------------------------ spreadsheet
def unique(folder: Path, name: str) -> Path:
    path = folder / name
    n = 2
    while path.exists():
        path = folder / f"{Path(name).stem} ({n}){Path(name).suffix}"
        n += 1
    return path


def write_sheet(rs: list[Receipt], budgets: dict[str, float], folder: Path, month: str) -> Path:
    """A new workbook: Receipts (with a SUM total) and By category (SUMIF next to the budget).
    Receipts in more than one currency get one total per currency, on both sheets: euros and
    dollars added together are not an amount of anything."""
    from openpyxl import Workbook
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = "Receipts"
    ws.append(["Date", "Merchant", "Category", "Amount", "Currency", "Email"])
    for r in rs:
        ws.append(
            [datetime.fromisoformat(r.day), r.merchant, r.category, r.amount, r.currency, r.subject]
        )
    n = len(rs) + 1
    by_cur: dict[str, float] = {}
    for r in rs:
        by_cur[r.currency] = by_cur.get(r.currency, 0.0) + r.amount
    # The budgets are in the money most of the spending is in, as in the summary.
    main = max(by_cur, key=lambda c: abs(by_cur[c])) if by_cur else ""
    currencies = sorted(by_cur, key=lambda c: (c != main, c))
    mixed = len(currencies) > 1
    if mixed:
        for c in currencies:
            ws.append([f"Total {c}", "", "", f'=SUMIF(E2:E{n},"{c}",D2:D{n})', c, ""])
    else:
        ws.append(["Total", "", "", f"=SUM(D2:D{n})", "", ""])
    for row in ws.iter_rows(min_row=n + 1, max_row=ws.max_row):
        for cell in row:
            cell.font = Font(bold=True)
        row[3].number_format = "#,##0.00"
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for row in ws.iter_rows(min_row=2, max_row=n):
        row[0].number_format = "yyyy-mm-dd"
        row[3].number_format = "#,##0.00"
    for i, width in enumerate([12, 26, 20, 12, 10, 50], start=1):
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.freeze_panes = "A2"
    cs = wb.create_sheet("By category")
    if mixed:
        # One row for each category in each currency, and a total for each currency.
        cs.append(["Category", "Currency", "Spent", "Budget", "Left"])
        pairs = sorted({(r.category, r.currency) for r in rs})
        for i, (c, cur) in enumerate(pairs, start=2):
            limit = budgets.get(c) if cur == main else None
            cs.append(
                [
                    c,
                    cur,
                    f"=SUMIFS(Receipts!D2:D{n},Receipts!C2:C{n},A{i},Receipts!E2:E{n},B{i})",
                    limit if limit is not None else "",
                    f"=D{i}-C{i}" if limit is not None else "",
                ]
            )
        last = len(pairs) + 1
        for j, cur in enumerate(currencies, start=last + 1):
            has = cur == main and "total" in budgets
            cs.append(
                [
                    "Total",
                    cur,
                    f'=SUMIF(B2:B{last},"{cur}",C2:C{last})',
                    budgets["total"] if has else "",
                    f"=D{j}-C{j}" if has else "",
                ]
            )
        money_cols = "CDE"
    else:
        cs.append(["Category", "Spent", "Budget", "Left"])
        cats = sorted({r.category for r in rs})
        for i, c in enumerate(cats, start=2):
            limit = budgets.get(c)
            cs.append(
                [
                    c,
                    f"=SUMIF(Receipts!C2:C{n},A{i},Receipts!D2:D{n})",
                    limit if limit is not None else "",
                    f"=C{i}-B{i}" if limit is not None else "",
                ]
            )
        last = len(cats) + 1
        cs.append(
            [
                "Total",
                f"=SUM(B2:B{last})",
                budgets.get("total", ""),
                f"=C{last + 1}-B{last + 1}" if "total" in budgets else "",
            ]
        )
        money_cols = "BCD"
    for row in cs.iter_rows(min_row=last + 1, max_row=cs.max_row):
        for cell in row:
            cell.font = Font(bold=True)
    for cell in cs[1]:
        cell.font = Font(bold=True)
    for col in money_cols:
        for row_no in range(2, cs.max_row + 1):
            cs[f"{col}{row_no}"].number_format = "#,##0.00"
    cs.column_dimensions["A"].width = 22
    folder.mkdir(parents=True, exist_ok=True)
    path = unique(folder, f"Receipts {month}.xlsx")
    wb.save(path)
    return path


def rows_text(rs: list[Receipt]) -> str:
    return "\n".join(
        f"{r.day}\t{r.merchant}\t{r.category}\t{r.amount:.2f}\t{r.currency}" for r in rs
    )


# ------------------------------------------------------------------------------ the job
clock = datetime.now  # tests move time
FETCH = 400  # receipts read for one request: a busy month and more


def out_dir() -> Path:
    return Path.home() / "Documents" / "Pyxi"


async def run(manager: JobManager, job: Job, agent: AgentProfile) -> None:
    action = str(job.params.get("action") or "summary")
    now = clock()
    start, end = month_range(str(job.params.get("month") or ""), now)
    n_months = months_in(start, end)
    last = end - timedelta(days=1)
    label = f"{start:%B %Y}"
    if n_months > 1:  # "August to October 2026"
        label = f"{start:%B}{'' if start.year == last.year else f' {start.year}'} to {last:%B %Y}"
    google = manager._google()
    if not google.connected():
        raise RuntimeError("Gmail isn't connected. Connect Google in Settings and ask again.")
    await manager._status(job, f"Reading your receipts for {span_name(start, end, now)}")
    # Newest first, so when a busy month has more than this the first days are what's missing,
    # and the result says how far back it read (it used to stop at 100 without a word).
    mails = await google.gmail_fetch(query(start, end), FETCH)
    since = read_back_to(mails)
    missing = (
        f" I could only read back to {said_day(since, now)}, so earlier receipts are missing."
        if since
        else ""
    )
    rs = receipts(mails, start, end)
    budgets = {
        k: float(v) for k, v in (load(manager.settings.data_dir).get("budgets") or {}).items()
    }
    if action == "summary":
        rep = summarise(rs, budgets, start, now, end, since)
        job.summary = rep["summary"]
        job.data = {k: rep[k] for k in ("title", "body", "changes", "card")}
        job.data["receipts"] = [asdict(r) for r in rs]
        job.status = "done" if rs else "failed"
        return
    if not rs:
        job.status = "failed"
        job.summary = f"I didn't find any receipts for {label} in your mail.{missing}"
        job.data = {"title": f"No receipts in {span_name(start, end, now)}", "body": job.summary}
        return
    target = str(job.params.get("target") or "").strip()
    if not target:  # a new workbook, written here with real formulas
        path = write_sheet(
            rs,
            {k: v * n_months for k, v in budgets.items()},  # budgets are monthly
            Path(job.params.get("folder") or out_dir()),
            label,
        )
        job.outputs = [str(path)]
        job.summary = f"Saved {len(rs)} receipts from {label} to {path}.{missing}"
        job.data = {
            "title": f"{len(rs)} receipts in a spreadsheet",
            "body": f"{path.name}, with totals by category. It's in {path.parent}.{missing}",
            "actions": ["open", "folder"],
            "outputs": job.outputs,
        }
        job.status = "done"
        return
    # Their own spreadsheet or Google Sheet: Ledger adds the rows.
    ledger = manager.registry.get("ledger") or agent
    asked = job.task  # the tray chip keeps saying what they asked, not Ledger's instructions
    total = (
        "one total of the Amount column for each currency under them (never add amounts in "
        "different currencies together)"
        if len({r.currency for r in rs}) > 1
        else "a total of the Amount column under them"
    )
    job.task = (
        f"Add these {len(rs)} receipts from {label} as new rows at the end of the receipts table "
        "(columns Date, Merchant, Category, Amount, Currency; add a header row first if the "
        f"sheet is empty), then make sure there's {total}."
    )
    job.context = (
        "Receipts (tab-separated: date, merchant, category, amount, currency):\n" + rows_text(rs)
    )
    local = Path(target.strip('"')).expanduser()
    if local.suffix.lower() in (".xlsx", ".xlsm") and local.is_file():
        job.files, job.params["google"] = [str(local)], []
    else:
        job.files, job.params["google"] = [], [target]
    try:
        await manager._run_documents(job, ledger)
    finally:
        job.task = asked
    if job.status == "done":
        where = Path(job.outputs[0]).name if job.outputs else "your Google Sheet"
        job.data = {
            **(job.data or {}),
            "title": f"{len(rs)} receipts added",
            "body": f"From {label}, added to {where}.{missing}",
        }
