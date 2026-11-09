"""A table pasted into whatever is open in front of the user, in one step.

From a competitor's demo: a sample sales table landed in Excel on the web in under a second, by
one paste into the selected cell. Pyxi's only route into a spreadsheet was Ledger, which reaches
Google files it has permission for and files on the PC, so a sheet in a browser tab (or a Google
file in another account) ended in "I can't open that".
"""

from __future__ import annotations

import json

import pytest

from pyxi.companion import filework, prompts
from pyxi.companion.paste import PasteError, looks_like_a_sheet, table_clip
from test_brain_and_server import make_brain
from test_notes_and_guides import Scripted

ROWS = [["Region", "Sales"], ["West", 1200], ["East & North", 800.0], ["Total", "=SUM(B2:B3)"]]


def test_a_table_is_cells_for_a_sheet_and_a_real_table_for_a_document():
    sheet = table_clip(ROWS, bordered=False)
    assert sheet["text"] == "Region\tSales\r\nWest\t1200\r\nEast & North\t800\r\nTotal\t=SUM(B2:B3)"
    assert sheet["html"].startswith("<table><tr><td>Region</td><td>Sales</td></tr>")
    assert "East &amp; North" in sheet["html"] and "<th>" not in sheet["html"]  # bare cells, escaped
    doc = table_clip(ROWS, bordered=True)
    assert 'border="1"' in doc["html"] and "<th>Region</th>" in doc["html"]
    # A short row is padded; a tab or line break inside a cell can't split it in two.
    ragged = table_clip([["a", "b", "c"], ["one\ttwo\nthree"]], bordered=False)
    assert ragged["text"] == "a\tb\tc\r\none two three\t\t"
    # An empty table of a size is a real request in a document ("insert a 5 by 5 table").
    empty = table_clip([[""] * 5] * 5, bordered=True)
    assert empty["html"].count("<tr>") == 5 and empty["html"].count("<td></td>") == 25


def test_rows_that_cant_be_pasted_say_why():
    for bad in (None, [], "a,b", [[]]):
        with pytest.raises(PasteError, match="give rows"):
            table_clip(bad, bordered=False)
    with pytest.raises(PasteError, match="too big"):
        table_clip([["x"]] * 501, bordered=False)


def test_a_spreadsheet_is_told_from_a_document_by_its_window():
    for title in ("Book 3.xlsx - Mozilla Firefox", "Untitled spreadsheet - Google Sheets", "Budget - Excel", "Tasks - Airtable"):
        assert looks_like_a_sheet(title), title
    for title in ("Untitled document - Google Docs", "Letter - Word", "Inbox - Gmail", "", None):
        assert not looks_like_a_sheet(title), title


async def test_the_table_goes_to_the_app_as_one_paste(settings):
    llm = Scripted([("paste_table", {"rows": ROWS}), ("say", {"text": "It's in."})])
    brain, rec = make_brain(settings, llm=llm)
    await brain.turn("put some sample sales data in this sheet")
    acts = rec.of("action")
    assert [a["kind"] for a in acts] == ["insert_table"]
    clip = json.loads(acts[0]["value"])
    assert clip["text"].startswith("Region\tSales\r\nWest\t1200") and clip["html"].startswith("<table")


async def test_a_sheet_gets_bare_cells_and_a_refused_focus_becomes_a_copy(settings, monkeypatch):
    from pyxi import windows

    front = {"hwnd": 0}
    monkeypatch.setattr(windows, "available", lambda: True)
    monkeypatch.setattr(windows, "open_windows", lambda minimized=False: [(4, "Book 3.xlsx - Mozilla Firefox", "firefox.exe"), (5, "Notes - Word", "winword.exe")])
    monkeypatch.setattr(windows, "focus", lambda hwnd: front.update(hwnd=hwnd) or True)
    monkeypatch.setattr(windows, "foreground", lambda: front["hwnd"])
    brain, rec = make_brain(settings, llm=Scripted([("paste_table", {"rows": ROWS}), ("say", {"text": "Ok."})]))
    brain._named_window = lambda text, ctx: 4 if ctx is None else None  # type: ignore[method-assign]
    await brain.turn("sample data here")
    assert "border" not in json.loads(rec.of("action")[0]["value"])["html"]  # a spreadsheet: bare cells
    brain, rec = make_brain(settings, llm=Scripted([("paste_table", {"rows": ROWS}), ("say", {"text": "Ok."})]))
    brain._named_window = lambda text, ctx: 5 if ctx is None else None  # type: ignore[method-assign]
    await brain.turn("put this table in my Word notes")
    assert 'border="1"' in json.loads(rec.of("action")[0]["value"])["html"]  # a document: a drawn table
    # Windows won't hand over the focus: the cells are copied, and they're told to paste.
    monkeypatch.setattr(windows, "focus", lambda hwnd: False)
    front["hwnd"] = 9
    brain, rec = make_brain(settings, llm=Scripted([("paste_table", {"rows": ROWS}), ("say", {"text": "Ok."})]))
    brain._named_window = lambda text, ctx: 4 if ctx is None else None  # type: ignore[method-assign]
    await brain.turn("sample data here")
    assert [(a["kind"], a["value"][:12]) for a in rec.of("action")] == [("copy", "Region\tSales")]


async def test_bad_rows_come_back_to_the_model_and_nothing_is_pasted(settings):
    brain, rec = make_brain(settings, llm=Scripted([("paste_table", {"rows": []}), ("say", {"text": "Hm."})]))
    await brain.turn("paste a table")
    assert rec.of("action") == []


def test_a_file_ledger_cant_open_is_not_a_dead_end():
    """Live: 'that doc belongs to a different Google account', and the table never went in."""
    assert "call paste_table now" in filework._PASTE_INSTEAD
    assert "is `paste_table`" in prompts.SYSTEM and "never the end of it" in prompts.SYSTEM
