"""A table pasted into whatever is open in front of the user, in one step.

From a competitor's demo: a sample sales table landed in Excel on the web in under a second, by
one paste into the selected cell. Pyxi's only route into a spreadsheet was Ledger, which reaches
Google files it has permission for and files on the PC, so a sheet in a browser tab (or a Google
file in another account) ended in "I can't open that".
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pyxi.companion import filework, prompts
from pyxi.companion.paste import (
    MAX_MESSAGE,
    PasteError,
    clip_message,
    looks_like_a_sheet,
    plain_cells,
    table_clip,
)
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


# ------------------------------------------------------------------ what the app will take
def test_a_large_table_in_chinese_fits_in_one_message():
    """Escaped as \\uXXXX it was six times the size, over the app's limit: the app pasted
    nothing, said nothing, and Pyxi said the table was in."""
    rows = [["数据" * 4 for _ in range(50)] for _ in range(450)]  # 180,000 characters of cells
    message = clip_message(table_clip(rows, bordered=False))
    assert len(message) < MAX_MESSAGE and "数据数据" in message and "\\u" not in message
    assert json.loads(message)["text"].startswith("数据数据数据数据\t")


def test_the_brain_refuses_what_the_app_would_drop():
    # The two limits are one number, and the brain counts the way the app does (UTF-16 units).
    app = (Path(__file__).parents[1] / "desktop/src/main/tableClip.ts").read_text(encoding="utf-8")
    assert f"const MAX = {MAX_MESSAGE:_};" in app
    bell = table_clip([["\x07" * 4000 for _ in range(45)]], bordered=False)  # 6 characters each, escaped
    assert len(json.dumps(bell, ensure_ascii=False)) > MAX_MESSAGE
    with pytest.raises(PasteError, match="too much text"):
        clip_message(bell)
    faces = {"text": "\U0001F600" * 999_990, "html": "<table></table>"}  # two units each in the app
    assert len(json.dumps(faces, ensure_ascii=False)) < MAX_MESSAGE
    with pytest.raises(PasteError, match="too much text"):
        clip_message(faces)


async def test_a_table_the_app_would_drop_is_refused_and_the_model_is_told(settings):
    rows = [["\x07" * 4000 for _ in range(45)]]
    llm = Scripted([("paste_table", {"rows": rows}), ("say", {"text": "That one's too big."})])
    brain, rec = make_brain(settings, llm=llm)
    await brain.turn("paste this in")
    assert rec.of("action") == []  # nothing was sent for the app to drop in silence
    assert brain._calls[0]["note"].startswith("paste_table: that's too much text")


# ------------------------------------------------------------------ formulas from outside
def test_cells_a_spreadsheet_would_run_become_plain_text():
    rows = [
        ["Item", "Price", "Note"],
        ["=HYPERLINK(\"https://evil.example\",\"Click\")", "+SUM(A1:A9)", "-A1"],
        ["@SUM(A1)", "-(1+2)", "-1+cmd|' /C calc'!A0"],
        ["-5", "+12%", "- a note"],
        ["12.50", 7, "a = b"],
        "one cell",
    ]
    plain, changed = plain_cells(rows)
    assert changed == 6
    assert plain[1] == ["'=HYPERLINK(\"https://evil.example\",\"Click\")", "'+SUM(A1:A9)", "'-A1"]
    assert plain[2] == ["'@SUM(A1)", "'-(1+2)", "'-1+cmd|' /C calc'!A0"]
    assert plain[3:] == [["-5", "+12%", "- a note"], ["12.50", 7, "a = b"], ["one cell"]]
    assert rows[1][2] == "-A1"  # the rows given are not changed
    assert plain_cells("a,b") == ("a,b", 0)  # not rows: table_clip says so


async def test_formulas_from_a_page_are_pasted_as_text_and_their_own_stay_live(
    settings, monkeypatch
):
    from pyxi import web

    async def fake_search(query, max_results=6):
        return "Results:\n- Price list (shop.example.com)\n  Total: =HYPERLINK(...)"

    monkeypatch.setattr(web, "search_text", fake_search)
    rows = [["Item", "Total"], ["Kettle", '=HYPERLINK("https://evil.example","Click")'], ["Sum", "-5"]]
    llm = Scripted(
        [("web_search", {"query": "kettle price list"})],
        [("say", {"text": "Putting the prices in now."}), ("paste_table", {"rows": rows})],
        [("say", {"text": "One cell went in as text, since it came from the web."})],
    )
    brain, rec = make_brain(settings, llm=llm)
    await brain.turn("look up the kettle prices and paste them in this sheet")
    [pasted] = rec.of("action")
    text = json.loads(pasted["value"])["text"]
    assert text == "Item\tTotal\r\nKettle\t'=HYPERLINK(\"https://evil.example\",\"Click\")\r\nSum\t-5"
    # It went in, so the line that said so is spoken; then the model is told, and says the rest.
    told = llm.prompts[-1]
    assert "1 cell that began like a formula" in told and "the web search" in told
    assert [s["text"] for s in rec.of("say")] == [
        "Putting the prices in now.",
        "One cell went in as text, since it came from the web.",
    ]
    paste = next(c for c in brain._calls if c["tool"] == "paste_table")
    assert "outcome" not in paste  # pasted, not refused
    # Rows from their own words: a formula is a formula.
    llm = Scripted([("paste_table", {"rows": ROWS}), ("say", {"text": "It's in."})])
    brain, rec = make_brain(settings, llm=llm)
    await brain.turn("put some sample sales data with a total in this sheet")
    assert json.loads(rec.of("action")[0]["value"])["text"].endswith("Total\t=SUM(B2:B3)")
    assert len(llm.prompts) == 1
