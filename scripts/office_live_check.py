"""A real check of Ledger's direct route into Excel, Word and PowerPoint (agents/ledger_live.py).

It starts its OWN copy of each app, makes a NEW blank document in it, runs the typed edits a
job would run, reads the result back, and closes the document without saving. It never
attaches to a document it did not make: it stops if the app it reached is not the one it
started. No model is called unless --model is given (then one Ledger call per app, a few cents).

    python scripts/office_live_check.py            # Excel, Word, PowerPoint
    python scripts/office_live_check.py excel      # one of them
    python scripts/office_live_check.py excel --model
"""

from __future__ import annotations

import asyncio
import ctypes
import gc
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

HANDS_S = 52.0  # three bold red headings by mouse and keyboard, measured 13 November 2026

SALES = [
    ["Region", "Rep", "Units", "Sales"],
    ["West", "Ana Mock", 3, 1200.5],
    ["East", "Bo Sample", 5, 800],
    ["West", "Cy Madeup", 2, 300],
    ["North", "Di Pretend", 7, 2100],
    ["East", "Ed Example", 1, 150.25],
    ["North", "Flo Fiction", 4, 990],
]


def _cells(rows: list[list[Any]]) -> dict[str, Any]:
    return {f"{chr(65 + c)}{r}": v for r, row in enumerate(rows, 1) for c, v in enumerate(row)}


class Timer:
    def __init__(self) -> None:
        self.rows: list[tuple[str, float, str]] = []

    async def step(self, name: str, coro: Any) -> Any:
        began = time.perf_counter()
        try:
            out = await coro
            said = str(out) if out is not None else ""
        except Exception as exc:
            out, said = None, f"FAILED: {type(exc).__name__}: {exc}"
        self.rows.append((name, time.perf_counter() - began, said))
        print(f"  {time.perf_counter() - began:6.2f}s  {name}: {said}", flush=True)
        return out

    def total(self, names: list[str]) -> float:
        return sum(t for n, t, _ in self.rows if n in names)


def _own_pid(hwnd: int) -> int:
    pid = ctypes.c_ulong()
    ctypes.windll.user32.GetWindowThreadProcessId(ctypes.c_void_p(hwnd), ctypes.byref(pid))
    return int(pid.value)


def _end(pid: int) -> None:
    """Our own copy of the app, if it is still there a few seconds after it was asked to go."""
    import subprocess

    for _ in range(20):
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True
        ).stdout
        if str(pid) not in out:
            return
        time.sleep(0.25)
    subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
    print(f"  (our own copy, process {pid}, had not gone after 5 seconds and was ended)")


async def _model_job(live: Any, task: str) -> tuple[Any, float]:
    """One real Ledger job on the open document: the model plans, the edits run."""
    from otto.config import load_dotenv
    from otto.llm.base import Message

    from pyxi.agents.jobs import UNTRUSTED_RULE
    from pyxi.agents.ledger import run_ledger
    from pyxi.companion.brain import make_chat_llm
    from pyxi.config import PyxiSettings

    load_dotenv()  # the model's key, from the folder this is run in; never printed
    llm = make_chat_llm(PyxiSettings())  # the model Ledger's jobs use in the app

    async def ask(system: str, prompt: str) -> tuple[str, float]:
        began = time.perf_counter()
        response = await llm.complete(
            system=system + UNTRUSTED_RULE, messages=[Message.user(prompt)], max_tokens=8000
        )
        print(f"          the model's answer took {time.perf_counter() - began:.2f}s")
        return response.text, response.usage.cost_usd

    began = time.perf_counter()
    result = await run_ledger(task, files=[], live=[live], ask=ask)
    return result, time.perf_counter() - began


async def excel(model: bool) -> None:
    import comtypes.client

    from pyxi.agents import ledger_live
    from pyxi.agents.ledger_live import ExcelLive

    print("\nExcel")
    app = comtypes.client.CreateObject("Excel.Application", dynamic=True)
    pid = 0
    book = None
    try:
        app.Visible = True
        book = app.Workbooks.Add()
        hwnd = int(app.Hwnd)
        pid = _own_pid(hwnd)
        asked: list[str] = []

        async def confirm(action: str, reason: str) -> bool:
            asked.append(action)
            return True

        t = Timer()
        began = time.perf_counter()
        why, name = ledger_live.probe("excel", hwnd)
        print(f"  {time.perf_counter() - began:6.2f}s  look before the job: {why or name}")
        live = ExcelLive(hwnd, confirm=confirm)
        await t.step("find Excel and read the workbook", live.load())
        if live.name != str(book.Name) or str(book.Path):
            raise SystemExit("That is not the blank workbook this check made. Stopping.")
        print("  outline:", live.describe().splitlines()[0])

        await t.step("paste the sales table", live.apply({"op": "set_cells", "cells": _cells(SALES)}))
        await t.step("headings bold and red", live.apply({"op": "format", "range": "A1:D1", "bold": True, "color": "red"}))
        await t.step("totals row", live.apply({"op": "totals_row"}))
        await t.step("pivot beside the data", live.apply({"op": "pivot", "group_by": "Region", "value": "Sales", "agg": "sum"}))
        four = ["paste the sales table", "headings bold and red", "totals row", "pivot beside the data"]
        core = t.total(four)

        sheet = book.ActiveSheet
        checks = {
            "A1 is bold": bool(sheet.Range("A1").Font.Bold),
            "A1 is red": int(sheet.Range("A1").Font.Color) == 0x2530D9,
            "D8 totals Sales": str(sheet.Range("D8").Formula) == "=SUM(D2:D7)" and abs(float(sheet.Range("D8").Value2) - 5540.75) < 0.01,
            "A8 says Total": str(sheet.Range("A8").Formula) == "Total",
            "one pivot table": int(sheet.PivotTables().Count) == 1,
            "pivot is beside the data (F1)": str(sheet.Range("F1").Formula) != "",
            "pivot total matches": any(
                isinstance(v, float) and abs(v - 5540.75) < 0.01
                for row in sheet.Range("F1:G6").Value2 for v in row
            ),
        }

        await t.step("chart", live.apply({"op": "chart", "labels": "Rep", "values": "Sales", "type": "bar", "title": "Sales by rep"}))
        await t.step("number format", live.apply({"op": "format", "range": "D2:D8", "number_format": "$#,##0.00", "fill": "light yellow", "italic": True}))
        await t.step("sort by Sales", live.apply({"op": "sort", "by": "Sales", "descending": True}))
        top_after_sort = float(sheet.Range("D2").Value2 or 0)
        await t.step("freeze the top row", live.apply({"op": "freeze", "rows": 1}))
        await t.step("column widths", live.apply({"op": "resize", "columns": "A:D", "width": 140}))
        await t.step("fit columns", live.apply({"op": "resize", "columns": "B:B"}))
        await t.step("filter buttons", live.apply({"op": "filter"}))
        await t.step("filter off", live.apply({"op": "filter", "off": True}))
        await t.step("find and replace", live.apply({"op": "find_replace", "find": "West", "replace": "Coast"}))
        await t.step("append a row", live.apply({"op": "append_row", "values": ["South", "Gus Invented", 2, 410]}))
        await t.step("add a column", live.apply({"op": "add_column", "header": "Each", "formula": "=D{r}/C{r}"}))
        await t.step("rename the sheet", live.apply({"op": "rename_sheet", "name": "Made-up sales"}))
        await t.step("new sheet", live.apply({"op": "add_sheet", "name": "Notes", "rows": [["Note"], ["All of this is made up"]]}))
        await t.step("overwrite 8 cells (asks first)", live.apply({"op": "set_cells", "sheet": "Notes", "cells": {"A1": "x"} | {f"B{i}": i for i in range(1, 3)}}))
        await t.step("clear cells (asks first)", live.apply({"op": "clear", "sheet": "Made-up sales", "range": "A2:D5"}))
        await t.step("an edit it has no calls for", live.apply({"op": "delete_sheet", "sheet": "Notes"}))
        await t.step("a bad range", live.apply({"op": "format", "range": "A1:D1); evil", "bold": True}))
        await t.step("read it again", live.load())

        checks |= {
            "sorted, largest first": top_after_sort == 2100.0,
            "top row frozen": bool(app.ActiveWindow.FreezePanes) or str(book.ActiveSheet.Name) == "Notes",
            "sheet renamed": "Made-up sales" in [str(s.Name) for s in book.Worksheets],
            "Notes sheet made": "Notes" in [str(s.Name) for s in book.Worksheets],
            "a chart is there": int(book.Worksheets("Made-up sales").ChartObjects().Count) == 1,
            "asked before clearing": any("Clear" in a for a in asked),
            "the unknown edit was refused": "isn't an edit" in t.rows[-3][2],
            "the bad range was refused": "FAILED: LedgerError" in t.rows[-2][2],
            "alerts still on": bool(app.DisplayAlerts),
            "still unsaved, never saved": str(book.Path) == "" and not bool(book.Saved),
        }
        # Can Excel take one of these edits back? Reported, not required: it is Excel's rule.
        data = book.Worksheets("Made-up sales")
        offered = bool(app.CommandBars.FindControl(1, 128).Enabled)
        try:
            app.Undo()
            took_back = str(data.Range("A2").Formula) != ""  # the cleared cells are back
        except Exception as exc:
            took_back = False
            print("  Undo raised:", str(exc)[:120])
        print(f"  Excel's Undo after these edits: offered={offered}, took the last edit (the clear) back={took_back}")

        # A box open in Excel: the look must come back with "busy" in its time limit, not hang.
        def box() -> None:
            import comtypes

            comtypes.CoInitialize()
            try:
                other = comtypes.client.GetActiveObject("Excel.Application", dynamic=True)
                other.Dialogs(42).Show()  # Format Cells > Number; stays until it is closed
            except Exception as exc:
                print("  (the box could not be opened:", exc, ")")
            finally:
                comtypes.CoUninitialize()

        if int(ledger_live._windows_of("XLMAIN").count(hwnd)) and comtypes.client.GetActiveObject("Excel.Application", dynamic=True).Hwnd == hwnd:
            thread = threading.Thread(target=box, daemon=True)
            thread.start()
            await asyncio.sleep(1.5)
            began = time.perf_counter()
            why, _ = await asyncio.to_thread(ledger_live.probe, "excel", hwnd, 3.0)
            took = time.perf_counter() - began
            print(f"  {took:6.2f}s  with a box open in Excel, the look says: {why or 'reachable (!)'}")
            checks["a box open in Excel is reported, within the time limit"] = bool(why) and took < 5
            for dialog in _dialogs(pid):
                ctypes.windll.user32.PostMessageW(ctypes.c_void_p(dialog), 0x0010, 0, 0)  # WM_CLOSE
            thread.join(5)
            checks["the box was closed again"] = not thread.is_alive()
            await asyncio.sleep(0.5)
            why, _ = await asyncio.to_thread(ledger_live.probe, "excel", hwnd, 3.0)
            if why:
                print("  after the box was closed, the look says:", why)
            checks["reachable again afterwards"] = why == ""
        await live.close()

        if model:
            # Two real jobs, each on a second NEW blank workbook of its own.
            jobs = [
                (
                    "three headings bold and red (what took the hands 52s)",
                    "Make the heading cells bold and red.",
                    [["Region", "Units", "Sales"], ["West", 3, 1200.5], ["East", 5, 800]],
                ),
                (
                    "table, bold red headings, totals row, pivot beside it",
                    "Paste a small made-up sales table (Region, Rep, Units, Sales; six rows), make the "
                    "headings bold and red, add a totals row, and add a pivot of sales by region beside it.",
                    [],
                ),
            ]
            for label, task, rows in jobs:
                extra = app.Workbooks.Add()
                try:
                    sheet2 = extra.ActiveSheet
                    for r, row in enumerate(rows, 1):
                        for c, v in enumerate(row, 1):
                            sheet2.Cells(r, c).Formula = v
                    live = ExcelLive(int(app.Hwnd), confirm=confirm)
                    result, took = await _model_job(live, task)
                    print(f"  {took:6.2f}s  with the real model, end to end: {label} (${result.cost_usd:.4f})")
                    for change in result.changes:
                        print("          -", change)
                    for problem in result.problems:
                        print("          ! ", problem)
                    checks[f"model run, {label}: headings are bold and red"] = (
                        bool(sheet2.Range("A1").Font.Bold) and int(sheet2.Range("A1").Font.Color) == 0x2530D9
                    )
                    if not rows:
                        checks["model run made the pivot"] = int(sheet2.PivotTables().Count) == 1
                        checks["model run added a total"] = any(
                            str(v).startswith("=SUM(") for row in sheet2.UsedRange.Formula for v in row
                        )
                finally:
                    extra.Close(False)
            print(f"  against the hands: {HANDS_S:.0f}s for three bold red headings alone")

        print(f"  the four steps asked for: {core:.2f}s without the model's answer")
        _report(checks)
    finally:
        try:
            if book is not None:
                book.Close(False)
        finally:
            app.Quit()
            del book, app
            gc.collect()
            if pid:
                _end(pid)
    print("  closed without saving")


def _dialogs(pid: int) -> list[int]:
    """Visible top-level windows of that process that are boxes, not the main window."""
    u = ctypes.windll.user32
    found: list[int] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def visit(hwnd: int, _: Any) -> bool:
        buf = ctypes.create_unicode_buffer(64)
        u.GetClassNameW(ctypes.c_void_p(hwnd), buf, 64)
        if u.IsWindowVisible(ctypes.c_void_p(hwnd)) and _own_pid(hwnd) == pid and buf.value.startswith("bosa_sdm"):
            found.append(int(hwnd))
        return True

    u.EnumWindows(visit, 0)
    return found


def _report(checks: dict[str, bool]) -> None:
    for name, ok in checks.items():
        print(f"  {'ok  ' if ok else 'NOT '} {name}")
    if not all(checks.values()):
        global FAILED
        FAILED = True


FAILED = False


async def word(model: bool) -> None:
    import comtypes.client

    from pyxi.agents.ledger_live_docs import WordLive

    print("\nWord")
    app = comtypes.client.CreateObject("Word.Application", dynamic=True)
    doc = None
    pid = 0
    try:
        app.Visible = True
        doc = app.Documents.Add()
        hwnd = int(app.ActiveWindow.Hwnd)
        pid = _own_pid(hwnd)
        t = Timer()
        live = WordLive(hwnd)
        await t.step("find Word and read the document", live.load())
        if live.name != str(doc.Name) or str(doc.Path):
            raise SystemExit("That is not the blank document this check made. Stopping.")
        await t.step("heading", live.apply({"op": "add_heading", "text": "Made-up quarterly notes", "level": 1}))
        await t.step("paragraph", live.apply({"op": "add_paragraph", "text": "Apples sold well in the west. Pears were flat."}))
        await t.step("bullets", live.apply({"op": "add_bullets", "items": ["Apples up", "Pears flat", "Plums new"]}))
        await t.step("bold and colour", live.apply({"op": "format_text", "find": "Apples", "bold": True, "color": "red"}))
        await t.step("find and replace", live.apply({"op": "replace_text", "find": "Pears", "replace": "Peaches"}))
        await t.step("table", live.apply({"op": "add_table", "rows": [["Fruit", "Boxes"], ["Apples", 12], ["Peaches", 7]]}))
        await t.step("landscape", live.apply({"op": "page_setup", "orientation": "landscape"}))
        await t.step("read it again", live.load())
        await t.step("a line made a heading", live.apply({"op": "set_style", "i": 1, "style": "Heading 2"}))
        await t.step("centred", live.apply({"op": "align", "i": 0, "align": "center"}))
        await t.step("an edit it has no calls for", live.apply({"op": "delete_paragraph", "i": 0}))
        text = str(doc.Content.Text)
        checks = {
            "the heading is Heading 1": int(doc.Paragraphs(1).OutlineLevel) == 1,
            "the text is there": "Made-up quarterly notes" in text and "Plums new" in text,
            "Pears became Peaches": "Pears" not in text and "Peaches" in text,
            "Apples is bold": bool(doc.Paragraphs(2).Range.Words(1).Font.Bold),
            "one table, 3 by 2": int(doc.Tables.Count) == 1 and int(doc.Tables(1).Rows.Count) == 3,
            "landscape": int(doc.PageSetup.Orientation) == 1,
            "paragraph 1 is Heading 2": int(doc.Paragraphs(2).OutlineLevel) == 2,
            "never saved": str(doc.Path) == "" and not bool(doc.Saved),
        }
        before = int(doc.Paragraphs.Count)
        undone = bool(doc.Undo())
        checks["one Undo takes back one edit (the centring)"] = undone and int(doc.Paragraphs(1).Alignment) == 0 and int(doc.Paragraphs.Count) == before
        await live.close()
        print(f"  all edits: {sum(x for _, x, _ in t.rows[1:]):.2f}s without the model's answer")
        if model:
            live = WordLive(hwnd)
            result, took = await _model_job(live, "Add a heading 'Next steps' at the end and two bullets under it: 'Order more apples' and 'Review plums'.")
            print(f"  {took:6.2f}s  a job with the real model, end to end (${result.cost_usd:.4f}): {result.changes}")
            checks["model run added the heading"] = "Next steps" in str(doc.Content.Text)
        _report(checks)
    finally:
        try:
            if doc is not None:
                doc.Close(0)
        finally:
            app.Quit(0)
            del doc, app
            gc.collect()
            if pid:
                _end(pid)
    print("  closed without saving")


async def powerpoint(model: bool) -> None:
    import comtypes.client
    from PIL import Image

    from pyxi.agents.ledger_live_docs import DeckLive

    print("\nPowerPoint")
    picture = Path(tempfile.mkdtemp(prefix="pyxi_check_")) / "made-up.png"
    Image.new("RGB", (240, 160), (30, 120, 200)).save(picture)
    app = comtypes.client.CreateObject("PowerPoint.Application", dynamic=True)
    deck = None
    try:
        app.Visible = True
        deck = app.Presentations.Add()
        t = Timer()
        live = DeckLive()
        await t.step("find PowerPoint and read the deck", live.load())
        if live.name != str(deck.Name) or str(deck.Path):
            raise SystemExit("That is not the blank deck this check made. Stopping.")
        await t.step("title slide", live.apply({"op": "add_slide", "title": "Made-up sales review", "subtitle": "Nothing here is real"}))
        await t.step("slide with bullets", live.apply({"op": "add_slide", "title": "Highlights", "bullets": ["West is up", "East is flat"], "notes": "Say it is made up"}))
        await t.step("more bullets", live.apply({"op": "add_bullets", "slide": 2, "items": ["North is new"]}))
        await t.step("text box", live.apply({"op": "add_text_box", "slide": 2, "text": "A note", "bold": True, "color": "red", "size": 20}))
        await t.step("picture", live.apply({"op": "add_image", "slide": 2, "path": str(picture), "x": 8, "y": 4}))
        await t.step("title text", live.apply({"op": "set_title", "slide": 2, "text": "Highlights so far"}))
        await t.step("title bold", live.apply({"op": "format_text", "slide": 2, "which": "title", "bold": True}))
        await t.step("reorder", live.apply({"op": "move_slide", "slide": 2, "to": 1}))
        await t.step("an edit it has no calls for", live.apply({"op": "delete_slide", "slide": 1}))
        await t.step("read it again", live.load())
        first = deck.Slides(1)
        checks = {
            "two slides": int(deck.Slides.Count) == 2,
            "the bullets slide is first now": str(first.Shapes.Title.TextFrame.TextRange.Text) == "Highlights so far",
            "three bullets": str(first.Shapes(2).TextFrame.TextRange.Text).count("\r") == 2,
            "a picture is on it": any(int(s.Type) == 13 for s in first.Shapes),
            "notes set": "made up" in str(first.NotesPage.Shapes.Placeholders(2).TextFrame.TextRange.Text),
            "outline names the title": "Highlights so far" in live.describe(),
            "never saved": str(deck.Path) == "",
        }
        await live.close()
        print(f"  all edits: {sum(x for _, x, _ in t.rows[1:]):.2f}s without the model's answer")
        if model:
            live = DeckLive()
            result, took = await _model_job(live, "Add a slide at the end titled 'Next steps' with two bullets: 'Order more apples' and 'Review plums'.")
            print(f"  {took:6.2f}s  a job with the real model, end to end (${result.cost_usd:.4f}): {result.changes}")
            checks["model run added the slide"] = int(deck.Slides.Count) == 3
        _report(checks)
    finally:
        try:
            if deck is not None:
                deck.Saved = True
                deck.Close()
        finally:
            app.Quit()
            del deck, app
            gc.collect()
    print("  closed without saving")


async def main() -> int:
    import comtypes

    if sys.platform != "win32":
        print("This check needs Windows and Microsoft Office.")
        return 2
    comtypes.CoInitialize()
    wanted = [a for a in sys.argv[1:] if not a.startswith("--")] or ["excel", "word", "powerpoint"]
    model = "--model" in sys.argv
    running = _running()
    for name in wanted:
        if name in running:
            print(f"\n{name}: it is already open on this PC, so this check leaves it alone. Close it and run again.")
            continue
        await {"excel": excel, "word": word, "powerpoint": powerpoint}[name](model)
    return 1 if FAILED else 0


def _running() -> set[str]:
    import subprocess

    out = subprocess.run(["tasklist", "/NH"], capture_output=True, text=True).stdout.lower()
    return {
        name
        for name, exe in (("excel", "excel.exe"), ("word", "winword.exe"), ("powerpoint", "powerpnt.exe"))
        if exe in out
    }


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
