"""Money in your mail: the forgotten-subscription hunter and the return-window keeper.

Realistic receipts and order emails in a fake Gmail (no network, never the real mailbox). Both
features work in code, so these tests also pin down exactly what they find.
"""

from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from pyxi.mailwatch import (
    amount_in,
    find_orders,
    find_subscriptions,
    merchant,
    parse_date,
    reminder_day,
    subscriptions_report,
)
from test_brain_and_server import make_brain
from test_connectors_and_docs import Script

TZ = "America/Vancouver"


def now() -> datetime:
    return datetime.now(ZoneInfo(TZ))


def ago(days: float) -> datetime:
    return now() - timedelta(days=days)


def ahead(days: int) -> datetime:
    return now() + timedelta(days=days)


def receipts() -> list[dict[str, Any]]:
    netflix = (
        "Hi Tay,\nThanks for your payment. Your membership continues.\nPlan: Standard\n"
        "Amount: $15.49 USD\nNext billing date: see your account.\n"
    )
    return [
        *(
            {
                "from": "Netflix <info@account.netflix.com>",
                "subject": "Your payment was received",
                "body": netflix,
                "html": '<p>Thanks!</p><a href="https://www.netflix.com/youraccount">Account</a> '
                '<a href="https://www.netflix.com/unsubscribe?u=1">Unsubscribe</a>',
                "date": ago(d),
            }
            for d in (3, 33, 63)
        ),
        *(
            {
                "from": "PayPal <service@paypal.com>",
                "subject": "Receipt for your payment to Spotify USA Inc",
                "body": "You sent an automatic payment of $11.99 USD to Spotify USA Inc.\n"
                "This is a recurring payment for Spotify Premium.",
                "date": ago(d),
            }
            for d in (12, 42)
        ),
        {
            "from": "Adobe <message@adobe.com>",
            "subject": "Your Adobe invoice",
            "body": "Creative Cloud Photography plan\nAnnual plan, billed yearly\n"
            "Subtotal US$108.98\nTax US$10.90\nTotal US$119.88\n",
            "html": '<a href="https://account.adobe.com/plans">Manage plan</a>',
            "date": ago(70),
        },
        {
            "from": "OpenAI <noreply@tm.openai.com>",
            "subject": "Your ChatGPT Plus subscription receipt",
            "body": "Receipt\nChatGPT Plus subscription, monthly\nSubtotal $20.00\nTax $0.00\n"
            "Total $20.00\nAmount paid $20.00",
            "date": ago(5),
        },
        *(
            {
                "from": "FitLife Gym Billing <billing@fitlifegym.com>",
                "subject": "Membership payment receipt",
                "body": "Your monthly membership was renewed.\nCharged: $39.99",
                "html": '<a href="https://fitlifegym.com/account/membership">Manage your '
                'membership</a> <a href="https://fitlifegym.com/email/unsubscribe">unsubscribe</a>',
                "date": ago(d),
            }
            for d in (10, 40)
        ),
        *(
            {
                "from": "Headspace <help@headspace.com>",
                "subject": "Your Headspace receipt",
                "body": "Thanks for your payment. Headspace Monthly subscription. Total: $12.99",
                "date": ago(d),
            }
            for d in (8, 38)
        ),
        # Cancelled since its last charge: not a forgotten subscription.
        {
            "from": "Hulu <hulu@hulumail.com>",
            "subject": "Your Hulu receipt",
            "body": "Subscription renewed. Total: $7.99 a month",
            "date": ago(20),
        },
        {
            "from": "Hulu <hulu@hulumail.com>",
            "subject": "Your subscription has been canceled",
            "body": "We're sorry to see you go.",
            "date": ago(15),
        },
        # A one-off purchase, a promotion and a subscription that stopped months ago.
        {
            "from": "Amazon.com <auto-confirm@amazon.com>",
            "subject": 'Your Amazon.com order of "Sony WH-1000XM5"',
            "body": "Thank you for your order.\nOrder Total: $348.00",
            "date": ago(12),
        },
        {
            "from": "Disney+ <disneyplus@mail.disneyplus.com>",
            "subject": "Get Disney+ for $7.99/month",
            "body": "Limited time: subscribe now for $7.99 a month.",
            "date": ago(2),
        },
        {
            "from": "Dropbox <no-reply@dropbox.com>",
            "subject": "Your Dropbox receipt",
            "body": "Dropbox Plus, monthly subscription. Total: $11.99",
            "date": ago(130),
        },
    ]


def orders() -> list[dict[str, Any]]:
    amazon = "Order #112-3456789-1234567"
    return [
        {
            "from": "Amazon.com <auto-confirm@amazon.com>",
            "subject": 'Your Amazon.com order of "Sony WH-1000XM5 Headphones"',
            "body": f"Thanks for your order. {amazon}\nOrder Total: $348.00",
            "date": ago(12),
        },
        {
            "from": "Amazon.com <shipment-tracking@amazon.com>",
            "subject": 'Your Amazon.com order of "Sony WH-1000XM5 Headphones" has shipped',
            "body": amazon,
            "date": ago(10),
        },
        {
            "from": "Amazon.com <order-update@amazon.com>",
            "subject": 'Delivered: "Sony WH-1000XM5 Headphones"',
            "body": f"Your package was delivered. {amazon}",
            "date": ago(8),
        },
        {
            "from": "Best Buy <BestBuyInfo@emailinfo.bestbuy.com>",
            "subject": "Thanks for your order",
            "body": "Order number: BBY01-806123456789\nItem: Logitech MX Master 3S\n"
            f"Return by: {ahead(5):%b} {ahead(5).day}, {ahead(5).year}",
            "date": ago(10),
        },
        {
            "from": "ZARA <noreply@zara.com>",
            "subject": "Your order has shipped",
            "body": "Order 51234567890\nItem: Wool coat\n"
            f"You can return items until {ahead(2):%A}, {ahead(2):%B} {ahead(2).day}.",
            "date": ago(20),
        },
        {
            "from": "Etsy <transaction@etsy.com>",
            "subject": "Your order has shipped",
            "body": "Order #3141592653\nItem: Handmade mug",
            "date": ago(3),
        },
        {
            "from": "Target <orders@oe.target.com>",
            "subject": "Thanks for your order",
            "body": "Order #902000111222\nItem: Desk lamp",
            "date": ago(100),
        },
        {
            "from": "Walmart <help@walmart.com>",
            "subject": "Thanks for your order",
            "body": "Order #2000123-45678\nItem: Air fryer",
            "date": ago(9),
        },
        {
            "from": "Walmart <help@walmart.com>",
            "subject": "Your refund has been issued",
            "body": "Your refund for order #2000123-45678 has been issued.",
            "date": ago(4),
        },
    ]


class FakeGmail:
    def __init__(self, mails: list[dict[str, Any]]) -> None:
        self.mails = sorted(mails, key=lambda m: m["date"], reverse=True)
        for i, m in enumerate(self.mails):
            m["id"] = f"m{i}"
        self.queries: list[str] = []
        self.sent: list[Any] = []

    def _message(self, m: dict[str, Any]) -> dict[str, Any]:
        enc = lambda s: base64.urlsafe_b64encode(s.encode()).decode()  # noqa: E731
        parts = [{"mimeType": "text/plain", "body": {"data": enc(m["body"])}}]
        if m.get("html"):
            parts.append({"mimeType": "text/html", "body": {"data": enc(m["html"])}})
        return {
            "id": m["id"],
            "internalDate": str(int(m["date"].timestamp() * 1000)),
            "payload": {
                "mimeType": "multipart/alternative",
                "headers": [
                    {"name": "From", "value": m["from"]},
                    {"name": "Subject", "value": m["subject"]},
                ],
                "parts": parts,
            },
        }

    async def __call__(self, method: str, url: str, **kw: Any) -> Any:
        if url.endswith("/settings/timezone"):
            return {"value": TZ}
        if method == "GET" and url.endswith("/messages"):
            self.queries.append(kw["params"]["q"])
            return {"messages": [{"id": m["id"]} for m in self.mails][: kw["params"]["maxResults"]]}
        if method == "GET" and "/messages/" in url:
            mid = url.rsplit("/", 1)[1]
            return self._message(next(m for m in self.mails if m["id"] == mid))
        if method == "POST":
            self.sent.append(kw)
            raise AssertionError("nothing is ever sent")
        raise AssertionError(f"unexpected {method} {url}")


def connected(settings, mails, llm=None):
    brain, rec = make_brain(settings, llm=llm)
    g = brain.connectors.google
    g.token_path.parent.mkdir(parents=True, exist_ok=True)
    g.token_path.write_text("{}", encoding="utf-8")
    fake = FakeGmail(mails)
    g._api = fake  # type: ignore[method-assign]
    return brain, rec, fake


async def fetched(mails) -> list[dict[str, Any]]:
    from pyxi.connectors.google import GoogleConnector

    g = GoogleConnector.__new__(GoogleConnector)
    g._api = FakeGmail(mails)  # type: ignore[method-assign]
    g._tz = TZ
    return await g.gmail_fetch("newer_than:400d", 100)


async def until(rec, kind: str, n: int = 1) -> list[dict[str, Any]]:
    for _ in range(150):
        if len(rec.of(kind)) >= n:
            break
        await asyncio.sleep(0.02)
    return rec.of(kind)


# ---------------------------------------------------------------- reading receipts


def test_the_charged_amount_beats_subtotals_and_tax():
    assert amount_in("Subtotal US$108.98\nTax US$10.90\nTotal US$119.88") == (119.88, "$")
    assert amount_in("Plan: Standard\nAmount: $15.49 USD") == (15.49, "$")
    assert amount_in("Total: 12,99 kr") is None
    assert amount_in("Montant total: 9.99 EUR") == (9.99, "EUR")
    assert amount_in("Order Total: £1,249.00") == (1249.0, "GBP")


def test_who_the_money_went_to():
    assert merchant("FitLife Gym Billing <billing@fitlifegym.com>") == "FitLife Gym"
    assert merchant("<no-reply@dropbox.com>") == "Dropbox"
    assert (
        merchant("PayPal <service@paypal.com>", "Receipt for your payment to Spotify USA Inc")
        == "Spotify"
    )


def test_dates_as_stores_write_them():
    ref = datetime(2026, 11, 7, tzinfo=ZoneInfo(TZ))
    assert parse_date("Nov 12, 2026", ref).date().isoformat() == "2026-11-12"
    assert parse_date("Monday, November 9", ref).date().isoformat() == "2026-11-09"
    assert parse_date("2nd December", ref).date().isoformat() == "2026-12-02"
    assert parse_date("January 3", ref).date().isoformat() == "2027-01-03"  # the next one
    assert parse_date("11/30/2026", ref).date().isoformat() == "2026-11-30"
    assert parse_date("soon", ref) is None


async def test_the_hunter_finds_what_they_forgot_and_skips_the_rest():
    subs = find_subscriptions(await fetched(receipts()), now())
    by = {s.service: s for s in subs}
    assert list(by) == ["FitLife Gym", "OpenAI", "Netflix", "Headspace", "Spotify", "Adobe"]
    assert by["Netflix"].price() == "$15.49 a month" and by["Netflix"].charges == 3
    assert by["Adobe"].cadence == "yearly" and round(by["Adobe"].monthly, 2) == 9.99
    assert by["Spotify"].amount == 11.99 and by["Spotify"].cadence == "monthly"
    # Cancel links: the receipt's own "Manage" link, never its "Unsubscribe" (that's emails).
    assert by["FitLife Gym"].cancel_url == "https://fitlifegym.com/account/membership"
    assert by["FitLife Gym"].cancel_from == "email"
    assert by["Netflix"].cancel_url == "https://www.netflix.com/cancelplan"
    assert by["Adobe"].cancel_url == "https://account.adobe.com/plans"
    assert by["Headspace"].cancel_url is None  # Scout finds that one
    # Hulu was cancelled, Amazon was one order, Disney+ was an ad, Dropbox stopped in summer.
    assert not {"Hulu", "Amazon", "Disney+", "Dropbox"} & set(by)
    title, body, lines = subscriptions_report(subs)
    assert title == "$110.45 a month in subscriptions"
    assert lines[0].startswith("FitLife Gym: $39.99 a month, last charged")
    assert "cancel" in body and "—" not in title + body + "".join(lines)  # no em dashes


# ---------------------------------------------------------------- the subscription errand


async def test_finding_subscriptions_is_quiet_in_the_tray_and_one_card(settings):
    llm = Script([("subscriptions", {"action": "find"}), ("say", {"text": "On it."})])
    brain, rec, gmail = connected(settings, receipts(), llm)
    await brain.turn("find subscriptions I forgot about")
    [ready] = await until(rec, "job_ready")
    assert [s["text"] for s in rec.of("say")] == ["On it."]
    assert ready["ok"] and ready["title"] == "$110.45 a month in subscriptions"
    assert ready["changes"][0].startswith("FitLife Gym")
    jobs = [e["job"] for e in rec.of("job")]
    assert any(j["task"].startswith("Looking for subscriptions") for j in jobs)  # the tray
    assert jobs[-1]["status"] == "done"
    assert "newer_than:400d" in gmail.queries[0] and not gmail.sent
    assert brain.turns.spent_today() == 0  # no model call for the scan itself


async def cancel(settings, service: str, allow: bool):
    llm = Script(
        [("subscriptions", {"action": "find"}), ("say", {"text": "On it."})],
        [("subscriptions", {"action": "cancel", "service": service}), ("say", {"text": "On it."})],
    )
    brain, rec, _gmail = connected(settings, receipts(), llm)
    started: list[str] = []

    async def fake_start(agent, task, **kw):
        started.append(f"{agent}: {task}")

    brain.jobs.start = fake_start  # type: ignore[method-assign]
    await brain.turn("what subscriptions am I paying for?")
    await until(rec, "job_ready")
    await brain.turn(f"cancel {service}")
    [ask] = await until(rec, "confirm")
    brain.hub.resolve(ask["request_id"], allow)
    await asyncio.sleep(0.1)
    return ask, rec, started


async def test_cancel_opens_the_page_only_after_approval(settings):
    ask, rec, started = await cancel(settings, "netflix", True)
    assert ask["action"] == "Open the cancel page for Netflix"
    assert "$15.49 a month" in ask["reason"] and "nothing is cancelled for you" in ask["reason"]
    assert [a["value"] for a in rec.of("action")] == ["https://www.netflix.com/cancelplan"]
    assert rec.of("job_ready")[-1]["title"] == "Netflix's cancel page is open" and not started


async def test_the_receipts_own_link_is_used(settings):
    ask, rec, _ = await cancel(settings, "my gym membership", True)
    assert ask["action"] == "Open the cancel page for FitLife Gym"
    assert "the cancel link in your receipt" in ask["reason"]
    assert [a["value"] for a in rec.of("action")] == ["https://fitlifegym.com/account/membership"]


async def test_no_known_page_means_scout_finds_it_without_cancelling(settings):
    _, rec, started = await cancel(settings, "headspace", True)
    [task] = started
    assert task.startswith("scout: ") and "Do NOT press cancel" in task
    assert not rec.of("action")


async def test_saying_no_opens_nothing(settings):
    _, rec, started = await cancel(settings, "netflix", False)
    assert not rec.of("action") and not started


# ---------------------------------------------------------------- returns


async def test_return_windows_from_the_email_or_the_usual_policy():
    found = find_orders(await fetched(orders()), now())
    by = {o.store: o for o in found}
    # Walmart was refunded, Target's 90 days are over.
    assert list(by) == ["ZARA", "Best Buy", "Amazon", "Etsy"]
    assert by["ZARA"].return_by == ahead(2).date().isoformat()
    assert by["Best Buy"].return_by == ahead(5).date().isoformat()
    assert (
        by["Best Buy"].basis == "the email says so"
        and by["Best Buy"].item == "Logitech MX Master 3S"
    )
    # Amazon: the usual 30 days, counted from the delivery email (8 days ago).
    assert by["Amazon"].return_by == (ago(8) + timedelta(days=30)).date().isoformat()
    assert by["Amazon"].item == "Sony WH-1000XM5 Headphones" and by["Amazon"].basis.startswith(
        "usual"
    )
    assert by["Etsy"].return_by is None  # no stated window, no store policy: nothing invented
    assert "usual policy" in by["Amazon"].line(now())


def test_reminders_land_a_few_days_before():
    from pyxi.mailwatch import Order

    o = Order(store="Amazon", item="x", order_no=None, ordered="2026-11-01", return_by="2026-11-20")
    t = datetime(2026, 11, 7, 12, tzinfo=ZoneInfo(TZ))
    assert reminder_day(o, t) == datetime(2026, 11, 17, 10, tzinfo=ZoneInfo(TZ))
    o.return_by = "2026-11-09"  # too close for three days' notice: the day before
    assert reminder_day(o, t) == datetime(2026, 11, 8, 10, tzinfo=ZoneInfo(TZ))
    o.return_by = "2026-11-07"
    assert reminder_day(o, t) is None  # the card they're reading is the reminder


async def test_what_can_i_still_return(settings):
    llm = Script([("returns", {}), ("say", {"text": "On it."})])
    brain, rec, _ = connected(settings, orders(), llm)
    await brain.turn("what can I still return?")
    [ready] = await until(rec, "job_ready")
    assert ready["title"] == "3 orders you can still return"
    assert ready["changes"][0].startswith("ZARA, Wool coat: return by")
    reminders = [r for r in brain.routines.items if r.kind == "remind"]
    assert {r.key for r in reminders} == {
        "return:zara:51234567890",
        "return:best buy:BBY01-806123456789",
        "return:amazon:112-3456789-1234567",
    }
    assert any(r.kind == "returns" for r in brain.routines.items)  # the daily check
    # Asking again sets no duplicate reminders.
    await brain.turn("what can I still return?")
    await until(rec, "job_ready", 2)
    assert len([r for r in brain.routines.items if r.kind == "remind"]) == 3


async def test_the_reminder_and_the_daily_check_need_no_model(settings):
    llm = Script([("returns", {}), ("say", {"text": "On it."})])
    brain, rec, _ = connected(settings, orders(), llm)
    await brain.turn("keep track of my returns")
    await until(rec, "job_ready")
    calls = len(llm.prompts)
    zara = next(r for r in brain.routines.items if r.key == "return:zara:51234567890")
    daily = next(r for r in brain.routines.items if r.kind == "returns")
    when = datetime.fromtimestamp(max(zara.next_run, daily.next_run) + 60)
    await brain.run_due_routines(when)
    await asyncio.sleep(0.2)
    titles = [r["title"] for r in rec.of("job_ready")]
    assert titles.count("Reminder") == 1
    assert "Last days to return ZARA, Wool coat" in rec.of("job_ready")[-1]["body"] or any(
        "ZARA, Wool coat" in r["body"] for r in rec.of("job_ready") if r["title"] == "Reminder"
    )
    assert len(titles) == 2  # the daily check found nothing new, so it stayed quiet
    assert len(llm.prompts) == calls  # neither one asked the model anything
    assert not any(r.key == "return:zara:51234567890" for r in brain.routines.items)  # one-off


async def test_without_gmail_it_says_so(settings):
    llm = Script([("returns", {})], [("say", {"text": "Connect Google in Settings first."})])
    brain, _, _ = make_brain(settings, llm=llm), None, None
    await brain[0].turn("what can I still return?")
    assert "Gmail isn't connected" in llm.prompts[1]
