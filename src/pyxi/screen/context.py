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
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from pyxi.config import ScreenConfig
from pyxi.screen import ocr, secrets, uia
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

    def describe(self, to_image=None, cut: str = "") -> str:
        """``cut``: the edge that hides part of it ("bottom"), when one does."""
        x, y, w, h = to_image(self.rect) if to_image else self.rect
        name = (
            "(password field)" if self.is_password else f'"{self.name[:70]}"' if self.name else ""
        )
        line = f"[{self.id}] {self.role} {name} @({x},{y},{w}x{h})".replace("  ", " ")
        return f"{line} (cut off at the {cut} edge: only part of it shows)" if cut else line


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
    # How the window's accessibility tree was read: "ok"; "partial" (it stopped part-way);
    # "failed" (none of it); "none" (no window of this screen was read at all).
    tree: str = "ok"

    # ------------------------------------------------------------------ lookups
    def element(self, element_id: int) -> UIElement | None:
        found = next((e for e in self.elements if e.id == element_id), None)
        if found is None and (line := next((t for t in self.text if t.id == element_id), None)):
            found = UIElement(line.id, line.text, "ScreenText", line.rect)
        return found

    def find_text(
        self,
        quote: str,
        within: tuple[int, int, int, int] | None = None,
        near: tuple[float, float] | None = None,
    ):
        """Where a phrase is on screen, word-exact (from OCR). None if it isn't there.
        ``near``: where it was thought to be, to pick among several places that say it."""
        return ocr.find_text(self.text, quote, within, near)

    def view_rect(self) -> tuple[int, int, int, int] | None:
        """The part of the window that is in the picture (all of the picture when no window
        was read), in screen px: what "did the view change" is asked of."""
        if self.screenshot is None:
            return None
        sx, sy = self.origin
        sw, sh = self.screenshot.width, self.screenshot.height
        x, y, w, h = self.window.rect
        if w <= 0 or h <= 0:
            return sx, sy, sw, sh
        x0, y0 = max(x, sx), max(y, sy)
        x1, y1 = min(x + w, sx + sw), min(y + h, sy + sh)
        return (x0, y0, x1 - x0, y1 - y0) if x1 > x0 and y1 > y0 else (sx, sy, sw, sh)

    def cut_off(self) -> dict[int, str]:
        """Controls that an edge hides part of, by id: "top" or "bottom" (and "left" or
        "right" for ones that run past the window). A row half under the bottom of a list
        looks, in numbers, like a row that is simply short; live the model had to work out
        from "@(432,721,716x14)" that Recovery needed scrolling to. Two signs: it runs past
        the window, or it is the last (first) of its kind in its list and much shorter than
        the others, which is what a list does to the row its edge cuts."""
        bounds = self.view_rect() or self.window.rect
        bx, by, bw, bh = bounds
        if bw <= 0 or bh <= 0:
            return {}
        mine = [
            e
            for e in self.elements
            if e.role != "TaskbarButton"
            and e.role != "Document"
            and (e.role + "Control" in uia.INTERACTIVE or e.role in ("Text", "Image"))
        ]
        out: dict[int, str] = {}
        rows: dict[tuple[int | None, str], list[UIElement]] = {}
        for e in mine:
            x, y, w, h = e.rect
            seen_w = min(x + w, bx + bw) - max(x, bx)
            seen_h = min(y + h, by + bh) - max(y, by)
            if seen_w > 0 and seen_h > 0 and seen_w * seen_h < 0.75 * w * h:
                over = {
                    "bottom": y + h - (by + bh),
                    "top": by - y,
                    "right": x + w - (bx + bw),
                    "left": bx - x,
                }
                out[e.id] = max(over, key=lambda k: over[k])
            rows.setdefault((e.parent, e.role), []).append(e)
        for same in rows.values():
            for e in same:
                # Its column: the rows of one list start at the same x and are as wide.
                col = [
                    s
                    for s in same
                    if abs(s.rect[0] - e.rect[0]) <= 4 and abs(s.rect[2] - e.rect[2]) <= 4
                ]
                if len(col) < 3 or e.id in out:
                    continue
                others = sorted(s.rect[3] for s in col if s is not e)
                if e.rect[3] >= 0.6 * others[len(others) // 2]:
                    continue
                if all(s.rect[1] <= e.rect[1] for s in col):
                    out[e.id] = "bottom"
                elif all(s.rect[1] >= e.rect[1] for s in col):
                    out[e.id] = "top"
        return out

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
        if not w.title and not w.process:
            # Said plainly: "window: ''" read as "they are back at the desktop", which it
            # may or may not be.
            lines = [
                "No window was read on this screen (the bare desktop, or a window that could "
                "not be read): go by the screenshot for what is open."
            ]
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
            cut = self.cut_off()
            lines.extend(
                "  " * min(e.depth, 6) + e.describe(img, cut.get(e.id, ""))
                for e in self.elements[:max_elements]
            )
        else:
            lines.append("(No accessibility tree available: rely on the screenshot.)")
        if self.tree == "failed" and self.elements:
            lines.append(
                "(This window's accessibility tree could not be read this time, so nothing of "
                "the window is listed above: rely on the screenshot for what is in it.)"
            )
        elif self.tree == "partial":
            lines.append(
                "(The accessibility tree stopped part-way: things that are on screen may be "
                "missing from the list above. The screenshot has them.)"
            )
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

    def set_private_apps(self, names: list[str]) -> None:
        """The built-in private list plus the user's own apps (Settings, screen/private_apps.py)."""
        from pyxi.screen.private_apps import patterns

        self._excluded = [
            re.compile(p, re.IGNORECASE) for p in [*self.config.excluded_windows, *patterns(names)]
        ]

    def is_excluded(self, window: WindowInfo) -> str | None:
        haystack = f"{window.title} {window.process}"
        for pattern in self._excluded:
            if pattern.search(haystack):
                return window.app or "private window"
        return None

    def private_rects(self, skip: int | None = None) -> list[tuple[int, int, int, int]]:
        """Where private windows (password managers, banking...) are on screen right now, even
        when they aren't the one in front: they're blacked out of every screenshot."""
        from pyxi import windows

        rects = []
        for hwnd, title, exe in windows.open_windows():
            if hwnd == skip:
                continue
            if self.is_excluded(WindowInfo(title, Path(exe).name if exe else "")):
                r = windows.rect_of(hwnd)
                if r and r[2] > 0 and r[3] > 0:
                    rects.append(r)
        return rects

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

        tree = raw_window.tree if raw_window else "ok"
        shot, origin = (
            self._grab(window.rect, follow_mouse and not hwnd) if pixels else (None, (0, 0))
        )
        on_screen = None
        if shot is not None:
            # One screen's picture goes with that screen's controls only. Live, on three
            # screens, the model got the buttons of a window on the second screen (at
            # x=2037, y=-598 of a 1366 px picture) under a screenshot of the first.
            screen = (origin[0], origin[1], shot.width, shot.height)
            wx, wy, ww, wh = window.rect
            apart = (
                wx >= screen[0] + screen[2]
                or wx + ww <= screen[0]
                or wy >= screen[1] + screen[3]
                or wy + wh <= screen[1]
            )
            if ww > 0 and wh > 0 and apart:
                raw, window, tree = [], WindowInfo(rect=screen), "none"
            else:

                def on_screen(r: uia.RawElement) -> bool:
                    return _contains(screen, r.rect)

        elements = self._number(raw, on_screen)
        if shot is not None:
            self._black_out(shot, origin, self.private_rects(skip=window.hwnd))
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
        text = self._hide_secrets(shot, origin, elements, text)
        return ScreenContext(
            captured_at=now,
            window=window,
            elements=elements,
            screenshot=shot,
            origin=origin,
            text=text,
            tree=tree,
        )

    def glance(self, rect: tuple[int, int, int, int]) -> Image.Image | None:
        """Only the pixels of one part of the screen, to tell whether it changed since the
        last look: no tree, no OCR, nothing kept or sent anywhere. None while sharing is
        paused, or when that part can't be grabbed."""
        x, y, w, h = rect
        if self.paused or w <= 0 or h <= 0:
            return None
        try:
            import mss

            with (getattr(mss, "MSS", None) or mss.mss)() as sct:
                raw = sct.grab({"left": x, "top": y, "width": w, "height": h})
                img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
        except Exception:
            return None
        self._black_out(img, (x, y), self.private_rects())
        return img

    def text_now(self, ctx: ScreenContext) -> list[TextLine] | None:
        """The words on the screen ``ctx`` was a look at, read again now: pixels and OCR, no
        tree (a fraction of a second), to check that something is still where it was before
        drawing on it. None when it can't be read."""
        if ctx.screenshot is None or not self.config.ocr:
            return None
        shot = self.glance((ctx.origin[0], ctx.origin[1], ctx.screenshot.width, ctx.screenshot.height))
        return ocr.read_lines(shot, ctx.origin) if shot is not None else None

    @staticmethod
    def input_idle_s() -> float | None:
        """Seconds since the mouse or keyboard was last touched (when, never what)."""
        return uia.input_idle_s()

    @staticmethod
    def _black_out(
        shot: Image.Image, origin: tuple[int, int], rects: list[tuple[int, int, int, int]]
    ) -> None:
        """Private windows elsewhere on screen go black before OCR or the model sees them."""
        if not rects:
            return
        draw = ImageDraw.Draw(shot)
        for x, y, w, h in rects:
            lx, ly = x - origin[0], y - origin[1]
            draw.rectangle([lx, ly, lx + w, ly + h], fill=(0, 0, 0))

    @staticmethod
    def _hide_secrets(
        shot: Image.Image | None,
        origin: tuple[int, int],
        elements: list[UIElement],
        text: list[TextLine],
    ) -> list[TextLine]:
        """Black out anything that looks like a key or token, in the pixels and the text, so it's
        never sent to the model or saved (see screen/secrets.py)."""
        spots = []
        for e in elements:
            if secrets.looks_secret(e.name):
                e.name = secrets.HIDDEN
                spots.append(e.rect)
        kept = []
        for ln in text:
            if secrets.looks_secret(ln.text):
                spots.append(ln.rect)
            else:
                kept.append(ln)
        if shot is not None and spots:
            draw = ImageDraw.Draw(shot)
            for x, y, w, h in spots:
                lx, ly = x - origin[0], y - origin[1]
                draw.rectangle([lx - 2, ly - 2, lx + w + 2, ly + h + 2], fill=(20, 20, 20))
        return kept

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

    def _number(self, raw: list[uia.RawElement], keep: Any = None) -> list[UIElement]:
        """``keep``: which of them are in the picture (a window can lie across two screens,
        and the desktop lies across all of them)."""
        # Prefer interactive and named elements when trimming to the budget.
        ranked = sorted(
            (i for i in range(len(raw)) if keep is None or keep(raw[i])),
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
