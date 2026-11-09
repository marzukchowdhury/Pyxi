"""Small helpers over the Accessibility API (AXUIElement) and the Quartz window list.

pyobjc returns ``(error, value)`` from the AX copy calls; these turn that into "the value, or
None", so the callers read like the UI Automation code on Windows.
"""

from __future__ import annotations

import logging
from typing import Any

from pyxi.macos import appkit, axapi, quartz

log = logging.getLogger(__name__)

# The window list's keys (CoreGraphics defines them as these very strings).
NUMBER, PID, OWNER, NAME = "kCGWindowNumber", "kCGWindowOwnerPID", "kCGWindowOwnerName", "kCGWindowName"
BOUNDS, LAYER, ONSCREEN, ALPHA = "kCGWindowBounds", "kCGWindowLayer", "kCGWindowIsOnscreen", "kCGWindowAlpha"
# System processes whose windows are never "an app the user is in".
SYSTEM_OWNERS = {"Window Server", "Dock", "WindowManager", "Control Center", "Notification Center"}


def trusted() -> bool:
    """Has the user allowed Pyxi under Privacy & Security > Accessibility? (Never asks.)"""
    try:
        return bool(axapi().AXIsProcessTrusted())
    except Exception:
        return False


# ---------------------------------------------------------------- the window list (Quartz)
def cg_windows(all_windows: bool = False) -> list[dict]:
    """Every window, frontmost first. On screen only, unless ``all_windows`` (then minimized
    windows and windows on other Spaces too)."""
    Q = quartz()
    opts = Q.kCGWindowListOptionAll if all_windows else Q.kCGWindowListOptionOnScreenOnly
    try:
        found = Q.CGWindowListCopyWindowInfo(opts | Q.kCGWindowListExcludeDesktopElements, Q.kCGNullWindowID)
    except Exception as exc:
        log.debug("window list failed: %s", exc)
        return []
    return [dict(w) for w in (found or [])]


def cg_info(wid: int) -> dict | None:
    """One window's entry in the window list (None once it's gone)."""
    if not wid:
        return None
    Q = quartz()
    try:
        found = Q.CGWindowListCopyWindowInfo(Q.kCGWindowListOptionIncludingWindow, int(wid))
    except Exception:
        return None
    for w in found or []:
        if int(w.get(NUMBER, 0)) == int(wid):
            return dict(w)
    return None


def bounds(info: dict) -> tuple[int, int, int, int]:
    b = info.get(BOUNDS) or {}
    return int(b.get("X", 0)), int(b.get("Y", 0)), int(b.get("Width", 0)), int(b.get("Height", 0))


def is_app_window(info: dict, min_size: int = 50) -> bool:
    """A normal app window: the regular layer, not see-through, not the Dock or the menu bar,
    and bigger than a tooltip."""
    if int(info.get(LAYER, 0)) != 0 or float(info.get(ALPHA, 1.0)) <= 0:
        return False
    if info.get(OWNER, "") in SYSTEM_OWNERS:
        return False
    _x, _y, w, h = bounds(info)
    return w >= min_size and h >= min_size


def contains(r: tuple[int, int, int, int], x: float, y: float) -> bool:
    return r[0] <= x < r[0] + r[2] and r[1] <= y < r[1] + r[3]


# ---------------------------------------------------------------- running apps (AppKit)
_exe_cache: dict[int, str] = {}


def running_app(pid: int) -> Any:
    try:
        return appkit().NSRunningApplication.runningApplicationWithProcessIdentifier_(int(pid))
    except Exception:
        return None


def exe_of_pid(pid: int) -> str:
    """The app's executable ('/Applications/Safari.app/Contents/MacOS/Safari'), '' if unknown."""
    if not pid:
        return ""
    if pid in _exe_cache:
        return _exe_cache[pid]
    app = running_app(pid)
    path = ""
    try:
        url = app.executableURL() if app is not None else None
        path = str(url.path()) if url is not None else ""
    except Exception:
        path = ""
    if path:
        _exe_cache[pid] = path
    return path


def bundle_of_pid(pid: int) -> str:
    """The app bundle ('/Applications/Safari.app'), '' if unknown."""
    app = running_app(pid)
    try:
        url = app.bundleURL() if app is not None else None
        return str(url.path()) if url is not None else ""
    except Exception:
        return ""


def app_name(pid: int) -> str:
    app = running_app(pid)
    try:
        return str(app.localizedName() or "") if app is not None else ""
    except Exception:
        return ""


def frontmost_pid() -> int:
    try:
        app = appkit().NSWorkspace.sharedWorkspace().frontmostApplication()
        return int(app.processIdentifier()) if app is not None else 0
    except Exception:
        return 0


# ---------------------------------------------------------------- AXUIElement
def attr(el: Any, name: str) -> Any:
    """An element's attribute, or None (not there, or the app didn't answer)."""
    if el is None:
        return None
    try:
        res = axapi().AXUIElementCopyAttributeValue(el, name, None)
    except Exception:
        return None
    if isinstance(res, tuple):
        err, value = res[0], res[1] if len(res) > 1 else None
        return value if err == 0 else None
    return res


def set_attr(el: Any, name: str, value: Any) -> bool:
    try:
        return axapi().AXUIElementSetAttributeValue(el, name, value) == 0
    except Exception:
        return False


def perform(el: Any, action: str) -> bool:
    try:
        return axapi().AXUIElementPerformAction(el, action) == 0
    except Exception:
        return False


def _value(v: Any, kind: str) -> Any:
    """An AXValue's point or size (pyobjc hands back ``(ok, struct)``)."""
    AX = axapi()
    code = getattr(AX, f"kAXValueCG{kind}Type", None)
    if code is None:
        code = getattr(AX, f"kAXValueTypeCG{kind}", 1 if kind == "Point" else 2)
    try:
        res = AX.AXValueGetValue(v, code, None)
    except Exception:
        return None
    if isinstance(res, tuple):
        return res[1] if res[0] else None
    return res


def element_rect(el: Any) -> tuple[int, int, int, int] | None:
    """x, y, w, h in points, top-left origin (Accessibility already uses that)."""
    pos, size = attr(el, "AXPosition"), attr(el, "AXSize")
    if pos is None or size is None:
        return None
    p, s = _value(pos, "Point"), _value(size, "Size")
    if p is None or s is None:
        return None
    return int(p.x), int(p.y), int(s.width), int(s.height)


def point_value(x: float, y: float) -> Any:
    AX, Q = axapi(), quartz()
    code = getattr(AX, "kAXValueCGPointType", getattr(AX, "kAXValueTypeCGPoint", 1))
    return AX.AXValueCreate(code, Q.CGPointMake(x, y))


def size_value(w: float, h: float) -> Any:
    AX, Q = axapi(), quartz()
    code = getattr(AX, "kAXValueCGSizeType", getattr(AX, "kAXValueTypeCGSize", 2))
    return AX.AXValueCreate(code, Q.CGSizeMake(w, h))


def app_element(pid: int) -> Any:
    AX = axapi()
    el = AX.AXUIElementCreateApplication(int(pid))
    try:  # a hung app must not hang Pyxi: every AX call on it gives up after a quarter second
        AX.AXUIElementSetMessagingTimeout(el, 0.25)
    except Exception:
        pass
    return el


def ax_window(wid: int) -> Any:
    """The Accessibility element of a window-list window: same app, same frame (else same title,
    else the app's front window). The two APIs share no id, so this is how they're matched."""
    info = cg_info(wid)
    if info is None:
        return None
    pid = int(info.get(PID, 0))
    if not pid:
        return None
    wins = list(attr(app_element(pid), "AXWindows") or [])
    if not wins:
        return None
    want, title = bounds(info), str(info.get(NAME) or "")
    by_title = None
    for w in wins:
        r = element_rect(w)
        if r and all(abs(a - b) <= 4 for a, b in zip(r, want, strict=True)):
            return w
        if by_title is None and title and str(attr(w, "AXTitle") or "") == title:
            by_title = w
    return by_title or wins[0]


def pid_of_element(el: Any) -> int:
    try:
        res = axapi().AXUIElementGetPid(el, None)
    except Exception:
        return 0
    if isinstance(res, tuple):
        return int(res[1]) if res[0] == 0 else 0
    return int(res or 0)
