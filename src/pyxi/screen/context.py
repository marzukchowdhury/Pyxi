"""ScreenContext: one moment of what's on screen, ready for the model and the overlay.

Coordinates: everything Pyxi stores is in *physical* screen pixels of the primary monitor
(the process is made DPI-aware so UIA, mss and the overlay agree). The overlay converts to
CSS pixels with the display's scale factor. The model sees a downscaled screenshot; the
``to_screen`` helper maps its coordinates back.
"""

from __future__ import annotations

import asyncio
import ctypes
import io
import re
import sys
import time
from dataclasses import dataclass, field

from PIL import Image, ImageDraw, ImageFont

from pyxi.config import ScreenConfig
from pyxi.screen import ocr, uia
from pyxi.screen.ocr import TextLine

_DPI_SET = False


def enable_dpi_awareness() -> None:
    global _DPI_SET
    if _DPI_SET or sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor aware
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass
    _DPI_SET = True


@dataclass
class WindowInfo:
    title: str = ""
    process: str = ""
    rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    hwnd: int = 0

    @property
    def app(self) -> str:
        return self.process.removesuffix(".exe") or self.title.split(" - ")[-1]


@dataclass
class UIElement:
    id: int
    name: str
    role: str
    rect: tuple[int, int, int, int]
    depth: int = 0
    parent: int | None = None
    is_password: bool = False

    @property
    def center(self) -> tuple[int, int]:
        x, y, w, h = self.rect
        return x + w // 2, y + h // 2

    def describe(self, to_image=None) -> str:
        x, y, w, h = to_image(self.rect) if to_image else self.rect
        name = (
            "(password field)" if self.is_password else f'"{self.name[:70]}"' if self.name else ""
        )
        return f"[{self.id}] {self.role} {name} @({x},{y},{w}x{h})".replace("  ", " ")


@dataclass
class ScreenContext:
    captured_at: float
    window: WindowInfo
    elements: list[UIElement] = field(default_factory=list)
    screenshot: Image.Image | None = None
    origin: tuple[int, int] = (0, 0)  # monitor's top-left in virtual screen coords
    excluded: str | None = None  # reason, when the foreground app is private
    model_scale: float = 1.0  # model-image px * model_scale = screen px
    text: list[TextLine] = field(default_factory=list)  # OCR lines, ids continue after elements

    # ------------------------------------------------------------------ lookups
    def element(self, element_id: int) -> UIElement | None:
        found = next((e for e in self.elements if e.id == element_id), None)
        if found is None and (line := next((t for t in self.text if t.id == element_id), None)):
            found = UIElement(line.id, line.text, "ScreenText", line.rect)
        return found

    def find_text(self, quote: str, within: tuple[int, int, int, int] | None = None):
        """Where a phrase is on screen, word-exact (from OCR). None if it isn't there."""
        return ocr.find_text(self.text, quote, within)

    def children(self, element_id: int) -> list[UIElement]:
        return [e for e in self.elements if e.parent == element_id]

    def subtree(self, element_id: int, max_depth: int = 3) -> list[UIElement]:
        root = self.element(element_id)
        if root is None:
            return []
        out, frontier = [root], [root.id]
        for _ in range(max_depth):
            nxt = [c for pid in frontier for c in self.children(pid)]
            out.extend(nxt)
            frontier = [c.id for c in nxt]
        return out

    def to_screen(self, x: float, y: float) -> tuple[int, int]:
        """Model-image coordinates -> physical screen coordinates."""
        return int(x * self.model_scale) + self.origin[0], int(y * self.model_scale) + self.origin[
            1
        ]

    # ------------------------------------------------------------------ rendering
    def render(self, max_elements: int = 120) -> str:
        if self.excluded:
            return f"The user is in a private app ({self.excluded}). You cannot see it, and you must not ask what is in it."
        w = self.window
        lines = [f"Foreground app: {w.app} | window: {w.title[:120]!r}"]
        s = self.model_scale or 1.0

        def img(r: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
            # Same units as the screenshot the model sees (and the px it answers in).
            x, y, ww, hh = r
            return (
                round((x - self.origin[0]) / s),
                round((y - self.origin[1]) / s),
                round(ww / s),
                round(hh / s),
            )

        if self.screenshot is not None:
            lines.append(
                f"Screenshot: {round(self.screenshot.width / s)}x{round(self.screenshot.height / s)} px "
                "(every position below is in these screenshot px)"
            )
        if self.elements:
            lines.append(
                f"UI elements ({min(len(self.elements), max_elements)} of {len(self.elements)}, from the accessibility tree):"
            )
            lines.extend(
                "  " * min(e.depth, 6) + e.describe(img) for e in self.elements[:max_elements]
            )
        else:
            lines.append("(No accessibility tree available: rely on the screenshot.)")
        if self.text:
            lines.append(
                "Text read from the pixels (OCR, top to bottom; includes text inside images, "
                "so frame or underline these, or quote their exact words):"
            )
            lines.extend(
                f'[{t.id}] "{t.text[:90]}" @({img(t.rect)[0]},{img(t.rect)[1]},{img(t.rect)[2]}x{img(t.rect)[3]})'
                for t in self.text
            )
        return "\n".join(lines)

    def model_image(self, width: int, *, marks: bool = True) -> bytes | None:
        """Downscaled JPEG with element ids drawn on (set-of-marks) and passwords masked."""
        if self.screenshot is None:
            return None
        img = self.screenshot.copy()
        draw = ImageDraw.Draw(img)
        for e in self.elements:
            if e.is_password:
                x, y, w, h = self._local(e.rect)
                draw.rectangle([x, y, x + w, y + h], fill=(20, 20, 20))
        scale = min(1.0, width / img.width)
        if scale < 1.0:
            img = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
        self.model_scale = 1 / scale
        if marks and self.elements:
            draw = ImageDraw.Draw(img)
            font = _font(13)
            for e in self.elements:
                if e.role not in _MARKED_ROLES:
                    continue
                x, y, w, h = (v * scale for v in self._local(e.rect))
                color = _PALETTE[e.id % len(_PALETTE)]
                draw.rectangle([x, y, x + w, y + h], outline=color, width=2)
                tag = str(e.id)
                tw = draw.textlength(tag, font=font)
                draw.rectangle([x, max(0, y - 15), x + tw + 6, max(15, y)], fill=color)
                draw.text((x + 3, max(0, y - 15)), tag, fill="white", font=font)
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=72)
        return buf.getvalue()

    def crop(self, rect: tuple[int, int, int, int], pad: int = 0) -> Image.Image | None:
        if self.screenshot is None:
            return None
        x, y, w, h = self._local(rect)
        box = (
            max(0, x - pad),
            max(0, y - pad),
            min(self.screenshot.width, x + w + pad),
            min(self.screenshot.height, y + h + pad),
        )
        if box[2] <= box[0] or box[3] <= box[1]:
            return None
        return self.screenshot.crop(box)

    def _local(self, rect: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
        x, y, w, h = rect
        return x - self.origin[0], y - self.origin[1], w, h


_MARKED_ROLES = {r.removesuffix("Control") for r in uia.INTERACTIVE} | {
    "Group",
    "Pane",
    "Tab",
    "List",
    "ToolBar",
    "Text",
    "Image",
}
_PALETTE = ["#ef4444", "#f59e0b", "#10b981", "#3b82f6", "#8b5cf6", "#ec4899", "#14b8a6", "#f97316"]


def _font(size: int) -> ImageFont.ImageFont:
    for name in ("segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


class ScreenReader:
    """Captures ScreenContexts, honouring privacy settings and the pause switch."""

    @staticmethod
    def foreground_title() -> str | None:
        """Title of the window in front right now (cheap; no capture)."""
        try:
            import ctypes

            user32 = ctypes.windll.user32  # type: ignore[attr-defined]
            hwnd = user32.GetForegroundWindow()
            n = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            return buf.value
        except Exception:
            return None

    reads_windows = True  # can look at a named window, not only the one under the mouse

    def __init__(self, config: ScreenConfig) -> None:
        self.config = config
        self.paused = False
        self._excluded = [re.compile(p, re.IGNORECASE) for p in config.excluded_windows]
        self._next_id = 1
        enable_dpi_awareness()

    def is_excluded(self, window: WindowInfo) -> str | None:
        haystack = f"{window.title} {window.process}"
        for pattern in self._excluded:
            if pattern.search(haystack):
                return window.app or "private window"
        return None

    async def capture(
        self, *, pixels: bool = True, follow_mouse: bool = True, hwnd: int | None = None
    ) -> ScreenContext:
        """``follow_mouse``: read the window under the mouse (what they're looking at); off while
        Pyxi drives, when the window in front is the one being worked in. ``hwnd``: read this
        window and its screen instead (the request named it, "...in Notepad")."""
        return await asyncio.to_thread(self._capture_sync, pixels, follow_mouse, hwnd)

    def _capture_sync(
        self, pixels: bool, follow_mouse: bool = True, hwnd: int | None = None
    ) -> ScreenContext:
        now = time.time()
        if self.paused:
            return ScreenContext(
                captured_at=now, window=WindowInfo(), excluded="screen sharing is paused"
            )
        raw_window, raw = uia.read_foreground(
            budget_s=self.config.uia_budget_s,
            max_elements=self.config.max_elements * 3,
            follow_mouse=follow_mouse,
            hwnd=hwnd,
        )
        window = (
            WindowInfo(raw_window.title, raw_window.process, raw_window.rect, raw_window.hwnd)
            if raw_window
            else WindowInfo()
        )
        if reason := self.is_excluded(window):
            return ScreenContext(captured_at=now, window=window, excluded=reason)

        elements = self._number(raw)
        shot, origin = (
            self._grab(window.rect, follow_mouse and not hwnd) if pixels else (None, (0, 0))
        )
        if shot is not None:
            # The taskbar too (pinned apps, the clock): it's on screen and people ask about it.
            x0, y0, x1, y1 = origin[0], origin[1], origin[0] + shot.width, origin[1] + shot.height
            bar = [
                b
                for b in uia.read_taskbar()
                if x0 <= b.rect[0] + b.rect[2] // 2 < x1 and y0 <= b.rect[1] + b.rect[3] // 2 < y1
            ]
            for b in bar:
                elements.append(UIElement(self._next_id, b.name, b.role, b.rect))
                self._next_id += 1
        text = self._read_text(shot, origin, elements) if shot is not None else []
        return ScreenContext(
            captured_at=now,
            window=window,
            elements=elements,
            screenshot=shot,
            origin=origin,
            text=text,
        )

    def _read_text(
        self, shot: Image.Image, origin: tuple[int, int], elements: list[UIElement]
    ) -> list[TextLine]:
        if not self.config.ocr:
            return []
        masked = [e.rect for e in elements if e.is_password]
        lines = [
            ln
            for ln in ocr.read_lines(shot, origin)
            if not any(_contains(m, ln.rect) for m in masked)
        ]
        # Keep the lines nearest the middle of the screen when there are too many.
        if len(lines) > self.config.max_text_lines:
            cx, cy = origin[0] + shot.width / 2, origin[1] + shot.height / 2
            keep = sorted(
                lines,
                key=lambda ln: abs(ln.rect[0] + ln.rect[2] / 2 - cx) + abs(ln.rect[1] - cy),
            )[: self.config.max_text_lines]
            lines = [ln for ln in lines if ln in keep]
        for ln in lines:
            ln.id = self._next_id
            self._next_id += 1
        return lines

    def _number(self, raw: list[uia.RawElement]) -> list[UIElement]:
        # Prefer interactive and named elements when trimming to the budget.
        ranked = sorted(
            range(len(raw)),
            key=lambda i: (raw[i].role + "Control" not in uia.INTERACTIVE, not raw[i].name, i),
        )
        keep = sorted(ranked[: self.config.max_elements])
        index_to_id: dict[int, int] = {}
        out: list[UIElement] = []
        for i in keep:
            r = raw[i]
            eid = self._next_id
            self._next_id += 1
            index_to_id[i] = eid
            parent = r.parent
            while parent is not None and parent not in index_to_id:
                parent = raw[parent].parent
            out.append(
                UIElement(
                    eid,
                    r.name,
                    r.role,
                    r.rect,
                    r.depth,
                    index_to_id.get(parent) if parent is not None else None,
                    r.is_password,
                )
            )
        return out

    @staticmethod
    def window_title(hwnd: int) -> str | None:
        return uia.window_title(hwnd)

    @staticmethod
    def window_at_point(x: int, y: int) -> int | None:
        return uia.window_at(x, y)

    @staticmethod
    def _grab(
        window_rect: tuple[int, int, int, int], follow_mouse: bool = True
    ) -> tuple[Image.Image | None, tuple[int, int]]:
        """Capture the monitor the user is on: the one under the mouse, else the one holding
        the window being read."""
        import mss

        pos = uia.cursor_pos() if follow_mouse else None
        with (getattr(mss, "MSS", None) or mss.mss)() as sct:
            mon = pick_monitor(sct.monitors[1:], (*pos, 1, 1) if pos else window_rect)
            raw = sct.grab(mon)
            return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX"), (
                mon["left"],
                mon["top"],
            )


def _contains(outer: tuple[int, int, int, int], r: tuple[int, int, int, int]) -> bool:
    cx, cy = r[0] + r[2] / 2, r[1] + r[3] / 2
    return outer[0] <= cx <= outer[0] + outer[2] and outer[1] <= cy <= outer[1] + outer[3]


def pick_monitor(
    monitors: list[dict[str, int]], window_rect: tuple[int, int, int, int]
) -> dict[str, int]:
    x, y, w, h = window_rect
    cx, cy = x + w // 2, y + h // 2
    for mon in monitors:
        if (
            mon["left"] <= cx < mon["left"] + mon["width"]
            and mon["top"] <= cy < mon["top"] + mon["height"]
        ):
            return mon
    return monitors[0]
