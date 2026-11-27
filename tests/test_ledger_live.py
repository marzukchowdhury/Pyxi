"""Ledger's direct route into Excel, Word and PowerPoint (agents/ledger_live.py), against a
pretend app (tests/office_fakes.py): which calls each typed edit turns into, what is refused
before anything is sent, the card before cells are replaced, and every way back to the old
route. scripts/office_live_check.py runs the same edits in the real apps."""

from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

import pytest

from office_fakes import (
    SALES,
    PowerPoint,
    Stuck,
    Word,
    busy,
    cells,
    excel,
    live,
    refused,
)
from pyxi.agents import ledger, ledger_live
from pyxi.agents.ledger import run_ledger
from pyxi.agents.ledger_common import LedgerError
from pyxi.agents.ledger_live import ComThread, Declined, ExcelLive, Unreachable, Unsupported
from pyxi.agents.ledger_live_docs import DeckLive, WordLive
from pyxi.companion import brain as brain_module
from pyxi.companion import filework, prompts, tools
from pyxi.screen.context import ScreenContext, WindowInfo
from test_brain_and_server import make_brain
from test_connectors_and_docs import Script

RED = 0x2530D9  # Ledger's red (#D93025) the way Office counts a colour, blue first


async def opened(cls, app, **kw):
    target = live(cls, app, **kw)
    await target.load()
    return target


async def run(target, /, **op):
    try:
        return await target.apply(op)
    finally:
        await asyncio.sleep(0)


# ============================================================================ Excel
async def test_the_outline_is_read_from_the_open_workbook():
    app, sheet = excel()
    sheet.cells[(5, 4)] = "=SUM(D2:D4)"
    t = await opened(ExcelLive, app)
    text = t.describe()
    assert text.startswith("Excel workbook Book1, open in Excel now. It has never been saved.")
    assert "Sheet 'Sales': 5 rows x 4 columns; header row 1." in text
    assert "A2='West' | B2='Ana Mock' | C2=3 | D2=1200.5" in text
    assert "D5==SUM(D2:D4)" in text  # a formula as it's typed, not its result
    assert t.title == "Book1 in Excel" and t.kind == "xlsx_live"
    await t.close()


async def test_set_cells_types_each_cell_and_checks_every_address_first():
    app, sheet = excel(rows=None)
    t = await opened(ExcelLive, app)
    said = await run(t, op="set_cells", cells=cells(SALES))
    assert said == "Set 16 cells on Sales"
    assert sheet.text("A1") == "Region" and sheet.text("D2") == 1200.5
    assert "Sales.Cells(2, 4).Formula = 1200.5" in app.log
    before = dict(sheet.cells)
    with pytest.raises(LedgerError, match="isn't a cell"):
        await run(t, op="set_cells", cells={"A9": "ok", "A1:B2": "no", "Sheet2!A1": 1})
    assert sheet.cells == before  # one bad address: none of them written


async def test_format_maps_each_look_to_one_property():
    app, _ = excel()
    t = await opened(ExcelLive, app)
    said = await run(
        t, op="format", range="A1:D1", bold=True, italic=True, color="red", fill="#FFF2CC",
        number_format="$#,##0.00", align="center", border="all", size=12, font="Arial", wrap=True,
    )  # fmt: skip
    assert said.startswith("Made A1:D1 on Sales bold, italic")
    for line in (
        "Sales.Range(A1:D1).Font.Bold = True",
        "Sales.Range(A1:D1).Font.Italic = True",
        f"Sales.Range(A1:D1).Font.Color = {RED}",
        f"Sales.Range(A1:D1).Interior.Color = {0xCCF2FF}",
        "Sales.Range(A1:D1).NumberFormat = '$#,##0.00'",
        "Sales.Range(A1:D1).HorizontalAlignment = -4108",
        "Sales.Range(A1:D1).Borders.LineStyle = 1",
        "Sales.Range(A1:D1).Font.Size = 12.0",
        "Sales.Range(A1:D1).Font.Name = 'Arial'",
        "Sales.Range(A1:D1).WrapText = True",
    ):
        assert line in app.log, line


@pytest.mark.parametrize(
    "op, why",
    [
        ({"range": "A1:D1", "color": "reddish"}, "can't read the color"),
        ({"range": "A1:D1"}, "something to change"),
        ({"range": "A1:D1); DROP", "bold": True}, "isn't a cell range"),
        ({"range": "A1:D1", "align": "sideways"}, "align should be"),
        ({"range": "A1:D1", "size": "big"}, "should be a number"),
        ({"range": "'Nope'!A1", "bold": True}, "no sheet called"),
    ],
)
async def test_a_format_that_is_wrong_sends_nothing(op, why):
    app, _ = excel()
    t = await opened(ExcelLive, app)
    with pytest.raises(LedgerError, match=why):
        await run(t, op="format", **op)
    assert app.log == []


async def test_totals_row_sums_the_number_columns_under_the_table():
    app, sheet = excel()
    t = await opened(ExcelLive, app)
    said = await run(t, op="totals_row")
    assert said == "Added a Total row (row 5) on Sales: Units 10, Sales 2,300.5"
    assert sheet.text("A5") == "Total"
    assert sheet.text("C5") == "=SUM(C2:C4)" and sheet.text("D5") == "=SUM(D2:D4)"
    assert sheet.text("B5") is None  # names are not totalled
    assert "Sales.Cells(5, 4).Font.Bold = True" in app.log
    assert await run(t, op="totals_row") == "Sales already ends with a total row (row 5)"
    with pytest.raises(LedgerError, match="SUM, AVERAGE"):
        await run(t, op="totals_row", function="EVAL")


async def test_a_pivot_beside_the_data_is_left_out_of_totals_and_sorts():
    app, sheet = excel()
    sheet.cells |= {(1, 6): "Row Labels", (1, 7): "Sum of Sales", (2, 6): "East", (2, 7): 800,
                    (3, 6): "West", (3, 7): 1500.5, (4, 6): "Grand Total", (4, 7): 2300.5,
                    (5, 6): "x", (6, 6): "y"}  # fmt: skip
    t = await opened(ExcelLive, app)
    assert "row 5" in await run(t, op="totals_row")
    assert sheet.text("G5") is None and sheet.text("G7") is None
    await run(t, op="sort", by="Sales", descending=True)
    assert "Sales.Range(A2:D4).Sort(Sales.Cells(2, 4), 2)" in app.log  # never F or G


async def test_sort_by_two_columns_and_by_a_given_range():
    app, _ = excel()
    t = await opened(ExcelLive, app)
    said = await run(t, op="sort", by="Region", then_by="D")
    assert said == "Sorted rows 2 to 4 on Sales by Region"
    assert "Sales.Range(A2:D4).Sort(Sales.Cells(2, 1), 1, Sales.Cells(2, 4), -, 1)" in app.log
    await run(t, op="sort", by="Units", range="A2:D3", descending=True)
    assert "Sales.Range(A2:D3).Sort(Sales.Cells(2, 3), 2)" in app.log
    with pytest.raises(LedgerError, match="no column called 'Profit'"):
        await run(t, op="sort", by="Profit")


async def test_pivot_is_a_real_pivot_table_beside_the_data():
    app, sheet = excel()
    t = await opened(ExcelLive, app)
    said = await run(t, op="pivot", group_by="Region", value="Sales", agg="sum")
    assert said == (
        "Made a pivot table beside the data on Sales, at F1: sum of Sales by Region, 2 groups"
    )
    made = "Book.PivotCaches().Create(1, Sales.Range(A1:D4)).CreatePivotTable(Sales.Range(F1), 'PyxiPivot1')"
    assert "Book.PivotCaches().Create(1, Sales.Range(A1:D4))" in app.log
    assert made in app.log
    assert f"{made}.PivotFields('Region').Orientation = 1" in app.log
    assert f"{made}.AddDataField({made}.PivotFields('Sales'), 'Sum of Sales', -4157)" in app.log
    sheet.cells[(5, 1)] = "Total"  # a totals row is not data
    sheet.pivots.append(SimpleNamespace(Name="PyxiPivot1"))
    await run(t, op="pivot", group_by="A", agg="count", target="By region")
    assert "Sheet2.Name = 'By region'" in app.log
    assert any("Create(1, Sales.Range(A1:D4)).CreatePivotTable(By region.Range(A1)" in x for x in app.log)
    for bad, why in (({"group_by": "Nope"}, "no column"), ({"group_by": "Region", "agg": "median"}, "sum, count")):
        with pytest.raises(LedgerError, match=why):
            await run(t, op="pivot", **bad)


async def test_chart_goes_beside_the_data_from_two_columns():
    app, _ = excel()
    t = await opened(ExcelLive, app)
    said = await run(t, op="chart", labels="Rep", values="Sales", type="pie", title="Share")
    assert said == "Added a pie chart of Sales on Sales"
    added = "Sales.Shapes.AddChart2(-1, 5, 60.0, 10.0, 420, 260)"  # at F1, a pie
    assert added in app.log
    assert f"{added}.Chart.SetSourceData(Union(Sales.Range(B1:B4), Sales.Range(D1:D4)))" in app.log
    assert f"{added}.Chart.ChartTitle.Text = 'Share'" in app.log
    with pytest.raises(LedgerError, match="chart type"):
        await run(t, op="chart", values="Sales", type="radar")


async def test_widths_freezing_filters_and_names():
    app, sheet = excel()
    t = await opened(ExcelLive, app)
    await run(t, op="resize", columns="a:d", width=145)
    assert "Sales.Range(A:D).EntireColumn.ColumnWidth = 20.0" in app.log
    await run(t, op="resize", columns="B", rows="1:2", height=40)
    assert "Sales.Range(B:B).EntireColumn.AutoFit()" in app.log
    assert "Sales.Range(1:2).EntireRow.RowHeight = 30.0" in app.log
    assert await run(t, op="freeze", rows=1) == "Froze 1 row and 0 columns on Sales"
    at = app.log.index("Sales.Activate()")
    assert app.log[at + 1 : at + 5] == [
        "Window.FreezePanes = False", "Window.SplitColumn = 0", "Window.SplitRow = 1",
        "Window.FreezePanes = True",
    ]  # fmt: skip
    assert await run(t, op="filter") == "Put filter buttons on A1:D4 on Sales"
    assert await run(t, op="filter") == "Sales already has filter buttons"  # never toggled off
    await run(t, op="filter", off=True)
    assert sheet.AutoFilterMode is False
    assert await run(t, op="rename_sheet", name="Q3") == "Renamed sheet Sales to Q3"
    assert sheet.name == "Q3"
    assert await run(t, op="append_row", values=["North", "Di Pretend", 7, 2100]) == "Added row 5 on Q3"
    with pytest.raises(LedgerError, match="with no"):
        await run(t, op="rename_sheet", name="a/b")
    for bad in ({"columns": "A1:B2"}, {"columns": "A:D", "width": 99999}, {}):
        with pytest.raises(LedgerError):
            await run(t, op="resize", **bad)


async def test_rows_columns_sheets_and_replacing():
    app, sheet = excel()
    t = await opened(ExcelLive, app)
    await run(t, op="insert_rows", at=2, count=2)
    assert "Sales.Range(2:3).EntireRow.Insert()" in app.log
    await run(t, op="insert_columns", at="c")
    assert "Sales.Range(C:C).EntireColumn.Insert()" in app.log
    said = await run(t, op="find_replace", find="west", replace="Coast")
    assert said == "Replaced 'west' with 'Coast' in 2 cells on Sales"
    assert "Sales.UsedRange.Replace('west', 'Coast', 2, -, False)" in app.log
    assert "isn't anywhere" in await run(t, op="find_replace", find="zzz", replace="y")
    await run(t, op="add_column", header="Each", formula="=D{r}/C{r}")
    assert sheet.text("E1") == "Each" and sheet.text("E3") == "=D3/C3"
    with pytest.raises(LedgerError, match="formula with"):
        await run(t, op="add_column", header="X", formula="=NOW()")
    assert await run(t, op="add_sheet", name="Notes", rows=[["Note"], ["made up"]]) == "Added sheet Notes"
    assert app.book.sheets[1].text("A2") == "made up"
    with pytest.raises(LedgerError, match="already a sheet"):
        await run(t, op="add_sheet", name="notes")
    assert await run(t, op="set_cells", cells={"B9": 1}) == "Set 1 cell on Sales"  # still the sheet they had open


@pytest.mark.parametrize(
    "formula",
    ["=cmd|' /C calc'!A0", "+cmd|'/c notepad'!A1", '=CALL("kernel32","WinExec","JCJ","calc",1)',
     '=REGISTER.ID("a","b")', "@EXEC(\"calc\")", '=RTD("some.server",,"x")'],
)  # fmt: skip
async def test_a_formula_that_would_start_a_program_is_never_typed(formula):
    app, sheet = excel()
    t = await opened(ExcelLive, app)
    for op in (
        {"op": "set_cells", "cells": {"F1": "fine", "F2": formula}},
        {"op": "append_row", "values": ["x", formula]},
        {"op": "add_sheet", "name": "New", "rows": [["a"], [formula]]},
        {"op": "find_replace", "find": "West", "replace": formula},
    ):
        with pytest.raises(LedgerError, match="run another program"):
            await t.apply(op)
    assert app.log == [] and sheet.text("F1") is None and len(app.book.sheets) == 1
    assert await run(t, op="set_cells", cells={"F1": "=SUM(D2:D4)", "F2": "-5", "F3": "a|b"}) == "Set 3 cells on Sales"


async def test_excel_in_the_middle_of_typing_is_busy_before_anything_is_sent():
    app, _ = excel()
    t = await opened(ExcelLive, app)
    app.Ready = False  # a cell is being typed in
    with pytest.raises(Unreachable, match="Excel is busy"):
        await run(t, op="format", range="A1:D1", bold=True)
    assert app.log == [] and not t.sent
    assert "busy" in ledger_live.probe("excel", connect=lambda n, h: app, thread=ComThread("Excel", init=None))[0]


# ---------------------------------------------------------------------------- the card
async def test_replacing_more_than_a_few_filled_cells_asks_first():
    app, sheet = excel()
    asked: list[tuple[str, str]] = []
    answer = [False]

    async def confirm(action, reason):
        asked.append((action, reason))
        return answer[0]

    t = await opened(ExcelLive, app, confirm=confirm)
    over = {"A2": "North", "B2": "X", "C2": 9, "D2": 9}
    with pytest.raises(Declined, match="replace 4 filled cells in Book1"):
        await run(t, op="set_cells", cells=over)
    assert asked[0][0] == "Replace 4 filled cells in Book1?"
    assert "Nothing is saved" in asked[0][1] and "Ctrl+Z" in asked[0][1]
    assert sheet.text("A2") == "West" and app.log == [] and t.declined and not t.sent
    answer[0] = True
    assert await run(t, op="set_cells", cells=over) == "Set 4 cells on Sales"
    assert sheet.text("A2") == "North"
    asked.clear()
    # three filled cells, empty cells, and cells that already say that: no card
    await run(t, op="set_cells", cells={"A3": "a", "B3": "b", "C3": 1, "F9": "new", "A1": "Region"})
    assert asked == []


async def test_clearing_and_deleting_filled_cells_ask_first_and_formats_do_not():
    app, sheet = excel()
    asked: list[str] = []

    async def confirm(action, reason):
        asked.append(action)
        return len(asked) > 2

    t = await opened(ExcelLive, app, confirm=confirm)
    with pytest.raises(Declined):
        await run(t, op="clear", range="A2:D3")
    with pytest.raises(Declined):
        await run(t, op="delete_rows", **{"from": 2, "to": 3})
    assert asked == ["Clear 8 filled cells in Book1?", "Delete 8 filled cells in Book1?"]
    assert sheet.text("A2") == "West" and app.log == []
    assert await run(t, op="clear", range="A2:D3") == "Cleared the values in A2:D3 on Sales"
    assert sheet.text("A2") is None
    await run(t, op="clear", range="A1:D4", what="formats")
    assert len(asked) == 3 and "Sales.Range(A1:D4).ClearFormats()" in app.log
    await run(t, op="delete_columns", **{"from": "F"})  # nothing in it: no card
    assert "Sales.Range(F:F).EntireColumn.Delete()" in app.log and len(asked) == 3


async def test_with_nobody_to_ask_cells_are_not_replaced():
    app, sheet = excel()
    t = await opened(ExcelLive, app)  # no card to show
    with pytest.raises(Declined):
        await run(t, op="clear", range="A1:D4", what="all")
    assert sheet.text("A1") == "Region"


# ---------------------------------------------------------------------------- the ways back
async def test_an_edit_with_no_calls_is_refused_and_nothing_is_sent():
    app, _ = excel()
    t = await opened(ExcelLive, app)
    for name in ("delete_sheet", "merge", "raw", "__init__", "", "apply", "save"):
        with pytest.raises(Unsupported, match="straight in Excel"):
            await run(t, op=name)
    assert app.log == [] and not t.sent and app.book.saved_calls == 0


async def test_no_app_no_workbook_and_a_busy_app_are_unreachable():
    def gone(name, hwnd):
        raise Unreachable("Excel isn't open.")

    t = ExcelLive(None, connect=gone, thread=ComThread("Excel", init=None))
    with pytest.raises(Unreachable, match="isn't open"):
        await t.load()
    app, _ = excel()
    app.book = None
    with pytest.raises(Unreachable, match="no workbook open in Excel"):
        await opened(ExcelLive, app)
    app.book = SimpleNamespace(Worksheets=[], ActiveSheet=None)  # only a chart sheet
    with pytest.raises(Unreachable):
        await opened(ExcelLive, app)

    def typing(name, hwnd):
        raise busy()

    began = time.perf_counter()
    t = ExcelLive(None, connect=typing, thread=ComThread("Excel", init=None))
    with pytest.raises(Unreachable, match="Excel is busy"):
        await t.load()
    assert 1.0 < time.perf_counter() - began < 4.0  # asked again for a moment, then given up


async def test_what_excel_refuses_is_said_in_its_own_words():
    app, sheet = excel()
    t = await opened(ExcelLive, app)
    app.fail = refused("The cell is protected.")
    with pytest.raises(LedgerError, match="Excel said no: The cell is protected") as err:
        await run(t, op="set_cells", cells={"A9": 1})
    assert not isinstance(err.value, Unreachable) and t.sent
    app.fail = busy()
    with pytest.raises(Unreachable, match="busy"):
        await run(t, op="set_cells", cells={"A9": 1})
    assert sheet.text("A9") is None


async def test_an_app_that_does_not_answer_is_given_up_on_in_time():
    stuck = Stuck()
    t = live(ExcelLive, stuck, timeout=0.4)
    began = time.perf_counter()
    with pytest.raises(Unreachable, match=r"didn't answer within 0\.4 seconds"):
        await t.load()
    assert time.perf_counter() - began < 2.0
    asked = stuck.asked
    began = time.perf_counter()
    with pytest.raises(Unreachable, match="stopped answering"):  # refused at once from now on
        await t.apply({"op": "set_cells", "cells": {"A1": 1}})
    assert time.perf_counter() - began < 0.2
    stuck.release.set()  # the box is closed: what was waiting must not start now
    await t.close()
    await asyncio.sleep(0.3)
    assert stuck.asked == asked


def test_a_call_that_was_waiting_when_time_ran_out_is_never_started():
    thread = ComThread("Excel", init=None)
    stuck = Stuck()
    ran: list[str] = []
    with pytest.raises(Unreachable):
        thread.run(lambda: stuck.anything, 0.2)
    with pytest.raises(Unreachable):
        thread.run(lambda: ran.append("late"), 0.2)
    stuck.release.set()
    time.sleep(0.3)
    assert ran == []
    thread.close()


async def test_cells_showing_an_error_do_not_stop_the_read():
    app, sheet = excel()
    sheet.cells[(2, 5)] = "=D2/0"
    sheet.has_error_cells = True
    t = await opened(ExcelLive, app)
    assert "E2==D2/0" in t.describe() and "D2=1200.5" in t.describe()
    assert "row 5" in await run(t, op="totals_row", columns=["Sales"])


def test_the_look_before_a_job_changes_nothing_and_never_raises():
    app, _ = excel()
    kw = {"connect": lambda name, hwnd: app}
    assert ledger_live.probe("excel", thread=ComThread("Excel", init=None), **kw) == ("", "Book1")
    assert app.log == []
    app.book = None
    why, name = ledger_live.probe("excel", thread=ComThread("Excel", init=None), **kw)
    assert "no workbook open" in why and name == ""
    why, _ = ledger_live.probe(
        "excel", timeout=0.3, connect=lambda n, h: Stuck(), thread=ComThread("Excel", init=None)
    )
    assert "didn't answer" in why
    assert ledger_live.probe("notepad")[0].startswith("Pyxi can't edit")
    assert ledger_live.app_of("C:\\Program Files\\Office\\EXCEL.EXE") == "excel"
    assert ledger_live.app_of("WINWORD.EXE") == "word" and ledger_live.app_of("chrome.exe") == ""


# ============================================================================ Word
NOTES = ["Quarterly notes", "Apples sold well. Pears were flat.", "Apples again.", ""]


async def test_word_outline_and_headings():
    app = Word(NOTES, levels={"Quarterly notes": 1})
    t = await opened(WordLive, app)
    text = t.describe()
    assert text.startswith("Word document Document1, open in Word now: 4 paragraphs, 0 tables.")
    assert "[0] (Heading 1) Quarterly notes" in text and "[3] (empty)" in text
    assert await run(t, op="add_heading", text="Next steps", level=2) == "Added the heading 'Next steps'"
    assert app.doc.paras[3] == "Next steps"  # the empty line at the end took it
    assert "Paragraphs(4).Style = -3" in app.log  # Word's own Heading 2
    assert app.log[0] == "UndoRecord.StartCustomRecord('Pyxi: add heading')"
    assert app.log[-1] == "UndoRecord.EndCustomRecord()"  # one step under Undo
    await run(t, op="add_paragraph", text="Order more apples.")
    assert app.doc.paras[4] == "Order more apples." and "Paragraphs(5).Style = -1" in app.log


async def test_word_inserts_keep_to_the_outline_numbers():
    app = Word(NOTES)
    t = await opened(WordLive, app)
    await run(t, op="add_paragraph", text="After the title.", after=0)
    await run(t, op="add_bullets", items=["One", "Two"], after=0)
    await run(t, op="add_paragraph", text="At the top.", after="start")
    assert app.doc.paras[:5] == ["At the top.", "Quarterly notes", "After the title.", "One", "Two"]
    assert "Range(2, 4).ListFormat.ApplyBulletDefault()" in app.log  # the two new lines only
    await run(t, op="set_style", i=1, style="Heading 2")  # the outline's paragraph 1, wherever it is now
    assert "Paragraphs(6).Style = -3" in app.log and app.doc.paras[5].startswith("Apples sold")
    await run(t, op="align", i=0, align="center")
    assert "Paragraphs(2).Alignment = 1" in app.log
    for bad, why in (
        ({"op": "add_paragraph", "text": "x", "after": 99}, "no paragraph 99"),
        ({"op": "set_style", "i": 1, "style": "Fancy"}, "style should be"),
        ({"op": "add_heading", "text": "", "level": 1}, "needs text"),
        ({"op": "align", "i": 0, "align": "up"}, "align should be"),
    ):
        with pytest.raises(LedgerError, match=why):
            await t.apply(bad)


async def test_word_formatting_replacing_tables_and_the_page():
    app = Word(NOTES)
    t = await opened(WordLive, app)
    said = await run(t, op="format_text", find="Apples", bold=True, color="red")
    assert said == "Made 'Apples' bold, red in 2 places"
    assert app.log.count("Content.Font.Bold = True") == 2
    assert app.log.count(f"Content.Font.Color = {RED}") == 2
    await run(t, op="format_text", i=1, italic=True, highlight="yellow")
    assert "Paragraphs(2).Range.Font.Italic = True" in app.log
    assert "Paragraphs(2).Range.HighlightColorIndex = 7" in app.log
    said = await run(t, op="replace_text", find="Pears", replace="Peaches")
    assert said == "Replaced 'Pears' with 'Peaches' in 1 place"
    assert "Find.Execute('Pears', False, False, False, False, False, True, 1, False, 'Peaches', 2)" in app.log
    assert "Peaches were flat" in app.doc.paras[1]
    said = await run(t, op="add_table", rows=[["Fruit", "Boxes"], ["Apples", 12]])
    assert said == "Added a table of 2 rows by 2 columns"
    assert "Tables.Add(Paragraphs(4).Range, 2, 2)" in app.log
    assert "Table1.Cell(2, 2).Range.Text = '12'" in app.log and "Table1.Borders.Enable = True" in app.log
    said = await run(t, op="page_setup", orientation="landscape", margin=1)
    assert said == "Turned the page landscape and set the margins"
    assert "PageSetup.Orientation = 1" in app.log and "PageSetup.LeftMargin = 72.0" in app.log
    for bad, why in (
        ({"op": "format_text", "find": "Bananas", "bold": True}, "isn't in the document"),
        ({"op": "replace_text", "find": "Bananas", "replace": "x"}, "isn't in the document"),
        ({"op": "page_setup", "orientation": "diagonal"}, "landscape or portrait"),
        ({"op": "add_table", "n_rows": 500, "n_cols": 2}, "up to 100"),
        ({"op": "format_text", "find": "Apples"}, "something to change"),
    ):
        with pytest.raises(LedgerError, match=why):
            await t.apply(bad)
    with pytest.raises(Unsupported):
        await t.apply({"op": "delete_paragraph", "i": 0})
    app.doc = None
    with pytest.raises(Unreachable, match="no document open in Word"):
        await opened(WordLive, app)


# ============================================================================ PowerPoint
async def test_powerpoint_slides_text_pictures_and_order(tmp_path):
    app = PowerPoint()
    t = await opened(DeckLive, app)
    assert t.describe().startswith("PowerPoint Presentation1, open in PowerPoint now: 0 slides, each 13.33 x 7.50")
    said = await run(t, op="add_slide", title="Made-up review", subtitle="Nothing here is real")
    assert said == "Added slide 1, 'Made-up review'"
    assert app.log[:2] == ["StartNewUndoEntry()", "Slides.Add(1, 1)"]  # its own step under Undo
    await run(t, op="add_slide", title="Highlights", bullets=["West up", "East flat"], notes="Say so")
    assert "Slides.Add(2, 2)" in app.log
    second = app.deck.slides[1]
    assert second.title == "Highlights" and second.shapes[1].text == "West up\rEast flat"
    assert "Notes.Shapes.Placeholders(2).TextFrame.TextRange.Text = 'Say so'" in app.log
    await run(t, op="add_bullets", slide=2, items=["North new"])
    assert second.shapes[1].text == "West up\rEast flat\rNorth new"
    await run(t, op="set_title", slide=2, text="So far")
    await run(t, op="set_text", slide=1, which="subtitle", text="Still made up")
    assert second.title == "So far" and app.deck.slides[0].shapes[1].text == "Still made up"
    await run(t, op="add_text_box", slide=2, text="A note", bold=True, color="red", x=1, y=1)
    assert "Slide.AddTextbox(1, 72.0, 72.0, 432, 72)" in app.log
    assert "TextBox 3.Text.Font.Bold = -1" in app.log
    assert f"TextBox 3.Text.Font.Color.RGB = {RED}" in app.log
    picture = tmp_path / "made-up.png"
    picture.write_bytes(b"png")
    assert await run(t, op="add_image", slide=2, path=str(picture)) == "Put made-up.png on slide 2"
    assert "Slide.AddPicture(0, -1, 336.0, 162.0, -1, -1)" in app.log  # centred, its own size
    await run(t, op="format_text", slide=2, which="title", size=40)
    assert "Title 1.Text.Font.Size = 40.0" in app.log
    assert await run(t, op="move_slide", slide=2, to=1) == "Moved slide 2 to place 1"
    assert app.deck.slides[0].title == "So far"
    await t.load()
    assert "Slide 1:\n  title (Title 1): 'So far'\n  body (Placeholder 2): 'West up / East flat / North new'" in t.describe()
    for bad, why in (
        ({"op": "set_title", "slide": 9, "text": "x"}, "no slide 9; the deck has 2"),
        ({"op": "add_image", "slide": 1, "url": "https://example.com/a.png"}, "needs path"),
        ({"op": "add_image", "slide": 1, "path": str(tmp_path / "gone.png")}, "can't find a picture"),
        ({"op": "add_image", "slide": 1, "path": __file__}, "can't find a picture"),
        ({"op": "add_slide", "layout": "wide"}, "layout should be"),
        ({"op": "move_slide", "slide": 1, "to": 7}, "no place 7"),
        ({"op": "add_text_box", "slide": 1, "text": "x", "w": 900}, "off the slide"),
    ):
        with pytest.raises(LedgerError, match=why):
            await t.apply(bad)
    with pytest.raises(Unsupported):
        await t.apply({"op": "delete_slide", "slide": 1})
    app.deck = None
    with pytest.raises(Unreachable, match="no presentation open in PowerPoint"):
        await opened(DeckLive, app)


# ============================================================================ the job
def _answers(*replies):
    calls: list[tuple[str, str]] = []
    left = list(replies)

    async def ask(system, prompt):
        calls.append((system, prompt))
        return json.dumps(left.pop(0)), 0.001

    return ask, calls


async def test_a_job_edits_the_open_workbook_and_saves_nothing():
    app, _ = excel()
    ask, calls = _answers(
        {
            "edits": [
                {"file": 0, "op": "format", "range": "A1:D1", "bold": True, "color": "red"},
                {"file": 0, "op": "totals_row"},
            ],
            "summary": "Done.",
        }
    )
    t = live(ExcelLive, app)
    r = await run_ledger("bold red headings and a total", files=[], live=[t], ask=ask)
    assert r.changes[0] == "Made A1:D1 on Sales bold, red text" and "Total row" in r.changes[1]
    assert r.live == ["Book1 in Excel"] and r.outputs == [] and r.links == [] and r.sent
    assert app.book.saved_calls == 0
    system, prompt = calls[0]
    assert "The workbook open in Excel on their PC" in system and '"op": "totals_row"' in system
    assert "delete_sheet" not in system.split("Anything else")[0] and "Google Sheets only" not in system
    assert "Excel workbook Book1, open in Excel now" in prompt
    assert t.thread._thread.join(2) is None and not t.thread._thread.is_alive()  # let go at the end


async def test_a_job_whose_app_stops_answering_is_not_asked_twice():
    app, sheet = excel()
    ask, calls = _answers(
        {"edits": [{"file": 0, "op": "set_cells", "cells": {"A9": 1}}, {"file": 0, "op": "totals_row"}]}
    )
    t = live(ExcelLive, app)
    await t.load()
    app.fail = busy()
    r = await run_ledger("add a cell and a total", files=[], live=[t], ask=ask)
    assert len(calls) == 1 and r.changes == [] and len(r.problems) == 1
    assert "busy" in r.summary and sheet.text("A5") is None


async def test_a_job_on_an_app_that_is_gone_stops_before_the_model():
    def gone(name, hwnd):
        raise Unreachable("Excel isn't open.")

    ask, calls = _answers()
    t = ExcelLive(None, connect=gone, thread=ComThread("Excel", init=None))
    with pytest.raises(Unreachable):
        await run_ledger("anything", files=[], live=[t], ask=ask)
    assert calls == []


def test_the_file_menus_are_as_they_were():
    assert "open in Excel" not in ledger.EDITS and "open in Word" not in ledger.SYSTEM
    assert ledger.edits_for({"xlsx"}) == ledger.edits_for({"xlsx"} | set())
    live_kinds = {kinds.copy().pop() for kinds, _ in ledger_live.menus()}
    assert live_kinds == {"xlsx_live", "docx_live", "pptx_live"}
    for kind, words in (("docx_live", "open in Word"), ("pptx_live", "open in PowerPoint")):
        menu = ledger.edits_for({kind})
        assert words in menu and "Spreadsheets (Excel .xlsx" not in menu
    # every edit a live menu offers has calls behind it, and nothing else is offered
    import re

    for cls in (ExcelLive, WordLive, DeckLive):
        offered = set(re.findall(r'\{"op": "([a-z_]+)"', cls.menu))
        have = {name[3:] for name in dir(cls) if name.startswith("op_")}
        assert offered == have, (cls.__name__, offered ^ have)


# ============================================================================ the routing
def _in_front(brain, title, process, hwnd=77):
    brain._ctx = ScreenContext(captured_at=0.0, window=WindowInfo(title, process, (0, 0, 800, 600), hwnd))


def _pretend(monkeypatch, app, why=""):
    """The look and the job both reach the pretend app instead of a real one."""
    looked: list[tuple[str, int | None]] = []

    def probe(name, hwnd=None, **kw):
        looked.append((name, hwnd))
        return (why, "") if why else ("", "Book1")

    def target(name, hwnd=None, **kw):
        return live(ExcelLive, app, **kw)

    monkeypatch.setattr(ledger_live, "probe", probe)
    monkeypatch.setattr(ledger_live, "target", target)
    monkeypatch.setattr(brain_module.windows, "open_windows", lambda *a, **k: [(55, "Book1 - Excel", "EXCEL.EXE")])
    return looked


async def test_edit_document_goes_straight_into_the_open_excel(settings, monkeypatch):
    reply = json.dumps(
        {"edits": [{"file": 0, "op": "format", "range": "A1:D1", "bold": True}], "summary": "Bold."}
    )
    brain, _ = make_brain(settings, llm=Script(reply))
    app, _ = excel()
    looked = _pretend(monkeypatch, app)
    _in_front(brain, "Book1 - Excel", "EXCEL.EXE")
    note = await filework.handle(brain, "edit_document", {"task": "make the headings bold", "app": "excel"})
    assert "changing Book1 right in Excel" in note and "nothing is saved" in note
    assert looked == [("excel", 77)]  # the window in front
    await brain.jobs.wait_all()
    [job] = [j for j in brain.jobs.jobs.values() if j.agent_id == "ledger"]
    assert job.status == "done", job.summary
    assert job.summary == (
        "Made A1:D1 on Sales bold. Changed in Book1 in Excel, where it's open. I haven't saved it."
    )
    assert job.outputs == [] and job.data["edited"] == ["Book1 in Excel"]
    assert "Sales.Range(A1:D1).Font.Bold = True" in app.log and app.book.saved_calls == 0


async def test_the_app_in_front_is_enough_and_a_named_one_is_found(settings, monkeypatch):
    brain, _ = make_brain(settings, llm=Script(json.dumps({"edits": [], "summary": "x"})))
    app, _ = excel()
    looked = _pretend(monkeypatch, app)
    _in_front(brain, "Book1 - Excel", "EXCEL.EXE")
    assert "right in Excel" in await filework.handle(brain, "edit_document", {"task": "bold"})
    _in_front(brain, "Inbox - Mail", "chrome.exe")
    assert "right in Excel" in await filework.handle(brain, "edit_document", {"task": "bold", "app": "Excel"})
    assert looked == [("excel", 77), ("excel", 55)]  # named, not in front: its own window
    note = await filework.handle(brain, "edit_document", {"task": "bold"})
    assert note.startswith("edit_document: which file?") and len(looked) == 2
    assert "app is excel, word or powerpoint" in await filework.handle(
        brain, "edit_document", {"task": "bold", "app": "notepad"}
    )
    await brain.jobs.wait_all()


async def test_an_excel_that_cannot_be_reached_goes_back_to_the_hands(settings, monkeypatch):
    brain, _ = make_brain(settings)
    app, _ = excel()
    _pretend(monkeypatch, app, why="Excel is busy (a box is open in it, or a cell is being typed in).")
    _in_front(brain, "Book1 - Excel", "EXCEL.EXE")
    note = await filework.handle(brain, "edit_document", {"task": "bold", "app": "excel"})
    assert note.startswith("edit_document: Excel is busy") and "use_computer" in note
    assert "don't mention this note" in note
    assert not [j for j in brain.jobs.jobs.values() if j.agent_id == "ledger"]  # no job, nothing changed


async def test_a_private_window_is_left_alone(settings, monkeypatch):
    brain, _ = make_brain(settings)
    app, _ = excel()
    looked = _pretend(monkeypatch, app)
    _in_front(brain, "Family bank statement - Excel", "EXCEL.EXE")
    monkeypatch.setattr(brain.screen, "is_excluded", lambda w: "banking" if "bank" in w.title else None, raising=False)
    note = await filework.handle(brain, "edit_document", {"task": "bold", "app": "excel"})
    assert "private" in note and looked == []


async def test_a_request_the_direct_route_cannot_make_is_handed_to_the_hands(settings, monkeypatch):
    reply = json.dumps({"edits": [], "summary": "Merging cells is not something I can do here."})
    brain, rec = make_brain(settings, llm=Script(reply))
    app, _ = excel()
    _pretend(monkeypatch, app)
    _in_front(brain, "Book1 - Excel", "EXCEL.EXE")
    driven: list[tuple[str, int | None]] = []
    said: list[str] = []

    async def drive(task, window, *a, **k):
        driven.append((task, window))

    async def say_line(text):
        said.append(text)

    monkeypatch.setattr(brain, "_drive", drive)
    monkeypatch.setattr(brain, "say_line", say_line)
    monkeypatch.setattr(brain_module.hands, "available", lambda: True)
    await filework.handle(brain, "edit_document", {"task": "merge A1 to D1", "app": "excel"})
    await brain.jobs.wait_all()
    await asyncio.sleep(0.05)
    assert driven == [("In Excel: merge A1 to D1", 77)]
    assert said == ["I can't do that one straight in Excel, so I'll use the mouse and keyboard."]
    assert app.log == [] and not rec.of("job_ready")  # nothing changed, and no "it failed" notice


async def test_a_no_on_the_card_or_a_half_done_job_is_not_handed_over(settings, monkeypatch):
    brain, _ = make_brain(settings)
    monkeypatch.setattr(brain_module.hands, "available", lambda: True)
    job = SimpleNamespace(role="documents", status="failed", task="clear it", id="j1")
    assert await brain._hand_over(job, {}) is False  # they said no, or an edit was part-way
    job.status = "done"
    assert await brain._hand_over(job, {"handover": "x"}) is False
    job.status, job.role = "failed", "files"
    assert await brain._hand_over(job, {"handover": "x"}) is False


async def test_a_declined_card_ends_the_job_without_the_hands(settings, monkeypatch):
    reply = json.dumps({"edits": [{"file": 0, "op": "clear", "range": "A1:D4"}], "summary": "Cleared."})
    brain, rec = make_brain(settings, llm=Script(reply, reply))
    app, sheet = excel()
    _pretend(monkeypatch, app)
    _in_front(brain, "Book1 - Excel", "EXCEL.EXE")

    async def request(kind, timeout=300, **data):
        rec.events.append({"type": kind, **data})
        return False

    brain.hub.request = request  # type: ignore[method-assign]
    await filework.handle(brain, "edit_document", {"task": "clear the table", "app": "excel"})
    await brain.jobs.wait_all()
    [job] = [j for j in brain.jobs.jobs.values() if j.agent_id == "ledger"]
    [card] = [e for e in rec.events if e["type"] == "confirm"][:1]
    assert card["action"] == "Clear 16 filled cells in Book1?" and card["agent"] == "Ledger"
    assert job.status == "failed" and "Left as it was" in job.summary
    assert "handover" not in job.data and sheet.text("A1") == "Region"


def test_the_model_is_told_about_the_open_app():
    spec = {t.name: t for t in tools.COMPANION_TOOLS}["edit_document"]
    assert spec.parameters["properties"]["app"]["enum"] == ["excel", "word", "powerpoint"]
    assert "OPEN in the Excel, Word or PowerPoint app" in spec.description
    assert "Nothing is saved" in spec.description
    assert 'app "excel", "word" or \n"powerpoint"'.replace("\n", "") in prompts.SYSTEM.replace("\n", "")
    for text in (spec.description, prompts.SYSTEM):
        assert "\u2014" not in text
