"""A window's controls are read in one call to its program, not one call a property.

Measured on a browser window, 13 November: the walk asked the program some thousands of
separate questions and took over 8 seconds, where the budget is 1.5, so Pyxi saw the first
107 controls of 364. Asked once for the whole tree with the properties attached, all 364 came
back in half a second, the same controls in the same order with the same parents.
"""

from __future__ import annotations

import contextlib
import threading
import types

import pytest

from pyxi.screen import uia

TYPES = {50000: "ButtonControl", 50033: "PaneControl", 50020: "TextControl", 50004: "EditControl", 50026: "GroupControl", 50031: "SeparatorControl"}


class Node:
    def __init__(self, kind=50033, name="", rect=(0, 0, 800, 600), kids=(), offscreen=False, auto_id="", password=False, broken=False):
        self.kind, self.name, self.kids, self.offscreen, self.auto_id, self.password, self.broken = kind, name, list(kids), offscreen, auto_id, password, broken
        self.rect = types.SimpleNamespace(left=rect[0], top=rect[1], right=rect[0] + rect[2], bottom=rect[1] + rect[3])

    CachedControlType = property(lambda self: self.kind)
    CachedName = property(lambda self: self.name)
    CachedIsOffscreen = property(lambda self: self.offscreen)
    CachedAutomationId = property(lambda self: self.auto_id)

    @property
    def CachedBoundingRectangle(self):
        if self.broken:
            raise OSError("this control went away")
        return self.rect

    def GetCachedPropertyValue(self, prop):
        return self.password

    def GetCachedChildren(self):
        kids = self.kids
        return types.SimpleNamespace(Length=len(kids), GetElement=lambda i: kids[i]) if kids else None

    def BuildUpdatedCache(self, request):
        return self


class Auto:
    """uiautomation, as far as the one-call read uses it."""

    ControlTypeNames = TYPES

    def __init__(self, root: Node, wait: threading.Event | None = None, fail: Exception | None = None) -> None:
        self.calls = 0
        self.request = types.SimpleNamespace(props=[], AddProperty=lambda p: self.request.props.append(p), TreeScope=None, TreeFilter=None)
        outer = self

        def element_from_handle(hwnd):
            outer.calls += 1
            if wait is not None:
                wait.wait(5)
            if fail is not None:
                raise fail
            return root

        ui = types.SimpleNamespace(CreateCacheRequest=lambda: self.request, ElementFromHandle=element_from_handle, RawViewCondition="raw")
        client = types.SimpleNamespace(instance=lambda: types.SimpleNamespace(IUIAutomation=ui))
        self.uiautomation = types.SimpleNamespace(_AutomationClient=client)

    @contextlib.contextmanager
    def UIAutomationInitializerInThread(self, debug=False):
        yield


@pytest.fixture(autouse=True)
def fresh():
    uia._one_call_off.clear()
    yield
    uia._one_call_off.clear()


def page() -> Node:
    return Node(
        kids=[
            Node(50033, "Toolbar", (0, 0, 800, 40), kids=[Node(50000, "Back", (4, 4, 30, 30), auto_id="back"), Node(50000, "", (40, 4, 30, 30))]),
            Node(50033, "", (0, 40, 800, 560), kids=[
                Node(50020, "Sign in", (20, 60, 100, 20)),
                Node(50004, "Password", (20, 90, 200, 24), password=True),
                Node(50020, "", (20, 120, 100, 20)),  # a label with nothing on it
                Node(50000, "Off the page", (20, 900, 80, 30), offscreen=True),
                Node(50031, "rule", (20, 130, 300, 2)),  # not a kind Pyxi shows
                Node(50026, "", (20, 140, 30, 30), kids=[Node(50000, "Go", (22, 142, 26, 26))]),  # a tiny unnamed group: skipped, its button kept
            ]),
        ]
    )


def test_the_whole_window_comes_from_one_call_in_the_walks_order():
    auto = Auto(page())
    found = uia._cached_elements(auto, 77, 400, 25)
    assert [(e.role, e.name, e.depth, e.parent) for e in found] == [
        ("Pane", "Toolbar", 1, None),
        ("Button", "Back", 2, 0),
        ("Button", "", 2, 0),
        ("Pane", "", 1, None),
        ("Text", "Sign in", 2, 3),
        ("Edit", "Password", 2, 3),
        ("Button", "Go", 3, 5),  # its own group was not kept: the last kept thing above it, as the walk has it
    ]
    assert found[1].rect == (4, 4, 30, 30) and found[1].automation_id == "back"
    assert [e.is_password for e in found] == [False, False, False, False, False, True, False]
    assert auto.calls == 1  # one question to the program, however many controls
    assert auto.request.TreeScope == 7 and auto.request.TreeFilter == "raw"  # the whole tree, the view the walk uses
    assert set(auto.request.props) == {30001, 30003, 30005, 30011, 30019, 30022}


def test_it_stops_at_the_same_limits_as_the_walk():
    assert [e.name for e in uia._cached_elements(Auto(page()), 77, 3, 25)] == ["Toolbar", "Back", ""]
    assert [e.name for e in uia._cached_elements(Auto(page()), 77, 400, 1)] == ["Toolbar", ""]
    assert [e.depth for e in uia._cached_elements(Auto(page()), 77, 400, 2)] == [1, 2, 2, 1, 2, 2]


def test_one_control_that_throws_costs_only_itself():
    root = page()
    root.kids[0].kids[0].broken = True
    assert [e.name for e in uia._cached_elements(Auto(root), 77, 400, 25)] == ["Toolbar", "", "", "Sign in", "Password", "Go"]


def test_a_read_that_comes_back_is_used_and_the_walk_is_not():
    auto = Auto(page())
    assert len(uia.read_in_one_call(auto, 77, 1.5, 400, 25)) == 7
    assert 77 not in uia._one_call_off


def test_a_program_that_refuses_or_has_nothing_is_walked_instead():
    assert uia.read_in_one_call(Auto(page(), fail=OSError("no")), 77, 1.5, 400, 25) is None
    assert uia.read_in_one_call(Auto(Node()), 77, 1.5, 400, 25) is None  # an empty answer is no answer
    assert uia.read_in_one_call(Auto(page()), 0, 1.5, 400, 25) is None  # no window to ask
    assert not uia._one_call_off  # none of that is a reason to stop trying


def test_a_read_that_does_not_come_back_in_time_leaves_that_window_to_the_walk_for_a_while(monkeypatch):
    hold = threading.Event()
    slow = Auto(page(), wait=hold)
    assert uia.read_in_one_call(slow, 77, 0.05, 400, 25) is None
    hold.set()
    quick = Auto(page())
    assert uia.read_in_one_call(quick, 77, 1.5, 400, 25) is None and quick.calls == 0  # not asked again yet
    assert len(uia.read_in_one_call(quick, 78, 1.5, 400, 25)) == 7  # another window is
    now = uia.time.monotonic()
    monkeypatch.setattr(uia.time, "monotonic", lambda: now + 61)
    assert len(uia.read_in_one_call(quick, 77, 1.5, 400, 25)) == 7  # and a minute on, this one again
