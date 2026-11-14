"""Ledger building a big workbook: a plan first, then every sheet at the same time.

One reply can hold only so much. Live, 13 November: a 15-sheet financial workbook, asked for
in one go, was planned as one JSON reply, the reply hit its length limit and was cut off, and
so was the second try: "my plan came out unreadable, twice", after 75 seconds and with nothing
built. So a big job is split by Ledger itself:

1. A short call for the plan: every sheet's name, its purpose in a line, its columns in
   order, how many rows it has (or what each row is, where other sheets point at them), and
   whether a Total row follows. That fixes where everything is: headers in row 1, data from
   row 2, the totals right under the data.
2. The sheets are made (empty), then one call per sheet for that sheet's cells and formulas,
   all of them at the same time: each is written against the plan, not against the other
   sheets' cells, so none waits for another. (They used to: a sheet that read from others
   was written after them, and the same workbook took three to five minutes, most of it one
   sheet after another.) Each one's edits go into the file as they arrive, and the header's
   look is done here, with no model.
3. A check of every formula that reads another sheet, done here: a sheet that came out a
   little different from the plan (a title row above the header, a line moved down one) has
   the formulas that point at it moved to where its cells really are, and only a formula
   that still points at nothing, or an edit that failed, goes back to the model, with the
   real cells in front of it.

A reply that comes back cut off (it can't be read as JSON) costs only its own part: that sheet
is asked for again in smaller parts, a few rows at a time. If a sheet still can't be had, the
rest of the workbook is built and saved all the same, and the result says exactly which
sheets are done and which are not.

Nothing the model writes is run: the compact edits it may send here (a block of rows, a
formula filled down a column) are turned into plain ``set_cells`` edits by this file.

Where the time went, measured live on 13 November with a 15-sheet set of accounts (285 s
before, 26 to 29 s after, 52 s the once a sheet went back): sheets waiting for each other;
the model thinking before every reply, for as long as it liked and sometimes until its
length limit; and every cell sent with its own address. So the caller can hand over the same
model with thinking off (``quick``): the plan, the sheets of typed-in rows and the last step
go to it, and only a sheet that works its figures out from other sheets is left to think.

A small edit never comes here (:func:`looks_big`): a pivot or a totals row is one call, as it
always was.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from pyxi.agents.ledger_common import a1_bounds, a1_tab, col_letter, col_number

log = logging.getLogger(__name__)

BIG_BRIEF = 1500  # characters: a brief this long is planned before it is built
SPLIT_SHEETS = 4  # a brief that names this many sheets or more is planned first
MIN_SHEETS = 3  # a plan with fewer sheets than this goes the usual way, in one call
MAX_SHEETS = 40
PARALLEL = 16  # sheets with the model at the same time
MAX_PARTS = 8  # the most parts one sheet is asked for in, once it has to be cut up
PART_ROWS = 15  # rows in one part
MAX_LINES = 60  # the most rows a plan names one by one on a sheet
FILL_CELLS = 5000  # the most cells one block or one filled-down formula may write
SHORT = 9000  # characters: an unreadable reply shorter than this was not cut off at its limit
BACKOFF = 1.5  # seconds before a call that was turned away (too many at once) is made again
TRIES = 3  # how many times such a call is made in all

Ask = Callable[[str, str], Awaitable[tuple[str, float]]]
Put = Callable[[dict[str, Any]], Awaitable[str]]  # one edit: "" when it worked, else why not
Grid = dict[tuple[int, int], Any]  # a sheet's filled cells, by (row, column), 1-based

_NUMBER = {
    "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19, "twenty": 20, "dozen": 12,
}  # fmt: skip
_SHEET_COUNT = re.compile(
    rf"(?i)\b(\d{{1,3}}|{'|'.join(_NUMBER)})[- ]?(?:[a-z]+[- ]){{0,2}}?(?:work)?(?:sheets?|tabs?)\b"
)
_LISTED = re.compile(r"(?m)^\s*(?:\d{1,2}[.)]|[-*•])\s+\S")


def looks_big(task: str) -> bool:
    """Is this a build to plan first? From the brief alone: it is long, it says how many
    sheets (four or more), or it lists them. "Make a pivot of sales by region" is none of
    these, and stays one fast call."""
    for m in _SHEET_COUNT.finditer(task):
        word = m.group(1).lower()
        if (int(word) if word.isdigit() else _NUMBER[word]) >= SPLIT_SHEETS:
            return True
    return len(task) >= BIG_BRIEF or len(_LISTED.findall(task)) >= 6


PLANNER = (
    "You are {name}, the documents specialist on the user's team. The spreadsheet job below is "
    "too big to write in one reply, so plan it first; each sheet is written afterwards, in its "
    "own reply. Return JSON only:\n"
    '{"sheets": [{"name": "<tab name, 31 characters at most>", "purpose": "<one line: what '
    'it shows, how its figures are made (typed in, or worked out from which sheet), and '
    'any chart on it>", '
    '"columns": ["<header of column A>", "<of column B>", ..], '
    '"lines": ["<what column A says in row 2>", "<in row 3>", ..], '
    '"each_row": "<what one row of data is>", "rows": <how many rows of data, exactly>, '
    '"total": <true when a Total row follows the data>, '
    '"needs": ["<names of other sheets in this plan whose cells this one reads>"]}]}\n'
    "All the sheets are written at the same time, each by someone who sees only this plan, so "
    "the plan is what fixes where everything is. Every sheet has its headers in row 1, column "
    "A first, in the order you list them, and its data from row 2 down, with nothing above, "
    'between or beside. "total": true puts one Total row right after the last row of data.\n'
    '"lines" is for a sheet whose rows other sheets point at or look up one by one: a chart '
    "of accounts (its account codes), a statement or a summary (its line items in order, "
    "section headings and subtotals included), a dashboard (its figures). Give column A of "
    'every row from row 2 down, just as it will be typed there (the code alone when column '
    'A is a code), and leave "rows" out. When it is the same list as another '
    'sheet\'s, write "lines": "same as <that sheet>". A plain table of records (a journal, '
    'a list of invoices, a payroll) has "each_row" and "rows" and no "lines"; "rows" counts '
    "rows on the sheet, so a journal of 12 entries, each row one debit or one credit line, "
    "has 24, and a sheet that repeats those lines has 24 too. A sheet that works its "
    'figures out from another names it in "needs" and is built to read it with formulas. '
    "No two sheets read each other, straight or through a third: decide which way each "
    "figure flows (the cash on a balance sheet comes from the trial balance, and the cash "
    "flow reads the balance sheet, never the other way round as well).\n"
    "Use the same codes and names wherever sheets share them. List every sheet the task asks "
    "for, in the order they should appear, and none it doesn't ask for: when the task says how "
    "many sheets, or names them, plan exactly those. A sheet that is already "
    "in the file and only needs changing is listed too, under its real name. Inputs and "
    "assumptions come before the sheets that read them. Keep it short: no figures, no "
    "formulas, no em dashes. If the task is a small edit to what is already there and not "
    'several sheets to build, return {"sheets": []}.'
)


@dataclass
class Sheet:
    name: str
    purpose: str = ""
    columns: list[str] = field(default_factory=list)
    rows: int = 0
    needs: list[str] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)  # column A from row 2 down, when planned
    total: bool = False  # a Total row right under the data
    there: str = ""  # what was on it before the build, when it wasn't empty
    laid: bool = False  # its headers (and listed column A) are written
    each_row: str = ""  # what one row of its data is
    read_by: list[str] = field(default_factory=list)  # the sheets that read it, so it reads none of them
    state: str = "waiting"  # built, partly, missing
    why: str = ""  # why it is not (all) there
    edits: int = 0
    failed: list[str] = field(default_factory=list)  # edits that didn't work, with why
    reasons: list[str] = field(default_factory=list)  # just why, for the user's card

    @property
    def last(self) -> int:
        """The last row of data the plan gives it, or 0 when the plan doesn't say."""
        n = len(self.lines) or self.rows
        return n + 1 if n else 0

    @property
    def total_row(self) -> int:
        return self.last + 1 if self.total and self.last else 0

    def line(self, n: int) -> str:
        """This sheet in the plan, with the addresses the plan fixes."""
        text = f"{n}. {self.name}: {self.purpose.rstrip('.')}."
        if self.there:
            return text + " Already in the file with data on it (shown below, as it is now)."
        if self.columns:
            cols = " | ".join(f"{col_letter(i)}={c}" for i, c in enumerate(self.columns[:30], 1))
            text += f" Row 1 (headers): {cols}."
        if self.last:
            what = f", each row {self.each_row.rstrip('.')}" if self.each_row and not self.lines else ""
            text += f" Data: rows 2 to {self.last}{what}."
        if self.total_row:
            text += f" Total: row {self.total_row} (A{self.total_row} says Total)."
        if self.lines:
            text += " Column A: " + " | ".join(f"A{i}={t}" for i, t in enumerate(self.lines, 2)) + "."
        if self.needs:
            text += f" Reads from: {', '.join(self.needs)}."
        return text


@dataclass
class Built:
    sheets: list[Sheet]
    cost_usd: float = 0.0
    problems: list[str] = field(default_factory=list)  # for the user's card
    finished: bool = True  # the last step (the check of formulas between sheets) went through

    @property
    def done(self) -> list[Sheet]:
        return [s for s in self.sheets if s.state == "built"]

    @property
    def not_done(self) -> list[Sheet]:
        return [s for s in self.sheets if s.state != "built"]

    def changes(self) -> list[str]:
        return [f"Built the {s.name} sheet" for s in self.sheets if s.state == "built"] + [
            f"Started the {s.name} sheet" for s in self.sheets if s.state == "partly"
        ]

    def headline(self) -> str:
        """What was built, in words that stand on their own: every sheet by name when some
        are missing, so nobody has to open the file to find out which."""
        done, total = self.done, len(self.sheets)
        if len(done) == total:
            names = ", ".join(s.name for s in done[:6]) + (
                f" and {total - 6} more" if total > 6 else ""
            )
            text = f"Built all {total} sheets: {names}."
        else:
            text = f"Built {len(done)} of {total} sheets."
            if done:
                text += f" Done: {', '.join(s.name for s in done)}."
            partly = [s for s in self.sheets if s.state == "partly"]
            missing = [s for s in self.sheets if s.state not in ("built", "partly")]
            if partly:
                text += f" Only partly filled in: {', '.join(s.name for s in partly)}."
            if missing:
                text += f" Not built: {', '.join(s.name for s in missing)}."
            why = next((s.why for s in self.not_done if s.why), "")
            text += f" {why}" if why else ""
            text += " Ask me to finish those and I'll pick up from here."
        if not self.finished:
            text += " I couldn't finish checking the formulas that read across sheets; ask me to go over those too."
        return text


def read_json(text: str) -> dict[str, Any] | None:
    """A reply's JSON object, or None when there is none to read: cut off at the length
    limit, most often."""
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except ValueError:
        try:
            data = json.loads(_no_stray_brackets(m.group(0)))
        except ValueError:
            return None
    return data if isinstance(data, dict) else None


def _no_stray_brackets(text: str) -> str:
    """JSON with the closing brackets that close nothing taken out. Live, a reply in five
    ended ``...}]), "more": false}`` or ``...}]], "more": false}``: whole, but for one
    bracket too many. Nothing is added: a reply that was cut off still can't be read."""
    out: list[str] = []
    open_: list[str] = []
    in_text = escaped = False
    for ch in text:
        if in_text:
            out.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_text = False
            continue
        if ch == '"':
            in_text = True
        elif ch in "{[":
            open_.append("}" if ch == "{" else "]")
        elif ch in "}]":
            if not open_ or open_[-1] != ch:
                continue  # it closes nothing
            open_.pop()
        elif ch in "()":
            continue  # never JSON outside a text
        out.append(ch)
    return "".join(out)


def _clean_name(name: Any, most: int) -> str:
    return re.sub(r"[\[\]:*?/\\]", " ", str(name or "")).strip().strip("'")[:most].strip()


def _words(value: Any, most: int = 60) -> str:
    return " ".join(str(value).split())[:most]


def read_plan(data: dict[str, Any] | None, kind: str) -> list[Sheet] | None:
    """The plan from the model's reply: None when it can't be read, [] when it says this is
    not a build after all."""
    if data is None or not isinstance(data.get("sheets"), list):
        return None
    most = 31 if kind == "xlsx" else 100
    sheets: list[Sheet] = []
    same: dict[str, str] = {}  # a sheet whose lines are another sheet's
    seen: set[str] = set()
    for item in data["sheets"][:MAX_SHEETS]:
        if isinstance(item, str):
            item = {"name": item}
        if not isinstance(item, dict):
            continue
        name = _clean_name(item.get("name"), most)
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        try:
            rows = max(0, int(float(item.get("rows") or 0)))
        except (TypeError, ValueError):
            rows = 0
        cols = item.get("columns") if isinstance(item.get("columns"), list) else []
        needs = item.get("needs") if isinstance(item.get("needs"), list) else []
        lines = item.get("lines")
        if isinstance(lines, list) and len(lines) == 1 and re.match(r"(?i)\s*(the\s+)?same\s+as\s", str(lines[0])):
            lines = str(lines[0])  # "same as <sheet>", sent as a list of one
        if isinstance(lines, str):
            same[name] = re.sub(r"(?i)^\s*(the\s+)?same\s+as\s+", "", lines).strip().strip("'\".").lower()
        sheets.append(
            Sheet(
                name=name,
                purpose=" ".join(str(item.get("purpose") or "").split())[:200],
                columns=[_words(c) for c in cols if str(c).strip()][:40],
                rows=rows,
                needs=[_clean_name(n, most) for n in needs if str(n).strip()],
                lines=[_words(t, 80) for t in lines][:MAX_LINES] if isinstance(lines, list) else [],
                total=item.get("total") is True,
                each_row=_words(item.get("each_row") or "", 120),
            )
        )
    known = {s.name.lower(): s for s in sheets}
    for s in sheets:
        other = known.get(same.get(s.name, ""))
        if other is not None and other is not s:
            s.lines = list(other.lines)
        s.needs = list(
            dict.fromkeys(
                known[n.lower()].name
                for n in s.needs
                if n.lower() in known and n.lower() != s.name.lower()
            )
        )
    _no_circles(sheets)
    return sheets


def _no_circles(sheets: list[Sheet]) -> None:
    """Two sheets that read each other can never both be worked out. A need that closes a
    circle is dropped, the later sheet's first, so what is left runs one way; each sheet is
    then told which sheets read it (straight or through others) and so must not be read."""
    by_name = {s.name: s for s in sheets}
    settled: set[str] = set()
    left = [s.name for s in sheets]
    while left:
        ready = [n for n in left if all(m in settled for m in by_name[n].needs)]
        if not ready:  # a circle: the first one left goes ahead without what isn't settled
            first = by_name[left[0]]
            first.needs = [m for m in first.needs if m in settled]
            ready = [first.name]
        settled.update(ready)
        left = [n for n in left if n not in settled]

    def above(name: str, seen: set[str]) -> set[str]:
        for m in by_name[name].needs:
            if m not in seen:
                seen.add(m)
                above(m, seen)
        return seen

    reads = {s.name: above(s.name, set()) for s in sheets}
    for s in sheets:
        s.read_by = [o.name for o in sheets if s.name in reads[o.name]]


def tabs_of(target: Any) -> list[str]:
    if getattr(target, "kind", "") == "xlsx":
        return list(target.wb.sheetnames)
    return [s["properties"]["title"] for s in target.meta.get("sheets", [])]


def _blank_tab(target: Any) -> str | None:
    """The one empty tab of a brand-new file ('Sheet', 'Sheet1'), or None."""
    names = tabs_of(target)
    if len(names) != 1:
        return None
    if getattr(target, "kind", "") == "xlsx":
        ws = target.wb[names[0]]
        empty = ws.max_row <= 1 and ws.max_column <= 1 and ws.cell(1, 1).value in (None, "")
    else:
        empty = not any(any(c not in ("", None) for c in row) for row in target._seen(names[0]))
    return names[0] if empty else None


async def layout(target: Any, names: list[str], rows: int = 30) -> str:
    """What is on these tabs now, the way the outline shows it, read from the file itself
    (never remembered): the cells a formula on another sheet can point at."""
    if not names:
        return ""
    if hasattr(target, "_fresh"):  # a Google Sheet: read these tabs again if they changed
        await target._fresh(names)
    text = "\n" + await asyncio.to_thread(target.describe, rows)
    parts = []
    for name in names:
        m = re.search(
            rf"\n(?:Sheet|Tab) {re.escape(repr(name))}:.*?(?=\n\n(?:Sheet|Tab) |\nNot read yet|\Z)",
            text,
            re.S,
        )
        if m:
            parts.append(m.group(0).strip())
    return "\n\n".join(parts)


async def grids(target: Any, names: list[str]) -> dict[str, Grid]:
    """Every filled cell of these tabs as it is in the file now, formulas as they were typed."""
    out: dict[str, Grid] = {}
    if getattr(target, "kind", "") == "xlsx":

        def read() -> None:
            for name in names:
                if name in target.wb.sheetnames:
                    out[name] = {
                        at: c.value
                        for at, c in target.wb[name]._cells.items()
                        if c.value not in (None, "")
                    }

        await asyncio.to_thread(read)
        return out
    await target._fresh(names)
    for name in names:
        if name in target.grids:
            out[name] = {
                (r, c): v
                for r, row in enumerate(target.grids[name], 1)
                for c, v in enumerate(row, 1)
                if v not in (None, "")
            }
    return out


# ---------------------------------------------------------------------- compact edits
_PLACE = re.compile(r"\{r\s*([+-]\s*\d{1,3})?\}")  # {r}, {r-1}, {r+1}
_OTHER_PLACE = re.compile(r"\{[A-Za-z_][^{}\",;]*\}")  # {p}, {row}: not an array like {1,2}


def expand(edit: dict[str, Any]) -> dict[str, Any]:
    """A compact edit of a sheet's reply as the plain ``set_cells`` every kind of file takes.

    - ``{"op": "set_cells", "start": "A1", "rows": [[..], [..]]}``: a block of rows from the
      start cell; null leaves a cell alone.
    - ``{"op": "fill", "range": "E2:E13", "formula": "=C{r}*D{r}"}``: one formula down a
      column, ``{r}`` standing for each row's number (``{r-1}`` for the row above it).

    Anything else is returned as it came. Raises ValueError (with why) for one it can't read."""
    op = str(edit.get("op") or "")
    if op == "fill":
        formula = edit.get("formula", edit.get("value"))
        if formula in (None, ""):
            raise ValueError('fill needs a formula like "=C{r}*D{r}".')
        _, raw = _split(edit.get("range"))
        try:
            c1, r1, c2, r2 = a1_bounds(raw)
        except Exception:
            raise ValueError("fill needs a range like E2:E13.") from None
        if None in (c1, r1, c2, r2) or r2 < r1 or c2 < c1:
            raise ValueError("fill needs a range like E2:E13.")
        if (r2 - r1 + 1) * (c2 - c1 + 1) > FILL_CELLS:
            raise ValueError(f"fill can write {FILL_CELLS} cells at most in one go.")
        if isinstance(formula, str) and (odd := _PLACE.sub("", formula)) and (m := _OTHER_PLACE.search(odd)):
            raise ValueError(
                f"fill knows {{r}} (the row), {{r-1}} (the row above) and {{r+1}}, not {m.group(0)}."
            )

        def at(r: int, c: int) -> Any:
            if not isinstance(formula, str):
                return formula
            text = _PLACE.sub(
                lambda m: str(max(1, r + int((m.group(1) or "0").replace(" ", "")))), formula
            )
            return _dragged_right(text, c - c1) if c > c1 and text.startswith("=") else text

        cells = {
            f"{col_letter(c)}{r}": at(r, c) for r in range(r1, r2 + 1) for c in range(c1, c2 + 1)
        }
        return _set_cells(edit, cells)
    if op == "set_cells" and isinstance(edit.get("rows"), list):
        try:
            c1, r1, _, _ = a1_bounds(str(edit.get("start") or "A1"))
        except Exception:
            raise ValueError('set_cells with rows needs "start", a cell like A1.') from None
        cells = dict(edit["cells"]) if isinstance(edit.get("cells"), dict) else {}
        count = 0
        for r, row in enumerate(edit["rows"], r1 or 1):
            for c, v in enumerate(row if isinstance(row, list) else [row], c1 or 1):
                if v is None:
                    continue
                count += 1
                if count > FILL_CELLS:
                    raise ValueError(f"set_cells can write {FILL_CELLS} cells at most in one go.")
                cells[f"{col_letter(c)}{r}"] = v
        if not cells:
            raise ValueError("set_cells had no cells in its rows.")
        return _set_cells(edit, cells)
    return edit


_CELL = re.compile(r"(?<![A-Za-z0-9_.$])([A-Z]{1,3})(\$?\d+)(?![A-Za-z0-9_(!])")
_NOT_FORMULA = re.compile(r"\"[^\"]*\"|'(?:[^']|'')*'")  # a text, or a sheet's name in quotes


def _dragged_right(formula: str, by: int) -> str:
    """A formula as it reads ``by`` columns to the right of where it was written: every
    column letter moved along, but for the ones held with $. What a spreadsheet does when a
    formula is dragged across, and what a fill over several columns is taken to mean (live,
    one filled F2:G25 with ='General Journal'!F{r}, and the credits column read the debits)."""
    out, at = [], 0
    for m in _NOT_FORMULA.finditer(formula):
        out.append(_CELL.sub(lambda c: col_letter(col_number(c.group(1)) + by) + c.group(2), formula[at : m.start()]))
        out.append(m.group(0))
        at = m.end()
    out.append(_CELL.sub(lambda c: col_letter(col_number(c.group(1)) + by) + c.group(2), formula[at:]))
    return "".join(out)


def _set_cells(edit: dict[str, Any], cells: dict[str, Any]) -> dict[str, Any]:
    kept = {k: v for k, v in edit.items() if k in ("file", "sheet")}
    tab, _ = _split(edit.get("range"))
    if tab:
        kept["sheet"] = tab
    return {"op": "set_cells", **kept, "cells": cells}


def _split(a1: Any) -> tuple[str | None, str]:
    from pyxi.agents.ledger_common import split_tab

    return split_tab(a1)


# ---------------------------------------------------------------------- formulas between sheets
_REF = re.compile(
    r"(?:'((?:[^']|'')+)'|([A-Za-z_][A-Za-z0-9_.]*))!"
    r"(\$?)([A-Z]{1,3})(\$?)(\d+)(?::(\$?)([A-Z]{1,3})(\$?)(\d+))?(?![A-Za-z0-9_(])"
)
_SHEET_REF = re.compile(r"(?:'((?:[^']|'')+)'|([A-Za-z_][A-Za-z0-9_.]*))!")
_QUOTED = re.compile(r'"[^"]*"')


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value if value is not None else "").lower())


@dataclass
class Moves:
    """Where a sheet's planned cells really are, when it didn't come out as planned."""

    shift: int = 0  # every row is this many lower (a title row above the header)
    rows: dict[int, int] = field(default_factory=dict)  # a planned row, and where it is
    cols: dict[int, int] = field(default_factory=dict)
    # A table with more or fewer rows than planned: (its planned last row, its real one). A
    # range that ran to the planned last row runs to the real one.
    end: tuple[int, int] | None = None

    def __bool__(self) -> bool:
        return bool(self.shift or self.rows or self.cols or self.end)

    def at(self, col: int, row: int, *, last: bool = False) -> tuple[int, int]:
        if last and self.end and row == self.end[0]:
            return self.cols.get(col, col), self.end[1]
        return self.cols.get(col, col), self.rows.get(row, row + self.shift)


def moves(sheet: Sheet, grid: Grid) -> Moves:
    """How this sheet, as written, differs from its plan: its header row lower, a column
    somewhere else, a named line or the Total row on another row. Only what can be told for
    sure (a header or a line found in exactly one other place) is moved."""
    found = Moves()
    if sheet.there or not sheet.columns or not grid:
        return found
    wanted = [_norm(c) for c in sheet.columns]
    head, best = 1, 0
    for r in range(1, 7):
        here = {_norm(grid.get((r, c))) for c in range(1, len(wanted) + 6)} - {""}
        score = sum(1 for w in wanted if w and w in here)
        if score > best:
            head, best = r, score
    if best == 0:
        head = 1
    found.shift = head - 1
    width = max((c for _, c in grid), default=0)
    for i, w in enumerate(wanted, 1):
        if not w or _norm(grid.get((head, i))) == w or wanted.count(w) > 1:
            continue
        places = [c for c in range(1, width + 1) if _norm(grid.get((head, c))) == w]
        if len(places) == 1:
            found.cols[i] = places[0]
    label = found.cols.get(1, 1)
    depth = max((r for r, _ in grid), default=0)
    column = {r: _norm(grid.get((r, label))) for r in range(head + 1, depth + 1)}
    names = [_norm(t) for t in sheet.lines]
    for row, w in enumerate(names, 2):
        if not w or names.count(w) > 1 or column.get(row + found.shift) == w:
            continue
        places = [r for r, text in column.items() if text == w]
        if len(places) == 1 and places[0] != row + found.shift:
            found.rows[row] = places[0]
    def says_total(r: int) -> bool:
        return any(
            _norm(grid.get((r, c))).startswith(("total", "grandtotal"))
            for c in range(1, len(wanted) + 2)
        )

    total = 0  # the row its Total is really on
    if sheet.total_row:
        total = sheet.total_row + found.shift
        if not says_total(total):
            places = [r for r in column if says_total(r)]
            total = places[-1] if places else 0
            if total:
                found.rows[sheet.total_row] = total
    if sheet.last and not sheet.lines:
        # A table of records: as many rows as were written, whatever the plan guessed.
        filled = {r for r, _ in grid if r > head and r != total}
        last = max((r for r in filled if not total or r < total), default=0)
        if last and last != sheet.last + found.shift:
            found.end = (sheet.last, last)
    return found


def _nearly(said: str, names: dict[str, str]) -> str | None:
    """The one sheet a name that isn't any sheet's must mean: 'General Journal' in a file
    whose journal is called Journal. None when no sheet, or more than one, could be meant."""
    want = _norm(said)
    if len(want) < 4:
        return None
    close = {t for t in names.values() if len(_norm(t)) >= 4 and (_norm(t) in want or want in _norm(t))}
    return close.pop() if len(close) == 1 else None


def remap(formula: str, own: str, names: dict[str, str], moved: dict[str, Moves]) -> str:
    """A formula with what it reads on other sheets pointed at where those cells really are:
    a sheet's name as the file spells it, and the rows and columns of a sheet that came out
    different from its plan. A formula with nothing to move comes back as it was."""

    def fix(m: re.Match[str]) -> str:
        said = (m.group(1) or m.group(2)).replace("''", "'")
        real = names.get(said.lower()) or names.get(_norm(said)) or _nearly(said, names)
        if real is None or real.lower() == own.lower():
            return m.group(0)
        mv = moved.get(real)
        c1, r1 = col_number(m.group(4)), int(m.group(6))
        if mv:
            c1, r1 = mv.at(c1, r1)
        text = f"{a1_tab(real)}!{m.group(3)}{col_letter(c1)}{m.group(5)}{r1}"
        if m.group(8):
            c2, r2 = col_number(m.group(8)), int(m.group(10))
            if mv:
                c2, r2 = mv.at(c2, r2, last=True)
            text += f":{m.group(7)}{col_letter(c2)}{m.group(9)}{r2}"
        if real == said and not mv:
            return m.group(0)
        return text

    def spelled(m: re.Match[str]) -> str:
        said = (m.group(1) or m.group(2)).replace("''", "'")
        if said.lower() in names:
            return m.group(0)
        real = names.get(_norm(said)) or _nearly(said, names)
        return f"{a1_tab(real)}!" if real else m.group(0)

    return _REF.sub(fix, _SHEET_REF.sub(spelled, formula))


def broken(
    formula: str,
    own: str,
    names: dict[str, str],
    cells: dict[str, Grid],
    tables: frozenset[str] = frozenset(),
) -> list[str]:
    """What this formula reads on other sheets that isn't there: a sheet the file doesn't
    have, a cell with nothing in it, a range with nothing anywhere in it. On a sheet in
    ``tables`` (plain rows of records) a blank cell in a row that is there, under a column
    that is there, is a blank and not a mistake: the credit of a debit line."""
    bad: list[str] = []
    formula = _QUOTED.sub('""', formula)  # "Done!" in a text is not a sheet
    for m in _SHEET_REF.finditer(formula):
        said = (m.group(1) or m.group(2)).replace("''", "'")
        if said.lower() not in names and f"there is no sheet {said}" not in bad:
            bad.append(f"there is no sheet {said}")
    for m in _REF.finditer(formula):
        said = (m.group(1) or m.group(2)).replace("''", "'")
        real = names.get(said.lower())
        if real is None or real.lower() == own.lower():
            continue
        grid = cells.get(real, {})
        c1, r1 = col_number(m.group(4)), int(m.group(6))
        if not m.group(8):
            if (r1, c1) in grid:
                continue
            blank = (
                real in tables
                and any(r == r1 for r, _ in grid)
                and any(c == c1 and r != r1 for r, c in grid)
            )
            if not blank:
                bad.append(f"{m.group(0)} is empty")
            continue
        c2, r2 = col_number(m.group(8)), int(m.group(10))
        lo_r, hi_r, lo_c, hi_c = min(r1, r2), max(r1, r2), min(c1, c2), max(c1, c2)
        if not any(lo_r <= r <= hi_r and lo_c <= c <= hi_c for r, c in grid):
            bad.append(f"{m.group(0)} has nothing in it")
    return bad


_OWN_REF = re.compile(
    r"(?<![!A-Za-z0-9_$'.])\$?([A-Z]{1,3})\$?(\d+)(?::\$?([A-Z]{1,3})\$?(\d+))?(?![A-Za-z0-9_(!])"
)


_COUNTS = re.compile(r"(?i)\b(ROWS?|COLUMNS?)\s*\([^()]*\)")


def reads_itself(formula: str, row: int, col: int) -> bool:
    """Does this formula read the cell it is in (=B4 typed into B4, or a sum whose range
    takes in its own cell)? A spreadsheet shows an error there, or 0."""
    bare = _REF.sub("0", _QUOTED.sub('""', formula))
    bare = _COUNTS.sub("0", bare)  # ROWS($A$2:A2) counts rows; it reads none of them
    for m in _OWN_REF.finditer(bare):
        c1, r1 = col_number(m.group(1)), int(m.group(2))
        c2, r2 = (col_number(m.group(3)), int(m.group(4))) if m.group(3) else (c1, r1)
        if min(r1, r2) <= row <= max(r1, r2) and min(c1, c2) <= col <= max(c1, c2):
            return True
    return False


_NAME = re.compile(r"(?<![A-Za-z0-9_.$!'#\[@])([A-Za-z_][A-Za-z0-9_.]*)(?![A-Za-z0-9_.(\[\]!'])")
_NOT_NAMES = re.compile(r"(?i)^(\$?[A-Z]{1,3}\$?\d+|true|false|[A-Z]{1,3}|e\d*)$")


def made_up_names(formula: str) -> list[str]:
    """Words in a formula that are neither a function, a cell nor a sheet: a name nobody
    defined (=Cash_Placeholder), which a spreadsheet shows as #NAME?."""
    if re.search(r"(?i)\b(LET|LAMBDA)\s*\(", formula):
        return []  # those name their own values
    bare = _SHEET_REF.sub("", _QUOTED.sub('""', formula))
    return list(dict.fromkeys(w for w in _NAME.findall(bare) if not _NOT_NAMES.match(w)))


def to_the_end(formula: str, end: tuple[int, int]) -> str:
    """A sheet's own Total, written for the rows the plan gave it, over the rows it has:
    =SUM(E2:E13) on a table that ran to row 25 becomes =SUM(E2:E25)."""
    planned, real = end
    return re.sub(
        rf"(?<![!A-Za-z0-9_$'.])(\$?[A-Z]{{1,3}}\$?\d+:\$?[A-Z]{{1,3}}\$?){planned}(?![0-9A-Za-z_(!])",
        rf"\g<1>{real}",
        formula,
    )


def _turned_away(exc: Exception) -> bool:
    """Was this call refused because too many were in flight (or the service was busy)? Then
    it is worth making again in a moment; any other failure is not."""
    status = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
    if status in (408, 409, 425, 429, 500, 502, 503, 504, 529):
        return True
    text = f"{type(exc).__name__} {exc}".lower()
    return any(w in text for w in ("ratelimit", "rate limit", "rate-limit", "overloaded", "too many requests"))


_SHEET_RULES = """Rules for this reply:
- Every edit here is for the sheet {name}: leave "file" and "sheet" out. Never add, rename or delete a sheet: they are all made.
- Keep to the plan's layout for this sheet exactly: its headers in row 1 in the order given, data from row 2, the rows the plan names in the rows it names them, one Total row right under the last row of data when the plan gives one ("Total" in column A), and nothing above or between (no title row, no blank spacer rows). The other sheets are being written right now, with formulas that point at those addresses. Write whole rows, column A included, just as the plan lists them.
- You cannot see the other sheets' cells, only where the plan puts them. So a sheet that repeats another's rows does it with formulas, row for row (={other}!C2 filled down), and a sheet that sums another's rows by a code uses SUMIFS over the range the plan gives. Never guess another sheet's figures.
- Write cells in blocks: {{"op": "set_cells", "start": "A1", "rows": [["Code", "Name", "Amount"], [1000, "Cash", 250]]}} fills whole rows from the start cell (null leaves a cell alone). A formula that repeats down a column is ONE edit, never a cell each: {{"op": "fill", "range": "E2:E13", "formula": "=C{{r}}*D{{r}}"}} ({{r}} is each row's own number, {{r-1}} the row above it: a running balance is "=H{{r-1}}+F{{r}}-G{{r}}" from the second row of data). Over several columns a fill moves the column letters along, as dragging a formula to the right does ($ before a letter holds it). Single cells: {{"op": "set_cells", "cells": {{"B15": "=SUM(B2:B14)"}}}}.
- Formulas, not typed-in results, wherever a number follows from other cells, so the file stays live. A formula may read another sheet at the addresses the plan gives for it, under the name the plan gives it, written like ={other}!C17 or SUMIFS({other}!E2:E31,{other}!C2:C31,A2). Never point at a cell the plan doesn't place; work the figure out from cells it does place.
- A figure that comes from another sheet is a formula that reads it, never the number typed in again.
- Looks: making row 1 bold, freezing it and setting column widths happen afterwards without you, so send no edits for those. Send number formats only, one format edit for each block of columns.
- A chart the brief asks for on this sheet is a chart edit here, after its cells: {{"op": "chart", "labels": "<header>", "values": "<header>", "type": "bar", "title": "..", "at": "H2"}}. A chart draws two columns of the sheet it is on, from row 1 down. To chart figures that live on another sheet, bring them onto this sheet first: a small table with its own two headers in row 1, in the first free columns to the right of the planned ones, each cell a formula that reads the other sheet; then chart those two columns.
- Made-up sample data: as many rows as the plan says, 10 to 12 when it doesn't, figures that add up (in a journal, every entry's debits equal its credits) and dates that exist, written like 2026-03-31. Keep the whole reply under about 250 cells; if this sheet needs more, send the first {part_rows} rows and set "more": true.
Return JSON only: {{"edits": [..], "more": false}}."""


class Builder:
    def __init__(
        self,
        task: str,
        *,
        target: Callable[[], Awaitable[Any]],
        put: Put,
        ask: Ask,
        system: str,
        name: str = "Ledger",
        context: str = "",
        progress: Callable[[str], Awaitable[None]] | None = None,
        least: int | None = None,
        quick: Ask | None = None,
    ) -> None:
        # The fewest sheets a plan needs to be built this way. One, when the usual single
        # call has already been cut off twice: then anything is better written in parts.
        self.least = MIN_SHEETS if least is None else least
        self.task = task
        self.target = target  # the file as it stands now (reopened if an edit died on it)
        self.put = put
        self._ask = ask
        # The same model answering straight away, with no thinking first. Left to think, a
        # call takes as long as it likes and sometimes its whole length limit (live: a plan
        # that thought for 30 s and was cut off before its JSON ended). So the plan, the
        # sheets of typed-in rows, the last step and anything asked for a second time go to
        # this one, and only a sheet that reads other sheets is given time to think.
        self._quick = quick or ask
        self.system = system
        self.name = name
        self.context = context
        self.progress = progress
        self.cost = 0.0
        self.gate = asyncio.Semaphore(PARALLEL)
        self.hold = 0.0  # no new call before this moment: one was just turned away
        self.writing = asyncio.Lock()  # one reply's edits go into the file at a time
        self.sheets: list[Sheet] = []
        self.count = 0
        self.late: list[str] = []  # what the last step couldn't put right

    async def ask(self, system: str, prompt: str, *, quick: bool = False) -> dict[str, Any] | None:
        """One call, several at a time. None: the reply couldn't be read, or the call failed."""
        return (await self.reply(system, prompt, quick=quick))[0]

    async def reply(
        self, system: str, prompt: str, *, quick: bool = False
    ) -> tuple[dict[str, Any] | None, int]:
        """One call: what came back as JSON (None when it couldn't be read, or the call
        failed) and how long the reply was, in characters. A call turned away because too
        many were in flight waits a moment and is made again, and the calls that haven't
        started yet wait with it."""
        async with self.gate:
            began = time.perf_counter()
            text = ""
            for attempt in range(TRIES):
                if (wait := self.hold - time.monotonic()) > 0:
                    await asyncio.sleep(wait)
                try:
                    text, cost = await (self._quick if quick else self._ask)(system, prompt)
                    break
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    if attempt + 1 == TRIES or not _turned_away(exc):
                        log.warning("ledger build: a model call failed", exc_info=True)
                        return None, 0
                    pause = BACKOFF * 2**attempt * (1 + random.random() / 2)
                    self.hold = max(self.hold, time.monotonic() + pause)
                    log.warning("ledger build: turned away (%s); again in %.1fs", type(exc).__name__, pause)
            self.cost += cost
            data = read_json(text)
            log.info(
                "ledger build: a call took %.1fs, %d characters back%s",
                time.perf_counter() - began,
                len(text or ""),
                "" if data is not None else " (cut off or unreadable)",
            )
            if data is None and text:
                log.info("ledger build: unreadable reply ends %r", text[-160:])
            return data, len(text or "")

    async def say(self, line: str) -> None:
        if self.progress:
            try:
                await self.progress(line)
            except Exception:  # a progress line is never worth the job
                log.debug("ledger build: progress line dropped", exc_info=True)

    def brief(self) -> str:
        head = f"Context from your teammates:\n{self.context[:3000]}\n\n" if self.context else ""
        return f"{head}Task (the whole brief):\n{self.task}"

    def plan_text(self) -> str:
        text = "\n".join(s.line(n) for n, s in enumerate(self.sheets, 1))
        there = "\n\n".join(s.there for s in self.sheets if s.there)
        return text + (f"\n\nThe sheets that were already in the file:\n{there}" if there else "")

    def shared(self) -> str:
        """The start of every sheet's prompt, the same for all of them."""
        return (
            f"{self.brief()}\n\nThe workbook is being built one sheet to a reply, all the sheets "
            "at the same time. The plan, with where everything on each sheet is:\n"
            f"{self.plan_text()}\n\nEvery sheet in the plan is already in the file"
            " (empty until it is written).\n\n"
        )

    # ------------------------------------------------------------------ the plan
    async def plan(self) -> list[Sheet] | None:
        """The sheets to build, or None when this is not a build to split (or no plan could
        be had): the job then goes the usual way, in one call."""
        target = await self.target()
        kind = getattr(target, "kind", "")
        try:
            there = await asyncio.to_thread(target.describe, 6)
        except Exception:
            there = ""
        system = PLANNER.replace("{name}", self.name)
        prompt = f"{self.brief()}\n\nThe file now:\n{there[:6000]}"
        await self.say("planning the sheets")
        for attempt in range(2):
            data = await self.ask(
                system,
                prompt
                if attempt == 0
                else prompt
                + "\n\nYour last reply could not be read: it was cut off or was not the JSON "
                "asked for. Again, shorter: for each sheet only its name, a purpose of five "
                'words, its columns, its rows and what it needs; "lines" only where another '
                "sheet points at them.",
                quick=True,
            )
            sheets = read_plan(data, kind)
            if sheets is not None:
                return sheets if len(sheets) >= self.least else None
        return None

    # ------------------------------------------------------------------ the sheets
    async def make_tabs(self) -> None:
        """Every planned sheet, made empty and in order, so each can be written on its own
        and a formula can name any of them. A sheet that was there already, with something
        on it, is read: the others are shown it as it is, not as the plan imagines it."""
        target = await self.target()
        tabs = {t.lower(): t for t in tabs_of(target)}
        blank = _blank_tab(target)
        old = [s for s in self.sheets if s.name.lower() in tabs and tabs[s.name.lower()] != blank]
        for s in self.sheets:
            if s.name.lower() in tabs:
                continue
            if blank is not None:  # a new file's "Sheet1" becomes the first sheet of the plan
                why = await self.put({"op": "rename_sheet", "sheet": blank, "name": s.name})
                blank = None
            else:
                why = await self.put({"op": "add_sheet", "name": s.name})
            if why:
                s.state, s.why = "missing", why
        for s in old:
            s.name = tabs[s.name.lower()]  # as the file spells it
            mine = await self.look([s.name], 30)
            if mine.count("\n"):  # more than its one-line heading: it has data
                s.there = mine

    async def lay_out(self, sheet: Sheet) -> None:
        """What the plan fixed for this sheet, written here before its first edits: the
        headers in row 1 and, where the plan lists it, column A. Other sheets point at these
        rows whether or not this sheet's own reply remembers to write them. Holding
        ``writing``."""
        if sheet.laid or sheet.there or not sheet.columns:
            return
        sheet.laid = True
        cells: dict[str, Any] = {f"{col_letter(i)}1": c for i, c in enumerate(sheet.columns, 1)}
        for r, text in enumerate(sheet.lines, 2):
            cells[f"A{r}"] = int(text) if re.fullmatch(r"\d{1,9}", text) else text
        if why := await self.put({"op": "set_cells", "sheet": sheet.name, "cells": cells}):
            log.info("ledger build: the headers of %s didn't go in: %s", sheet.name, why)

    async def apply(self, sheet: Sheet, edits: list[Any]) -> int:
        """One reply's edits, in order, onto this sheet. Returns how many worked."""
        worked = 0
        async with self.writing:
            if any(isinstance(e, dict) and e.get("op") for e in edits):
                await self.lay_out(sheet)
            for e in edits:
                if not isinstance(e, dict) or not e.get("op"):
                    continue
                op = str(e["op"])
                if op in ("add_sheet", "delete_sheet", "rename_sheet", "duplicate_sheet"):
                    continue  # the tabs are made: a reply for one sheet doesn't reshape the file
                try:
                    e = expand(e)
                except ValueError as exc:
                    sheet.failed.append(f"{json.dumps(e, default=str)[:200]}: {exc}")
                    sheet.reasons.append(str(exc))
                    continue
                if "!" not in str(e.get("range") or ""):
                    e = {**e, "sheet": sheet.name}  # this reply is about this sheet, whatever it says
                why = await self.put(e)
                if why:
                    sheet.failed.append(f"{json.dumps(e, default=str)[:200]}: {why}")
                    sheet.reasons.append(why)
                else:
                    worked += 1
        sheet.edits += worked
        return worked

    async def dress(self, sheet: Sheet) -> None:
        """The look every sheet's header gets, with no model: bold, frozen, wide enough to
        read. A sheet that was in the file before is left as its owner had it."""
        if sheet.there or not sheet.columns or not sheet.edits:
            return
        end = col_letter(len(sheet.columns))
        first = max([len(sheet.columns[0]), *(len(t) for t in sheet.lines)])
        rest = max((len(c) for c in sheet.columns[1:]), default=10)
        edits: list[dict[str, Any]] = [
            {"op": "format", "range": f"A1:{end}1", "bold": True},
            {"op": "freeze", "rows": 1},
            {"op": "resize", "columns": "A:A", "width": min(320, max(110, 8 * first + 20))},
        ]
        if len(sheet.columns) > 1:
            edits.append({"op": "resize", "columns": f"B:{end}", "width": min(200, max(110, 8 * rest + 24))})
        async with self.writing:
            for e in edits:
                if why := await self.put({**e, "sheet": sheet.name}):
                    log.info("ledger build: %s on %s didn't go in: %s", e["op"], sheet.name, why)

    async def look(self, names: list[str], rows: int = 30) -> str:
        """What is on these sheets now. Never while a reply's edits are going in: the file is
        read whole, between edits."""
        async with self.writing:
            return await layout(await self.target(), names, rows)

    def sheet_prompt(self, sheet: Sheet, n: int) -> str:
        return (
            self.shared()
            + (
                f"{sheet.name!r} is not empty: it is shown above. Change only what the brief asks "
                "for on this sheet: what is there stays, at the addresses it is at now.\n\n"
                if sheet.there
                else ""
            )
            + f"Now write ONLY the sheet {sheet.name!r} (number {n} of {len(self.sheets)}).\n"
            + self.flow(sheet)
            + self.rules(sheet, PART_ROWS)
        )

    def rules(self, sheet: Sheet, part_rows: int) -> str:
        """The rules for a sheet's reply, their examples naming a sheet of this plan: live,
        examples that said 'General Journal' were copied into a workbook whose journal was
        called Journal, and 144 formulas read a sheet that wasn't there."""
        others = [*sheet.needs, *(s.name for s in self.sheets if s.name != sheet.name), "Sheet 2"]
        return _SHEET_RULES.format(name=sheet.name, part_rows=part_rows, other=a1_tab(others[0]))

    def flow(self, sheet: Sheet) -> str:
        """Which way the figures run past this sheet: what it reads, and what reads it."""
        text = ""
        if sheet.needs:
            text += (
                f"It takes its figures from {', '.join(sheet.needs)} with formulas, at the "
                "addresses the plan gives for them.\n"
            )
        if sheet.read_by:
            text += (
                f"These sheets read this one, so no formula here may read them (that would go "
                f"round in a circle): {', '.join(sheet.read_by)}.\n"
            )
        if re.search(r"(?i)\b(charts?|graphs?)\b", sheet.purpose):
            text += "The plan gives this sheet a chart: the chart edit is part of this reply.\n"
        return text

    async def build_sheet(self, sheet: Sheet, n: int) -> None:
        try:
            if sheet.state == "missing":
                return  # its tab couldn't be made
            prompt = self.sheet_prompt(sheet, n)
            # A sheet of its own typed-in rows is asked for straight away. One that works
            # its figures out from other sheets is given time to think: live, asked straight
            # away, a ledger typed twelve entries of its own beside the journal's.
            thinks = bool(sheet.needs)
            data, size = await self.reply(self.system, prompt, quick=not thinks)
            if data is None and self._quick is not self._ask and (thinks or 0 < size < SHORT):
                # Not a reply cut off for being long: either the thinking used up the length
                # limit before a cell was written, or it was too short to have reached the
                # limit and was simply not the JSON asked for. Asked once more, whole and
                # straight away; only a reply that was really cut off goes in parts.
                data = await self.ask(
                    self.system,
                    prompt + "\n\nYour last reply could not be read. Again: one JSON object and "
                    'nothing else, every " inside a formula written as \\".',
                    quick=True,
                )
            more = False
            if data is not None:
                await self.apply(sheet, data.get("edits") if isinstance(data.get("edits"), list) else [])
                more = bool(data.get("more"))
            if data is None or more:
                # Too much for one reply (or it said so itself): this sheet, in parts.
                await self.in_parts(sheet, n, started=data is not None)
            elif sheet.edits:
                sheet.state = "built"
            else:
                sheet.state = "missing"
                sheet.why = sheet.why or (
                    f"None of the edits for {sheet.name} worked."
                    if sheet.failed
                    else f"No edits came back for {sheet.name}."
                )
            await self.dress(sheet)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # one sheet's trouble is that sheet's, never the workbook's
            log.exception("ledger build: the sheet %s failed", sheet.name)
            sheet.state = "partly" if sheet.edits else "missing"
            sheet.why = sheet.why or f"{sheet.name} hit a problem ({str(exc)[:120]})."
        finally:
            self.count += 1
            if sheet.state == "built":
                await self.say(f"built {sheet.name} ({self.count} of {len(self.sheets)})")

    async def in_parts(self, sheet: Sheet, n: int, *, started: bool) -> None:
        """A sheet too long for one reply, asked for a few rows at a time. Each part is put in
        the file before the next is asked for, and the next is shown what is there. A part
        that can't be read is asked for once more, smaller; if that fails too, what is in the
        sheet stays and the result says it is only partly there."""
        await self.say(f"{sheet.name} is long, so it goes in in parts")
        rows = PART_ROWS
        misses = 0
        for part in range(1, MAX_PARTS + 1):
            there = await self.look([sheet.name], 14)
            first = part == 1 and not started and not sheet.edits
            prompt = (
                self.shared()
                + f"The sheet {sheet.name!r} (number {n} of {len(self.sheets)}) is too long for one "
                "reply, so it is written in parts.\n"
                + (
                    f"This is the first part: the header row and the first {rows} rows only.\n"
                    if first
                    else f"What is on it so far:\n{there}\n\nSend the NEXT part only: the next "
                    f"{rows} rows at most, carrying on right under what is there, or, when the "
                    "rows are all in, its number formats. Do not send again what is already there.\n"
                )
                + self.rules(sheet, rows)
                + '\n"more" is true while anything of this sheet is still to come.'
            )
            data = await self.ask(self.system, prompt, quick=True)
            if data is None:
                misses += 1
                if misses >= 2:
                    sheet.state = "partly" if sheet.edits else "missing"
                    sheet.why = (
                        f"The plan for {sheet.name} came back cut off, even in small parts."
                    )
                    return
                rows = max(5, rows // 2)  # asked again, smaller
                continue
            misses = 0
            worked = await self.apply(
                sheet, data.get("edits") if isinstance(data.get("edits"), list) else []
            )
            if not data.get("more"):
                sheet.state = "built" if sheet.edits else "missing"
                if not sheet.edits:
                    sheet.why = f"None of the edits for {sheet.name} worked."
                return
            if not worked:
                break  # it says there is more, and nothing it sent went in: stop asking
        sheet.state = "partly" if sheet.edits else "missing"
        sheet.why = sheet.why or f"{sheet.name} was longer than I could write in {MAX_PARTS} parts."

    # ------------------------------------------------------------------ the last step
    async def cells(self) -> tuple[dict[str, str], dict[str, Grid]]:
        """Every tab of the file as it is now: its name by its lower-case (and its bare)
        spelling, and its filled cells."""
        async with self.writing:
            target = await self.target()
            tabs = tabs_of(target)
            found = await grids(target, tabs)
        names = {_norm(t): t for t in tabs} | {t.lower(): t for t in tabs}
        return names, found

    async def settle(self) -> None:
        """Formulas that read a sheet which came out different from its plan, pointed at
        where its cells really are. Done here, with no model: the plan says where a line was
        meant to be, the file says where it is."""
        names, found = await self.cells()
        moved = {s.name: mv for s in self.sheets if (mv := moves(s, found.get(s.name, {})))}
        for name, mv in moved.items():
            log.info("ledger build: %s is not where the plan put it: %s", name, mv)
        for s in self.sheets:
            cells = {}
            mine = moved.get(s.name)
            for (r, c), v in found.get(s.name, {}).items():
                if not (isinstance(v, str) and v.startswith("=")):
                    continue
                new = remap(v, s.name, names, moved) if "!" in v else v
                if mine and mine.end and r > mine.end[1]:  # its own Total, under its rows
                    new = to_the_end(new, mine.end)
                if new != v and not reads_itself(new, r, c):
                    cells[f"{col_letter(c)}{r}"] = new
            if cells:
                log.info("ledger build: moved %d formulas on %s to the real cells", len(cells), s.name)
                async with self.writing:
                    if why := await self.put({"op": "set_cells", "sheet": s.name, "cells": cells}):
                        self.late.append(why)

    async def astray(self) -> dict[str, dict[str, list[str]]]:
        """By sheet: its formulas that read something on another sheet which isn't there."""
        names, found = await self.cells()
        real = {t.lower(): t for t in names.values()}
        tables = frozenset(s.name for s in self.sheets if not s.lines and not s.there)
        out: dict[str, dict[str, list[str]]] = {}
        for s in self.sheets:
            for (r, c), v in sorted(found.get(s.name, {}).items()):
                if not (isinstance(v, str) and v.startswith("=")):
                    continue
                bad = broken(v, s.name, real, found, tables) if "!" in v else []
                if reads_itself(v, r, c):
                    bad.append("it reads its own cell")
                if _PLACE.search(v) or _OTHER_PLACE.search(_QUOTED.sub('""', v)):
                    bad.append("it still has a {placeholder} in it")
                if not s.there and (words := made_up_names(v)):
                    bad.append(f"nothing is called {', '.join(words[:3])}")
                if bad:
                    out.setdefault(s.name, {})[f"{col_letter(c)}{r}"] = [v, *bad]
        return out

    async def mend(self, sheet: Sheet, wrong: dict[str, list[str]]) -> bool:
        """One sheet's formulas that point at nothing, and its edits that failed, back to
        the model with the real cells in front of it. False when no reply could be read."""
        reads = sorted(
            {
                name
                for bad in wrong.values()
                for m in _SHEET_REF.finditer(bad[0])
                if (name := (m.group(1) or m.group(2)).replace("''", "'")) != sheet.name
            }
            | set(sheet.needs)
        )
        known = {t.lower() for t in tabs_of(await self.target())}
        there = await self.look([n for n in reads if n.lower() in known][:8], 40)
        mine = await self.look([sheet.name], 40)
        listed = "\n".join(
            f"- {at}: {bad[0][:200]}  ({'; '.join(bad[1:4])})" for at, bad in list(wrong.items())[:60]
        )
        failed = "\n".join(f"- {f}" for f in sheet.failed[:8])
        prompt = (
            f"{self.brief()}\n\nThe workbook was built one sheet to a reply. The plan:\n"
            f"{self.plan_text()}\n\nThis is the last step, for the sheet {sheet.name!r} only. "
            "The sheets it reads, as they really are now:\n"
            f"{there[:16000]}\n\n{sheet.name!r} as it is now:\n{mine[:8000]}\n\n"
            + (
                f"These formulas on {sheet.name!r} can't work as they are (why is in brackets):\n"
                f"{listed}\n\n"
                if listed
                else ""
            )
            + (f"These edits for {sheet.name!r} failed and are not in the file:\n{failed}\n\n" if failed else "")
            + "Send ONLY what puts those right: each formula again, reading the real addresses "
            "shown above (or, when the row it was for doesn't exist on the other sheet, a clear "
            "edit for that row here), and a corrected version of a failed edit that still "
            "matters. Do not point at an empty cell, do not write again what is already right, "
            "and never add, rename or delete a sheet. Keep it short: a formula that repeats "
            'down a column is one edit, {"op": "fill", "range": "E2:E13", "formula": '
            '"=C{r}*D{r}"} ({r} is each row\'s own number), not a cell each. '
            'Return JSON only: {"edits": [..]}; {"edits": []} when nothing can be done.'
        )
        for attempt in range(2):
            data = await self.ask(
                self.system,
                prompt
                if attempt == 0
                else prompt + "\n\nYour last reply was cut off. Again, the 15 most important edits only.",
                # Straight away: with the real cells in front of it this is looking an
                # address up, and what it sends is checked again afterwards. Live, given
                # time to think, one took 18 s to send 24 formulas a cell at a time.
                quick=True,
            )
            if data is None:
                continue
            before = len(sheet.failed)
            if await self.apply(sheet, data.get("edits") if isinstance(data.get("edits"), list) else []):
                # What failed the first time was tried again: only what fails now is said.
                del sheet.failed[:before]
                del sheet.reasons[:before]
            return True
        return False

    async def finish(self) -> bool:
        """What belongs to the workbook as a whole: every formula that reads another sheet
        reads a cell that is there. Put right here where it can be (:meth:`settle`); what is
        left, and any edit that failed, goes back to the model one sheet at a time, side by
        side. True when it went through (or there was nothing to put right)."""
        built = [s for s in self.sheets if s.state in ("built", "partly")]
        if not built:
            return True
        await self.say("checking the formulas between sheets")
        await self.settle()
        wrong = await self.astray()
        again = [s for s in built if s.name in wrong or s.failed]
        if not again:
            return True
        log.info(
            "ledger build: %d sheets go back for formulas that read nothing or edits that failed: %s",
            len(again),
            ", ".join(f"{s.name} ({len(wrong.get(s.name, {}))})" for s in again),
        )
        await self.say("fixing formulas that read the wrong cells")
        went = await asyncio.gather(*(self.mend(s, wrong.get(s.name, {})) for s in again))
        await self.settle()
        for name, bad in (await self.astray()).items():
            at = ", ".join(list(bad)[:6]) + (" and more" if len(bad) > 6 else "")
            self.late.append(
                f"{len(bad)} formula{'s' if len(bad) != 1 else ''} on {name} still read"
                f"{'s' if len(bad) == 1 else ''} a cell with nothing in it ({at})."
            )
        return all(went)

    async def run(self) -> Built | None:
        """Plan, build, check. None when the job is not one to split: nothing was changed,
        and the caller does it the usual way."""
        plan = await self.plan()
        if plan is None:
            return None
        self.sheets = plan
        await self.say(f"building {len(plan)} sheets")
        await self.make_tabs()
        tasks = [asyncio.ensure_future(self.build_sheet(s, n)) for n, s in enumerate(plan, 1)]
        try:
            await asyncio.gather(*tasks)
        except BaseException:
            for t in tasks:
                t.cancel()
            raise
        finished = await self.finish()
        problems = [s.why for s in plan if s.why and s.state != "built"]
        problems += [why for s in plan if s.state == "built" for why in s.reasons[:2]]
        problems += self.late
        return Built(
            sheets=plan,
            cost_usd=self.cost,
            problems=list(dict.fromkeys(problems)),
            finished=finished,
        )
