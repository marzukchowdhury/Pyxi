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


# ================================================================ after the review of the fix
#
# The first fix kept the three live false positives out, and with them a good part of the real
# mail: the gate had been tuned on six made-up receipts. A reviewer wrote twelve senders the
# way they really write (three monthly charges each) and a month of ordinary spending; these
# are those mails, with made-up addresses.

MONTHS_BACK = (66, 35, 5)  # three charges, 31 and 30 days apart


def monthly(
    sender: str,
    subject: str,
    body: str,
    amounts: tuple[str, ...] = (),
    days: tuple[float, ...] = MONTHS_BACK,
) -> list[dict[str, Any]]:
    """The same receipt once a month: {DATE} is the day it was sent, {NEXT} a month later,
    {AMT} that month's amount."""
    out = []
    for i, d in enumerate(days):
        when, nxt = ago(d), ago(d - 30)
        text = body.replace("{DATE}", f"{when:%B} {when.day}, {when.year}")
        text = text.replace("{NEXT}", f"{nxt:%B} {nxt.day}, {nxt.year}")
        if amounts:
            text = text.replace("{AMT}", amounts[i])
        title = subject.replace("{DATE}", f"{when:%b} {when.day}, {when.year}")
        out.append(dict(mail(sender, title, text, when), id=f"{sender[:8]}-{i}"))
    return out


TWELVE: dict[str, tuple[list[dict[str, Any]], str]] = {
    # the sender as find_subscriptions names it -> (its three mails, the price it must find)
    "Netflix": (
        monthly(
            "Netflix <info@account.netflix.example>",
            "We received your payment",
            "Hi Tay,\n\nThanks for being a Netflix member. We've received your payment and "
            "your membership continues.\n\nPlan: Standard\n"
            "Price: $16.49/month (plus applicable taxes)\nPayment method: Visa •••• 4242\n"
            "Next billing date: {NEXT}\n\nWe're here to help if you need it.\n",
        ),
        "$16.49 a month",
    ),
    "Spotify": (
        monthly(
            "Spotify <no-reply@spotify.example>",
            "Your Spotify Premium receipt",
            "Receipt\n\nOrder number\n3847561029384\nOrder date\n{DATE}\n\nItem\n"
            "Spotify Premium Individual\n\nSubtotal\n$11.99\nTax (GST/HST)\n$1.56\nTotal\n$13.55\n"
            "\nPayment method\nVisa ending in 4242\n",
        ),
        "$13.55 a month",  # the total, not the subtotal above it
    ),
    "Apple": (
        monthly(
            "Apple <no_reply@email.apple.example>",
            "Your receipt from Apple.",
            "Receipt\n\nAPPLE ID\ntay@example.com\n\nBILLED TO\nVisa .... 4242\n\nDATE\n{DATE}\n\n"
            "ORDER ID\nMSD4X2K9LQ\n\nApp Store\n\niCloud+ with 200 GB of Storage\nMonthly\n"
            "Renews {NEXT}\n$3.99\n\nSubtotal $3.99\nGST/HST $0.20\n\nTOTAL $4.19\n",
        ),
        "$4.19 a month",
    ),
    "Google Play": (
        monthly(
            "Google Play <googleplay-noreply@google.example>",
            "Your Google Play Order Receipt from {DATE}",
            "Google Play\n\nThank you.\n\nYour subscription from Google LLC on Google Play has "
            "renewed.\n\nOrder number: GPA.3345-1122-9087-66231..3\nOrder date: {DATE}\n\n"
            "Item Price\nGoogle One (100 GB) (Google One) $2.79/month\n"
            "Auto-renewing subscription\n\nTax: $0.14\nTotal: $2.93/month\n",
        ),
        "$2.93 a month",
    ),
    "Amazon Prime": (
        monthly(
            "Amazon Prime <prime@amazon.example>",
            "Your Amazon Prime membership has renewed",
            # a sentence wrapped at 72 columns: the word and the amount on different lines
            "Hello Tay,\n\nThank you for being a Prime member. Your Prime membership has renewed\n"
            "for another month, and the payment method on file (Visa ending in 4242)\n"
            "has been charged the monthly membership fee plus applicable taxes:\nCDN$ 11.19.\n\n"
            "Next payment date: {NEXT}\n",
        ),
        "CA$11.19 a month",
    ),
    "Rogers": (
        monthly(
            "Rogers <notifications@rci.rogers.example>",
            "Your Rogers bill is ready",
            "Hi Tay,\n\nYour latest Rogers bill is now available in MyRogers.\n\n"
            "Account number: 2-3456-7890\nBill date: {DATE}\nAmount due: {AMT}\n"
            "Due date: {NEXT}\n",
            amounts=("$271.07", "$268.42", "$271.07"),
        ),
        "$271.07 a month",
    ),
    "Fido": (
        monthly(
            "Fido <fido@e.fido.example>",
            "Your Fido bill is ready",
            "Your bill is ready\n\nHi Tay, your Fido bill for the account ending in 4410 is "
            "ready to view.\n\nTotal due\n{AMT}\nPayment due date\n{NEXT}\n",
            amounts=("$68.25", "$71.40", "$68.25"),
        ),
        "$68.25 a month",
    ),
    "Anytime Fitness": (
        monthly(
            "Anytime Fitness <noreply@anytimefitness.example>",
            "Your monthly dues have been processed",
            "Hi Tay,\n\nThis is a confirmation that your monthly dues were collected today.\n\n"
            "Member number: 0045-221987\nDate: {DATE}\nMonthly dues: $49.99\nTax: $2.50\n"
            "Collected from: Chequing account ending 789\n",
        ),
        "$49.99 a month",
    ),
    "Adobe": (
        monthly(
            "Adobe <message@adobe.example>",
            "Thank you for your payment",
            "Thanks for your payment\n\nHi Tay, we've received your payment for your Creative "
            "Cloud membership.\n\nOrder number: 7012345678\nBilling period: {DATE} to {NEXT}\n"
            "Amount: CA$78.39 (includes taxes)\nPayment method: Visa ending in 4242\n",
        ),
        "CA$78.39 a month",
    ),
    "Microsoft": (
        monthly(
            "Microsoft <microsoft-noreply@microsoft.example>",
            "Your Microsoft 365 Personal subscription has been renewed",
            "Thanks for your continued subscription\n\nHi Tay,\n\nYour subscription for "
            "Microsoft 365 Personal has been renewed. Here are the details.\n\n"
            "Subscription: Microsoft 365 Personal\nRenewal date: {DATE}\n"
            "Plan price: CAD $11.00/month plus applicable taxes\nPayment method: Visa **4242\n"
            "Next billing date: {NEXT}\n",
        ),
        "CA$11.00 a month",
    ),
    "Dropbox International Unlimited Company": (
        monthly(
            '"service@paypal.example" <service@paypal.example>',
            "You sent an automatic payment to Dropbox International Unlimited Company",
            "Hello, Tay Tester\n\nYou sent an automatic payment of $15.81 CAD to Dropbox "
            "International Unlimited Company.\n\nTransaction ID: 4GX12345AB678901C\n"
            "Transaction date: {DATE}\n\nPaid with\nVisa x-4242\n$15.81 CAD\n",
        ),
        "CA$15.81 a month",
    ),
    "Linear Orbit": (
        monthly(
            "Linear <receipts+acct_1Abc@stripe.example>",
            "Your receipt from Linear Orbit, Inc. #2123-4567",
            "Receipt from Linear Orbit, Inc.\n$12.00\nPaid {DATE}\n\nReceipt number 2123-4567\n"
            "Payment method Visa - 4242\n\nLinear Standard\nQty 1\n$12.00\n\nSubtotal\n$12.00\n"
            "Total\n$12.00\nAmount paid\n$12.00\n",
        ),
        "$12.00 a month",
    ),
}


def test_twelve_real_senders_are_found_and_the_three_live_ones_stay_out():
    """After the first fix: Amazon Prime (a sentence wrapped at 72 columns) and Microsoft 365
    ("Plan price: CAD $11.00/month") were found before it and not after; Netflix ("Price:
    $16.49/month") and a gym ("Monthly dues: $49.99") were never found. All twelve are now,
    each alone and all together, and the newsletters and the single ride still are not."""
    for name, (mails, price) in TWELVE.items():
        [sub] = find_subscriptions(mails, now())
        assert (sub.service, sub.price(), sub.charges) == (name, price, 3), name
    everything = [m for mails, _ in TWELVE.values() for m in mails]
    live = [startup_club(), the_gist(), lyft()]
    subs = find_subscriptions(everything + live, now())
    assert {s.service for s in subs} == set(TWELVE)
    assert not {"The Startup Club", "The GIST", "Lyft Ride"} & {s.service for s in subs}
    assert {s.currency for s in subs} == {"CAD"}  # "$" beside CA$ and CDN$ is the same money
    title, body, _lines = subscriptions_report(subs)
    assert title == "CA$554.86 a month in subscriptions" and body.startswith("12 recurring")
    assert find_subscriptions(live, now()) == []


ACME = (
    "Hi Tay,\n\nThanks for being a customer. Here are the details of your latest charge.\n\n"
    "Date: {DATE}\nPlan: Pro\n%s\nPayment method: Visa ending in 4242\n\n"
    "Questions? Reply to this email.\n"
)


@pytest.mark.parametrize(
    "line",
    [
        "Price: $9.99/month",
        "Plan price: $9.99",
        "Charge: $9.99",
        "Monthly fee: $9.99",
        "Membership dues: $9.99",
        "Subscription: $9.99",
        "Pro plan (monthly)    $9.99",
        "Pro plan (monthly) $9.99",  # as Gmail's text arrives, runs of spaces squeezed
        "Cost: $9.99",
        "Your card was debited $9.99.",
        "Price\n$9.99",
        "Fee\n$9.99",
        "Amount charged:\n\n$9.99",
        "Total (CAD)\n$9.99",
        "$9.99",
        "Transaction: $9.99",
        # the word and the amount on different lines of a wrapped sentence
        "We charged your Visa ending in 4242 the monthly fee for your Pro plan,\n"
        "which comes to $9.99 including tax.",
        "Your payment method has been charged for another month of service in the\n"
        "amount of $9.99.",
    ],
)
def test_the_ways_a_receipt_writes_its_amount(line):
    """After the first fix only "Total", "paid", "charged" and "due" lines counted, so a
    receipt that said "Price: $9.99/month" had no amount and its subscription was gone."""
    [sub] = find_subscriptions(
        monthly("Acme <billing@acme.example>", "Your receipt from Acme", ACME % line), now()
    )
    assert (sub.service, sub.price(), sub.charges) == ("Acme", "$9.99 a month", 3)


def test_a_price_that_is_only_mentioned_is_still_not_an_amount():
    assert amount_in("Go Pro: $485/year gets you every event.") is None
    assert amount_in("Tickets start at $45.") is None and amount_in("Seats from $45") is None
    assert amount_in("Save $20 with the annual plan") is None
    assert amount_in("Subtotal: $11.99\nTax: $1.56\nShipping: $4.00") is None  # parts, no total
    # A whole receipt on one line (a table without its line breaks): the total, not the price.
    assert amount_in("Plan Standard Price $16.49 Tax $0.82 Total $17.31 Visa 4242") == (17.31, "$")
    assert amount_in("Total: CDN$ 36.74") == (36.74, "CAD")
    assert amount_in("Plan price: CAD $11.00/month") == (11.0, "CAD")
    assert amount_in("You sent a payment of $15.81 CAD to Dropbox") == (15.81, "CAD")
    assert amount_in("Amount: $15.49 USD") == (15.49, "$")


MEMBERS = "Hi Tay,\n\nYour membership continues. We charged your card on file.\n\nDate: {DATE}\nTotal: $9.99\nCard: Visa 4242\n"  # fmt: skip


@pytest.mark.parametrize(
    ("sender", "subject"),
    [
        ("Crave <noreply@crave.example>", "Your monthly charge"),
        ("Uber One <noreply@uber.example>", "Your Uber One transaction"),
        ("ChatGPT <noreply@tm.openai.example>", "ChatGPT Plus - Thank you"),
        ("Notion <team@makenotion.example>", "Your Notion Plus plan"),
        ("Patreon <bingo@patreon.example>", "Your pledge to Some Creator was processed"),
        ("Duolingo <hello@duolingo.example>", "Super Duolingo: you're all set for another month"),
        ("Hydro <ebill@hydro.mb.example>", "Your Manitoba Hydro eBill is available"),
    ],
)
def test_a_subject_that_says_nothing_of_money_over_a_total_line(sender, subject):
    """After the first fix a mail was a receipt only if its sender or subject said so; these
    say "Total: $9.99" in the text and were all dropped."""
    [sub] = find_subscriptions(monthly(sender, subject, MEMBERS), now())
    assert (sub.price(), sub.charges) == ("$9.99 a month", 3)


def one(sender: str, subject: str, body: str, days: float = 5) -> list[dict[str, Any]]:
    return monthly(sender, subject, body, days=(days,))


def test_a_yearly_plan_has_one_receipt_and_is_still_found():
    """After the first fix one charge needed its "yearly" above the total. An annual plan has
    one receipt in the whole scan, and most say how often under the total."""
    found = {}
    for name, sender, subject, body in (
        (
            "Microsoft",
            "Microsoft <microsoft-noreply@microsoft.example>",
            "Your Microsoft 365 Family subscription has been renewed",
            "Your subscription has been renewed.\n\nSubscription: Microsoft 365 Family\n"
            "Renewal date: {DATE}\nTotal: CAD $109.00 for 1 year\nPayment method: Visa **4242\n",
        ),
        (
            "Namecheap",
            "Namecheap <support@namecheap.example>",
            "Namecheap Renewal Order Confirmation",
            "Thank you for your order.\n\nDomain Renewal example.com\nSubtotal: $16.98\n"
            "Total: $17.16\n\nYour domain will renew automatically every year unless "
            "auto-renew is turned off.\n",
        ),
        (
            "Dropbox",
            "Dropbox <no-reply@dropbox.example>",
            "Your Dropbox Plus receipt",
            "Thanks for your payment.\n\nDropbox Plus\nTotal: $131.88\nPaid with Visa 4242\n\n"
            "Your plan renews yearly. Next billing date: November 3, 2027.\n",
        ),
        (
            "Crunchyroll",
            "Crunchyroll <billing@crunchyroll.example>",
            "Your Crunchyroll receipt",
            "Thanks for subscribing!\n\nFan plan\nTotal: $7.99\n\n"
            "You'll be billed monthly until you cancel.\n",
        ),
    ):
        [sub] = find_subscriptions(one(sender, subject, body), now())
        found[name] = (sub.price(), sub.charges)
    assert found == {
        "Microsoft": ("CA$109.00 a year", 1),
        "Namecheap": ("$17.16 a year", 1),
        "Dropbox": ("$131.88 a year", 1),
        "Crunchyroll": ("$7.99 a month", 1),
    }
    # "Save with an annual plan" further down a monthly receipt doesn't make it yearly.
    crave = one(
        "Crave <billing@crave.example>",
        "Your Crave receipt",
        "Thanks for your payment.\n\nCrave Premium\nTotal: $22.00\n"
        "Your plan renews monthly until you cancel.\n\nSave 15% with an annual plan.\n",
    )
    assert find_subscriptions(crave, now())[0].price() == "$22.00 a month"


def test_a_first_receipt_that_never_says_how_often_is_listed_but_not_counted():
    """A "membership receipt" with one charge and no "monthly" anywhere: after the first fix
    it was not listed at all; before it, it was counted as $5.00 every month. Now it is
    listed as charged once, and the monthly total leaves it out and says so."""
    medium = one(
        "Medium <noreply@medium.example>",
        "Your Medium membership receipt",
        "Thanks for being a member.\n\nMedium membership\nAmount paid: $5.00\nDate: {DATE}\n",
    )
    [sub] = find_subscriptions(medium, now())
    assert (sub.cadence, sub.price(), sub.monthly) == ("once", "$5.00 once so far", 0.0)
    assert subscriptions_report([sub])[0] == "1 subscription charged once so far"
    subs = find_subscriptions(medium + netflix(), now())
    title, body, lines = subscriptions_report(subs)
    assert title == "$15.49 a month in subscriptions" and lines[-1].startswith("Medium: $5.00 once")
    assert "1 of them has been charged once and doesn't say how often" in body
    # One ride is still no subscription, whatever is advertised under its total.
    assert find_subscriptions([lyft()], now()) == []


def test_invoices_whose_amount_changes_every_month_are_found():
    """After the first fix a rhythm needed about the same amount each time, so a cloud bill
    ($12.40, $18.75, $9.10) and pay-as-you-go receipts ($10, $25, $10) were not found."""
    cloud = monthly(
        "DigitalOcean <no-reply@digitalocean.example>",
        "Your DigitalOcean invoice for {DATE} is available",
        "Hi Tay,\n\nYour invoice is now available for team My Team.\n\nTotal: {AMT}\n",
        amounts=("$12.40", "$18.75", "$9.10"),
    )
    api = monthly(
        "OpenAI <noreply@tm.openai.example>",
        "Your receipt from OpenAI, LLC #2764-1182",
        "Receipt from OpenAI, LLC\n\nAmount paid\n{AMT}\nDate paid\n{DATE}\n",
        amounts=("$10.00", "$25.00", "$10.00"),
    )
    assert [s.line().split(",")[0] for s in find_subscriptions(cloud + api, now())] == [
        "OpenAI: $10.00 a month",
        "DigitalOcean: $9.10 a month",
    ]
    # Two receipts of different amounts are two purchases, and so are three at odd gaps.
    assert find_subscriptions(cloud[1:], now()) == []
    odd = monthly(
        "Daily Grind <receipts@dailygrind.example>",
        "Your Daily Grind receipt",
        "Thanks for your purchase.\nTotal: {AMT}\n",
        amounts=("$4.10", "$9.75", "$6.20"),
        days=(70, 31, 4),
    )
    assert find_subscriptions(odd, now()) == []


def test_orders_and_rides_a_month_apart_are_not_subscriptions():
    """Found by the review, and older than the fix: two orders a month apart for about the
    same amount were "Best Buy: $27.50 a month"."""
    orders = monthly(
        "Best Buy <orders@bestbuy.example>",
        "Your order confirmation",
        "Thanks for your order.\n\nUSB-C cable\nOrder total: {AMT}\n",
        amounts=("$24.99", "$27.50"),
        days=(35, 5),
    )
    rides = monthly(
        "Uber Receipts <noreply@uber.example>",
        "Your Tuesday morning trip with Uber",
        "Thanks for riding, Tay\n\nTotal {AMT}\n",
        amounts=("$18.40", "$19.10", "$18.75"),
    )
    assert find_subscriptions(orders + rides, now()) == []


def test_a_card_statement_is_not_a_subscription_and_a_bill_is():
    """After the first fix "Your TD credit card statement is ready" with "Statement balance:
    $1,602.90" was a subscription of $1,602.90 a month: a statement counted as a bill, and
    its balance as the amount."""
    statement = monthly(
        "Tidewater Bank <noreply@tidewater.example>",
        "Your Tidewater credit card statement is ready",
        "Your statement is ready to view online.\n\nStatement date: {DATE}\n"
        "Statement balance: {AMT}\nMinimum payment: $10.00\nPayment due date: {NEXT}\n\n"
        "Interest will be charged on any unpaid balance.\n",
        amounts=("$1,482.13", "$1,390.55", "$1,602.90"),
    )
    bank = monthly(
        "Tidewater Bank <ibanking@tidewater.example>",
        "Your eStatement is ready",
        "Your statement for account ending 5521 is ready.\n\nClosing balance: {AMT}\n",
        amounts=("$3,210.55", "$3,400.00", "$2,980.10"),
    )
    assert find_subscriptions(statement + bank, now()) == []
    assert amount_in("Statement balance: $1,602.90\nMinimum payment: $10.00") is None
    assert amount_in("Balance due: $141.07") == (141.07, "$")
    water = monthly(
        "City of Winnipeg <noreply@winnipeg.example>",
        "Your water bill is ready",
        "Amount due: {AMT}\nDue date: {NEXT}\n",
        amounts=("$141.07", "$152.42", "$139.07"),
    )
    assert find_subscriptions(water, now())[0].price() == "$139.07 a month"


def test_the_scan_asks_gmail_for_what_the_gate_accepts():
    """After the first fix the gate took "Your bill is ready / Amount due", but the search
    never asked for "bill" or "amount due", so such a mail was fetched only by luck."""
    from pyxi.mailwatch import subscriptions_query

    q = subscriptions_query()
    assert q.startswith("newer_than:400d -category:promotions {")
    for word in ("receipt", "invoice", "bill", "renewal", "payment", '"amount due"', "membership"):
        assert f" {word} " in q.replace("{", " ").replace("}", " "), word
    assert "statement" not in q


def test_paypals_receipt_and_the_services_own_are_one_charge():
    """The same payment mailed by PayPal and, a day later, by the service: two charges a day
    apart broke the monthly rhythm."""
    both = []
    for d in MONTHS_BACK:
        both += monthly(
            "PayPal <service@paypal.example>",
            "Receipt for your payment to Bandcamp, Inc.",
            "You sent a payment of $12.00 USD to Bandcamp, Inc.\n\nTotal: $12.00 USD\n",
            days=(d,),
        )
        both += monthly(
            "Bandcamp <noreply@bandcamp.example>",
            "Your Bandcamp receipt",
            "Thanks for your payment.\n\nFan club, monthly\nTotal: $12.00 USD\n",
            days=(d - 1,),
        )
    [sub] = find_subscriptions(both, now())
    assert (sub.service, sub.price(), sub.charges) == ("Bandcamp", "$12.00 a month", 3)


# ---------------------------------------------------------------- Penny's month, after the review


def test_spending_whose_subject_says_nothing_of_money_is_still_spending():
    """After the first fix Penny's month kept one receipt of six, $39.99 of $590.44: tickets,
    a transit top-up, a hotel, a donation and parking, none with a money word in its subject."""
    month = [
        mail(
            "Ticketmaster <customer_support@email.ticketmaster.example>",
            "You Got Tickets To Winnipeg Jets vs. Oilers",
            "You're in!\n\nSec 310, Row 4, Seats 5-6\n\nTickets: $120.00\nFees: $21.60\n"
            "Total: $141.60\nCharged to Visa 4242\n",
            on(4),
        ),
        mail(
            "PRESTO <no-reply@prestocard.example>",
            "Autoload Confirmation",
            "Your card ending 1123 was loaded.\n\nAmount charged: $20.00\n",
            on(6),
        ),
        mail(
            "Fairmont Hotels <reservations@fairmont.example>",
            "Your booking is confirmed",
            "Thank you for booking with us.\n\nRoom: $289.00\nTaxes: $43.35\n"
            "Total paid: $332.35\n",
            on(8),
        ),
        mail(
            "Winnipeg Humane Society <info@humanesociety.example>",
            "Thank you for your gift",
            "Your generosity matters.\n\nDonation amount: $50.00\n"
            "An official tax receipt is attached.\n",
            on(9),
        ),
        mail(
            "HonkMobile <no-reply@honkmobile.example>",
            "Your parking session",
            "Zone 4412\nTotal: $6.50\nPaid with Visa 4242\n",
            on(10),
        ),
        mail(
            "Amazon.ca <auto-confirm@amazon.example>",
            "Your Amazon.ca order of Kettle",
            "Order #702-1111111-2222222\nOrder Total: $39.99\n",
            on(11),
        ),
        mail(
            "Manitoba Hydro <ebill@hydro.mb.example>",
            "Your Manitoba Hydro eBill is available",
            "Your bill is ready.\n\nAmount due: $141.07\nDue date: November 5, 2026\n",
            on(22),
        ),
        # and the newsletter of the live fault, which must stay out
        dict(startup_club(), date=on(12)),
        dict(the_gist(), date=on(13)),
    ]
    got = spent(month)
    assert [(name, amount) for name, amount, _ in got] == [
        ("Ticketmaster", 141.60),
        ("PRESTO", 20.00),
        ("Fairmont Hotels", 332.35),
        ("Winnipeg Humane Society", 50.00),
        ("HonkMobile", 6.50),
        ("Amazon", 39.99),
        ("Manitoba Hydro", 141.07),
    ]
    assert round(sum(amount for _, amount, _ in got), 2) == 731.51
    assert dict((name, cat) for name, _, cat in got)["HonkMobile"] == "Transport"


def test_a_processed_return_is_a_refund_even_when_its_subject_says_return():
    """After the first fix a refund needed "refund" in its subject, and "Your return of ... has
    been processed" with "Refund total: $23.09" was not subtracted any more."""
    amazon = mail(
        "Amazon.ca <return@amazon.example>",
        "Your return of Anker USB-C Cable has been processed",
        "Hello Tay,\nWe've processed your return for order #702-1234567-7654321.\n\n"
        "Refund total: $23.09\nYour refund will be credited to your Visa within 3-5 days.\n",
        on(9),
    )
    uniqlo = mail(
        "Uniqlo <noreply@mail.uniqlo.example>",
        "We've received your return",
        "Hi Tay,\nYour return was received and your refund issued.\n\nRefund total\n$49.90\n"
        "Refunded to: Visa 4242\n",
        on(12),
    )
    # The fault before that one stays fixed: a returns policy under an order is no refund,
    # with "return" in the subject or without.
    order = mail(
        "Best Buy <orders@bestbuy.example>",
        "Thanks for your order (free returns for 30 days)",
        "Thanks for your order.\nOrder number: BBY01-806123456789\nOrder Total: $348.00\n\n"
        "Changed your mind? Send it back and your refund goes to the card you paid with.\n",
        on(5),
    )
    assert spent([amazon, uniqlo, order]) == [
        ("Best Buy", 348.0, "Shopping"),
        ("Amazon", -23.09, "Refunds"),
        ("Uniqlo", -49.9, "Refunds"),
    ]


def test_paypal_and_the_shop_mailing_one_payment_hours_apart_is_one_receipt():
    """After the first fix "the same charge mailed twice" meant within two hours; PayPal's
    receipt at 9 and the shop's own at 2 were two purchases of $12.00."""
    paypal = mail(
        "PayPal <service@paypal.example>",
        "Receipt for your payment to Bandcamp, Inc.",
        "You sent a payment of $12.00 USD to Bandcamp, Inc.\n\nTotal: $12.00 USD\n",
        on(21, 9),
    )
    shop = mail(
        "Bandcamp <noreply@bandcamp.example>",
        "Thank you for your purchase",
        "Thanks for your order.\n\nAlbum: Blue\nTotal: $12.00 USD\n",
        on(21, 14),
    )
    assert [(n, a) for n, a, _ in spent([paypal, shop])] == [("Bandcamp", 12.0)]
    assert [(n, a) for n, a, _ in spent([shop, dict(paypal, date=on(22, 9))])] == [("Bandcamp", 12.0)]
    # Two coffees five hours apart, both from the shop itself, are still two.
    assert len(spent([coffee(on(5, 8)), coffee(on(5, 13))])) == 2


def test_names_run_together_still_find_their_category():
    assert penny.categorise("HydroOne", "Your bill") == "Bills and utilities"
    assert penny.categorise("PrestoCard", "Load receipt") == "Transport"
    assert penny.categorise("Wirelesswave", "Receipt") == "Bills and utilities"
    assert penny.category_named("foods") == "Eating out"
    assert penny.category_named("rideshare") == "Transport"
    assert penny.categorise("Piano Lessons Co", "Your receipt") == "Other"  # still not Esso


# ---------------------------------------------------------------- paging, after the review


class FlakyGmail(PagedGmail):
    """Gmail on a busy day: some messages, or a page of the list, answer 429."""

    def __init__(self, mails, fail_ids=(), fail_page=0) -> None:
        super().__init__(mails)
        self.fail_ids, self.fail_page, self.pages = set(fail_ids), fail_page, 0

    async def __call__(self, method: str, url: str, **kw: Any) -> Any:
        if method == "GET" and url.endswith("/messages"):
            self.pages += 1
            if self.pages == self.fail_page:
                raise RuntimeError("Google is busy (429). Try again in a minute.")
        if method == "GET" and "/messages/" in url and url.rsplit("/", 1)[1] in self.fail_ids:
            raise RuntimeError("Google is busy (429). Try again in a minute.")
        return await super().__call__(method, url, **kw)


def flaky(mails, **kw) -> tuple[Any, FlakyGmail]:
    from pyxi.connectors.google import GoogleConnector

    g = GoogleConnector.__new__(GoogleConnector)
    fake = FlakyGmail(mails, **kw)
    g._api = fake  # type: ignore[method-assign]
    g._tz = "America/Vancouver"
    return g, fake


async def test_one_mail_that_cannot_be_fetched_does_not_lose_the_scan():
    """After the first fix a scan fetched 300 mails instead of 80, and one "busy" answer
    among them threw the whole scan away."""
    from pyxi.mailwatch import not_read

    receipts = [as_sent(m) for m in (*netflix(), *spotify(), *internet())]
    g, _ = flaky(receipts + newsletters(60), fail_ids={"m3", "m40"})  # two newsletters
    mails = await g.gmail_fetch("newer_than:400d", 300)
    assert len(mails) == 65 and mails.failed == 2 and not mails.cut
    assert not_read(mails) == "2 mails couldn't be read just now, so something may be missing."
    subs = find_subscriptions(mails, now())
    assert [s.service for s in subs] == ["Northline", "Netflix", "Spotify"]
    assert subscriptions_report(subs)[1].endswith("2 mails couldn't be read just now, so something may be missing.")
    # Not one mail could be read: that is a failure, said as one.
    g, _ = flaky(receipts, fail_ids={f"m{i}" for i in range(len(receipts))})
    with pytest.raises(RuntimeError, match="busy"):
        await g.gmail_fetch("newer_than:400d", 300)


async def test_a_page_of_the_list_that_fails_keeps_the_pages_before_it():
    g, _ = flaky(newsletters(250), fail_page=3)  # pages of 100: the third one fails
    mails = await g.gmail_fetch("newer_than:400d", 300)
    assert len(mails) == 200 and mails.cut and read_back_to(mails) is not None
    g, _ = flaky(newsletters(250), fail_page=1)  # nothing listed at all: the scan fails
    with pytest.raises(RuntimeError, match="busy"):
        await g.gmail_fetch("newer_than:400d", 300)


async def test_pennys_month_says_when_some_mail_could_not_be_read(settings, monkeypatch):
    monkeypatch.setattr(penny, "clock", lambda: NOW)
    brain, rec, _ = connected(settings, [])
    fake = FlakyGmail(many_coffees(30), fail_ids={"m4"})
    brain.connectors.google._api = fake  # type: ignore[method-assign]
    await specialists.handle(brain, "money", {"action": "summary", "month": "October"})
    [ready] = await until(rec, "job_ready")
    assert ready["ok"] and ready["title"] == "October: $290.00 spent"
    assert ready["body"].endswith("1 mail couldn't be read just now, so something may be missing.")


# ---------------------------------------------------------------- the trial of 12 November


def test_the_five_that_nobody_was_paying_for():
    """Live, 12 November: "which subscriptions am I paying for?" came back with five, all
    wrong, and "CA$2,576.27 a month". A gas bill that was only ready (and its reminder a week
    later, read as a charge every seven days), eight payments that had failed, food orders, a
    Black Friday advert, and three billing-failure notices. The mails here are made up to the
    same shapes."""
    gas = [
        mail(
            "Coastline Gas <ebill@coastlinegas.example>",
            "Your November 2026 Coastline Gas natural gas bill for account 1234567 is ready",
            "Your bill is ready to view.\nAmount due: $519.63\nDue date: November 27\n",
            ago(13),
        ),
        mail(
            "Coastline Gas <ebill@coastlinegas.example>",
            "Reminder: your Coastline Gas bill is due soon",
            "Your bill is due soon.\nAmount due: $519.63\n",
            ago(6),
        ),
    ]
    failed = [
        mail(
            "Tagmill <billing@tagmill.example>",
            "$29.00 payment to Tagmill.ai was unsuccessful again",
            "We tried to charge your card for your Tagmill subscription.\nAmount: $29.00\n",
            ago(d),
        )
        for d in (2, 9, 16, 23, 30, 37, 44, 51)
    ]
    orders = [
        mail(
            "DashDine <no-reply@dashdine.example>",
            "Order Confirmation for Tay from Corner Chicken",
            "Thanks for your order.\nTotal: $18.78\nA DashDine membership, billed monthly, saves on delivery.\n",
            ago(d),
        )
        for d in (8, 15, 22, 29, 36)
    ]
    advert = mail(
        "Strum School <news@strumschool.example>",
        "What $99 gets you this Black Friday",
        "Full Access: a year of lessons.\nTotal: $899.00\nAnnual membership, renews yearly.\n",
        ago(4),
    )
    notices = [
        mail(
            "Pixelworks <message@pixelworks.example>",
            f"NOTICE: {nth} billing failure",
            "We couldn't process the payment for your Pixelworks plan.\nAmount due: $7.83\n"
            "Your plan renews monthly.\n",
            ago(d),
        )
        for nth, d in (("Third", 8), ("Second", 15), ("First", 22))
    ]
    for mails in (gas, failed, orders, [advert], notices):
        assert find_subscriptions(mails, now()) == [], mails[0]["subject"]
    everything = [*gas, *failed, *orders, advert, *notices]
    found = find_subscriptions(everything, now())
    assert found == []
    title, body, lines = subscriptions_report(found)
    assert title == "No subscriptions found" and lines == []
    assert "only see what sends a receipt to this mailbox" in body
    # The real ones beside them are still found, and the total is theirs alone.
    found = find_subscriptions([*everything, *netflix(), *spotify()], now())
    assert [s.service for s in found] == ["Netflix", "Spotify"]
    title, body, _ = subscriptions_report(found)
    assert title == "$27.48 a month in subscriptions"
    assert "only see what sends a receipt to this mailbox" in body


def test_a_bill_that_comes_every_month_is_still_one_and_its_reminder_is_not_a_second():
    water = monthly(
        "City Water <noreply@citywater.example>",
        "Your water bill is ready",
        "Amount due: {AMT}\nDue date: {NEXT}\n",
        amounts=("$141.07", "$152.42", "$139.07"),
    )
    reminder = mail(
        "City Water <noreply@citywater.example>",
        "Reminder: your water bill is due",
        "Amount due: $141.07\n",
        water[0]["date"] + timedelta(days=9),
    )
    [sub] = find_subscriptions([*water, reminder], now())
    assert (sub.cadence, sub.charges) == ("monthly", 3)


def test_a_payment_that_failed_or_an_advert_is_not_spending_either():
    from pyxi.mailwatch import charge_in

    assert charge_in("Tagmill <billing@tagmill.example>", "Your payment was declined", "Amount: $29.00\n") is None
    assert charge_in("Shop <billing@shop.example>", "We couldn't process your payment", "Total: $12.00\n") is None
    assert charge_in("Strum School <news@strumschool.example>", "Black Friday: 50% off", "Total: $99.00\n") is None
    # A receipt is a receipt, whatever the season.
    assert charge_in("Shop <billing@shop.example>", "Your receipt for the Black Friday sale", "Total: $99.00\n")
    assert charge_in("Tagmill <billing@tagmill.example>", "Your Tagmill receipt", "Amount paid: $29.00\n")
