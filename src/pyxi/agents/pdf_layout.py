"""Reading a PDF the way a person does: columns in order, tables as tables.

A PDF holds letters at positions, in whatever order the program that made it wrote them. Read
in that order (what pypdf's plain text gives), a table comes out one cell to a line, a page
whose two columns were written a row at a time comes out with the columns shuffled into each
other, and the running header turns up in the middle of a sentence on every page.

The big document readers (Docling, Marker) solve this with layout and table models that need
PyTorch and hundreds of megabytes. This is the small version of the same steps, on what Pyxi
already ships (PDFium for the letters and the drawn lines, pypdf for form fields), with no
model:

1. Letters to words to rows, by where they are on the page.
2. Ruled tables from the lines drawn on the page: the lines make a grid, the grid's boxes are
   the cells, and each word goes in the box it sits in.
3. Columns: a gutter is a strip of the page that almost no row of text crosses, with running
   text on at least one side of it. What does cross it (a title, a wide table) cuts the page
   into bands; each band is read left column first.
4. Tables with no lines: a run of rows that share the same gaps between their columns.
5. Across pages: a line that repeats at the top or bottom of the pages is a header or footer
   and is dropped; a table that ends a page and one of the same shape that starts the next are
   one table; a paragraph cut by the page break is joined.

A scanned page has no letters, so its words come from OCR (with their boxes) and go through
the same steps. A filled-in form's answers are not page text either: they are read from the
form fields and put where the boxes are.

Where this falls short of the model-based readers is said in :data:`LIMITS`.
"""

from __future__ import annotations

import ctypes
import re
import statistics
import threading
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path
from typing import Any

LIMITS = (
    "Tables with no ruling lines and cells that span columns, tables whose columns sit closer "
    "than a letter's height, text set in three or more ragged columns, rotated pages, formulas, "
    "and poor scans are where a model-based reader still does better."
)

MAX_PAGES = 2000  # pages read from one PDF
MAX_CHARS_PAGE = 60000  # a page with more letters than this is read plainly
MAX_LINES_PAGE = 4000  # drawn lines looked at on one page
OCR_DPI = 200

_lock = threading.RLock()  # PDFium is not safe to use from two threads at once


# ---------------------------------------------------------------------------- what comes out
@dataclass
class Para:
    text: str
    kind: str = "p"  # p, h1, h2, bullet, number
    page: int = 1
    size: float = 11.0
    bold: bool = False


@dataclass
class Table:
    rows: list[list[str]]
    page: int = 1
    pages: list[int] = field(default_factory=list)
    ruled: bool = False
    xs: list[float] = field(default_factory=list)  # where its columns start, to match a continuation

    @property
    def cols(self) -> int:
        return max((len(r) for r in self.rows), default=0)

    def markdown(self) -> str:
        if not self.rows:
            return ""
        n = self.cols

        def line(row: list[str]) -> str:
            cells = [_one_line(c).replace("|", "\\|") for c in row] + [""] * (n - len(row))
            return "| " + " | ".join(cells) + " |"

        out = [line(self.rows[0]), "| " + " | ".join(["---"] * n) + " |"]
        out += [line(r) for r in self.rows[1:]]
        return "\n".join(out)


def _one_line(text: Any) -> str:
    return " ".join(str(text if text is not None else "").split())


@dataclass
class Layout:
    blocks: list[Para | Table] = field(default_factory=list)
    pages: int = 0
    scanned: list[int] = field(default_factory=list)  # pages read with OCR
    plain: list[int] = field(default_factory=list)  # pages read the old plain way
    dropped: list[str] = field(default_factory=list)  # headers and footers left out
    no_reader: str = ""  # why a scan could not be read, when it could not

    @property
    def tables(self) -> list[Table]:
        return [b for b in self.blocks if isinstance(b, Table)]

    @property
    def words(self) -> int:
        n = 0
        for b in self.blocks:
            if isinstance(b, Table):
                n += sum(len(c.split()) for r in b.rows for c in r)
            else:
                n += len(b.text.split())
        return n

    def markdown(self, *, marks: bool = True) -> str:
        return "\n\n".join(_block_text(b, marks) for b in self.blocks).strip()

    def page_texts(self, *, marks: bool = False) -> list[str]:
        """The text of each page (a table that runs over pages is on the page it starts on)."""
        out: list[list[str]] = [[] for _ in range(self.pages)]
        for b in self.blocks:
            if 1 <= b.page <= self.pages:
                out[b.page - 1].append(_block_text(b, marks))
        return ["\n\n".join(p).strip() for p in out]


def _block_text(b: Para | Table, marks: bool) -> str:
    if isinstance(b, Table):
        return b.markdown()
    if b.kind == "bullet":
        return f"- {b.text}"
    if b.kind == "number":
        return b.text
    if marks and b.kind in ("h1", "h2"):
        return ("# " if b.kind == "h1" else "## ") + b.text
    return b.text


# ---------------------------------------------------------------------------- words on a page
@dataclass
class Word:
    x0: float
    y0: float  # top, measured down from the top of the page
    x1: float
    y1: float
    text: str
    size: float = 10.0
    bold: bool = False

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def n(self) -> int:
        return max(1, len(self.text.split()))


@dataclass
class Seg:
    """A run of words on one row with no wide gap in it, or a ruled table standing as one."""

    x0: float
    y0: float
    x1: float
    y1: float
    words: list[Word] = field(default_factory=list)
    rid: int = 0
    table: Table | None = None

    @property
    def n(self) -> int:
        return sum(w.n for w in self.words)


_RTL = re.compile(r"[֐-ࣿיִ-﷿ﹰ-ﻼ]")
_SPACES = {0x20, 0xA0, 0x09, 0x0A, 0x0D, 0x3000}


def _is_rtl(text: str) -> bool:
    if text.isascii():
        return False
    letters = [c for c in text if c.isalpha()]
    return bool(letters) and sum(1 for c in letters if _RTL.match(c)) * 2 > len(letters)


def _page_words(textpage: Any, height: float) -> list[Word] | None:
    """The words of a page with their boxes, or None when the page has too many letters (or
    its text runs at an angle) to lay out."""
    import pypdfium2.raw as raw

    tp = textpage.raw
    n = raw.FPDFText_CountChars(tp)
    if n <= 0:
        return []
    if n > MAX_CHARS_PAGE:
        return None
    left, right, bottom, top = (ctypes.c_double() for _ in range(4))
    refs = tuple(ctypes.byref(v) for v in (left, right, bottom, top))
    words: list[Word] = []
    cur: Word | None = None
    chars: list[str] = []
    turned = 0

    def flush() -> None:
        nonlocal cur
        if cur is not None and chars:
            cur.text = "".join(chars)
            words.append(cur)
        cur = None
        chars.clear()

    for i in range(n):
        code = raw.FPDFText_GetUnicode(tp, i)
        if code in _SPACES or code in (0, 0xFFFE, 0xFFFF):
            flush()
            continue
        if code == 2:  # PDFium's mark for a hyphen at a line's end
            flush()
            continue
        if not raw.FPDFText_GetCharBox(tp, i, *refs):
            continue
        x0, x1, yb, yt = left.value, right.value, bottom.value, top.value
        if x1 - x0 <= 0 and yt - yb <= 0:
            flush()
            continue
        y0, y1 = height - yt, height - yb
        ch = chr(code)
        if cur is not None:
            h = max(cur.y1 - cur.y0, y1 - y0, 1.0)
            same_line = abs((y0 + y1) / 2 - cur.cy) < 0.6 * h
            gap_ltr = x0 - cur.x1
            gap_rtl = cur.x0 - x1
            near = 0.3 * max(cur.size, 4.0)
            joins = same_line and (
                -0.6 * cur.size <= gap_ltr <= near
                or (-0.6 * cur.size <= gap_rtl <= near and _RTL.match(ch))
            )
            if not joins:
                flush()
        if cur is None:
            size = float(raw.FPDFText_GetFontSize(tp, i) or 0.0)
            if size <= 1.0:  # some files give the size as 1 and scale the page instead
                size = max(y1 - y0, 4.0)
            if abs(float(raw.FPDFText_GetCharAngle(tp, i) or 0.0)) > 0.2:
                turned += 1
            weight = raw.FPDFText_GetFontWeight(tp, i)
            cur = Word(x0, y0, x1, y1, "", size, weight >= 600 or _bold_font(raw, tp, i))
        else:
            cur.x0, cur.x1 = min(cur.x0, x0), max(cur.x1, x1)
            cur.y0, cur.y1 = min(cur.y0, y0), max(cur.y1, y1)
        chars.append(ch)
    flush()
    if words and turned * 2 > len(words):
        return None  # a page set sideways: its rows are not rows
    return words


def _bold_font(raw: Any, tp: Any, i: int) -> bool:
    buf = ctypes.create_string_buffer(128)
    flags = ctypes.c_int()
    try:
        got = raw.FPDFText_GetFontInfo(tp, i, buf, 128, ctypes.byref(flags))
    except Exception:
        return False
    if not got:
        return False
    name = buf.value.decode("utf-8", "replace").lower()
    return "bold" in name or "black" in name or "heavy" in name


def _rows(words: list[Word]) -> list[list[Word]]:
    """Words on the same line of the page, left to right."""
    rows: list[list[Word]] = []
    mid = 0.0
    for w in sorted(words, key=lambda w: (w.cy, w.x0)):
        h = max(w.y1 - w.y0, 1.0)
        if rows and abs(w.cy - mid) <= 0.35 * h:
            rows[-1].append(w)
            mid += (w.cy - mid) / len(rows[-1])
        else:
            rows.append([w])
            mid = w.cy
    for r in rows:
        r.sort(key=lambda w: w.x0)
    return rows


def _segments(row: list[Word], rid: int) -> list[Seg]:
    out: list[Seg] = []
    for w in row:
        last = out[-1] if out else None
        wide = max(0.95 * max(w.size, last.words[-1].size if last else 0), 6.0)
        if last is not None and w.x0 - last.x1 <= wide:
            last.words.append(w)
            last.x1 = max(last.x1, w.x1)
            last.y0, last.y1 = min(last.y0, w.y0), max(last.y1, w.y1)
        else:
            out.append(Seg(w.x0, w.y0, w.x1, w.y1, [w], rid))
    return out


def _row_text(words: list[Word]) -> str:
    words = sorted(words, key=lambda w: w.x0)
    text = " ".join(w.text for w in words)
    if _is_rtl(text):
        text = " ".join(w.text for w in reversed(words))
    return text


# ---------------------------------------------------------------------------- ruled tables
def _drawn_lines(page: Any, height: float) -> tuple[list[tuple[float, float, float]], list[tuple[float, float, float]]]:
    """The straight lines drawn on a page: horizontal ones as (y, x0, x1) and vertical ones as
    (x, y0, y1), y measured down from the top. A thin filled box is a line too, and a box
    drawn with a border gives its four sides."""
    import pypdfium2.raw as raw

    hs: list[tuple[float, float, float]] = []
    vs: list[tuple[float, float, float]] = []

    def add(x0: float, y0: float, x1: float, y1: float) -> None:
        x0, x1 = min(x0, x1), max(x0, x1)
        y0, y1 = min(y0, y1), max(y0, y1)
        if y1 - y0 <= 2.5 and x1 - x0 >= 8:
            hs.append(((y0 + y1) / 2, x0, x1))
        elif x1 - x0 <= 2.5 and y1 - y0 >= 6:
            vs.append(((x0 + x1) / 2, y0, y1))

    count = 0
    for obj in page.get_objects(filter=[raw.FPDF_PAGEOBJ_PATH], max_depth=2):
        count += 1
        if count > MAX_LINES_PAGE:
            break
        try:
            left, b, r, t = obj.get_bounds()
        except Exception:
            continue
        x0, x1, y0, y1 = left, r, height - t, height - b
        if (y1 - y0 <= 2.5) != (x1 - x0 <= 2.5):
            add(x0, y0, x1, y1)  # a line, or a box as thin as one
            continue
        if y1 - y0 <= 2.5:
            continue  # a dot
        fill, stroke = ctypes.c_int(), ctypes.c_int()
        try:
            raw.FPDFPath_GetDrawMode(obj.raw, ctypes.byref(fill), ctypes.byref(stroke))
        except Exception:
            continue
        if not stroke.value:
            continue  # shading behind a row: not a border
        segs = raw.FPDFPath_CountSegments(obj.raw)
        if segs <= 0 or segs > 400:
            continue
        try:
            m = obj.get_matrix()
            a, b_, c, d, e, f = m.a, m.b, m.c, m.d, m.e, m.f
        except Exception:
            a, b_, c, d, e, f = 1.0, 0.0, 0.0, 1.0, 0.0, 0.0
        px, py = ctypes.c_float(), ctypes.c_float()
        prev: tuple[float, float] | None = None
        start: tuple[float, float] | None = None
        for k in range(segs):
            seg = raw.FPDFPath_GetPathSegment(obj.raw, k)
            if not raw.FPDFPathSegment_GetPoint(seg, ctypes.byref(px), ctypes.byref(py)):
                prev = None
                continue
            x = a * px.value + c * py.value + e
            y = height - (b_ * px.value + d * py.value + f)
            kind = raw.FPDFPathSegment_GetType(seg)
            if kind == raw.FPDF_SEGMENT_MOVETO:
                start = (x, y)
            elif kind == raw.FPDF_SEGMENT_LINETO and prev is not None:
                add(prev[0], prev[1], x, y)
            if kind == raw.FPDF_SEGMENT_BEZIERTO:
                prev = None
                continue
            if raw.FPDFPathSegment_GetClose(seg) and start is not None:
                add(x, y, start[0], start[1])
            prev = (x, y)
    return hs, vs


def _merge_lines(lines: list[tuple[float, float, float]], tol: float = 2.0) -> list[tuple[float, float, float]]:
    """Lines on the same level that touch or overlap, as one line each."""
    out: list[tuple[float, float, float]] = []
    for at, a, b in sorted(lines):
        joined = False
        for i in range(len(out) - 1, max(-1, len(out) - 400), -1):
            at2, a2, b2 = out[i]
            if at - at2 > tol:
                break
            if a <= b2 + tol and b >= a2 - tol:
                out[i] = (at2, min(a, a2), max(b, b2))
                joined = True
                break
        if not joined:
            out.append((at, a, b))
    return out


def _levels(values: list[float], tol: float = 2.5) -> list[float]:
    out: list[list[float]] = []
    for v in sorted(values):
        if out and v - out[-1][-1] <= tol:
            out[-1].append(v)
        else:
            out.append([v])
    return [sum(g) / len(g) for g in out]


def _ruled_tables(
    hs: list[tuple[float, float, float]], vs: list[tuple[float, float, float]]
) -> list[tuple[list[float], list[float], list[tuple[float, float, float]], list[tuple[float, float, float]]]]:
    """Grids made by the drawn lines: (column edges, row edges, its horizontal lines, its
    vertical lines) for each."""
    hs, vs = _merge_lines(hs), _merge_lines(vs)
    if len(hs) < 2 or len(vs) < 2 or len(hs) * len(vs) > 400000:
        return []
    tol = 2.5
    parent = list(range(len(hs) + len(vs)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, (y, x0, x1) in enumerate(hs):
        for j, (x, y0, y1) in enumerate(vs):
            if x0 - tol <= x <= x1 + tol and y0 - tol <= y <= y1 + tol:
                parent[find(i)] = find(len(hs) + j)
    groups: dict[int, tuple[list[int], list[int]]] = {}
    for i in range(len(hs)):
        groups.setdefault(find(i), ([], []))[0].append(i)
    for j in range(len(vs)):
        groups.setdefault(find(len(hs) + j), ([], []))[1].append(j)
    out = []
    for hi, vi in groups.values():
        if len(hi) < 2 or len(vi) < 2:
            continue
        gh, gv = [hs[i] for i in hi], [vs[j] for j in vi]
        xs = _levels([x for x, _, _ in gv])
        ys = _levels([y for y, _, _ in gh])
        # Rows ruled across with no line down the sides: the ends of the rules are the sides.
        lo, hi_x = min(x0 for _, x0, _ in gh), max(x1 for _, _, x1 in gh)
        if lo < xs[0] - 6:
            xs.insert(0, lo)
        if hi_x > xs[-1] + 6:
            xs.append(hi_x)
        top, bot = min(y0 for _, y0, _ in gv), max(y1 for _, _, y1 in gv)
        if top < ys[0] - 6:
            ys.insert(0, top)
        if bot > ys[-1] + 6:
            ys.append(bot)
        xs = [x for k, x in enumerate(xs) if k == 0 or x - xs[k - 1] >= 3]
        ys = [y for k, y in enumerate(ys) if k == 0 or y - ys[k - 1] >= 3]
        if len(xs) < 3 or len(ys) < 3:
            continue  # one row or one column: a box round some text, not a table
        out.append((xs, ys, gh, gv))
    return out


def _fill_grid(
    xs: list[float], ys: list[float], gv: list[tuple[float, float, float]], words: list[Word]
) -> tuple[list[list[str]], list[Word]]:
    """The words that sit in a grid, cell by cell, and the words that were used."""
    rows, cols = len(ys) - 1, len(xs) - 1
    cells: list[list[list[Word]]] = [[[] for _ in range(cols)] for _ in range(rows)]
    used: list[Word] = []
    for w in words:
        if not (xs[0] - 1 <= w.cx <= xs[-1] + 1 and ys[0] - 1 <= w.cy <= ys[-1] + 1):
            continue
        r = max(0, min(rows - 1, sum(1 for y in ys[1:-1] if w.cy > y)))
        c = max(0, min(cols - 1, sum(1 for x in xs[1:-1] if w.cx > x)))
        # No line between this cell and the one to its left: they are one cell, and its words
        # belong to where it starts.
        while c > 0 and not any(
            abs(x - xs[c]) <= 2.5 and min(y1, ys[r + 1]) - max(y0, ys[r]) >= 0.5 * (ys[r + 1] - ys[r])
            for x, y0, y1 in gv
        ):
            c -= 1
        cells[r][c].append(w)
        used.append(w)
    table = [[_cell_text(c) for c in row] for row in cells]
    return table, used


def _cell_text(words: list[Word]) -> str:
    return " ".join(_row_text(r) for r in _rows(words))


# ---------------------------------------------------------------------------- columns
MIN_GUTTER = 5.0


def _by_row(segs: list[Seg]) -> list[list[Seg]]:
    rows: dict[int, list[Seg]] = {}
    for s in segs:
        rows.setdefault(s.rid, []).append(s)
    out = sorted(rows.values(), key=lambda r: (min(s.y0 for s in r), min(s.x0 for s in r)))
    for r in out:
        r.sort(key=lambda s: s.x0)
    return out


def _texty(segs: list[Seg], fill: float) -> bool:
    """Is this side of a gutter running text: lines of several words that fill their column?"""
    segs = [s for s in segs if s.table is None]
    if len(segs) < 4:
        return False
    width = max(s.x1 - s.x0 for s in segs) or 1.0
    return (
        statistics.median(s.n for s in segs) >= 4
        and statistics.median((s.x1 - s.x0) / width for s in segs) >= fill
    )


def _gutter(rows: list[list[Seg]]) -> float | None:
    """Where this part of the page splits into a left and a right column, if it does."""
    if len(rows) < 4:
        return None
    x_lo = min(s.x0 for r in rows for s in r)
    x_hi = max(s.x1 for r in rows for s in r)
    step = 2.0
    bins = int((x_hi - x_lo) / step)
    if bins < 8:
        return None
    cross = [0] * (bins + 1)
    for r in rows:
        hit = set()
        for s in r:
            a, b = int((s.x0 - x_lo) / step) + 1, int((s.x1 - x_lo) / step)
            hit.update(range(max(a, 0), min(b, bins) + 1))
        for k in hit:
            cross[k] += 1
    most = max(1, int(0.25 * len(rows)))
    best: tuple[tuple[int, int, float], float] | None = None
    k = 1
    while k < bins:
        if cross[k] > most:
            k += 1
            continue
        j = k
        while j + 1 < bins and cross[j + 1] <= most:
            j += 1
        low = min(cross[k : j + 1])
        run = [i for i in range(k, j + 1) if cross[i] == low]
        # the longest stretch of the emptiest bins
        runs: list[list[int]] = []
        for i in run:
            if runs and i == runs[-1][-1] + 1:
                runs[-1].append(i)
            else:
                runs.append([i])
        stretch = max(runs, key=len)
        width = len(stretch) * step
        x = x_lo + (stretch[0] + stretch[-1]) / 2 * step
        k = j + 1
        if width < MIN_GUTTER:
            continue
        free = [r for r in rows if not any(s.x0 < x < s.x1 for s in r)]
        left = [s for r in free for s in r if s.x1 <= x]
        right = [s for r in free for s in r if s.x0 >= x]
        if len({s.rid for s in left}) < 3 or len({s.rid for s in right}) < 3:
            continue
        both = _texty(left, 0.6) and _texty(right, 0.6)
        one = _texty(left, 0.75) or _texty(right, 0.75)
        if not (both or one):
            continue  # short pieces on both sides: the gap between two columns of a table
        score = (2 if both else 1, -low, width)
        if best is None or score > best[0]:
            best = (score, x)
    return best[1] if best else None


def _order(segs: list[Seg], depth: int = 0) -> list[list[Seg]]:
    """This part of the page as single columns, in the order they are read."""
    if not segs:
        return []
    rows = _by_row(segs)
    x = _gutter(rows) if depth < 6 else None
    if x is None:
        return [segs]
    out: list[list[Seg]] = []
    band: list[Seg] = []
    wide: list[Seg] = []

    def flush_band() -> None:
        if band:
            out.extend(_order([s for s in band if s.x1 <= x], depth + 1))
            out.extend(_order([s for s in band if s.x0 >= x], depth + 1))
            band.clear()

    def flush_wide() -> None:
        if wide:
            out.extend(_order(list(wide), depth + 1) if len(wide) < len(segs) else [list(wide)])
            wide.clear()

    crosses = [any(s.x0 < x < s.x1 for s in r) for r in rows]
    under = _under_both(rows, crosses, x)
    for k, r in enumerate(rows):
        if crosses[k] or k in under:
            flush_band()
            wide.extend(r)
        else:
            flush_wide()
            band.extend(r)
    flush_band()
    flush_wide()
    return out


def _under_both(rows: list[list[Seg]], crosses: list[bool], x: float) -> set[int]:
    """Rows that sit under both columns though they don't reach across: a short line after a
    clear gap, below the last line of the right column and just above something that does run
    across. A left column that is simply longer than the right one is not this: its lines keep
    the spacing they had."""
    out: set[int] = set()
    k = 0
    while k < len(rows):
        if crosses[k]:
            k += 1
            continue
        j = k
        while j + 1 < len(rows) and not crosses[j + 1]:
            j += 1
        band = range(k, j + 1)
        k = j + 1
        if j + 1 >= len(rows):
            continue  # nothing runs across below it
        right = [i for i in band if any(s.x0 >= x for s in rows[i])]
        if not right or right[-1] == j:
            continue
        left = [i for i in band if any(s.x1 <= x for s in rows[i])]
        tops = {i: min(s.y0 for s in rows[i]) for i in left}
        seen = 0.0
        for a, b in pairwise(left):
            gap = tops[b] - tops[a]
            if b > right[-1] and seen and gap > 1.3 * seen:
                out.update(i for i in left if i >= b)
                break
            seen = max(seen, gap)
    return out


# ---------------------------------------------------------------------------- tables with no lines
_LIST_MARK = re.compile(r"^(?:[•●▪‣⁃∙·◦*\-–]|\(?\d{1,3}[.)]|\(?[a-zA-Z][.)])$")
_NUMBERISH = re.compile(r"^[\s$€£¥(+-]*\d[\d,.\s]*[%)]?$")


def _separators(rows: list[list[Seg]]) -> list[float]:
    """The gaps every one of these rows leaves free: where the columns of a table part."""
    words = [w for r in rows for s in r for w in s.words]
    if not words:
        return []
    size = statistics.median(w.size for w in words)
    need = max(0.5 * size, 4.0)
    x_lo, x_hi = min(w.x0 for w in words), max(w.x1 for w in words)
    spans = sorted((w.x0, w.x1) for w in words)
    seps: list[float] = []
    edge = spans[0][1]
    for a, b in spans[1:]:
        if a - edge >= need:
            seps.append((edge + a) / 2)
        edge = max(edge, b)
    return [x for x in seps if x_lo < x < x_hi]


def _stream_tables(rows: list[list[Seg]], sure: bool = False) -> list[tuple[int, int, list[float]]]:
    """Runs of rows that are a table with no ruling lines: (first row, one past the last, the
    places its columns part). ``sure`` keeps only what can be nothing else, whatever columns
    the page has: three columns or more of short cells."""
    out: list[tuple[int, int, list[float]]] = []
    i, n = 0, len(rows)
    while i < n:
        if len(rows[i]) < 2 or any(s.table is not None for s in rows[i]):
            i += 1
            continue
        j = i
        last_multi = i
        while j + 1 < n:
            nxt = rows[j + 1]
            if any(s.table is not None for s in nxt):
                break
            if not _gap_ok(rows, i, j + 1, 1.8):
                break  # a clear space: whatever is under it is not this table
            if len(nxt) >= 2:
                j += 1
                last_multi = j
            elif j + 2 < n and len(rows[j + 2]) >= 2 and _gap_ok(rows, i, j + 1):
                j += 1  # one cell on its own row, with the table going on under it
            else:
                break
        j = last_multi
        found = _table_in(rows, i, j + 1)
        if found is not None and sure and not _surely(rows, *found):
            found = None
        if found is None:
            i += 1
            continue
        a, b, seps = found
        # The last cell of the table wrapped on to a line of its own: that line is the table's.
        tops = [min(s.y0 for s in r) for r in rows[a:b]]
        pitch = [q - p for p, q in pairwise(tops)]
        usual = statistics.median(pitch) if pitch else 0.0
        while b < n and len(rows[b]) == 1 and rows[b][0].table is None and usual:
            cols = {sum(1 for x in seps if w.cx > x) for w in rows[b][0].words}
            near = min(s.y0 for s in rows[b]) - min(s.y0 for s in rows[b - 1]) < 0.8 * usual
            if len(cols) == 1 and min(cols) > 0 and near:
                b += 1
            else:
                break
        out.append((a, b, seps))
        i = b
    return out


def _surely(rows: list[list[Seg]], a: int, b: int, seps: list[float]) -> bool:
    if len(seps) < 2:
        return False
    cells = [_cells(r, seps) for r in rows[a:b]]
    for c in range(len(seps) + 1):
        col = [len(row[c].split()) for row in cells if row[c]]
        if col and statistics.median(col) > 5:
            return False  # a column of running text: maybe the page's own column beside a table
    every = [len(c.split()) for row in cells for c in row if c]
    return bool(every) and statistics.median(every) <= 3


def _gap_ok(rows: list[list[Seg]], first: int, k: int, slack: float = 1.4) -> bool:
    """Is row ``k`` as close to the row above it as the rows of this table are to each other?"""
    first_seg = rows[k][0]
    h = max(first_seg.y1 - first_seg.y0, first_seg.words[0].size if first_seg.words else 0.0, 1.0)
    gap = rows[k][0].y0 - rows[k - 1][0].y0
    pitch = [rows[i + 1][0].y0 - rows[i][0].y0 for i in range(first, k - 1)]
    if pitch:
        return gap < slack * max(pitch)
    return gap < 3.2 * h * slack / 1.4


def _table_in(rows: list[list[Seg]], a: int, b: int) -> tuple[int, int, list[float]] | None:
    while b - a >= 2:
        multi = [r for r in rows[a:b] if len(r) >= 2]
        if len(multi) < 3 and not (len(multi) == 2 and min(len(r) for r in multi) >= 3):
            return None
        seps = _separators(multi)
        if not seps:
            return None
        # A row that runs across the gaps is not of this table: the table ends before it.
        cut = next(
            (
                k
                for k in range(a, b)
                if len(rows[k]) < 2 and any(s.x0 < x < s.x1 for s in rows[k] for x in seps)
            ),
            None,
        )
        if cut is not None:
            if cut - a >= 2:
                b = cut
                continue
            a = cut + 1
            continue
        cols = len(seps) + 1
        cells = [_cells(r, seps) for r in rows[a:b]]
        filled = statistics.median(sum(1 for c in row if c) for row in cells)
        if filled < max(2, 0.5 * cols) and cols > 2:
            return None
        if cols == 2:
            first = [row[0] for row in cells if row[0]]
            second = [row[1] for row in cells if row[1]]
            if first and all(_LIST_MARK.match(t) for t in first):
                return None  # a list with its numbers or bullets set apart
            if second and statistics.median(len(t.split()) for t in second) >= 9:
                return None  # labels beside running text
            if len(multi) < 3:
                return None
        return a, b, seps
    return None


def _cells(row: list[Seg], seps: list[float]) -> list[str]:
    cols: list[list[Word]] = [[] for _ in range(len(seps) + 1)]
    for s in row:
        for w in s.words:
            cols[sum(1 for x in seps if w.cx > x)].append(w)
    return [_row_text(c) for c in cols]


def _build_stream(rows: list[list[Seg]], seps: list[float]) -> list[list[str]]:
    cells = [_cells(r, seps) for r in rows]
    tops = [min(s.y0 for s in r) for r in rows]
    pitch = [b - a for a, b in pairwise(tops)]
    usual = statistics.median(pitch) if pitch else 0.0
    out: list[list[str]] = []
    for k, row in enumerate(cells):
        carried = (
            out
            and k > 0
            and not row[0]
            and usual
            and pitch[k - 1] < 0.8 * usual
            and not any(_NUMBERISH.match(c) for c in row if c)
        )
        if carried:  # the second line of a cell that wrapped
            out[-1] = [f"{a} {b}".strip() for a, b in zip(out[-1], row, strict=False)]
        else:
            out.append(row)
    return out


# ---------------------------------------------------------------------------- paragraphs
_BULLET = re.compile(r"^[•●▪‣⁃∙·◦*-]\s+")
_NUMBER = re.compile(r"^(\d{1,3}|[a-zA-Z])[.)]\s+")


@dataclass
class _Line:
    text: str
    size: float
    bold: bool
    y0: float
    y1: float
    x0: float
    x1: float


def _paragraphs(lines: list[_Line], body: float, page: int) -> list[Para]:
    """Lines of one column joined into paragraphs: a bigger gap than the usual one between
    lines ends a paragraph; larger or short bold lines are headings; marks make lists."""
    if not lines:
        return []
    gaps = sorted(b.y0 - a.y0 for a, b in pairwise(lines) if 0 < b.y0 - a.y0 < body * 4)
    step = gaps[len(gaps) // 3] if gaps else body * 1.25
    out: list[Para] = []
    para: list[str] = []
    kind = "p"
    prev: _Line | None = None

    bolds: list[bool] = []

    def flush() -> None:
        nonlocal para, kind
        if para:
            out.append(Para(" ".join(para), kind, page, body, bool(bolds) and all(bolds)))
        para, kind = [], "p"
        bolds.clear()

    for ln in lines:
        gap = ln.y0 - prev.y0 if prev is not None else 0.0
        heading = ln.size >= body * 1.2 or (
            ln.bold and len(ln.text) < 70 and not ln.text.endswith((".", ","))
        )
        mark = "bullet" if _BULLET.match(ln.text) else "number" if _NUMBER.match(ln.text) else ""
        if para and (heading or mark or gap > step * 1.25 or gap < 0):
            flush()
        prev = ln
        if heading:
            flush()
            level = "h1" if ln.size >= body * 1.5 else "h2"
            if out and out[-1].kind == level and out[-1].page == page and 0 < gap <= ln.size * 1.6:
                out[-1].text += " " + ln.text  # a heading on two lines
            else:
                out.append(Para(ln.text, level, page, ln.size, ln.bold))
            continue
        bolds.append(ln.bold)
        if mark == "bullet":
            kind = "bullet"
            para.append(_BULLET.sub("", ln.text, count=1))
        elif mark == "number":
            kind = "number"
            para.append(ln.text)
        else:
            para.append(ln.text)
    flush()
    return out


# ---------------------------------------------------------------------------- headers and footers
_PAGE_NO = re.compile(r"(?i)^\W*(page\s*)?\d{1,4}(\s*(of|/)\s*\d{1,4})?\W*$")
_PAGE_WORD = re.compile(r"(?i)^\W*page\s*\d{1,4}(\s*(of|/)\s*\d{1,4})?\W*$")


def _margin_key(row: list[Word], height: float) -> tuple[str, int, str] | None:
    y0, y1 = min(w.y0 for w in row), max(w.y1 for w in row)
    zone = "top" if y1 < 0.11 * height else "bottom" if y0 > 0.89 * height else ""
    if not zone:
        return None
    text = re.sub(r"\d+", "#", _row_text(row)).lower()
    return zone, round(y0 / 6), re.sub(r"\s+", " ", text).strip()


def _running(pages: list[tuple[float, list[list[Word]]]]) -> set[tuple[str, int, str]]:
    """The lines that repeat at the top or bottom of the pages."""
    if len(pages) < 3:
        return set()
    seen: dict[tuple[str, int, str], int] = {}
    for height, rows in pages:
        for key in {k for r in rows if (k := _margin_key(r, height))}:
            seen[key] = seen.get(key, 0) + 1
    need = max(2, int(0.4 * len(pages) + 0.5))
    return {k for k, n in seen.items() if n >= need}


# ---------------------------------------------------------------------------- one page
@dataclass
class _Page:
    number: int
    width: float
    height: float
    words: list[Word] = field(default_factory=list)
    grids: list[Any] = field(default_factory=list)
    plain: str | None = None  # read the plain way: its text as PDFium gives it
    scanned: bool = False


def _lay_out(page: _Page, running: set[tuple[str, int, str]], body: float, dropped: list[str]) -> list[Para | Table]:
    if page.plain is not None:
        return [Para(t, "p", page.number, body) for t in re.split(r"\n\s*\n", page.plain) if t.strip()]
    words = page.words
    segs: list[Seg] = []
    rid = 0
    for xs, ys, _gh, gv in page.grids:
        rows, used = _fill_grid(xs, ys, gv, words)
        rows = [r for r in rows if any(c for c in r)]
        if len(rows) < 2 or not used:
            continue
        ids = {id(w) for w in used}
        words = [w for w in words if id(w) not in ids]
        rid += 1
        table = Table(rows, page.number, [page.number], True, list(xs[:-1]))
        segs.append(Seg(xs[0], ys[0], xs[-1], ys[-1], [], rid, table))
    for row in _rows(words):
        key = _margin_key(row, page.height)
        text = _row_text(row)
        if key and (key in running or _PAGE_WORD.match(text)):
            if len(dropped) < 40 and text not in dropped:
                dropped.append(text)
            continue
        rid += 1
        segs.extend(_segments(row, rid))
    # A table that can be nothing else is taken out whole before the columns are looked for,
    # so the gaps between its columns are never taken for the page's own.
    every = _by_row(segs)
    for a, b, seps in _stream_tables(every, sure=True):
        part = every[a:b]
        gone = {id(s) for r in part for s in r}
        segs = [s for s in segs if id(s) not in gone]
        rid += 1
        box = [s for r in part for s in r]
        first = min(s.x0 for s in box)
        table = Table(_build_stream(part, seps), page.number, [page.number], False, [first, *seps])
        segs.append(
            Seg(first, min(s.y0 for s in box), max(s.x1 for s in box), max(s.y1 for s in box), [], rid, table)
        )
    blocks: list[Para | Table] = []
    for column in _order(segs):
        rows = _by_row(column)
        tables = {a: (b, seps) for a, b, seps in _stream_tables(rows)}
        lines: list[_Line] = []

        def flush(lines: list[_Line] = lines) -> None:
            blocks.extend(_paragraphs(lines, body, page.number))
            lines.clear()

        k = 0
        while k < len(rows):
            row = rows[k]
            if k in tables:
                b, seps = tables[k]
                flush()
                built = _build_stream(rows[k:b], seps)
                first = min(s.x0 for r in rows[k:b] for s in r)
                blocks.append(Table(built, page.number, [page.number], False, [first, *seps]))
                k = b
                continue
            if row[0].table is not None:
                flush()
                blocks.append(row[0].table)
                k += 1
                continue
            ws = [w for s in row for w in s.words]
            lines.append(
                _Line(
                    _row_text(ws),
                    max(w.size for w in ws),
                    all(w.bold for w in ws),
                    min(w.y0 for w in ws),
                    max(w.y1 for w in ws),
                    min(w.x0 for w in ws),
                    max(w.x1 for w in ws),
                )
            )
            k += 1
        flush()
    if page.scanned:
        for b in blocks:
            if isinstance(b, Table):
                b.rows = [[_ocr_number(c) for c in r] for r in b.rows]
    return blocks


_OCR_NUMBER = re.compile(r"^[-(]?[$€£]?\s?\d{1,3}(\s?[,.]\s?[\doO]{3})*(\s?[.,]\s?[\doO]{1,2})?\)?%?$")


def _ocr_number(text: str) -> str:
    """A figure in a scanned table as it was printed: OCR reads 1,870.00 as "1 ,870.oo"."""
    if not re.search(r"[oO\s]", text) or not _OCR_NUMBER.match(text):
        return text
    return re.sub(r"\s", "", text).replace("o", "0").replace("O", "0")


# ---------------------------------------------------------------------------- across pages
_ENDS = re.compile(r"[.!?:;\"'”’)\]]\s*$")


def _same_shape(a: Table, b: Table) -> bool:
    if a.cols != b.cols or a.ruled != b.ruled:
        return False
    if len(a.xs) == len(b.xs) and a.xs:
        return all(abs(x - y) <= 12 for x, y in zip(a.xs, b.xs, strict=True))
    return True


def _join(blocks: list[Para | Table]) -> list[Para | Table]:
    out: list[Para | Table] = []
    for b in blocks:
        prev = out[-1] if out else None
        if isinstance(b, Table) and isinstance(prev, Table) and b.page == prev.pages[-1] + 1 and _same_shape(prev, b):
            rows = b.rows
            if rows and prev.rows and [_one_line(c) for c in rows[0]] == [_one_line(c) for c in prev.rows[0]]:
                rows = rows[1:]  # the heading row, printed again at the top of the page
            prev.rows.extend(rows)
            prev.pages.append(b.page)
            continue
        if (
            isinstance(b, Para)
            and isinstance(prev, Para)
            and b.kind == "p"
            and prev.kind == "p"
            and b.page == prev.page + 1
            and prev.text
            and not _ENDS.search(prev.text)
            and b.text[:1].islower()
        ):
            prev.text += " " + b.text  # a paragraph the page break cut in two
            continue
        out.append(b)
    return out


# ---------------------------------------------------------------------------- reading a file
class PdfLocked(ValueError):
    """The PDF needs a password that was not given."""


def _form_words(path: Path, password: str | None) -> dict[int, list[Word]]:
    """What is typed into a form's fields, as words where the fields are, by page (0-based)."""
    out: dict[int, list[Word]] = {}
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        if reader.is_encrypted:
            reader.decrypt(password or "")
        if "/AcroForm" not in reader.trailer["/Root"]:
            return out
        for i, page in enumerate(reader.pages):
            height = float(page.mediabox.height)
            for ref in page.get("/Annots") or []:
                a = ref.get_object()
                if a.get("/Subtype") != "/Widget":
                    continue
                kind = a.get("/FT") or (a.get("/Parent") or {}).get("/FT")
                value = a.get("/V")
                if value is None and a.get("/Parent") is not None:
                    value = a["/Parent"].get_object().get("/V")
                rect = [float(v) for v in a.get("/Rect") or []]
                if len(rect) != 4:
                    continue
                if kind == "/Btn":
                    state = str(a.get("/AS") or value or "")
                    if "/Ff" in a and int(a.get("/Ff") or 0) & 0x10000:
                        continue  # a push button
                    text = "[x]" if state not in ("", "/Off", "Off") else "[ ]"
                elif value is None or not str(value).strip():
                    continue
                else:
                    text = _one_line(value)
                x0, x1 = min(rect[0], rect[2]), max(rect[0], rect[2])
                y0, y1 = height - max(rect[1], rect[3]), height - min(rect[1], rect[3])
                mid = (y0 + y1) / 2
                size = min(11.0, max(6.0, (y1 - y0) * 0.6))
                # As tall as a line of type, on the line its label is on.
                out.setdefault(i, []).append(
                    Word(x0, mid - size / 2, max(x1, x0 + 1), mid + size / 2, text, size)
                )
    except Exception:
        return {}
    return out


def read(
    path: Path,
    *,
    password: str | None = None,
    ocr: bool = True,
    max_pages: int | None = None,
    max_chars: int | None = None,
    ocr_image: Any = None,
) -> Layout:
    """A PDF as paragraphs, headings and tables in reading order. ``max_chars`` stops early once
    that much text is read (for a quick look at a long file). ``ocr_image`` reads the lines of
    text in a picture (Pixel's own reader when not given). Raises :class:`PdfLocked` for a
    PDF that needs a password, and ValueError for a file that is not a PDF."""
    import pypdfium2 as pdfium

    path = Path(path)
    with _lock:
        try:
            doc = pdfium.PdfDocument(str(path), password=password)
        except Exception as exc:
            if "password" in str(exc).lower():
                raise PdfLocked(f"{path.name} is password-protected.") from None
            raise ValueError(f"{path.name} doesn't open as a PDF ({type(exc).__name__}).") from None
        try:
            return _read(doc, path, password, ocr, max_pages, max_chars, ocr_image)
        finally:
            doc.close()


def _read(
    doc: Any,
    path: Path,
    password: str | None,
    ocr: bool,
    max_pages: int | None,
    max_chars: int | None,
    ocr_image: Any = None,
) -> Layout:
    total = len(doc)
    count = min(total, max_pages or MAX_PAGES, MAX_PAGES)
    layout = Layout(pages=total)
    forms = _form_words(path, password)
    pages: list[_Page] = []
    read_chars = 0
    for i in range(count):
        page = doc[i]
        try:
            width, height = page.get_size()
            item = _Page(i + 1, width, height)
            textpage = page.get_textpage()
            try:
                words = _page_words(textpage, height)
                if words is None:
                    item.plain = textpage.get_text_range()
                    layout.plain.append(i + 1)
                else:
                    item.words = words
            finally:
                textpage.close()
            if item.plain is None:
                typed = sum(len(w.text) for w in item.words)
                if typed < 25 and ocr and not layout.no_reader:
                    got = _ocr_words(page, layout, item, ocr_image)
                    # A page with one short typed line (a cover, a line in another script)
                    # keeps its own words unless the picture plainly holds far more.
                    if got and (typed == 0 or sum(len(w.text) for w in got) > 3 * typed):
                        item.words, item.scanned = got, True
                        layout.scanned.append(i + 1)
                    else:
                        item.grids = []
                if not item.scanned:
                    try:
                        hs, vs = _drawn_lines(page, height)
                        item.grids = _ruled_tables(hs, vs)
                    except Exception:
                        item.grids = []
                item.words = [*item.words, *forms.get(i, [])]
            read_chars += len(item.plain or "") + sum(len(w.text) + 1 for w in item.words)
            pages.append(item)
        finally:
            page.close()
        if max_chars and read_chars > max_chars:
            break
    sizes: dict[float, int] = {}
    for p in pages:
        for w in p.words:
            sizes[round(w.size, 1)] = sizes.get(round(w.size, 1), 0) + len(w.text)
    body = max(sizes, key=lambda s: sizes[s]) if sizes else 11.0
    running = _running([(p.height, _rows(p.words)) for p in pages if p.plain is None])
    blocks: list[Para | Table] = []
    for p in pages:
        try:
            blocks.extend(_lay_out(p, running, body, layout.dropped))
        except Exception:  # one odd page is read plainly, never the whole file lost
            text = "\n".join(_row_text(r) for r in _rows(p.words))
            layout.plain.append(p.number)
            blocks.extend(Para(t, "p", p.number, body) for t in text.split("\n") if t.strip())
    layout.blocks = _join(blocks)
    return layout


def _long_runs(dark: Any, length: int) -> Any:
    """Which pixels sit in an unbroken run of ``length`` or more dark pixels along a row."""
    import numpy as np

    h, w = dark.shape
    if w < length:
        return np.zeros_like(dark)
    cs = np.zeros((h, w + 1), dtype=np.int32)
    cs[:, 1:] = np.cumsum(dark, axis=1)
    full = (cs[:, length:] - cs[:, :-length]) == length  # a run starts here
    c2 = np.zeros((h, full.shape[1] + 1), dtype=np.int32)
    c2[:, 1:] = np.cumsum(full, axis=1)
    j = np.arange(w)
    hi = np.minimum(j, w - length) + 1
    lo = np.maximum(j - length + 1, 0)
    return (c2[:, hi] - c2[:, lo]) > 0


def _runs_of(mask: Any) -> list[tuple[int, int, int]]:
    """(row, first column, one past the last) of each run of set pixels along the rows."""
    import numpy as np

    h, w = mask.shape
    padded = np.zeros((h, w + 2), dtype=np.int8)
    padded[:, 1:-1] = mask
    d = np.diff(padded, axis=1)
    r0, c0 = np.nonzero(d == 1)
    _r1, c1 = np.nonzero(d == -1)
    return list(zip(r0.tolist(), c0.tolist(), c1.tolist(), strict=True))


def picture_lines(img: Any, dpi: int = OCR_DPI) -> tuple[list[tuple[float, float, float]], list[tuple[float, float, float]], Any]:
    """The ruling lines in a picture of a page, in page units, and the picture with them wiped
    out. A scanned table's lines are its grid, as a typed table's are; and with the lines gone
    the letters that touch them (the tail of a g or a p on a row's bottom line) are read as
    letters again."""
    import numpy as np
    from PIL import Image

    gray = np.asarray(img.convert("L"))
    dark = gray < 140
    length = int(dpi * 0.45)
    thick = max(6, dpi // 22)
    across = _long_runs(dark, length) & ~_long_runs(dark.T, thick).T
    down = _long_runs(dark.T, length).T & ~_long_runs(dark, thick)
    s = 72 / dpi
    hs = [((r + 0.5) * s, a * s, b * s) for r, a, b in _runs_of(across) if b - a >= length // 3]
    vs = [((c + 0.5) * s, a * s, b * s) for c, a, b in _runs_of(down.T) if b - a >= length // 3]
    if len(hs) + len(vs) > 20000:
        return [], [], img  # a picture full of streaks, not a ruled page
    clean = gray.copy()
    wipe = across | down
    # one pixel more on each side: the grey edge of a line
    wide = wipe.copy()
    wide[1:, :] |= wipe[:-1, :]
    wide[:-1, :] |= wipe[1:, :]
    wide[:, 1:] |= wipe[:, :-1]
    wide[:, :-1] |= wipe[:, 1:]
    clean[wide] = 255
    return hs, vs, Image.fromarray(clean)


def _ocr_words(page: Any, layout: Layout, item: _Page | None = None, ocr_image: Any = None) -> list[Word]:
    """The words of a page that is only a picture, read with OCR, in page units."""
    try:
        if ocr_image is None:
            from pyxi.agents.pixel_pdf import _ocr_image as ocr_image
        img = page.render(scale=OCR_DPI / 72).to_pil()
        try:
            hs, vs, clean = picture_lines(img)
            grids = _ruled_tables(hs, vs)
        except Exception:
            grids, clean = [], img
        lines = ocr_image(clean if grids else img)
        if item is not None and lines:
            item.grids = grids
    except ValueError as exc:  # no text reader on this PC: said once, by the caller
        layout.no_reader = layout.no_reader or str(exc) or "No text reader could start."
        return []
    except Exception:
        return []
    s = 72 / OCR_DPI
    words: list[Word] = []
    for k, ln in enumerate(lines):
        # A reader that gives lines with no boxes: one under the other, down the page.
        rect = getattr(ln, "rect", None) or (100, 100 + 40 * k, 12 * len(ln.text), 28)
        parts = getattr(ln, "words", None) or [(ln.text, rect)]
        for text, rect in parts:
            x, y, w, h = rect
            if not str(text).strip():
                continue
            words.append(Word(x * s, y * s, (x + w) * s, (y + h) * s, str(text).strip(), max(h * s * 0.8, 5.0)))
    # OCR boxes on one line differ a little in height: give a line's words one size.
    for row in _rows(words):
        size = statistics.median(w.size for w in row)
        mid = statistics.median(w.cy for w in row)
        for w in row:
            w.size = size
            w.y0, w.y1 = mid - size / 1.6, mid + size / 1.6
    return words


# ---------------------------------------------------------------------------- numbers for a sheet
_NUM = re.compile(r"^\(?[-+]?[$€£]?\s?\d{1,3}(?:,\d{3})*(?:\.\d+)?\)?%?$|^\(?[-+]?[$€£]?\s?\d+(?:\.\d+)?\)?%?$")


def cell_value(text: str) -> Any:
    """A table cell as what a spreadsheet should hold: a number when it plainly is one (1,740.00,
    (250), 12%), else the text as it stands. Codes that only look like numbers (a leading zero,
    more than 15 digits) stay text."""
    t = text.strip()
    if not t or not _NUM.match(t):
        return t
    digits = re.sub(r"\D", "", t)
    bare = re.sub(r"[,$€£\s()%+]", "", t)
    if len(digits) > 15 or (len(digits) > 1 and bare.lstrip("-").startswith("0") and "." not in bare):
        return t
    try:
        value = float(bare)
    except ValueError:
        return t
    if t.startswith("(") and t.rstrip("%").endswith(")"):
        value = -value
    if t.endswith("%"):
        return value / 100
    return int(value) if "." not in bare else value
