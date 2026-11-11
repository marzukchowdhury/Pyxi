"""The rest of what a person does in Excel, Word and PowerPoint files, for Ledger.

``ledger.py`` holds the edits Ledger started with. This adds the wider set (sorting, filters,
conditional formats, table cells, pictures, page setup, text boxes, shapes, charts on a
slide...), under the same edit names as their Google counterparts in ``ledger_google.py`` so
one menu serves both. Everything is done in the file itself: no Office, no window.
"""

from __future__ import annotations

import re
from copy import copy, deepcopy
from pathlib import Path
from typing import Any

from pyxi.agents.ledger_common import (
    LedgerError,
    a1_bounds,
    col_letter,
    col_number,
    hex_color,
    split_tab,
    table_cells,
)

_IMAGES = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff"}


def _n(value: Any, what: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        raise LedgerError(f"{what} should be a number, not {value!r}.") from None


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _hex(color: Any, what: str = "color") -> str:
    h = hex_color(color)
    if h is None:
        raise LedgerError(
            f"I can't read the {what} {color!r}: use a name like red or a code like #1A73E8."
        )
    return h


def _picture(path: Any) -> Path:
    p = Path(str(path or "").strip().strip('"')).expanduser()
    if not p.is_file():
        raise LedgerError(f"I can't find the picture {p.name or path!r}.")
    if p.suffix.lower() not in _IMAGES:
        raise LedgerError(f"{p.name} isn't a picture I can place (png, jpg, gif, bmp or tif).")
    return p


# ============================================================================ Excel
_OPERATORS = {
    "greater than": "greaterThan",
    "more than": "greaterThan",
    "above": "greaterThan",
    "greater or equal": "greaterThanOrEqual",
    "at least": "greaterThanOrEqual",
    "less than": "lessThan",
    "below": "lessThan",
    "less or equal": "lessThanOrEqual",
    "at most": "lessThanOrEqual",
    "between": "between",
    "equals": "equal",
    "equal to": "equal",
    "is": "equal",
    "not equal": "notEqual",
}
_CELL = re.compile(r"(\$?)([A-Z]{1,3})(\$?)(\d+)")
_COLS = re.compile(r"(\$?)([A-Z]{1,3})")
_ROWS = re.compile(r"(\$?)(\d+)")


_GONE = "#REF!"


def _shift_ref(ref: str, axis: str, at: int, delta: int) -> str:
    """One reference (A1, $B$2, A1:C9, A:A, 3:7) after rows or columns moved by ``delta``
    from ``at`` on.

    When rows were deleted (``delta`` below 0) the ones that went are ``at + delta`` to
    ``at - 1``. A range that ended among them now ends just before them: a total under the
    last data row used to keep its old end and add itself in. A cell that was one of them is
    #REF!, as in Excel."""
    gone = range(at + delta, at) if delta < 0 else range(0)

    def split(text: str):
        if m := _CELL.fullmatch(text):
            return m.group(1), col_number(m.group(2)), m.group(3), int(m.group(4))
        if axis == "col" and (m := _COLS.fullmatch(text)):
            return m.group(1), col_number(m.group(2)), "", None
        if axis == "row" and (m := _ROWS.fullmatch(text)):
            return "", None, m.group(1), int(m.group(2))
        return None

    parts = [split(part) for part in ref.split(":")]
    if len(parts) > 2 or any(part is None for part in parts):
        return ref
    out, places = [], []
    for i, (c_abs, col, r_abs, row) in enumerate(parts):
        n = row if axis == "row" else col
        if n is not None:
            if n in gone:
                if len(parts) == 1:
                    return _GONE
                n = gone.start - 1 if i == 1 else gone.start
            elif n >= at:
                n += delta
            if n < 1:
                return _GONE
            places.append(n)
            if axis == "row":
                row = n
            else:
                col = n
        out.append(f"{c_abs}{col_letter(col) if col else ''}{r_abs}{row if row else ''}")
    if len(places) == 2 and places[1] < places[0]:
        return _GONE  # every row of the range went
    return ":".join(out)


def _shift_text(formula: str, owner: str, moved: str, axis: str, at: int, delta: int) -> str:
    """A formula (with its =) written on sheet ``owner``, after rows or columns moved on sheet
    ``moved``: the references that point there now point at where the cells went."""
    from openpyxl.formula.tokenizer import Token, Tokenizer

    try:
        tokens = Tokenizer(formula).items
    except Exception:
        return formula
    changed = False
    for t in tokens:
        if t.type != Token.OPERAND or t.subtype != Token.RANGE:
            continue
        prefix, bang, ref = t.value.rpartition("!")
        if (prefix.strip("'").replace("''", "'") if bang else owner) != moved:
            continue
        new = _shift_ref(ref, axis, at, delta)
        if new != ref:
            t.value = f"{prefix}{bang}{new}"
            changed = True
    return "=" + "".join(t.value for t in tokens) if changed else formula


def _reads(
    formula: Any, owner: str, moved: str, axis: str, lo: int, hi: int, own: int | None = None
) -> bool:
    """Does this formula (written on sheet ``owner``) read rows or columns ``lo`` to ``hi`` of
    sheet ``moved``? Then what it came to before those were deleted or reordered may no
    longer hold. A reference to just ``own`` (the formula's own row, which moves with it)
    doesn't count, and neither does a range that takes in all of ``lo`` to ``hi`` when rows
    were only reordered (a total is the same whatever the order)."""
    from openpyxl.formula.tokenizer import Token, Tokenizer

    try:
        tokens = Tokenizer(str(formula)).items
    except Exception:
        return True
    for t in tokens:
        if t.type != Token.OPERAND or t.subtype != Token.RANGE:
            continue
        prefix, bang, ref = t.value.rpartition("!")
        if (prefix.strip("'").replace("''", "'") if bang else owner) != moved:
            continue
        places = []
        for part in ref.split(":"):
            if m := _CELL.fullmatch(part):
                places.append(int(m.group(4)) if axis == "row" else col_number(m.group(2)))
            elif axis == "col" and (m := _COLS.fullmatch(part)):
                places.append(col_number(m.group(2)))
            elif axis == "row" and (m := _ROWS.fullmatch(part)):
                places.append(int(m.group(2)))
            else:  # a whole column, a name, a table: it may well
                return True
        first, last = min(places), max(places)
        if last < lo or first > hi or (own is not None and first == last == own):
            continue
        if own is not None and first <= lo and last >= hi:
            continue
        return True
    return False


def shift_formulas(wb, ws, axis: str, at: int, delta: int) -> None:
    """After rows or columns were inserted or deleted on ``ws``: every formula in the workbook
    that points at cells which moved now points at where they went. (openpyxl moves the cells
    but leaves the formulas as they were, so a total would quietly add up the wrong rows.)"""
    for sheet in wb.worksheets:
        for row in sheet.iter_rows():
            for cell in row:
                v = cell.value
                if isinstance(v, str) and v.startswith("="):
                    new = _shift_text(v, sheet.title, ws.title, axis, at, delta)
                    if new != v:
                        cell.value = new


def shift_ranges(wb, ws, axis: str, at: int, delta: int) -> None:
    """The rest of what names cells follows the rows or columns that moved on ``ws``:
    conditional formats, dropdowns, the filter, tables, and what every chart draws. openpyxl
    leaves all of these where they were: after one row was inserted at the top, a chart read
    the header row as its first value and a colour rule sat one row too high."""
    from openpyxl.formatting.formatting import ConditionalFormattingList

    def text(f: Any, owner: str) -> Any:  # a formula kept without its "=" (rules, charts)
        if not isinstance(f, str) or not f:
            return f
        lead = f.startswith("=")
        new = _shift_text(f if lead else "=" + f, owner, ws.title, axis, at, delta)
        return new if lead else new[1:]

    def spans(sqref: Any) -> str:  # "A1:A5 C1:C5", without the ranges that went altogether
        moved = (_shift_ref(part, axis, at, delta) for part in str(sqref).split())
        return " ".join(m for m in moved if m != _GONE)

    for sheet in wb.worksheets:
        here = sheet is ws
        rules = ConditionalFormattingList()
        for cf in sheet.conditional_formatting:
            where = spans(cf.sqref) if here else str(cf.sqref)
            for rule in cf.rules:
                if rule.formula:
                    rule.formula = [text(f, sheet.title) for f in rule.formula]
                if where:
                    rules.add(where, rule)
        sheet.conditional_formatting = rules
        validations = sheet.data_validations.dataValidation
        for dv in list(validations):
            if here:
                where = spans(dv.sqref)
                if not where:
                    validations.remove(dv)
                    continue
                dv.sqref = where
            dv.formula1, dv.formula2 = text(dv.formula1, sheet.title), text(dv.formula2, sheet.title)
        for chart in getattr(sheet, "_charts", []):
            for series in getattr(chart, "series", None) or []:
                for part in ("val", "cat", "tx", "xVal", "yVal", "zVal", "bubbleSize"):
                    for kind in ("numRef", "strRef", "multiLvlStrRef"):
                        source = getattr(getattr(series, part, None), kind, None)
                        if source is not None and getattr(source, "f", None):
                            source.f = text(source.f, sheet.title)
            if not here:
                continue
            anchor = getattr(chart, "anchor", None)
            if isinstance(anchor, str):  # the chart itself moves down with its rows
                new = _shift_ref(anchor, axis, at, delta)
                chart.anchor = anchor if new == _GONE else new
                continue
            for mark in (getattr(anchor, "_from", None), getattr(anchor, "to", None)):
                if mark is None:
                    continue
                if axis == "row" and mark.row + 1 >= at:
                    mark.row = max(0, mark.row + delta)
                if axis == "col" and mark.col + 1 >= at:
                    mark.col = max(0, mark.col + delta)
    if ws.auto_filter.ref:
        new = _shift_ref(ws.auto_filter.ref, axis, at, delta)
        ws.auto_filter.ref = None if new == _GONE else new
    for table in list(ws.tables.values()):
        new = _shift_ref(table.ref, axis, at, delta)
        if new == _GONE:
            del ws.tables[table.name]
            continue
        if new == table.ref:
            continue
        table.ref = new
        if table.autoFilter is not None and table.autoFilter.ref:
            inner = _shift_ref(table.autoFilter.ref, axis, at, delta)
            table.autoFilter.ref = new if inner == _GONE else inner
        if axis == "col":
            # Its columns are named again from the header cells when the file is saved, and
            # Excel refuses a table with a blank heading: a new column inside it gets one.
            table.tableColumns = []
            c1, r1, c2, _ = a1_bounds(new)
            for c in range(c1 or 1, (c2 or c1 or 1) + 1):
                head = ws.cell(r1 or 1, c)
                if head.value in (None, ""):
                    head.value = f"Column{c - (c1 or 1) + 1}"


class WorkbookExtras:
    """Mixed into ``ledger.Workbook``."""

    # ---------------------------------------------------------------- where
    def _range(self, op: dict[str, Any], key: str = "range"):
        """(worksheet, 'A1:D5') for an edit's range, which may name its sheet (Summary!A1:B4)."""
        name, raw = split_tab(op.get(key))
        sheet = name if name is not None else op.get("sheet")
        if not raw:
            raise LedgerError(f"{op.get('op')} needs a range like A1:D10.")
        ws = self.sheet(sheet)
        c1, r1, c2, r2 = a1_bounds(raw)
        ref = (
            f"{col_letter(c1 or 1)}{r1 or 1}:"
            f"{col_letter(c2 or max(ws.max_column, 1))}{r2 or max(ws.max_row, 1)}"
        )
        return ws, ref

    @staticmethod
    def _cells(ws, ref: str):
        for row in ws[ref]:
            yield from row

    def _block(self, ws) -> tuple[int, int, int]:
        """(header row, last data row, width) of the table on a sheet; a total row is left out."""
        from pyxi.agents import ledger as base

        head, last = base._header_row(ws), base._last_row(ws)
        while last > head and base._is_total_label(ws.cell(last, 1).value):
            last -= 1
        width = ws.max_column
        while width > 1 and ws.cell(head, width).value in (None, ""):
            width -= 1
        return head, last, width

    def _column(self, ws, ref: Any, head: int) -> int:
        from pyxi.agents import ledger as base

        col = base._col_index(ws, ref, head)
        if col is None:
            raise LedgerError(f"{ref!r} isn't a column on {ws.title}.")
        return col

    @staticmethod
    def _index(v: Any, columns: bool) -> int:
        s = str(v).strip()
        if s.isdigit():
            return int(s)
        if columns and re.fullmatch(r"[A-Za-z]{1,3}", s):
            return col_number(s)
        raise LedgerError(f"{v!r} isn't a {'column' if columns else 'row'}.")

    def _span(self, op: dict[str, Any], key: str, columns: bool) -> tuple[int, int]:
        raw = op.get(key)
        if raw in (None, ""):
            raise LedgerError(f"{op.get('op')} needs {key}.")
        if isinstance(raw, str) and ":" in raw:
            a, b = raw.split(":", 1)
            start, end = self._index(a, columns), self._index(b, columns)
        else:
            start = self._index(raw, columns)
            end = self._index(op["to"], columns) if op.get("to") not in (None, "") else start
            if op.get("count"):
                end = start + int(op["count"]) - 1
        return min(start, end), max(start, end)

    @staticmethod
    def _chart_title(chart) -> str:
        title = getattr(chart, "title", None)
        if title is None or isinstance(title, str):
            return title or ""
        try:
            return "".join(
                r.t for p in title.tx.rich.p for r in (p.r or []) if getattr(r, "t", None)
            )
        except Exception:
            return ""

    @staticmethod
    def _chart_at(chart) -> str:
        anchor = getattr(chart, "anchor", None)
        if isinstance(anchor, str):
            return anchor
        marker = getattr(anchor, "_from", None)
        if marker is None:
            return "?"
        return f"{col_letter(marker.col + 1)}{marker.row + 1}"

    def chart_lines(self, ws) -> list[str]:
        return [
            f"  Chart {i} {self._chart_title(c)!r}, its top left corner at {self._chart_at(c)}"
            for i, c in enumerate(getattr(ws, "_charts", []), 1)
        ]

    def _chart(self, op: dict[str, Any]):
        sheets = [self.sheet(op["sheet"])] if op.get("sheet") else self.wb.worksheets
        charts = [(ws, c) for ws in sheets for c in getattr(ws, "_charts", [])]
        if not charts:
            raise LedgerError("There's no chart in this workbook yet.")
        want = op.get("chart")
        if want in (None, ""):
            return charts[-1]
        if str(want).isdigit() and 1 <= int(want) <= len(charts):
            return charts[int(want) - 1]
        for ws, c in charts:
            if str(want).lower() in self._chart_title(c).lower():
                return ws, c
        raise LedgerError(f"There's no chart {want!r}.")

    # ---------------------------------------------------------------- charts
    def op_move_chart(self, op: dict[str, Any]) -> str:
        ws, chart = self._chart(op)
        to = str(op.get("to") or "").upper()
        if op.get("width"):
            chart.width = _n(op["width"], "width") / 37.8  # pixels to cm
        if op.get("height"):
            chart.height = _n(op["height"], "height") / 37.8
        if to:
            a1_bounds(to)
            chart.anchor = to
        elif not (op.get("width") or op.get("height")):
            raise LedgerError('move_chart needs "to" (a cell like H2) or a width and height.')
        return f"Moved the chart {self._chart_title(chart)!r}" + (
            f" to {to} on {ws.title}" if to else ""
        )

    def op_delete_chart(self, op: dict[str, Any]) -> str:
        ws, chart = self._chart(op)
        ws._charts.remove(chart)
        return f"Removed the chart {self._chart_title(chart)!r}"

    def op_edit_chart(self, op: dict[str, Any]) -> str:
        _, chart = self._chart(op)
        if not op.get("title"):
            raise LedgerError(
                "In an Excel file a chart's title can change; for another type, delete it and add a new chart."
            )
        chart.title = str(op["title"])[:100]
        return f"Titled the chart {chart.title!r}"

    # ---------------------------------------------------------------- rows and columns
    def op_sort(self, op: dict[str, Any]) -> str:
        from openpyxl.formula.translate import Translator

        ws = self.sheet(op.get("sheet"))
        head, last, width = self._block(ws)
        if last <= head + 1:
            return f"{ws.title} has nothing to sort"
        keys = []
        for key, flag in (("by", "descending"), ("then_by", "then_descending")):
            if op.get(key) not in (None, ""):
                keys.append((self._column(ws, op[key], head), bool(op.get(flag))))
        if not keys:
            raise LedgerError('sort needs "by": the column to sort on.')
        rows = list(range(head + 1, last + 1))
        # Checked before a single cell is written. A merged cell can't take a value, so the
        # sort used to stop part-way: one row lost, another there twice, and the copy saved.
        merged = [
            str(m)
            for m in ws.merged_cells.ranges
            if m.min_row <= last and m.max_row >= head + 1 and m.min_col <= width
        ]
        if merged:
            raise LedgerError(
                f"{ws.title} has merged cells in the rows to sort ({', '.join(merged[:3])}), "
                "and sorting would pull them apart. Unmerge them first, then sort."
            )
        ranks: dict[tuple[int, int], tuple] = {}

        def rank(r: int, c: int):
            if (r, c) not in ranks:
                v = self.result(ws, r, c)  # a formula's result, never its text
                if v in (None, ""):
                    ranks[r, c] = (2, 0, "")
                elif isinstance(v, int | float) and not isinstance(v, bool):
                    ranks[r, c] = (0, v, "")
                else:
                    ranks[r, c] = (1, 0, str(v).lower())
            return ranks[r, c]

        order = rows
        for c, down in reversed(keys):  # a stable sort, least important key first
            filled = [r for r in order if rank(r, c)[0] != 2]
            empty = [r for r in order if rank(r, c)[0] == 2]
            order = sorted(filled, key=lambda r, c=c: rank(r, c), reverse=down) + empty
        if order == rows:
            return f"{ws.title} was already in that order"
        saved = {
            r: [(ws.cell(r, c).value, copy(ws.cell(r, c)._style)) for c in range(1, width + 1)]
            for r in rows
        }
        # What each formula on the sheet came to, before its row moves. A formula that reads
        # only its own row comes to the same in its new place; one that reads other rows of
        # the block (a running total) doesn't, and neither does one outside the block that
        # reads a single row of it. A total over the whole block is unchanged.
        known = {
            at: v
            for at, v in self._results(ws).items()
            if not _reads(
                ws.cell(*at).value,
                ws.title,
                ws.title,
                "row",
                rows[0],
                rows[-1],
                own=at[0] if rows[0] <= at[0] <= rows[-1] and at[1] <= width else 0,
            )
        }
        to = dict(zip(order, rows, strict=True))  # where each row goes
        for dest, src in zip(rows, order, strict=True):
            for c, (v, style) in enumerate(saved[src], 1):
                cell = ws.cell(dest, c)
                if isinstance(v, str) and v.startswith("=") and src != dest:
                    origin = f"{col_letter(c)}{src}"
                    v = Translator(v, origin=origin).translate_formula(f"{col_letter(c)}{dest}")
                cell.value = v
                cell._style = style
        # Excel's stored results belong to the rows as they were: the ones that moved with
        # their rows go with them (so a second sort, or a look for duplicates, in the same
        # job still has them).
        self._carry(ws, known, lambda r, c: (to[r], c) if r in to and c <= width else (r, c))
        names = " then ".join(str(op[k]) for k in ("by", "then_by") if op.get(k) not in (None, ""))
        return f"Sorted {ws.title} by {names}, {'largest' if keys[0][1] else 'smallest'} first"

    def op_filter(self, op: dict[str, Any]) -> str:
        ws = self.sheet(op.get("sheet"))
        if op.get("off"):
            ws.auto_filter.ref = None
            return f"Turned the filter off on {ws.title}"
        if op.get("range"):
            _, ref = self._range(op)
        else:
            head, last, width = self._block(ws)
            ref = f"A{head}:{col_letter(width)}{last}"
        ws.auto_filter.ref = ref
        return f"Turned on filter buttons on {ws.title}"

    def op_freeze(self, op: dict[str, Any]) -> str:
        ws = self.sheet(op.get("sheet"))
        # What isn't named stays as it is: "freeze the first column" used to unfreeze a
        # header row that was already frozen.
        had_rows = had_cols = 0
        if ws.freeze_panes:
            c1, r1, _, _ = a1_bounds(str(ws.freeze_panes))
            had_rows, had_cols = (r1 or 1) - 1, (c1 or 1) - 1
        named = op.get("rows") is not None or op.get("columns") is not None
        rows = int(op["rows"]) if op.get("rows") is not None else (had_rows if named else 1)
        cols = int(op["columns"]) if op.get("columns") is not None else had_cols
        ws.freeze_panes = ws.cell(rows + 1, cols + 1) if rows or cols else None
        if not rows and not cols:
            return f"Unfroze {ws.title}"
        parts = ([f"top {_plural(rows, 'row')}"] if rows else []) + (
            [f"first {_plural(cols, 'column')}"] if cols else []
        )
        return f"Froze the {' and the '.join(parts)} on {ws.title}"

    def _move(self, ws, columns: bool, at: int, count: int) -> None:
        """Insert ``count`` rows or columns at ``at`` (or, below 0, delete that many from
        there), and have everything that names cells follow them: formulas, merged cells,
        colour rules, dropdowns, the filter, tables and charts."""
        axis = "col" if columns else "row"
        edge, delta = (at, count) if count > 0 else (at - count, count)
        # What every formula in the workbook is known to come to, to go with the cells that
        # move. Without this, a sort by a formula column after a title row went in was
        # refused even on a file Excel had calculated. A formula that read what is being
        # deleted is not kept: its total is a different number now.
        known = {}
        for sheet in self.wb.worksheets:
            known[id(sheet)] = {
                cell: v
                for cell, v in self._results(sheet).items()
                if count > 0
                or not _reads(sheet.cell(*cell).value, sheet.title, ws.title, axis, at, edge - 1)
            }
        # Merged ranges are taken off first and put back where they belong: openpyxl would
        # leave them covering whatever slid into their old place.
        merged = [str(m) for m in ws.merged_cells.ranges]
        for m in merged:
            ws.unmerge_cells(m)
        if count > 0:
            (ws.insert_cols if columns else ws.insert_rows)(at, count)
        else:
            (ws.delete_cols if columns else ws.delete_rows)(at, -count)
        for m in merged:
            new = _shift_ref(m, axis, edge, delta)
            first, _, last = new.partition(":")
            if new != _GONE and last and first != last:
                ws.merge_cells(new)
        shift_formulas(self.wb, ws, axis, edge, delta)
        shift_ranges(self.wb, ws, axis, edge, delta)

        def moved_to(r: int, c: int) -> tuple[int, int] | None:
            n = c if columns else r
            if count < 0 and at <= n < edge:
                return None  # the cell itself went
            n = n + delta if n >= edge else n
            return (r, n) if columns else (n, c)

        for sheet in self.wb.worksheets:
            self._carry(sheet, known[id(sheet)], moved_to if sheet is ws else lambda r, c: (r, c))

    def _results(self, ws) -> dict[tuple[int, int], Any]:
        """What each formula on a sheet is known to come to right now, by cell: stored by
        Excel, carried along by an earlier move, or worked out a moment ago."""
        out = {}
        for at, cell in list(ws._cells.items()):
            if cell.data_type == "f" and isinstance(cell.value, str):
                try:
                    out[at] = self.result(ws, at[0], at[1], work_out=False)
                except LedgerError:
                    pass
        return out

    def _carry(self, ws, known: dict[tuple[int, int], Any], where) -> None:
        """After cells moved on a sheet: Excel's stored results no longer line up with it, and
        what is still known is these results, at the cells' new places (``where(r, c)``)."""
        carried = {}
        for (r, c), value in known.items():
            new = where(r, c)
            text = ws.cell(*new).value if new else None
            if isinstance(text, str) and text.startswith("="):
                carried[new] = (text, value)
        self._moved.add(id(ws))
        self._carried[id(ws)] = carried

    def _insert(self, op: dict[str, Any], columns: bool) -> str:
        ws = self.sheet(op.get("sheet"))
        at = self._index(op.get("at"), columns) if op.get("at") not in (None, "") else None
        if at is None:
            raise LedgerError(f"{op.get('op')} needs at: where the new ones go.")
        count = max(1, int(op.get("count") or 1))
        self._move(ws, columns, at, count)
        return f"Inserted {_plural(count, 'column' if columns else 'row')} at {op.get('at')} on {ws.title}"

    def op_insert_rows(self, op: dict[str, Any]) -> str:
        return self._insert(op, False)

    def op_insert_columns(self, op: dict[str, Any]) -> str:
        return self._insert(op, True)

    def _delete(self, op: dict[str, Any], columns: bool) -> str:
        ws = self.sheet(op.get("sheet"))
        start, end = self._span(op, "from", columns)
        count = end - start + 1
        self._move(ws, columns, start, -count)
        return f"Deleted {_plural(count, 'column' if columns else 'row')} on {ws.title}"

    def op_delete_rows(self, op: dict[str, Any]) -> str:
        return self._delete(op, False)

    def op_delete_columns(self, op: dict[str, Any]) -> str:
        return self._delete(op, True)

    def op_resize(self, op: dict[str, Any]) -> str:
        ws = self.sheet(op.get("sheet"))
        done = []
        if op.get("columns") not in (None, ""):
            start, end = self._span({**op, "to": None, "count": None}, "columns", True)
            for c in range(start, end + 1):
                if op.get("width"):
                    width = _n(op["width"], "width") / 7.0  # pixels to Excel's character widths
                else:
                    longest = max(
                        (len(str(self.value(ws, r, c) or "")) for r in range(1, ws.max_row + 1)),
                        default=8,
                    )
                    width = min(60, max(8, longest + 2))
                ws.column_dimensions[col_letter(c)].width = width
            done.append(f"columns {op['columns']}")
        if op.get("rows") not in (None, ""):
            start, end = self._span({**op, "to": None, "count": None}, "rows", False)
            for r in range(start, end + 1):
                ws.row_dimensions[r].height = (
                    _n(op["height"], "height") * 0.75 if op.get("height") else None
                )
            done.append(f"rows {op['rows']}")
        if not done:
            raise LedgerError('resize needs "columns" (like "A:D") or "rows" (like "1:5").')
        return f"Resized {' and '.join(done)} on {ws.title}"

    def op_merge(self, op: dict[str, Any]) -> str:
        ws, ref = self._range(op)
        if op.get("unmerge"):
            ws.unmerge_cells(ref)
            return f"Unmerged {ref}"
        ws.merge_cells(ref)
        return f"Merged {ref}"

    # ---------------------------------------------------------------- looks
    @staticmethod
    def _font(old, op: dict[str, Any]):
        from openpyxl.styles import Font

        def pick(key: str, now: Any) -> Any:
            return bool(op[key]) if op.get(key) is not None else now

        underline = old.underline
        if op.get("underline") is not None:
            underline = "single" if op["underline"] else None
        return Font(
            name=str(op["font"]) if op.get("font") else old.name,
            size=_n(op["size"], "size") if op.get("size") else old.size,
            bold=pick("bold", old.bold),
            italic=pick("italic", old.italic),
            strike=pick("strikethrough", old.strike),
            underline=underline,
            color=_hex(op["color"]) if op.get("color") else old.color,
        )

    def op_format(self, op: dict[str, Any]) -> str:
        from openpyxl.styles import Alignment, Border, PatternFill, Side

        ws, ref = self._range(op)
        fonts = any(
            op.get(k) is not None
            for k in ("bold", "italic", "underline", "strikethrough", "size", "font", "color")
        )
        align = {"centre": "center", "justified": "justify"}.get(
            str(op.get("align") or "").lower(), str(op.get("align") or "").lower()
        )
        valign = {"middle": "center"}.get(
            str(op.get("valign") or "").lower(), str(op.get("valign") or "").lower()
        )
        if align and align not in ("left", "center", "right", "justify"):
            raise LedgerError("align should be left, center or right.")
        if valign and valign not in ("top", "center", "bottom"):
            raise LedgerError("valign should be top, middle or bottom.")
        aligned = bool(align or valign or op.get("wrap") is not None)
        if not (fonts or aligned or op.get("fill") or op.get("number_format") or op.get("border")):
            raise LedgerError(
                "format needs something to change: bold, italic, size, color, fill, "
                "number_format, align, wrap or border."
            )
        fill = _hex(op["fill"], "fill") if op.get("fill") else None
        side = Side(style="thin", color=_hex(op.get("border_color") or "black", "border colour"))
        c1, r1, c2, r2 = a1_bounds(ref)
        outer = str(op.get("border") or "").lower() == "outer"
        n = 0
        for cell in self._cells(ws, ref):
            if fonts:
                cell.font = self._font(cell.font, op)
            if fill:
                cell.fill = PatternFill("solid", start_color=fill, end_color=fill)
            if op.get("number_format"):
                cell.number_format = str(op["number_format"])
            if aligned:
                now = cell.alignment
                cell.alignment = Alignment(
                    horizontal=align or now.horizontal,
                    vertical=valign or now.vertical,
                    wrap_text=bool(op["wrap"]) if op.get("wrap") is not None else now.wrap_text,
                )
            if op.get("border"):
                cell.border = Border(
                    left=side if not outer or cell.column == c1 else cell.border.left,
                    right=side if not outer or cell.column == c2 else cell.border.right,
                    top=side if not outer or cell.row == r1 else cell.border.top,
                    bottom=side if not outer or cell.row == r2 else cell.border.bottom,
                )
            n += 1
        return f"Formatted {_plural(n, 'cell')} on {ws.title}"

    def op_conditional_format(self, op: dict[str, Any]) -> str:
        from openpyxl.formatting.rule import CellIsRule, ColorScaleRule, FormulaRule
        from openpyxl.styles import Font, PatternFill

        ws, ref = self._range(op)
        when = str(op.get("when") or "").strip().lower()
        first = ref.split(":")[0]
        if when in ("color scale", "colour scale", "heat map", "heatmap"):
            rule = ColorScaleRule(
                start_type="min",
                start_color=_hex(op.get("min_color") or "#FFFFFF"),
                end_type="max",
                end_color=_hex(op.get("max_color") or "#57BB8A"),
            )
            ws.conditional_formatting.add(ref, rule)
            return f"Added a colour scale to {ref} on {ws.title}"
        fill_hex = _hex(
            op.get("fill") or ("light yellow" if not op.get("color") else "white"), "fill"
        )
        look: dict[str, Any] = {}
        if op.get("fill") or not op.get("color"):
            look["fill"] = PatternFill("solid", start_color=fill_hex, end_color=fill_hex)
        if op.get("color") or op.get("bold") is not None:
            look["font"] = Font(
                bold=bool(op["bold"]) if op.get("bold") is not None else None,
                color=_hex(op["color"]) if op.get("color") else None,
            )
        value = op.get("value")
        number = isinstance(value, int | float) or bool(
            re.fullmatch(r"-?\d+(\.\d+)?", str(value or ""))
        )
        quoted = (
            str(value)
            if number or str(value or "").startswith("=")
            else '"' + str(value or "").replace('"', '""') + '"'
        )
        text = str(value or "").replace('"', '""')
        if when in _OPERATORS:
            formula = [quoted.lstrip("=")]
            if when == "between":
                formula.append(str(op.get("value2") or 0))
            rule = CellIsRule(operator=_OPERATORS[when], formula=formula, **look)
        elif when in ("contains", "text contains"):
            rule = FormulaRule(formula=[f'NOT(ISERROR(SEARCH("{text}",{first})))'], **look)
        elif when == "not contains":
            rule = FormulaRule(formula=[f'ISERROR(SEARCH("{text}",{first}))'], **look)
        elif when == "starts with":
            rule = FormulaRule(formula=[f'LEFT({first},{len(str(value or ""))})="{text}"'], **look)
        elif when == "ends with":
            rule = FormulaRule(formula=[f'RIGHT({first},{len(str(value or ""))})="{text}"'], **look)
        elif when in ("empty", "blank"):
            rule = FormulaRule(formula=[f"LEN(TRIM({first}))=0"], **look)
        elif when == "not empty":
            rule = FormulaRule(formula=[f"LEN(TRIM({first}))>0"], **look)
        elif when == "formula":
            rule = FormulaRule(formula=[str(value or "").lstrip("=")], **look)
        else:
            raise LedgerError(
                f"I don't know the rule {when!r}. Use one of: greater than, less than, between, "
                "equals, not equal, contains, starts with, ends with, empty, not empty, formula, "
                "color scale."
            )
        ws.conditional_formatting.add(ref, rule)
        said = f"cells {when}" + (f" {value}" if value not in (None, "") else "")
        return f"Highlighted {said} in {ref} on {ws.title}"

    def op_dropdown(self, op: dict[str, Any]) -> str:
        from openpyxl.worksheet.datavalidation import DataValidation

        ws, ref = self._range(op)
        options = [str(o) for o in op.get("options") or [] if str(o).strip()]
        if op.get("from_range"):
            source = "=" + str(op["from_range"]).lstrip("=")
        elif options:
            source = '"' + ",".join(o.replace(",", " ") for o in options) + '"'
            if len(source) > 255:
                raise LedgerError(
                    "That's too many choices to type into one dropdown. Put them in cells and "
                    "use from_range."
                )
        else:
            raise LedgerError("dropdown needs options (the choices).")
        dv = DataValidation(type="list", formula1=source, allow_blank=True)
        ws.add_data_validation(dv)
        dv.add(ref)
        return f"Added a dropdown to {ref} on {ws.title}"

    def op_checkbox(self, op: dict[str, Any]) -> str:
        raise LedgerError(
            "Excel files don't have tick boxes in cells the way Google Sheets does. A dropdown "
            "with Yes and No does the same job."
        )

    # ---------------------------------------------------------------- cells
    def op_clear(self, op: dict[str, Any]) -> str:
        ws, ref = self._range(op)
        what = str(op.get("what") or "values").lower()
        if what not in ("values", "formats", "format", "all"):
            raise LedgerError("clear's what should be values, formats or all.")
        for cell in self._cells(ws, ref):
            if what in ("values", "all"):
                cell.value = None
            if what in ("formats", "format", "all"):
                cell.style = "Normal"
        return f"Cleared {ref} on {ws.title}"

    def op_find_replace(self, op: dict[str, Any]) -> str:
        find, repl = str(op.get("find") or ""), str(op.get("replace") or "")
        if not find:
            raise LedgerError("find_replace needs the text to find.")
        sheets = [self.sheet(op["sheet"])] if op.get("sheet") else self.wb.worksheets
        flags = 0 if op.get("match_case") else re.I
        pattern = re.compile(re.escape(find), flags)
        n = 0
        for ws in sheets:
            for row in ws.iter_rows():
                for cell in row:
                    v = cell.value
                    if not isinstance(v, str) or (v.startswith("=") and not op.get("formulas")):
                        continue
                    if op.get("whole_cell"):
                        if pattern.fullmatch(v):
                            cell.value = repl
                            n += 1
                    elif pattern.search(v):
                        cell.value, k = pattern.subn(lambda _: repl, v)
                        n += k
        if not n:
            raise LedgerError(f"'{find[:40]}' isn't in the workbook.")
        return f"Replaced '{find[:40]}' {_plural(n, 'time')}"

    def op_copy(self, op: dict[str, Any]) -> str:
        from openpyxl.formula.translate import Translator

        src_ws, src = self._range(op, "from")
        dest_ws, dest = self._range(op, "to")
        c1, r1, _, _ = a1_bounds(src)
        dc, dr, _, _ = a1_bounds(dest)
        moved = []
        for cell in self._cells(src_ws, src):
            v = cell.value
            if op.get("values_only"):  # what the formula comes to (it used to paste a blank)
                v = self.result(src_ws, cell.row, cell.column)
            moved.append((cell.row - r1, cell.column - c1, v, copy(cell._style), cell.coordinate))
        if op.get("cut"):
            for cell in self._cells(src_ws, src):
                cell.value = None
        for row, col, v, style, origin in moved:
            target = dest_ws.cell(dr + row, dc + col)
            if isinstance(v, str) and v.startswith("=") and not op.get("values_only"):
                v = Translator(v, origin=origin).translate_formula(target.coordinate)
            target.value = v
            target._style = style
        return f"{'Moved' if op.get('cut') else 'Copied'} {src} to {dest.split(':')[0]}"

    def op_note(self, op: dict[str, Any]) -> str:
        from openpyxl.comments import Comment

        ws, ref = self._range(op, "cell")
        ws[ref.split(":")[0]].comment = Comment(str(op.get("text") or ""), "Pyxi")
        return f"Left a note on {ref.split(':')[0]}"

    def op_remove_duplicates(self, op: dict[str, Any]) -> str:
        ws = self.sheet(op.get("sheet"))
        head, last, width = self._block(ws)
        cols = (
            [self._column(ws, c, head) for c in op["columns"]]
            if op.get("columns")
            else list(range(1, width + 1))
        )
        from openpyxl.formula.translate import Translator

        def part(r: int, c: int) -> Any:
            v = ws.cell(r, c).value
            if not (isinstance(v, str) and v.startswith("=")):
                return str(v or "").strip().lower()
            try:
                return str(self.result(ws, r, c) or "").strip().lower()
            except LedgerError:
                # A formula nobody has calculated. Named as a column to compare by, its
                # result is what's asked for and isn't known. Among all the columns, two
                # rows are the same when their other cells are and the formula is the same
                # one (read as from its own row), which is how it used to be counted.
                if op.get("columns"):
                    raise
                at = f"{col_letter(c)}{r}"
                return ("=", Translator(v, origin=at).translate_formula(f"{col_letter(c)}{head + 1}"))

        seen: set[tuple] = set()
        gone = []
        for r in range(head + 1, last + 1):
            key = tuple(part(r, c) for c in cols)
            if all(isinstance(k, tuple) for k in key):  # nothing but formulas to go by
                self.result(ws, r, cols[0])
            if key in seen:
                gone.append(r)
            seen.add(key)
        for r in reversed(gone):
            self._move(ws, False, r, -1)
        return f"Removed {_plural(len(gone), 'duplicate row')} on {ws.title}"

    def op_trim(self, op: dict[str, Any]) -> str:
        ws = self.sheet(op.get("sheet"))
        n = 0
        for row in ws.iter_rows():
            for cell in row:
                v = cell.value
                if isinstance(v, str) and not v.startswith("=") and v != " ".join(v.split()):
                    cell.value = " ".join(v.split())
                    n += 1
        return f"Trimmed extra spaces in {_plural(n, 'cell')} on {ws.title}"

    # ---------------------------------------------------------------- sheets
    def op_rename_sheet(self, op: dict[str, Any]) -> str:
        ws = self.sheet(op.get("sheet"))
        new = re.sub(r"[\[\]:*?/\\]", " ", str(op.get("name") or "")).strip()[:31]
        if not new:
            raise LedgerError("rename_sheet needs the new name.")
        old = ws.title
        ws.title = new
        if self.default == old:
            self.default = new
        return f"Renamed the sheet {old} to {new}"

    def op_delete_sheet(self, op: dict[str, Any]) -> str:
        if not op.get("sheet"):
            raise LedgerError("delete_sheet needs the sheet's name.")
        ws = self.sheet(op["sheet"])
        if len(self.wb.worksheets) < 2:
            raise LedgerError(f"{ws.title} is the only sheet, and a workbook needs one.")
        title = ws.title
        self.wb.remove(ws)
        if self.default == title:
            self.default = self.wb.worksheets[0].title
        return f"Deleted the sheet {title}"

    def op_duplicate_sheet(self, op: dict[str, Any]) -> str:
        ws = self.sheet(op.get("sheet"))
        new = self.wb.copy_worksheet(ws)
        if op.get("name"):
            new.title = str(op["name"])[:31]
        return f"Copied the sheet {ws.title} as {new.title}"

    def op_hide_sheet(self, op: dict[str, Any]) -> str:
        ws = self.sheet(op.get("sheet"))
        ws.sheet_state = "visible" if op.get("show") else "hidden"
        return f"{'Showed' if op.get('show') else 'Hid'} the sheet {ws.title}"


# ============================================================================ Word
_PAPER = {"letter": (8.5, 11.0), "a4": (8.27, 11.69), "legal": (8.5, 14.0)}


class WordExtras:
    """Mixed into ``ledger.WordDoc``."""

    # ---------------------------------------------------------------- where
    def _table(self, op: dict[str, Any]):
        tables = self.doc.tables
        if not tables:
            raise LedgerError("There's no table in this document.")
        try:
            n = int(op.get("table") or 1)
            if n < 1:
                raise IndexError
            return tables[n - 1]
        except (ValueError, IndexError):
            raise LedgerError(
                f"There's no table {op.get('table')}; the document has {_plural(len(tables), 'table')}."
            ) from None

    @staticmethod
    def _cell(table, r: int, c: int):
        n_rows, n_cols = len(table.rows), len(table.columns)
        if not (1 <= r <= n_rows and 1 <= c <= n_cols):
            raise LedgerError(
                f"There's no cell {col_letter(max(c, 1))}{r}: the table is {n_rows} rows by {n_cols} columns."
            )
        return table.cell(r - 1, c - 1)

    def _bounds(self, table, op: dict[str, Any]) -> tuple[int, int, int, int]:
        n_rows, n_cols = len(table.rows), len(table.columns)
        if not op.get("range"):
            return 1, 1, n_rows, n_cols
        c1, r1, c2, r2 = a1_bounds(str(op["range"]))
        r1, c1, r2, c2 = r1 or 1, c1 or 1, r2 or n_rows, c2 or n_cols
        self._cell(table, r1, c1)
        self._cell(table, r2, c2)
        return r1, c1, r2, c2

    def _picked(self, op: dict[str, Any]) -> list:
        """The paragraphs an edit covers: "i", "from" to "to", or all of them."""
        paras = self.paras
        if op.get("all") or all(op.get(k) is None for k in ("i", "from", "to")):
            return paras
        try:
            first = int(op.get("i") if op.get("i") is not None else op.get("from"))
            last = int(op["to"]) if op.get("to") is not None else first
            if not (0 <= first <= last < len(paras)):
                raise IndexError
        except (TypeError, ValueError, IndexError):
            raise LedgerError(f"There's no paragraph {op.get('i', op.get('from'))}.") from None
        return paras[first : last + 1]

    def _everywhere(self):
        """Every paragraph, the ones inside tables too."""
        yield from self.doc.paragraphs
        for table in self.doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    yield from cell.paragraphs

    def table_lines(self) -> list[str]:
        out = []
        for n, table in enumerate(self.doc.tables[:8], 1):
            out.append(
                f"Table {n}: {_plural(len(table.rows), 'row')} x {_plural(len(table.columns), 'column')} "
                "(cells are A1, B1... like a spreadsheet)"
            )
            for r, row in enumerate(table.rows[:15], 1):
                cells = [c.text.strip()[:40] for c in row.cells]
                out.append(f"  row {r}: " + (" | ".join(cells) if any(cells) else "(empty)"))
            if len(table.rows) > 15:
                out.append("  ...")
        return out

    # ---------------------------------------------------------------- tables
    @staticmethod
    def _write_cell(cell, text: str) -> None:
        """New words in a cell, in the look its text already had."""
        first = cell.paragraphs[0]
        for extra in cell.paragraphs[1:]:
            extra._p.getparent().remove(extra._p)
        if first.runs:
            first.runs[0].text = text
            for run in first.runs[1:]:
                run._r.getparent().remove(run._r)
        else:
            first.add_run(text)

    def op_set_table_cells(self, op: dict[str, Any]) -> str:
        table = self._table(op)
        cells = table_cells(op)
        need = max(r for r, _ in cells) - len(table.rows)
        if need > 50:
            raise LedgerError("That's more than 50 new rows.")
        for _ in range(max(0, need)):
            table.add_row()
        for (r, c), text in cells.items():
            self._write_cell(self._cell(table, r, c), text)
        return f"Filled {_plural(len(cells), 'cell')} in the table"

    def op_add_table_row(self, op: dict[str, Any]) -> str:
        table = self._table(op)
        n_rows = len(table.rows)
        after = n_rows if op.get("after") in (None, "", "end") else int(op["after"])
        rows = op.get("rows") if isinstance(op.get("rows"), list) else None
        count = len(rows) if rows else max(1, int(op.get("count") or 1))
        if not 0 <= after <= n_rows or count > 50:
            raise LedgerError(f"The table has {_plural(n_rows, 'row')}.")
        for k in range(count):
            new = table.add_row()._tr
            if after + k < n_rows + k:  # not at the end: slide it up into place
                trs = table._tbl.tr_lst
                anchor = trs[after + k - 1] if after + k > 0 else None
                new.getparent().remove(new)
                if anchor is None:
                    trs[0].addprevious(new)
                else:
                    anchor.addnext(new)
        values = rows or ([op["values"]] if isinstance(op.get("values"), list) else [])
        if values:
            self.op_set_table_cells(
                {"table": op.get("table"), "rows": values, "start": f"A{after + 1}"}
            )
        return f"Added {_plural(count, 'row')} to the table"

    def op_add_table_column(self, op: dict[str, Any]) -> str:
        from docx.shared import Inches

        table = self._table(op)
        n_cols = len(table.columns)
        raw = op.get("after")
        after = (
            n_cols
            if raw in (None, "", "end")
            else int(raw)
            if str(raw).isdigit()
            else col_number(str(raw))
        )
        if not 0 <= after <= n_cols or n_cols >= 20:
            raise LedgerError(f"The table has {_plural(n_cols, 'column')}, and 20 is the most.")
        width = table.columns[0].width or Inches(1)
        table.add_column(width)
        if after < n_cols:  # not at the end: move each row's new cell into place
            for tr in table._tbl.tr_lst:
                tcs = tr.tc_lst
                new = tcs[-1]
                tr.remove(new)
                if after == 0:
                    tcs[0].addprevious(new)
                else:
                    tcs[after - 1].addnext(new)
        if isinstance(op.get("values"), list):
            self.op_set_table_cells(
                {"table": op.get("table"), "column": after + 1, "values": op["values"]}
            )
        return "Added a column to the table"

    def _lines(self, op: dict[str, Any], what: str, limit: int) -> list[int]:
        raw = op.get(f"{what}s") if isinstance(op.get(f"{what}s"), list) else [op.get(what)]
        picked = {
            int(v) if str(v).isdigit() else col_number(str(v)) for v in raw if v not in (None, "")
        }
        if not picked or any(not 1 <= n <= limit for n in picked):
            raise LedgerError(f"Which {what}? The table has {_plural(limit, what)}.")
        return sorted(picked, reverse=True)

    def op_delete_table_row(self, op: dict[str, Any]) -> str:
        table = self._table(op)
        rows = self._lines(op, "row", len(table.rows))
        for n in rows:
            tr = table.rows[n - 1]._tr
            tr.getparent().remove(tr)
        return f"Deleted {_plural(len(rows), 'row')} from the table"

    def op_delete_table_column(self, op: dict[str, Any]) -> str:
        table = self._table(op)
        cols = self._lines(op, "column", len(table.columns))
        for n in cols:
            for tr in table._tbl.tr_lst:
                tcs = tr.tc_lst
                if n - 1 < len(tcs):
                    tr.remove(tcs[n - 1])
            grid = table._tbl.tblGrid.gridCol_lst
            if n - 1 < len(grid):
                table._tbl.tblGrid.remove(grid[n - 1])
        return f"Deleted {_plural(len(cols), 'column')} from the table"

    def op_delete_table(self, op: dict[str, Any]) -> str:
        tbl = self._table(op)._tbl
        tbl.getparent().remove(tbl)
        return "Deleted the table"

    def op_merge_cells(self, op: dict[str, Any]) -> str:
        table = self._table(op)
        r1, c1, r2, c2 = self._bounds(table, op)
        table.cell(r1 - 1, c1 - 1).merge(table.cell(r2 - 1, c2 - 1))
        return f"Merged {str(op.get('range') or 'the table').upper()}"

    @staticmethod
    def _style_run(run, op: dict[str, Any]) -> None:
        from docx.shared import Pt, RGBColor

        for key, attr in (
            ("bold", "bold"),
            ("italic", "italic"),
            ("underline", "underline"),
            ("strikethrough", "strike"),
        ):
            if op.get(key) is not None:
                setattr(run.font, attr, bool(op[key]))
        if op.get("size"):
            run.font.size = Pt(_n(op["size"], "size"))
        if op.get("font"):
            run.font.name = str(op["font"])
        if op.get("color"):
            run.font.color.rgb = RGBColor.from_string(_hex(op["color"]))

    def op_format_table(self, op: dict[str, Any]) -> str:
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.oxml import OxmlElement
        from docx.oxml.ns import qn

        table = self._table(op)
        r1, c1, r2, c2 = self._bounds(table, op)
        how = {
            "left": WD_ALIGN_PARAGRAPH.LEFT,
            "center": WD_ALIGN_PARAGRAPH.CENTER,
            "centre": WD_ALIGN_PARAGRAPH.CENTER,
            "right": WD_ALIGN_PARAGRAPH.RIGHT,
            "justify": WD_ALIGN_PARAGRAPH.JUSTIFY,
        }.get(str(op.get("align") or "").lower())
        if op.get("align") and how is None:
            raise LedgerError("align should be left, center, right or justify.")
        text_keys = ("bold", "italic", "underline", "strikethrough", "size", "font", "color")
        styled = any(op.get(k) is not None for k in text_keys)
        fill = _hex(op["fill"], "fill") if op.get("fill") else None
        if not (styled or fill or how is not None):
            raise LedgerError(
                "format_table needs something to change: fill, bold, color, size or align."
            )
        for r in range(r1, r2 + 1):
            for c in range(c1, c2 + 1):
                cell = table.cell(r - 1, c - 1)
                if fill:
                    props = cell._tc.get_or_add_tcPr()
                    for old in props.findall(qn("w:shd")):
                        props.remove(old)
                    shade = OxmlElement("w:shd")
                    shade.set(qn("w:val"), "clear")
                    shade.set(qn("w:color"), "auto")
                    shade.set(qn("w:fill"), fill)
                    props.append(shade)
                for p in cell.paragraphs:
                    if how is not None:
                        p.alignment = how
                    if styled:
                        for run in p.runs:
                            self._style_run(run, op)
        return f"Formatted {str(op['range']).upper() if op.get('range') else 'the table'}"

    # ---------------------------------------------------------------- text
    def op_set_text(self, op: dict[str, Any]) -> str:
        p = self._picked({"i": op.get("i")})[0]
        text = str(op.get("text") or "")
        if p.runs:
            p.runs[0].text = text
            for run in p.runs[1:]:
                run._r.getparent().remove(run._r)
        else:
            p.add_run(text)
        return f"Rewrote paragraph {int(op['i']) + 1}"

    def op_insert_text(self, op: dict[str, Any]) -> str:
        text = str(op.get("text") or "")
        anchor = str(op.get("after_text") or op.get("before_text") or "")
        if not text or not anchor:
            raise LedgerError(
                "insert_text needs text, and after_text or before_text (words already there)."
            )
        joined = anchor + text if op.get("after_text") else text + anchor
        for p in self._everywhere():
            if anchor not in p.text:
                continue
            for run in p.runs:
                if anchor in run.text:
                    run.text = run.text.replace(anchor, joined, 1)
                    break
            else:  # the words run across two looks: the line keeps the first one
                whole = p.text.replace(anchor, joined, 1)
                p.runs[0].text = whole
                for run in p.runs[1:]:
                    run._r.getparent().remove(run._r)
            return f"Added text {'after' if op.get('after_text') else 'before'} '{anchor[:40]}'"
        raise LedgerError(f"'{anchor[:40]}' isn't in the document.")

    def op_delete_text(self, op: dict[str, Any]) -> str:
        self.op_replace_text({"find": op.get("find"), "replace": ""})
        return f"Deleted '{str(op.get('find') or '')[:40]}'"

    def op_make_bullets(self, op: dict[str, Any]) -> str:
        names = ("List Number",) if op.get("numbered") else ("List Bullet",)
        style = self._style(*names)
        if style is None:
            raise LedgerError(f"This document has no {names[0]} style to use.")
        for p in self._picked(op):
            p.style = style
        return f"Made those lines {'a numbered list' if op.get('numbered') else 'bullets'}"

    def op_remove_bullets(self, op: dict[str, Any]) -> str:
        style = self._style("Normal")
        for p in self._picked(op):
            if p.style is not None and p.style.name.startswith("List") and style:
                p.style = style
        return "Removed the bullets"

    def op_spacing(self, op: dict[str, Any]) -> str:
        from docx.shared import Inches, Pt

        if not any(
            op.get(k) is not None for k in ("line", "before", "after", "indent", "first_line")
        ):
            raise LedgerError(
                "spacing needs line (1.5), before or after (points), or indent (inches)."
            )
        for p in self._picked(op):
            fmt = p.paragraph_format
            if op.get("line"):
                fmt.line_spacing = _n(op["line"], "line")
            if op.get("before") is not None:
                fmt.space_before = Pt(_n(op["before"], "before"))
            if op.get("after") is not None:
                fmt.space_after = Pt(_n(op["after"], "after"))
            if op.get("indent") is not None:
                fmt.left_indent = Inches(_n(op["indent"], "indent"))
            if op.get("first_line") is not None:
                fmt.first_line_indent = Inches(_n(op["first_line"], "first_line"))
        return "Set the spacing"

    # ---------------------------------------------------------------- pictures and pages
    def op_add_image(self, op: dict[str, Any]) -> str:
        from docx.shared import Inches

        path = _picture(op.get("path") or op.get("url"))
        anchor = self._anchor(op)
        self.doc.add_picture(str(path), width=Inches(_n(op.get("width") or 5, "width")))
        if anchor is not None:
            self._place(self.doc.paragraphs[-1]._p, anchor, [])
        return f"Added the picture {path.name}"

    def op_page_setup(self, op: dict[str, Any]) -> str:
        from docx.enum.section import WD_ORIENT
        from docx.shared import Inches

        margins = op.get("margins") if isinstance(op.get("margins"), dict) else {}
        said = []
        if op.get("size") and str(op["size"]).lower() not in _PAPER:
            raise LedgerError("size should be letter, a4 or legal.")
        for section in self.doc.sections:
            for side in ("top", "bottom", "left", "right"):
                v = margins.get(side, op.get("margin"))
                if v is not None:
                    setattr(section, f"{side}_margin", Inches(_n(v, "margin")))
            if op.get("size"):
                w, h = _PAPER[str(op["size"]).lower()]
                wide = section.orientation == WD_ORIENT.LANDSCAPE
                section.page_width, section.page_height = (
                    (Inches(h), Inches(w)) if wide else (Inches(w), Inches(h))
                )
            if op.get("orientation"):
                wide = str(op["orientation"]).lower().startswith("land")
                w, h = section.page_width, section.page_height
                section.orientation = WD_ORIENT.LANDSCAPE if wide else WD_ORIENT.PORTRAIT
                section.page_width, section.page_height = (
                    (max(w, h), min(w, h)) if wide else (min(w, h), max(w, h))
                )
        if margins or op.get("margin") is not None:
            said.append("margins")
        if op.get("size"):
            said.append(f"{op['size']} paper")
        if op.get("orientation"):
            said.append(
                "landscape" if str(op["orientation"]).lower().startswith("land") else "portrait"
            )
        if not said:
            raise LedgerError("page_setup needs margin (inches), orientation or size.")
        return "Set the page: " + ", ".join(said)

    def _edge(self, op: dict[str, Any], kind: str) -> str:
        from docx.enum.text import WD_ALIGN_PARAGRAPH

        text = str(op.get("text") or "")
        for section in self.doc.sections:
            part = getattr(section, kind)
            part.is_linked_to_previous = False
            p = part.paragraphs[0] if part.paragraphs else part.add_paragraph()
            p.text = text
            how = {
                "center": WD_ALIGN_PARAGRAPH.CENTER,
                "centre": WD_ALIGN_PARAGRAPH.CENTER,
                "right": WD_ALIGN_PARAGRAPH.RIGHT,
                "left": WD_ALIGN_PARAGRAPH.LEFT,
            }.get(str(op.get("align") or "").lower())
            if how is not None:
                p.alignment = how
        return f"Set the {kind} to '{text[:40]}'" if text else f"Cleared the {kind}"

    def op_header(self, op: dict[str, Any]) -> str:
        return self._edge(op, "header")

    def op_footer(self, op: dict[str, Any]) -> str:
        return self._edge(op, "footer")


# ============================================================================ PowerPoint
_PP_SHAPES = {
    "rectangle": "RECTANGLE",
    "rounded rectangle": "ROUNDED_RECTANGLE",
    "ellipse": "OVAL",
    "circle": "OVAL",
    "oval": "OVAL",
    "triangle": "ISOSCELES_TRIANGLE",
    "diamond": "DIAMOND",
    "arrow": "RIGHT_ARROW",
    "right arrow": "RIGHT_ARROW",
    "left arrow": "LEFT_ARROW",
    "up arrow": "UP_ARROW",
    "down arrow": "DOWN_ARROW",
    "star": "STAR_5_POINT",
    "heart": "HEART",
    "cloud": "CLOUD",
}
_PP_CHARTS = {
    "bar": "COLUMN_CLUSTERED",
    "column": "COLUMN_CLUSTERED",
    "horizontal bar": "BAR_CLUSTERED",
    "line": "LINE_MARKERS",
    "pie": "PIE",
    "area": "AREA",
    "doughnut": "DOUGHNUT",
}
_PP_LAYOUTS = {
    "title": ("title slide",),
    "title slide": ("title slide",),
    "title and body": ("title and content", "title and body"),
    "bullets": ("title and content", "title and body"),
    "section": ("section header",),
    "section header": ("section header",),
    "two columns": ("two content", "comparison"),
    "title only": ("title only",),
    "blank": ("blank",),
    "caption": ("content with caption", "picture with caption"),
}


class DeckExtras:
    """Mixed into ``ledger.Deck``. Positions and sizes are inches from the slide's top left."""

    # ---------------------------------------------------------------- where
    def _page(self) -> tuple[float, float]:
        return self.prs.slide_width / 914400.0, self.prs.slide_height / 914400.0

    def _named_layout(self, op: dict[str, Any]):
        """The slide layout an edit asks for by name, or None when it names none."""
        if not op.get("layout"):
            return None
        want = str(op["layout"]).strip().lower()
        names = _PP_LAYOUTS.get(want, (want,))
        for name in names:
            for layout in self.prs.slide_layouts:
                if layout.name.strip().lower() == name:
                    return layout
        for layout in self.prs.slide_layouts:
            if any(n in layout.name.lower() for n in names):
                return layout
        have = ", ".join(layout.name for layout in self.prs.slide_layouts)
        raise LedgerError(f"This deck has no {op['layout']!r} layout; it has {have}.")

    @staticmethod
    def _kind(slide, shape) -> str:
        if shape.is_placeholder:
            kind = str(shape.placeholder_format.type or "").split(".")[-1].split(" ")[0].lower()
            if "title" in kind and "sub" not in kind:
                return "title"
            if "subtitle" in kind:
                return "subtitle"
            if kind in ("body", "object"):
                return "body"
            return kind.replace("_", " ") or "placeholder"
        if getattr(shape, "has_table", False) and shape.has_table:
            return "table"
        if getattr(shape, "has_chart", False) and shape.has_chart:
            return "chart"
        kind = str(shape.shape_type or "").split(".")[-1].split(" ")[0].lower()
        return {"text_box": "text box", "auto_shape": "shape"}.get(
            kind, kind.replace("_", " ") or "shape"
        )

    def shape_lines(self, slide) -> list[str]:
        out = []
        for shape in slide.shapes:
            box = ""
            if shape.left is not None and shape.width is not None:
                box = (
                    f" at {shape.left / 914400:.1f},{shape.top / 914400:.1f} size "
                    f"{shape.width / 914400:.1f}x{shape.height / 914400:.1f}"
                )
            line = f"  {self._kind(slide, shape)} [id {shape.shape_id}]{box}"
            if shape.has_text_frame:
                text = " / ".join(t for t in shape.text_frame.text.strip().split("\n") if t)
                line += f": {text[:300]!r}" if text else ": (empty)"
            elif getattr(shape, "has_table", False) and shape.has_table:
                table = shape.table
                first = (
                    [c.text.strip()[:30] for c in table.rows[0].cells] if len(table.rows) else []
                )
                line += f": {len(table.rows)} rows x {len(table.columns)} columns; first row: {' | '.join(first) if any(first) else 'empty'}"
            out.append(line)
        return out

    def _shape(self, op: dict[str, Any]):
        slide = self._slide(op)
        if op.get("id") not in (None, ""):
            for shape in slide.shapes:
                if str(shape.shape_id) == str(op["id"]).strip():
                    return slide, shape
            raise LedgerError(f"Slide {op.get('slide')} has nothing with the id {op['id']}.")
        which = str(op.get("which") or "body").lower()
        for shape in slide.shapes:
            if self._kind(slide, shape) == which:
                return slide, shape
        raise LedgerError(f"Slide {op.get('slide')} has no {which} box.")

    def _place(self, op: dict[str, Any], w: float, h: float) -> tuple[int, int, int, int]:
        from pptx.util import Inches

        page_w, page_h = self._page()
        w = _n(op.get("w", w), "w")
        h = _n(op.get("h", h), "h")
        x = _n(op.get("x", max(0.0, (page_w - w) / 2)), "x")
        y = _n(op.get("y", max(0.0, (page_h - h) / 2)), "y")
        return Inches(x), Inches(y), Inches(w), Inches(h)

    @staticmethod
    def _style_runs(runs, op: dict[str, Any]) -> None:
        from pptx.dml.color import RGBColor
        from pptx.util import Pt

        for run in runs:
            for key, attr in (("bold", "bold"), ("italic", "italic"), ("underline", "underline")):
                if op.get(key) is not None:
                    setattr(run.font, attr, bool(op[key]))
            if op.get("size"):
                run.font.size = Pt(_n(op["size"], "size"))
            if op.get("font"):
                run.font.name = str(op["font"])
            if op.get("color"):
                run.font.color.rgb = RGBColor.from_string(_hex(op["color"]))
            if op.get("link"):
                run.hyperlink.address = str(op["link"])

    @staticmethod
    def _align(frame, op: dict[str, Any]) -> None:
        from pptx.enum.text import PP_ALIGN

        if not op.get("align"):
            return
        how = {
            "left": PP_ALIGN.LEFT,
            "center": PP_ALIGN.CENTER,
            "centre": PP_ALIGN.CENTER,
            "right": PP_ALIGN.RIGHT,
            "justify": PP_ALIGN.JUSTIFY,
        }.get(str(op["align"]).lower())
        if how is None:
            raise LedgerError("align should be left, center, right or justify.")
        for p in frame.paragraphs:
            p.alignment = how

    # ---------------------------------------------------------------- text
    def op_set_text(self, op: dict[str, Any]) -> str:
        slide, shape = self._shape(op)
        if not shape.has_text_frame:
            raise LedgerError("That isn't a text box. For a table, use set_table_cells.")
        items = [str(b) for b in op.get("bullets") or [] if str(b).strip()]
        if items:
            self._fill(shape.text_frame, items, append=False)
        else:
            shape.text_frame.text = str(op.get("text") or "")
        return f"Set the {self._kind(slide, shape)} text on slide {op.get('slide')}"

    def op_add_text_box(self, op: dict[str, Any]) -> str:
        slide = self._slide(op)
        page_w, _ = self._page()
        box = slide.shapes.add_textbox(*self._place(op, page_w - 2, 1.0))
        frame = box.text_frame
        frame.word_wrap = True
        frame.text = str(op.get("text") or "")
        self._style_runs([r for p in frame.paragraphs for r in p.runs], op)
        self._align(frame, op)
        return f"Added a text box to slide {op.get('slide')}"

    def op_format_text(self, op: dict[str, Any]) -> str:
        from pptx.text.text import _Run

        find = str(op.get("find") or "")
        if op.get("id") not in (None, "") or op.get("which") or (op.get("slide") and not find):
            shapes = [self._shape(op)[1]]
        else:
            slides = [self._slide(op)] if op.get("slide") else list(self.prs.slides)
            shapes = [s for slide in slides for s in slide.shapes]
        frames = []
        for shape in shapes:
            if shape.has_text_frame:
                frames.append(shape.text_frame)
            elif getattr(shape, "has_table", False) and shape.has_table:
                frames += [c.text_frame for row in shape.table.rows for c in row.cells]
        hits = 0
        for frame in frames:
            if not find:
                runs = [r for p in frame.paragraphs for r in p.runs]
                self._style_runs(runs, op)
                self._align(frame, op)
                hits += len(runs)
                continue
            for p in frame.paragraphs:
                for run in list(p.runs):
                    while find in run.text:
                        # Cut the run in three so only these words change.
                        before, _, after = run.text.partition(find)
                        mid, tail = deepcopy(run._r), deepcopy(run._r)
                        run._r.addnext(mid)
                        mid.addnext(tail)
                        run.text = before
                        hit, rest = _Run(mid, p), _Run(tail, p)
                        hit.text, rest.text = find, after
                        if not before:
                            run._r.getparent().remove(run._r)
                        self._style_runs([hit], op)
                        hits += 1
                        run = rest
                    if not run.text and run._r.getparent() is not None:
                        run._r.getparent().remove(run._r)
        if not hits:
            if find:
                raise LedgerError(f"'{find[:40]}' isn't on the slides.")
            raise LedgerError("There's no text there to format.")
        return f"Formatted '{find[:40]}'" if find else "Formatted the text"

    # ---------------------------------------------------------------- things on a slide
    def op_add_shape(self, op: dict[str, Any]) -> str:
        from pptx.dml.color import RGBColor
        from pptx.enum.shapes import MSO_SHAPE

        slide = self._slide(op)
        name = _PP_SHAPES.get(str(op.get("shape") or "rectangle").lower())
        if name is None:
            raise LedgerError(f"shape should be one of: {', '.join(sorted(_PP_SHAPES))}.")
        shape = slide.shapes.add_shape(getattr(MSO_SHAPE, name), *self._place(op, 2.0, 2.0))
        if op.get("fill"):
            shape.fill.solid()
            shape.fill.fore_color.rgb = RGBColor.from_string(_hex(op["fill"], "fill"))
        if op.get("text"):
            shape.text_frame.text = str(op["text"])
            self._style_runs([r for p in shape.text_frame.paragraphs for r in p.runs], op)
        return f"Added a {str(op.get('shape') or 'rectangle').lower()} to slide {op.get('slide')}"

    def op_add_image(self, op: dict[str, Any]) -> str:
        from pptx.util import Inches

        slide = self._slide(op)
        path = _picture(op.get("path") or op.get("url"))
        width = Inches(_n(op["w"], "w")) if op.get("w") is not None else None
        height = Inches(_n(op["h"], "h")) if op.get("h") is not None else None
        if width is None and height is None:
            width = Inches(min(4.0, self._page()[0] - 1))
        pic = slide.shapes.add_picture(str(path), 0, 0, width=width, height=height)
        page_w, page_h = self._page()
        pic.left = (
            Inches(_n(op["x"], "x"))
            if op.get("x") is not None
            else int((Inches(page_w) - pic.width) / 2)
        )
        pic.top = (
            Inches(_n(op["y"], "y"))
            if op.get("y") is not None
            else int((Inches(page_h) - pic.height) / 2)
        )
        return f"Added the picture {path.name} to slide {op.get('slide')}"

    def op_add_table(self, op: dict[str, Any]) -> str:
        slide = self._slide(op)
        rows = [[str(c) for c in r] for r in op.get("rows") or [] if isinstance(r, list)]
        n_rows = len(rows) or int(op.get("n_rows") or 0)
        n_cols = max((len(r) for r in rows), default=0) or int(op.get("n_cols") or 0)
        if not (1 <= n_rows <= 25 and 1 <= n_cols <= 20):
            raise LedgerError(
                "add_table needs rows (the cells) or n_rows and n_cols (up to 25 x 20)."
            )
        page_w, _ = self._page()
        frame = slide.shapes.add_table(n_rows, n_cols, *self._place(op, page_w - 2, 0.4 * n_rows))
        for r, row in enumerate(rows):
            for c, text in enumerate(row):
                frame.table.cell(r, c).text = text
        return f"Added a {n_rows} x {n_cols} table to slide {op.get('slide')}"

    def op_set_table_cells(self, op: dict[str, Any]) -> str:
        slide = self._slide(op)
        tables = [
            s
            for s in slide.shapes
            if getattr(s, "has_table", False)
            and s.has_table
            and (op.get("id") in (None, "") or str(s.shape_id) == str(op["id"]).strip())
        ]
        if not tables:
            raise LedgerError(f"Slide {op.get('slide')} has no table.")
        table = tables[0].table
        cells = table_cells(op)
        for (r, c), text in cells.items():
            if not (1 <= r <= len(table.rows) and 1 <= c <= len(table.columns)):
                raise LedgerError(
                    f"There's no cell {col_letter(max(c, 1))}{r}: the table is {len(table.rows)} rows by "
                    f"{len(table.columns)} columns."
                )
            table.cell(r - 1, c - 1).text = text
        return f"Filled {_plural(len(cells), 'cell')} in the table"

    def op_add_chart(self, op: dict[str, Any]) -> str:
        from pptx.chart.data import CategoryChartData
        from pptx.enum.chart import XL_CHART_TYPE

        slide = self._slide(op)
        kind = str(op.get("type") or "bar").lower()
        if kind not in _PP_CHARTS:
            raise LedgerError(f"type should be one of: {', '.join(_PP_CHARTS)}.")
        categories = [str(c) for c in op.get("categories") or []]
        series = op.get("series")
        if isinstance(series, list):  # one unnamed set of numbers
            series = {str(op.get("title") or "Values"): series}
        if not categories or not isinstance(series, dict) or not series:
            raise LedgerError(
                'add_chart needs categories (the labels) and series ({"Sales": [1, 2, 3]}).'
            )
        data = CategoryChartData()
        data.categories = categories
        for name, values in series.items():
            numbers = [_n(v, "a chart value") for v in values]
            if len(numbers) != len(categories):
                raise LedgerError(f"{name} has {len(numbers)} values for {len(categories)} labels.")
            data.add_series(str(name), numbers)
        page_w, page_h = self._page()
        frame = slide.shapes.add_chart(
            getattr(XL_CHART_TYPE, _PP_CHARTS[kind]),
            *self._place(op, page_w - 2, page_h - 2.5),
            data,
        )
        chart = frame.chart
        if op.get("title"):
            chart.has_title = True
            chart.chart_title.text_frame.text = str(op["title"])
        chart.has_legend = len(series) > 1 or kind in ("pie", "doughnut")
        return f"Added a {kind} chart to slide {op.get('slide')}"

    def op_move(self, op: dict[str, Any]) -> str:
        from pptx.util import Inches

        slide, shape = self._shape(op)
        resized = False
        for key, attr in (("w", "width"), ("h", "height")):
            if op.get(key) is not None:
                setattr(shape, attr, Inches(_n(op[key], key)))
                resized = True
        if op.get("center"):
            page_w, page_h = self._page()
            shape.left = int((Inches(page_w) - shape.width) / 2)
            shape.top = int((Inches(page_h) - shape.height) / 2)
        for key, attr in (("x", "left"), ("y", "top")):
            if op.get(key) is not None:
                setattr(shape, attr, Inches(_n(op[key], key)))
        return f"{'Resized' if resized else 'Moved'} the {self._kind(slide, shape)} on slide {op.get('slide')}"

    def op_delete(self, op: dict[str, Any]) -> str:
        if op.get("id") in (None, "") and not op.get("which"):
            raise LedgerError("delete needs the id of the thing to remove (from the outline).")
        slide, shape = self._shape(op)
        kind = self._kind(slide, shape)
        shape._element.getparent().remove(shape._element)
        return f"Deleted the {kind} on slide {op.get('slide')}"

    # ---------------------------------------------------------------- slides
    def op_duplicate_slide(self, op: dict[str, Any]) -> str:
        src = self._slide(op)
        new = self.prs.slides.add_slide(src.slide_layout)
        for shape in list(new.shapes):
            shape._element.getparent().remove(shape._element)
        rids: dict[str, str] = {}
        for rid, rel in src.part.rels.items():
            if rel.reltype.endswith(("/notesSlide", "/slideLayout")):
                continue
            if rel.is_external:
                rids[rid] = new.part.relate_to(rel.target_ref, rel.reltype, is_external=True)
            else:
                rids[rid] = new.part.relate_to(rel.target_part, rel.reltype)
        for shape in src.shapes:
            el = deepcopy(shape._element)
            for node in el.iter():
                for attr, value in list(node.attrib.items()):
                    if (
                        attr.startswith(
                            "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
                        )
                        and value in rids
                    ):
                        node.set(attr, rids[value])
            new.shapes._spTree.append(el)
        ids = self.prs.slides._sldIdLst
        el = ids[-1]
        ids.remove(el)
        ids.insert(int(op["slide"]), el)
        return f"Copied slide {op['slide']} (the copy is slide {int(op['slide']) + 1})"

    def op_move_slide(self, op: dict[str, Any]) -> str:
        self._slide(op)
        ids = self.prs.slides._sldIdLst
        try:
            to = max(1, min(int(op.get("to")), len(ids)))
        except (TypeError, ValueError):
            raise LedgerError('move_slide needs "to": the slide number it should become.') from None
        el = ids[int(op["slide"]) - 1]
        ids.remove(el)
        ids.insert(to - 1, el)
        return f"Moved slide {op['slide']} to position {to}"

    def op_background(self, op: dict[str, Any]) -> str:
        from pptx.dml.color import RGBColor

        if not op.get("color"):
            raise LedgerError("background needs a color.")
        slides = [self._slide(op)] if op.get("slide") else list(self.prs.slides)
        for slide in slides:
            fill = slide.background.fill
            fill.solid()
            fill.fore_color.rgb = RGBColor.from_string(_hex(op["color"]))
        where = f"slide {op['slide']}" if op.get("slide") else "every slide"
        return f"Changed the background of {where}"
