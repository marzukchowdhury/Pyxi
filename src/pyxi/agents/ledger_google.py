"""The rest of what a person does in Google Sheets, Docs and Slides, for Ledger.

``ledger.py`` holds the edits Ledger started with (totals, pivots, charts, headings, bullets).
This adds the wider set: tables in a Doc, moving a chart, sorting and filtering, conditional
formats, page setup, and Google Slides as a whole. Each ``x_<name>`` method is one edit; the
classes in ``ledger.py`` look here first. ``raw`` passes Google's own batchUpdate requests
through, so anything their API can do is reachable even when no edit here names it.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from pyxi.agents.ledger_common import (
    LedgerError,
    a1_bounds,
    a1_grid,
    cell_rc,
    col_letter,
    col_number,
    rgb_json,
    table_cells,
)

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


def _n(value: Any, what: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        raise LedgerError(f"{what} should be a number, not {value!r}.") from None


def _pt(value: float) -> dict[str, Any]:
    return {"magnitude": round(float(value), 2), "unit": "PT"}


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _units(text: str) -> int:
    """Length the way Google counts it (UTF-16 units: an emoji is two)."""
    return sum(2 if ord(ch) > 0xFFFF else 1 for ch in text)


# ============================================================================ Sheets
_WHEN = {
    "greater than": "NUMBER_GREATER",
    "more than": "NUMBER_GREATER",
    "above": "NUMBER_GREATER",
    "greater or equal": "NUMBER_GREATER_THAN_EQ",
    "at least": "NUMBER_GREATER_THAN_EQ",
    "less than": "NUMBER_LESS",
    "below": "NUMBER_LESS",
    "less or equal": "NUMBER_LESS_THAN_EQ",
    "at most": "NUMBER_LESS_THAN_EQ",
    "between": "NUMBER_BETWEEN",
    "not equal": "NUMBER_NOT_EQ",
    "contains": "TEXT_CONTAINS",
    "text contains": "TEXT_CONTAINS",
    "not contains": "TEXT_NOT_CONTAINS",
    "starts with": "TEXT_STARTS_WITH",
    "ends with": "TEXT_ENDS_WITH",
    "empty": "BLANK",
    "blank": "BLANK",
    "not empty": "NOT_BLANK",
    "formula": "CUSTOM_FORMULA",
}
_CHART_TYPES = {
    "bar": "COLUMN",
    "column": "COLUMN",
    "horizontal bar": "BAR",
    "line": "LINE",
    "area": "AREA",
    "scatter": "SCATTER",
    "combo": "COMBO",
}


def _color_style(color: Any, what: str = "color") -> dict[str, Any]:
    return {"rgbColor": rgb_json(color, what)}


def sheet_format(op: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """A Sheets CellFormat and its field mask from an edit's plain words."""
    fmt: dict[str, Any] = {}
    fields: list[str] = []
    text: dict[str, Any] = {}
    for key in ("bold", "italic", "underline", "strikethrough"):
        if op.get(key) is not None:
            text[key] = bool(op[key])
            fields.append(f"userEnteredFormat.textFormat.{key}")
    if op.get("size"):
        text["fontSize"] = int(_n(op["size"], "size"))
        fields.append("userEnteredFormat.textFormat.fontSize")
    if op.get("font"):
        text["fontFamily"] = str(op["font"])
        fields.append("userEnteredFormat.textFormat.fontFamily")
    if op.get("color"):
        text["foregroundColorStyle"] = _color_style(op["color"])
        fields.append("userEnteredFormat.textFormat.foregroundColorStyle")
    if text:
        fmt["textFormat"] = text
    if op.get("fill"):
        fmt["backgroundColorStyle"] = _color_style(op["fill"], "fill")
        fields.append("userEnteredFormat.backgroundColorStyle")
    if op.get("number_format"):
        pattern = str(op["number_format"])
        dated = bool(re.search(r"[yYdD]|mmm", pattern)) and not re.search(r"[#0]", pattern)
        kind = "DATE" if dated else "PERCENT" if "%" in pattern else "NUMBER"
        fmt["numberFormat"] = {"type": kind, "pattern": pattern}
        fields.append("userEnteredFormat.numberFormat")
    if op.get("align"):
        how = {"START": "LEFT", "END": "RIGHT"}.get(
            _ALIGN.get(str(op["align"]).lower(), ""), _ALIGN.get(str(op["align"]).lower())
        )
        if how not in ("LEFT", "CENTER", "RIGHT"):
            raise LedgerError("align should be left, center or right.")
        fmt["horizontalAlignment"] = how
        fields.append("userEnteredFormat.horizontalAlignment")
    if op.get("valign"):
        how = {"top": "TOP", "middle": "MIDDLE", "center": "MIDDLE", "bottom": "BOTTOM"}.get(
            str(op["valign"]).lower()
        )
        if how is None:
            raise LedgerError("valign should be top, middle or bottom.")
        fmt["verticalAlignment"] = how
        fields.append("userEnteredFormat.verticalAlignment")
    if op.get("wrap") is not None:
        fmt["wrapStrategy"] = "WRAP" if op["wrap"] else "OVERFLOW_CELL"
        fields.append("userEnteredFormat.wrapStrategy")
    return fmt, fields


class SheetExtras:
    """Mixed into ``ledger.GoogleSheet``."""

    # ---------------------------------------------------------------- where
    def _grid(self, op: dict[str, Any], key: str = "range", *, whole: bool = False):
        """(tab title, tab id, GridRange, the tab's rows) for an edit's range. The range may
        name its tab ('Summary'!A1:B4); with ``whole``, no range means the whole tab."""
        raw = str(op.get(key) or "").strip()
        sheet = op.get("sheet")
        if "!" in raw:
            tab, raw = raw.rsplit("!", 1)
            sheet = tab.strip().strip("'")
        title, sid, rows = self._tab(sheet)
        if not raw:
            if not whole:
                raise LedgerError(f"{op.get('op')} needs a range like A1:D10.")
            return title, sid, {"sheetId": sid}, rows
        return title, sid, a1_grid(raw, sid), rows

    def _data(self, op: dict[str, Any]):
        """The data under the header row (a total row at the bottom left out), for edits that
        work on "the table" when no range is given."""
        title, sid, rows = self._tab(op.get("sheet"))
        if op.get("range"):
            _, _, grid, _ = self._grid(op)
            return title, sid, grid, rows, self._header(rows)
        head = self._header(rows)
        last = len(rows)
        while last > head + 1 and str((rows[last - 1] or [""])[0]).strip().lower() in (
            "total",
            "totals",
            "grand total",
            "sum",
        ):
            last -= 1
        width = max((len(r) for r in rows), default=1)
        grid = {
            "sheetId": sid,
            "startRowIndex": head + 1,
            "endRowIndex": max(last, head + 1),
            "startColumnIndex": 0,
            "endColumnIndex": width,
        }
        return title, sid, grid, rows, head

    def _charts(self) -> list[dict[str, Any]]:
        out = []
        for s in self.meta.get("sheets", []):
            for c in s.get("charts") or []:
                anchor = c.get("position", {}).get("overlayPosition", {}).get("anchorCell", {})
                out.append(
                    {
                        "id": c.get("chartId"),
                        "title": c.get("spec", {}).get("title", ""),
                        "tab": s["properties"]["title"],
                        "at": f"{col_letter(int(anchor.get('columnIndex', 0)) + 1)}{int(anchor.get('rowIndex', 0)) + 1}",
                    }
                )
        return out

    def chart_lines(self, tab: str) -> list[str]:
        """For the outline: the charts on a tab, so "move the chart" has something to name."""
        return [
            f"  Chart id {c['id']} {c['title']!r}, its top left corner at {c['at']}"
            for c in self._charts()
            if c["tab"] == tab
        ]

    def _chart(self, op: dict[str, Any]) -> dict[str, Any]:
        charts = self._charts()
        if not charts:
            raise LedgerError("There's no chart on this spreadsheet yet.")
        want = op.get("chart")
        if want in (None, ""):
            return charts[-1]
        for c in charts:
            if str(c["id"]) == str(want).strip():
                return c
        named = [c for c in charts if str(want).strip().lower() in str(c["title"]).lower()]
        if named:
            return named[0]
        raise LedgerError(
            f"There's no chart {want!r}; the charts are {', '.join(repr(c['title']) or str(c['id']) for c in charts)}."
        )

    def _dimension(self, op: dict[str, Any], key: str, dimension: str) -> dict[str, Any]:
        """A row or column span from "from"/"to", "at"/"count", or an "A:D" / "2:9" string."""
        _, sid, _ = self._tab(op.get("sheet"))
        raw = op.get(key)

        def index(v: Any) -> int:
            s = str(v).strip()
            if s.isdigit():
                return int(s)
            if dimension == "COLUMNS" and re.fullmatch(r"[A-Za-z]{1,3}", s):
                return col_number(s)
            raise LedgerError(f"{v!r} isn't a {'column' if dimension == 'COLUMNS' else 'row'}.")

        if isinstance(raw, str) and ":" in raw:
            a, b = raw.split(":", 1)
            start, end = index(a), index(b)
        elif raw not in (None, ""):
            start = index(raw)
            end = index(op["to"]) if op.get("to") not in (None, "") else start
            if op.get("count"):
                end = start + int(op["count"]) - 1
        else:
            raise LedgerError(f"{op.get('op')} needs {key}.")
        if end < start:
            start, end = end, start
        return {"sheetId": sid, "dimension": dimension, "startIndex": start - 1, "endIndex": end}

    async def _batch(self, *requests: dict[str, Any]) -> Any:
        return await self.google.sheets_batch(self.id, list(requests))

    # ---------------------------------------------------------------- charts
    async def x_move_chart(self, op: dict[str, Any]) -> str:
        chart = self._chart(op)
        if op.get("own_sheet"):
            await self._batch(
                {
                    "updateEmbeddedObjectPosition": {
                        "objectId": chart["id"],
                        "newPosition": {"newSheet": True},
                        "fields": "*",
                    }
                }
            )
            return f"Moved the chart {chart['title']!r} to a tab of its own"
        pos: dict[str, Any] = {}
        fields = []
        said = []
        if op.get("to"):
            raw = str(op["to"])
            sheet = op.get("sheet") or chart["tab"]
            if "!" in raw:
                tab, raw = raw.rsplit("!", 1)
                sheet = tab.strip().strip("'")
            title, sid, _ = self._tab(sheet)
            r, c = cell_rc(raw)
            pos["anchorCell"] = {"sheetId": sid, "rowIndex": r - 1, "columnIndex": c - 1}
            fields.append("anchorCell")
            said.append(f"to {raw.upper()} on {title}")
        for key, api in (("width", "widthPixels"), ("height", "heightPixels")):
            if op.get(key):
                pos[api] = int(_n(op[key], key))
                fields.append(api)
        if "widthPixels" in pos or "heightPixels" in pos:
            said.append("and resized it" if said else "to a new size")
        if not fields:
            raise LedgerError('move_chart needs "to" (a cell like H2) or a width and height.')
        await self._batch(
            {
                "updateEmbeddedObjectPosition": {
                    "objectId": chart["id"],
                    "newPosition": {"overlayPosition": pos},
                    "fields": ",".join(fields),
                }
            }
        )
        return f"Moved the chart {chart['title']!r} {' '.join(said)}"

    async def x_delete_chart(self, op: dict[str, Any]) -> str:
        chart = self._chart(op)
        await self._batch({"deleteEmbeddedObject": {"objectId": chart["id"]}})
        return f"Removed the chart {chart['title']!r}"

    async def x_edit_chart(self, op: dict[str, Any]) -> str:
        chart = self._chart(op)
        full = await self.google.sheets_charts(self.id)
        spec = next((c.get("spec") for c in full if c.get("chartId") == chart["id"]), None)
        if spec is None:
            raise LedgerError("That chart isn't there any more.")
        said = []
        if op.get("title"):
            spec["title"] = str(op["title"])[:100]
            said.append(f"titled it {spec['title']!r}")
        if op.get("type"):
            kind = _CHART_TYPES.get(str(op["type"]).lower())
            if kind is None or "basicChart" not in spec:
                raise LedgerError(
                    "A chart's type can change between bar, horizontal bar, line, area and "
                    "scatter. For a pie, delete it and add a pie chart."
                )
            spec["basicChart"]["chartType"] = kind
            said.append(f"made it a {str(op['type']).lower()} chart")
        if op.get("legend"):
            where = (
                f"{str(op['legend']).upper()}_LEGEND"
                if str(op["legend"]).lower() != "none"
                else "NO_LEGEND"
            )
            (spec.get("basicChart") or spec.get("pieChart") or {})["legendPosition"] = where
            said.append("set its legend")
        if not said:
            raise LedgerError("edit_chart needs a title, type or legend.")
        await self._batch({"updateChartSpec": {"chartId": chart["id"], "spec": spec}})
        return f"Chart {chart['title']!r}: " + " and ".join(said)

    # ---------------------------------------------------------------- rows and columns
    async def x_sort(self, op: dict[str, Any]) -> str:
        title, _, grid, rows, head = self._data(op)
        specs = []
        names = []
        for key in ("by", "then_by"):
            if op.get(key) in (None, ""):
                continue
            col = self._col(rows, op[key], head)
            down = bool(op.get("descending")) if key == "by" else bool(op.get("then_descending"))
            specs.append(
                {"dimensionIndex": col, "sortOrder": "DESCENDING" if down else "ASCENDING"}
            )
            names.append(str(op[key]))
        if not specs:
            raise LedgerError('sort needs "by": the column to sort on.')
        await self._batch({"sortRange": {"range": grid, "sortSpecs": specs}})
        order = "largest first" if op.get("descending") else "smallest first"
        return f"Sorted {title} by {' then '.join(names)}, {order}"

    async def x_filter(self, op: dict[str, Any]) -> str:
        title, sid, grid, _, head = self._data(op)
        if op.get("off"):
            await self._batch({"clearBasicFilter": {"sheetId": sid}})
            return f"Turned the filter off on {title}"
        if not op.get("range"):
            grid = {**grid, "startRowIndex": head}  # the filter buttons sit on the header row
        await self._batch({"setBasicFilter": {"filter": {"range": grid}}})
        return f"Turned on filter buttons on {title}"

    async def x_freeze(self, op: dict[str, Any]) -> str:
        title, sid, _ = self._tab(op.get("sheet"))
        cols = int(op.get("columns") or 0)
        rows = int(op["rows"]) if op.get("rows") is not None else (0 if cols else 1)
        await self._batch(
            {
                "updateSheetProperties": {
                    "properties": {
                        "sheetId": sid,
                        "gridProperties": {"frozenRowCount": rows, "frozenColumnCount": cols},
                    },
                    "fields": "gridProperties.frozenRowCount,gridProperties.frozenColumnCount",
                }
            }
        )
        if not rows and not cols:
            return f"Unfroze {title}"
        parts = ([f"top {_plural(rows, 'row')}"] if rows else []) + (
            [f"first {_plural(cols, 'column')}"] if cols else []
        )
        return f"Froze the {' and the '.join(parts)} on {title}"

    async def _insert(self, op: dict[str, Any], dimension: str) -> str:
        span = self._dimension({**op, "to": None}, "at", dimension)
        count = max(1, int(op.get("count") or 1))
        span["endIndex"] = span["startIndex"] + count
        await self._batch(
            {"insertDimension": {"range": span, "inheritFromBefore": span["startIndex"] > 0}}
        )
        what = "row" if dimension == "ROWS" else "column"
        return f"Inserted {_plural(count, what)} at {op.get('at')}"

    async def x_insert_rows(self, op: dict[str, Any]) -> str:
        return await self._insert(op, "ROWS")

    async def x_insert_columns(self, op: dict[str, Any]) -> str:
        return await self._insert(op, "COLUMNS")

    async def _delete(self, op: dict[str, Any], dimension: str) -> str:
        span = self._dimension(op, "from", dimension)
        await self._batch({"deleteDimension": {"range": span}})
        what = "row" if dimension == "ROWS" else "column"
        return f"Deleted {_plural(span['endIndex'] - span['startIndex'], what)}"

    async def x_delete_rows(self, op: dict[str, Any]) -> str:
        return await self._delete(op, "ROWS")

    async def x_delete_columns(self, op: dict[str, Any]) -> str:
        return await self._delete(op, "COLUMNS")

    async def x_resize(self, op: dict[str, Any]) -> str:
        done = []
        reqs = []
        for key, dimension, size in (("columns", "COLUMNS", "width"), ("rows", "ROWS", "height")):
            if op.get(key) in (None, ""):
                continue
            span = self._dimension({**op, "to": None, "count": None}, key, dimension)
            if op.get(size):
                reqs.append(
                    {
                        "updateDimensionProperties": {
                            "range": span,
                            "properties": {"pixelSize": int(_n(op[size], size))},
                            "fields": "pixelSize",
                        }
                    }
                )
                done.append(f"{key} {op[key]} to {int(_n(op[size], size))} px")
            else:
                reqs.append({"autoResizeDimensions": {"dimensions": span}})
                done.append(f"{key} {op[key]} to fit")
        if not reqs:
            raise LedgerError('resize needs "columns" (like "A:D") or "rows" (like "1:5").')
        await self._batch(*reqs)
        return "Resized " + " and ".join(done)

    async def x_merge(self, op: dict[str, Any]) -> str:
        _, _, grid, _ = self._grid(op)
        if op.get("unmerge"):
            await self._batch({"unmergeCells": {"range": grid}})
            return f"Unmerged {op.get('range')}"
        await self._batch({"mergeCells": {"range": grid, "mergeType": "MERGE_ALL"}})
        return f"Merged {op.get('range')}"

    # ---------------------------------------------------------------- looks
    async def x_format(self, op: dict[str, Any]) -> str:
        title, _, grid, _ = self._grid(op)
        fmt, fields = sheet_format(op)
        reqs: list[dict[str, Any]] = []
        if fields:
            reqs.append(
                {
                    "repeatCell": {
                        "range": grid,
                        "cell": {"userEnteredFormat": fmt},
                        "fields": ",".join(fields),
                    }
                }
            )
        if op.get("border"):
            line: dict[str, Any] = {"style": "SOLID"}
            if op.get("border_color"):
                line["colorStyle"] = _color_style(op["border_color"], "border colour")
            sides = (
                ("top", "bottom", "left", "right")
                if str(op["border"]).lower() == "outer"
                else ("top", "bottom", "left", "right", "innerHorizontal", "innerVertical")
            )
            reqs.append({"updateBorders": {"range": grid, **dict.fromkeys(sides, line)}})
        if not reqs:
            raise LedgerError(
                "format needs something to change: bold, italic, size, color, fill, "
                "number_format, align, wrap or border."
            )
        await self._batch(*reqs)
        return f"Formatted {op.get('range')} on {title}"

    async def x_conditional_format(self, op: dict[str, Any]) -> str:
        title, _, grid, _ = self._grid(op)
        when = str(op.get("when") or "").strip().lower()
        if when in ("color scale", "colour scale", "heat map", "heatmap"):
            rule: dict[str, Any] = {
                "gradientRule": {
                    "minpoint": {
                        "colorStyle": _color_style(op.get("min_color") or "#FFFFFF"),
                        "type": "MIN",
                    },
                    "maxpoint": {
                        "colorStyle": _color_style(op.get("max_color") or "#57BB8A"),
                        "type": "MAX",
                    },
                }
            }
            said = "a colour scale"
        else:
            value = op.get("value")
            is_number = isinstance(value, int | float) or bool(
                re.fullmatch(r"-?\d+(\.\d+)?", str(value or ""))
            )
            kind = _WHEN.get(when)
            if when in ("equals", "equal to", "is"):
                kind = "NUMBER_EQ" if is_number else "TEXT_EQ"
            if kind is None:
                raise LedgerError(
                    f"I don't know the rule {when!r}. Use one of: greater than, less than, "
                    "between, equals, not equal, contains, starts with, ends with, empty, "
                    "not empty, formula, color scale."
                )
            values = [v for v in (value, op.get("value2")) if v not in (None, "")]
            fmt, _ = sheet_format({k: op.get(k) for k in ("bold", "italic", "color", "fill")})
            if not fmt:
                fmt, _ = sheet_format({"fill": "light yellow"})
            rule = {
                "booleanRule": {
                    "condition": {
                        "type": kind,
                        "values": [{"userEnteredValue": str(v)} for v in values],
                    },
                    "format": fmt,
                }
            }
            said = f"cells {when}" + (f" {' and '.join(str(v) for v in values)}" if values else "")
        await self._batch(
            {"addConditionalFormatRule": {"rule": {"ranges": [grid], **rule}, "index": 0}}
        )
        return f"Highlighted {said} in {op.get('range')} on {title}"

    async def x_dropdown(self, op: dict[str, Any]) -> str:
        title, _, grid, _ = self._grid(op)
        if op.get("off"):
            await self._batch({"setDataValidation": {"range": grid}})
            return f"Removed the dropdown from {op.get('range')}"
        options = [str(o) for o in op.get("options") or [] if str(o).strip()]
        if op.get("from_range"):
            src = str(op["from_range"])
            condition = {
                "type": "ONE_OF_RANGE",
                "values": [{"userEnteredValue": src if src.startswith("=") else f"={src}"}],
            }
        elif options:
            condition = {
                "type": "ONE_OF_LIST",
                "values": [{"userEnteredValue": o} for o in options],
            }
        else:
            raise LedgerError("dropdown needs options (the choices).")
        await self._batch(
            {
                "setDataValidation": {
                    "range": grid,
                    "rule": {"condition": condition, "showCustomUi": True, "strict": True},
                }
            }
        )
        return f"Added a dropdown to {op.get('range')} on {title}"

    async def x_checkbox(self, op: dict[str, Any]) -> str:
        title, _, grid, _ = self._grid(op)
        await self._batch(
            {
                "setDataValidation": {
                    "range": grid,
                    "rule": {"condition": {"type": "BOOLEAN"}, "showCustomUi": True},
                }
            }
        )
        return f"Added checkboxes to {op.get('range')} on {title}"

    async def x_banding(self, op: dict[str, Any]) -> str:
        title, _, grid, _, head = self._data(op)
        if not op.get("range"):
            grid = {**grid, "startRowIndex": head}
        await self._batch(
            {
                "addBanding": {
                    "bandedRange": {
                        "range": grid,
                        "rowProperties": {
                            "headerColorStyle": _color_style(op.get("header") or "#BDBDBD"),
                            "firstBandColorStyle": _color_style("#FFFFFF"),
                            "secondBandColorStyle": _color_style(op.get("color") or "#F3F3F3"),
                        },
                    }
                }
            }
        )
        return f"Added alternating row colours on {title}"

    # ---------------------------------------------------------------- cells
    async def x_clear(self, op: dict[str, Any]) -> str:
        title, _, grid, _ = self._grid(op)
        what = str(op.get("what") or "values").lower()
        fields = {
            "values": "userEnteredValue",
            "formats": "userEnteredFormat",
            "format": "userEnteredFormat",
            "all": "userEnteredValue,userEnteredFormat",
        }.get(what)
        if fields is None:
            raise LedgerError("clear's what should be values, formats or all.")
        await self._batch({"updateCells": {"range": grid, "fields": fields}})
        return f"Cleared {op.get('range')} on {title}"

    async def x_find_replace(self, op: dict[str, Any]) -> str:
        find = str(op.get("find") or "")
        if not find:
            raise LedgerError("find_replace needs the text to find.")
        req: dict[str, Any] = {
            "find": find,
            "replacement": str(op.get("replace") or ""),
            "matchCase": bool(op.get("match_case")),
            "matchEntireCell": bool(op.get("whole_cell")),
            "includeFormulas": bool(op.get("formulas")),
        }
        if op.get("sheet"):
            req["sheetId"] = self._tab(op["sheet"])[1]
        else:
            req["allSheets"] = True
        r = await self._batch({"findReplace": req})
        n = (
            ((r or {}).get("replies") or [{}])[0]
            .get("findReplace", {})
            .get("occurrencesChanged", 0)
        )
        if not n:
            raise LedgerError(f"'{find[:40]}' isn't in the spreadsheet.")
        return f"Replaced '{find[:40]}' {_plural(int(n), 'time')}"

    async def x_copy(self, op: dict[str, Any]) -> str:
        _, _, source, _ = self._grid(op, "from")
        _, dest_id, dest, _ = self._grid(op, "to")
        paste = "PASTE_VALUES" if op.get("values_only") else "PASTE_NORMAL"
        if op.get("cut"):
            cell = {
                "sheetId": dest_id,
                "rowIndex": dest.get("startRowIndex", 0),
                "columnIndex": dest.get("startColumnIndex", 0),
            }
            await self._batch(
                {"cutPaste": {"source": source, "destination": cell, "pasteType": paste}}
            )
            return f"Moved {op.get('from')} to {op.get('to')}"
        await self._batch(
            {"copyPaste": {"source": source, "destination": dest, "pasteType": paste}}
        )
        return f"Copied {op.get('from')} to {op.get('to')}"

    async def x_note(self, op: dict[str, Any]) -> str:
        _, _, grid, _ = self._grid(op, "cell")
        await self._batch(
            {
                "updateCells": {
                    "range": grid,
                    "rows": [{"values": [{"note": str(op.get("text") or "")}]}],
                    "fields": "note",
                }
            }
        )
        return f"Left a note on {op.get('cell')}"

    async def x_remove_duplicates(self, op: dict[str, Any]) -> str:
        title, sid, grid, rows, head = self._data(op)
        req: dict[str, Any] = {"range": grid}
        if op.get("columns"):
            req["comparisonColumns"] = [
                {"sheetId": sid, "dimension": "COLUMNS", "startIndex": c, "endIndex": c + 1}
                for c in (self._col(rows, x, head) for x in op["columns"])
                if c is not None
            ]
        r = await self._batch({"deleteDuplicates": req})
        n = (
            ((r or {}).get("replies") or [{}])[0]
            .get("deleteDuplicates", {})
            .get("duplicatesRemovedCount", 0)
        )
        return f"Removed {_plural(int(n), 'duplicate row')} on {title}"

    async def x_trim(self, op: dict[str, Any]) -> str:
        title, _, grid, _ = self._grid(op, whole=True)
        await self._batch({"trimWhitespace": {"range": grid}})
        return f"Trimmed extra spaces on {title}"

    # ---------------------------------------------------------------- tabs and the file
    async def x_rename_sheet(self, op: dict[str, Any]) -> str:
        title, sid, _ = self._tab(op.get("sheet"))
        new = str(op.get("name") or "").strip()[:100]
        if not new:
            raise LedgerError("rename_sheet needs the new name.")
        await self._batch(
            {
                "updateSheetProperties": {
                    "properties": {"sheetId": sid, "title": new},
                    "fields": "title",
                }
            }
        )
        return f"Renamed the tab {title} to {new}"

    async def x_delete_sheet(self, op: dict[str, Any]) -> str:
        if not op.get("sheet"):
            raise LedgerError("delete_sheet needs the tab's name.")
        title, sid, _ = self._tab(op.get("sheet"))
        if len(self.meta.get("sheets", [])) < 2:
            raise LedgerError(f"{title} is the only tab, and a spreadsheet needs one.")
        await self._batch({"deleteSheet": {"sheetId": sid}})
        self.grids.pop(title, None)
        return f"Deleted the tab {title}"

    async def x_duplicate_sheet(self, op: dict[str, Any]) -> str:
        title, sid, _ = self._tab(op.get("sheet"))
        new = str(op.get("name") or f"Copy of {title}")[:100]
        await self._batch({"duplicateSheet": {"sourceSheetId": sid, "newSheetName": new}})
        return f"Copied the tab {title} as {new}"

    async def x_hide_sheet(self, op: dict[str, Any]) -> str:
        title, sid, _ = self._tab(op.get("sheet"))
        hidden = not op.get("show")
        await self._batch(
            {
                "updateSheetProperties": {
                    "properties": {"sheetId": sid, "hidden": hidden},
                    "fields": "hidden",
                }
            }
        )
        return f"{'Hid' if hidden else 'Showed'} the tab {title}"

    async def x_rename(self, op: dict[str, Any]) -> str:
        new = str(op.get("title") or op.get("name") or "").strip()[:200]
        if not new:
            raise LedgerError("rename needs the new title.")
        await self._batch(
            {"updateSpreadsheetProperties": {"properties": {"title": new}, "fields": "title"}}
        )
        return f"Renamed the spreadsheet to {new!r}"

    # ---------------------------------------------------------------- anything else
    def _raw_fix(self, node: Any, key: str = "") -> Any:
        """Google's own requests, with the shorthand a person writes: "A1:D5" (or 'Tab'!A1:D5)
        where a GridRange belongs, a tab's name where its id belongs."""
        if isinstance(node, dict):
            return {k: self._raw_fix(v, k) for k, v in node.items()}
        if isinstance(node, list):
            return [self._raw_fix(v, key) for v in node]
        if isinstance(node, str):
            if key in ("sheetId", "sourceSheetId"):
                return self._tab(node)[1]
            if key in ("range", "ranges", "source", "sources", "destination", "anchorCell"):
                _, sid, grid, _ = self._grid({"op": "raw", "range": node})
                if key == "anchorCell":
                    return {
                        "sheetId": sid,
                        "rowIndex": grid.get("startRowIndex", 0),
                        "columnIndex": grid.get("startColumnIndex", 0),
                    }
                return grid
        return node

    async def x_raw(self, op: dict[str, Any]) -> str:
        requests = [r for r in op.get("requests") or [] if isinstance(r, dict) and r]
        if not requests:
            raise LedgerError("raw needs requests: Google Sheets batchUpdate requests.")
        await self.google.sheets_batch(self.id, [self._raw_fix(r) for r in requests])
        names = ", ".join(sorted({next(iter(r)) for r in requests}))
        return str(op.get("did") or f"Applied {names}")


# ============================================================================ Docs
_PAGE = {"letter": (612.0, 792.0), "a4": (595.28, 841.89), "legal": (612.0, 1008.0)}


def doc_text_style(op: dict[str, Any]) -> dict[str, Any]:
    """A Docs TextStyle from an edit's plain words (its keys are the field mask)."""
    style: dict[str, Any] = {}
    for key in ("bold", "italic", "underline", "strikethrough"):
        if op.get(key) is not None:
            style[key] = bool(op[key])
    if op.get("size"):
        style["fontSize"] = _pt(_n(op["size"], "size"))
    if op.get("font"):
        style["weightedFontFamily"] = {"fontFamily": str(op["font"])}
    for key, api in (("color", "foregroundColor"), ("highlight", "backgroundColor")):
        if op.get(key):
            style[api] = {"color": {"rgbColor": rgb_json(op[key], key)}}
    if op.get("link"):
        style["link"] = {"url": str(op["link"])}
    return style


def _para_text(paragraph: dict[str, Any]) -> str:
    return "".join(e.get("textRun", {}).get("content", "") for e in paragraph.get("elements", []))


def _cell_text(cell: dict[str, Any]) -> str:
    return " ".join(
        _para_text(c["paragraph"]).strip() for c in cell.get("content", []) if "paragraph" in c
    ).strip()


class DocExtras:
    """Mixed into ``ledger.GoogleDoc``."""

    # ---------------------------------------------------------------- where
    def _tables(self) -> list[dict[str, Any]]:
        return [b for b in self.doc.get("body", {}).get("content", []) if "table" in b]

    def _table(self, op: dict[str, Any]) -> dict[str, Any]:
        tables = self._tables()
        if not tables:
            raise LedgerError("There's no table in this doc.")
        try:
            n = int(op.get("table") or 1)
            if n < 1:
                raise IndexError
            return tables[n - 1]
        except (ValueError, IndexError):
            raise LedgerError(
                f"There's no table {op.get('table')}; the doc has {_plural(len(tables), 'table')}."
            ) from None

    @staticmethod
    def _size(table: dict[str, Any]) -> tuple[int, int]:
        t = table["table"]
        rows = t.get("tableRows", [])
        return (
            len(rows) or int(t.get("rows", 0)),
            max((len(r.get("tableCells", [])) for r in rows), default=0)
            or int(t.get("columns", 0)),
        )

    def _cell(self, table: dict[str, Any], r: int, c: int) -> dict[str, Any]:
        n_rows, n_cols = self._size(table)
        if not (1 <= r <= n_rows and 1 <= c <= n_cols):
            raise LedgerError(
                f"There's no cell {col_letter(max(c, 1))}{r}: the table is {n_rows} rows by {n_cols} columns."
            )
        return table["table"]["tableRows"][r - 1]["tableCells"][c - 1]

    @staticmethod
    def _span(cell: dict[str, Any]) -> tuple[int, int]:
        """A cell's text, from its first character to just before its last newline."""
        content = cell.get("content") or []
        return int(content[0]["startIndex"]), int(content[-1]["endIndex"]) - 1

    @staticmethod
    def _loc(table: dict[str, Any], r: int, c: int) -> dict[str, Any]:
        return {
            "tableStartLocation": {"index": int(table["startIndex"])},
            "rowIndex": r - 1,
            "columnIndex": c - 1,
        }

    def _bounds(self, table: dict[str, Any], op: dict[str, Any]) -> tuple[int, int, int, int]:
        """(first row, first column, last row, last column) of an edit's range in a table,
        1-based; the whole table when it gives none."""
        n_rows, n_cols = self._size(table)
        if not op.get("range"):
            return 1, 1, n_rows, n_cols
        c1, r1, c2, r2 = a1_bounds(str(op["range"]))
        r1, c1, r2, c2 = r1 or 1, c1 or 1, r2 or n_rows, c2 or n_cols
        self._cell(table, r1, c1)
        self._cell(table, r2, c2)
        return r1, c1, r2, c2

    def _paragraphs(self):
        """Every paragraph, the ones inside tables too."""

        def walk(content: list[dict[str, Any]]):
            for b in content:
                if "paragraph" in b:
                    yield b
                elif "table" in b:
                    for row in b["table"].get("tableRows", []):
                        for cell in row.get("tableCells", []):
                            yield from walk(cell.get("content", []))

        yield from walk(self.doc.get("body", {}).get("content", []))

    def _find(self, find: str) -> list[tuple[int, int]]:
        """Every place these words appear (tables included), as document index ranges. Exact
        case first; if that finds nothing, any case."""
        runs = []
        for b in self._paragraphs():
            text, index = "", []
            for e in b["paragraph"].get("elements", []):
                at = int(e.get("startIndex", b.get("startIndex", 0)))
                for ch in e.get("textRun", {}).get("content", ""):
                    text += ch
                    index.append(at)
                    at += 2 if ord(ch) > 0xFFFF else 1
            runs.append((text, index))
        for fold in (False, True):
            out = []
            needle = find.lower() if fold else find
            for text, index in runs:
                hay = text.lower() if fold else text
                at = hay.find(needle)
                while at >= 0:
                    last = at + len(find) - 1
                    out.append((index[at], index[last] + (2 if ord(text[last]) > 0xFFFF else 1)))
                    at = hay.find(needle, at + len(find))
            if out:
                return out
        return []

    def _block(self, i: Any) -> dict[str, Any]:
        try:
            return self._blocks()[int(i)]
        except (TypeError, ValueError, IndexError):
            raise LedgerError(f"There's no paragraph {i}.") from None

    def _para_range(self, op: dict[str, Any]) -> dict[str, int]:
        """The paragraphs an edit covers: "i", "from" to "to", or the whole doc ("all")."""
        if op.get("all") or all(op.get(k) is None for k in ("i", "from", "to")):
            return {"startIndex": 1, "endIndex": max(2, self._end())}
        first = self._block(op.get("i") if op.get("i") is not None else op.get("from"))
        last = self._block(op["to"]) if op.get("to") is not None else first
        return {"startIndex": int(first["startIndex"]), "endIndex": int(last["endIndex"])}

    async def _send(self, *requests: dict[str, Any]) -> Any:
        r = await self.google.docs_batch(self.id, list(requests))
        await self.load()  # indexes moved
        return r

    def table_lines(self, n: int, table: dict[str, Any]) -> list[str]:
        """For the outline: a table with what's in it, so its cells can be named."""
        n_rows, n_cols = self._size(table)
        rows = table["table"].get("tableRows", [])
        filled = [[_cell_text(c)[:40] for c in r.get("tableCells", [])] for r in rows]
        head = (
            f"    (Table {n}, a table here: {_plural(n_rows, 'row')} x {_plural(n_cols, 'column')}"
        )
        if not any(any(r) for r in filled):
            return [head + ", all empty)"]
        out = [head + "; cells are A1, B1... like a spreadsheet)"]
        for r, cells in enumerate(filled[:15], 1):
            out.append(f"      row {r}: " + (" | ".join(cells) if any(cells) else "(empty)"))
        if len(filled) > 15:
            out.append("      ...")
        return out

    # ---------------------------------------------------------------- tables
    async def x_set_table_cells(self, op: dict[str, Any]) -> str:
        table = self._table(op)
        cells = table_cells(op)
        n_rows, _ = self._size(table)
        need = max(r for r, _ in cells) - n_rows
        if need > 0:  # more rows than the table has: it grows, as it would for a person
            if need > 50:
                raise LedgerError("That's more than 50 new rows.")
            await self._send(
                *[
                    {
                        "insertTableRow": {
                            "tableCellLocation": self._loc(table, n_rows, 1),
                            "insertBelow": True,
                        }
                    }
                ]
                * need
            )
            table = self._table(op)
        spans = sorted(
            ((self._span(self._cell(table, r, c)), text) for (r, c), text in cells.items()),
            reverse=True,  # last cell first: typing in an earlier cell moves every later index
        )
        reqs: list[dict[str, Any]] = []
        for (start, end), text in spans:
            if end > start:
                reqs.append(
                    {"deleteContentRange": {"range": {"startIndex": start, "endIndex": end}}}
                )
            if text:
                reqs.append({"insertText": {"location": {"index": start}, "text": text}})
        if reqs:
            await self._send(*reqs)
        return f"Filled {_plural(len(cells), 'cell')} in the table"

    async def x_add_table_row(self, op: dict[str, Any]) -> str:
        table = self._table(op)
        n_rows, _ = self._size(table)
        after = n_rows if op.get("after") in (None, "", "end") else int(op["after"])
        rows = op.get("rows") if isinstance(op.get("rows"), list) else None
        count = len(rows) if rows else max(1, int(op.get("count") or 1))
        if not 0 <= after <= n_rows or count > 50:
            raise LedgerError(f"The table has {_plural(n_rows, 'row')}.")
        req = {
            "insertTableRow": {
                "tableCellLocation": self._loc(table, max(after, 1), 1),
                "insertBelow": after > 0,
            }
        }
        await self._send(*[req] * count)
        values = rows or ([op["values"]] if isinstance(op.get("values"), list) else [])
        if values:
            await self.x_set_table_cells(
                {"table": op.get("table"), "rows": values, "start": f"A{after + 1}"}
            )
        return f"Added {_plural(count, 'row')} to the table"

    async def x_add_table_column(self, op: dict[str, Any]) -> str:
        table = self._table(op)
        _, n_cols = self._size(table)
        raw = op.get("after")
        after = (
            n_cols
            if raw in (None, "", "end")
            else int(raw)
            if str(raw).isdigit()
            else col_number(str(raw))
        )
        count = max(1, int(op.get("count") or 1))
        if not 0 <= after <= n_cols or n_cols + count > 20:
            raise LedgerError(f"The table has {_plural(n_cols, 'column')}, and 20 is the most.")
        req = {
            "insertTableColumn": {
                "tableCellLocation": self._loc(table, 1, max(after, 1)),
                "insertRight": after > 0,
            }
        }
        await self._send(*[req] * count)
        if isinstance(op.get("values"), list):
            await self.x_set_table_cells(
                {"table": op.get("table"), "column": after + 1, "values": op["values"]}
            )
        return f"Added {_plural(count, 'column')} to the table"

    async def _delete_lines(self, op: dict[str, Any], what: str) -> str:
        table = self._table(op)
        n_rows, n_cols = self._size(table)
        raw = op.get(f"{what}s") if isinstance(op.get(f"{what}s"), list) else [op.get(what)]
        picked = set()
        for v in raw:
            if v in (None, ""):
                continue
            picked.add(int(v) if str(v).isdigit() else col_number(str(v)))
        limit = n_rows if what == "row" else n_cols
        if not picked or any(not 1 <= n <= limit for n in picked):
            raise LedgerError(f"Which {what}? The table has {_plural(limit, what)}.")
        api = "deleteTableRow" if what == "row" else "deleteTableColumn"
        reqs = [
            {
                api: {
                    "tableCellLocation": self._loc(table, n, 1)
                    if what == "row"
                    else self._loc(table, 1, n)
                }
            }
            for n in sorted(picked, reverse=True)
        ]
        await self._send(*reqs)
        return f"Deleted {_plural(len(picked), what)} from the table"

    async def x_delete_table_row(self, op: dict[str, Any]) -> str:
        return await self._delete_lines(op, "row")

    async def x_delete_table_column(self, op: dict[str, Any]) -> str:
        return await self._delete_lines(op, "column")

    async def x_delete_table(self, op: dict[str, Any]) -> str:
        table = self._table(op)
        await self._send(
            {
                "deleteContentRange": {
                    "range": {
                        "startIndex": int(table["startIndex"]),
                        "endIndex": int(table["endIndex"]),
                    }
                }
            }
        )
        return "Deleted the table"

    async def x_format_table(self, op: dict[str, Any]) -> str:
        table = self._table(op)
        r1, c1, r2, c2 = self._bounds(table, op)
        reqs: list[dict[str, Any]] = []
        cell_style: dict[str, Any] = {}
        if op.get("fill"):
            cell_style["backgroundColor"] = {"color": {"rgbColor": rgb_json(op["fill"], "fill")}}
        if op.get("border_color") or op.get("border_width") is not None:
            border = {
                "color": {"color": {"rgbColor": rgb_json(op.get("border_color") or "black")}},
                "width": _pt(_n(op.get("border_width", 1), "border_width")),
                "dashStyle": "SOLID",
            }
            for side in ("borderTop", "borderBottom", "borderLeft", "borderRight"):
                cell_style[side] = border
        if cell_style:
            reqs.append(
                {
                    "updateTableCellStyle": {
                        "tableRange": {
                            "tableCellLocation": self._loc(table, r1, c1),
                            "rowSpan": r2 - r1 + 1,
                            "columnSpan": c2 - c1 + 1,
                        },
                        "tableCellStyle": cell_style,
                        "fields": ",".join(cell_style),
                    }
                }
            )
        style = doc_text_style(op)
        how = _ALIGN.get(str(op.get("align") or "").lower())
        if op.get("align") and how is None:
            raise LedgerError("align should be left, center, right or justify.")
        if style or how:
            for r in range(r1, r2 + 1):
                for c in range(c1, c2 + 1):
                    start, end = self._span(self._cell(table, r, c))
                    rng = {"startIndex": start, "endIndex": end + 1}
                    if style:
                        reqs.append(
                            {
                                "updateTextStyle": {
                                    "range": rng,
                                    "textStyle": style,
                                    "fields": ",".join(style),
                                }
                            }
                        )
                    if how:
                        reqs.append(
                            {
                                "updateParagraphStyle": {
                                    "range": rng,
                                    "paragraphStyle": {"alignment": how},
                                    "fields": "alignment",
                                }
                            }
                        )
        if op.get("column_width"):
            reqs.append(
                {
                    "updateTableColumnProperties": {
                        "tableStartLocation": {"index": int(table["startIndex"])},
                        "columnIndices": list(range(c1 - 1, c2)),
                        "tableColumnProperties": {
                            "widthType": "FIXED_WIDTH",
                            "width": _pt(_n(op["column_width"], "column_width") * 72),
                        },
                        "fields": "width,widthType",
                    }
                }
            )
        if not reqs:
            raise LedgerError(
                "format_table needs something to change: fill, bold, color, size, align, "
                "border_color, border_width or column_width."
            )
        await self._send(*reqs)
        where = str(op["range"]).upper() if op.get("range") else "the table"
        return f"Formatted {where}"

    async def x_merge_cells(self, op: dict[str, Any]) -> str:
        table = self._table(op)
        r1, c1, r2, c2 = self._bounds(table, op)
        api = "unmergeTableCells" if op.get("unmerge") else "mergeTableCells"
        await self._send(
            {
                api: {
                    "tableRange": {
                        "tableCellLocation": self._loc(table, r1, c1),
                        "rowSpan": r2 - r1 + 1,
                        "columnSpan": c2 - c1 + 1,
                    }
                }
            }
        )
        return f"{'Unmerged' if op.get('unmerge') else 'Merged'} {str(op.get('range') or 'the table').upper()}"

    # ---------------------------------------------------------------- text
    async def x_set_text(self, op: dict[str, Any]) -> str:
        block = self._block(op.get("i"))
        start, end = int(block["startIndex"]), int(block["endIndex"]) - 1
        text = str(op.get("text") or "")
        reqs: list[dict[str, Any]] = []
        if end > start:
            reqs.append({"deleteContentRange": {"range": {"startIndex": start, "endIndex": end}}})
        if text:
            reqs.append({"insertText": {"location": {"index": start}, "text": text}})
        if not reqs:
            return "That line was already empty"
        await self._send(*reqs)
        return f"Rewrote paragraph {int(op['i']) + 1}"

    async def x_insert_text(self, op: dict[str, Any]) -> str:
        text = str(op.get("text") or "")
        anchor = str(op.get("after_text") or op.get("before_text") or "")
        if not text or not anchor:
            raise LedgerError(
                "insert_text needs text, and after_text or before_text (words already there)."
            )
        found = self._find(anchor)
        if not found:
            raise LedgerError(f"'{anchor[:40]}' isn't in the doc.")
        start, end = found[0]
        at = end if op.get("after_text") else start
        await self._send({"insertText": {"location": {"index": at}, "text": text}})
        return f"Added text {'after' if op.get('after_text') else 'before'} '{anchor[:40]}'"

    async def x_delete_text(self, op: dict[str, Any]) -> str:
        find = str(op.get("find") or "")
        found = self._find(find) if find else []
        if not found:
            raise LedgerError(f"'{find[:40]}' isn't in the doc.")
        await self._send(
            *[
                {"deleteContentRange": {"range": {"startIndex": a, "endIndex": b}}}
                for a, b in sorted(found, reverse=True)
            ]
        )
        return f"Deleted '{find[:40]}'" + (f" ({len(found)} places)" if len(found) > 1 else "")

    async def x_make_bullets(self, op: dict[str, Any]) -> str:
        preset = (
            "BULLET_CHECKBOX"
            if op.get("checklist")
            else "NUMBERED_DECIMAL_ALPHA_ROMAN"
            if op.get("numbered")
            else "BULLET_DISC_CIRCLE_SQUARE"
        )
        await self._send(
            {"createParagraphBullets": {"range": self._para_range(op), "bulletPreset": preset}}
        )
        kind = (
            "a checklist"
            if op.get("checklist")
            else "a numbered list"
            if op.get("numbered")
            else "bullets"
        )
        return f"Made those lines {kind}"

    async def x_remove_bullets(self, op: dict[str, Any]) -> str:
        await self._send({"deleteParagraphBullets": {"range": self._para_range(op)}})
        return "Removed the bullets"

    async def x_spacing(self, op: dict[str, Any]) -> str:
        style: dict[str, Any] = {}
        if op.get("line"):
            style["lineSpacing"] = round(_n(op["line"], "line") * 100, 1)
        for key, api in (("before", "spaceAbove"), ("after", "spaceBelow")):
            if op.get(key) is not None:
                style[api] = _pt(_n(op[key], key))
        for key, api in (("indent", "indentStart"), ("first_line", "indentFirstLine")):
            if op.get(key) is not None:
                style[api] = _pt(_n(op[key], key) * 72)
        if not style:
            raise LedgerError(
                "spacing needs line (1.5), before or after (points), or indent (inches)."
            )
        await self._send(
            {
                "updateParagraphStyle": {
                    "range": self._para_range(op),
                    "paragraphStyle": style,
                    "fields": ",".join(style),
                }
            }
        )
        return "Set the spacing"

    # ---------------------------------------------------------------- pictures and pages
    async def x_add_image(self, op: dict[str, Any]) -> str:
        url = str(op.get("url") or "").strip()
        if not re.match(r"https?://", url):
            raise LedgerError(
                "A picture in a Google Doc has to come from a web address (https://...) that "
                "anyone can open."
            )
        req: dict[str, Any] = {"uri": url}
        if op.get("after") is not None:
            req["location"] = {"index": self._at(op)}
        else:
            req["endOfSegmentLocation"] = {}
        size = {
            k: _pt(_n(op[k], k) * 72) for k in ("width", "height") if op.get(k) not in (None, "")
        }
        if size:
            req["objectSize"] = size
        await self._send({"insertInlineImage": req})
        return "Added the picture"

    async def x_page_setup(self, op: dict[str, Any]) -> str:
        style: dict[str, Any] = {}
        said = []
        margins = op.get("margins") if isinstance(op.get("margins"), dict) else {}
        for side in ("top", "bottom", "left", "right"):
            v = margins.get(side, op.get("margin"))
            if v is not None:
                style[f"margin{side.capitalize()}"] = _pt(_n(v, "margin") * 72)
        if any(k.startswith("margin") for k in style):
            said.append("margins")
        now = self.doc.get("documentStyle", {}).get("pageSize", {})
        w = float(now.get("width", {}).get("magnitude", 612.0))
        h = float(now.get("height", {}).get("magnitude", 792.0))
        if op.get("size"):
            if str(op["size"]).lower() not in _PAGE:
                raise LedgerError("size should be letter, a4 or legal.")
            landscape = w > h
            w, h = _PAGE[str(op["size"]).lower()]
            if landscape:
                w, h = h, w
            said.append(
                f"{str(op['size']).upper() if len(str(op['size'])) < 3 else str(op['size']).lower()} paper"
            )
        if op.get("orientation"):
            wide = str(op["orientation"]).lower().startswith("land")
            w, h = (max(w, h), min(w, h)) if wide else (min(w, h), max(w, h))
            said.append("landscape" if wide else "portrait")
        if op.get("size") or op.get("orientation"):
            style["pageSize"] = {"width": _pt(w), "height": _pt(h)}
        if not style:
            raise LedgerError("page_setup needs margin (inches), orientation or size.")
        await self._send(
            {"updateDocumentStyle": {"documentStyle": style, "fields": ",".join(style)}}
        )
        return "Set the page: " + ", ".join(said)

    async def _header_footer(self, op: dict[str, Any], kind: str) -> str:
        key = f"default{kind.capitalize()}Id"
        seg_id = self.doc.get("documentStyle", {}).get(key)
        if not seg_id:
            r = await self._send({f"create{kind.capitalize()}": {"type": "DEFAULT"}})
            seg_id = (
                ((r or {}).get("replies") or [{}])[0]
                .get(f"create{kind.capitalize()}", {})
                .get(f"{kind}Id")
            )
            seg_id = seg_id or self.doc.get("documentStyle", {}).get(key)
        if not seg_id:
            raise LedgerError(f"Google didn't make the {kind}.")
        content = (self.doc.get(f"{kind}s", {}).get(seg_id) or {}).get("content") or []
        start = int(content[0].get("startIndex", 0)) if content else 0
        end = int(content[-1].get("endIndex", 1)) - 1 if content else 0
        text = str(op.get("text") or "")
        reqs: list[dict[str, Any]] = []
        if end > start:
            reqs.append(
                {
                    "deleteContentRange": {
                        "range": {"segmentId": seg_id, "startIndex": start, "endIndex": end}
                    }
                }
            )
        if text:
            reqs.append(
                {"insertText": {"location": {"segmentId": seg_id, "index": start}, "text": text}}
            )
            how = _ALIGN.get(str(op.get("align") or "").lower())
            if how:
                reqs.append(
                    {
                        "updateParagraphStyle": {
                            "range": {
                                "segmentId": seg_id,
                                "startIndex": start,
                                "endIndex": start + _units(text) + 1,
                            },
                            "paragraphStyle": {"alignment": how},
                            "fields": "alignment",
                        }
                    }
                )
        if reqs:
            await self._send(*reqs)
        return f"Set the {kind} to '{text[:40]}'" if text else f"Cleared the {kind}"

    async def x_header(self, op: dict[str, Any]) -> str:
        return await self._header_footer(op, "header")

    async def x_footer(self, op: dict[str, Any]) -> str:
        return await self._header_footer(op, "footer")

    async def x_footnote(self, op: dict[str, Any]) -> str:
        anchor = str(op.get("after_text") or "")
        found = self._find(anchor) if anchor else []
        if not found:
            raise LedgerError(
                f"'{anchor[:40]}' isn't in the doc, so I can't hang a footnote on it."
            )
        r = await self._send({"createFootnote": {"location": {"index": found[0][1]}}})
        note_id = ((r or {}).get("replies") or [{}])[0].get("createFootnote", {}).get("footnoteId")
        if not note_id:
            raise LedgerError("Google didn't make the footnote.")
        await self._send(
            {
                "insertText": {
                    "location": {"segmentId": note_id, "index": 1},
                    "text": str(op.get("text") or ""),
                }
            }
        )
        return f"Added a footnote after '{anchor[:40]}'"

    async def x_rename(self, op: dict[str, Any]) -> str:
        new = str(op.get("title") or op.get("name") or "").strip()[:200]
        if not new:
            raise LedgerError("rename needs the new title.")
        await self.google.drive_rename(self.id, new)
        await self.load()
        return f"Renamed the doc to {new!r}"

    async def x_raw(self, op: dict[str, Any]) -> str:
        requests = [r for r in op.get("requests") or [] if isinstance(r, dict) and r]
        if not requests:
            raise LedgerError("raw needs requests: Google Docs batchUpdate requests.")
        await self._send(*requests)
        names = ", ".join(sorted({next(iter(r)) for r in requests}))
        return str(op.get("did") or f"Applied {names}")


# ============================================================================ Slides
_EMU = 914400.0  # per inch
_LAYOUTS = {
    "title": "TITLE",
    "title slide": "TITLE",
    "title and body": "TITLE_AND_BODY",
    "bullets": "TITLE_AND_BODY",
    "section": "SECTION_HEADER",
    "section header": "SECTION_HEADER",
    "two columns": "TITLE_AND_TWO_COLUMNS",
    "title only": "TITLE_ONLY",
    "blank": "BLANK",
    "big number": "BIG_NUMBER",
    "main point": "MAIN_POINT",
    "caption": "CAPTION_ONLY",
}
_SHAPES = {
    "rectangle": "RECTANGLE",
    "rounded rectangle": "ROUND_RECTANGLE",
    "ellipse": "ELLIPSE",
    "circle": "ELLIPSE",
    "oval": "ELLIPSE",
    "triangle": "TRIANGLE",
    "diamond": "DIAMOND",
    "arrow": "RIGHT_ARROW",
    "right arrow": "RIGHT_ARROW",
    "left arrow": "LEFT_ARROW",
    "up arrow": "UP_ARROW",
    "down arrow": "DOWN_ARROW",
    "star": "STAR_5",
    "heart": "HEART",
    "cloud": "CLOUD",
    "speech bubble": "WEDGE_RECTANGLE_CALLOUT",
}
_PLACE = {"CENTERED_TITLE": "title", "TITLE": "title", "SUBTITLE": "subtitle", "BODY": "body"}


def _inches(dim: dict[str, Any] | None) -> float:
    if not dim:
        return 0.0
    mag = float(dim.get("magnitude", 0) or 0)
    return mag / _EMU if dim.get("unit", "EMU") == "EMU" else mag / 72.0


def _shape_text(el: dict[str, Any]) -> str:
    text = (el.get("shape") or {}).get("text") or {}
    return "".join(t.get("textRun", {}).get("content", "") for t in text.get("textElements", []))


def _text_end(text: dict[str, Any] | None) -> int:
    """Where a shape's (or cell's) text ends, before its last newline; 0 when it has none."""
    elements = (text or {}).get("textElements") or []
    return max(0, int(elements[-1].get("endIndex", 1)) - 1) if elements else 0


def slide_text_style(op: dict[str, Any]) -> dict[str, Any]:
    style: dict[str, Any] = {}
    for key in ("bold", "italic", "underline", "strikethrough"):
        if op.get(key) is not None:
            style[key] = bool(op[key])
    if op.get("size"):
        style["fontSize"] = _pt(_n(op["size"], "size"))
    if op.get("font"):
        style["fontFamily"] = str(op["font"])
    for key, api in (("color", "foregroundColor"), ("highlight", "backgroundColor")):
        if op.get(key):
            style[api] = {"opaqueColor": {"rgbColor": rgb_json(op[key], key)}}
    if op.get("link"):
        style["link"] = {"url": str(op["link"])}
    return style


class GoogleSlides:
    """A Google Slides deck, edited through the Slides API. Positions and sizes are in inches
    from the slide's top left, the way the ruler shows them."""

    kind = "gslides"

    def __init__(self, google: Any, deck_id: str) -> None:
        self.google = google
        self.id = deck_id
        self.deck: dict[str, Any] = {}

    async def load(self) -> None:
        self.deck = await self.google.slides_get(self.id)

    @property
    def title(self) -> str:
        return self.deck.get("title", "Slides")

    @property
    def url(self) -> str:
        return f"https://docs.google.com/presentation/d/{self.id}/edit"

    @property
    def slides(self) -> list[dict[str, Any]]:
        return self.deck.get("slides") or []

    def _page(self) -> tuple[float, float]:
        size = self.deck.get("pageSize") or {}
        return _inches(size.get("width")) or 10.0, _inches(size.get("height")) or 5.625

    # ---------------------------------------------------------------- reading
    @staticmethod
    def _kind(el: dict[str, Any]) -> str:
        if "shape" in el:
            place = (el["shape"].get("placeholder") or {}).get("type")
            if place:
                return _PLACE.get(place, place.lower().replace("_", " "))
            kind = el["shape"].get("shapeType", "shape")
            return "text box" if kind == "TEXT_BOX" else kind.lower().replace("_", " ")
        for key, name in (
            ("image", "picture"),
            ("table", "table"),
            ("sheetsChart", "chart"),
            ("video", "video"),
            ("line", "line"),
            ("elementGroup", "group"),
            ("wordArt", "word art"),
        ):
            if key in el:
                return name
        return "element"

    @staticmethod
    def _box(el: dict[str, Any]) -> tuple[float, float, float, float]:
        t = el.get("transform") or {}
        per = _EMU if t.get("unit", "EMU") == "EMU" else 72.0
        size = el.get("size") or {}
        return (
            float(t.get("translateX", 0) or 0) / per,
            float(t.get("translateY", 0) or 0) / per,
            _inches(size.get("width")) * float(t.get("scaleX", 1) or 1),
            _inches(size.get("height")) * float(t.get("scaleY", 1) or 1),
        )

    def _layout_name(self, slide: dict[str, Any]) -> str:
        want = (slide.get("slideProperties") or {}).get("layoutObjectId")
        for layout in self.deck.get("layouts") or []:
            if layout.get("objectId") == want:
                props = layout.get("layoutProperties") or {}
                return props.get("displayName") or props.get("name") or ""
        return ""

    def _notes(self, slide: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
        page = (slide.get("slideProperties") or {}).get("notesPage") or {}
        note_id = (page.get("notesProperties") or {}).get("speakerNotesObjectId")
        el = next((e for e in page.get("pageElements") or [] if e.get("objectId") == note_id), None)
        return note_id, el

    def describe(self, max_chars: int = 9000) -> str:
        w, h = self._page()
        out = [
            f"Google Slides '{self.title}': {_plural(len(self.slides), 'slide')}, each {w:.2f} x "
            f"{h:.2f} inches. Positions (x, y) and sizes are inches from a slide's top left."
        ]
        size = 0
        for n, slide in enumerate(self.slides, 1):
            layout = self._layout_name(slide)
            lines = [f"Slide {n}" + (f" ({layout})" if layout else "") + ":"]
            for el in slide.get("pageElements") or []:
                x, y, ew, eh = self._box(el)
                line = f"  {self._kind(el)} [id {el.get('objectId')}] at {x:.1f},{y:.1f} size {ew:.1f}x{eh:.1f}"
                if "shape" in el:
                    text = " / ".join(t for t in _shape_text(el).strip().split("\n") if t)
                    line += f": {text[:300]!r}" if text else ": (empty)"
                elif "table" in el:
                    t = el["table"]
                    rows = t.get("tableRows") or []
                    first = [
                        "".join(
                            x.get("textRun", {}).get("content", "")
                            for x in (c.get("text") or {}).get("textElements", [])
                        ).strip()[:30]
                        for c in (rows[0].get("tableCells", []) if rows else [])
                    ]
                    line += f": {t.get('rows', len(rows))} rows x {t.get('columns', len(first))} columns; first row: {' | '.join(first) if any(first) else 'empty'}"
                lines.append(line)
            if len(lines) == 1:
                lines.append("  (nothing on it)")
            _, notes = self._notes(slide)
            if notes is not None and _shape_text(notes).strip():
                lines.append(f"  notes: {_shape_text(notes).strip()[:200]!r}")
            size += sum(len(x) for x in lines)
            if size > max_chars:
                out.append("  ...")
                break
            out.extend(lines)
        return "\n".join(out)

    # ---------------------------------------------------------------- where
    def _slide(self, op: dict[str, Any]) -> dict[str, Any]:
        try:
            n = int(op.get("slide"))
            if n < 1:
                raise IndexError
            return self.slides[n - 1]
        except (TypeError, ValueError, IndexError):
            raise LedgerError(
                f"There's no slide {op.get('slide')}; the deck has {len(self.slides)}."
            ) from None

    def _element(self, op: dict[str, Any]) -> dict[str, Any]:
        """The thing an edit names: by its id from the outline, or "which" (title, body,
        subtitle) on a slide."""
        if op.get("id"):
            for slide in self.slides:
                for el in slide.get("pageElements") or []:
                    if el.get("objectId") == str(op["id"]):
                        return el
            raise LedgerError(f"There's nothing with the id {op['id']!r} in the deck.")
        which = str(op.get("which") or "body").lower()
        slide = self._slide(op)
        for el in slide.get("pageElements") or []:
            if "shape" in el and self._kind(el) == which:
                return el
        raise LedgerError(f"Slide {op.get('slide')} has no {which} box.")

    def _props(
        self, slide: dict[str, Any], op: dict[str, Any], w: float, h: float
    ) -> dict[str, Any]:
        """Where a new thing goes: x, y, w, h in inches, centred by default."""
        page_w, page_h = self._page()
        w = _n(op.get("w", w), "w")
        h = _n(op.get("h", h), "h")
        x = _n(op.get("x", max(0.0, (page_w - w) / 2)), "x")
        y = _n(op.get("y", max(0.0, (page_h - h) / 2)), "y")
        return {
            "pageObjectId": slide["objectId"],
            "size": {"width": _pt(w * 72), "height": _pt(h * 72)},
            "transform": {
                "scaleX": 1,
                "scaleY": 1,
                "translateX": round(x * 72, 2),
                "translateY": round(y * 72, 2),
                "unit": "PT",
            },
        }

    @staticmethod
    def _new_id() -> str:
        return "pyxi_" + uuid.uuid4().hex[:14]

    async def _send(self, *requests: dict[str, Any]) -> Any:
        r = await self.google.slides_batch(self.id, list(requests))
        await self.load()
        return r

    @staticmethod
    def _write(
        object_id: str, text: str, old_end: int, cell: dict[str, int] | None = None
    ) -> list[dict[str, Any]]:
        """Requests that make a box (or table cell) say exactly this."""
        where: dict[str, Any] = {"objectId": object_id}
        if cell:
            where["cellLocation"] = cell
        reqs: list[dict[str, Any]] = []
        if old_end > 0:
            reqs.append({"deleteText": {**where, "textRange": {"type": "ALL"}}})
        if text:
            reqs.append({"insertText": {**where, "insertionIndex": 0, "text": text}})
        return reqs

    # ---------------------------------------------------------------- edits
    async def apply(self, op: dict[str, Any]) -> str:
        fn = getattr(self, f"x_{op.get('op')}", None)
        if fn is None:
            raise LedgerError(f"Google Slides doesn't have an edit called {op.get('op')!r} here.")
        return await fn(op)

    async def x_add_slide(self, op: dict[str, Any]) -> str:
        title = str(op.get("title") or "")
        bullets = [str(b) for b in op.get("bullets") or [] if str(b).strip()]
        subtitle = str(op.get("subtitle") or "")
        layout = _LAYOUTS.get(str(op.get("layout") or "").lower())
        if op.get("layout") and layout is None:
            raise LedgerError(f"layout should be one of: {', '.join(sorted(set(_LAYOUTS)))}.")
        if layout is None:
            layout = (
                "TITLE_AND_BODY"
                if bullets
                else "TITLE"
                if subtitle
                else "TITLE_ONLY"
                if title
                else "BLANK"
            )
        new_id = self._new_id()
        req: dict[str, Any] = {
            "objectId": new_id,
            "slideLayoutReference": {"predefinedLayout": layout},
        }
        if op.get("after") not in (None, "", "end"):
            req["insertionIndex"] = max(0, min(int(op["after"]), len(self.slides)))
        await self._send({"createSlide": req})
        slide = next((s for s in self.slides if s.get("objectId") == new_id), None)
        if slide is None:
            raise LedgerError("Google didn't make the slide.")
        # Its boxes are known only now: a theme's layout decides which placeholders it has.
        boxes: dict[str, str] = {}
        for el in slide.get("pageElements") or []:
            if "shape" in el:
                boxes.setdefault(self._kind(el), el["objectId"])
        reqs: list[dict[str, Any]] = []
        missing = []
        for kind, text in (("title", title), ("subtitle", subtitle), ("body", "\n".join(bullets))):
            if not text:
                continue
            if kind not in boxes:
                missing.append(kind)
                continue
            reqs.append(
                {"insertText": {"objectId": boxes[kind], "insertionIndex": 0, "text": text}}
            )
            if kind == "body":
                reqs.append(
                    {
                        "createParagraphBullets": {
                            "objectId": boxes[kind],
                            "textRange": {"type": "ALL"},
                            "bulletPreset": "BULLET_DISC_CIRCLE_SQUARE",
                        }
                    }
                )
        if op.get("notes"):
            note_id, _ = self._notes(slide)
            if note_id:
                reqs.append(
                    {
                        "insertText": {
                            "objectId": note_id,
                            "insertionIndex": 0,
                            "text": str(op["notes"]),
                        }
                    }
                )
        if reqs:
            await self._send(*reqs)
        if missing:
            raise LedgerError(
                f"Added the slide, but its layout has no {' or '.join(missing)} box. Use "
                "add_text_box on it instead."
            )
        return f"Added slide '{title[:50]}'" + (
            f" with {_plural(len(bullets), 'bullet')}" if bullets else ""
        )

    async def x_set_text(self, op: dict[str, Any]) -> str:
        el = self._element(op)
        if "shape" not in el:
            raise LedgerError("That isn't a text box. For a table, use set_table_cells.")
        items = [str(b) for b in op.get("bullets") or [] if str(b).strip()]
        text = "\n".join(items) if items else str(op.get("text") or "")
        reqs = self._write(el["objectId"], text, _text_end(el["shape"].get("text")))
        if items:
            reqs.append(
                {
                    "createParagraphBullets": {
                        "objectId": el["objectId"],
                        "textRange": {"type": "ALL"},
                        "bulletPreset": "BULLET_DISC_CIRCLE_SQUARE",
                    }
                }
            )
        if not reqs:
            return "That box was already empty"
        await self._send(*reqs)
        return f"Set the {self._kind(el)} text" + (
            f" on slide {op['slide']}" if op.get("slide") else ""
        )

    async def x_set_title(self, op: dict[str, Any]) -> str:
        await self.x_set_text({**op, "which": "title"})
        return f"Retitled slide {op.get('slide')}"

    async def x_add_bullets(self, op: dict[str, Any]) -> str:
        items = [str(b) for b in op.get("items") or [] if str(b).strip()]
        if not items:
            raise LedgerError("add_bullets needs items.")
        el = self._element({**op, "which": op.get("which") or "body"})
        end = _text_end(el["shape"].get("text"))
        if op.get("replace") or end == 0:
            return await self.x_set_text(
                {**op, "id": el["objectId"], "bullets": items, "text": None}
            )
        await self._send(
            {
                "insertText": {
                    "objectId": el["objectId"],
                    "insertionIndex": end,
                    "text": "\n" + "\n".join(items),
                }
            }
        )
        return f"Added {_plural(len(items), 'bullet')} to slide {op.get('slide')}"

    async def x_add_text_box(self, op: dict[str, Any]) -> str:
        slide = self._slide(op)
        text = str(op.get("text") or "")
        new_id = self._new_id()
        page_w, _ = self._page()
        reqs: list[dict[str, Any]] = [
            {
                "createShape": {
                    "objectId": new_id,
                    "shapeType": "TEXT_BOX",
                    "elementProperties": self._props(slide, op, page_w - 2, 1.0),
                }
            }
        ]
        if text:
            reqs.append({"insertText": {"objectId": new_id, "insertionIndex": 0, "text": text}})
            reqs += self._style_requests(new_id, op, {"type": "ALL"})
        await self._send(*reqs)
        return f"Added a text box to slide {op.get('slide')}"

    async def x_add_shape(self, op: dict[str, Any]) -> str:
        slide = self._slide(op)
        kind = _SHAPES.get(str(op.get("shape") or "rectangle").lower())
        if kind is None:
            raise LedgerError(f"shape should be one of: {', '.join(sorted(_SHAPES))}.")
        new_id = self._new_id()
        reqs: list[dict[str, Any]] = [
            {
                "createShape": {
                    "objectId": new_id,
                    "shapeType": kind,
                    "elementProperties": self._props(slide, op, 2.0, 2.0),
                }
            }
        ]
        if op.get("fill"):
            reqs.append(
                {
                    "updateShapeProperties": {
                        "objectId": new_id,
                        "shapeProperties": {
                            "shapeBackgroundFill": {
                                "solidFill": {"color": {"rgbColor": rgb_json(op["fill"], "fill")}}
                            }
                        },
                        "fields": "shapeBackgroundFill.solidFill.color",
                    }
                }
            )
        if op.get("text"):
            reqs.append(
                {"insertText": {"objectId": new_id, "insertionIndex": 0, "text": str(op["text"])}}
            )
            reqs += self._style_requests(new_id, op, {"type": "ALL"})
        await self._send(*reqs)
        return f"Added a {str(op.get('shape') or 'rectangle').lower()} to slide {op.get('slide')}"

    async def x_add_image(self, op: dict[str, Any]) -> str:
        slide = self._slide(op)
        url = str(op.get("url") or "").strip()
        if not re.match(r"https?://", url):
            raise LedgerError(
                "A picture in Google Slides has to come from a web address (https://...) that "
                "anyone can open."
            )
        await self._send(
            {"createImage": {"url": url, "elementProperties": self._props(slide, op, 4.0, 3.0)}}
        )
        return f"Added the picture to slide {op.get('slide')}"

    async def x_add_table(self, op: dict[str, Any]) -> str:
        slide = self._slide(op)
        rows = [[str(c) for c in r] for r in op.get("rows") or [] if isinstance(r, list)]
        n_rows = len(rows) or int(op.get("n_rows") or 0)
        n_cols = max((len(r) for r in rows), default=0) or int(op.get("n_cols") or 0)
        if not (1 <= n_rows <= 25 and 1 <= n_cols <= 20):
            raise LedgerError(
                "add_table needs rows (the cells) or n_rows and n_cols (up to 25 x 20)."
            )
        new_id = self._new_id()
        page_w, _ = self._page()
        reqs: list[dict[str, Any]] = [
            {
                "createTable": {
                    "objectId": new_id,
                    "elementProperties": self._props(slide, op, page_w - 2, 0.4 * n_rows),
                    "rows": n_rows,
                    "columns": n_cols,
                }
            }
        ]
        for r, row in enumerate(rows):
            for c, text in enumerate(row):
                if text:
                    reqs.append(
                        {
                            "insertText": {
                                "objectId": new_id,
                                "cellLocation": {"rowIndex": r, "columnIndex": c},
                                "insertionIndex": 0,
                                "text": text,
                            }
                        }
                    )
        await self._send(*reqs)
        return f"Added a {n_rows} x {n_cols} table to slide {op.get('slide')}"

    def _table(self, op: dict[str, Any]) -> dict[str, Any]:
        if op.get("id"):
            el = self._element(op)
            if "table" not in el:
                raise LedgerError(f"{op['id']!r} isn't a table.")
            return el
        slide = self._slide(op)
        tables = [e for e in slide.get("pageElements") or [] if "table" in e]
        if not tables:
            raise LedgerError(f"Slide {op.get('slide')} has no table.")
        return tables[0]

    async def x_set_table_cells(self, op: dict[str, Any]) -> str:
        el = self._table(op)
        rows = el["table"].get("tableRows") or []
        cells = table_cells(op)
        reqs: list[dict[str, Any]] = []
        for (r, c), text in cells.items():
            try:
                cell = rows[r - 1]["tableCells"][c - 1]
                if r < 1 or c < 1:
                    raise IndexError
            except IndexError:
                raise LedgerError(
                    f"There's no cell {col_letter(max(c, 1))}{r}: the table is {len(rows)} rows by "
                    f"{len(rows[0].get('tableCells', [])) if rows else 0} columns."
                ) from None
            reqs += self._write(
                el["objectId"],
                text,
                _text_end(cell.get("text")),
                {"rowIndex": r - 1, "columnIndex": c - 1},
            )
        if reqs:
            await self._send(*reqs)
        return f"Filled {_plural(len(cells), 'cell')} in the table"

    def _style_requests(
        self, object_id: str, op: dict[str, Any], text_range: dict[str, Any]
    ) -> list[dict[str, Any]]:
        reqs: list[dict[str, Any]] = []
        style = slide_text_style(op)
        if style:
            reqs.append(
                {
                    "updateTextStyle": {
                        "objectId": object_id,
                        "textRange": text_range,
                        "style": style,
                        "fields": ",".join(style),
                    }
                }
            )
        if op.get("align"):
            how = _ALIGN.get(str(op["align"]).lower())
            if how is None:
                raise LedgerError("align should be left, center, right or justify.")
            reqs.append(
                {
                    "updateParagraphStyle": {
                        "objectId": object_id,
                        "textRange": text_range,
                        "style": {"alignment": how},
                        "fields": "alignment",
                    }
                }
            )
        return reqs

    async def x_format_text(self, op: dict[str, Any]) -> str:
        find = str(op.get("find") or "")
        if op.get("id") or op.get("which") or (op.get("slide") and not find):
            targets = [self._element(op)]
        else:
            pages = [self._slide(op)] if op.get("slide") else self.slides
            targets = [e for s in pages for e in s.get("pageElements") or [] if "shape" in e]
        reqs: list[dict[str, Any]] = []
        for el in targets:
            if "shape" not in el:
                continue
            text = _shape_text(el)
            if not find:
                if text.strip():
                    reqs += self._style_requests(el["objectId"], op, {"type": "ALL"})
                continue
            hay, needle = (text, find) if find in text else (text.lower(), find.lower())
            at = hay.find(needle)
            while at >= 0:
                start = _units(text[:at])
                rng = {"type": "FIXED_RANGE", "startIndex": start, "endIndex": start + _units(find)}
                reqs += self._style_requests(el["objectId"], op, rng)
                at = hay.find(needle, at + len(find))
        if not reqs:
            if find:
                raise LedgerError(f"'{find[:40]}' isn't on the slides.")
            raise LedgerError(
                "format_text needs something to change (bold, size, color, font, align...) "
                "on a box that has text."
            )
        await self._send(*reqs)
        return f"Formatted '{find[:40]}'" if find else "Formatted the text"

    async def x_move(self, op: dict[str, Any]) -> str:
        el = self._element(op)
        x, y, w, h = self._box(el)
        t = el.get("transform") or {}
        size = el.get("size") or {}
        base_w, base_h = _inches(size.get("width")), _inches(size.get("height"))
        new_w = _n(op["w"], "w") if op.get("w") is not None else w
        new_h = _n(op["h"], "h") if op.get("h") is not None else h
        if op.get("center"):
            page_w, page_h = self._page()
            x, y = (page_w - new_w) / 2, (page_h - new_h) / 2
        if op.get("x") is not None:
            x = _n(op["x"], "x")
        if op.get("y") is not None:
            y = _n(op["y"], "y")
        transform = {
            "scaleX": new_w / base_w if base_w else float(t.get("scaleX", 1) or 1),
            "scaleY": new_h / base_h if base_h else float(t.get("scaleY", 1) or 1),
            "shearX": float(t.get("shearX", 0) or 0),
            "shearY": float(t.get("shearY", 0) or 0),
            "translateX": round(x * _EMU),
            "translateY": round(y * _EMU),
            "unit": "EMU",
        }
        await self._send(
            {
                "updatePageElementTransform": {
                    "objectId": el["objectId"],
                    "applyMode": "ABSOLUTE",
                    "transform": transform,
                }
            }
        )
        resized = op.get("w") is not None or op.get("h") is not None
        return f"{'Resized' if resized else 'Moved'} the {self._kind(el)}"

    async def x_delete(self, op: dict[str, Any]) -> str:
        if not op.get("id") and not op.get("which"):
            raise LedgerError("delete needs the id of the thing to remove (from the outline).")
        el = self._element(op)
        await self._send({"deleteObject": {"objectId": el["objectId"]}})
        return f"Deleted the {self._kind(el)}"

    async def x_delete_slide(self, op: dict[str, Any]) -> str:
        slide = self._slide(op)
        if len(self.slides) < 2:
            raise LedgerError("That's the only slide, and a deck needs one.")
        await self._send({"deleteObject": {"objectId": slide["objectId"]}})
        return f"Removed slide {op['slide']}"

    async def x_duplicate_slide(self, op: dict[str, Any]) -> str:
        slide = self._slide(op)
        await self._send({"duplicateObject": {"objectId": slide["objectId"]}})
        return f"Copied slide {op['slide']} (the copy is slide {int(op['slide']) + 1})"

    async def x_move_slide(self, op: dict[str, Any]) -> str:
        slide = self._slide(op)
        now = int(op["slide"]) - 1
        try:
            to = max(0, min(int(op.get("to")) - 1, len(self.slides) - 1))
        except (TypeError, ValueError):
            raise LedgerError('move_slide needs "to": the slide number it should become.') from None
        if to == now:
            return f"Slide {op['slide']} is already there"
        await self._send(
            {
                "updateSlidesPosition": {
                    "slideObjectIds": [slide["objectId"]],
                    "insertionIndex": to + 1 if to > now else to,
                }
            }
        )
        return f"Moved slide {op['slide']} to position {to + 1}"

    async def x_background(self, op: dict[str, Any]) -> str:
        pages = [self._slide(op)] if op.get("slide") else self.slides
        if op.get("image"):
            fill = {"stretchedPictureFill": {"contentUrl": str(op["image"])}}
            fields = "pageBackgroundFill.stretchedPictureFill.contentUrl"
        elif op.get("color"):
            fill = {"solidFill": {"color": {"rgbColor": rgb_json(op["color"])}}}
            fields = "pageBackgroundFill.solidFill.color"
        else:
            raise LedgerError("background needs a color or an image (a web address).")
        await self._send(
            *[
                {
                    "updatePageProperties": {
                        "objectId": p["objectId"],
                        "pageProperties": {"pageBackgroundFill": fill},
                        "fields": fields,
                    }
                }
                for p in pages
            ]
        )
        where = f"slide {op['slide']}" if op.get("slide") else "every slide"
        return f"Changed the background of {where}"

    async def x_set_notes(self, op: dict[str, Any]) -> str:
        slide = self._slide(op)
        note_id, el = self._notes(slide)
        if not note_id:
            raise LedgerError(f"Slide {op.get('slide')} has no notes box.")
        end = _text_end((el or {}).get("shape", {}).get("text"))
        reqs = self._write(note_id, str(op.get("text") or ""), end)
        if reqs:
            await self._send(*reqs)
        return f"Wrote speaker notes on slide {op.get('slide')}"

    async def x_replace_text(self, op: dict[str, Any]) -> str:
        find = str(op.get("find") or "")
        if not find:
            raise LedgerError("replace_text needs the text to find.")
        r = await self._send(
            {
                "replaceAllText": {
                    "containsText": {"text": find, "matchCase": True},
                    "replaceText": str(op.get("replace") or ""),
                }
            }
        )
        n = (
            ((r or {}).get("replies") or [{}])[0]
            .get("replaceAllText", {})
            .get("occurrencesChanged", 0)
        )
        if not n:
            raise LedgerError(f"'{find[:40]}' isn't in the deck.")
        return f"Replaced '{find[:40]}' {_plural(int(n), 'time')}"

    async def x_rename(self, op: dict[str, Any]) -> str:
        new = str(op.get("title") or op.get("name") or "").strip()[:200]
        if not new:
            raise LedgerError("rename needs the new title.")
        await self.google.drive_rename(self.id, new)
        await self.load()
        return f"Renamed the deck to {new!r}"

    async def x_raw(self, op: dict[str, Any]) -> str:
        requests = [r for r in op.get("requests") or [] if isinstance(r, dict) and r]
        if not requests:
            raise LedgerError("raw needs requests: Google Slides batchUpdate requests.")
        fixed = []
        for r in requests:  # "slide": 2 in place of a page's objectId
            text = repr(r)
            fixed.append(r if "'slide'" not in text else self._raw_fix(r))
        await self._send(*fixed)
        names = ", ".join(sorted({next(iter(r)) for r in requests}))
        return str(op.get("did") or f"Applied {names}")

    def _raw_fix(self, node: Any) -> Any:
        if isinstance(node, dict):
            out = {}
            for k, v in node.items():
                if k == "slide" and isinstance(v, int | str) and str(v).isdigit():
                    out["pageObjectId"] = self._slide({"slide": v})["objectId"]
                else:
                    out[k] = self._raw_fix(v)
            return out
        if isinstance(node, list):
            return [self._raw_fix(v) for v in node]
        return node
