"""Pixel, the files teammate: conversions done straight on the files, in the background.

No app windows and no mouse: images to PDF, merge, split and compress PDFs, Word to PDF, PDF
to images, image format and size, CSV to and from Excel. Every result is a new file beside the
original (or in Documents\\Pyxi when that folder can't be written), and nothing is ever
overwritten: a name that's taken gets " (2)", " (3)" and so on.

Uses pypdf, Pillow, python-docx, openpyxl and pypdfium2. LibreOffice, when it's installed, does
Word to PDF with full fidelity; without it the text, headings, lists and tables still come
through via reportlab.
"""

from __future__ import annotations

import csv
import io
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff", ".heic"}
IMAGE_FORMATS = {
    "jpg": ("JPEG", ".jpg"),
    "jpeg": ("JPEG", ".jpg"),
    "png": ("PNG", ".png"),
    "webp": ("WEBP", ".webp"),
    "bmp": ("BMP", ".bmp"),
    "gif": ("GIF", ".gif"),
    "tiff": ("TIFF", ".tiff"),
}

OPERATIONS = {
    "images_to_pdf": "Put one or more images into a PDF, one page each, in the order given.",
    "merge_pdfs": "Join PDFs into one, in the order given.",
    "split_pdf": "Split a PDF: every page on its own, or by page ranges like '1-3, 4, 5-9'.",
    "compress_pdf": "Make a PDF smaller (images re-saved at lower quality, streams compressed).",
    "word_to_pdf": "Turn a Word document (.docx) into a PDF.",
    "pdf_to_images": "Turn each page of a PDF into a PNG or JPG image.",
    "convert_image": "Change an image's format (png, jpg, webp, bmp, gif, tiff).",
    "resize_image": "Resize an image (width and/or height in px, or a percent), keeping its shape.",
    "csv_to_excel": "Turn a CSV file into an Excel workbook (.xlsx).",
    "excel_to_csv": "Turn an Excel sheet into a CSV file.",
}


class PixelError(ValueError):
    """Something about the request or the file, said the way you'd tell a person."""


@dataclass
class PixelResult:
    outputs: list[Path]
    summary: str
    details: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------- where files go
def pyxi_folder() -> Path:
    return Path.home() / "Documents" / "Pyxi"


def _writable(folder: Path) -> bool:
    try:
        folder.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=folder):
            pass
        return True
    except OSError:
        return False


def out_dir_for(source: Path, fallback: Path | None = None) -> Path:
    """Beside the original when that folder can be written, else Documents\\Pyxi."""
    if _writable(source.parent):
        return source.parent
    folder = fallback or pyxi_folder()
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def unique_path(folder: Path, name: str) -> Path:
    """A path in ``folder`` that doesn't exist yet: "name.pdf", then "name (2).pdf"..."""
    stem, ext = os.path.splitext(name)
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", stem).strip(" .") or "file"
    path = folder / f"{stem}{ext}"
    n = 2
    while path.exists():
        path = folder / f"{stem} ({n}){ext}"
        n += 1
    return path


def _need(paths: list[Path], exts: set[str], what: str, *, many: bool = False) -> list[Path]:
    if not paths:
        raise PixelError(f"Which {what}? I need the file's path.")
    for p in paths:
        if not p.is_file():
            raise PixelError(f"I can't find {p.name} at {p.parent}.")
        if p.suffix.lower() not in exts:
            raise PixelError(f"{p.name} isn't a {what}.")
    if not many and len(paths) > 1:
        return paths[:1]
    return paths


def _size(n: int) -> str:
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024  # type: ignore[assignment]
    return f"{n} bytes"


# ---------------------------------------------------------------------------- images
def _open_image(path: Path):
    from PIL import Image, ImageOps

    img = Image.open(path)
    return ImageOps.exif_transpose(img)  # phone photos come out the right way up


def _flat_rgb(img):
    from PIL import Image

    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1])
        return bg
    return img.convert("RGB")


def images_to_pdf(
    files: list[Path], name: str | None = None, out: Path | None = None
) -> PixelResult:
    files = _need(files, IMAGE_EXTS, "image", many=True)
    pages = [_flat_rgb(_open_image(p)) for p in files]
    folder = out or out_dir_for(files[0])
    target = unique_path(folder, f"{name or files[0].stem}.pdf")
    pages[0].save(target, "PDF", save_all=True, append_images=pages[1:], resolution=150)
    n = len(pages)
    return PixelResult(
        [target],
        f"Made {target.name} from {n} image{'s' if n != 1 else ''}.",
        {"pages": n},
    )


def convert_image(
    files: list[Path], to: str = "png", quality: int = 90, out: Path | None = None
) -> PixelResult:
    files = _need(files, IMAGE_EXTS, "image", many=True)
    key = to.lower().lstrip(".")
    if key not in IMAGE_FORMATS:
        raise PixelError(f"I can save images as {', '.join(sorted(set(IMAGE_FORMATS)))}, not {to}.")
    fmt, ext = IMAGE_FORMATS[key]
    outputs = []
    for p in files:
        img = _open_image(p)
        if fmt in ("JPEG", "BMP"):
            img = _flat_rgb(img)
        target = unique_path(out or out_dir_for(p), p.stem + ext)
        kw: dict[str, Any] = {"quality": quality} if fmt in ("JPEG", "WEBP") else {}
        img.save(target, fmt, **kw)
        outputs.append(target)
    return PixelResult(
        outputs, f"Saved {len(outputs)} image{'s' if len(outputs) != 1 else ''} as {key.upper()}."
    )


def resize_image(
    files: list[Path],
    width: int | None = None,
    height: int | None = None,
    percent: float | None = None,
    out: Path | None = None,
) -> PixelResult:
    from PIL import Image

    files = _need(files, IMAGE_EXTS, "image", many=True)
    if not (width or height or percent):
        raise PixelError("How big? Give a width, a height or a percent.")
    outputs, sizes = [], []
    for p in files:
        img = _open_image(p)
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
        small = img.resize((w, h), Image.LANCZOS)
        target = unique_path(out or out_dir_for(p), f"{p.stem} ({w}x{h}){p.suffix}")
        if target.suffix.lower() in (".jpg", ".jpeg"):
            small = _flat_rgb(small)
        small.save(target)
        outputs.append(target)
        sizes.append(f"{w0}x{h0} to {w}x{h}")
    return PixelResult(outputs, f"Resized {', '.join(sizes)}.")


# ---------------------------------------------------------------------------- PDFs
def merge_pdfs(files: list[Path], name: str | None = None, out: Path | None = None) -> PixelResult:
    from pypdf import PdfWriter

    files = _need(files, {".pdf"}, "PDF", many=True)
    if len(files) < 2:
        raise PixelError("I need at least two PDFs to merge.")
    writer = PdfWriter()
    for p in files:
        writer.append(str(p))
    target = unique_path(out or out_dir_for(files[0]), f"{name or files[0].stem + ' (merged)'}.pdf")
    with target.open("wb") as fh:
        writer.write(fh)
    pages = len(writer.pages)
    return PixelResult(
        [target], f"Merged {len(files)} PDFs into {target.name} ({pages} pages).", {"pages": pages}
    )


def parse_ranges(spec: str, pages: int) -> list[tuple[int, int]]:
    """'1-3, 5, 7-' -> [(1, 3), (5, 5), (7, pages)], 1-based and inclusive."""
    out = []
    for part in re.split(r"[,;]\s*", spec.strip()):
        if not part:
            continue
        m = re.fullmatch(r"(\d*)\s*(?:-|to)\s*(\d*)|(\d+)", part.strip())
        if not m:
            raise PixelError(f"I don't understand the page range {part!r}.")
        if m.group(3):
            a = b = int(m.group(3))
        else:
            a = int(m.group(1) or 1)
            b = int(m.group(2) or pages)
        if not 1 <= a <= b <= pages:
            raise PixelError(f"Pages {part} aren't in this PDF (it has {pages}).")
        out.append((a, b))
    return out


def split_pdf(files: list[Path], ranges: str = "", out: Path | None = None) -> PixelResult:
    from pypdf import PdfReader, PdfWriter

    (src,) = _need(files, {".pdf"}, "PDF")
    reader = PdfReader(str(src))
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
    return PixelResult(
        outputs, f"Split {src.name} into {len(outputs)} PDFs.", {"folder": str(folder)}
    )


def compress_pdf(
    files: list[Path], quality: int = 60, max_px: int = 1600, out: Path | None = None
) -> PixelResult:
    """Smaller PDFs: big images are scaled down and re-saved as JPEG, then streams are compressed
    and identical objects shared. Text stays text."""
    from PIL import Image
    from pypdf import PdfReader, PdfWriter

    (src,) = _need(files, {".pdf"}, "PDF")
    before = src.stat().st_size
    writer = PdfWriter(clone_from=PdfReader(str(src)))
    for page in writer.pages:
        for img in page.images:
            try:
                pic = img.image
                if pic is None:
                    continue
                if max(pic.size) > max_px:
                    pic.thumbnail((max_px, max_px), Image.LANCZOS)
                img.replace(_flat_rgb(pic), quality=quality)
            except Exception:  # an image pypdf can't re-encode stays as it is
                continue
        page.compress_content_streams()
    try:
        writer.compress_identical_objects(remove_duplicates=True, remove_unreferenced=True)
    except TypeError:  # pypdf before 6.x names them differently
        writer.compress_identical_objects(remove_identicals=True, remove_orphans=True)
    buf = io.BytesIO()
    writer.write(buf)
    after = buf.tell()
    if after >= before * 0.98:
        return PixelResult(
            [],
            f"{src.name} is already about as small as it gets ({_size(before)}).",
            {"before": before, "after": before},
        )
    target = unique_path(out or out_dir_for(src), f"{src.stem} (smaller).pdf")
    target.write_bytes(buf.getvalue())
    saved = round(100 * (1 - after / before))
    return PixelResult(
        [target],
        f"Made {target.name}: {_size(before)} down to {_size(after)}, {saved}% smaller.",
        {"before": before, "after": after},
    )


def pdf_to_images(
    files: list[Path], fmt: str = "png", dpi: int = 150, pages: str = "", out: Path | None = None
) -> PixelResult:
    import pypdfium2 as pdfium

    (src,) = _need(files, {".pdf"}, "PDF")
    key = fmt.lower().lstrip(".")
    if key not in ("png", "jpg", "jpeg"):
        raise PixelError("PDF pages can come out as PNG or JPG.")
    pil_fmt, ext = IMAGE_FORMATS[key]
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
                img = _flat_rgb(img)
            target = unique_path(folder, f"{src.stem} page {i}{ext}")
            img.save(target, pil_fmt, **({"quality": 90} if pil_fmt == "JPEG" else {}))
            outputs.append(target)
    finally:
        doc.close()
    return PixelResult(
        outputs,
        f"Saved {len(outputs)} page{'s' if len(outputs) != 1 else ''} of {src.name} as {key.upper()}.",
    )


# ---------------------------------------------------------------------------- Word to PDF
def libreoffice() -> str | None:
    """soffice, if LibreOffice is installed (it's optional)."""
    found = shutil.which("soffice") or shutil.which("soffice.exe")
    if found:
        return found
    for base in (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)")):
        if base and (p := Path(base) / "LibreOffice" / "program" / "soffice.exe").is_file():
            return str(p)
    return None


def word_to_pdf(
    files: list[Path], out: Path | None = None, *, use_libreoffice: bool = True
) -> PixelResult:
    files = _need(files, {".docx", ".doc", ".odt", ".rtf"}, "Word document", many=True)
    soffice = libreoffice() if use_libreoffice else None
    outputs = []
    how = "LibreOffice" if soffice else "the built-in converter"
    for src in files:
        target = unique_path(out or out_dir_for(src), f"{src.stem}.pdf")
        if soffice:
            _soffice_pdf(soffice, src, target)
        elif src.suffix.lower() == ".docx":
            _docx_pdf(src, target)
        else:
            raise PixelError(
                f"{src.suffix} files need LibreOffice to turn into PDF; .docx works without it."
            )
        outputs.append(target)
    note = "" if soffice else " Install LibreOffice for an exact copy of the layout."
    return PixelResult(outputs, f"Made {', '.join(o.name for o in outputs)} with {how}.{note}")


def _soffice_pdf(soffice: str, src: Path, target: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(
            [
                soffice,
                "--headless",
                "--norestore",
                "--convert-to",
                "pdf",
                "--outdir",
                tmp,
                str(src),
            ],
            check=True,
            capture_output=True,
            timeout=180,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        made = Path(tmp) / f"{src.stem}.pdf"
        if not made.is_file():
            raise PixelError(f"LibreOffice couldn't convert {src.name}.")
        shutil.move(str(made), target)


def _docx_pdf(src: Path, target: Path) -> None:
    """Headings, paragraphs (bold and italic runs), bullets and tables, in reading order."""
    from xml.sax.saxutils import escape

    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import ListFlowable, ListItem, SimpleDocTemplate, Spacer, TableStyle
    from reportlab.platypus import Paragraph as P
    from reportlab.platypus import Table as T

    doc = Document(str(src))
    styles = getSampleStyleSheet()
    sec = doc.sections[0] if doc.sections else None
    size = (
        A4
        if sec is not None and sec.page_width and abs(sec.page_width.inches - 8.27) < 0.1
        else letter
    )

    def rich(p: Paragraph) -> str:
        out = []
        for r in p.runs:
            t = escape(r.text)
            if r.bold:
                t = f"<b>{t}</b>"
            if r.italic:
                t = f"<i>{t}</i>"
            if r.underline:
                t = f"<u>{t}</u>"
            out.append(t)
        return "".join(out) or escape(p.text)

    story: list[Any] = []
    bullets: list[Any] = []

    def flush() -> None:
        if bullets:
            story.append(ListFlowable(list(bullets), bulletType="bullet", leftIndent=14))
            bullets.clear()

    body = doc.element.body
    for child in body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            p = Paragraph(child, doc)
            style = (p.style.name if p.style is not None else "") or ""
            if not p.text.strip():
                flush()
                story.append(Spacer(1, 6))
                continue
            if "List" in style:
                bullets.append(ListItem(P(rich(p), styles["BodyText"])))
                continue
            flush()
            m = re.match(r"Heading (\d)", style)
            if style == "Title":
                story.append(P(rich(p), styles["Title"]))
            elif m:
                story.append(P(rich(p), styles[f"Heading{min(int(m.group(1)), 6)}"]))
            else:
                story.append(P(rich(p), styles["BodyText"]))
        elif tag == "tbl":
            flush()
            table = Table(child, doc)
            rows = [
                [P(escape(c.text), styles["BodyText"]) for c in row.cells] for row in table.rows
            ]
            if rows:
                t = T(rows, repeatRows=1)
                t.setStyle(
                    TableStyle(
                        [
                            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eeeeee")),
                            ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ]
                    )
                )
                story += [t, Spacer(1, 8)]
    flush()
    if not story:
        story.append(Spacer(1, 1))
    SimpleDocTemplate(
        str(target), pagesize=size, leftMargin=inch, rightMargin=inch, title=src.stem
    ).build(story)


# ---------------------------------------------------------------------------- CSV and Excel
def _cell(value: str) -> Any:
    v = value.strip()
    if re.fullmatch(r"-?\d+", v) and not (len(v) > 1 and v.lstrip("-").startswith("0")):
        return int(v)
    if re.fullmatch(r"-?\d*\.\d+|-?\d{1,3}(,\d{3})+(\.\d+)?", v):
        try:
            return float(v.replace(",", ""))
        except ValueError:
            return value
    return value


def csv_to_excel(files: list[Path], out: Path | None = None) -> PixelResult:
    from openpyxl import Workbook
    from openpyxl.styles import Font

    files = _need(files, {".csv", ".tsv", ".txt"}, "CSV file", many=True)
    outputs = []
    for src in files:
        raw = src.read_bytes()
        text = raw.decode("utf-8-sig", errors="replace")
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel_tab if src.suffix.lower() == ".tsv" else csv.excel
        rows = list(csv.reader(io.StringIO(text), dialect))
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


def excel_to_csv(files: list[Path], sheet: str = "", out: Path | None = None) -> PixelResult:
    from openpyxl import load_workbook

    (src,) = _need(files, {".xlsx", ".xlsm"}, "Excel workbook")
    wb = load_workbook(src, data_only=True, read_only=True)
    try:
        names = [sheet] if sheet else wb.sheetnames[:1]
        if sheet and sheet not in wb.sheetnames:
            raise PixelError(
                f"There's no sheet called {sheet!r}; it has {', '.join(wb.sheetnames)}."
            )
        outputs = []
        for name in names:
            label = src.stem if len(wb.sheetnames) == 1 else f"{src.stem} ({name})"
            target = unique_path(out or out_dir_for(src), f"{label}.csv")
            with target.open("w", newline="", encoding="utf-8-sig") as fh:
                w = csv.writer(fh)
                for row in wb[name].iter_rows(values_only=True):
                    w.writerow(["" if v is None else v for v in row])
            outputs.append(target)
    finally:
        wb.close()
    extra = f" (sheet {names[0]}; it has {len(wb.sheetnames)})" if len(wb.sheetnames) > 1 else ""
    return PixelResult(outputs, f"Made {outputs[0].name}{extra}.")


# ---------------------------------------------------------------------------- one entry point
def run(
    operation: str, files: list[str | Path], options: dict[str, Any] | None = None
) -> PixelResult:
    """Do one operation. Blocking (call it in a thread); raises PixelError for a bad request."""
    o = dict(options or {})
    paths = [Path(str(f)).expanduser() for f in files]
    out = Path(str(o["out_dir"])) if o.get("out_dir") else None

    def num(key: str) -> Any:
        v = o.get(key)
        if v in (None, ""):
            return None
        try:
            return float(v) if key == "percent" else int(float(v))
        except (TypeError, ValueError):
            raise PixelError(f"{key} should be a number, not {v!r}.") from None

    if operation == "images_to_pdf":
        return images_to_pdf(paths, o.get("name"), out)
    if operation == "merge_pdfs":
        return merge_pdfs(paths, o.get("name"), out)
    if operation == "split_pdf":
        return split_pdf(paths, str(o.get("pages") or ""), out)
    if operation == "compress_pdf":
        return compress_pdf(paths, num("quality") or 60, out=out)
    if operation == "word_to_pdf":
        return word_to_pdf(paths, out)
    if operation == "pdf_to_images":
        return pdf_to_images(
            paths, str(o.get("format") or "png"), num("dpi") or 150, str(o.get("pages") or ""), out
        )
    if operation == "convert_image":
        return convert_image(paths, str(o.get("format") or "png"), num("quality") or 90, out)
    if operation == "resize_image":
        return resize_image(paths, num("width"), num("height"), num("percent"), out)
    if operation == "csv_to_excel":
        return csv_to_excel(paths, out)
    if operation == "excel_to_csv":
        return excel_to_csv(paths, str(o.get("sheet") or ""), out)
    raise PixelError(f"I don't know how to {operation!r}. I can: {', '.join(OPERATIONS)}.")


def guess_operation(task: str, files: list[str]) -> str | None:
    """For a plain `delegate` to Pixel with no operation: pick one from the words and files."""
    t = task.lower()
    exts = {Path(f).suffix.lower() for f in files}
    if re.search(r"smaller|compress|shrink|reduce", t) and ".pdf" in exts:
        return "compress_pdf"
    if re.search(r"merge|combine|join", t):
        return "merge_pdfs" if exts <= {".pdf"} else "images_to_pdf"
    if re.search(r"split|separate|each page", t) and ".pdf" in exts and "image" not in t:
        return "split_pdf"
    if exts & IMAGE_EXTS and "pdf" in t:
        return "images_to_pdf"
    if exts & {".docx", ".doc", ".odt", ".rtf"} and "pdf" in t:
        return "word_to_pdf"
    if ".pdf" in exts and re.search(r"image|png|jpe?g|picture", t):
        return "pdf_to_images"
    if exts & {".csv", ".tsv"}:
        return "csv_to_excel"
    if exts & {".xlsx", ".xlsm"} and "csv" in t:
        return "excel_to_csv"
    if exts & IMAGE_EXTS and re.search(r"resize|smaller|bigger|width|height|%|percent", t):
        return "resize_image"
    if exts & IMAGE_EXTS:
        return "convert_image"
    return None
