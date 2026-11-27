"""The parts of "make this PDF smaller" that leave every page looking exactly as it did.

pypdf writes each object of a PDF out in the open, one after another, with a plain table of
where they are. Most PDFs made in the last fifteen years keep those objects packed together
and compressed (object streams), so a PDF that pypdf has only read and written again comes
out BIGGER than it went in, before any picture is touched. Live: a 99 KB statement, nearly
all text, came back 8% smaller, and a second ask got "already about as small as it gets".

:func:`packed` writes the same objects the compact way. What it writes is read back and
compared with the plain file before it is used; anything odd and the plain file stands.
"""

from __future__ import annotations

import io
import logging
import re
import struct
import zlib
from typing import Any

log = logging.getLogger(__name__)

_PER_STREAM = 200  # objects in one object stream
_SIZE = re.compile(r"(?i)(\d+(?:[.,]\d+)?)\s*(kb|kilobytes?|k|mb|megabytes?|m|bytes?|b)?\s*$")
_UNIT = {"k": 1024, "m": 1024 * 1024, "b": 1}
_AIM = re.compile(
    r"(?i)\b(?:under|below|beneath|less than|smaller than|at most|no more than|max(?:imum)?(?: of)?|"
    r"down to|to|within)\s+(\d+(?:\.\d+)?)\s*(kb|mb|kilobytes?|megabytes?)\b"
)
_LESS = re.compile(
    r"(?i)\b(\d+(?:\.\d+)?)\s*(kb|mb|kilobytes?|megabytes?)\s+(?:less|smaller|lighter|off)\b"
)


def size_bytes(value: Any) -> int | None:
    """A size someone gave ("80 KB", "1.5mb", 80, "80"): in bytes, a bare number being KB.
    None for nothing given; ValueError for something that is not a size."""
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise ValueError(str(value))
    if isinstance(value, (int, float)):
        n, unit = float(value), "k"
    else:
        m = _SIZE.match(str(value).strip())
        if not m:
            raise ValueError(str(value))
        n, unit = float(m.group(1).replace(",", ".")), (m.group(2) or "k")[0].lower()
    if n <= 0:
        raise ValueError(str(value))
    return int(n * _UNIT[unit])


def aim_from_words(task: str) -> dict[str, int]:
    """The size a request in words asks for: {"target": bytes} for "under 80 KB", {"less":
    bytes} for "20 KB less", {} for neither."""
    if m := _LESS.search(task or ""):
        return {"less": size_bytes(f"{m.group(1)} {m.group(2)}") or 0}
    if m := _AIM.search(task or ""):
        return {"target": size_bytes(f"{m.group(1)} {m.group(2)}") or 0}
    return {}


def tighten(writer: Any) -> None:
    """Every stream that is stored loose or lightly packed, packed as hard as zlib can. Only
    plain ones: a picture with its own coding (JPEG, a predictor) is left exactly as it is."""
    from pypdf.generic import EncodedStreamObject, NameObject, StreamObject

    for i, obj in enumerate(writer._objects):
        if not isinstance(obj, StreamObject):
            continue
        try:
            how = obj.get("/Filter")
            if how is not None and (str(how) != "/FlateDecode" or "/DecodeParms" in obj):
                continue
            tight = zlib.compress(obj.get_data(), 9)
            if len(tight) >= len(obj._data):
                continue  # no smaller than it is stored now
            new = EncodedStreamObject()
            new.update({k: v for k, v in obj.items() if k not in ("/Length", "/Filter")})
            new[NameObject("/Filter")] = NameObject("/FlateDecode")
            new._data = tight
            new.indirect_reference = obj.indirect_reference
            writer._objects[i] = new
        except Exception:  # one odd stream stays as it is
            continue


def drop_extras(writer: Any) -> None:
    """Page thumbnails and the private notes an app leaves for itself (/PieceInfo: Illustrator
    keeps a whole second copy of the artwork there). Neither is part of what a page shows."""
    for page in writer.pages:
        for key in ("/Thumb", "/PieceInfo"):
            if key in page:
                del page[key]
    root = writer.root_object
    if "/PieceInfo" in root:
        del root["/PieceInfo"]


def _plain(writer: Any) -> bytes:
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _compact(writer: Any) -> bytes:
    """The writer's objects as a PDF 1.5 file: everything that is not a stream goes into
    compressed object streams, and the table of objects is itself a compressed stream."""
    from pypdf.generic import StreamObject

    if getattr(writer, "_encryption", None) is not None:
        raise ValueError("an encrypted file is written the plain way")
    writer._resolve_links()
    out = io.BytesIO()
    header = writer.pdf_header
    m = re.match(r"%PDF-(\d)\.(\d)", header)
    if not m or (int(m.group(1)), int(m.group(2))) < (1, 5):
        header = "%PDF-1.5"
    out.write(header.encode() + b"\n%\xe2\xe3\xcf\xd3\n")
    count = len(writer._objects)
    entries: dict[int, tuple[int, int, int]] = {}  # object number: (type, field 2, field 3)
    loose: list[tuple[int, bytes]] = []
    for num, obj in enumerate(writer._objects, start=1):
        if obj is None:
            continue
        body = io.BytesIO()
        obj.write_to_stream(body)
        if isinstance(obj, StreamObject):
            entries[num] = (1, out.tell(), 0)
            out.write(f"{num} 0 obj\n".encode() + body.getvalue() + b"\nendobj\n")
        else:
            loose.append((num, body.getvalue()))
    next_num = count + 1
    for start in range(0, len(loose), _PER_STREAM):
        group = loose[start : start + _PER_STREAM]
        index, bodies, at = [], [], 0
        for num, body in group:
            index.append(f"{num} {at}")
            bodies.append(body)
            at += len(body) + 1
        head = (" ".join(index) + "\n").encode()
        data = zlib.compress(head + b"\n".join(bodies) + b"\n", 9)
        entries[next_num] = (1, out.tell(), 0)
        out.write(
            f"{next_num} 0 obj\n<< /Type /ObjStm /N {len(group)} /First {len(head)} "
            f"/Filter /FlateDecode /Length {len(data)} >>\nstream\n".encode()
            + data
            + b"\nendstream\nendobj\n"
        )
        for k, (num, _) in enumerate(group):
            entries[num] = (2, next_num, k)
        next_num += 1
    xref_at = out.tell()
    entries[next_num] = (1, xref_at, 0)
    rows = [struct.pack(">BIH", 0, 0, 65535)]  # object 0: the head of the free list
    for num in range(1, next_num + 1):
        kind, a, b = entries.get(num, (0, 0, 0))
        rows.append(struct.pack(">BIH", kind, a, b))
    table = zlib.compress(b"".join(rows), 9)
    extra = io.BytesIO()
    root = writer.root_object.indirect_reference
    extra.write(f"/Root {root.idnum} 0 R ".encode())
    if writer._info is not None and writer._info.indirect_reference is not None:
        extra.write(f"/Info {writer._info.indirect_reference.idnum} 0 R ".encode())
    if writer._ID:
        extra.write(b"/ID ")
        writer._ID.write_to_stream(extra)
        extra.write(b" ")
    out.write(
        f"{next_num} 0 obj\n<< /Type /XRef /Size {next_num + 1} /W [1 4 2] ".encode()
        + extra.getvalue()
        + f"/Filter /FlateDecode /Length {len(table)} >>\nstream\n".encode()
        + table
        + f"\nendstream\nendobj\nstartxref\n{xref_at}\n%%EOF\n".encode()
    )
    return out.getvalue()


def _same(plain: bytes, compact: bytes) -> bool:
    """Does the compact file read back as the plain one does: the same pages, the same words
    on each, the same pictures, in pypdf and in PDFium (what Edge and Chrome open it with)?"""
    import pypdfium2 as pdfium
    from pypdf import PdfReader

    a, b = PdfReader(io.BytesIO(plain)), PdfReader(io.BytesIO(compact))
    if len(a.pages) != len(b.pages) or not a.pages:
        return False
    for pa, pb in zip(a.pages, b.pages, strict=True):
        if (pa.extract_text() or "") != (pb.extract_text() or ""):
            return False
        if [len(i.data) for i in pa.images] != [len(i.data) for i in pb.images]:
            return False
    doc = pdfium.PdfDocument(compact)
    try:
        if len(doc) != len(a.pages):
            return False
        # The first page drawn both ways, small: a page that reads right but draws blank
        # (a font or a picture it can't find) is not the same file.
        was = pdfium.PdfDocument(plain)
        try:
            one = was[0].render(scale=0.3).to_pil().convert("L")
            two = doc[0].render(scale=0.3).to_pil().convert("L")
        finally:
            was.close()
        return one.size == two.size and one.tobytes() == two.tobytes()
    finally:
        doc.close()


def packed(writer: Any) -> bytes:
    """The writer's PDF as bytes, the compact way when that reads back the same and is
    smaller, the plain way otherwise."""
    plain = _plain(writer)
    try:
        compact = _compact(writer)
        if len(compact) < len(plain) and _same(plain, compact):
            return compact
    except Exception:
        log.info("pixel: the compact PDF didn't check out; writing it the plain way", exc_info=True)
    return plain
