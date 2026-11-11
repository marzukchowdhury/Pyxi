"""What every Pixel operation shares: results and errors, where new files go (beside the original,
never over anything), page ranges, and opening images (HEIC included)."""

from __future__ import annotations

import os
import re
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff", ".heic", ".heif"}
IMAGE_FORMATS = {
    "jpg": ("JPEG", ".jpg"),
    "jpeg": ("JPEG", ".jpg"),
    "png": ("PNG", ".png"),
    "webp": ("WEBP", ".webp"),
    "bmp": ("BMP", ".bmp"),
    "gif": ("GIF", ".gif"),
    "tiff": ("TIFF", ".tiff"),
    "tif": ("TIFF", ".tiff"),
}


class PixelError(ValueError):
    """Something about the request or the file, said the way you'd tell a person."""


@dataclass
class PixelResult:
    outputs: list[Path]
    summary: str
    details: dict[str, Any] = field(default_factory=dict)


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


MAX_PATH = 240  # Windows turns down longer paths unless long paths are switched on


def unique_path(folder: Path, name: str) -> Path:
    """A path in ``folder`` that doesn't exist yet: "name.pdf", then "name (2).pdf"...
    A name too long for Windows is cut short, never an error from the disk."""
    stem, ext = os.path.splitext(name)
    if not re.fullmatch(r"\.\w{1,8}", ext):  # "Report v1.2 (split)": that dot starts no extension
        stem, ext = name, ""
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", stem).strip(" .") or "file"
    room = max(24, min(180, MAX_PATH - len(str(folder)) - len(ext) - 8))  # 8: a slash and " (999)"
    if len(stem) > room:
        cut = stem[:room].rstrip(" .,(")
        stem = cut + (")" if cut.count("(") > cut.count(")") else "")
    path = folder / f"{stem}{ext}"
    n = 2
    while path.exists():
        path = folder / f"{stem} ({n}){ext}"
        n += 1
    return path


def plain_name(name: Any, ext: str) -> str | None:
    """The name they asked for, without a folder and without ``ext`` on the end: the caller adds
    it, and "Combined.pdf" must not come out as "Combined.pdf.pdf"."""
    text = re.split(r"[\\/]", str(name or "").strip())[-1].strip()
    if text.lower().endswith(ext.lower()):
        text = text[: -len(ext)].rstrip(" .")
    return text or None


def need(paths: list[Path], exts: set[str], what: str, *, many: bool = False) -> list[Path]:
    if not paths:
        raise PixelError(f"Which {what}? I need the file's path.")
    for p in paths:
        if p.is_dir():
            raise PixelError(f"{p.name} is a folder. Which file in it?")
        if not p.is_file():
            raise PixelError(f"I can't find {p.name} at {p.parent}.")
        try:
            p.open("rb").close()
        except PermissionError:  # open in Word or Excel with a lock, or not theirs to read
            raise PixelError(
                f"{p.name} is locked by another app. Close it there and try again."
            ) from None
        if p.suffix.lower() not in exts:
            an = "an" if what[:1].lower() in "aeiou" else "a"
            raise PixelError(f"{p.name} isn't {an} {what}.")
    if not many and len(paths) > 1:
        return paths[:1]
    return paths


def size_text(n: float) -> str:
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} bytes"


def parse_ranges(spec: str, pages: int) -> list[tuple[int, int]]:
    """'1-3, 5, 7-' -> [(1, 3), (5, 5), (7, pages)], 1-based and inclusive. 'last' works too."""
    out = []
    said = str(spec).strip()
    spec = re.sub(r"(?i)\blast\b", str(pages), said)
    spec = re.sub(r"(?i)\bfirst\b", "1", spec)
    # "page 2 to page 4" is one range: the words go, they don't split it (as commas they made
    # it "2 to the end" and "4", and deleting those pages took nine of the ten).
    spec = re.sub(r"(?i)\b(pages?|the)\b", " ", spec)
    spec = re.sub(r"(?i)\band\b", ",", spec)
    for part in re.split(r"[,;]\s*", spec.strip()):
        part = part.strip()
        if not part:
            continue
        m = re.fullmatch(r"(\d*)\s*(?:-|–|—|to|through|thru)\s*(\d*)|(\d+)", part)
        if not m or not (m.group(1) or m.group(2) or m.group(3)):
            # Quote what they said, not the half-rewritten spec ("the 5 one"). A dash with no
            # number on either side is not "every page".
            raise PixelError(f"I don't understand the pages {said!r}. Try something like 1-3, 5.")
        if m.group(3):
            a = b = int(m.group(3))
        else:
            a = int(m.group(1) or 1)
            b = int(m.group(2) or pages)
        if not 1 <= a <= b <= pages:
            raise PixelError(f"Pages {part} aren't in this PDF (it has {pages}).")
        out.append((a, b))
    return out


_heif = None


def heif_ready() -> bool:
    """HEIC/HEIF (iPhone photos) open like any other image once pillow-heif is registered."""
    global _heif
    if _heif is None:
        try:
            import pillow_heif

            pillow_heif.register_heif_opener()
            _heif = True
        except Exception:
            _heif = False
    return _heif


def open_image(path: Path):
    from PIL import Image, ImageOps

    if path.suffix.lower() in (".heic", ".heif") and not heif_ready():
        raise PixelError("HEIC photos need the pillow-heif package, which isn't installed.")
    img = Image.open(path)
    return ImageOps.exif_transpose(img)  # phone photos come out the right way up


def image_pages(path: Path) -> list[Any]:
    """The pictures in an image file, one per page: a fax or a scanner's TIFF holds several, and
    only the first used to make it into the PDF. Any other image is the one picture."""
    from PIL import Image, ImageOps, ImageSequence

    if path.suffix.lower() not in (".tif", ".tiff"):
        return [open_image(path)]
    with Image.open(path) as img:
        return [ImageOps.exif_transpose(frame.copy()) for frame in ImageSequence.Iterator(img)]


def flat_rgb(img):
    from PIL import Image

    if img.mode in ("RGBA", "LA", "P", "PA"):
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1])
        return bg
    return img.convert("RGB")


# ---------------------------------------------------------------------------- Word paragraphs
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
# What can sit between a paragraph and its runs. python-docx's ``paragraph.runs`` sees none of
# these, so a link's words went missing from a PDF and were doubled by a grammar fix.
WORD_WRAPPERS = {
    W + t for t in ("hyperlink", "ins", "smartTag", "sdt", "sdtContent", "fldSimple", "customXml")
}
WORD_ZONES = {W + "hyperlink", W + "fldSimple", W + "sdt"}


def word_runs(p: Any, zone: Any = None) -> Iterator[tuple[Any, Any]]:
    """Each run (w:r) of a Word paragraph in reading order, with the link, field or content
    control it sits in (None for ordinary text). A text box inside the paragraph is not the
    paragraph's own words, and is left out."""
    for child in p:
        if child.tag == W + "r":
            yield child, zone
        elif child.tag in WORD_WRAPPERS:
            inner = child if zone is None and child.tag in WORD_ZONES else zone
            yield from word_runs(child, inner)


def run_pieces(r: Any) -> Iterator[tuple[Any, str, bool]]:
    """(element, its text, whether it is plain text) for what a run shows, the way python-docx
    reads it: a tab is a tab, a line break is a new line, a page break is nothing."""
    for child in r:
        tag = child.tag
        if tag == W + "t":
            yield child, child.text or "", True
        elif tag in (W + "tab", W + "ptab"):
            yield child, "\t", False
        elif tag == W + "cr" or (
            tag == W + "br" and child.get(W + "type") in (None, "textWrapping")
        ):
            yield child, "\n", False
        elif tag == W + "noBreakHyphen":
            yield child, "-", False


def word_text(p: Any) -> str:
    """A Word paragraph's text, a link's words included."""
    return "".join(text for r, _ in word_runs(p) for _, text, _ in run_pieces(r))
