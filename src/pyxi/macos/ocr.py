"""The Mac side of ``pyxi.screen.ocr``: Apple's Vision text recognition, offline, built in.

Every line with its word boxes, in the same shape as Windows' OCR engine gives them, so pointing
and underlining work the same. Vision reports boxes from 0 to 1 with the origin at the bottom
left; they're turned into screen points here.
"""

from __future__ import annotations

import io
import logging
import sys

from PIL import Image

from pyxi.screen.ocr import TextLine, _union, _worth_keeping

log = logging.getLogger(__name__)

__all__ = ["available", "read_lines"]

_failed = False


def _vision():
    import importlib

    return importlib.import_module("Vision")


def available() -> bool:
    global _failed
    if sys.platform != "darwin" or _failed:
        return False
    try:
        _vision()
        return True
    except Exception:
        _failed = True
        return False


def _cgimage(img: Image.Image):
    """A PIL image -> a CGImage, through PNG bytes (no pixel format guessing)."""
    import importlib

    Q = importlib.import_module("Quartz")
    Foundation = importlib.import_module("Foundation")
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "PNG")
    data = Foundation.NSData.dataWithBytes_length_(buf.getvalue(), len(buf.getvalue()))
    src = Q.CGImageSourceCreateWithData(data, None)
    return Q.CGImageSourceCreateImageAtIndex(src, 0, None)


def _box(r, img_w: int, img_h: int, origin: tuple[int, int]) -> tuple[int, int, int, int]:
    """A Vision box (0-1, bottom-left origin) -> screen points (top-left origin)."""
    x, y = r.origin.x, r.origin.y
    w, h = r.size.width, r.size.height
    return (
        round(x * img_w) + origin[0],
        round((1 - y - h) * img_h) + origin[1],
        max(1, round(w * img_w)),
        max(1, round(h * img_h)),
    )


def _words(text: str) -> list[tuple[str, int, int]]:
    """Each word with its start and length in the line, for Vision's per-range boxes."""
    out, i = [], 0
    for word in text.split():
        start = text.index(word, i)
        out.append((word, start, len(word)))
        i = start + len(word)
    return out


def read_lines(img: Image.Image, origin: tuple[int, int] = (0, 0)) -> list[TextLine]:
    """OCR a screenshot. Returns [] when Vision isn't there or fails (never raises)."""
    if not available():
        return []
    try:
        V = _vision()
        request = V.VNRecognizeTextRequest.alloc().init()
        request.setRecognitionLevel_(0)  # VNRequestTextRecognitionLevelAccurate
        request.setUsesLanguageCorrection_(True)
        handler = V.VNImageRequestHandler.alloc().initWithCGImage_options_(_cgimage(img), None)
        ok = handler.performRequests_error_([request], None)
        if isinstance(ok, tuple):
            ok = ok[0]
        if not ok:
            return []
        results = list(request.results() or [])
    except Exception as exc:
        log.debug("Vision OCR failed: %s", exc)
        return []

    lines: list[TextLine] = []
    for obs in results:
        try:
            cands = obs.topCandidates_(1)
            if not cands:
                continue
            cand = cands[0]
            text = str(cand.string()).strip()
            if not _worth_keeping(text):
                continue
            words = []
            for word, start, length in _words(text):
                res = cand.boundingBoxForRange_error_((start, length), None)
                box = res[0] if isinstance(res, tuple) else res
                if box is not None:
                    words.append((word, _box(box.boundingBox(), img.width, img.height, origin)))
            if not words:
                words = [(text, _box(obs.boundingBox(), img.width, img.height, origin))]
            lines.append(TextLine(text, _union([r for _, r in words]), words))
        except Exception:
            continue
    lines.sort(key=lambda ln: (ln.rect[1] // 12, ln.rect[0]))
    return lines
