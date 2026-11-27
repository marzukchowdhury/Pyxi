"""How a deck Pyxi builds looks: its themes, its grid, its layouts, and text that fits.

A deck made by dropping text into PowerPoint's default template looks like one. This file is
the small design system the deck builder (deck_build.py) draws with instead: five themes (a
palette of six colours and two fonts each), one grid for every slide (the same margins, the
title always in the same place, a footer line), and ten layouts drawn as real, editable
PowerPoint objects: text boxes, shapes and native charts, never pictures of them.

Nothing here calls a model. Text is measured with the real font files before it is placed
(:func:`measure`), so a box is given the largest size at which its words fit, and what will
not fit at the smallest size is cut and reported, never left to run off the slide. The same
measuring is what the builder's last check uses.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

W, H = 13.333, 7.5  # inches: a 16:9 slide
MX = 0.75  # left and right margin
CW = W - 2 * MX  # the width everything sits in
TITLE_Y, TITLE_H = 0.55, 1.05
BODY_Y, BODY_B = 1.9, 6.5  # where a slide's body starts and ends
FOOT_Y = 6.92
LINE = 1.0  # line spacing, in lines of the font
LAYOUTS = (
    "title", "section", "statement", "bullets", "two_columns", "picture", "comparison",
    "timeline", "big_number", "chart", "closing",
)  # fmt: skip


@dataclass(frozen=True)
class Theme:
    key: str
    name: str
    suits: str  # for the planner: what it is for
    bg: str
    surface: str  # cards and panels
    text: str
    muted: str
    accent: str
    accent2: str
    on_accent: str  # text on an accent block
    head: str  # heading font
    body: str  # body font
    dark: bool = False
    words: tuple[str, ...] = ()  # words in a request that pick it

    @property
    def colours(self) -> set[str]:
        return {
            c.upper()
            for c in (self.bg, self.surface, self.text, self.muted, self.accent, self.accent2, self.on_accent)
        }

    @property
    def fonts(self) -> set[str]:
        return {self.head, self.body}

    @property
    def series(self) -> list[str]:
        return [self.accent, self.accent2, self.muted, self.text]


THEMES: dict[str, Theme] = {
    t.key: t
    for t in (
        Theme(
            "slate", "Slate", "business, finance, technology, reports to a board",
            "0F1B2D", "1B2B44", "F4F6FA", "9FB0C8", "4DA3FF", "FFB454", "0F1B2D",
            "Segoe UI Semibold", "Segoe UI", True,
            ("dark", "navy", "corporate", "board", "finance", "investor", "quarter", "revenue", "software", "security"),
        ),
        Theme(
            "paper", "Paper", "reports, research, history, books, anything to be read closely",
            "FAF7F2", "F0EAE0", "22201C", "6F6A62", "B4532A", "2F5D62", "FFFFFF",
            "Georgia", "Segoe UI", False,
            ("report", "research", "study", "history", "essay", "thesis", "literature", "editorial", "classic", "warm"),
        ),
        Theme(
            "signal", "Signal", "pitches, launches, marketing, sales",
            "FFFFFF", "F3F3F3", "111111", "666666", "E5383B", "1F1F1F", "FFFFFF",
            "Segoe UI Black", "Segoe UI", False,
            ("pitch", "startup", "launch", "marketing", "sales", "bold", "campaign", "brand", "product"),
        ),
        Theme(
            "forest", "Forest", "health, nature, education, community, food",
            "F3F7F2", "E2ECDF", "17301F", "5C7263", "2E7D4F", "C98A1B", "FFFFFF",
            "Trebuchet MS", "Segoe UI", False,
            ("health", "nature", "environment", "climate", "school", "class", "lesson", "garden", "food", "wellbeing", "green"),
        ),
        Theme(
            "dusk", "Dusk", "creative work, design, events, a talk on a stage",
            "1B1530", "2A2146", "F3EEFF", "A99CC9", "B58CFF", "FF7AA8", "1B1530",
            "Segoe UI Light", "Segoe UI", True,
            ("creative", "design", "event", "talk", "conference", "keynote", "music", "film", "game", "purple", "night"),
        ),
    )
}
DEFAULT_THEME = "paper"


def pick_theme(task: str, said: str = "") -> Theme:
    """The theme for a deck: the one the user named, else the one the planner chose, else the
    one whose words the request uses most."""
    t = task.lower()
    for theme in THEMES.values():
        if re.search(rf"\b{theme.key}\b\s+(theme|style|look)|\b(theme|style|look)\W+{theme.key}\b", t):
            return theme
    if re.search(r"\b(dark|black)\s+(theme|mode|background|look|style)", t):
        return THEMES["slate"]
    if re.search(r"\b(light|white|clean|minimal\w*)\s+(theme|background|look|style)", t):
        return THEMES["signal"]
    if said.strip().lower() in THEMES:
        return THEMES[said.strip().lower()]
    score = {k: sum(1 for w in th.words if re.search(rf"\b{w}", t)) for k, th in THEMES.items()}
    best = max(score, key=lambda k: score[k])
    return THEMES[best if score[best] else DEFAULT_THEME]


# ---------------------------------------------------------------------------- measuring text
_FONT_FILES = {
    "segoe ui": "segoeui.ttf",
    "segoe ui semibold": "seguisb.ttf",
    "segoe ui black": "seguibl.ttf",
    "segoe ui light": "segoeuil.ttf",
    "georgia": "georgia.ttf",
    "trebuchet ms": "trebuc.ttf",
    "arial": "arial.ttf",
    "calibri": "calibri.ttf",
}
_BOLD_FILES = {"segoe ui": "segoeuib.ttf", "georgia": "georgiab.ttf", "trebuchet ms": "trebucbd.ttf"}
_UNIT = 200  # the size fonts are loaded at; widths scale from it


@lru_cache(maxsize=32)
def _font(name: str, bold: bool) -> Any:
    try:
        from PIL import ImageFont

        folder = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
        key = name.lower()
        file = (_BOLD_FILES.get(key) if bold else None) or _FONT_FILES.get(key)
        if file and (folder / file).is_file():
            return ImageFont.truetype(str(folder / file), _UNIT)
    except Exception:
        pass
    return None


def text_width(text: str, font: str, size: float, bold: bool = False) -> float:
    """How wide these words are, in inches, at ``size`` points."""
    f = _font(font, bold)
    if f is None:  # no font file to measure with: an average letter, on the safe side
        return len(text) * size * (0.6 if bold else 0.56) / 72
    return f.getlength(text) / _UNIT * size / 72 * (1.03 if bold and font.lower() not in _BOLD_FILES else 1.0)


def wrap(text: str, font: str, size: float, width: float, bold: bool = False) -> list[str]:
    """The lines these words break into in a box ``width`` inches wide."""
    out: list[str] = []
    for part in str(text).split("\n"):
        line = ""
        for word in part.split():
            trial = f"{line} {word}".strip()
            if line and text_width(trial, font, size, bold) > width:
                out.append(line)
                line = word
            else:
                line = trial
        out.append(line)
    return out or [""]


def measure(paras: list[str], font: str, size: float, width: float, *, bold: bool = False, gap: float = 0.0) -> float:
    """The height, in inches, of these paragraphs set at ``size`` in a box ``width`` wide, with
    ``gap`` (in lines) between paragraphs."""
    lines = sum(len(wrap(p, font, size, width, bold)) for p in paras)
    return (lines + max(0, len(paras) - 1) * gap) * LINE * line_height(font, bold) * size / 72


def line_height(font: str, bold: bool = False) -> float:
    """A line of this font, in ems: PowerPoint's "single" spacing is the font's own line (a
    third taller than the letters in Segoe UI), not the point size."""
    f = _font(font, bold)
    if f is None:
        return 1.3
    ascent, descent = f.getmetrics()
    return max(1.0, (ascent + descent) / _UNIT)


def widest_word(paras: list[str], font: str, size: float, bold: bool = False) -> float:
    return max((text_width(w, font, size, bold) for p in paras for w in p.split()), default=0.0)


def fit(paras: list[str], font: str, width: float, height: float, big: float, small: float, *, bold: bool = False, gap: float = 0.0) -> float | None:
    """The largest size from ``big`` down to ``small`` at which the paragraphs fit the box, or
    None when they don't fit even at ``small``."""
    size = float(big)
    while size >= small - 0.01:
        if measure(paras, font, size, width, bold=bold, gap=gap) <= height and widest_word(paras, font, size, bold) <= width:
            return size
        size -= 1.0 if size > 20 else 0.5
    return None


def cut_to_fit(paras: list[str], font: str, size: float, width: float, height: float, *, bold: bool = False, gap: float = 0.0) -> list[str]:
    """The paragraphs with words taken off the end until they fit, the cut marked with dots."""
    paras = list(paras)
    while paras and measure(paras, font, size, width, bold=bold, gap=gap) > height:
        words = paras[-1].rstrip(" .…").split()
        if len(words) <= 3:
            if len(paras) == 1:
                break
            paras.pop()
            continue
        paras[-1] = " ".join(words[:-2]).rstrip(" ,;:") + "…"
    return paras


# ---------------------------------------------------------------------------- drawing
@dataclass
class Placed:
    """One piece of text as it was put on a slide: what the last check measures again."""

    slide: int
    what: str
    paras: list[str]
    font: str
    size: float
    width: float
    height: float
    bold: bool = False
    gap: float = 0.0
    cut: bool = False


def _rgb(hex6: str) -> Any:
    from pptx.dml.color import RGBColor

    return RGBColor.from_string(hex6.upper())


def _clean(text: Any) -> str:
    """Words for a slide: no markdown marks, no em dashes, one line."""
    t = " ".join(str(text if text is not None else "").split())
    t = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: m.group(1) or m.group(2), t)
    t = re.sub(r"^\s*(?:[-*•]|\d{1,2}[.)])\s+", "", t)
    t = re.sub(r"\s*—\s*|\s+–\s+", ", ", t)
    return t.strip().strip("`")


class DeckWriter:
    """Draws slides into a new presentation. Every slide has a real title (PowerPoint's own
    title placeholder, so the outline and a screen reader have it), a background, a footer and
    speaker notes."""

    def __init__(self, theme: Theme, title: str) -> None:
        from pptx import Presentation
        from pptx.util import Inches

        self.theme = theme
        self.title = _clean(title)
        self.prs = Presentation()
        self.prs.slide_width, self.prs.slide_height = Inches(W), Inches(H)
        self.prs.core_properties.title = self.title
        self.prs.core_properties.author = "Pyxi"
        self.placed: list[Placed] = []
        self.charts: dict[int, dict[str, Any]] = {}  # slide number: the data its chart was given
        self.problems: list[str] = []
        self.n = 0

    # ------------------------------------------------------------------ pieces
    def _slide(self, bg: str | None = None) -> Any:
        slide = self.prs.slides.add_slide(self.prs.slide_layouts[5])  # Title Only
        fill = slide.background.fill
        fill.solid()
        fill.fore_color.rgb = _rgb(bg or self.theme.bg)
        self.n += 1
        return slide

    def _box(self, slide: Any, x: float, y: float, w: float, h: float, colour: str, *, shape: str = "rect", alpha: float | None = None, radius: float = 0.06) -> Any:
        from pptx.enum.shapes import MSO_SHAPE
        from pptx.oxml.ns import qn
        from pptx.oxml.xmlchemy import OxmlElement
        from pptx.util import Inches

        kind = {"rect": MSO_SHAPE.RECTANGLE, "round": MSO_SHAPE.ROUNDED_RECTANGLE, "oval": MSO_SHAPE.OVAL}[shape]
        s = slide.shapes.add_shape(kind, Inches(x), Inches(y), Inches(w), Inches(h))
        s.fill.solid()
        s.fill.fore_color.rgb = _rgb(colour)
        s.line.fill.background()
        # Flat: the template's shape style brings a drop shadow with it.
        style = s._element.find(qn("p:style"))
        if style is not None:
            s._element.remove(style)
        if shape == "round":
            s.adjustments[0] = radius
        if alpha is not None:
            clr = s.fill._xPr.find(qn("a:solidFill"))[0]
            a = OxmlElement("a:alpha")
            a.set("val", str(int(alpha * 100000)))
            clr.append(a)
        return s

    def _text(
        self,
        slide: Any,
        what: str,
        paras: list[str],
        x: float,
        y: float,
        w: float,
        h: float,
        *,
        big: float,
        small: float,
        colour: str | None = None,
        font: str | None = None,
        bold: bool = False,
        align: str = "left",
        anchor: str = "top",
        bullets: bool = False,
        gap: float = 0.0,
        frame: Any = None,
        shrink_box: bool = False,
    ) -> float:
        """Text in a box at the largest size that fits. Returns the size used."""
        from pptx.enum.text import MSO_ANCHOR, MSO_AUTO_SIZE, PP_ALIGN
        from pptx.util import Inches, Pt

        font = font or self.theme.body
        paras = [p for p in (_clean(p) for p in paras) if p]
        if not paras:
            return 0.0
        inner = w - (0.3 if bullets else 0.0)
        size = fit(paras, font, inner, h, big, small, bold=bold, gap=gap)
        cut = False
        if size is None:
            size = small
            kept = cut_to_fit(paras, font, size, inner, h, bold=bold, gap=gap)
            cut = kept != paras
            paras = kept
            if cut:
                self.problems.append(f"Slide {self.n}: the {what} was too long for its box, so its end was cut.")
        if frame is None:
            shape = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
        else:
            shape = frame
            shape.left, shape.top, shape.width, shape.height = Inches(x), Inches(y), Inches(w), Inches(h)
        tf = shape.text_frame
        tf.word_wrap = True
        tf.auto_size = MSO_AUTO_SIZE.NONE
        tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
        tf.vertical_anchor = {"top": MSO_ANCHOR.TOP, "middle": MSO_ANCHOR.MIDDLE, "bottom": MSO_ANCHOR.BOTTOM}[anchor]
        for i, text in enumerate(paras):
            p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            for r in list(p.runs):
                r._r.getparent().remove(r._r)
            p.alignment = {"left": PP_ALIGN.LEFT, "center": PP_ALIGN.CENTER, "right": PP_ALIGN.RIGHT}[align]
            p.line_spacing = LINE
            p.space_before = Pt(0)
            p.space_after = Pt(size * LINE * line_height(font, bold) * gap) if i < len(paras) - 1 else Pt(0)
            if bullets:
                self._bullet(p, size)
            else:
                self._no_bullet(p)
            run = p.add_run()
            run.text = text
            run.font.name = font
            run.font.size = Pt(size)
            run.font.bold = bold
            run.font.color.rgb = _rgb(colour or self.theme.text)
        self.placed.append(Placed(self.n, what, paras, font, size, inner, h, bold, gap, cut))
        return size

    def _bullet(self, p: Any, size: float) -> None:
        from pptx.oxml.xmlchemy import OxmlElement
        from pptx.util import Inches

        pPr = p._p.get_or_add_pPr()
        pPr.set("marL", str(Inches(0.3)))
        pPr.set("indent", str(-Inches(0.3)))
        for tag, attrs in (
            ("a:buClr", None),
            ("a:buFont", {"typeface": "Arial"}),
            ("a:buChar", {"char": "•"}),
        ):
            el = OxmlElement(tag)
            if tag == "a:buClr":
                clr = OxmlElement("a:srgbClr")
                clr.set("val", self.theme.accent)
                el.append(clr)
            for k, v in (attrs or {}).items():
                el.set(k, v)
            pPr.append(el)

    def _no_bullet(self, p: Any) -> None:
        from pptx.oxml.xmlchemy import OxmlElement

        pPr = p._p.get_or_add_pPr()
        pPr.set("marL", "0")
        pPr.set("indent", "0")
        pPr.append(OxmlElement("a:buNone"))

    def _title(self, slide: Any, text: str, *, x: float = MX, y: float = TITLE_Y, w: float = CW, h: float = TITLE_H, big: float = 34, small: float = 22, colour: str | None = None, anchor: str = "bottom", align: str = "left") -> float:
        return self._text(
            slide, "title", [text or self.title], x, y, w, h, big=big, small=small,
            colour=colour or self.theme.text, font=self.theme.head, anchor=anchor, align=align,
            frame=slide.shapes.title,
        )  # fmt: skip

    def _chrome(self, slide: Any, *, on: str | None = None, rule: bool = True) -> None:
        """What every content slide carries: a short accent rule over the title, and the deck's
        name and the slide's number along the bottom."""
        colour = on or self.theme.muted
        if rule:
            self._box(slide, MX, 0.42, 0.9, 0.07, self.theme.accent)
        self._text(slide, "footer", [self.title], MX, FOOT_Y, CW - 1.2, 0.28, big=10, small=8, colour=colour)
        self._text(slide, "slide number", [str(self.n)], W - MX - 1.0, FOOT_Y, 1.0, 0.28, big=10, small=8, colour=colour, align="right")

    def _notes(self, slide: Any, notes: str) -> None:
        slide.notes_slide.notes_text_frame.text = _clean(notes) or "Notes for this slide."

    # ------------------------------------------------------------------ layouts
    def add(self, s: dict[str, Any]) -> None:
        layout = str(s.get("layout") or "bullets")
        draw = getattr(self, f"_draw_{layout}", None) or self._draw_bullets
        before = len(self.prs.slides)
        try:
            slide = draw(s)
        except Exception as exc:  # one slide's trouble is that slide's: it is drawn plainly
            if len(self.prs.slides) > before:
                self._drop_last()
            log.warning("deck: the %s layout failed on slide %d: %s", layout, self.n + 1, exc)
            self.problems.append(f"Slide {self.n + 1}: the {layout.replace('_', ' ')} layout didn't fit what was on it, so it is a plain slide. Ask me to redo that slide if you want the layout.")
            slide = self._draw_bullets({**s, "layout": "bullets", "bullets": s.get("bullets") or [s.get("point") or ""]})
        self._notes(slide, str(s.get("notes") or s.get("point") or s.get("title") or ""))

    def _drop_last(self) -> None:
        ids = self.prs.slides._sldIdLst
        last = ids[-1]
        self.prs.part.drop_rel(last.rId)
        ids.remove(last)
        self.placed = [p for p in self.placed if p.slide != self.n]
        self.charts.pop(self.n, None)
        self.n -= 1

    def _draw_title(self, s: dict[str, Any]) -> Any:
        t = self.theme
        slide = self._slide()
        # A block of colour down the right, with two circles cut into it.
        self._box(slide, W - 4.4, 0, 4.4, H, t.accent)
        self._box(slide, W - 3.6, 1.2, 4.8, 4.8, t.on_accent, shape="oval", alpha=0.12)
        self._box(slide, W - 5.3, 4.6, 2.4, 2.4, t.accent2, shape="oval")
        self._box(slide, MX, 2.35, 1.3, 0.09, t.accent)
        self._title(slide, s.get("title") or self.title, y=2.6, w=7.4, h=2.1, big=50, small=28, anchor="top")
        if s.get("subtitle"):
            self._text(slide, "subtitle", [s["subtitle"]], MX, 4.9, 7.4, 1.0, big=20, small=14, colour=t.muted)
        return slide

    def _draw_section(self, s: dict[str, Any]) -> Any:
        t = self.theme
        slide = self._slide(t.accent)
        self._box(slide, W - 5.2, -1.4, 6.6, 6.6, t.on_accent, shape="oval", alpha=0.10)
        if s.get("number"):
            self._text(slide, "section number", [str(s["number"]).zfill(2)], MX, 1.5, 3.0, 1.3, big=66, small=40, colour=t.on_accent, font=t.head)
        self._box(slide, MX, 3.05, 1.3, 0.09, t.on_accent)
        self._title(slide, s.get("title") or "", y=3.3, w=9.0, h=1.6, big=44, small=26, colour=t.on_accent, anchor="top")
        if s.get("subtitle") or s.get("point"):
            self._text(slide, "section line", [s.get("subtitle") or s.get("point")], MX, 5.0, 9.0, 0.9, big=18, small=13, colour=t.on_accent)
        return slide

    def _draw_statement(self, s: dict[str, Any]) -> Any:
        t = self.theme
        slide = self._slide()
        self._box(slide, MX, 1.7, 0.12, 3.2, t.accent)
        self._title(slide, s.get("statement") or s.get("title") or "", x=MX + 0.5, y=1.7, w=CW - 1.6, h=3.2, big=44, small=24, anchor="middle")
        if s.get("support"):
            self._text(slide, "supporting line", [s["support"]], MX + 0.5, 5.15, CW - 1.6, 0.9, big=18, small=13, colour=t.muted)
        self._chrome(slide, rule=False)
        return slide

    def _draw_bullets(self, s: dict[str, Any]) -> Any:
        t = self.theme
        slide = self._slide()
        self._title(slide, s.get("title") or "")
        items = [_clean(b) for b in (s.get("bullets") or []) if _clean(b)][:6]
        if 2 <= len(items) <= 4 and all(len(b.split()) <= 16 for b in items):
            # A few short points: a card each, numbered, side by side.
            gap = 0.3
            cw = (CW - gap * (len(items) - 1)) / len(items)
            sizes = [fit([b], t.body, cw - 0.7, 2.3, 26, 13) or 13 for b in items]
            for i, b in enumerate(items):
                x = MX + i * (cw + gap)
                self._box(slide, x, BODY_Y + 0.3, cw, 3.8, t.surface, shape="round")
                self._box(slide, x + 0.35, BODY_Y + 0.3, 0.9, 0.09, t.accent if i % 2 == 0 else t.accent2)
                self._text(slide, f"number of point {i + 1}", [f"{i + 1:02d}"], x + 0.35, BODY_Y + 0.6, cw - 0.7, 0.75, big=30, small=20, colour=t.accent, font=t.head)
                self._text(slide, f"point {i + 1}", [b], x + 0.35, BODY_Y + 1.5, cw - 0.7, 2.3, big=min(sizes), small=13)
        else:
            self._box(slide, MX, BODY_Y + 0.15, 0.07, 4.2, t.accent)
            self._text(slide, "points", items or [s.get("point") or ""], MX + 0.4, BODY_Y + 0.15, CW - 1.4, 4.2, big=26, small=14, bullets=True, gap=0.4, anchor="middle" if len(items) <= 3 else "top")
        self._chrome(slide)
        return slide

    def _column(self, slide: Any, side: str, col: dict[str, Any], x: float, w: float, colour: str) -> None:
        t = self.theme
        heading = _clean(col.get("heading"))
        items = [_clean(b) for b in (col.get("bullets") or col.get("points") or []) if _clean(b)][:5]
        if heading:
            self._text(slide, f"{side} heading", [heading], x, BODY_Y + 0.1, w, 0.8, big=26, small=15, colour=colour, font=t.head, anchor="bottom")
            self._box(slide, x, BODY_Y + 1.02, 0.9, 0.06, colour)
        self._text(slide, f"{side} points", items, x, BODY_Y + 1.35, w, 3.05, big=22, small=13, bullets=True, gap=0.4)

    def _draw_two_columns(self, s: dict[str, Any]) -> Any:
        t = self.theme
        slide = self._slide()
        self._title(slide, s.get("title") or "")
        half = (CW - 0.8) / 2
        self._column(slide, "left", s.get("left") or {}, MX, half, t.accent)
        self._box(slide, MX + half + 0.39, BODY_Y + 0.2, 0.02, 4.1, t.muted)
        self._column(slide, "right", s.get("right") or {}, MX + half + 0.8, half, t.accent2)
        self._chrome(slide)
        return slide

    def _draw_comparison(self, s: dict[str, Any]) -> Any:
        t = self.theme
        slide = self._slide()
        self._title(slide, s.get("title") or "")
        gap = 0.4
        cw = (CW - gap) / 2
        verdict = _clean(s.get("verdict"))
        card_h = 3.55 if verdict else 4.3
        for i, (side, colour) in enumerate((("left", t.accent), ("right", t.accent2))):
            col = s.get(side) or {}
            x = MX + i * (cw + gap)
            self._box(slide, x, BODY_Y + 0.1, cw, card_h, t.surface, shape="round", radius=0.04)
            self._box(slide, x, BODY_Y + 0.1, cw, 0.75, colour, shape="round", radius=0.2)
            on = t.on_accent if colour == t.accent else ("FFFFFF" if not t.dark else t.bg)
            self._text(slide, f"{side} heading", [col.get("heading") or side.title()], x + 0.3, BODY_Y + 0.1, cw - 0.6, 0.75, big=22, small=14, colour=on, font=t.head, anchor="middle")
            items = [_clean(b) for b in (col.get("points") or col.get("bullets") or []) if _clean(b)][:5]
            self._text(slide, f"{side} points", items, x + 0.3, BODY_Y + 1.1, cw - 0.6, card_h - 1.3, big=22, small=12, bullets=True, gap=0.4, anchor="middle")
        if verdict:
            self._box(slide, MX, BODY_Y + 3.85, 0.07, 0.6, t.accent)
            self._text(slide, "verdict", [verdict], MX + 0.3, BODY_Y + 3.85, CW - 0.3, 0.6, big=18, small=13, anchor="middle", bold=True)
        self._chrome(slide)
        return slide

    def _draw_timeline(self, s: dict[str, Any]) -> Any:
        t = self.theme
        slide = self._slide()
        self._title(slide, s.get("title") or "")
        steps = [st for st in (s.get("steps") or []) if isinstance(st, dict) and (_clean(st.get("label")) or _clean(st.get("text")))][:6]
        if len(steps) < 2:
            raise ValueError("a timeline needs two steps or more")
        n = len(steps)
        cw = CW / n
        y = BODY_Y + 1.75
        self._box(slide, MX + cw / 2, y + 0.14, CW - cw, 0.04, t.muted)
        texts = [[_clean(st.get("text"))] for st in steps]
        size = min(fit(p, t.body, cw - 0.4, 2.0, 20, 11) or 11 for p in texts if p[0]) if any(p[0] for p in texts) else 14
        label = min(fit([_clean(st.get("label")) or str(i + 1)], t.head, cw - 0.3, 1.0, 28, 13) or 13 for i, st in enumerate(steps))
        for i, st in enumerate(steps):
            x = MX + i * cw
            self._box(slide, x + cw / 2 - 0.16, y, 0.32, 0.32, t.accent if i % 2 == 0 else t.accent2, shape="oval")
            self._text(slide, f"step {i + 1} label", [_clean(st.get("label")) or str(i + 1)], x + 0.15, y - 1.2, cw - 0.3, 1.0, big=label, small=13, colour=t.accent, font=t.head, align="center", anchor="bottom")
            if _clean(st.get("text")):
                self._text(slide, f"step {i + 1}", [st.get("text")], x + 0.2, y + 0.6, cw - 0.4, 2.0, big=size, small=11, align="center")
        self._chrome(slide)
        return slide

    def _draw_big_number(self, s: dict[str, Any]) -> Any:
        t = self.theme
        slide = self._slide()
        self._title(slide, s.get("title") or "")
        number = _clean(s.get("number"))
        if not number:
            raise ValueError("no number given")
        self._box(slide, MX, BODY_Y + 0.25, 5.9, 3.9, t.surface, shape="round", radius=0.05)
        self._text(slide, "big number", [number], MX + 0.3, BODY_Y + 0.25, 5.3, 3.9, big=110, small=40, colour=t.accent, font=t.head, align="center", anchor="middle")
        x = MX + 6.5
        self._box(slide, x, BODY_Y + 0.5, 0.9, 0.07, t.accent2)
        self._text(slide, "what the number is", [s.get("label") or ""], x, BODY_Y + 0.8, CW - 6.5, 1.5, big=28, small=17, font=t.head)
        if s.get("context"):
            self._text(slide, "context", [s["context"]], x, BODY_Y + 2.45, CW - 6.5, 1.7, big=18, small=12, colour=t.muted)
        self._chrome(slide)
        return slide

    def _draw_chart(self, s: dict[str, Any]) -> Any:
        from pptx.chart.data import CategoryChartData
        from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION
        from pptx.util import Inches, Pt

        t = self.theme
        data = s.get("data") or {}
        cats = [str(c) for c in data.get("categories") or []]
        series = [(str(n), [float(v) for v in vs]) for n, vs in (data.get("series") or [])]
        if not cats or not series or any(len(vs) != len(cats) for _, vs in series):
            raise ValueError("no figures to chart")
        slide = self._slide()
        self._title(slide, s.get("title") or "")
        side = [_clean(b) for b in (s.get("bullets") or []) if _clean(b)][:3]
        takeaway = _clean(s.get("takeaway"))
        wide = not (side or takeaway)
        cw = CW if wide else 7.6
        kind = str(s.get("chart_type") or "column").lower()
        if kind == "pie" and (len(series) > 1 or len(cats) > 7 or any(v < 0 for v in series[0][1])):
            kind = "column"
        xl = {
            "bar": XL_CHART_TYPE.BAR_CLUSTERED,
            "column": XL_CHART_TYPE.COLUMN_CLUSTERED,
            "line": XL_CHART_TYPE.LINE_MARKERS,
            "pie": XL_CHART_TYPE.PIE,
        }.get(kind, XL_CHART_TYPE.COLUMN_CLUSTERED)
        cd = CategoryChartData()
        cd.categories = cats
        for name, values in series:
            cd.add_series(name, values)
        self._box(slide, MX, BODY_Y + 0.1, cw, 4.35, t.surface, shape="round", radius=0.03)
        frame = slide.shapes.add_chart(xl, Inches(MX + 0.15), Inches(BODY_Y + 0.2), Inches(cw - 0.3), Inches(4.15), cd)
        chart = frame.chart
        chart.font.size = Pt(12)
        chart.font.name = t.body
        chart.font.color.rgb = _rgb(t.text)
        chart.has_title = False
        chart.has_legend = kind == "pie" or len(series) > 1
        if chart.has_legend:
            chart.legend.position = XL_LEGEND_POSITION.BOTTOM
            chart.legend.include_in_layout = False
        plot = chart.plots[0]
        if kind == "pie":
            plot.vary_by_categories = True
            for i, point in enumerate(plot.series[0].points):
                point.format.fill.solid()
                point.format.fill.fore_color.rgb = _rgb(_shade(t, i))
        else:
            for i, ser in enumerate(plot.series):
                colour = _rgb(t.series[i % len(t.series)])
                if kind == "line":
                    ser.format.line.color.rgb = colour
                    ser.format.line.width = Pt(2.5)
                    ser.smooth = False
                    ser.marker.format.fill.solid()
                    ser.marker.format.fill.fore_color.rgb = colour
                else:
                    ser.format.fill.solid()
                    ser.format.fill.fore_color.rgb = colour
            if kind in ("bar", "column"):
                plot.gap_width = 80
            va, ca = chart.value_axis, chart.category_axis
            va.has_major_gridlines = True
            va.major_gridlines.format.line.color.rgb = _rgb(t.muted)
            va.major_gridlines.format.line.width = Pt(0.5)
            va.format.line.fill.background()
            ca.format.line.color.rgb = _rgb(t.muted)
            va.tick_labels.font.size = ca.tick_labels.font.size = Pt(11)
        if len(cats) * len(series) <= 12:
            plot.has_data_labels = True
            plot.data_labels.font.size = Pt(11)
            plot.data_labels.font.color.rgb = _rgb(t.text)
            plot.data_labels.number_format = s.get("number_format") or "General"
            plot.data_labels.number_format_is_linked = False
        self.charts[self.n] = {"categories": cats, "series": series, "source": s.get("data_source") or ""}
        if not wide:
            x = MX + cw + 0.45
            w = CW - cw - 0.45
            y = BODY_Y + 0.2
            if takeaway and not side:
                # The one line the chart is there to prove, large, beside it.
                self._box(slide, x, BODY_Y + 1.1, 0.9, 0.07, t.accent)
                self._text(slide, "takeaway", [takeaway], x, BODY_Y + 1.4, w, 2.6, big=30, small=15, font=t.head)
            elif takeaway:
                self._box(slide, x, y, 0.9, 0.07, t.accent)
                size = self._text(slide, "takeaway", [takeaway], x, y + 0.25, w, 1.7, big=22, small=14, font=t.head)
                y += 0.25 + measure([takeaway], t.head, size, w) + 0.3
            if side:
                self._text(slide, "chart points", side, x, y, w, BODY_Y + 4.4 - y, big=16, small=11, bullets=True, gap=0.4, colour=t.muted)
        self._chrome(slide)
        return slide

    def _draw_picture(self, s: dict[str, Any]) -> Any:
        from pptx.util import Inches

        t = self.theme
        image = s.get("image")
        if not image:
            raise ValueError("no picture to place")
        slide = self._slide()
        self._title(slide, s.get("title") or "")
        import io

        from PIL import Image

        raw = image if isinstance(image, bytes) else Path(str(image)).read_bytes()
        with Image.open(io.BytesIO(raw)) as im:
            ratio = im.width / max(1, im.height)
        box_w, box_h = 7.4, 4.35
        w, h = (box_w, box_w / ratio) if box_w / ratio <= box_h else (box_h * ratio, box_h)
        self._box(slide, MX, BODY_Y + 0.1, box_w, box_h, t.surface, shape="round", radius=0.03)
        slide.shapes.add_picture(io.BytesIO(raw), Inches(MX + (box_w - w) / 2), Inches(BODY_Y + 0.1 + (box_h - h) / 2), Inches(w), Inches(h))
        x = MX + box_w + 0.45
        self._box(slide, x, BODY_Y + 0.3, 0.9, 0.07, t.accent)
        self._text(slide, "caption", [s.get("caption") or s.get("point") or ""], x, BODY_Y + 0.55, CW - box_w - 0.45, 3.7, big=20, small=13)
        self._chrome(slide)
        return slide

    def _draw_closing(self, s: dict[str, Any]) -> Any:
        t = self.theme
        slide = self._slide()
        self._box(slide, 0, 0, 0.35, H, t.accent)
        self._box(slide, W - 2.5, H - 2.5, 4.0, 4.0, t.accent if t.dark else t.accent2, shape="oval", alpha=0.18)
        self._title(slide, s.get("title") or "Thank you", x=MX + 0.3, y=0.9, w=CW - 1.0, h=1.3, big=44, small=26, anchor="bottom")
        items = [_clean(b) for b in (s.get("takeaways") or s.get("bullets") or []) if _clean(b)][:4]
        y = 2.65
        if items:
            row = 0.95 if len(items) <= 3 else 0.8
            size = min(fit([b], t.body, CW - 3.2, row - 0.1, 24, 13) or 13 for b in items)
            for i, b in enumerate(items):
                self._text(slide, f"number of takeaway {i + 1}", [f"{i + 1:02d}"], MX + 0.3, y, 0.9, row - 0.1, big=26, small=16, colour=t.accent, font=t.head, anchor="middle")
                self._text(slide, f"takeaway {i + 1}", [b], MX + 1.3, y, CW - 3.2, row - 0.1, big=size, small=13, anchor="middle")
                y += row
        if s.get("next"):
            self._box(slide, MX + 0.3, y + 0.2, 1.3, 0.07, t.accent)
            self._text(slide, "closing line", [s["next"]], MX + 0.3, y + 0.4, CW - 3.2, 0.8, big=20, small=14, colour=t.muted)
        return slide

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.prs.save(str(path))
        return path


def _shade(theme: Theme, i: int) -> str:
    """Colours for the slices of a pie: the theme's, then lighter and darker turns of them."""
    base = [theme.accent, theme.accent2, theme.muted]
    hex6 = base[i % len(base)]
    turn = i // len(base)
    if not turn:
        return hex6
    r, g, b = (int(hex6[k : k + 2], 16) for k in (0, 2, 4))
    f = 0.55 if turn % 2 else -0.3
    mix = [(c + (255 - c) * f) if f > 0 else c * (1 + f) for c in (r, g, b)]
    return "".join(f"{int(max(0, min(255, c))):02X}" for c in mix)


# ---------------------------------------------------------------------------- the last check
@dataclass
class Audit:
    problems: list[str] = field(default_factory=list)
    slides: int = 0
    texts: int = 0
    charts: int = 0


def audit(path: Path, theme: Theme, placed: list[Placed], charts: dict[int, dict[str, Any]]) -> Audit:
    """The saved file, opened again and gone over: every text fits its box, no placeholder is
    empty, nothing sits off the slide, only the theme's fonts and colours are used, every slide
    has notes, and every chart holds the figures it was given."""
    from pptx import Presentation
    from pptx.util import Inches

    out = Audit()
    prs = Presentation(str(path))
    out.slides = len(prs.slides)
    wide, tall = prs.slide_width, prs.slide_height
    by_slide: dict[int, list[Placed]] = {}
    for p in placed:
        by_slide.setdefault(p.slide, []).append(p)
    for n, slide in enumerate(prs.slides, 1):
        title = slide.shapes.title
        if title is None or not title.text_frame.text.strip():
            out.problems.append(f"Slide {n} has no title.")
        for shape in slide.shapes:
            if shape.left < -Inches(1.5) or shape.top < -Inches(1.5) or shape.left + shape.width > wide + Inches(1.5) or shape.top + shape.height > tall + Inches(1.5):
                out.problems.append(f"Slide {n}: something sits off the slide.")
            if shape.is_placeholder and shape.has_text_frame and not shape.text_frame.text.strip():
                out.problems.append(f"Slide {n} has an empty placeholder.")
            if shape.has_text_frame and shape.text_frame.text.strip():
                if shape.left < 0 or shape.top < 0 or shape.left + shape.width > wide or shape.top + shape.height > tall:
                    out.problems.append(f"Slide {n}: text runs off the slide.")
                for para in shape.text_frame.paragraphs:
                    for run in para.runs:
                        if run.font.name and run.font.name not in theme.fonts:
                            out.problems.append(f"Slide {n}: a font that is not the theme's ({run.font.name}).")
                        try:
                            colour = str(run.font.color.rgb) if run.font.color and run.font.color.type is not None else ""
                        except AttributeError:
                            colour = ""
                        if colour and colour.upper() not in theme.colours:
                            out.problems.append(f"Slide {n}: a colour that is not the theme's (#{colour}).")
            if getattr(shape, "has_chart", False) and shape.has_chart:
                out.charts += 1
                want = charts.get(n)
                plot = shape.chart.plots[0]
                got_cats = [str(c) for c in plot.categories]
                got = [(s.name, [float(v) for v in s.values]) for s in plot.series]
                if want is None:
                    out.problems.append(f"Slide {n}: a chart nobody asked for.")
                elif got_cats != want["categories"] or len(got) != len(want["series"]) or any(
                    abs(a - b) > 1e-9 * max(1.0, abs(b)) for (_, gv), (_, wv) in zip(got, want["series"], strict=False) for a, b in zip(gv, wv, strict=False)
                ):
                    out.problems.append(f"Slide {n}: the chart's figures are not the ones from the source.")
        try:
            notes = slide.notes_slide.notes_text_frame.text.strip() if slide.has_notes_slide else ""
        except Exception:
            notes = ""
        if not notes:
            out.problems.append(f"Slide {n} has no speaker notes.")
        for p in by_slide.get(n, []):
            out.texts += 1
            if measure(p.paras, p.font, p.size, p.width, bold=p.bold, gap=p.gap) > p.height + 0.02 or widest_word(p.paras, p.font, p.size, p.bold) > p.width + 0.02:
                out.problems.append(f"Slide {n}: the {p.what} does not fit its box.")
    for n in charts:
        if n > out.slides:
            out.problems.append(f"Slide {n}: its chart is missing.")
    out.problems = list(dict.fromkeys(out.problems))
    return out
