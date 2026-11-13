"""Ledger in the app itself: the workbook, document or deck that is OPEN in Excel, Word or
PowerPoint on this PC, edited through the app's own automation (COM) instead of the mouse.

The hands (companion/operator.py) look at the screen, ask a model, click, and look again: three
bold red headings in a desktop spreadsheet took 52 seconds. The same edits here are calls that
take milliseconds each, so a job costs one model answer and about a second of work.

Nothing new is said to the model. It is shown the same kind of outline Ledger makes of a file
and answers with the same typed edits (``set_cells``, ``totals_row``, ``format``, ``pivot``...);
each one is checked here and turned into fixed calls. Nothing the model writes is ever run.

What this never does: save, close or delete a document, switch the app's alerts off, or send
the document anywhere Ledger does not already send an outline. An edit that would replace or
remove more than a few filled cells asks on a card first.

Every call into the app runs on one thread of its own with a time limit (the app's objects
belong to the thread that got them, and a box open in the app can hold a call for as long as
it stays open). When the app can't be reached, is busy, or doesn't answer in time, the edit
ends with :class:`Unreachable` and the caller goes back to the way it worked before.

Undo: Word and PowerPoint take these edits back, each edit as one step. Excel as a rule does
not. Measured on Excel 16: after cells were typed, made bold and coloured, a pivot added and
rows sorted this way, Undo was empty (what the user had done before was gone from it too);
only clearing cells could be undone. That is Excel's own rule for automated changes, so the
card that asks before cells are replaced says so.
"""

from __future__ import annotations

import asyncio
import gc
import logging
import queue
import re
import sys
import threading
import time
from collections.abc import Awaitable, Callable
from datetime import date, datetime
from typing import Any

from pyxi.agents.ledger_common import (
    LedgerError,
    a1_bounds,
    cell_rc,
    col_letter,
    col_number,
    hex_color,
    split_tab,
)

log = logging.getLogger(__name__)

FEW = 3  # more filled cells than this replaced or removed by one edit: ask first
TIMEOUT_S = 8.0  # one edit; the slowest measured (a chart) took a third of a second
PROBE_S = 3.0  # "is it there?", asked before a job starts
_MAX_CELLS = 400_000  # a sheet bigger than this is not read whole

APPS: dict[str, dict[str, Any]] = {
    "excel": {"label": "Excel", "progid": "Excel.Application", "exe": "excel.exe",
              "top": "XLMAIN", "pane": "EXCEL7", "thing": "workbook"},
    "word": {"label": "Word", "progid": "Word.Application", "exe": "winword.exe",
             "top": "OpusApp", "pane": "_WwG", "thing": "document"},
    "powerpoint": {"label": "PowerPoint", "progid": "PowerPoint.Application", "exe": "powerpnt.exe",
                   "top": "PPTFrameClass", "pane": "", "thing": "presentation"},
}  # fmt: skip
_BY_EXE = {a["exe"]: key for key, a in APPS.items()}


class Unreachable(LedgerError):
    """The app can't be edited directly right now: not installed, not running, nothing open in
    it, busy in a box or a cell, or it stopped answering. The caller falls back."""


class Unsupported(LedgerError):
    """An edit the direct route has no calls for."""


class Declined(LedgerError):
    """They said no on the card that asked before replacing or removing cells."""


def app_of(exe: str) -> str:
    """'EXCEL.EXE' -> 'excel'; '' for anything that isn't one of the three."""
    return _BY_EXE.get(str(exe or "").rsplit("\\", 1)[-1].lower(), "")


# ------------------------------------------------------------------ the app's own thread
_BUSY = {
    -2146777998,  # 0x800AC472: Excel is in the middle of a cell edit or a box
    -2147418111,  # RPC_E_CALL_REJECTED
    -2147417846,  # RPC_E_SERVERCALL_RETRYLATER
}


def _hresult(exc: BaseException) -> int | None:
    code = getattr(exc, "hresult", None)
    if isinstance(code, int):
        return code
    args = getattr(exc, "args", ())
    return args[0] if args and isinstance(args[0], int) else None


def _is_com(exc: BaseException) -> bool:
    return type(exc).__name__ == "COMError"


def _is_busy(exc: BaseException) -> bool:
    return _is_com(exc) and _hresult(exc) in _BUSY


def _said(exc: BaseException) -> str:
    """The app's own words for why it refused, when it gave any."""
    args = getattr(exc, "args", ())
    details = args[2] if len(args) > 2 else None
    text = details[0] if isinstance(details, tuple) and details and details[0] else ""
    return " ".join(str(text or (args[1] if len(args) > 1 else "") or "").split())[:240]


class _Box:
    def __init__(self) -> None:
        self.done = threading.Event()
        self.result: Any = None
        self.error: BaseException | None = None
        self.left = False  # nobody is waiting any more: don't start it


def _com_init() -> Callable[[], None]:
    import comtypes

    comtypes.CoInitialize()
    return comtypes.CoUninitialize


class ComThread:
    """One thread that owns everything got from the app. :meth:`run` waits for a call up to a
    time limit; once a call has run out of time the thread counts as stuck and every later
    call is refused at once, so nothing is sent to an app that may be sitting in a box."""

    def __init__(self, label: str, init: Callable[[], Callable[[], None]] | None = _com_init) -> None:
        self.label = label
        self.stuck = False
        self._init = init
        self._jobs: queue.SimpleQueue[tuple[Callable[[], Any], _Box] | None] = queue.SimpleQueue()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name=f"pyxi-live-{label.lower()}"
        )
        self._thread.start()

    def _loop(self) -> None:
        undo: Callable[[], None] | None = None
        try:
            undo = self._init() if self._init else None
        except Exception as exc:  # no COM here at all
            log.info("live %s: this thread can't use COM: %s", self.label, exc)
            self._failed = exc
        try:
            while (item := self._jobs.get()) is not None:
                fn, box = item
                if box.left:
                    continue
                try:
                    if getattr(self, "_failed", None) is not None:
                        raise Unreachable(f"{self.label} can't be reached from here.")
                    box.result = fn()
                except BaseException as exc:
                    box.error = exc
                del fn
                box.done.set()
        finally:
            gc.collect()  # what was got from the app is let go on the thread that got it
            if undo:
                try:
                    undo()
                except Exception:
                    pass

    def run(self, fn: Callable[[], Any], timeout: float = TIMEOUT_S) -> Any:
        if self.stuck:
            raise Unreachable(
                f"{self.label} stopped answering (a box is probably open in it), so nothing more was sent."
            )
        box = _Box()
        self._jobs.put((fn, box))
        if not box.done.wait(timeout):
            box.left = True
            self.stuck = True
            raise Unreachable(
                f"{self.label} didn't answer within {timeout:g} seconds: a box is probably open in "
                "it, or a cell is being typed in. Nothing more was sent."
            )
        if box.error is not None:
            raise _plain(box.error, self.label)
        return box.result

    def close(self) -> None:
        self._jobs.put(None)


def _plain(exc: BaseException, label: str) -> BaseException:
    """An error from the app as one Ledger can report."""
    if isinstance(exc, LedgerError):
        return exc
    if _is_busy(exc):
        return Unreachable(f"{label} is busy (a box is open in it, or a cell is being typed in).")
    if _is_com(exc):
        said = _said(exc)
        return LedgerError(f"{label} said no: {said}" if said else f"{label} refused that edit.")
    return exc


# ------------------------------------------------------------------ finding the app
def _missing() -> Any:
    """What an optional argument left out looks like to the app."""
    try:
        from comtypes.automation import VARIANT

        return VARIANT.missing
    except Exception:  # not on Windows: only the pretend app sees it
        return MISSING


MISSING = object()


def _windows_of(class_name: str) -> list[int]:
    """Top-level windows of that class, topmost first."""
    import ctypes

    u = ctypes.windll.user32
    found: list[int] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def visit(hwnd: int, _: Any) -> bool:
        buf = ctypes.create_unicode_buffer(64)
        u.GetClassNameW(ctypes.c_void_p(hwnd), buf, 64)
        if buf.value == class_name and u.IsWindowVisible(ctypes.c_void_p(hwnd)):
            found.append(int(hwnd))
        return True

    u.EnumWindows(visit, 0)
    return found


def _pane(top: int, class_name: str) -> int:
    import ctypes

    u = ctypes.windll.user32
    found: list[int] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def visit(hwnd: int, _: Any) -> bool:
        buf = ctypes.create_unicode_buffer(64)
        u.GetClassNameW(ctypes.c_void_p(hwnd), buf, 64)
        if buf.value == class_name:
            found.append(int(hwnd))
            return False
        return True

    u.EnumChildWindows(ctypes.c_void_p(top), visit, 0)
    return found[0] if found else 0


def _from_window(pane: int) -> Any:
    """The app behind one of its document windows: the exact copy of the app that owns that
    window, which the system's "running app" list can't promise when two copies are open."""
    import ctypes

    from comtypes.automation import IDispatch
    from comtypes.client import dynamic

    ptr = ctypes.POINTER(IDispatch)()
    ctypes.oledll.oleacc.AccessibleObjectFromWindow(
        ctypes.c_void_p(pane),
        ctypes.c_ulong(0xFFFFFFF0),  # OBJID_NATIVEOM
        ctypes.byref(IDispatch._iid_),
        ctypes.byref(ptr),
    )
    return dynamic.Dispatch(ptr).Application


def connect(app: str, hwnd: int | None = None) -> Any:
    """The running Excel, Word or PowerPoint, by the window given (or its topmost window), else
    whichever copy the system lists as running. Runs on the app's thread."""
    info = APPS[app]
    if sys.platform != "win32":
        raise Unreachable(f"{info['label']} can only be edited directly on Windows.")
    if info["pane"]:
        tops = _windows_of(info["top"])
        if hwnd in tops:
            tops = [hwnd, *[t for t in tops if t != hwnd]]
        for top in tops:
            try:
                if pane := _pane(top, info["pane"]):
                    return _from_window(pane)
            except Exception as exc:
                log.debug("live %s: window %s gave nothing: %s", app, top, exc)
    try:
        import comtypes.client

        return comtypes.client.GetActiveObject(info["progid"], dynamic=True)
    except Exception as exc:
        if _is_busy(exc):
            raise
        raise Unreachable(f"{info['label']} isn't open.") from None


# ------------------------------------------------------------------ what every app shares
Confirm = Callable[[str, str], Awaitable[bool]]


def _count(collection: Any) -> int:
    try:
        return int(collection.Count)
    except Exception:
        return 0


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _bgr(color: Any, what: str = "color") -> int:
    """A colour the way Office takes it: one number, blue first."""
    h = hex_color(color)
    if h is None:
        raise LedgerError(f"I can't read the {what} {color!r}: use a name like red or a code like #1A73E8.")
    return int(h[4:6] + h[2:4] + h[0:2], 16)


def _number(value: Any, what: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        raise LedgerError(f"{what} should be a number, not {value!r}.") from None


class Live:
    """A document open in an app. ``load`` finds it and reads its outline; ``apply`` runs one
    edit; ``close`` lets the app go. Nothing here saves."""

    app = ""
    kind = ""
    live = True
    menu = ""

    def __init__(
        self,
        hwnd: int | None = None,
        *,
        confirm: Confirm | None = None,
        connect: Callable[[str, int | None], Any] = connect,
        thread: ComThread | None = None,
        timeout: float = TIMEOUT_S,
    ) -> None:
        self.label = APPS[self.app]["label"]
        self.thing = APPS[self.app]["thing"]
        self.hwnd = hwnd
        self.confirm = confirm
        self._connect = connect
        self.thread = thread or ComThread(self.label)
        self.timeout = timeout
        self.com: Any = None
        self.doc: Any = None
        self.name = ""
        self.sent = False  # an edit's calls have gone to the app
        self.declined = False
        self._outline = ""

    # -- on the app's thread
    def _open(self) -> None:
        if self.com is None:
            self.com = self._wait(lambda: self._connect(self.app, self.hwnd))
        self.doc = self._wait(self._document)
        if self.doc is None:
            raise Unreachable(f"There's no {self.thing} open in {self.label} to edit.")
        self.name = str(self._wait(lambda: self.doc.Name))
        self._outline = self._read()

    def _wait(self, fn: Callable[[], Any], tries: int = 6) -> Any:
        """A call that changes nothing, asked again for a moment while the app says it's busy."""
        for attempt in range(tries):
            try:
                return fn()
            except BaseException as exc:
                if not _is_busy(exc) or attempt == tries - 1 or self.thread.stuck:
                    raise  # (stuck: nobody is waiting for the answer any more)
                time.sleep(0.25)
        return None

    def _document(self) -> Any:
        raise NotImplementedError

    def _read(self) -> str:
        raise NotImplementedError

    def _risk(self, op: dict[str, Any]) -> tuple[int, str]:
        """How many filled cells this edit would replace or remove, and in what words."""
        return 0, ""

    def _do(self, fn: Callable[[dict[str, Any]], str], op: dict[str, Any]) -> str:
        self._ready()
        self.sent = True
        return fn(op)

    def _ready(self) -> None:
        """Still there, and not in the middle of something. Raises before anything is sent."""
        self._wait(lambda: self.doc.Name)

    def _forget(self) -> None:
        self.com = self.doc = None

    # -- from the job
    async def _run(self, fn: Callable[[], Any], timeout: float | None = None) -> Any:
        return await asyncio.to_thread(self.thread.run, fn, timeout or self.timeout)

    async def load(self) -> None:
        await self._run(self._open)

    def describe(self) -> str:
        return self._outline

    @property
    def title(self) -> str:
        return f"{self.name} in {self.label}"

    async def apply(self, op: dict[str, Any]) -> str:
        name = str(op.get("op") or "")
        fn = getattr(self, f"op_{name}", None) if re.fullmatch(r"[a-z_]+", name) else None
        if fn is None:
            raise Unsupported(f"{name or 'That'} isn't an edit Pyxi can make straight in {self.label}.")
        count, what = await self._run(lambda: self._risk(op))
        if count > FEW:
            ok = bool(await self.confirm(*self._question(count, what))) if self.confirm else False
            if not ok:
                self.declined = True
                raise Declined(
                    f"Left as it was: that would {what} {_plural(count, 'filled cell')} in {self.name}, "
                    "and you didn't say yes."
                )
        return await self._run(lambda: self._do(fn, op))

    def _question(self, count: int, what: str) -> tuple[str, str]:
        return (
            f"{what.capitalize()} {_plural(count, 'filled cell')} in {self.name}?",
            f"This changes the {self.thing} open in {self.label} now. Ctrl+Z in {self.label} usually "
            "can't bring it back after Pyxi changes it. Nothing is saved unless you save it.",
        )

    async def close(self) -> None:
        try:
            if not self.thread.stuck:
                await self._run(self._forget, 2.0)
        except Exception:
            pass
        self.thread.close()


# ------------------------------------------------------------------ Excel
_ALIGN = {"left": -4131, "center": -4108, "centre": -4108, "right": -4152}
_VALIGN = {"top": -4160, "middle": -4108, "center": -4108, "bottom": -4107}
_CHARTS = {"bar": 51, "column": 51, "horizontal bar": 57, "line": 4, "area": 1, "pie": 5}
_AGG = {"sum": -4157, "count": -4112, "average": -4106}
_TOTALS = ("SUM", "AVERAGE", "COUNT", "MAX", "MIN")
_COLS = re.compile(r"(?i)^\s*([a-z]{1,3})(?:\s*:\s*([a-z]{1,3}))?\s*$")
# A formula that reaches outside the workbook into a program: a DDE link (=cmd|'/c ..'!A1) or
# one of the functions that call into a DLL or a macro.
_RUNS_SOMETHING = re.compile(r"(?is)^\s*[=+\-@].*(\|.*!|\b(CALL|REGISTER(\.ID)?|EXEC|RTD|DDE\w*)\s*\()")
_ROWS = re.compile(r"^\s*(\d{1,7})(?:\s*:\s*(\d{1,7}))?\s*$")


def _table(raw: Any, rows: int, cols: int) -> list[list[Any]]:
    """What a range hands back (one value for one cell, rows of values for more) as rows."""
    if not isinstance(raw, tuple | list):
        return [[raw]]
    out = [list(r) if isinstance(r, tuple | list) else [r] for r in raw]
    return out if out else [[None] * cols for _ in range(rows)]


def _tidy(v: Any) -> Any:
    """3.0 as 3, a date as 2026-10-03: a cell the way the outline quotes it."""
    if isinstance(v, bool):
        return v
    if isinstance(v, float) and v.is_integer() and abs(v) < 1e15:
        return int(v)
    if isinstance(v, datetime):
        return v.date().isoformat() if v.time() == datetime.min.time() else v.isoformat(" ", "minutes")
    if isinstance(v, date):
        return v.isoformat()
    if v is not None and not isinstance(v, int | float | str):
        try:
            return _tidy(float(v))  # money comes back as a Decimal
        except (TypeError, ValueError):
            return str(v)
    return v


def _typed_value(typed: Any) -> Any:
    """What a cell holds, from what's typed in it alone: None for a formula (its result is
    not known this way)."""
    if typed in (None, ""):
        return None
    text = str(typed)
    if text.startswith("="):
        return None
    try:
        return float(text)
    except ValueError:
        return text


def _is_number(v: Any) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool)


def _is_total_label(v: Any) -> bool:
    return isinstance(v, str) and bool(re.match(r"\s*(grand\s+)?totals?\b", v, re.I))


def _shown(x: float) -> str:
    x = round(float(x), 2)
    return f"{int(x):,}" if x.is_integer() else f"{x:,}"


class Grid:
    """One sheet's cells, read in two calls: what's typed in each (a formula as its text) and
    what each shows."""

    def __init__(self, ws: Any) -> None:
        used = ws.UsedRange
        self.r0, self.c0 = int(used.Row), int(used.Column)
        rows, cols = int(used.Rows.Count), int(used.Columns.Count)
        if rows * cols > _MAX_CELLS:
            raise LedgerError(
                f"{ws.Name} is too big to edit this way ({rows:,} rows by {cols:,} columns)."
            )
        self.typed = _table(used.Formula, rows, cols)
        try:
            self.shown = _table(used.Value(), rows, cols)
        except Exception:
            # A cell showing an error (#DIV/0!, #N/A) can't be carried across with the rest.
            # What's typed is still known: numbers as numbers, and a formula as a formula
            # whose result isn't.
            self.shown = [[_typed_value(v) for v in row] for row in self.typed]
        self.last = 0  # the last row with anything in it
        self.width = 0  # the last column with anything in it
        for i, row in enumerate(self.typed):
            for j, v in enumerate(row):
                if v not in (None, ""):
                    self.last = max(self.last, self.r0 + i)
                    self.width = max(self.width, self.c0 + j)

    def _at(self, table: list[list[Any]], r: int, c: int) -> Any:
        i, j = r - self.r0, c - self.c0
        if 0 <= i < len(table) and 0 <= j < len(table[i]):
            return table[i][j]
        return None

    def formula(self, r: int, c: int) -> str:
        v = self._at(self.typed, r, c)
        return "" if v is None else str(v)

    def value(self, r: int, c: int) -> Any:
        v = self._at(self.shown, r, c)
        return None if v == "" else _tidy(v)

    def filled(self, r: int, c: int) -> bool:
        return self.formula(r, c) != ""

    def header(self) -> int:
        for r in range(1, min(self.last, 20) + 1):
            if sum(1 for c in range(1, self.width + 1) if self.filled(r, c)) >= 2:
                return r
        return 1

    def block(self, head: int) -> tuple[int, int]:
        """The table the header row starts: its first and last column. A pivot table or a
        second table beside the data, past an empty column, is not part of it."""
        first = next((c for c in range(1, self.width + 1) if self.filled(head, c)), 1)
        last = first
        while last < self.width and self.filled(head, last + 1):
            last += 1
        return first, last

    def bottom(self, first: int, last: int) -> int:
        """The last row with anything in it in those columns."""
        for r in range(self.last, 0, -1):
            if any(self.filled(r, c) for c in range(first, last + 1)):
                return r
        return 0

    def column(self, ref: Any, head: int) -> int | None:
        """A column from its header ('Amount'), its letter ('C') or its number."""
        if ref is None or ref == "":
            return None
        if isinstance(ref, int) and not isinstance(ref, bool):
            return ref
        s = str(ref).strip()
        for c in range(1, self.width + 1):
            v = self.value(head, c)
            if v is not None and str(v).strip().lower() == s.lower():
                return c
        if re.fullmatch(r"[A-Za-z]{1,3}", s) and col_number(s) <= max(self.width, 1):
            return col_number(s)
        if s.isdigit() and 1 <= int(s) <= max(self.width, 1):
            return int(s)
        raise LedgerError(f"There's no column called {s!r}.")

    def count_filled(self, c1: int | None, r1: int | None, c2: int | None, r2: int | None) -> int:
        rows = range(max(r1 or 1, 1), min(r2 or self.last, self.last) + 1)
        cols = range(max(c1 or 1, 1), min(c2 or self.width, self.width) + 1)
        return sum(1 for r in rows for c in cols if self.filled(r, c))


EXCEL_MENU = """The workbook open in Excel on their PC. You change it live, in the app: nothing is saved, and Excel usually can't undo these changes, so make exactly what was asked. "sheet" names a tab and can be left out for the one they're looking at; a "range" can name its tab too ('Summary'!A1:B4). Columns are a header name or a letter. Prefer the high-level edits (totals_row, pivot) over hand-written cells, and write formulas, not hard-coded results:
- {"op": "set_cells", "sheet"?: name, "cells": {"B5": 120, "C5": "=B5*1.13"}}  (new or sample data for an empty sheet goes here, from A1)
- {"op": "append_row", "sheet"?: name, "values": [..]}
- {"op": "totals_row", "sheet"?: name, "columns"?: ["B", "Amount"], "function"?: "SUM"|"AVERAGE"|"COUNT"|"MAX"|"MIN", "label"?: "Total"}  (finds the number columns and the last row itself)
- {"op": "add_column", "sheet"?: name, "header": "Total", "formula": "=B{r}*C{r}"}  ({r} is each data row)
- {"op": "pivot", "sheet"?: name, "group_by": column, "value"?: column or [columns], "agg": "sum"|"count"|"average", "target"?: "Pivot"}  (a real PivotTable beside the data, where they can see it; give "target" only when they asked for a new tab)
- {"op": "chart", "sheet"?: name, "labels": column, "values": column, "type": "bar"|"horizontal bar"|"line"|"area"|"pie", "title"?: "..", "at"?: "H2"}  (beside the data unless "at" says where)
- {"op": "format", "range": "A1:D1", "bold"?: true, "italic"?: true, "underline"?: true, "strikethrough"?: true, "size"?: 12, "font"?: "Arial", "color"?: "#1A73E8"|"red", "fill"?: "#FFF2CC"|"yellow", "number_format"?: "$#,##0.00", "align"?: "left"|"center"|"right", "valign"?: "top"|"middle"|"bottom", "wrap"?: true, "border"?: "all"|"outer"}
- {"op": "sort", "by": column, "descending"?: true, "then_by"?: column, "range"?: "A2:D20"}  (the rows under the header unless a range is given)
- {"op": "filter", "range"?: "A1:D20", "off"?: true}  (filter buttons on the header row)
- {"op": "freeze", "rows"?: 1, "columns"?: 0}
- {"op": "insert_rows", "at": 5, "count"?: 1}  and  {"op": "insert_columns", "at": "C", "count"?: 1}
- {"op": "delete_rows", "from": 5, "to"?: 7}  and  {"op": "delete_columns", "from": "C", "to"?: "D"}  (only when they asked for rows or columns to go)
- {"op": "resize", "columns"?: "A:D", "width"?: pixels, "rows"?: "1:5", "height"?: pixels}  (no width or height: fit what's in them)
- {"op": "clear", "range": "A2:D9", "what"?: "values"|"formats"|"all"}
- {"op": "find_replace", "find": "..", "replace": "..", "sheet"?: name, "match_case"?: true, "whole_cell"?: true}
- {"op": "add_sheet", "name": "Summary", "rows"?: [[..], [..]]}  (only when they ask for a new tab)
- {"op": "rename_sheet", "sheet"?: name, "name": ".."}
Anything else (saving, closing, deleting a sheet, merging cells, conditional colours, dropdowns) is not yours here: return no edits and say so, and it is done another way."""


class ExcelLive(Live):
    app = "excel"
    kind = "xlsx_live"
    menu = EXCEL_MENU

    def _ready(self) -> None:
        super()._ready()
        self._idle()

    def _idle(self) -> None:
        # Excel says it isn't ready while a cell is being typed in or a box is open. An edit
        # sent then is refused half-way through, so none is sent.
        if getattr(self.com, "Ready", True) is False:
            raise Unreachable("Excel is busy (a box is open in it, or a cell is being typed in).")

    def _document(self) -> Any:
        self._idle()
        book = self.com.ActiveWorkbook
        if book is None:
            return None
        self.default = ""
        names = [str(ws.Name) for ws in book.Worksheets]
        if not names:
            return None
        try:
            active = str(book.ActiveSheet.Name)
        except Exception:
            active = ""
        self.default = active if active in names else names[0]
        return book

    def _sheets(self) -> list[str]:
        return [str(ws.Name) for ws in self.doc.Worksheets]

    def sheet(self, name: Any) -> Any:
        want = str(name or self.default)
        for ws in self.doc.Worksheets:
            if str(ws.Name).lower() == want.lower():
                return ws
        raise LedgerError(f"There's no sheet called {want!r}; it has {', '.join(self._sheets())}.")

    def _where(self, op: dict[str, Any], key: str = "range") -> tuple[Any, str]:
        """The sheet and the plain A1 range an edit names ('Summary'!A1:B4 picks its own tab)."""
        tab, a1 = split_tab(op.get(key))
        if not a1:
            raise LedgerError(f"{op.get('op')} needs {key}, like A1:D5.")
        a1_bounds(a1)  # a range, and nothing else, reaches Excel
        return self.sheet(tab or op.get("sheet")), a1.replace("$", "").upper()

    def _read(self, max_rows: int = 40) -> str:
        names = self._sheets()
        saved = "" if str(getattr(self.doc, "Path", "") or "") else " It has never been saved."
        out = [
            f"Excel workbook {self.name}, open in Excel now.{saved} Sheets: {', '.join(names)} "
            f"(the one in front: {self.default})."
        ]
        for ws in self.doc.Worksheets:
            grid = Grid(ws)
            out.append(
                f"\nSheet {str(ws.Name)!r}: {grid.last} rows x {grid.width} columns; header row {grid.header()}."
            )
            rows = list(range(1, grid.last + 1))
            if len(rows) > max_rows:
                rows = [*rows[: max_rows - 6], -1, *rows[-5:]]
            for r in rows:
                if r == -1:
                    out.append("  ...")
                    continue
                cells = []
                for c in range(1, grid.width + 1):
                    typed = grid.formula(r, c)
                    if typed == "":
                        continue
                    shown = typed if typed.startswith("=") else repr(grid.value(r, c))
                    cells.append(f"{col_letter(c)}{r}={shown}"[:80])
                if cells:
                    out.append("  " + " | ".join(cells))
            charts = _count(ws.ChartObjects())
            pivots = _count(ws.PivotTables())
            if charts or pivots:
                out.append(f"  ({_plural(charts, 'chart')} and {_plural(pivots, 'pivot table')} on it)")
        return "\n".join(out)

    def _data(self, grid: Grid, head: int) -> tuple[int, int, int]:
        """The table under the header row: its first column, its last column, and its last
        row of data (the last filled row that isn't a total)."""
        first, right = grid.block(head)
        last = grid.bottom(first, right)
        while last > head and _is_total_label(grid.value(last, first)):
            last -= 1
        return first, right, last

    # -- what an edit would wipe
    def _risk(self, op: dict[str, Any]) -> tuple[int, str]:
        name = op.get("op")
        if name == "set_cells":
            cells = op.get("cells")
            if not isinstance(cells, dict):
                return 0, ""
            grid = Grid(self.sheet(op.get("sheet")))
            n = 0
            for ref, v in cells.items():
                r, c = cell_rc(ref)
                was = grid.formula(r, c)
                if was != "" and was != ("" if v is None else str(v)) and str(grid.value(r, c)) != str(v):
                    n += 1
            return n, "replace"
        if name == "clear" and str(op.get("what") or "values").lower() != "formats":
            ws, a1 = self._where(op)
            return Grid(ws).count_filled(*a1_bounds(a1)), "clear"
        if name in ("delete_rows", "delete_columns"):
            grid = Grid(self.sheet(op.get("sheet")))
            first, last = self._span(op, rows=name == "delete_rows")
            box = (None, first, None, last) if name == "delete_rows" else (first, None, last, None)
            return grid.count_filled(*box), "delete"
        return 0, ""

    def _span(self, op: dict[str, Any], *, rows: bool) -> tuple[int, int]:
        def one(v: Any) -> int:
            s = str(v).strip()
            if rows or s.isdigit():
                n = int(_number(v, "A row" if rows else "A column"))
            elif re.fullmatch(r"[A-Za-z]{1,3}", s):
                n = col_number(s)
            else:
                raise LedgerError(f"{v!r} isn't a column letter.")
            if n < 1:
                raise LedgerError(f"There's no {'row' if rows else 'column'} {v}.")
            return n

        if op.get("from") in (None, ""):
            raise LedgerError(f"{op.get('op')} needs from: the first {'row' if rows else 'column'} to delete.")
        first = one(op["from"])
        last = one(op["to"]) if op.get("to") not in (None, "") else first
        return min(first, last), max(first, last)

    # -- edits
    def op_set_cells(self, op: dict[str, Any]) -> str:
        ws = self.sheet(op.get("sheet"))
        cells = op.get("cells") or {}
        if not isinstance(cells, dict) or not cells:
            raise LedgerError("set_cells needs cells like {'B5': 120, 'C5': '=B5*2'}.")
        # every address and every value checked before the first cell is typed
        todo = [(cell_rc(ref), self._cell(v)) for ref, v in cells.items()]
        for (r, c), v in todo:
            ws.Cells(r, c).Formula = v
        return f"Set {_plural(len(todo), 'cell')} on {ws.Name}"

    @staticmethod
    def _cell(v: Any) -> Any:
        """What goes into a cell. A formula is typed as a formula, as it is in a file, except
        one that would have Excel start or call into another program: in a file that waits
        for someone to open it, and here Excel is open and would act on it at once."""
        if v is None:
            return ""
        if isinstance(v, bool | int | float):
            return v
        text = str(v)
        if _RUNS_SOMETHING.search(text):
            raise LedgerError("That formula would have Excel run another program, so I left it out.")
        return text

    def op_append_row(self, op: dict[str, Any]) -> str:
        ws = self.sheet(op.get("sheet"))
        values = op.get("values")
        if not isinstance(values, list) or not values:
            raise LedgerError("append_row needs values: the cells of the new row.")
        values = [self._cell(v) for v in values]
        grid = Grid(ws)
        first, right = grid.block(grid.header())
        r = grid.bottom(first, right) + 1
        for c, v in enumerate(values, first):
            ws.Cells(r, c).Formula = v
        return f"Added row {r} on {ws.Name}"

    def op_totals_row(self, op: dict[str, Any]) -> str:
        ws = self.sheet(op.get("sheet"))
        grid = Grid(ws)
        head = int(op.get("header_row") or grid.header())
        label_col, right = grid.block(head)
        last = grid.bottom(label_col, right)
        if last <= head:
            raise LedgerError(f"{ws.Name} has no rows under its header to total.")
        fn = str(op.get("function") or "SUM").upper()
        if fn not in _TOTALS:
            raise LedgerError("totals_row function should be SUM, AVERAGE, COUNT, MAX or MIN.")
        if _is_total_label(grid.value(last, label_col)):
            return f"{ws.Name} already ends with a total row (row {last})"
        if op.get("columns"):
            cols = [c for c in (grid.column(x, head) for x in op["columns"]) if c]
        else:
            cols = []
            for c in range(label_col, right + 1):
                data = [(grid.formula(r, c), grid.value(r, c)) for r in range(head + 1, last + 1)]
                filled = [(f, v) for f, v in data if f != ""]
                numbers = sum(1 for f, v in filled if _is_number(v) or f.startswith("="))
                if c != label_col and filled and numbers >= 0.8 * len(filled):
                    cols.append(c)
        if not cols:
            raise LedgerError(f"I couldn't find number columns to total on {ws.Name}.")
        r = last + 1
        label = str(op.get("label") or ("Total" if fn == "SUM" else fn.title()))
        ws.Cells(r, label_col).Formula = label
        ws.Cells(r, label_col).Font.Bold = True
        shown = []
        for c in cols:
            letter = col_letter(c)
            cell = ws.Cells(r, c)
            cell.Formula = f"={fn}({letter}{head + 1}:{letter}{last})"
            cell.Font.Bold = True
            try:
                cell.NumberFormat = ws.Cells(last, c).NumberFormat
            except Exception:  # a mix of formats in that cell: the total keeps its own
                pass
            try:
                result = _tidy(cell.Value2)
            except Exception:  # it came to an error (#DIV/0!): the row is there, with no figure
                result = None
            if _is_number(result):
                shown.append(f"{grid.value(head, c) or letter} {_shown(result)}")
        tail = f": {', '.join(shown)}" if shown else ""
        return f"Added a {label} row (row {r}) on {ws.Name}{tail}"

    def op_add_column(self, op: dict[str, Any]) -> str:
        ws = self.sheet(op.get("sheet"))
        grid = Grid(ws)
        head = grid.header()
        formula = str(op.get("formula") or "")
        self._cell(formula)
        if "{r}" not in formula or not formula.startswith("="):
            raise LedgerError("add_column needs a formula with {r} for the row, like '=B{r}*C{r}'.")
        first, right, last = self._data(grid, head)
        c = right + 1
        if any(grid.filled(r, c + 1) for r in range(head, last + 1)):
            raise LedgerError(
                f"There's no room for a new column on {ws.Name}: something sits right beside the table."
            )
        header = str(op.get("header") or "New")
        ws.Cells(head, c).Formula = header
        ws.Cells(head, c).Font.Bold = True
        n = 0
        for r in range(head + 1, last + 1):
            if _is_total_label(grid.value(r, first)):
                continue
            ws.Cells(r, c).Formula = formula.replace("{r}", str(r))
            n += 1
        return f"Added column {header} with a formula on {_plural(n, 'row')}"

    def op_pivot(self, op: dict[str, Any]) -> str:
        src = self.sheet(op.get("sheet"))
        grid = Grid(src)
        head = grid.header()
        first_col, width, last = self._data(grid, head)
        g = grid.column(op.get("group_by") or op.get("rows"), head)
        if g is None:
            raise LedgerError("pivot needs group_by: the column to group by.")
        wanted = op.get("values") if isinstance(op.get("values"), list) else op.get("value")
        wanted = wanted if isinstance(wanted, list) else [wanted]
        vs = [c for c in (grid.column(w, head) for w in wanted if w not in (None, "")) if c]
        agg = str(op.get("agg") or ("sum" if vs else "count")).lower()
        if agg not in _AGG:
            raise LedgerError("pivot agg should be sum, count or average.")
        if agg != "count" and not vs:
            raise LedgerError(f"pivot with {agg} needs value: the column to {agg}.")
        if last <= head:
            raise LedgerError(f"{src.Name} has no rows under its header to group.")
        heads ={c: str(grid.value(head, c)) for c in range(first_col, width + 1)}
        for c in (g, *vs):
            if c not in heads:
                raise LedgerError(f"Column {col_letter(c)} has no heading on {src.Name}, and a pivot needs one.")
        groups = {
            str(grid.value(r, g)).casefold()
            for r in range(head + 1, last + 1)
            if grid.value(r, g) not in (None, "")
        }
        if not groups:
            raise LedgerError(f"There's nothing to group in that column on {src.Name}.")
        source = src.Range(f"{col_letter(first_col)}{head}:{col_letter(width)}{last}")
        if op.get("target"):
            title = str(op["target"])[:31]
            name, n = title, 2
            while name.lower() in {s.lower() for s in self._sheets()}:
                name, n = f"{title} {n}"[:31], n + 1
            sheets = self.doc.Worksheets
            dest_ws = sheets.Add(_missing(), sheets(_count(sheets)))
            dest_ws.Name = name
            at, where = "A1", f"on a new sheet, {name}"
        else:
            dest_ws = src
            at = f"{col_letter(grid.width + 2)}{head}"
            where = f"beside the data on {src.Name}, at {at}"
        taken = {str(p.Name).lower() for p in dest_ws.PivotTables()}
        n = 1
        while f"pyxipivot{n}" in taken:
            n += 1
        cache = self.doc.PivotCaches().Create(1, source)  # 1: from a range on a sheet
        table = cache.CreatePivotTable(dest_ws.Range(at), f"PyxiPivot{n}")
        table.PivotFields(heads[g]).Orientation = 1  # a row for each group
        names = [heads[c] for c in vs] or [heads[g]]
        for v in names:
            table.AddDataField(table.PivotFields(v), f"{agg.title()} of {v}", _AGG[agg])
        return (
            f"Made a pivot table {where}: {agg} of {' and '.join(names) if vs else 'rows'} by "
            f"{heads[g]}, {_plural(len(groups), 'group')}"
        )

    def op_chart(self, op: dict[str, Any]) -> str:
        ws = self.sheet(op.get("sheet"))
        grid = Grid(ws)
        head = grid.header()
        first, _, last = self._data(grid, head)
        lc = grid.column(op.get("labels") or col_letter(first), head) or first
        vc = grid.column(op.get("values"), head)
        if vc is None:
            raise LedgerError("chart needs values: the column to plot.")
        kind = str(op.get("type") or "bar").lower()
        if kind not in _CHARTS:
            raise LedgerError("chart type should be bar, horizontal bar, line, area or pie.")
        if last <= head:
            raise LedgerError(f"{ws.Name} has no rows under its header to chart.")
        at = str(op.get("at") or f"{col_letter(grid.width + 2)}{head}").replace("$", "").upper()
        cell_rc(at)
        corner = ws.Range(at)
        shape = ws.Shapes.AddChart2(-1, _CHARTS[kind], corner.Left, corner.Top, 420, 260)
        chart = shape.Chart
        L, V = col_letter(lc), col_letter(vc)
        chart.SetSourceData(
            self.com.Union(ws.Range(f"{L}{head}:{L}{last}"), ws.Range(f"{V}{head}:{V}{last}"))
        )
        what = grid.value(head, vc) or "the values"
        chart.HasTitle = True
        chart.ChartTitle.Text = str(op.get("title") or what)
        return f"Added a {kind} chart of {what} on {ws.Name}"

    def op_format(self, op: dict[str, Any]) -> str:
        ws, a1 = self._where(op)
        todo: list[Callable[[Any], None]] = []
        did: list[str] = []

        def font(attr: str, value: Any, word: str) -> None:
            todo.append(lambda rng: setattr(rng.Font, attr, value))
            did.append(word)

        for key in ("bold", "italic", "strikethrough"):
            if key in op:
                on = bool(op[key])
                font(key.title(), on, key if on else f"not {key}")
        if "underline" in op:
            font("Underline", 2 if op["underline"] else -4142, "underlined" if op["underline"] else "not underlined")
        if op.get("size") is not None:
            size = _number(op["size"], "size")
            if not 1 <= size <= 409:
                raise LedgerError("A font size is between 1 and 409.")
            font("Size", size, f"size {size:g}")
        if op.get("font"):
            font("Name", str(op["font"])[:60], str(op["font"])[:60])
        if op.get("color"):
            font("Color", _bgr(op["color"]), f"{op['color']} text")
        if op.get("fill"):
            fill = _bgr(op["fill"], "fill")
            todo.append(lambda rng: setattr(rng.Interior, "Color", fill))
            did.append(f"{op['fill']} fill")
        if op.get("number_format"):
            fmt = str(op["number_format"])[:120]
            todo.append(lambda rng: setattr(rng, "NumberFormat", fmt))
            did.append(f"shown as {fmt}")
        for key, table, attr in (("align", _ALIGN, "HorizontalAlignment"), ("valign", _VALIGN, "VerticalAlignment")):
            if op.get(key):
                code = table.get(str(op[key]).lower())
                if code is None:
                    raise LedgerError(f"{key} should be one of {', '.join(sorted(set(table)))}.")
                todo.append(lambda rng, attr=attr, code=code: setattr(rng, attr, code))
                did.append(f"{op[key]} aligned")
        if "wrap" in op:
            wrap = bool(op["wrap"])
            todo.append(lambda rng: setattr(rng, "WrapText", wrap))
            did.append("wrapped" if wrap else "not wrapped")
        if op.get("border"):
            border = str(op["border"]).lower()
            if border not in ("all", "outer"):
                raise LedgerError("border should be all or outer.")
            if border == "all":
                todo.append(lambda rng: setattr(rng.Borders, "LineStyle", 1))
            else:
                todo.append(lambda rng: rng.BorderAround(1))
            did.append(f"{border} borders")
        if not todo:
            raise LedgerError("format needs something to change: bold, color, fill, number_format...")
        rng = ws.Range(a1)
        for step in todo:
            step(rng)
        return f"Made {a1} on {ws.Name} {', '.join(did)}"

    def op_sort(self, op: dict[str, Any]) -> str:
        ws = self.sheet(op.get("sheet"))
        grid = Grid(ws)
        head = grid.header()
        by = grid.column(op.get("by"), head)
        if by is None:
            raise LedgerError("sort needs by: the column to sort by.")
        then = grid.column(op.get("then_by"), head)
        if op.get("range"):
            ws, a1 = self._where(op)
            c1, r1, c2, r2 = a1_bounds(a1)
            first, last = r1 or head + 1, r2 or grid.last
            left, right = c1 or 1, c2 or grid.width
        else:
            left, right, last = self._data(grid, head)
            first = head + 1
        if last <= first or not left <= by <= right:
            raise LedgerError(f"There's nothing to sort there on {ws.Name}.")
        rng = ws.Range(f"{col_letter(left)}{first}:{col_letter(right)}{last}")
        order = 2 if op.get("descending") else 1
        if then is not None:
            rng.Sort(ws.Cells(first, by), order, ws.Cells(first, then), _missing(), 1)
        else:
            rng.Sort(ws.Cells(first, by), order)
        name = grid.value(head, by) or col_letter(by)
        return f"Sorted rows {first} to {last} on {ws.Name} by {name}{', largest first' if order == 2 else ''}"

    def op_filter(self, op: dict[str, Any]) -> str:
        ws = self.sheet(op.get("sheet"))
        on = bool(ws.AutoFilterMode)
        if op.get("off"):
            if on:
                ws.AutoFilterMode = False
            return f"Took the filter off {ws.Name}"
        if on:
            return f"{ws.Name} already has filter buttons"
        if op.get("range"):
            ws, a1 = self._where(op)
        else:
            grid = Grid(ws)
            head = grid.header()
            left, right, last = self._data(grid, head)
            if last <= head:
                raise LedgerError(f"{ws.Name} has nothing to filter.")
            a1 = f"{col_letter(left)}{head}:{col_letter(right)}{last}"
        ws.Range(a1).AutoFilter()
        return f"Put filter buttons on {a1} on {ws.Name}"

    def op_freeze(self, op: dict[str, Any]) -> str:
        ws = self.sheet(op.get("sheet"))
        rows = int(_number(op.get("rows") if op.get("rows") is not None else 1, "rows"))
        cols = int(_number(op.get("columns") or 0, "columns"))
        if not (0 <= rows <= 1000 and 0 <= cols <= 100):
            raise LedgerError("freeze takes a small number of rows and columns.")
        ws.Activate()  # panes belong to the window, so the sheet has to be the one showing
        window = self.com.ActiveWindow
        window.FreezePanes = False
        window.SplitColumn = cols
        window.SplitRow = rows
        if rows or cols:
            window.FreezePanes = True
            return f"Froze {_plural(rows, 'row')} and {_plural(cols, 'column')} on {ws.Name}"
        return f"Unfroze {ws.Name}"

    def op_insert_rows(self, op: dict[str, Any]) -> str:
        ws = self.sheet(op.get("sheet"))
        at = int(_number(op.get("at"), "at"))
        n = int(_number(op.get("count") or 1, "count"))
        if at < 1 or not 1 <= n <= 1000:
            raise LedgerError("insert_rows needs at (a row number) and a count up to 1000.")
        ws.Range(f"{at}:{at + n - 1}").EntireRow.Insert()
        return f"Inserted {_plural(n, 'row')} at row {at} on {ws.Name}"

    def op_insert_columns(self, op: dict[str, Any]) -> str:
        ws = self.sheet(op.get("sheet"))
        at = str(op.get("at") or "").strip()
        n = int(_number(op.get("count") or 1, "count"))
        if not re.fullmatch(r"[A-Za-z]{1,3}", at) or not 1 <= n <= 200:
            raise LedgerError("insert_columns needs at (a column letter) and a count up to 200.")
        first = col_number(at)
        ws.Range(f"{col_letter(first)}:{col_letter(first + n - 1)}").EntireColumn.Insert()
        return f"Inserted {_plural(n, 'column')} at {at.upper()} on {ws.Name}"

    def op_delete_rows(self, op: dict[str, Any]) -> str:
        ws = self.sheet(op.get("sheet"))
        first, last = self._span(op, rows=True)
        ws.Range(f"{first}:{last}").EntireRow.Delete()
        return f"Deleted rows {first} to {last} on {ws.Name}" if last > first else f"Deleted row {first} on {ws.Name}"

    def op_delete_columns(self, op: dict[str, Any]) -> str:
        ws = self.sheet(op.get("sheet"))
        first, last = self._span(op, rows=False)
        a, b = col_letter(first), col_letter(last)
        ws.Range(f"{a}:{b}").EntireColumn.Delete()
        return f"Deleted columns {a} to {b} on {ws.Name}" if last > first else f"Deleted column {a} on {ws.Name}"

    def op_resize(self, op: dict[str, Any]) -> str:
        ws = self.sheet(op.get("sheet"))
        did = []
        if op.get("columns"):
            m = _COLS.match(str(op["columns"]))
            if not m:
                raise LedgerError("resize columns should look like A:D.")
            a1 = f"{m.group(1)}:{m.group(2) or m.group(1)}".upper()
            cols = ws.Range(a1).EntireColumn
            if op.get("width") is not None:
                px = _number(op["width"], "width")
                if not 5 <= px <= 1800:
                    raise LedgerError("A column is between 5 and 1800 pixels wide.")
                cols.ColumnWidth = round(max(px - 5, 0) / 7, 2)  # Excel counts in characters
                did.append(f"columns {a1} {px:g} pixels wide")
            else:
                cols.AutoFit()
                did.append(f"columns {a1} as wide as what's in them")
        if op.get("rows"):
            m = _ROWS.match(str(op["rows"]))
            if not m:
                raise LedgerError("resize rows should look like 1:5.")
            a1 = f"{m.group(1)}:{m.group(2) or m.group(1)}"
            rows = ws.Range(a1).EntireRow
            if op.get("height") is not None:
                px = _number(op["height"], "height")
                if not 1 <= px <= 540:
                    raise LedgerError("A row is between 1 and 540 pixels tall.")
                rows.RowHeight = round(px * 0.75, 2)  # Excel counts in points
                did.append(f"rows {a1} {px:g} pixels tall")
            else:
                rows.AutoFit()
                did.append(f"rows {a1} as tall as what's in them")
        if not did:
            raise LedgerError("resize needs columns (A:D) or rows (1:5).")
        return f"Made {' and '.join(did)} on {ws.Name}"

    def op_clear(self, op: dict[str, Any]) -> str:
        ws, a1 = self._where(op)
        what = str(op.get("what") or "values").lower()
        rng = ws.Range(a1)
        if what == "values":
            rng.ClearContents()
        elif what == "formats":
            rng.ClearFormats()
        elif what == "all":
            rng.Clear()
        else:
            raise LedgerError("clear what should be values, formats or all.")
        return f"Cleared the {'cells' if what == 'all' else what} in {a1} on {ws.Name}"

    def op_find_replace(self, op: dict[str, Any]) -> str:
        ws = self.sheet(op.get("sheet"))
        find, new = str(op.get("find") or ""), str(self._cell(op.get("replace") or ""))
        if not find:
            raise LedgerError("find_replace needs find: the words to look for.")
        grid = Grid(ws)
        case, whole = bool(op.get("match_case")), bool(op.get("whole_cell"))

        def hit(text: str) -> bool:
            a, b = (text, find) if case else (text.lower(), find.lower())
            return a == b if whole else b in a

        n = sum(
            1
            for r in range(1, grid.last + 1)
            for c in range(1, grid.width + 1)
            if grid.filled(r, c) and hit(grid.formula(r, c))
        )
        if not n:
            return f"{find!r} isn't anywhere on {ws.Name}"
        ws.UsedRange.Replace(find, new, 1 if whole else 2, _missing(), case)
        return f"Replaced {find!r} with {new!r} in {_plural(n, 'cell')} on {ws.Name}"

    def op_add_sheet(self, op: dict[str, Any]) -> str:
        name = str(op.get("name") or "Sheet")[:31]
        if re.search(r"[\\/?*\[\]:]", name):
            raise LedgerError("A sheet's name can't have \\ / ? * [ ] or : in it.")
        if name.lower() in {s.lower() for s in self._sheets()}:
            raise LedgerError(f"There's already a sheet called {name!r}.")
        rows = op.get("rows") or []
        if not isinstance(rows, list):
            raise LedgerError("add_sheet rows should be a list of rows.")
        rows = [[self._cell(v) for v in (row if isinstance(row, list) else [row])] for row in rows]
        sheets = self.doc.Worksheets
        ws = sheets.Add(_missing(), sheets(_count(sheets)))
        ws.Name = name
        for r, row in enumerate(rows, 1):
            for c, v in enumerate(row, 1):
                ws.Cells(r, c).Formula = v
        return f"Added sheet {name}"

    def op_rename_sheet(self, op: dict[str, Any]) -> str:
        ws = self.sheet(op.get("sheet"))
        name = str(op.get("name") or "").strip()[:31]
        if not name or re.search(r"[\\/?*\[\]:]", name):
            raise LedgerError("rename_sheet needs name, with no \\ / ? * [ ] or : in it.")
        if name.lower() in {s.lower() for s in self._sheets()} and name.lower() != str(ws.Name).lower():
            raise LedgerError(f"There's already a sheet called {name!r}.")
        old = str(ws.Name)
        ws.Name = name
        if self.default.lower() == old.lower():
            self.default = name
        return f"Renamed sheet {old} to {name}"


# ------------------------------------------------------------------ for the job and the brain
def _classes() -> dict[str, type[Live]]:
    from pyxi.agents.ledger_live_docs import DeckLive, WordLive

    return {"excel": ExcelLive, "word": WordLive, "powerpoint": DeckLive}


def target(app: str, hwnd: int | None = None, **kw: Any) -> Live:
    """The open document of that app, not yet looked at (``load`` does that)."""
    cls = _classes().get(app)
    if cls is None:
        raise LedgerError(f"Pyxi can't edit {app!r} directly.")
    return cls(hwnd, **kw)


def menus() -> list[tuple[set[str], str]]:
    return [({cls.kind}, cls.menu) for cls in _classes().values()]


def probe(app: str, hwnd: int | None = None, timeout: float = PROBE_S, **kw: Any) -> tuple[str, str]:
    """Can that app's open document be edited directly right now? ("", its name) when it can,
    (why not, "") when it can't. Changes nothing; never raises."""
    if app not in APPS:
        return f"Pyxi can't edit {app!r} directly.", ""
    live: Live | None = None
    try:
        live = target(app, hwnd, timeout=timeout, **kw)
        live.thread.run(lambda: live._open(), timeout)
        return "", live.name
    except LedgerError as exc:
        return str(exc), ""
    except Exception as exc:
        log.info("live %s: the look before the job failed: %s", app, exc)
        return f"{APPS[app]['label']} can't be reached right now.", ""
    finally:
        if live is not None:
            try:
                if not live.thread.stuck:
                    live.thread.run(live._forget, 2.0)
            except Exception:
                pass
            live.thread.close()
