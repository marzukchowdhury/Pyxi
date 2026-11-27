"""Doing a thing to a control itself (screen/control.py), and what the desktop loop does with
it: a field set and read back, a row chosen by name however far down, a switch set, a menu
walked by its words, a control found by name after the screen changed. Nothing real is
touched here: a pretend window and pretend hands."""

import contextlib
import threading
import time
from types import SimpleNamespace

from otto.types import LLMResponse, ToolCall, Usage
from PIL import Image

from pyxi.companion.operator import ACT, SYSTEM, Operator, _action_key, _say_action
from pyxi.screen import uia
from pyxi.screen.context import ScreenContext, ScreenReader, UIElement, WindowInfo, _echoes
from pyxi.screen.control import Aim, Controls, Done, Live, choose, closeness, describe, norm
from pyxi.screen.ocr import TextLine
from pyxi.screen.press import Result

FIELD = frozenset({"Value"})
TICK = frozenset({"Toggle"})
ROW = frozenset({"SelectionItem", "ScrollItem"})
OPENS = frozenset({"ExpandCollapse"})
PRESS = frozenset({"Invoke"})


# ---------------------------------------------------------------- which control is meant
def test_a_name_is_compared_without_its_shortcut_its_dots_or_its_underline():
    assert norm("Save &as...\tCtrl+Shift+S") == "save as"
    assert norm("  Replace   all… ") == "replace all"
    assert closeness("save as", "Save As...") == 0
    assert closeness("report_217", "report_217.txt") == 1
    assert closeness("farouk", "Nadia Farouk") == 2
    assert closeness("save", "Open") is None and closeness("", "Open") is None


def test_the_closest_name_wins_and_the_kind_asked_for_comes_first():
    exact, longer = Live("Replace", "Button", (0, 0, 9, 9)), Live("Replace all", "Button", (0, 20, 9, 9))
    assert choose(Aim(1, "replace"), [longer, exact]) == (exact, 0)
    field, button = Live("Find", "Edit", (0, 0, 9, 9)), Live("Find", "Button", (0, 20, 9, 9))
    assert choose(Aim(1, "Find", "Button"), [field, button])[0] is button
    assert choose(Aim(1, "Find"), [field, button], lambda f: f.role == "Edit") == (field, 0)
    assert choose(Aim(1, "Nothing"), [field, button]) == (None, 0)


def test_one_that_can_be_used_comes_before_one_greyed_out_or_out_of_view_and_a_label_comes_last():
    grey = Live("Save", "Button", (0, 0, 9, 9), enabled=False)
    away = Live("Save", "Button", (0, 0, 9, 9), offscreen=True)
    good = Live("Save", "Button", (0, 0, 9, 9))
    assert choose(Aim(1, "Save"), [grey, away, good]) == (good, 2)
    label, box = Live("File name:", "Text", (0, 0, 9, 9)), Live("File name:", "Edit", (50, 0, 9, 9))
    assert choose(Aim(1, "File name:"), [label, box])[0] is box


def test_of_several_alike_it_is_the_one_nearest_to_where_the_look_saw_it():
    rows = [Live("Delete", "Button", (500, 140, 60, 24)), Live("Delete", "Button", (500, 200, 60, 24))]
    assert choose(Aim(1, "Delete", "Button", (502, 203, 60, 24)), rows)[0] is rows[1]


def test_a_field_with_no_name_is_the_one_of_its_kind_still_in_place():
    a, b = Live("", "Edit", (10, 10, 200, 20), automation_id="first"), Live("", "Edit", (10, 400, 200, 20))
    assert choose(Aim(1, "", "Edit", (12, 11, 200, 20)), [a, b])[0] is a
    assert choose(Aim(1, "", "Edit", (10, 800, 200, 20)), [a, b]) == (None, 0)
    assert choose(Aim(1, "", "Edit"), [a, b]) == (None, 0)  # no name and no place: nobody


# ---------------------------------------------------------------- a pretend window
class Window:
    """Controls that answer as an app would. ``later``: they are not there for this long."""

    def __init__(self, *found: Live, front=True, later=0.0, dead=(), hang=None, virtual=(), refuse=()) -> None:
        self.found, self.front, self.dead, self.hang = list(found), front, set(dead), hang
        self.virtual, self.refuse = list(virtual), set(refuse)
        self.did: list[tuple[str, str]] = []
        self.at = time.monotonic() + later
        self.searches = 0
        self.behind = False  # a dialog has come up in front of these controls

    @contextlib.contextmanager
    def thread(self):
        yield

    def roots(self, hwnd):
        return [hwnd] if self.front else []

    def search(self, roots, name, role=""):
        self.searches += 1
        if time.monotonic() < self.at:
            return []
        if name:
            return [f for f in self.found if closeness(name, f.name) is not None]
        return [f for f in self.found if f.role == role]

    def refresh(self, live):
        return live

    def do(self, live, way):
        if way in self.refuse:
            raise LookupError(way)
        self.did.append((live.name, way))
        if self.hang is not None and way == "invoke":
            self.hang.wait(5)
        if way in self.dead:
            return
        if way == "toggle":
            live.on = not live.on
        elif way == "select":
            live.on = True
        elif way in ("expand", "collapse"):
            live.open = way == "expand"
        elif way == "into_view":
            live.offscreen = False

    def set_value(self, live, text):
        self.did.append((live.name, "value"))
        if "value" not in self.dead:
            live.value = text

    def text(self, live, most):
        return "the whole of the document"[:most]

    def realize(self, roots, name):
        made = next((v for v in self.virtual if norm(v.name) == norm(name)), None)
        if made is not None:
            self.did.append((made.name, "made"))
        return made

    def in_front(self, hwnd):
        return ""

    def blocked(self, live):
        return self.behind


def controls(*found: Live, **how) -> tuple[Controls, Window]:
    window = Window(*found, **how)
    return Controls(backend=window), window


# ---------------------------------------------------------------- a field's value
def test_a_field_is_set_in_one_step_and_says_what_it_now_holds():
    c, w = controls(Live("File name:", "Text", (0, 0, 9, 9), patterns=FIELD, read_only=True),
                    Live("File name:", "Edit", (50, 0, 200, 20), patterns=FIELD, value="notes.txt"))  # fmt: skip
    out = c.set_value(Aim(1, "File name:"), "C:\\made up\\copy.txt")
    assert out.ok and out.sure and out.now == "C:\\made up\\copy.txt" and out.role == "Edit"
    assert w.did == [("File name:", "focus"), ("File name:", "value")]  # the field, not its label


def test_a_password_field_is_never_written_and_never_read():
    secret = Live("Password", "Edit", (0, 0, 200, 20), patterns=FIELD, password=True)
    c, w = controls(secret)
    assert c.set_value(Aim(1, "Password"), "hunter2").why == "password" and w.did == []
    assert c.read(Aim(1, "Password")).why == "password"
    assert "hunter2" not in describe(Live("Password", "Edit", (0, 0, 9, 9), value="hunter2", password=True))


def test_a_field_that_cannot_be_set_that_way_says_so_and_nothing_is_done():
    c, w = controls(Live("Notes", "Edit", (0, 0, 200, 20), patterns=FIELD, read_only=True))
    assert c.set_value(Aim(1, "Notes"), "x").why == "no way"
    c, w2 = controls(Live("Notes", "Edit", (0, 0, 200, 20), patterns=FIELD, enabled=False))
    assert c.set_value(Aim(1, "Notes"), "x").why == "disabled"
    assert w.did == [] and w2.did == []


def test_typing_never_replaces_what_a_field_already_holds():
    c, w = controls(Live("Notes", "Edit", (0, 0, 200, 20), patterns=FIELD, value="Dear Rosa,"))
    assert c.set_value(Aim(1, "Notes"), " thank you", if_empty=True).why == "not empty" and w.did == []
    c, w = controls(Live("Notes", "Edit", (0, 0, 200, 20), patterns=FIELD, value=""))
    assert c.set_value(Aim(1, "Notes"), "Dear Rosa,", if_empty=True).ok


def test_a_value_that_did_not_take_is_not_called_done():
    c, _ = controls(Live("Name Box", "Edit", (0, 0, 80, 20), patterns=FIELD, value="A1"), dead={"value"})
    out = c.set_value(Aim(1, "Name Box"), "A1:C6")
    assert not out.ok and out.why == "did not take" and out.now == "A1"


def test_nothing_is_done_in_a_window_that_is_not_in_front():
    c, w = controls(Live("Name", "Edit", (0, 0, 80, 20), patterns=FIELD), front=False)
    assert c.set_value(Aim(1, "Name"), "x").why == "not in front"
    assert c.click(Aim(1, "Name")) is None and c.find(1, "Name").why == "not in front"
    assert c.menu(1, ["File", "Save"]).why == "not in front" and w.did == []


def test_a_control_that_is_not_there_yet_is_waited_for():
    c, w = controls(Live("File name:", "Edit", (0, 0, 200, 20), patterns=FIELD), later=0.35)
    assert c.set_value(Aim(1, "File name:"), "x").why == "not found"  # asked for at once
    c, w = controls(Live("File name:", "Edit", (0, 0, 200, 20), patterns=FIELD), later=0.35)
    assert c.set_value(Aim(1, "File name:"), "x", wait_s=2.0).ok and w.searches > 2


# ---------------------------------------------------------------- switches, dropdowns
def test_a_switch_is_set_only_if_it_is_not_so_already():
    c, w = controls(Live("Word wrap", "CheckBox", (0, 0, 80, 20), patterns=TICK, on=True))
    out = c.toggle(Aim(1, "Word wrap"), True)
    assert out.ok and out.how == "already" and out.now == "on" and w.did == []
    out = c.toggle(Aim(1, "Word wrap"), False)
    assert out.ok and out.how == "toggle" and out.now == "off" and w.did == [("Word wrap", "toggle")]


def test_a_switch_that_does_not_move_is_not_called_done():
    c, w = controls(Live("Dark mode", "Button", (0, 0, 80, 20), patterns=TICK, on=False), dead={"toggle"})
    out = c.toggle(Aim(1, "Dark mode"), True)
    assert not out.ok and out.why == "did not take" and len(w.did) == 2
    c, _ = controls(Live("Logo", "Button", (0, 0, 80, 20)))
    assert c.toggle(Aim(1, "Logo"), True).why == "no way"


def test_a_dropdown_is_opened_and_closed_and_left_alone_when_it_already_is():
    box = Live("Billing cycle", "ComboBox", (0, 0, 80, 20), patterns=OPENS, open=False)
    c, w = controls(box)
    assert c.expand(Aim(1, "Billing cycle")).now == "open"
    assert c.expand(Aim(1, "Billing cycle")).how == "already"
    assert c.expand(Aim(1, "Billing cycle"), open_=False).now == "closed"
    assert w.did == [("Billing cycle", "expand"), ("Billing cycle", "collapse")]


# ---------------------------------------------------------------- rows and options
def test_a_row_out_of_view_is_brought_into_view_and_chosen_with_no_scrolling():
    rows = [Live(f"report_{i:03d}.txt", "ListItem", (0, 20 * i, 300, 20), patterns=ROW, offscreen=i > 30, on=False) for i in range(300)]
    c, w = controls(*rows)
    out = c.select(Aim(1), "report_217.txt")
    assert out.ok and out.sure and out.name == "report_217.txt" and not out.offscreen
    assert w.did == [("report_217.txt", "into_view"), ("report_217.txt", "select")]


def test_a_row_its_list_has_not_made_yet_is_asked_for_by_name():
    # A list of thousands only makes the rows it shows: the row is not in the tree at all.
    there = [Live("Vera Drummond", "ListItem", (0, 0, 300, 20), patterns=ROW, on=False)]
    far = Live("Nadia Farouk", "ListItem", (0, 9999, 300, 20), patterns=ROW, offscreen=True, on=False)
    c, w = controls(*there, virtual=[far])
    out = c.select(Aim(1), "Nadia Farouk")
    assert out.ok and out.name == "Nadia Farouk"
    assert w.did == [("Nadia Farouk", "made"), ("Nadia Farouk", "into_view"), ("Nadia Farouk", "select")]


def test_a_file_is_found_with_or_without_the_ending_the_folder_hides():
    c, _ = controls(Live("report_217", "ListItem", (0, 0, 300, 20), patterns=ROW, on=False))
    assert c.select(Aim(1), "report_217.txt").name == "report_217"


def test_an_option_in_a_dropdown_is_chosen_and_the_dropdown_closed_again():
    box = Live("Billing cycle", "ComboBox", (0, 0, 200, 20), patterns=OPENS, open=False)
    option = Live("Quarterly", "ListItem", (0, 400, 200, 20), patterns=ROW, on=False)
    c, w = controls(box, option)
    out = c.select(Aim(1, "Billing cycle"), "Quarterly")
    assert out.ok and out.name == "Quarterly"
    assert w.did == [("Quarterly", "select")]  # with the dropdown shut: nothing flashes open
    # One whose options can only be pressed once it is open: opened, pressed, closed again.
    box = Live("Billing cycle", "ComboBox", (0, 0, 200, 20), patterns=OPENS, open=False)
    c, w = controls(box, Live("Quarterly", "ListItem", (0, 400, 200, 20), patterns=PRESS))
    assert c.select(Aim(1, "Billing cycle"), "Quarterly").ok
    assert w.did == [("Billing cycle", "expand"), ("Quarterly", "invoke"), ("Billing cycle", "collapse")]


def test_a_row_that_is_not_there_is_said_and_a_dropdown_opened_for_it_is_closed():
    box = Live("Billing cycle", "ComboBox", (0, 0, 200, 20), patterns=OPENS, open=False)
    c, w = controls(box)
    out = c.select(Aim(1, "Billing cycle"), "Hourly")
    assert not out.ok and out.why == "no such row"
    assert w.did == [("Billing cycle", "expand"), ("Billing cycle", "collapse")]


def test_the_row_that_can_be_chosen_comes_before_a_stand_in_of_the_same_name():
    # Live: an open dropdown listed "Quarterly" three times; two could not be chosen, and
    # one of those said it was in view at a place where another row was.
    ghost = Live("Quarterly", "ListItem", (0, 300, 200, 20), patterns=frozenset({"LegacyIAccessible"}))
    real = Live("Quarterly", "ListItem", (0, 900, 200, 20), patterns=ROW, offscreen=True, on=False)
    c, w = controls(ghost, real)
    assert c.select(Aim(1), "Quarterly").ok and w.did == [("Quarterly", "into_view"), ("Quarterly", "select")]
    c, w = controls(ghost)
    out = c.select(Aim(1), "Quarterly")
    assert out.why == "no way" and out.rect is None  # nowhere to click on its say-so


def test_a_field_is_found_by_words_that_have_its_whole_name_in_them_but_a_button_never_is():
    c, w = controls(Live("Find", "Edit", (0, 0, 200, 20), patterns=FIELD), Live("Find next", "Button", (0, 30, 80, 20), patterns=PRESS),
                    Live("Save", "Button", (0, 60, 80, 20), patterns=PRESS))  # fmt: skip
    assert c.set_value(Aim(1, "Find what"), "colour").name == "Find"
    assert c.click(Aim(1, "Save as")) is None and ("Save", "invoke") not in w.did
    assert closeness("File name box", "File name:", loose=True) == 3 and closeness("Refind", "Find", loose=True) is None


def test_a_row_that_only_a_click_chooses_is_brought_into_view_and_left_to_the_mouse():
    c, _ = controls(Live("Open now", "ListItem", (0, 900, 300, 20), patterns=frozenset({"ScrollItem"}), offscreen=True))
    out = c.select(Aim(1), "Open now")
    assert not out.ok and out.why == "no way" and out.rect == (0, 900, 300, 20) and not out.offscreen


def test_a_named_control_is_brought_into_view():
    c, w = controls(Live("Recovery", "ListItem", (0, 2000, 300, 20), patterns=ROW, offscreen=True))
    assert c.scroll_to(Aim(1, "Recovery")).now == "in view" and w.did == [("Recovery", "into_view")]
    assert c.scroll_to(Aim(1, "Recovery")).how == "already"


# ---------------------------------------------------------------- menus
def menu_window(**how):
    return controls(
        Live("File", "MenuItem", (0, 0, 40, 20), patterns=OPENS | PRESS, open=False),
        Live("Export", "MenuItem", (0, 20, 80, 20), patterns=OPENS, open=False),
        Live("Reload", "MenuItem", (0, 40, 80, 20), patterns=PRESS),
        Live("As CSV", "MenuItem", (80, 20, 80, 20), patterns=PRESS),
        Live("Print", "MenuItem", (0, 60, 80, 20), patterns=PRESS, enabled=False),
        **how,
    )


def test_a_menu_is_walked_by_its_words_each_opened_and_the_last_pressed():
    c, w = menu_window()
    out = c.menu(1, ["File", "Export", "As CSV"])
    assert out.ok and w.did == [("File", "expand"), ("Export", "expand"), ("As CSV", "invoke")]


def test_a_word_that_is_not_in_the_menu_says_what_the_menu_holds_and_how_far_it_got():
    c, w = menu_window()
    out = c.menu(1, ["File", "Exprt", "As CSV"], wait_s=0.2)
    assert not out.ok and out.name == "Exprt" and out.steps == 1
    assert {"Export", "Reload"} <= set(out.found) and w.did == [("File", "expand")]


def test_a_menu_that_closed_itself_is_not_counted_as_one_to_close():
    c, w = menu_window()
    inner = w.do

    def do(live, way):
        inner(live, way)
        live.open = False  # it opens and shuts again at once

    w.do = do
    out = c.menu(1, ["File", "Exprt"], wait_s=0.2)
    assert not out.ok and out.steps == 0  # so no Esc is sent to the app for it


def test_what_is_behind_a_dialog_that_came_up_is_left_alone():
    c, w = controls(Live("Contact name", "Edit", (0, 0, 200, 20), patterns=FIELD),
                    Live("Apply", "Button", (0, 40, 80, 20), patterns=PRESS))  # fmt: skip
    w.behind = True
    assert c.set_value(Aim(1, "Contact name"), "x").why == "behind a dialog"
    assert c.click(Aim(1, "Apply")) is None and w.did == []


def test_a_greyed_out_menu_item_is_not_pressed():
    c, w = menu_window()
    out = c.menu(1, ["File", "Print"])
    assert out.why == "disabled" and out.steps == 1 and ("Print", "invoke") not in w.did


def test_a_menu_item_that_opens_a_dialog_and_does_not_come_back_was_done_once():
    gate = threading.Event()
    c, w = controls(Live("Save as", "MenuItem", (0, 0, 80, 20), patterns=PRESS), hang=gate)
    c._run_timeout = None
    started = time.monotonic()
    out = c._run(lambda box: (box.begin("menu", w.found[0]), w.do(w.found[0], "invoke"), Done(ok=True))[2], 0.3)
    assert out.ok and out.late and time.monotonic() - started < 2
    gate.set()
    time.sleep(0.2)
    assert w.did == [("Save as", "invoke")]


# ---------------------------------------------------------------- pressing by name, finding, reading
def test_a_control_is_pressed_by_its_name_and_a_tick_confirms_itself():
    c, _ = controls(Live("Replace all", "Button", (10, 10, 80, 20), patterns=PRESS),
                    Live("Match case", "CheckBox", (10, 40, 80, 20), patterns=TICK, on=False))  # fmt: skip
    assert c.click(Aim(1, "Replace all")) == Result("invoke", (10, 10, 80, 20))
    out = c.click(Aim(1, "Match case"))
    assert out.how == "toggle" and out.sure
    assert c.click(Aim(1, "No such button")) is None


def test_a_press_by_name_waits_for_a_dialog_that_is_still_opening():
    c, _ = controls(Live("OK", "Button", (10, 10, 80, 20), patterns=PRESS), later=0.3)
    assert c.click(Aim(1, "OK"), wait_s=2.0).how == "invoke"


def test_what_can_not_be_pressed_that_way_is_found_and_left_to_the_mouse():
    c, _ = controls(Live("Canvas", "Pane", (0, 0, 800, 600)), Live("Save", "Button", (0, 0, 80, 20), patterns=PRESS, enabled=False))
    out = c.click(Aim(1, "Canvas"))
    assert out.how == "" and out.why == "no way" and out.rect == (0, 0, 800, 600)
    assert c.click(Aim(1, "Save")).why == "disabled"


def test_find_lists_what_has_the_words_in_view_or_not_and_what_each_says():
    c, _ = controls(
        Live("Nadia Farouk", "ListItem", (0, 0, 9, 9), patterns=ROW, offscreen=True),
        Live("Omar Farouk", "ListItem", (0, 0, 9, 9), patterns=ROW, on=True),
        Live("Farouk filter", "Edit", (0, 0, 9, 9), patterns=FIELD, value="abc"),
        Live("Zane Upton", "ListItem", (0, 0, 9, 9)),
    )
    out = c.find(1, "farouk")
    assert out.ok and len(out.found) == 3
    assert 'ListItem "Nadia Farouk" (out of view)' in out.found
    assert 'ListItem "Omar Farouk" (chosen)' in out.found
    assert "Edit \"Farouk filter\" (reads 'abc')" in out.found
    assert c.find(1, "nobody").why == "not found"


def test_read_says_what_a_control_says_now():
    c, _ = controls(Live("Scale", "ComboBox", (0, 0, 9, 9), patterns=FIELD | OPENS, value="125%", open=False),
                    Live("Text editor", "Document", (0, 0, 9, 9), patterns=frozenset({"Text"})))  # fmt: skip
    assert c.read(Aim(1, "Scale")).now == "reads '125%', closed"
    assert c.read(Aim(1, "Text editor")).now == "the whole of the document"


def test_a_key_or_a_token_in_a_field_is_not_passed_on():
    key = "sk-ant-api03-" + "a" * 40
    c, _ = controls(Live("Token", "Edit", (0, 0, 9, 9), patterns=FIELD, value=key))
    assert key not in c.read(Aim(1, "Token")).now and "hidden" in c.read(Aim(1, "Token")).now
    assert key not in " ".join(c.find(1, "Token").found)


def test_waiting_for_a_control_to_come_or_to_go():
    c, _ = controls(Live("Save as", "Button", (0, 0, 9, 9)), later=0.3)
    assert c.wait_for(Aim(1, "Save as"), 2.0).ok
    assert c.wait_for(Aim(1, "Never"), 0.3).why == "not found"
    assert c.wait_for(Aim(1, "Never"), 0.3, gone=True).ok


def test_a_window_that_throws_is_an_answer_never_an_error():
    class Broken(Window):
        def search(self, roots, name, role=""):
            raise OSError("the app went away")

    c = Controls(backend=Broken())
    assert c.set_value(Aim(1, "x"), "y").why == "failed" and c.click(Aim(1, "x")) is None
    assert c.select(Aim(1), "x").why == "failed" and c.find(1, "x").why == "failed"


# ---------------------------------------------------------------- what the hands are shown
def test_the_hands_are_shown_what_each_control_says_and_nobody_else_is():
    ctx = ScreenContext(
        time.time(),
        WindowInfo("Bench form", "form.exe", (0, 0, 800, 600), hwnd=5),
        [
            UIElement(1, "Contact name", "Edit", (10, 10, 200, 20), value="Rosa Lindqvist"),
            UIElement(2, "Monthly newsletter", "CheckBox", (10, 40, 200, 20), on=False),
            UIElement(3, "Billing cycle", "ComboBox", (10, 70, 200, 20), value="Quarterly", open=False),
            UIElement(4, "Apply", "Button", (10, 100, 80, 20), enabled=False),
            UIElement(5, "Customers", "List", (300, 10, 300, 500), more=4),
            UIElement(6, "Nadia Farouk", "ListItem", (300, 10, 300, 20), parent=5, chosen=True),
            UIElement(7, "Password", "Edit", (10, 130, 200, 20), is_password=True, value="never shown"),
        ],
        text=[TextLine("Apply", (30, 102, 40, 16), 0.9, id=8), TextLine("Nothing applied yet", (10, 160, 200, 16), 0.9, id=9)],
    )
    plain, shown = ctx.render(120), ctx.render(120, hands=True)
    assert '"Contact name" @(10,10,200x20) = "Rosa Lindqvist"' in shown
    assert '"Monthly newsletter" @(10,40,200x20) (off)' in shown
    assert '= "Quarterly" (closed)' in shown and "(greyed out)" in shown and "(chosen)" in shown
    assert "more rows out of view" in shown and "never scroll" in shown
    assert "never shown" not in shown and "never shown" not in plain
    assert "Rosa Lindqvist" not in plain and "(off)" not in plain  # the chat's look is as it was
    assert '[8] "Apply"' in plain and '[8] "Apply"' not in shown  # the button's own words, said once
    assert '[9] "Nothing applied yet"' in shown


def test_a_label_inside_the_control_it_names_is_not_listed_for_the_hands():
    raw = [
        uia.RawElement("Customers", "List", (0, 0, 300, 500), 1, None, False, ""),
        uia.RawElement("Nadia Farouk", "ListItem", (0, 0, 300, 20), 2, 0, False, ""),
        uia.RawElement("Nadia Farouk", "Text", (4, 2, 90, 16), 3, 1, False, ""),
        uia.RawElement("", "Image", (0, 0, 16, 16), 3, 1, False, ""),
        uia.RawElement("12 November", "Text", (200, 2, 90, 16), 3, 1, False, ""),  # another column: kept
        uia.RawElement("Apply", "Button", (0, 510, 80, 20), 1, None, False, ""),
        uia.RawElement("Apply", "Text", (20, 512, 40, 16), 2, 5, False, ""),
        uia.RawElement("Nothing applied yet", "Text", (0, 540, 200, 16), 1, None, False, ""),
    ]
    assert _echoes(raw) == {2, 3, 6}
    reader = ScreenReader.__new__(ScreenReader)
    reader.config, reader._next_id = SimpleNamespace(max_elements=70), 1
    assert len(reader._number(raw)) == 8  # every other look lists them as it always did
    kept = reader._number(raw, hands=True)
    assert [e.name for e in kept] == ["Customers", "Nadia Farouk", "12 November", "Apply", "Nothing applied yet"]
    assert kept[2].parent == kept[1].id


def test_a_value_that_looks_like_a_key_or_is_a_passwords_is_never_kept():
    raw = [
        uia.RawElement("Token", "Edit", (0, 0, 300, 20), 1, None, False, "", value="sk-ant-api03-" + "a" * 40),
        uia.RawElement("Password", "Edit", (0, 30, 300, 20), 1, None, True, "", value="hunter2"),
        uia.RawElement("City", "Edit", (0, 60, 300, 20), 1, None, False, "", value="Lisbon"),
    ]
    reader = ScreenReader.__new__(ScreenReader)
    reader.config, reader._next_id = SimpleNamespace(max_elements=70), 1
    assert [e.value for e in reader._number(raw)] == ["", "", "Lisbon"]


def test_what_a_control_says_is_read_from_what_one_call_brought_back():
    class Node:
        def __init__(self, props):
            self.props = props

        def GetCachedPropertyValue(self, prop):
            if prop not in self.props:
                raise KeyError(prop)
            return self.props[prop]

    field = Node({uia._P_HAS_VALUE: True, uia._P_VALUE: "  A1:C6\n ", uia._P_ENABLED: True})
    assert uia._states(field, False) == {"value": "A1:C6"}
    assert uia._states(field, True) == {}  # a password's value is not even looked at
    tick = Node({uia._P_HAS_TOGGLE: True, uia._P_TOGGLE: 1, uia._P_ENABLED: False})
    assert uia._states(tick, False) == {"enabled": False, "on": True}
    row = Node({uia._P_HAS_SELECTION: True, uia._P_SELECTED: True, uia._P_HAS_EXPAND: True, uia._P_EXPANDED: 0})
    assert uia._states(row, False) == {"chosen": True, "open": False}
    assert uia._states(Node({}), False) == {}  # an app that says nothing: nothing is made up


# ---------------------------------------------------------------- in the desktop loop
def screen(color: str = "white") -> ScreenContext:
    ctx = ScreenContext(
        time.time(),
        WindowInfo("Bench form", "form.exe", (0, 0, 1366, 768), hwnd=77),
        [
            UIElement(1, "Contact name", "Edit", (100, 100, 200, 30), automation_id="contact"),
            UIElement(2, "Password", "Edit", (100, 150, 200, 30), is_password=True),
            UIElement(3, "Monthly newsletter", "CheckBox", (100, 200, 200, 30), on=False),
            UIElement(4, "Customers", "List", (400, 100, 400, 500)),
            UIElement(5, "Delete account", "Button", (100, 300, 120, 30)),
        ],
        Image.new("RGB", (1366, 768), color),
    )
    ctx.model_scale = 1.0
    return ctx


class Hands:
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

    def press(self, keys):
        self.did.append(("press", keys))


class Scripted:
    def __init__(self, *calls: tuple[str, dict]) -> None:
        self.calls, self.asked, self.texts = list(calls), 0, []

    async def complete(self, **kw):
        self.asked += 1
        self.texts.append(kw["messages"][0].parts[0].text)
        name, args = self.calls.pop(0) if self.calls else ("done", {"ok": True, "summary": "Done."})
        return LLMResponse(text="", tool_calls=[ToolCall(id="c", name=name, arguments=args)], usage=Usage())


class Pretend:
    """What the loop asks of the controls, written down, and answered from ``says``."""

    def __init__(self, **says) -> None:
        self.says, self.asked, self.front, self.gone = says, [], "", False

    def _answer(self, verb, *what):
        self.asked.append((verb, *what))
        return self.says.get(verb, Done(why="not found"))

    def where(self, aim, wait_s=0.0, kinds=None):
        return self._answer("where", aim.name)

    def click(self, aim, wait_s=0.0):
        self.asked.append(("click", aim.name, wait_s))
        return self.says.get("click")

    def set_value(self, aim, text, wait_s=0.0, if_empty=False):
        return self._answer("set_value", aim.name, text)

    def toggle(self, aim, want=None, wait_s=0.0):
        return self._answer("toggle", aim.name, want)

    def expand(self, aim, open_=True, wait_s=0.0):
        return self._answer("expand", aim.name, open_)

    def select(self, aim, item, wait_s=0.0):
        return self._answer("select", aim.name, item)

    def scroll_to(self, aim, wait_s=0.0):
        return self._answer("scroll_to", aim.name)

    def menu(self, hwnd, path, wait_s=2.0):
        return self._answer("menu", tuple(path))

    def find(self, hwnd, text, limit=10):
        return self._answer("find", text)

    def read(self, aim, wait_s=0.0, most=600):
        return self._answer("read", aim.name)

    def wait_for(self, aim, timeout_s=5.0, gone=False):
        return self._answer("wait_for", aim.name)

    def in_front(self, hwnd):
        return self.front

    def closed(self, hwnd):
        return self.gone

    def watch(self, hwnd):
        self.watched = hwnd


def operator(llm, box: list, control=None, allow=True, asked=None, same=False):
    colors = iter(["white", "black", "red", "blue", "green", "gray"] * 4)
    still = screen("white")

    async def cap():
        return still if same else screen(next(colors))

    async def confirm(action, reason):
        if asked is not None:
            asked.append(action)
        return allow

    async def report(doing, n):
        pass

    def make():
        box.append(Hands())
        return box[-1]

    op = Operator(llm, cap, confirm, report, make_hands=make, aim_llm=llm, control=control)
    op.settle, op.settle_click, op.wait_named = (0.0, 0.0), (0.0,), 0.0
    return op


def act(*actions: dict, **more) -> tuple[str, dict]:
    return "act", {"actions": list(actions), **more}


async def test_a_field_is_filled_in_one_action_with_no_mouse_and_the_model_is_told_what_it_holds():
    box, control = [], Pretend(set_value=Done(ok=True, how="value", now="Rosa Lindqvist", name="Contact name", sure=True))
    llm = Scripted(act({"do": "set_value", "element_id": 1, "text": "Rosa Lindqvist"}))
    out = await operator(llm, box, control).run("fill in the name")
    assert out.ok and box[0].did == []
    assert control.asked == [("set_value", "Contact name", "Rosa Lindqvist")]
    assert out.steps[0].actions == "set “Contact name” to 'Rosa Lindqvist' (it now reads that)"
    assert "it now reads that" in llm.texts[1]  # said in "Done so far": no look needed to check


async def test_a_box_the_keyboard_is_in_is_woken_with_a_key_so_the_app_reads_what_was_put_in():
    # Live: Save as showed the new path, and Enter still saved under the old name.
    box = []
    control = Pretend(
        set_value=Done(ok=True, how="value", now="C:\\made up\\copy.txt", name="File name:", role="Edit", sure=True, focused=True),
        read=Done(ok=True, now="reads 'C:\\\\made up\\\\copy.txt'"),
    )
    llm = Scripted(act({"do": "set_value", "name": "File name:", "text": "C:\\made up\\copy.txt"}, {"do": "press", "text": "enter"}))
    out = await operator(llm, box, control).run("save a copy")
    assert box[0].did == [("press", "end"), ("type", " "), ("press", "backspace"), ("press", "enter")]
    assert "it now reads that" in out.steps[0].actions
    box, control = [], Pretend(set_value=Done(ok=True, how="value", now="All of it.", name="Text editor", role="Document", sure=True, focused=True))
    await operator(Scripted(act({"do": "set_value", "element_id": 1, "text": "All of it."})), box, control).run("write it")
    assert box[0].did == []  # a document's body is left as it was set: no key for autocorrect to act on


async def test_a_field_that_cannot_be_set_that_way_is_clicked_and_typed_into_instead():
    box, control = [], Pretend(set_value=Done(why="no way", name="Contact name"))
    llm = Scripted(act({"do": "set_value", "element_id": 1, "text": "Rosa"}))
    out = await operator(llm, box, control).run("fill in the name")
    assert box[0].did == [("click", 200, 115), ("press", "ctrl+a"), ("type", "Rosa")]
    assert out.steps[0].actions.startswith("typed 'Rosa' into Contact name")


async def test_with_nothing_to_set_it_with_a_field_is_clicked_and_typed_into_as_before():
    box = []
    llm = Scripted(act({"do": "set_value", "element_id": 1, "text": "Rosa"}), act({"do": "toggle", "element_id": 3, "on": True}))
    await operator(llm, box, None).run("fill in the form")
    assert box[0].did == [("click", 200, 115), ("press", "ctrl+a"), ("type", "Rosa"), ("click", 200, 215)]


async def test_a_password_field_is_never_set_whichever_way_is_tried():
    box, control = [], Pretend(set_value=Done(why="password", name="Password", password=True))
    llm = Scripted(act({"do": "set_value", "element_id": 2, "text": "hunter2"}), ("done", {"ok": False, "summary": "They type that."}))
    out = await operator(llm, box, control).run("sign me in")
    assert box[0].did == [] and "password field" in out.steps[0].note
    box = []
    llm = Scripted(act({"do": "set_value", "element_id": 2, "text": "hunter2"}), ("done", {"ok": False, "summary": "They type that."}))
    out = await operator(llm, box, None).run("sign me in")  # nothing to set it with: still never typed
    assert ("type", "hunter2") not in box[0].did and "password field" in out.steps[0].note


async def test_a_row_is_chosen_by_name_and_one_that_needs_a_click_gets_it_where_it_now_is():
    box, control = [], Pretend(select=Done(ok=True, how="select", now="chosen", name="Nadia Farouk", sure=True))
    llm = Scripted(act({"do": "select", "element_id": 4, "text": "Nadia Farouk"}))
    out = await operator(llm, box, control).run("choose Nadia Farouk")
    assert control.asked == [("select", "Customers", "Nadia Farouk")] and box[0].did == []
    assert out.steps[0].actions == "chose “Nadia Farouk” in Customers (it is the chosen one now)"
    box, control = [], Pretend(select=Done(why="no way", name="Open now", rect=(400, 300, 400, 20)))
    await operator(Scripted(act({"do": "select", "text": "Open now"})), box, control).run("choose it")
    assert box[0].did == [("click", 600, 310)]


async def test_a_row_that_is_not_there_is_said_plainly_and_the_rest_of_the_act_waits():
    box, control = [], Pretend(select=Done(why="no such row", name="Nobody"))
    llm = Scripted(act({"do": "select", "text": "Nobody"}, {"do": "press", "text": "enter"}))
    out = await operator(llm, box, control).run("choose Nobody")
    assert box[0].did == [] and "no row or option called “Nobody”" in out.steps[0].note


async def test_anything_that_sends_or_deletes_is_asked_about_before_it_is_done_that_way_too():
    for action, card in (
        ({"do": "select", "text": "Delete all rows"}, "Choose “Delete all rows”"),
        ({"do": "menu", "text": "File > Exit"}, "Use the menu File > Exit"),
        ({"do": "toggle", "element_id": 3, "on": True, "risky": True}, "Press “Monthly newsletter”"),
        ({"do": "expand", "name": "Sign out options"}, "Press “Sign out options”"),
    ):
        box, asked, control = [], [], Pretend()
        out = await operator(Scripted(act(action)), box, control, allow=False, asked=asked).run("do it")
        assert asked == [card] and control.asked == [] and box[0].did == []
        assert not out.ok and out.summary == "Okay, I left that for you."


async def test_a_risky_button_aimed_at_by_name_is_asked_about_whatever_label_it_is_given():
    box, asked = [], []
    control = Pretend(where=Done(ok=True, name="Delete account", rect=(100, 300, 120, 30)), click=Result("invoke", (100, 300, 120, 30)))
    llm = Scripted(act({"do": "click", "name": "Delete account", "label": "the button"}))
    out = await operator(llm, box, control, allow=False, asked=asked).run("tidy up")
    assert asked == ["Click “Delete account”"] and not out.ok and box[0].did == []
    assert not any(verb == "click" for verb, *_ in control.asked)  # found, asked about, not pressed


async def test_a_menu_is_one_action_and_a_path_that_stops_part_way_is_closed_again():
    box, control = [], Pretend(menu=Done(ok=True, how="menu", name="As CSV"))
    out = await operator(Scripted(act({"do": "menu", "text": "File > Export > As CSV"})), box, control).run("export it")
    assert control.asked == [("menu", ("File", "Export", "As CSV"))] and out.steps[0].actions == "menu File > Export > As CSV"
    box, control = [], Pretend(menu=Done(why="not found", name="Exprt", found=["Export", "Reload"], steps=1))
    llm = Scripted(act({"do": "menu", "text": "File > Exprt"}, {"do": "press", "text": "enter"}), ("done", {"ok": False, "summary": "No."}))
    out = await operator(llm, box, control).run("export it")
    assert box[0].did == [("press", "esc")]  # the menu it opened is not left hanging, and enter is not pressed
    assert "there is no “Exprt” in that menu. It holds: Export, Reload" in out.steps[0].note


async def test_a_control_that_appears_after_an_earlier_action_is_aimed_at_by_name_in_the_same_act():
    box = []
    control = Pretend(
        set_value=Done(ok=True, how="value", now="colour", name="Find", sure=True),
        click=Result("invoke", (500, 200, 90, 30)),
        where=Done(ok=True, name="Replace all", rect=(500, 200, 90, 30)),
    )
    llm = Scripted(act({"do": "press", "text": "ctrl+h"}, {"do": "set_value", "name": "Find", "text": "colour"}, {"do": "click", "name": "Replace all"}, finish="Replaced them all."))
    out = await operator(llm, box, control).run("replace colour with color")
    assert out.ok and out.summary == "Replaced them all." and llm.asked == 1  # one answer for all of it
    assert box[0].did == [("press", "ctrl+h")]
    assert ("set_value", "Find", "colour") in control.asked and ("click", "Replace all", 0.0) in control.asked


async def test_a_named_control_that_cannot_be_pressed_is_clicked_where_it_is_now():
    box, control = [], Pretend(where=Done(ok=True, name="Canvas", rect=(100, 100, 800, 400)), click=Result("", (100, 100, 800, 400), "no way"))
    await operator(Scripted(act({"do": "click", "name": "Canvas"})), box, control).run("click the canvas")
    assert box[0].did == [("click", 500, 300)]


async def test_a_name_that_is_not_in_the_window_is_skipped_and_said_never_clicked_on_a_guess():
    box, control = [], Pretend()
    llm = Scripted(act({"do": "click", "name": "Save as"}, {"do": "type", "text": "x"}), ("done", {"ok": False, "summary": "No."}))
    out = await operator(llm, box, control).run("save it")
    assert box[0].did == [] and "nothing called “Save as” is in this window right now" in out.steps[0].note


async def test_a_field_that_is_gone_since_the_look_is_never_typed_into_where_it_was():
    for why, says in (("not found", "nothing called “Contact name”"), ("behind a dialog", "come up in front of")):
        box, control = [], Pretend(set_value=Done(why=why))
        llm = Scripted(act({"do": "set_value", "element_id": 1, "text": "Rosa"}), ("done", {"ok": False, "summary": "No."}))
        out = await operator(llm, box, control).run("fill in the name")
        assert box[0].did == [] and says in out.steps[0].note


async def test_another_window_in_front_means_nothing_is_done_there():
    box, control = [], Pretend(set_value=Done(why="not in front"))
    llm = Scripted(act({"do": "set_value", "name": "File name", "text": "x"}), ("done", {"ok": False, "summary": "No."}))
    out = await operator(llm, box, control).run("save it")
    assert box[0].did == [] and "another window is in front now" in out.steps[0].note


async def test_a_hand_on_the_mouse_stops_these_too():
    box, control = [], Pretend(set_value=Done(ok=True, now="x"))
    op = operator(Scripted(act({"do": "press", "text": "tab"}, {"do": "set_value", "element_id": 1, "text": "x"})), box, control)

    class Grabbing(Hands):
        def press(self, keys):
            super().press(keys)
            self.touched, self.reason = True, "esc"

    op.make_hands = lambda: box.append(Grabbing()) or box[-1]
    out = await op.run("fill it in")
    assert out.stopped and control.asked == []


async def test_find_read_and_wait_tell_the_model_what_they_found():
    box = []
    control = Pretend(
        find=Done(ok=True, found=['ListItem "Nadia Farouk" (out of view)', 'ListItem "Omar Farouk"'], others=3),
        read=Done(ok=True, name="Scale", now="reads '125%'"),
        wait_for=Done(ok=True, name="Save as"),
    )
    llm = Scripted(act({"do": "find", "text": "farouk"}, {"do": "read", "name": "Scale"}, {"do": "wait", "name": "Save as"}))
    out = await operator(llm, box, control).run("look around")
    line = out.steps[0].actions
    assert 'looked for “farouk”, found: ListItem "Nadia Farouk" (out of view); ListItem "Omar Farouk" and 3 more' in line
    assert "read “Scale”: reads '125%'" in line and "waited for “Save as”" in line and box[0].did == []


async def test_a_dialog_that_came_up_is_named_to_the_model():
    box, control = [], Pretend(menu=Done(ok=True, how="menu", name="Save as"))
    control.front = "Save as"
    llm = Scripted(act({"do": "menu", "text": "File > Save as"}))
    out = await operator(llm, box, control).run("save a copy")
    assert "A window called “Save as” is in front now." in out.steps[0].note
    assert "Save as” is in front now" in llm.texts[1]
    box, control = [], Pretend()
    control.front, control.gone = "minutes_copy.txt - Notepad", True  # the dialog took it and went
    llm = Scripted(act({"do": "press", "text": "enter"}))
    out = await operator(llm, box, control).run("save a copy")
    assert "The “Bench form” window has closed, and “minutes_copy.txt - Notepad” is in front again." in out.steps[0].note


async def test_a_task_is_not_ended_on_the_models_word_when_a_window_it_has_not_seen_came_up():
    box, control = [], Pretend(menu=Done(ok=True, how="menu", name="Save as"))
    control.front = "Confirm Save As"
    llm = Scripted(act({"do": "menu", "text": "File > Save as"}, finish="Saved a copy."), ("done", {"ok": False, "summary": "It asks whether to replace the file."}))
    out = await operator(llm, box, control).run("save a copy")
    assert llm.asked == 2 and out.summary == "It asks whether to replace the file."
    box, control = [], Pretend(menu=Done(ok=True, how="menu", name="Save"))  # nothing came up: it ends there
    llm = Scripted(act({"do": "menu", "text": "File > Save"}, finish="Saved."))
    out = await operator(llm, box, control).run("save it")
    assert llm.asked == 1 and out.ok and out.summary == "Saved."


async def test_typing_into_a_field_the_look_saw_empty_puts_the_text_in_whole():
    # Live: "harbour" typed into Notepad was saved as "harbor" (its autocorrect).
    box, control = [], Pretend(set_value=Done(ok=True, how="value", now="The harbour opens.", name="Contact name", role="Document", sure=True))
    llm = Scripted(act({"do": "click", "element_id": 1}, {"do": "type", "text": "The harbour opens."}, {"do": "press", "text": "ctrl+s"}))
    out = await operator(llm, box, control).run("type it and save")
    assert control.asked == [("set_value", "Contact name", "The harbour opens.")]
    assert box[0].did == [("click", 200, 115), ("press", "ctrl+end"), ("press", "ctrl+s")]  # not typed key by key
    assert "typed 'The harbour opens.' (it now reads exactly that)" in out.steps[0].actions
    # A field that can't be set that way, one that already holds text, or text with Enter in it: typed.
    for control, action in (
        (Pretend(set_value=Done(why="no way")), {"do": "type", "element_id": 1, "text": "Rosa"}),
        (Pretend(set_value=Done(ok=True)), {"do": "type", "element_id": 1, "text": "Rosa\n"}),
        (Pretend(set_value=Done(ok=True)), {"do": "type", "text": "Rosa"}),
    ):
        box = []
        await operator(Scripted(act(action)), box, control).run("fill it in")
        assert box[0].did[-1] == ("type", action["text"])


async def test_the_same_direct_action_three_times_is_named_as_a_way_that_does_not_work():
    box, control = [], Pretend(set_value=Done(ok=True, how="value", now="A1:C6", name="Name Box", sure=True))
    again = act({"do": "set_value", "name": "Name Box", "text": "A1:C6"}, {"do": "press", "text": "enter"})
    llm = Scripted(again, again, again, again)
    out = await operator(llm, box, control, same=True).run("select the range")
    assert len(control.asked) == 2  # never six times, as the Name box once was typed into
    assert any("already failed twice" in s.note for s in out.steps)


def test_direct_actions_are_told_apart_by_what_they_were_done_to_and_with():
    ctx = screen()
    a = _action_key({"do": "set_value", "element_id": 1, "text": "Rosa"}, ctx)
    assert a == ("set_value", "Contact name (Edit)", "rosa")
    assert a != _action_key({"do": "set_value", "element_id": 1, "text": "Omar"}, ctx)
    assert _action_key({"do": "select", "text": "Nadia Farouk"}, ctx) == ("select", "", "nadia farouk")
    assert _action_key({"do": "menu", "text": "File > Save as"}, ctx) == ("menu", "", "file > save as")
    assert _action_key({"do": "find"}, ctx) is None
    assert _say_action(("select", "customers", "nadia farouk")) == "choosing customers (nadia farouk)"


def test_the_model_is_offered_the_direct_actions_and_told_when_to_use_them():
    doing = ACT.parameters["properties"]["actions"]["items"]["properties"]
    assert {"set_value", "select", "toggle", "expand", "collapse", "menu", "scroll_to", "find", "read"} <= set(doing["do"]["enum"])
    assert "name" in doing and "on" in doing and "[x, y]" in doing["to_box"]["description"]
    assert "NEVER scroll a list to hunt for a row" in SYSTEM and "`set_value`" in SYSTEM and "File > Export > As CSV" in SYSTEM
    assert "Never type passwords" in SYSTEM and "Mark `risky: true`" in SYSTEM  # the safety rules are all still there
