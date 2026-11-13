"""Read the pixels: every line of text on screen, with exact word boxes.

The accessibility tree only knows real UI elements. Text inside an image (a screenshot in a
tweet, a meme, a chart label, a slide in a video call) has no element at all, so without this
Pyxi could see it but couldn't put a pen on it. Windows' own OCR engine reads the whole screen
offline in about a tenth of a second, and each line becomes something Pyxi can frame or
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
    return re.sub(r"[\W_]+", "", s.casefold())


def _letters(s: str) -> str:
    """The letters and digits as written, capitals kept ("Pivot" is not "pivot,")."""
    return re.sub(r"[\W_]+", "", s)


def _apart(r: Rect, near: tuple[float, float] | None) -> float:
    if near is None:
        return 0.0
    return ((r[0] + r[2] / 2 - near[0]) ** 2 + (r[1] + r[3] / 2 - near[1]) ** 2) ** 0.5


def find_text(
    lines: list[TextLine],
    quote: str,
    within: Rect | None = None,
    near: tuple[float, float] | None = None,
) -> Rect | None:
    """The exact box of a phrase on screen: the words themselves, not the whole line.

    Matches the run of words whose letters spell the quote (ignoring case, spacing and
    punctuation), across line breaks too. Falls back to the closest line if OCR misread a
    letter or two.

    The words are often on screen more than once, and the first in reading order is rarely
    the one meant: live, "Pivot" landed on "...and then a pivot, into the Google Sheet" in a
    notification instead of the sheet tab "Pivot", and "Settings" on the Start menu's
    "Search for apps, settings, and documents". So every match is weighed: one that is its
    whole line (a tab, a button, a label) first, then one that starts its line, then one
    written with the same capitals, then reading order. ``near`` (screen px, where the model
    thought it was) settles a tie.
    """
    target = _norm(quote)
    if not target:
        return None
    pool = [ln for ln in lines if within is None or _inside(ln.rect, within)] or lines
    found = _said(pool, quote, near)
    if found:
        return min(found)[1]
    # Inside a longer word run ("inflation" inside "Cost of living and inflation").
    inside = [ln for ln in pool if target in _norm(ln.text)]
    if inside:
        ln = min(inside, key=lambda c: _apart(c.rect, near))
        hits = [r for w, r in ln.words if _norm(w) and _norm(w) in target]
        return _union(hits) if hits else ln.rect
    best, score = None, 0.0
    for ln in pool:
        s = difflib.SequenceMatcher(None, target, _norm(ln.text)).ratio()
        if s > score:
            best, score = ln, s
    return best.rect if best and score >= 0.78 else None


def find_places(lines: list[TextLine], quote: str, within: Rect | None = None) -> list[Rect]:
    """Every place that says the phrase as well as the best place does (word for word, and
    as much a line of its own). More than one: the words alone do not say which was meant.
    A short quote ("See more", a name, a price) is often on a page several times."""
    if not _norm(quote):
        return []
    pool = [ln for ln in lines if within is None or _inside(ln.rect, within)] or lines
    found = _said(pool, quote, None)
    if not found:
        return []
    best = min(found)[0][:3]
    return [rect for key, rect in found if key[:3] == best]


def share_still_there(before: list[TextLine], now: list[TextLine]) -> float:
    """How much of what was read before is still on screen, anywhere (0..1; 1 when there was
    nothing to go by). Near 0: it is another page now."""
    was = {k for ln in before if len(k := _norm(ln.text)) >= 6}
    if len(was) < 4:
        return 1.0
    return len(was & {_norm(ln.text) for ln in now}) / len(was)


def page_shift(
    before: list[TextLine], now: list[TextLine], around: Rect | None = None
) -> tuple[int, int] | None:
    """How far the page moved between two reads of the same screen, in px: (0, 0) when it
    did not, None when that cannot be told (too little text that is in both).

    Live, a card's marks were drawn 100 to 500 px from what they were about: the look was
    15 s old, and the page had moved under it (a video loading, a small scroll). Lines that
    are on screen once, before and now, say by how much: the move most of them agree on is
    the page's. ``around``: where the marks are; the lines near there are asked first, since
    a side bar that stays put while the middle scrolls would outvote the part that matters."""

    def once(lines: list[TextLine]) -> dict[str, Rect]:
        seen: dict[str, Rect | None] = {}
        for ln in lines:
            key = _norm(ln.text)
            if len(key) >= 6:
                seen[key] = None if key in seen else ln.rect
        return {k: r for k, r in seen.items() if r is not None}

    was, is_now = once(before), once(now)
    moves = [(r, (is_now[k][0] - r[0], is_now[k][1] - r[1])) for k, r in was.items() if k in is_now]

    def agreed(some: list[tuple[int, int]]) -> tuple[int, int] | None:
        if len(some) < 3:
            return None
        best: list[tuple[int, int]] = []
        for m in some:
            same = [o for o in some if abs(o[0] - m[0]) <= 6 and abs(o[1] - m[1]) <= 6]
            if len(same) > len(best):
                best = same
        if len(best) < 3 or len(best) * 2 < len(some):
            return None  # no one move that most of the page made
        dx = round(sum(m[0] for m in best) / len(best))
        dy = round(sum(m[1] for m in best) / len(best))
        return (0, 0) if abs(dx) <= 6 and abs(dy) <= 6 else (dx, dy)

    if around is not None:
        x, y, w, h = around
        wide = (x - 60, y - 200, w + 120, h + 400)
        if (near := agreed([m for r, m in moves if _inside(r, wide)])) is not None:
            return near
    return agreed([m for _, m in moves])


def _said(
    pool: list[TextLine], quote: str, near: tuple[float, float] | None
) -> list[tuple[tuple[bool, bool, bool, float, int], Rect]]:
    """Each run of words that spells the quote, with what it is weighed by (see find_text)."""
    target = _norm(quote)
    words = [(w, r, li, wi) for li, ln in enumerate(pool) for wi, (w, r) in enumerate(ln.words)]
    normed = [_norm(w[0]) for w in words]
    as_written = _letters(quote)
    found: list[tuple[tuple[bool, bool, bool, float, int], Rect]] = []
    for i in range(len(words)):
        if not normed[i]:
            continue  # a run starts on a word, not on a bullet or a dash before it
        acc = ""
        for j in range(i, min(len(words), i + 40)):
            acc += normed[j]
            if not target.startswith(acc):
                break
            if acc == target:
                rects = [w[1] for w in words[i : j + 1]]
                # A phrase that wraps onto the next line: keep the first line's part.
                rect = _union([r for r in rects if abs(r[1] - rects[0][1]) < rects[0][3]])
                line, at = pool[words[i][2]], words[i][3]
                whole = words[j][2] == words[i][2] and _norm(line.text) == target
                starts = not any(_norm(w) for w, _ in line.words[:at])
                same = _letters("".join(w[0] for w in words[i : j + 1])) == as_written
                found.append(((not whole, not starts, not same, _apart(rect, near), len(found)), rect))
                break
    return found


def _inside(r: Rect, outer: Rect) -> bool:
    cx, cy = r[0] + r[2] / 2, r[1] + r[3] / 2
    return outer[0] <= cx <= outer[0] + outer[2] and outer[1] <= cy <= outer[1] + outer[3]
