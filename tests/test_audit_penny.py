"""Penny, after the audit of a live evening: what she listed that nobody was paying for.

Live, "what subscriptions am I paying for?" came back with "The Startup Club: $485.00 a year"
(a free newsletter), "Lyft: $8.52 each time" (one ride) and "The GIST: $1.00 each time"
(another newsletter), and a monthly total that counted all three. The mails here are made up
to the same shape: a newsletter with "$485 million" and "you subscribed", one ride receipt
with a membership ad under its total, a newsletter with "$1.2 billion"; beside real-looking
Netflix, Spotify and internet-bill receipts that must still be found.

No network, no model, never the real mailbox.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from openpyxl import load_workbook

from pyxi.agents import penny
from pyxi.companion import specialists
from pyxi.mailwatch import (
    CHARGED,
    ENDED,
    RECURRING,
    MailWatchStore,
    amount_in,
    find_subscriptions,
    paid_amount,
    parse_date,
    read_back_to,
    subscriptions_report,
)
from test_mailwatch import FakeGmail, connected, until

TZ = ZoneInfo("America/Vancouver")


def now() -> datetime:
    return datetime.now(TZ)


def ago(days: float) -> datetime:
    return now() - timedelta(days=days)


def mail(sender: str, subject: str, text: str, when: datetime) -> dict[str, Any]:
    """A mail as GoogleConnector.gmail_fetch hands it over."""
    return {
        "id": f"{sender[:6]}-{when:%m%d%H%M}",
        "from": sender,
        "subject": subject,
        "text": text,
        "date": when,
        "links": [],
    }


FOOTER = (
    "\nYou're receiving this email because you subscribed to our newsletter.\n"
    "Unsubscribe | Manage your subscription | View in browser\n"
)


def startup_club(days: float = 3) -> dict[str, Any]:
    return mail(
        "The Startup Club <hello@thestartupclub.example>",
        "5 lessons from founders who supercharged their growth",
        "Hi there,\nThis week: how one founder sent her first invoice.\n"
        "The company raised $485 million a year ago and never looked back.\n" + FOOTER,
        ago(days),
    )


def the_gist(days: float = 8) -> dict[str, Any]:
    return mail(
        "The GIST <pass@thegist.example>",
        "Your morning sports fix",
        "The league supercharged its media deal: $1.2 billion over six seasons.\n"
        "Tickets start at $45.\n" + FOOTER,
        ago(days),
    )


def lyft(days: float = 8, total: str = "8.52") -> dict[str, Any]:
    return mail(
        "Lyft Ride Receipt <no-reply@lyftmail.example>",
        "Your ride with Sam",
        f"Thanks for riding with Sam!\nLyft fare (2.1mi, 9m) $7.02\nService fee $1.50\n"
        f"You paid ${total}\nVisa *4242 charged ${total}\n\n"
        "Ride more, save more: try a Lyft Pink membership, billed monthly.\n",
        ago(days),
    )


def netflix() -> list[dict[str, Any]]:
    return [
        mail(
            "Netflix <info@account.netflix.example>",
            "Your payment was received",
            "Hi Tay,\nThanks for your payment. Your membership continues.\nPlan: Standard\n"
            "Amount: $15.49\nNext billing date: see your account.\n",
            ago(d),
        )
        for d in (3, 33, 63)
    ]


def spotify() -> list[dict[str, Any]]:
    return [
        mail(
            "Spotify <no-reply@spotify.example>",
            "Your Spotify Premium receipt",
            "Spotify Premium\nTotal $11.99\nPayment method: Visa\n",
            ago(d),
        )
        for d in (12, 42)
    ]


def internet() -> list[dict[str, Any]]:
    return [
        mail(
            "Northline <billing@northline.example>",
            "Your Northline bill is ready",
            f"Your bill is ready.\nAmount due: ${amount}\n"
            "It will be taken from your account on the 20th.\n",
            ago(d),
        )
        for d, amount in ((6, "89.00"), (36, "86.40"))
    ]


def inbox() -> list[dict[str, Any]]:
    return [startup_club(), the_gist(), lyft(), *netflix(), *spotify(), *internet()]


# ---------------------------------------------------------------- finding 1: what is a subscription


def test_newsletters_and_one_ride_are_not_subscriptions():
    """Live: The Startup Club at $485.00 a year, The GIST at $1.00 and one Lyft ride at $8.52
    "each time" were all listed, and the monthly total counted them."""
    subs = find_subscriptions(inbox(), now())
    assert [s.service for s in subs] == ["Northline", "Netflix", "Spotify"]
    assert [s.price() for s in subs] == ["$89.00 a month", "$15.49 a month", "$11.99 a month"]
    title, body, lines = subscriptions_report(subs)
    assert title == "$116.48 a month in subscriptions"
    assert body.startswith("3 recurring charges") and "each time" not in " ".join(lines)
    # The three live ones alone find nothing at all.
    assert find_subscriptions([startup_club(), the_gist(), lyft()], now()) == []


def test_a_size_is_not_a_price():
    """Live: "$485 million" was read as $485.00 and "$1.2 billion" as $1.00."""
    for words in ("raised a total of $485 million", "a total of $1.2 billion", "$3.5M paid",
                  "paid $20k", "total funding: $2bn"):  # fmt: skip
        assert amount_in(words) is None, words
    assert amount_in("Total: $1,249.00") == (1249.0, "$")
    assert amount_in("Charged $5 monthly") == (5.0, "$")  # "m" for monthly is not millions
    assert amount_in("You paid $100.") == (100.0, "$")


def test_the_words_are_whole_words():
    """Live: "supercharged" made a newsletter a receipt (it counted as "charged")."""
    assert not CHARGED.search("founders who supercharged and turbocharged their growth")
    assert CHARGED.search("We charged your card") and CHARGED.search("Your e-receipt")
    assert not RECURRING.search("You have unsubscribed.") and RECURRING.search("$7.99/month")
    assert not ENDED.search("Your plan has been extended and upgraded")
    assert ENDED.search("Your subscription has been canceled")


def test_the_amount_has_to_be_on_a_line_that_says_it_was_paid():
    """Live: with no "Total" line, the first price anywhere in the mail was the amount, so a
    newsletter's "Go Pro: $485/year" became a charge."""
    assert amount_in("Go Pro: $485/year gets you every event.\nTickets from $45.") is None
    assert amount_in("Subtotal $108.98\nTax $10.90\nTotal $119.88") == (119.88, "$")
    # A mail laid out as a table: the label on one line, the amount on the next.
    assert amount_in("Sub-total\n$28.50\nTotal\n$34.20\n") == (34.2, "$")
    text = "Lyft fare $7.02\nYou paid $8.52\nTry a Lyft Pink membership\n"
    amount, _currency, end = paid_amount(text)
    assert amount == 8.52 and text[:end].endswith("You paid $8.52\n")  # the ad is not in it


def test_only_the_receipt_itself_says_whether_it_recurs():
    """Live: one ride was a subscription because an ad under its total said "membership"."""
    assert find_subscriptions([lyft()], now()) == []
    # The same words above the total are the receipt's own: one yearly invoice is found.
    adobe = mail(
        "Adobe <message@adobe.example>",
        "Your Adobe invoice",
        "Creative Cloud Photography plan\nAnnual plan, billed yearly\nSubtotal US$108.98\n"
        "Tax US$10.90\nTotal US$119.88\n",
        ago(70),
    )
    [sub] = find_subscriptions([adobe], now())
    assert (sub.service, sub.cadence, round(sub.monthly, 2)) == ("Adobe", "yearly", 9.99)
    # A newsletter's "a year ago" is not how often anything is charged.
    club = dict(startup_club(), subject="Your membership invoice")
    assert find_subscriptions([club], now()) == []


def test_one_charge_that_does_not_say_how_often_is_not_a_subscription():
    """Live: charges with no rhythm were listed "each time" and counted in full every month."""
    one_bill, earlier = internet()
    assert find_subscriptions([one_bill], now()) == []  # one bill, no "monthly" on it
    [sub] = find_subscriptions([one_bill, earlier], now())  # two, a month apart
    assert (sub.cadence, sub.charges, sub.price()) == ("monthly", 2, "$89.00 a month")
    said = dict(one_bill, text="Your monthly bill is ready.\nAmount due: $89.00\n")
    assert find_subscriptions([said], now())[0].cadence == "monthly"
    # Two rides a month apart: a rhythm, but a subscription charges about the same again.
    assert find_subscriptions([lyft(4, "8.52"), lyft(34, "23.10")], now()) == []
    # The same charge mailed twice (PayPal's receipt and the service's own) is one charge.
    twice = [*spotify(), dict(spotify()[0], id="again", subject="Receipt for your payment")]
    assert find_subscriptions(twice, now())[0].charges == 2


def test_a_charge_every_three_months_is_a_third_a_month():
    quarterly = [
        mail(
            "Harbour Storage <billing@harbourstorage.example>",
            "Your storage subscription receipt",
            "Storage unit subscription\nTotal: $90.00\n",
            ago(d),
        )
        for d in (10, 101, 192)
    ]
    [sub] = find_subscriptions(quarterly, now())
    assert sub.cadence == "repeating" and 29 < sub.monthly < 31 and sub.every_days == 91.0


# ---------------------------------------------------------------- "that's not a subscription"


def test_what_they_said_is_not_a_subscription_stays_out(tmp_path):
    """Live: "only what I'm actually paying" ran the same scan and gave the same list; there
    was no way to take a wrong one off it."""
    store = MailWatchStore(tmp_path / "mailwatch.json")
    store.set_subscriptions(find_subscriptions(inbox(), now()))
    assert store.not_a_subscription("the netflix one") == "Netflix"
    assert store.not_a_subscription("hulu") is None  # nothing like it in the last list
    assert store.ignored() == {"Netflix"}
    assert [s.service for s in store.subscriptions()] == ["Northline", "Spotify"]
    again = MailWatchStore(tmp_path / "mailwatch.json")  # it is saved
    subs = find_subscriptions(inbox(), now(), ignore=again.ignored())
    assert [s.service for s in subs] == ["Northline", "Spotify"]
    assert subscriptions_report(subs)[0] == "$100.99 a month in subscriptions"


# ---------------------------------------------------------------- finding 9: currencies


def sub_mail(name: str, total: str, days: float) -> dict[str, Any]:
    return mail(
        f"{name} <billing@{name.lower()}.example>",
        f"Your {name} receipt",
        f"{name}, monthly subscription\nTotal: {total}\n",
        ago(days),
    )


def test_a_plain_dollar_beside_canadian_dollars_is_the_same_money():
    """Found in the audit: CA$20.99 and CA$22.00 beside $115.00 and $11.99 gave the title
    "$126.99 a month", and the Canadian ones were left out without a word."""
    subs = find_subscriptions(
        [
            sub_mail("Netflix", "CA$20.99", 3),
            sub_mail("Northline", "$115.00", 5),
            sub_mail("Crave", "CA$22.00", 6),
            sub_mail("Spotify", "$11.99", 9),
        ],
        now(),
    )
    assert {s.currency for s in subs} == {"CAD"}
    title, body, lines = subscriptions_report(subs)
    assert title == "CA$169.98 a month in subscriptions" and "Not in that total" not in body
    assert lines[0].startswith("Northline: CA$115.00 a month")


def test_what_the_total_leaves_out_is_said():
    subs = find_subscriptions(
        [
            sub_mail("Netflix", "CA$20.99", 3),
            sub_mail("Northline", "$115.00", 5),
            sub_mail("Notion", "US$10.00", 6),  # it says US dollars: never added to the others
            sub_mail("Deezer", "9,99 €", 7),
        ],
        now(),
    )
    title, body, lines = subscriptions_report(subs)
    assert title == "CA$135.99 a month in subscriptions"
    assert "Not in that total: US$10.00 and €9.99 a month in other currencies." in body
    assert any(line.startswith("Notion: US$10.00 a month") for line in lines)


# ---------------------------------------------------------------- finding 4: how much mail is read


class PagedGmail(FakeGmail):
    """Gmail as it really lists mail: a page at a time, with a token for the next page."""

    PAGE = 100

    async def __call__(self, method: str, url: str, **kw: Any) -> Any:
        if method == "GET" and url.endswith("/messages"):
            params = kw["params"]
            self.queries.append(params["q"])
            start = int(params.get("pageToken") or 0)
            size = min(int(params["maxResults"]), self.PAGE)
            page = self.mails[start : start + size]
            out: dict[str, Any] = {"messages": [{"id": m["id"]} for m in page]}
            if start + size < len(self.mails):
                out["nextPageToken"] = str(start + size)
            return out
        return await super().__call__(method, url, **kw)


def connector(mails: list[dict[str, Any]]) -> tuple[Any, PagedGmail]:
    from pyxi.connectors.google import GoogleConnector

    g = GoogleConnector.__new__(GoogleConnector)
    fake = PagedGmail(mails)
    g._api = fake  # type: ignore[method-assign]
    g._tz = "America/Vancouver"
    return g, fake


def newsletters(n: int, first: float = 0.5, step: float = 0.1) -> list[dict[str, Any]]:
    """Mail that matches the search by a word and is nothing: what an inbox is full of."""
    return [
        {
            "from": f"Weekly Digest {i} <news{i}@digest.example>",
            "subject": "This week: a membership story and hotel news",
            "body": "An invoice template, a reservation tip and flight news. No prices here.",
            "date": ago(first + i * step),
        }
        for i in range(n)
    ]


def as_sent(m: dict[str, Any]) -> dict[str, Any]:
    """One of this file's mails, as the fake Gmail stores it (it has a body, not a text)."""
    return {"from": m["from"], "subject": m["subject"], "body": m["text"], "date": m["date"]}


async def test_a_scan_pages_through_gmail_and_says_when_it_was_cut():
    """Live: the scan read the 80 newest mails that matched, called that "a year of
    receipts", and every charge it listed was from the last ten days."""
    receipts = [as_sent(m) for m in (*netflix(), *spotify(), *internet())]
    # 250 newsletters in front of the receipts: all of it fits in a scan of 300.
    g, fake = connector(receipts + newsletters(250))
    mails = await g.gmail_fetch("newer_than:400d", 300)
    assert len(mails) == 257 and not mails.cut and len(fake.queries) == 3  # three pages
    assert read_back_to(mails) is None
    subs = find_subscriptions(mails, now())
    assert [s.service for s in subs] == ["Northline", "Netflix", "Spotify"]
    assert "read back to" not in subscriptions_report(subs)[1]
    # 400 newsletters in front: the scan stops at 300, knows it, and says how far it got.
    g, _ = connector(receipts + newsletters(400))
    mails = await g.gmail_fetch("newer_than:400d", 300)
    oldest = read_back_to(mails)
    assert len(mails) == 300 and mails.cut and oldest is not None
    assert oldest == min(m["date"] for m in mails)
    title, body, _lines = subscriptions_report(find_subscriptions(mails, now()))
    assert title == "No subscriptions found" and "a year of receipts" not in body
    assert f"since {oldest.day} {oldest:%b}" in body and "older charges may be missing" in body


async def test_a_short_list_is_still_one_request():
    g, fake = connector([as_sent(m) for m in netflix()])
    assert len(await g._list("from:netflix", 10)) == 3 and len(fake.queries) == 1


# ---------------------------------------------------------------- Penny's month of spending

NOW = datetime(2026, 11, 7, 9, 0)


def on(day: int, hour: int = 12, minute: int = 0, month: int = 10) -> datetime:
    return datetime(2026, month, day, hour, minute, tzinfo=TZ)


def october() -> tuple[datetime, datetime]:
    return penny.month_range("October", NOW)


def spent(mails: list[dict[str, Any]]) -> list[tuple[str, float, str]]:
    return [(r.merchant, r.amount, r.category) for r in penny.receipts(mails, *october())]


def test_a_newsletter_is_not_spending_and_a_ride_is():
    """Live shape: the same newsletter was $485.00 of spending in the month's summary, filed
    under Transport because "lessons" contains "esso"."""
    club = dict(startup_club(), date=on(5))
    ride = dict(lyft(), date=on(6))
    assert spent([club, dict(the_gist(), date=on(7)), ride]) == [("Lyft Ride", 8.52, "Transport")]
    rep = penny.summarise(penny.receipts([club, ride], *october()), {}, october()[0], NOW)
    assert rep["title"] == "October: $8.52 spent"


def test_an_order_with_a_returns_policy_is_not_a_refund():
    """Found in the audit: an order whose footer said "your refund goes to the card you paid
    with" was booked as a refund, so the month read "$-348.00 spent"."""
    order = mail(
        "Best Buy <orders@bestbuy.example>",
        "Thanks for your order",
        "Thanks for your order.\nOrder number: BBY01-806123456789\nOrder Total: $348.00\n\n"
        "Changed your mind? Send it back within 30 days and your refund goes to the card "
        "you paid with.\n",
        on(5),
    )
    refund = mail(
        "Best Buy <returns@bestbuy.example>",
        "Your refund has been issued",
        "Your refund of $25.00 for order #BBY01-111111222222 has been issued.",
        on(9),
    )
    assert spent([order, refund]) == [("Best Buy", 348.0, "Shopping"), ("Best Buy", -25.0, "Refunds")]


def test_categories_match_whole_words():
    """Live shape: "5 lessons from founders" was Transport (Esso), and a budget for
    "refunds" was saved as Entertainment ("fun")."""
    assert penny.categorise("Piano Lessons Co", "Your receipt\nTotal $60.00") == "Other"
    assert penny.categorise("Espresso House", "Your receipt\nTotal $4.50") == "Eating out"
    assert penny.categorise("Esso", "Your receipt\nTotal $61.20") == "Transport"
    assert penny.categorise("Petro-Canada", "Your receipt\nTotal $58.00") == "Transport"
    assert penny.categorise("Retargeting Weekly", "Invoice\nTotal $20.00") != "Shopping"
    assert penny.categorise("BC Hydro", "Your bill\nAmount due $91.00") == "Bills and utilities"
    assert penny.category_named("refunds") == "Other"
    assert penny.category_named("fun") == "Entertainment"
    assert penny.category_named("rent") == "Bills and utilities"
    assert penny.category_named("eating out") == "Eating out"
    assert penny.category_named("groceries") == "Groceries" and penny.category_named("") is None


def coffee(when: datetime, subject: str = "Your Daily Grind receipt") -> dict[str, Any]:
    return mail(
        "Daily Grind <receipts@dailygrind.example>",
        subject,
        "Thanks for your purchase.\nTotal: $5.25\n",
        when,
    )


def test_two_coffees_for_the_same_price_on_one_day_are_two():
    """Found in the audit: the same amount from the same place on the same day counted once,
    whatever the hours between."""
    assert len(spent([coffee(on(5, 8)), coffee(on(5, 15))])) == 2
    # A confirmation and its invoice ten minutes later are still one charge.
    assert len(spent([coffee(on(5, 8)), coffee(on(5, 8, 10), "Invoice from Daily Grind")])) == 1


def test_months_in_their_words_are_the_months_they_said():
    """Found in the audit: "2 months ago" came back as this month and "last 3 months" as last
    month, with nothing to say so."""
    first = datetime(2026, 11, 1)
    assert penny.month_range("2 months ago", NOW) == (datetime(2026, 9, 1), datetime(2026, 10, 1))
    assert penny.month_range("a month ago", NOW)[0] == datetime(2026, 10, 1)
    assert penny.month_range("a couple of months ago", NOW)[0] == datetime(2026, 9, 1)
    assert penny.month_range("last 3 months", NOW) == (datetime(2026, 8, 1), first)
    assert penny.month_range("the past two months", NOW) == (datetime(2026, 9, 1), first)
    assert penny.month_range("this year", NOW) == (datetime(2026, 1, 1), datetime(2026, 12, 1))
    assert penny.month_range("last year", NOW) == (datetime(2025, 1, 1), datetime(2026, 1, 1))
    # "last October", said in March, is not February.
    assert penny.month_range("last october", datetime(2026, 3, 9))[0] == datetime(2025, 10, 1)
    assert penny.month_range("last November", NOW)[0] == datetime(2025, 11, 1)
    assert penny.month_range("14 months ago", NOW)[0] == datetime(2025, 9, 1)
    # What already worked still does.
    assert penny.month_range("last month", NOW) == (datetime(2026, 10, 1), first)
    assert penny.month_range("sept 2025", NOW)[0] == datetime(2025, 9, 1)
    assert penny.month_range("", NOW)[0] == first


def test_three_months_are_named_and_get_three_budgets():
    start, end = penny.month_range("last 3 months", NOW)
    rs = penny.receipts([coffee(on(5, 8, month=m)) for m in (8, 9, 10)], start, end)
    rep = penny.summarise(rs, {"total": 10.0}, start, NOW, end)
    assert rep["title"] == "August to October: $15.75 spent"
    assert "$14.25 left of your $30.00 3-month budget." in rep["body"]
    empty = penny.summarise([], {}, start, NOW, end)
    assert empty["title"] == "No receipts in August to October"


async def test_the_spreadsheet_has_one_total_for_each_currency(tmp_path):
    """Found in the audit: €400, CA$100 and US$50 were added up to a Total of 550."""
    rs = penny.receipts(
        [
            mail("Hotel Lisboa <stay@lisboa.example>", "Your invoice", "Total: 400,00 €\n", on(8)),
            mail("Loblaws <r@loblaws.example>", "Your receipt", "Total: CA$100.00\n", on(9)),
            mail("Daily Grind <r@dailygrind.example>", "Your receipt", "Total: $5.25\n", on(9)),
            mail("Steam <noreply@steam.example>", "Your receipt", "Total: US$50.00\n", on(10)),
        ],
        *october(),
    )
    wb = load_workbook(penny.write_sheet(rs, {"total": 300.0, "Groceries": 150.0}, tmp_path, "October 2026"))
    ws = wb["Receipts"]
    totals = {r[0].value: r[3].value for r in ws.iter_rows(min_row=6)}
    assert totals == {
        "Total EUR": '=SUMIF(E2:E5,"EUR",D2:D5)',
        "Total CAD": '=SUMIF(E2:E5,"CAD",D2:D5)',
        "Total USD": '=SUMIF(E2:E5,"USD",D2:D5)',
    }
    assert not any("=SUM(" in str(c.value) for row in ws.iter_rows() for c in row)
    cs = wb["By category"]
    assert [c.value for c in cs[1]] == ["Category", "Currency", "Spent", "Budget", "Left"]
    rows = {(r[0].value, r[1].value): r for r in cs.iter_rows(min_row=2)}
    assert rows[("Groceries", "CAD")][2].value.startswith("=SUMIFS(Receipts!D2:D5,Receipts!C2:C5,")
    # Budgets sit beside the money most was spent in (euros here), never beside another one.
    assert rows[("Groceries", "CAD")][3].value in ("", None)
    assert rows[("Total", "EUR")][3].value == 300.0 and rows[("Total", "CAD")][3].value in ("", None)
    # One currency: the sheet is as it always was.
    one = load_workbook(penny.write_sheet(rs[1:3], {}, tmp_path, "October 2026"))
    assert one["Receipts"]["A4"].value == "Total" and one["Receipts"]["D4"].value == "=SUM(D2:D3)"
    assert [c.value for c in one["By category"][1]] == ["Category", "Spent", "Budget", "Left"]


def paged(settings, mails: list[dict[str, Any]]):
    brain, rec, _ = connected(settings, [])
    fake = PagedGmail(mails)
    brain.connectors.google._api = fake  # type: ignore[method-assign]
    return brain, rec, fake


def many_coffees(n: int) -> list[dict[str, Any]]:
    """n receipts of $10 through October, the oldest first."""
    step = timedelta(days=30) / n
    return [
        {
            "from": "Daily Grind <receipts@dailygrind.example>",
            "subject": "Your receipt",
            "body": f"Thanks for your purchase.\nOrder number: DG-{1000 + i}\nTotal: $10.00\n",
            "date": on(1, 8) + step * i,
        }
        for i in range(n)
    ]


async def test_a_busy_month_is_read_whole(settings, monkeypatch):
    """Found in the audit: 120 receipts of $10 came back as "October: $1,000.00 spent"; the
    scan stopped at the newest 100."""
    monkeypatch.setattr(penny, "clock", lambda: NOW)
    brain, rec, fake = paged(settings, many_coffees(120))
    await specialists.handle(brain, "money", {"action": "summary", "month": "October"})
    [ready] = await until(rec, "job_ready")
    assert ready["title"] == "October: $1,200.00 spent" and len(fake.queries) == 2  # two pages
    assert "read back to" not in ready["body"]


async def test_a_month_too_big_to_read_says_where_it_stopped(settings, monkeypatch):
    monkeypatch.setattr(penny, "clock", lambda: NOW)
    monkeypatch.setattr(penny, "FETCH", 100)  # a small scan, so the test stays quick
    mails = many_coffees(150)
    brain, rec, _ = paged(settings, mails)
    await specialists.handle(brain, "money", {"action": "summary", "month": "October"})
    [ready] = await until(rec, "job_ready")
    oldest = sorted(m["date"] for m in mails)[50]  # the newest 100 of the 150 were read
    assert ready["title"] == f"Since {oldest.day} Oct: $1,000.00 spent"
    assert f"I could only read back to {oldest.day} Oct, so earlier receipts are missing." in (
        ready["body"]
    )


# ---------------------------------------------------------------- finding 10: dates


def test_day_first_and_dotted_dates():
    """Found in the audit: "05/12/2026" in an October mail was read as 12 May (and the trip
    was dropped as past), and "25/11/2026" and "19.11.2026" were not dates at all."""
    ref = datetime(2026, 10, 20, tzinfo=TZ)

    def day(text: str, **kw: Any) -> str | None:
        got = parse_date(text, ref, **kw)
        return got.date().isoformat() if got else None

    assert day("25/11/2026") == "2026-11-25"  # there is no month 25
    assert day("19.11.2026") == "2026-11-19" and day("5.12.2026") == "2026-12-05"
    assert day("05/12/2026") == "2026-12-05"  # May is long gone when the mail was sent
    assert day("11/30/2026") == "2026-11-30"  # as the stores here write it
    assert day("11/12/2026") == "2026-11-12"  # both could be: month first, as before
    assert day("11/12/2026", day_first=True) == "2026-12-11"  # the mail writes them day first
    assert day("31.02.2026") is None and day("1.2.34") is None


@pytest.mark.parametrize("text", ["Version 10.0.26300 is out", "Call 604.555.0199"])
def test_numbers_with_dots_are_not_dates(text):
    import re

    from pyxi.mailwatch import DATE

    assert not re.search(DATE, text, re.I)
