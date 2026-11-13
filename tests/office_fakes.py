"""A pretend Excel, Word and PowerPoint for the direct route's tests (agents/ledger_live.py).

Nothing here is COM. Each pretend app keeps just enough of a document to answer what the
edits read (cells, paragraphs, slides) and writes every call and every property set to
``log`` as one line, so a test can say exactly which calls an edit turned into:

    Sheet1.Range(A1:D1).Font.Bold = True
    Book.PivotCaches().Create(1, Sheet1.Range(A1:D4))
"""

from __future__ import annotations

import re
import threading
from typing import Any

from pyxi.agents import ledger_live
from pyxi.agents.ledger_common import a1_bounds, col_letter


class COMError(Exception):
    """Shaped like comtypes' own: (hresult, text, (description, source, help file, id, _))."""


def busy() -> COMError:
    return COMError(-2146777998, None, (None, None, None, 0, None))


def refused(words: str) -> COMError:
    return COMError(-2146827284, None, (words, "Microsoft Excel", None, 0, None))


def show(arg: Any) -> str:
    if isinstance(arg, Rec):
        return arg._path
    if arg is ledger_live.MISSING or repr(arg) == "VARIANT.missing":  # an argument left out
        return "-"
    return repr(arg)


class Rec:
    """Anything in the app that only has to be told things: it writes them down."""

    def __init__(self, log: list[str], path: str, answers: dict[str, Any] | None = None) -> None:
        object.__setattr__(self, "_log", log)
        object.__setattr__(self, "_path", path)
        object.__setattr__(self, "_answers", answers or {})

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)
        key = f"{self._path}.{name}"
        if key in self._answers:
            return self._answers[key]
        return Rec(self._log, key, self._answers)

    def __setattr__(self, name: str, value: Any) -> None:
        self._log.append(f"{self._path}.{name} = {show(value)}")

    def __call__(self, *args: Any) -> Any:
        line = f"{self._path}({', '.join(show(a) for a in args)})"
        self._log.append(line)
        if line in self._answers:
            return self._answers[line]
        return Rec(self._log, line, self._answers)

    def __iter__(self) -> Any:
        return iter(())


class Items:
    """A collection the way Office hands one out: counted, walked, and asked for by number."""

    def __init__(self, items: list[Any]) -> None:
        self.items = items

    @property
    def Count(self) -> int:
        return len(self.items)

    def __iter__(self) -> Any:
        return iter(list(self.items))

    def __call__(self, index: Any) -> Any:
        if isinstance(index, int):
            return self.items[index - 1]
        return next(i for i in self.items if str(i.Name).lower() == str(index).lower())


# ------------------------------------------------------------------ Excel
def _evaluate(sheet: Sheet, formula: str) -> Any:
    m = re.fullmatch(r"=(SUM|AVERAGE|COUNT|MAX|MIN)\(([A-Z]+)(\d+):([A-Z]+)(\d+)\)", formula)
    if not m:
        return 0.0
    c1, r1, c2, r2 = a1_bounds(f"{m.group(2)}{m.group(3)}:{m.group(4)}{m.group(5)}")
    nums = [
        float(v)
        for r in range(r1, r2 + 1)
        for c in range(c1, c2 + 1)
        if isinstance(v := sheet.cells.get((r, c)), int | float) and not isinstance(v, bool)
    ]
    if not nums:
        return 0.0
    return {
        "SUM": sum(nums), "AVERAGE": sum(nums) / len(nums), "COUNT": float(len(nums)),
        "MAX": max(nums), "MIN": min(nums),
    }[m.group(1)]  # fmt: skip


class Cell(Rec):
    def __init__(self, sheet: Sheet, r: int, c: int) -> None:
        super().__init__(sheet.log, f"{sheet.name}.Cells({r}, {c})")
        object.__setattr__(self, "_sheet", sheet)
        object.__setattr__(self, "_at", (r, c))

    def __getattr__(self, name: str) -> Any:
        v = self._sheet.cells.get(self._at)
        if name == "Formula":
            return "" if v is None else str(v)
        if name == "Value2":
            if isinstance(v, str) and v.startswith("="):
                return _evaluate(self._sheet, v)
            return float(v) if isinstance(v, int | float) and not isinstance(v, bool) else v
        if name == "NumberFormat":
            return self._sheet.formats.get(self._at, "General")
        return super().__getattr__(name)

    def __setattr__(self, name: str, value: Any) -> None:
        if self._sheet.book.app.fail is not None:
            raise self._sheet.book.app.fail
        if name == "Formula":
            if value == "":
                self._sheet.cells.pop(self._at, None)
            else:
                self._sheet.cells[self._at] = value
        if name == "NumberFormat":
            self._sheet.formats[self._at] = value
        super().__setattr__(name, value)


class Range(Rec):
    def __init__(self, sheet: Sheet, a1: str) -> None:
        super().__init__(sheet.log, f"{sheet.name}.Range({a1})")
        object.__setattr__(self, "_sheet", sheet)
        object.__setattr__(self, "_a1", a1)

    def __getattr__(self, name: str) -> Any:
        if name in ("Left", "Top"):
            return 10.0 * a1_bounds(self._a1)[0 if name == "Left" else 1]
        if name in ("ClearContents", "Clear"):
            return self._clear(name)
        if name == "AutoFilter":
            return self._filter
        return super().__getattr__(name)

    def _clear(self, name: str) -> Any:
        def run() -> None:
            self._log.append(f"{self._path}.{name}()")
            c1, r1, c2, r2 = a1_bounds(self._a1)
            for at in [k for k in self._sheet.cells if r1 <= k[0] <= r2 and c1 <= k[1] <= c2]:
                del self._sheet.cells[at]

        return run

    def _filter(self) -> None:
        self._log.append(f"{self._path}.AutoFilter()")
        self._sheet.AutoFilterMode = True


class Used:
    def __init__(self, sheet: Sheet) -> None:
        self._sheet = sheet
        cells = sheet.cells
        self.Row, self.Column = 1, 1
        self._rows = max((r for r, _ in cells), default=1)
        self._cols = max((c for _, c in cells), default=1)
        self.Rows = Items([None] * self._rows)
        self.Columns = Items([None] * self._cols)

    def _table(self, of: Any) -> Any:
        rows = tuple(
            tuple(of(self._sheet.cells.get((r, c))) for c in range(1, self._cols + 1))
            for r in range(1, self._rows + 1)
        )
        return rows[0][0] if self._rows == self._cols == 1 else rows

    @property
    def Formula(self) -> Any:
        return self._table(lambda v: "" if v is None else str(v))

    def Value(self) -> Any:
        if self._sheet.has_error_cells:
            raise NotImplementedError("typecode 10 = 0xa)")  # what comtypes says to #DIV/0!

        def of(v: Any) -> Any:
            if isinstance(v, str) and v.startswith("="):
                return _evaluate(self._sheet, v)
            return float(v) if isinstance(v, int | float) and not isinstance(v, bool) else v

        return self._table(of)

    def Replace(self, *args: Any) -> None:
        self._sheet.log.append(f"{self._sheet.name}.UsedRange.Replace({', '.join(show(a) for a in args)})")


class Sheet:
    def __init__(self, book: Book, name: str, rows: list[list[Any]] | None = None) -> None:
        self.book, self.log, self.name = book, book.log, name
        self.cells: dict[tuple[int, int], Any] = {}
        self.formats: dict[tuple[int, int], str] = {}
        self.AutoFilterMode = False
        self.has_error_cells = False
        self.pivots: list[Any] = []
        self.Shapes = Rec(self.log, f"{name}.Shapes")
        for r, row in enumerate(rows or [], 1):
            for c, v in enumerate(row, 1):
                if v not in (None, ""):
                    self.cells[(r, c)] = v

    @property
    def Name(self) -> str:
        return self.name

    @Name.setter
    def Name(self, value: str) -> None:
        self.log.append(f"{self.name}.Name = {value!r}")
        self.name = value

    def Cells(self, r: int, c: int) -> Cell:
        return Cell(self, r, c)

    def Range(self, a1: str) -> Range:
        return Range(self, a1)

    @property
    def UsedRange(self) -> Used:
        return Used(self)

    def ChartObjects(self) -> Items:
        return Items([])

    def PivotTables(self) -> Items:
        return Items(self.pivots)

    def Activate(self) -> None:
        self.log.append(f"{self.name}.Activate()")
        self.book.active = self

    def text(self, a1: str) -> Any:
        c, r, _, _ = a1_bounds(a1)
        return self.cells.get((r, c))


class Sheets(Items):
    def __init__(self, book: Book) -> None:
        super().__init__(book.sheets)
        self.book = book

    def Add(self, before: Any = None, after: Any = None) -> Sheet:
        sheet = Sheet(self.book, f"Sheet{len(self.items) + 1}")
        self.book.log.append(f"Book.Worksheets.Add({show(before)}, after {after.name})")
        self.items.append(sheet)
        self.book.active = sheet
        return sheet


class Book:
    def __init__(self, app: Excel, name: str = "Book1", path: str = "") -> None:
        self.app, self.log = app, app.log
        self.Name, self.Path = name, path
        self.sheets: list[Sheet] = []
        self.active: Sheet | None = None
        self.saved_calls = 0

    def add(self, name: str, rows: list[list[Any]] | None = None) -> Sheet:
        sheet = Sheet(self, name, rows)
        self.sheets.append(sheet)
        self.active = self.active or sheet
        return sheet

    @property
    def Worksheets(self) -> Sheets:
        return Sheets(self)

    @property
    def ActiveSheet(self) -> Sheet | None:
        return self.active

    def PivotCaches(self) -> Rec:
        return Rec(self.log, "Book.PivotCaches()")

    def Save(self) -> None:  # nothing may ever call these
        self.saved_calls += 1

    Close = SaveAs = Save


class Excel:
    def __init__(self) -> None:
        self.log: list[str] = []
        self.book: Book | None = Book(self)
        self.fail: Exception | None = None  # the next write raises this
        self.ActiveWindow = Rec(self.log, "Window")
        self.DisplayAlerts = True

    @property
    def ActiveWorkbook(self) -> Book | None:
        return self.book

    def Union(self, a: Any, b: Any) -> Rec:
        return Rec(self.log, f"Union({show(a)}, {show(b)})")


SALES = [
    ["Region", "Rep", "Units", "Sales"],
    ["West", "Ana Mock", 3, 1200.5],
    ["East", "Bo Sample", 5, 800],
    ["West", "Cy Madeup", 2, 300],
]


def excel(rows: list[list[Any]] | None = SALES, name: str = "Sales") -> tuple[Excel, Sheet]:
    app = Excel()
    assert app.book is not None
    return app, app.book.add(name, rows)


def cells(rows: list[list[Any]]) -> dict[str, Any]:
    return {f"{col_letter(c)}{r}": v for r, row in enumerate(rows, 1) for c, v in enumerate(row, 1)}


# ------------------------------------------------------------------ Word
class WordRange(Rec):
    def __init__(self, doc: Doc, index: int) -> None:
        super().__init__(doc.log, f"Paragraphs({index + 1}).Range")
        object.__setattr__(self, "_doc", doc)
        object.__setattr__(self, "_index", index)

    def __getattr__(self, name: str) -> Any:
        doc, i = self._doc, self._index
        if name == "Text":
            return doc.paras[i] + "\r"
        if name in ("Start", "End"):
            return i if name == "Start" else i + 1
        if name == "InsertBefore":
            def insert(text: str) -> None:
                parts = text.split("\r")
                doc.paras[i : i + 1] = [*parts[:-1], parts[-1] + doc.paras[i]]
                doc.log.append(f"Paragraphs({i + 1}).Range.InsertBefore({text!r})")

            return insert
        if name == "InsertParagraphAfter":
            return lambda: doc.paras.insert(i + 1, "")
        if name == "InsertParagraphBefore":
            return lambda: doc.paras.insert(i, "")
        return super().__getattr__(name)


class Para:
    def __init__(self, doc: Doc, index: int) -> None:
        self._doc, self._index = doc, index
        self.Range = WordRange(doc, index)
        self.OutlineLevel = doc.levels.get(doc.paras[index], 10)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in ("Style", "Alignment"):
            self._doc.log.append(f"Paragraphs({self._index + 1}).{name} = {value!r}")
            return
        object.__setattr__(self, name, value)


class Find:
    def __init__(self, doc: Doc) -> None:
        self._doc, self._seen = doc, 0

    def Execute(self, find: str, *more: Any) -> bool:
        text = "\r".join(self._doc.paras)
        if more:  # every place at once
            self._doc.log.append(f"Find.Execute({find!r}, {', '.join(show(m) for m in more)})")
            self._doc.paras[:] = re.sub(re.escape(find), str(more[8]), text, flags=re.I).split("\r")
            return True
        self._seen += 1
        return self._seen <= text.lower().count(find.lower())


class Content(Rec):
    def __init__(self, doc: Doc) -> None:
        super().__init__(doc.log, "Content")
        object.__setattr__(self, "_doc", doc)
        object.__setattr__(self, "_find", Find(doc))

    def __getattr__(self, name: str) -> Any:
        if name == "Text":
            return "".join(p + "\r" for p in self._doc.paras)
        if name == "Find":
            return self._find
        if name == "InsertParagraphAfter":
            return lambda: self._doc.paras.append("")
        return super().__getattr__(name)


class Tables(Items):
    def __init__(self, doc: Doc) -> None:
        super().__init__(doc.tables)
        self.doc = doc

    def Add(self, where: Any, rows: int, cols: int) -> Rec:
        self.doc.log.append(f"Tables.Add({show(where)}, {rows}, {cols})")
        at = where._index
        self.doc.paras[at:at] = ["\x07"] * (rows * (cols + 1))  # a mark for each cell and row end
        table = Rec(self.doc.log, f"Table{len(self.items) + 1}")
        self.items.append(table)
        return table


class Doc:
    def __init__(self, app: Word, paras: list[str], levels: dict[str, int] | None = None) -> None:
        self.app, self.log = app, app.log
        self.Name, self.Path = "Document1", ""
        self.paras = list(paras)
        self.levels = levels or {}
        self.tables: list[Any] = []
        self.PageSetup = Rec(self.log, "PageSetup")

    @property
    def Paragraphs(self) -> Any:
        doc = self

        class Paragraphs:
            Count = len(doc.paras)

            def __call__(self, index: int) -> Para:
                return Para(doc, index - 1)

        return Paragraphs()

    @property
    def Content(self) -> Content:
        return Content(self)

    @property
    def Tables(self) -> Tables:
        return Tables(self)

    def Range(self, start: int, end: int) -> Rec:
        return Rec(self.log, f"Range({start}, {end})")


class Word:
    def __init__(self, paras: list[str] | None = None, levels: dict[str, int] | None = None) -> None:
        self.log: list[str] = []
        self.doc: Doc | None = Doc(self, paras if paras is not None else [""], levels)
        self.UndoRecord = Rec(self.log, "UndoRecord")

    @property
    def Documents(self) -> Items:
        return Items([self.doc] if self.doc else [])

    @property
    def ActiveDocument(self) -> Doc | None:
        return self.doc


# ------------------------------------------------------------------ PowerPoint
class TextRange(Rec):
    def __init__(self, shape: Shape) -> None:
        super().__init__(shape.log, f"{shape.Name}.Text")
        object.__setattr__(self, "_shape", shape)

    def __getattr__(self, name: str) -> Any:
        if name == "Text":
            return self._shape.text
        return super().__getattr__(name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "Text":
            self._shape.text = value
        super().__setattr__(name, value)


class Shape:
    def __init__(self, log: list[str], name: str, kind: int, placeholder: int = 0, text: bool = True) -> None:
        self.log, self.Name, self.Type = log, name, kind
        self.text = ""
        self.HasTextFrame = -1 if text else 0
        self.PlaceholderFormat = Items([])
        self.PlaceholderFormat.Type = placeholder  # type: ignore[attr-defined]

    @property
    def TextFrame(self) -> Any:
        shape = self

        class Frame:
            TextRange = TextRange(shape)

        return Frame()


class Shapes(Items):
    def __init__(self, slide: Slide) -> None:
        super().__init__(slide.shapes)
        self.slide = slide

    @property
    def HasTitle(self) -> int:
        return -1 if any(s.PlaceholderFormat.Type in (1, 3) for s in self.items) else 0

    @property
    def Title(self) -> Shape:
        return next(s for s in self.items if s.PlaceholderFormat.Type in (1, 3))

    def AddTextbox(self, *args: Any) -> Shape:
        self.slide.log.append(f"Slide.AddTextbox({', '.join(show(a) for a in args)})")
        box = Shape(self.slide.log, f"TextBox {len(self.items) + 1}", 17)
        self.items.append(box)
        return box

    def AddPicture(self, *args: Any) -> Shape:
        self.slide.log.append(f"Slide.AddPicture({', '.join(show(a) for a in args[1:])})")
        picture = Shape(self.slide.log, f"Picture {len(self.items) + 1}", 13, text=False)
        self.items.append(picture)
        return picture


class Slide:
    def __init__(self, deck: Deck, layout: int) -> None:
        self.deck, self.log, self.layout = deck, deck.log, layout
        self.shapes: list[Shape] = []
        if layout not in (12,):
            self.shapes.append(Shape(self.log, "Title 1", 14, 1))
        if layout in (1, 2, 29, 33):
            self.shapes.append(Shape(self.log, "Placeholder 2", 14, 4 if layout == 1 else 2))
        self.NotesPage = Rec(self.log, "Notes")

    @property
    def Shapes(self) -> Shapes:
        return Shapes(self)

    def MoveTo(self, place: int) -> None:
        self.log.append(f"Slide.MoveTo({place})")
        self.deck.slides.remove(self)
        self.deck.slides.insert(place - 1, self)

    def Duplicate(self) -> None:
        self.log.append("Slide.Duplicate()")

    @property
    def title(self) -> str:
        return self.Shapes.Title.text


class Slides(Items):
    def __init__(self, deck: Deck) -> None:
        super().__init__(deck.slides)
        self.deck = deck

    def Add(self, at: int, layout: int) -> Slide:
        self.deck.log.append(f"Slides.Add({at}, {layout})")
        slide = Slide(self.deck, layout)
        self.items.insert(at - 1, slide)
        return slide


class Deck:
    def __init__(self, app: PowerPoint) -> None:
        self.app, self.log = app, app.log
        self.Name, self.Path = "Presentation1", ""
        self.slides: list[Slide] = []

        class Setup:
            SlideWidth, SlideHeight = 960.0, 540.0

        self.PageSetup = Setup()

    @property
    def Slides(self) -> Slides:
        return Slides(self)


class PowerPoint:
    def __init__(self) -> None:
        self.log: list[str] = []
        self.deck: Deck | None = Deck(self)

    @property
    def Presentations(self) -> Items:
        return Items([self.deck] if self.deck else [])

    @property
    def ActivePresentation(self) -> Deck | None:
        return self.deck

    def StartNewUndoEntry(self) -> None:
        self.log.append("StartNewUndoEntry()")


# ------------------------------------------------------------------ getting one into a target
def live(cls: type, app: Any, **kw: Any) -> Any:
    """That target, joined to the pretend app, on a thread that needs no COM."""
    kw.setdefault("thread", ledger_live.ComThread(ledger_live.APPS[cls.app]["label"], init=None))
    return cls(None, connect=lambda name, hwnd: app, **kw)


class Stuck:
    """An app with a box open: whatever is asked of it does not come back until released."""

    def __init__(self) -> None:
        self.release = threading.Event()
        self.asked = 0

    def __getattr__(self, name: str) -> Any:
        self.asked += 1
        self.release.wait(10)
        raise busy()
