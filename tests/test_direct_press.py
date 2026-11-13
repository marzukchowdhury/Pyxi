"""Pressing a control itself, with no mouse: which control, which way, and what the desktop
loop does with the answer. Nothing real is pressed here (a pretend backend and pretend hands)."""

import contextlib
import threading
import time

from otto.types import LLMResponse, ToolCall, Usage
from PIL import Image

from pyxi import hands
from pyxi.companion.operator import Operator
from pyxi.screen import press as presses
from pyxi.screen.context import ScreenContext, UIElement, WindowInfo
from pyxi.screen.press import Live, Result, Target, pick, press, ways

ALL = frozenset({"Invoke", "Toggle", "SelectionItem", "ExpandCollapse", "LegacyIAccessible"})


# ---------------------------------------------------------------- which control
def test_the_only_control_of_that_name_is_it_wherever_it_went():
    target = Target(1, "Button", "Save", (100, 100, 80, 30))
    moved = Live("Save", (100, 400, 80, 30))
    assert pick(target, [Live("Cancel", (200, 100, 80, 30)), moved]) is moved


def test_of_several_with_one_name_it_is_the_one_still_in_place():
    target = Target(1, "Button", "Delete", (500, 200, 60, 24))
    rows = [Live("Delete", (500, 140, 60, 24)), Live("Delete", (502, 201, 60, 24)), Live("Delete", (500, 260, 60, 24))]
    assert pick(target, rows) is rows[1]


def test_several_with_one_name_and_none_in_place_is_nobody():
    target = Target(1, "Button", "Delete", (500, 200, 60, 24))
    rows = [Live("Delete", (500, 600, 60, 24)), Live("Delete", (500, 700, 60, 24))]
    assert pick(target, rows) is None


def test_a_control_out_of_view_or_with_no_size_is_not_pressed():
    target = Target(1, "Button", "Save", (100, 100, 80, 30))
    assert pick(target, [Live("Save", (100, 100, 80, 30), offscreen=True)]) is None
    assert pick(target, [Live("Save", (100, 100, 0, 0))]) is None
    assert pick(Target(1, "Button", "  ", (0, 0, 9, 9)), [Live("", (0, 0, 9, 9))]) is None


def test_a_name_is_matched_as_the_look_kept_it():
    long = "x" * 300
    target = Target(1, "Button", long[:160], (0, 0, 50, 20))
    assert pick(target, [Live(f"  {long} ", (0, 0, 50, 20))]) is not None


# ---------------------------------------------------------------- which way
def test_a_button_is_invoked_and_a_switch_that_only_toggles_is_toggled():
    assert ways("Button", ALL) == ("invoke", "legacy")
    assert ways("Button", frozenset({"Toggle", "LegacyIAccessible"})) == ("toggle",)
    assert ways("CheckBox", ALL) == ("toggle", "legacy")
    assert ways("Hyperlink", frozenset({"Invoke"})) == ("invoke",)


def test_a_row_is_chosen_never_opened():
    # Invoking a file in File Explorer opens it: that is a double click, not a click.
    assert ways("ListItem", ALL) == ("select",)
    assert ways("TreeItem", ALL) == ("select",)
    assert ways("TabItem", ALL) == ("select",)
    assert ways("ListItem", frozenset({"Invoke"})) == ("invoke",)  # a Start menu tile


def test_a_dropdown_opens_and_a_menu_with_a_submenu_opens():
    assert ways("ComboBox", ALL) == ("expand",)
    assert ways("MenuItem", frozenset({"ExpandCollapse", "LegacyIAccessible"})) == ("expand", "legacy")
    assert ways("Button", frozenset()) == ()


# ---------------------------------------------------------------- the press itself
class Pretend:
    """A window with these controls in it. ``do`` records, and can refuse, hang or do nothing."""

    def __init__(self, *found: Live, refuse=(), hang=None, dead=()) -> None:
        self.found, self.refuse, self.hang, self.dead = list(found), set(refuse), hang, set(dead)
        self.did: list[str] = []

    @contextlib.contextmanager
    def thread(self):
        yield

    def find(self, target):
        return self.found

    def do(self, live, way):
        if way in self.refuse:
            raise LookupError(way)
        self.did.append(way)
        if self.hang is not None and way == "invoke":
            self.hang.wait(5)
        if way in self.dead:
            return
        if way == "toggle":
            live.now = not live.on
        if way == "select":
            live.now = True

    def state(self, live, way):
        return getattr(live, "now", live.on)


def test_a_button_is_pressed_where_it_is_now():
    backend = Pretend(Live("Save", (100, 400, 80, 30), patterns=ALL))
    out = press(Target(1, "Button", "Save", (100, 100, 80, 30)), backend=backend)
    assert out == Result("invoke", (100, 400, 80, 30)) and backend.did == ["invoke"]


def test_a_tick_that_flipped_is_sure():
    backend = Pretend(Live("Wi-Fi", (0, 0, 40, 20), patterns=ALL, on=False))
    out = press(Target(1, "CheckBox", "Wi-Fi", (0, 0, 40, 20)), backend=backend)
    assert out.how == "toggle" and out.sure


def test_a_toggle_that_did_not_take_goes_on_to_the_next_way_then_to_the_mouse():
    backend = Pretend(Live("Wi-Fi", (0, 0, 40, 20), patterns=ALL, on=False), dead={"toggle"})
    out = press(Target(1, "CheckBox", "Wi-Fi", (0, 0, 40, 20)), backend=backend)
    assert backend.did == ["toggle", "legacy"] and out.how == "legacy" and not out.sure
    backend = Pretend(Live("Dark", (0, 0, 40, 20), patterns=frozenset({"Toggle"}), on=False), dead={"toggle"})
    out = press(Target(1, "Button", "Dark", (0, 0, 40, 20)), backend=backend)
    assert not out.how and out.why == "did not take" and out.rect == (0, 0, 40, 20)


def test_a_way_the_control_refuses_is_followed_by_the_next():
    backend = Pretend(Live("Save", (0, 0, 80, 30), patterns=ALL), refuse={"invoke"})
    assert press(Target(1, "Button", "Save", (0, 0, 80, 30)), backend=backend).how == "legacy"


def test_greyed_out_chosen_and_no_way_are_found_but_not_pressed():
    grey = Pretend(Live("Save", (5, 5, 80, 30), patterns=ALL, enabled=False))
    out = press(Target(1, "Button", "Save", (0, 0, 80, 30)), backend=grey)
    assert (out.how, out.why, out.rect, grey.did) == ("", "disabled", (5, 5, 80, 30), [])
    chosen = Pretend(Live("General", (0, 0, 80, 30), patterns=ALL, on=True))
    assert press(Target(1, "TabItem", "General", (0, 0, 80, 30)), backend=chosen).why == "chosen"
    bare = Pretend(Live("Logo", (0, 0, 80, 30)))
    assert press(Target(1, "Button", "Logo", (0, 0, 80, 30)), backend=bare).why == "no way"


def test_what_is_not_there_or_not_pressable_is_nobodys():
    assert press(Target(1, "Button", "Save", (0, 0, 80, 30)), backend=Pretend()) is None
    edit = Pretend(Live("Name", (0, 0, 80, 30), patterns=ALL))
    assert press(Target(1, "Edit", "Name", (0, 0, 80, 30)), backend=edit) is None
    assert press(Target(0, "Button", "Save", (0, 0, 80, 30))) is None  # no window: not even looked for


def test_a_press_that_does_not_come_back_was_a_press_and_is_never_done_again():
    # A button that opens a dialog: older apps answer the press only when the dialog closes.
    gate = threading.Event()
    backend = Pretend(Live("Save As", (0, 0, 80, 30), patterns=ALL), hang=gate)
    started = time.monotonic()
    out = press(Target(1, "Button", "Save As", (0, 0, 80, 30)), timeout_s=0.3, backend=backend)
    assert out.how == "invoke" and out.late and time.monotonic() - started < 2
    gate.set()  # the dialog is closed: the old press comes back
    time.sleep(0.3)
    assert backend.did == ["invoke"]  # and nothing more was pressed


def test_a_search_that_takes_too_long_presses_nothing_later():
    class Slow(Pretend):
        def find(self, target):
            time.sleep(0.5)
            return self.found

    backend = Slow(Live("Save", (0, 0, 80, 30), patterns=ALL))
    assert press(Target(1, "Button", "Save", (0, 0, 80, 30)), timeout_s=0.1, backend=backend) is None
    time.sleep(0.7)
    assert backend.did == []


def test_a_backend_that_throws_is_nobodys():
    class Broken(Pretend):
        def find(self, target):
            raise OSError("the app went away")

    assert press(Target(1, "Button", "Save", (0, 0, 80, 30)), backend=Broken()) is None


# ---------------------------------------------------------------- in the desktop loop
def screen(color: str = "white") -> ScreenContext:
    ctx = ScreenContext(
        time.time(),
        WindowInfo("Settings", "SystemSettings.exe", (0, 0, 1366, 768), hwnd=77),
        [
            UIElement(1, "Bluetooth", "Button", (900, 100, 60, 30)),
            UIElement(2, "Send", "Button", (100, 100, 80, 30)),
            UIElement(3, "Device name", "Edit", (100, 300, 200, 30)),
            UIElement(4, "report.docx", "ListItem", (100, 400, 400, 30)),
        ],
        Image.new("RGB", (1366, 768), color),
    )
    ctx.model_scale = 1.0
    return ctx


class PretendHands:
    def __init__(self) -> None:
        self.did: list[tuple] = []
        self.reason = ""
        self.touched = False

    def interrupted(self) -> bool:
        return self.touched

    def click(self, x, y, button="left", count=1):
        self.did.append(("click", x, y))

    def type(self, text):
        self.did.append(("type", text))


class Scripted:
    def __init__(self, *calls: tuple[str, dict]) -> None:
        self.calls = list(calls)

    async def complete(self, **_):
        name, args = self.calls.pop(0) if self.calls else ("done", {"ok": True, "summary": "Done."})
        return LLMResponse(text="", tool_calls=[ToolCall(id="c", name=name, arguments=args)], usage=Usage())


def operator(llm, box: list, answers, capture=None, allow=True, asked=None):
    """``answers``: what the pretend press says for each control, by name."""
    colors = iter(["white", "black", "red", "blue", "green", "gray"] * 4)
    targets: list[Target] = []

    async def cap():
        return capture() if capture else screen(next(colors))

    async def confirm(action, reason):
        if asked is not None:
            asked.append(action)
        return allow

    async def report(doing, n):
        pass

    def make():
        box.append(PretendHands())
        return box[-1]

    def pretend_press(target: Target):
        targets.append(target)
        return answers.get(target.name)

    op = Operator(llm, cap, confirm, report, make_hands=make, aim_llm=llm, press=pretend_press)
    op.targets = targets
    return op


def click(element_id: int, **more) -> tuple[str, dict]:
    return "act", {"actions": [{"do": "click", "element_id": element_id, **more}]}


async def test_a_button_is_pressed_without_the_mouse():
    box = []
    op = operator(Scripted(click(1)), box, {"Bluetooth": Result("toggle", (900, 100, 60, 30), sure=True)})
    out = await op.run("turn bluetooth on")
    assert out.ok and box[0].did == []
    assert op.targets == [Target(77, "Button", "Bluetooth", (900, 100, 60, 30))]
    assert out.steps[0].actions == "click Bluetooth" and hands.recently_acted()


async def test_with_pretend_hands_and_no_press_every_click_is_a_click():
    box = []

    def make():
        box.append(PretendHands())
        return box[-1]

    async def cap():
        return screen()

    async def yes(*_):
        return True

    async def quiet(*_):
        pass

    op = Operator(Scripted(click(1)), cap, yes, quiet, make_hands=make)
    assert op.press is None
    await op.run("turn bluetooth on")
    assert box[0].did == [("click", 930, 115)]


async def test_what_was_not_found_live_is_clicked_as_before():
    box = []
    op = operator(Scripted(click(1)), box, {})
    await op.run("turn bluetooth on")
    assert box[0].did == [("click", 930, 115)]


async def test_a_control_that_moved_and_cannot_be_pressed_is_clicked_where_it_is_now():
    box = []
    op = operator(Scripted(click(1)), box, {"Bluetooth": Result(rect=(900, 300, 60, 30), why="no way")})
    await op.run("turn bluetooth on")
    assert box[0].did == [("click", 930, 315)]


async def test_a_field_or_an_ocr_line_or_a_double_click_is_never_pressed():
    box = []
    sure = Result("invoke", (0, 0, 9, 9))
    op = operator(
        Scripted(
            click(3),
            ("act", {"actions": [{"do": "double_click", "element_id": 4}]}),
            ("act", {"actions": [{"do": "right_click", "element_id": 1}]}),
            click(4, quote="report.docx"),
        ),
        box,
        {"Device name": sure, "report.docx": sure, "Bluetooth": sure},
    )
    await op.run("open the report")
    assert op.targets == [] and len(box[0].did) == 4


async def test_the_card_comes_before_a_press_and_a_no_presses_nothing():
    box, asked = [], []
    op = operator(Scripted(click(2)), box, {"Send": Result("invoke", (100, 100, 80, 30))}, allow=False, asked=asked)
    out = await op.run("send the email")
    assert asked == ["Click “Send”"] and op.targets == [] and box[0].did == [] and not out.ok


async def test_greyed_out_is_said_once_then_clicked_if_asked_again():
    box = []
    grey = Result(rect=(900, 100, 60, 30), why="disabled")
    op = operator(Scripted(click(1), click(1)), box, {"Bluetooth": grey})
    out = await op.run("turn bluetooth on")
    assert "greyed out" in out.steps[0].note and out.steps[0].note.startswith("skipped")
    assert box[0].did == [("click", 930, 115)]  # the second time


async def test_a_press_that_shows_nothing_is_a_click_the_next_time():
    box = []
    same = screen("white")
    op = operator(Scripted(click(1), click(1)), box, {"Bluetooth": Result("invoke", (900, 100, 60, 30))}, capture=lambda: same)
    out = await op.run("turn bluetooth on")
    assert len(op.targets) == 1  # pressed once
    assert "without the mouse" in out.steps[0].note
    assert box[0].did == [("click", 930, 115)]  # then clicked


async def test_a_press_that_was_sure_is_not_doubted_when_the_picture_barely_changes():
    # A small tick in a big window changes too little for the look to see.
    box = []
    same = screen("white")
    op = operator(
        Scripted(click(1), ("done", {"ok": True, "summary": "Bluetooth is on."})),
        box,
        {"Bluetooth": Result("toggle", (900, 100, 60, 30), sure=True)},
        capture=lambda: same,
    )
    out = await op.run("turn bluetooth on")
    assert out.ok and "without the mouse" not in out.steps[0].note and box[0].did == []


async def test_touching_the_mouse_stops_a_press_too():
    box = []
    op = operator(Scripted(click(1)), box, {"Bluetooth": Result("invoke", (900, 100, 60, 30))})

    async def cap():
        box[-1].touched = True  # they grab the mouse right after the look
        return screen()

    op.capture = cap
    out = await op.run("turn bluetooth on")
    assert out.stopped and op.targets == []


def test_the_real_press_is_only_for_the_window_in_front_and_never_pyxis_own(monkeypatch):
    monkeypatch.setattr(presses.sys, "platform", "win32")
    monkeypatch.setattr(hands, "foreground_window", lambda: 5)
    monkeypatch.setattr(hands, "is_own", lambda hwnd: hwnd == 6)
    assert not presses.usable(0)
    assert not presses.usable(4)  # behind another window: keys typed next would go astray
    monkeypatch.setattr(hands, "foreground_window", lambda: 6)
    assert not presses.usable(6)  # one of Pyxi's own cards
