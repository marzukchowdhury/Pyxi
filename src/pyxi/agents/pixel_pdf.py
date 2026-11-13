"""Pixel's PDF and document toolkit: pages (rotate, reorder, delete, extract), page numbers and
watermarks, passwords, form filling, PDF to Word, text out of PDFs and images (Windows OCR for
scans) with searchable PDFs, and comparing two documents.

Everything works on the files directly, never overwrites the original, and raises PixelError
with a sentence a person can act on.
"""

from __future__ import annotations

import difflib
import io
import re
from collections.abc import Callable
from itertools import pairwise
from pathlib import Path
from typing import Any

from pyxi.agents.pixel_common import (
    IMAGE_EXTS,
    PixelError,
    PixelResult,
    image_pages,
    need,
    out_dir_for,
    parse_ranges,
    unique_path,
)


# ============================================================================ helpers
def _reader(src: Path, password: str | None = None):
    from pypdf import PdfReader

    try:
        reader = PdfReader(str(src))
    except Exception as exc:
        raise PixelError(f"{src.name} doesn't open as a PDF ({type(exc).__name__}).") from None
    if reader.is_encrypted:
        if password is None:
            # Many "protected" PDFs only restrict printing and open with an empty password.
            if reader.decrypt("") == 0:
                raise PixelError(f"{src.name} is password-protected. Ask Pixel to unlock it first.")
        elif reader.decrypt(password) == 0:
            raise PixelError("That password didn't open it. Check it and try again.")
    return reader


def _pages_list(spec: str, n: int, default_all: bool = True) -> list[int]:
    """1-based page numbers from '1-3, 5' (in the order given)."""
    if not str(spec or "").strip():
        if default_all:
            return list(range(1, n + 1))
        raise PixelError("Which pages? Say them like '2' or '1-3, 5'.")
    out: list[int] = []
    for a, b in parse_ranges(str(spec), n):
        out.extend(range(a, b + 1) if a <= b else range(a, b - 1, -1))
    return out


def _write(writer, target: Path) -> None:
    with target.open("wb") as fh:
        writer.write(fh)


def _label(pages: list[int]) -> str:
    if len(pages) == 1:
        return f"page {pages[0]}"
    runs, start, prev = [], pages[0], pages[0]
    for p in [*pages[1:], None]:  # type: ignore[list-item]
        if p is not None and p == prev + 1:
            prev = p
            continue
        runs.append(str(start) if start == prev else f"{start}-{prev}")
        if p is not None:
            start = prev = p
    return "pages " + ", ".join(runs)


def _short_label(pages: list[int]) -> str:
    """The same, for a file's name and a notification: a long list ("every other page" of a
    300-page scan) is said as a count. Spelt out, the name was too long for Windows to save."""
    label = _label(pages)
    return label if len(label) <= 48 else f"{len(pages)} pages"


# ============================================================================ pages
def rotate_pages(
    files: list[Path], pages: str = "", angle: Any = 90, out: Path | None = None
) -> PixelResult:
    from pypdf import PdfWriter

    (src,) = need(files, {".pdf"}, "PDF")
    turn = _angle(angle)
    reader = _reader(src)
    chosen = set(_pages_list(pages, len(reader.pages)))
    writer = PdfWriter(clone_from=reader)
    for i, page in enumerate(writer.pages, 1):
        if i in chosen:
            page.rotate(turn)
    target = unique_path(out or out_dir_for(src), f"{src.stem} (rotated).pdf")
    _write(writer, target)
    which = "every page" if len(chosen) == len(reader.pages) else _label(sorted(chosen))
    way = {90: "clockwise", 180: "upside down", 270: "anticlockwise"}[turn]
    return PixelResult([target], f"Turned {which} {way} in {target.name}.")


def _angle(angle: Any) -> int:
    words = {
        "right": 90,
        "clockwise": 90,
        "left": 270,
        "anticlockwise": 270,
        "counterclockwise": 270,
        "upside down": 180,
        "flip": 180,
    }
    if isinstance(angle, str) and angle.strip().lower() in words:
        return words[angle.strip().lower()]
    try:
        a = int(float(str(angle).strip().rstrip("°"))) % 360
    except ValueError:
        raise PixelError("Rotate by 90, 180 or 270 degrees (or left, right).") from None
    if a not in (90, 180, 270):
        raise PixelError("Rotate by 90, 180 or 270 degrees (or left, right).")
    return a


def reorder_pages(files: list[Path], order: str, out: Path | None = None) -> PixelResult:
    """Pages in a new order: '3, 1-2' puts page 3 first. Pages left out keep their order after."""
    from pypdf import PdfWriter

    (src,) = need(files, {".pdf"}, "PDF")
    reader = _reader(src)
    n = len(reader.pages)
    first = _pages_list(order, n, default_all=False)
    seen: list[int] = []
    for p in first:
        if p not in seen:
            seen.append(p)
    rest = [p for p in range(1, n + 1) if p not in seen]
    final = seen + rest
    writer = PdfWriter()
    for p in final:
        writer.add_page(reader.pages[p - 1])
    target = unique_path(out or out_dir_for(src), f"{src.stem} (reordered).pdf")
    _write(writer, target)
    return PixelResult(
        [target],
        f"Put the pages in the order {', '.join(map(str, final))} in {target.name}.",
        {"order": final},
    )


def delete_pages(files: list[Path], pages: str, out: Path | None = None) -> PixelResult:
    from pypdf import PdfWriter

    (src,) = need(files, {".pdf"}, "PDF")
    reader = _reader(src)
    gone = set(_pages_list(pages, len(reader.pages), default_all=False))
    if len(gone) >= len(reader.pages):
        raise PixelError("That would delete every page. Which ones should stay?")
    writer = PdfWriter()
    for i, page in enumerate(reader.pages, 1):
        if i not in gone:
            writer.add_page(page)
    which = _short_label(sorted(gone))
    target = unique_path(out or out_dir_for(src), f"{src.stem} (without {which}).pdf")
    _write(writer, target)
    left = len(reader.pages) - len(gone)
    return PixelResult(
        [target],
        f"Removed {which}; {target.name} has {left} page{'s' if left != 1 else ''}.",
    )


def extract_pages(files: list[Path], pages: str, out: Path | None = None) -> PixelResult:
    from pypdf import PdfWriter

    (src,) = need(files, {".pdf"}, "PDF")
    reader = _reader(src)
    keep = _pages_list(pages, len(reader.pages), default_all=False)
    writer = PdfWriter()
    for p in keep:
        writer.add_page(reader.pages[p - 1])
    which = _short_label(keep)
    target = unique_path(out or out_dir_for(src), f"{src.stem} ({which}).pdf")
    _write(writer, target)
    return PixelResult([target], f"Saved {which} as {target.name}.")


# ============================================================================ stamps
def _overlay(width: float, height: float, draw: Callable[[Any, float, float], None]):
    from pypdf import PdfReader
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(width, height))
    draw(c, width, height)
    c.save()
    buf.seek(0)
    return PdfReader(buf).pages[0]


def _stamp(
    src: Path, suffix: str, draw: Callable[[Any, float, float, int, int], None], out: Path | None
) -> Path:
    from pypdf import PdfWriter

    reader = _reader(src)
    writer = PdfWriter(clone_from=reader)
    total = len(writer.pages)
    for i, page in enumerate(writer.pages, 1):
        page.transfer_rotation_to_content()  # so "bottom" is the bottom as you read it
        # The part of the page that is shown and printed. A cropped PDF's full sheet (its
        # media box) is bigger, and a number placed by that landed outside what anyone sees.
        box = page.cropbox
        w, h = float(box.width), float(box.height)
        stamp = _overlay(w, h, lambda c, w_, h_, i=i: draw(c, w_, h_, i, total))
        # The stamp is drawn from (0, 0); shift it when the page's box doesn't start there.
        from pypdf import Transformation

        page.merge_transformed_page(
            stamp, Transformation().translate(float(box.left), float(box.bottom))
        )
    target = unique_path(out or out_dir_for(src), f"{src.stem} ({suffix}).pdf")
    _write(writer, target)
    return target


def add_page_numbers(
    files: list[Path],
    position: str = "bottom center",
    start: int = 1,
    fmt: str = "",
    out: Path | None = None,
) -> PixelResult:
    from pyxi.agents.pixel_render import fonts

    (src,) = need(files, {".pdf"}, "PDF")
    pos = (position or "bottom center").lower()
    style = fmt or "{n}"
    if "{n}" not in style:
        raise PixelError("The page number format needs {n}, like 'Page {n} of {total}'.")
    font = fonts()[0]

    def draw(c, w, h, i, total) -> None:
        text = style.replace("{n}", str(i + start - 1)).replace("{total}", str(total + start - 1))
        c.setFont(font, 9.5)
        c.setFillGray(0.25)
        y = h - 28 if "top" in pos else 22
        if "left" in pos:
            c.drawString(36, y, text)
        elif "right" in pos:
            c.drawRightString(w - 36, y, text)
        else:
            c.drawCentredString(w / 2, y, text)

    target = _stamp(src, "numbered", draw, out)
    return PixelResult([target], f"Numbered the pages of {src.name}; saved as {target.name}.")


def add_watermark(
    files: list[Path], text: str, opacity: float = 0.15, angle: float = 45, out: Path | None = None
) -> PixelResult:
    from pyxi.agents.pixel_render import font_for, fonts

    (src,) = need(files, {".pdf"}, "PDF")
    text = (text or "").strip()
    if not text:
        raise PixelError("What should the watermark say? (e.g. DRAFT, CONFIDENTIAL)")
    bold = font_for(text, fonts()[1])  # 機密 needs a Chinese font, not empty boxes
    alpha = max(0.05, min(float(opacity or 0.15), 0.6))

    def draw(c, w, h, i, total) -> None:
        size = min(w, h) / max(4, len(text)) * 1.5
        c.saveState()
        c.setFillColorRGB(0.45, 0.45, 0.45, alpha=alpha)
        c.setFont(bold, size)
        c.translate(w / 2, h / 2)
        c.rotate(float(angle))
        c.drawCentredString(0, -size / 3, text)
        c.restoreState()

    target = _stamp(src, "watermarked", draw, out)
    return PixelResult(
        [target], f"Added a '{text[:40]}' watermark to every page; saved as {target.name}."
    )


# ============================================================================ passwords
def protect_pdf(files: list[Path], password: str, out: Path | None = None) -> PixelResult:
    from pypdf import PdfWriter

    (src,) = need(files, {".pdf"}, "PDF")
    if not password:
        raise PixelError("No password given, so I left it as it is.")
    if len(password) < 4:
        raise PixelError("That password is too short; use at least 4 characters.")
    writer = PdfWriter(clone_from=_reader(src))
    writer.encrypt(user_password=password, owner_password=None, algorithm="AES-256")
    target = unique_path(out or out_dir_for(src), f"{src.stem} (protected).pdf")
    _write(writer, target)
    return PixelResult(
        [target],
        f"Locked {target.name} with your password (AES-256). The original is still unlocked.",
    )


def unlock_pdf(files: list[Path], password: str, out: Path | None = None) -> PixelResult:
    from pypdf import PdfReader, PdfWriter

    (src,) = need(files, {".pdf"}, "PDF")
    if not PdfReader(str(src)).is_encrypted:
        raise PixelError(f"{src.name} isn't password-protected.")
    if not password:
        raise PixelError("I need the password to open it.")
    writer = PdfWriter(clone_from=_reader(src, password))
    target = unique_path(out or out_dir_for(src), f"{src.stem} (unlocked).pdf")
    _write(writer, target)
    return PixelResult([target], f"Unlocked it: {target.name} opens without a password.")


# ============================================================================ forms
# Field-name words for each saved detail (booking.FIELDS), most specific first.
FIELD_WORDS: list[tuple[str, tuple[str, ...]]] = [
    ("first_name", ("firstname", "givenname", "forename", "fname", "first")),
    ("last_name", ("lastname", "surname", "familyname", "lname", "last")),
    ("email", ("email", "emailaddress", "mail")),
    ("phone", ("phone", "telephone", "mobile", "cell", "tel", "phonenumber")),
    ("date_of_birth", ("dateofbirth", "birthdate", "dob", "birthday")),
    ("city", ("city", "town", "postalcode", "postcode", "zip", "zipcode")),
]
SECRET = re.compile(
    r"(?i)(sin|ssn|social.?ins|social.?sec|passport|health.?card|licen[cs]e|card.?num|cvv|cvc|"
    r"account.?num|routing|iban|password|pin\b|tax.?id|nino)"
)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def detail_key(name: str) -> str:
    """Which saved detail a field's name asks for ('first_name', 'email'...), or ''."""
    key = _norm(name)
    if not key or SECRET.search(name):
        return ""
    if key in ("name", "fullname", "yourname", "applicantname", "nameofapplicant", "legalname"):
        return "full_name"
    for detail, words in FIELD_WORDS:
        for w in words:
            if key == w or (len(w) > 4 and w in key):
                return detail
            if w in ("first", "last") and key.startswith(w) and "name" in key:
                return detail
    return ""


def match_field(name: str, details: dict[str, str]) -> str | None:
    """The saved detail a form field wants, or None."""
    key = _norm(name)
    if not key or SECRET.search(name):
        return None
    full = " ".join(x for x in (details.get("first_name", ""), details.get("last_name", "")) if x)
    if (
        key in ("name", "fullname", "yourname", "applicantname", "nameofapplicant", "legalname")
        and full
    ):
        return full
    for detail, words in FIELD_WORDS:
        if not details.get(detail):
            continue
        for w in words:
            if key == w or (len(w) > 4 and w in key):
                return details[detail]
            if w in ("first", "last") and key.startswith(w) and "name" in key:
                return details[detail]
    return None


def _write_filled(src: Path, reader: Any, filled: dict[str, str], out: Path | None) -> Path:
    from pypdf import PdfWriter
    from pypdf.generic import BooleanObject, NameObject

    writer = PdfWriter(clone_from=reader)
    for page in writer.pages:
        writer.update_page_form_field_values(page, filled, auto_regenerate=False)
    writer._root_object["/AcroForm"][NameObject("/NeedAppearances")] = BooleanObject(True)
    target = unique_path(out or out_dir_for(src), f"{src.stem} (filled).pdf")
    _write(writer, target)
    return target


def _fill_placed(
    src: Path,
    reader: Any,
    fields: dict[str, Any],
    placed: dict[str, str],
    labels: dict[str, str],
    out: Path | None,
) -> PixelResult:
    """Write what was typed on the form card. Only a field the form has, never one that asks
    for an ID number, and only if something would change."""

    def now(name: str) -> str:
        f = fields[name]
        value = f.get("/V")
        if f.get("/FT") == "/Btn":
            return str(value) if value not in (None, "") else "/Off"
        return "" if value is None else str(value)

    filled = {
        name: str(value)
        for name, value in placed.items()
        if name in fields
        and fields[name].get("/FT") in ("/Tx", "/Ch", "/Btn")
        and not SECRET.search(name)
        and not SECRET.search(labels.get(name, ""))
    }
    changed = sorted(name for name, value in filled.items() if value != now(name))
    if not changed:
        raise PixelError("Nothing was changed on the card, so I left the form as it is.")
    filled = {name: filled[name] for name in changed}  # a field they left alone is not touched
    target = _write_filled(src, reader, filled, out)
    empty = sum(
        1
        for name, f in fields.items()
        if f.get("/FT") in ("/Tx", "/Ch") and filled.get(name, now(name)) == ""
    )
    n = len(changed)
    tail = f" {empty} place{'s' if empty != 1 else ''} to write {'are' if empty != 1 else 'is'} still empty." if empty else ""
    return PixelResult(
        [target],
        f"Filled in {n} answer{'s' if n != 1 else ''} in {target.name}.{tail} Check it before you send it.",
        {"filled": changed, "empty": empty},
    )


def fill_form(
    files: list[Path],
    details: dict[str, str] | None = None,
    values: dict[str, Any] | None = None,
    out: Path | None = None,
    placed: dict[str, str] | None = None,
    labels: dict[str, str] | None = None,
) -> PixelResult:
    """Fill a PDF's form fields: values they gave first, then saved details by field name.
    ID numbers, card numbers and passwords are never filled.

    ``placed`` is what they typed on Pixel's form card, already matched to the form's fields
    (pixel_ask.py): {field name: what to write}. When it is given it is the whole of what is
    written, and nothing is matched by name. ``labels`` are the words printed for each field,
    so a box that asks for an ID number is known by them too."""

    (src,) = need(files, {".pdf"}, "PDF")
    reader = _reader(src)
    fields = reader.get_fields() or {}
    if not fields:
        raise PixelError(
            f"{src.name} has no fillable fields. Scout can fill a form on a website instead."
        )
    if placed is not None:
        return _fill_placed(src, reader, fields, placed, labels or {}, out)
    given = {_norm(k): v for k, v in (values or {}).items()}
    filled: dict[str, str] = {}
    left: list[str] = []
    for name, f in fields.items():
        if f.get("/FT") not in ("/Tx", "/Ch", "/Btn"):
            continue
        if SECRET.search(name):
            left.append(name)
            continue
        value = given.get(_norm(name))
        if value is None and f.get("/FT") == "/Tx":
            value = match_field(name, details or {})
        if value is None or value == "":
            left.append(name)
            continue
        if f.get("/FT") == "/Btn":
            states = [s for s in (f.get("/_States_") or []) if s != "/Off"]
            on = bool(value) and str(value).strip().lower() not in ("no", "false", "off", "0")
            if not states:
                left.append(name)
                continue
            value = states[0] if on else "/Off"
        filled[name] = str(value)
    if not filled:
        raise PixelError(
            "None of the form's fields matched your saved details. Tell me what to put in which field."
        )
    target = _write_filled(src, reader, filled, out)
    tail = f" Still empty: {', '.join(left[:8])}." if left else ""
    return PixelResult(
        [target],
        f"Filled {len(filled)} field{'s' if len(filled) != 1 else ''} in {target.name}.{tail} Check it before you send it.",
        {"filled": sorted(filled), "empty": left},
    )


# ============================================================================ text and OCR
def _ocr_image(img) -> list[Any]:
    """Lines of text in a picture: Windows OCR, or RapidOCR when Windows can't read it."""
    from pyxi.agents import pixel_ocr

    if not pixel_ocr.available():
        raise PixelError(
            "Reading scanned pages needs a text reader, and neither Windows' own nor the built-in "
            "one could start. Reinstalling Pyxi usually fixes it."
        )
    lines, _engine = pixel_ocr.read_lines(img)
    return lines


def _render_page(pdf_path: Path, index: int, dpi: int = 200, password: str | None = None):
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(pdf_path), password=password)
    try:
        return doc[index].render(scale=dpi / 72).to_pil()
    finally:
        doc.close()


def page_texts(src: Path, *, ocr: bool = True) -> tuple[list[str], list[int]]:
    """Text of each page, and which pages were read with OCR (scans)."""
    reader = _reader(src)
    texts, scanned = [], []
    no_reader: PixelError | None = None
    for i, page in enumerate(reader.pages):
        text = (page.extract_text() or "").strip()
        if len(re.sub(r"\s", "", text)) < 25 and ocr and no_reader is None:
            try:
                lines = _ocr_image(_render_page(src, i))
            except PixelError as exc:  # no text reader on this PC
                no_reader, lines = exc, []
            if lines:
                text = "\n".join(ln.text for ln in lines)
                scanned.append(i + 1)
        texts.append(text)
    # A typed PDF with one near-empty page (a cover, a blank back) is still read without a
    # text reader. Only a PDF with no words at all needs one.
    if no_reader is not None and not any(texts):
        raise no_reader
    return texts, scanned


def extract_text(files: list[Path], out: Path | None = None) -> PixelResult:
    """Text out of a PDF (OCR for scanned pages) or an image, as a .txt beside it."""
    (src,) = need(files, {".pdf", *IMAGE_EXTS}, "PDF or image")
    if src.suffix.lower() == ".pdf":
        texts, scanned = page_texts(src)
        how = f" (read {_short_label(scanned)} with OCR)" if scanned else ""
    else:  # a fax or a scanner's TIFF is several pages in one file
        texts = ["\n".join(ln.text for ln in _ocr_image(img)) for img in image_pages(src)]
        how = " with OCR"
    # Counted and checked before the "--- Page n ---" lines go in: with them, three pages
    # nothing could be read from were "12 words" saved to a file.
    words = sum(len(t.split()) for t in texts)
    if not words:
        raise PixelError(f"I couldn't find any text in {src.name}.")
    body = "\n\n".join(
        f"--- Page {i} ---\n{t}" if len(texts) > 1 else t for i, t in enumerate(texts, 1)
    )
    target = unique_path(out or out_dir_for(src), f"{src.stem} (text).txt")
    target.write_text(body, encoding="utf-8")
    return PixelResult(
        [target],
        f"Pulled {words:,} words out of {src.name}{how}; saved as {target.name}.",
        {"text": body[:4000]},
    )


def make_searchable(files: list[Path], dpi: int = 200, out: Path | None = None) -> PixelResult:
    """A PDF you can search and copy from: each scanned page (or image) keeps its picture, with the
    recognised words laid invisibly on top in the right places. Pages that already have text are
    kept as they are."""
    from pypdf import PdfReader, PdfWriter
    from reportlab.pdfgen import canvas

    from pyxi.agents.pixel_render import fonts

    files = need(files, {".pdf", *IMAGE_EXTS}, "PDF or image", many=True)
    font = fonts()[0]
    writer = PdfWriter()
    ocr_pages = 0
    words_found = 0

    def scanned_page(img) -> Any:
        nonlocal ocr_pages, words_found
        img = img.convert("RGB")
        lines = _ocr_image(img)
        w_pt, h_pt = img.width * 72 / dpi, img.height * 72 / dpi
        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=(w_pt, h_pt))
        jpg = io.BytesIO()
        img.save(jpg, "JPEG", quality=85)
        jpg.seek(0)
        from reportlab.lib.utils import ImageReader

        c.drawImage(ImageReader(jpg), 0, 0, w_pt, h_pt)
        s = 72 / dpi
        for line in lines:
            # One invisible run per line, stretched over the line's real width: search and copy
            # get whole lines ("Page one"), and selecting lands on the words in the picture.
            x, y, w, h = line.rect
            size = max(4.0, h * s * 0.8)
            t = c.beginText()
            t.setTextRenderMode(3)  # invisible: there to search and select, not to see
            t.setFont(font, size)
            natural = c.stringWidth(line.text, font, size) or 1
            t.setHorizScale(100 * (w * s) / natural)
            t.setTextOrigin(x * s, h_pt - (y + h) * s + h * s * 0.2)
            t.textOut(line.text)
            c.drawText(t)
            words_found += len(line.words)
        c.showPage()
        c.save()
        buf.seek(0)
        ocr_pages += 1
        return PdfReader(buf).pages[0]

    for src in files:
        if src.suffix.lower() == ".pdf":
            reader = _reader(src)
            for i, page in enumerate(reader.pages):
                if len(re.sub(r"\s", "", page.extract_text() or "")) >= 25:
                    writer.add_page(page)
                else:
                    writer.add_page(scanned_page(_render_page(src, i, dpi)))
        else:
            for img in image_pages(src):
                writer.add_page(scanned_page(img))
    if not ocr_pages:
        raise PixelError(f"{files[0].name} already has text you can search; nothing to do.")
    if not words_found:  # "now searchable" with no words in it would not be true
        raise PixelError(f"I couldn't read any text in {files[0].name}, so it can't be searched.")
    target = unique_path(out or out_dir_for(files[0]), f"{files[0].stem} (searchable).pdf")
    _write(writer, target)
    return PixelResult(
        [target],
        f"Made {target.name}: read {ocr_pages} page{'s' if ocr_pages != 1 else ''} ({words_found:,} words), now searchable.",
    )


# ============================================================================ PDF to Word
def pdf_to_word(files: list[Path], out: Path | None = None) -> PixelResult:
    """Text and basic layout: headings (larger type), paragraphs, bold runs, a page break per page,
    and the pictures on each page. Scanned pages come through with OCR."""
    from docx import Document
    from docx.enum.text import WD_BREAK
    from docx.shared import Inches, Pt

    (src,) = need(files, {".pdf"}, "PDF")
    reader = _reader(src)
    doc = Document()
    doc.styles["Normal"].font.size = Pt(11)
    scanned = []
    made = 0  # paragraphs and pictures that went in
    no_reader: PixelError | None = None
    for i, page in enumerate(reader.pages):
        if i:
            doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
        lines = _page_lines(page)
        if sum(len(ln[0]) for ln in lines) < 25 and no_reader is None:
            try:
                ocr_lines = _ocr_image(_render_page(src, i))
            except PixelError as exc:  # no text reader on this PC: keep what was typed
                no_reader, ocr_lines = exc, []
            if ocr_lines:  # a short typed page the reader made nothing of keeps its own words
                lines = [(ln.text, 11.0, False, -float(ln.rect[1])) for ln in ocr_lines]
                scanned.append(i + 1)
        body = _body_size(lines)
        for text, size, bold, kind in _paragraphs(lines, body):
            made += 1
            if kind == "h":
                level = 1 if size >= body * 1.5 else 2
                doc.add_heading(text, level=level)
            elif kind in ("bullet", "number"):
                doc.add_paragraph(text, style="List Bullet" if kind == "bullet" else "List Number")
            else:
                p = doc.add_paragraph()
                run = p.add_run(text)
                run.bold = bold
        for img in _page_images(page):
            try:
                doc.add_picture(io.BytesIO(img), width=Inches(min(6.0, 6.0)))
                made += 1
            except Exception:
                continue
    if not made:
        # An empty Word file is not "Made X.docx from 3 pages".
        if no_reader is not None:
            raise no_reader
        raise PixelError(f"I couldn't find any text or pictures in {src.name}.")
    target = unique_path(out or out_dir_for(src), f"{src.stem}.docx")
    doc.save(str(target))
    how = f" Read {_label(scanned)} with OCR." if scanned else ""
    return PixelResult(
        [target],
        f"Made {target.name} from {len(reader.pages)} page{'s' if len(reader.pages) != 1 else ''}.{how}",
    )


def _page_lines(page) -> list[tuple[str, float, bool, float]]:
    """(text, font size, bold, baseline y) per line, in reading order (y falls down the page)."""
    pieces: list[tuple[float, float, str, float, bool]] = []

    def visit(text, cm, tm, font_dict, font_size) -> None:
        if not text or not text.strip("\n"):
            if text == "\n":
                pieces.append((0, 0, "\n", 0, False))
            return
        scale = (tm[0] ** 2 + tm[1] ** 2) ** 0.5 * (cm[0] ** 2 + cm[1] ** 2) ** 0.5 or 1
        size = float(font_size or 0) * scale
        name = str((font_dict or {}).get("/BaseFont", "")).lower()
        y = tm[5] * cm[3] + cm[5]
        pieces.append((y, tm[4], text, size, "bold" in name or "black" in name or "heavy" in name))

    try:
        page.extract_text(visitor_text=visit)
    except Exception:
        text = page.extract_text() or ""
        rows = [ln.strip() for ln in text.splitlines() if ln.strip()]
        return [(t, 11.0, False, -14.0 * k) for k, t in enumerate(rows)]
    lines: list[tuple[str, float, bool, float]] = []
    buf: list[str] = []
    sizes: list[float] = []
    bolds: list[bool] = []
    last_y: float | None = None
    line_y: list[float] = []

    def flush() -> None:
        text = re.sub(r"\s+", " ", "".join(buf)).strip()
        if text:
            y = line_y[-1] if line_y else (lines[-1][3] - 14 if lines else 0.0)
            lines.append((text, max(sizes) if sizes else 11.0, bool(bolds) and all(bolds), y))
        line_y.clear()
        buf.clear()
        sizes.clear()
        bolds.clear()

    for y, _x, text, size, bold in pieces:
        if text == "\n":
            flush()
            last_y = None
            continue
        if last_y is not None and abs(y - last_y) > 2:
            flush()
        line_y.append(y)
        parts = text.split("\n")
        for k, part in enumerate(parts):
            if part:
                buf.append(part)
                if part.strip():
                    sizes.append(size)
                    bolds.append(bold)
            if k < len(parts) - 1:
                flush()
        last_y = y
    flush()
    return lines


def _body_size(lines: list[tuple[str, float, bool, float]]) -> float:
    from collections import Counter

    counts: Counter[float] = Counter()
    for text, size, _, _ in lines:
        counts[round(size, 1)] += len(text)
    return counts.most_common(1)[0][0] if counts else 11.0


BULLET_MARK = re.compile(r"^[\u2022\u25cf\u25aa\u2023\u2043\u2219\u00b7\u25e6\uf0b7*-]\s+")
NUMBER_MARK = re.compile(r"^(\d{1,3}|[a-zA-Z])[.)]\s+")


def _paragraphs(lines: list[tuple[str, float, bool, float]], body: float):
    """Join wrapped lines into paragraphs. A paragraph ends at a bigger gap than the usual line
    spacing; larger or short bold lines become headings; bullet and number marks become lists."""
    gaps = sorted(a[3] - b[3] for a, b in pairwise(lines) if 0 < a[3] - b[3] < body * 4)
    step = gaps[len(gaps) // 3] if gaps else body * 1.25  # a typical line-to-line distance
    para: list[str] = []
    kind = "p"
    prev_y: float | None = None
    for text, size, bold, y in lines:
        gap = (prev_y - y) if prev_y is not None else 0.0
        prev_y = y
        heading = size >= body * 1.2 or (bold and len(text) < 70 and not text.endswith((".", ",")))
        marker = (
            "bullet" if BULLET_MARK.match(text) else "number" if NUMBER_MARK.match(text) else None
        )
        if para and (heading or marker or gap > step * 1.4 or gap < 0):
            yield " ".join(para), body, False, kind
            para, kind = [], "p"
        if heading:
            yield text, size, bold, "h"
            continue
        if marker:
            kind = marker
            text = (BULLET_MARK if marker == "bullet" else NUMBER_MARK).sub("", text, count=1)
        para.append(text)
    if para:
        yield " ".join(para), body, False, kind


def _page_images(page) -> list[bytes]:
    out = []
    try:
        for img in page.images:
            pic = img.image
            if pic is None or min(pic.size) < 40:
                continue
            buf = io.BytesIO()
            pic.convert("RGB").save(buf, "JPEG", quality=88)
            out.append(buf.getvalue())
    except Exception:
        pass
    return out[:6]


# ============================================================================ compare
COMPARABLE = {
    ".pdf",
    ".docx",
    ".txt",
    ".md",
    ".markdown",
    ".html",
    ".htm",
    ".xlsx",
    ".xlsm",
    ".pptx",
    ".csv",
    ".rtf",
}


def document_lines(path: Path) -> list[str]:
    """A document as comparable lines: paragraphs, table rows, cells, slide text."""
    ext = path.suffix.lower()
    if ext == ".pdf":
        texts, _ = page_texts(path, ocr=False)
        raw = "\n".join(texts)
        # Re-join lines a PDF wrapped mid-sentence, so a reflow isn't a "change".
        paras = re.split(r"\n\s*\n|(?<=[.!?:])\n", raw)
        return [re.sub(r"\s+", " ", p).strip() for p in paras if p.strip()]
    if ext == ".docx":
        from docx import Document

        doc = Document(str(path))
        lines = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
        for t in doc.tables:
            for row in t.rows:
                lines.append(" | ".join(c.text.strip() for c in row.cells))
        return lines
    if ext in (".xlsx", ".xlsm"):
        from openpyxl import load_workbook

        wb = load_workbook(path, data_only=True, read_only=True)
        try:
            out = []
            for ws in wb.worksheets:
                ws.reset_dimensions()  # the file's own word for its size can be wrong ("A1")
                for r, row in enumerate(ws.iter_rows(values_only=True), 1):
                    if any(v is not None for v in row):
                        out.append(
                            f"{ws.title} row {r}: "
                            + " | ".join("" if v is None else str(v) for v in row).rstrip(" |")
                        )
            return out
        finally:
            wb.close()
    if ext == ".pptx":
        from pptx import Presentation

        out = []
        for i, slide in enumerate(Presentation(str(path)).slides, 1):
            for shape in slide.shapes:
                if shape.has_text_frame:
                    for p in shape.text_frame.paragraphs:
                        t = "".join(r.text for r in p.runs).strip()
                        if t:
                            out.append(f"Slide {i}: {t}")
        return out
    if ext in (".html", ".htm"):
        from pyxi.agents.pixel_render import html_blocks, read_any_text

        return [re.sub(r"<[^>]+>", "", b.text) for b in html_blocks(read_any_text(path)) if b.text]
    from pyxi.agents.pixel_render import read_any_text

    return [ln.strip() for ln in read_any_text(path).splitlines() if ln.strip()]


def _word_diff(a: str, b: str) -> str:
    """'pay $1,200 by June' vs 'pay $1,300 by July' -> '$1,200 -> $1,300; June -> July'."""
    wa, wb = a.split(), b.split()
    parts = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=wa, b=wb, autojunk=False).get_opcodes():
        if op == "equal":
            continue
        old, new = " ".join(wa[i1:i2]), " ".join(wb[j1:j2])
        if op == "replace":
            parts.append(f"'{old}' -> '{new}'")
        elif op == "delete":
            parts.append(f"removed '{old}'")
        else:
            parts.append(f"added '{new}'")
    return "; ".join(parts)


def compare_documents(files: list[Path], out: Path | None = None) -> PixelResult:
    """What changed from the first document to the second, as a short list and a Word report
    (removed text struck through in red, new text in green)."""
    from docx import Document
    from docx.shared import RGBColor

    files = need(files, COMPARABLE, "document", many=True)
    if len(files) != 2:
        raise PixelError(
            "Give me exactly two documents to compare: the old one first, then the new one."
        )
    a, b = files
    la, lb = document_lines(a), document_lines(b)
    if not la and not lb:
        raise PixelError("I couldn't read any text in either document.")
    changes: list[tuple[str, str, str]] = []  # (kind, old, new)
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=la, b=lb, autojunk=False).get_opcodes():
        if op == "equal":
            continue
        old, new = la[i1:i2], lb[j1:j2]
        if op == "replace":
            # Pair up similar lines as "changed"; the rest are removed or added.
            used = set()
            for o in old:
                best, score = None, 0.0
                for k, n in enumerate(new):
                    if k in used:
                        continue
                    s = difflib.SequenceMatcher(a=o, b=n, autojunk=False).ratio()
                    if s > score:
                        best, score = k, s
                if best is not None and score >= 0.5:
                    used.add(best)
                    changes.append(("changed", o, new[best]))
                else:
                    changes.append(("removed", o, ""))
            changes += [("added", "", n) for k, n in enumerate(new) if k not in used]
        elif op == "delete":
            changes += [("removed", o, "") for o in old]
        else:
            changes += [("added", "", n) for n in new]
    counts = {k: sum(1 for c in changes if c[0] == k) for k in ("changed", "added", "removed")}
    if not changes:
        return PixelResult(
            [], f"No differences in the text of {a.name} and {b.name}.", {"changes": []}
        )
    doc = Document()
    doc.add_heading(f"What changed: {a.name} to {b.name}", 1)
    doc.add_paragraph(
        f"{counts['changed']} changed, {counts['added']} added, {counts['removed']} removed."
    )
    red, green = RGBColor(0xC0, 0x1F, 0x1F), RGBColor(0x1A, 0x7F, 0x37)
    lines = []
    for kind, old, new in changes:
        p = doc.add_paragraph(style="List Bullet")
        p.add_run(kind.title() + ": ").bold = True
        if kind == "changed":
            lines.append(_word_diff(old, new) or f"'{old}' -> '{new}'")
            sm = difflib.SequenceMatcher(a=old.split(), b=new.split(), autojunk=False)
            wa, wb = old.split(), new.split()
            for op, i1, i2, j1, j2 in sm.get_opcodes():
                if op == "equal":
                    p.add_run(" ".join(wa[i1:i2]) + " ")
                    continue
                if i2 > i1:
                    r = p.add_run(" ".join(wa[i1:i2]) + " ")
                    r.font.strike, r.font.color.rgb = True, red
                if j2 > j1:
                    r = p.add_run(" ".join(wb[j1:j2]) + " ")
                    r.font.color.rgb, r.bold = green, True
        elif kind == "added":
            lines.append(f"added '{new[:120]}'")
            p.add_run(new).font.color.rgb = green
        else:
            lines.append(f"removed '{old[:120]}'")
            r = p.add_run(old)
            r.font.strike, r.font.color.rgb = True, red
    target = unique_path(out or out_dir_for(a), f"{a.stem} vs {b.stem} (changes).docx")
    doc.save(str(target))
    head = ", ".join(f"{v} {k}" for k, v in counts.items() if v)
    return PixelResult(
        [target],
        f"{head}. First: {lines[0][:160]}. The full list is in {target.name}.",
        {"changes": lines[:20], "counts": counts},
    )
