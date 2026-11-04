"""Read the pixels: every line of text on screen, with exact word boxes.

The accessibility tree only knows real UI elements. Text inside an image (a screenshot in a
tweet, a meme, a chart label, a slide in a video call) has no element at all, so without this
Wisp could see it but couldn't put a pen on it. Windows' own OCR engine reads the whole screen
offline in about a tenth of a second, and each line becomes something Wisp can frame or
underline exactly.
"""

from __future__ import annotations

import asyncio
import difflib
import re
import sys
from dataclasses import dataclass, field

from PIL import Image

Rect = tuple[int, int, int, int]


@dataclass
class TextLine:
    text: str
    rect: Rect  # physical global screen px
    words: list[tuple[str, Rect]] = field(default_factory=list)
    id: int = 0  # set by the screen reader, from the same counter as UI elements


_engine = None
_failed = False


def _get_engine():
    global _engine, _failed
    if _engine is None and not _failed:
        try:
            from winrt.windows.media.ocr import OcrEngine

            _engine = OcrEngine.try_create_from_user_profile_languages()
            _failed = _engine is None
        except Exception:
            _failed = True
    return _engine


def available() -> bool:
    return sys.platform == "win32" and _get_engine() is not None


def read_lines(img: Image.Image, origin: tuple[int, int] = (0, 0)) -> list[TextLine]:
    """OCR a screenshot. Returns [] when OCR isn't available (never raises)."""
    engine = _get_engine() if sys.platform == "win32" else None
    if engine is None:
        return []
    try:
        from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
        from winrt.windows.media.ocr import OcrEngine
        from winrt.windows.storage.streams import DataWriter

        limit = int(OcrEngine.max_image_dimension) or 10000
        scale = min(1.0, limit / max(img.width, img.height))
        if scale < 1.0:
            img = img.resize((int(img.width * scale), int(img.height * scale)))
        r, g, b, a = img.convert("RGBA").split()
        writer = DataWriter()
        writer.write_bytes(Image.merge("RGBA", (b, g, r, a)).tobytes())
        bitmap = SoftwareBitmap.create_copy_from_buffer(
            writer.detach_buffer(), BitmapPixelFormat.BGRA8, img.width, img.height
        )

        async def recognize():
            return await engine.recognize_async(bitmap)

        result = asyncio.run(recognize())
    except Exception:
        return []

    ox, oy = origin

    def box(r) -> Rect:
        return (
            int(r.x / scale) + ox,
            int(r.y / scale) + oy,
            max(1, int(r.width / scale)),
            max(1, int(r.height / scale)),
        )

    lines: list[TextLine] = []
    for line in result.lines:
        words = [(w.text, box(w.bounding_rect)) for w in line.words]
        text = line.text.strip()
        if not words or not _worth_keeping(text):
            continue
        lines.append(TextLine(text, _union([r for _, r in words]), words))
    lines.sort(key=lambda ln: (ln.rect[1] // 12, ln.rect[0]))
    return lines


def _worth_keeping(text: str) -> bool:
    """Drop OCR crumbs: single glyphs, icon noise, lone numbers on toolbars."""
    letters = sum(ch.isalnum() for ch in text)
    return letters >= 3 or (letters >= 1 and len(text) >= 2 and text[0].isdigit())


def _union(rects: list[Rect]) -> Rect:
    x0 = min(r[0] for r in rects)
    y0 = min(r[1] for r in rects)
    x1 = max(r[0] + r[2] for r in rects)
    y1 = max(r[1] + r[3] for r in rects)
    return x0, y0, x1 - x0, y1 - y0


def _norm(s: str) -> str:
    s = s.lower().replace("’", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"[^a-z0-9]+", "", s)


def find_text(lines: list[TextLine], quote: str, within: Rect | None = None) -> Rect | None:
    """The exact box of a phrase on screen: the words themselves, not the whole line.

    Matches the run of words whose letters spell the quote (ignoring case, spacing and
    punctuation), across line breaks too. Falls back to the closest line if OCR misread a
    letter or two.
    """
    target = _norm(quote)
    if not target:
        return None
    pool = [ln for ln in lines if within is None or _inside(ln.rect, within)] or lines
    words = [(w, r) for ln in pool for w, r in ln.words]
    normed = [_norm(w) for w, _ in words]
    for i in range(len(words)):
        acc = ""
        for j in range(i, min(len(words), i + 40)):
            acc += normed[j]
            if not target.startswith(acc):
                break
            if acc == target:
                rects = [r for _, r in words[i : j + 1]]
                # A phrase that wraps onto the next line: keep the first line's part.
                first = [r for r in rects if abs(r[1] - rects[0][1]) < rects[0][3]]
                return _union(first)
    # Inside a longer word run ("inflation" inside "Cost of living and inflation").
    for ln in pool:
        if target in _norm(ln.text):
            hits = [r for w, r in ln.words if _norm(w) and _norm(w) in target]
            return _union(hits) if hits else ln.rect
    best, score = None, 0.0
    for ln in pool:
        s = difflib.SequenceMatcher(None, target, _norm(ln.text)).ratio()
        if s > score:
            best, score = ln, s
    return best.rect if best and score >= 0.78 else None


def _inside(r: Rect, outer: Rect) -> bool:
    cx, cy = r[0] + r[2] / 2, r[1] + r[3] / 2
    return outer[0] <= cx <= outer[0] + outer[2] and outer[1] <= cy <= outer[1] + outer[3]
