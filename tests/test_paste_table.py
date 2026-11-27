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
    check_words,
    clip_message,
    looks_like_a_sheet,
    plain_cells,
    seen,
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


# ------------------------------------------------------------------ did it arrive
class _Sheet:
    """A screen that shows one window: what can be read in it, look by look."""

    def __init__(self, brain, looks: list[list[str]]) -> None:
        from PIL import Image

        from pyxi.screen.ocr import TextLine

        self.looks, self.taken, self.paused = list(looks), 0, False
        self._line, self._shot = TextLine, Image.new("RGB", (8, 8))
        self._base = brain.screen.ctx

    async def capture(self, pixels: bool = True, follow_mouse: bool = True, hwnd: int | None = None):
        import dataclasses

        words = self.looks[min(self.taken, len(self.looks) - 1)]
        self.taken += 1
        return dataclasses.replace(
            self._base, screenshot=self._shot, text=[self._line(w, (0, 0, 10, 10)) for w in words]
        )

    def is_excluded(self, window):
        return None


def _on_a_sheet(settings, monkeypatch, looks, llm_calls):
    from pyxi import windows

    monkeypatch.setattr(windows, "available", lambda: True)
    monkeypatch.setattr(windows, "open_windows", lambda minimized=False: [(4, "Untitled Spreadsheet.xlsx", "genoffice.exe")])
    monkeypatch.setattr(windows, "focus", lambda hwnd: True)
    monkeypatch.setattr(windows, "foreground", lambda: 4)
    llm = Scripted(*llm_calls)
    brain, rec = make_brain(settings, llm=llm)
    brain._named_window = lambda text, ctx: 4 if ctx is None else None  # type: ignore[method-assign]
    brain.screen = _Sheet(brain, looks)
    return brain, rec, llm


def test_the_cells_to_look_for_are_plain_words_and_numbers():
    text = table_clip(ROWS, bordered=False)["text"]
    assert check_words(text) == ["region", "sales", "west", "1200", "eastnorth", "800", "total"]  # no formula
    assert check_words(table_clip([[""] * 3] * 3, bordered=False)["text"]) == []  # nothing to tell it by
    assert seen(["Region | Sales", "W e s t   1200"], ["region", "west", "total"]) == 2
    assert seen(["Region Sales West", "Region Sales West"], ["region", "west", "total"]) == 4  # counted


async def test_a_paste_that_never_arrived_is_not_called_done(settings, monkeypatch, fast_paste):
    """Live: "Can you create a pivot table over here?", into a spreadsheet app in front of the
    founder. Three pastes, an empty sheet, and "Done." each time, until he asked what was
    wrong. Pyxi looks at the window after pasting now."""
    empty = ["Untitled Spreadsheet.xlsx", "A", "B", "C"]
    brain, rec, llm = _on_a_sheet(
        settings,
        monkeypatch,
        [empty],  # however often it looks, the sheet stays empty
        [
            [("say", {"text": "Done."}), ("paste_table", {"rows": ROWS})],
            [("say", {"text": "That didn't land. It's copied: click the first cell and press ctrl+v."})],
        ],
    )
    await brain.turn("create a sample sales table here")
    kinds = [a["kind"] for a in rec.of("action")]
    assert kinds == ["insert_table", "copy"]  # sent, and when it didn't arrive, left copied for them
    assert rec.of("action")[1]["value"].startswith("Region\tSales")
    paste = next(c for c in brain._calls if c["tool"] == "paste_table")
    assert paste["outcome"] == "refused" and "did NOT go in" in paste["note"]
    assert "Done." not in [s["text"] for s in rec.of("say")]  # the claim is never spoken
    assert "did NOT go in" in llm.prompts[-1] and "copied" in llm.prompts[-1]


async def test_a_paste_that_arrived_is_done_and_one_that_cant_be_checked_is_trusted(settings, monkeypatch, fast_paste):
    empty = ["Untitled Spreadsheet.xlsx", "A", "B", "C"]
    full = [*empty, "Region", "Sales", "West", "1200"]
    # The cells show up at the second look after pasting: an app can take a moment to draw.
    brain, rec, _ = _on_a_sheet(
        settings, monkeypatch, [empty, empty, full], [[("paste_table", {"rows": ROWS}), ("say", {"text": "It's in."})]]
    )
    await brain.turn("create a sample sales table here")
    assert [a["kind"] for a in rec.of("action")] == ["insert_table"]
    assert "outcome" not in next(c for c in brain._calls if c["tool"] == "paste_table")
    assert [s["text"] for s in rec.of("say")] == ["It's in."]
    # The same table pasted beside itself (live: reported as not landed, because no word in
    # it was new): its cells are on screen twice now, and that is what counts.
    once = ["Region", "Sales", "West", "1200", "East & North", "800", "Total"]
    brain, rec, _ = _on_a_sheet(
        settings, monkeypatch, [once, once, [*once, *once]],  # the turn's look, before, after
        [[("paste_table", {"rows": ROWS}), ("say", {"text": "It's in."})]],
    )
    await brain.turn("put that table in again")
    assert [a["kind"] for a in rec.of("action")] == ["insert_table"]
    # And the same table on screen, no more often than before: that one did not arrive.
    brain, rec, _ = _on_a_sheet(
        settings, monkeypatch, [once],
        [[("paste_table", {"rows": ROWS})], [("say", {"text": "It didn't land."})]],
    )
    await brain.turn("put that table in again")
    assert [a["kind"] for a in rec.of("action")] == ["insert_table", "copy"]
    # A window whose text can't be read at all (a private one, a look that fails): trusted too.
    brain, rec, _ = _on_a_sheet(settings, monkeypatch, [[]], [[("paste_table", {"rows": ROWS}), ("say", {"text": "It's in."})]])
    await brain.turn("create a sample sales table here")
    assert [a["kind"] for a in rec.of("action")] == ["insert_table"]


async def test_when_it_doesnt_land_pyxi_puts_the_cursor_in_the_cell_and_pastes_again(settings, monkeypatch, fast_paste):
    """Live: a spreadsheet Pyxi had just opened with the mouse. "The table didn't land, click
    cell A1 first", four times, while the founder asked it to just do it."""
    empty = ["Untitled Spreadsheet.xlsx", "A", "B", "C"]
    full = [*empty, "Region", "Sales", "West", "1200"]
    # the turn's look, before, three looks after the first paste, then after the click
    looks = [empty, empty, empty, empty, empty, full]
    brain, rec, _ = _on_a_sheet(settings, monkeypatch, looks, [[("paste_table", {"rows": ROWS}), ("say", {"text": "It's in."})]])
    clicks = []

    async def click(target):
        clicks.append(target)
        return True

    brain._put_cursor_in_cell = click  # type: ignore[method-assign]
    await brain.turn("create a sample sales table here")
    assert clicks == [4]
    assert [a["kind"] for a in rec.of("action")] == ["insert_table", "insert_table"]  # pasted again, nothing left copied
    assert "outcome" not in next(c for c in brain._calls if c["tool"] == "paste_table")
    assert [s["text"] for s in rec.of("say")] == ["It's in."]


async def test_after_its_own_mouse_work_there_pyxi_clicks_the_cell_first(settings, monkeypatch, fast_paste):
    """Live: Pyxi opened a new sheet by clicking, and the paste that followed took 11 seconds of
    a 15 second turn: a first paste into nowhere, three looks to find that out, then the click.
    The keyboard is known not to be in the grid, so the click comes first and one paste lands."""
    import time

    empty = ["Untitled Spreadsheet.xlsx", "A", "B", "C"]
    full = [*empty, "Region", "Sales", "West", "1200"]
    brain, rec, _ = _on_a_sheet(
        settings, monkeypatch, [empty, empty, full], [[("paste_table", {"rows": ROWS}), ("say", {"text": "It's in."})]]
    )
    clicks = []

    async def click(target):
        clicks.append(len(rec.of("action")))  # how many pastes had gone before the click
        return True

    brain._put_cursor_in_cell = click  # type: ignore[method-assign]
    brain._mouse_was_in = (4, time.monotonic())
    await brain.turn("create a sample sales table here")
    assert clicks == [0]  # clicked before anything was pasted
    assert [a["kind"] for a in rec.of("action")] == ["insert_table"]  # and one paste did it
    assert brain._mouse_was_in == (0, 0.0)  # the next paste there does not click again


async def test_no_click_without_their_standing_allow_for_the_mouse(settings, monkeypatch, fast_paste):
    from pyxi import hands

    empty = ["Untitled Spreadsheet.xlsx", "A", "B", "C"]
    brain, rec, _ = _on_a_sheet(
        settings, monkeypatch, [empty],
        [[("paste_table", {"rows": ROWS})], [("say", {"text": "It didn't land. It's copied."})]],
    )
    pressed = []
    monkeypatch.setattr(hands, "available", lambda: True)
    monkeypatch.setattr(hands, "Hands", lambda: pressed.append("made") or None)
    brain._hands_always = lambda: False  # type: ignore[method-assign]
    await brain.turn("create a sample sales table here")
    assert pressed == []  # the mouse was never touched
    assert [a["kind"] for a in rec.of("action")] == ["insert_table", "copy"]


@pytest.fixture
def fast_paste(monkeypatch):
    """The waits between looks, without the waiting."""
    import asyncio

    real = asyncio.sleep

    async def nap(seconds: float, *a, **k):
        await real(0)

    monkeypatch.setattr("pyxi.companion.brain.asyncio.sleep", nap)
