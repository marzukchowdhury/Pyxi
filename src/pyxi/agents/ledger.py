"""Ledger, the documents teammate: Excel, Word and PowerPoint edited in the files themselves, and
Google Sheets and Docs through their APIs. No Office license and no app windows.

How a job runs: Ledger reads each file into a short, addressed outline (cells with their
coordinates, numbered paragraphs, numbered slides), asks the model once for a list of edits in
a small, fixed vocabulary (``totals_row``, ``pivot``, ``set_cells``, ``add_heading``,
``add_bullets``...), applies them exactly, and saves a copy beside the original:
"budget (edited by Pyxi).xlsx". Originals are never overwritten. Google files are edited in
place (Google keeps every earlier version under File > Version history).

The edits are plain functions too, so they're tested without a model.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pyxi.agents.ledger_common import LedgerError, a1_tab, cell_rc, col_letter, split_tab
from pyxi.agents.ledger_common import rgb as _rgb
from pyxi.agents.ledger_google import DocExtras, GoogleSlides, SheetExtras
from pyxi.agents.ledger_live import Unreachable
from pyxi.agents.ledger_live import menus as _live_menus
from pyxi.agents.ledger_office import DeckExtras, WordExtras, WorkbookExtras
from pyxi.agents.pixel import out_dir_for, unique_path

__all__ = ["GoogleDoc", "GoogleSheet", "GoogleSlides", "LedgerError", "run_ledger"]

log = logging.getLogger(__name__)

LOCAL_EXTS = {".xlsx", ".xlsm", ".docx", ".pptx"}


# ============================================================================ Excel
def _col_index(ws, ref: str | int | None, header_row: int) -> int | None:
    """A column from its letter ('C'), its header ('Amount') or a 1-based number."""
    from openpyxl.utils import column_index_from_string

    if ref is None or ref == "":
        return None
    if isinstance(ref, int):
        return ref
    s = str(ref).strip()
    for c in range(1, ws.max_column + 1):
        v = ws.cell(header_row, c).value
        if v is not None and str(v).strip().lower() == s.lower():
            return c
    # A letter or number only for a column the sheet has: a header that isn't there ("Tax")
    # must not become column TAX, 13,000 columns to the right.
    if re.fullmatch(r"[A-Za-z]{1,3}", s) and column_index_from_string(s.upper()) <= ws.max_column:
        return column_index_from_string(s.upper())
    if s.isdigit() and 1 <= int(s) <= ws.max_column:
        return int(s)
    raise LedgerError(f"There's no column called {s!r}.")


def _header_row(ws) -> int:
    for r in range(1, min(ws.max_row, 20) + 1):
        if (
            sum(1 for c in range(1, ws.max_column + 1) if ws.cell(r, c).value not in (None, ""))
            >= 2
        ):
            return r
    return 1


def _last_row(ws) -> int:
    for r in range(ws.max_row, 0, -1):
        if any(ws.cell(r, c).value not in (None, "") for c in range(1, ws.max_column + 1)):
            return r
    return 0


def _is_number(v: Any) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool)


def _is_numberish(v: Any) -> bool:
    return _is_number(v) or (isinstance(v, str) and v.startswith("="))


def _shown(x: float) -> str:
    """1850.0 as "1,850", 296.517 as "296.52": for the summary they read."""
    x = round(float(x), 2)
    return f"{int(x):,}" if x.is_integer() else f"{x:,}"


def _is_total_label(v: Any) -> bool:
    return isinstance(v, str) and bool(re.match(r"\s*(grand\s+)?totals?\b", v, re.I))


_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_DATE_FORMULA = re.compile(r"=\s*DATE\s*\(", re.I)


def _dated(ws, cell) -> None:
    """A date the model wrote (=DATE(2026,10,3), or "2026-10-03" under other dates) shows as a
    date, not as 46298 or as text: in the column's own date format when it has one."""
    from datetime import datetime

    from openpyxl.styles.numbers import is_date_format

    above = ws.cell(cell.row - 1, cell.column) if cell.row > 1 else None
    fmt = getattr(above, "number_format", "") or ""
    fmt = fmt if is_date_format(fmt) else ""  # a date above (or a =DATE() made a moment ago)
    v = cell.value
    if isinstance(v, str) and fmt and _ISO_DATE.fullmatch(v.strip()):
        cell.value = datetime.fromisoformat(v.strip())
    elif not (isinstance(v, str) and _DATE_FORMULA.match(v)):
        return
    if fmt or cell.number_format == "General":
        cell.number_format = fmt or "yyyy-mm-dd"


class Unsolved(LedgerError):
    """A formula whose result is needed and isn't known: Excel hasn't calculated it (or its rows
    have moved since), and LibreOffice isn't there to work it out."""


def _worked_out(wb: Any, suffix: str) -> Any:
    """This workbook as it stands, with every formula calculated by LibreOffice: a values-only
    copy, or None when LibreOffice isn't installed or couldn't do it."""
    import shutil
    import tempfile

    from openpyxl import load_workbook

    soffice = _soffice()
    if soffice is None:
        return None
    tmp = Path(tempfile.mkdtemp(prefix="pyxi_calc_"))
    pictures = [(ws, ws._images) for ws in wb.worksheets]
    try:
        src = tmp / f"book{suffix if suffix.lower() in ('.xlsx', '.xlsm') else '.xlsx'}"
        # Saved without its pictures: openpyxl can write a picture it loaded only once, so
        # this save used to leave the real one, at the end of the job, with "I/O operation on
        # closed file" and nothing saved at all. The sums don't need the logo.
        try:
            for ws, _ in pictures:
                ws._images = []
            wb.save(src)
        finally:
            for ws, images in pictures:
                ws._images = images
        _convert(soffice, src, tmp / "out")
        return load_workbook(tmp / "out" / "book.xlsx", data_only=True)
    except Exception:  # never the command line; the edit that needed it says what to do
        log.info("ledger: LibreOffice couldn't work the formulas out", exc_info=True)
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _soffice() -> str | None:
    """Where LibreOffice is, or None when it isn't installed."""
    from pyxi.agents.pixel import libreoffice

    return libreoffice()


def _convert(soffice: str, src: Path, out: Path, to: str = "xlsx") -> None:
    """One run of LibreOffice, turning ``src`` into ``to`` in the folder ``out``. Never on
    the event loop (it takes seconds), and one at a time: two runs share Pyxi's one
    LibreOffice profile, and the second would hand its file to the first and exit."""
    import subprocess

    from pyxi.agents.libreoffice import CONVERTING, command

    with CONVERTING:
        subprocess.run(
            command(soffice, "--convert-to", to, "--outdir", str(out), str(src)),
            check=True,
            capture_output=True,
            timeout=180,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )


class Workbook(WorkbookExtras):
    """An .xlsx opened twice: once for formulas (what we edit) and once for the values Excel last
    calculated (what the outline and summaries quote)."""

    kind = "xlsx"

    def __init__(self, path: Path) -> None:
        from openpyxl import load_workbook

        self.path = path
        self.wb = load_workbook(path, keep_vba=path.suffix.lower() == ".xlsm")
        self.default = self.wb.active.title  # edits with no sheet go here, even after a pivot
        try:
            self.values = load_workbook(path, data_only=True)
        except Exception:
            self.values = None
        # Which of Excel's stored results still hold: only a formula that is the same text in
        # the same cell, on a sheet whose rows haven't moved since the file was opened.
        self._origin = {id(ws): ws.title for ws in self.wb.worksheets}
        self._was = {
            ws.title: {at: c.value for at, c in ws._cells.items() if c.data_type == "f"}
            for ws in self.wb.worksheets
        }
        # Has Excel ever calculated this file? Then a formula with nothing stored came to
        # nothing (=IF(A2="","",..)); in a file nobody calculated, nothing stored means unknown.
        self._calculated = self.values is not None and any(
            self.values[title].cell(*at).value is not None
            for title, cells in self._was.items()
            for at in cells
        )
        self._moved: set[int] = set()  # sheets (by id) where rows or columns were moved
        # After a sort: the results that moved with their rows, as {cell: (formula, result)}.
        self._carried: dict[int, dict[tuple[int, int], tuple[str, Any]]] = {}
        self._edits = 0  # how many edits have run: a LibreOffice calculation is good for one
        self._solved: Any = None
        self._solved_at = -1

    def sheet(self, name: str | None):
        if not name:
            return self.wb[self.default]
        for ws in self.wb.worksheets:
            if ws.title.lower() == str(name).lower():
                return ws
        raise LedgerError(
            f"There's no sheet called {name!r}; it has {', '.join(self.wb.sheetnames)}."
        )

    def _stored(self, ws, r: int, c: int) -> Any:
        """What Excel last worked out for a formula cell, or None when that isn't known or no
        longer holds (a formula written in this job, a row that has been sorted elsewhere)."""
        origin = self._origin.get(id(ws))
        if id(ws) in self._moved:  # only what a sort took along with its rows still holds
            text, kept = self._carried.get(id(ws), {}).get((r, c), (None, None))
            return kept if text is not None and text == ws.cell(r, c).value else None
        if self.values is None or origin is None:
            return None
        if self._was.get(origin, {}).get((r, c)) != ws.cell(r, c).value:
            return None
        try:
            stored = self.values[origin].cell(r, c).value
        except KeyError:
            return None
        return "" if stored is None and self._calculated else stored

    def value(self, ws, r: int, c: int) -> Any:
        """A cell for a summary: a formula's stored result when there is one, else the formula."""
        v = ws.cell(r, c).value
        if isinstance(v, str) and v.startswith("="):
            cached = self._stored(ws, r, c)
            return cached if cached is not None else v
        return v

    def result(self, ws, r: int, c: int, *, work_out: bool = True) -> Any:
        """A cell where its value decides something: the order of a sort, which rows are
        duplicates, what a "values only" copy pastes, a pivot's groups.

        A formula with no stored result (every file saved by Ledger itself, and any column
        added in the same job) used to stand in as its own text: a sort by Total ordered the
        rows by "=C2*D2", and a pivot's summary said every group came to 0. Now LibreOffice
        works the workbook out when it's installed, and otherwise the edit says it can't."""
        v = ws.cell(r, c).value
        if not (isinstance(v, str) and v.startswith("=")):
            return v
        got = self._stored(ws, r, c)
        if got is None and (work_out or self._solved_at == self._edits):
            if self._solved_at != self._edits:
                self._solved_at, self._solved = self._edits, _worked_out(self.wb, self.path.suffix)
            if self._solved is not None and ws.title in self._solved.sheetnames:
                got = self._solved[ws.title].cell(r, c).value
                got = "" if got is None else got  # worked out, and it came to nothing
        if got is None:
            from openpyxl.utils import get_column_letter

            raise Unsolved(
                f"{get_column_letter(c)}{r} on {ws.title} is a formula that hasn't been worked "
                "out yet (Excel does that when it opens the file), and this edit needs its "
                "result. Open the file in Excel and save it, or turn on the free LibreOffice "
                "converter in Settings (under Your files), then ask again."
            )
        return got

    def describe(self, max_rows: int = 40) -> str:
        from openpyxl.utils import get_column_letter

        out = [
            f"Excel workbook {self.path.name}. Sheets: {', '.join(self.wb.sheetnames)} (active: {self.wb.active.title})."
        ]
        for ws in self.wb.worksheets:
            last = _last_row(ws)
            out.append(
                f"\nSheet {ws.title!r}: {last} rows x {ws.max_column} columns; header row {_header_row(ws)}."
            )
            rows = list(range(1, last + 1))
            if len(rows) > max_rows:
                rows = [*rows[: max_rows - 6], -1, *rows[-5:]]
            for r in rows:
                if r == -1:
                    out.append("  ...")
                    continue
                cells = []
                for c in range(1, ws.max_column + 1):
                    v = ws.cell(r, c).value
                    if v in (None, ""):
                        continue
                    shown = repr(v) if not isinstance(v, str) or not v.startswith("=") else v
                    cells.append(f"{get_column_letter(c)}{r}={shown}"[:80])
                if cells:
                    out.append("  " + " | ".join(cells))
            out.extend(self.chart_lines(ws))
        return "\n".join(out)

    # ---------------------------------------------------------------- edits
    def apply(self, op: dict[str, Any]) -> str:
        name = op.get("op")
        fn = getattr(self, f"op_{name}", None)
        if fn is None:
            raise LedgerError(f"Excel files don't have an edit called {name!r}.")
        try:
            return fn(op)
        finally:
            self._edits += 1  # what LibreOffice worked out was for the workbook before this

    def op_set_cells(self, op: dict[str, Any]) -> str:
        ws = self.sheet(op.get("sheet"))
        cells = op.get("cells") or {}
        if not isinstance(cells, dict) or not cells:
            raise LedgerError("set_cells needs cells like {'B5': 120, 'C5': '=B5*2'}.")
        for ref, v in cells.items():
            ws[str(ref).upper()] = v
            _dated(ws, ws[str(ref).upper()])
        return f"Set {len(cells)} cell{'s' if len(cells) != 1 else ''} on {ws.title}"

    def op_append_row(self, op: dict[str, Any]) -> str:
        ws = self.sheet(op.get("sheet"))
        values = list(op.get("values") or [])
        r = _last_row(ws) + 1
        for c, v in enumerate(values, 1):
            _dated(ws, ws.cell(r, c, v))
        return f"Added row {r} on {ws.title}"

    def op_totals_row(self, op: dict[str, Any]) -> str:
        from openpyxl.styles import Font
        from openpyxl.utils import get_column_letter

        ws = self.sheet(op.get("sheet"))
        head = int(op.get("header_row") or _header_row(ws))
        last = _last_row(ws)
        if last <= head:
            raise LedgerError(f"{ws.title} has no rows under its header to total.")
        label_col = 1
        while label_col < ws.max_column and ws.cell(head, label_col).value in (None, ""):
            label_col += 1
        if _is_total_label(ws.cell(last, label_col).value):
            return f"{ws.title} already ends with a total row (row {last})"
        if op.get("columns"):
            cols = [c for c in (_col_index(ws, x, head) for x in op["columns"]) if c]
        else:
            cols = []
            for c in range(1, ws.max_column + 1):
                data = [ws.cell(r, c).value for r in range(head + 1, last + 1)]
                filled = [v for v in data if v not in (None, "")]
                if (
                    c != label_col
                    and filled
                    and sum(map(_is_numberish, filled)) >= 0.8 * len(filled)
                ):
                    cols.append(c)
        if not cols:
            raise LedgerError(f"I couldn't find number columns to total on {ws.title}.")
        r = last + 1
        fn = str(op.get("function") or "SUM").upper()
        if fn not in ("SUM", "AVERAGE", "COUNT", "MAX", "MIN"):
            raise LedgerError("totals_row function should be SUM, AVERAGE, COUNT, MAX or MIN.")
        label = ws.cell(
            r, label_col, str(op.get("label") or ("Total" if fn == "SUM" else fn.title()))
        )
        label.font = Font(bold=True)
        shown = []
        for c in cols:
            L = get_column_letter(c)
            cell = ws.cell(r, c, f"={fn}({L}{head + 1}:{L}{last})")
            cell.font = Font(bold=True)
            cell.number_format = ws.cell(last, c).number_format
            try:  # a figure only when every cell of it is known: never part of a sum
                nums = [
                    v
                    for v in (
                        self.result(ws, x, c, work_out=False) for x in range(head + 1, last + 1)
                    )
                    if _is_number(v)
                ]
            except Unsolved:
                nums = []
            if nums:
                result = {
                    "SUM": sum(nums),
                    "AVERAGE": sum(nums) / len(nums),
                    "COUNT": len(nums),
                    "MAX": max(nums),
                    "MIN": min(nums),
                }[fn]
                head_name = ws.cell(head, c).value or L
                shown.append(f"{head_name} {_shown(result)}")
        tail = f": {', '.join(shown)}" if shown else ""
        return f"Added a {label.value} row (row {r}) on {ws.title}{tail}"

    def op_add_column(self, op: dict[str, Any]) -> str:
        """A new column with a formula on every data row, '{r}' standing for the row number."""
        from openpyxl.styles import Font

        ws = self.sheet(op.get("sheet"))
        head = _header_row(ws)
        last = _last_row(ws)
        formula = str(op.get("formula") or "")
        if "{r}" not in formula:
            raise LedgerError("add_column needs a formula with {r} for the row, like '=B{r}*C{r}'.")
        c = ws.max_column + 1
        ws.cell(head, c, str(op.get("header") or "New")).font = Font(bold=True)
        n = 0
        for r in range(head + 1, last + 1):
            if _is_total_label(ws.cell(r, 1).value):
                continue
            ws.cell(r, c, formula.replace("{r}", str(r)))
            n += 1
        return f"Added column {op.get('header') or 'New'} with a formula on {n} rows"

    def op_pivot(self, op: dict[str, Any]) -> str:
        """A pivot-style summary on its own sheet, with live SUMIFS/COUNTIFS/AVERAGEIFS formulas, so
        it updates when the data does."""
        from openpyxl.styles import Font
        from openpyxl.utils import get_column_letter, quote_sheetname

        src = self.sheet(op.get("sheet"))
        head = _header_row(src)
        last = _last_row(src)
        while last > head and _is_total_label(src.cell(last, 1).value):
            last -= 1
        g = _col_index(src, op.get("group_by") or op.get("rows"), head)
        if g is None:
            raise LedgerError("pivot needs group_by: the column to group by.")
        # One value column or several (a list), the same as in Google Sheets: the menu offers
        # both, and a list used to end "There's no column called ['Units', 'Sales']".
        wanted = op.get("values") if isinstance(op.get("values"), list) else op.get("value")
        wanted = wanted if isinstance(wanted, list) else [wanted]
        vs = [c for c in (_col_index(src, w, head) for w in wanted if w not in (None, "")) if c]
        agg = str(op.get("agg") or ("sum" if vs else "count")).lower()
        if agg not in ("sum", "count", "average"):
            raise LedgerError("pivot agg should be sum, count or average.")
        if agg != "count" and not vs:
            raise LedgerError(f"pivot with {agg} needs value: the column to {agg}.")
        # Groups the way SUMIFS matches them, without regard to capitals: "West" and "west"
        # as two rows each showed the sum of both, and the total counted them twice.
        keys: list[Any] = []  # each group as it's first spelled
        groups: dict[Any, list[list[float]]] = {}
        counts: dict[Any, int] = {}
        known = True  # every value needed for the "top" figures is known
        for r in range(head + 1, last + 1):
            k = self.result(src, r, g)
            if k in (None, ""):
                continue
            fold = k.casefold() if isinstance(k, str) else k
            if fold not in groups:
                keys.append(k)
                groups[fold] = [[] for _ in vs]
                counts[fold] = 0
            counts[fold] += 1
            for i, vc in enumerate(vs):
                try:
                    x = self.result(src, r, vc, work_out=False)
                except Unsolved:
                    known = False
                    continue
                if _is_number(x):
                    groups[fold][i].append(float(x))
        if not keys:
            raise LedgerError(f"There's nothing to group in that column on {src.title}.")
        keys.sort(key=lambda k: (not isinstance(k, str), str(k).lower()))
        title = str(op.get("target") or "Pivot")[:31]
        name, n = title, 2
        while name in self.wb.sheetnames:
            name, n = f"{title} {n}"[:31], n + 1
        ws = self.wb.create_sheet(name)
        g_name = src.cell(head, g).value or get_column_letter(g)
        v_names = [src.cell(head, vc).value or get_column_letter(vc) for vc in vs] or ["rows"]
        ws.append([g_name, *(f"{agg.title()} of {v}" for v in v_names)])
        for c in ws[1]:
            c.font = Font(bold=True)
        sheet = quote_sheetname(src.title)
        GL = get_column_letter(g)
        crit = f"{sheet}!${GL}${head + 1}:${GL}${last}"
        end = len(keys) + 1
        for j, vc in enumerate(vs or [None], start=2):
            L = get_column_letter(vc) if vc else ""
            rng = f"{sheet}!${L}${head + 1}:${L}${last}" if vc else ""
            for i, k in enumerate(keys, start=2):
                ws.cell(i, 1, k)
                if agg == "count":
                    ws.cell(i, j, f"=COUNTIFS({crit},A{i})")
                elif agg == "sum":
                    ws.cell(i, j, f"=SUMIFS({rng},{crit},A{i})")
                else:
                    ws.cell(i, j, f"=AVERAGEIFS({rng},{crit},A{i})")
            J = get_column_letter(j)
            if agg != "average":
                ws.cell(end + 1, j, f"=SUM({J}2:{J}{end})").font = Font(bold=True)
            ws.column_dimensions[J].width = 18
            if vc:
                for i in range(2, end + 2):
                    ws.cell(i, j).number_format = src.cell(head + 1, vc).number_format
        if agg != "average":
            ws.cell(end + 1, 1, "Total").font = Font(bold=True)
        ws.column_dimensions["A"].width = max(12, min(40, max(len(str(k)) for k in keys) + 2))

        def result(k: Any) -> float:
            fold = k.casefold() if isinstance(k, str) else k
            xs = groups[fold][0] if vs else []
            if agg == "count":
                return float(counts[fold])
            if agg == "sum":
                return sum(xs)
            return sum(xs) / len(xs) if xs else 0.0

        said = f"Made a pivot on sheet {name}: {agg} of {' and '.join(str(v) for v in v_names)} by {g_name}, {len(keys)} groups"
        if len(v_names) == 1 and (known or agg == "count"):
            # The biggest groups, when they're known. Formulas nobody has calculated yet used
            # to count as nothing, and the summary read "top: Gadget 0, Widget 0".
            top = sorted(keys, key=result, reverse=True)[:3]
            said += f" (top: {', '.join(f'{k} {_shown(result(k))}' for k in top)})"
        return said

    def op_make_table(self, op: dict[str, Any]) -> str:
        from openpyxl.utils import get_column_letter
        from openpyxl.worksheet.table import Table, TableStyleInfo

        ws = self.sheet(op.get("sheet"))
        head, last = _header_row(ws), _last_row(ws)
        while last > head and _is_total_label(ws.cell(last, 1).value):
            last -= 1
        width = ws.max_column
        while width > 1 and ws.cell(head, width).value in (None, ""):
            width -= 1
        for c in range(1, width + 1):  # Excel needs a text heading on every table column
            h = ws.cell(head, c)
            if h.value in (None, ""):
                h.value = f"Column {c}"
            elif not isinstance(h.value, str):
                h.value = str(h.value)
        ref = str(op.get("range") or f"A{head}:{get_column_letter(width)}{last}")
        name = re.sub(r"\W", "", str(op.get("name") or f"Table{len(ws.tables) + 1}")) or "Table1"
        t = Table(displayName=name, ref=ref)
        t.tableStyleInfo = TableStyleInfo(name="TableStyleMedium2", showRowStripes=True)
        ws.add_table(t)
        return f"Made {ref} on {ws.title} a table"

    def op_chart(self, op: dict[str, Any]) -> str:
        from openpyxl.chart import AreaChart, BarChart, LineChart, PieChart, Reference
        from openpyxl.utils import get_column_letter

        ws = self.sheet(op.get("sheet"))
        head, last = _header_row(ws), _last_row(ws)
        while last > head and _is_total_label(ws.cell(last, 1).value):
            last -= 1
        lc = _col_index(ws, op.get("labels") or "A", head) or 1
        vc = _col_index(ws, op.get("values"), head)
        if vc is None:
            raise LedgerError("chart needs values: the column to plot.")
        kind = str(op.get("type") or "bar").lower()
        chart = {"line": LineChart, "pie": PieChart, "area": AreaChart}.get(kind, BarChart)()
        if kind == "horizontal bar":
            chart.type = "bar"
        chart.title = str(op.get("title") or ws.cell(head, vc).value or "Chart")
        chart.add_data(Reference(ws, min_col=vc, min_row=head, max_row=last), titles_from_data=True)
        chart.set_categories(Reference(ws, min_col=lc, min_row=head + 1, max_row=last))
        at = str(op.get("at") or f"{get_column_letter(ws.max_column + 2)}{head}").upper()
        cell_rc(at)
        ws.add_chart(chart, at)
        return f"Added a {kind} chart of {ws.cell(head, vc).value or 'the values'} on {ws.title}"

    def op_add_sheet(self, op: dict[str, Any]) -> str:
        ws = self.wb.create_sheet(str(op.get("name") or "Sheet")[:31])
        for row in op.get("rows") or []:
            ws.append(list(row))
        return f"Added sheet {ws.title}"

    def save(self) -> Path:
        if getattr(self, "fresh", False):  # built from nothing: saved under its own name
            self.path.parent.mkdir(parents=True, exist_ok=True)
            out = unique_path(self.path.parent, self.path.name)
        else:
            out = unique_path(
                out_dir_for(self.path), f"{self.path.stem} (edited by Pyxi){self.path.suffix}"
            )
        self.wb.save(out)
        return out


def new_workbook(title: str, folder: Path) -> Workbook:
    """An empty workbook that isn't on the disk yet: Ledger fills it from a brief, and it is
    saved as "<title>.xlsx" in ``folder`` (never over a file that is already there)."""
    import shutil
    import tempfile

    import openpyxl

    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", title).strip(" .")[:80] or "Workbook"
    if name.lower().endswith(".xlsx"):
        name = name[:-5].rstrip(" .") or "Workbook"
    tmp = Path(tempfile.mkdtemp(prefix="pyxi_new_"))
    try:  # read into memory like any other workbook; nothing is left behind in Temp
        openpyxl.Workbook().save(tmp / "new.xlsx")
        book = Workbook(tmp / "new.xlsx")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    book.path = folder / f"{name}.xlsx"
    book.fresh = True
    return book


_ALIGN = {
    "left": "START",
    "start": "START",
    "center": "CENTER",
    "centre": "CENTER",
    "right": "END",
    "end": "END",
    "justify": "JUSTIFIED",
    "justified": "JUSTIFIED",
}
_NAMED = {
    "title": "TITLE",
    "subtitle": "SUBTITLE",
    "normal": "NORMAL_TEXT",
    "normal text": "NORMAL_TEXT",
    "body": "NORMAL_TEXT",
}


def _look(op: dict[str, Any]) -> str:
    """'bold and blue', 'italic, 14 pt and highlighted yellow': every change, said plainly."""
    parts = [k for k in ("bold", "italic", "underlined", "struck through") if op.get(k.split()[0].replace("underlined", "underline").replace("struck", "strikethrough"))]
    if op.get("color"):
        parts.append(str(op["color"]) if not str(op["color"]).startswith("#") else "coloured")
    if op.get("size"):
        parts.append(f"{op['size']} pt")
    if op.get("highlight"):
        parts.append(f"highlighted {op['highlight']}" if not str(op["highlight"]).startswith("#") else "highlighted")
    if op.get("font"):
        parts.append(f"in {op['font']}")
    if op.get("link"):
        parts.append("a link")
    if not parts:
        return "the new look"
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


# ============================================================================ Word
def _move_after(new_el, anchor_el) -> None:
    anchor_el.addnext(new_el)


_TOP = object()  # "after": "start": before the first thing in the document


class WordDoc(WordExtras):
    kind = "docx"

    def __init__(self, path: Path) -> None:
        from docx import Document

        self.path = path
        self.doc = Document(str(path))

    @property
    def paras(self):
        return list(self.doc.paragraphs)

    def describe(self, max_chars: int = 9000) -> str:
        out = [
            f"Word document {self.path.name}: {len(self.paras)} paragraphs, {len(self.doc.tables)} tables."
        ]
        size = 0
        for i, p in enumerate(self.paras):
            style = p.style.name if p.style is not None else ""
            line = f"[{i}] ({style}) {p.text}" if p.text.strip() else f"[{i}] (empty)"
            size += len(line)
            if size > max_chars:
                out.append("  ...")
                break
            out.append(line[:400])
        out.extend(self.table_lines())
        return "\n".join(out)

    def _anchor(self, op: dict[str, Any]):
        after = op.get("after")
        if after is None or after == "" or after == "end":
            return None
        if str(after).strip().lower() in ("start", "top", "-1", "before"):
            return _TOP  # the menu offers "start" for Word and Docs alike; Word refused it
        try:
            return self.paras[int(after)]
        except (ValueError, IndexError):
            raise LedgerError(f"There's no paragraph {after}.") from None

    def _place(self, new_el, anchor, chain: list) -> None:
        """Put a new block after the anchor (or after the last block placed in this op), or,
        for the top of the document, before everything that's there."""
        if chain:
            _move_after(new_el, chain[-1])
        elif anchor is _TOP:
            first = next((el for el in self.doc.element.body if el is not new_el), None)
            if first is not None:
                first.addprevious(new_el)
        elif anchor is not None:
            _move_after(new_el, anchor._p)
        chain.append(new_el)

    def _style(self, *names: str) -> str | None:
        have = {s.name for s in self.doc.styles}
        return next((n for n in names if n in have), None)

    def apply(self, op: dict[str, Any]) -> str:
        name = op.get("op")
        fn = getattr(self, f"op_{name}", None)
        if fn is None:
            raise LedgerError(f"Word documents don't have an edit called {name!r}.")
        return fn(op)

    def op_format_text(self, op: dict[str, Any]) -> str:
        from docx.shared import Pt, RGBColor

        runs = self._runs_for(op)
        if not runs:
            raise LedgerError(f"'{str(op.get('find') or '')[:40]}' isn't in the document.")
        for run in runs:
            for key in ("bold", "italic", "underline"):
                if op.get(key) is not None:
                    setattr(run, key, bool(op[key]))
            if op.get("strikethrough") is not None:
                run.font.strike = bool(op["strikethrough"])
            if op.get("size"):
                run.font.size = Pt(float(op["size"]))
            if op.get("font"):
                run.font.name = str(op["font"])
            if op.get("color") and (rgb := _rgb(op["color"])):
                run.font.color.rgb = RGBColor(*(round(v * 255) for v in rgb))
            if op.get("highlight"):
                from docx.enum.text import WD_COLOR_INDEX

                run.font.highlight_color = getattr(
                    WD_COLOR_INDEX, str(op["highlight"]).upper(), WD_COLOR_INDEX.YELLOW
                )
        return f"Formatted {len(runs)} run{'s' if len(runs) != 1 else ''}"

    def _runs_for(self, op: dict[str, Any]) -> list:
        """The runs to format: a whole paragraph ("i"), or exactly the words ("find"), splitting
        a run around them so the rest of the line keeps its look."""
        import copy

        if op.get("i") is not None:
            try:
                return list(self.paras[int(op["i"])].runs)
            except (ValueError, IndexError):
                raise LedgerError(f"There's no paragraph {op['i']}.") from None
        find = str(op.get("find") or "")
        if not find:
            raise LedgerError("format_text needs find (the words) or i (a paragraph number).")
        out = []
        for p in self.paras:
            if find.lower() not in p.text.lower():
                continue
            for run in list(p.runs):
                at = run.text.lower().find(find.lower())
                if at < 0:
                    continue
                before, middle, after = (
                    run.text[:at],
                    run.text[at : at + len(find)],
                    run.text[at + len(find) :],
                )
                run.text = middle
                if before:
                    el = copy.deepcopy(run._r)
                    run._r.addprevious(el)
                    from docx.text.run import Run

                    Run(el, p).text = before
                if after:
                    el = copy.deepcopy(run._r)
                    run._r.addnext(el)
                    from docx.text.run import Run

                    Run(el, p).text = after
                out.append(run)
            if not out:  # spread over several runs: format the whole line
                out.extend(p.runs)
        return out

    def op_align(self, op: dict[str, Any]) -> str:
        from docx.enum.text import WD_ALIGN_PARAGRAPH

        how = {
            "START": WD_ALIGN_PARAGRAPH.LEFT,
            "CENTER": WD_ALIGN_PARAGRAPH.CENTER,
            "END": WD_ALIGN_PARAGRAPH.RIGHT,
            "JUSTIFIED": WD_ALIGN_PARAGRAPH.JUSTIFY,
        }.get(_ALIGN.get(str(op.get("align") or "").lower(), ""))
        if how is None:
            raise LedgerError("align should be left, center, right or justify.")
        try:
            para = self.paras[int(op.get("i", op.get("paragraph")))]
        except (TypeError, ValueError, IndexError):
            raise LedgerError(f"There's no paragraph {op.get('i')}.") from None
        para.alignment = how
        verb = {"CENTER": "Centred", "END": "Right-aligned", "JUSTIFIED": "Justified"}.get(
            _ALIGN.get(str(op.get("align") or "").lower(), ""), "Left-aligned"
        )
        return f"{verb} '{para.text.strip()[:40]}'"

    def op_page_break(self, op: dict[str, Any]) -> str:
        from docx.enum.text import WD_BREAK

        anchor = self._anchor(op)
        p = self.doc.add_paragraph()
        p.add_run().add_break(WD_BREAK.PAGE)
        self._place(p._p, anchor, [])
        return "Added a page break"

    def op_add_heading(self, op: dict[str, Any]) -> str:
        level = max(0, min(int(op.get("level") or 1), 6))
        text = str(op.get("text") or "").strip()
        if not text:
            raise LedgerError("add_heading needs text.")
        anchor = self._anchor(op)
        style = self._style("Title" if level == 0 else f"Heading {level}")
        p = self.doc.add_paragraph(text, style=style) if style else self.doc.add_paragraph()
        if not style:
            p.add_run(text).bold = True
        self._place(p._p, anchor, [])
        return f"Added heading '{text[:60]}'"

    def op_add_paragraph(self, op: dict[str, Any]) -> str:
        text = str(op.get("text") or "")
        anchor = self._anchor(op)
        chain: list = []
        for part in text.split("\n"):
            p = self.doc.add_paragraph(part)
            self._place(p._p, anchor, chain)
        return f"Added a paragraph ({len(text.split())} words)"

    def op_add_bullets(self, op: dict[str, Any]) -> str:
        items = [str(x) for x in op.get("items") or [] if str(x).strip()]
        if not items:
            raise LedgerError("add_bullets needs items.")
        numbered = bool(op.get("numbered"))
        style = self._style("List Number" if numbered else "List Bullet", "List Paragraph")
        anchor = self._anchor(op)
        chain: list = []
        for n, item in enumerate(items, 1):
            if style and style.startswith("List") and style != "List Paragraph":
                p = self.doc.add_paragraph(item, style=style)
            else:
                p = self.doc.add_paragraph(f"{n}. {item}" if numbered else f"• {item}", style=style)
            self._place(p._p, anchor, chain)
        return f"Added {len(items)} {'numbered items' if numbered else 'bullets'}"

    def op_add_table(self, op: dict[str, Any]) -> str:
        rows = [list(r) for r in op.get("rows") or [] if isinstance(r, list | tuple)]
        if not rows and op.get("n_rows") and op.get("n_cols"):
            rows = [[""] * min(int(op["n_cols"]), 20) for _ in range(min(int(op["n_rows"]), 50))]
        if not rows:
            raise LedgerError("add_table needs rows (the first row is the header).")
        cols = max(len(r) for r in rows)
        table = self.doc.add_table(rows=len(rows), cols=cols)
        if style := self._style("Table Grid", "Light Grid Accent 1"):
            table.style = style
        for r, row in enumerate(rows):
            for c in range(cols):
                cell = table.cell(r, c)
                cell.text = str(row[c]) if c < len(row) else ""
                if r == 0:
                    for run in cell.paragraphs[0].runs:
                        run.bold = True
        self._place(table._tbl, self._anchor(op), [])
        return f"Added a {len(rows)}x{cols} table"

    def op_replace_text(self, op: dict[str, Any]) -> str:
        from pyxi.docs import _docx_paragraphs, _set_text

        find, repl = str(op.get("find") or ""), str(op.get("replace") or "")
        if not find:
            raise LedgerError("replace_text needs the text to find.")
        n = 0
        for p in _docx_paragraphs(self.doc):
            if find not in p.text:
                continue
            hit = False
            for run in p.runs:
                if find in run.text:
                    n += run.text.count(find)
                    run.text = run.text.replace(find, repl)
                    hit = True
            if not hit:  # the words span runs: rewrite the paragraph in its first run's style
                n += p.text.count(find)
                _set_text(p, p.text.replace(find, repl))
        if not n:
            raise LedgerError(f"'{find[:40]}' isn't in the document.")
        return f"Replaced '{find[:40]}' {n} time{'s' if n != 1 else ''}"

    def op_set_style(self, op: dict[str, Any]) -> str:
        try:
            p = self.paras[int(op["i"])]
        except (KeyError, ValueError, IndexError):
            raise LedgerError("set_style needs i, a paragraph number from the outline.") from None
        style = self._style(str(op.get("style") or ""))
        if not style:
            raise LedgerError(f"This document has no style called {op.get('style')!r}.")
        p.style = style
        return f"Made paragraph {op['i']} {style}"

    def op_delete_paragraph(self, op: dict[str, Any]) -> str:
        try:
            p = self.paras[int(op["i"])]
        except (KeyError, ValueError, IndexError):
            raise LedgerError(
                "delete_paragraph needs i, a paragraph number from the outline."
            ) from None
        p._p.getparent().remove(p._p)
        return f"Removed paragraph {op['i']}"

    def save(self) -> Path:
        out = unique_path(out_dir_for(self.path), f"{self.path.stem} (edited by Pyxi).docx")
        self.doc.save(str(out))
        return out


# ============================================================================ PowerPoint
class Deck(DeckExtras):
    kind = "pptx"

    def __init__(self, path: Path) -> None:
        from pptx import Presentation

        self.path = path
        self.prs = Presentation(str(path))

    def describe(self) -> str:
        w, h = self._page()
        out = [
            f"PowerPoint {self.path.name}: {len(self.prs.slides)} slides, each {w:.2f} x {h:.2f} "
            "inches. Positions (x, y) and sizes are inches from a slide's top left."
        ]
        for i, slide in enumerate(self.prs.slides, 1):
            out.append(f"Slide {i} ({slide.slide_layout.name}):")
            out.extend(self.shape_lines(slide) or ["  (nothing on it)"])
            if slide.has_notes_slide and slide.notes_slide.notes_text_frame.text.strip():
                out.append(f"  notes: {slide.notes_slide.notes_text_frame.text.strip()[:200]!r}")
        return "\n".join(out)

    def _slide(self, op: dict[str, Any]):
        try:
            n = int(op.get("slide"))
            return self.prs.slides[n - 1]
        except (TypeError, ValueError, IndexError):
            raise LedgerError(
                f"There's no slide {op.get('slide')}; the deck has {len(self.prs.slides)}."
            ) from None

    @staticmethod
    def _body(slide):
        for shape in slide.placeholders:
            if shape.placeholder_format.idx != 0 and shape.has_text_frame:
                return shape
        return None

    def apply(self, op: dict[str, Any]) -> str:
        name = op.get("op")
        fn = getattr(self, f"op_{name}", None)
        if fn is None:
            raise LedgerError(f"PowerPoint files don't have an edit called {name!r}.")
        return fn(op)

    def _fill(self, frame, items: list[str], *, append: bool) -> None:
        start = 0
        if not append:
            frame.text = items[0] if items else ""
            start = 1
        elif len(frame.paragraphs) == 1 and not frame.paragraphs[0].text:
            frame.paragraphs[0].text = items[0]
            start = 1
        for item in items[start:]:
            frame.add_paragraph().text = item

    def op_add_slide(self, op: dict[str, Any]) -> str:
        from pptx.util import Inches, Pt

        layouts = self.prs.slide_layouts
        bullets = [str(b) for b in op.get("bullets") or []]
        layout = self._named_layout(op) or (
            layouts[1]
            if bullets and len(layouts) > 1
            else layouts[min(5, len(layouts) - 1)]
            if not bullets
            else layouts[0]
        )
        slide = self.prs.slides.add_slide(layout)
        if slide.shapes.title is not None:
            slide.shapes.title.text = str(op.get("title") or "")
        if bullets:
            body = self._body(slide)
            if body is None:
                box = slide.shapes.add_textbox(
                    Inches(0.8), Inches(1.6), self.prs.slide_width - Inches(1.6), Inches(4)
                )
                body = box
                for p in body.text_frame.paragraphs:
                    p.font.size = Pt(20)
            self._fill(body.text_frame, bullets, append=False)
        if op.get("after") not in (None, ""):  # move it into place (0 = the front)
            ids = self.prs.slides._sldIdLst
            el = ids[-1]
            ids.remove(el)
            ids.insert(max(0, min(int(op["after"]), len(ids))), el)
        if op.get("notes"):
            slide.notes_slide.notes_text_frame.text = str(op["notes"])
        return f"Added slide '{str(op.get('title') or '')[:50]}'" + (
            f" with {len(bullets)} bullets" if bullets else ""
        )

    def op_set_title(self, op: dict[str, Any]) -> str:
        slide = self._slide(op)
        if slide.shapes.title is None:
            raise LedgerError(f"Slide {op.get('slide')} has no title box.")
        slide.shapes.title.text = str(op.get("text") or "")
        return f"Retitled slide {op.get('slide')}"

    def op_add_bullets(self, op: dict[str, Any]) -> str:
        slide = self._slide(op)
        items = [str(x) for x in op.get("items") or []]
        body = self._body(slide)
        if body is None:
            raise LedgerError(f"Slide {op.get('slide')} has no text box for bullets.")
        self._fill(body.text_frame, items, append=not op.get("replace"))
        return f"Added {len(items)} bullets to slide {op.get('slide')}"

    def op_replace_text(self, op: dict[str, Any]) -> str:
        find, repl = str(op.get("find") or ""), str(op.get("replace") or "")
        if not find:
            raise LedgerError("replace_text needs the text to find.")
        n = 0
        for slide in self.prs.slides:
            for shape in slide.shapes:
                if not shape.has_text_frame:
                    continue
                for p in shape.text_frame.paragraphs:
                    for run in p.runs:
                        if find in run.text:
                            n += run.text.count(find)
                            run.text = run.text.replace(find, repl)
        if not n:
            raise LedgerError(f"'{find[:40]}' isn't in the deck.")
        return f"Replaced '{find[:40]}' {n} time{'s' if n != 1 else ''}"

    def op_delete_slide(self, op: dict[str, Any]) -> str:
        self._slide(op)
        ids = self.prs.slides._sldIdLst
        el = ids[int(op["slide"]) - 1]
        self.prs.part.drop_rel(el.rId)
        ids.remove(el)
        return f"Removed slide {op['slide']}"

    def op_set_notes(self, op: dict[str, Any]) -> str:
        self._slide(op).notes_slide.notes_text_frame.text = str(op.get("text") or "")
        return f"Wrote speaker notes on slide {op.get('slide')}"

    def save(self) -> Path:
        out = unique_path(out_dir_for(self.path), f"{self.path.stem} (edited by Pyxi).pptx")
        self.prs.save(str(out))
        return out


def open_local(path: Path) -> Workbook | WordDoc | Deck:
    ext = path.suffix.lower()
    if not path.is_file():
        raise LedgerError(f"I can't find {path.name} at {path.parent}.")
    try:
        path.open("rb").close()
    except PermissionError:  # open in Excel or Word with a lock
        raise LedgerError(
            f"{path.name} is locked by another app. Close it there and try again."
        ) from None
    if ext in (".xlsx", ".xlsm"):
        return Workbook(path)
    if ext == ".docx":
        return WordDoc(path)
    if ext == ".pptx":
        return Deck(path)
    if ext in (".xls", ".doc", ".ppt"):
        return _open_old(path)
    raise LedgerError(f"Ledger works on Excel, Word and PowerPoint files, not {ext}.")


def _open_old(path: Path) -> Workbook | WordDoc | Deck:
    """An old .doc/.xls/.ppt: LibreOffice turns a copy into the modern format, Ledger edits that,
    and the result is saved beside the original as "<name> (edited by Pyxi).docx" and so on."""
    import shutil
    import subprocess
    import tempfile

    soffice = _soffice()
    new_ext = path.suffix.lower() + "x"
    if soffice is None:
        raise LedgerError(
            f"{path.name} is an old Office file. Pyxi can open it once the free LibreOffice converter "
            "is set up (Settings, under Your files)."
        )
    tmp = Path(tempfile.mkdtemp(prefix="pyxi_old_"))
    try:
        _convert(soffice, path, tmp, new_ext[1:])
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):  # never the command line
        raise LedgerError(
            f"LibreOffice couldn't open {path.name}. If it's open in another app, close it and "
            "try again."
        ) from None
    try:
        made = tmp / f"{path.stem}{new_ext}"
        if not made.is_file():
            raise LedgerError(f"LibreOffice couldn't open {path.name}.")
        target = {".xlsx": Workbook, ".docx": WordDoc, ".pptx": Deck}[new_ext](made)
    finally:  # the copy is read into memory: no folder left behind in Temp for every file
        shutil.rmtree(tmp, ignore_errors=True)
    target.path = path.with_suffix(new_ext)  # saves beside the original, in the new format
    return target


# ============================================================================ Google
class NoSuchTab(LedgerError):
    """An edit named a tab the spreadsheet doesn't have (as far as the last read knows)."""


# The most of one tab that is ever read. A tab with more in it than this is still edited by
# range, but nothing is added "at the end" of it: Ledger can't see where the end is.
_READ_ROWS, _READ_COLS = 20000, 200
# The most of a tab the first read takes. The outline shows 40 rows of it; an edit that works
# from the cells reads its own tab whole, then. (Reading every tab whole, again before each
# such edit, was 4.8 million cells for one job on a 20,000-row tab.)
_OUTLINE_ROWS = 2000
# Edits that work from what's in the cells: their tab is read whole first, and read again
# only if an earlier edit in the job changed it.
_READS = frozenset(
    {"totals_row", "pivot", "chart", "add_sheet", "sort", "filter", "banding", "remove_duplicates"}
)
# Edits that work from the list of tabs and charts.
_NEEDS_TABS = frozenset({"move_chart", "delete_chart", "edit_chart", "delete_sheet", "add_sheet"})
# After one of these the tabs are read again before the next edit, whatever it is: the tab an
# edit means when it names none may have a new name, or be gone.
_RESHAPES = frozenset({"rename_sheet", "delete_sheet", "duplicate_sheet", "raw"})
# Edits that change the list of charts (or a tab's look), and none of the cells.
_NO_CELLS = frozenset(
    {
        "chart",
        "move_chart",
        "delete_chart",
        "edit_chart",
        "freeze",
        "resize",
        "note",
        "filter",
        "banding",
        "conditional_format",
        "dropdown",
        "checkbox",
        "hide_sheet",
        "rename",
    }
)
_GROWS = frozenset({"insert_rows", "insert_columns", "delete_rows", "delete_columns", "append_row"})
_DATEISH = re.compile(
    r"\d\s*[/:]\s*\d|\d{1,4}-\d{1,2}-\d{1,4}|"
    r"(?i:\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b)"
)
_DURATION = re.compile(r"\d+:\d\d(:\d\d)?")  # hours worked, as 7:30 or 7:30:00: a number


def _seconds(text: Any) -> int | None:
    """"7:30" or "7:30:00" as seconds, or None for anything else."""
    s = str(text).strip()
    if not _DURATION.fullmatch(s):
        return None
    h, m, *rest = (int(x) for x in s.split(":"))
    return h * 3600 + m * 60 + (rest[0] if rest else 0)


def _same_heading(top: list[Any], heading: list[str], values: list[str]) -> bool:
    """Is this run of cells (a pivot's top row, as shown) the heading of the pivot being made:
    the grouping column, then one cell per value? In English that is "SUM of Sales" exactly.
    A spreadsheet in another language words it its own way ("SUMME von Sales"), so there the
    value's name at the end of the cell is enough."""
    run = [str(x).strip().lower() for x in top]
    if len(run) != len(heading):
        return False
    if run == heading:
        return True
    if run[0] != heading[0] or any(" of " in cell for cell in run[1:]):
        return False
    return all(cell.endswith(" " + v) for cell, v in zip(run[1:], values, strict=True))


class GoogleSheet(SheetExtras):
    """A Google Sheet, edited through the Sheets API (values with USER_ENTERED, so formulas work)."""

    kind = "gsheet"

    def __init__(self, google: Any, sheet_id: str, gid: int | None = None) -> None:
        self.google = google
        self.id = sheet_id
        self.gid = gid  # the tab open on their screen (from the link): unnamed edits go there
        self.aliases: dict[str, str] = {}  # a tab they asked for that went on the blank sheet
        self.meta: dict[str, Any] = {}
        self.grids: dict[str, list[list[Any]]] = {}  # what was typed: values and formulas
        self.shown: dict[str, list[list[Any]]] = {}  # what shows: a pivot's cells are only here
        self.cut: set[str] = set()  # tabs with more in them than is ever read
        self.wanted: set[str] = set()  # tabs past the first six that an edit named
        self.whole: set[str] = set()  # tabs read to the end of their data, not just for the outline
        self.dirty: set[str] = set()  # tabs an edit has changed since they were read
        self.reshown: set[str] = set()  # tabs where only what shows changed (a pivot went in)
        self.tabs_stale = False  # the list of tabs or charts changed since it was read
        self.sizes_stale = False  # a tab may have more or fewer rows than the list of tabs says
        self.reread = False  # read the tabs again before the next edit (one was renamed, deleted..)
        self.stale = False  # an edit has run since the last full read
        # The pivots this job has written, by the tab they are on: where, and of which columns.
        self.placed: dict[str, list[dict[str, Any]]] = {}

    async def load(self) -> None:
        """The tabs, their charts, and the top of the first six tabs (plus the open one and any
        an edit named), twice over: as typed and as shown. Live, a tab holding only a pivot
        table read as empty (its cells are worked out, not typed), so Ledger was sure the
        pivot wasn't there and made it again, twice."""
        self.meta = await self.google.sheets_meta(self.id)
        props = [s["properties"] for s in self.meta.get("sheets", [])]
        titles = [p["title"] for p in props[:6]]
        titles += [
            p["title"]
            for p in props[6:]
            if p["title"] in self.wanted or (self.gid is not None and p.get("sheetId") == self.gid)
        ]
        self.grids, self.shown, self.cut, self.whole, self.dirty = {}, {}, set(), set(), set()
        self.reshown = set()
        await self._read(titles, _OUTLINE_ROWS)
        self.tabs_stale = self.sizes_stale = self.reread = self.stale = False

    async def _tabs(self) -> None:
        """The list of tabs and charts again (not the cells): what a tab is called, how big it
        is, which charts there are."""
        self.meta = await self.google.sheets_meta(self.id)
        have = {s["properties"]["title"] for s in self.meta.get("sheets", [])}
        for kept in (self.grids, self.shown):
            for title in [t for t in kept if t not in have]:
                del kept[title]
        self.whole &= have
        self.dirty &= have
        self.reshown &= have
        self.cut &= have
        self.tabs_stale = self.sizes_stale = self.reread = False

    def _size(self, title: str) -> dict[str, Any]:
        for s in self.meta.get("sheets", []):
            if s["properties"]["title"] == title:
                return s["properties"].setdefault("gridProperties", {})
        return {}

    async def _read(self, titles: list[str], most: int, typed_too: bool = True) -> None:
        """These tabs' cells, down to row ``most`` at the latest, in one round trip for all of
        them where the connector can. Without ``typed_too``, only what shows is read again
        (after a pivot went in beside the data, nothing that was typed has changed)."""
        if not titles:
            return
        ranges, asked = [], []
        for title in titles:
            size = self._size(title)
            rows = min(int(size.get("rowCount") or most), most)
            cols = min(int(size.get("columnCount") or 26), _READ_COLS + 1)
            ranges.append(f"{a1_tab(title)}!A1:{col_letter(cols)}{rows}")
            asked.append((rows, int(size.get("rowCount") or 0)))
        batch = getattr(self.google, "sheets_values_batch", None)
        if not typed_too:
            shown = (
                await batch(self.id, ranges)
                if batch is not None
                else [await self.google.sheets_values(self.id, r) for r in ranges]
            )
            for title, b in zip(titles, shown, strict=True):
                self.shown[title] = b
                self.reshown.discard(title)
            return
        if batch is not None:
            typed, shown = await asyncio.gather(
                batch(self.id, ranges, formulas=True), batch(self.id, ranges)
            )
        else:  # a connector with no batch read: one tab at a time
            typed = [await self.google.sheets_values(self.id, r, formulas=True) for r in ranges]
            shown = [await self.google.sheets_values(self.id, r) for r in ranges]
        for title, a, b, (rows, all_rows) in zip(titles, typed, shown, asked, strict=True):
            self.grids[title], self.shown[title] = a, b
            self.dirty.discard(title)
            self.reshown.discard(title)
            deep = max(len(a), len(b))
            # Everything there is, when the read wasn't stopped by its own limit.
            if deep < rows or (all_rows and all_rows <= rows):
                self.whole.add(title)
            else:
                self.whole.discard(title)
            wide = max((len(r) for r in [*a, *b]), default=0)
            if deep > _READ_ROWS or wide > _READ_COLS:
                self.cut.add(title)
            else:
                self.cut.discard(title)

    async def _fresh(self, titles: list[str]) -> None:
        """Before an edit that works from the cells: its tabs read to the end of their data,
        unless that was done already and nothing has changed them since."""
        if self.sizes_stale or (self.tabs_stale and any(self._title(t) is None for t in titles)):
            await self._tabs()
        need, light = [], []
        for name in titles:
            title = self._title(name)
            if not title or title in need or title in light:
                continue
            if title not in self.whole or title in self.dirty:
                need.append(title)
            elif title in self.reshown:
                light.append(title)
        if need:
            await self._read(need, _READ_ROWS + 1)
            self.whole.update(need)
            self.wanted.update(need)
        if light:
            await self._read(light, _READ_ROWS + 1, typed_too=False)

    @property
    def title(self) -> str:
        return self.meta.get("properties", {}).get("title", "Sheet")

    @property
    def url(self) -> str:
        return f"https://docs.google.com/spreadsheets/d/{self.id}/edit"

    def _open_tab(self) -> dict[str, Any] | None:
        """The tab open on their screen, when the link said which."""
        if self.gid is None:
            return None
        for s in self.meta.get("sheets", []):
            if s["properties"].get("sheetId") == self.gid:
                return s["properties"]
        return None

    def _default(self) -> str | None:
        """The tab an edit means when it names none."""
        sheets = self.meta.get("sheets", [])
        if not sheets:
            return None
        return (self._open_tab() or sheets[0]["properties"])["title"]

    def _title(self, name: Any) -> str | None:
        """A tab's real title from the name an edit used (any capitals), or None."""
        want = str(name).lower()
        want = self.aliases.get(want, want).lower()
        for s in self.meta.get("sheets", []):
            if s["properties"]["title"].lower() == want:
                return s["properties"]["title"]
        return None

    def _tab(self, name: str | None) -> tuple[str, int, list[list[Any]]]:
        sheets = self.meta.get("sheets", [])
        if not sheets:
            raise LedgerError("That spreadsheet has no sheets.")
        title = self._title(name) if name else self._default()
        for s in sheets:
            p = s["properties"]
            if p["title"] == title:
                return p["title"], p["sheetId"], self.grids.get(p["title"], [])
        raise NoSuchTab(f"There's no sheet called {name!r}.")

    def _seen(self, title: str) -> list[list[Any]]:
        """A tab as a person sees it: what was typed, and where nothing was typed, what shows
        (a pivot table's cells, which no read of formulas returns)."""
        typed, shown = self.grids.get(title, []), self.shown.get(title, [])
        out = []
        for r in range(max(len(typed), len(shown))):
            a = typed[r] if r < len(typed) else []
            b = shown[r] if r < len(shown) else []
            out.append(
                [
                    a[c] if c < len(a) and a[c] not in (None, "") else (b[c] if c < len(b) else "")
                    for c in range(max(len(a), len(b)))
                ]
            )
        return out

    def _typed(self, title: str, r: int, c: int) -> bool:
        """Did someone type this cell (or is it worked out, like a pivot's)?"""
        rows = self.grids.get(title, [])
        return r < len(rows) and c < len(rows[r]) and rows[r][c] not in (None, "")

    def _whole(self, title: str) -> None:
        """Refuse an edit that needs the end of the data on a tab too big to read whole. Live
        risk: the read stopped at row 500, and a total went into row 501, over a real row."""
        if title in self.cut:
            raise LedgerError(
                f"{title} is bigger than I can read in one go ({_READ_ROWS:,} rows by "
                f"{_READ_COLS} columns), so I can't see where its data ends and won't write "
                "there. Give me the exact range, or a smaller tab."
            )

    def describe(self, max_rows: int = 40) -> str:
        from openpyxl.utils import get_column_letter

        names = [s["properties"]["title"] for s in self.meta.get("sheets", [])] or list(self.grids)
        first = f"Google Sheet '{self.title}'. Tabs: {', '.join(names)}."
        if (open_tab := self._open_tab()) and len(names) > 1:
            first += (
                f" The tab open on their screen is {open_tab['title']!r}: an edit that names "
                "no sheet goes there."
            )
        out = [first]
        for title in self.grids:
            rows = self._seen(title)
            if title in self.cut:
                out.append(
                    f"\nTab {title!r}: more than {_READ_ROWS:,} rows or {_READ_COLS} columns. "
                    "Too big to total, pivot, chart or sort; an edit that gives its range works."
                )
            elif title not in self.whole:
                out.append(f"\nTab {title!r}: its first {len(rows):,} rows (it has more).")
            else:
                out.append(f"\nTab {title!r}: {len(rows)} rows.")
            worked_out = any(
                v not in (None, "") and not self._typed(title, r, c)
                for r, row in enumerate(rows)
                for c, v in enumerate(row)
            )
            if worked_out:
                out.append(
                    "  (some cells here were not typed: they are worked out, like a pivot "
                    "table's. They are listed as they show; chart them, don't retype them)"
                )
            out.extend(self.chart_lines(title))
            idx = list(range(len(rows)))
            if len(idx) > max_rows:
                idx = [*idx[: max_rows - 6], -1, *idx[-5:]]
            for r in idx:
                if r == -1:
                    out.append("  ...")
                    continue
                cells = [
                    f"{get_column_letter(c + 1)}{r + 1}={v!r}"[:80]
                    for c, v in enumerate(rows[r])
                    if v not in (None, "")
                ]
                if cells:
                    out.append("  " + " | ".join(cells))
        unread = [n for n in names if n not in self.grids]
        if unread:
            out.append(
                f"\nNot read yet: {', '.join(unread)}. An edit that names one of these in "
                '"sheet" reads it first.'
            )
        return "\n".join(out)

    @staticmethod
    def _header(rows: list[list[Any]]) -> int:
        for i, row in enumerate(rows[:20]):
            if sum(1 for v in row if v not in (None, "")) >= 2:
                return i
        return 0

    def _col(self, rows: list[list[Any]], ref: Any, head: int) -> int | None:
        from openpyxl.utils import column_index_from_string

        if ref in (None, ""):
            return None
        s = str(ref).strip()
        for c, v in enumerate(rows[head] if rows else []):
            if str(v).strip().lower() == s.lower():
                return c
        if re.fullmatch(r"[A-Za-z]{1,3}", s):
            return column_index_from_string(s.upper()) - 1
        raise LedgerError(f"There's no column called {s!r}.")

    def _named(self, op: dict[str, Any]) -> list[str]:
        """The tabs an edit names: in "sheet", "target", or in front of a range ('Q2'!A1:B4)."""
        found = [op.get("sheet"), op.get("target") if op.get("op") == "pivot" else None]
        for key in ("range", "from", "to", "cell"):
            if isinstance(op.get(key), str):
                found.append(split_tab(op[key])[0])
        return [str(n) for n in found if n not in (None, "")]

    def _works_on(self, op: dict[str, Any]) -> list[str]:
        """The tabs an edit reads or writes: the ones it names, or the one meant by none."""
        if op.get("op") == "add_sheet":  # it only looks at a lone tab, to see if it's blank
            return [self._default()] if len(self.meta.get("sheets", [])) == 1 else []
        named = self._named(op)
        if not op.get("sheet") and (not named or op.get("op") == "pivot") and self._default():
            named.append(self._default())
        return [n for n in named if n]

    async def apply(self, op: dict[str, Any]) -> str:
        """One edit. The wider set lives in ledger_google.SheetExtras (``x_<name>``); what
        Ledger started with is below.

        Nothing is read again that doesn't have to be. An edit that works from the cells
        (a total, a pivot, a chart, a sort) reads its own tab first, whole, and only if it
        wasn't read whole already or an earlier edit changed it. It used to be every tab
        after every edit: 17 of one job's 22 round trips. And a read that failed just after
        an edit had landed made that edit look failed, so it was done a second time."""
        name = str(op.get("op") or "")
        if self.reread or (self.tabs_stale and name in _NEEDS_TABS):
            await self._tabs()
        try:
            return await self._run(op)
        except NoSuchTab:
            if not self.tabs_stale:
                raise
            await self._tabs()  # the tab may have been made by an earlier edit in this batch
            return await self._run(op)
        finally:
            self._mark(op)

    async def _run(self, op: dict[str, Any]) -> str:
        if op.get("op") in _READS:
            await self._fresh(self._works_on(op))
        fn = getattr(self, f"x_{op.get('op')}", None)
        return await fn(op) if fn else await self._first_edits(op)

    def _mark(self, op: dict[str, Any]) -> None:
        """What an edit that just ran (or died part-way) leaves to be read again."""
        name = str(op.get("op") or "")
        self.stale = True
        if name in _RESHAPES:
            self.reread = self.tabs_stale = True
            if name != "rename_sheet":  # anything may have changed
                self.dirty.update(self.grids)
            return
        if name in ("chart", "move_chart", "delete_chart", "edit_chart", "hide_sheet", "rename"):
            self.tabs_stale = True
        if name in _GROWS:
            self.sizes_stale = True
        if name in _NO_CELLS or name in ("pivot", "add_sheet"):
            return  # no cells changed, or the edit said which itself
        if name == "find_replace" and not op.get("sheet"):
            self.dirty.update(self.grids)
            return
        for n in self._works_on(op):
            if title := self._title(n):
                self.dirty.add(title)

    def _know_tab(self, title: str, sheet_id: int, grid: dict[str, Any] | None = None) -> None:
        """A tab this job just made, added to the list of tabs without reading it again."""
        size = {"rowCount": 1000, "columnCount": 26, **(grid or {})}
        self.meta.setdefault("sheets", []).append(
            {"properties": {"title": title, "sheetId": sheet_id, "gridProperties": size}}
        )
        self.grids[title], self.shown[title] = [], []
        self.whole.add(title)

    async def _first_edits(self, op: dict[str, Any]) -> str:
        from openpyxl.utils import get_column_letter

        name = op.get("op")
        if name == "set_cells":
            title, _, _ = self._tab(op.get("sheet"))
            cells = op.get("cells") or {}
            data = [{"range": f"{a1_tab(title)}!{ref}", "values": [[v]]} for ref, v in cells.items()]
            await self.google.sheets_write(self.id, data)
            return f"Set {len(data)} cells on {title}"
        if name == "append_row":
            title, _, _ = self._tab(op.get("sheet"))
            await self.google.sheets_append(
                self.id, f"{a1_tab(title)}!A1", [list(op.get("values") or [])]
            )
            return f"Added a row on {title}"
        if name == "totals_row":
            title, sheet_id, rows = self._tab(op.get("sheet"))
            self._whole(title)
            head = self._header(rows)
            last = len(rows)
            if last <= head + 1:
                raise LedgerError(f"{title} has no rows to total.")
            if _is_total_label((rows[-1] or [""])[0]):
                return f"{title} already ends with a total row"
            width = max(len(r) for r in rows)
            shown = self.shown.get(title, [])

            def show(i: int, c: int) -> str:
                return str(shown[i][c]).strip() if i < len(shown) and c < len(shown[i]) else ""

            if op.get("columns"):
                cols = [
                    c for c in (self._col(rows, x, head) for x in op["columns"]) if c is not None
                ]
            else:
                cols = []
                for c in range(1, width):
                    at = [i for i in range(head + 1, last) if c < len(rows[i]) and rows[i][c] not in (None, "")]
                    # Hours worked (7:30) add up like any number, though they read as text.
                    hours = {i for i in at if _seconds(rows[i][c]) is not None or _seconds(show(i, c)) is not None}
                    numbers = sum(
                        1
                        for i in at
                        if i in hours or _num(rows[i][c]) is not None or str(rows[i][c]).startswith("=")
                    )
                    # A date is a number underneath (46298), and a column of them was totalled:
                    # what a cell shows ("10/3/2026") says whether it's one.
                    dates = sum(1 for i in at if i not in hours and _DATEISH.search(show(i, c)))
                    if at and numbers >= 0.8 * len(at) and dates < 0.5 * len(at):
                        cols.append(c)
            row = ["Total"] + [""] * (width - 1)
            for c in cols:
                L = get_column_letter(c + 1)
                row[c] = f"=SUM({L}{head + 2}:{L}{last})"
            size = self._size(title)
            if size.get("rowCount") and last + 1 > int(size["rowCount"]):
                # The data fills the tab to its last row: Google refuses a write below it.
                await self.google.sheets_batch(
                    self.id,
                    [{"appendDimension": {"sheetId": sheet_id, "dimension": "ROWS", "length": 1}}],
                )
                size["rowCount"] = int(size["rowCount"]) + 1
            await self.google.sheets_write(
                self.id, [{"range": f"{a1_tab(title)}!A{last + 1}", "values": [row]}]
            )
            sums = []
            for c in cols:
                heading = rows[head][c] if c < len(rows[head]) else get_column_letter(c + 1)
                cells = [r[c] for r in rows[head + 1 :] if c < len(r) and r[c] not in (None, "")]
                nums = [n for v in cells if (n := _num(v)) is not None]
                spans = [s for i in range(head + 1, last) if (s := _seconds(show(i, c))) is not None]
                if nums:
                    sums.append(f"{heading} {_shown(sum(nums))}")
                elif spans:
                    t = sum(spans)
                    sums.append(f"{heading} {t // 3600}:{t % 3600 // 60:02d}" + (f":{t % 60:02d}" if t % 60 else ""))
            return f"Added a Total row (row {last + 1}) on {title}" + (
                f": {', '.join(sums)}" if sums else ""
            )
        if name == "pivot":
            title, sheet_id, rows = self._tab(op.get("sheet"))
            self._whole(title)
            if not rows:
                raise LedgerError(f"{title} is empty, so there's nothing to pivot yet.")
            head = self._header(rows)
            g = self._col(rows, op.get("group_by") or op.get("rows"), head)
            if g is None:
                raise LedgerError("pivot needs group_by.")
            # One value column, or several ("sum of Units and sum of Sales"): live, two of
            # them had no named edit, the model wrote the request out by hand, the reply ran
            # past its limit and the pivot was never made.
            wanted = op.get("values") if isinstance(op.get("values"), list) else op.get("value")
            wanted = wanted if isinstance(wanted, list) else [wanted]
            cols = [c for c in (self._col(rows, w, head) for w in wanted if w not in (None, "")) if c is not None]
            v = cols[0] if cols else None
            agg = str(op.get("agg") or ("sum" if v is not None else "count")).lower()
            fn = {"sum": "SUM", "count": "COUNTA", "average": "AVERAGE"}.get(agg)
            if fn is None:
                raise LedgerError("pivot agg should be sum, count or average.")
            width = max(len(r) for r in rows)
            last = len(rows)  # a Total row under the data is not one of the groups
            while last > head + 1 and _is_total_label((rows[last - 1] or [""])[0]):
                last -= 1

            def header(c: int) -> str:
                return str(rows[head][c]) if c < len(rows[head]) else get_column_letter(c + 1)

            g_name = header(g)
            v_name = " and ".join(header(c) for c in cols) or "rows"
            # What the pivot's own top row will say ("Region", "SUM of Sales"): how one that is
            # already there is told from anything else.
            names = [header(c).strip().lower() for c in (cols or [g])]
            heading = [g_name.strip().lower()] + [f"{fn} of {n}".lower() for n in names]
            what = f"{agg} of {v_name} by {g_name}"
            verb = "Made a pivot table"

            def holds_it(tab: str) -> bool:
                on_it = self._seen(tab)
                top = on_it[0] if on_it else []
                while top and top[-1] in (None, ""):
                    top = top[:-1]
                return _same_heading(top, heading, names)

            def anything_on(tab: str) -> bool:
                return any(x not in (None, "") for r in self._seen(tab) for x in r)

            # On its own tab, if they asked for one (the data's own tab is "beside the data").
            if op.get("target") and self._title(str(op["target"])[:90]) != title:
                # Live: "chart the pivot on the Pivot tab" made the pivot again on "Pivot 2",
                # then "Pivot 3". Now a tab of that name is used if it's empty. If it holds
                # this very pivot, the pivot is written again in its place, with the rows
                # the data has now (so a row added a moment ago is in it, and Q2's pivot is
                # not mistaken for Q1's). A different pivot on it is left alone: the new one
                # gets a tab named for what it is.
                target = str(op["target"])[:90]
                there = self._title(target)
                if there is not None and anything_on(there) and not holds_it(there):
                    if any(x not in (None, "") for r in self.grids.get(there, []) for x in r):
                        raise LedgerError(
                            f"There's already a tab called {there} with other things on it. "
                            "Name another tab for the pivot, or delete that one first."
                        )
                    base = f"{'Count' if v is None else v_name} by {g_name}"[:90]
                    target, n = base, 2
                    while True:
                        there = self._title(target)
                        if there is None:
                            break
                        await self._fresh([there])
                        if not anything_on(there) or holds_it(there):
                            break
                        target, n = f"{base} {n}", n + 1
                if there is None:
                    added = await self.google.sheets_batch(
                        self.id, [{"addSheet": {"properties": {"title": target}}}]
                    )
                    made = added["replies"][0]["addSheet"]["properties"]
                    new_id, there = made["sheetId"], target
                    self._know_tab(there, new_id, made.get("gridProperties"))
                else:
                    new_id = self._tab(there)[1]
                    if holds_it(there):
                        verb = "Updated the pivot"
                at_row, at_col, where, dest = 0, 0, f"on tab {there}", there
            else:  # beside the data, where they're already looking (a new tab goes unseen)
                shown = self.shown.get(title, [])
                same = next(
                    (
                        b
                        for b in _blocks(shown, g_name, avoid=g)
                        if _same_heading([g_name, *(t for _, t in b[3])], heading, names)
                    ),
                    None,
                )
                if same is not None:  # the same pivot again: written where it is, never beside it
                    at_row, at_col = same[0], same[2]
                    verb = "Updated the pivot"
                else:
                    # Right of everything that shows there: a pivot already beside the data
                    # has no typed cells, and a second one must not land on top of it.
                    at_row, at_col = head, max([width] + [len(r) for r in shown]) + 1
                new_id, dest = sheet_id, title
                where = f"beside the data on {title}, from {get_column_letter(at_col + 1)}{at_row + 1}"
            pivot = {
                "source": {
                    "sheetId": sheet_id,
                    "startRowIndex": head,
                    "endRowIndex": last,
                    "startColumnIndex": 0,
                    "endColumnIndex": width,
                },
                "rows": [{"sourceColumnOffset": g, "showTotals": True, "sortOrder": "ASCENDING"}],
                "values": [
                    {"summarizeFunction": fn, "sourceColumnOffset": c} for c in (cols or [g])
                ],
                "valueLayout": "HORIZONTAL",
            }
            requests: list[dict[str, Any]] = [
                {
                    "updateCells": {
                        "rows": [{"values": [{"pivotTable": pivot}]}],
                        "start": {"sheetId": new_id, "rowIndex": at_row, "columnIndex": at_col},
                        "fields": "pivotTable",
                    }
                }
            ]
            size = self._size(dest)
            short = at_col + len(pivot["values"]) + 1 - int(size.get("columnCount") or 10**6)
            if short > 0:  # the tab ends before the pivot would: Google refuses a write past it
                requests.insert(
                    0,
                    {"appendDimension": {"sheetId": new_id, "dimension": "COLUMNS", "length": short}},
                )
                size["columnCount"] = int(size["columnCount"]) + short
            # A pivot beside the data changes what shows there and nothing that was typed.
            (self.reshown if dest == title else self.dirty).add(dest)
            await self.google.sheets_batch(self.id, requests)
            self.placed.setdefault(dest, []).append(
                {"row": at_row, "col": at_col, "source": title, "group": g, "values": cols or [g]}
            )
            return f"{verb} {where}: {what}"
        if name == "chart":
            title, sheet_id, _ = self._tab(op.get("sheet"))
            self._whole(title)
            rows = self._seen(title)  # a pivot's cells count: they are what's being charted
            if not rows:
                raise LedgerError(f"{title} is empty, so there's nothing to chart yet.")
            head = self._header(rows)
            lc = self._col(rows, op.get("labels") or "A", head) or 0
            vc = self._col(rows, op.get("values"), head)
            if vc is None:
                raise LedgerError("chart needs values: the column to plot.")
            if max(lc, vc) >= len(rows[head]):
                raise LedgerError(
                    f"{title} has no column {get_column_letter(max(lc, vc) + 1)} to chart."
                )
            label_name, value_name = str(rows[head][lc]), str(rows[head][vc])
            last = len(rows)  # one past the last data row (the API's end is exclusive)
            while last > head + 1 and str((rows[last - 1] or [""])[0]).strip().lower() in (
                "total",
                "totals",
                "grand total",
                "sum",
            ):
                last -= 1
            while last > head + 1 and all(c in (None, "") for c in rows[last - 1][: vc + 1]):
                last -= 1
            r0, r1, dc, sc = head, last, lc, vc

            def of_a_pivot(c: int) -> bool:
                return rows[head][c] not in (None, "") and not self._typed(title, head, c)

            labels = [str(r[lc]) for r in rows[head + 1 : last] if len(r) > lc]
            if of_a_pivot(lc) or of_a_pivot(vc):
                # They named a pivot's own cells (the outline lists them: "SUM of Sales", or
                # columns G and H beside the data). Its groups are the bars: not the Grand
                # Total, and not the blank rows under it down to where the data ends.
                c0 = lc if of_a_pivot(lc) else vc
                while not of_a_pivot(lc) and c0 > 0 and of_a_pivot(c0 - 1):
                    c0 -= 1
                r0, r1, dc = head, _block_end(rows, head, c0), c0
                sc = vc if of_a_pivot(vc) and vc > c0 else c0 + 1
                label_name, value_name = str(rows[head][dc]), str(rows[head][sc])
            elif len(set(labels)) < len(labels):
                # A label repeats (North on several rows), so the bars are a pivot's totals: a
                # plain chart draws one bar per row, never per region. The pivot of the column
                # that was asked for: with a pivot of Sales beside the data, a chart of Units
                # used to draw Sales under the title "Units by Region".
                def known() -> set[str]:  # the groups, as the data shows them
                    shown = self.shown.get(title, [])
                    return {str(r[lc]).strip() for r in shown[head + 1 : last] if len(r) > lc}

                def mine() -> tuple[int, int, int, int] | None:
                    # A pivot of these very columns that this job wrote: it is at a cell
                    # Ledger knows, whatever its headings say. They are worded in the
                    # spreadsheet's language, so nothing may hang on the words "SUM of".
                    for p in reversed(self.placed.get(title, [])):
                        if p["source"] == title and p["group"] == lc and vc in p["values"]:
                            end = _block_end(self.shown.get(title, []), p["row"], p["col"], total=True)
                            if end > p["row"] + 1:
                                return p["row"], end, p["col"], p["col"] + 1 + p["values"].index(vc)
                    return None

                def look() -> tuple[int, int, int, int] | None:
                    return _pick(self.shown.get(title, []), label_name, lc, value_name, known()) or mine()

                found = look()
                if found is None:
                    await self._first_edits(
                        {"op": "pivot", "sheet": title, "group_by": label_name, "value": value_name}
                    )
                    await self._fresh([title])
                    rows = self._seen(title)
                    found = look()
                if found is not None:
                    r0, r1, dc, sc = found

            def col(c: int) -> dict[str, Any]:
                return {
                    "sourceRange": {
                        "sources": [
                            {
                                "sheetId": sheet_id,
                                "startRowIndex": r0,
                                "endRowIndex": r1,
                                "startColumnIndex": c,
                                "endColumnIndex": c + 1,
                            }
                        ]
                    }
                }

            kind = str(op.get("type") or "bar").lower()
            chart_title = str(op.get("title") or f"{value_name} by {label_name}")[:100]
            if kind == "pie":
                spec = {
                    "title": chart_title,
                    "pieChart": {
                        "legendPosition": "RIGHT_LEGEND",
                        "domain": col(dc),
                        "series": col(sc),
                    },
                }
            else:
                spec = {
                    "title": chart_title,
                    "basicChart": {
                        "chartType": {
                            "line": "LINE",
                            "area": "AREA",
                            "scatter": "SCATTER",
                            "horizontal bar": "BAR",
                        }.get(kind, "COLUMN"),
                        "legendPosition": "NO_LEGEND",
                        "headerCount": 1,
                        "domains": [{"domain": col(dc)}],
                        "series": [{"series": col(sc), "targetAxis": "LEFT_AXIS"}],
                    },
                }
            # Right of everything shown there (the data and a pivot's cells), so nothing is covered.
            free = max(len(r) for r in rows) + 1
            anchor = {"sheetId": sheet_id, "rowIndex": head, "columnIndex": free}
            if op.get("at"):  # where they asked for it
                at_row, at_col = cell_rc(op["at"])
                anchor = {"sheetId": sheet_id, "rowIndex": at_row - 1, "columnIndex": at_col - 1}
            await self.google.sheets_batch(
                self.id,
                [
                    {
                        "addChart": {
                            "chart": {
                                "spec": spec,
                                "position": {"overlayPosition": {"anchorCell": anchor}},
                            }
                        }
                    }
                ],
            )
            return f"Added a {kind} chart of {value_name} by {label_name} on {title}"
        if name == "add_sheet":
            title = str(op.get("name") or "Sheet")
            sheets = self.meta.get("sheets", [])
            only = sheets[0]["properties"]["title"] if len(sheets) == 1 else None
            if op.get("rows") and only and not any(any(c not in ("", None) for c in r) for r in self._seen(only)):
                # A brand-new, blank spreadsheet open in front of them: the data goes on it, from
                # A1, where they're looking (a new tab next to an empty Sheet1 looked like nothing
                # had happened).
                await self.google.sheets_write(self.id, [{"range": f"{a1_tab(only)}!A1", "values": op["rows"]}])
                # What was just written is what is there: no need to read it back.
                self.grids[only] = [list(r) for r in op["rows"]]
                self.shown[only] = [["" if c is None else str(c) for c in r] for r in op["rows"]]
                self.aliases[title.lower()] = only  # a later edit "on Sample Sales" means here
                return f"Put the data on {only}, from A1"
            self.tabs_stale = True
            added = await self.google.sheets_batch(
                self.id, [{"addSheet": {"properties": {"title": title}}}]
            )
            made = ((added or {}).get("replies") or [{}])[0].get("addSheet", {}).get("properties", {})
            if made.get("sheetId") is not None and self._title(title) is None:
                self._know_tab(title, made["sheetId"], made.get("gridProperties"))
            if op.get("rows"):
                self.dirty.add(title)
                await self.google.sheets_write(
                    self.id, [{"range": f"{a1_tab(title)}!A1", "values": op["rows"]}]
                )
            return f"Added tab {title}"
        raise LedgerError(f"Google Sheets doesn't have an edit called {name!r} here.")


def _block_end(grid: list[list[Any]], r: int, c: int, total: bool = False) -> int:
    """One past the last group row of a pivot whose top-left cell is (r, c): its rows run down
    to the first blank, and its Grand Total row is not a group. ``total``: the pivot is known
    to end with a totals row, whatever that row is called in the spreadsheet's language."""
    end = r + 1
    while end < len(grid) and len(grid[end]) > c and str(grid[end][c]).strip():
        if str(grid[end][c]).strip().lower() in ("grand total", "total"):
            return end
        end += 1
    return end - 1 if total and end > r + 2 else end


def _blocks(grid: list[list[Any]], label: str, avoid: int) -> list[tuple[int, int, int, list[tuple[int, str]]]]:
    """Every pivot shown on a tab that groups by this column: a header cell named like it (not
    that column itself, ``avoid``), heading cells to its right, a row per group under it.
    Each is (header row, one past the last group row, label column, [(column, heading)..])."""
    out = []
    for r, row in enumerate(grid[:60]):
        for c, v in enumerate(row):
            if c == avoid or c + 1 >= len(row) or str(v).strip().lower() != label.strip().lower():
                continue
            end = _block_end(grid, r, c)
            if end <= r + 1:
                continue
            heads = []
            for sc in range(c + 1, len(row)):
                if not str(row[sc]).strip():
                    break
                heads.append((sc, str(row[sc]).strip()))
            if heads:
                out.append((r, end, c, heads))
    return out


def _pick(
    grid: list[list[Any]], label: str, avoid: int, value: str, known: set[str] | None = None
) -> tuple[int, int, int, int] | None:
    """The pivot to chart for "value by label", as (header row, one past the last group row,
    label column, value column), or None when no pivot shown there is of that column.

    A sum of it first ("SUM of Sales"), then any other summary of it (the average they made
    a moment ago is what they mean to chart), then a column that is simply called that (they
    named the pivot's own heading, "SUM of Sales", which the outline shows them). Last, in a
    spreadsheet that isn't in English ("SUMME von Sales"), a heading that ends in its name.
    ``known``: the groups the data has. A last row that isn't one of them is the pivot's
    grand total under another name, and is left off."""
    want = value.strip().lower()
    blocks = _blocks(grid, label, avoid)
    for fits in (
        lambda h, heads: h == f"sum of {want}",
        lambda h, heads: h.endswith(f" of {want}"),
        lambda h, heads: h == want,
        lambda h, heads: h.endswith(f" {want}") and not any(" of " in t.lower() for _, t in heads),
    ):
        for r, end, c, heads in blocks:
            for sc, text in heads:
                if fits(text.lower(), heads):
                    if known and end > r + 2 and str(grid[end - 1][c]).strip() not in known:
                        end -= 1
                    return r, end, c, sc
    return None


def _num(v: Any) -> float | None:
    if _is_number(v):
        return float(v)
    if isinstance(v, str):
        s = re.sub(r"[,$£€\s]", "", v)
        try:
            return float(s) if s and not s.startswith("=") else None
        except ValueError:
            return None
    return None


class GoogleDoc(DocExtras):
    kind = "gdoc"

    def __init__(self, google: Any, doc_id: str) -> None:
        self.google = google
        self.id = doc_id
        self.doc: dict[str, Any] = {}
        self.stale = False  # a change went through and the read after it failed

    async def load(self) -> None:
        self.doc = await self.google.docs_get(self.id)
        self.stale = False

    async def _reread(self) -> None:
        """Read the doc again after a change (its indexes moved). If the read fails the change
        still happened: the edit is not reported as failed (and so not done a second time),
        and the next edit reads first."""
        try:
            await self.load()
        except Exception:
            self.stale = True

    @property
    def title(self) -> str:
        return self.doc.get("title", "Doc")

    @property
    def url(self) -> str:
        return f"https://docs.google.com/document/d/{self.id}/edit"

    def _blocks(self) -> list[dict[str, Any]]:
        return [b for b in self.doc.get("body", {}).get("content", []) if "paragraph" in b]

    def _end(self) -> int:
        content = self.doc.get("body", {}).get("content", [])
        return max(1, int(content[-1].get("endIndex", 2)) - 1) if content else 1

    def describe(self, max_chars: int = 9000) -> str:
        out = [f"Google Doc '{self.title}'."]
        size, i, t_no = 0, 0, 0
        for b in self.doc.get("body", {}).get("content", []):
            if "table" in b:  # where a table sits, between the numbered paragraphs
                t_no += 1
                out.extend(self.table_lines(t_no, b))
                continue
            if "paragraph" in b:
                p = b["paragraph"]
                style = p.get("paragraphStyle", {}).get("namedStyleType", "")
                text = "".join(
                    e.get("textRun", {}).get("content", "") for e in p.get("elements", [])
                ).rstrip("\n")
                line = f"[{i}] ({style}) {text}" if text.strip() else f"[{i}] (empty line)"
                i += 1
            else:
                continue
            size += len(line)
            if size > max_chars:
                out.append("  ...")
                break
            out.append(line[:400])
        starts = [int(b.get("startIndex", 1)) for b in self._blocks()]
        if 0 < len(starts) <= 120:  # for a raw edit, which speaks in indexes
            out.append(
                "Start index of each paragraph (only raw edits need these): "
                + " ".join(f"[{n}]={at}" for n, at in enumerate(starts))
            )
        return "\n".join(out)

    @staticmethod
    def _top(op: dict[str, Any]) -> bool:
        return str(op.get("after")).strip().lower() in ("start", "top", "-1", "before")

    def _at(self, op: dict[str, Any]) -> int:
        after = op.get("after")
        if after in (None, "", "end"):
            return self._end()
        if self._top(op):
            blocks = self._blocks()
            return int(blocks[0]["startIndex"]) if blocks else 1
        try:
            # Just before that paragraph's own newline: always inside a paragraph, even when a
            # table comes next (the start of a table is no place to type, Google says 400).
            return int(self._blocks()[int(after)]["endIndex"]) - 1
        except (ValueError, IndexError, KeyError):
            raise LedgerError(f"There's no paragraph {after}.") from None

    async def _insert(
        self,
        at: int,
        text: str,
        style: str | None = None,
        bullets: str | None = None,
        top: bool = False,
    ) -> None:
        # ``at`` is just before a paragraph's newline. An empty paragraph there (a blank first
        # line, the one line of a new doc) is filled; otherwise the text becomes a new paragraph
        # after it.
        here = next(
            (
                b
                for b in self._blocks()
                if int(b.get("startIndex", 0)) <= at < int(b.get("endIndex", 0))
            ),
            None,
        )
        empty = (
            here is not None
            and not "".join(
                e.get("textRun", {}).get("content", "")
                for e in here["paragraph"].get("elements", [])
            ).strip()
        )
        if empty:
            at = int(here["startIndex"])
        lead = "" if empty or top else "\n"
        body = lead + text + ("\n" if top and not empty else "")  # at the top: a line of its own
        start = at + len(lead)
        end = start + len(text)
        reqs: list[dict[str, Any]] = [{"insertText": {"location": {"index": at}, "text": body}}]
        rng = {"startIndex": start, "endIndex": max(start + 1, end)}
        reqs.append(
            {
                "updateParagraphStyle": {
                    "range": rng,
                    "paragraphStyle": {"namedStyleType": style or "NORMAL_TEXT"},
                    "fields": "namedStyleType",
                }
            }
        )
        if bullets:
            reqs.append({"createParagraphBullets": {"range": rng, "bulletPreset": bullets}})
        await self.google.docs_batch(self.id, reqs)
        await self._reread()  # indexes moved

    async def apply(self, op: dict[str, Any]) -> str:
        """One edit. The wider set lives in ledger_google.DocExtras (``x_<name>``); what Ledger
        started with is below."""
        if self.stale:
            await self.load()
        fn = getattr(self, f"x_{op.get('op')}", None)
        return await fn(op) if fn else await self._first_edits(op)

    async def _first_edits(self, op: dict[str, Any]) -> str:
        name = op.get("op")
        if name == "add_heading":
            level = max(1, min(int(op.get("level") or 1), 6))
            text = str(op.get("text") or "").strip()
            await self._insert(self._at(op), text, f"HEADING_{level}", top=self._top(op))
            return f"Added heading '{text[:60]}'"
        if name == "add_paragraph":
            text = str(op.get("text") or "")
            await self._insert(self._at(op), text, top=self._top(op))
            return "Added a paragraph"
        if name == "add_bullets":
            items = [str(x) for x in op.get("items") or [] if str(x).strip()]
            if not items:
                raise LedgerError("add_bullets needs items.")
            preset = (
                "NUMBERED_DECIMAL_ALPHA_ROMAN"
                if op.get("numbered")
                else "BULLET_DISC_CIRCLE_SQUARE"
            )
            await self._insert(self._at(op), "\n".join(items), bullets=preset, top=self._top(op))
            return f"Added {len(items)} bullets"
        if name == "replace_text":
            find = str(op.get("find") or "")
            if not find:
                raise LedgerError("replace_text needs the text to find.")
            r = await self.google.docs_batch(
                self.id,
                [
                    {
                        "replaceAllText": {
                            "containsText": {"text": find, "matchCase": True},
                            "replaceText": str(op.get("replace") or ""),
                        }
                    }
                ],
            )
            n = (r.get("replies") or [{}])[0].get("replaceAllText", {}).get("occurrencesChanged", 0)
            await self._reread()
            if not n:
                raise LedgerError(f"'{find[:40]}' isn't in the doc.")
            return f"Replaced '{find[:40]}' {n} times"
        if name == "add_table":
            rows = [[str(c) for c in r] for r in op.get("rows") or [] if isinstance(r, list)]
            n_rows = len(rows) or int(op.get("n_rows") or 0)
            n_cols = max((len(r) for r in rows), default=0) or int(op.get("n_cols") or 0)
            if not (1 <= n_rows <= 50 and 1 <= n_cols <= 20):
                raise LedgerError(
                    "add_table needs rows (the cells) or n_rows and n_cols (up to 50 x 20)."
                )
            where: dict[str, Any] = (
                {"location": {"index": self._at(op)}}
                if op.get("after") is not None
                else {"endOfSegmentLocation": {}}
            )
            await self.google.docs_batch(
                self.id, [{"insertTable": {"rows": n_rows, "columns": n_cols, **where}}]
            )
            await self.load()
            if rows:
                tables = [b for b in self.doc.get("body", {}).get("content", []) if "table" in b]
                if op.get("after") is not None:
                    at = self._at(op)
                    tables = [t for t in tables if t.get("startIndex", 0) >= at] or tables
                    table = tables[0]
                else:
                    table = tables[-1]
                fills = []
                for r, tr in enumerate(table["table"].get("tableRows", [])):
                    for c, cell in enumerate(tr.get("tableCells", [])):
                        text = rows[r][c] if r < len(rows) and c < len(rows[r]) else ""
                        if text:
                            fills.append((cell["content"][0]["startIndex"], text))
                # Last cell first: text typed into an earlier cell would move every later index.
                reqs = [
                    {"insertText": {"location": {"index": i}, "text": t}}
                    for i, t in sorted(fills, reverse=True)
                ]
                if reqs:
                    await self.google.docs_batch(self.id, reqs)
                    await self._reread()
            return f"Added a {n_rows} x {n_cols} table"
        if name in ("set_style", "delete_paragraph", "align"):
            i = op.get("i", op.get("paragraph"))
            try:
                block = self._blocks()[int(i)]
            except (TypeError, ValueError, IndexError):
                raise LedgerError(f"There's no paragraph {i}.") from None
            start, end = int(block["startIndex"]), int(block["endIndex"])
            if name == "delete_paragraph":
                last = end >= self._end()
                rng = {
                    "startIndex": start - 1 if last and start > 1 else start,
                    "endIndex": end - 1 if last else end,
                }
                gone = "".join(
                    e.get("textRun", {}).get("content", "")
                    for e in block["paragraph"].get("elements", [])
                ).strip()
                await self.google.docs_batch(self.id, [{"deleteContentRange": {"range": rng}}])
                await self._reread()
                return f"Deleted '{gone[:40]}'" if gone else "Deleted an empty line"
            if name == "align":
                how = _ALIGN.get(str(op.get("align") or "").lower())
                if how is None:
                    raise LedgerError("align should be left, center, right or justify.")
                style, fields = {"alignment": how}, "alignment"
            else:
                want = str(op.get("style") or "").strip().lower()
                m = re.fullmatch(r"heading\s*([1-6])", want)
                named = f"HEADING_{m.group(1)}" if m else _NAMED.get(want)
                if named is None:
                    raise LedgerError("style should be Heading 1-6, Title, Subtitle or Normal.")
                style, fields = {"namedStyleType": named}, "namedStyleType"
            await self.google.docs_batch(
                self.id,
                [
                    {
                        "updateParagraphStyle": {
                            "range": {"startIndex": start, "endIndex": end},
                            "paragraphStyle": style,
                            "fields": fields,
                        }
                    }
                ],
            )
            text = "".join(
                e.get("textRun", {}).get("content", "")
                for e in block["paragraph"].get("elements", [])
            ).strip()
            await self._reread()
            what = f"'{text[:40]}'" if text else f"paragraph {int(i) + 1}"
            if name == "align":
                verb = {"CENTER": "Centred", "END": "Right-aligned", "JUSTIFIED": "Justified"}.get(
                    style["alignment"], "Left-aligned"
                )
                return f"{verb} {what}"
            return f"Made {what} {str(op.get('style') or '').strip() or 'normal text'}"
        if name == "format_text":
            ranges = self._ranges(op)
            if not ranges:
                raise LedgerError(f"'{str(op.get('find') or '')[:40]}' isn't in the doc.")
            style: dict[str, Any] = {}
            for key in ("bold", "italic", "underline", "strikethrough"):
                if op.get(key) is not None:
                    style[key] = bool(op[key])
            if op.get("size"):
                style["fontSize"] = {"magnitude": float(op["size"]), "unit": "PT"}
            if op.get("font"):
                style["weightedFontFamily"] = {"fontFamily": str(op["font"])}
            for key, api in (("color", "foregroundColor"), ("highlight", "backgroundColor")):
                if op.get(key) and (rgb := _rgb(op[key])):
                    style[api] = {
                        "color": {"rgbColor": {"red": rgb[0], "green": rgb[1], "blue": rgb[2]}}
                    }
            if op.get("link"):
                style["link"] = {"url": str(op["link"])}
            if not style:
                raise LedgerError("format_text needs something to change (bold, size, color...).")
            await self.google.docs_batch(
                self.id,
                [
                    {
                        "updateTextStyle": {
                            "range": {"startIndex": a, "endIndex": b},
                            "textStyle": style,
                            "fields": ",".join(style),
                        }
                    }
                    for a, b in ranges
                ],
            )
            await self._reread()
            look = _look(op)
            shown = f"'{str(op.get('find'))[:40]}'" if op.get("find") else "the line"
            return f"Made {shown} {look}" + (f" ({len(ranges)} places)" if len(ranges) > 1 else "")
        if name == "page_break":
            where = (
                {"location": {"index": self._at(op)}}
                if op.get("after") is not None
                else {"endOfSegmentLocation": {}}
            )
            await self.google.docs_batch(self.id, [{"insertPageBreak": where}])
            await self._reread()
            return "Added a page break"
        raise LedgerError(f"Google Docs doesn't have an edit called {name!r} here.")

    def _ranges(self, op: dict[str, Any]) -> list[tuple[int, int]]:
        """Where a format applies: a whole paragraph ("i"), or every place the words appear."""
        blocks = self._blocks()
        if op.get("i") is not None:
            try:
                b = blocks[int(op["i"])]
            except (ValueError, IndexError):
                raise LedgerError(f"There's no paragraph {op['i']}.") from None
            return [(int(b["startIndex"]), max(int(b["startIndex"]) + 1, int(b["endIndex"]) - 1))]
        find = str(op.get("find") or "")
        if not find:
            raise LedgerError("format_text needs find (the words) or i (a paragraph number).")
        return self._find(find)  # tables too


# ============================================================================ the job
_SHEET_EDITS = """Spreadsheets (Excel .xlsx and Google Sheets). "sheet" names a tab and can be left out for the first one; a "range" can name its tab too ('Summary'!A1:B4). Columns are a header name or a letter. Prefer the high-level edits (totals_row, pivot) over hand-written cells, and write formulas, not hard-coded results, so the file stays live:
- {"op": "set_cells", "sheet"?: name, "cells": {"B5": 120, "C5": "=B5*1.13"}}
- {"op": "append_row", "sheet"?: name, "values": [..]}
- {"op": "totals_row", "sheet"?: name, "columns"?: ["B", "Amount"], "function"?: "SUM"|"AVERAGE"|"COUNT"|"MAX"|"MIN", "label"?: "Total"}  (finds the number columns and the last row itself)
- {"op": "pivot", "sheet"?: name, "group_by": column, "value"?: column or [columns], "agg": "sum"|"count"|"average", "target"?: "Pivot"}  (Google Sheets: beside the data, where they can see it; give "target" only when they asked for a new tab; several value columns go in one pivot as a list, never as raw requests. Excel: on a new sheet)
- {"op": "chart", "sheet"?: name, "labels": column, "values": column, "type": "bar"|"horizontal bar"|"line"|"area"|"pie", "title"?: "..", "at"?: "H2"}  (beside the data unless "at" says where; repeated labels are summed)
- {"op": "move_chart", "chart"?: its id or title from the outline, "to": "H2", "width"?: pixels, "height"?: pixels}  (no "chart": the newest one. A chart that's already there is moved, never added again)
- {"op": "delete_chart", "chart"?: ..}
- {"op": "edit_chart", "chart"?: .., "title"?: "..", "type"?: "bar"|"line"|"area"}
- {"op": "format", "range": "A1:D1", "bold"?: true, "italic"?: true, "underline"?: true, "strikethrough"?: true, "size"?: 12, "font"?: "Arial", "color"?: "#1A73E8"|"red", "fill"?: "#FFF2CC"|"yellow", "number_format"?: "$#,##0.00", "align"?: "left"|"center"|"right", "valign"?: "top"|"middle"|"bottom", "wrap"?: true, "border"?: "all"|"outer"}
- {"op": "conditional_format", "range": "C2:C50", "when": "greater than"|"less than"|"between"|"equals"|"not equal"|"contains"|"starts with"|"ends with"|"empty"|"not empty"|"formula"|"color scale", "value"?: 100, "value2"?: 200, "fill"?: "light red", "color"?: "red", "bold"?: true}
- {"op": "sort", "by": column, "descending"?: true, "then_by"?: column, "range"?: "A2:D20"}  (the rows under the header unless a range is given)
- {"op": "filter", "range"?: "A1:D20", "off"?: true}  (filter buttons on the header row)
- {"op": "freeze", "rows"?: 1, "columns"?: 0}
- {"op": "insert_rows", "at": 5, "count"?: 1}  and  {"op": "insert_columns", "at": "C", "count"?: 1}  (blank ones starting there; formulas follow the cells that move)
- {"op": "delete_rows", "from": 5, "to"?: 7}  and  {"op": "delete_columns", "from": "C", "to"?: "D"}
- {"op": "resize", "columns"?: "A:D", "width"?: pixels, "rows"?: "1:5", "height"?: pixels}  (no width or height: fit what's in them)
- {"op": "merge", "range": "A1:D1", "unmerge"?: true}
- {"op": "dropdown", "range": "E2:E50", "options": ["Yes", "No"]}
- {"op": "clear", "range": "A2:D9", "what"?: "values"|"formats"|"all"}
- {"op": "find_replace", "find": "..", "replace": "..", "sheet"?: name, "match_case"?: true, "whole_cell"?: true}
- {"op": "copy", "from": "A1:C5", "to": "E1", "cut"?: true, "values_only"?: true, "format_only"?: true}  ("cut" moves it; "format_only" copies how the cells look and nothing in them, repeated over the whole "to" range: "give B2:D2 the same background, font and colour as A2" is {"op": "copy", "from": "A2", "to": "B2:D2", "format_only": true}. You never need to know a cell's colour to match it)
- {"op": "note", "cell": "B2", "text": ".."}
- {"op": "remove_duplicates", "columns"?: [..]}  and  {"op": "trim"}  (strips stray spaces)
- {"op": "add_sheet", "name": "Summary", "rows"?: [[..], [..]]}  (only when they ask for a new tab; sample or new data for an empty sheet is set_cells on it from A1)
- {"op": "rename_sheet", "sheet": name, "name": ".."}, {"op": "delete_sheet", "sheet": name}, {"op": "duplicate_sheet", "sheet": name, "name"?: ".."}, {"op": "hide_sheet", "sheet": name, "show"?: true}"""

_EXCEL_ONLY = """Excel only:
- {"op": "add_column", "sheet"?: name, "header": "Total", "formula": "=B{r}*C{r}"}  ({r} is each data row)
- {"op": "make_table", "sheet"?: name}"""

_GSHEET_ONLY = """Google Sheets only:
- {"op": "checkbox", "range": "A2:A20"}, {"op": "banding"}  (alternating row colours), {"op": "rename", "title": ".."}  (the file's name)
- {"op": "raw", "requests": [..], "did": "<what it did, in a few plain words>"}  (anything else Sheets can do: Google Sheets API batchUpdate requests, such as addProtectedRange, addNamedRange, textToColumns, updateSheetProperties, addSlicer. Write a range as "A1:D5" and a sheetId as the tab's name; they're converted. Only when no edit above fits)"""

_DOC_EDITS = """Documents (Word .docx and Google Docs). "after" is a paragraph number from the outline; leave it out for the end; "after": "start" puts it at the very top; text placed after an empty line fills that line. Tables are numbered from 1 and their cells are named like a spreadsheet's (A1 is the top left):
- {"op": "add_heading", "text": "..", "level": 1, "after"?: 3}
- {"op": "add_paragraph", "text": "..", "after"?: 3}
- {"op": "add_bullets", "items": [".."], "numbered"?: false, "after"?: 3}
- {"op": "set_text", "i": 4, "text": ".."}  (rewrites one paragraph)
- {"op": "insert_text", "text": "..", "after_text": "words already there"}  (or "before_text")
- {"op": "replace_text", "find": "..", "replace": ".."}  and  {"op": "delete_text", "find": ".."}
- {"op": "delete_paragraph", "i": 4}
- {"op": "set_style", "i": 4, "style": "Heading 2"|"Title"|"Subtitle"|"Normal"}
- {"op": "format_text", "find"?: "exact words" | "i"?: 4, "bold"?: true, "italic"?: true, "underline"?: true, "strikethrough"?: true, "size"?: 14, "color"?: "#1A73E8"|"red", "highlight"?: "yellow", "font"?: "Arial", "link"?: "https://.."}  (find formats every place the words appear; i a whole paragraph)
- {"op": "align", "i": 4, "align": "left"|"center"|"right"|"justify"}
- {"op": "spacing", "i"?: 4, "from"?: 2, "to"?: 5, "line"?: 1.5, "before"?: 6, "after"?: 6, "indent"?: 0.5, "first_line"?: 0.5}  (no paragraph given: the whole document; before and after are points, indents inches)
- {"op": "make_bullets", "from": 3, "to": 6, "numbered"?: true}  and  {"op": "remove_bullets", "from": 3, "to": 6}  (lines already there)
- {"op": "page_break", "after"?: 3}
- {"op": "add_table", "rows": [["Header", ".."], ["..", ".."]], "after"?: 3}  (or "n_rows" and "n_cols" for an empty table of that size)
- {"op": "set_table_cells", "table"?: 1, "cells": {"A1": "Saturday", "B1": "Sunday"}}  (or "row": 1, "values": [..] for one row, or "rows": [[..], [..]] with "start"?: "A1" for a block. This fills a table that's already there, and it grows if it needs more rows)
- {"op": "add_table_row", "table"?: 1, "after"?: 2, "count"?: 1, "values"?: [..]}  (no "after": at the bottom; 0: at the top)
- {"op": "add_table_column", "table"?: 1, "after"?: "B", "values"?: [..]}
- {"op": "delete_table_row", "table"?: 1, "row": 3}, {"op": "delete_table_column", "table"?: 1, "column": "B"}, {"op": "delete_table", "table"?: 1}
- {"op": "format_table", "table"?: 1, "range"?: "A1:E1", "fill"?: "light gray", "bold"?: true, "italic"?: true, "color"?: "red", "size"?: 11, "font"?: "Arial", "align"?: "center"}  (no range: the whole table)
- {"op": "merge_cells", "table"?: 1, "range": "A1:B1"}
- {"op": "add_image", "url": "https://..", "after"?: 3, "width"?: inches}  (Word: "path", a picture file on this PC, in place of "url")
- {"op": "page_setup", "margin"?: 1.0, "margins"?: {"top": 1, "bottom": 1, "left": 1.25, "right": 1.25}, "orientation"?: "landscape"|"portrait", "size"?: "letter"|"a4"|"legal"}  (inches)
- {"op": "header", "text": "..", "align"?: "center"}  and  {"op": "footer", "text": ".."}"""

_GDOC_ONLY = """Google Docs only:
- {"op": "make_bullets", "from": 3, "to": 6, "checklist": true}, {"op": "footnote", "after_text": "words already there", "text": ".."}, {"op": "rename", "title": ".."}  (the file's name)
- format_table also takes "border_color", "border_width" (points) and "column_width" (inches)
- {"op": "raw", "requests": [..], "did": "<what it did, in a few plain words>"}  (anything else Docs can do: Google Docs API batchUpdate requests, such as updateSectionStyle for columns or createNamedRange. They speak in indexes: the outline's last line has each paragraph's start. Requests run in order and each sees the indexes the one before left, so work from the end of the document backwards. Only when no edit above fits)"""

_DECK_EDITS = """Slide decks (PowerPoint .pptx and Google Slides). Slides are numbered from 1. Everything on a slide has an id in the outline. x, y, w and h are inches from the slide's top left; leave them out and a new thing is centred. PowerPoint needs "slide" along with an "id":
- {"op": "add_slide", "title"?: "..", "bullets"?: [".."], "subtitle"?: "..", "layout"?: "title"|"title and body"|"section"|"two columns"|"title only"|"blank", "after"?: 2, "notes"?: ".."}  ("after": 0 puts it first; no "after": last)
- {"op": "set_title", "slide": 2, "text": ".."}
- {"op": "set_text", "slide": 2, "which"?: "title"|"subtitle"|"body", "id"?: .., "text"?: "..", "bullets"?: [".."]}  (what the box says, replacing what was there)
- {"op": "add_bullets", "slide": 2, "items": [".."], "replace"?: false}
- {"op": "add_text_box", "slide": 2, "text": "..", "x"?: 1, "y"?: 1, "w"?: 4, "h"?: 1, "size"?: 18, "bold"?: true, "color"?: "red", "align"?: "center"}
- {"op": "add_shape", "slide": 2, "shape": "rectangle"|"rounded rectangle"|"ellipse"|"triangle"|"diamond"|"arrow"|"star"|"heart"|"cloud", "fill"?: "blue", "text"?: "..", "x"?: 1, "y"?: 1, "w"?: 2, "h"?: 2}
- {"op": "add_image", "slide": 2, "url": "https://..", "x"?: 1, "y"?: 1, "w"?: 4, "h"?: 3}  (PowerPoint: "path", a picture file on this PC, in place of "url")
- {"op": "add_table", "slide": 2, "rows": [["Header", ".."], ["..", ".."]], "x"?: 1, "y"?: 2}  (or "n_rows" and "n_cols")
- {"op": "set_table_cells", "slide": 2, "id"?: .., "cells": {"A1": ".."}}  (a table that's already there)
- {"op": "format_text", "slide"?: 2, "id"?: .., "which"?: "title", "find"?: "exact words", "bold"?: true, "italic"?: true, "underline"?: true, "size"?: 24, "color"?: "#1A73E8", "font"?: "Arial", "link"?: "https://..", "align"?: "center"}  (find: those words wherever they are; otherwise the whole box)
- {"op": "move", "slide": 2, "id": .., "x"?: 1, "y"?: 1, "w"?: 4, "h"?: 3, "center"?: true}  (moves or resizes; "which" works in place of "id")
- {"op": "delete", "slide": 2, "id": ..}  (one thing on a slide)
- {"op": "replace_text", "find": "..", "replace": ".."}
- {"op": "delete_slide", "slide": 3}, {"op": "duplicate_slide", "slide": 3}, {"op": "move_slide", "slide": 3, "to": 1}
- {"op": "background", "slide"?: 2, "color": "#0B1F3A"}  (no slide: every slide)
- {"op": "set_notes", "slide": 2, "text": ".."}"""

_PPTX_ONLY = """PowerPoint only:
- {"op": "add_chart", "slide": 2, "type": "bar"|"horizontal bar"|"line"|"pie"|"area", "categories": ["Q1", "Q2"], "series": {"Sales": [10, 20]}, "title"?: "..", "x"?: 1, "y"?: 1.5, "w"?: 8, "h"?: 4}"""

_GSLIDES_ONLY = """Google Slides only:
- {"op": "background", "slide"?: 2, "image": "https://.."}, {"op": "rename", "title": ".."}  (the file's name)
- {"op": "raw", "requests": [..], "did": "<what it did, in a few plain words>"}  (anything else Slides can do: Google Slides API batchUpdate requests using the ids from the outline, such as createSheetsChart, createLine, createVideo, updateShapeProperties, groupObjects, updatePageElementsZOrder. Only when no edit above fits)"""

_MENUS = (
    ({"xlsx", "gsheet"}, _SHEET_EDITS),
    ({"xlsx"}, _EXCEL_ONLY),
    ({"gsheet"}, _GSHEET_ONLY),
    ({"docx", "gdoc"}, _DOC_EDITS),
    ({"gdoc"}, _GDOC_ONLY),
    ({"pptx", "gslides"}, _DECK_EDITS),
    ({"pptx"}, _PPTX_ONLY),
    ({"gslides"}, _GSLIDES_ONLY),
)
_ALL_KINDS = {"xlsx", "gsheet", "docx", "gdoc", "pptx", "gslides"}


def edits_for(kinds: set[str]) -> str:
    """The edit menu for these kinds of file only: a Doc job isn't sent the spreadsheet edits.
    A document open in Excel, Word or PowerPoint has a menu of its own (ledger_live.py)."""
    menus = _MENUS if kinds <= _ALL_KINDS else (*_MENUS, *_live_menus())
    return (
        'Edits you can use (each is a JSON object with "file": <file number> and "op"):\n'
        + "\n".join(menu for wanted, menu in menus if wanted & kinds)
    )


EDITS = edits_for(_ALL_KINDS)

_SYSTEM = (
    "You are {name}, the documents specialist on the user's team. {persona} You edit spreadsheets, documents "
    "and decks by returning precise edits; a program applies them exactly, in order. You can do whatever a "
    "person could do in these files by hand. Read the outline carefully: use the real sheet names, column "
    "headers, cell addresses, paragraph numbers, table numbers and ids you see. Change what's already there "
    "in place (fill the table that exists, move the chart that exists) and never add a second one. Use as "
    "few edits as the task needs, never invent data, and no em dashes in any text you write.\n\n"
)
_ANSWER = (
    '\n\nReturn JSON only: {"edits": [..], "summary": "<one short sentence for the user>"}. If the task can\'t be '
    'done on these files, return {"edits": [], "summary": "<why, in one sentence>"}.'
)


def system_for(kinds: set[str]) -> str:
    return _SYSTEM + edits_for(kinds or _ALL_KINDS) + _ANSWER


SYSTEM = system_for(_ALL_KINDS)

_URL = re.compile(r"https?://[^\s\"'<>\\)]+", re.I)
_LOADS = re.compile(r"(?i)\b(IMAGE|IMPORT[A-Z]*|WEBSERVICE)\s*\(")


def fetched_from(edit: dict[str, Any], allowed: str) -> str:
    """A web address this edit would make Google load that the user never mentioned, or "".

    A picture placed by address (or an IMAGE or IMPORT formula) is fetched by Google the moment
    it lands, so words planted in a document could have Ledger send that document's contents
    out in the address. Ledger loads only from sites named in the task itself."""
    op = str(edit.get("op") or "")
    texts: list[str] = []
    if op == "raw":
        texts.append(json.dumps(edit.get("requests") or []))
    elif op in ("add_image", "background"):
        texts += [str(edit.get(k) or "") for k in ("url", "image")]

    def formulas(node: Any) -> None:
        if isinstance(node, dict):
            for v in node.values():
                formulas(v)
        elif isinstance(node, list):
            for v in node:
                formulas(v)
        elif isinstance(node, str) and node.startswith("=") and _LOADS.search(node):
            texts.append(node)

    formulas(edit)
    for text in texts:
        for url in _URL.findall(text):
            host = re.sub(r"^https?://(www\.)?", "", url, flags=re.I).split("/")[0].split(":")[0].lower()
            if host and host not in allowed:
                return host
    return ""


@dataclass
class LedgerResult:
    outputs: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    changes: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    summary: str = ""
    cost_usd: float = 0.0
    live: list[str] = field(default_factory=list)  # documents changed in the open app, unsaved
    sent: bool = False  # an edit's calls went to an open app (whether or not it worked)
    declined: bool = False  # they said no on a card that asked before replacing cells
    # A workbook built sheet by sheet (ledger_build.py): what was built, said in full, and
    # the sheets by name. Empty for an ordinary edit.
    headline: str = ""
    built: dict[str, list[str]] = field(default_factory=dict)
    made: list[str] = field(default_factory=list)  # outputs that are new files, not copies


def parse_edits(text: str) -> dict[str, Any]:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


async def _apply(target: Any, op: dict[str, Any]) -> str:
    """One edit on one target. A local file's edit runs in a thread, one at a time and in
    order: working a workbook's formulas out through LibreOffice takes 6 to 14 seconds, and on
    the event loop that froze all of Pyxi for as long."""
    if asyncio.iscoroutinefunction(target.apply):
        return await target.apply(op)
    return await asyncio.to_thread(target.apply, op)


# Local edits that can write something and then fail with a plain LedgerError (a table that
# grew rows before a cell turned out not to exist). After one of these the file is opened
# again; after any other LedgerError nothing was written, so there's nothing to undo. Every
# PowerPoint edit counts: several place a thing on the slide before they check its look.
_WRITES_FIRST = {
    "xlsx": {"resize", "move_chart"},
    "docx": {
        "set_table_cells",
        "add_table_row",
        "add_table_column",
        "add_table",
        "format_table",
        "page_setup",
        "spacing",
    },
}


def _may_have_written(target: Any, op: dict[str, Any], exc: Exception) -> bool:
    kind = getattr(target, "kind", "")
    if not isinstance(exc, LedgerError) or kind == "pptx":
        return True
    return str(op.get("op")) in _WRITES_FIRST.get(kind, set())


def _alike(failed: dict[str, Any], new: dict[str, Any]) -> int:
    """How much a second-pass edit looks like the try again of one that failed: the same kind
    of edit, or one that names the same things (a raw addChart with the failed chart's title)."""
    text = json.dumps(new, default=str).lower()
    op = str(failed.get("op") or "")
    score = 3 if op and op == new.get("op") else 0
    score += 1 if op and op.replace("_", "") in text.replace("_", "") else 0
    for key, value in failed.items():
        if key not in ("op", "file") and isinstance(value, str) and len(value) >= 3:
            score += 1 if value.lower() in text else 0
    return score


def _reopen(path: Any, edits: list[dict[str, Any]]) -> Any:
    # A workbook being built from nothing has no file to open again: it is made again.
    target = path() if callable(path) else open_local(path)
    for e in edits:
        try:
            target.apply(e)
        except Exception:  # it worked a moment ago on the same file; say so and carry on
            log.warning("ledger: %s didn't replay on %s", e.get("op"), target.path.name, exc_info=True)
    return target


def _edit_key(index: int, edit: dict[str, Any]) -> str:
    """An edit as one string to compare by: which file, and every field of it."""
    return json.dumps({**edit, "file": index}, sort_keys=True, default=str)


async def _reopened(path: Any, edits: list[dict[str, Any]]) -> Any:
    """A local file opened afresh, with the edits that worked done again in order. An edit
    that died part-way leaves half its work in memory (a sort that stopped at a merged cell
    had already rewritten three rows): this is the file without that half."""
    return await asyncio.to_thread(_reopen, path, edits)


Ask = Callable[[str, str], Awaitable[tuple[str, float]]]


async def run_ledger(task: str, *, live: list[Any] | None = None, **kw: Any) -> LedgerResult:
    """Open the targets, ask the model for edits once (and once more to fix any that failed),
    apply them, save copies. ``ask(system, prompt)`` returns (text, cost).

    ``live`` holds documents open in Excel, Word or PowerPoint (ledger_live.py): edited where
    they are, never saved, and let go when the job ends, however it ends. When one of them
    can't be reached at the start the job stops with ``Unreachable`` and nothing changed.

    An edit that worked is never applied a second time, whatever the second reply says, and
    how long each part took is logged (live, one chart took 20.9 s and nothing said where).

    A big spreadsheet build doesn't fit in one reply, so it is planned first and written a
    sheet at a time (ledger_build.py); ``new`` starts it in a workbook that doesn't exist
    yet, saved under its own name in ``new_dir``. ``ask_quick``, when given, is the same
    model answering without thinking first: the build uses it for its plan, for the sheets
    of typed-in rows and for its last check. Nothing else uses it."""
    try:
        return await _run_ledger(task, live=live or [], **kw)
    finally:
        for t in live or []:
            await t.close()


async def _run_ledger(
    task: str,
    *,
    files: list[str],
    live: list[Any],
    google: Any = None,
    google_refs: list[str] | None = None,
    ask: Ask,
    name: str = "Ledger",
    persona: str = "",
    context: str = "",
    progress: Callable[[str], Awaitable[None]] | None = None,
    new: list[dict[str, Any]] | None = None,
    new_dir: Path | None = None,
    ask_quick: Ask | None = None,
) -> LedgerResult:
    began = time.perf_counter()
    targets: list[Any] = []
    # Local files by their place in ``targets``: the path, or for a workbook that is being
    # built from nothing, how to make an empty one again.
    sources: dict[int, Any] = {}
    fresh: set[int] = set()
    for f in files:
        sources[len(targets)] = Path(f)
        targets.append(await asyncio.to_thread(open_local, Path(f)))
    for item in new or []:
        # "new xlsx: NEXORA": an empty workbook, filled from the brief and saved under its
        # own name (Documents\Pyxi unless the caller says where).
        if str(item.get("kind") or "xlsx") != "xlsx":
            raise LedgerError("I can start a new Excel workbook (.xlsx), or a new Google file.")
        from pyxi.agents.pixel_common import pyxi_folder

        def make(title: str = str(item.get("title") or "Workbook")) -> Workbook:
            return new_workbook(title, new_dir or pyxi_folder())

        sources[len(targets)] = make
        fresh.add(len(targets))
        targets.append(await asyncio.to_thread(make))
    for ref in google_refs or []:
        if google is None or not google.connected():
            raise LedgerError("Google isn't connected. Connect it in Settings, then ask again.")
        targets.append(await google.open_for_edit(ref))
    targets.extend(live)
    if not targets:
        raise LedgerError(
            "Which file? Give me the path of the Excel, Word or PowerPoint file, or the Google Sheet or Doc."
        )
    for t in targets:
        if hasattr(t, "load"):
            await t.load()
    log.info("ledger: opened and read %d file(s) in %.1fs", len(targets), time.perf_counter() - began)
    outline = "\n\n".join(f"# File {i}\n{t.describe()}" for i, t in enumerate(targets))
    prompt = f"Task: {task}\n\n{outline}"
    if context:
        prompt = f"Context from your teammates:\n{context[:4000]}\n\n{prompt}"
    system = system_for({t.kind for t in targets}).replace("{name}", name).replace("{persona}", persona)
    result = LedgerResult()
    allowed = f"{task}\n{context}".lower()  # the only web addresses an edit may load from
    changed: set[int] = set()
    done: list[tuple[int, dict[str, Any], str]] = []  # edits that worked: file, edit, what it said
    done_keys: set[str] = set()
    half_done: set[int] = set()  # local files an edit died on: reopened before they're used again
    first: list[tuple[int, dict[str, Any], str]] = []  # the first pass's failures: file, edit, why
    tried = False
    pending = prompt
    unread = False  # the model's last reply was not the plan asked for
    gone = False  # an open app stopped answering: nothing more is sent to it, or asked

    async def clean(index: int) -> None:
        if index in half_done:
            targets[index] = await _reopened(
                sources[index], [e for i, e, _ in done if i == index]
            )
            half_done.discard(index)

    async def put(e: dict[str, Any]) -> str:
        """One edit of a workbook that is being built sheet by sheet, onto the one file:
        "" when it is in, else why it isn't. The same care as an edit of the usual pass: it
        loads nothing from a web address the task didn't name, and a local file an edit died
        on is opened afresh before it is used again."""
        try:
            if stray := fetched_from(e, allowed):
                raise LedgerError(
                    f"I only load things from web addresses you gave me, and {stray} isn't one."
                )
            await clean(0)
            said = await _apply(targets[0], e)
        except LedgerError as exc:
            why, wrote = str(exc), _may_have_written(targets[0], e, exc)
        except Exception as exc:
            what = str(e.get("op") or "edit").replace("_", " ")
            why, wrote = f"The {what} didn't work ({str(exc)[:120]}).", True
        else:
            changed.add(0)
            done.append((0, e, said))
            done_keys.add(_edit_key(0, e))
            return ""
        if 0 in sources and wrote:
            half_done.add(0)
        return why

    async def now() -> Any:
        await clean(0)
        return targets[0]

    async def build(least: int | None = None) -> bool:
        """The job as a sheet-by-sheet build (ledger_build.py). False when it isn't one:
        nothing was changed, and it goes the usual way."""
        from pyxi.agents import ledger_build

        builder = ledger_build.Builder(
            task, target=now, put=put, ask=ask, system=system, name=name, context=context,
            progress=progress, least=least, quick=ask_quick,
        )  # fmt: skip
        try:
            built = await builder.run()
        finally:
            result.cost_usd += builder.cost
        if built is None:
            return False
        result.changes = built.changes()
        result.problems = built.problems
        result.headline = built.headline()
        result.built = {
            "done": [s.name for s in built.done],
            "not_done": [s.name for s in built.not_done],
        }
        log.info(
            "ledger: built %d of %d sheets in %.1fs",
            len(built.done), len(built.sheets), time.perf_counter() - began,
        )  # fmt: skip
        return True

    # A big spreadsheet build is planned first and written a sheet at a time: one reply for
    # all of it is cut off at its length limit (live: a 15-sheet workbook, "my plan came out
    # unreadable, twice"). A small edit never takes this road.
    one_sheet_file = len(targets) == 1 and getattr(targets[0], "kind", "") in ("xlsx", "gsheet")
    split = False
    if one_sheet_file:
        from pyxi.agents.ledger_build import looks_big

        split = looks_big(task) and await build()

    for attempt in range(0 if split else 2):
        asked = time.perf_counter()
        try:
            text, cost = await ask(system, pending)
        except Exception:
            if attempt == 0:
                raise
            # The second call is only there to mend what failed. When it can't be made, what
            # the first pass did still stands and is still reported, with what didn't work.
            # (It used to end the whole job as failed, with the first pass's edits in place.)
            log.warning("ledger: the second model call failed; keeping the first pass", exc_info=True)
            break
        log.info("ledger: model call %d took %.1fs", attempt + 1, time.perf_counter() - asked)
        result.cost_usd += cost
        data = parse_edits(text)
        if not data and attempt == 0:
            # Cut off at its limit, or not JSON at all (live: a pivot ended "I couldn't make
            # that change" twice, with nothing said about why). Asked once more, plainly.
            unread = True
            asked = time.perf_counter()
            text, cost = await ask(
                system,
                prompt
                + "\n\nYour last reply could not be read: it was cut off or was not the JSON "
                "object asked for. Reply again with ONLY that JSON object, as short as it can "
                "be: named edits, no raw requests unless nothing else can do it.",
            )
            log.info("ledger: asking again took %.1fs", time.perf_counter() - asked)
            result.cost_usd += cost
            data = parse_edits(text)
        unread = not data
        edits = [e for e in data.get("edits") or [] if isinstance(e, dict)]
        if attempt == 1 and not edits:
            break  # nothing more it can do: keep the first round's problems
        if data.get("summary") and attempt == 0:
            result.summary = str(data["summary"]).strip()
        tried = tried or bool(edits)
        failed: list[str] = []  # for the model: the edit itself and why
        reasons: list[str] = []  # for the user's card: just why
        fails: list[tuple[int, dict[str, Any], str]] = []
        retried: list[tuple[int, dict[str, Any]]] = []  # this pass's edits that were tried
        for e in edits:
            try:
                index = int(e.get("file") or 0)
                target = targets[index]
            except (ValueError, IndexError):
                failed.append(f"{json.dumps(e)[:200]}: there's no file {e.get('file')}")
                reasons.append(f"There's no file {e.get('file')}.")
                fails.append((-1, e, reasons[-1]))
                continue
            key = _edit_key(index, e)
            if key in done_keys and attempt == 1:
                # It worked in the first pass and is in the file. Live, the second reply sent
                # the pivot again along with the chart, and a second pivot tab was made.
                continue
            if progress:
                await progress(f"{e.get('op', 'edit').replace('_', ' ')}")
            began_edit = time.perf_counter()
            retried.append((index, e))
            why, wrote = "", False
            try:
                if stray := fetched_from(e, allowed):
                    raise LedgerError(
                        f"I only load things from web addresses you gave me, and {stray} isn't one."
                    )
                if index in sources:
                    await clean(index)
                    target = targets[index]
                said = await _apply(target, e)
                result.changes.append(said)
                changed.add(index)
                done.append((index, e, said))
                done_keys.add(key)
            except LedgerError as exc:
                failed.append(f"{json.dumps(e)[:200]}: {exc}")
                why, wrote = str(exc), _may_have_written(target, e, exc)
                gone = gone or isinstance(exc, Unreachable)
            except Exception as exc:  # a bad cell address, a style the file lacks...
                failed.append(f"{json.dumps(e)[:200]}: {type(exc).__name__}: {str(exc)[:160]}")
                what = str(e.get("op") or "edit").replace("_", " ")
                why, wrote = f"The {what} didn't work ({str(exc)[:120]}).", True
            if why:
                reasons.append(why)
                fails.append((index, e, why))
                # Only an edit that may have got part-way has anything to undo. An edit that
                # was refused before it wrote ("no column called Nope") used to cost a second
                # opening of the whole file all the same: 4 seconds more on a 20,000-row one.
                if index in sources and wrote:
                    half_done.add(index)
            log.info(
                "ledger: %s took %.1fs%s",
                e.get("op"),
                time.perf_counter() - began_edit,
                " (it failed)" if why else "",
            )
            if gone:
                break  # the edits after it would each wait on an app that isn't answering
        if attempt == 0:
            first = fails
            result.problems = reasons
        else:
            # A problem from the first pass is dropped only when this pass tried something in
            # its place: the edit most like the one that failed, not whichever failed first.
            # One it never came back to is still reported: it used to vanish whenever a second
            # reply arrived, even one that mended nothing.
            left = list(first)
            for index, e in retried:
                on_file = [f for f in left if f[0] == index]
                scored = sorted(on_file, key=lambda f: -_alike(f[1], e))
                if scored and (_alike(scored[0][1], e) > 0 or len(on_file) == 1):
                    left.remove(scored[0])
            result.problems = list(dict.fromkeys([*(why for _, _, why in left), *reasons]))
        if not failed or gone:
            break
        if attempt == 0:
            try:  # the outline for the second try is read fresh, never remembered
                for i, t in enumerate(targets):
                    if i in sources:
                        await clean(i)
                    elif hasattr(t, "load") and getattr(t, "stale", True):
                        await t.load()
            except Exception:
                log.warning("ledger: couldn't read the files again for a second try", exc_info=True)
                break
            outline = "\n\n".join(f"# File {i}\n{t.describe()}" for i, t in enumerate(targets))
            already = "\n".join(
                f"- {json.dumps({**e, 'file': i})[:200]} ({said})" for i, e, said in done
            )
            pending = (
                f"Task: {task}\n\nThese edits failed:\n"
                + "\n".join(failed)
                + (
                    "\n\nThese edits already worked and are in the files now. Do not send them "
                    f"again:\n{already}"
                    if already
                    else ""
                )
                + f"\n\nThe files now (with the edits that worked):\n{outline}\n\nReturn only the edits still needed, or none if one can't be done."
            )
    if one_sheet_file and not split and unread and not tried and not gone:
        # The plan was cut off twice and nothing was touched: it was a bigger job than its
        # words let on. It is written in parts instead, however few sheets it has.
        split = await build(least=1)
        unread = not split
    if split and not result.changes:
        changed.clear()  # no sheet was written: a file of empty tabs is not worth saving
    saving = time.perf_counter()
    for i in sorted(changed):
        if i in sources:
            await clean(i)
        t = targets[i]
        if getattr(t, "live", False):
            result.live.append(t.title)  # changed where it's open; saving is theirs to do
        elif hasattr(t, "save"):
            result.outputs.append(str(await asyncio.to_thread(t.save)))
            if i in fresh:
                result.made.append(result.outputs[-1])
        else:
            result.links.append(t.url)
    result.sent = any(getattr(t, "sent", False) for t in live)
    result.declined = any(getattr(t, "declined", False) for t in live)
    log.info(
        "ledger: saved in %.1fs; %.1fs in all, %d edit(s) made, %d problem(s)",
        time.perf_counter() - saving,
        time.perf_counter() - began,
        len(result.changes),
        len(result.problems),
    )
    if not result.changes and (tried or not result.summary):
        # The model's summary was written before its edits ran ("Added a total row"): when
        # none of them worked, say why instead.
        why = f" {result.problems[0]}" if result.problems else ""
        if not why and unread and not tried:
            why = (
                " I couldn't work out the edits for it: my plan came out unreadable, twice. "
                "Nothing in the file was touched. Ask again, or for one part at a time."
            )
        result.summary = f"I couldn't make that change.{why}"
    return result
