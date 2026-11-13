"""Pixel, the files teammate: anything document-shaped, done straight on the files, in the
background. No app windows, no mouse, and no model call.

* Images: to PDF, format changes (HEIC to JPG included), resizing, compressing.
* PDFs: merge, split, compress, rotate, reorder, delete or extract pages, page numbers, a text
  watermark, password-protect or unlock (the password comes from a card the user types into,
  never from the model), fill form fields from saved details.
* Conversions: Word, Excel, PowerPoint, Markdown, HTML and text to PDF; PDF to Word; PDF pages
  to images; CSV to and from Excel.
* Reading: text out of PDFs and images (Windows OCR for scans), and searchable PDFs from scans.
* Comparing two documents: what changed, as a list and a marked-up Word report.

Every result is a new file beside the original (or in Documents\\Pyxi when that folder can't be
written); nothing is ever overwritten. LibreOffice, when installed, renders Office files to PDF
with their exact layout; without it the built-in renderer (pixel_render) keeps text, headings,
lists, tables and pictures.
"""

from __future__ import annotations

import csv
import io
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from pyxi.agents import pixel_pdf, pixel_render
from pyxi.agents.pixel_common import (
    IMAGE_EXTS,
    IMAGE_FORMATS,
    PixelError,
    PixelResult,
    flat_rgb,
    image_pages,
    need,
    open_image,
    out_dir_for,
    parse_ranges,
    plain_name,
    pyxi_folder,
    size_text,
    unique_path,
)

__all__ = [
    "IMAGE_EXTS",
    "NEEDS_PASSWORD",
    "OPERATIONS",
    "PixelError",
    "PixelResult",
    "guess_operation",
    "out_dir_for",
    "parse_ranges",
    "pyxi_folder",
    "run",
    "unique_path",
]

OPERATIONS = {
    "images_to_pdf": "Put one or more images into a PDF, one page each, in the order given.",
    "to_pdf": "Turn Word, Excel, PowerPoint, Markdown, HTML or text files into PDF.",
    "word_to_pdf": "Turn a Word document into a PDF (same as to_pdf).",
    "pdf_to_word": "Turn a PDF into an editable Word document (text, headings, pictures).",
    "pdf_to_images": "Turn PDF pages into PNG or JPG images (options: pages, format, dpi).",
    "merge_pdfs": "Join PDFs into one, in the order given.",
    "split_pdf": "Split a PDF into several: every page on its own, or by pages like '1-2, 3'.",
    "extract_pages": "Copy some pages into one new PDF (options: pages).",
    "delete_pages": "Remove pages from a PDF (options: pages).",
    "rotate_pages": "Turn pages (options: pages, default all; angle 90, 180, 270, left or right).",
    "reorder_pages": "Put pages in a new order (options: order like '3, 1-2'; the rest follow).",
    "compress_pdf": "Make a PDF smaller (images re-saved at lower quality, streams compressed).",
    "add_page_numbers": "Number the pages (options: position like 'bottom right', start, format 'Page {n} of {total}').",
    "add_watermark": "Stamp text across every page (options: text, opacity).",
    "protect_pdf": "Lock a PDF with a password. The user types it on a card; never put it in options.",
    "unlock_pdf": "Remove a PDF's password. The user types it on a card; never put it in options.",
    "fill_form": "Fill a PDF form's fields from their saved details (options: values for anything else, by field name).",
    "extract_text": "Get the text out of a PDF or an image (OCR for scans) as a .txt.",
    "make_searchable": "Turn a scanned PDF or photos of pages into a searchable PDF (OCR).",
    "compare_documents": "List what changed between two documents (old first, then new).",
    "convert_image": "Change an image's format (jpg, png, webp, bmp, gif, tiff); HEIC photos to JPG.",
    "resize_image": "Resize an image (width and/or height in px, or a percent), keeping its shape.",
    "compress_image": "Make images smaller to send (options: quality 1-95, max_side in px).",
    "csv_to_excel": "Turn a CSV file into an Excel workbook (.xlsx).",
    "excel_to_csv": "Turn an Excel sheet into a CSV file (options: sheet).",
}

BUILT_IN_NOTE = " Install LibreOffice for an exact copy of the Office layout."

# The job asks the user for these on a password card, so the model never sees them.
NEEDS_PASSWORD = {"protect_pdf", "unlock_pdf"}

OFFICE = {
    ".docx",
    ".doc",
    ".odt",
    ".rtf",
    ".xlsx",
    ".xlsm",
    ".xls",
    ".ods",
    ".pptx",
    ".ppt",
    ".odp",
}
BUILT_IN = {
    ".docx",
    ".xlsx",
    ".xlsm",
    ".pptx",
    ".md",
    ".markdown",
    ".html",
    ".htm",
    ".txt",
    ".text",
    ".log",
    ".csv",
}
TO_PDF = OFFICE | BUILT_IN
# What to save an older or OpenDocument file as when there's no LibreOffice to read it. (The
# advice used to be the extension plus an "x": right for .doc, but ".rtfx" is no format.)
NEWER_FORMAT = {
    ".doc": ".docx",
    ".odt": ".docx",
    ".rtf": ".docx",
    ".xls": ".xlsx",
    ".ods": ".xlsx",
    ".ppt": ".pptx",
    ".odp": ".pptx",
}


# ---------------------------------------------------------------------------- images
def images_to_pdf(
    files: list[Path], name: str | None = None, out: Path | None = None
) -> PixelResult:
    files = need(files, IMAGE_EXTS, "image", many=True)
    # Every page of a fax or scanner TIFF, not just its first.
    pages = [flat_rgb(img) for p in files for img in image_pages(p)]
    target = unique_path(
        out or out_dir_for(files[0]), f"{plain_name(name, '.pdf') or files[0].stem}.pdf"
    )
    pages[0].save(target, "PDF", save_all=True, append_images=pages[1:], resolution=150)
    n, count = len(pages), len(files)
    more = f" ({n} pages)" if n != count else ""
    return PixelResult(
        [target],
        f"Made {target.name} from {count} image{'s' if count != 1 else ''}{more}.",
        {"pages": n},
    )


def convert_image(
    files: list[Path], to: str = "jpg", quality: int = 90, out: Path | None = None
) -> PixelResult:
    files = need(files, IMAGE_EXTS, "image", many=True)
    key = to.lower().lstrip(".")
    if key not in IMAGE_FORMATS:
        raise PixelError(f"I can save images as {', '.join(sorted(set(IMAGE_FORMATS)))}, not {to}.")
    fmt, ext = IMAGE_FORMATS[key]
    outputs = []
    for p in files:
        img = open_image(p)
        if fmt in ("JPEG", "BMP"):
            img = flat_rgb(img)
        target = unique_path(out or out_dir_for(p), p.stem + ext)
        kw: dict[str, Any] = {"quality": quality} if fmt in ("JPEG", "WEBP") else {}
        img.save(target, fmt, **kw)
        outputs.append(target)
    n = len(outputs)
    return PixelResult(
        outputs,
        f"Saved {n} image{'s' if n != 1 else ''} as {key.upper()}: {', '.join(o.name for o in outputs[:3])}.",
    )


def resize_image(
    files: list[Path],
    width: int | None = None,
    height: int | None = None,
    percent: float | None = None,
    out: Path | None = None,
) -> PixelResult:
    from PIL import Image

    files = need(files, IMAGE_EXTS, "image", many=True)
    if not (width or height or percent):
        raise PixelError("How big? Give a width, a height or a percent.")
    if any(v is not None and v < 0 for v in (width, height, percent)):
        raise PixelError("The new size has to be bigger than zero.")
    outputs, sizes = [], []
    for p in files:
        img = open_image(p)
        w0, h0 = img.size
        if percent:
            w, h = round(w0 * percent / 100), round(h0 * percent / 100)
        elif width and height:
            scale = min(width / w0, height / h0)  # fit inside the box, keep the shape
            w, h = round(w0 * scale), round(h0 * scale)
        elif width:
            w, h = width, round(h0 * width / w0)
        else:
            w, h = round(w0 * height / h0), height  # type: ignore[operator]
        w, h = max(1, w), max(1, h)
        if w * h > 150_000_000:  # past ~12000 x 12000 Pillow runs out of memory
            raise PixelError(f"{w}x{h} is too big for a picture. Try a smaller size.")
        small = img.resize((w, h), Image.LANCZOS)
        suffix = ".jpg" if p.suffix.lower() in (".heic", ".heif") else p.suffix
        target = unique_path(out or out_dir_for(p), f"{p.stem} ({w}x{h}){suffix}")
        if target.suffix.lower() in (".jpg", ".jpeg"):
            small = flat_rgb(small)
        small.save(target)
        outputs.append(target)
        sizes.append(f"{w0}x{h0} to {w}x{h}")
    return PixelResult(outputs, f"Resized {', '.join(sizes)}.")


def compress_image(
    files: list[Path], quality: int = 72, max_side: int = 2560, out: Path | None = None
) -> PixelResult:
    """Smaller images to send: scaled to max_side, metadata dropped, re-saved. Photos (and HEIC)
    become JPG; PNGs with transparency stay PNG, optimised. Bigger results are thrown away."""
    from PIL import Image

    files = need(files, IMAGE_EXTS, "image", many=True)
    outputs: list[Path] = []
    lines, before_all, after_all = [], 0, 0
    for p in files:
        before = p.stat().st_size
        img = open_image(p)
        if (
            before < 100_000
            and max(img.size) <= max_side
            and p.suffix.lower() not in (".heic", ".heif")
        ):
            lines.append(f"{p.name} was already small")  # under 100 KB: nothing worth saving
            continue
        if max(img.size) > max_side:
            img.thumbnail((max_side, max_side), Image.LANCZOS)
        alpha = img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info)
        ext = p.suffix.lower()
        buf = io.BytesIO()
        if alpha and ext in (".png", ".webp", ".gif"):
            if ext == ".webp":
                img.save(buf, "WEBP", quality=quality)
                out_ext = ".webp"
            else:
                img.convert("RGBA").quantize(256, method=Image.Quantize.FASTOCTREE).save(
                    buf, "PNG", optimize=True
                )
                out_ext = ".png"
        elif ext == ".webp":
            flat_rgb(img).save(buf, "WEBP", quality=quality)
            out_ext = ".webp"
        else:
            flat_rgb(img).save(buf, "JPEG", quality=quality, optimize=True, progressive=True)
            out_ext = ".jpg"
        after = buf.tell()
        if after >= before * 0.95:
            lines.append(f"{p.name} was already small")
            continue
        target = unique_path(out or out_dir_for(p), f"{p.stem} (smaller){out_ext}")
        target.write_bytes(buf.getvalue())
        outputs.append(target)
        before_all, after_all = before_all + before, after_all + after
        lines.append(f"{p.name} {size_text(before)} to {size_text(after)}")
    if not outputs:
        return PixelResult([], ". ".join(lines) + ". Nothing to shrink.")
    saved = round(100 * (1 - after_all / before_all))
    return PixelResult(
        outputs,
        f"Shrank {len(outputs)} image{'s' if len(outputs) != 1 else ''} by {saved}%: {'; '.join(lines[:3])}.",
    )


# ---------------------------------------------------------------------------- PDFs
def merge_pdfs(files: list[Path], name: str | None = None, out: Path | None = None) -> PixelResult:
    from pypdf import PdfWriter

    files = need(files, {".pdf"}, "PDF", many=True)
    if len(files) < 2:
        raise PixelError("I need at least two PDFs to merge.")
    writer = PdfWriter()
    for p in files:
        pixel_pdf._reader(p)  # a clear message for a locked or broken one
        writer.append(str(p))
    wanted = plain_name(name, ".pdf") or files[0].stem + " (merged)"
    target = unique_path(out or out_dir_for(files[0]), f"{wanted}.pdf")
    with target.open("wb") as fh:
        writer.write(fh)
    pages = len(writer.pages)
    return PixelResult(
        [target], f"Merged {len(files)} PDFs into {target.name} ({pages} pages).", {"pages": pages}
    )


def split_pdf(files: list[Path], ranges: str = "", out: Path | None = None) -> PixelResult:
    """One PDF per range ('1-2, 3' makes two), or one per page when no ranges are given."""
    from pypdf import PdfWriter

    (src,) = need(files, {".pdf"}, "PDF")
    reader = pixel_pdf._reader(src)
    n = len(reader.pages)
    parts = parse_ranges(ranges, n) if ranges.strip() else [(i, i) for i in range(1, n + 1)]
    folder = out or out_dir_for(src)
    if len(parts) > 3:  # lots of pieces get their own folder
        folder = unique_path(folder, f"{src.stem} (split)")
        folder.mkdir(parents=True)
    outputs = []
    for a, b in parts:
        writer = PdfWriter()
        for i in range(a - 1, b):
            writer.add_page(reader.pages[i])
        label = f"page {a}" if a == b else f"pages {a}-{b}"
        target = unique_path(folder, f"{src.stem} ({label}).pdf")
        with target.open("wb") as fh:
            writer.write(fh)
        outputs.append(target)
    pieces = ", ".join(o.name for o in outputs[:4]) + (", ..." if len(outputs) > 4 else "")
    return PixelResult(
        outputs, f"Split {src.name} into {len(outputs)} PDFs: {pieces}.", {"folder": str(folder)}
    )


def _soft_mask(writer: Any, ref: Any, alpha: Any) -> None:
    """Give the image at ``ref`` this transparency (an 8-bit grey picture of the same size)."""
    from pypdf.generic import DecodedStreamObject, NameObject, NumberObject

    mask = DecodedStreamObject()
    mask.set_data(alpha.convert("L").tobytes())
    mask.update(
        {
            NameObject("/Type"): NameObject("/XObject"),
            NameObject("/Subtype"): NameObject("/Image"),
            NameObject("/Width"): NumberObject(alpha.width),
            NameObject("/Height"): NumberObject(alpha.height),
            NameObject("/ColorSpace"): NameObject("/DeviceGray"),
            NameObject("/BitsPerComponent"): NumberObject(8),
        }
    )
    ref.get_object()[NameObject("/SMask")] = writer._add_object(mask.flate_encode())


def compress_pdf(
    files: list[Path], quality: int = 60, max_px: int = 1600, out: Path | None = None
) -> PixelResult:
    """Smaller PDFs: big images are scaled down and re-saved as JPEG, then streams are compressed
    and identical objects shared. Text stays text."""
    from PIL import Image
    from pypdf import PdfWriter

    (src,) = need(files, {".pdf"}, "PDF")
    before = src.stat().st_size
    writer = PdfWriter(clone_from=pixel_pdf._reader(src))
    for page in writer.pages:
        for img in page.images:
            try:
                pic = img.image
                if pic is None:
                    continue
                ref = img.indirect_reference
                xobj = ref.get_object() if ref is not None else {}
                # A stencil or a colour-keyed image is left alone: as a JPEG it would lose
                # what makes it see-through, and a black-and-white scan gets bigger.
                if "/ImageMask" in xobj or "/Mask" in xobj:
                    continue
                alpha = None
                if pic.mode in ("RGBA", "LA"):
                    # A picture with a soft mask, which is how Canva, Keynote and PowerPoint
                    # export every photo. Most of those masks hide nothing: then it is an
                    # ordinary photo. (Skipping them all, "make this smaller" did nothing.)
                    alpha = pic.getchannel("A")
                    if alpha.getextrema()[0] == 255:
                        alpha = None
                    pic = pic.convert("L" if pic.mode == "LA" else "RGB")
                elif pic.mode not in ("RGB", "L", "CMYK") or "/SMask" in xobj:
                    continue
                if max(pic.size) > max_px:
                    scale = max_px / max(pic.size)
                    size = (max(1, round(pic.width * scale)), max(1, round(pic.height * scale)))
                    pic = pic.resize(size, Image.LANCZOS)
                    if alpha is not None:
                        alpha = alpha.resize(size, Image.LANCZOS)
                img.replace(pic if pic.mode == "L" else flat_rgb(pic), quality=quality)
                if alpha is not None:
                    # Really see-through (a logo): the picture is re-saved and its mask goes
                    # back on at the same size. As a bare JPEG it came back in a white box.
                    _soft_mask(writer, ref, alpha)
            except Exception:  # an image pypdf can't re-encode stays as it is
                continue
        page.compress_content_streams()
    try:
        writer.compress_identical_objects(remove_duplicates=True, remove_unreferenced=True)
    except TypeError:  # older pypdf names them differently
        writer.compress_identical_objects(remove_identicals=True, remove_orphans=True)
    buf = io.BytesIO()
    writer.write(buf)
    after = buf.tell()
    if after >= before * 0.98:
        return PixelResult(
            [],
            f"{src.name} is already about as small as it gets ({size_text(before)}).",
            {"before": before, "after": before},
        )
    target = unique_path(out or out_dir_for(src), f"{src.stem} (smaller).pdf")
    target.write_bytes(buf.getvalue())
    saved = round(100 * (1 - after / before))
    return PixelResult(
        [target],
        f"Made {target.name}: {size_text(before)} down to {size_text(after)}, {saved}% smaller.",
        {"before": before, "after": after},
    )


def pdf_to_images(
    files: list[Path], fmt: str = "png", dpi: int = 150, pages: str = "", out: Path | None = None
) -> PixelResult:
    import pypdfium2 as pdfium

    (src,) = need(files, {".pdf"}, "PDF")
    key = fmt.lower().lstrip(".")
    if key not in ("png", "jpg", "jpeg"):
        raise PixelError("PDF pages can come out as PNG or JPG.")
    pil_fmt, ext = IMAGE_FORMATS[key]
    pixel_pdf._reader(src)
    doc = pdfium.PdfDocument(str(src))
    try:
        n = len(doc)
        wanted = (
            [i for a, b in parse_ranges(pages, n) for i in range(a, b + 1)]
            if pages.strip()
            else list(range(1, n + 1))
        )
        folder = out or out_dir_for(src)
        if len(wanted) > 3:
            folder = unique_path(folder, f"{src.stem} (pages)")
            folder.mkdir(parents=True)
        outputs = []
        for i in wanted:
            img = doc[i - 1].render(scale=dpi / 72).to_pil()
            if pil_fmt == "JPEG":
                img = flat_rgb(img)
            target = unique_path(folder, f"{src.stem} page {i}{ext}")
            img.save(target, pil_fmt, **({"quality": 90} if pil_fmt == "JPEG" else {}))
            outputs.append(target)
    finally:
        doc.close()
    return PixelResult(
        outputs,
        f"Saved {len(outputs)} page{'s' if len(outputs) != 1 else ''} of {src.name} as {key.upper()}.",
    )


# ---------------------------------------------------------------------------- to PDF
def libreoffice() -> str | None:
    """soffice: Pyxi's own copy (set up on demand, see agents/libreoffice.py), or one the user
    installed themselves. None when there's neither."""
    from pyxi.agents.libreoffice import installed

    if own := installed():
        return str(own)
    found = shutil.which("soffice") or shutil.which("soffice.exe")
    if found:
        return found
    for base in (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)")):
        if base and (p := Path(base) / "LibreOffice" / "program" / "soffice.exe").is_file():
            return str(p)
    return None


def to_pdf(files: list[Path], out: Path | None = None, *, sheet: str = "") -> PixelResult:
    """Office files go through LibreOffice when it's installed (exact layout), otherwise through the
    built-in renderer; Markdown, HTML and text always use the built-in one."""
    files = need(files, TO_PDF, "document Pixel can turn into PDF", many=True)
    soffice = libreoffice()
    outputs, used_builtin = [], False
    failed: list[str] = []
    for src in files:
        ext = src.suffix.lower()
        target = unique_path(out or out_dir_for(src), f"{src.stem}.pdf")
        try:
            if soffice and ext in OFFICE:
                _soffice_pdf(soffice, src, target)
            elif ext in BUILT_IN:
                pixel_render.to_pdf(src, target, sheet=sheet)
                used_builtin = used_builtin or ext in OFFICE
            else:
                newer = NEWER_FORMAT.get(ext, ".docx")
                raise PixelError(
                    f"{ext} files need LibreOffice to turn into PDF; save {src.name} as "
                    f"{newer}, or install LibreOffice."
                )
        except PixelError as exc:
            if len(files) == 1:
                raise
            failed.append(str(exc))  # the others are still made, and this one is said
            continue
        outputs.append(target)
    if not outputs:
        raise PixelError(" ".join(failed))
    note = BUILT_IN_NOTE if used_builtin else ""
    if failed:
        note += " But " + " ".join(failed)
    pages = []
    for o in outputs:
        try:
            from pypdf import PdfReader

            pages.append(len(PdfReader(str(o)).pages))
        except Exception:
            pages.append(0)
    made = ", ".join(
        f"{o.name} ({n} page{'s' if n != 1 else ''})" for o, n in zip(outputs, pages, strict=True)
    )
    return PixelResult(outputs, f"Made {made}.{note}")


def _soffice_pdf(soffice: str, src: Path, target: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        from pyxi.agents.libreoffice import CONVERTING, command

        try:
            # One conversion at a time: two LibreOffices sharing Pyxi's profile get in each
            # other's way, and one of two jobs started together failed every time.
            with CONVERTING:
                subprocess.run(
                    command(soffice, "--convert-to", "pdf", "--outdir", tmp, str(src)),
                    check=True,
                    capture_output=True,
                    timeout=180,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
        except subprocess.TimeoutExpired:
            raise PixelError(f"LibreOffice took too long with {src.name}.") from None
        except subprocess.CalledProcessError:  # never the command line on the card
            raise PixelError(
                f"LibreOffice couldn't convert {src.name}. If it's open in another app, close "
                "it and try again."
            ) from None
        made = Path(tmp) / f"{src.stem}.pdf"
        if not made.is_file():
            raise PixelError(f"LibreOffice couldn't convert {src.name}.")
        shutil.move(str(made), target)


# ---------------------------------------------------------------------------- CSV and Excel
def _cell(value: str) -> Any:
    v = value.strip()
    if re.fullmatch(r"-?(\d+|\d{1,3}(,\d{3})+)", v) and sum(c.isdigit() for c in v) > 15:
        # A card, tracking or account number. Excel keeps 15 digits of a number and turns the
        # rest into zeros, so a long whole number stays text, every digit of it. A long
        # decimal (0.30000000000000004) is still a number: as text it dropped out of a SUM.
        return value
    if re.fullmatch(r"-?\d+", v) and not (len(v) > 1 and v.lstrip("-").startswith("0")):
        return int(v)
    if re.fullmatch(r"-?\d*\.\d+|-?\d{1,3}(,\d{3})+(\.\d+)?", v):
        try:
            return float(v.replace(",", ""))
        except ValueError:
            return value
    return value


def read_csv(src: Path) -> list[list[str]]:
    text = pixel_render.read_any_text(src)
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel_tab if src.suffix.lower() == ".tsv" else csv.excel
    return list(csv.reader(io.StringIO(text), dialect))


def csv_to_excel(files: list[Path], out: Path | None = None) -> PixelResult:
    from openpyxl import Workbook
    from openpyxl.styles import Font

    files = need(files, {".csv", ".tsv", ".txt"}, "CSV file", many=True)
    outputs = []
    for src in files:
        rows = read_csv(src)
        if not any(any(c.strip() for c in r) for r in rows):
            raise PixelError(f"{src.name} is empty, so there's nothing to put in a spreadsheet.")
        wb = Workbook()
        ws = wb.active
        ws.title = re.sub(r"[\[\]:*?/\\]", "_", src.stem)[:31] or "Sheet1"
        for r in rows:
            ws.append([_cell(v) for v in r])
        if rows:
            for c in ws[1]:
                c.font = Font(bold=True)
            ws.freeze_panes = "A2"
            for col in ws.columns:
                width = max((len(str(c.value)) for c in col if c.value is not None), default=8)
                ws.column_dimensions[col[0].column_letter].width = min(max(width + 2, 8), 60)
        target = unique_path(out or out_dir_for(src), f"{src.stem}.xlsx")
        wb.save(target)
        outputs.append(target)
    return PixelResult(outputs, f"Made {', '.join(o.name for o in outputs)}.")


def _csv_value(v: Any) -> Any:
    """A cell for a CSV: a date as a date (not "2026-03-05 00:00:00"), and a sum as the number
    Excel shows (0.3, not 0.30000000000000004)."""
    import datetime as dt

    if v is None:
        return ""
    if isinstance(v, dt.datetime):
        midnight = not (v.hour or v.minute or v.second or v.microsecond)
        return v.date().isoformat() if midnight else v.isoformat(sep=" ", timespec="seconds")
    if isinstance(v, float):
        return float(f"{v:.15g}")
    return v


def excel_to_csv(files: list[Path], sheet: str = "", out: Path | None = None) -> PixelResult:
    from openpyxl import load_workbook

    (src,) = need(files, {".xlsx", ".xlsm"}, "Excel workbook")
    wb = load_workbook(src, data_only=True, read_only=True)
    try:
        if sheet and sheet not in wb.sheetnames:
            raise PixelError(
                f"There's no sheet called {sheet!r}; it has {', '.join(wb.sheetnames)}."
            )
        names = [sheet] if sheet else wb.sheetnames[:1]
        outputs = []
        for name in names:
            label = src.stem if len(wb.sheetnames) == 1 else f"{src.stem} ({name})"
            target = unique_path(out or out_dir_for(src), f"{label}.csv")
            ws = wb[name]
            # Some exports say the sheet is one cell big (<dimension ref="A1"/>); believed,
            # the CSV had that one cell. Reading the rows themselves finds them all.
            ws.reset_dimensions()
            with target.open("w", newline="", encoding="utf-8-sig") as fh:
                w = csv.writer(fh)
                for row in ws.iter_rows(values_only=True):
                    w.writerow([_csv_value(v) for v in row])
            outputs.append(target)
        count = len(wb.sheetnames)
    finally:
        wb.close()
    extra = f" (sheet {names[0]}; it has {count})" if count > 1 else ""
    return PixelResult(outputs, f"Made {outputs[0].name}{extra}.")


# ---------------------------------------------------------------------------- one entry point
# Operations that work on one file: given several ("compress these three PDFs"), each is done
# in turn instead of quietly doing only the first.
ONE_FILE = {
    "split_pdf",
    "extract_pages",
    "delete_pages",
    "rotate_pages",
    "reorder_pages",
    "compress_pdf",
    "add_page_numbers",
    "add_watermark",
    "protect_pdf",
    "unlock_pdf",
    "fill_form",
    "extract_text",
    "pdf_to_word",
    "pdf_to_images",
    "excel_to_csv",
}


def run(
    operation: str, files: list[str | Path], options: dict[str, Any] | None = None
) -> PixelResult:
    """Do one operation. Blocking (call it in a thread); raises PixelError for a bad request."""
    if operation in ONE_FILE and len(files) > 1:
        for f in files:  # a missing or wrong file is said before any work is done
            p = Path(str(f)).expanduser()
            if not p.is_file():
                need([p], {p.suffix.lower()}, "file")
        results: list[PixelResult] = []
        failed: list[str] = []
        for f in files:
            try:
                results.append(_run_one(operation, [f], options))
            except PixelError as exc:
                # The files already done are on the disk: they are reported, with this one's
                # trouble said after them, not lost behind a "failed".
                failed.append(str(exc))
        if not results:
            raise PixelError(" ".join(failed))
        outputs = [o for r in results for o in r.outputs]
        summary = " ".join(r.summary for r in results)
        if failed:
            summary += " But " + " ".join(failed)
        return PixelResult(
            outputs, summary, {"each": [r.details for r in results], "failed": failed}
        )
    return _run_one(operation, files, options)


def _run_one(
    operation: str, files: list[str | Path], options: dict[str, Any] | None = None
) -> PixelResult:
    o = dict(options or {})
    paths = [Path(str(f)).expanduser() for f in files]
    out = Path(str(o["out_dir"])) if o.get("out_dir") else None

    def num(key: str) -> Any:
        v = o.get(key)
        if v in (None, ""):
            return None
        try:
            return float(v) if key in ("percent", "opacity") else int(float(v))
        except (TypeError, ValueError):
            raise PixelError(f"{key} should be a number, not {v!r}.") from None

    def pages(*keys: str) -> str:
        for k in keys:
            v = o.get(k)
            if isinstance(v, list):
                v = ", ".join(str(x) for x in v)
            if v not in (None, ""):
                return str(v)
        return ""

    op = {"word_to_pdf": "to_pdf", "heic_to_jpg": "convert_image"}.get(operation, operation)
    if op == "images_to_pdf":
        return images_to_pdf(paths, o.get("name"), out)
    if op == "to_pdf":
        return to_pdf(paths, out, sheet=str(o.get("sheet") or ""))
    if op == "pdf_to_word":
        return pixel_pdf.pdf_to_word(paths, out)
    if op == "merge_pdfs":
        return merge_pdfs(paths, o.get("name"), out)
    if op == "split_pdf":
        return split_pdf(paths, pages("ranges", "pages"), out)
    if op == "extract_pages":
        return pixel_pdf.extract_pages(paths, pages("pages", "ranges"), out)
    if op == "delete_pages":
        return pixel_pdf.delete_pages(paths, pages("pages", "ranges"), out)
    if op == "rotate_pages":
        return pixel_pdf.rotate_pages(paths, pages("pages", "ranges"), o.get("angle") or 90, out)
    if op == "reorder_pages":
        return pixel_pdf.reorder_pages(paths, pages("order", "pages"), out)
    if op == "compress_pdf":
        return compress_pdf(paths, num("quality") or 60, out=out)
    if op == "add_page_numbers":
        return pixel_pdf.add_page_numbers(
            paths,
            str(o.get("position") or "bottom center"),
            num("start") or 1,
            str(o.get("format") or ""),
            out,
        )
    if op == "add_watermark":
        return pixel_pdf.add_watermark(
            paths, str(o.get("text") or ""), num("opacity") or 0.15, out=out
        )
    if op == "protect_pdf":
        return pixel_pdf.protect_pdf(paths, str(o.get("password") or ""), out)
    if op == "unlock_pdf":
        return pixel_pdf.unlock_pdf(paths, str(o.get("password") or ""), out)
    if op == "fill_form":
        values = o.get("values") if isinstance(o.get("values"), dict) else {}
        return pixel_pdf.fill_form(
            paths, o.get("details") or {}, values, out, o.get("placed"), o.get("labels")
        )
    if op == "extract_text":
        return pixel_pdf.extract_text(paths, out)
    if op == "make_searchable":
        return pixel_pdf.make_searchable(paths, num("dpi") or 200, out)
    if op == "compare_documents":
        return pixel_pdf.compare_documents(paths, out)
    if op == "pdf_to_images":
        return pdf_to_images(
            paths, str(o.get("format") or "png"), num("dpi") or 150, pages("pages", "ranges"), out
        )
    if op == "convert_image":
        return convert_image(paths, str(o.get("format") or "jpg"), num("quality") or 90, out)
    if op == "resize_image":
        return resize_image(paths, num("width"), num("height"), num("percent"), out)
    if op == "compress_image":
        return compress_image(paths, num("quality") or 72, num("max_side") or 2560, out)
    if op == "csv_to_excel":
        return csv_to_excel(paths, out)
    if op == "excel_to_csv":
        return excel_to_csv(paths, str(o.get("sheet") or ""), out)
    raise PixelError(f"I don't know how to {operation!r}. I can: {', '.join(OPERATIONS)}.")


def guess_operation(task: str, files: list[str]) -> str | None:
    """For a plain `delegate` to Pixel with no operation: pick one from the words and files."""
    t = task.lower()
    exts = {Path(f).suffix.lower() for f in files}
    pdf = ".pdf" in exts
    images = bool(exts & IMAGE_EXTS)
    into_something = bool(
        re.search(r"\b(in)?to\b.*\b(jpe?gs?|pngs?|images?|pictures?|photos?|word|docx|text)\b", t)
    )
    rules: list[tuple[bool, str]] = [
        (
            len(files) == 2 and bool(re.search(r"compare|differen|what changed|diff\b", t)),
            "compare_documents",
        ),
        (pdf and bool(re.search(r"unlock|remove (the )?password|decrypt", t)), "unlock_pdf"),
        (pdf and bool(re.search(r"password|protect|\block|encrypt", t)), "protect_pdf"),
        # The word "form", not the letters: "to Word format" and "reformat" asked for no form.
        (pdf and bool(re.search(r"\bfill\b|\bforms?\b", t)), "fill_form"),
        (pdf and bool(re.search(r"page numbers?|number the pages", t)), "add_page_numbers"),
        (pdf and "watermark" in t, "add_watermark"),
        (
            # "Turn each page into a JPG" turns nothing around.
            pdf and bool(re.search(r"rotate|turn .*(page|sideways|upside)|sideways", t))
            and not (re.search(r"\bturn\b", t) and into_something),
            "rotate_pages",
        ),
        (pdf and bool(re.search(r"reorder|re-order|move page|order of", t)), "reorder_pages"),
        (pdf and bool(re.search(r"delete|remove|drop", t)) and "page" in t, "delete_pages"),
        (
            pdf and bool(re.search(r"extract|pull out|just pages?|only pages?", t)) and "page" in t,
            "extract_pages",
        ),
        (bool(re.search(r"searchable|ocr", t)), "make_searchable"),
        ((pdf or images) and bool(re.search(r"\btext\b|words|transcribe", t)), "extract_text"),
        (pdf and bool(re.search(r"\bword\b|docx|editable", t)), "pdf_to_word"),
        (pdf and bool(re.search(r"smaller|compress|shrink|reduce", t)), "compress_pdf"),
        (
            images and bool(re.search(r"compress|smaller to send|shrink|reduce", t)),
            "compress_image",
        ),
        (
            bool(re.search(r"merge|combine|join", t)),
            "merge_pdfs" if exts <= {".pdf"} else "images_to_pdf",
        ),
        (
            pdf
            and bool(re.search(r"split|separate|each page", t))
            and not re.search(r"image|png|jpe?g|picture", t),  # "each page as a JPG" is images
            "split_pdf",
        ),
        (images and "pdf" in t, "images_to_pdf"),
        (bool(exts & (TO_PDF - {".csv"})) and "pdf" in t, "to_pdf"),
        (pdf and bool(re.search(r"image|png|jpe?g|picture", t)), "pdf_to_images"),
        (bool(exts & {".csv", ".tsv"}) and "pdf" not in t, "csv_to_excel"),
        (bool(exts & {".xlsx", ".xlsm"}) and "csv" in t, "excel_to_csv"),
        (images and bool(re.search(r"resize|bigger|width|height|%|percent|px", t)), "resize_image"),
        (images and bool(re.search(r"smaller", t)), "compress_image"),
        (images, "convert_image"),
    ]
    return next((op for ok, op in rules if ok), None)
