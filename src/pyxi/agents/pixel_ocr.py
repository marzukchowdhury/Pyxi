"""Text out of pictures for Pixel: Windows' built-in OCR first (fast, offline), RapidOCR when
Windows can't (no OCR language pack on this PC, or a language it doesn't have installed).

RapidOCR runs on onnxruntime, which Pyxi already ships for its voice, and its models come in the
package (Latin-script languages and Chinese), so it works offline on any PC. Both engines give the
same TextLine shape the screen reader uses, so searchable PDFs place text the same way.
"""

from __future__ import annotations

import os
import threading
from typing import Any

from pyxi.screen.ocr import TextLine

_rapid: Any = None
_rapid_failed = False
_lock = threading.Lock()


def _engine_choice() -> str:
    """PYXI_OCR=windows or rapid forces one engine (tests, troubleshooting); default is auto."""
    return os.environ.get("PYXI_OCR", "auto").strip().lower()


def rapid_engine() -> Any:
    global _rapid, _rapid_failed
    with _lock:
        if _rapid is None and not _rapid_failed:
            try:
                from rapidocr_onnxruntime import RapidOCR

                _rapid = RapidOCR()
            except Exception:
                _rapid_failed = True
    return _rapid


def windows_available() -> bool:
    from pyxi.screen import ocr

    return ocr.available()


def available() -> bool:
    choice = _engine_choice()
    if choice == "windows":
        return windows_available()
    if choice == "rapid":
        return rapid_engine() is not None
    return windows_available() or rapid_engine() is not None


def read_rapid(img) -> list[TextLine]:
    """RapidOCR lines (one box per line; the line stands in for its words)."""
    import numpy as np

    engine = rapid_engine()
    if engine is None:
        return []
    result, _ = engine(np.array(img.convert("RGB")))
    lines: list[TextLine] = []
    for box, text, score in result or []:
        text = str(text).strip()
        if not text or float(score) < 0.5:
            continue
        xs = [int(p[0]) for p in box]
        ys = [int(p[1]) for p in box]
        rect = (min(xs), min(ys), max(1, max(xs) - min(xs)), max(1, max(ys) - min(ys)))
        lines.append(TextLine(text, rect, [(w, rect) for w in text.split()]))
    lines.sort(key=lambda ln: (ln.rect[1] // 12, ln.rect[0]))
    return lines


def read_lines(img) -> tuple[list[TextLine], str]:
    """(lines, engine used). Windows OCR first; RapidOCR when Windows has nothing to offer."""
    choice = _engine_choice()
    if choice != "rapid" and windows_available():
        from pyxi.screen import ocr

        lines = ocr.read_lines(img.convert("RGB"))
        if lines or choice == "windows":
            return lines, "windows"
    if choice != "windows":
        lines = read_rapid(img)
        if lines or rapid_engine() is not None:
            return lines, "rapid"
    return [], "none"
