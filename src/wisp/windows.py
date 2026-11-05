"""Arrange the user's windows: split the screen, snap left or right, maximize, minimize, restore.

"Split the screen, Google Sheets on the right, keep my browser on the left" is a basic thing to
ask a desktop buddy, so Wisp does it with Windows' own window calls, the way Snap does: the
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
