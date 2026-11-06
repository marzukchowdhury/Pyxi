"""Arrange the user's windows: split the screen, snap left or right, maximize, minimize, restore.

"Split the screen, Google Sheets on the right, keep my browser on the left" is a basic thing to
ask a desktop buddy, so Pyxi does it with Windows' own window calls, the way Snap does: the
window goes to exactly half of the screen it's on (taskbar respected, no gaps from Windows'
invisible borders), and a site or app opens in its own new window on the other half.

Only moves, sizes and opens; never closes anything.
"""

from __future__ import annotations

import asyncio
import ctypes
import ctypes.wintypes as wt
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

log = logging.getLogger(__name__)

SW_MAXIMIZE, SW_MINIMIZE, SW_RESTORE = 3, 6, 9
SWP_NOZORDER, SWP_SHOWWINDOW = 0x0004, 0x0040
BROWSER_FLAGS = {
    "firefox.exe": "-new-window",
    "chrome.exe": "--new-window",
    "msedge.exe": "--new-window",
    "brave.exe": "--new-window",
}


class _MonitorInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD),
        ("rcMonitor", wt.RECT),
        ("rcWork", wt.RECT),
        ("dwFlags", wt.DWORD),
    ]


def available() -> bool:
    return sys.platform == "win32"


def _user32():
    return ctypes.windll.user32  # type: ignore[attr-defined]


def work_area(hwnd: int) -> tuple[int, int, int, int]:
    """The usable area (without the taskbar) of the monitor the window is on: x, y, w, h."""
    u = _user32()
    mon = u.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
    info = _MonitorInfo()
    info.cbSize = ctypes.sizeof(_MonitorInfo)
    u.GetMonitorInfoW(mon, ctypes.byref(info))
    r = info.rcWork
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def _invisible_border(hwnd: int) -> tuple[int, int, int, int]:
    """Windows 10/11 windows have a few px of invisible resize border; without accounting for it,
    two snapped windows leave a gap between them."""
    outer = wt.RECT()
    _user32().GetWindowRect(hwnd, ctypes.byref(outer))
    frame = wt.RECT()
    try:
        ctypes.windll.dwmapi.DwmGetWindowAttribute(  # type: ignore[attr-defined]
            hwnd,
            9,
            ctypes.byref(frame),
            ctypes.sizeof(frame),  # DWMWA_EXTENDED_FRAME_BOUNDS
        )
    except OSError:
        return 0, 0, 0, 0
    return (
        frame.left - outer.left,
        frame.top - outer.top,
        outer.right - frame.right,
        outer.bottom - frame.bottom,
    )


def place(hwnd: int, x: int, y: int, w: int, h: int) -> None:
    u = _user32()
    if u.IsZoomed(hwnd) or u.IsIconic(hwnd):
        u.ShowWindow(hwnd, SW_RESTORE)  # a maximized window ignores being moved
    left, top, right, bottom = _invisible_border(hwnd)
    u.SetWindowPos(
        hwnd,
        0,
        x - left,
        y - top,
        w + left + right,
        h + top + bottom,
        SWP_NOZORDER | SWP_SHOWWINDOW,
    )


def snap(hwnd: int, where: str) -> None:
    u = _user32()
    if where == "maximize":
        u.ShowWindow(hwnd, SW_MAXIMIZE)
        return
    if where == "minimize":
        u.ShowWindow(hwnd, SW_MINIMIZE)
        return
    if where == "restore":
        u.ShowWindow(hwnd, SW_RESTORE)
        return
    x, y, w, h = work_area(hwnd)
    half = w // 2
    rects = {
        "left": (x, y, half, h),
        "right": (x + half, y, w - half, h),
        "top": (x, y, w, h // 2),
        "bottom": (x, y + h // 2, w, h - h // 2),
    }
    place(hwnd, *rects[where])


def focus(hwnd: int) -> bool:
    """Bring a window to the front so typing goes to it. Windows only lets the app the user
    last used do this, so a harmless Alt tap first marks this process as "in use"."""
    u = _user32()
    if u.IsIconic(hwnd):
        u.ShowWindow(hwnd, SW_RESTORE)
    u.keybd_event(0x12, 0, 0, 0)  # Alt down
    u.keybd_event(0x12, 0, 2, 0)  # Alt up
    ok = bool(u.SetForegroundWindow(hwnd))
    for _ in range(10):  # the switch takes a moment, with no window in front meanwhile
        if int(u.GetForegroundWindow() or 0) == int(hwnd):
            return True
        time.sleep(0.03)
    return ok


def _visible_windows() -> set[int]:
    u = _user32()
    found: set[int] = set()

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def visit(hwnd, _):
        if u.IsWindowVisible(hwnd) and u.GetWindowTextLengthW(hwnd) > 0:
            found.add(int(hwnd))
        return True

    u.EnumWindows(visit, 0)
    return found


def exe_of(hwnd: int) -> str:
    """Full path of the program that owns a window ('' if Windows won't say)."""
    pid = wt.DWORD()
    _user32().GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    k = ctypes.windll.kernel32  # type: ignore[attr-defined]
    handle = k.OpenProcess(0x1000, False, pid.value)
    if not handle:
        return ""
    try:
        size = wt.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        return buf.value if k.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)) else ""
    finally:
        k.CloseHandle(handle)


async def open_new_window(target: str, like: int | None = None, is_url: bool = True) -> int | None:
    """Open a site or app in its own new window and return that window (None if it never showed).

    Sites open in a new window of the browser the user is in (so it can be placed), falling back to
    Edge, which every Windows PC has."""
    before = _visible_windows()
    if is_url:
        exe = exe_of(like) if like else ""
        flag = BROWSER_FLAGS.get(Path(exe).name.lower()) if exe else None
        if flag:
            subprocess.Popen([exe, flag, target])
        else:
            subprocess.Popen(
                ["cmd", "/c", "start", "", "msedge", "--new-window", target], shell=False
            )
    else:
        os.startfile(target)  # type: ignore[attr-defined]
    for _ in range(40):  # up to ~8 s for the window to appear
        await asyncio.sleep(0.2)
        new = _visible_windows() - before
        if new:
            await asyncio.sleep(0.4)  # let it finish creating before it's moved
            return max(new)
    return None


# Words that name a program but also turn up in ordinary requests ("explain this code").
_TOO_COMMON = {
    "code",
    "explorer",
    "applicationframehost",
    "electron",
    "python",
    "pythonw",
    "cmd",
    "windowsterminal",
    "powershell",
    "pyxi",
    "shell",
    "settings",
    "search",
    "start",
    "files",
}


def app_names(title: str, exe: str) -> set[str]:
    """What someone might call a window: its program ("notepad", "capcut") and the app part of
    its title ("Untitled - Notepad" -> "notepad", "Vercel - Google Chrome" -> "google chrome")."""
    names = {Path(exe).stem.lower()} if exe else set()
    if " - " in title:
        names.add(title.rsplit(" - ", 1)[1].strip().lower())
    return {n for n in names if len(n) >= 4 and n not in _TOO_COMMON}


def named_window(text: str, windows: list[tuple[int, str, str]]) -> int | None:
    """The open window the request names, topmost first (windows come in z-order), or None.

    ``windows`` holds (hwnd, title, exe). "Guide me to turn on word wrap in Notepad" -> the
    Notepad window, wherever it is, so Pyxi looks at it even if the mouse is on another screen."""
    words = " " + "".join(c.lower() if c.isalnum() else " " for c in text) + " "
    for hwnd, title, exe in windows:
        for name in app_names(title, exe):
            spaced = " " + "".join(c if c.isalnum() else " " for c in name).strip() + " "
            if spaced.strip() and spaced in words:
                return hwnd
    return None


def open_windows(minimized: bool = False) -> list[tuple[int, str, str]]:
    """Visible top-level windows with a title, topmost first: (hwnd, title, exe). Minimized ones
    only when asked."""
    if not available():
        return []
    u = _user32()
    found: list[tuple[int, str, str]] = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def visit(hwnd, _):
        n = u.GetWindowTextLengthW(hwnd)
        if u.IsWindowVisible(hwnd) and n > 0 and (minimized or not u.IsIconic(hwnd)):
            buf = ctypes.create_unicode_buffer(n + 1)
            u.GetWindowTextW(hwnd, buf, n + 1)
            found.append((int(hwnd), buf.value, ""))
        return True

    u.EnumWindows(visit, 0)
    return [(h, t, exe_of(h)) for h, t, _ in found]


def is_minimized(hwnd: int) -> bool:
    return bool(available() and _user32().IsIconic(hwnd))


def same_app(a: int, b: int) -> bool:
    """Do two windows belong to the same program? An app's own menus, flyouts and dialogs are
    windows of their own, and opening one isn't switching away from the app."""
    if not available() or not a or not b:
        return False
    u = _user32()
    if u.GetAncestor(a, 3) == b or u.GetAncestor(b, 3) == a:  # GA_ROOTOWNER
        return True
    pa, pb = wt.DWORD(), wt.DWORD()
    u.GetWindowThreadProcessId(a, ctypes.byref(pa))
    u.GetWindowThreadProcessId(b, ctypes.byref(pb))
    return bool(pa.value) and pa.value == pb.value


def pane_rects(
    area: tuple[int, int, int, int], n: int, arrangement: str = "auto"
) -> list[tuple[int, int, int, int]]:
    """Split a work area into ``n`` panes, gap-free, in reading order.

    columns: side by side · rows: stacked · grid: 2x2 (four) · main: a big left half and the rest
    stacked on the right. "auto" is columns for two or three (what "split it into three parts"
    means) and a grid for four."""
    x, y, w, h = area
    n = max(1, min(n, 6))
    if arrangement == "auto":
        arrangement = "grid" if n == 4 else "columns"
    if n == 1:
        return [area]
    if arrangement == "rows":
        cuts = [y + h * i // n for i in range(n + 1)]
        return [(x, cuts[i], w, cuts[i + 1] - cuts[i]) for i in range(n)]
    if arrangement == "grid" and n == 4:
        mx, my = x + w // 2, y + h // 2
        return [
            (x, y, mx - x, my - y),
            (mx, y, x + w - mx, my - y),
            (x, my, mx - x, y + h - my),
            (mx, my, x + w - mx, y + h - my),
        ]
    if arrangement in ("main", "grid"):
        mx = x + w // 2
        rest = pane_rects((mx, y, x + w - mx, h), n - 1, "rows")
        return [(x, y, mx - x, h), *rest]
    cuts = [x + w * i // n for i in range(n + 1)]
    return [(cuts[i], y, cuts[i + 1] - cuts[i], h) for i in range(n)]


# What a site's window is called, when its address doesn't say (Gmail's tab title is "Gmail").
_SITE_TITLES = {
    "mail.google.com": "gmail",
    "music.youtube.com": "youtube music",
    "web.whatsapp.com": "whatsapp",
    "docs.google.com": "google docs",
    "sheets.google.com": "google sheets",
    "calendar.google.com": "google calendar",
    "x.com": "x",
}


def window_like(name: str, open_now: list[tuple[int, str, str]], skip: set[int]) -> int | None:
    """An open window that already shows ``name`` ("Gmail", "Spotify", "https://stake.com"),
    by its title or program, topmost first. A site in a background tab can't be seen this way."""
    from urllib.parse import urlparse

    text = name.strip().lower()
    if "://" in text or "." in text.split("/")[0]:
        host = urlparse(text if "://" in text else "https://" + text).hostname or ""
        host = host.removeprefix("www.")
        text = _SITE_TITLES.get(host) or host.split(".")[0]
    words = " " + "".join(c if c.isalnum() else " " for c in text).strip() + " "
    if len(words.strip()) < 2:
        return None
    for hwnd, title, exe in open_now:
        if hwnd in skip:
            continue
        hay = (
            " "
            + "".join(c.lower() if c.isalnum() else " " for c in f"{title} {Path(exe).stem}")
            + " "
        )
        if words in hay:
            return hwnd
    return None


def foreground() -> int:
    return int(_user32().GetForegroundWindow() or 0) if available() else 0
