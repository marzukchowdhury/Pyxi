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
    RETURNED,
    amount_in,
    merchant,
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
    r"thanks? (?:you )?for (?:riding|ordering|shopping|dining)|"
    r"\btotal\b\s*:?\s*(?:CA|US|C|A|AU|NZ)?\$\s?\d",
    re.I,
)
# First match wins: a phone bill is a bill even though it repeats, Uber Eats is food not a ride.
CATEGORIES: list[tuple[str, re.Pattern[str]]] = [
    (
        "Bills and utilities",
        re.compile(
            r"hydro|electric|energy|power|water bill|internet|wireless|mobile|telus|rogers|"
            r"\bbell\b|fido|koodo|freedom mobile|verizon|at&t|t-mobile|comcast|xfinity|insurance|"
            r"\brent\b|property tax|utility|maplewave",
            re.I,
        ),
    ),
    (
        "Eating out",
        re.compile(
            r"uber ?eats|doordash|skip ?the ?dishes|grubhub|deliveroo|just eat|restaurant|"
            r"\bcaf[eé]\b|coffee|starbucks|tim hortons|mcdonald|pizza|sushi|burger|chipotle",
            re.I,
        ),
    ),
    (
        "Groceries",
        re.compile(
            r"grocer|supermarket|instacart|costco|safeway|loblaws|whole foods|trader joe|"
            r"save-on|no frills|kroger|sobeys|\bmetro\b|aldi|lidl|tesco|walmart grocery|"
            r"real canadian superstore|t&t|pc express|voila|fresh ?co|food basics|freshco",
            re.I,
        ),
    ),
    (
        "Transport",
        re.compile(
            r"\buber\b|\blyft\b|transit|presto|compass card|\bgas\b|fuel|shell|esso|petro|"
            r"chevron|parking|evo car|modo|translink|ttc",
            re.I,
        ),
    ),
    (
        "Travel",
        re.compile(
            r"airline|airways|\bair\b|flight|hotel|airbnb|booking\.com|expedia|westjet|"
            r"air canada|skyward|via rail|amtrak",
            re.I,
        ),
    ),
    (
        "Health",
        re.compile(r"pharmacy|shoppers drug|cvs|walgreens|dental|clinic|physio|optometr", re.I),
    ),
    (
        "Entertainment",
        re.compile(
            r"steam|playstation|nintendo|ticketmaster|cineplex|cinema|concert|eventbrite", re.I
        ),
    ),
    ("Subscriptions", RECURRING),
    (
        "Shopping",
        re.compile(
            r"amazon|best ?buy|apple|ikea|target|ebay|etsy|shein|walmart|uniqlo|h&m|zara|"
            r"best ?buy|staples|canadian tire|home depot|lowe.s|sephora",
            re.I,
        ),
    ),
]
# "US$0.99", "Total: 0.99 USD": a receipt that says it's in US dollars
US_DOLLARS = re.compile(r"\bUS\s?\$|\$\s?\d[\d,.]*\s?USD\b|\bUSD\s?\$?\s?\d")


def money(amount: float, currency: str) -> str:
    """mailwatch's money(), but US dollars next to other dollars read "US$"."""
    return f"US${amount:,.2f}" if currency == "USD" else _money(amount, currency)


MONTHS = {
    m: i + 1
    for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
    )
}


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
        "food": "Eating out",
        "eating out": "Eating out",
        "takeout": "Eating out",
        "restaurants": "Eating out",
        "bills": "Bills and utilities",
        "utilities": "Bills and utilities",
        "subscriptions": "Subscriptions",
        "transport": "Transport",
        "rides": "Transport",
        "gas": "Transport",
        "travel": "Travel",
        "shopping": "Shopping",
        "health": "Health",
        "fun": "Entertainment",
        "entertainment": "Entertainment",
        "groceries": "Groceries",
        "grocery": "Groceries",
    }
    for k, v in aliases.items():
        if k in s:
            return v
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
def month_range(said: str, now: datetime) -> tuple[datetime, datetime]:
    """'this month', 'last month', 'October', 'Oct 2025', '2026-10' -> [first day, next first)."""
    s = said.strip().lower()
    first = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if "last" in s or "previous" in s:
        first = (first - timedelta(days=1)).replace(day=1)
    elif "next" in s:  # nothing is spent yet, but it's that month they asked about
        first = (first + timedelta(days=32)).replace(day=1)
    elif m := re.search(r"(\d{4})-(\d{1,2})", s):
        if not 1 <= int(m.group(2)) <= 12:
            raise ValueError(f"There's no month {m.group(2)}. Say a month like October.")
        first = first.replace(year=int(m.group(1)), month=int(m.group(2)))
    else:
        for name, n in MONTHS.items():
            if re.search(rf"\b{name}", s):
                y = re.search(r"\b(20\d\d)\b", s)
                year = int(y.group(1)) if y else now.year if n <= now.month else now.year - 1
                first = first.replace(year=year, month=n)
                break
    nxt = (first + timedelta(days=32)).replace(day=1)
    return first, nxt


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
    """Real charges (and refunds) in these emails, one per order, oldest first."""
    out: list[Receipt] = []
    seen: set[tuple[str, str]] = set()
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
        text = f"{subject}\n{body}"
        refund = bool(RETURNED.search(text)) and bool(re.search(r"refund", text, re.I))
        paid = CHARGED.search(text) or PAID.search(text)
        if not refund and (not paid or NOT_A_CHARGE.search(subject)):
            continue
        if PROMO.search(text) and not re.search(r"receipt|invoice", text, re.I):
            continue
        got = amount_in(body) or amount_in(subject)
        if not got or got[0] <= 0:
            continue
        if got[1] == "$" and US_DOLLARS.search(f"{subject}\n{body}"):
            got = (got[0], "USD")  # said so: never mixed into Canadian dollars below
        name = merchant(str(mail.get("from") or ""), subject, body)
        if name.lower() == "uber" and re.search(r"uber ?eats", text, re.I):
            name = "Uber Eats"
        m = ORDER_NO.search(text)
        order = m.group("n") if m else ""
        key = (name.lower(), (order or f"{got[0]:.2f}:{local.date()}") + (":r" if refund else ""))
        if key in seen:
            continue  # the same order's confirmation and invoice
        seen.add(key)
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
    local = {r.currency for r in out} & {"CAD", "AUD", "NZD"}
    if len(local) == 1:
        (cur,) = local
        for r in out:
            if r.currency == "$":
                r.currency = cur
    else:
        for r in out:
            if r.currency == "USD":
                r.currency = "$"
    return out


def signed(amount: float, currency: str) -> str:
    return ("-" if amount < 0 else "") + money(abs(amount), currency)


def summarise(
    rs: list[Receipt], budgets: dict[str, float], start: datetime, now: datetime
) -> dict[str, Any]:
    """The notification and card: total, top categories, budget left (and the pace for a month
    that isn't over yet)."""
    month = f"{start:%B}" + ("" if start.year == now.year else f" {start.year}")
    if not rs:
        return {
            "title": f"No receipts in {month}",
            "body": "I didn't find any purchases or bills in your mail for that month.",
            "changes": [],
            "card": None,
            "summary": f"No receipts found for {month}.",
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
                else f", {money(-left, cur)} over the {money(budgets[c], cur)} budget"
            )
        lines.append(line)
    end = (start + timedelta(days=32)).replace(day=1)
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
            bits.append(f"That's {money(total - b, cur)} over your {money(b, cur)} budget.")
        else:
            bits.append(f"{money(b - total, cur)} left of your {money(b, cur)} budget.")
            if ongoing:
                days_in = (end - start).days
                pace = total / max(1, (now - start).days + 1) * days_in
                if pace > b * 1.05:
                    bits.append(
                        f"At this pace you'd spend about {money(pace, cur)} by the end of {start:%B}."
                    )
    over = [c for c in order if c in budgets and spent[c] > budgets[c]]
    if over:
        bits.append("Over budget: " + ", ".join(c.lower() for c in over) + ".")
    others = [f"{money(v, c)}" for c, v in by_cur.items() if c != cur]
    if others:
        bits.append("Also " + " and ".join(others) + " in other currencies.")
    title = f"{month}{' so far' if ongoing else ''}: {money(total, cur)} spent"
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
    """A new workbook: Receipts (with a SUM total) and By category (SUMIF next to the budget)."""
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
    ws.append(["Total", "", "", f"=SUM(D2:D{n})", "", ""])
    for cell in ws[1] + ws[n + 1]:
        cell.font = Font(bold=True)
    for row in ws.iter_rows(min_row=2, max_row=n):
        row[0].number_format = "yyyy-mm-dd"
        row[3].number_format = "#,##0.00"
    ws[f"D{n + 1}"].number_format = "#,##0.00"
    for i, width in enumerate([12, 26, 20, 12, 10, 50], start=1):
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.freeze_panes = "A2"
    cs = wb.create_sheet("By category")
    cs.append(["Category", "Spent", "Budget", "Left"])
    cats = sorted({r.category for r in rs})
    for i, c in enumerate(cats, start=2):
        budget = budgets.get(c)
        cs.append(
            [
                c,
                f"=SUMIF(Receipts!C2:C{n},A{i},Receipts!D2:D{n})",
                budget if budget is not None else "",
                f"=C{i}-B{i}" if budget is not None else "",
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
    for cell in cs[1] + cs[last + 1]:
        cell.font = Font(bold=True)
    for col in "BCD":
        for row in range(2, last + 2):
            cs[f"{col}{row}"].number_format = "#,##0.00"
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


def out_dir() -> Path:
    return Path.home() / "Documents" / "Pyxi"


async def run(manager: JobManager, job: Job, agent: AgentProfile) -> None:
    action = str(job.params.get("action") or "summary")
    now = clock()
    start, end = month_range(str(job.params.get("month") or ""), now)
    label = f"{start:%B %Y}"
    google = manager._google()
    if not google.connected():
        raise RuntimeError("Gmail isn't connected. Connect Google in Settings and ask again.")
    await manager._status(job, f"Reading your receipts for {start:%B}")
    mails = await google.gmail_fetch(query(start, end), 100)
    rs = receipts(mails, start, end)
    budgets = {
        k: float(v) for k, v in (load(manager.settings.data_dir).get("budgets") or {}).items()
    }
    if action == "summary":
        rep = summarise(rs, budgets, start, now)
        job.summary = rep["summary"]
        job.data = {k: rep[k] for k in ("title", "body", "changes", "card")}
        job.data["receipts"] = [asdict(r) for r in rs]
        job.status = "done" if rs else "failed"
        return
    if not rs:
        job.status, job.summary = "failed", f"I didn't find any receipts for {label} in your mail."
        job.data = {"title": f"No receipts in {start:%B}", "body": job.summary}
        return
    target = str(job.params.get("target") or "").strip()
    if not target:  # a new workbook, written here with real formulas
        path = write_sheet(rs, budgets, Path(job.params.get("folder") or out_dir()), label)
        job.outputs = [str(path)]
        job.summary = f"Saved {len(rs)} receipts from {label} to {path}."
        job.data = {
            "title": f"{len(rs)} receipts in a spreadsheet",
            "body": f"{path.name}, with totals by category. It's in {path.parent}.",
            "actions": ["open", "folder"],
            "outputs": job.outputs,
        }
        job.status = "done"
        return
    # Their own spreadsheet or Google Sheet: Ledger adds the rows.
    ledger = manager.registry.get("ledger") or agent
    asked = job.task  # the tray chip keeps saying what they asked, not Ledger's instructions
    job.task = (
        f"Add these {len(rs)} receipts from {label} as new rows at the end of the receipts table "
        "(columns Date, Merchant, Category, Amount, Currency; add a header row first if the "
        "sheet is empty), then make sure there's a total of the Amount column under them."
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
            "body": f"From {label}, added to {where}.",
        }
