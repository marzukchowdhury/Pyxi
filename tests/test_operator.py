"""Pyxi's hands, without touching the real mouse: the loop, and every safety guard."""

import time

import pytest
from otto.types import LLMResponse, ToolCall, Usage
from PIL import Image

from pyxi.companion.operator import CLOSING_KEYS, RISKY, Operator
from pyxi.hands import parse_keys
from pyxi.screen.context import ScreenContext, UIElement, WindowInfo


def screen(color: str = "white") -> ScreenContext:
    ctx = ScreenContext(
        time.time(),
        WindowInfo("Mail", "firefox.exe", (0, 0, 1366, 768)),
        [
            UIElement(1, "Send", "Button", (100, 100, 80, 30)),
            UIElement(2, "Password", "Edit", (100, 200, 200, 30), is_password=True),
            UIElement(3, "Subject", "Edit", (100, 300, 200, 30)),
        ],
        Image.new("RGB", (1366, 768), color),
    )
    ctx.model_scale = 1.0
    return ctx


class PretendHands:
    def __init__(self) -> None:
        self.did: list[tuple] = []
        self.reason = ""

    def interrupted(self) -> bool:
        return False

    def click(self, x, y, button="left", count=1):
        self.did.append(("click", x, y))

    def type(self, text):
        self.did.append(("type", text))

    def press(self, keys):
        self.did.append(("press", keys))


class Scripted:
    """A model that answers with these tool calls, one per look."""

    def __init__(self, *calls: tuple[str, dict]) -> None:
        self.calls = list(calls)

    async def complete(self, **_):
        name, args = self.calls.pop(0) if self.calls else ("done", {"ok": True, "summary": "Done."})
        return LLMResponse(
            text="", tool_calls=[ToolCall(id="c", name=name, arguments=args)], usage=Usage()
        )


def operator(llm, hands_box: list, capture=None, allow: bool = False, asked: list | None = None):
    colors = iter(["white", "black", "red", "blue", "green", "gray", "yellow", "pink"] * 4)

    async def cap():
        return capture() if capture else screen(next(colors))

    async def confirm(action, reason):
        if asked is not None:
            asked.append(action)
        return allow

    async def report(doing, n):
        pass

    def make():
        h = PretendHands()
        hands_box.append(h)
        return h

    return Operator(llm, cap, confirm, report, make_hands=make, aim_llm=llm)


def test_risky_labels_and_closing_keys():
    for label in ["Send", "Place order", "Delete", "Close Tab", "Sign in", "Empty Recycle Bin"]:
        assert RISKY.search(label), label
    for label in ["Close Tables panel", "Got it", "Region option", "zoom reset", "empty cell"]:
        assert not RISKY.search(label), label
    assert {"ctrl+w", "alt+f4"} <= CLOSING_KEYS
    assert parse_keys("ctrl+shift+s") == [0x11, 0x10, 0x53]
    assert parse_keys("playpause") == [0xB3]
    with pytest.raises(ValueError):
        parse_keys("ctrl+banana")


async def test_a_declined_send_is_never_clicked():
    box, asked = [], []
    op = operator(
        Scripted(("act", {"actions": [{"do": "click", "element_id": 1}]})), box, asked=asked
    )
    out = await op.run("send the email")
    assert asked == ["Click “Send”"]
    assert box[0].did == [] and not out.ok


async def test_it_never_types_into_a_password_field():
    box = []
    op = operator(
        Scripted(
            ("act", {"actions": [{"do": "type", "element_id": 2, "text": "hunter2"}]}),
            ("done", {"ok": False, "summary": "They need to type the password."}),
        ),
        box,
    )
    out = await op.run("log in")
    assert not any(d[0] == "type" for d in box[0].did)
    assert "password field" in out.steps[0].note


async def test_it_stops_when_the_screen_stops_changing():
    box = []
    same = screen("white")
    clicks = [("act", {"actions": [{"do": "click", "element_id": 3}]})] * 20
    op = operator(Scripted(*clicks), box, capture=lambda: same)
    out = await op.run("do something")
    assert not out.ok and "stuck" in out.summary
    assert len(out.steps) <= 6  # gave up quickly instead of burning credits


async def test_a_finished_task_reports_what_it_did():
    box = []
    op = operator(
        Scripted(
            (
                "act",
                {
                    "actions": [
                        {"do": "type", "element_id": 3, "text": "Hello"},
                        {"do": "press", "text": "enter"},
                    ]
                },
            ),
            ("done", {"ok": True, "summary": "I typed Hello into the subject."}),
        ),
        box,
    )
    out = await op.run("type hello in the subject")
    assert out.ok and out.summary.startswith("I typed")
    assert ("type", "Hello") in box[0].did and ("press", "enter") in box[0].did


async def test_touching_the_mouse_pauses_it_and_it_carries_on():
    """The user grabbed the mouse mid-task: it waits until they let go, then finishes."""
    from pyxi import hands as hands_mod

    class Grabbed(PretendHands):
        def __init__(self) -> None:
            super().__init__()
            self.waited = 0
            self.grabbed = True

        def type(self, text):
            if self.grabbed:  # the user is on the mouse the first time it tries
                self.grabbed = False
                self.reason = "mouse at (5, 5)"
                raise hands_mod.Interrupted
            super().type(text)

        def wait_idle(self, quiet_s=3.0, timeout_s=300.0):
            self.waited += 1
            return True

    box: list = []
    op = operator(
        Scripted(
            ("act", {"actions": [{"do": "type", "element_id": 3, "text": "Hello"}]}),
            ("act", {"actions": [{"do": "type", "element_id": 3, "text": "Hello"}]}),
        ),
        box,
    )
    op.make_hands = lambda: box.append(Grabbed()) or box[-1]
    out = await op.run("type hello")
    assert box[-1].waited == 1 and ("type", "Hello") in box[-1].did
    assert out.ok and not out.stopped


async def test_going_round_in_circles_is_noticed_and_stopped():
    # What happened merging Firefox windows: right-click a tab, open "Move Tab", Escape, and
    # again on a slightly different spot each time, so no two attempts were exactly the same.
    box = []
    looks = iter(["white", "black", "white", "black", "white", "black", "white"] * 3)
    tries = [
        ("act", {"actions": [{"do": "click", "element_id": 3, "button": "right"}]}),
        ("act", {"actions": [{"do": "press", "keys": "escape"}]}),
        ("act", {"actions": [{"do": "click", "element_id": 3, "button": "right", "why": "again"}]}),
        ("act", {"actions": [{"do": "press", "keys": "escape"}]}),
    ] * 5
    op = operator(Scripted(*tries), box, capture=lambda: screen(next(looks)))
    out = await op.run("move the tabs into the other window")
    assert not out.ok and "circles" in out.summary
    assert len(out.steps) <= 7  # a few laps, not twenty
    assert any("back to how it was" in s.note for s in out.steps)  # and the model was told
    assert op.escalated or op.strong_llm is None


async def test_real_progress_is_not_mistaken_for_circles():
    box = []
    shades = iter(["#ffffff", "#000000", "#808080", "#404040", "#c0c0c0", "#202020", "#e0e0e0"])
    steps = [("act", {"actions": [{"do": "click", "element_id": 3, "n": i}]}) for i in range(5)]
    op = operator(Scripted(*steps), box, capture=lambda: screen(next(shades)))
    out = await op.run("do something")
    assert out.ok and not any("back to how it was" in s.note for s in out.steps)
