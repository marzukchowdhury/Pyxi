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
import threading
import time
from dataclasses import dataclass
from typing import Any

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
    # "partial": the walk stopped early (an app threw part-way), "failed": none of the tree
    # could be read. Either way the window itself is known, and Pyxi says so to the model.
    tree: str = "ok"


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


def _real_windows() -> list[tuple[int, tuple[int, int, int, int]]]:
    """Every visible, real top-level window, topmost first: (hwnd, (left, top, right, bottom)).
    Pyxi's own (click-through) windows don't count."""
    if sys.platform != "win32":
        return []
    import ctypes.wintypes as wt

    user32 = ctypes.windll.user32
    dwm = ctypes.windll.dwmapi
    found: list[tuple[int, tuple[int, int, int, int]]] = []

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
        title = window_title(hwnd) or ""
        if not title.strip() or title.startswith("Pyxi"):
            return True
        found.append((int(hwnd), (r.left, r.top, r.right, r.bottom)))
        return True

    user32.EnumWindows(visit, 0)
    return found


def window_at(x: int, y: int) -> int | None:
    """The top-level window the user sees at a point: the first visible, real window in
    z-order whose frame contains it. Pyxi's own (click-through) windows don't count."""
    for hwnd, (left, top, right, bottom) in _real_windows():
        if left <= x < right and top <= y < bottom:
            return hwnd  # the topmost match is the one they see
    return None


def screen_at(x: int, y: int) -> tuple[int, int, int, int] | None:
    """The monitor a point is on, as x, y, w, h in physical px."""
    if sys.platform != "win32":
        return None
    import ctypes.wintypes as wt

    class Info(ctypes.Structure):
        _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT), ("dwFlags", wt.DWORD)]

    user32 = ctypes.WinDLL("user32")  # its own handle: the types set here are nobody else's
    user32.MonitorFromPoint.restype = ctypes.c_void_p
    user32.MonitorFromPoint.argtypes = [wt.POINT, wt.DWORD]
    user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    mon = user32.MonitorFromPoint(wt.POINT(x, y), 2)  # MONITOR_DEFAULTTONEAREST
    info = Info()
    info.cbSize = ctypes.sizeof(Info)
    if not mon or not user32.GetMonitorInfoW(mon, ctypes.byref(info)):
        return None
    m = info.rcMonitor
    return m.left, m.top, m.right - m.left, m.bottom - m.top


def top_window_on(screen: tuple[int, int, int, int]) -> int | None:
    """The topmost real window that sits on a screen (its middle is on it)."""
    sx, sy, sw, sh = screen
    for hwnd, (left, top, right, bottom) in _real_windows():
        cx, cy = (left + right) // 2, (top + bottom) // 2
        if right - left > 80 and bottom - top > 80 and sx <= cx < sx + sw and sy <= cy < sy + sh:
            return hwnd
    return None


Box = tuple[int, int, int, int]  # x, y, w, h


def _clip(r: Box, to: Box) -> Box | None:
    x0, y0 = max(r[0], to[0]), max(r[1], to[1])
    x1, y1 = min(r[0] + r[2], to[0] + to[2]), min(r[1] + r[3], to[1] + to[3])
    return (x0, y0, x1 - x0, y1 - y0) if x1 > x0 and y1 > y0 else None


def _share_in_view(part: Box, above: list[Box], grid: int = 12) -> float:
    """How much of this part of a window nothing above it covers (0..1), by sampling."""
    seen = 0
    for i in range(grid):
        for j in range(grid):
            px = part[0] + (i + 0.5) * part[2] / grid
            py = part[1] + (j + 0.5) * part[3] / grid
            if not any(a[0] <= px < a[0] + a[2] and a[1] <= py < a[1] + a[3] for a in above):
                seen += 1
    return seen / (grid * grid)


def windows_in_view(
    stack: list[tuple[int, tuple[int, int, int, int]]],
    screen: Box,
    main: int = 0,
    limit: int = 3,
) -> list[tuple[int, Box, list[Box]]]:
    """The other windows a picture of ``screen`` shows, besides ``main`` (the one under the
    mouse): (hwnd, the part of it on this screen, the windows above it), most in view first.

    Two windows side by side are both in the picture, and live the model was given the
    controls of one of them only, so nothing could be drawn on the other. ``stack`` is
    `_real_windows()`: topmost first, (hwnd, (left, top, right, bottom)). A window counts
    when a good part of the screen is its own and most of that is not behind another window:
    one that only peeks out from behind the window in front is not what anyone is reading."""
    area = max(1, screen[2] * screen[3])
    found: list[tuple[float, int, Box, list[Box]]] = []
    above: list[Box] = []
    for hwnd, (left, top, right, bottom) in stack:
        rect = (left, top, right - left, bottom - top)
        part = _clip(rect, screen)
        if hwnd != main and part and part[2] >= 200 and part[3] >= 150:
            showing = _share_in_view(part, above) * part[2] * part[3]
            if showing >= 0.08 * area and showing >= 0.5 * part[2] * part[3]:
                found.append((showing, hwnd, part, list(above)))
        above.append(rect)
    found.sort(key=lambda f: -f[0])
    return [(hwnd, part, over) for _, hwnd, part, over in found[: max(0, limit)]]


def other_windows(screen: Box, main: int = 0, limit: int = 3) -> list[tuple[int, Box, list[Box]]]:
    """`windows_in_view` of the windows that are open now (the desktop itself is not one)."""
    shell = desktop_window()
    stack = [(hwnd, r) for hwnd, r in _real_windows() if hwnd != shell]
    return windows_in_view(stack, screen, main, limit)


def read_window(
    hwnd: int, *, budget_s: float, max_elements: int = 400, max_depth: int = 25
) -> tuple[RawWindow, list[RawElement]]:
    """One more window of the screen, read in one call or not at all: its controls when they
    came back within ``budget_s``, else the window alone, marked as not read. It is never
    walked the slow way: the look as a whole has to stay quick. Never raises."""
    window = named_window(hwnd)
    if not available() or budget_s <= 0:
        window.tree = "failed"
        return window, []
    try:
        import uiautomation as auto

        got = read_in_one_call(auto, hwnd, budget_s, max_elements, max_depth)
    except Exception as exc:
        log.debug("couldn't read another window of the screen: %s", exc)
        got = None
    if got is None:
        window.tree = "failed"
    return window, got or []


def named_window(hwnd: int) -> RawWindow:
    """A window's title, program and place, asked of Windows directly (nothing of what is in
    it): enough to tell whether it is a private one before any of it is read."""
    try:
        return RawWindow(
            title=window_title(hwnd) or "",
            process=process_name(_pid_of(hwnd)),
            rect=_rect_of(hwnd),
            hwnd=hwnd,
        )
    except Exception:
        return RawWindow("", "", (0, 0, 0, 0), hwnd)


def desktop_window() -> int:
    """The desktop itself (its icons), which is what's there when no window is."""
    if sys.platform != "win32":
        return 0
    return int(ctypes.windll.user32.GetShellWindow() or 0)


def window_to_read(
    follow_mouse: bool, named: int | None
) -> tuple[int, tuple[int, int, int, int] | None]:
    """Which window a look at the screen reads, and (when it is the bare desktop) the screen
    it is read on. The request's own window; else the one under the mouse; else, with the
    mouse over no window (the desktop, the taskbar), the window in front when it is on the
    mouse's screen, else the top window on that screen, or its desktop. Never the window in
    front when it is on another screen: live, with three screens, the model was given the
    buttons of a window on the second screen under a picture of the first. 0 when the window
    in front is the one to read."""
    if named:
        return named, None
    pos = cursor_pos() if follow_mouse else None
    if not pos:
        return 0, None
    hwnd = window_at(*pos)
    if hwnd:
        return hwnd, None
    screen = screen_at(*pos)
    if screen is None:
        return 0, None
    # The one they are working in comes before whatever floats on top of it: a small
    # always-on-top window (a video in a corner, a sticky note) is first in the pile, and
    # with the mouse on the taskbar the model got its title and buttons under a picture of Word.
    front = _front_window()
    sx, sy, sw, sh = screen
    for real, (left, top, right, bottom) in _real_windows():
        cx, cy = (left + right) // 2, (top + bottom) // 2
        if real == front and sx <= cx < sx + sw and sy <= cy < sy + sh:
            return front, None
    hwnd = top_window_on(screen)
    if hwnd:
        return hwnd, None
    return desktop_window(), screen


def input_idle_s() -> float | None:
    """Seconds since the user last touched the mouse or keyboard (Windows' own clock: when,
    never what)."""
    if sys.platform != "win32":
        return None

    class LastInput(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

    info = LastInput()
    info.cbSize = ctypes.sizeof(LastInput)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return None
    now = ctypes.windll.kernel32.GetTickCount()
    return ((now - info.dwTime) & 0xFFFFFFFF) / 1000.0


def _rect_of(hwnd: int) -> tuple[int, int, int, int]:
    r = ctypes.wintypes.RECT()
    if not ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r)):
        return 0, 0, 0, 0
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def _pid_of(hwnd: int) -> int:
    pid = ctypes.wintypes.DWORD()
    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def _front_window() -> int:
    user32 = ctypes.windll.user32
    fg = int(user32.GetForegroundWindow() or 0)
    return int(user32.GetAncestor(fg, 2) or 0) or fg if fg else 0  # GA_ROOT


def unread_window(follow_mouse: bool = True, named: int | None = None) -> RawWindow | None:
    """The window a look was for, when its tree could not be read: its title, program and
    place, asked of Windows directly. Without it the model was told "window: ''" and took
    that for an empty desktop, and nothing drawn could be tied to a window. Never raises."""
    if sys.platform != "win32":
        return None
    try:
        hwnd, screen = window_to_read(follow_mouse, named)
        hwnd = hwnd or _front_window()
        if not hwnd:
            return None
        return RawWindow(
            title=window_title(hwnd) or "",
            process=process_name(_pid_of(hwnd)),
            rect=screen or _rect_of(hwnd),
            hwnd=hwnd,
            tree="failed",
        )
    except Exception as exc:
        log.debug("couldn't name the window either: %s", exc)
        return None


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

    failure: Exception | None = None
    for attempt in range(2):
        try:
            with auto.UIAutomationInitializerInThread(debug=False):
                return _walk(auto, budget_s, max_elements, max_depth, follow_mouse, hwnd)
        except Exception as exc:  # UIA is best-effort: some apps throw COM errors
            failure = exc
            if attempt == 0:
                time.sleep(0.15)  # often an app that is just opening: a moment later it answers
    log.warning("UI Automation failed: %s", failure)
    return unread_window(follow_mouse, hwnd), []


# UI Automation property ids (UIAutomationClient.h), and "this control and everything under it".
_P_RECT, _P_TYPE, _P_NAME, _P_AUTOMATION_ID, _P_PASSWORD, _P_OFFSCREEN = 30001, 30003, 30005, 30011, 30019, 30022
_WHOLE_TREE = 1 | 2 | 4  # TreeScope_Element | Children | Descendants
# A window whose one-call read did not come back in time is walked the old way for a while.
_ONE_CALL_OFF_S = 60.0
_one_call_off: dict[int, float] = {}


def _keep(role: str, offscreen: bool, w: int, h: int, name: str) -> bool:
    """Is this control worth showing the model? The same rule for both ways of reading."""
    if role not in INTERACTIVE and role not in STRUCTURAL and role not in LABELS:
        return False
    if offscreen or w < 3 or h < 3:
        return False
    if role in LABELS and not name:
        return False
    return not (role in STRUCTURAL and not name and w * h < 2_000)


def _cached_elements(auto, hwnd: int, max_elements: int, max_depth: int) -> list[RawElement]:
    """Every control of a window from ONE call across to its program.

    The walk below asks the program a separate question for each property of each control
    (its kind, its place, its name, ...) and for each step to the next control: thousands of
    round trips for a web page. Measured on a browser window: 8 s, where the app's budget is
    1.5 s, so Pyxi saw the top of the page and nothing else. Asked once for the whole tree
    with those properties attached, the same window came back in 0.3 s. Reading the answer
    costs nothing: it is all on this side already.
    """
    ui = auto.uiautomation._AutomationClient.instance().IUIAutomation
    request = ui.CreateCacheRequest()
    for prop in (_P_RECT, _P_TYPE, _P_NAME, _P_AUTOMATION_ID, _P_PASSWORD, _P_OFFSCREEN):
        request.AddProperty(prop)
    request.TreeScope = _WHOLE_TREE
    # The raw view, as the walk below uses: a browser page marks many of its groups and texts
    # as "not controls", and the control view drops them (and shifts every depth).
    request.TreeFilter = ui.RawViewCondition
    root = ui.ElementFromHandle(hwnd).BuildUpdatedCache(request)
    names = auto.ControlTypeNames
    out: list[RawElement] = []
    kept_above: list[tuple[int, int | None]] = []  # depth -> index of the last kept ancestor

    def children(node, depth: int) -> list[tuple[Any, int]]:
        try:
            kids = node.GetCachedChildren()
        except Exception:
            return []
        return [(kids.GetElement(i), depth) for i in reversed(range(kids.Length if kids else 0))]

    todo = children(root, 1)
    while todo and len(out) < max_elements:
        node, depth = todo.pop()
        if depth < max_depth:
            todo += children(node, depth + 1)
        try:
            role = names.get(node.CachedControlType, "")
            rect = node.CachedBoundingRectangle
            w, h = rect.right - rect.left, rect.bottom - rect.top
            name = (node.CachedName or "").strip()
            if not _keep(role, bool(node.CachedIsOffscreen), w, h, name):
                continue
        except Exception:
            continue
        while kept_above and kept_above[-1][0] >= depth:
            kept_above.pop()
        is_password, automation_id = False, ""
        try:
            is_password = bool(node.GetCachedPropertyValue(_P_PASSWORD))
        except Exception:
            pass
        try:
            automation_id = (node.CachedAutomationId or "")[:80]
        except Exception:
            pass
        out.append(
            RawElement(
                name=name[:160],
                role=role.removesuffix("Control"),
                rect=(rect.left, rect.top, w, h),
                depth=depth,
                parent=kept_above[-1][1] if kept_above else None,
                is_password=is_password,
                automation_id=automation_id,
            )
        )
        kept_above.append((depth, len(out) - 1))
    return out


def read_in_one_call(auto, hwnd: int, budget_s: float, max_elements: int, max_depth: int) -> list[RawElement] | None:
    """The window's controls from one call, or None to walk it the old way: the call failed,
    found nothing, or did not come back within the budget (it cannot be stopped part-way, so
    it runs on a thread of its own and that window is left to the walk for a minute)."""
    if not hwnd or _one_call_off.get(hwnd, 0.0) > time.monotonic():
        return None
    got: dict[str, Any] = {}

    def work() -> None:
        try:
            with auto.UIAutomationInitializerInThread(debug=False):
                got["out"] = _cached_elements(auto, hwnd, max_elements, max_depth)
        except Exception as exc:  # a program that will not answer this way still gets the walk
            got["failed"] = exc

    worker = threading.Thread(target=work, daemon=True, name="pyxi-uia-read")
    started = time.monotonic()
    worker.start()
    worker.join(budget_s)
    if got.get("out"):
        log.debug("read %d controls in one call, %.2f s", len(got["out"]), time.monotonic() - started)
        return got["out"]
    if worker.is_alive():
        _one_call_off[hwnd] = time.monotonic() + _ONE_CALL_OFF_S
        log.info("reading a window in one call took over %.1f s: walking it instead", budget_s)
    elif "failed" in got:
        log.info("reading a window in one call failed (%s): walking it instead", got["failed"])
    return None


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
    hwnd, screen = window_to_read(follow_mouse, named)
    if hwnd:
        try:
            top = auto.ControlFromHandle(hwnd)
        except Exception:
            top = None
    if top is None:
        screen = None
        # The foreground window's top-level ancestor, asked of Windows directly: uiautomation's
        # own GetTopLevelControl passes a c_long where an int belongs and throws ("argument 2:
        # TypeError"), which left Pyxi blind in the Start menu and in popups.
        user32 = ctypes.windll.user32
        fg_hwnd = int(user32.GetForegroundWindow() or 0)
        root = int(user32.GetAncestor(fg_hwnd, 2) or 0) if fg_hwnd else 0  # GA_ROOT
        try:
            top = auto.ControlFromHandle(root or fg_hwnd) if (root or fg_hwnd) else None
        except Exception:
            top = None
        if top is None:
            top = auto.GetForegroundControl()
        if top is None:
            return None, []
    r = top.BoundingRectangle
    window = RawWindow(
        title=top.Name or "",
        process=process_name(top.ProcessId),
        # The bare desktop is one window across every screen: here it is the mouse's screen.
        rect=screen or (r.left, r.top, r.width(), r.height()),
        hwnd=top.NativeWindowHandle,
    )

    whole = read_in_one_call(auto, int(window.hwnd or hwnd or 0), budget_s, max_elements, max_depth)
    if whole is not None:
        return window, whole
    deadline = time.monotonic() + budget_s
    out: list[RawElement] = []
    stack: list[tuple[int, int | None]] = []  # depth -> index of last kept ancestor
    walker = iter(auto.WalkControl(top, includeTop=False, maxDepth=max_depth))
    while True:
        try:
            control, depth = next(walker)
        except StopIteration:
            break
        except Exception as exc:
            # The tree itself gave out part-way (live: "An event was unable to invoke any of
            # the subscribers"). What was read so far is kept, and the window is still known:
            # throwing it all away left the model with "window: ''" and nothing to point at.
            log.warning("UI Automation stopped part-way through %r: %s", window.title, exc)
            window.tree = "partial" if out else "failed"
            break
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
            if not _keep(role, False, w, h, name):
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
        # Guarded like every other property: read bare, one control that threw here took the
        # whole walk with it, and the second try (read_foreground) walked it all again for
        # the same end, twice as slow with nothing kept.
        automation_id = ""
        try:
            automation_id = (control.AutomationId or "")[:80]
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
                automation_id=automation_id,
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


_ADDRESS_BARS = ("enter address", "address and search bar", "address bar", "search or enter")


def browser_url(hwnd: int, budget_s: float = 0.5) -> str | None:
    """The full address in a browser window's address bar (Firefox, Chrome, Edge, Brave), read
    from the bar itself: the screen shows it cut short, and OCR garbles a long id. Never raises."""
    if not available():
        return None
    try:
        import uiautomation as auto

        deadline = time.monotonic() + budget_s
        with auto.UIAutomationInitializerInThread(debug=False):
            top = auto.ControlFromHandle(hwnd)
            if top is None:
                return None
            for control, _depth in auto.WalkControl(top, maxDepth=12):
                if time.monotonic() > deadline:
                    return None
                if control.ControlTypeName not in ("EditControl", "ComboBoxControl"):
                    continue
                if not any(k in (control.Name or "").lower() for k in _ADDRESS_BARS):
                    continue
                for c in (control, *control.GetChildren()):
                    try:
                        value = c.GetValuePattern().Value
                    except Exception:
                        continue
                    if value and "." in value and " " not in value.strip():
                        v = value.strip()
                        return v if "://" in v else "https://" + v
            return None
    except Exception as exc:
        log.debug("address bar read failed: %s", exc)
        return None
