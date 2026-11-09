"""A table for the clipboard, pasted at the cursor in one step.

It goes on the clipboard twice: as tab-separated text and as an HTML table. A spreadsheet takes
it as a grid from the selected cell, a document as a real table, a chat box as plain rows. This
is the route that works on whatever is open in front of the user, in any app, any browser tab
and any account: it needs no connection to the file, only the cursor.
"""

from __future__ import annotations

import re
from html import escape
from typing import Any

MAX_ROWS = 500
MAX_COLS = 50
MAX_CHARS = 200_000

# Windows whose paste should be bare cells (no borders drawn around them).
_SHEET = re.compile(
    r"(?i)\b(excel|sheets?|spreadsheet|workbook|calc|airtable|smartsheet|numbers)\b|\.(xlsx?|xlsm|csv|ods)\b"
)


class PasteError(ValueError):
    """Rows that can't be pasted; the message is written for the model to act on."""


def looks_like_a_sheet(title: str | None) -> bool:
    return bool(_SHEET.search(title or ""))


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def table_clip(rows: Any, *, bordered: bool) -> dict[str, str]:
    """{"text": tab-separated rows, "html": the same as a table}. ``bordered`` draws cell
    borders and a bold first row, for documents; a spreadsheet gets bare cells."""
    if not isinstance(rows, list) or not rows:
        raise PasteError("give rows: a list of rows, each a list of cells.")
    grid = [[_cell(c) for c in (r if isinstance(r, list) else [r])] for r in rows]
    width = max(len(r) for r in grid)
    if len(grid) > MAX_ROWS or width > MAX_COLS:
        raise PasteError(f"that's too big to paste in one go (up to {MAX_ROWS} rows by {MAX_COLS} columns).")
    if width == 0:
        raise PasteError("give rows: a list of rows, each a list of cells.")
    grid = [r + [""] * (width - len(r)) for r in grid]
    if sum(len(c) for r in grid for c in r) > MAX_CHARS:
        raise PasteError("that's too much text to paste in one go.")
    # A tab or a line break inside a cell would split it into two cells.
    text = "\r\n".join("\t".join(re.sub(r"[\t\r\n]+", " ", c) for c in r) for r in grid)
    open_table = (
        '<table border="1" cellpadding="4" style="border-collapse:collapse">' if bordered else "<table>"
    )
    body = []
    for i, r in enumerate(grid):
        heading = bordered and i == 0 and len(grid) > 1 and any(c.strip() for c in r)
        tag = "th" if heading else "td"
        cells = "".join(f"<{tag}>{escape(c).replace(chr(10), '<br>')}</{tag}>" for c in r)
        body.append(f"<tr>{cells}</tr>")
    return {"text": text, "html": open_table + "".join(body) + "</table>"}
