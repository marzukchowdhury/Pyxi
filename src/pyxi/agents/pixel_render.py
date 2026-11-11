"""Any document to PDF without Office or LibreOffice: Word, Excel, PowerPoint, Markdown, HTML and
plain text, rendered with reportlab.

Each format becomes a list of simple blocks (headings, paragraphs, list items, tables, code,
images, page breaks), and one renderer lays them out. Text uses a real TrueType font from
Windows (Arial, or Segoe UI) so bullets, accents and symbols come through as themselves;
Helvetica is the fallback elsewhere. LibreOffice, when installed, is still preferred for Office
files (exact layout); this is what keeps Pyxi working without it.
"""

from __future__ import annotations

import html
import io
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

BULLET = "\u2022"


@dataclass
class Block:
    kind: str  # h, p, li, table, pre, img, br
    text: str = ""  # reportlab mini-markup for h/p/li, raw text for pre
    level: int = 1  # heading level, or list depth
    number: int | None = None  # numbered list item
    rows: list[list[str]] = field(default_factory=list)
    image: bytes | None = None


# ---------------------------------------------------------------------------- fonts
_FONTS: tuple[str, str, str, str] | None = None


def fonts() -> tuple[str, str, str, str]:
    """(regular, bold, italic, bold italic) font names, registered once."""
    global _FONTS
    if _FONTS is not None:
        return _FONTS
    from reportlab.lib.fonts import addMapping
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    win = Path(r"C:\Windows\Fonts")
    for family, files in (
        ("PyxiArial", ("arial.ttf", "arialbd.ttf", "ariali.ttf", "arialbi.ttf")),
        ("PyxiSegoe", ("segoeui.ttf", "segoeuib.ttf", "segoeuii.ttf", "segoeuiz.ttf")),
    ):
        paths = [win / f for f in files]
        if all(p.is_file() for p in paths):
            try:
                names = [family, family + "-B", family + "-I", family + "-BI"]
                for name, path in zip(names, paths, strict=True):
                    pdfmetrics.registerFont(TTFont(name, str(path)))
                addMapping(family, 0, 0, names[0])
                addMapping(family, 1, 0, names[1])
                addMapping(family, 0, 1, names[2])
                addMapping(family, 1, 1, names[3])
                _FONTS = (names[0], names[1], names[2], names[3])
                return _FONTS
            except Exception:
                continue
    _FONTS = ("Helvetica", "Helvetica-Bold", "Helvetica-Oblique", "Helvetica-BoldOblique")
    return _FONTS


# Windows fonts for the scripts Arial and Segoe UI don't have (Chinese, Japanese, Korean,
# Indian scripts), so a name like 李雷 comes out as itself instead of empty boxes.
FALLBACK_FONTS = (
    ("PyxiYaHei", "msyh.ttc"),
    ("PyxiMalgun", "malgun.ttf"),
    ("PyxiYuGothic", "YuGothR.ttc"),
    ("PyxiNirmala", "Nirmala.ttc"),
)
_FALLBACKS: list[tuple[str, Any]] | None = None


def _glyphs(name: str) -> Any:
    from reportlab.pdfbase import pdfmetrics

    try:
        return pdfmetrics.getFont(name).face.charToGlyph
    except Exception:
        return None


def fallbacks() -> list[tuple[str, Any]]:
    """(font name, its characters) for each fallback font found on this PC, registered once."""
    global _FALLBACKS
    if _FALLBACKS is None:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont

        _FALLBACKS = []
        for name, file in FALLBACK_FONTS:
            path = Path(r"C:\Windows\Fonts") / file
            if not path.is_file():
                continue
            try:
                pdfmetrics.registerFont(TTFont(name, str(path), subfontIndex=0))
            except Exception:
                continue
            if (chars := _glyphs(name)) is not None:
                _FALLBACKS.append((name, chars))
    return _FALLBACKS


def font_for(text: str, preferred: str) -> str:
    """``preferred`` when it has every character of ``text``, else a fallback font that does."""
    have = _glyphs(preferred)
    if have is None or all(ord(c) in have or c.isspace() for c in text):
        return preferred
    for name, chars in fallbacks():
        if all(ord(c) in chars or c.isspace() for c in text):
            return name
    return preferred


def with_fallback(markup: str) -> str:
    """Paragraph markup with each run of characters the main font lacks set in a fallback font."""
    have = _glyphs(fonts()[0])
    if have is None or all(ord(c) in have or c.isspace() for c in markup):
        return markup
    out = []
    for piece in re.split(r"(<[^>]*>|&#?\w+;)", markup):
        if piece.startswith(("<", "&")):
            out.append(piece)
            continue
        run_font, run = None, ""
        for c in piece:
            font = None
            if not (ord(c) in have or c.isspace()):
                font = next((n for n, chars in fallbacks() if ord(c) in chars), None)
            if font != run_font and run:
                out.append(f'<font name="{run_font}">{run}</font>' if run_font else run)
                run = ""
            run_font, run = font, run + c
        if run:
            out.append(f'<font name="{run_font}">{run}</font>' if run_font else run)
    return "".join(out)


def _styles() -> dict[str, Any]:
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet

    regular, bold, _, _ = fonts()
    base = getSampleStyleSheet()
    out = {
        "p": ParagraphStyle(
            "p",
            parent=base["BodyText"],
            fontName=regular,
            fontSize=10.5,
            leading=14.5,
            spaceAfter=5,
        ),
        "pre": ParagraphStyle(
            "pre", parent=base["Code"], fontName="Courier", fontSize=8.5, leading=11
        ),
        "cell": ParagraphStyle(
            "cell", parent=base["BodyText"], fontName=regular, fontSize=9, leading=11.5
        ),
    }
    sizes = {1: 20, 2: 16, 3: 13.5, 4: 12, 5: 11, 6: 10.5}
    for level, size in sizes.items():
        out[f"h{level}"] = ParagraphStyle(
            f"h{level}", parent=base["Heading1"], fontName=bold, fontSize=size,
            leading=size * 1.25, spaceBefore=size * 0.6, spaceAfter=size * 0.35,
        )  # fmt: skip
    out["title"] = ParagraphStyle("title", parent=out["h1"], fontSize=24, leading=30)
    return out


def safe_markup(text: str) -> str:
    """Plain text, escaped for reportlab's paragraph markup."""
    return escape(text).replace("\n", "<br/>")


# ---------------------------------------------------------------------------- rendering
def render(
    blocks: list[Block], target: Path, *, title: str = "", landscape: bool = False, a4: bool = False
) -> int:
    """Lay the blocks out as a PDF. Returns the page count."""
    from reportlab.lib import colors
    from reportlab.lib import pagesizes as ps
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        Image,
        KeepTogether,
        PageBreak,
        Paragraph,
        Preformatted,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )
    from reportlab.platypus.doctemplate import LayoutError

    from pyxi.agents.pixel_common import PixelError

    size = ps.A4 if a4 else ps.letter
    if landscape:
        size = ps.landscape(size)
    margin = 0.75 * inch
    width = size[0] - 2 * margin
    st = _styles()
    regular, bold, _, _ = fonts()

    def lay_out(stacked: bool) -> list[Any]:
        """The blocks as reportlab flowables. ``stacked``: a table's cells one under the other
        as plain paragraphs, for a table that can't be made to fit a page."""
        story: list[Any] = []
        for b in blocks:
            if b.kind == "h":
                story.append(
                    Paragraph(
                        with_fallback(b.text),
                        st["title"] if b.level == 0 else st[f"h{min(max(b.level, 1), 6)}"],
                    )
                )
            elif b.kind == "p":
                story.append(Paragraph(with_fallback(b.text), st["p"]))
            elif b.kind == "li":
                marker = f"{b.number}." if b.number is not None else BULLET
                indent = 14 * max(1, b.level)
                # The bullet in the embedded font: reportlab's default (Helvetica) puts a glyph
                # there that copies and searches as \x7f instead of a bullet.
                style = st["p"].clone(
                    "li",
                    leftIndent=indent + 10,
                    bulletIndent=indent - 4,
                    spaceAfter=2,
                    bulletFontName=regular,
                )
                story.append(Paragraph(with_fallback(b.text), style, bulletText=marker))
            elif b.kind == "pre":
                story.append(Preformatted(b.text, st["pre"], maxLineLength=110))
                story.append(Spacer(1, 6))
            elif b.kind == "table" and b.rows and stacked:
                for r in b.rows:
                    story += [Paragraph(with_fallback(c), st["p"]) for c in r if c.strip()]
                story.append(Spacer(1, 8))
            elif b.kind == "table" and b.rows:
                cols = max(len(r) for r in b.rows)
                rows = [
                    [Paragraph(with_fallback(c), st["cell"]) for c in r] + [""] * (cols - len(r))
                    for r in b.rows
                ]
                # A row taller than a page (a resume laid out as one two-column table) is split
                # across pages; a one-row table has no heading row to repeat.
                t = Table(
                    rows,
                    repeatRows=1 if len(rows) > 1 else 0,
                    colWidths=[width / cols] * cols,
                    hAlign="LEFT",
                    splitInRow=1,
                )
                t.setStyle(
                    TableStyle(
                        [
                            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#b0b0b0")),
                            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eeeeee")),
                            ("FONTNAME", (0, 0), (-1, 0), bold),
                            ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ]
                    )
                )
                story += [t, Spacer(1, 8)]
            elif b.kind == "img" and b.image:
                try:
                    from PIL import Image as PILImage

                    with PILImage.open(io.BytesIO(b.image)) as im:
                        w, h = im.size
                    scale = min(1.0, width / w, (size[1] - 2 * margin) * 0.6 / h)
                    story.append(
                        KeepTogether(
                            [Image(io.BytesIO(b.image), w * scale, h * scale), Spacer(1, 6)]
                        )
                    )
                except Exception:
                    continue  # an image format reportlab can't place (EMF, WMF) is skipped
            elif b.kind == "br":
                story.append(PageBreak())
        return story or [Spacer(1, 1)]

    pages = {"n": 0}

    def count(canvas, doc) -> None:
        pages["n"] = doc.page

    def build(stacked: bool) -> None:
        doc = SimpleDocTemplate(
            str(target), pagesize=size, leftMargin=margin, rightMargin=margin,
            topMargin=margin, bottomMargin=margin, title=title, author="Pyxi",
        )  # fmt: skip
        doc.build(lay_out(stacked), onFirstPage=count, onLaterPages=count)

    try:
        build(False)
    except LayoutError:
        # Something still won't fit a page as a table: its words, at least, come through. The
        # user never sees reportlab's own "Flowable <Table@0x...> too large".
        try:
            build(True)
        except LayoutError:
            target.unlink(missing_ok=True)
            raise PixelError(
                "Part of this document is too big to fit on a page, so I couldn't lay it out "
                "as a PDF."
            ) from None
    return pages["n"]


# ---------------------------------------------------------------------------- Word
def docx_blocks(path: Path) -> tuple[list[Block], bool]:
    """Blocks in reading order, and whether the page is A4."""
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    from docx.text.run import Run

    from pyxi.agents.pixel_common import W, word_runs, word_text

    doc = Document(str(path))
    sec = doc.sections[0] if doc.sections else None
    a4 = bool(sec is not None and sec.page_width and abs(sec.page_width.inches - 8.27) < 0.1)
    out: list[Block] = []
    counters: dict[int, int] = {}
    current: dict[str, str] = {}  # which list the counters belong to
    try:
        numbering = doc.part.numbering_part.element
    except Exception:  # a document with no lists has no numbering part
        numbering = None
    formats: dict[tuple[str, str], str] = {}

    def list_format(num_id: str, level: str) -> str:
        """'bullet', 'decimal', 'lowerLetter'... for a list paragraph, '' when it can't be told."""
        key = (num_id, level)
        if key not in formats:
            found: list[str] = []
            if numbering is not None and num_id.isdigit() and level.isdigit():
                base = numbering.xpath(f"./w:num[@w:numId='{num_id}']/w:abstractNumId/@w:val")
                if base and str(base[0]).isdigit():
                    found = numbering.xpath(
                        f"./w:abstractNum[@w:abstractNumId='{base[0]}']"
                        f"/w:lvl[@w:ilvl='{level}']/w:numFmt/@w:val"
                    )
            formats[key] = str(found[0]) if found else ""
        return formats[key]

    def rich(p: Paragraph) -> str:
        parts = []
        # Every run, a link's included: ``p.runs`` skips those, and "Email: jane@example.com"
        # came out as "Email:".
        for r_el, _ in word_runs(p._p):
            r = Run(r_el, p)
            t = escape(r.text).replace("\n", "<br/>")
            if not t:
                continue
            if r.bold:
                t = f"<b>{t}</b>"
            if r.italic:
                t = f"<i>{t}</i>"
            if r.underline:
                t = f"<u>{t}</u>"
            parts.append(t)
        return "".join(parts) or escape(p.text)

    def cell_text(tc: Any) -> str:
        """All of a cell's words: its paragraphs, and those of a table or content control in it."""
        return "\n".join(word_text(p) for p in tc.iter(W + "p")).strip("\n")

    def images(p: Paragraph) -> list[bytes]:
        found = []
        for blip in p._p.xpath(".//a:blip"):
            rid = blip.get(
                "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed"
            )
            part = doc.part.related_parts.get(rid) if rid else None
            if part is not None and getattr(part, "blob", None):
                found.append(part.blob)
        return found

    def paragraph(child: Any) -> None:
        p = Paragraph(child, doc)
        style = (p.style.name if p.style is not None else "") or ""
        for blob in images(p):
            out.append(Block("img", image=blob))
        if child.xpath(".//w:br[@w:type='page']"):
            out.append(Block("br"))
        if not word_text(child).strip():
            return
        num_id = child.xpath("./w:pPr/w:numPr/w:numId/@w:val")
        listed = bool(num_id) and str(num_id[0]) != "0"  # numId 0 switches a list off
        m = re.match(r"Heading (\d)", style)
        if style == "Title":
            out.append(Block("h", rich(p), 0))
        elif m:
            out.append(Block("h", rich(p), int(m.group(1))))
        elif "List" in style or listed:
            depth = 1
            level = "0"
            if listed:
                ilvl = child.xpath("./w:pPr/w:numPr/w:ilvl/@w:val")
                level = str(ilvl[0]) if ilvl else "0"
                depth = int(level) + 1 if level.isdigit() else 1
            if m2 := re.search(r"(\d)$", style):
                depth = max(depth, int(m2.group(1)))
            # Numbered or bulleted is what the list itself says. Word's numbering button gives
            # the style "List Paragraph", with no "Number" in its name, and those lists came
            # out as bullets.
            kind = list_format(str(num_id[0]), level) if listed else ""
            numbered = kind not in ("bullet", "none") if kind else "Number" in style
            which = str(num_id[0]) if listed else re.sub(r"\s*\d$", "", style)
            if current.get("list") != which:  # another list right after this one starts at 1
                counters.clear()
                current["list"] = which
            n = None
            if numbered:
                counters[depth] = counters.get(depth, 0) + 1
                n = counters[depth]
            out.append(Block("li", rich(p), depth, n))
            return
        else:
            out.append(Block("p", rich(p)))
        counters.clear()

    def walk(container: Any) -> None:
        for child in container.iterchildren():
            tag = child.tag.rsplit("}", 1)[-1]
            if tag == "p":
                paragraph(child)
            elif tag == "sdt":
                # A content control (the boxes Word's resume and letter templates are made of):
                # what's inside it is part of the document, and used to be left out.
                for content in child.iterchildren(W + "sdtContent"):
                    walk(content)
            elif tag == "tbl":
                table = Table(child, doc)
                rows = []
                for row in table.rows:
                    cells, seen = [], set()
                    for c in row.cells:
                        if id(c._tc) in seen:  # merged cells repeat
                            continue
                        seen.add(id(c._tc))
                        cells.append(safe_markup(cell_text(c._tc)))
                    rows.append(cells)
                out.append(Block("table", rows=rows))
                counters.clear()

    walk(doc.element.body)
    return out, a4


# ---------------------------------------------------------------------------- Excel
def xlsx_blocks(path: Path, sheet: str = "") -> tuple[list[Block], bool]:
    from openpyxl import load_workbook

    wb = load_workbook(path, data_only=True, read_only=True)
    out: list[Block] = []
    wide = False
    try:
        names = [sheet] if sheet else wb.sheetnames
        for name in names:
            ws = wb[name]
            # Some exports say the sheet is one cell big (<dimension ref="A1"/>): believed, the
            # PDF had that one cell. Reading the rows themselves finds them all.
            ws.reset_dimensions()
            rows = []
            for row in ws.iter_rows():
                cells = ["" if c.value is None else _fmt(c.value, c.number_format) for c in row]
                while cells and cells[-1] == "":
                    cells.pop()
                rows.append(cells)
            while rows and not any(rows[-1]):
                rows.pop()
            while rows and not any(rows[0]):
                rows.pop(0)
            if not rows:
                continue
            width = max(len(r) for r in rows)
            wide = wide or width > 6
            if out:  # a new page per sheet, but no blank one in front when the first is empty
                out.append(Block("br"))
            if len(names) > 1:
                out.append(Block("h", safe_markup(name), 2))
            out.append(
                Block(
                    "table",
                    rows=[[safe_markup(c) for c in r] + [""] * (width - len(r)) for r in rows],
                )
            )
    finally:
        wb.close()
    return out, wide


def _fmt(v: Any, number_format: str = "General") -> str:
    """A cell's value the way the sheet shows it, near enough: a percent as a percent, the
    decimals its format asks for, and otherwise every digit it has. (Rounded to two decimals
    whatever the cell, a 3.25% rate printed as 0.03 and a 0.004 price as 0.)"""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        fmt = (number_format or "General").split(";")[0]  # the part for positive numbers
        shape = re.sub(r'"[^"]*"|\[[^\]]*\]|\\.', "", fmt)  # without quoted text and [$-409]
        places = len(m.group(1)) if (m := re.search(r"\.(0+)", shape)) else 0
        if "%" in shape:
            return f"{v * 100:.{places}f}%"
        if re.search(r"[0#]", shape) and not re.search(r"(?i)e\+|/|general", shape):
            symbol = next((s for s in "$£€¥" if s in re.sub(r"\[\$-[^\]]*\]", "", fmt)), "")
            number = f"{abs(v):,.{places}f}" if "," in shape else f"{abs(v):.{places}f}"
            return ("-" if v < 0 else "") + symbol + number
    if isinstance(v, float):
        if v.is_integer() and abs(v) >= 1e15:
            return f"{v:.0f}"  # a long account number, not 1.2e+16
        return f"{v:,.15g}"
    if hasattr(v, "strftime"):
        return v.strftime("%Y-%m-%d") if not getattr(v, "hour", 0) else v.strftime("%Y-%m-%d %H:%M")
    return str(v)


# ---------------------------------------------------------------------------- PowerPoint
def pptx_blocks(path: Path) -> list[Block]:
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    prs = Presentation(str(path))
    out: list[Block] = []
    for i, slide in enumerate(prs.slides):
        if i:
            out.append(Block("br"))
        title = slide.shapes.title
        out.append(
            Block(
                "h",
                safe_markup(
                    title.text_frame.text
                    if title is not None and title.has_text_frame
                    else f"Slide {i + 1}"
                ),
                1,
            )
        )
        shapes = sorted(slide.shapes, key=lambda s: (s.top or 0, s.left or 0))
        for shape in shapes:
            if title is not None and shape.shape_id == title.shape_id:
                continue
            if shape.has_text_frame:
                for para in shape.text_frame.paragraphs:
                    text = "".join(r.text for r in para.runs).strip()
                    if not text:
                        continue
                    is_body = shape.is_placeholder and shape.placeholder_format.idx not in (0,)
                    if is_body or para.level:
                        out.append(Block("li", safe_markup(text), para.level + 1))
                    else:
                        out.append(Block("p", safe_markup(text)))
            elif getattr(shape, "has_table", False) and shape.has_table:
                rows = [[safe_markup(c.text) for c in r.cells] for r in shape.table.rows]
                out.append(Block("table", rows=rows))
            elif shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
                try:
                    out.append(Block("img", image=shape.image.blob))
                except Exception:
                    pass
        if slide.has_notes_slide and slide.notes_slide.notes_text_frame.text.strip():
            out.append(
                Block(
                    "p",
                    "<i>Notes: "
                    + safe_markup(slide.notes_slide.notes_text_frame.text.strip())
                    + "</i>",
                )
            )
    return out


# ---------------------------------------------------------------------------- Markdown
def _md_inline(text: str) -> str:
    t = escape(text)
    t = re.sub(r"`([^`]+)`", r'<font face="Courier">\1</font>', t)
    t = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: f"<b>{m.group(1) or m.group(2)}</b>", t)
    t = re.sub(
        r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?!\w)|(?<!\w)_(?!\s)(.+?)(?<!\s)_(?!\w)",
        lambda m: f"<i>{m.group(1) or m.group(2)}</i>",
        t,
    )
    t = re.sub(r"~~(.+?)~~", r"<strike>\1</strike>", t)
    t = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"[\1]", t)
    t = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", r'<link href="\2" color="blue">\1</link>', t)
    return t


def markdown_blocks(text: str) -> list[Block]:
    out: list[Block] = []
    lines = text.replace("\r\n", "\n").split("\n")
    para: list[str] = []
    i = 0

    def flush() -> None:
        if para:
            out.append(Block("p", _md_inline(" ".join(s.strip() for s in para))))
            para.clear()

    while i < len(lines):
        line = lines[i]
        s = line.strip()
        if s.startswith("```"):
            flush()
            code = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            out.append(Block("pre", "\n".join(code)))
        elif m := re.match(r"(#{1,6})\s+(.*?)\s*#*$", s):
            flush()
            out.append(Block("h", _md_inline(m.group(2)), len(m.group(1))))
        elif re.match(r"^(-{3,}|\*{3,}|_{3,})$", s):
            flush()
        elif (
            s.startswith("|")
            and i + 1 < len(lines)
            and re.match(r"^\|?\s*:?-{2,}", lines[i + 1].strip())
        ):
            flush()
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                if not re.match(r"^\|?\s*:?-{2,}", lines[i].strip()):
                    rows.append(
                        [_md_inline(c.strip()) for c in lines[i].strip().strip("|").split("|")]
                    )
                i += 1
            out.append(Block("table", rows=rows))
            continue
        elif m := re.match(r"^(\s*)([-*+]|\d+[.)])\s+(\[[ xX]\]\s+)?(.*)$", line):
            flush()
            depth = len(m.group(1).replace("\t", "    ")) // 2 + 1
            box = m.group(3)
            item = (("\u2611 " if "x" in box.lower() else "\u2610 ") if box else "") + m.group(4)
            number = int(re.match(r"\d+", m.group(2)).group(0)) if m.group(2)[0].isdigit() else None
            out.append(Block("li", _md_inline(item), min(depth, 4), number))
        elif s.startswith(">"):
            flush()
            out.append(Block("p", "<i>" + _md_inline(s.lstrip("> ")) + "</i>"))
        elif not s:
            flush()
        else:
            para.append(line)
        i += 1
    flush()
    return out


# ---------------------------------------------------------------------------- HTML
class _HTML(HTMLParser):
    BLOCK = {"p", "div", "section", "article", "header", "footer", "main", "blockquote", "figure"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[Block] = []
        self.buf: list[str] = []
        self.kind = "p"
        self.level = 1
        self.lists: list[list[int | None]] = []  # [counter or None] per open list
        self.skip = 0
        self.pre = False
        self.table: list[list[str]] | None = None
        self.cell: list[str] | None = None

    def _flush(self) -> None:
        text = "".join(self.buf).strip()
        self.buf = []
        if not text:
            return
        if self.kind == "li" and self.lists:
            counter = self.lists[-1]
            n = None
            if counter[0] is not None:
                counter[0] += 1
                n = counter[0]
            self.blocks.append(Block("li", re.sub(r"\s+", " ", text), len(self.lists), n))
        elif self.kind == "h":
            self.blocks.append(Block("h", re.sub(r"\s+", " ", text), self.level))
        elif self.kind == "pre":
            self.blocks.append(Block("pre", html.unescape(re.sub(r"<[^>]+>", "", text))))
        else:
            self.blocks.append(Block("p", re.sub(r"\s+", " ", text)))
        self.kind = "p"

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style", "head", "noscript", "svg"):
            self.skip += 1
            return
        if self.skip:
            return
        if self.cell is not None:
            if tag == "br":
                self.cell.append("<br/>")
            return
        if re.fullmatch(r"h[1-6]", tag):
            self._flush()
            self.kind, self.level = "h", int(tag[1])
        elif tag in ("ul", "ol"):
            self._flush()
            self.lists.append([0 if tag == "ol" else None])
        elif tag == "li":
            self._flush()
            self.kind = "li"
        elif tag == "pre":
            self._flush()
            self.kind, self.pre = "pre", True
        elif tag in self.BLOCK:
            self._flush()
        elif tag == "br":
            self.buf.append("\n" if self.pre else "<br/>")
        elif tag in ("b", "strong"):
            self.buf.append("<b>")
        elif tag in ("i", "em"):
            self.buf.append("<i>")
        elif tag == "u":
            self.buf.append("<u>")
        elif tag == "table":
            self._flush()
            self.table = []
        elif tag == "tr" and self.table is not None:
            self.table.append([])
        elif tag in ("td", "th") and self.table is not None:
            self.cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "head", "noscript", "svg"):
            self.skip = max(0, self.skip - 1)
            return
        if self.skip:
            return
        if tag in ("td", "th") and self.cell is not None and self.table is not None:
            if not self.table:
                self.table.append([])
            self.table[-1].append(re.sub(r"\s+", " ", "".join(self.cell)).strip())
            self.cell = None
        elif tag == "table" and self.table is not None:
            rows = [r for r in self.table if r]
            if rows:
                self.blocks.append(Block("table", rows=rows))
            self.table = None
        elif self.cell is not None:
            return
        elif tag in ("b", "strong"):
            self.buf.append("</b>")
        elif tag in ("i", "em"):
            self.buf.append("</i>")
        elif tag == "u":
            self.buf.append("</u>")
        elif re.fullmatch(r"h[1-6]", tag) or tag == "li" or tag in self.BLOCK:
            self._flush()
        elif tag == "pre":
            self._flush()
            self.pre = False
        elif tag in ("ul", "ol") and self.lists:
            self._flush()
            self.lists.pop()

    def handle_data(self, data: str) -> None:
        if self.skip:
            return
        text = escape(data)
        if self.cell is not None:
            self.cell.append(text)
        else:
            self.buf.append(text if self.pre else re.sub(r"\s+", " ", text))

    def close(self) -> None:
        super().close()
        self._flush()


def html_blocks(text: str) -> list[Block]:
    parser = _HTML()
    parser.feed(text)
    parser.close()
    # Unbalanced inline tags would break reportlab's markup: drop any block that won't parse.
    return [b for b in parser.blocks if b.kind not in ("p", "h", "li") or _balanced(b.text)]


def _balanced(markup: str) -> bool:
    stack = []
    for close, tag in re.findall(r"<(/?)(b|i|u)>", markup):
        if not close:
            stack.append(tag)
        elif not stack or stack.pop() != tag:
            return False
    return not stack


# ---------------------------------------------------------------------------- text
def text_blocks(text: str) -> list[Block]:
    out = []
    for chunk in re.split(r"\n\s*\n", text.replace("\r\n", "\n")):
        if chunk.strip():
            out.append(Block("p", safe_markup(chunk.strip("\n"))))
    return out


def read_any_text(path: Path) -> str:
    """A text file's words whatever it was saved as: UTF-8, UTF-16 (Notepad's "Unicode"), or
    Windows-1252 (Notepad's "ANSI", and what Excel writes as "CSV (Comma delimited)")."""
    raw = path.read_bytes()
    # UTF-16 only when the file says so. Tried on every file that wasn't UTF-8, it "read" any
    # Windows-1252 file with an even number of bytes as Chinese: a CSV with one é in it came
    # out as a workbook of one cell of nonsense, and the job said "Made it".
    sample = raw[:4096]
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        utf16 = "utf-16"
    elif sample.count(0) > len(sample) // 4:  # no mark, but every other byte is empty
        utf16 = "utf-16-le" if sample[1::2].count(0) >= sample[0::2].count(0) else "utf-16-be"
    else:
        utf16 = ""
    for enc in (utf16, "utf-8-sig"):
        if not enc:
            continue
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("cp1252", errors="replace")


def to_pdf(src: Path, target: Path, *, sheet: str = "") -> int:
    """Render one document with the built-in renderer. Returns the page count."""
    ext = src.suffix.lower()
    if ext == ".docx":
        blocks, a4 = docx_blocks(src)
        return render(blocks, target, title=src.stem, a4=a4)
    if ext in (".xlsx", ".xlsm"):
        blocks, wide = xlsx_blocks(src, sheet)
        return render(blocks, target, title=src.stem, landscape=wide)
    if ext == ".pptx":
        return render(pptx_blocks(src), target, title=src.stem, landscape=True)
    if ext in (".md", ".markdown"):
        return render(markdown_blocks(read_any_text(src)), target, title=src.stem)
    if ext in (".html", ".htm"):
        return render(html_blocks(read_any_text(src)), target, title=src.stem)
    if ext == ".csv":
        import csv as _csv

        text = read_any_text(src)
        try:
            dialect = _csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
        except _csv.Error:
            dialect = _csv.excel
        rows = [[safe_markup(c) for c in r] for r in _csv.reader(io.StringIO(text), dialect) if r]
        wide = bool(rows) and max(len(r) for r in rows) > 6
        return render([Block("table", rows=rows)], target, title=src.stem, landscape=wide)
    if ext in (".txt", ".text", ".log"):
        return render(text_blocks(read_any_text(src)), target, title=src.stem)
    raise ValueError(f"I can't turn {ext} files into PDF without LibreOffice.")
