"""Pressing a control through Windows' accessibility layer, with no mouse.

The desktop loop (companion/operator.py) aims with element ids. A click was always the pointer
glided to the middle of the rectangle the look gave, and pressed there. That lands beside a
switch whose rectangle is mostly its words, lands on whatever is there *now* when the control
moved between the look and the click, and takes the user's mouse for every button.

Here the control is found again, live, in the window (the same kind, the same name, nearest
to where it was) and pressed by what it offers: Invoke for a button, Toggle for a checkbox or
a switch, Select for a tab, a radio button or a list row, Expand for a dropdown. These are the
calls a screen reader makes; screenlink.py has pressed things in browser tabs this way for a
while. Nothing here moves the pointer or sends a key.

:func:`press` answers one of three ways:

* pressed (``how`` says which way; ``sure`` when the control's own state confirmed it);
* found but not pressed (greyed out, nothing it offers, or the way did not take), with where
  the control is now, so a mouse click goes to the right place;
* ``None``: not found live, or not a window to press in. The caller does what it always did.

It never raises. The work runs on a thread of its own with a time limit: in older Win32 apps
a button that opens a dialog does not let the press return until the dialog is closed, and
that is a press that worked, not one to do again.
"""

from __future__ import annotations

import contextlib
import logging
import math
import sys
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

# What a single click presses, by UI Automation control type.
_TYPES = {
    "Button": 50000,
    "CheckBox": 50002,
    "ComboBox": 50003,
    "Hyperlink": 50005,
    "ListItem": 50007,
    "MenuItem": 50011,
    "RadioButton": 50013,
    "TabItem": 50019,
    "TreeItem": 50024,
    "SplitButton": 50031,
}
PRESSABLE = frozenset(_TYPES)
# Pressable with nothing but itself in it: words quoted on one of these are its own label. (A
# quote inside a list row or a dropdown may be a link or a button within it: that is clicked.)
SIMPLE = frozenset({"Button", "CheckBox", "Hyperlink", "MenuItem", "RadioButton", "TabItem", "SplitButton"})

_P_RECT, _P_TYPE, _P_NAME, _P_ENABLED, _P_OFFSCREEN, _P_SELECTED, _P_TOGGLE = (
    30001, 30003, 30005, 30010, 30022, 30079, 30086,
)  # fmt: skip
_HAS = {
    30031: "Invoke", 30041: "Toggle", 30036: "SelectionItem", 30028: "ExpandCollapse",
    30090: "LegacyIAccessible",
}  # fmt: skip
_PAT = {
    "Invoke": 10000, "Toggle": 10015, "SelectionItem": 10010, "ExpandCollapse": 10005,
    "LegacyIAccessible": 10018,
}  # fmt: skip
_NEEDS = {
    "invoke": "Invoke",
    "toggle": "Toggle",
    "select": "SelectionItem",
    "expand": "ExpandCollapse",
    "legacy": "LegacyIAccessible",
}
_DESCENDANTS = 4  # TreeScope_Descendants
NAME_CHARS = 160  # how much of a name the look keeps (screen/uia.py)


@dataclass(frozen=True)
class Target:
    """The control as the look saw it."""

    hwnd: int
    role: str
    name: str
    rect: tuple[int, int, int, int]  # x, y, w, h in screen pixels


@dataclass
class Live:
    """One control of that kind in the window, as it is now."""

    name: str
    rect: tuple[int, int, int, int]
    enabled: bool = True
    offscreen: bool = False
    patterns: frozenset[str] = frozenset()
    on: bool | None = None  # ticked, or chosen, when the control says
    handle: Any = None


@dataclass
class Result:
    how: str = ""  # the way it was pressed; empty: found, not pressed
    rect: tuple[int, int, int, int] | None = None  # where the control is now
    why: str = ""  # not pressed because: "disabled", "no way", "chosen", "did not take"
    sure: bool = False  # the control's own state says the press took (a tick, a chosen tab)
    late: bool = False  # the app had not answered when the time was up (a dialog opened)


def _name(text: str) -> str:
    return (text or "").strip()[:NAME_CHARS]


def _mid(rect: tuple[int, int, int, int]) -> tuple[float, float]:
    return rect[0] + rect[2] / 2, rect[1] + rect[3] / 2


def pick(target: Target, found: list[Live]) -> Live | None:
    """Which of the controls in the window is the one the look meant. The only one with that
    name is it, wherever it has got to (a list that scrolled, a panel that opened above it).
    With several of the same name it is the one still where the look saw it; if none is, it
    can't be told which was meant."""
    want = _name(target.name)
    if not want:
        return None
    same = [
        f for f in found
        if _name(f.name) == want and not f.offscreen and f.rect[2] >= 3 and f.rect[3] >= 3
    ]  # fmt: skip
    if not same:
        return None
    tx, ty = _mid(target.rect)

    def far(f: Live) -> float:
        x, y = _mid(f.rect)
        return math.hypot(x - tx, y - ty)

    best = min(same, key=far)
    if len(same) == 1:
        return best
    reach = max(24.0, math.hypot(target.rect[2], target.rect[3]) / 2)
    return best if far(best) <= reach else None


def ways(role: str, patterns: frozenset[str]) -> tuple[str, ...]:
    """How a click on this kind of control is done, best way first, of the ways it offers.

    A list row or a tree row is chosen, never invoked: invoking a file in File Explorer opens
    it, which is a double click. A button that only toggles (a switch) is toggled."""
    if role == "CheckBox":
        order: tuple[str, ...] = ("toggle", "legacy")
    elif role in ("RadioButton", "TabItem", "ListItem", "TreeItem"):
        order = ("select",) if "SelectionItem" in patterns else ("invoke", "legacy")
    elif role == "ComboBox":
        order = ("expand",)
    elif role == "MenuItem":
        order = ("invoke", "expand", "legacy")
    elif role == "Button" and "Toggle" in patterns and "Invoke" not in patterns:
        order = ("toggle",)
    else:
        order = ("invoke", "legacy")
    return tuple(w for w in order if _NEEDS[w] in patterns)


class _Box:
    """What the pressing thread and the one waiting for it both know."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.done = False
        self.result: Result | None = None
        self.pressing: tuple[str, tuple[int, int, int, int]] | None = None
        self.left = False  # nobody is waiting any more: press nothing from here on

    def finish(self, result: Result | None) -> None:
        with self.lock:
            self.result, self.done = result, True

    def begin(self, way: str, rect: tuple[int, int, int, int]) -> bool:
        with self.lock:
            if self.left:
                return False
            self.pressing = (way, rect)
            return True

    def refused(self) -> bool:
        """That way was not done after all. False: nobody is waiting, stop here."""
        with self.lock:
            if self.left:
                return False
            self.pressing = None
            return True

    def gone(self) -> bool:
        with self.lock:
            return self.left


def _work(target: Target, box: _Box, backend: Any) -> None:
    try:
        with backend.thread():
            live = pick(target, backend.find(target))
            if live is None:
                return box.finish(None)
            if not live.enabled:
                return box.finish(Result(rect=live.rect, why="disabled"))
            offered = ways(target.role, live.patterns)
            if not offered:
                return box.finish(Result(rect=live.rect, why="no way"))
            if offered[0] == "select" and live.on:
                # Already the chosen one. A click on it may still do something (a list that
                # opens on one click), so that is left to the mouse.
                return box.finish(Result(rect=live.rect, why="chosen"))
            for way in offered:
                if not box.begin(way, live.rect):
                    return None
                try:
                    backend.do(live, way)
                except Exception as exc:  # it doesn't offer this one after all
                    log.debug("direct press: %s by %s refused: %s", target.name, way, exc)
                    if not box.refused():
                        return None
                    continue
                if box.gone():
                    return None  # it came back late (a dialog was closed): never press again
                if way in ("toggle", "select"):
                    if _took(backend, live, way):
                        return box.finish(Result(way, live.rect, sure=True))
                    if not box.refused():
                        return None
                    continue
                return box.finish(Result(way, live.rect))
            return box.finish(Result(rect=live.rect, why="did not take"))
    except Exception as exc:
        log.debug("direct press: %s failed: %s", target.name, exc)
        box.finish(None)


def _took(backend: Any, live: Live, way: str) -> bool:
    """Did the tick flip, or is it the chosen one now? Asked for up to half a second: apps
    answer the press first and change a moment later."""
    for _ in range(5):
        try:
            now = backend.state(live, way)
        except Exception:
            now = None
        if now is not None and (now != live.on if way == "toggle" else now):
            return True
        time.sleep(0.1)
    return False


def usable(hwnd: int) -> bool:
    """Is this a window to press in: the one in front, and not one of Pyxi's own. A press
    doesn't bring a window forward the way a click does, so keys typed afterwards would go
    to another window; and Pyxi never presses its own cards."""
    if sys.platform != "win32" or not hwnd:
        return False
    try:
        import uiautomation  # noqa: F401

        from pyxi import hands

        return not hands.is_own(hwnd) and hands.foreground_window() == hwnd
    except Exception:
        return False


def press(target: Target, timeout_s: float = 2.5, backend: Any = None) -> Result | None:
    """Press ``target`` without the mouse. See the module's notes for what comes back."""
    if target.role not in PRESSABLE or not _name(target.name):
        return None
    if backend is None:
        if not usable(target.hwnd):
            return None
        backend = Uia()
    box = _Box()
    worker = threading.Thread(target=_work, args=(target, box, backend), daemon=True, name="pyxi-press")
    worker.start()
    worker.join(timeout_s)
    with box.lock:
        if box.done:
            return box.result
        box.left = True
        if box.pressing is not None:
            way, rect = box.pressing
            log.info("direct press: %r by %s has not come back; taken as pressed", target.name, way)
            return Result(way, rect, late=True)
    log.info("direct press: %r was not found in time", target.name)
    return None


class Uia:
    """The real thing: UI Automation, on the pressing thread."""

    @contextlib.contextmanager
    def thread(self) -> Iterator[None]:
        from uiautomation import uiautomation as u

        with u.UIAutomationInitializerInThread(debug=False):
            yield

    def find(self, target: Target) -> list[Live]:
        from uiautomation import uiautomation as u

        ui = u._AutomationClient.instance().IUIAutomation
        cache = ui.CreateCacheRequest()
        for prop in (_P_RECT, _P_NAME, _P_ENABLED, _P_OFFSCREEN, _P_SELECTED, _P_TOGGLE, *_HAS):
            cache.AddProperty(prop)
        top = ui.ElementFromHandle(target.hwnd)
        found = top.FindAllBuildCache(
            _DESCENDANTS, ui.CreatePropertyCondition(_P_TYPE, _TYPES[target.role]), cache
        )
        out: list[Live] = []
        for i in range(found.Length if found else 0):
            raw = found.GetElement(i)

            def get(prop: int, raw: Any = raw) -> Any:
                try:
                    return raw.GetCachedPropertyValue(prop)
                except Exception:
                    return None

            try:
                left, top_, width, height = (int(v) for v in (get(_P_RECT) or (0, 0, 0, 0))[:4])
            except (TypeError, ValueError):
                continue
            patterns = frozenset(name for prop, name in _HAS.items() if get(prop))
            on: bool | None = None
            if "Toggle" in patterns and (state := get(_P_TOGGLE)) is not None:
                on = state == 1
            elif "SelectionItem" in patterns:
                on = bool(get(_P_SELECTED))
            out.append(
                Live(
                    name=str(get(_P_NAME) or ""),
                    rect=(left, top_, width, height),
                    enabled=get(_P_ENABLED) is not False,
                    offscreen=bool(get(_P_OFFSCREEN)),
                    patterns=patterns,
                    on=on,
                    handle=raw,
                )
            )
        return out

    def _pattern(self, live: Live, name: str) -> Any:
        from uiautomation import uiautomation as u

        raw = live.handle.GetCurrentPattern(_PAT[name])
        if not raw:
            raise LookupError(name)
        return u.CreatePattern(_PAT[name], raw)

    def do(self, live: Live, way: str) -> None:
        if way == "invoke":
            self._pattern(live, "Invoke").Invoke(waitTime=0)
        elif way == "toggle":
            self._pattern(live, "Toggle").Toggle(waitTime=0)
        elif way == "select":
            self._pattern(live, "SelectionItem").Select(waitTime=0)
        elif way == "expand":
            self._pattern(live, "ExpandCollapse").Expand(waitTime=0)
        elif way == "legacy":
            self._pattern(live, "LegacyIAccessible").DoDefaultAction(waitTime=0)
        else:
            raise LookupError(way)

    def state(self, live: Live, way: str) -> bool | None:
        value = live.handle.GetCurrentPropertyValue(_P_TOGGLE if way == "toggle" else _P_SELECTED)
        if value is None:
            return None
        return value == 1 if way == "toggle" else bool(value)
