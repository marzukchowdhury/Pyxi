"""Ledger, after an audit of what went wrong live with a pivot and a chart.

One evening a Google Sheet had a pivot table on a tab called "Pivot". "Insert a chart of the
pivot table summary" came back as: made a pivot table on tab Pivot 2, made a pivot table on tab
Pivot 3, added a chart. It took 20.9 seconds. The audit found why, and nine more faults beside
it. Each test here is one of them. The Google edits run against a fake that keeps real tabs,
shows a pivot's cells only in what is displayed (never in what was typed), and counts every
round trip; the Office ones run on real files in a temp folder. Nothing reaches a network, a
model or a real account.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx
import pytest
from openpyxl import Workbook as NewBook
from openpyxl import load_workbook
from openpyxl.chart import BarChart, Reference
from openpyxl.formatting.rule import CellIsRule
from openpyxl.styles import PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

from pyxi.agents import ledger
from pyxi.agents.ledger import GoogleDoc, GoogleSheet, LedgerError, WordDoc, Workbook, run_ledger
from pyxi.agents.ledger_common import a1_bounds, split_tab
from pyxi.connectors.google import GoogleConnector

URL = "https://docs.google.com/spreadsheets/d/abc123abc123abc123abc123abc/edit"
SALES = [
    ["Region", "Product", "Sales"],
    ["North", "Widget", 1200],
    ["North", "Gadget", 850],
    ["South", "Widget", 950],
    ["South", "Gadget", 700],
    ["East", "Widget", 1100],
    ["East", "Gadget", 600],
]


# ============================================================================ fakes
class Tab:
    def __init__(self, title: str, sheet_id: int, rows: list[list[Any]] | None = None) -> None:
        self.title, self.id = title, sheet_id
        self.rows = [list(r) for r in rows or []]  # what was typed
        self.worked_out: dict[tuple[int, int], Any] = {}  # a pivot's cells: only ever shown
        self.display: dict[tuple[int, int], str] = {}  # how a typed cell shows (a date)
        self.charts: list[dict[str, Any]] = []
        self.size = (1000, 26)


class Sheets:
    """Google Sheets, as far as Ledger can tell. Every call is in ``calls``."""

    def __init__(self, *tabs: Tab) -> None:
        self.tabs = list(tabs)
        self.calls: list[tuple[str, Any]] = []
        self.sent: list[dict[str, Any]] = []  # every batchUpdate request
        self.wrote: list[dict[str, Any]] = []  # every values write
        self.next_id = 900
        self.fail_meta_on: int | None = None  # this numbered read of the tabs fails once
        self.fail_read_on: int | None = None  # this numbered read of cells fails once
        self.metas = self.reads = 0
        self.cells_read = 0  # how many cells every read so far has returned
        # A pivot's wording, which goes by the spreadsheet's language.
        self.heading = lambda fn, name: f"{fn} of {name}"
        self.total = "Grand Total"

    def connected(self) -> bool:
        return True

    async def open_for_edit(self, ref: str):
        return await GoogleConnector.open_for_edit(self, ref)  # type: ignore[arg-type]

    def tab(self, title: str) -> Tab:
        return next(t for t in self.tabs if t.title == title)

    def by_id(self, sheet_id: int) -> Tab:
        return next(t for t in self.tabs if t.id == sheet_id)

    def count(self, name: str) -> int:
        return sum(1 for n, _ in self.calls if n == name)

    async def sheets_meta(self, sid):
        self.metas += 1
        self.calls.append(("meta", None))
        if self.metas == self.fail_meta_on:
            raise RuntimeError("Google said 429: Quota exceeded for quota metric 'Read requests'")
        return {
            "properties": {"title": "Untitled spreadsheet"},
            "sheets": [
                {
                    "properties": {
                        "title": t.title,
                        "sheetId": t.id,
                        "gridProperties": {"rowCount": t.size[0], "columnCount": t.size[1]},
                    },
                    "charts": t.charts,
                }
                for t in self.tabs
            ],
        }

    async def sheets_charts(self, sid):
        return [c for t in self.tabs for c in t.charts]

    def _grid(self, a1: str, formulas: bool) -> list[list[Any]]:
        title, cells = split_tab(a1)
        _, _, last_col, last_row = a1_bounds(cells)
        t = self.tab(title)
        grid = [list(r) for r in t.rows]
        if not formulas:
            for (r, c), v in t.worked_out.items():
                while len(grid) <= r:
                    grid.append([])
                grid[r] += [""] * (c + 1 - len(grid[r]))
                grid[r][c] = v
            grid = [
                [t.display.get((r, c), "" if v is None else str(v)) for c, v in enumerate(row)]
                for r, row in enumerate(grid)
            ]
        grid = [row[:last_col] for row in grid[:last_row]]
        while grid and not any(v not in ("", None) for v in grid[-1]):
            grid.pop()
        self.cells_read += sum(len(row) for row in grid)
        return grid

    async def sheets_values_batch(self, sid, ranges, formulas=False):
        self.calls.append(("read", (tuple(ranges), formulas)))
        if formulas:  # the typed read; the read of what shows goes with it, in the same breath
            self.reads += 1
        if self.reads == self.fail_read_on:
            raise RuntimeError("Google said 429: Quota exceeded for quota metric 'Read requests'")
        return [self._grid(a1, formulas) for a1 in ranges]

    async def sheets_values(self, sid, a1, formulas=False):
        raise AssertionError("tabs are read in one batch, not one at a time")

    async def sheets_write(self, sid, data):
        self.calls.append(("write", [d["range"] for d in data]))
        for d in data:
            if not any(t.title == split_tab(d["range"])[0] for t in self.tabs):
                raise RuntimeError(f"Google said 400: Unable to parse range: {d['range']}")
        self.wrote += data
        for d in data:
            title, cell = split_tab(d["range"])
            c0, r0, _, _ = a1_bounds(cell)
            t = self.tab(title)
            for dr, row in enumerate(d["values"]):
                while len(t.rows) <= r0 - 1 + dr:
                    t.rows.append([])
                line = t.rows[r0 - 1 + dr]
                line += [""] * (c0 - 1 + len(row) - len(line))
                line[c0 - 1 : c0 - 1 + len(row)] = row

    async def sheets_append(self, sid, a1, rows):
        self.calls.append(("append", a1))
        self.tab(split_tab(a1)[0]).rows += [list(r) for r in rows]

    async def sheets_batch(self, sid, requests):
        self.calls.append(("batch", [next(iter(r)) for r in requests]))
        self.sent += requests
        replies: list[dict[str, Any]] = []
        for r in requests:
            if "addSheet" in r:
                title = r["addSheet"]["properties"]["title"]
                if any(t.title == title for t in self.tabs):
                    raise RuntimeError(f'Google said 400: A sheet with the name "{title}" already exists.')
                self.next_id += 1
                self.tabs.append(Tab(title, self.next_id))
                replies.append({"addSheet": {"properties": {"sheetId": self.next_id, "title": title}}})
                continue
            renamed = r.get("updateSheetProperties", {}).get("properties", {})
            if "title" in renamed:
                self.by_id(renamed["sheetId"]).title = renamed["title"]
            if "deleteSheet" in r:
                self.tabs = [t for t in self.tabs if t.id != r["deleteSheet"]["sheetId"]]
            if "appendDimension" in r:
                t = self.by_id(r["appendDimension"]["sheetId"])
                more = r["appendDimension"]["length"]
                rows = r["appendDimension"]["dimension"] == "ROWS"
                t.size = (t.size[0] + more, t.size[1]) if rows else (t.size[0], t.size[1] + more)
            if "updateCells" in r and "pivotTable" in json.dumps(r):
                start = r["updateCells"]["start"]
                pivot = r["updateCells"]["rows"][0]["values"][0]["pivotTable"]
                self.show_pivot(self.by_id(start["sheetId"]), start["rowIndex"], start["columnIndex"], pivot)
            if "addChart" in r:
                chart = r["addChart"]["chart"]
                anchor = chart["position"]["overlayPosition"]["anchorCell"]
                self.by_id(anchor["sheetId"]).charts.append({"chartId": len(self.sent), **chart})
            replies.append({})
        return {"replies": replies}

    def show_pivot(self, dest: Tab, r0: int, c0: int, pivot: dict[str, Any]) -> None:
        """What Sheets shows for a pivot: its headings, a row per group, Grand Total. One
        written where another starts takes its place."""
        how = {"SUM": sum, "AVERAGE": lambda xs: sum(xs) / len(xs) if xs else 0, "COUNTA": len}
        src = pivot["source"]
        rows = self.by_id(src["sheetId"]).rows[src["startRowIndex"] : src["endRowIndex"]]
        g = pivot["rows"][0]["sourceColumnOffset"]
        head = rows[0]
        placed = dest.__dict__.setdefault("pivot_cells", {})
        for at in placed.pop((r0, c0), []):
            dest.worked_out.pop(at, None)
        mine = placed[(r0, c0)] = []

        def put(at: tuple[int, int], v: Any) -> None:
            dest.worked_out[at] = v
            mine.append(at)

        put((r0, c0), head[g])
        groups: dict[str, list[list[Any]]] = {}
        for i, v in enumerate(pivot["values"], 1):
            put((r0, c0 + i), self.heading(v["summarizeFunction"], head[v["sourceColumnOffset"]]))
        for row in rows[1:]:
            if len(row) <= g or row[g] in ("", None):
                continue
            into = groups.setdefault(str(row[g]), [[] for _ in pivot["values"]])
            for i, v in enumerate(pivot["values"]):
                x = row[v["sourceColumnOffset"]] if v["sourceColumnOffset"] < len(row) else None
                if x not in ("", None):
                    into[i].append(x)

        def worked(fn: str, xs: list[Any]) -> Any:
            return how[fn](xs if fn == "COUNTA" else [float(x) for x in xs if isinstance(x, int | float)])

        for n, key in enumerate(sorted(groups), 1):
            put((r0 + n, c0), key)
            for i, v in enumerate(pivot["values"]):
                put((r0 + n, c0 + 1 + i), worked(v["summarizeFunction"], groups[key][i]))
        end = r0 + len(groups) + 1
        put((end, c0), self.total)
        for i, v in enumerate(pivot["values"]):
            everything = [x for xs in groups.values() for x in xs[i]]
            put((end, c0 + 1 + i), worked(v["summarizeFunction"], everything))

    def pivots(self) -> list[dict[str, Any]]:
        return [r for r in self.sent if "updateCells" in r and "pivotTable" in json.dumps(r)]


def that_evening() -> Sheets:
    """Sample sales on Sheet1 and a pivot of them on a tab called Pivot, made a minute earlier."""
    g = Sheets(Tab("Sheet1", 0, SALES), Tab("Pivot", 901))
    g.next_id = 901
    g.show_pivot(
        g.tab("Pivot"),
        0,
        0,
        {
            "source": {"sheetId": 0, "startRowIndex": 0, "endRowIndex": 7},
            "rows": [{"sourceColumnOffset": 0}],
            "values": [{"summarizeFunction": "SUM", "sourceColumnOffset": 2}],
        },
    )
    return g


def answers(*rounds: list[dict[str, Any]]):
    """A model that returns these edits, one list per call (the last list again after that)."""
    prompts: list[str] = []

    async def ask(system: str, prompt: str) -> tuple[str, float]:
        prompts.append(prompt)
        edits = rounds[min(len(prompts) - 1, len(rounds) - 1)]
        return json.dumps({"edits": list(edits), "summary": "Done."}), 0.0001

    ask.prompts = prompts  # type: ignore[attr-defined]
    return ask


async def on_google(g: Sheets, *rounds: list[dict[str, Any]], url: str = URL, task: str = "do it"):
    ask = answers(*rounds)
    return await run_ledger(task, files=[], google=g, google_refs=[url], ask=ask), ask


def book(path, rows, extra=None):
    wb = NewBook()
    ws = wb.active
    ws.title = "Data"
    for r in rows:
        ws.append(r)
    if extra:
        extra(wb, ws)
    wb.save(path)
    return path


def cells(path, sheet="Data", cols=3):
    ws = load_workbook(path)[sheet]
    return [[ws.cell(r, c).value for c in range(1, cols + 1)] for r in range(1, ws.max_row + 1)]


PIVOT = {"file": 0, "op": "pivot", "sheet": "Sheet1", "group_by": "Region", "value": "Sales", "target": "Pivot"}


# ============================================================================ 1: the double pivot
async def test_a_tab_that_holds_only_a_pivot_is_not_seen_as_empty():
    """Live: the outline said "Tab 'Pivot': 0 rows" for a tab with a pivot table on it (its
    cells are worked out, so a read of what was typed has none of them), and the chart edit on
    that tab ended "Pivot is empty, so there's nothing to chart yet"."""
    g = that_evening()
    r, ask = await on_google(
        g, [{"file": 0, "op": "chart", "sheet": "Pivot", "labels": "Region", "values": "SUM of Sales", "at": "E2"}]
    )
    assert "Tab 'Pivot': 5 rows." in ask.prompts[0]
    assert "A1='Region' | B1='SUM of Sales'" in ask.prompts[0]
    assert r.changes == ["Added a bar chart of SUM of Sales by Region on Pivot"] and not r.problems
    assert len(ask.prompts) == 1 and [t.title for t in g.tabs] == ["Sheet1", "Pivot"]
    chart = g.tab("Pivot").charts[0]["spec"]["basicChart"]
    domain = chart["domains"][0]["domain"]["sourceRange"]["sources"][0]
    assert (domain["startRowIndex"], domain["endRowIndex"]) == (0, 4)  # Grand Total left out


async def test_a_pivot_that_is_already_on_its_tab_is_not_made_again():
    """Live: "made a pivot table on tab Pivot 2 ... on tab Pivot 3". A pivot asked for on a tab
    that already holds that pivot is written again in its place; an empty tab of that name is
    used; a tab with something typed on it is refused. "Pivot 2" is never made."""
    g = that_evening()
    r, _ = await on_google(g, [PIVOT])
    assert r.changes == ["Updated the pivot on tab Pivot: sum of Sales by Region"]
    assert [next(iter(x)) for x in g.sent] == ["updateCells"]  # no new tab
    assert g.sent[0]["updateCells"]["start"] == {"sheetId": 901, "rowIndex": 0, "columnIndex": 0}
    assert [t.title for t in g.tabs] == ["Sheet1", "Pivot"]

    g = Sheets(Tab("Sheet1", 0, SALES), Tab("Pivot", 5))  # the tab is there, with nothing on it
    r, _ = await on_google(g, [PIVOT])
    assert r.changes == ["Made a pivot table on tab Pivot: sum of Sales by Region"]
    assert [next(iter(x)) for x in g.sent] == ["updateCells"]
    assert g.sent[0]["updateCells"]["start"] == {"sheetId": 5, "rowIndex": 0, "columnIndex": 0}

    g = Sheets(Tab("Sheet1", 0, SALES), Tab("Pivot", 5, [["Notes"], ["Call the bank"]]))
    r, _ = await on_google(g, [PIVOT], [])
    assert not r.changes and not g.sent
    assert "already a tab called Pivot with other things on it" in r.problems[0]
    assert all(t.title in ("Sheet1", "Pivot") for t in g.tabs)


async def test_the_second_pass_never_repeats_an_edit_that_worked():
    """Live: the chart edit failed, the model was asked again, and its second reply sent the
    pivot again along with a new chart. Everything in that reply was applied, so the pivot was
    made twice. An edit that worked is now skipped if it comes back, the model is told what
    is already done, and the outline it gets is read fresh (the new tab is in it)."""
    g = Sheets(Tab("Sheet1", 0, SALES))
    pivot = {**PIVOT, "target": "By region"}
    bad = {"file": 0, "op": "chart", "sheet": "Nope", "values": "Sales"}
    good = {"file": 0, "op": "chart", "sheet": "By region", "labels": "Region", "values": "SUM of Sales"}
    r, ask = await on_google(g, [pivot, bad], [pivot, good])
    assert r.changes == [
        "Made a pivot table on tab By region: sum of Sales by Region",
        "Added a bar chart of SUM of Sales by Region on By region",
    ]
    assert len(g.pivots()) == 1 and [t.title for t in g.tabs] == ["Sheet1", "By region"]
    assert not r.problems  # the chart that failed was mended by the one that worked
    second = ask.prompts[1]
    assert "These edits already worked and are in the files now. Do not send them again:" in second
    assert '"op": "pivot"' in second.split("Do not send them again:")[1].split("The files now")[0]
    assert "Tabs: Sheet1, By region." in second and "Tab 'By region': 5 rows." in second


async def test_a_first_pass_problem_is_kept_when_the_second_reply_mends_nothing():
    """The second reply used to wipe the first pass's problems whatever it held: a reply that
    only repeated the edit that had worked left "1 edit didn't work" unsaid."""
    g = Sheets(Tab("Sheet1", 0, SALES))
    ok = {"file": 0, "op": "set_cells", "cells": {"E1": "Checked"}}
    bad = {"file": 0, "op": "chart", "sheet": "Nope", "values": "Sales"}
    r, _ = await on_google(g, [ok, bad], [ok])
    assert r.changes == ["Set 1 cells on Sheet1"] and g.count("write") == 1
    assert r.problems == ["There's no sheet called 'Nope'."]


# ============================================================================ 2: speed
async def test_every_tab_is_read_in_one_round_trip_and_only_when_an_edit_needs_it(caplog):
    """Live: 20.9 s for one chart. The spreadsheet was read again after every edit, a round
    trip per tab: 17 reads out of 22 round trips. Now all tabs come in one read of what was
    typed and one of what shows, and only an edit that works from the cells brings a new read.
    How long each part took is logged."""
    g = that_evening()
    with caplog.at_level(logging.INFO, logger="pyxi.agents.ledger"):
        r, _ = await on_google(
            g,
            [
                {"file": 0, "op": "format", "range": "A1:C1", "bold": True},
                {"file": 0, "op": "set_cells", "cells": {"E1": "Checked"}},
                {"file": 0, "op": "note", "cell": "A1", "text": "From the till"},
            ],
        )
    assert len(r.changes) == 3
    assert g.count("meta") == 1 and g.count("read") == 2  # typed and shown, both tabs in each
    ranges, _ = next(d for n, d in g.calls if n == "read")
    assert ranges == ("'Sheet1'!A1:Z1000", "'Pivot'!A1:Z1000")
    said = " | ".join(rec.getMessage() for rec in caplog.records)
    assert "opened and read 1 file(s) in" in said and "model call 1 took" in said
    assert "format took" in said and "saved in" in said

    g = Sheets(Tab("Sheet1", 0, []))  # cells go in, then a pivot of them: one new read, before it
    data = {f"{'ABC'[c]}{r + 1}": v for r, row in enumerate(SALES) for c, v in enumerate(row)}
    r, _ = await on_google(
        g,
        [
            {"file": 0, "op": "set_cells", "cells": data},
            {"file": 0, "op": "pivot", "group_by": "Region", "value": "Sales"},
        ],
    )
    assert len(r.changes) == 2 and not r.problems
    assert g.count("meta") == 1 and g.count("read") == 4  # the tab's cells again, not the tabs


# ============================================================================ 4: the chart's column
async def test_a_chart_draws_the_column_that_was_asked_for():
    """With a pivot of Sales beside the data, a chart of Units by Region drew the Sales totals
    under the title "Units by Region": any pivot grouped by Region was taken, and always its
    first number column."""
    rows = [
        ["Region", "Product", "Units", "Sales"],
        ["North", "Widget", 10, 1200],
        ["North", "Gadget", 5, 850],
        ["South", "Widget", 8, 950],
    ]
    g = Sheets(Tab("Sheet1", 0, rows))
    await on_google(g, [{"file": 0, "op": "pivot", "group_by": "Region", "value": ["Units", "Sales"]}])
    shown = g.tab("Sheet1").worked_out
    assert (shown[(0, 5)], shown[(0, 6)], shown[(0, 7)]) == ("Region", "SUM of Units", "SUM of Sales")
    r, _ = await on_google(g, [{"file": 0, "op": "chart", "labels": "Region", "values": "Sales"}])
    series = g.tab("Sheet1").charts[0]["spec"]["basicChart"]["series"][0]["series"]
    assert series["sourceRange"]["sources"][0]["startColumnIndex"] == 7  # SUM of Sales, not Units
    assert len(g.pivots()) == 1 and r.changes == ["Added a bar chart of Sales by Region on Sheet1"]

    # No pivot of that column yet: one is made for it, and that is what the chart draws.
    g = Sheets(Tab("Sheet1", 0, rows))
    await on_google(g, [{"file": 0, "op": "pivot", "group_by": "Region", "value": "Sales"}])
    await on_google(g, [{"file": 0, "op": "chart", "labels": "Region", "values": "Units"}])
    made = g.tab("Sheet1").worked_out
    series = g.tab("Sheet1").charts[0]["spec"]["basicChart"]["series"][0]["series"]
    at = series["sourceRange"]["sources"][0]["startColumnIndex"]
    assert made[(0, at)] == "SUM of Units" and len(g.pivots()) == 2


# ============================================================================ 5: big sheets
async def test_a_total_goes_under_the_last_row_however_many_rows_there_are():
    """The read stopped at row 500 whatever the sheet held, and the last row read was taken for
    the end of the data: on 800 rows, "Total" was written into row 501, over a real row, and
    a pivot covered the first 500 rows only."""
    rows = [["Region", "Sales"]] + [[f"R{i % 7}", 10] for i in range(800)]
    g = Sheets(Tab("Sheet1", 0, rows))
    r, _ = await on_google(g, [{"file": 0, "op": "totals_row"}])
    assert r.changes == ["Added a Total row (row 802) on Sheet1: Sales 8,000"]
    assert g.tab("Sheet1").rows[500] == ["R2", 10]  # row 501 is still what it was
    assert g.tab("Sheet1").rows[801] == ["Total", "=SUM(B2:B801)"]
    g = Sheets(Tab("Sheet1", 0, rows))
    await on_google(g, [{"file": 0, "op": "pivot", "group_by": "Region", "value": "Sales"}])
    source = g.pivots()[0]["updateCells"]["rows"][0]["values"][0]["pivotTable"]["source"]
    assert (source["startRowIndex"], source["endRowIndex"]) == (0, 801)


async def test_columns_past_z_are_read_and_a_pivot_lands_clear_of_them():
    """The read also stopped at column Z. A pivot "beside the data" was placed from what had
    been read, so on a sheet 30 columns wide it went in column AB, on top of real data."""
    head = ["Region"] + [f"M{i}" for i in range(1, 30)]
    tab = Tab("Sheet1", 0, [head, ["North"] + [1] * 29, ["South"] + [2] * 29, ["North"] + [3] * 29])
    tab.size = (1000, 40)
    g = Sheets(tab)
    r, _ = await on_google(g, [{"file": 0, "op": "pivot", "group_by": "Region", "value": "M29"}])
    assert next(d for n, d in g.calls if n == "read")[0] == ("'Sheet1'!A1:AN1000",)
    update = g.pivots()[0]["updateCells"]
    assert update["start"]["columnIndex"] == 31  # AF: one clear column after AD, the last of the data
    assert update["rows"][0]["values"][0]["pivotTable"]["source"]["endColumnIndex"] == 30
    assert r.changes == ["Made a pivot table beside the data on Sheet1, from AF1: sum of M29 by Region"]


async def test_a_tab_too_big_to_read_whole_is_refused_and_never_written_over(monkeypatch):
    """Past what one read takes, Ledger can't see where the data ends. It says so, plainly,
    and writes nothing there; an edit that names its range still works."""
    monkeypatch.setattr(ledger, "_READ_ROWS", 50)
    rows = [["Region", "Sales"]] + [[f"R{i % 7}", 10] for i in range(80)]
    g = Sheets(Tab("Sheet1", 0, rows))
    r, ask = await on_google(
        g,
        [
            {"file": 0, "op": "totals_row"},
            {"file": 0, "op": "pivot", "group_by": "Region", "value": "Sales"},
            {"file": 0, "op": "sort", "by": "Sales"},
            {"file": 0, "op": "format", "range": "A1:B1", "bold": True},
        ],
        [],
    )
    assert r.changes == ["Formatted A1:B1 on Sheet1"]
    assert len(r.problems) == 3  # the total, the pivot and the sort: none of them guessed
    assert all("bigger than I can read in one go (50 rows" in p for p in r.problems)
    assert not g.wrote and [next(iter(x)) for x in g.sent] == ["repeatCell"]
    assert "Tab 'Sheet1': more than 50 rows or 200 columns. Too big to total" in ask.prompts[0]


# ============================================================================ 6: half-done edits
def test_sort_refuses_merged_cells_before_it_writes_anything(tmp_path):
    """A merged cell can't take a value, so a sort over one stopped part-way through: three
    rows rewritten, two not. Now it looks first and says what to do."""
    rows = [["Region", "Rep", "Sales"], ["North", "Ana", 100], ["North", "Bo", 900], ["South", "Cy", 500], ["South", "Di", 300], ["East", "Ed", 700]]
    b = Workbook(book(tmp_path / "m.xlsx", rows, lambda wb, ws: ws.merge_cells("A4:A5")))
    before = [[c.value for c in row] for row in b.wb["Data"].iter_rows()]
    with pytest.raises(LedgerError, match=r"merged cells in the rows to sort \(A4:A5\)"):
        b.apply({"op": "sort", "by": "Sales", "descending": True})
    assert [[c.value for c in row] for row in b.wb["Data"].iter_rows()] == before


async def test_an_edit_that_dies_part_way_leaves_nothing_in_the_saved_copy(tmp_path):
    """One edit failed half-way, another worked, and the copy was saved with the half in it:
    a row lost and a row there twice, under a summary that only said "1 edit didn't work".
    The file is opened again and the edits that worked are done again, so the half is gone."""
    rows = [["Region", "Rep", "Sales"], ["North", "Ana", 100], ["South", "Cy", 500]]
    path = book(tmp_path / "h.xlsx", rows, lambda wb, ws: ws.merge_cells("E1:F1"))
    r = await run_ledger(
        "x",
        files=[str(path)],
        ask=answers(
            [
                {"file": 0, "op": "set_cells", "cells": {"C2": 999}},
                # A2 and B2 are written, then F1 (inside the merge) stops it.
                {"file": 0, "op": "set_cells", "cells": {"A2": "WRONG", "B2": "WRONG", "F1": "x"}},
                {"file": 0, "op": "freeze", "rows": 1},
            ],
            [],
        ),
    )
    assert r.changes == ["Set 1 cell on Data", "Froze the top 1 row on Data"] and len(r.problems) == 1
    assert cells(r.outputs[0])[1] == ["North", "Ana", 999]  # the first edit, and none of the second
    assert load_workbook(r.outputs[0])["Data"].freeze_panes == "A2"
    assert cells(path)[1] == ["North", "Ana", 100]  # the original is never touched


# ============================================================================ 7: formulas nobody calculated
UNITS = [["Region", "Product", "Units", "Price"], ["North", "Widget", 10, 5], ["South", "Gadget", 2, 100], ["East", "Widget", 30, 1], ["West", "Gadget", 4, 20]]
TOTAL = {"file": 0, "op": "add_column", "header": "Total", "formula": "=C{r}*D{r}"}


def multiplied(wb, suffix):
    """Stands in for LibreOffice: a values-only copy with every =Cn*Dn worked out."""
    out = NewBook()
    out.remove(out.active)
    for ws in wb.worksheets:
        new = out.create_sheet(ws.title)
        for row in ws.iter_rows():
            for c in row:
                m = re.fullmatch(r"=C(\d+)\*D(\d+)", str(c.value))
                new.cell(c.row, c.column, ws.cell(int(m[1]), 3).value * ws.cell(int(m[2]), 4).value if m else c.value)
    return out


async def test_a_formula_nobody_has_calculated_is_never_sorted_by_its_text(tmp_path, monkeypatch):
    """A column added in the same job (or any file Ledger saved) holds formulas with no stored
    result. A sort by it ordered the rows by the text "=C2*D2", a "values only" copy pasted
    blanks, and a pivot's summary read "top: Gadget 0, Widget 0". Without LibreOffice the
    edit now says it can't, and the summary has no figures it doesn't know."""
    monkeypatch.setattr(ledger, "_worked_out", lambda wb, suffix: None)
    path = book(tmp_path / "f.xlsx", UNITS)
    r = await run_ledger(
        "x",
        files=[str(path)],
        ask=answers(
            [
                TOTAL,
                {"file": 0, "op": "sort", "by": "Total", "descending": True},
                {"file": 0, "op": "copy", "from": "E2:E5", "to": "G2", "values_only": True},
                {"file": 0, "op": "pivot", "group_by": "Product", "value": "Total"},
                {"file": 0, "op": "totals_row", "columns": ["Total"]},
            ],
            [],
        ),
    )
    assert r.changes == [
        "Added column Total with a formula on 4 rows",
        "Made a pivot on sheet Pivot: sum of Total by Product, 2 groups",
        "Added a Total row (row 6) on Data",
    ]
    assert len(r.problems) == 2  # the sort and the copy
    assert all("E2 on Data is a formula that hasn't been worked out yet" in p for p in r.problems)
    assert [row[0] for row in cells(r.outputs[0])[1:5]] == ["North", "South", "East", "West"]  # unsorted


async def test_libreoffice_works_the_formulas_out_when_it_is_there(tmp_path, monkeypatch):
    """The same job with LibreOffice installed: the rows really are largest first, and a
    "values only" copy pastes what the formulas come to."""
    monkeypatch.setattr(ledger, "_worked_out", multiplied)
    path = book(tmp_path / "f.xlsx", UNITS)
    r = await run_ledger(
        "x",
        files=[str(path)],
        ask=answers(
            [
                TOTAL,
                {"file": 0, "op": "sort", "by": "Total", "descending": True},
                {"file": 0, "op": "copy", "from": "E2:E5", "to": "G2", "values_only": True},
            ]
        ),
    )
    assert not r.problems
    after = cells(r.outputs[0], cols=7)
    assert [row[0] for row in after[1:]] == ["South", "West", "North", "East"]  # 200, 80, 50, 30
    assert [row[6] for row in after[1:]] == [200, 80, 50, 30]


def test_a_file_excel_has_calculated_is_sorted_by_its_stored_results(tmp_path, monkeypatch):
    """What Excel last worked out is used as it always was, with no LibreOffice: and in such a
    file a formula with nothing stored came to nothing (=IF(B3=0,"",B3)), which sorts last
    like any empty cell. It is not an unknown."""
    monkeypatch.setattr(ledger, "_worked_out", lambda wb, suffix: pytest.fail("not needed"))
    rows = [["Item", "Qty", "Shown"], ["a", 3, "=B2"], ["b", 0, '=IF(B3=0,"",B3)'], ["c", 1, "=B4"]]
    b = Workbook(book(tmp_path / "s.xlsx", rows))
    results = NewBook()  # the values Excel would have left in the file
    results.active.title = "Data"
    for row in [["Item", "Qty", "Shown"], ["a", 3, 3], ["b", 0, None], ["c", 1, 1]]:
        results.active.append(row)
    b.values, b._calculated = results, True
    assert b.apply({"op": "sort", "by": "Shown"}) == "Sorted Data by Shown, smallest first"
    assert [r[0] for r in b.wb["Data"].iter_rows(min_row=2, values_only=True)] == ["c", "a", "b"]
    # The results moved with their rows, so a second sort in the same job still has them.
    b.apply({"op": "sort", "by": "Shown", "descending": True})
    assert [r[0] for r in b.wb["Data"].iter_rows(min_row=2, values_only=True)] == ["a", "c", "b"]
    # And they go with their rows when a row above them is deleted, too.
    b.apply({"op": "delete_rows", "from": 2})
    b.apply({"op": "sort", "by": "Shown", "descending": True})
    assert [r[0] for r in b.wb["Data"].iter_rows(min_row=2, values_only=True)] == ["c", "b"]


# ============================================================================ 8: rows that move
def test_deleting_the_last_data_row_does_not_leave_the_total_adding_itself(tmp_path):
    """=SUM(C2:C5) under four rows: with row 5 deleted the total moved up into row 5 and still
    said C2:C5, itself included. A range now ends where its last row that is left ends, and a
    cell that was deleted is #REF!, as in Excel."""
    rows = [r[:3] for r in UNITS] + [["Total", None, "=SUM(C2:C5)"], [None, None, "=C5*2"], [None, None, "=SUM(C3:C4)"]]
    b = Workbook(book(tmp_path / "d.xlsx", rows))
    assert b.apply({"op": "delete_rows", "from": 5}) == "Deleted 1 row on Data"
    ws = b.wb["Data"]
    assert (ws["A5"].value, ws["C5"].value) == ("Total", "=SUM(C2:C4)")
    assert ws["C6"].value == "=#REF!*2" and ws["C7"].value == "=SUM(C3:C4)"
    b.apply({"op": "delete_rows", "from": 3, "to": 4})  # every row of the last range goes
    assert ws["C3"].value == "=SUM(C2:C2)" and ws["C5"].value == "=SUM(#REF!)"


def test_what_names_cells_follows_the_rows_that_moved(tmp_path):
    """One row inserted at the top moved the cells and their formulas and nothing else: the
    colour rule, the dropdown, the merged title, the filter, the table and the chart all stayed
    where they were, so the chart drew the header row as its first value."""

    def extra(wb, ws):
        ws.merge_cells("F1:G1")
        ws["F1"] = "Notes"
        ws.conditional_formatting.add("C2:C5", CellIsRule(operator="greaterThan", formula=["$C$2"], fill=PatternFill("solid", start_color="FFFF00", end_color="FFFF00")))
        dv = DataValidation(type="list", formula1='"Widget,Gadget"')
        ws.add_data_validation(dv)
        dv.add("B2:B5")
        ws.auto_filter.ref = "A1:D5"
        chart = BarChart()
        chart.add_data(Reference(ws, min_col=3, min_row=1, max_row=5), titles_from_data=True)
        chart.set_categories(Reference(ws, min_col=1, min_row=2, max_row=5))
        ws.add_chart(chart, "I2")
        ws["D7"] = "=SUM(C2:C5)"

    path = book(tmp_path / "r.xlsx", UNITS, extra)
    b = Workbook(path)
    b.apply({"op": "insert_rows", "at": 1})
    ws = load_workbook(b.save())["Data"]
    assert ws["A2"].value == "Region" and ws["D8"].value == "=SUM(C3:C6)"
    [rule] = ws.conditional_formatting
    assert str(rule.sqref) == "C3:C6" and rule.rules[0].formula == ["$C$3"]
    assert [str(d.sqref) for d in ws.data_validations.dataValidation] == ["B3:B6"]
    assert [str(m) for m in ws.merged_cells.ranges] == ["F2:G2"] and ws["F2"].value == "Notes"
    assert ws.auto_filter.ref == "A2:D6"
    series = ws._charts[0].series[0]
    assert series.val.numRef.f == "'Data'!$C$3:$C$6" and series.tx.strRef.f == "'Data'!C2"
    assert (series.cat.numRef or series.cat.strRef).f == "'Data'!$A$3:$A$6"
    assert ws._charts[0].anchor._from.row == 2  # the chart moved down a row with its data

    # A table follows too, and a column put inside it is given a heading (Excel refuses a
    # table with a blank one, and with more columns than it has names for).
    b = Workbook(book(tmp_path / "t.xlsx", UNITS))
    b.apply({"op": "make_table"})
    b.apply({"op": "insert_columns", "at": "B"})
    b.apply({"op": "delete_rows", "from": 5})
    ws = load_workbook(b.save())["Data"]
    [table] = ws.tables.values()
    assert table.ref == "A1:E4"
    assert [c.name for c in table.tableColumns] == ["Region", "Column2", "Product", "Units", "Price"]


# ============================================================================ 9: totals and pivots
async def test_a_column_of_dates_is_not_totalled():
    """A date is a number underneath, and a read of formulas gave 46298 for it: a totals row
    added the Order date column up ("Order date 138,891"). What a cell shows says it's a date,
    and the connector now asks for dates as they show."""
    rows = [["Item", "Order date", "Amount"], ["Desk", 46296, 300], ["Chair", 46297, 120], ["Lamp", 46298, 45]]
    tab = Tab("Sheet1", 0, rows)
    tab.display = {(1, 1): "10/1/2026", (2, 1): "10/2/2026", (3, 1): "10/3/2026"}
    g = Sheets(tab)
    r, _ = await on_google(g, [{"file": 0, "op": "totals_row"}])
    assert r.changes == ["Added a Total row (row 5) on Sheet1: Amount 465"]
    assert g.tab("Sheet1").rows[4] == ["Total", "", "=SUM(C2:C4)"]


async def test_a_totals_row_is_not_one_of_a_pivots_groups():
    """A pivot made after a totals row took the Total row in as a group, so its grand total was
    twice the data's. The Excel pivot and the Google chart already left that row out."""
    g = Sheets(Tab("Sheet1", 0, [*SALES, ["Total", "", "=SUM(C2:C7)"]]))
    await on_google(g, [{"file": 0, "op": "pivot", "group_by": "Region", "value": "Sales"}])
    source = g.pivots()[0]["updateCells"]["rows"][0]["values"][0]["pivotTable"]["source"]
    assert source["endRowIndex"] == 7  # the six data rows under the header, and no further
    groups = [v for (r, c), v in sorted(g.tab("Sheet1").worked_out.items()) if c == 4]
    assert groups == ["Region", "East", "North", "South", "Grand Total"]


# ============================================================================ 10: tabs
async def test_an_edit_that_names_no_tab_goes_to_the_one_open_on_their_screen():
    """The link from the address bar says which tab they're looking at (gid). It was dropped,
    so "add a totals row" on the second tab totalled the first."""
    g = Sheets(Tab("Q1", 0, [["Item", "Amount"], ["a", 1], ["b", 2]]), Tab("Q2", 222, [["Item", "Amount"], ["c", 30], ["d", 40]]))
    r, ask = await on_google(g, [{"file": 0, "op": "totals_row"}], url=URL + "?gid=222#gid=222")
    assert r.changes == ["Added a Total row (row 4) on Q2: Amount 70"]
    assert "The tab open on their screen is 'Q2'" in ask.prompts[0]
    assert g.tab("Q1").rows == [["Item", "Amount"], ["a", 1], ["b", 2]]


async def test_a_tab_made_a_moment_ago_can_be_edited_in_the_same_job():
    """"Make a Summary tab and bold its header": the second edit ended "There's no sheet called
    'Summary'", because the list of tabs was only read again after some kinds of edit. The same
    went for a chart after a totals row, which was drawn without it."""
    g = Sheets(Tab("Sheet1", 0, SALES))
    r, ask = await on_google(
        g,
        [
            {"file": 0, "op": "add_sheet", "name": "Summary", "rows": [["Region", "Sales"], ["North", 2050]]},
            {"file": 0, "op": "format", "range": "'Summary'!A1:B1", "bold": True},
            {"file": 0, "op": "totals_row", "sheet": "Summary"},
            {"file": 0, "op": "totals_row", "sheet": "Summary"},
        ],
    )
    assert r.changes == [
        "Added tab Summary",
        "Formatted 'Summary'!A1:B1 on Summary",
        "Added a Total row (row 3) on Summary: Sales 2,050",
        "Summary already ends with a total row",  # the second one sees the first
    ]
    assert not r.problems and len(ask.prompts) == 1
    assert g.sent[1]["repeatCell"]["range"]["sheetId"] == g.tab("Summary").id


async def test_a_tab_past_the_sixth_is_read_when_an_edit_names_it():
    """Only the first six tabs were read. A seventh wasn't in the outline at all, and an edit on
    it found it empty: "T7 has no rows to total"."""
    g = Sheets(*[Tab(f"T{i}", i, [["Item", "Amount"], ["a", 1], ["b", 2]]) for i in range(1, 8)])
    r, ask = await on_google(g, [{"file": 0, "op": "totals_row", "sheet": "T7"}])
    assert "Tabs: T1, T2, T3, T4, T5, T6, T7." in ask.prompts[0] and "Not read yet: T7." in ask.prompts[0]
    assert r.changes == ["Added a Total row (row 4) on T7: Amount 3"] and not r.problems


# ============================================================================ the small ones
async def test_freezing_a_column_leaves_a_frozen_header_row_alone(tmp_path):
    """"Freeze the first column" sent rows: 0 with it, and the header row came unfrozen. In an
    Excel file it moved the freeze from A2 to B1, the same thing."""
    g = Sheets(Tab("Sheet1", 0, SALES))
    s = GoogleSheet(g, "x")
    await s.load()
    assert await s.apply({"op": "freeze", "columns": 1}) == "Froze the first 1 column on Sheet1"
    sent = g.sent[0]["updateSheetProperties"]
    assert sent["properties"]["gridProperties"] == {"frozenColumnCount": 1}
    assert sent["fields"] == "gridProperties.frozenColumnCount"

    def extra(wb, ws):
        ws.freeze_panes = "A2"

    b = Workbook(book(tmp_path / "z.xlsx", SALES, extra))
    assert b.apply({"op": "freeze", "columns": 1}) == "Froze the top 1 row and the first 1 column on Data"
    assert b.wb["Data"].freeze_panes == "B2"
    b.apply({"op": "freeze", "rows": 0, "columns": 0})
    assert b.wb["Data"].freeze_panes is None


def test_an_excel_pivot_groups_as_excel_matches_and_takes_several_value_columns(tmp_path):
    """"West" and "west" were two groups, and SUMIFS doesn't tell them apart: each showed the
    sum of both and the total counted them twice (370 for data that adds up to 220). And a
    list of value columns, which the menu offers, ended "There's no column called ['Units',
    'Sales']"."""
    rows = [["Region", "Units", "Sales"], ["West", 1, 100], ["west", 2, 50], ["East", 3, 70]]
    b = Workbook(book(tmp_path / "p.xlsx", rows))
    said = b.apply({"op": "pivot", "group_by": "Region", "value": "Sales"})
    assert said == "Made a pivot on sheet Pivot: sum of Sales by Region, 2 groups (top: West 150, East 70)"
    assert [r[0] for r in b.wb["Pivot"].iter_rows(values_only=True)] == ["Region", "East", "West", "Total"]
    said = b.apply({"op": "pivot", "group_by": "Region", "value": ["Units", "Sales"], "target": "Both"})
    assert said == "Made a pivot on sheet Both: sum of Units and Sales by Region, 2 groups"
    both = list(b.wb["Both"].iter_rows(values_only=True))
    assert both[0] == ("Region", "Sum of Units", "Sum of Sales")
    assert both[2] == ("West", "=SUMIFS('Data'!$B$2:$B$4,'Data'!$A$2:$A$4,A3)", "=SUMIFS('Data'!$C$2:$C$4,'Data'!$A$2:$A$4,A3)")
    assert both[3] == ("Total", "=SUM(B2:B3)", "=SUM(C2:C3)")


async def test_a_read_that_fails_after_an_edit_landed_is_not_a_failed_edit():
    """The spreadsheet was read again straight after each edit, inside it. When that read
    failed (a 429, a timeout) the edit was reported as failed though it had gone through, and
    the model was asked to do it again. Now the read belongs to the next edit that needs it:
    that one fails, is asked for again, and the first is not repeated."""
    g = Sheets(Tab("Sheet1", 0, SALES))
    g.fail_read_on = 2  # the tab's cells, read again before the total
    cell = {"file": 0, "op": "set_cells", "cells": {"E1": "Checked"}}
    total = {"file": 0, "op": "totals_row"}
    r, ask = await on_google(g, [cell, total], [cell, total])
    assert r.changes == ["Set 1 cells on Sheet1", "Added a Total row (row 8) on Sheet1: Sales 5,400"]
    assert not r.problems and g.count("write") == 2  # E1 once, the total once
    failed = ask.prompts[1].split("These edits failed:\n")[1].split("\n\n")[0]
    assert '"op": "totals_row"' in failed and "set_cells" not in failed and "429" in failed


async def test_a_google_doc_edit_stands_when_the_read_after_it_fails():
    """The same in a Doc: the paragraph is in, the read of the doc after it fails. The edit is
    done, the doc is read before the next edit, and nothing is typed twice."""

    class Docs:
        def __init__(self) -> None:
            self.sent: list[dict[str, Any]] = []
            self.reads = 0

        async def docs_get(self, did):
            self.reads += 1
            if self.reads == 2:
                raise RuntimeError("Google said 503: The service is currently unavailable.")
            text = "First line\n"
            return {
                "title": "Notes",
                "body": {
                    "content": [
                        {"endIndex": 1, "sectionBreak": {}},
                        {
                            "startIndex": 1,
                            "endIndex": 12,
                            "paragraph": {
                                "elements": [{"startIndex": 1, "endIndex": 12, "textRun": {"content": text}}],
                                "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                            },
                        },
                    ]
                },
            }

        async def docs_batch(self, did, requests):
            self.sent += requests
            return {}

    g = Docs()
    doc = GoogleDoc(g, "d1")
    await doc.load()
    assert await doc.apply({"op": "add_paragraph", "text": "Second line"}) == "Added a paragraph"
    assert doc.stale and len(g.sent) == 2  # the text and its style, once
    await doc.apply({"op": "add_paragraph", "text": "Third line"})
    assert g.reads == 4 and not doc.stale  # read before the next edit, and after it


async def test_what_the_first_pass_did_is_kept_when_the_second_model_call_fails():
    """The second call is only there to mend what failed. When the model was busy for it, the
    whole job ended "failed", with the first pass's edits already in the Google file and
    nothing said about them, so the next request did them again."""
    g = Sheets(Tab("Sheet1", 0, SALES))
    calls = []

    async def ask(system, prompt):
        calls.append(prompt)
        if len(calls) == 2:
            raise RuntimeError("overloaded")
        edits = [{"file": 0, "op": "set_cells", "cells": {"E1": "Checked"}}, {"file": 0, "op": "chart", "sheet": "Nope", "values": "Sales"}]
        return json.dumps({"edits": edits, "summary": "Done."}), 0.0

    r = await run_ledger("x", files=[], google=g, google_refs=[URL], ask=ask)
    assert r.changes == ["Set 1 cells on Sheet1"] and r.links == [URL]
    assert r.problems == ["There's no sheet called 'Nope'."]


def test_word_takes_after_start_as_the_top_of_the_document(tmp_path):
    """The menu offers "after": "start" for Word and Docs alike. Word answered "There's no
    paragraph start", and "after": 0 puts a title under the first line, not over it."""
    from docx import Document

    d = Document()
    d.add_paragraph("First line")
    d.add_paragraph("Second line")
    d.save(str(tmp_path / "w.docx"))
    doc = WordDoc(tmp_path / "w.docx")
    doc.apply({"op": "add_bullets", "items": ["one", "two"], "after": "start"})
    assert doc.apply({"op": "add_heading", "text": "Title", "after": "start"}) == "Added heading 'Title'"
    assert [p.text for p in doc.paras] == ["Title", "one", "two", "First line", "Second line"]


async def test_a_tab_name_is_quoted_and_encoded_the_way_google_asks():
    """A tab called "Mom's budget" or "Sheet #2". In a range, the name goes in single quotes
    with an apostrophe in it doubled; in the address of a read, every character is encoded
    (the # used to cut the address short). Dates come back as they show, not as numbers."""
    g = Sheets(Tab("Mom's budget", 0, [["Item", "Amount"], ["Rent", 900]]))
    r, _ = await on_google(
        g,
        [
            {"file": 0, "op": "set_cells", "cells": {"A3": "Food"}},
            {"file": 0, "op": "format", "range": "'Mom''s budget'!A1:B1", "bold": True},
        ],
    )
    assert not r.problems and g.wrote[0]["range"] == "'Mom''s budget'!A3"
    assert next(d for n, d in g.calls if n == "read")[0] == ("'Mom''s budget'!A1:Z1000",)
    assert split_tab("'Mom''s budget'!A1:B1") == ("Mom's budget", "A1:B1")

    class Stub:
        _render = staticmethod(GoogleConnector._render)
        asked: list[tuple[str, dict[str, Any]]] = []

        def _need_sheets(self) -> None:
            pass

        async def _api(self, method, url, **kw):
            self.asked.append((url, kw["params"]))
            return {"values": [[1]], "valueRanges": [{"values": [[1]]}, {}]}

    stub = Stub()
    assert await GoogleConnector.sheets_values(stub, "ID", "'Sheet #2'!A1:Z500", formulas=True) == [[1]]  # type: ignore[arg-type]
    url, params = stub.asked[0]
    assert url.endswith("/ID/values/%27Sheet%20%232%27%21A1%3AZ500")
    assert httpx.URL(url).fragment == "" and httpx.URL(url).raw_path.endswith(b"%21A1%3AZ500")
    assert params == {"valueRenderOption": "FORMULA", "dateTimeRenderOption": "FORMATTED_STRING"}
    both = await GoogleConnector.sheets_values_batch(stub, "ID", ["'A'!A1:B2", "'B'!A1:B2"])  # type: ignore[arg-type]
    url, params = stub.asked[1]
    assert both == [[[1]], []] and url.endswith("/ID/values:batchGet")
    assert params == {"ranges": ["'A'!A1:B2", "'B'!A1:B2"], "valueRenderOption": "FORMATTED_VALUE"}


# ============================================================================ found by the review
# An independent review of the fixes above found twelve things they had broken. Each is here.
DATED = [
    ["Date", "Region", "Product", "Units", "Sales"],
    ["2026-01-05", "North", "Widget", 10, 1200],
    ["2026-01-06", "North", "Gadget", 7, 850],
    ["2026-01-07", "South", "Widget", 8, 950],
    ["2026-01-08", "South", "Gadget", 5, 700],
    ["2026-01-09", "East", "Widget", 9, 1100],
    ["2026-01-10", "East", "Gadget", 4, 600],
]
WEST = {"file": 0, "op": "append_row", "values": ["2026-01-11", "West", "Widget", 3, 400]}
BY_REGION = {"file": 0, "op": "pivot", "group_by": "Region", "value": "Sales"}
SAMPLE_JOB = [
    {"file": 0, "op": "add_sheet", "name": "Sample Sales", "rows": DATED},
    {"file": 0, "op": "pivot", "sheet": "Sample Sales", "group_by": "Region", "value": "Sales"},
    {"file": 0, "op": "chart", "sheet": "Sample Sales", "labels": "Region", "values": "Sales"},
]


def pivot_of(value: int = 4, fn: str = "SUM", sheet: int = 0) -> dict[str, Any]:
    return {
        "source": {"sheetId": sheet, "startRowIndex": 0, "endRowIndex": 7},
        "rows": [{"sourceColumnOffset": 1}],
        "values": [{"summarizeFunction": fn, "sourceColumnOffset": value}],
    }


def beside(fn: str = "SUM") -> Sheets:
    """The dated sales with a pivot of Sales by Region beside them, from G1."""
    g = Sheets(Tab("Sheet1", 0, DATED))
    g.show_pivot(g.tab("Sheet1"), 0, 6, pivot_of(fn=fn))
    return g


def on_pivot_tab() -> Sheets:
    """The dated sales, and their pivot on a tab called Pivot."""
    g = Sheets(Tab("Sheet1", 0, DATED), Tab("Pivot", 901))
    g.next_id = 901
    g.show_pivot(g.tab("Pivot"), 0, 0, pivot_of())
    return g


def shows(g: Sheets, title: str) -> list[list[Any]]:
    return g._grid(f"'{title}'!A1:Z1000", False)


def drawn(g: Sheets, title: str = "Sheet1") -> tuple[int, int, int, int]:
    """The last chart on a tab: its first row, one past its last, its label and value columns."""
    spec = g.tab(title).charts[-1]["spec"]["basicChart"]
    labels = spec["domains"][0]["domain"]["sourceRange"]["sources"][0]
    values = spec["series"][0]["series"]["sourceRange"]["sources"][0]
    return labels["startRowIndex"], labels["endRowIndex"], labels["startColumnIndex"], values["startColumnIndex"]


async def test_a_pivot_asked_for_again_is_written_again_with_the_rows_there_now():
    """"That pivot is already there" was decided from the pivot's top row alone. A row was
    added and the pivot asked for again in one plan: it was skipped, and stayed without the
    row. Q1 and Q2 with the same headings: Q2's pivot was skipped because Q1's was on the tab.
    The same pivot is now written again where it is, from the rows and the tab asked for."""
    g = beside()
    r, _ = await on_google(g, [WEST, BY_REGION])
    assert r.changes[1] == "Updated the pivot beside the data on Sheet1, from G1: sum of Sales by Region"
    assert [row[6] for row in shows(g, "Sheet1")[:6]] == ["Region", "East", "North", "South", "West", "Grand Total"]
    assert len(g.pivots()) == 1 and g.pivots()[0]["updateCells"]["start"]["columnIndex"] == 6

    g = on_pivot_tab()
    r, _ = await on_google(g, [WEST, {**BY_REGION, "target": "Pivot"}])
    assert r.changes[1] == "Updated the pivot on tab Pivot: sum of Sales by Region"
    assert [t.title for t in g.tabs] == ["Sheet1", "Pivot"] and shows(g, "Pivot")[4] == ["West", "400.0"]

    q2 = [DATED[0]] + [[d, region, p, u * 3, s * 3] for d, region, p, u, s in DATED[1:]]
    g = Sheets(Tab("Q1", 0, DATED), Tab("Q2", 1, q2), Tab("Pivot", 901))
    g.show_pivot(g.tab("Pivot"), 0, 0, pivot_of())
    r, _ = await on_google(g, [{**BY_REGION, "sheet": "Q2", "target": "Pivot"}])
    assert r.changes == ["Updated the pivot on tab Pivot: sum of Sales by Region"]
    assert g.pivots()[0]["updateCells"]["rows"][0]["values"][0]["pivotTable"]["source"]["sheetId"] == 1
    assert shows(g, "Pivot")[1] == ["East", "5100.0"]  # Q2's numbers, three times Q1's


async def test_a_chart_after_an_average_pivot_draws_that_pivot_and_makes_no_second_one():
    """"Average sales by region, and chart it": the chart only took a pivot headed "SUM of
    Sales", so it made a second pivot beside the first and drew sums under the title
    "Average sales by region". Any summary of the column will do; a sum is taken first."""
    g = Sheets(Tab("Sheet1", 0, DATED))
    r, _ = await on_google(
        g,
        [
            {**BY_REGION, "agg": "average"},
            {"file": 0, "op": "chart", "labels": "Region", "values": "Sales", "title": "Average sales by region"},
        ],
    )
    assert not r.problems and len(g.pivots()) == 1
    assert shows(g, "Sheet1")[0][6:] == ["Region", "AVERAGE of Sales"] and drawn(g) == (0, 4, 6, 7)

    g = beside("AVERAGE")  # an average at G, a sum at J: the sum is the one a plain chart means
    g.show_pivot(g.tab("Sheet1"), 0, 9, pivot_of())
    await on_google(g, [{"file": 0, "op": "chart", "labels": "Region", "values": "Sales"}])
    assert drawn(g) == (0, 4, 9, 10) and not g.pivots()


async def test_a_chart_of_the_pivots_own_cells_draws_its_groups_and_no_grand_total():
    """The outline now lists a pivot's cells and says "chart them". Asked for by its own
    heading, the chart ended "There's no column called 'SUM of Sales'" (it went to make a
    pivot of a column of that name). Asked for by its letters, G and H, it drew every row down
    to the end of the data: the three regions, a Grand Total bar, and blanks."""
    g = beside()
    r, ask = await on_google(g, [{"file": 0, "op": "chart", "labels": "Region", "values": "SUM of Sales"}])
    assert r.changes == ["Added a bar chart of SUM of Sales by Region on Sheet1"] and len(ask.prompts) == 1
    assert drawn(g) == (0, 4, 6, 7) and not g.pivots()

    g = beside()
    r, _ = await on_google(g, [{"file": 0, "op": "chart", "labels": "G", "values": "H"}])
    assert not r.problems and drawn(g) == (0, 4, 6, 7)


async def test_pivot_and_chart_do_not_hang_on_the_words_sum_of():
    """Google words a pivot's headings in the spreadsheet's language. Where "SUM of Sales"
    reads "SUM von Sales", the chart didn't know the pivot made a moment before, made a
    second one, didn't know that one either, and drew a bar for every row of the data. And
    the same pivot asked for twice went in twice, side by side."""
    g = Sheets(Tab("Sheet1", 0))
    g.heading, g.total = (lambda fn, name: f"{fn} von {name}"), "Gesamtergebnis"
    r, _ = await on_google(g, SAMPLE_JOB)
    assert not r.problems and len(g.pivots()) == 1
    assert drawn(g) == (0, 4, 6, 7)  # the three regions: not the data's rows, not the total
    r, _ = await on_google(g, [BY_REGION])
    assert r.changes == ["Updated the pivot beside the data on Sheet1, from G1: sum of Sales by Region"]
    assert shows(g, "Sheet1")[0][5:] == ["", "Region", "SUM von Sales"]  # still the one pivot

    # A wording that doesn't even end in the column's name. The pivot this job wrote is at a
    # cell Ledger knows; one from an earlier job is not, so the chart makes its own and uses it.
    g = Sheets(Tab("Sheet1", 0))
    g.heading, g.total = (lambda fn, name: f"{name} ({fn.title()})"), "Gesamt"
    r, _ = await on_google(g, SAMPLE_JOB)
    assert not r.problems and len(g.pivots()) == 1 and drawn(g) == (0, 4, 6, 7)
    await on_google(g, [{"file": 0, "op": "chart", "labels": "Region", "values": "Sales"}])
    assert len(g.pivots()) == 2 and drawn(g) == (0, 4, 9, 10)


async def test_an_edit_that_names_no_tab_follows_a_rename_and_a_delete():
    """The tabs were read again only before some kinds of edit. After "rename the tab to
    Sales", cells set with no tab named were sent to 'Sheet1'!D1: "Unable to parse range", and
    a second model call. After the first tab was deleted, they were sent to the deleted tab."""
    cell = {"file": 0, "op": "set_cells", "cells": {"D1": "Notes"}}
    g = Sheets(Tab("Sheet1", 0, SALES))
    r, ask = await on_google(g, [{"file": 0, "op": "rename_sheet", "name": "Sales"}, cell])
    assert r.changes == ["Renamed the tab Sheet1 to Sales", "Set 1 cells on Sales"]
    assert not r.problems and len(ask.prompts) == 1 and g.wrote[0]["range"] == "'Sales'!D1"

    g = Sheets(Tab("Old", 0, SALES), Tab("Keep", 5, SALES))
    r, ask = await on_google(g, [{"file": 0, "op": "delete_sheet", "sheet": "Old"}, cell])
    assert r.changes == ["Deleted the tab Old", "Set 1 cells on Keep"] and len(ask.prompts) == 1

    g = Sheets(Tab("Sheet1", 0, SALES))  # the old name is no longer a tab, and is said not to be
    r, _ = await on_google(g, [{"file": 0, "op": "rename_sheet", "name": "Sales"}, {**cell, "sheet": "Sheet1"}], [])
    assert r.problems == ["There's no sheet called 'Sheet1'."] and not g.wrote
    r, _ = await on_google(g, [{"file": 0, "op": "totals_row"}])  # what was read moved with the name
    assert r.changes == ["Added a Total row (row 8) on Sales: Sales 5,400"]


async def test_a_different_pivot_aimed_at_a_taken_tab_gets_a_tab_named_for_itself():
    """A pivot of Units asked for on "Pivot", which held the pivot of Sales: an error, and a
    second model call (before that: "Pivot 2"). It goes on a tab named for what it is, and
    asked for again it is written again there, not on a "Units by Region 2"."""
    units = {**BY_REGION, "value": "Units", "target": "Pivot"}
    g = on_pivot_tab()
    r, ask = await on_google(g, [units])
    assert r.changes == ["Made a pivot table on tab Units by Region: sum of Units by Region"]
    assert len(ask.prompts) == 1 and [t.title for t in g.tabs] == ["Sheet1", "Pivot", "Units by Region"]
    assert shows(g, "Pivot")[0] == ["Region", "SUM of Sales"]  # the one that was there is untouched
    r, _ = await on_google(g, [units])
    assert r.changes == ["Updated the pivot on tab Units by Region: sum of Units by Region"]
    assert len(g.tabs) == 3


async def test_a_workbook_with_a_picture_is_still_saved_after_libreoffice_worked_it_out(tmp_path, monkeypatch):
    """With LibreOffice installed, a sort by a formula column saved the workbook to a temp
    file for it. openpyxl can write a picture it loaded only once: the job's own save, at the
    end, died with "I/O operation on closed file" and nothing was saved. And LibreOffice ran
    on the event loop, so all of Pyxi stood still for 6 to 14 seconds."""
    import threading

    from openpyxl.drawing.image import Image
    from PIL import Image as Png

    def with_logo(wb, ws):
        Png.new("RGB", (20, 20), (200, 30, 30)).save(tmp_path / "logo.png")
        ws.add_image(Image(str(tmp_path / "logo.png")), "H2")

    ran_on = []

    def convert(soffice, src, out, to="xlsx"):
        ran_on.append(threading.current_thread())
        out.mkdir(parents=True, exist_ok=True)
        multiplied(load_workbook(src), "").save(out / "book.xlsx")

    monkeypatch.setattr(ledger, "_soffice", lambda: "soffice.exe")
    monkeypatch.setattr(ledger, "_convert", convert)
    path = book(tmp_path / "logo.xlsx", UNITS, with_logo)
    r = await run_ledger(
        "x", files=[str(path)], ask=answers([TOTAL, {"file": 0, "op": "sort", "by": "Total", "descending": True}])
    )
    assert not r.problems and len(r.outputs) == 1
    saved = load_workbook(r.outputs[0])["Data"]
    assert [saved.cell(i, 1).value for i in range(2, 6)] == ["South", "West", "North", "East"]
    assert len(saved._images) == 1  # the logo is in the copy
    assert ran_on and ran_on[0] is not threading.main_thread()  # and Pyxi was never held up


def test_only_one_libreoffice_runs_at_a_time(tmp_path, monkeypatch):
    """Two runs of LibreOffice share Pyxi's one profile: the second hands its file to the
    first and exits before anything is written. Pixel takes a lock round each run; Ledger's
    runs (working formulas out, opening an old .xls) took none."""
    import subprocess

    from pyxi.agents import libreoffice

    held = []

    def run(cmd, **kw):
        held.append(libreoffice.CONVERTING.locked())
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", run)
    ledger._convert("soffice.exe", tmp_path / "a.xlsx", tmp_path / "out")
    assert held == [True] and not libreoffice.CONVERTING.locked()


FIGURES = [
    ["Region", "Rep", "Units", "Price", "Total"],
    ["North", "Ana", 10, 5, "=C2*D2"],
    ["South", "Bo", 2, 100, "=C3*D3"],
    ["East", "Cy", 30, 1, "=C4*D4"],
    ["West", "Di", 4, 20, "=C5*D5"],
    ["North", "Ed", 6, 10, "=C6*D6"],
    ["South", "Bo", 2, 100, "=C7*D7"],
    ["Total", None, None, None, "=SUM(E2:E7)"],
]


def calculated(path) -> Workbook:
    """The figures as Excel leaves a file: every formula with its result stored."""
    b = Workbook(book(path, FIGURES))
    results = NewBook()
    results.active.title = "Data"
    for row in FIGURES:
        results.active.append([620 if row[0] == "Total" and c == 4 else row[2] * row[3] if c == 4 and row[0] != "Region" else v for c, v in enumerate(row)])
    b.values, b._calculated = results, True
    return b


def reps(b: Workbook) -> list[Any]:
    return [r[1] for r in b.wb["Data"].iter_rows(values_only=True) if r[1] not in (None, "Rep")]


def test_stored_results_go_with_their_cells_when_rows_move(tmp_path, monkeypatch):
    """Without LibreOffice, any row put in or taken out threw away every stored result on the
    sheet. "Add a title row, then sort by Total" on a file Excel had calculated was refused:
    "E3 is a formula that hasn't been worked out yet". The results now move with their cells.
    One whose formula read a row that was deleted is not kept: its sum is another number."""
    monkeypatch.setattr(ledger, "_worked_out", lambda wb, suffix: None)
    b = calculated(tmp_path / "a.xlsx")
    b.apply({"op": "insert_rows", "at": 1})
    assert b.apply({"op": "sort", "by": "Total", "descending": True}) == "Sorted Data by Total, largest first"
    assert reps(b) == ["Bo", "Bo", "Di", "Ed", "Ana", "Cy"]  # 200, 200, 80, 60, 50, 30

    b = calculated(tmp_path / "b.xlsx")
    assert b.apply({"op": "remove_duplicates", "columns": ["Rep"]}) == "Removed 1 duplicate row on Data"
    b.apply({"op": "sort", "by": "Total", "descending": True})
    assert reps(b) == ["Bo", "Di", "Ed", "Ana", "Cy"]
    ws = b.wb["Data"]
    assert ws["E7"].value == "=SUM(E2:E6)" and b.result(ws, 2, 5) == 200
    with pytest.raises(LedgerError, match="hasn't been worked out yet"):
        b.result(ws, 7, 5)  # the total was 620 with the row that's gone: not said again


def test_removing_duplicates_does_not_need_results_nobody_asked_about(tmp_path, monkeypatch):
    """On a file with no stored results, "remove duplicates" used to say "Removed 0" and then
    said a formula hadn't been worked out. Two rows are the same when their cells are and
    their formulas are the same one; only a formula column named as the thing to compare by
    needs its results."""
    monkeypatch.setattr(ledger, "_worked_out", lambda wb, suffix: None)
    b = Workbook(book(tmp_path / "p.xlsx", FIGURES))
    assert b.apply({"op": "remove_duplicates"}) == "Removed 1 duplicate row on Data"
    assert reps(b) == ["Ana", "Bo", "Cy", "Di", "Ed"]
    with pytest.raises(LedgerError, match="hasn't been worked out yet"):
        b.apply({"op": "remove_duplicates", "columns": ["Total"]})


async def test_a_column_of_hours_is_still_totalled():
    """A timesheet: hours as 7:30:00. Telling dates from numbers by what a cell shows took
    the colon for a date's, and the Hours column was left out of the total row."""
    rows = [["Day", "Task", "Hours"], ["Mon", "Build", "7:30:00"], ["Tue", "Test", "6:00:00"], ["Wed", "Ship", "4:30:00"]]
    g = Sheets(Tab("Sheet1", 0, rows))
    r, _ = await on_google(g, [{"file": 0, "op": "totals_row"}])
    assert r.changes == ["Added a Total row (row 5) on Sheet1: Hours 18:00"]
    assert g.wrote[0]["values"] == [["Total", "", "=SUM(C2:C4)"]]


def big(n_rows: int, n_cols: int) -> list[list[Any]]:
    head = ["Region"] + [f"Col{i}" for i in range(n_cols - 1)]
    return [head] + [[("North", "South", "East", "West")[r % 4]] + [r % 97 + c for c in range(n_cols - 1)] for r in range(n_rows - 1)]


async def test_a_big_tab_is_read_whole_once_by_the_edit_that_needs_it_and_not_before():
    """Every tab was read whole, as typed and as shown, at the start and again before every
    edit that works from the cells: 4.8 million cells for a pivot, a chart and a total on a
    20,000-row tab. The first read now stops at row 2,000 (the outline shows 40). An edit that
    needs the data reads its own tab to the end, once; after a pivot goes in beside the
    data only what shows is read again; a tab no edit works from is never read further."""
    data, other = Tab("Sheet1", 0, big(20000, 6)), Tab("Other", 7, big(5000, 3))
    data.size, other.size = (20000, 26), (5000, 26)
    g = Sheets(data, other)
    r, ask = await on_google(
        g,
        [
            {"file": 0, "op": "pivot", "group_by": "Region", "value": "Col0"},
            {"file": 0, "op": "chart", "labels": "Region", "values": "Col0"},
            {"file": 0, "op": "totals_row"},
        ],
    )
    assert not r.problems and r.changes[2].startswith("Added a Total row (row 20001) on Sheet1: Col0 ")
    reads = [d for n, d in g.calls if n == "read"]
    assert [(ranges, typed) for ranges, typed in reads] == [
        (("'Sheet1'!A1:Z2000", "'Other'!A1:Z2000"), True),  # the outline
        (("'Sheet1'!A1:Z2000", "'Other'!A1:Z2000"), False),
        (("'Sheet1'!A1:Z20000",), True),  # the pivot's tab, whole
        (("'Sheet1'!A1:Z20000",), False),
        (("'Sheet1'!A1:Z20000",), False),  # the chart: what shows, now the pivot is in
    ]
    assert g.cells_read < 420_000  # it was over a million
    assert "Tab 'Sheet1': its first 2,000 rows (it has more)." in ask.prompts[0]
    assert drawn(g) == (0, 5, 7, 8) and data.size == (20001, 26)  # a row was added for the total

    g = Sheets(Tab("Sheet1", 0, big(20000, 6)))  # an edit that needs no cells reads no more
    g.tabs[0].size = (20000, 26)
    await on_google(g, [{"file": 0, "op": "format", "range": "A1:F1", "bold": True}])
    assert g.count("read") == 2 and g.cells_read == 2 * 2000 * 6


async def test_the_blank_sheet_job_is_six_round_trips():
    """Sample data, a pivot and a chart on a blank sheet, the job this all started from: ten
    round trips before the audit, nine after it, six now. What Ledger has just written it
    doesn't read back, and the tabs are not listed again when no tab changed."""
    g = Sheets(Tab("Sheet1", 0))
    r, _ = await on_google(g, SAMPLE_JOB)
    assert len(r.changes) == 3 and not r.problems and drawn(g) == (0, 4, 6, 7)
    # The two reads at the start go together (as typed, as shown): one round trip's wait.
    assert [n for n, _ in g.calls] == ["meta", "read", "read", "write", "batch", "read", "batch"]


async def test_an_edit_refused_before_it_wrote_anything_does_not_reopen_the_file(tmp_path, monkeypatch):
    """So that half an edit is never saved, the file was opened again after any failed edit
    at all: four seconds more on a big workbook for "There's no column called 'Nope'", which
    wrote nothing. Only an edit that can have got part-way has anything to undo."""
    opened = []
    real = ledger.open_local
    monkeypatch.setattr(ledger, "open_local", lambda p: opened.append(p) or real(p))
    path = book(tmp_path / "o.xlsx", SALES, lambda wb, ws: ws.merge_cells("E1:F1"))
    plan = [
        {"file": 0, "op": "format", "range": "A1:C1", "bold": True},
        {"file": 0, "op": "sort", "by": "Nope"},
        {"file": 0, "op": "freeze", "rows": 1},
    ]
    r = await run_ledger("x", files=[str(path)], ask=answers(plan, []))
    assert len(r.changes) == 2 and r.problems == ["There's no column called 'Nope'."]
    assert len(opened) == 1

    opened.clear()  # one that died part-way: opened again, once, and the half is gone
    plan[1] = {"file": 0, "op": "set_cells", "cells": {"A2": "WRONG", "F1": "x"}}
    r = await run_ledger("x", files=[str(path)], ask=answers(plan, []))
    assert len(opened) == 2 and cells(r.outputs[0])[1][0] == "North"


async def test_the_problem_dropped_after_the_second_pass_is_the_one_it_mended():
    """Two edits failed, and the second reply mended the second of them. The problem that
    was dropped was the first on the list, so the card told them about the wrong one."""
    g = Sheets(Tab("Sheet1", 0, SALES))
    chart = {"file": 0, "op": "chart", "sheet": "Nope", "values": "Sales"}
    colour = {"file": 0, "op": "format", "range": "A1:C1", "color": "sort of teal"}
    r, _ = await on_google(g, [chart, colour], [{"file": 0, "op": "format", "range": "A1:C1", "color": "teal"}])
    assert r.changes == ["Formatted A1:C1 on Sheet1"]
    assert r.problems == ["There's no sheet called 'Nope'."]
