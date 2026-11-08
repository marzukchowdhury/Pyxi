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
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pyxi.agents.pixel import out_dir_for, unique_path

LOCAL_EXTS = {".xlsx", ".xlsm", ".docx", ".pptx"}


class LedgerError(ValueError):
    """A bad edit or a file Ledger can't work on, said plainly."""


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


class Workbook:
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

    def sheet(self, name: str | None):
        if not name:
            return self.wb[self.default]
        for ws in self.wb.worksheets:
            if ws.title.lower() == str(name).lower():
                return ws
        raise LedgerError(
            f"There's no sheet called {name!r}; it has {', '.join(self.wb.sheetnames)}."
        )

    def value(self, ws, r: int, c: int) -> Any:
        v = ws.cell(r, c).value
        if isinstance(v, str) and v.startswith("=") and self.values is not None:
            cached = self.values[ws.title].cell(r, c).value
            return cached if cached is not None else v
        return v

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
        return "\n".join(out)

    # ---------------------------------------------------------------- edits
    def apply(self, op: dict[str, Any]) -> str:
        name = op.get("op")
        fn = getattr(self, f"op_{name}", None)
        if fn is None:
            raise LedgerError(f"Excel files don't have an edit called {name!r}.")
        return fn(op)

    def op_set_cells(self, op: dict[str, Any]) -> str:
        ws = self.sheet(op.get("sheet"))
        cells = op.get("cells") or {}
        if not isinstance(cells, dict) or not cells:
            raise LedgerError("set_cells needs cells like {'B5': 120, 'C5': '=B5*2'}.")
        for ref, v in cells.items():
            ws[str(ref).upper()] = v
        return f"Set {len(cells)} cell{'s' if len(cells) != 1 else ''} on {ws.title}"

    def op_append_row(self, op: dict[str, Any]) -> str:
        ws = self.sheet(op.get("sheet"))
        values = list(op.get("values") or [])
        r = _last_row(ws) + 1
        for c, v in enumerate(values, 1):
            ws.cell(r, c, v)
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
            nums = [
                v
                for v in (self.value(ws, x, c) for x in range(head + 1, last + 1))
                if _is_number(v)
            ]
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
        v = _col_index(src, op.get("value"), head)
        agg = str(op.get("agg") or ("sum" if v else "count")).lower()
        if agg not in ("sum", "count", "average"):
            raise LedgerError("pivot agg should be sum, count or average.")
        if agg != "count" and v is None:
            raise LedgerError(f"pivot with {agg} needs value: the column to {agg}.")
        keys: list[Any] = []
        groups: dict[Any, list[float]] = {}
        for r in range(head + 1, last + 1):
            k = self.value(src, r, g)
            if k in (None, ""):
                continue
            if k not in groups:
                keys.append(k)
                groups[k] = []
            x = self.value(src, r, v) if v else None
            if _is_number(x):
                groups[k].append(float(x))
        if not keys:
            raise LedgerError(f"There's nothing to group in that column on {src.title}.")
        keys.sort(key=lambda k: (not isinstance(k, str), str(k).lower()))
        title = str(op.get("target") or "Pivot")[:31]
        name, n = title, 2
        while name in self.wb.sheetnames:
            name, n = f"{title} {n}"[:31], n + 1
        ws = self.wb.create_sheet(name)
        g_name = src.cell(head, g).value or get_column_letter(g)
        v_name = (src.cell(head, v).value or get_column_letter(v)) if v else "rows"
        ws.append([g_name, f"{agg.title()} of {v_name}"])
        for c in ws[1]:
            c.font = Font(bold=True)
        sheet = quote_sheetname(src.title)
        GL = get_column_letter(g)
        crit = f"{sheet}!${GL}${head + 1}:${GL}${last}"
        rng = (
            f"{sheet}!${get_column_letter(v)}${head + 1}:${get_column_letter(v)}${last}"
            if v
            else ""
        )
        for i, k in enumerate(keys, start=2):
            ws.cell(i, 1, k)
            if agg == "count":
                ws.cell(i, 2, f"=COUNTIFS({crit},A{i})")
            elif agg == "sum":
                ws.cell(i, 2, f"=SUMIFS({rng},{crit},A{i})")
            else:
                ws.cell(i, 2, f"=AVERAGEIFS({rng},{crit},A{i})")
        end = len(keys) + 1
        if agg != "average":
            ws.cell(end + 1, 1, "Total").font = Font(bold=True)
            ws.cell(end + 1, 2, f"=SUM(B2:B{end})").font = Font(bold=True)
        ws.column_dimensions["A"].width = max(12, min(40, max(len(str(k)) for k in keys) + 2))
        ws.column_dimensions["B"].width = 18
        if v:
            ws.cell(2, 2).number_format = src.cell(head + 1, v).number_format
            for i in range(3, end + 2):
                ws.cell(i, 2).number_format = src.cell(head + 1, v).number_format

        def result(k: Any) -> float:
            xs = groups[k]
            if agg == "count":
                return float(
                    sum(1 for r in range(head + 1, last + 1) if self.value(src, r, g) == k)
                )
            if agg == "sum":
                return sum(xs)
            return sum(xs) / len(xs) if xs else 0.0

        top = sorted(keys, key=result, reverse=True)[:3]
        best = ", ".join(f"{k} {_shown(result(k))}" for k in top)
        return f"Made a pivot on sheet {name}: {agg} of {v_name} by {g_name}, {len(keys)} groups (top: {best})"

    def op_format(self, op: dict[str, Any]) -> str:
        from openpyxl.styles import Font, PatternFill

        ws = self.sheet(op.get("sheet"))
        rng = str(op.get("range") or "")
        if not rng:
            raise LedgerError("format needs a range like 'A1:D1'.")
        cells = ws[rng]
        rows = cells if isinstance(cells, tuple) else ((cells,),)
        rows = tuple(r if isinstance(r, tuple) else (r,) for r in rows)
        n = 0
        for row in rows:
            for c in row:
                if op.get("bold") is not None:
                    c.font = Font(bold=bool(op["bold"]), italic=c.font.italic, color=c.font.color)
                if op.get("number_format"):
                    c.number_format = str(op["number_format"])
                if op.get("fill"):
                    color = str(op["fill"]).lstrip("#")
                    c.fill = PatternFill("solid", start_color=color, end_color=color)
                n += 1
        return f"Formatted {n} cell{'s' if n != 1 else ''} on {ws.title}"

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
        from openpyxl.chart import BarChart, LineChart, PieChart, Reference
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
        chart = {"line": LineChart, "pie": PieChart}.get(kind, BarChart)()
        chart.title = str(op.get("title") or ws.cell(head, vc).value or "Chart")
        chart.add_data(Reference(ws, min_col=vc, min_row=head, max_row=last), titles_from_data=True)
        chart.set_categories(Reference(ws, min_col=lc, min_row=head + 1, max_row=last))
        ws.add_chart(chart, f"{get_column_letter(ws.max_column + 2)}{head}")
        return f"Added a {kind} chart of {ws.cell(head, vc).value or 'the values'} on {ws.title}"

    def op_add_sheet(self, op: dict[str, Any]) -> str:
        ws = self.wb.create_sheet(str(op.get("name") or "Sheet")[:31])
        for row in op.get("rows") or []:
            ws.append(list(row))
        return f"Added sheet {ws.title}"

    def save(self) -> Path:
        out = unique_path(
            out_dir_for(self.path), f"{self.path.stem} (edited by Pyxi){self.path.suffix}"
        )
        self.wb.save(out)
        return out


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


def _rgb(color: Any) -> tuple[float, float, float] | None:
    """'#1A73E8', 'red', 'blue' -> 0-1 floats."""
    names = {
        "red": "#D93025",
        "blue": "#1A73E8",
        "green": "#188038",
        "orange": "#E8710A",
        "purple": "#8E24AA",
        "black": "#000000",
        "gray": "#5F6368",
        "grey": "#5F6368",
        "yellow": "#FBBC04",
        "white": "#FFFFFF",
    }
    c = names.get(str(color or "").strip().lower(), str(color or "").strip())
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", c)
    if not m:
        return None
    h = m.group(1)
    return tuple(int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


# ============================================================================ Word
def _move_after(new_el, anchor_el) -> None:
    anchor_el.addnext(new_el)


class WordDoc:
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
        for t, table in enumerate(self.doc.tables[:5]):
            first = " | ".join(c.text for c in table.rows[0].cells) if table.rows else ""
            out.append(f"Table {t}: {len(table.rows)} rows; first row: {first[:200]}")
        return "\n".join(out)

    def _anchor(self, op: dict[str, Any]):
        after = op.get("after")
        if after is None or after == "" or after == "end":
            return None
        try:
            return self.paras[int(after)]
        except (ValueError, IndexError):
            raise LedgerError(f"There's no paragraph {after}.") from None

    def _place(self, new_el, anchor, chain: list) -> None:
        """Put a new block after the anchor (or after the last block placed in this op)."""
        target = chain[-1] if chain else (anchor._p if anchor is not None else None)
        if target is not None:
            _move_after(new_el, target)
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
class Deck:
    kind = "pptx"

    def __init__(self, path: Path) -> None:
        from pptx import Presentation

        self.path = path
        self.prs = Presentation(str(path))

    def describe(self) -> str:
        out = [f"PowerPoint {self.path.name}: {len(self.prs.slides)} slides."]
        for i, slide in enumerate(self.prs.slides, 1):
            texts = []
            for s, shape in enumerate(slide.shapes):
                if shape.has_text_frame and shape.text_frame.text.strip():
                    kind = "title" if shape == slide.shapes.title else f"shape {s}"
                    texts.append(f"{kind}: {shape.text_frame.text.strip()[:200]!r}")
            out.append(
                f"Slide {i} ({slide.slide_layout.name}): " + ("; ".join(texts) or "(no text)")
            )
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
        layout = (
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

    from pyxi.agents.libreoffice import command
    from pyxi.agents.pixel import libreoffice

    soffice = libreoffice()
    new_ext = path.suffix.lower() + "x"
    if soffice is None:
        raise LedgerError(
            f"{path.name} is an old Office file. Pyxi can open it once the free LibreOffice converter "
            "is set up (Settings, under Your files)."
        )
    tmp = Path(tempfile.mkdtemp(prefix="pyxi_old_"))
    try:
        subprocess.run(
            command(soffice, "--convert-to", new_ext[1:], "--outdir", str(tmp), str(path)),
            check=True,
            capture_output=True,
            timeout=180,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
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
class GoogleSheet:
    """A Google Sheet, edited through the Sheets API (values with USER_ENTERED, so formulas work)."""

    kind = "gsheet"

    def __init__(self, google: Any, sheet_id: str) -> None:
        self.google = google
        self.id = sheet_id
        self.meta: dict[str, Any] = {}
        self.grids: dict[str, list[list[Any]]] = {}

    async def load(self) -> None:
        self.meta = await self.google.sheets_meta(self.id)
        for s in self.meta.get("sheets", [])[:6]:
            title = s["properties"]["title"]
            self.grids[title] = await self.google.sheets_values(
                self.id, f"'{title}'!A1:Z500", formulas=True
            )

    @property
    def title(self) -> str:
        return self.meta.get("properties", {}).get("title", "Sheet")

    @property
    def url(self) -> str:
        return f"https://docs.google.com/spreadsheets/d/{self.id}/edit"

    def _tab(self, name: str | None) -> tuple[str, int, list[list[Any]]]:
        sheets = self.meta.get("sheets", [])
        if not sheets:
            raise LedgerError("That spreadsheet has no sheets.")
        for s in sheets:
            p = s["properties"]
            if not name or p["title"].lower() == str(name).lower():
                return p["title"], p["sheetId"], self.grids.get(p["title"], [])
        raise LedgerError(f"There's no sheet called {name!r}.")

    def describe(self, max_rows: int = 40) -> str:
        from openpyxl.utils import get_column_letter

        out = [f"Google Sheet '{self.title}'. Tabs: {', '.join(self.grids)}."]
        for title, rows in self.grids.items():
            out.append(f"\nTab {title!r}: {len(rows)} rows.")
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

    async def apply(self, op: dict[str, Any]) -> str:
        from openpyxl.utils import get_column_letter

        name = op.get("op")
        if name == "set_cells":
            title, _, _ = self._tab(op.get("sheet"))
            cells = op.get("cells") or {}
            data = [{"range": f"'{title}'!{ref}", "values": [[v]]} for ref, v in cells.items()]
            await self.google.sheets_write(self.id, data)
            await self.load()  # a pivot or totals row in the same batch reads what's there now
            return f"Set {len(data)} cells on {title}"
        if name == "append_row":
            title, _, _ = self._tab(op.get("sheet"))
            await self.google.sheets_append(
                self.id, f"'{title}'!A1", [list(op.get("values") or [])]
            )
            await self.load()
            return f"Added a row on {title}"
        if name == "totals_row":
            title, _, rows = self._tab(op.get("sheet"))
            head = self._header(rows)
            last = len(rows)
            if last <= head + 1:
                raise LedgerError(f"{title} has no rows to total.")
            if _is_total_label((rows[-1] or [""])[0]):
                return f"{title} already ends with a total row"
            width = max(len(r) for r in rows)
            if op.get("columns"):
                cols = [
                    c for c in (self._col(rows, x, head) for x in op["columns"]) if c is not None
                ]
            else:
                cols = []
                for c in range(1, width):
                    vals = [r[c] for r in rows[head + 1 :] if c < len(r) and r[c] not in (None, "")]
                    if vals and sum(
                        1 for v in vals if _num(v) is not None or str(v).startswith("=")
                    ) >= 0.8 * len(vals):
                        cols.append(c)
            row = ["Total"] + [""] * (width - 1)
            for c in cols:
                L = get_column_letter(c + 1)
                row[c] = f"=SUM({L}{head + 2}:{L}{last})"
            await self.google.sheets_write(
                self.id, [{"range": f"'{title}'!A{last + 1}", "values": [row]}]
            )
            sums = []
            for c in cols:
                nums = [n for r in rows[head + 1 :] if c < len(r) and (n := _num(r[c])) is not None]
                if nums:
                    sums.append(
                        f"{rows[head][c] if c < len(rows[head]) else get_column_letter(c + 1)} {_shown(sum(nums))}"
                    )
            return f"Added a Total row (row {last + 1}) on {title}" + (
                f": {', '.join(sums)}" if sums else ""
            )
        if name == "pivot":
            title, sheet_id, rows = self._tab(op.get("sheet"))
            head = self._header(rows)
            g = self._col(rows, op.get("group_by") or op.get("rows"), head)
            if g is None:
                raise LedgerError("pivot needs group_by.")
            v = self._col(rows, op.get("value"), head)
            agg = str(op.get("agg") or ("sum" if v is not None else "count")).lower()
            fn = {"sum": "SUM", "count": "COUNTA", "average": "AVERAGE"}.get(agg)
            if fn is None:
                raise LedgerError("pivot agg should be sum, count or average.")
            width = max(len(r) for r in rows)
            if op.get("target"):  # they asked for it on its own tab
                target = str(op["target"])[:90]
                existing = {s["properties"]["title"] for s in self.meta.get("sheets", [])}
                name_, n = target, 2
                while name_ in existing:
                    name_, n = f"{target} {n}", n + 1
                added = await self.google.sheets_batch(
                    self.id, [{"addSheet": {"properties": {"title": name_}}}]
                )
                new_id = added["replies"][0]["addSheet"]["properties"]["sheetId"]
                at_row, at_col, where = 0, 0, f"on tab {name_}"
            else:  # beside the data, where they're already looking (a new tab goes unseen)
                new_id, at_row, at_col = sheet_id, head, width + 1
                where = f"beside the data on {title}, from {get_column_letter(width + 2)}{head + 1}"
            pivot = {
                "source": {
                    "sheetId": sheet_id,
                    "startRowIndex": head,
                    "endRowIndex": len(rows),
                    "startColumnIndex": 0,
                    "endColumnIndex": width,
                },
                "rows": [{"sourceColumnOffset": g, "showTotals": True, "sortOrder": "ASCENDING"}],
                "values": [
                    {"summarizeFunction": fn, "sourceColumnOffset": v if v is not None else g}
                ],
                "valueLayout": "HORIZONTAL",
            }
            await self.google.sheets_batch(
                self.id,
                [
                    {
                        "updateCells": {
                            "rows": [{"values": [{"pivotTable": pivot}]}],
                            "start": {"sheetId": new_id, "rowIndex": at_row, "columnIndex": at_col},
                            "fields": "pivotTable",
                        }
                    }
                ],
            )
            await self.load()  # a chart in the same batch goes beside the pivot, not under it
            g_name = rows[head][g] if g < len(rows[head]) else get_column_letter(g + 1)
            v_name = rows[head][v] if v is not None and v < len(rows[head]) else "rows"
            return f"Made a pivot table {where}: {agg} of {v_name} by {g_name}"
        if name == "chart":
            title, sheet_id, rows = self._tab(op.get("sheet"))
            if not rows:
                raise LedgerError(f"{title} is empty, so there's nothing to chart yet.")
            head = self._header(rows)
            lc = self._col(rows, op.get("labels") or "A", head) or 0
            vc = self._col(rows, op.get("values"), head)
            if vc is None:
                raise LedgerError("chart needs values: the column to plot.")
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
            # Where it reads from: the column itself, or, when a label repeats (North on several
            # rows), a pivot's totals: a plain chart draws one bar per row, never per region.
            r0, r1, dc, sc = head, last, lc, vc
            labels = [str(r[lc]) for r in rows[head + 1 : last] if len(r) > lc]
            shown = await self.google.sheets_values(self.id, f"'{title}'!A1:Z500")
            if len(set(labels)) < len(labels):
                block = _summary_block(shown, label_name, avoid=lc)
                if block is None:
                    await self.apply(
                        {"op": "pivot", "sheet": title, "group_by": label_name, "value": value_name}
                    )
                    shown = await self.google.sheets_values(self.id, f"'{title}'!A1:Z500")
                    block = _summary_block(shown, label_name, avoid=lc)
                if block is not None:
                    r0, r1, dc = block
                    sc = dc + 1

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
                        "chartType": "LINE" if kind == "line" else "COLUMN",
                        "legendPosition": "NO_LEGEND",
                        "headerCount": 1,
                        "domains": [{"domain": col(dc)}],
                        "series": [{"series": col(sc), "targetAxis": "LEFT_AXIS"}],
                    },
                }
            # Right of everything shown there (the data and a pivot's cells), so nothing is covered.
            free = max([len(r) for r in shown] + [len(r) for r in rows]) + 1
            anchor = {"sheetId": sheet_id, "rowIndex": head, "columnIndex": free}
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
        if name == "format":
            title, sheet_id, _ = self._tab(op.get("sheet"))
            grid = _a1_grid(str(op.get("range") or "A1"), sheet_id)
            fmt: dict[str, Any] = {}
            fields = []
            if op.get("bold") is not None:
                fmt["textFormat"] = {"bold": bool(op["bold"])}
                fields.append("userEnteredFormat.textFormat.bold")
            if op.get("number_format"):
                fmt["numberFormat"] = {"type": "NUMBER", "pattern": str(op["number_format"])}
                fields.append("userEnteredFormat.numberFormat")
            if not fields:
                raise LedgerError("format needs bold or number_format.")
            await self.google.sheets_batch(
                self.id,
                [
                    {
                        "repeatCell": {
                            "range": grid,
                            "cell": {"userEnteredFormat": fmt},
                            "fields": ",".join(fields),
                        }
                    }
                ],
            )
            return f"Formatted {op.get('range')} on {title}"
        if name == "add_sheet":
            title = str(op.get("name") or "Sheet")
            await self.google.sheets_batch(
                self.id, [{"addSheet": {"properties": {"title": title}}}]
            )
            if op.get("rows"):
                await self.google.sheets_write(
                    self.id, [{"range": f"'{title}'!A1", "values": op["rows"]}]
                )
            return f"Added tab {title}"
        raise LedgerError(f"Google Sheets doesn't have an edit called {name!r} here.")


def _summary_block(grid: list[list[Any]], label: str, avoid: int) -> tuple[int, int, int] | None:
    """A pivot's output in the shown grid: a header cell named like the labels column (not that
    column itself), a number column to its right, and one row per group until Grand Total.
    Returns (header row, one past the last group row, label column)."""
    for r, row in enumerate(grid[:60]):
        for c, v in enumerate(row):
            if c == avoid or c + 1 >= len(row) or str(v).strip().lower() != label.strip().lower():
                continue
            end = r + 1
            while end < len(grid) and len(grid[end]) > c + 1 and str(grid[end][c]).strip():
                if str(grid[end][c]).strip().lower() in ("grand total", "total"):
                    break
                end += 1
            if end > r + 1:
                return r, end, c
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


def _a1_grid(a1: str, sheet_id: int) -> dict[str, Any]:
    from openpyxl.utils.cell import range_boundaries

    c1, r1, c2, r2 = range_boundaries(a1.upper())
    return {
        "sheetId": sheet_id,
        "startRowIndex": r1 - 1,
        "endRowIndex": r2,
        "startColumnIndex": c1 - 1,
        "endColumnIndex": c2,
    }


class GoogleDoc:
    kind = "gdoc"

    def __init__(self, google: Any, doc_id: str) -> None:
        self.google = google
        self.id = doc_id
        self.doc: dict[str, Any] = {}

    async def load(self) -> None:
        self.doc = await self.google.docs_get(self.id)

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
        size = 0
        for i, b in enumerate(self._blocks()):
            p = b["paragraph"]
            style = p.get("paragraphStyle", {}).get("namedStyleType", "")
            text = "".join(
                e.get("textRun", {}).get("content", "") for e in p.get("elements", [])
            ).rstrip("\n")
            line = f"[{i}] ({style}) {text}"
            size += len(line)
            if size > max_chars:
                out.append("  ...")
                break
            out.append(line[:400])
        return "\n".join(out)

    def _at(self, op: dict[str, Any]) -> int:
        after = op.get("after")
        if after in (None, "", "end"):
            return self._end()
        try:
            # Just before that paragraph's own newline: always inside a paragraph, even when a
            # table comes next (the start of a table is no place to type, Google says 400).
            return int(self._blocks()[int(after)]["endIndex"]) - 1
        except (ValueError, IndexError, KeyError):
            raise LedgerError(f"There's no paragraph {after}.") from None

    async def _insert(
        self, at: int, text: str, style: str | None = None, bullets: str | None = None
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
        lead = "" if empty else "\n"
        body = lead + text
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
        await self.load()  # indexes moved

    async def apply(self, op: dict[str, Any]) -> str:
        name = op.get("op")
        if name == "add_heading":
            level = max(1, min(int(op.get("level") or 1), 6))
            text = str(op.get("text") or "").strip()
            await self._insert(self._at(op), text, f"HEADING_{level}")
            return f"Added heading '{text[:60]}'"
        if name == "add_paragraph":
            text = str(op.get("text") or "")
            await self._insert(self._at(op), text)
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
            await self._insert(self._at(op), "\n".join(items), bullets=preset)
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
            await self.load()
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
                    await self.load()
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
                await self.load()
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
            await self.load()
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
            await self.load()
            look = (
                ", ".join(k for k in ("bold", "italic", "underline") if op.get(k)) or "the new look"
            )
            shown = f"'{str(op.get('find'))[:40]}'" if op.get("find") else "the line"
            return f"Gave {shown} {look}" + (f" ({len(ranges)} places)" if len(ranges) > 1 else "")
        if name == "page_break":
            where = (
                {"location": {"index": self._at(op)}}
                if op.get("after") is not None
                else {"endOfSegmentLocation": {}}
            )
            await self.google.docs_batch(self.id, [{"insertPageBreak": where}])
            await self.load()
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
        out = []
        for b in blocks:
            elements = b["paragraph"].get("elements", [])
            if not elements:
                continue
            text = "".join(e.get("textRun", {}).get("content", "") for e in elements)
            base = int(elements[0].get("startIndex", b["startIndex"]))
            hay, needle = (text, find) if find in text else (text.lower(), find.lower())
            at = hay.find(needle)
            while at >= 0:
                out.append((base + at, base + at + len(find)))
                at = hay.find(needle, at + len(find))
        return out


# ============================================================================ the job
EDITS = """Edits you can use (each is a JSON object with "file": <file number> and "op"):
Excel (.xlsx) and Google Sheets:
- {"op": "totals_row", "sheet"?: name, "columns"?: ["B", "Amount"], "function"?: "SUM"|"AVERAGE"|"COUNT"|"MAX"|"MIN", "label"?: "Total"}  (finds the number columns and the last row itself)
- {"op": "pivot", "sheet"?: name, "group_by": column header or letter, "value"?: column, "agg": "sum"|"count"|"average", "target"?: "Pivot"}  (Google Sheets: beside the data, where they can see it; give "target" only when they asked for a new tab. Excel: on a new sheet)
- {"op": "chart", "sheet"?: name, "labels": column, "values": column, "type": "bar"|"line"|"pie", "title"?: ".."}  (placed beside the data; repeated labels are summed)
- {"op": "set_cells", "sheet"?: name, "cells": {"B5": 120, "C5": "=B5*1.13"}}
- {"op": "append_row", "sheet"?: name, "values": [..]}
- {"op": "format", "sheet"?: name, "range": "A1:D1", "bold"?: true, "number_format"?: "$#,##0.00", "fill"?: "#FFF2CC"}
- {"op": "add_sheet", "name": "Summary", "rows"?: [[..], [..]]}
Excel only:
- {"op": "add_column", "sheet"?: name, "header": "Total", "formula": "=B{r}*C{r}"}  ({r} is each data row)
- {"op": "make_table", "sheet"?: name}
Word (.docx) and Google Docs ("after" is a paragraph number from the outline; leave it out for the end):
- {"op": "add_heading", "text": "..", "level": 1, "after"?: 3}
- {"op": "add_paragraph", "text": "..", "after"?: 3}
- {"op": "add_bullets", "items": [".."], "numbered"?: false, "after"?: 3}
- {"op": "replace_text", "find": "..", "replace": ".."}
- {"op": "add_table", "rows": [["Header", ".."], ["..", ".."]], "after"?: 3}  (or "n_rows" and "n_cols" for an empty table of that size)
- {"op": "set_style", "i": 4, "style": "Heading 2"|"Title"|"Subtitle"|"Normal"}
- {"op": "delete_paragraph", "i": 4}
- {"op": "format_text", "find"?: "exact words" | "i"?: 4, "bold"?: true, "italic"?: true, "underline"?: true, "strikethrough"?: true, "size"?: 14, "color"?: "#1A73E8"|"red", "highlight"?: "yellow", "font"?: "Arial", "link"?: "https://.."}  (find formats every place the words appear; i a whole paragraph)
- {"op": "align", "i": 4, "align": "left"|"center"|"right"|"justify"}
- {"op": "page_break", "after"?: 3}
PowerPoint (.pptx) (slides are numbered from 1):
- {"op": "add_slide", "title": "..", "bullets"?: [".."], "after"?: 2, "notes"?: ".."}
- {"op": "set_title", "slide": 2, "text": ".."}
- {"op": "add_bullets", "slide": 2, "items": [".."], "replace"?: false}
- {"op": "replace_text", "find": "..", "replace": ".."}
- {"op": "delete_slide", "slide": 3}
- {"op": "set_notes", "slide": 2, "text": ".."}"""

SYSTEM = (
    "You are {name}, the documents specialist on the user's team. {persona} You edit spreadsheets, documents "
    "and decks by returning precise edits; a program applies them exactly. Read the outline carefully: use the "
    "real sheet names, column headers, cell addresses and paragraph numbers you see. Prefer the high-level edits "
    "(totals_row, pivot) over hand-written cells. Write formulas, not hard-coded results, so the file stays live. "
    "Use as few edits as the task needs, never invent data, and no em dashes in any text you write.\n\n"
    + EDITS
    + '\n\nReturn JSON only: {"edits": [..], "summary": "<one short sentence for the user>"}. If the task can\'t be '
    'done on these files, return {"edits": [], "summary": "<why, in one sentence>"}.'
)


@dataclass
class LedgerResult:
    outputs: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    changes: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    summary: str = ""
    cost_usd: float = 0.0


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
    result = target.apply(op)
    return await result if asyncio.iscoroutine(result) else result


Ask = Callable[[str, str], Awaitable[tuple[str, float]]]


async def run_ledger(
    task: str,
    *,
    files: list[str],
    google: Any = None,
    google_refs: list[str] | None = None,
    ask: Ask,
    name: str = "Ledger",
    persona: str = "",
    context: str = "",
    progress: Callable[[str], Awaitable[None]] | None = None,
) -> LedgerResult:
    """Open the targets, ask the model for edits once (and once more to fix any that failed),
    apply them, save copies. ``ask(system, prompt)`` returns (text, cost)."""
    targets: list[Any] = []
    for f in files:
        targets.append(await asyncio.to_thread(open_local, Path(f)))
    for ref in google_refs or []:
        if google is None or not google.connected():
            raise LedgerError("Google isn't connected. Connect it in Settings, then ask again.")
        targets.append(await google.open_for_edit(ref))
    if not targets:
        raise LedgerError(
            "Which file? Give me the path of the Excel, Word or PowerPoint file, or the Google Sheet or Doc."
        )
    for t in targets:
        if hasattr(t, "load"):
            await t.load()
    outline = "\n\n".join(f"# File {i}\n{t.describe()}" for i, t in enumerate(targets))
    prompt = f"Task: {task}\n\n{outline}"
    if context:
        prompt = f"Context from your teammates:\n{context[:4000]}\n\n{prompt}"
    system = SYSTEM.replace("{name}", name).replace("{persona}", persona)
    result = LedgerResult()
    changed: set[int] = set()
    tried = False
    pending = prompt
    for attempt in range(2):
        text, cost = await ask(system, pending)
        result.cost_usd += cost
        data = parse_edits(text)
        edits = [e for e in data.get("edits") or [] if isinstance(e, dict)]
        if attempt == 1 and not edits:
            break  # nothing more it can do: keep the first round's problems
        if data.get("summary") and attempt == 0:
            result.summary = str(data["summary"]).strip()
        tried = tried or bool(edits)
        failed: list[str] = []  # for the model: the edit itself and why
        reasons: list[str] = []  # for the user's card: just why
        for e in edits:
            try:
                index = int(e.get("file") or 0)
                target = targets[index]
            except (ValueError, IndexError):
                failed.append(f"{json.dumps(e)[:200]}: there's no file {e.get('file')}")
                reasons.append(f"There's no file {e.get('file')}.")
                continue
            if progress:
                await progress(f"{e.get('op', 'edit').replace('_', ' ')}")
            try:
                result.changes.append(await _apply(target, e))
                changed.add(index)
            except LedgerError as exc:
                failed.append(f"{json.dumps(e)[:200]}: {exc}")
                reasons.append(str(exc))
            except Exception as exc:  # a bad cell address, a style the file lacks...
                failed.append(f"{json.dumps(e)[:200]}: {type(exc).__name__}: {str(exc)[:160]}")
                what = str(e.get("op") or "edit").replace("_", " ")
                reasons.append(f"The {what} didn't work ({str(exc)[:120]}).")
        result.problems = reasons
        if not failed:
            break
        if attempt == 0:
            outline = "\n\n".join(f"# File {i}\n{t.describe()}" for i, t in enumerate(targets))
            pending = (
                f"Task: {task}\n\nThese edits failed:\n"
                + "\n".join(failed)
                + f"\n\nThe files now (with the edits that worked):\n{outline}\n\nReturn only the edits still needed, or none if one can't be done."
            )
    for i, t in enumerate(targets):
        if i not in changed:
            continue
        if hasattr(t, "save"):
            result.outputs.append(str(await asyncio.to_thread(t.save)))
        else:
            result.links.append(t.url)
    if not result.changes and (tried or not result.summary):
        # The model's summary was written before its edits ran ("Added a total row"): when
        # none of them worked, say why instead.
        why = f" {result.problems[0]}" if result.problems else ""
        result.summary = f"I couldn't make that change.{why}"
    return result
