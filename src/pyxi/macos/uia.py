"""The Mac side of ``pyxi.screen.uia``: the Accessibility tree of the window the user is in.

Same job as UI Automation on Windows: every button, field, tab and menu item with its name, role
and exact rectangle. Roles are reported with the Windows names ("Button", "Edit", "TabItem"), so
everything downstream (ranking, the operator's prompts, password masking) works unchanged.

Needs the Accessibility permission. Without it every read returns nothing (never raises) and
Pyxi falls back to the screenshot alone, as it does on any other platform.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import time
from typing import Any

from pyxi.macos import _ax, axapi, quartz
from pyxi.screen.uia import RawElement, RawFocus, RawWindow

log = logging.getLogger(__name__)

__all__ = [
    "available",
    "browser_url",
    "cursor_pos",
    "focused_field",
    "process_name",
    "read_focus",
    "read_foreground",
    "read_taskbar",
    "window_at",
    "window_title",
]

# Accessibility roles -> the UI Automation control type names Pyxi already speaks.
ROLES = {
    "AXButton": "Button",
    "AXMenuButton": "Button",
    "AXDisclosureTriangle": "Button",
    "AXCheckBox": "CheckBox",
    "AXPopUpButton": "ComboBox",
    "AXComboBox": "ComboBox",
    "AXTextField": "Edit",
    "AXTextArea": "Edit",
    "AXSearchField": "Edit",
    "AXLink": "Hyperlink",
    "AXRow": "ListItem",
    "AXCell": "DataItem",
    "AXMenuItem": "MenuItem",
    "AXMenuBarItem": "MenuItem",
    "AXRadioButton": "RadioButton",
    "AXSlider": "Slider",
    "AXIncrementor": "Spinner",
    "AXWebArea": "Document",
    "AXGroup": "Group",
    "AXScrollArea": "Pane",
    "AXSplitGroup": "Pane",
    "AXLayoutArea": "Pane",
    "AXToolbar": "ToolBar",
    "AXTabGroup": "Tab",
    "AXList": "List",
    "AXOutline": "Tree",
    "AXMenuBar": "MenuBar",
    "AXTable": "Table",
    "AXBrowser": "Table",
    "AXStaticText": "Text",
    "AXImage": "Image",
    "AXHeading": "HeaderItem",
}
_INTERACTIVE_ROLES = {
    "Button", "CheckBox", "ComboBox", "Edit", "Hyperlink", "ListItem", "MenuItem", "RadioButton",
    "Slider", "Spinner", "TabItem", "TreeItem", "DataItem", "Document",
}  # fmt: skip
_LABELS = {"Text", "Image", "HeaderItem"}
_ADDRESS_BARS = ("address", "search or enter", "smart search field", "url")


def available() -> bool:
    return sys.platform == "darwin"


def cursor_pos() -> tuple[int, int] | None:
    """Where the mouse is, in points from the main screen's top left."""
    try:
        Q = quartz()
        p = Q.CGEventGetLocation(Q.CGEventCreate(None))
        return int(p.x), int(p.y)
    except Exception:
        return None


def window_title(hwnd: int) -> str | None:
    info = _ax.cg_info(hwnd)
    if info is None:
        return None
    return str(info.get(_ax.NAME) or info.get(_ax.OWNER) or "")


def _own_pids() -> set[int]:
    from pyxi.hands import own_pids

    return own_pids()


def window_at(x: int, y: int) -> int | None:
    """The frontmost app window whose frame holds the point. Pyxi's overlays sit on a higher
    window level than apps, so they never count; neither do Pyxi's own panel and cards."""
    own = _own_pids()
    for info in _ax.cg_windows():
        if not _ax.is_app_window(info, min_size=1):
            continue
        if int(info.get(_ax.PID, 0)) in own or str(info.get(_ax.OWNER, "")) == "Pyxi":
            continue
        if _ax.contains(_ax.bounds(info), x, y):
            return int(info[_ax.NUMBER])
    return None


def process_name(pid: int) -> str:
    """The app's name ("Safari", "Google Chrome"): what Windows' 'chrome.exe' is for."""
    name = _ax.app_name(pid)
    if name:
        return name
    exe = _ax.exe_of_pid(pid)
    return exe.rsplit("/", 1)[-1] if exe else ""


def _role(el: Any, parent_role: str) -> str:
    role = str(_ax.attr(el, "AXRole") or "")
    if role == "AXRadioButton" and parent_role == "AXTabGroup":
        return "TabItem"
    if role == "AXRow" and parent_role == "AXOutline":
        return "TreeItem"
    return ROLES.get(role, "")


def _name(el: Any, role: str) -> str:
    for key in ("AXTitle", "AXDescription", "AXLabel"):
        v = _ax.attr(el, key)
        if v:
            return str(v).strip()
    if role in ("Text", "HeaderItem"):
        v = _ax.attr(el, "AXValue")
        if v:
            return str(v).strip()
    v = _ax.attr(el, "AXHelp")
    return str(v).strip() if v else ""


def _is_password(el: Any) -> bool:
    return str(_ax.attr(el, "AXSubrole") or "") == "AXSecureTextField"


def _target(follow_mouse: bool, hwnd: int | None) -> int:
    if hwnd:
        return int(hwnd)
    pos = cursor_pos() if follow_mouse else None
    found = window_at(*pos) if pos else None
    if found:
        return found
    from pyxi.macos.windows import foreground

    return foreground()


def read_foreground(
    *,
    budget_s: float = 1.5,
    max_elements: int = 400,
    max_depth: int = 25,
    follow_mouse: bool = True,
    hwnd: int | None = None,
) -> tuple[RawWindow | None, list[RawElement]]:
    """Walk the Accessibility tree of the window under the mouse (else the front window, or
    ``hwnd``) within a time budget. Never raises."""
    if not available():
        return None, []
    try:
        if not _ax.trusted():
            log.info("Accessibility isn't allowed for Pyxi yet: reading the screen by its pixels")
            return None, []
        return _walk(_target(follow_mouse, hwnd), budget_s, max_elements, max_depth)
    except Exception as exc:
        log.warning("Accessibility read failed: %s", exc)
        return None, []


def _walk(
    wid: int, budget_s: float, max_elements: int, max_depth: int
) -> tuple[RawWindow | None, list[RawElement]]:
    info = _ax.cg_info(wid)
    if info is None:
        return None, []
    pid = int(info.get(_ax.PID, 0))
    top = _ax.ax_window(wid)
    wrect = _ax.bounds(info)
    window = RawWindow(
        title=str(info.get(_ax.NAME) or _ax.attr(top, "AXTitle") or ""),
        process=process_name(pid),
        rect=wrect,
        hwnd=int(wid),
    )
    if top is None:
        return window, []
    deadline = time.monotonic() + budget_s
    out: list[RawElement] = []
    # Depth-first, children in order, like UI Automation's WalkControl.
    todo: list[tuple[Any, int, int | None, str]] = [
        (c, 1, None, "AXWindow") for c in reversed(list(_ax.attr(top, "AXChildren") or []))
    ]
    while todo:
        if time.monotonic() > deadline or len(out) >= max_elements:
            break
        el, depth, parent, parent_role = todo.pop()
        raw_role = str(_ax.attr(el, "AXRole") or "")
        role = _role(el, parent_role)
        kept = parent
        rect = _ax.element_rect(el) if role else None
        if role and rect and rect[2] >= 3 and rect[3] >= 3 and _overlaps(rect, wrect):
            name = _name(el, role)
            keep = not (role in _LABELS and not name)
            keep = keep and not (role not in _INTERACTIVE_ROLES and role not in _LABELS and not name and rect[2] * rect[3] < 2_000)
            if keep:
                out.append(
                    RawElement(
                        name=name[:160],
                        role=role,
                        rect=rect,
                        depth=depth,
                        parent=parent,
                        is_password=_is_password(el),
                        automation_id=str(_ax.attr(el, "AXIdentifier") or "")[:80],
                    )
                )
                kept = len(out) - 1
        if depth < max_depth:
            kids = list(_ax.attr(el, "AXChildren") or [])
            todo.extend((c, depth + 1, kept, raw_role) for c in reversed(kids))
    return window, out


def _overlaps(r: tuple[int, int, int, int], w: tuple[int, int, int, int]) -> bool:
    """On screen within the window (scrolled-away rows report rects far outside it)."""
    return r[0] < w[0] + w[2] and r[0] + r[2] > w[0] and r[1] < w[1] + w[3] and r[1] + r[3] > w[1]


def read_taskbar(budget_s: float = 0.4) -> list[RawElement]:
    """The Dock's icons (apps, folders, the Trash), named as the Dock names them. The Mac's
    taskbar, so "click Spotify in the Dock" has something to aim at. Never raises."""
    if not available() or not _ax.trusted():
        return []
    try:
        dock = next(
            (int(i.get(_ax.PID, 0)) for i in _ax.cg_windows() if i.get(_ax.OWNER) == "Dock"),
            0,
        )
        if not dock:
            return []
        deadline = time.monotonic() + budget_s
        out: list[RawElement] = []
        for lst in _ax.attr(_ax.app_element(dock), "AXChildren") or []:
            for item in _ax.attr(lst, "AXChildren") or []:
                if time.monotonic() > deadline:
                    return out
                name = str(_ax.attr(item, "AXTitle") or "").strip()
                r = _ax.element_rect(item)
                if name and r and r[2] >= 6 and r[3] >= 6:
                    out.append(RawElement(name[:80], "TaskbarButton", r, 1, None, False, ""))
        return out
    except Exception as exc:
        log.debug("Dock read failed: %s", exc)
        return []


def _focused() -> Any:
    AX = axapi()
    return _ax.attr(AX.AXUIElementCreateSystemWide(), "AXFocusedUIElement")


def read_focus() -> RawFocus | None:
    """The element with keyboard focus right now (cheap enough to poll)."""
    if not available() or not _ax.trusted():
        return None
    try:
        el = _focused()
        if el is None:
            return None
        pid = _ax.pid_of_element(el)
        win = _ax.attr(el, "AXWindow")
        role = ROLES.get(str(_ax.attr(el, "AXRole") or ""), "Custom")
        password = _is_password(el)
        return RawFocus(
            window_title=str(_ax.attr(win, "AXTitle") or ""),
            process=process_name(pid),
            name="(password field)" if password else _name(el, role)[:160],
            role=role,
            rect=_ax.element_rect(el) or (0, 0, 0, 0),
        )
    except Exception as exc:
        log.debug("focus read failed: %s", exc)
        return None


def focused_field() -> dict | None:
    """The focused element with its text (for a field), for Ditto. Password fields: no text."""
    if not available() or not _ax.trusted():
        return None
    try:
        el = _focused()
        if el is None:
            return None
        pid = _ax.pid_of_element(el)
        win = _ax.attr(el, "AXWindow")
        role = ROLES.get(str(_ax.attr(el, "AXRole") or ""), "Custom")
        password = _is_password(el)
        value = "" if password else str(_ax.attr(el, "AXValue") or "")[:300]
        return {
            "process": process_name(pid),
            "title": str(_ax.attr(win, "AXTitle") or ""),
            "role": role,
            "name": _name(el, role)[:160],
            "password": password,
            "value": value if role in ("Edit", "ComboBox", "Document") else "",
            "rect": _ax.element_rect(el) or (0, 0, 0, 0),
        }
    except Exception as exc:
        log.debug("focused field read failed: %s", exc)
        return None


_SCRIPTED = {  # browsers that answer "what's your front tab's address?" over AppleScript
    "Safari": 'tell application "Safari" to get URL of front document',
    "Google Chrome": 'tell application "Google Chrome" to get URL of active tab of front window',
    "Microsoft Edge": 'tell application "Microsoft Edge" to get URL of active tab of front window',
    "Brave Browser": 'tell application "Brave Browser" to get URL of active tab of front window',
    "Arc": 'tell application "Arc" to get URL of active tab of front window',
}


def browser_url(hwnd: int, budget_s: float = 0.5) -> str | None:
    """The full address in a browser window: from its address field (Accessibility), else by
    asking the browser (AppleScript: macOS asks the user once whether Pyxi may). Never raises."""
    if not available():
        return None
    try:
        info = _ax.cg_info(hwnd)
        if info is None:
            return None
        if _ax.trusted():
            found = _address_field(_ax.ax_window(hwnd), time.monotonic() + budget_s)
            if found:
                return found
        script = _SCRIPTED.get(str(info.get(_ax.OWNER) or ""))
        if not script:
            return None
        out = subprocess.run(
            ["osascript", "-e", script], capture_output=True, text=True, timeout=2
        ).stdout.strip()
        if out and "." in out and " " not in out:
            return out if "://" in out else "https://" + out
        return None
    except Exception as exc:
        log.debug("address read failed: %s", exc)
        return None


def _address_field(top: Any, deadline: float) -> str | None:
    if top is None:
        return None
    todo = [(top, 0)]
    while todo and time.monotonic() < deadline:
        el, depth = todo.pop()
        role = str(_ax.attr(el, "AXRole") or "")
        if role in ("AXTextField", "AXComboBox"):
            label = " ".join(
                str(_ax.attr(el, k) or "") for k in ("AXDescription", "AXTitle", "AXIdentifier")
            ).lower()
            if any(k in label for k in _ADDRESS_BARS):
                v = str(_ax.attr(el, "AXValue") or "").strip()
                if v and "." in v and " " not in v:
                    return v if "://" in v else "https://" + v
        if depth < 12 and role != "AXWebArea":  # the page itself never holds the address bar
            todo.extend((c, depth + 1) for c in _ax.attr(el, "AXChildren") or [])
    return None
