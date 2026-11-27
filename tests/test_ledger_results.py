"""Ledger: a chart of rows whose labels repeat, and formulas saved with what they come to.

Live, 27 Nov 2026: "create a sample sales pivot data, create a pivot table and also insert the
chart of that data". The workbook had 24 rows of sales, a pivot with the four regions' totals,
and a chart of the 24 rows: North, North, South, South along the axis. Opened in an office app
that doesn't work formulas out, every Sales cell and every bar read CA$0.00, because a file
saved by openpyxl holds its formulas and none of their results.

Real files in a temp folder. No model, no network; LibreOffice only in the one test that says
so (skipped where it isn't installed).
"""

from __future__ import annotations

import json
import zipfile
from datetime import datetime
from typing import Any

import pytest
from openpyxl import Workbook as NewBook
from openpyxl import load_workbook
from openpyxl.chart import BarChart, Reference
from openpyxl.styles import Font

from pyxi.agents import ledger
from pyxi.agents.ledger import Workbook, new_workbook, run_ledger
from pyxi.agents.ledger_results import ErrorValue, fill_sheet, simple_results, write_results

SALES = "Sales (CA$)"
HEAD = ["Date", "Region", "Product", "Units", "Unit Price (CA$)", SALES]
ROWS = [
    ["2026-10-03", "North", "Wireless Mouse", 12, 35],
    ["2026-10-17", "North", "Laptop Stand", 8, 60],
    ["2026-10-09", "South", "Wireless Mouse", 6, 35],
    ["2026-11-21", "South", "USB-C Hub", 4, 49.5],
    ["2026-10-11", "East", "Laptop Stand", 3, 60],
    ["2026-12-02", "East", "USB-C Hub", 10, 49.5],
    ["2026-11-05", "West", "Wireless Mouse", 5, 35],
    ["2026-12-14", "West", "Laptop Stand", 2, 60],
]
BY_REGION = {"North": 900, "South": 408, "East": 675, "West": 295}  # units x price, added up
EACH = [420, 480, 210, 198, 180, 495, 175, 120]


def sample(tmp_path) -> Workbook:
    """The workbook of that day, made the way Ledger made it: its own edits, from nothing."""
    book = new_workbook("Sample Sales", tmp_path)
    book.apply({"op": "rename_sheet", "sheet": book.default, "name": "Sales Data"})
    book.default = "Sales Data"
    cells: dict[str, Any] = {f"{'ABCDEF'[c]}1": h for c, h in enumerate(HEAD)}
    for r, row in enumerate(ROWS, start=2):
        cells.update({f"{'ABCDE'[c]}{r}": v for c, v in enumerate(row)})
        cells[f"F{r}"] = f"=D{r}*E{r}"
    book.apply({"op": "set_cells", "cells": cells})
    return book


def answers(*edits: dict[str, Any]):
    async def ask(system: str, prompt: str) -> tuple[str, float]:
        return json.dumps({"edits": list(edits), "summary": "Done."}), 0.0001

    return ask


def plotted(path) -> tuple[str, str, list[Any], list[Any]]:
    """The one chart in a saved workbook: its two ranges, and the cells they are (labels as
    typed, values as the file's stored results)."""
    typed, valued = load_workbook(path), load_workbook(path, data_only=True)
    (chart,) = [c for ws in typed.worksheets for c in ws._charts]
    series = chart.series[0]
    cat = (series.cat.numRef or series.cat.strRef).f
    val = series.val.numRef.f

    def of(ref: str, wb) -> list[Any]:
        sheet, span = ref.rsplit("!", 1)
        ws = wb[sheet.strip("'")]
        return [c.value for row in ws[span.replace("$", "")] for c in row]

    return cat, val, of(cat, typed), of(val, valued)


def no_libreoffice(monkeypatch) -> None:
    monkeypatch.setattr(ledger, "_worked_out", lambda wb, suffix: None)


def parts(path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as z:
        return {n: z.read(n) for n in z.namelist()}


# ============================================================================ 1: the chart
def test_a_chart_of_labels_that_repeat_is_drawn_from_the_pivot_that_is_there(tmp_path, monkeypatch):
    """What was asked for that day: the data, a pivot, a chart. The chart is of the pivot's
    four totals, not of the rows, and no second summary is written beside the first."""
    no_libreoffice(monkeypatch)
    book = sample(tmp_path)
    book.apply({"op": "pivot", "group_by": "Region", "value": SALES})
    said = book.apply({"op": "chart", "labels": "Region", "values": SALES, "type": "bar"})
    assert said == (
        "Added a bar chart of Sales (CA$) by Region on Sales Data. Region repeats down the rows, "
        "so the chart shows one total for each of the 4, drawn from the totals already on sheet Pivot"
    )
    assert book.wb.sheetnames == ["Sales Data", "Pivot"]
    assert len(book.wb["Sales Data"]._charts) == 1  # where it was asked for: beside the data
    cat, val, labels, values = plotted(book.save())
    assert (cat, val) == ("'Pivot'!$A$2:$A$5", "'Pivot'!$B$2:$B$5")  # never the Total row
    assert labels == ["East", "North", "South", "West"]  # one point per region, none twice
    assert values == [BY_REGION[k] for k in labels]


def test_with_no_summary_the_chart_writes_its_own_in_the_order_of_the_rows(tmp_path, monkeypatch):
    no_libreoffice(monkeypatch)
    book = sample(tmp_path)
    said = book.apply({"op": "chart", "labels": "Region", "values": SALES, "type": "pie"})
    assert said.endswith(
        "drawn from a small table of totals written for it on the new sheet Sales (CA$) by Region"
    )
    made = book.wb["Sales (CA$) by Region"]
    assert [c.value for c in made[1]] == ["Region", "Sum of Sales (CA$)"]  # a pivot's own look
    assert made["A1"].font.bold and made["A6"].value == "Total" and made["B6"].value == "=SUM(B2:B5)"
    assert made["B2"].value == "=SUMIFS('Sales Data'!$F$2:$F$9,'Sales Data'!$B$2:$B$9,A2)"
    cat, val, labels, values = plotted(book.save())
    assert (cat, val) == ("'Sales (CA$) by Region'!$A$2:$A$5", "'Sales (CA$) by Region'!$B$2:$B$5")
    assert labels == ["North", "South", "East", "West"]  # as they first appear
    assert values == [900, 408, 675, 295]
    # Asked again, the summary that is now there is the one used: not a second sheet of it.
    book.apply({"op": "chart", "labels": "Region", "values": SALES, "at": "H20"})
    assert book.wb.sheetnames == ["Sales Data", "Sales (CA$) by Region"]


def test_a_summary_of_another_column_or_of_fewer_rows_is_not_the_one(tmp_path, monkeypatch):
    """A pivot of Units is not the totals of Sales, and a pivot made before rows were added
    leaves them out: neither is charted as if it were the answer."""
    no_libreoffice(monkeypatch)
    book = sample(tmp_path)
    book.apply({"op": "pivot", "group_by": "Region", "value": "Units"})
    book.apply({"op": "pivot", "group_by": "Region", "value": SALES, "target": "Old"})
    book.apply({"op": "append_row", "sheet": "Sales Data", "values": ["2026-12-20", "North", "USB-C Hub", 2, 49.5, "=D10*E10"]})
    said = book.apply({"op": "chart", "sheet": "Sales Data", "labels": "Region", "values": SALES})
    assert "on the new sheet Sales (CA$) by Region" in said
    _, _, labels, values = plotted(book.save())
    assert labels == ["North", "South", "East", "West"] and values == [999, 408, 675, 295]


def test_labels_that_do_not_repeat_are_charted_as_they_are(tmp_path, monkeypatch):
    no_libreoffice(monkeypatch)
    book = sample(tmp_path)
    said = book.apply({"op": "chart", "labels": "Date", "values": "Units", "type": "line"})
    assert said == "Added a line chart of Units on Sales Data" and book.wb.sheetnames == ["Sales Data"]
    cat, val, labels, values = plotted(book.save())
    assert (cat, val) == ("'Sales Data'!$A$2:$A$9", "'Sales Data'!$D$2:$D$9")
    assert len(labels) == 8 and values == [r[3] for r in ROWS]


async def test_the_job_of_that_day_from_start_to_finish(tmp_path, monkeypatch):
    """The whole job through run_ledger, with the edits the model gave: one bar per region,
    and every number in the file for an app that works nothing out."""
    no_libreoffice(monkeypatch)
    cells: dict[str, Any] = {f"{'ABCDEF'[c]}1": h for c, h in enumerate(HEAD)}
    for r, row in enumerate(ROWS, start=2):
        cells.update({f"{'ABCDE'[c]}{r}": v for c, v in enumerate(row)})
        cells[f"F{r}"] = f"=D{r}*E{r}"
    r = await run_ledger(
        "create a sample sales pivot data, a pivot table and a chart of that data",
        files=[],
        new=[{"kind": "xlsx", "title": "Sample Sales"}],
        new_dir=tmp_path,
        ask=answers(
            {"file": 0, "op": "set_cells", "cells": cells},
            {"file": 0, "op": "pivot", "group_by": "Region", "value": SALES},
            {"file": 0, "op": "chart", "labels": "B", "values": "F", "title": "Sales by Region (CA$)"},
        ),
    )
    assert not r.problems and len(r.outputs) == 1
    cat, _, labels, values = plotted(r.outputs[0])
    assert cat == "'Pivot'!$A$2:$A$5" and sorted(labels) == sorted(BY_REGION)
    assert values == [BY_REGION[k] for k in labels] and 0 not in values
    assert not list(tmp_path.glob(".pyxi-*"))  # nothing left beside the file


# ============================================================================ 2: the results
def test_a_saved_workbook_holds_what_its_formulas_come_to(tmp_path, monkeypatch):
    """With no LibreOffice at all: products, a pivot's SUMIFS and its total are simple enough
    to work out here. The formulas are still formulas, and Excel still works them out."""
    no_libreoffice(monkeypatch)
    book = sample(tmp_path)
    book.apply({"op": "pivot", "group_by": "Region", "value": SALES})
    book.apply({"op": "totals_row", "sheet": "Sales Data", "columns": [SALES]})
    out = book.save()
    valued, typed = load_workbook(out, data_only=True), load_workbook(out)
    assert [valued["Sales Data"].cell(r, 6).value for r in range(2, 10)] == EACH
    assert valued["Sales Data"]["F10"].value == sum(EACH) == 2278
    pivot = {row[0]: row[1] for row in valued["Pivot"].iter_rows(min_row=2, values_only=True)}
    assert pivot == {**BY_REGION, "Total": 2278}
    assert typed["Sales Data"]["F2"].value == "=D2*E2" and typed["Pivot"]["B6"].value == "=SUM(B2:B5)"
    assert typed.calculation.fullCalcOnLoad is True
    assert typed["Pivot"]["A1"].font.bold  # and it looks as it did
    # Opened again by Ledger, the results are there to sort by: no LibreOffice, no refusal.
    again = Workbook(out)
    assert again.apply({"op": "sort", "sheet": "Pivot", "by": "Sum of Sales (CA$)"}).startswith("Sorted Pivot")


def test_the_results_come_from_libreoffice_when_it_is_there(tmp_path, monkeypatch):
    """Formulas past the simple ones (text, a test, an error, one that comes to nothing) are
    LibreOffice's to work out: here a stand-in for it, with the values-only copy it gives."""
    known = {"C2": "Pens x 3", "D2": True, "E2": "#DIV/0!", "F2": None, "G2": 7.5, "H2": datetime(2026, 11, 27)}

    def worked_out(wb, suffix):
        out = NewBook()
        out.remove(out.active)
        for ws in wb.worksheets:
            new = out.create_sheet(ws.title)
            for row in ws.iter_rows():
                for c in row:
                    new.cell(c.row, c.column, known[c.coordinate] if c.data_type == "f" else c.value)
            new["H2"].number_format = "yyyy-mm-dd"
        return out

    monkeypatch.setattr(ledger, "_worked_out", worked_out)
    wb = NewBook()
    wb.active.title = "Data"
    wb.active.append(["Item", "Qty", "Label", "Many", "Share", "Note", "Half", "Day"])
    wb.active.append(["Pens", 3, '=A2&" x "&B2', "=B2>2", "=B2/0", '=IF(B2>5,"lots","")', "=AVERAGE(B2,12)", "=DATE(2026,11,27)"])
    wb.active["H2"].number_format = "yyyy-mm-dd"
    wb.save(tmp_path / "mixed.xlsx")
    book = Workbook(tmp_path / "mixed.xlsx")
    book.apply({"op": "format", "range": "A1:H1", "bold": True})
    out = book.save()
    row = [c.value for c in load_workbook(out, data_only=True)["Data"][2]]
    assert row == ["Pens", 3, "Pens x 3", True, "#DIV/0!", None, 7.5, datetime(2026, 11, 27)]
    assert load_workbook(out)["Data"]["C2"].value == '=A2&" x "&B2'
    sheet = parts(out)["xl/worksheets/sheet1.xml"].decode()
    assert '<c r="C2" t="str"><f>A2&amp;" x "&amp;B2</f><v>Pens x 3</v></c>' in sheet
    assert '<c r="D2" t="b"><f>B2&gt;2</f><v>1</v></c>' in sheet
    assert '<c r="E2" t="e"><f>B2/0</f><v>#DIV/0!</v></c>' in sheet
    assert '<c r="G2"><f>AVERAGE(B2,12)</f><v>7.5</v></c>' in sheet


def test_one_formula_too_many_and_none_is_guessed(tmp_path, monkeypatch, caplog):
    """Without LibreOffice a formula past the simple ones is not known, and then no result is
    written at all: some and not others would read as a file Excel had finished. The file is
    saved as it always was."""
    no_libreoffice(monkeypatch)
    book = sample(tmp_path)
    book.apply({"op": "set_cells", "cells": {"H1": "Best", "H2": "=MAX(F2:F9)"}})
    with caplog.at_level("INFO", logger="pyxi.agents.ledger"):
        out = book.save()
    assert "saved without its formulas' results" in caplog.text
    valued = load_workbook(out, data_only=True)["Sales Data"]
    assert valued["F2"].value is None and valued["H2"].value is None
    assert load_workbook(out)["Sales Data"]["H2"].value == "=MAX(F2:F9)"


def calculated(path):
    """A file as Excel leaves it: formulas with their results stored, one of them (a lookup)
    far past anything worked out here."""
    wb = NewBook()
    ws = wb.active
    ws.title = "Data"
    for row in (["Item", "Qty", "Price", "Total", "Found"], ["Pens", 3, 2, "=B2*C2", '=VLOOKUP("Ink",A2:C3,3,FALSE)'], ["Ink", 1, 9, "=B3*C3", None]):
        ws.append(row)
    wb.save(path)
    assert write_results(path, {"Data": {"D2": 6, "D3": 9, "E2": 9}}, wb) == 3
    return path


def test_what_excel_stored_is_kept_while_the_cells_are_as_they_were(tmp_path, monkeypatch):
    """A file Excel calculated used to lose every result to one change of look. They hold
    while nothing they could read has changed, so they are written back: no LibreOffice
    needed, and none started. A sheet added in the job (a pivot's simple sums) is worked out
    from them when there is no LibreOffice to ask."""
    monkeypatch.setattr(ledger, "_worked_out", lambda wb, suffix: pytest.fail("not needed"))
    book = Workbook(calculated(tmp_path / "stock.xlsx"))
    book.apply({"op": "format", "range": "A1:E1", "bold": True})
    valued = load_workbook(book.save(), data_only=True)
    assert [valued["Data"][at].value for at in ("D2", "D3", "E2")] == [6, 9, 9]

    no_libreoffice(monkeypatch)
    book.apply({"op": "pivot", "group_by": "Item", "value": "Total"})
    valued = load_workbook(book.save(), data_only=True)
    assert [valued["Data"][at].value for at in ("D2", "D3", "E2")] == [6, 9, 9]
    assert [valued["Pivot"][at].value for at in ("B2", "B3", "B4")] == [9, 6, 15]  # Ink, Pens, Total


def test_a_stored_result_is_not_kept_once_a_cell_has_changed(tmp_path, monkeypatch):
    """Ink's price goes from 9 to 11: the lookup that read it stored 9, and nothing here can
    work it out again. Old numbers are not passed off as the file's: none are written."""
    no_libreoffice(monkeypatch)
    book = Workbook(calculated(tmp_path / "stock.xlsx"))
    book.apply({"op": "set_cells", "cells": {"C3": 11}})
    valued = load_workbook(book.save(), data_only=True)["Data"]
    assert [valued[at].value for at in ("D2", "D3", "E2")] == [None, None, None]


def test_the_real_libreoffice_fills_in_a_saved_workbook(tmp_path, monkeypatch):
    if ledger._soffice() is None:
        pytest.skip("LibreOffice isn't installed here")
    monkeypatch.setattr(ledger, "_worked_out_for_save", ledger._worked_out)
    book = sample(tmp_path)
    book.apply({"op": "pivot", "group_by": "Region", "value": SALES})
    book.apply({"op": "chart", "labels": "Region", "values": SALES})
    book.apply({"op": "set_cells", "cells": {"H1": "Best", "H2": "=MAX(F2:F9)", "H3": '=B2&" leads"'}})
    out = book.save()
    valued = load_workbook(out, data_only=True)
    assert [valued["Sales Data"].cell(r, 6).value for r in range(2, 10)] == EACH
    assert valued["Sales Data"]["H2"].value == 495 and valued["Sales Data"]["H3"].value == "North leads"
    assert {r[0]: r[1] for r in valued["Pivot"].iter_rows(min_row=2, values_only=True)} == {**BY_REGION, "Total": 2278}
    _, _, labels, values = plotted(out)
    assert values == [BY_REGION[k] for k in labels]


# ============================================================================ the writing itself
def styled(path):
    wb = NewBook()
    ws = wb.active
    ws.title = "Data"
    for row in (["Item", "Qty", "Price", "Total"], ["Pens", 3, 2.5, "=B2*C2"], ["Ink & <more>", 1, 9, "=B3*C3"], ["Total", None, None, "=SUM(D2:D3)"]):
        ws.append(row)
    ws["A1"].font = Font(bold=True)
    ws["D2"].number_format = '"CA$"#,##0.00'
    chart = BarChart()
    chart.add_data(Reference(ws, min_col=4, min_row=1, max_row=3), titles_from_data=True)
    chart.set_categories(Reference(ws, min_col=1, min_row=2, max_row=3))
    ws.add_chart(chart, "F2")
    wb.create_sheet("Notes").append(["Nothing to work out"])
    wb.save(path)
    return wb


def test_only_the_formula_cells_change_in_the_file(tmp_path):
    """Everything else is the bytes openpyxl wrote: the styles, the chart, the other sheet,
    and in the sheet itself every cell that isn't a formula."""
    path = tmp_path / "s.xlsx"
    wb = styled(path)
    before = parts(path)
    assert write_results(path, {"Data": {"D2": 7.5, "D3": 9, "D4": 16.5, "A2": "not a formula"}, "Gone": {"A1": 1}}, wb) == 3
    after = parts(path)
    assert list(after) == list(before)  # the same parts, in the same order
    assert {n for n in before if before[n] != after[n]} == {"xl/worksheets/sheet1.xml"}
    old, new = before["xl/worksheets/sheet1.xml"].decode(), after["xl/worksheets/sheet1.xml"].decode()
    for at, v in (("D2", "7.5"), ("D3", "9"), ("D4", "16.5")):
        formula = old.split(f'<c r="{at}"')[1].split("</c>")[0]
        assert formula.endswith("<v></v>")
        old = old.replace(f'<c r="{at}"{formula}</c>', f'<c r="{at}"{formula[: -len("<v></v>")]}<v>{v}</v></c>')
    assert new == old
    assert load_workbook(path, data_only=True)["Data"]["D4"].value == 16.5
    assert len(load_workbook(path)["Data"]._charts) == 1 and not list(tmp_path.glob(".pyxi-*"))


def test_the_file_is_never_lost_when_the_writing_goes_wrong(tmp_path, monkeypatch):
    """A file that wouldn't have the same formulas, or wouldn't open, never takes the place
    of the one that was saved."""
    path = tmp_path / "s.xlsx"
    wb = styled(path)
    saved = path.read_bytes()
    other = NewBook()
    other.active.title = "Data"
    other.active["D2"] = "=B2+C2"  # not the workbook this file is
    assert write_results(path, {"Data": {"D2": 7.5}}, other) == 0
    assert path.read_bytes() == saved and not list(tmp_path.glob(".pyxi-*"))

    import openpyxl

    def broken(*a, **kw):
        raise OSError("can't be read")

    monkeypatch.setattr(openpyxl, "load_workbook", broken)
    assert write_results(path, {"Data": {"D2": 7.5}}, wb) == 0
    assert path.read_bytes() == saved and not list(tmp_path.glob(".pyxi-*"))
    assert write_results(tmp_path / "missing.xlsx", {"Data": {"D2": 1}}) == 0


def test_each_kind_of_result_is_written_as_its_own_type():
    xml = (
        b'<worksheet><cols><col min="1" max="1"/></cols><sheetData><row r="1">'
        b'<c r="A1" s="2"/><c r="B1" t="n"><v>4</v></c>'
        b'<c r="C1" s="1"><f>B1*2</f><v></v></c><c r="D1"><f>B1&amp;"&lt;"</f><v /></c>'
        b'<c r="E1" t="str"><f>B1&gt;3</f><v>old</v></c><c r="F1"><f>1/0</f><v></v></c>'
        b'<c r="G1"><f t="array" ref="G1:G2">B1:B2*2</f><v></v></c><c r="H1"><f>NA()</f></c>'
        b'<c r="I1" t="inlineStr"><is><t>&lt;c r="C1"&gt;</t></is></c>'
        b"</row></sheetData><conditionalFormatting><cfRule/></conditionalFormatting></worksheet>"
    )
    results = {"A1": 1, "B1": 2, "C1": 8, "D1": "4<&", "E1": True, "F1": ErrorValue("#DIV/0!"), "G1": 8.25, "H1": float("nan"), "I1": 3}
    new, n = fill_sheet(xml, results)
    assert n == 5
    assert new == (
        b'<worksheet><cols><col min="1" max="1"/></cols><sheetData><row r="1">'
        b'<c r="A1" s="2"/><c r="B1" t="n"><v>4</v></c>'
        b'<c r="C1" s="1"><f>B1*2</f><v>8</v></c><c r="D1" t="str"><f>B1&amp;"&lt;"</f><v>4&lt;&amp;</v></c>'
        b'<c r="E1" t="b"><f>B1&gt;3</f><v>1</v></c><c r="F1" t="e"><f>1/0</f><v>#DIV/0!</v></c>'
        b'<c r="G1"><f t="array" ref="G1:G2">B1:B2*2</f><v>8.25</v></c><c r="H1"><f>NA()</f></c>'
        b'<c r="I1" t="inlineStr"><is><t>&lt;c r="C1"&gt;</t></is></c>'
        b"</row></sheetData><conditionalFormatting><cfRule/></conditionalFormatting></worksheet>"
    )
    assert fill_sheet(xml, {}) == (xml, 0)


def test_only_the_simplest_formulas_are_worked_out_without_libreoffice():
    """Two cells or numbers multiplied, added or taken away; SUM over plain ranges; SUMIFS
    with one "is this label". Anything else, and the answer is that it isn't known."""
    wb = NewBook()
    ws = wb.active
    ws.title = "Data"
    for row in (["Region", "Units", "Price"], ["North", 2, 1.5], ["south", 3, 2], ["North", 4, None], ["South", "n/a", 2], [2026, 5, 2]):
        ws.append(row)
    other = wb.create_sheet("By Region")

    def comes_to(formula: str) -> Any:
        other["A1"], other["A2"], other["B1"] = "South", 2026, formula
        got = simple_results(wb)
        return None if got is None else got["By Region"]["B1"]

    assert comes_to("=Data!B2*Data!C2") == 3.0 and comes_to("=Data!$B$3-1.5") == 1.5
    assert comes_to("= 'Data'!B2 + 'Data'!C4") == 2  # an empty cell is 0
    assert comes_to("=SUM(Data!B2:B6)") == 14  # the text is passed over, as Excel does
    assert comes_to("=SUM(Data!B2:B3,Data!C2:C3,Data!B6)") == 13.5
    assert comes_to("=SUMIFS(Data!$C$2:$C$6,Data!$A$2:$A$6,A1)") == 4  # South and south
    assert comes_to("=SUMIFS('Data'!B2:B6,'Data'!A2:A6,A2)") == 5  # a number for a label
    assert comes_to('=SUMIFS(Data!B2:B6,Data!A2:A6,"north")') == 6
    for past_it in (
        "=Data!B2*Data!C2*2", "=Data!B2/Data!C2", "=-Data!B2", "=Data!A2*2", "=SUM(Data!B:B)",
        "=AVERAGE(Data!B2:B6)", '=SUMIFS(Data!B2:B6,Data!A2:A6,"N*")', '=SUMIFS(Data!B2:B6,Data!B2:B6,">2")',
        "=SUMIFS(Data!B2:B6,Data!A2:A5,A1)", "=SUMIFS(Data!B2:B6,Data!A2:A6,A1,Data!C2:C6,2)",
        "=Nowhere!A1+1", "=B1+1", "=SUM(A1:B1)",
    ):  # fmt: skip
        assert comes_to(past_it) is None, past_it
    # A result that is known already is taken as it is, and read by the formulas after it.
    other["B1"], other["B2"] = "=SOMETHING(1)", "=B1*2"
    known = simple_results(wb, lambda ws, r, c: 21 if (ws.title, r, c) == ("By Region", 1, 2) else None)
    assert known == {"By Region": {"B1": 21, "B2": 42}}
