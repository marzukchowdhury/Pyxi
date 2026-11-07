"""Penny, the money teammate: a month of spending from receipts in a fake Gmail, budgets, and
receipts into a spreadsheet (a new one, or the user's own through Ledger).

Realistic receipts: food delivery, a ride, groceries (plus its "ready for pickup" mail), an
Amazon order with its shipped and delivered mails, a subscription, a phone bill, a refund, a
shop that sends both a confirmation and an invoice, an ad, and last month's receipt.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from openpyxl import Workbook, load_workbook

from pyxi.agents import penny
from pyxi.companion import specialists
from test_connectors_and_docs import Script
from test_mailwatch import connected, fetched, until

TZ = ZoneInfo("America/Vancouver")
NOW = datetime(2026, 11, 7, 9, 0)


def on(month: int, day: int, hour: int = 12) -> datetime:
    return datetime(2026, month, day, hour, 0, tzinfo=TZ)


def mails() -> list[dict[str, Any]]:
    return [
        {
            "from": "Uber Receipts <noreply@uber.example>",
            "subject": "Your Thursday evening order with Uber Eats",
            "body": "Thanks for ordering, Alex.\nPad Thai Palace\nSubtotal CA$28.50\nTax CA$1.43\nTotal CA$34.20",
            "date": on(10, 3),
        },
        {
            "from": "Uber Receipts <noreply@uber.example>",
            "subject": "Your Saturday trip with Uber",
            "body": "Thanks for riding, Alex.\nTotal: CA$18.75\nTrip fare CA$15.10",
            "date": on(10, 5),
        },
        {
            "from": "PC Express <orders@pcexpress.example>",
            "subject": "Order confirmation: PC Express order 55120931",
            "body": "Thanks for your order!\nOrder number: 55120931\nOrder total: $142.36\nPickup at Loblaws Queen St.",
            "date": on(10, 6),
        },
        {
            "from": "PC Express <orders@pcexpress.example>",
            "subject": "Your order is ready for pickup",
            "body": "Order number: 55120931\nOrder total: $142.36",
            "date": on(10, 7),
        },
        {
            "from": "Amazon.ca <auto-confirm@amazon.example>",
            "subject": 'Your Amazon.ca order of "Anker USB-C charger"',
            "body": "Thanks for your order.\nOrder #702-8812345-1234567\nOrder Total: $89.99",
            "date": on(10, 10),
        },
        {
            "from": "Amazon.ca <shipment-tracking@amazon.example>",
            "subject": "Your Amazon.ca order has shipped",
            "body": "Order #702-8812345-1234567\nOrder Total: $89.99\nArriving Tuesday",
            "date": on(10, 11),
        },
        {
            "from": "Amazon.ca <order-update@amazon.example>",
            "subject": "Delivered: your package",
            "body": "Order #702-8812345-1234567 was delivered.",
            "date": on(10, 13),
        },
        {
            "from": "Netflix <info@account.netflix.example>",
            "subject": "Your payment was received",
            "body": "Thanks for your payment. Your membership continues.\nPlan: Standard\nAmount: $16.49 CAD",
            "date": on(10, 15),
        },
        {
            "from": "TELUS <billing@telus.example>",
            "subject": "Thank you for your payment",
            "body": "Thank you for your payment of $85.00 for your mobile account ending 4410.",
            "date": on(10, 20),
        },
        {
            "from": "Amazon.ca <return@amazon.example>",
            "subject": "Your refund has been issued",
            "body": "Your refund of $25.00 for order #702-1111111-2222222 has been issued.",
            "date": on(10, 22),
        },
        {
            "from": "Northwind Tea <shop@northwind.example>",
            "subject": "Order #1042 confirmed",
            "body": "Thanks for your purchase!\nOrder #1042\nTotal: $41.80",
            "date": on(10, 24),
        },
        {
            "from": "Northwind Tea <shop@northwind.example>",
            "subject": "Invoice for order #1042",
            "body": "Invoice\nOrder #1042\nTotal: $41.80",
            "date": on(10, 24, 13),
        },
        {
            "from": "Starbucks <offers@starbucks.example>",
            "subject": "50% off this week only",
            "body": "Limited time: 50% off any drink. Prices from $3.25.",
            "date": on(10, 26),
        },
        {
            "from": "Uber Receipts <noreply@uber.example>",
            "subject": "Your Monday trip with Uber",
            "body": "Total: CA$22.10",
            "date": on(9, 28),
        },
        {
            "from": "Uber Receipts <noreply@uber.example>",
            "subject": "Your Tuesday trip with Uber",
            "body": "Total: CA$12.40",
            "date": on(11, 3),
        },
    ]


def october() -> tuple[datetime, datetime]:
    return penny.month_range("last month", NOW)


async def test_a_month_of_receipts_counts_each_charge_once():
    start, end = october()
    assert (start, end) == (datetime(2026, 10, 1), datetime(2026, 11, 1))
    rs = penny.receipts(await fetched(mails()), start, end)
    got = [(r.merchant, r.amount, r.category) for r in rs]
    assert got == [
        ("Uber Eats", 34.20, "Eating out"),
        ("Uber", 18.75, "Transport"),
        ("PC Express", 142.36, "Groceries"),
        ("Amazon", 89.99, "Shopping"),
        ("Netflix", 16.49, "Subscriptions"),
        ("TELUS", 85.00, "Bills and utilities"),
        ("Amazon", -25.00, "Refunds"),
        ("Northwind Tea", 41.80, "Shopping"),
    ], got
    assert {r.currency for r in rs} == {"CAD"}  # "$" next to CA$ receipts is the same dollars


async def test_the_summary_compares_with_the_budget(tmp_path):
    start, end = october()
    rs = penny.receipts(await fetched(mails()), start, end)
    rep = penny.summarise(rs, {"total": 400.0, "Eating out": 30.0}, start, NOW)
    assert rep["title"] == "October: CA$403.59 spent"
    assert "That's CA$3.59 over your CA$400.00 budget." in rep["body"]
    assert "Over budget: eating out." in rep["body"]
    assert rep["body"].startswith("8 receipts. Most on groceries (CA$142.36), shopping (CA$131.79)")
    assert rep["changes"][0] == "Groceries: CA$142.36 (1 charge)"
    assert any("CA$4.20 over the CA$30.00 budget" in c for c in rep["changes"])
    assert rep["card"]["sections"][-1]["heading"].startswith("Refunds")
    assert "—" not in rep["summary"]
    # This month so far, with a pace warning.
    nov = penny.month_range("this month", NOW)
    rs_nov = penny.receipts(await fetched(mails()), *nov)
    rep2 = penny.summarise(rs_nov, {"total": 40.0}, nov[0], NOW)
    assert rep2["title"] == "November so far: CA$12.40 spent"
    assert "At this pace" in rep2["body"]


def test_months_in_their_words():
    assert penny.month_range("October", NOW)[0] == datetime(2026, 10, 1)
    assert penny.month_range("december", NOW)[0] == datetime(2025, 12, 1)  # the last one
    assert penny.month_range("2026-09", NOW) == (datetime(2026, 9, 1), datetime(2026, 10, 1))
    assert penny.month_range("this month", NOW)[0] == datetime(2026, 11, 1)
    start, end = penny.month_range("last month", datetime(2026, 1, 15))
    assert (start, end) == (datetime(2025, 12, 1), datetime(2026, 1, 1))
    assert "after:2026/10/01 before:2026/11/01" in penny.query(*october())


def test_budgets_are_saved_by_category(tmp_path):
    assert penny.set_budget(tmp_path, 1200) == "Monthly budget set to $1,200.00 a month."
    assert penny.set_budget(tmp_path, 400, "groceries").startswith("Groceries budget set")
    assert penny.set_budget(tmp_path, 60, "food").startswith("Eating out")
    assert penny.load(tmp_path)["budgets"] == {
        "total": 1200.0,
        "Groceries": 400.0,
        "Eating out": 60.0,
    }
    assert penny.set_budget(tmp_path, 0, "groceries") == "Removed the Groceries budget."
    assert "Groceries" not in penny.load(tmp_path)["budgets"]


async def test_receipts_go_into_a_new_workbook_with_real_formulas(tmp_path):
    rs = penny.receipts(await fetched(mails()), *october())
    path = penny.write_sheet(rs, {"total": 400.0, "Groceries": 150.0}, tmp_path, "October 2026")
    again = penny.write_sheet(rs, {}, tmp_path, "October 2026")
    assert (
        path.name == "Receipts October 2026.xlsx" and again.name == "Receipts October 2026 (2).xlsx"
    )
    wb = load_workbook(path)
    ws, cs = wb["Receipts"], wb["By category"]
    assert [c.value for c in ws[1]] == [
        "Date",
        "Merchant",
        "Category",
        "Amount",
        "Currency",
        "Email",
    ]
    assert ws["A10"].value == "Total" and ws["D10"].value == "=SUM(D2:D9)"
    groceries = next(r for r in cs.iter_rows(min_row=2) if r[0].value == "Groceries")
    assert groceries[1].value.startswith("=SUMIF(Receipts!C2:C9") and groceries[2].value == 150.0
    assert cs.cell(cs.max_row, 1).value == "Total" and cs.cell(cs.max_row, 3).value == 400.0


async def test_pyxi_hands_the_month_to_penny(settings, monkeypatch):
    monkeypatch.setattr(penny, "clock", lambda: NOW)
    brain, rec, fake = connected(settings, mails())
    penny.set_budget(settings.data_dir, 400)
    out = await specialists.handle(
        brain, "money", {"action": "summary", "month": "last month", "monthly": True}
    )
    assert out.startswith("Started job_") and "Penny" in out
    [ready] = await until(rec, "job_ready")
    assert (
        ready["agent"] == "Penny" and ready["ok"] and ready["title"] == "October: CA$403.59 spent"
    )
    await brain.jobs.wait_all()
    [monthly] = [r for r in brain.routines.items if (r.key or "").startswith("penny:monthly")]
    assert (monthly.on, monthly.time, monthly.kind) == ("2026-12-01", "09:00", "job")
    assert monthly.job["params"] == {"action": "summary", "month": "last month", "monthly": True}
    assert fake.sent == []
    off = await specialists.handle(brain, "money", {"action": "monthly_off"})
    assert "off" in off and not any((r.key or "").startswith("penny") for r in brain.routines.items)


async def test_a_new_spreadsheet_lands_in_documents(settings, monkeypatch, tmp_path):
    monkeypatch.setattr(penny, "clock", lambda: NOW)
    monkeypatch.setattr(penny, "out_dir", lambda: tmp_path / "Documents" / "Pyxi")
    brain, rec, _ = connected(settings, mails())
    await specialists.handle(brain, "money", {"action": "sheet", "month": "October"})
    [ready] = await until(rec, "job_ready")
    assert ready["title"] == "8 receipts in a spreadsheet"
    assert ready["files"][0].endswith("Receipts October 2026.xlsx") and "folder" in ready["actions"]


async def test_their_own_spreadsheet_goes_through_ledger(settings, monkeypatch, tmp_path):
    monkeypatch.setattr(penny, "clock", lambda: NOW)
    own = tmp_path / "budget 2026.xlsx"
    wb = Workbook()
    wb.active.title = "Spending"
    wb.active.append(["Date", "Merchant", "Category", "Amount", "Currency"])
    wb.save(own)
    rows = [
        {
            "file": 0,
            "op": "append_row",
            "sheet": "Spending",
            "values": ["2026-10-03", "Uber Eats", "Eating out", 34.2, "CAD"],
        },
        {"file": 0, "op": "totals_row", "sheet": "Spending", "columns": ["Amount"]},
    ]
    llm = Script(json.dumps({"edits": rows, "summary": "Added the receipts."}))
    brain, rec, _ = connected(settings, mails(), llm=llm)
    out = await specialists.handle(
        brain, "money", {"action": "sheet", "month": "October", "target": str(own)}
    )
    assert out.startswith("Started")
    [ready] = await until(rec, "job_ready")
    assert ready["ok"] and ready["title"] == "8 receipts added", ready
    assert "budget 2026 (edited by Pyxi).xlsx" in ready["body"]
    assert "Uber Eats\tEating out\t34.20" in llm.prompts[0]  # Ledger saw the rows
    assert load_workbook(own)["Spending"].max_row == 1  # their original is untouched


async def test_budget_and_missing_files_answer_straight_away(settings):
    brain, _rec, _ = connected(settings, [])
    out = await specialists.handle(brain, "money", {"action": "budget", "amount": 900})
    assert out.startswith("money: Monthly budget set to $900.00") and not brain.jobs.jobs
    out = await specialists.handle(
        brain, "money", {"action": "sheet", "target": r"C:\nope\missing.xlsx"}
    )
    assert "no spreadsheet at" in out and not brain.jobs.jobs


async def test_refunds_read_as_refunds():
    rs = penny.receipts(await fetched(mails()), *october())
    rep = penny.summarise(rs, {}, october()[0], NOW)
    assert "Refunds: -CA$25.00 (1 refund)" in rep["changes"]
    assert rep["card"]["sections"][-1]["heading"] == "Refunds -CA$25.00"
