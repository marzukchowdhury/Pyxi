"""A table for the clipboard, pasted at the cursor in one step.

It goes on the clipboard twice: as tab-separated text and as an HTML table. A spreadsheet takes
it as a grid from the selected cell, a document as a real table, a chat box as plain rows. This
is the route that works on whatever is open in front of the user, in any app, any browser tab
and any account: it needs no connection to the file, only the cursor.
"""

from __future__ import annotations

import json
import re
from html import escape
from typing import Any

MAX_ROWS = 500
MAX_COLS = 50
MAX_CHARS = 200_000
# The longest message the app takes for one paste (MAX in desktop/src/main/tableClip.ts). Over
# it the app pastes nothing and says nothing, so it is refused here, where the model hears why.
MAX_MESSAGE = 2_000_000

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


# What a spreadsheet runs instead of showing: =SUM(..), @SUM(..), +A1, -A1, -(A1), and the old
# "-1+cmd|..." that starts a program. "-5", "+12%" and "- a note" are left as they are.
_FORMULA = re.compile(r"^\s*(?:[=@]|[+-][A-Za-z(@=]|[+-].*\|)")


def plain_cells(rows: Any) -> tuple[Any, int]:
    """The rows with every cell a spreadsheet would run as a formula turned into plain text
    (an apostrophe in front), and how many there were. For rows whose words may have come
    from a page, mail or a file rather than from the user: their own formulas stay live."""
    if not isinstance(rows, list):
        return rows, 0
    out, changed = [], 0
    for row in rows:
        cells = list(row) if isinstance(row, list) else [row]
        for i, cell in enumerate(cells):
            if isinstance(cell, str) and _FORMULA.match(cell):
                cells[i] = "'" + cell
                changed += 1
        out.append(cells)
    return out, changed


def _plain(s: str) -> str:
    return re.sub(r"[\W_]+", "", str(s).casefold())


def check_words(text: str, limit: int = 8) -> list[str]:
    """Cells to look for on screen after a paste, to know that it arrived: the first few that
    are plain words or numbers of three characters or more, each once. Formulas are left out
    (a sheet shows their result, not their text). Empty when the table has nothing to tell
    it by (an empty table of some size)."""
    out: list[str] = []
    for cell in re.split(r"[\t\r\n]+", text or ""):
        word = _plain(cell)
        if len(word) >= 3 and not cell.lstrip().startswith(("=", "+", "@")) and word not in out:
            out.append(word)
            if len(out) == limit:
                break
    return out


def seen(lines: Any, words: list[str]) -> set[str]:
    """Which of those cells are in the text read off the screen (lines with a .text)."""
    hay = [_plain(getattr(line, "text", line)) for line in lines or []]
    whole = "".join(hay)
    return {w for w in words if any(w in h for h in hay) or w in whole}


def clip_message(clip: dict[str, str]) -> str:
    """The clip as the one message the app takes. Characters go as they are (escaped, a large
    table in Chinese or Japanese was six times its size and over the app's limit). The app
    counts the way JavaScript does, in UTF-16 units, so that is how it is counted here."""
    message = json.dumps(clip, ensure_ascii=False)
    if len(message.encode("utf-16-le", "surrogatepass")) // 2 > MAX_MESSAGE:
        raise PasteError("that's too much text to paste in one go.")
    return message


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
