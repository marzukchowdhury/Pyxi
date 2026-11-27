"""Reading a PDF form the way a person does: each page's printed words, line by line, with
every field marked where it sits.

Many real forms give their fields no names worth reading: every one is "untitled17", and
what a box is for is only printed beside it. So Pixel lays the page out:

    Monthly rent: $ [f31] Utilities included: [f32 box] Yes [f33 box] No

and works out, for each field, the words it belongs to (the words before a place to write,
or the heading over its column; the words after a box). What is typed in a field is kept
for the card shown on this PC and is never part of the outline the model sees.

What is done with the outline is in pixel_ask.py.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pyxi.agents.pixel_common import PixelError

# What a form tool calls a field nobody named: "untitled17", "Text3", "Check Box 12", "f1_02".
_JUNK_WORDS = frozenset(
    ["untitled", "undefined", "text", "textbox", "textfield", "field", "fill", "check", "checkbox", "box", "button", "radio", "radiobutton", "group", "form", "page", "combobox", "dropdown", "listbox", "p", "f", "t", "c", "cb", "tb"]
)
_PRIVATE_USE = re.compile(r"[\ue000-\uf8ff\x00-\x08\x0b-\x1f]")  # symbol-font glyphs
_LINES = re.compile(r"_{3,}|\.{6,}")
MAX_FIELDS = 600
MAX_VALUE = 2000
_PUSH, _READ_ONLY = 1 << 16, 1


@dataclass
class Field:
    id: str  # f1, f2... in reading order: what the model calls it
    name: str  # the PDF's own name for it
    kind: str  # "text" or "box"
    page: int
    rect: tuple[float, float, float, float]  # left, bottom, right, top, in points
    state: str = ""  # a box: the value that ticks it
    filled: bool = False
    label: str = ""  # the printed words it belongs to
    value: str = ""  # what is typed in it now: for the card on this PC, never for the model
    line: int = 0  # which line of its page it sits on
    before: str = ""  # the words just before it on its line
    above: str = ""  # the nearest words printed over it
    below: str = ""  # the words printed right under it
    mates: list[str] = field(default_factory=list)  # other places to write on its line


@dataclass
class Form:
    fields: list[Field] = field(default_factory=list)
    pages: list[str] = field(default_factory=list)  # each page as laid-out text

    def by_id(self) -> dict[str, Field]:
        return {f.id: f for f in self.fields}

    def labels(self) -> dict[str, str]:
        """The PDF's field name -> the words printed for it (a ticked box's name: its last)."""
        return {f.name: f.label for f in self.fields if f.label}

    def unnamed(self) -> bool:
        """Do most of its fields have a name that says nothing?"""
        names = {f.name for f in self.fields}
        return bool(names) and sum(1 for n in names if junk_name(n)) > len(names) / 2

    def outline(self) -> str:
        return "\n\n".join(f"Page {i}\n{text}" for i, text in enumerate(self.pages, 1))


def junk_name(name: str) -> bool:
    letters = re.sub(r"[^a-z]", "", name.rsplit(".", 1)[-1].lower())
    return letters in _JUNK_WORDS or letters == ""


def _clean(text: str) -> str:
    return " ".join(_LINES.sub("__", _PRIVATE_USE.sub(" ", text)).split())


def _widgets(reader: Any) -> list[Field]:
    out: list[Field] = []
    for number, page in enumerate(reader.pages):
        for ref in page.get("/Annots") or []:
            try:
                w = ref.get_object()
                if w.get("/Subtype") != "/Widget" or "/Rect" not in w:
                    continue
                parts: list[str] = []
                kind = flags = value = None
                node, hops = w, 0
                while node is not None and hops < 12:
                    if "/T" in node:
                        parts.append(str(node["/T"]))
                    kind = kind or node.get("/FT")
                    flags = flags if flags is not None else node.get("/Ff")
                    value = value if value is not None else node.get("/V")
                    parent = node.get("/Parent")
                    node, hops = (parent.get_object() if parent is not None else None), hops + 1
                flags = int(flags or 0)
                if not parts or kind not in ("/Tx", "/Btn", "/Ch") or flags & _READ_ONLY:
                    continue
                if kind == "/Btn" and flags & _PUSH:
                    continue  # a button to press, not a box to tick
                x0, y0, x1, y1 = (float(v) for v in w["/Rect"])
                rect = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
                state, filled = "", value not in (None, "", "/Off")
                if kind == "/Btn":
                    looks = w.get("/AP", {}).get("/N")
                    states = [str(k) for k in (looks.get_object() if looks is not None else {})]
                    state = next((s for s in states if s != "/Off"), "")
                    if not state:
                        continue
                    filled = str(w.get("/AS") or value or "/Off") == state
                out.append(
                    Field("", ".".join(reversed(parts)), "box" if kind == "/Btn" else "text",
                          number, rect, state, filled,
                          value="" if kind == "/Btn" or value is None else str(value)[:2000])
                )  # fmt: skip
            except Exception:  # one broken box is not the whole form
                continue
            if len(out) >= MAX_FIELDS:
                return out
    return out


def _words(path: Path, pages: int) -> list[list[tuple[float, float, float, float, str]]]:
    """Each page's printed words as (left, bottom, right, top, text) runs."""
    import pypdfium2 as pdfium

    out: list[list[tuple[float, float, float, float, str]]] = []
    doc = pdfium.PdfDocument(str(path))
    try:
        for i in range(min(pages, len(doc))):
            runs: list[tuple[float, float, float, float, str]] = []
            page = doc[i]
            text = page.get_textpage()
            try:
                for k in range(text.count_rects()):
                    left, bottom, right, top = text.get_rect(k)
                    said = _clean(text.get_text_bounded(left, bottom, right, top))
                    if said:
                        runs.append((left, bottom, right, top, said))
            finally:
                text.close()
                page.close()
            out.append(runs)
    finally:
        doc.close()
    return out


def _lay_out(runs: list[tuple[float, float, float, float, str]], fields: list[Field]) -> str:
    """One page as lines of text, top to bottom, each field marked where it sits; and each
    field's own label worked out on the way."""
    items: list[tuple[float, float, float, Any]] = []  # middle height, left, right, what
    for left, bottom, right, top, said in runs:
        items.append(((bottom + top) / 2, left, right, said))
    for f in fields:
        left, bottom, right, top = f.rect
        # a tall box (several lines to write in) reads from its first line
        middle = (bottom + top) / 2 if top - bottom <= 26 else top - 9
        items.append((middle, left, right, f))
    items.sort(key=lambda it: (-it[0], it[1]))
    lines: list[list[tuple[float, float, float, Any]]] = []
    for it in items:
        if lines and abs(lines[-1][0][0] - it[0]) <= 5.5:
            lines[-1].append(it)
        else:
            lines.append([it])
    shown: list[str] = []
    for n, line in enumerate(lines):
        line.sort(key=lambda it: it[1])
        parts: list[str] = []
        for at, (_, left, right, what) in enumerate(line):
            if isinstance(what, str):
                parts.append(what)
                continue
            before = line[at - 1][3] if at and isinstance(line[at - 1][3], str) else ""
            if not re.search(r"\w", before):
                before = ""  # a "$" or a rule before the box is not what it is for
            after = line[at + 1][3] if at + 1 < len(line) and isinstance(line[at + 1][3], str) else ""
            what.line = n
            what.before = before
            what.above = _above(lines[:n], left, right)
            what.below = _above(list(reversed(lines[n + 1 : n + 2])), left, right)
            if what.kind == "box":
                what.label = after or before  # a box's words follow it
            else:
                what.label = before or what.above or what.below or after
                what.mates = [
                    o[3].id for o in line if not isinstance(o[3], str) and o[3] is not what and o[3].kind == "text"
                ]
            mark = what.id + (" box" if what.kind == "box" else "") + (" filled" if what.filled else "")
            parts.append(f"[{mark}]")
        shown.append(" ".join(parts))
    return "\n".join(shown)


def _above(lines: list[list[tuple[float, float, float, Any]]], left: float, right: float) -> str:
    """The nearest words printed over that stretch of the page: a column's heading."""
    for line in reversed(lines[-12:]):
        # every run of that line that reaches over the stretch: one heading is often several
        # runs ("(Co", "-", "Applicant. Please print)")
        over = [(a, what) for _, a, b, what in line if isinstance(what, str) and a < right and b > left]
        if over:
            return " ".join(what for _, what in sorted(over))
    return ""


def read(path: Path, reader: Any = None) -> Form:
    """The form's fields in reading order, each with the words printed for it, and its pages
    laid out as text. A PDF with no fields gives an empty form."""
    if reader is None:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
    fields = _widgets(reader)
    if not fields:
        return Form()
    try:
        words = _words(path, len(reader.pages))
    except Exception:  # the fields are still there; the page just can't be read as text
        words = []
    fields.sort(key=lambda f: (f.page, -round(f.rect[3] / 6), f.rect[0]))
    for i, f in enumerate(fields, 1):
        f.id = f"f{i}"
    pages = []
    for number in range(len(reader.pages)):
        on_page = [f for f in fields if f.page == number]
        runs = words[number] if number < len(words) else []
        pages.append(_lay_out(runs, on_page) if runs or on_page else "")
    return Form(fields, pages)


def check(path: Path) -> Form:
    """The form in a PDF, or the plain reason it has none."""
    form = read(path)
    if not form.fields:
        raise PixelError(f"{path.name} has no fillable fields. Scout can fill a form on a website instead.")
    return form
