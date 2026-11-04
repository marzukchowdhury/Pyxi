"""Windows UI Automation: the accessibility tree of the foreground window.

This is what gives Wisp *structured* screen understanding: every button, field, tab and
menu item with its name, role and exact on-screen rectangle. Pixels tell the model what
things look like; the tree tells it what they are and precisely where they are, which is
what makes pointing and exploded 3D views accurate.

On other platforms (or if UIA fails) Wisp falls back to vision only.
"""

from __future__ import annotations

import ctypes
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
    *, budget_s: float = 1.5, max_elements: int = 400, max_depth: int = 25
) -> tuple[RawWindow | None, list[RawElement]]:
    """Walk the foreground window's UIA tree within a time budget. Never raises."""
    if not available():
        return None, []
    try:
        import uiautomation as auto
    except ImportError:
        return None, []

    try:
        with auto.UIAutomationInitializerInThread(debug=False):
            return _walk(auto, budget_s, max_elements, max_depth)
    except Exception as exc:  # UIA is best-effort: some apps throw COM errors mid-walk
        log.warning("UI Automation failed: %s", exc)
        return None, []


def _walk(
    auto, budget_s: float, max_elements: int, max_depth: int
) -> tuple[RawWindow | None, list[RawElement]]:
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
