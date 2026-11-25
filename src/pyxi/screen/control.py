"""Doing a thing to a control by what it is, in one step, with no mouse and no keys.

The desktop loop (companion/operator.py) could click, type and press keys. So putting text in
a field was a click, a select-all and typing; choosing a row far down a list was scrolling and
looking, again and again; a menu was a click, a look, a click, a look. Each look costs the
user about five seconds of the model answering.

Windows' accessibility layer offers the thing itself: a field's value can be set and read
back, a row can be found by its name however far down the list it is and brought into view, a
switch can be asked whether it is on, a menu can be walked by its words. This module does
those, on the controls of one window and of the menus and dialogs that belong to it:

* :meth:`Controls.where`      find a control by its name (waiting a moment for it to appear)
* :meth:`Controls.click`      press it (as screen/press.py does, but found by name, in a menu too)
* :meth:`Controls.set_value`  put text in a field and read back what it now says
* :meth:`Controls.toggle`     turn a switch or a tick on or off, only if it is not already
* :meth:`Controls.expand`     open or close a dropdown, a tree row, a section
* :meth:`Controls.select`     choose a row or an option by its name, out of view or not
* :meth:`Controls.scroll_to`  bring a named row into view
* :meth:`Controls.menu`       walk a menu by its words ("File > Save as")
* :meth:`Controls.find`       which controls have these words, in view or not
* :meth:`Controls.read`       what a control says now
* :meth:`Controls.wait_for`   wait until a named control is there (or gone)

Nothing here moves the pointer or sends a key, and nothing here decides whether a thing may
be done: the caller asks the user first, as it does for a click. What this module will not do
on its own account: act in a window that is not the one in front (or a menu or dialog of it),
act in one of Pyxi's own windows or in a private app, or read or write a password field.

Every call answers with a :class:`Done` and never raises. The work runs on a thread of its
own with a time limit: a menu item that opens a dialog does not come back, in older apps,
until the dialog is closed, and that is a thing done, not one to do again.
"""

from __future__ import annotations

import contextlib
import ctypes
import logging
import math
import re
import sys
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

from pyxi.screen import press as presses
from pyxi.screen import secrets

log = logging.getLogger(__name__)

_ROLE_IDS = {
    "Button": 50000, "Calendar": 50001, "CheckBox": 50002, "ComboBox": 50003, "Edit": 50004,
    "Hyperlink": 50005, "Image": 50006, "ListItem": 50007, "List": 50008, "Menu": 50009,
    "MenuBar": 50010, "MenuItem": 50011, "ProgressBar": 50012, "RadioButton": 50013,
    "ScrollBar": 50014, "Slider": 50015, "Spinner": 50016, "StatusBar": 50017, "Tab": 50018,
    "TabItem": 50019, "Text": 50020, "ToolBar": 50021, "ToolTip": 50022, "Tree": 50023,
    "TreeItem": 50024, "Custom": 50025, "Group": 50026, "Thumb": 50027, "DataGrid": 50028,
    "DataItem": 50029, "Document": 50030, "SplitButton": 50031, "Window": 50032, "Pane": 50033,
    "Header": 50034, "HeaderItem": 50035, "Table": 50036, "TitleBar": 50037, "Separator": 50038,
}  # fmt: skip
_ROLES = {v: k for k, v in _ROLE_IDS.items()}

(_P_RECT, _P_TYPE, _P_NAME, _P_ENABLED, _P_AUTOMATION_ID, _P_PASSWORD, _P_OFFSCREEN) = (
    30001, 30003, 30005, 30010, 30011, 30019, 30022,
)  # fmt: skip
_P_VALUE, _P_READONLY, _P_EXPANDED, _P_SELECTED, _P_TOGGLE = 30045, 30046, 30070, 30079, 30086
_P_FOCUSED = 30008
_HAS = {
    30031: "Invoke", 30041: "Toggle", 30036: "SelectionItem", 30028: "ExpandCollapse",
    30090: "LegacyIAccessible", 30043: "Value", 30035: "ScrollItem", 30108: "ItemContainer",
    30109: "VirtualizedItem", 30040: "Text",
}  # fmt: skip
_PAT = {
    "Invoke": 10000, "Value": 10002, "ExpandCollapse": 10005, "SelectionItem": 10010,
    "Text": 10014, "Toggle": 10015, "ScrollItem": 10017, "LegacyIAccessible": 10018,
    "ItemContainer": 10019, "VirtualizedItem": 10020,
}  # fmt: skip
_DESCENDANTS = 4

# What counts as a row or an option: the things one is chosen of.
ITEMS = frozenset({"ListItem", "TreeItem", "DataItem", "TabItem", "RadioButton", "MenuItem"})
FIELDS = frozenset({"Edit", "ComboBox", "Document", "Spinner"})
MENUS = frozenset({"MenuItem", "Button", "SplitButton", "TabItem", "ListItem"})
VALUE_CHARS = 200  # how much of what a control says is passed on


@dataclass(frozen=True)
class Aim:
    """The control that is meant: as the look saw it (an id's kind, name and place), or only
    its name, for one that was not on screen at the look (a dialog that has opened since)."""

    hwnd: int
    name: str = ""
    role: str = ""  # "": any kind
    rect: tuple[int, int, int, int] | None = None
    automation_id: str = ""


@dataclass
class Live:
    """One control as it is now."""

    name: str
    role: str
    rect: tuple[int, int, int, int]
    enabled: bool = True
    offscreen: bool = False
    patterns: frozenset[str] = frozenset()
    value: str | None = None
    read_only: bool = False
    on: bool | None = None  # ticked, or chosen
    open: bool | None = None  # a dropdown or a tree row: open or closed
    password: bool = False
    focused: bool = False  # the keyboard is in it
    automation_id: str = ""
    root: int = 0  # the window (or menu) it was found in
    handle: Any = None


@dataclass
class Done:
    ok: bool = False
    how: str = ""  # the way it was done
    now: str = ""  # what the control says now: its value, "on", "off", "chosen", "open"
    why: str = ""  # not done: "not found", "disabled", "password", "no way", "did not take", ...
    name: str = ""  # the control's own name
    role: str = ""
    rect: tuple[int, int, int, int] | None = None  # where it is now
    found: list[str] = field(default_factory=list)  # for find, and what a menu holds instead
    sure: bool = False  # the control's own state says it took
    late: bool = False  # the app had not answered in time (a dialog opened): done, not checked
    others: int = 0  # how many more controls answer to that name
    offscreen: bool = False
    password: bool = False
    focused: bool = False  # the keyboard is in it now
    steps: int = 0  # menus opened on the way, when a menu path stopped part-way


def norm(text: str) -> str:
    """A control's name as it is compared: its words, in lower case, without the shortcut a
    menu item carries after a tab, the dots of "Save as...", or an underlined letter's mark."""
    text = (text or "").split("\t")[0].replace("&", "").replace("…", "")
    text = re.sub(r"(\.{2,}|:)\s*$", "", text.strip())
    return " ".join(text.casefold().split())


def closeness(want: str, name: str, loose: bool = False) -> int | None:
    """How well a control's name answers to the words asked for: 0 the same, 1 it starts with
    them, 2 it has them. None: it does not. ``loose``: 3 when the words asked for have
    the whole name in them ("Find what" for a box called "Find"): only for fields, where
    what was put in is read back; never for something that is pressed."""
    w, n = norm(want), norm(name)
    if not w or not n:
        return None
    if w == n:
        return 0
    if n.startswith(w):
        return 1
    if w in n:
        return 2
    return 3 if loose and len(n) >= 3 and re.search(rf"(?<!\w){re.escape(n)}(?!\w)", w) else None


def _mid(rect: tuple[int, int, int, int]) -> tuple[float, float]:
    return rect[0] + rect[2] / 2, rect[1] + rect[3] / 2


def choose(
    aim: Aim, found: list[Live], fits: Callable[[Live], bool] | None = None, loose: bool = False
) -> tuple[Live | None, int]:
    """Which of the controls found is the one meant, and how many others would have done.

    The kind asked for comes first, then the closest name, then one that can be used (not
    greyed out, in view), then the one nearest to where the look saw it. With no name (a
    field the look gave an id but the app gave no name), it is the one of that kind still in
    place."""
    pool = [f for f in found if fits is None or fits(f)]
    if aim.role and any(f.role == aim.role for f in pool):
        pool = [f for f in pool if f.role == aim.role]
    if not norm(aim.name):
        if aim.rect is None:
            return None, 0
        tx, ty = _mid(aim.rect)
        reach = max(24.0, math.hypot(aim.rect[2], aim.rect[3]) / 2)
        near = [f for f in pool if not f.offscreen and math.hypot(_mid(f.rect)[0] - tx, _mid(f.rect)[1] - ty) <= reach]
        if aim.automation_id and any(f.automation_id == aim.automation_id for f in near):
            near = [f for f in near if f.automation_id == aim.automation_id]
        near.sort(key=lambda f: math.hypot(_mid(f.rect)[0] - tx, _mid(f.rect)[1] - ty))
        return (near[0], len(near) - 1) if near else (None, 0)
    scored = [(closeness(aim.name, f.name, loose), f) for f in pool]
    scored = [(s, f) for s, f in scored if s is not None]
    if not scored:
        return None, 0
    best = min(s for s, _ in scored)
    same = [f for s, f in scored if s == best]

    def order(f: Live) -> tuple:
        far = 0.0
        if aim.rect is not None:
            far = math.hypot(_mid(f.rect)[0] - _mid(aim.rect)[0], _mid(f.rect)[1] - _mid(aim.rect)[1])
        # A label that only says the words comes after the control that carries them.
        longest = -len(norm(f.name)) if best == 3 else 0  # of names inside the words, the fullest
        return (longest, not f.enabled, f.offscreen or f.rect[2] < 3 or f.rect[3] < 3, f.role in ("Text", "Image"), far)

    same.sort(key=order)  # a stable sort: of equals, the first found (a menu before its window)
    return same[0], len(same) - 1


class _Box:
    """What the working thread and the one waiting for it both know."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.done = False
        self.result: Done | None = None
        self.doing: Done | None = None  # set just before the thing itself is done
        self.left = False

    def finish(self, result: Done | None) -> None:
        with self.lock:
            self.result, self.done = result, True

    def begin(self, how: str, live: Live | None = None) -> bool:
        """About to do it. False: nobody is waiting any more, so do nothing."""
        with self.lock:
            if self.left:
                return False
            self.doing = Done(True, how, name=live.name if live else "", role=live.role if live else "",
                              rect=live.rect if live else None, late=True)  # fmt: skip
            return True

    def undone(self) -> bool:
        with self.lock:
            if self.left:
                return False
            self.doing = None
            return True

    def gone(self) -> bool:
        with self.lock:
            return self.left


class Controls:
    """The verbs. ``backend``: the real accessibility layer, or a pretend one in tests."""

    POLL_S = 0.1

    def __init__(self, backend: Any = None, private: Callable[[str, str], bool] | None = None) -> None:
        self.backend = backend if backend is not None else Uia(private)

    # ------------------------------------------------------------------ plumbing
    def _run(self, work: Callable[[_Box], Done | None], timeout_s: float) -> Done:
        box = _Box()

        def run() -> None:
            try:
                with self.backend.thread():
                    box.finish(work(box))
            except Exception as exc:
                log.debug("control: failed: %s", exc)
                box.finish(Done(why="failed"))

        worker = threading.Thread(target=run, daemon=True, name="pyxi-control")
        worker.start()
        worker.join(timeout_s)
        with box.lock:
            if box.done:
                return box.result or Done(why="not found")
            box.left = True
            if box.doing is not None:
                log.info("control: %s on %r has not come back; taken as done", box.doing.how, box.doing.name)
                return box.doing
        return Done(why="slow")

    def _seek(
        self, aim: Aim, fits: Callable[[Live], bool] | None, wait_s: float, box: _Box, loose: bool = False
    ) -> tuple[Live | None, int, str]:
        """The control meant, looked for until ``wait_s`` is up. With why, when it is not found.
        ``loose``: when nothing has the words asked for, a control whose whole name is
        among them will do (see `closeness`)."""
        end = time.monotonic() + max(0.0, wait_s)
        while True:
            roots = self.backend.roots(aim.hwnd)
            if not roots:
                return None, 0, "not in front"
            live, others = choose(aim, self.backend.search(roots, aim.name, aim.role if not aim.name else ""), fits)
            if live is None and loose:
                for word in [w for w in norm(aim.name).split() if len(w) >= 3][:3]:
                    live, others = choose(aim, self.backend.search(roots, word, ""), fits, loose=True)
                    if live is not None:
                        break
            if live is not None:
                # A dialog that came up in front of the window it is in: what is behind a
                # dialog is not to be used until the dialog is dealt with, as for a person.
                behind = getattr(self.backend, "blocked", None)
                if behind is not None and behind(live):
                    return None, 0, "behind a dialog"
                return live, others, ""
            if time.monotonic() >= end or box.gone():
                return None, 0, "not found"
            time.sleep(self.POLL_S)

    def _still_open(self, walked: list[Live]) -> int:
        """How many of the menus opened on the way are open still (one that closed itself
        needs no closing: an Esc for it would go to the app instead)."""
        still = 0
        for live in walked:
            try:
                still += bool(self.backend.refresh(live).open)
            except Exception:
                continue
        return still

    @staticmethod
    def _told(live: Live, **more: Any) -> Done:
        return Done(
            name=live.name, role=live.role, rect=live.rect, offscreen=live.offscreen, password=live.password,
            focused=live.focused, **more,
        )  # fmt: skip

    def _state(self, live: Live, want: Callable[[Live], bool], wait_s: float = 0.5) -> Live:
        """The control a moment later: apps answer first and change after."""
        end = time.monotonic() + wait_s
        now = live
        while True:
            try:
                now = self.backend.refresh(live)
            except Exception:
                return now
            if want(now) or time.monotonic() >= end:
                return now
            time.sleep(0.05)

    def in_front(self, hwnd: int) -> str:
        """The title of the window in front, when it is another one of the same family
        than ``hwnd`` (a dialog of it that has opened). Empty otherwise."""
        return self.backend.in_front(hwnd)

    def watch(self, hwnd: int) -> None:
        """A look was taken of this window: remembered, so that if it closes it can be said
        which window of the same program is in front after it."""
        seen = getattr(self.backend, "watch", None)
        if seen is not None:
            seen(hwnd)

    def closed(self, hwnd: int) -> bool:
        """Is the window a look was of gone now (a dialog that was answered)?"""
        gone = getattr(self.backend, "closed", None)
        return bool(gone(hwnd)) if gone is not None else False

    # ------------------------------------------------------------------ the verbs
    def where(self, aim: Aim, wait_s: float = 0.0, kinds: frozenset[str] | None = None) -> Done:
        """Where a control is now. ``kinds``: the kinds wanted first (any kind if none is)."""

        def work(box: _Box) -> Done:
            live, others, why = self._seek(aim, (lambda f: f.role in kinds) if kinds else None, 0.0, box)
            if live is None and why == "not found":
                live, others, why = self._seek(aim, None, wait_s, box)
            if live is None:
                return Done(why=why)
            return self._told(live, ok=True, others=others, now=_says(live), why="" if live.enabled else "disabled")

        return self._run(work, wait_s + 2.5)

    def wait_for(self, aim: Aim, timeout_s: float = 5.0, gone: bool = False) -> Done:
        """Wait until a control of that name is there (``gone``: is no longer there)."""

        def work(box: _Box) -> Done:
            end = time.monotonic() + timeout_s
            while True:
                live, _, why = self._seek(aim, None, 0.0, box)
                there = live is not None and not live.offscreen
                if why == "not in front" and not gone:
                    there = False
                if there != gone:
                    return self._told(live, ok=True) if live else Done(ok=True)
                if time.monotonic() >= end or box.gone():
                    return Done(why="still there" if gone else "not found")
                time.sleep(self.POLL_S)

        return self._run(work, timeout_s + 2.0)

    def click(self, aim: Aim, wait_s: float = 0.0) -> presses.Result | None:
        """Press a control found by its name, the way screen/press.py presses one the look
        gave an id: by what it offers, checked by its own state where it has one. None: not
        found. A result with no ``how``: found, not pressed, and where it is."""

        def work(box: _Box) -> Done:
            live, _, why = self._seek(aim, lambda f: f.role in presses.PRESSABLE, 0.0, box)
            if live is None and why == "not found":
                live, _, why = self._seek(aim, None, wait_s, box)
            if live is None:
                return Done(why=why)
            if (live.offscreen or live.rect[2] < 3 or live.rect[3] < 3) and "ScrollItem" in live.patterns:
                with contextlib.suppress(Exception):
                    self.backend.do(live, "into_view")
                    live = self._state(live, lambda n: not n.offscreen, 0.4)
            if not live.enabled:
                return self._told(live, why="disabled")
            offered = presses.ways(live.role, live.patterns) if live.role in presses.PRESSABLE else ()
            if not offered:
                return self._told(live, why="no way")
            if offered[0] == "select" and live.on:
                return self._told(live, why="chosen")
            for way in offered:
                if not box.begin(way, live):
                    return Done(why="slow")
                before = live.on
                try:
                    self.backend.do(live, way)
                except Exception as exc:
                    log.debug("control: %s by %s refused: %s", live.name, way, exc)
                    if not box.undone():
                        return Done(why="slow")
                    continue
                if box.gone():
                    return Done(why="slow")
                if way in ("toggle", "select"):
                    now = self._state(live, lambda n, b=before, w=way: n.on is not None and (n.on != b if w == "toggle" else n.on))
                    if now.on is not None and (now.on != before if way == "toggle" else now.on):
                        return self._told(now, ok=True, how=way, sure=True, now=_says(now))
                    if not box.undone():
                        return Done(why="slow")
                    continue
                return self._told(live, ok=True, how=way)
            return self._told(live, why="did not take")

        done = self._run(work, wait_s + 2.5)
        if done.why in ("not found", "not in front", "slow", "failed", "behind a dialog") and not done.ok:
            return None
        return presses.Result(done.how if done.ok else "", done.rect, done.why, done.sure, done.late)

    def set_value(self, aim: Aim, text: str, wait_s: float = 0.0, if_empty: bool = False) -> Done:
        """Put ``text`` in a field, in place of what it holds, and read back what it says.
        ``if_empty``: only into a field that holds nothing now ("not empty" otherwise):
        for typing, which adds to what is there and must never replace it."""

        def work(box: _Box) -> Done:
            # The field itself, not the label beside it that has the same words (a label
            # can say it has a value too, one that can't be written).
            writable = lambda f: "Value" in f.patterns and not f.read_only and f.role not in ("Text", "Image")  # noqa: E731
            live, others, why = self._seek(aim, writable, 0.0, box)
            if live is None and why == "not found":
                live, others, why = self._seek(aim, lambda f: f.role in FIELDS or "Value" in f.patterns, wait_s, box, loose=True)
                if live is not None and not writable(live):
                    again, others2, _ = self._seek(aim, writable, 0.0, box)  # it may have come up meanwhile
                    live, others = (again, others2) if again is not None else (live, others)
            if live is None:
                return Done(why=why)
            if live.role == "ComboBox" and not live.offscreen:
                # A box that can be typed in is a dropdown with a field inside it, and it is
                # the field the app reads when Enter is pressed (a file name box).
                x, y, w, h = live.rect
                with contextlib.suppress(Exception):
                    inner = [
                        f for f in self.backend.search(self.backend.roots(aim.hwnd), "", "Edit")
                        if writable(f) and not f.offscreen and x <= _mid(f.rect)[0] <= x + w and y <= _mid(f.rect)[1] <= y + h
                    ]  # fmt: skip
                    if inner:
                        live = inner[0]
            if live.password:
                return self._told(live, why="password")
            if not live.enabled:
                return self._told(live, why="disabled")
            if "Value" not in live.patterns or live.read_only:
                return self._told(live, why="no way", now=_says(live))
            if if_empty and live.value:
                return self._told(live, why="not empty")
            if not box.begin("value", live):
                return Done(why="slow")
            with contextlib.suppress(Exception):
                self.backend.do(live, "focus")  # so that Enter, pressed next, goes to this field
            self.backend.set_value(live, text)
            if box.gone():
                return Done(why="slow")
            now = self._state(live, lambda n: (n.value or "") == text, 0.4)
            if (now.value or "") == text:
                return self._told(now, ok=True, how="value", now=text[:VALUE_CHARS], sure=True, others=others)
            return self._told(now, why="did not take", now=(now.value or "")[:VALUE_CHARS])

        return self._run(work, wait_s + 2.5)

    def toggle(self, aim: Aim, want: bool | None = None, wait_s: float = 0.0) -> Done:
        """Turn a switch, a tick or a toggle button on or off. With ``want``, only if it is
        not so already; without, the other way from how it is."""

        def work(box: _Box) -> Done:
            live, _, why = self._seek(aim, lambda f: "Toggle" in f.patterns, wait_s, box)
            if live is None and why == "not found":
                live, _, why = self._seek(aim, None, 0.0, box)  # there, but no switch
            if live is None:
                return Done(why=why)
            if "Toggle" not in live.patterns or live.on is None:
                return self._told(live, why="no way")
            if want is not None and live.on == want:
                return self._told(live, ok=True, how="already", now=_says(live), sure=True)
            if not live.enabled:
                return self._told(live, why="disabled")
            before = live.on
            for _ in range(2):  # a tick with a third, "partly" state takes two
                if not box.begin("toggle", live):
                    return Done(why="slow")
                self.backend.do(live, "toggle")
                if box.gone():
                    return Done(why="slow")
                now = self._state(live, lambda n, b=before: n.on is not None and n.on != b)
                if now.on is not None and now.on != before:
                    return self._told(now, ok=True, how="toggle", now=_says(now), sure=True)
                live = now
            return self._told(live, why="did not take", now=_says(live))

        return self._run(work, wait_s + 3.0)

    def expand(self, aim: Aim, open_: bool = True, wait_s: float = 0.0) -> Done:
        """Open (or close) a dropdown, a tree row, a section, a menu."""

        def work(box: _Box) -> Done:
            live, _, why = self._seek(aim, lambda f: "ExpandCollapse" in f.patterns, wait_s, box)
            if live is None and why == "not found":
                live, _, why = self._seek(aim, None, 0.0, box)  # there, but nothing to open
            if live is None:
                return Done(why=why)
            if "ExpandCollapse" not in live.patterns:
                return self._told(live, why="no way")
            if live.open is not None and live.open == open_:
                return self._told(live, ok=True, how="already", now=_says(live), sure=True)
            if not live.enabled:
                return self._told(live, why="disabled")
            if not box.begin("expand" if open_ else "collapse", live):
                return Done(why="slow")
            self.backend.do(live, "expand" if open_ else "collapse")
            if box.gone():
                return Done(why="slow")
            now = self._state(live, lambda n: n.open == open_)
            if now.open == open_:
                return self._told(now, ok=True, how="expand" if open_ else "collapse", now=_says(now), sure=True)
            return self._told(now, why="did not take", now=_says(now))

        return self._run(work, wait_s + 2.5)

    def _item(self, aim: Aim, item: str, within: Live | None, box: _Box, wait_s: float) -> tuple[Live | None, int]:
        """A row or an option by its name: in view, out of view, or not yet made by a list
        that only makes the rows it shows."""
        fits = lambda f: f.role in ITEMS or "SelectionItem" in f.patterns  # noqa: E731
        names = [item] + ([item.rsplit(".", 1)[0]] if re.search(r"\.\w{1,5}$", item) else [])
        end = time.monotonic() + max(0.0, wait_s)
        while True:
            roots = self.backend.roots(aim.hwnd)
            for name in names:
                found = self.backend.search(roots, name, "") if roots else []
                if within is not None and not within.offscreen:
                    x, y, w, h = within.rect
                    inside = [f for f in found if x <= _mid(f.rect)[0] <= x + w and y <= _mid(f.rect)[1] <= y + h]
                    found = inside or found
                # One that can be chosen comes before a stand-in of the same name: an open
                # dropdown lists each row two or three times, and only one of them is real.
                live, others = choose(Aim(aim.hwnd, name), found, lambda f: "SelectionItem" in f.patterns)
                if live is not None and (closeness(name, live.name) in (0, 1) or others == 0):
                    return live, others
                if roots:
                    with contextlib.suppress(Exception):
                        made = self.backend.realize(roots, name)
                        if made is not None:
                            return self._made(made, name, aim.hwnd, fits), 0
                live, others = choose(Aim(aim.hwnd, name), found, fits)
                if live is not None and (closeness(name, live.name) in (0, 1) or others == 0):
                    return live, others
            if time.monotonic() >= end or box.gone():
                return None, 0
            time.sleep(self.POLL_S)

    def _made(self, made: Live, name: str, hwnd: int, fits: Callable[[Live], bool]) -> Live:
        """A row its list has only just made is a stand-in for a moment: no way to choose
        it yet, and not where it will be. The real one is looked for by name until it is
        there (half a second at most); failing that, the stand-in as it is now."""
        end = time.monotonic() + 0.6
        while True:
            with contextlib.suppress(Exception):
                # Asked afresh each time: a dropdown's rows are in a window of their own,
                # which is not there until the dropdown has opened.
                live, _ = choose(Aim(0, name), self.backend.search(self.backend.roots(hwnd), name, ""), fits)
                if live is not None and closeness(name, live.name) == 0 and "SelectionItem" in live.patterns:
                    return live
            if time.monotonic() >= end:
                break
            time.sleep(0.05)
        with contextlib.suppress(Exception):
            return self.backend.refresh(made)
        return made

    def select(self, aim: Aim, item: str, wait_s: float = 0.0) -> Done:
        """Choose the row or option called ``item``. ``aim``: the list or the dropdown it is
        in, when one was named (a dropdown is opened first and closed after); with only a
        window, the row is looked for in all of it. A row out of view is brought into view."""

        def work(box: _Box) -> Done:
            within: Live | None = None
            if not self.backend.roots(aim.hwnd):
                return Done(why="not in front")
            if aim.name or aim.rect is not None:
                within, _, why = self._seek(aim, None, wait_s, box)
                if within is None:
                    return Done(why=why)
                if not within.enabled:
                    return self._told(within, why="disabled")
            opened = False
            dropdown = within is not None and within.role == "ComboBox"
            # A dropdown's option can mostly be chosen with the dropdown shut. Only when no
            # option that can be chosen is found that way is it opened to look again.
            live, others = self._item(aim, item, None if dropdown else within, box, 0.0 if dropdown else wait_s)
            if dropdown and (live is None or "SelectionItem" not in live.patterns) and "ExpandCollapse" in within.patterns and not within.open:
                if not box.begin("select", within):
                    return Done(why="slow")
                with contextlib.suppress(Exception):
                    self.backend.do(within, "expand")
                    opened = True
                    time.sleep(0.25)
                box.undone()
                again, others2 = self._item(aim, item, None, box, 1.0)
                if again is not None:
                    live, others = again, others2
            if live is None:
                if opened:
                    with contextlib.suppress(Exception):
                        self.backend.do(within, "collapse")
                return Done(why="no such row", name=item)
            if live.offscreen or live.rect[2] < 3 or live.rect[3] < 3:
                with contextlib.suppress(Exception):
                    self.backend.do(live, "into_view")
                    live = self._state(live, lambda n: not n.offscreen, 0.5)
            if not live.enabled:
                return self._told(live, why="disabled")
            if live.on and "SelectionItem" in live.patterns and not opened:
                return self._told(live, ok=True, how="already", now="chosen", sure=True, others=others)
            way = "select" if "SelectionItem" in live.patterns else "invoke" if opened and "Invoke" in live.patterns else ""
            if not way:
                # Where it is, for a click, only from a control that really is one: a
                # stand-in says it is in view at a place where something else is.
                real = live.patterns & {"ScrollItem", "Invoke", "ExpandCollapse", "Toggle"}
                return self._told(live, why="no way") if real else Done(why="no way", name=live.name, role=live.role)
            if not box.begin(way, live):
                return Done(why="slow")
            self.backend.do(live, way)
            if box.gone():
                return Done(why="slow")
            now = self._state(live, lambda n: bool(n.on)) if way == "select" else live
            if opened:
                with contextlib.suppress(Exception):
                    if self.backend.refresh(within).open:
                        self.backend.do(within, "collapse")
            if way == "select" and not now.on and not opened:
                return self._told(now, why="did not take")
            return self._told(now, ok=True, how=way, now="chosen", sure=bool(now.on), others=others)

        return self._run(work, wait_s + 4.0)

    def scroll_to(self, aim: Aim, wait_s: float = 0.0) -> Done:
        """Bring a named row or control into view, however far away in its list it is."""

        def work(box: _Box) -> Done:
            live, others = self._item(aim, aim.name, None, box, 0.0)
            if live is None:
                live, others, why = self._seek(aim, None, wait_s, box)
                if live is None:
                    return Done(why=why)
            if not live.offscreen and live.rect[2] >= 3 and live.rect[3] >= 3:
                return self._told(live, ok=True, how="already", now="in view", others=others)
            if "ScrollItem" not in live.patterns:
                return self._told(live, why="no way")
            if not box.begin("into_view", live):
                return Done(why="slow")
            self.backend.do(live, "into_view")
            now = self._state(live, lambda n: not n.offscreen, 0.6)
            if now.offscreen:
                return self._told(now, why="did not take")
            return self._told(now, ok=True, how="into_view", now="in view", others=others)

        return self._run(work, wait_s + 3.0)

    def menu(self, hwnd: int, path: list[str], wait_s: float = 2.0) -> Done:
        """Walk a menu by its words: each one opened in turn, the last one pressed. When a
        word is not there, says which and what that menu holds instead."""
        path = [p.strip() for p in path if p and p.strip()]

        def work(box: _Box) -> Done:
            walked: list[Live] = []  # the menus opened on the way
            for i, word in enumerate(path):
                last = i == len(path) - 1
                aim = Aim(hwnd, word)
                live, _, why = self._seek(aim, lambda f: f.role == "MenuItem" and not f.offscreen, wait_s if i else 0.3, box)
                if live is None and why == "not found":
                    live, _, why = self._seek(aim, lambda f: f.role in MENUS and not f.offscreen, 0.0, box)
                if live is None:
                    roots = self.backend.roots(hwnd)
                    held = [f.name for f in (self.backend.search(roots, "", "MenuItem") if roots else []) if f.name and not f.offscreen]
                    return Done(why=why, name=word, found=list(dict.fromkeys(held))[:30], steps=self._still_open(walked))
                if not live.enabled:
                    return self._told(live, why="disabled", steps=self._still_open(walked))
                if last:
                    order = ("invoke", "select", "toggle", "legacy", "expand")
                else:
                    order = ("expand", "invoke", "legacy")
                needs = {"invoke": "Invoke", "select": "SelectionItem", "toggle": "Toggle", "legacy": "LegacyIAccessible", "expand": "ExpandCollapse"}
                ways = [w for w in order if needs[w] in live.patterns]
                if not last and live.open:
                    walked.append(live)
                    continue
                did = False
                for way in ways:
                    if not box.begin("menu", live):
                        return Done(why="slow")
                    try:
                        self.backend.do(live, way)
                        did = True
                        break
                    except Exception as exc:
                        log.debug("control: menu %s by %s refused: %s", word, way, exc)
                        if not box.undone():
                            return Done(why="slow")
                if box.gone():
                    return Done(why="slow")
                if not did:
                    return self._told(live, why="no way", steps=self._still_open(walked))
                if not last:
                    box.undone()
                    walked.append(live)
            return Done(ok=True, how="menu", name=path[-1] if path else "")

        if not path:
            return Done(why="not found")
        return self._run(work, len(path) * wait_s + 2.5)

    def find(self, hwnd: int, text: str, limit: int = 10) -> Done:
        """The controls whose names have these words, in view or not: a line each."""

        def work(box: _Box) -> Done:
            roots = self.backend.roots(hwnd)
            if not roots:
                return Done(why="not in front")
            found = [f for f in self.backend.search(roots, text, "") if closeness(text, f.name) is not None]
            made = None
            if not found:
                with contextlib.suppress(Exception):
                    made = self.backend.realize(roots, text)
            if made is not None:
                found = [made]
            found.sort(key=lambda f: (closeness(text, f.name) or 0, f.role == "Text", f.offscreen))
            seen: set[tuple[str, str, bool]] = set()
            lines: list[str] = []
            for f in found:
                key = (f.role, norm(f.name), f.offscreen)
                if key in seen:
                    continue
                seen.add(key)
                lines.append(describe(f))
            return Done(ok=bool(lines), found=lines[:limit], others=max(0, len(lines) - limit), why="" if lines else "not found")

        return self._run(work, 3.5)

    def read(self, aim: Aim, wait_s: float = 0.0, most: int = 600) -> Done:
        """What a control says now: a field's text, a switch's state, a chosen row."""

        def work(box: _Box) -> Done:
            live, others, why = self._seek(aim, None, wait_s, box)
            if live is None:
                return Done(why=why)
            if live.password:
                return self._told(live, why="password")
            says = _says(live)
            if not says and "Text" in live.patterns:
                with contextlib.suppress(Exception):
                    says = (self.backend.text(live, most) or "").strip()
                    if any(secrets.looks_secret(line) for line in says.splitlines()):
                        says = secrets.HIDDEN
            return self._told(live, ok=True, now=says[:most], others=others)

        return self._run(work, wait_s + 2.5)


def _says(live: Live) -> str:
    """What a control says about itself, in a few words."""
    if live.password:
        return ""
    bits: list[str] = []
    if live.value:
        # A key or a token in a field is no more passed on from here than from a look.
        bits.append(f"reads {secrets.HIDDEN}" if secrets.looks_secret(live.value) else f"reads {live.value[:VALUE_CHARS]!r}")
    if "Toggle" in live.patterns and live.on is not None:
        bits.append("on" if live.on else "off")
    elif live.on:
        bits.append("chosen")
    if live.open is not None and "ExpandCollapse" in live.patterns:
        bits.append("open" if live.open else "closed")
    return ", ".join(bits)


def describe(live: Live) -> str:
    notes = [n for n in (_says(live), "greyed out" if not live.enabled else "", "out of view" if live.offscreen else "") if n]
    return f'{live.role} "{live.name[:90]}"' + (f" ({'; '.join(notes)})" if notes else "")


# ---------------------------------------------------------------------- the real thing
def _family(hwnd: int, private: Callable[[str, str], bool] | None) -> list[int]:
    """The window being worked in and what belongs to it (its dialogs, its open menus and
    dropdowns), topmost first; empty unless one of them is the window in front. Never one of
    Pyxi's own, never a private one, never another window of the same program."""
    if sys.platform != "win32" or not hwnd:
        return []
    import ctypes.wintypes as wt

    from pyxi import hands
    from pyxi.screen import uia

    u = ctypes.WinDLL("user32")
    u.GetWindow.restype = wt.HWND
    u.GetWindow.argtypes = [wt.HWND, wt.UINT]
    u.GetAncestor.restype = wt.HWND
    u.GetAncestor.argtypes = [wt.HWND, wt.UINT]
    u.IsWindow.argtypes = [wt.HWND]
    u.IsWindowVisible.argtypes = [wt.HWND]

    def pid(h: int) -> int:
        p = wt.DWORD()
        u.GetWindowThreadProcessId(wt.HWND(h), ctypes.byref(p))
        return int(p.value)

    def owners(h: int) -> list[int]:
        chain, seen = [], 0
        while h and seen < 8:
            chain.append(int(h))
            h, seen = int(u.GetWindow(wt.HWND(h), 4) or 0), seen + 1  # GW_OWNER
        return chain

    front = hands.foreground_window()
    front = int(u.GetAncestor(wt.HWND(front), 2) or 0) or front if front else 0
    if not front or hands.is_own(front):
        return []
    if ctypes.windll.user32.IsIconic(front):  # type: ignore[attr-defined]
        return []  # put away by the user since the look: not a window to go on working in
    anchor = int(hwnd) if u.IsWindow(wt.HWND(hwnd)) else 0
    if not anchor:
        return []
    mine = pid(anchor)
    kin: set[int] = set(owners(anchor))
    found: list[int] = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def visit(h, _):
        h = int(h)
        if not u.IsWindowVisible(wt.HWND(h)) or pid(h) != mine:
            return True
        title = uia.window_title(h) or ""
        if h in kin or kin.intersection(owners(h)) or not title.strip():
            found.append(h)
        return True

    u.EnumWindows(visit, 0)
    if front not in found:
        return []
    out: list[int] = []
    for h in found:
        if hands.is_own(h):
            return []
        if private is not None:
            try:
                if private(uia.window_title(h) or "", uia.process_name(pid(h))):
                    continue
            except Exception:
                continue
        out.append(h)
    return out


class Uia:
    """UI Automation, on the working thread."""

    def __init__(self, private: Callable[[str, str], bool] | None = None) -> None:
        self.private = private
        self._pid: dict[int, int] = {}  # the program of each window asked about, while it was there

    @contextlib.contextmanager
    def thread(self) -> Iterator[None]:
        from uiautomation import uiautomation as u

        with u.UIAutomationInitializerInThread(debug=False):
            yield

    def roots(self, hwnd: int) -> list[int]:
        try:
            return _family(hwnd, self.private)
        except Exception as exc:
            log.debug("control: could not tell the windows apart: %s", exc)
            return []

    def blocked(self, live: Live) -> bool:
        """Is this control in a window that a dialog has taken over from (Windows switches
        such a window off until the dialog is answered)? A menu, a dropdown or a window
        that merely sits in front does not count."""
        if not live.root:
            return False
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        return bool(user32.IsWindow(live.root)) and not user32.IsWindowEnabled(live.root)

    def watch(self, hwnd: int) -> None:
        from pyxi.screen import uia

        if sys.platform != "win32" or not hwnd or hwnd in self._pid:
            return
        self._pid[hwnd] = uia._pid_of(hwnd)
        if len(self._pid) > 32:
            self._pid.pop(next(iter(self._pid)))

    def closed(self, hwnd: int) -> bool:
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        return not user32.IsWindow(hwnd) or not user32.IsWindowVisible(hwnd)

    def in_front(self, hwnd: int) -> str:
        from pyxi import hands
        from pyxi.screen import uia

        front = hands.foreground_window()
        if not front or front == hwnd or hands.is_own(front):
            return ""
        if self.closed(hwnd):
            # The look was of a dialog that has gone: the window in front is named only
            # when it is the one that dialog belonged to (the same program's, not private).
            mine = self._pid.pop(hwnd, None)
            title, pid = (uia.window_title(front) or "").strip(), uia._pid_of(front)
            if not mine or pid != mine or (self.private is not None and self.private(title, uia.process_name(pid))):
                return ""
            return title
        self.watch(hwnd)
        if front not in self.roots(hwnd):
            return ""
        return (uia.window_title(front) or "").strip()

    @staticmethod
    def _ui() -> Any:
        from uiautomation import uiautomation as u

        return u._AutomationClient.instance().IUIAutomation

    def _request(self, ui: Any) -> Any:
        cache = ui.CreateCacheRequest()
        for prop in (_P_RECT, _P_TYPE, _P_NAME, _P_ENABLED, _P_AUTOMATION_ID, _P_PASSWORD, _P_OFFSCREEN,
                     _P_VALUE, _P_READONLY, _P_EXPANDED, _P_SELECTED, _P_TOGGLE, _P_FOCUSED, *_HAS):  # fmt: skip
            cache.AddProperty(prop)
        cache.TreeFilter = ui.RawViewCondition
        return cache

    def _live(self, raw: Any, root: int, cached: bool = True) -> Live | None:
        def get(prop: int) -> Any:
            try:
                return raw.GetCachedPropertyValue(prop) if cached else raw.GetCurrentPropertyValue(prop)
            except Exception:
                return None

        try:
            # Through this call a rectangle comes as left, top, width, height.
            left, top, width, height = (int(v) for v in (get(_P_RECT) or (0, 0, 0, 0))[:4])
        except (TypeError, ValueError):
            return None
        patterns = frozenset(name for prop, name in _HAS.items() if get(prop))
        password = bool(get(_P_PASSWORD))
        on: bool | None = None
        if "Toggle" in patterns and (state := get(_P_TOGGLE)) is not None:
            on = state == 1
        elif "SelectionItem" in patterns:
            on = bool(get(_P_SELECTED))
        opened: bool | None = None
        if "ExpandCollapse" in patterns and (state := get(_P_EXPANDED)) is not None:
            opened = state in (1, 2)  # expanded, or partly
        value = None
        if "Value" in patterns and not password:
            raw_value = get(_P_VALUE)
            value = str(raw_value) if raw_value is not None else None
        return Live(
            name=str(get(_P_NAME) or ""),
            role=_ROLES.get(int(get(_P_TYPE) or 0), ""),
            rect=(left, top, width, height),
            enabled=get(_P_ENABLED) is not False,
            offscreen=bool(get(_P_OFFSCREEN)),
            patterns=patterns,
            value=value,
            read_only=bool(get(_P_READONLY)) if "Value" in patterns else False,
            on=on,
            open=opened,
            password=password,
            focused=bool(get(_P_FOCUSED)),
            automation_id=str(get(_P_AUTOMATION_ID) or "")[:80],
            root=root,
            handle=raw,
        )

    def search(self, roots: list[int], name: str, role: str = "") -> list[Live]:
        """Every control whose name has these words (any kind), or, with no words, every
        control of one kind. Asked of the program in one call a window."""
        ui = self._ui()
        cache = self._request(ui)
        conditions: list[Any] = []
        if name.strip():
            words = name.strip()
            for flags in (3, 1):  # ignoring case, anywhere in the name; then the whole name
                try:
                    conditions.append(ui.CreatePropertyConditionEx(_P_NAME, words, flags))
                    break
                except Exception:
                    continue
        elif role in _ROLE_IDS:
            conditions.append(ui.CreatePropertyCondition(_P_TYPE, _ROLE_IDS[role]))
        if not conditions:
            conditions.append(ui.RawViewCondition)
        out: list[Live] = []
        for hwnd in roots:
            try:
                top = ui.ElementFromHandle(hwnd)
                found = top.FindAllBuildCache(_DESCENDANTS, conditions[0], cache)
            except Exception as exc:
                log.debug("control: a window would not be searched: %s", exc)
                continue
            for i in range(found.Length if found else 0):
                live = self._live(found.GetElement(i), hwnd)
                if live is not None:
                    out.append(live)
        return out

    def refresh(self, live: Live) -> Live:
        return self._live(live.handle, live.root, cached=False) or live

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
        elif way == "collapse":
            self._pattern(live, "ExpandCollapse").Collapse(waitTime=0)
        elif way == "legacy":
            self._pattern(live, "LegacyIAccessible").DoDefaultAction(waitTime=0)
        elif way == "into_view":
            self._pattern(live, "ScrollItem").ScrollIntoView(waitTime=0)
        elif way == "focus":
            live.handle.SetFocus()
        else:
            raise LookupError(way)

    def set_value(self, live: Live, text: str) -> None:
        self._pattern(live, "Value").SetValue(text, waitTime=0)

    def text(self, live: Live, most: int) -> str:
        return self._pattern(live, "Text").DocumentRange.GetText(most)

    def realize(self, roots: list[int], name: str) -> Live | None:
        """A row that its list has not made yet (lists of thousands only make the rows they
        show): asked of the list by name, and made."""
        ui = self._ui()
        for hwnd in roots:
            top = ui.ElementFromHandle(hwnd)
            holders = top.FindAll(_DESCENDANTS, ui.CreatePropertyCondition(30108, True))
            for i in range(min(holders.Length if holders else 0, 6)):
                try:
                    container = holders.GetElement(i).GetCurrentPattern(_PAT["ItemContainer"])
                    from comtypes.gen.UIAutomationClient import (
                        IUIAutomationItemContainerPattern,
                        IUIAutomationVirtualizedItemPattern,
                    )

                    item = container.QueryInterface(IUIAutomationItemContainerPattern).FindItemByProperty(None, _P_NAME, name)
                    if not item:
                        continue
                    with contextlib.suppress(Exception):
                        virtual = item.GetCurrentPattern(_PAT["VirtualizedItem"])
                        if virtual:
                            virtual.QueryInterface(IUIAutomationVirtualizedItemPattern).Realize()
                    return self._live(item, hwnd, cached=False)
                except Exception as exc:
                    log.debug("control: a list would not find the row: %s", exc)
        return None
