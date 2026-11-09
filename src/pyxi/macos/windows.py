"""The Mac side of ``pyxi.windows``: the window list, screens, snapping, focus.

A window's handle here is its Quartz window number (``kCGWindowNumber``): stable while the
window lives, and what the window list and screen capture use. Moving and focusing go through
Accessibility, matched to the window by app and frame (see ``_ax.ax_window``).

Window titles of other apps only come with the Screen Recording permission. Without it a window
is listed under its app's name ("Safari"), so "the Safari window" still works.

Needs: Accessibility (to move, size and focus windows), Screen Recording (for titles).
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
import time
from pathlib import Path

from pyxi.macos import _ax, appkit

log = logging.getLogger(__name__)

__all__ = [
    "_visible_windows",
    "available",
    "exe_of",
    "focus",
    "foreground",
    "is_minimized",
    "open_new_window",
    "open_windows",
    "place",
    "rect_of",
    "same_app",
    "screens",
    "snap",
    "work_area",
]

# Browsers that take a "new window" flag; Safari is asked through AppleScript instead.
_CHROMIUM = {"google chrome", "microsoft edge", "brave browser", "vivaldi", "chromium", "opera", "arc"}


def available() -> bool:
    return sys.platform == "darwin"


def _name(found: list[dict]) -> list[dict]:
    found.sort(key=lambda s: (s["rect"][0], s["rect"][1]))
    names = {1: ["only"], 2: ["left", "right"], 3: ["left", "middle", "right"]}.get(len(found))
    for i, s in enumerate(found):
        s["n"] = i + 1
        s["name"] = names[i] if names else f"screen {i + 1}"
    return found


def screens() -> list[dict]:
    """Every display, left to right: {"n", "name", "rect", "work", "primary"}, in points with the
    origin at the main screen's top left (Cocoa counts y up from the bottom; flipped here)."""
    if not available():
        return []
    try:
        all_screens = list(appkit().NSScreen.screens() or [])
    except Exception as exc:
        log.debug("NSScreen failed: %s", exc)
        return []
    if not all_screens:
        return []
    height = all_screens[0].frame().size.height  # the main screen (with the menu bar) is first

    def flip(r) -> tuple[int, int, int, int]:
        return (
            int(r.origin.x),
            int(height - r.origin.y - r.size.height),
            int(r.size.width),
            int(r.size.height),
        )

    found = [
        {"rect": flip(s.frame()), "work": flip(s.visibleFrame()), "primary": i == 0}
        for i, s in enumerate(all_screens)
    ]
    return _name(found)


def open_windows(minimized: bool = False) -> list[tuple[int, str, str]]:
    """App windows with a title, frontmost first: (window number, title, executable). Minimized
    ones (and ones on other Spaces) only when asked."""
    if not available():
        return []
    out: list[tuple[int, str, str]] = []
    for info in _ax.cg_windows(all_windows=minimized):
        if not _ax.is_app_window(info):
            continue
        title = str(info.get(_ax.NAME) or "").strip()
        if not title:
            if not info.get(_ax.ONSCREEN, True):
                continue  # an untitled window nobody can see: a helper, not an app
            title = str(info.get(_ax.OWNER) or "")  # no Screen Recording: the app's name
        if not title:
            continue
        pid = int(info.get(_ax.PID, 0))
        out.append((int(info[_ax.NUMBER]), title, _ax.exe_of_pid(pid)))
    return out


def _visible_windows() -> set[int]:
    return {h for h, _t, _e in open_windows()}


def exe_of(hwnd: int) -> str:
    info = _ax.cg_info(hwnd)
    return _ax.exe_of_pid(int(info.get(_ax.PID, 0))) if info else ""


def rect_of(hwnd: int) -> tuple[int, int, int, int] | None:
    info = _ax.cg_info(hwnd)
    return _ax.bounds(info) if info else None


def is_minimized(hwnd: int) -> bool:
    win = _ax.ax_window(hwnd)
    value = _ax.attr(win, "AXMinimized") if win is not None else None
    if value is not None:
        return bool(value)
    info = _ax.cg_info(hwnd)
    return bool(info) and not info.get(_ax.ONSCREEN, True)


def same_app(a: int, b: int) -> bool:
    if not a or not b:
        return False
    ia, ib = _ax.cg_info(a), _ax.cg_info(b)
    pa = int(ia.get(_ax.PID, 0)) if ia else 0
    return bool(pa) and ib is not None and pa == int(ib.get(_ax.PID, 0))


def foreground() -> int:
    """The front window of the app in front (0 if there's none)."""
    if not available():
        return 0
    pid = _ax.frontmost_pid()
    if not pid:
        return 0
    for info in _ax.cg_windows():
        if int(info.get(_ax.PID, 0)) == pid and _ax.is_app_window(info, min_size=1):
            return int(info[_ax.NUMBER])
    return 0


def work_area(hwnd: int) -> tuple[int, int, int, int]:
    """The usable part (no menu bar, no Dock) of the screen the window is on."""
    all_screens = screens()
    r = rect_of(hwnd)
    if r:
        cx, cy = r[0] + r[2] // 2, r[1] + r[3] // 2
        for s in all_screens:
            if _ax.contains(s["rect"], cx, cy):
                return s["work"]
    main = next((s for s in all_screens if s["primary"]), all_screens[0] if all_screens else None)
    return main["work"] if main else (0, 0, 1440, 900)


def place(hwnd: int, x: int, y: int, w: int, h: int) -> None:
    win = _ax.ax_window(hwnd)
    if win is None:
        log.info("place: no Accessibility element for window %s (permission?)", hwnd)
        return
    if _ax.attr(win, "AXFullScreen"):
        _ax.set_attr(win, "AXFullScreen", False)  # a full-screen window can't be moved
        time.sleep(0.6)
    if _ax.attr(win, "AXMinimized"):
        _ax.set_attr(win, "AXMinimized", False)
    # Position, size, then position again: a window grown near a screen edge gets pushed back.
    _ax.set_attr(win, "AXPosition", _ax.point_value(x, y))
    _ax.set_attr(win, "AXSize", _ax.size_value(w, h))
    _ax.set_attr(win, "AXPosition", _ax.point_value(x, y))


def snap(hwnd: int, where: str) -> None:
    win = _ax.ax_window(hwnd)
    if where == "minimize":
        _ax.set_attr(win, "AXMinimized", True)
        return
    if where == "restore":
        _ax.set_attr(win, "AXMinimized", False)
        _ax.perform(win, "AXRaise")
        return
    x, y, w, h = work_area(hwnd)
    if where == "maximize":  # the whole work area (the green button's zoom varies by app)
        place(hwnd, x, y, w, h)
        return
    half = w // 2
    rects = {
        "left": (x, y, half, h),
        "right": (x + half, y, w - half, h),
        "top": (x, y, w, h // 2),
        "bottom": (x, y + h // 2, w, h - h // 2),
    }
    place(hwnd, *rects[where])


def focus(hwnd: int) -> bool:
    """Bring a window to the front: its app first, then the window within the app."""
    info = _ax.cg_info(hwnd)
    if info is None:
        return False
    pid = int(info.get(_ax.PID, 0))
    win = _ax.ax_window(hwnd)
    if win is not None and _ax.attr(win, "AXMinimized"):
        _ax.set_attr(win, "AXMinimized", False)
    app = _ax.running_app(pid)
    try:
        flag = getattr(appkit(), "NSApplicationActivateIgnoringOtherApps", 1 << 1)
        if app is not None:
            app.activateWithOptions_(flag)
    except Exception as exc:
        log.debug("activate failed: %s", exc)
    if win is not None:
        _ax.perform(win, "AXRaise")
        _ax.set_attr(win, "AXMain", True)
    for _ in range(10):
        if foreground() == int(hwnd):
            return True
        time.sleep(0.03)
    return _ax.frontmost_pid() == pid


def _new_window_command(target: str, like: int | None) -> list[str]:
    bundle = ""
    if like:
        info = _ax.cg_info(like)
        bundle = _ax.bundle_of_pid(int(info.get(_ax.PID, 0))) if info else ""
    name = Path(bundle).stem.lower() if bundle else ""
    if name in _CHROMIUM:
        return ["open", "-na", bundle, "--args", "--new-window", target]
    if name == "firefox":
        return ["open", "-na", bundle, "--args", "-new-window", target]
    if name == "safari":
        quoted = target.replace("\\", "\\\\").replace('"', '\\"')
        return [
            "osascript",
            "-e",
            f'tell application "Safari" to make new document with properties {{URL:"{quoted}"}}',
        ]
    return ["open", target]  # the default browser (it may pick a tab rather than a window)


async def open_new_window(target: str, like: int | None = None, is_url: bool = True) -> int | None:
    """Open a site (in a new window of the browser they're in) or an app, and return its window
    (None if it never showed)."""
    before = _visible_windows()
    if is_url:
        subprocess.Popen(_new_window_command(target, like))
    else:
        from pyxi.macos.apps import start

        start(target)
    for _ in range(40):  # up to ~8 s for the window to appear
        await asyncio.sleep(0.2)
        new = _visible_windows() - before
        if new:
            await asyncio.sleep(0.4)
            return max(new)
    return None
