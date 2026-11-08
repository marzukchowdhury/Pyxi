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
    pivot, write = g.calls  # no new tab: beside the data, where they're looking
    table = pivot[1][0]["updateCells"]["rows"][0]["values"][0]["pivotTable"]
    assert table["source"]["sheetId"] == 7 and table["rows"][0]["sourceColumnOffset"] == 0
    assert table["values"][0] == {"summarizeFunction": "SUM", "sourceColumnOffset": 2}
    assert pivot[1][0]["updateCells"]["start"] == {"sheetId": 7, "rowIndex": 0, "columnIndex": 4}
    assert "beside the data on Data, from E1" in r.changes[0]
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
    # Connected already, so Settings shows a button that asks Google for the one missing OK
    # (only Disconnect used to show, and "press Connect again" had nothing to press).
    assert status.connected and status.needs == "Allow Sheets" and "Allow Sheets" in status.detail
    assert "sheets_read" in {t.spec.name for t in g.tools()}
    with pytest.raises(RuntimeError, match="press Allow Sheets next to Google"):
        g._need_sheets()
    g.token_path.write_text(json.dumps({"scopes": SCOPES, "pyxi_email": "me@example.com"}))
    assert g.status().detail == "me@example.com" and not g.missing_scopes()
    assert not g.status().needs


def test_sheets_unticked_on_googles_page_is_caught_at_once(tmp_path, monkeypatch):
    import sys
    import types

    g = GoogleConnector(tmp_path / "connectors", tmp_path / "dl")
    g.token_path.parent.mkdir(parents=True)
    monkeypatch.setattr(g, "client_config", lambda: {"installed": {}})

    class Creds:
        token = "t"
        granted_scopes = " ".join(s for s in SCOPES if "spreadsheets" not in s)

        def to_json(self):
            return json.dumps({"scopes": SCOPES})  # what was asked for, not what was given

    class Flow:
        @staticmethod
        def from_client_config(config, scopes):
            return Flow()

        def run_local_server(self, **kw):
            return Creds()

    monkeypatch.setitem(
        sys.modules, "google_auth_oauthlib.flow", types.SimpleNamespace(InstalledAppFlow=Flow)
    )
    monkeypatch.setattr("pyxi.connectors.google.httpx.get", lambda *a, **k: 1 / 0)
    with pytest.raises(RuntimeError, match="tick the Google Sheets box"):
        g.sign_in()
    assert g.status().needs == "Allow Sheets"  # and Settings still offers the button


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


async def test_sample_data_then_a_pivot_on_an_empty_sheet_in_one_go():
    # "Make a small sample and pivot it" on a blank sheet: the pivot must see the new data.
    g = FakeGoogle()
    g.rows = []
    written: dict[str, object] = {}

    async def write(sid, data):
        g.calls.append(("write", data))
        for d in data:
            written[d["range"].split("!")[1]] = d["values"][0][0]

    async def values(sid, a1, formulas=False):
        if not written:
            return []
        grid = [["", "", ""] for _ in range(7)]
        for ref, v in written.items():
            grid[int(ref[1:]) - 1]["ABC".index(ref[0])] = v
        return grid

    g.sheets_write, g.sheets_values = write, values
    cells = {"A1": "Region", "B1": "Product", "C1": "Sales"}
    for i, (r, p, s) in enumerate(
        [("North", "A", 120), ("North", "B", 80), ("South", "A", 150), ("South", "B", 60),
         ("East", "A", 90), ("East", "B", 110)], start=2):
        cells |= {f"A{i}": r, f"B{i}": p, f"C{i}": s}
    edits = json.dumps(
        {
            "edits": [
                {"file": 0, "op": "set_cells", "cells": cells},
                {"file": 0, "op": "pivot", "group_by": "Region", "value": "Sales", "agg": "sum"},
            ]
        }
    )
    asked = []

    async def ask(system, prompt):
        asked.append(prompt)
        return edits, 0.0

    url = "https://docs.google.com/spreadsheets/d/blank123/edit"
    r = await run_ledger("sample sales and a pivot", files=[], google=g, google_refs=[url], ask=ask)
    assert not r.problems and len(asked) == 1  # one model call, nothing to fix afterwards
    assert any(c[0] == "batch" and "updateCells" in c[1][0] for c in g.calls)  # the pivot


def test_a_switched_off_google_api_says_which_and_where_to_turn_it_on():
    from pyxi.agents.jobs import describe_failure
    from pyxi.companion.brain import friendly_error
    from pyxi.connectors.google import google_error

    body = json.dumps(
        {
            "error": {
                "code": 403,
                "message": "Google Sheets API has not been used in project 83793709681 before or "
                "it is disabled. Enable it by visiting https://console.developers.google.com/apis/"
                "api/sheets.googleapis.com/overview?project=83793709681 then retry.",
                "status": "PERMISSION_DENIED",
                "details": [{"reason": "SERVICE_DISABLED"}],
            }
        }
    )
    said = google_error(403, body)
    assert said.startswith("The Google Sheets API is switched off in Pyxi's Google Cloud project")
    assert "console.cloud.google.com/apis/library/sheets.googleapis.com?project=83793709681" in said
    assert "{" not in said
    assert describe_failure(RuntimeError(said)) == said  # the notification keeps all of it
    assert friendly_error(RuntimeError(said)) == said
    other = google_error(404, json.dumps({"error": {"message": "Requested entity was not found."}}))
    assert other == "Google said 404: Requested entity was not found."


async def test_a_sheet_in_another_google_account(monkeypatch):
    """The Sheet open in Firefox belongs to another of their Google accounts: Google only says
    'The caller does not have permission'. Sample data goes in a new sheet in the connected
    account, which opens on their screen; real data is never guessed at."""
    import asyncio
    from types import SimpleNamespace

    from pyxi.companion import filework
    from pyxi.screen import uia

    link = "https://docs.google.com/spreadsheets/d/theirOtherAccount123/edit?gid=0"
    monkeypatch.setattr(uia, "browser_url", lambda hwnd, budget_s=0.5: link)

    class G:
        def connected(self):
            return True

        def _saved_email(self):
            return "me@example.com"

        async def sheets_meta(self, sid):
            raise RuntimeError("Google said 403: The caller does not have permission")

    started, opened, said, spawned = [], [], [], []

    class Jobs:
        async def start(self, agent, task, files=(), params=None):
            started.append(params["google"])
            return SimpleNamespace(id="job_1", agent_name="Ledger")

        async def wait(self, job_id):
            return SimpleNamespace(
                status="done", data={"links": ["https://docs.google.com/spreadsheets/d/new1/edit"]}
            )

    async def quick(action, value):
        opened.append(value)

    brain = SimpleNamespace(
        _ctx=SimpleNamespace(window=SimpleNamespace(hwnd=5, title="Untitled spreadsheet - Google Sheets")),
        connectors=SimpleNamespace(google=G()),
        jobs=Jobs(),
        _quick_action=quick,
        _speak=said.append,
        _spawn=lambda coro: spawned.append(asyncio.ensure_future(coro)),
    )
    note = await filework.handle(
        brain, "edit_document", {"task": "sample sales table and a pivot", "google": ["this sheet"]}
    )
    await asyncio.gather(*spawned)
    assert started == [["new sheet: Sample data"]] and "making a new sheet" in note
    assert opened == ["https://docs.google.com/spreadsheets/d/new1/edit"]  # on their screen
    assert "different Google account" in said[0]

    started.clear()
    note = await filework.handle(
        brain, "edit_document", {"task": "pivot of sales by region", "google": ["this sheet"]}
    )
    assert not started and "different Google account" in note and "me@example.com" in note


async def test_google_sheet_pivot_on_its_own_tab():
    g = FakeGoogle()
    edits = json.dumps(
        {"edits": [{"file": 0, "op": "pivot", "group_by": "Region", "value": "Sales", "target": "Pivot"}]}
    )

    async def ask(system, prompt):
        return edits, 0.0

    url = "https://docs.google.com/spreadsheets/d/abc123/edit"
    r = await run_ledger("pivot on a new tab", files=[], google=g, google_refs=[url], ask=ask)
    add, pivot = g.calls
    assert add[1][0]["addSheet"]["properties"]["title"] == "Pivot" and not r.problems
    assert pivot[1][0]["updateCells"]["start"]["sheetId"] == 99


async def test_a_chart_of_repeated_regions_draws_the_pivot_totals_beside_everything():
    # West is on two rows: a plain chart draws a bar per row, so the chart reads the pivot's
    # totals (E1:F3 here), and sits right of the pivot instead of covering it.
    g = FakeGoogle()
    g.rows = [
        ["Region", "Rep", "Sales", "", "Region", "SUM of Sales"],
        ["West", "Ana", "1200", "", "East", "800"],
        ["East", "Bo", "800", "", "West", "1500"],
        ["West", "Cy", "300", "", "Grand Total", "2300"],
    ]
    edits = json.dumps({"edits": [{"file": 0, "op": "chart", "labels": "Region", "values": "Sales"}]})

    async def ask(system, prompt):
        return edits, 0.0

    url = "https://docs.google.com/spreadsheets/d/abc123/edit"
    r = await run_ledger("chart of sales by region", files=[], google=g, google_refs=[url], ask=ask)
    [chart] = g.calls  # no second pivot: the one there is used
    assert not r.problems
    spec = chart[1][0]["addChart"]["chart"]["spec"]["basicChart"]
    domain = spec["domains"][0]["domain"]["sourceRange"]["sources"][0]
    series = spec["series"][0]["series"]["sourceRange"]["sources"][0]
    assert (domain["startColumnIndex"], domain["startRowIndex"], domain["endRowIndex"]) == (4, 0, 3)
    assert series["startColumnIndex"] == 5  # SUM of Sales, Grand Total left out
    anchor = chart[1][0]["addChart"]["chart"]["position"]["overlayPosition"]["anchorCell"]
    assert anchor["columnIndex"] == 7  # right of the pivot, not on top of it


# ------------------------------------------------- Google Docs and Word: the same everyday edits
async def test_google_docs_does_the_everyday_edits():
    from pyxi.agents.ledger import GoogleDoc

    g = FakeGoogle()
    g.doc["body"]["content"][1]["paragraph"]["elements"][0]["startIndex"] = 1
    sent: list[list[dict]] = []

    async def docs_batch(did, requests):
        sent.append(requests)
        if "insertTable" in requests[0]:  # the doc now ends with a 2 x 2 table
            cells = [
                {"tableCells": [{"content": [{"startIndex": 15 + 3 * (2 * r + c)}]} for c in range(2)]}
                for r in range(2)
            ]
            g.doc["body"]["content"].append({"startIndex": 13, "table": {"tableRows": cells}})
        return {}

    g.docs_batch = docs_batch
    d = GoogleDoc(g, "doc1")
    await d.load()
    assert await d.apply({"op": "add_table", "rows": [["Name", "Role"], ["Sam", "Design"]]}) == "Added a 2 x 2 table"
    fill = [r["insertText"] for r in sent[1]]
    assert [f["text"] for f in fill] == ["Design", "Sam", "Role", "Name"]  # last cell first
    assert fill[0]["location"]["index"] > fill[-1]["location"]["index"]
    assert "5 x 5" in await d.apply({"op": "add_table", "n_rows": 5, "n_cols": 5})

    await d.apply({"op": "format_text", "find": "line", "bold": True, "color": "red", "size": 14})
    style = sent[-1][0]["updateTextStyle"]
    assert style["range"] == {"startIndex": 7, "endIndex": 11}  # "line" in "First line"
    assert style["textStyle"]["bold"] and style["textStyle"]["fontSize"]["magnitude"] == 14
    assert set(style["fields"].split(",")) == {"bold", "fontSize", "foregroundColor"}

    await d.apply({"op": "set_style", "i": 0, "style": "Heading 2"})
    assert sent[-1][0]["updateParagraphStyle"]["paragraphStyle"] == {"namedStyleType": "HEADING_2"}
    await d.apply({"op": "align", "i": 0, "align": "center"})
    assert sent[-1][0]["updateParagraphStyle"]["paragraphStyle"] == {"alignment": "CENTER"}
    await d.apply({"op": "page_break"})
    assert sent[-1] == [{"insertPageBreak": {"endOfSegmentLocation": {}}}]
    await d.apply({"op": "delete_paragraph", "i": 0})
    assert "deleteContentRange" in sent[-1][0]


def test_word_does_the_same_everyday_edits(tmp_path):
    from pyxi.agents.ledger import WordDoc

    doc = Document()
    doc.add_paragraph("Welcome to the team, Sam.")
    doc.add_paragraph("Second line")
    doc.save(tmp_path / "note.docx")
    w = WordDoc(tmp_path / "note.docx")
    assert w.apply({"op": "format_text", "find": "Sam", "bold": True, "color": "blue"}).startswith("Formatted 1")
    runs = w.paras[0].runs
    assert [r.text for r in runs] == ["Welcome to the team, ", "Sam", "."]  # only the name
    assert runs[1].bold and not runs[0].bold
    w.apply({"op": "align", "i": 1, "align": "center"})
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    assert w.paras[1].alignment == WD_ALIGN_PARAGRAPH.CENTER
    w.apply({"op": "add_table", "n_rows": 3, "n_cols": 4})
    assert len(w.doc.tables) == 1 and len(w.doc.tables[0].columns) == 4
    assert w.apply({"op": "page_break"}) == "Added a page break"


async def test_typing_into_a_blank_first_line_above_a_table():
    # A new doc with a table: a blank first paragraph, then the table. Typing "team" into
    # that line used to aim at the table's start, and Google answered 400.
    from pyxi.agents.ledger import GoogleDoc

    g = FakeGoogle()
    g.doc["body"]["content"] = [
        {"endIndex": 1, "sectionBreak": {}},
        {"startIndex": 1, "endIndex": 2, "paragraph": {"elements": [{"startIndex": 1, "textRun": {"content": "\n"}}]}},
        {"startIndex": 2, "endIndex": 40, "table": {"tableRows": []}},
        {"startIndex": 40, "endIndex": 41, "paragraph": {"elements": [{"startIndex": 40, "textRun": {"content": "\n"}}]}},
    ]
    sent = []

    async def docs_batch(did, requests):
        sent.append(requests)
        return {}

    g.docs_batch = docs_batch
    d = GoogleDoc(g, "doc1")
    await d.load()
    await d.apply({"op": "add_paragraph", "text": "team", "after": 0})
    insert = sent[0][0]["insertText"]
    assert insert == {"location": {"index": 1}, "text": "team"}  # into the blank line itself

    g.doc["body"]["content"][1]["paragraph"]["elements"][0]["textRun"]["content"] = "Hello\n"
    g.doc["body"]["content"][1]["endIndex"] = 7
    await d.load()
    await d.apply({"op": "add_paragraph", "text": "team", "after": 0})
    assert sent[1][0]["insertText"] == {"location": {"index": 6}, "text": "\nteam"}  # after it


async def test_the_outline_shows_the_table_and_text_can_go_at_the_very_top():
    from pyxi.agents.ledger import GoogleDoc

    g = FakeGoogle()
    g.doc["body"]["content"] = [
        {"endIndex": 1, "sectionBreak": {}},
        {"startIndex": 1, "endIndex": 7, "paragraph": {"elements": [{"startIndex": 1, "textRun": {"content": "Hello\n"}}]}},
        {"startIndex": 7, "endIndex": 40, "table": {"rows": 5, "columns": 5, "tableRows": []}},
        {"startIndex": 40, "endIndex": 41, "paragraph": {"elements": [{"startIndex": 40, "textRun": {"content": "\n"}}]}},
    ]
    sent = []

    async def docs_batch(did, requests):
        sent.append(requests)
        return {}

    g.docs_batch = docs_batch
    d = GoogleDoc(g, "doc1")
    await d.load()
    outline = d.describe()
    assert "[0] (" in outline and "a table here: 5 rows x 5 columns" in outline and "[1] (empty line)" in outline
    assert outline.index("Hello") < outline.index("a table here") < outline.index("[1] (empty line)")
    await d.apply({"op": "add_heading", "text": "team", "level": 1, "after": "start"})
    assert sent[0][0]["insertText"] == {"location": {"index": 1}, "text": "team\n"}  # above Hello
