"""Ledger building a deck: a plan of the slides first, then the slides, then a check.

"Make a presentation about X", "turn this report into slides", "a 10-slide pitch deck from
this PDF": the result is a real .pptx, built the way Ledger builds a big workbook
(ledger_build.py):

1. The source is read here, with no model: a Word, PDF, text or Markdown file as its headings,
   paragraphs and tables (a PDF through agents/pdf_layout.py, so its tables are tables), a
   spreadsheet as its sheets with each column's totals worked out. Every figure in it is
   noted.
2. One short call plans the deck: its title and theme, and for every slide its layout, its
   title, the ONE point it makes and the visual it carries. The plan is put right here where
   it breaks the rules (a title slide first, a closing slide last, the number of slides asked
   for, no chart where there are no figures).
3. The slides are written a few to a call, all the calls at the same time, each against the
   plan. The title and section slides need no call: the plan says all they hold.
4. A check, done here. A deck made from a document may hold only what the document says: a
   figure that is not in the source (or in the request) goes back to the model once, and if
   it is still there the line that holds it is taken out and said. A chart's figures are
   never typed by the model: it names a table and its columns, and the figures are read from
   the source here, then read back out of the saved file and compared. Text is measured with
   the real fonts so nothing runs off a slide (deck_design.py), every slide gets speaker
   notes, and the saved file is opened again and gone over.

Nothing the model writes is run, and no fact is added here: where the source has nothing for
a slide, the slide is left out.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from itertools import islice
from pathlib import Path
from typing import Any

from pyxi.agents import deck_design as design
from pyxi.agents.ledger_build import _turned_away, read_json

log = logging.getLogger(__name__)

MAX_SLIDES = 40
MIN_SLIDES = 3
PARALLEL = 8  # calls with the model at the same time
BATCH = 3  # slides written in one call
PLAN_CHARS = 24000  # of the source shown to the planner
FILL_CHARS = 14000  # of the source shown with each batch of slides
TABLE_ROWS = 40  # rows of a table shown to the model
TRIES = 3
BACKOFF = 1.5
SOURCE_EXTS = {".pdf", ".docx", ".txt", ".md", ".markdown", ".xlsx", ".xlsm", ".csv", ".pptx"}

Ask = Callable[[str, str], Awaitable[tuple[str, float]]]


class DeckError(ValueError):
    """Why the deck could not be made, said the way you'd tell a person."""


# ---------------------------------------------------------------------------- the source
@dataclass
class SrcTable:
    name: str
    header: list[str]
    rows: list[list[Any]]

    def column(self, said: Any) -> int | None:
        want = _norm(said)
        if not want:
            return None
        names = [_norm(h) for h in self.header]
        if want in names:
            return names.index(want)
        close = [i for i, h in enumerate(names) if h and (want in h or h in want)]
        if len(close) == 1:
            return close[0]
        if re.fullmatch(r"[a-z]", str(said).strip().lower()):  # "B": the second column
            i = ord(str(said).strip().lower()) - 97
            return i if i < len(self.header) else None
        return None

    def numeric(self, c: int) -> list[float]:
        """The figures in a column, the table's own Total row left out (it would count twice)."""
        return [
            v
            for r in self.rows
            if c < len(r) and (v := _number(r[c])) is not None and not _is_total(r)
        ]


@dataclass
class Source:
    names: list[str] = field(default_factory=list)
    kind: str = "topic"  # topic, document, spreadsheet
    text: str = ""
    tables: list[SrcTable] = field(default_factory=list)
    numbers: list[float] = field(default_factory=list)
    pictures: list[tuple[str, bytes, str]] = field(default_factory=list)  # id, the picture, where from
    chars: int = 0

    def table(self, said: Any) -> SrcTable | None:
        want = _norm(said)
        for t in self.tables:
            if _norm(t.name) == want:
                return t
        close = [t for t in self.tables if want and (want in _norm(t.name) or _norm(t.name) in want)]
        if len(close) == 1:
            return close[0]
        return self.tables[0] if len(self.tables) == 1 else None


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value if value is not None else "").lower())


_TOTAL = re.compile(r"(?i)^\s*(grand\s+)?(sub\s?)?totals?\b")


def _is_total(row: list[Any]) -> bool:
    first = next((str(c) for c in row if str(c if c is not None else "").strip()), "")
    return bool(_TOTAL.match(first))


_NUMBER = re.compile(r"(?<![\w.])[-+]?\$?\d[\d,]*(?:\.\d+)?%?")


def _number(value: Any) -> float | None:
    """A cell as a number, when it plainly is one."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    t = str(value if value is not None else "").strip()
    if not t or not re.fullmatch(r"\(?[-+]?[$€£]?\s?\d[\d,]*(?:\.\d+)?\)?%?", t):
        return None
    try:
        v = float(re.sub(r"[,$€£\s()%+]", "", t))
    except ValueError:
        return None
    return -v if t.startswith("(") else v


def numbers_in(text: str) -> list[tuple[str, float, int]]:
    """Every figure written in this text: (as written, its value, its decimal places)."""
    out = []
    for m in _NUMBER.finditer(text or ""):
        raw = m.group(0)
        bare = re.sub(r"[,$%+]", "", raw)
        try:
            value = float(bare)
        except ValueError:
            continue
        out.append((raw, value, len(bare.split(".")[1]) if "." in bare else 0))
    return out


def known(value: float, places: int, have: list[float]) -> bool:
    """Is this figure one of the source's: as it stands, rounded, or said in thousands,
    millions or as a per cent of a share?"""
    slack = 0.5 * 10 ** (-places) + 1e-9
    for scale in (1.0, 1e3, 1e6, 1e9, 0.01):
        target, room = abs(value) * scale, slack * scale
        if any(abs(abs(h) - target) <= room for h in have):
            return True
    return False


def _shown(v: Any) -> str:
    if isinstance(v, float):
        return f"{v:,.2f}".rstrip("0").rstrip(".") if abs(v) < 1e15 else str(v)
    return " ".join(str("" if v is None else v).split())


def _table_text(t: SrcTable) -> str:
    def line(cells: list[Any]) -> str:
        return "| " + " | ".join(_shown(c).replace("|", "/")[:60] for c in cells) + " |"

    out = [f"Table \"{t.name}\" ({len(t.rows)} rows):", line(t.header)]
    out += [line(r) for r in t.rows[:TABLE_ROWS]]
    if len(t.rows) > TABLE_ROWS:
        out.append(f"... and {len(t.rows) - TABLE_ROWS} more rows (a chart reads them all)")
    stats = []
    for c, h in enumerate(t.header):
        nums = t.numeric(c)
        if len(nums) >= max(2, len(t.rows) // 2):
            stats.append(
                f"{h}: total {_shown(float(sum(nums)))}, average {_shown(round(sum(nums) / len(nums), 2))}, "
                f"lowest {_shown(float(min(nums)))}, highest {_shown(float(max(nums)))}"
            )
    if stats:
        out.append("Worked out from the rows: " + "; ".join(stats) + ".")
    return "\n".join(out)


def _as_table(name: str, rows: list[list[Any]]) -> SrcTable | None:
    rows = [list(r) for r in rows if any(str(c if c is not None else "").strip() for c in r)]
    if len(rows) < 2:
        return None
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    header = [_shown(c) or f"Column {i + 1}" for i, c in enumerate(rows[0])]
    return SrcTable(name, header, rows[1:])


def _stats(t: SrcTable) -> list[float]:
    out: list[float] = []
    for c in range(len(t.header)):
        nums = t.numeric(c)
        out += nums
        if len(nums) >= 2:
            out += [float(sum(nums)), round(sum(nums) / len(nums), 2), float(len(nums))]
    out.append(float(len(t.rows)))
    return out


def read_source(paths: list[Path]) -> Source:
    """What the deck is made from, read with no model. Raises :class:`DeckError` for a file
    that can't be read or has nothing in it."""
    src = Source()
    parts: list[str] = []
    for path in paths:
        ext = path.suffix.lower()
        if ext not in SOURCE_EXTS:
            raise DeckError(
                f"I can make slides from Word, PDF, text, Markdown, Excel and CSV files, not {path.name}."
            )
        before = len(src.tables)
        try:
            text = _read_one(path, src)
        except DeckError:
            raise
        except Exception as exc:
            from pyxi.plainwords import plain_failure

            log.warning("deck: %s could not be read: %s", path.suffix, type(exc).__name__)
            why = plain_failure(
                exc, "It may be damaged, or open in another app. Close it and ask again.", 200
            )
            raise DeckError(f"I couldn't read {path.name}. {why}") from None
        if not text.strip() and len(src.tables) == before:
            raise DeckError(
                f"{path.name} has nothing in it to make slides from. If it's a scan, ask Pixel "
                "to make it searchable first."
            )
        src.names.append(path.name)
        parts.append(f"# File: {path.name}\n{text}" if len(paths) > 1 else text)
        if ext in (".xlsx", ".xlsm", ".csv"):
            src.kind = "spreadsheet" if src.kind in ("topic", "spreadsheet") else "document"
        else:
            src.kind = "document"
    src.text = "\n\n".join(parts).strip()
    src.chars = len(src.text)
    src.numbers = [v for _, v, _ in numbers_in(src.text)]
    for t in src.tables:
        src.numbers += _stats(t)
    return src


def _read_one(path: Path, src: Source) -> str:
    ext = path.suffix.lower()
    n = len(src.tables)
    if ext == ".pdf":
        from pyxi.agents.pixel_pdf import read_layout

        layout = read_layout(path)
        if layout is None:
            raise DeckError(f"I couldn't read {path.name}.")
        out = []
        for b in layout.blocks:
            if hasattr(b, "rows"):
                t = _as_table(f"Table {len(src.tables) + 1}", b.rows)
                if t is not None:
                    src.tables.append(t)
                    out.append(_table_text(t))
            else:
                out.append(("# " if b.kind == "h1" else "## " if b.kind == "h2" else "- " if b.kind == "bullet" else "") + b.text)
        _pdf_pictures(path, src)
        return "\n\n".join(out)
    if ext == ".docx":
        from docx import Document
        from docx.table import Table as WordTable
        from docx.text.paragraph import Paragraph

        doc = Document(str(path))
        out = []
        for child in doc.element.body.iterchildren():
            tag = child.tag.rsplit("}", 1)[-1]
            if tag == "p":
                p = Paragraph(child, doc)
                text = p.text.strip()
                if not text:
                    continue
                style = (p.style.name if p.style is not None else "").lower()
                mark = "# " if style in ("title", "heading 1") else "## " if style.startswith("heading") else "- " if "list" in style else ""
                out.append(mark + text)
            elif tag == "tbl":
                wt = WordTable(child, doc)
                t = _as_table(f"Table {len(src.tables) + 1}", [[c.text.strip() for c in r.cells] for r in wt.rows])
                if t is not None:
                    src.tables.append(t)
                    out.append(_table_text(t))
        _docx_pictures(doc, src)
        return "\n\n".join(out)
    if ext in (".xlsx", ".xlsm"):
        from openpyxl import load_workbook

        out = []
        for values in (False, True):  # the results when the file holds them, else what was typed
            wb = load_workbook(path, data_only=values, read_only=True)
            try:
                sheets = {ws.title: [list(r) for r in islice(ws.iter_rows(values_only=True), 2000)] for ws in wb.worksheets}
            finally:
                wb.close()
            if values:
                best = sheets
            else:
                typed = sheets
        for title, rows in best.items():
            rows = [[typed[title][i][j] if c is None and i < len(typed[title]) and j < len(typed[title][i]) and not str(typed[title][i][j] or "").startswith("=") else c for j, c in enumerate(r)] for i, r in enumerate(rows)]
            t = _as_table(title, _trim(rows))
            if t is not None:
                src.tables.append(t)
                out.append(_table_text(t))
        if len(src.tables) == n:
            return ""
        return "\n\n".join(out)
    if ext == ".csv":
        import csv

        from pyxi.agents.pixel_render import read_any_text

        rows = list(csv.reader(read_any_text(path).splitlines()))[:2000]
        t = _as_table(path.stem, rows)
        if t is None:
            return ""
        src.tables.append(t)
        return _table_text(t)
    if ext == ".pptx":
        from pyxi.agents.pixel_pdf import document_lines

        return "\n".join(document_lines(path))
    from pyxi.agents.pixel_render import read_any_text

    text = read_any_text(path).replace("\r\n", "\n")
    for block in re.findall(r"(?m)(?:^\|.*\|[ \t]*\n?){3,}", text):
        rows = [[c.strip() for c in ln.strip().strip("|").split("|")] for ln in block.strip().splitlines()]
        rows = [r for r in rows if not all(re.fullmatch(r":?-{2,}:?", c or "-") for c in r)]
        t = _as_table(f"Table {len(src.tables) + 1}", rows)
        if t is not None:
            src.tables.append(t)
    return text


def _trim(rows: list[list[Any]]) -> list[list[Any]]:
    """A sheet's rows without the empty ones and the empty columns round them."""
    rows = [r for r in rows if any(c not in (None, "") for c in r)]
    if not rows:
        return []
    keep = [j for j in range(max(len(r) for r in rows)) if any(j < len(r) and r[j] not in (None, "") for r in rows)]
    return [[r[j] if j < len(r) else "" for j in keep] for r in rows]


def _pdf_pictures(path: Path, src: Source) -> None:
    try:
        from pypdf import PdfReader

        from pyxi.agents.pixel_pdf import _page_images

        reader = PdfReader(str(path))
        for i, page in enumerate(reader.pages[:60], 1):
            for img in _page_images(page):
                if len(src.pictures) >= 8:
                    return
                if _big_enough(img):
                    src.pictures.append((f"P{len(src.pictures) + 1}", img, f"page {i} of {path.name}"))
    except Exception:
        return


def _docx_pictures(doc: Any, src: Source) -> None:
    try:
        for rel in doc.part.rels.values():
            if "image" in rel.reltype and len(src.pictures) < 8 and _big_enough(rel.target_part.blob):
                src.pictures.append((f"P{len(src.pictures) + 1}", rel.target_part.blob, "the document"))
    except Exception:
        return


def _big_enough(blob: bytes) -> bool:
    try:
        import io

        from PIL import Image

        with Image.open(io.BytesIO(blob)) as im:
            return min(im.size) >= 200 and im.format in ("JPEG", "PNG", "GIF", "BMP", "TIFF")
    except Exception:
        return False


# ---------------------------------------------------------------------------- the request
_WORD_NUMBER = {
    "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40,
}  # fmt: skip
_COUNT = re.compile(rf"(?i)\b(\d{{1,3}}|{'|'.join(_WORD_NUMBER)})[- ]?(?:\w+[- ]){{0,1}}?slides?\b")
_EMPTY_TOPIC = re.compile(
    r"(?i)\b(make|create|build|give|do|write|prepare|put|together|me|us|a|an|the|new|some|please|"
    r"pyxi|ledger|can|you|i|need|want|would|like|powerpoint|ppt|pptx|presentation|deck|slides?|"
    r"slideshow|pitch|file|for|about|on|of|with|to|it|that|this|in|quick|nice|good|simple|short|"
    r"\d+|three|four|five|six|seven|eight|nine|ten|twelve|fifteen|twenty|thirty|forty|untitled)\b"
)


def slides_asked(task: str) -> int | None:
    m = _COUNT.search(task or "")
    if not m:
        return None
    word = m.group(1).lower()
    return int(word) if word.isdigit() else _WORD_NUMBER[word]


def has_topic(task: str, title: str = "") -> bool:
    """Does the request say what the deck is about? "Make me a presentation" does not."""
    left = _EMPTY_TOPIC.sub(" ", f"{task} {title}")
    return len(re.findall(r"[A-Za-z]{3,}", left)) >= 1


# ---------------------------------------------------------------------------- prompts
_LAYOUT_NOTES = {
    "title": "the opening slide (always slide 1)",
    "section": "a divider that opens a part of the deck (only in a deck of 8 slides or more)",
    "statement": "one big sentence on its own: the claim the deck turns on",
    "bullets": "two to five short points",
    "two_columns": "two groups of points side by side, each with a heading",
    "comparison": "two options or a before and after, point for point, with a one-line verdict",
    "timeline": "three to six steps or dates in order",
    "big_number": "one figure that matters, with what it is",
    "chart": "a native chart of figures from one of the source's tables",
    "picture": "one of the source's pictures with a caption",
    "closing": "the last slide: what to remember and what happens next (always the last slide)",
}

PLANNER = (
    "You are {name}, the documents specialist on the user's team, planning a slide deck. Each "
    "slide is written afterwards from this plan, so the plan is the deck: decide what every "
    "slide is for. Return JSON only:\n"
    '{"title": "<the deck\'s title, 8 words at most>", "subtitle": "<one line under it, or empty>", '
    '"theme": "<one of: {themes}>", "slides": [{"layout": "<one of the layouts below>", '
    '"title": "<the slide\'s title, 9 words at most, said as the point and not as a topic>", '
    '"point": "<the ONE thing this slide says, in a sentence>", '
    '"visual": "<what it shows: chart: the table\'s name, its label column against its value '
    'column; or picture: P1; or none>"}]}\n'
    "Layouts:\n{layouts}\n"
    "Themes: {theme_notes}. Pick the one that suits the subject, or the one they named.\n"
    "Rules: one point a slide. Slide 1 is the title slide and the last is the closing slide. "
    "{count} Vary the layouts: never the same one three times running, and use bullets for no "
    "more than half the slides. Tell a story in order: where things stand, what was found, what "
    "it means, what to do. {source_rule} A chart only where a table below holds the figures "
    "(name the table and the columns in \"visual\"){picture_rule}. No em dashes."
)

SOURCE_RULE = (
    "This deck is made FROM the source below and from nothing else: every fact, name and figure "
    "on a slide must be in it. Do not add what you know from elsewhere, and do not make up a "
    "figure, a date, a quote or a name. If the source has too little for the number of slides "
    "asked for, plan fewer slides and say nothing of it."
)
TOPIC_RULE = (
    "There is no source file: write from what is well known about the subject and from what "
    "the request says. Do not make up statistics, quotes, dates or names of people: where a "
    "figure would be needed and you are not sure of it, plan the slide without one. Charts and "
    "big numbers only from figures the request itself gives."
)

WRITER = (
    "You are {name}, the documents specialist on the user's team, writing slides for a deck "
    "that is already planned. Write ONLY the slides asked for, each to its plan: its layout, "
    "its title and the one point it makes. Return JSON only: {\"slides\": [{\"n\": <slide "
    "number>, ...the fields of its layout..., \"notes\": \"<speaker notes: two to four full "
    "sentences the presenter says aloud, adding what the slide leaves out>\"}]}\n"
    "Fields by layout:\n"
    '- statement: "statement" (one sentence, 16 words at most), "support" (one short line).\n'
    '- bullets: "bullets" (2 to 5, each 14 words at most, each a whole thought).\n'
    '- two_columns: "left" and "right", each {"heading": "..", "bullets": [2 to 4 short points]}.\n'
    '- comparison: "left" and "right", each {"heading": "..", "points": [2 to 4, matched point for point]}, and "verdict" (one line).\n'
    '- timeline: "steps": [{"label": "<a date, or Step 1>", "text": "<9 words at most>"}], 3 to 6 of them, in order.\n'
    '- big_number: "number" (the figure as it should be read, like 42% or 7,286), "label" (what it is, 8 words at most), "context" (one line).\n'
    '- chart: "chart": {"type": "column|bar|line|pie", "table": "<the table\'s name>", "labels": "<the header of the column of names or dates>", "values": ["<the header of a column of figures>", ..]}, "takeaway" (one line: what the chart shows), "bullets" (0 to 2 short points). Never type the figures: they are read from the table.\n'
    '- picture: "picture" (its id, like P1), "caption" (one or two lines).\n'
    '- closing: "takeaways" (3 at most, each 12 words at most), "next" (one line: the ask, or the next step).\n'
    "Every slide may also give \"title\" to sharpen the planned one (9 words at most). Plain "
    "words, no markdown, no em dashes, no emoji, nothing in brackets for someone to fill in. "
    "{source_rule}"
)
WRITER_SOURCE = (
    "Everything on a slide must come from the source below: no fact, name or figure from "
    "anywhere else, and every figure exactly as the source has it (rounding is fine). The "
    "speaker notes too."
)
WRITER_TOPIC = (
    "Write from what is well known about the subject. Do not make up statistics, quotes, dates "
    "or people; a figure only when the request itself gives it."
)


@dataclass
class Slide:
    n: int
    layout: str
    title: str = ""
    point: str = ""
    visual: str = ""
    spec: dict[str, Any] = field(default_factory=dict)
    state: str = "waiting"  # written, plain, dropped
    notes: list[str] = field(default_factory=list)  # what was changed on it here, for the user


@dataclass
class Deck:
    path: Path | None = None
    title: str = ""
    theme: str = ""
    slides: list[Slide] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    cost_usd: float = 0.0
    asked: int | None = None
    source: str = ""

    @property
    def kept(self) -> list[Slide]:
        return [s for s in self.slides if s.state != "dropped"]

    def headline(self) -> str:
        n = len(self.kept)
        charts = sum(1 for s in self.kept if s.layout == "chart")
        text = f"Built {self.title}, a {n}-slide deck in the {design.THEMES[self.theme].name} theme"
        text += f" from {self.source}" if self.source else ""
        text += f", with {charts} chart{'s' if charts != 1 else ''} drawn from its figures" if charts else ""
        text += ", and speaker notes on every slide."
        if self.asked and self.asked != n:
            text += f" You asked for {self.asked} slides; " + (
                f"the most I make in one deck is {MAX_SLIDES}."
                if self.asked > MAX_SLIDES
                else f"there was enough in the source for {n}."
                if self.source
                else f"it came to {n}."
            )
        return text

    def changes(self) -> list[str]:
        return [f"Slide {i}: {s.title}" for i, s in enumerate(self.kept, 1)]


class Builder:
    def __init__(
        self,
        task: str,
        title: str,
        *,
        ask: Ask,
        sources: list[Path] | None = None,
        folder: Path,
        name: str = "Ledger",
        context: str = "",
        progress: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        self.task = task
        self.title = title
        self._ask = ask
        self.paths = list(sources or [])
        self.folder = folder
        self.name = name
        self.context = context
        self.progress = progress
        self.cost = 0.0
        self.gate = asyncio.Semaphore(PARALLEL)
        self.hold = 0.0
        self.src = Source()
        self.problems: list[str] = []
        self.failed: Exception | None = None  # the last call that could not be made

    # ------------------------------------------------------------------ talking to the model
    async def ask(self, system: str, prompt: str) -> dict[str, Any] | None:
        async with self.gate:
            for attempt in range(TRIES):
                if (wait := self.hold - time.monotonic()) > 0:
                    await asyncio.sleep(wait)
                try:
                    text, cost = await self._ask(system, prompt)
                    break
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    if attempt + 1 == TRIES or not _turned_away(exc):
                        log.warning("deck build: a model call failed", exc_info=True)
                        self.failed = exc
                        return None
                    self.hold = max(self.hold, time.monotonic() + BACKOFF * 2**attempt)
            self.cost += cost
            return read_json(text)

    async def say(self, line: str) -> None:
        if self.progress:
            try:
                await self.progress(line)
            except Exception:
                log.debug("deck build: progress line dropped", exc_info=True)

    def brief(self, chars: int) -> str:
        head = f"Context from your teammates:\n{self.context[:3000]}\n\n" if self.context else ""
        text = f"{head}The request:\n{self.task}\n\nThe deck's working title: {self.title}"
        if self.src.kind == "topic":
            return text
        shown = self.src.text[:chars]
        cut = f"\n... (the source goes on: {self.src.chars:,} characters in all)" if self.src.chars > chars else ""
        text += f"\n\nThe source ({', '.join(self.src.names)}):\n{shown}{cut}"
        if self.src.pictures:
            text += "\n\nPictures in the source: " + "; ".join(f"{pid} (from {where})" for pid, _, where in self.src.pictures)
        return text

    # ------------------------------------------------------------------ the plan
    def allowed(self) -> list[str]:
        out = [k for k in design.LAYOUTS if k not in ("chart", "picture")]
        if self.chartable():
            out.append("chart")
        if self.src.pictures:
            out.append("picture")
        return out

    def chartable(self) -> bool:
        return any(len(t.numeric(c)) >= 2 for t in self.src.tables for c in range(len(t.header)))

    async def plan(self) -> tuple[str, str, str, list[Slide]]:
        asked = slides_asked(self.task)
        want = max(MIN_SLIDES, min(MAX_SLIDES, asked)) if asked else None
        count = (
            f"Plan exactly {want} slides, the title and closing slides included."
            if want
            else "Plan as many slides as the content needs, between 6 and 12."
        )
        layouts = "\n".join(f"- {k}: {_LAYOUT_NOTES[k]}" for k in self.allowed())
        system = (
            PLANNER.replace("{name}", self.name)
            .replace("{themes}", ", ".join(design.THEMES))
            .replace("{layouts}", layouts)
            .replace("{theme_notes}", "; ".join(f"{t.key} ({t.suits})" for t in design.THEMES.values()))
            .replace("{count}", count)
            .replace("{source_rule}", TOPIC_RULE if self.src.kind == "topic" else SOURCE_RULE)
            .replace("{picture_rule}", ", a picture only from the list of pictures" if self.src.pictures else "")
        )
        prompt = self.brief(PLAN_CHARS)
        await self.say("planning the slides")
        for attempt in range(2):
            data = await self.ask(
                system,
                prompt
                if attempt == 0
                else prompt + "\n\nYour last reply could not be read. Again: the JSON object only, shorter.",
            )
            if data and isinstance(data.get("slides"), list) and data["slides"]:
                break
        else:
            if self.failed is not None:
                raise self.failed  # the model could not be reached: said as that, not as a bad plan
            raise DeckError(
                "I couldn't plan the deck: the plan came back unreadable, twice. Ask again, or for fewer slides."
            )
        title = design._clean(data.get("title")) or self.title
        slides = self.put_right(data["slides"], want)
        return title[:120], design._clean(data.get("subtitle"))[:160], str(data.get("theme") or ""), slides

    def put_right(self, raw: list[Any], want: int | None) -> list[Slide]:
        """The plan as the rules have it: only layouts that can be drawn from this source, a
        title slide first and a closing slide last, no more slides than asked for."""
        ok = set(self.allowed())
        items: list[Slide] = []
        for item in raw[: MAX_SLIDES + 4]:
            if not isinstance(item, dict):
                continue
            layout = str(item.get("layout") or "bullets").strip().lower().replace(" ", "_").replace("-", "_")
            layout = {"two_column": "two_columns", "bignumber": "big_number", "number": "big_number", "content": "bullets", "quote": "statement", "end": "closing"}.get(layout, layout)
            if layout not in ok:
                layout = "bullets"
            title = design._clean(item.get("title"))[:140]
            point = design._clean(item.get("point"))[:300]
            if not title and not point:
                continue
            items.append(Slide(0, layout, title or point, point or title, design._clean(item.get("visual"))[:200]))
        body = [s for s in items if s.layout not in ("title", "closing")]
        closing = next((s for s in reversed(items) if s.layout == "closing"), None)
        limit = (want or MAX_SLIDES) - 2
        body = body[: max(1, limit)]
        if len(body) + 2 < 8:  # a short deck has no dividers
            for s in body:
                if s.layout == "section":
                    s.layout = "statement"
        slides = [Slide(0, "title", self.title, ""), *body, closing or Slide(0, "closing", "Thank you", "What to remember.")]
        for n, s in enumerate(slides, 1):
            s.n = n
        return slides

    # ------------------------------------------------------------------ the slides
    def plan_text(self, slides: list[Slide]) -> str:
        return "\n".join(
            f"{s.n}. [{s.layout}] {s.title}. Point: {s.point}" + (f" Visual: {s.visual}." if s.visual and s.visual.lower() != "none" else "")
            for s in slides
        )

    def writer(self) -> str:
        return WRITER.replace("{name}", self.name).replace(
            "{source_rule}", WRITER_TOPIC if self.src.kind == "topic" else WRITER_SOURCE
        )

    async def write(self, slides: list[Slide], batch: list[Slide], again: str = "") -> None:
        prompt = (
            f"{self.brief(FILL_CHARS)}\n\nThe plan of the whole deck:\n{self.plan_text(slides)}\n\n"
            f"Write slides {', '.join(str(s.n) for s in batch)} now."
            + (f"\n\n{again}" if again else "")
        )
        data = await self.ask(self.writer(), prompt)
        if data is None and not again:
            data = await self.ask(self.writer(), prompt + "\n\nYour last reply could not be read. Again: the JSON object only.")
        got = data.get("slides") if isinstance(data, dict) else None
        by_n = {}
        for item in got if isinstance(got, list) else []:
            if isinstance(item, dict):
                try:
                    by_n[int(item.get("n"))] = item
                except (TypeError, ValueError):
                    continue
        if len(batch) == 1 and not by_n and isinstance(got, list) and len(got) == 1 and isinstance(got[0], dict):
            by_n[batch[0].n] = got[0]
        for s in batch:
            if s.n in by_n:
                s.spec = by_n[s.n]
                s.state = "written"
            elif not again:
                s.state = "plain"  # nothing came back for it: the plan's own words

    # ------------------------------------------------------------------ the check
    def allowed_numbers(self) -> list[float]:
        return [*self.src.numbers, *(v for _, v, _ in numbers_in(f"{self.task} {self.context}"))]

    def strays(self, s: Slide) -> list[str]:
        """Figures on this slide that are in neither the source nor the request."""
        if self.src.kind == "topic":
            return []
        have = self.allowed_numbers()
        seen: list[str] = []
        for text in _texts(s.spec):
            for raw, value, places in numbers_in(text):
                if abs(value) <= 12 and places == 0 and "%" not in raw and "$" not in raw:
                    continue  # "three steps", "2 shops": a count of what is on the slide
                if not known(value, places, have) and raw not in seen:
                    seen.append(raw)
        return seen

    def strip(self, s: Slide, bad: list[str]) -> None:
        """Take out every line that still holds a figure the source doesn't have."""

        def clean(value: Any) -> Any:
            if isinstance(value, str):
                return "" if any(_has(value, b) for b in bad) else value
            if isinstance(value, list):
                return [c for c in (clean(v) for v in value) if c not in ("", None, {}, [])]
            if isinstance(value, dict):
                return {k: (v if k in ("chart", "n", "picture") else clean(v)) for k, v in value.items()}
            return value

        s.spec = clean(s.spec)
        s.notes.append(f"left out {len(bad)} figure{'s' if len(bad) != 1 else ''} that {'are' if len(bad) != 1 else 'is'} not in the source ({', '.join(bad[:4])})")

    def chart_data(self, s: Slide, note: bool = True) -> tuple[dict[str, Any] | None, str]:
        """The figures for this slide's chart, read from the source: (the data, "") or
        (None, why there is no chart)."""
        spec = s.spec.get("chart") if isinstance(s.spec.get("chart"), dict) else {}
        table = self.src.table(spec.get("table")) if spec.get("table") or len(self.src.tables) == 1 else None
        if table is None:
            return None, "it named no table of the source"
        label = table.column(spec.get("labels"))
        values = spec.get("values") if isinstance(spec.get("values"), list) else [spec.get("values")]
        cols = [c for c in (table.column(v) for v in values if v) if c is not None and c != label]
        cols = [c for c in dict.fromkeys(cols) if len(table.numeric(c)) >= 2][:4]
        if not cols:
            return None, "the column it named holds no figures"
        if label is None:
            label = next((c for c in range(len(table.header)) if c not in cols and len(table.numeric(c)) < len(table.rows) / 2), None)
        if label is None:
            return None, "there is no column of names to put the figures against"
        groups: dict[str, list[float]] = {}
        for r in table.rows:
            name = _shown(r[label])[:40]
            nums = [_number(r[c]) for c in cols]
            if not name or all(v is None for v in nums) or _is_total(r):
                continue
            acc = groups.setdefault(name, [0.0] * len(cols))
            for i, v in enumerate(nums):
                acc[i] += v or 0.0
        if len(groups) < 2:
            return None, "there are fewer than two rows to chart"
        kind = str(spec.get("type") or "column").lower()
        names = list(groups)
        timed = sum(1 for n in names if re.search(r"\d{4}|(?i:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|q[1-4]|week|day)", n)) > len(names) / 2
        if len(names) > (24 if timed else 10):
            # the latest dates, or the biggest rows, biggest first
            names = names[-24:] if timed else sorted(names, key=lambda n: -groups[n][0])[:10]
            if note:
                s.notes.append(f"the chart shows {len(names)} of the table's {len(groups)} rows")
        if timed and kind in ("pie", "bar"):
            kind = "line" if len(names) > 6 else "column"
        data = {
            "categories": names,
            "series": [(table.header[c], [round(groups[n][i], 6) for n in names]) for i, c in enumerate(cols)],
        }
        return {"data": data, "chart_type": kind, "data_source": table.name}, ""

    def ready(self, s: Slide, deck_title: str, subtitle: str, section: int) -> dict[str, Any] | None:
        """One slide as the writer draws it, or None when there is nothing to put on it."""
        spec = dict(s.spec)
        out: dict[str, Any] = {
            "layout": s.layout,
            "title": design._clean(spec.get("title"))[:140] or s.title,
            "point": s.point,
            "notes": design._clean(spec.get("notes")) or s.point or s.title,
        }
        if s.layout == "title":
            return {**out, "title": deck_title, "subtitle": subtitle, "notes": subtitle or f"This deck is about {deck_title}."}
        if s.layout == "section":
            return {**out, "number": section, "notes": s.point or s.title}
        if s.state == "plain":
            if not s.point or s.point == s.title:
                return None
            s.notes.append("written from the plan alone")
            return {**out, "layout": "statement", "statement": s.point, "title": s.point}
        if s.layout == "chart":
            found, why = self.chart_data(s)
            if found is None:
                s.notes.append(f"no chart ({why})")
                side = [b for b in [spec.get("takeaway"), *(spec.get("bullets") or [])] if design._clean(b)]
                if not side:
                    return None
                if len(side) == 1:
                    return {**out, "layout": "statement", "statement": side[0], "title": side[0]}
                return {**out, "layout": "bullets", "bullets": side}
            return {**out, **found, "takeaway": spec.get("takeaway"), "bullets": spec.get("bullets") or []}
        if s.layout == "picture":
            image = next((img for pid, img, _ in self.src.pictures if pid.lower() == str(spec.get("picture") or "").strip().lower()), None)
            if image is None:
                s.notes.append("no picture (it named none of the source's)")
                if not design._clean(spec.get("caption")):
                    return None
                return {**out, "layout": "statement", "statement": spec.get("caption"), "title": spec.get("caption")}
            return {**out, "image": image, "caption": spec.get("caption")}
        if s.layout == "statement":
            statement = design._clean(spec.get("statement")) or s.point
            return {**out, "statement": statement, "title": statement, "support": spec.get("support")}
        if s.layout == "big_number":
            if not design._clean(spec.get("number")) or not re.search(r"\d", str(spec.get("number"))):
                rest = [b for b in (spec.get("label"), spec.get("context")) if design._clean(b)]
                if not rest:
                    return None
                s.notes.append("no figure for a big number, so it is a plain slide")
                return {**out, "layout": "bullets", "bullets": rest}
            return {**out, "number": spec.get("number"), "label": spec.get("label"), "context": spec.get("context")}
        if s.layout == "timeline":
            steps = [st for st in spec.get("steps") or [] if isinstance(st, dict)]
            if len(steps) < 2:
                texts = [design._clean(st.get("text")) for st in steps]
                return {**out, "layout": "bullets", "bullets": texts} if any(texts) else None
            return {**out, "steps": steps}
        if s.layout in ("two_columns", "comparison"):
            left, right = (spec.get(k) if isinstance(spec.get(k), dict) else {} for k in ("left", "right"))
            items = lambda col: [b for b in (col.get("bullets") or col.get("points") or []) if design._clean(b)]  # noqa: E731
            if not items(left) or not items(right):
                both = items(left) + items(right)
                return {**out, "layout": "bullets", "bullets": both} if both else None
            return {**out, "left": left, "right": right, "verdict": spec.get("verdict")}
        if s.layout == "closing":
            takeaways = [b for b in (spec.get("takeaways") or spec.get("bullets") or []) if design._clean(b)]
            return {**out, "takeaways": takeaways, "next": spec.get("next")}
        bullets = [b for b in (spec.get("bullets") or []) if design._clean(b)]
        if not bullets:
            return None
        return {**out, "layout": "bullets", "bullets": bullets}

    # ------------------------------------------------------------------ all of it
    async def run(self) -> Deck:
        if self.paths:
            await self.say("reading " + ", ".join(p.name for p in self.paths[:2]))
            self.src = await asyncio.to_thread(read_source, self.paths)
            if self.src.chars > PLAN_CHARS:
                # Said, not left for them to find out: the last chapters are not in the deck.
                share = max(1, round(100 * PLAN_CHARS / self.src.chars))
                self.problems.append(
                    f"{', '.join(self.src.names)} is long: the slides are made from the first "
                    f"{share}% of it. Ask for a deck of one part to cover the rest."
                )
        title, subtitle, theme_said, slides = await self.plan()
        theme = design.pick_theme(self.task, theme_said)
        await self.say(f"writing {len(slides)} slides")
        todo = [s for s in slides if s.layout not in ("title", "section")]
        batches = [todo[i : i + BATCH] for i in range(0, len(todo), BATCH)]
        tasks = [asyncio.ensure_future(self.write(slides, b)) for b in batches]
        try:
            await asyncio.gather(*tasks)
        except BaseException:
            for t in tasks:
                t.cancel()
            raise
        # A figure the source doesn't have goes back once, then comes out.
        await self.say("checking every figure against the source")
        wrong = {s.n: bad for s in todo if s.state == "written" and (bad := self.strays(s))}
        if wrong:
            log.info("deck build: %d slides hold figures not in the source: %s", len(wrong), wrong)
            await asyncio.gather(
                *(
                    self.write(
                        slides,
                        [s],
                        f"Slide {s.n} as you wrote it holds figures that are not in the source: "
                        f"{', '.join(wrong[s.n])}. Write it again with only figures the source "
                        "has, or with none.",
                    )
                    for s in todo
                    if s.n in wrong
                )
            )
            for s in todo:
                if s.n in wrong and (bad := self.strays(s)):
                    self.strip(s, bad)
        deck = Deck(title=title, theme=theme.key, slides=slides, asked=slides_asked(self.task))
        deck.source = ", ".join(self.src.names)
        await self.say("laying out the slides")
        writer, path = await asyncio.to_thread(self.draw, deck, title, subtitle, theme)
        deck.path = path
        deck.cost_usd = self.cost
        for s in deck.kept:
            if s.notes:
                self.problems.append(f"Slide {s.n} ({s.title[:40]}): {'; '.join(s.notes)}.")
        deck.problems = list(dict.fromkeys([*self.problems, *writer.problems]))
        return deck

    def draw(self, deck: Deck, title: str, subtitle: str, theme: design.Theme) -> tuple[design.DeckWriter, Path]:
        """Draw, save, and go over the saved file. Blocking."""
        from pyxi.agents.pixel_common import unique_path

        writer = design.DeckWriter(theme, title)
        section = 0
        expected: dict[int, dict[str, Any]] = {}
        for s in deck.slides:
            if s.layout == "section":
                section += 1
            ready = self.ready(s, title, subtitle, section)
            if ready is None:
                s.state = "dropped"
                self.problems.append(f"Left out the slide \"{s.title[:60]}\": the source had nothing to put on it.")
                continue
            before = writer.n
            writer.add(ready)
            if writer.n == before:
                s.state = "dropped"
                continue
            s.n = writer.n
            s.title = ready.get("title") or s.title
            if ready.get("layout") == "chart" and writer.n in writer.charts:
                again, _ = self.chart_data(s, note=False)  # read from the source a second time
                expected[writer.n] = (again or {}).get("data") or {"categories": [], "series": []}
            elif s.layout == "chart":
                s.layout = "bullets"
        path = unique_path(self.folder, f"{_file_name(title)}.pptx")
        writer.save(path)
        found = design.audit(path, theme, writer.placed, writer.charts)
        # Every chart, read back from the file, against the source's own figures.
        for n, data in expected.items():
            drawn = writer.charts.get(n) or {}
            if drawn.get("categories") != data["categories"] or [v for _, v in drawn.get("series", [])] != [v for _, v in data["series"]]:
                found.problems.append(f"Slide {n}: the chart's figures are not the ones from the source.")
        self.problems.extend(found.problems)
        log.info(
            "deck build: %d slides, %d texts measured, %d charts checked, %d problems",
            found.slides, found.texts, found.charts, len(found.problems),
        )  # fmt: skip
        return writer, path


def _texts(value: Any) -> list[str]:
    """Every piece of text the model wrote for a slide (never the chart's table reference)."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [t for v in value for t in _texts(v)]
    if isinstance(value, dict):
        return [t for k, v in value.items() if k not in ("chart", "n", "picture") for t in _texts(v)]
    return []


def _has(text: str, raw: str) -> bool:
    return re.search(rf"(?<![\d.,]){re.escape(raw)}(?![\d])", text) is not None


def _file_name(title: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", title).strip(" .")[:80]
    if name.lower().endswith(".pptx"):
        name = name[:-5].rstrip(" .")
    return " ".join(name.split()) or "Presentation"


async def build(
    task: str,
    title: str,
    *,
    ask: Ask,
    sources: list[Path] | None = None,
    folder: Path,
    name: str = "Ledger",
    context: str = "",
    progress: Callable[[str], Awaitable[None]] | None = None,
) -> Deck:
    """Plan, write, check and save a deck. Raises :class:`DeckError` (with why) when no deck
    could be made; nothing is saved then."""
    return await Builder(
        task, title, ask=ask, sources=sources, folder=folder, name=name, context=context, progress=progress
    ).run()
