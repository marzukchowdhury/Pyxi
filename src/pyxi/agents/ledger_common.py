"""What every Ledger target shares: its error, colours, and spreadsheet addresses."""

from __future__ import annotations

import re
from typing import Any


class LedgerError(ValueError):
    """An edit that can't be applied; its message is written for the user."""


_COLORS = {
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
    "pink": "#E91E63",
    "teal": "#00897B",
    "navy": "#1A237E",
    "brown": "#795548",
    "light blue": "#CFE2F3",
    "light green": "#D9EAD3",
    "light yellow": "#FFF2CC",
    "light red": "#F4CCCC",
    "light gray": "#EFEFEF",
    "light grey": "#EFEFEF",
}


def hex_color(color: Any) -> str | None:
    """'#1A73E8', '1a73e8', 'red', 'light blue' -> 'RRGGBB' (upper case), or None."""
    c = _COLORS.get(str(color or "").strip().lower(), str(color or "").strip())
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", c)
    return m.group(1).upper() if m else None


def rgb(color: Any) -> tuple[float, float, float] | None:
    """'#1A73E8', 'red', 'blue' -> 0-1 floats."""
    h = hex_color(color)
    if h is None:
        return None
    return tuple(int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def rgb_json(color: Any, what: str = "color") -> dict[str, float]:
    """Google's {"red": .., "green": .., "blue": ..}, or a plain error for a colour it can't read."""
    c = rgb(color)
    if c is None:
        raise LedgerError(
            f"I can't read the {what} {color!r}: use a name like red or a code like #1A73E8."
        )
    return {"red": c[0], "green": c[1], "blue": c[2]}


def col_number(letters: str) -> int:
    """'A' -> 1, 'AB' -> 28."""
    n = 0
    for ch in letters.upper():
        n = n * 26 + (ord(ch) - 64)
    return n


def col_letter(n: int) -> str:
    """1 -> 'A', 28 -> 'AB'."""
    out = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        out = chr(65 + rem) + out
    return out


_A1 = re.compile(r"^\$?([A-Za-z]{0,3})\$?(\d*)(?::\$?([A-Za-z]{0,3})\$?(\d*))?$")


def a1_bounds(a1: str) -> tuple[int | None, int | None, int | None, int | None]:
    """'B2:D9' -> (2, 2, 4, 9) as (first column, first row, last column, last row), 1-based.
    Open ends are None: 'A:C' is (1, None, 3, None), '2:5' is (None, 2, None, 5), 'B2' is one cell."""
    m = _A1.match(str(a1).strip())
    if not m or not (m.group(1) or m.group(2)):
        raise LedgerError(f"{a1!r} isn't a cell range like A1:D5.")
    c1 = col_number(m.group(1)) if m.group(1) else None
    r1 = int(m.group(2)) if m.group(2) else None
    if m.group(3) is None and m.group(4) is None:
        return c1, r1, c1, r1
    c2 = col_number(m.group(3)) if m.group(3) else None
    r2 = int(m.group(4)) if m.group(4) else None
    return c1, r1, c2, r2


def a1_grid(a1: str, sheet_id: int) -> dict[str, Any]:
    """A Google Sheets GridRange (0-based, end exclusive) for an A1 range; open ends are left out."""
    c1, r1, c2, r2 = a1_bounds(a1)
    grid: dict[str, Any] = {"sheetId": sheet_id}
    if r1 is not None:
        grid["startRowIndex"] = r1 - 1
    if r2 is not None:
        grid["endRowIndex"] = r2
    if c1 is not None:
        grid["startColumnIndex"] = c1 - 1
    if c2 is not None:
        grid["endColumnIndex"] = c2
    return grid


def cell_rc(ref: Any) -> tuple[int, int]:
    """'B3' -> (row 3, column 2), 1-based: a cell in a table."""
    c1, r1, c2, r2 = a1_bounds(str(ref))
    if c1 is None or r1 is None or (c1, r1) != (c2, r2):
        raise LedgerError(f"{ref!r} isn't a cell like B3.")
    return r1, c1


def table_cells(op: dict[str, Any]) -> dict[tuple[int, int], str]:
    """The cells an edit names, as {(row, column): text}, 1-based. Either "cells" ({"A1": ".."}),
    "row" (a row number) with "values", "column" with "values", or "rows" (a block from "start")."""
    out: dict[tuple[int, int], str] = {}
    for ref, v in (op.get("cells") or {}).items():
        out[cell_rc(ref)] = "" if v is None else str(v)
    values = op.get("values")
    if isinstance(values, list) and op.get("row") is not None:
        r = int(op["row"])
        for c, v in enumerate(values, 1):
            out[(r, c)] = "" if v is None else str(v)
    elif isinstance(values, list) and op.get("column") is not None:
        col = op["column"]
        c = int(col) if str(col).isdigit() else col_number(str(col))
        for r, v in enumerate(values, int(op.get("from_row") or 1)):
            out[(r, c)] = "" if v is None else str(v)
    if isinstance(op.get("rows"), list):
        r0, c0 = cell_rc(op.get("start") or "A1")
        for dr, row in enumerate(op["rows"]):
            for dc, v in enumerate(row if isinstance(row, list) else [row]):
                out[(r0 + dr, c0 + dc)] = "" if v is None else str(v)
    if not out:
        raise LedgerError('Which cells? Give "cells" ({"A1": ".."}), or "row" with "values".')
    return out
