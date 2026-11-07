"""Pixel (files) and Ledger (documents): every conversion and edit on real sample files, the opt-in
file search, and the routing that keeps all of it off the user's mouse."""

from __future__ import annotations

import asyncio
import csv
import json
from pathlib import Path
from typing import Any

import pytest
from docx import Document
from openpyxl import Workbook, load_workbook
from PIL import Image
from pptx import Presentation
from pypdf import PdfReader, PdfWriter

from pyxi import files_access
from pyxi.agents import DEFAULT_ROSTER, ledger, pixel
from pyxi.agents.ledger import Deck, LedgerError, WordDoc, run_ledger
from pyxi.agents.ledger import Workbook as LedgerBook
from pyxi.agents.pixel import PixelError
from pyxi.companion import filework, prompts, tools
from pyxi.connectors.google import SCOPES, GoogleConnector
from test_brain_and_server import make_brain
from test_connectors_and_docs import Script


# ---------------------------------------------------------------------------- sample files
def photo(path: Path, size=(1600, 1200), color=(200, 60, 40), noisy=False) -> Path:
    img = Image.effect_noise(size, 90).convert("RGB") if noisy else Image.new("RGB", size, color)
    img.save(path, quality=95)
    return path


def pdf(path: Path, pages: int) -> Path:
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=612, height=792)
    with path.open("wb") as fh:
        w.write(fh)
    return path


def budget(path: Path) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "Budget"
    ws.append(["Item", "Category", "Amount", "Qty"])
    for row in [
        ["Rent", "Home", 1200, 1],
        ["Groceries", "Living", 400, 4],
        ["Bus pass", "Transport", 150, 1],
        ["Coffee", "Living", 55.5, 12],
    ]:
        ws.append(row)
    wb.save(path)
    return path


def snapshot(folder: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in folder.iterdir() if p.is_file()}


# ---------------------------------------------------------------------------- Pixel
def test_images_become_one_pdf_page_each_and_the_originals_stay(tmp_path):
    a = photo(tmp_path / "receipt.jpg")
    b = photo(tmp_path / "scan.png", color=(20, 120, 220))
    before = snapshot(tmp_path)
    r = pixel.run("images_to_pdf", [a, b])
    [out] = r.outputs
    assert out == tmp_path / "receipt.pdf" and len(PdfReader(str(out)).pages) == 2
    assert all(snapshot(tmp_path)[n] == data for n, data in before.items())  # untouched
    again = pixel.run("images_to_pdf", [a], {"name": "receipt"})
    assert again.outputs == [tmp_path / "receipt (2).pdf"]  # never overwrites


def test_merge_and_split_pdfs(tmp_path):
    one, two = pdf(tmp_path / "a.pdf", 2), pdf(tmp_path / "b.pdf", 3)
    merged = pixel.run("merge_pdfs", [one, two], {"name": "both"})
    assert merged.outputs == [tmp_path / "both.pdf"]
    assert len(PdfReader(str(merged.outputs[0])).pages) == 5
    parts = pixel.run("split_pdf", [merged.outputs[0]], {"pages": "1-2, 5"})
    assert [len(PdfReader(str(p)).pages) for p in parts.outputs] == [2, 1]
    assert parts.outputs[0].name == "both (pages 1-2).pdf"
    every = pixel.run("split_pdf", [two])  # each page, in its own folder when there are many
    assert len(every.outputs) == 3
    with pytest.raises(PixelError, match="aren't in this PDF"):
        pixel.run("split_pdf", [two], {"pages": "2-9"})
    with pytest.raises(PixelError, match="at least two"):
        pixel.run("merge_pdfs", [one])


def test_compressing_a_pdf_makes_it_smaller(tmp_path):
    big = pixel.run("images_to_pdf", [photo(tmp_path / "p.jpg", (2400, 1800), noisy=True)]).outputs[
        0
    ]
    r = pixel.run("compress_pdf", [big])
    [small] = r.outputs
    assert small.name == "p (smaller).pdf"
    assert small.stat().st_size < big.stat().st_size * 0.7
    assert len(PdfReader(str(small)).pages) == 1 and "smaller" in r.summary
    tiny = pdf(tmp_path / "blank.pdf", 1)
    nothing = pixel.run("compress_pdf", [tiny])
    assert nothing.outputs == [] and "already" in nothing.summary


def test_word_to_pdf_without_libreoffice(tmp_path, monkeypatch):
    monkeypatch.setattr(pixel, "libreoffice", lambda: None)
    doc = Document()
    doc.add_heading("Quarterly report", 1)
    doc.add_paragraph("Sales grew in every region.")
    doc.add_paragraph("Hire two people", style="List Bullet")
    t = doc.add_table(rows=2, cols=2)
    t.cell(0, 0).text, t.cell(1, 0).text = "Region", "West"
    doc.save(tmp_path / "report.docx")
    r = pixel.run("word_to_pdf", [tmp_path / "report.docx"])
    text = PdfReader(str(r.outputs[0])).pages[0].extract_text()
    assert "Quarterly report" in text and "Sales grew" in text and "West" in text
    assert "LibreOffice" in r.summary  # tells them how to get an exact layout


def test_pdf_pages_become_images(tmp_path):
    src = pixel.run(
        "images_to_pdf", [photo(tmp_path / "a.jpg"), photo(tmp_path / "b.jpg")]
    ).outputs[0]
    r = pixel.run("pdf_to_images", [src], {"format": "jpg", "dpi": 40})
    assert [p.suffix for p in r.outputs] == [".jpg", ".jpg"]
    assert Image.open(r.outputs[0]).size[0] > 10


def test_image_format_and_size(tmp_path):
    src = photo(tmp_path / "cat.png", (1000, 500))
    webp = pixel.run("convert_image", [src], {"format": "webp"}).outputs[0]
    assert webp.name == "cat.webp" and Image.open(webp).format == "WEBP"
    half = pixel.run("resize_image", [src], {"percent": 50}).outputs[0]
    assert Image.open(half).size == (500, 250) and half.name == "cat (500x250).png"
    wide = pixel.run("resize_image", [src], {"width": 200}).outputs[0]
    assert Image.open(wide).size == (200, 100)
    with pytest.raises(PixelError, match="How big"):
        pixel.run("resize_image", [src])
    with pytest.raises(PixelError, match="can save images as"):
        pixel.run("convert_image", [src], {"format": "psd"})


def test_csv_and_excel_both_ways(tmp_path):
    (tmp_path / "sales.csv").write_text(
        "region,amount,code\nWest,1200,007\nEast,99.5,12\n", encoding="utf-8"
    )
    xlsx = pixel.run("csv_to_excel", [tmp_path / "sales.csv"]).outputs[0]
    ws = load_workbook(xlsx).active
    assert [c.value for c in ws[2]] == ["West", 1200, "007"]  # numbers are numbers, codes stay text
    assert ws["A1"].font.bold
    back = pixel.run("excel_to_csv", [xlsx]).outputs[0]
    assert back.name == "sales (2).csv"  # sales.csv is taken
    rows = list(csv.reader(back.open(encoding="utf-8-sig")))
    assert rows[1] == ["West", "1200", "007"]


def test_bad_requests_say_what_is_wrong(tmp_path):
    with pytest.raises(PixelError, match="can't find"):
        pixel.run("images_to_pdf", [tmp_path / "nope.jpg"])
    (tmp_path / "x.txt").write_text("hi")
    with pytest.raises(PixelError, match="isn't a PDF"):
        pixel.run("compress_pdf", [tmp_path / "x.txt"])
    with pytest.raises(PixelError, match="don't know how"):
        pixel.run("teleport", [])


def test_guessing_the_operation_from_plain_words():
    g = pixel.guess_operation
    assert g("make this PDF smaller", ["a.pdf"]) == "compress_pdf"
    assert g("convert this image to PDF", ["a.jpg"]) == "images_to_pdf"
    assert g("merge these", ["a.pdf", "b.pdf"]) == "merge_pdfs"
    assert g("Word to PDF please", ["a.docx"]) == "to_pdf"
    assert g("turn into Excel", ["a.csv"]) == "csv_to_excel"
    assert g("hello", []) is None


# ---------------------------------------------------------------------------- Ledger, local
def test_totals_row_finds_the_number_columns(tmp_path):
    book = LedgerBook(budget(tmp_path / "budget.xlsx"))
    line = book.apply({"op": "totals_row"})
    assert "row 6" in line and "Amount 1,805.5" in line and "Qty 18" in line
    ws = book.wb["Budget"]
    assert ws["A6"].value == "Total" and ws["C6"].value == "=SUM(C2:C5)" and ws["B6"].value is None
    assert "already" in book.apply({"op": "totals_row"})  # doesn't add a second one
    out = book.save()
    assert out.name == "budget (edited by Pyxi).xlsx"
    assert load_workbook(tmp_path / "budget.xlsx")["Budget"].max_row == 5  # original untouched


def test_pivot_summary_uses_live_formulas(tmp_path):
    book = LedgerBook(budget(tmp_path / "budget.xlsx"))
    line = book.apply({"op": "pivot", "group_by": "Category", "value": "Amount", "agg": "sum"})
    assert "3 groups" in line and "Home 1,200" in line and "Living 455.5" in line
    ws = book.wb["Pivot"]
    assert [ws["A1"].value, ws["B1"].value] == ["Category", "Sum of Amount"]
    assert [ws.cell(r, 1).value for r in range(2, 5)] == ["Home", "Living", "Transport"]
    assert ws["B3"].value == "=SUMIFS('Budget'!$C$2:$C$5,'Budget'!$B$2:$B$5,A3)"
    assert ws["A5"].value == "Total" and ws["B5"].value == "=SUM(B2:B4)"
    count = book.apply({"op": "pivot", "sheet": "Budget", "group_by": "B", "agg": "count"})
    assert "Pivot 2" in count and book.wb["Pivot 2"]["B2"].value.startswith("=COUNTIFS(")
    with pytest.raises(LedgerError, match="no column called"):
        book.apply({"op": "pivot", "group_by": "Colour"})


def test_more_excel_edits(tmp_path):
    book = LedgerBook(budget(tmp_path / "budget.xlsx"))
    book.apply({"op": "add_column", "header": "Line total", "formula": "=C{r}*D{r}"})
    ws = book.wb["Budget"]
    assert ws["E1"].value == "Line total" and ws["E3"].value == "=C3*D3"
    book.apply({"op": "set_cells", "cells": {"G1": "Tax", "G2": "=C2*0.13"}})
    assert ws["G2"].value == "=C2*0.13"
    book.apply({"op": "format", "range": "A1:D1", "bold": True, "number_format": "@"})
    assert ws["B1"].font.bold
    book.apply({"op": "make_table"})
    assert "Table1" in ws.tables
    book.apply({"op": "chart", "values": "Amount", "type": "bar"})
    assert len(ws._charts) == 1
    book.apply({"op": "append_row", "values": ["Phone", "Living", 60, 1]})
    assert ws["A6"].value == "Phone"
    with pytest.raises(LedgerError, match="no sheet"):
        book.apply({"op": "totals_row", "sheet": "Nope"})
    load_workbook(book.save())  # still a valid workbook


def test_word_edits_keep_the_document_in_order(tmp_path):
    doc = Document()
    doc.add_heading("Plan", 1)
    doc.add_paragraph("Intro text here.")
    doc.add_paragraph("Closing line.")
    doc.save(tmp_path / "plan.docx")
    w = WordDoc(tmp_path / "plan.docx")
    w.apply({"op": "add_heading", "text": "Goals", "level": 2, "after": 1})
    w.apply({"op": "add_bullets", "items": ["Ship it", "Tell Sam"], "after": 2})
    w.apply({"op": "add_table", "rows": [["Who", "What"], ["Sam", "Design"]]})
    w.apply({"op": "replace_text", "find": "Intro", "replace": "Opening"})
    w.apply({"op": "set_style", "i": 5, "style": "Heading 2"})
    out = Document(str(w.save()))
    assert [(p.style.name, p.text) for p in out.paragraphs] == [
        ("Heading 1", "Plan"),
        ("Normal", "Opening text here."),
        ("Heading 2", "Goals"),
        ("List Bullet", "Ship it"),
        ("List Bullet", "Tell Sam"),
        ("Heading 2", "Closing line."),
    ]
    assert out.tables[0].cell(1, 0).text == "Sam"
    with pytest.raises(LedgerError, match="isn't in the document"):
        w.apply({"op": "replace_text", "find": "zebra", "replace": "x"})


def test_powerpoint_edits(tmp_path):
    prs = Presentation()
    s = prs.slides.add_slide(prs.slide_layouts[1])
    s.shapes.title.text = "Q3 review"
    s.placeholders[1].text_frame.text = "Revenue up"
    prs.save(tmp_path / "deck.pptx")
    d = Deck(tmp_path / "deck.pptx")
    d.apply({"op": "add_slide", "title": "Next steps", "bullets": ["Hire", "Launch"]})
    d.apply({"op": "add_bullets", "slide": 1, "items": ["Costs down"]})
    d.apply({"op": "set_title", "slide": 1, "text": "Q3 in review"})
    d.apply({"op": "replace_text", "find": "Launch", "replace": "Launch in May"})
    d.apply({"op": "set_notes", "slide": 2, "text": "Keep it short."})
    d.apply({"op": "add_slide", "title": "Agenda", "bullets": ["One"], "after": 0})
    d.apply({"op": "delete_slide", "slide": 3})
    out = Presentation(str(d.save()))
    titles = [sl.shapes.title.text for sl in out.slides]
    assert titles == ["Agenda", "Q3 in review"]
    assert out.slides[1].placeholders[1].text_frame.text == "Revenue up\nCosts down"


async def test_ledger_job_asks_once_retries_failures_and_saves_a_copy(tmp_path):
    src = budget(tmp_path / "budget.xlsx")
    replies = [
        json.dumps(
            {
                "edits": [
                    {"file": 0, "op": "totals_row"},
                    {"file": 0, "op": "pivot", "group_by": "Region", "value": "Amount"},
                ],
                "summary": "Added totals and a pivot.",
            }
        ),
        json.dumps(
            {"edits": [{"file": 0, "op": "pivot", "group_by": "Category", "value": "Amount"}]}
        ),
    ]
    prompts_seen: list[str] = []

    async def ask(system: str, prompt: str) -> tuple[str, float]:
        prompts_seen.append(prompt)
        assert "totals_row" in system and "{name}" not in system
        return replies.pop(0), 0.002

    r = await run_ledger(
        "add a totals row and a pivot of amount by category", files=[str(src)], ask=ask
    )
    assert len(prompts_seen) == 2 and "no column called 'Region'" in prompts_seen[1]
    assert "B1='Category'" in prompts_seen[0]  # the model sees addressed cells
    assert len(r.changes) == 2 and not r.problems and r.cost_usd == pytest.approx(0.004)
    [out] = r.outputs
    wb = load_workbook(out)
    assert wb["Budget"]["A6"].value == "Total" and "Pivot" in wb.sheetnames


async def test_ledger_reports_what_it_could_not_do(tmp_path, monkeypatch):
    async def ask(system, prompt):
        return '{"edits": [], "summary": "There is no sales data in this file."}', 0.0

    r = await run_ledger("pivot the sales", files=[str(budget(tmp_path / "b.xlsx"))], ask=ask)
    assert r.outputs == [] and r.summary == "There is no sales data in this file."
    with pytest.raises(LedgerError, match="Which file"):
        await run_ledger("anything", files=[], ask=ask)
    monkeypatch.setattr(pixel, "libreoffice", lambda: None)  # no converter on this PC
    with pytest.raises(LedgerError, match="free LibreOffice converter"):
        (tmp_path / "old.xls").write_bytes(b"x")
        await run_ledger("anything", files=[str(tmp_path / "old.xls")], ask=ask)


# ---------------------------------------------------------------------------- Ledger, Google
class FakeGoogle:
    """The Sheets and Docs calls Ledger makes, recorded; nothing reaches a real account."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.rows = [
            ["Region", "Rep", "Sales"],
            ["West", "Ana", "1200"],
            ["East", "Bo", "800"],
            ["West", "Cy", "300"],
        ]
        self.doc = {
            "title": "Weekly notes",
            "body": {
                "content": [
                    {"endIndex": 1, "sectionBreak": {}},
                    {
                        "startIndex": 1,
                        "endIndex": 12,
                        "paragraph": {
                            "elements": [{"textRun": {"content": "First line\n"}}],
                            "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                        },
                    },
                ]
            },
        }

    def connected(self) -> bool:
        return True

    async def open_for_edit(self, ref: str):
        return await GoogleConnector.open_for_edit(self, ref)  # type: ignore[arg-type]

    async def sheets_meta(self, sid):
        return {
            "properties": {"title": "Sales 2026"},
            "sheets": [{"properties": {"title": "Data", "sheetId": 7}}],
        }

    async def sheets_values(self, sid, a1, formulas=False):
        return self.rows

    async def sheets_write(self, sid, data):
        self.calls.append(("write", data))

    async def sheets_append(self, sid, a1, rows):
        self.calls.append(("append", rows))

    async def sheets_batch(self, sid, requests):
        self.calls.append(("batch", requests))
        if "addSheet" in requests[0]:
            return {"replies": [{"addSheet": {"properties": {"sheetId": 99}}}]}
        return {}

    async def docs_get(self, did):
        return self.doc

    async def docs_batch(self, did, requests):
        self.calls.append(("docs", requests))
        return {"replies": [{"replaceAllText": {"occurrencesChanged": 1}}]}

    async def _api(self, *a, **k):
        raise AssertionError("no raw API calls in this test")

    async def drive_find(self, name, kind):
        return [{"id": "found123", "name": name}] if "sales" in name.lower() else []


async def test_google_sheet_pivot_and_totals(tmp_path):
    g = FakeGoogle()
    edits = json.dumps(
        {
            "edits": [
                {"file": 0, "op": "pivot", "group_by": "Region", "value": "Sales", "agg": "sum"},
                {"file": 0, "op": "totals_row"},
            ],
            "summary": "Pivoted sales by region.",
        }
    )

    async def ask(system, prompt):
        assert "Tab 'Data'" in prompt and "A1='Region'" in prompt
        return edits, 0.0

    r = await run_ledger(
        "make a pivot of sales by region",
        files=[],
        google=g,
        google_refs=["my sales sheet"],
        ask=ask,
    )
    assert r.links == ["https://docs.google.com/spreadsheets/d/found123/edit"]
    add, pivot, write = g.calls
    assert add[1][0]["addSheet"]["properties"]["title"] == "Pivot"
    table = pivot[1][0]["updateCells"]["rows"][0]["values"][0]["pivotTable"]
    assert table["source"]["sheetId"] == 7 and table["rows"][0]["sourceColumnOffset"] == 0
    assert table["values"][0] == {"summarizeFunction": "SUM", "sourceColumnOffset": 2}
    assert pivot[1][0]["updateCells"]["start"]["sheetId"] == 99
    assert write[1][0] == {"range": "'Data'!A5", "values": [["Total", "", "=SUM(C2:C4)"]]}
    assert "Sales 2,300" in r.changes[1]


async def test_google_doc_heading_and_bullets():
    g = FakeGoogle()

    async def ask(system, prompt):
        assert "[0] (NORMAL_TEXT) First line" in prompt
        return json.dumps(
            {
                "edits": [
                    {"file": 0, "op": "add_heading", "text": "Weekly Report", "level": 1},
                    {"file": 0, "op": "add_bullets", "items": ["Done", "Next"]},
                ]
            }
        ), 0.0

    url = "https://docs.google.com/document/d/abcDEF123/edit"
    r = await run_ledger(
        "add a heading Weekly Report and two bullets",
        files=[],
        google=g,
        google_refs=[url],
        ask=ask,
    )
    assert r.links == [url] and len(r.changes) == 2
    heading = g.calls[0][1]
    assert heading[0]["insertText"] == {"location": {"index": 11}, "text": "\nWeekly Report"}
    assert heading[1]["updateParagraphStyle"]["paragraphStyle"]["namedStyleType"] == "HEADING_1"
    bullets = g.calls[1][1]
    assert bullets[2]["createParagraphBullets"]["bulletPreset"] == "BULLET_DISC_CIRCLE_SQUARE"


def test_google_asks_to_reconnect_for_sheets(tmp_path):
    g = GoogleConnector(tmp_path / "connectors", tmp_path / "dl")
    assert "https://www.googleapis.com/auth/spreadsheets" in SCOPES
    g.token_path.parent.mkdir(parents=True)
    old = [s for s in SCOPES if "spreadsheets" not in s and "metadata" not in s]
    g.token_path.write_text(json.dumps({"scopes": old, "pyxi_email": "me@example.com"}))
    status = g.status()
    assert status.connected and "Connect again" in status.detail
    assert "sheets_read" in {t.spec.name for t in g.tools()}
    with pytest.raises(RuntimeError, match="Connect next to Google"):
        g._need_sheets()
    g.token_path.write_text(json.dumps({"scopes": SCOPES, "pyxi_email": "me@example.com"}))
    assert g.status().detail == "me@example.com" and not g.missing_scopes()


# ---------------------------------------------------------------------------- file access
def test_file_access_is_off_until_allowed_and_stays_in_its_folders(tmp_path):
    assert files_access.load(tmp_path).enabled is False
    docs = tmp_path / "Docs"
    (docs / "sub").mkdir(parents=True)
    (docs / "sub" / "Budget 2026.xlsx").write_bytes(b"x")
    (docs / "notes.txt").write_text("hello budget")
    (docs / "node_modules").mkdir()
    (docs / "node_modules" / "budget.js").write_text("x")
    access = files_access.save(
        tmp_path, files_access.FileAccess(True, [str(docs), str(docs)], True)
    )
    assert access.folders == [str(docs)]  # no duplicates
    assert files_access.load(tmp_path).enabled
    hits = files_access.search("budget 2026", access, use_index=False)
    assert [Path(h.path).name for h in hits] == ["Budget 2026.xlsx"]
    assert [
        Path(h.path).name
        for h in files_access.search("budget", access, kind="excel", use_index=False)
    ] == ["Budget 2026.xlsx"]
    assert files_access.allowed(docs / "notes.txt", access)
    assert not files_access.allowed(tmp_path / "secret.txt", access)
    assert not files_access.allowed(docs / ".." / "secret.txt", access)


def test_reading_files_as_text(tmp_path):
    budget(tmp_path / "b.xlsx")
    assert "B1='Category'" in files_access.read_text(tmp_path / "b.xlsx")
    src = pdf(tmp_path / "x.pdf", 2)
    assert files_access.read_text(src).startswith("PDF, 2 pages.")
    photo(tmp_path / "p.png", (300, 200))
    assert "300x200" in files_access.read_text(tmp_path / "p.png")
    (tmp_path / "long.txt").write_text("a" * 9000)
    assert "cut;" in files_access.read_text(tmp_path / "long.txt")
    with pytest.raises(ValueError):
        files_access.read_text(tmp_path / "b.exe")


async def _answer_cards(brain, rec, value):
    for _ in range(100):
        cards = [c for c in rec.of("confirm") if c["action"] == filework.CONSENT_ACTION]
        if cards:
            brain.hub.resolve(cards[-1]["request_id"], value)
            return cards[-1]
        await asyncio.sleep(0.01)
    raise AssertionError("no consent card")


async def test_the_first_search_shows_a_consent_card(settings, tmp_path, monkeypatch):
    brain, rec = make_brain(settings)
    folder = tmp_path / "Mine"
    folder.mkdir()
    (folder / "Lease.pdf").write_bytes(b"%PDF")
    monkeypatch.setattr(files_access, "default_folders", lambda: [str(folder)])
    monkeypatch.setattr(files_access, "search_index", lambda *a, **k: None)
    task = asyncio.create_task(filework.handle(brain, "search_files", {"query": "lease"}))
    card = await _answer_cards(brain, rec, True)
    assert "Mine" in card["reason"]
    result = await task
    assert "Lease.pdf" in result
    saved = files_access.load(settings.data_dir)
    assert saved.enabled and saved.asked
    assert rec.of("file_access")[-1]["enabled"] is True
    # Allowed now: no second card.
    assert "Lease.pdf" in await filework.handle(brain, "search_files", {"query": "lease"})
    assert len(rec.of("confirm")) == 1
    outside = tmp_path / "other.txt"
    outside.write_text("x")
    assert "outside the folders" in await filework.handle(
        brain, "read_file", {"path": str(outside)}
    )


async def test_saying_no_sticks(settings, monkeypatch, tmp_path):
    brain, rec = make_brain(settings)
    monkeypatch.setattr(files_access, "default_folders", lambda: [str(tmp_path)])
    task = asyncio.create_task(
        filework.handle(brain, "read_file", {"path": str(tmp_path / "a.txt")})
    )
    await _answer_cards(brain, rec, False)
    assert "said no" in await task
    again = await filework.handle(brain, "search_files", {"query": "x"})
    assert "file search is off" in again and "Settings" in again
    assert len(rec.of("confirm")) == 1


# ---------------------------------------------------------------------------- jobs and routing
async def test_convert_files_runs_pixel_in_the_background(settings, tmp_path):
    brain, rec = make_brain(settings)
    src = photo(tmp_path / "receipt.jpg")
    note = await filework.handle(
        brain, "convert_files", {"operation": "images_to_pdf", "files": [str(src)]}
    )
    assert "Pixel is on it" in note
    await brain.jobs.wait_all()
    [job] = [j for j in brain.jobs.jobs.values() if j.agent_id == "pixel"]
    assert job.status == "done" and job.outputs == [str(tmp_path / "receipt.pdf")]
    assert job.cost_usd == 0  # no model call
    [ready] = rec.of("job_ready")
    assert (
        ready["ok"]
        and ready["files"] == [str(tmp_path / "receipt.pdf")]
        and "open" in ready["actions"]
    )
    assert "receipt.pdf" in ready["body"]
    missing = await filework.handle(
        brain, "convert_files", {"operation": "compress_pdf", "files": ["C:/nope.pdf"]}
    )
    assert "search_files" in missing
    bad = await filework.handle(brain, "convert_files", {"operation": "fly", "files": [str(src)]})
    assert "must be one of" in bad


async def test_a_failed_conversion_says_why(settings, tmp_path):
    brain, _ = make_brain(settings)
    (tmp_path / "a.pdf").write_bytes(b"%PDF")
    job = await brain.jobs.start(
        "pixel", "merge", files=[str(tmp_path / "a.pdf")], params={"operation": "merge_pdfs"}
    )
    job = await brain.jobs.wait(job.id)
    assert job.status == "failed" and "at least two" in job.summary


async def test_edit_document_runs_ledger(settings, tmp_path):
    reply = json.dumps({"edits": [{"file": 0, "op": "totals_row"}], "summary": "Totals added."})
    brain, _ = make_brain(settings, llm=Script(reply))
    src = budget(tmp_path / "budget.xlsx")
    note = await filework.handle(
        brain, "edit_document", {"task": "add a totals row", "files": [str(src)]}
    )
    assert "Ledger is on it" in note
    await brain.jobs.wait_all()
    [job] = [j for j in brain.jobs.jobs.values() if j.agent_id == "ledger"]
    assert job.status == "done", job.summary
    assert job.outputs == [str(tmp_path / "budget (edited by Pyxi).xlsx")]
    assert "Total row" in job.summary and "beside the original" in job.summary
    assert load_workbook(src)["Budget"].max_row == 5
    no_google = await filework.handle(
        brain, "edit_document", {"task": "pivot", "google": ["sales"]}
    )
    assert "isn't connected" in no_google


def test_the_team_and_the_prompt_route_files_away_from_the_mouse():
    roster = {a.id: a.role for a in DEFAULT_ROSTER}
    assert roster["pixel"] == "files" and roster["ledger"] == "documents"
    names = {t.name: t for t in tools.COMPANION_TOOLS}
    for name in ("convert_files", "edit_document", "search_files", "read_file"):
        assert name in names
    assert {"search_files", "read_file"} <= tools.INFO_TOOLS
    hands = names["use_computer"].description
    assert "pivot" not in hands and "Never for files" in hands
    assert set(names["convert_files"].parameters["properties"]["operation"]["enum"]) == set(
        pixel.OPERATIONS
    )
    text = prompts.SYSTEM
    assert (
        "`convert_files`" in text
        and "`edit_document`" in text
        and "never with `use_computer`" in text
    )
    assert "make a pivot" not in text.split("use_computer`) once they Allow")[-1]
    assert "—" not in text and "—" not in ledger.SYSTEM


async def test_registry_adds_new_teammates_for_existing_users(tmp_path):
    from pyxi.agents import AgentRegistry

    reg = AgentRegistry(tmp_path / "a.db")
    assert reg.get("Pixel").role == "files" and reg.get("ledger").name == "Ledger"
    assert not reg.delete("pixel")  # built in


# ---------------------------------------------------------------- found in the beta QA pass
async def test_ledger_never_claims_edits_that_all_failed(tmp_path):
    async def ask(system, prompt):
        if "These edits failed" in prompt:
            return '{"edits": []}', 0.0
        return (
            '{"edits": [{"file": 0, "op": "set_cells", "sheet": "Nope", "cells": {"A1": 1}}], '
            '"summary": "Set A1 on Nope."}'
        ), 0.0

    r = await run_ledger("put 1 in A1", files=[str(budget(tmp_path / "b.xlsx"))], ask=ask)
    assert not r.changes and r.outputs == []
    assert r.summary.startswith("I couldn't make that change. There's no sheet called 'Nope'")
    assert r.problems and not any("{" in p for p in r.problems)  # the card shows these


def test_a_missing_header_is_not_a_far_away_column(tmp_path):
    book = LedgerBook(budget(tmp_path / "b.xlsx"))
    with pytest.raises(LedgerError, match="no column called 'Tax'"):
        book.apply({"op": "totals_row", "columns": ["Tax"]})
    assert "Total row" in book.apply({"op": "totals_row", "columns": ["C"]})
    assert book.wb.active.max_column == 4


async def test_a_file_named_budget_is_not_the_spending_limit(settings, tmp_path):
    from pyxi.agents.jobs import describe_failure

    assert describe_failure(LedgerError("Budget 2026.xlsx is locked by another app.")) == (
        "Budget 2026.xlsx is locked by another app."
    )
    assert "spending limit" in describe_failure(RuntimeError("budget_exceeded: cost limit"))
    brain, _ = make_brain(settings)
    src = tmp_path / "Budget 2026.pdf"
    src.write_bytes(b"not a pdf")
    job = await brain.jobs.start(
        "pixel", "split", files=[str(src)], params={"operation": "split_pdf"}
    )
    job = await brain.jobs.wait(job.id)
    assert job.status == "failed" and "doesn't open as a PDF" in job.summary


def test_model_key_and_rate_limit_failures_read_plainly():
    from pyxi.agents.jobs import describe_failure

    auth = RuntimeError("OpenRouter API error: Error code: 401 - Missing Authentication header")
    assert describe_failure(auth).startswith("The AI model turned the key down.")
    assert "busy" in describe_failure(RuntimeError("Error code: 429 - rate limited"))


async def test_a_writers_draft_has_no_em_dashes(settings):
    brain, _ = make_brain(
        settings, llm=Script("Hi Dana — thanks for Tuesday. We meet 9–5 – see you.")
    )
    job = await brain.jobs.wait((await brain.jobs.start("quill", "Thank Dana")).id)
    assert job.summary == "Hi Dana, thanks for Tuesday. We meet 9–5, see you."
    assert Path(job.outputs[0]).read_text(encoding="utf-8") == job.summary


def test_summaries_show_whole_amounts_without_a_trailing_zero(tmp_path):
    book = LedgerBook(budget(tmp_path / "b.xlsx"))
    said = book.apply({"op": "pivot", "group_by": "Category", "value": "Amount", "agg": "sum"})
    assert "Home 1,200" in said and "1,200.0" not in said and "Living 455.5" in said
