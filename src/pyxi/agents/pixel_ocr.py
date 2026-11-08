"""Text out of pictures for Pixel: Windows' built-in OCR first (fast, offline), RapidOCR when
Windows can't (no OCR language pack on this PC, or a language it doesn't have installed).

RapidOCR runs on onnxruntime, like Pyxi's voice, and its models come in the package
(Latin-script languages and Chinese), so once it's here it works offline. In the installed app it
arrives with the voice pack (scripts/build_installer.py): the speech warm-up fetches that a moment
after Pyxi starts, and the first import fetches it anyway if that hasn't happened yet
(sitecustomize's pack hook). If it can't load (offline, a download that failed), it's tried again
a minute later, not never. Both engines give the same TextLine shape the screen reader uses, so
searchable PDFs place text the same way.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from typing import Any

from pyxi.screen.ocr import TextLine

_rapid: Any = None
_rapid_failed_at: float | None = None
_rapid_used = 0.0  # monotonic time it was last asked for
RETRY_AFTER_S = 60.0
_lock = threading.Lock()


def _engine_choice() -> str:
    """PYXI_OCR=windows or rapid forces one engine (tests, troubleshooting); default is auto."""
    return os.environ.get("PYXI_OCR", "auto").strip().lower()


def rapid_engine() -> Any:
    """RapidOCR, loaded once. A failed load (no voice pack yet, offline) is retried after a minute."""
    global _rapid, _rapid_failed_at, _rapid_used
    with _lock:
        recently = (
            _rapid_failed_at is not None and time.monotonic() - _rapid_failed_at < RETRY_AFTER_S
        )
        if _rapid is None and not recently:
            try:
                from rapidocr_onnxruntime import RapidOCR

                _rapid = RapidOCR()
                _rapid_failed_at = None
            except Exception:
                _rapid_failed_at = time.monotonic()
        _rapid_used = time.monotonic()
    return _rapid


def release_idle(idle_s: float) -> bool:
    """Let RapidOCR go (its models and onnxruntime buffers) after ``idle_s`` unused seconds; the
    next scan loads it again."""
    global _rapid
    with _lock:
        if _rapid is None or time.monotonic() - _rapid_used < idle_s:
            return False
        _rapid = None
        return True


def pack_pending() -> bool:
    """True when RapidOCR's onnxruntime is in the voice pack and that pack isn't downloaded yet
    (so the first scan waits for it). Looks at sitecustomize's import hook without triggering it."""
    for finder in sys.meta_path:
        by_module = getattr(finder, "by_module", {})
        pack = by_module.get("rapidocr_onnxruntime") or by_module.get("onnxruntime")
        if pack is not None:
            return not getattr(pack, "ready", True)
    return False


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
