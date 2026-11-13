"""Ledger in the app itself, for Word and PowerPoint: the document or deck that is open, edited
through the app's own automation. See ledger_live.py for how it works and what it never does.

Both apps can take these edits back: each edit is one step under Undo.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from pyxi.agents.ledger_common import LedgerError
from pyxi.agents.ledger_live import Live, _bgr, _count, _number, _plural

_END = 10**9  # an insert at the end of the document, as a place to count from
_HEADINGS = {"title": -63, "subtitle": -75, "normal": -1}
_WORD_ALIGN = {"left": 0, "center": 1, "centre": 1, "right": 2, "justify": 3}
_HIGHLIGHT = {"yellow": 7, "green": 4, "blue": 3, "pink": 5, "red": 6, "gray": 16, "grey": 16, "none": 0}
_PAPER = {"letter": 2, "a4": 7, "legal": 4}
_IMAGES = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff"}

WORD_MENU = """The document open in Word on their PC. You change it live, in the app; nothing is saved, and each edit is one step they can undo. "after" is a paragraph number from the outline; leave it out for the end; "after": "start" puts it at the very top; text placed after an empty line fills that line:
- {"op": "add_heading", "text": "..", "level": 1, "after"?: 3}
- {"op": "add_paragraph", "text": "..", "after"?: 3}
- {"op": "add_bullets", "items": [".."], "numbered"?: false, "after"?: 3}
- {"op": "set_style", "i": 4, "style": "Heading 2"|"Title"|"Subtitle"|"Normal"}  (makes a line that's already there a heading)
- {"op": "format_text", "find"?: "exact words" | "i"?: 4, "bold"?: true, "italic"?: true, "underline"?: true, "strikethrough"?: true, "size"?: 14, "color"?: "#1A73E8"|"red", "highlight"?: "yellow", "font"?: "Arial"}  (find formats every place the words appear; i a whole paragraph)
- {"op": "align", "i": 4, "align": "left"|"center"|"right"|"justify"}
- {"op": "replace_text", "find": "..", "replace": ".."}  (every place it appears)
- {"op": "add_table", "rows": [["Header", ".."], ["..", ".."]], "after"?: 3}  (or "n_rows" and "n_cols" for an empty table of that size)
- {"op": "page_setup", "margin"?: 1.0, "margins"?: {"top": 1, "bottom": 1, "left": 1.25, "right": 1.25}, "orientation"?: "landscape"|"portrait", "size"?: "letter"|"a4"|"legal"}  (inches)
Anything else (saving, deleting text or tables, pictures, headers and footers) is not yours here: return no edits and say so, and it is done another way."""

DECK_MENU = """The deck open in PowerPoint on their PC. You change it live, in the app; nothing is saved, and each edit is one step they can undo. Slides are numbered from 1. x, y, w and h are inches from the slide's top left; leave them out and a new thing is centred:
- {"op": "add_slide", "title"?: "..", "bullets"?: [".."], "subtitle"?: "..", "layout"?: "title"|"title and body"|"section"|"two columns"|"title only"|"blank", "after"?: 2, "notes"?: ".."}  ("after": 0 puts it first; no "after": last)
- {"op": "set_title", "slide": 2, "text": ".."}
- {"op": "set_text", "slide": 2, "which"?: "title"|"subtitle"|"body", "text"?: "..", "bullets"?: [".."]}  (what the box says, replacing what was there)
- {"op": "add_bullets", "slide": 2, "items": [".."], "replace"?: false}
- {"op": "add_text_box", "slide": 2, "text": "..", "x"?: 1, "y"?: 1, "w"?: 4, "h"?: 1, "size"?: 18, "bold"?: true, "color"?: "red"}
- {"op": "add_image", "slide": 2, "path": "C:\\\\..\\\\picture.png", "x"?: 1, "y"?: 1, "w"?: 4, "h"?: 3}  (a picture file on this PC, by the full path they gave)
- {"op": "format_text", "slide": 2, "which"?: "title"|"subtitle"|"body", "bold"?: true, "italic"?: true, "underline"?: true, "size"?: 24, "color"?: "#1A73E8", "font"?: "Arial"}  (the whole box)
- {"op": "move_slide", "slide": 3, "to": 1}  and  {"op": "duplicate_slide", "slide": 3}
- {"op": "set_notes", "slide": 2, "text": ".."}
Anything else (saving, deleting a slide or a thing on it, shapes, charts, tables, backgrounds) is not yours here: return no edits and say so, and it is done another way."""


def _one_line(text: Any) -> str:
    return " ".join(str(text or "").replace("\x07", " ").split())


# ------------------------------------------------------------------ Word
class WordLive(Live):
    app = "word"
    kind = "docx_live"
    menu = WORD_MENU

    def _document(self) -> Any:
        if _count(self.com.Documents) == 0:
            return None
        return self.com.ActiveDocument

    def _read(self, max_chars: int = 9000) -> str:
        doc = self.doc
        self._inserts: list[tuple[int, int]] = []  # (after which outline paragraph, how many came)
        count = _count(doc.Paragraphs)
        text = str(doc.Content.Text or "")
        pieces = text.split("\r")
        if text.endswith("\r"):
            pieces.pop()
        if len(pieces) != count:  # something in it that isn't plain paragraphs: read one by one
            pieces = [str(doc.Paragraphs(i).Range.Text or "") for i in range(1, min(count, 400) + 1)]
        self.paras = count
        out = [
            f"Word document {self.name}, open in Word now: {count} paragraphs, {_count(doc.Tables)} tables."
        ]
        size, asked = 0, 0
        for i, piece in enumerate(pieces):
            cell = "\x07" in piece
            shown = _one_line(piece)
            style = "table cell" if cell else ""
            if shown and not cell and len(shown) <= 120 and asked < 60:
                asked += 1  # one call each, so only for lines short enough to be a heading
                try:
                    level = int(doc.Paragraphs(i + 1).OutlineLevel)
                except Exception:
                    level = 10
                style = f"Heading {level}" if 1 <= level <= 9 else ""
            line = f"[{i}] ({style}) {shown}" if shown else f"[{i}] (empty)"
            size += len(line)
            if size > max_chars:
                out.append("  ...")
                break
            out.append(line[:400])
        return "\n".join(out)

    def _do(self, fn: Any, op: dict[str, Any]) -> str:
        self._wait(lambda: self.doc.Name)
        self.sent = True
        record = self.com.UndoRecord
        record.StartCustomRecord(f"Pyxi: {str(op.get('op')).replace('_', ' ')}")
        try:
            return fn(op)
        finally:
            record.EndCustomRecord()

    # -- places
    def _now(self, i: int) -> int:
        """Where the outline's paragraph ``i`` is now, counted from 1 the way Word counts."""
        return i + sum(n for after, n in self._inserts if after < i) + 1

    def _para(self, op: dict[str, Any], key: str = "i") -> Any:
        try:
            i = int(op.get(key))
        except (TypeError, ValueError):
            raise LedgerError(f"{op.get('op')} needs {key}: a paragraph number from the outline.") from None
        if not 0 <= i < self.paras:
            raise LedgerError(f"There's no paragraph {i}.")
        return self.doc.Paragraphs(self._now(i))

    def _spot(self, op: dict[str, Any]) -> tuple[int, int]:
        """An empty paragraph to write in, where the edit asked: (its number in Word now, the
        outline paragraph it comes after)."""
        doc = self.doc
        after = op.get("after")
        if after is None or after == "" or after == "end":
            last = _count(doc.Paragraphs)
            if last and not _one_line(doc.Paragraphs(last).Range.Text):
                return last, _END  # the empty line at the end takes it
            doc.Content.InsertParagraphAfter()
            return last + 1, _END
        if str(after).strip().lower() in ("start", "top", "-1", "before"):
            doc.Paragraphs(1).Range.InsertParagraphBefore()
            return 1, -1
        try:
            a = int(after)
        except (TypeError, ValueError):
            raise LedgerError(f"There's no paragraph {after}.") from None
        if not 0 <= a < self.paras:
            raise LedgerError(f"There's no paragraph {after}.")
        at = a + sum(n for other, n in self._inserts if other <= a) + 1  # after what came before
        anchor = doc.Paragraphs(at)
        if at == self._now(a) and not _one_line(anchor.Range.Text):
            return at, a  # an empty line: the text fills it
        anchor.Range.InsertParagraphAfter()
        return at + 1, a

    def _insert(self, op: dict[str, Any], write: Any) -> None:
        before = _count(self.doc.Paragraphs)
        at, after = self._spot(op)
        write(at)
        self._inserts.append((after, _count(self.doc.Paragraphs) - before))

    @staticmethod
    def _text(value: Any, what: str = "text") -> str:
        text = " ".join(str(value or "").split())
        if not text:
            raise LedgerError(f"That needs {what}.")
        return text

    def _plain(self, para: Any) -> None:
        para.Style = -1  # Normal
        try:
            para.Range.ListFormat.RemoveNumbers()
        except Exception:
            pass

    # -- edits
    def op_add_heading(self, op: dict[str, Any]) -> str:
        text = self._text(op.get("text"))
        level = int(_number(op.get("level") or 1, "level"))
        if not 1 <= level <= 9:
            raise LedgerError("A heading's level is 1 to 9.")

        def write(at: int) -> None:
            para = self.doc.Paragraphs(at)
            para.Range.InsertBefore(text)
            self._plain(para)
            self.doc.Paragraphs(at).Style = -1 - level  # Word's own Heading 1 to Heading 9

        self._insert(op, write)
        return f"Added the heading {text[:60]!r}"

    def op_add_paragraph(self, op: dict[str, Any]) -> str:
        text = self._text(op.get("text"))

        def write(at: int) -> None:
            para = self.doc.Paragraphs(at)
            para.Range.InsertBefore(text)
            self._plain(para)

        self._insert(op, write)
        return "Added a paragraph"

    def op_add_bullets(self, op: dict[str, Any]) -> str:
        items = [self._text(x, "items") for x in (op.get("items") or [])]
        if not items:
            raise LedgerError("add_bullets needs items: the lines of the list.")

        def write(at: int) -> None:
            doc = self.doc
            doc.Paragraphs(at).Range.InsertBefore("\r".join(items))
            start = doc.Paragraphs(at).Range.Start
            end = doc.Paragraphs(at + len(items) - 1).Range.End
            block = doc.Range(start, end)
            block.Style = -1
            if op.get("numbered"):
                block.ListFormat.ApplyNumberDefault()
            else:
                block.ListFormat.ApplyBulletDefault()

        self._insert(op, write)
        return f"Added {_plural(len(items), 'numbered line' if op.get('numbered') else 'bullet')}"

    def op_set_style(self, op: dict[str, Any]) -> str:
        style = " ".join(str(op.get("style") or "").lower().split())
        m = re.fullmatch(r"heading ([1-9])", style)
        code = -1 - int(m.group(1)) if m else _HEADINGS.get(style)
        if code is None:
            raise LedgerError("style should be Heading 1 to Heading 9, Title, Subtitle or Normal.")
        self._para(op).Style = code
        return f"Made paragraph {op.get('i')} {style.title()}"

    def _looks(self, op: dict[str, Any]) -> tuple[list[Any], list[str]]:
        todo: list[Any] = []
        did: list[str] = []
        for key, attr in (("bold", "Bold"), ("italic", "Italic"), ("strikethrough", "StrikeThrough")):
            if key in op:
                on = bool(op[key])
                todo.append(lambda rng, attr=attr, on=on: setattr(rng.Font, attr, on))
                did.append(key if on else f"not {key}")
        if "underline" in op:
            line = 1 if op["underline"] else 0
            todo.append(lambda rng: setattr(rng.Font, "Underline", line))
            did.append("underlined" if line else "not underlined")
        if op.get("size") is not None:
            size = _number(op["size"], "size")
            if not 1 <= size <= 1638:
                raise LedgerError("A font size is between 1 and 1638.")
            todo.append(lambda rng: setattr(rng.Font, "Size", size))
            did.append(f"size {size:g}")
        if op.get("color"):
            color = _bgr(op["color"])
            todo.append(lambda rng: setattr(rng.Font, "Color", color))
            did.append(f"{op['color']}")
        if op.get("font"):
            name = str(op["font"])[:60]
            todo.append(lambda rng: setattr(rng.Font, "Name", name))
            did.append(name)
        if not todo and not op.get("highlight"):
            raise LedgerError("format_text needs something to change: bold, color, size...")
        return todo, did

    def op_format_text(self, op: dict[str, Any]) -> str:
        todo, did = self._looks(op)
        if op.get("highlight"):
            mark = _HIGHLIGHT.get(str(op["highlight"]).lower())
            if mark is None:
                raise LedgerError(f"highlight should be one of {', '.join(_HIGHLIGHT)}.")
            todo.append(lambda rng: setattr(rng, "HighlightColorIndex", mark))
            did.append(f"highlighted {op['highlight']}")
        if op.get("i") not in (None, ""):
            rng = self._para(op).Range
            for step in todo:
                step(rng)
            return f"Made paragraph {op.get('i')} {', '.join(did)}"
        find = str(op.get("find") or "")
        if not find.strip():
            raise LedgerError("format_text needs find (the words) or i (a paragraph number).")
        rng = self.doc.Content
        finder = rng.Find
        n = 0
        while n < 500 and finder.Execute(find.replace("^", "^^")):
            for step in todo:
                step(rng)
            n += 1
            rng.Collapse(0)  # carry on from the end of what was found
        if not n:
            raise LedgerError(f"{find[:60]!r} isn't in the document.")
        return f"Made {find[:60]!r} {', '.join(did)} in {_plural(n, 'place')}"

    def op_align(self, op: dict[str, Any]) -> str:
        code = _WORD_ALIGN.get(str(op.get("align") or "").lower())
        if code is None:
            raise LedgerError("align should be left, center, right or justify.")
        self._para(op).Alignment = code
        return f"Aligned paragraph {op.get('i')} {str(op.get('align')).lower()}"

    def op_replace_text(self, op: dict[str, Any]) -> str:
        find, new = str(op.get("find") or ""), str(op.get("replace") or "")
        if not find.strip():
            raise LedgerError("replace_text needs find: the words to look for.")
        n = str(self.doc.Content.Text or "").lower().count(find.lower())
        if not n:
            raise LedgerError(f"{find[:60]!r} isn't in the document.")
        # find, case, whole word, wildcards, sounds like, all forms, forward, wrap, format, with, all
        self.doc.Content.Find.Execute(
            find.replace("^", "^^"), False, False, False, False, False, True, 1, False,
            new.replace("^", "^^"), 2,
        )  # fmt: skip
        return f"Replaced {find[:60]!r} with {new[:60]!r} in {_plural(n, 'place')}"

    def op_add_table(self, op: dict[str, Any]) -> str:
        rows = op.get("rows")
        if isinstance(rows, list) and rows:
            grid = [list(r) if isinstance(r, list) else [r] for r in rows]
            n_rows, n_cols = len(grid), max(len(r) for r in grid)
        else:
            grid = []
            n_rows = int(_number(op.get("n_rows") or 0, "n_rows"))
            n_cols = int(_number(op.get("n_cols") or 0, "n_cols"))
        if not (1 <= n_rows <= 100 and 1 <= n_cols <= 20):
            raise LedgerError("add_table needs rows, or n_rows (up to 100) and n_cols (up to 20).")

        def write(at: int) -> None:
            table = self.doc.Tables.Add(self.doc.Paragraphs(at).Range, n_rows, n_cols)
            table.Borders.Enable = True
            for r, row in enumerate(grid, 1):
                for c, v in enumerate(row, 1):
                    if v not in (None, ""):
                        table.Cell(r, c).Range.Text = str(v)
            if grid:
                table.Rows(1).Range.Font.Bold = True

        self._insert(op, write)
        return f"Added a table of {_plural(n_rows, 'row')} by {_plural(n_cols, 'column')}"

    def op_page_setup(self, op: dict[str, Any]) -> str:
        todo: list[Any] = []
        did: list[str] = []
        if op.get("orientation"):
            way = str(op["orientation"]).lower()
            if way not in ("landscape", "portrait"):
                raise LedgerError("orientation should be landscape or portrait.")
            todo.append(lambda page: setattr(page, "Orientation", 1 if way == "landscape" else 0))
            did.append(f"turned the page {way}")
        if op.get("size"):
            paper = _PAPER.get(str(op["size"]).lower())
            if paper is None:
                raise LedgerError("size should be letter, a4 or legal.")
            todo.append(lambda page: setattr(page, "PaperSize", paper))
            did.append(f"set the paper to {str(op['size']).lower()}")
        sides = dict(op["margins"]) if isinstance(op.get("margins"), dict) else {}
        if op.get("margin") is not None:
            sides = {s: op["margin"] for s in ("top", "bottom", "left", "right")} | sides
        for side, inches in sides.items():
            if side not in ("top", "bottom", "left", "right"):
                raise LedgerError("margins are top, bottom, left and right.")
            points = _number(inches, "A margin") * 72
            if not 0 <= points <= 720:
                raise LedgerError("A margin is between 0 and 10 inches.")
            todo.append(lambda page, side=side, points=points: setattr(page, f"{side.title()}Margin", points))
        if sides:
            did.append("set the margins")
        if not todo:
            raise LedgerError("page_setup needs orientation, margin, margins or size.")
        page = self.doc.PageSetup
        for step in todo:
            step(page)
        said = " and ".join(did)
        return said[:1].upper() + said[1:]


# ------------------------------------------------------------------ PowerPoint
_LAYOUTS = {"title": 1, "title and body": 2, "section": 33, "two columns": 29, "title only": 11, "blank": 12}
_TITLE_KINDS = (1, 3)  # a title, a centred title
_PLACEHOLDER = 14


class DeckLive(Live):
    app = "powerpoint"
    kind = "pptx_live"
    menu = DECK_MENU

    def _document(self) -> Any:
        if _count(self.com.Presentations) == 0:
            return None
        try:
            return self.com.ActivePresentation
        except Exception:  # no window of it in front (a slide show, a minimised window)
            return self.com.Presentations(1)

    def _page(self) -> tuple[float, float]:
        setup = self.doc.PageSetup
        return float(setup.SlideWidth), float(setup.SlideHeight)

    def _read(self) -> str:
        w, h = self._page()
        n = _count(self.doc.Slides)
        out = [
            f"PowerPoint {self.name}, open in PowerPoint now: {_plural(n, 'slide')}, each "
            f"{w / 72:.2f} x {h / 72:.2f} inches."
        ]
        for i in range(1, min(n, 80) + 1):
            slide = self.doc.Slides(i)
            out.append(f"Slide {i}:")
            lines = []
            body_seen = False
            for shape in slide.Shapes:
                role = "thing"
                if int(shape.Type) == _PLACEHOLDER:
                    kind = int(shape.PlaceholderFormat.Type)
                    if kind in _TITLE_KINDS:
                        role = "title"
                    elif not body_seen and int(shape.HasTextFrame):
                        role, body_seen = ("subtitle" if kind == 4 else "body"), True
                text = ""
                if int(shape.HasTextFrame):
                    text = " / ".join(str(shape.TextFrame.TextRange.Text or "").split("\r"))[:200]
                lines.append(f"  {role} ({shape.Name}): {text!r}" if text else f"  {role} ({shape.Name})")
            out.extend(lines or ["  (nothing on it)"])
        if n > 80:
            out.append("  ...")
        return "\n".join(out)

    def _do(self, fn: Any, op: dict[str, Any]) -> str:
        self._wait(lambda: self.doc.Name)
        self.sent = True
        self.com.StartNewUndoEntry()  # this edit is its own step under Undo
        return fn(op)

    def _slide(self, op: dict[str, Any], key: str = "slide") -> Any:
        n = _count(self.doc.Slides)
        try:
            i = int(op.get(key))
        except (TypeError, ValueError):
            i = 0
        if not 1 <= i <= n:
            raise LedgerError(f"There's no slide {op.get(key)}; the deck has {n}.")
        return self.doc.Slides(i)

    def _box(self, slide: Any, which: Any) -> Any:
        which = str(which or "body").lower()
        if which not in ("title", "subtitle", "body"):
            raise LedgerError("which should be title, subtitle or body.")
        if which == "title":
            if not int(slide.Shapes.HasTitle):
                raise LedgerError("That slide has no title box.")
            return slide.Shapes.Title
        for shape in slide.Shapes:
            if (
                int(shape.Type) == _PLACEHOLDER
                and int(shape.PlaceholderFormat.Type) not in _TITLE_KINDS
                and int(shape.HasTextFrame)
            ):
                return shape
        raise LedgerError(f"That slide has no {which} box.")

    @staticmethod
    def _lines(items: Any) -> str:
        lines = [" ".join(str(x).split()) for x in (items or []) if str(x).strip()]
        if not lines:
            raise LedgerError("That needs the lines to put in.")
        return "\r".join(lines)

    def _place(self, op: dict[str, Any], w: float, h: float) -> tuple[float, float, float, float]:
        """Where a new thing goes, in points: where the edit said, or the middle of the slide."""
        page_w, page_h = self._page()
        w = _number(op["w"], "w") * 72 if op.get("w") is not None else w
        h = _number(op["h"], "h") * 72 if op.get("h") is not None else h
        x = _number(op["x"], "x") * 72 if op.get("x") is not None else (page_w - w) / 2
        y = _number(op["y"], "y") * 72 if op.get("y") is not None else (page_h - h) / 2
        if not (0 < w <= 4000 and 0 < h <= 4000 and -4000 <= x <= 4000 and -4000 <= y <= 4000):
            raise LedgerError("That position or size is off the slide.")
        return x, y, w, h

    # -- edits
    def op_add_slide(self, op: dict[str, Any]) -> str:
        n = _count(self.doc.Slides)
        bullets = op.get("bullets")
        name = str(op.get("layout") or "").lower()
        if name and name not in _LAYOUTS:
            raise LedgerError(f"layout should be one of {', '.join(_LAYOUTS)}.")
        layout = _LAYOUTS.get(name) or (2 if bullets else 1 if op.get("subtitle") or n == 0 else 2)
        at = n + 1
        if op.get("after") not in (None, ""):
            after = int(_number(op["after"], "after"))
            if not 0 <= after <= n:
                raise LedgerError(f"There's no slide {after}; the deck has {n}.")
            at = after + 1
        body = self._lines(bullets) if bullets else str(op.get("subtitle") or "")
        slide = self.doc.Slides.Add(at, layout)
        if op.get("title") and int(slide.Shapes.HasTitle):
            slide.Shapes.Title.TextFrame.TextRange.Text = str(op["title"])
        if body:
            self._box(slide, "body").TextFrame.TextRange.Text = body
        if op.get("notes"):
            self._notes(slide).Text = str(op["notes"])
        return f"Added slide {at}" + (f", {str(op['title'])[:60]!r}" if op.get("title") else "")

    def _notes(self, slide: Any) -> Any:
        return slide.NotesPage.Shapes.Placeholders(2).TextFrame.TextRange

    def op_set_title(self, op: dict[str, Any]) -> str:
        slide = self._slide(op)
        text = str(op.get("text") or "")
        self._box(slide, "title").TextFrame.TextRange.Text = text
        return f"Set slide {op.get('slide')}'s title to {text[:60]!r}"

    def op_set_text(self, op: dict[str, Any]) -> str:
        slide = self._slide(op)
        which = str(op.get("which") or "body").lower()
        text = self._lines(op["bullets"]) if op.get("bullets") else str(op.get("text") or "")
        self._box(slide, which).TextFrame.TextRange.Text = text
        return f"Set the {which} on slide {op.get('slide')}"

    def op_add_bullets(self, op: dict[str, Any]) -> str:
        slide = self._slide(op)
        lines = self._lines(op.get("items"))
        box = self._box(slide, "body").TextFrame.TextRange
        was = str(box.Text or "")
        box.Text = lines if op.get("replace") or not was.strip() else f"{was}\r{lines}"
        return f"Added {_plural(lines.count(chr(13)) + 1, 'bullet')} to slide {op.get('slide')}"

    def _font(self, op: dict[str, Any]) -> tuple[list[Any], list[str]]:
        todo: list[Any] = []
        did: list[str] = []
        for key, attr in (("bold", "Bold"), ("italic", "Italic"), ("underline", "Underline")):
            if key in op:
                on = bool(op[key])
                todo.append(lambda font, attr=attr, on=on: setattr(font, attr, -1 if on else 0))
                did.append(key if on else f"not {key}")
        if op.get("size") is not None:
            size = _number(op["size"], "size")
            if not 1 <= size <= 4000:
                raise LedgerError("A font size is between 1 and 4000.")
            todo.append(lambda font: setattr(font, "Size", size))
            did.append(f"size {size:g}")
        if op.get("color"):
            color = _bgr(op["color"])
            todo.append(lambda font: setattr(font.Color, "RGB", color))
            did.append(str(op["color"]))
        if op.get("font"):
            name = str(op["font"])[:60]
            todo.append(lambda font: setattr(font, "Name", name))
            did.append(name)
        return todo, did

    def op_add_text_box(self, op: dict[str, Any]) -> str:
        slide = self._slide(op)
        text = str(op.get("text") or "").strip()
        if not text:
            raise LedgerError("add_text_box needs text.")
        todo, _ = self._font(op)
        x, y, w, h = self._place(op, 6 * 72, 72)
        box = slide.Shapes.AddTextbox(1, x, y, w, h)  # 1: text running left to right
        box.TextFrame.TextRange.Text = text
        font = box.TextFrame.TextRange.Font
        for step in todo:
            step(font)
        return f"Added a text box to slide {op.get('slide')}"

    def op_add_image(self, op: dict[str, Any]) -> str:
        slide = self._slide(op)
        if not op.get("path"):
            raise LedgerError("add_image here needs path: a picture file on this PC.")
        path = Path(str(op["path"]).strip().strip('"')).expanduser()
        if path.suffix.lower() not in _IMAGES or not path.is_file():
            raise LedgerError(f"I can't find a picture at {path.name}.")
        sized = op.get("w") is not None or op.get("h") is not None
        x, y, w, h = self._place(op, 4 * 72, 3 * 72)
        # link to the file: no; keep it in the deck: yes; -1 for a size keeps the picture's own
        slide.Shapes.AddPicture(str(path), 0, -1, x, y, w if sized else -1, h if sized else -1)
        return f"Put {path.name} on slide {op.get('slide')}"

    def op_format_text(self, op: dict[str, Any]) -> str:
        slide = self._slide(op)
        which = str(op.get("which") or "body").lower()
        todo, did = self._font(op)
        if not todo:
            raise LedgerError("format_text needs something to change: bold, color, size...")
        font = self._box(slide, which).TextFrame.TextRange.Font
        for step in todo:
            step(font)
        return f"Made the {which} on slide {op.get('slide')} {', '.join(did)}"

    def op_move_slide(self, op: dict[str, Any]) -> str:
        slide = self._slide(op)
        n = _count(self.doc.Slides)
        to = int(_number(op.get("to"), "to"))
        if not 1 <= to <= n:
            raise LedgerError(f"There's no place {to}; the deck has {n} slides.")
        slide.MoveTo(to)
        return f"Moved slide {op.get('slide')} to place {to}"

    def op_duplicate_slide(self, op: dict[str, Any]) -> str:
        self._slide(op).Duplicate()
        return f"Made a copy of slide {op.get('slide')} after it"

    def op_set_notes(self, op: dict[str, Any]) -> str:
        self._notes(self._slide(op)).Text = str(op.get("text") or "")
        return f"Set the notes on slide {op.get('slide')}"
