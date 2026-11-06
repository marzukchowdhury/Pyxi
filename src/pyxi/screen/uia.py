"""Windows UI Automation: the accessibility tree of the foreground window.

This is what gives Pyxi *structured* screen understanding: every button, field, tab and
menu item with its name, role and exact on-screen rectangle. Pixels tell the model what
things look like; the tree tells it what they are and precisely where they are, which is
what makes pointing and exploded 3D views accurate.

On other platforms (or if UIA fails) Pyxi falls back to vision only.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import logging
import sys
import time
from dataclasses import dataclass

log = logging.getLogger(__name__)

# Control types worth showing the model: things you can act on, plus structure and labels.
INTERACTIVE = {
    "ButtonControl",
    "CheckBoxControl",
    "ComboBoxControl",
    "EditControl",
    "HyperlinkControl",
    "ListItemControl",
    "MenuItemControl",
    "RadioButtonControl",
    "SliderControl",
    "SpinnerControl",
    "SplitButtonControl",
    "TabItemControl",
    "TreeItemControl",
    "DataItemControl",
    "DocumentControl",
}
STRUCTURAL = {
    "GroupControl",
    "PaneControl",
    "ToolBarControl",
    "TabControl",
    "ListControl",
    "MenuBarControl",
    "TreeControl",
    "TableControl",
    "HeaderControl",
    "StatusBarControl",
    "TitleBarControl",
}
LABELS = {"TextControl", "ImageControl", "HeaderItemControl"}


@dataclass
class RawElement:
    name: str
    role: str
    rect: tuple[int, int, int, int]  # x, y, w, h in physical screen pixels
    depth: int
    parent: int | None  # index into the returned list
    is_password: bool
    automation_id: str


@dataclass
class RawWindow:
    title: str
    process: str
    rect: tuple[int, int, int, int]
    hwnd: int


def available() -> bool:
    return sys.platform == "win32"


def cursor_pos() -> tuple[int, int] | None:
    """Where the mouse is, in physical screen px (the process is DPI aware)."""
    if sys.platform != "win32":
        return None
    pt = ctypes.wintypes.POINT()
    if not ctypes.windll.user32.GetCursorPos(ctypes.byref(pt)):
        return None
    return pt.x, pt.y


def window_title(hwnd: int) -> str | None:
    """A window's title right now; None if it no longer exists."""
    if sys.platform != "win32":
        return None
    user32 = ctypes.windll.user32
    if not user32.IsWindow(hwnd):
        return None
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def window_at(x: int, y: int) -> int | None:
    """The top-level window the user sees at a point: the first visible, real window in
    z-order whose frame contains it. Pyxi's own (click-through) windows don't count."""
    if sys.platform != "win32":
        return None
    import ctypes.wintypes as wt

    user32 = ctypes.windll.user32
    dwm = ctypes.windll.dwmapi
    found: list[int] = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def visit(hwnd, _):
        if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
            return True
        # Click-through overlays (NVIDIA's, Discord's, Pyxi's own) and tool palettes sit on
        # top of everything but aren't what anyone is looking at.
        ex = user32.GetWindowLongW(hwnd, -20) & 0xFFFFFFFF
        if ex & (0x20 | 0x80 | 0x08000000):  # TRANSPARENT, TOOLWINDOW, NOACTIVATE
            return True
        cloaked = ctypes.c_int(0)
        dwm.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
        if cloaked.value:  # on another virtual desktop, or a hidden UWP shell
            return True
        r = wt.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
            return True
        if not (r.left <= x < r.right and r.top <= y < r.bottom):
            return True
        title = window_title(hwnd) or ""
        if not title.strip() or title.startswith("Pyxi"):
            return True
        found.append(hwnd)
        return False  # the topmost match is the one they see

    user32.EnumWindows(visit, 0)
    return found[0] if found else None


def process_name(pid: int) -> str:
    if sys.platform != "win32":
        return ""
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    try:
        size = ctypes.c_ulong(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return buf.value.rsplit("\\", 1)[-1]
        return ""
    finally:
        kernel32.CloseHandle(handle)


def read_foreground(
    *,
    budget_s: float = 1.5,
    max_elements: int = 400,
    max_depth: int = 25,
    follow_mouse: bool = True,
    hwnd: int | None = None,
) -> tuple[RawWindow | None, list[RawElement]]:
    """Walk the foreground window's UIA tree within a time budget (or ``hwnd``'s, when the
    request names a window). Never raises."""
    if not available():
        return None, []
    try:
        import uiautomation as auto
    except ImportError:
        return None, []

    try:
        with auto.UIAutomationInitializerInThread(debug=False):
            return _walk(auto, budget_s, max_elements, max_depth, follow_mouse, hwnd)
    except Exception as exc:  # UIA is best-effort: some apps throw COM errors mid-walk
        log.warning("UI Automation failed: %s", exc)
        return None, []


def _walk(
    auto,
    budget_s: float,
    max_elements: int,
    max_depth: int,
    follow_mouse: bool = True,
    named: int | None = None,
) -> tuple[RawWindow | None, list[RawElement]]:
    # The window the user is looking at: the one under the mouse (on a second monitor the
    # foreground window is often somewhere else entirely), else the foreground window.
    top = None
    pos = cursor_pos() if follow_mouse and not named else None
    hwnd = named or (window_at(*pos) if pos else None)
    if hwnd:
        try:
            top = auto.ControlFromHandle(hwnd)
        except Exception:
            top = None
    if top is None:
        fg = auto.GetForegroundControl()
        if fg is None:
            return None, []
        top = fg.GetTopLevelControl() or fg
    r = top.BoundingRectangle
    window = RawWindow(
        title=top.Name or "",
        process=process_name(top.ProcessId),
        rect=(r.left, r.top, r.width(), r.height()),
        hwnd=top.NativeWindowHandle,
    )

    deadline = time.monotonic() + budget_s
    out: list[RawElement] = []
    stack: list[tuple[int, int | None]] = []  # depth -> index of last kept ancestor
    for control, depth in auto.WalkControl(top, includeTop=False, maxDepth=max_depth):
        if time.monotonic() > deadline or len(out) >= max_elements:
            break
        try:
            role = control.ControlTypeName
            if role not in INTERACTIVE and role not in STRUCTURAL and role not in LABELS:
                continue
            if control.IsOffscreen:
                continue
            rect = control.BoundingRectangle
            w, h = rect.width(), rect.height()
            if w < 3 or h < 3:
                continue
            name = (control.Name or "").strip()
            if role in LABELS and not name:
                continue
            if role in STRUCTURAL and not name and w * h < 2_000:
                continue
        except Exception:
            continue
        while stack and stack[-1][0] >= depth:
            stack.pop()
        parent = stack[-1][1] if stack else None
        is_password = False
        try:
            is_password = bool(control.GetPropertyValue(auto.PropertyId.IsPasswordProperty))
        except Exception:
            pass
        out.append(
            RawElement(
                name=name[:160],
                role=role.removesuffix("Control"),
                rect=(rect.left, rect.top, w, h),
                depth=depth,
                parent=parent,
                is_password=is_password,
                automation_id=(control.AutomationId or "")[:80],
            )
        )
        stack.append((depth, len(out) - 1))
    return window, out


def read_taskbar(budget_s: float = 0.4) -> list[RawElement]:
    """The taskbar's buttons (Start, pinned and running apps, the tray, the clock), named the
    way Windows names them ("YouTube Music pinned"), on every monitor. Never raises."""
    if not available():
        return []
    try:
        import uiautomation as auto

        out: list[RawElement] = []
        deadline = time.monotonic() + budget_s
        with auto.UIAutomationInitializerInThread(debug=False):
            root = auto.GetRootControl()
            for bar in root.GetChildren():
                if bar.ClassName not in ("Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
                    continue
                for control, depth in auto.WalkControl(bar, maxDepth=8):
                    if time.monotonic() > deadline:
                        return out
                    if control.ControlTypeName != "ButtonControl" or not control.Name:
                        continue
                    r = control.BoundingRectangle
                    if r.width() < 6 or r.height() < 6:
                        continue
                    name = " ".join(control.Name.replace("\u200e", "").split())
                    out.append(
                        RawElement(
                            name[:80],
                            "TaskbarButton",
                            (r.left, r.top, r.width(), r.height()),
                            depth,
                            None,
                            False,
                            "",
                        )
                    )
        return out
    except Exception as exc:
        log.debug("taskbar read failed: %s", exc)
        return []


@dataclass
class RawFocus:
    window_title: str
    process: str
    name: str
    role: str
    rect: tuple[int, int, int, int]


def read_focus() -> RawFocus | None:
    """The element that has keyboard focus right now: cheap enough to poll every second.

    Focus changes are a privacy-friendly proxy for "what the user interacted with": clicking
    a button, tabbing into a field or opening a menu moves focus. No keystrokes are read.
    """
    if not available():
        return None
    try:
        import uiautomation as auto

        with auto.UIAutomationInitializerInThread(debug=False):
            c = auto.GetFocusedControl()
            if c is None:
                return None
            top = c.GetTopLevelControl() or c
            r = c.BoundingRectangle
            is_password = False
            try:
                is_password = bool(c.GetPropertyValue(auto.PropertyId.IsPasswordProperty))
            except Exception:
                pass
            return RawFocus(
                window_title=top.Name or "",
                process=process_name(top.ProcessId),
                name="(password field)" if is_password else (c.Name or "").strip()[:160],
                role=c.ControlTypeName.removesuffix("Control"),
                rect=(r.left, r.top, r.width(), r.height()),
            )
    except Exception as exc:
        log.debug("focus read failed: %s", exc)
        return None
