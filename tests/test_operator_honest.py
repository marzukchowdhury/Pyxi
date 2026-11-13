"""The desktop hands do not call a step done when it did nothing, and do not go round for
minutes on a way that does not work.

All seen live on 13 November in a spreadsheet app ("GenOffice" here), with a build that
pressed buttons through the accessibility layer:

* "New tab" took the press and did nothing. On the next look the model saw the tab of the
  file that was already open and reported a new blank one. The user clicked the button.
* "Font color options" was pressed four times in sixteen seconds and never opened.
* A range was typed into the Name box six times, Escape pressed three times and two cells
  clicked four times, in a cycle, for 150 seconds: no two rounds were the same list.
* A chart was made in twelve seconds and then dragged about for fifty.
* A file was renamed in six seconds, and forty more went on looking for its old title.

Nothing real is pressed or clicked here: a pretend app, pretend hands, a scripted model.
"""

from __future__ import annotations

import time

from otto.types import LLMResponse, ToolCall, Usage
from PIL import Image, ImageDraw

from pyxi.companion import operator as operator_mod
from pyxi.companion.operator import ACT, SYSTEM, Operator, _action_key
from pyxi.screen.context import ScreenContext, UIElement, WindowInfo
from pyxi.screen.press import Result

NEW_TAB = (400, 40, 30, 30)
COLOUR = (600, 90, 40, 30)


class App:
    """A pretend spreadsheet app: one picture, redrawn only when something really happens."""

    def __init__(self, press_works: bool = False, click_works: bool = True) -> None:
        self.press_works, self.click_works = press_works, click_works
        self.tabs = 1  # the file that was already open
        self.pressed: list[str] = []
        self.marks = 0  # small changes: a selection that moved, a cell that was typed in

    def look(self) -> ScreenContext:
        shot = Image.new("RGB", (1366, 768), "white")
        draw = ImageDraw.Draw(shot)
        for i in range(self.tabs):
            draw.rectangle((20 + 180 * i, 30, 180 + 180 * i, 70), fill="#3060c0")
        if self.marks:  # a selection highlight, somewhere new each time
            x, y = 60 + 130 * (self.marks % 9), 200 + 60 * (self.marks // 9)
            draw.rectangle((x, y, x + 120, y + 50), fill="#909090")
        ctx = ScreenContext(
            time.time(),
            WindowInfo("Untitled Spreadsheet.xlsx - GenOffice", "genoffice.exe", (0, 0, 1366, 768), hwnd=77),
            [
                UIElement(1, "New tab", "Button", NEW_TAB),
                UIElement(2, "Font color options", "SplitButton", COLOUR),
                UIElement(3, "Name box", "Edit", (10, 130, 90, 24)),
                UIElement(4, "Untitled Spreadsheet.xlsx", "TabItem", (20, 30, 160, 40)),
                UIElement(5, "Next", "Button", (1200, 700, 80, 30)),
                UIElement(6, "File name", "Edit", (500, 12, 300, 24)),
            ],
            shot,
        )
        ctx.model_scale = 1.0
        return ctx

    def press(self, target):
        self.pressed.append(target.name)
        if self.press_works and target.name == "New tab":
            self.tabs += 1
        return Result("invoke", target.rect)

    def clicked(self, x: int, y: int) -> None:
        if self.click_works and (x, y) == (NEW_TAB[0] + 15, NEW_TAB[1] + 15):
            self.tabs += 1


class Hands:
    def __init__(self, app: App) -> None:
        self.app, self.did, self.reason = app, [], ""

    def interrupted(self) -> bool:
        return False

    def click(self, x, y, button="left", count=1):
        self.did.append(("click", x, y))
        self.app.clicked(x, y)

    def type(self, text):
        self.did.append(("type", text))
        self.app.marks += 1

    def press(self, keys):
        self.did.append(("press", keys))
        self.app.marks += 1

    def drag(self, x0, y0, x1, y1):
        self.did.append(("drag", x0, y0, x1, y1))
        self.app.marks += 1


class Model:
    """Answers with these tool calls, one per look, and keeps what it was shown."""

    def __init__(self, *calls: tuple[str, dict]) -> None:
        self.calls = list(calls)
        self.shown: list[str] = []

    async def complete(self, **kw):
        self.shown.append(kw["messages"][0].parts[0].text)
        name, args = self.calls.pop(0) if self.calls else ("done", {"ok": True, "summary": "Done."})
        return LLMResponse(text="", tool_calls=[ToolCall(id="c", name=name, arguments=args)], usage=Usage())


def hands_for(app: App, llm: Model, box: list, press: bool = True) -> Operator:
    async def cap():
        return app.look()

    async def yes(*_):
        return True

    async def quiet(*_):
        pass

    def make():
        box.append(Hands(app))
        return box[-1]

    op = Operator(llm, cap, yes, quiet, make_hands=make, aim_llm=llm, press=app.press if press else None)
    op.settle, op.settle_click = (0.0, 0.0), (0.0,)
    return op


def act(*actions: dict, **more) -> tuple[str, dict]:
    return "act", {"actions": list(actions), **more}


def click(element_id: int, **more) -> dict:
    return {"do": "click", "element_id": element_id, **more}


NEW_FILE = ("done", {"ok": True, "summary": "A new blank “Untitled Spreadsheet.xlsx” is now open."})


# ---------------------------------------------------------------- 1. a false "done"
async def test_a_press_that_did_nothing_is_clicked_with_the_mouse_in_the_same_step():
    app, box = App(press_works=False, click_works=True), []
    llm = Model(act(click(1)), NEW_FILE)
    out = await hands_for(app, llm, box).run("Open a new blank file in GenOffice.")
    assert app.pressed == ["New tab"]  # pressed once, with no mouse
    assert box[0].did == [("click", 415, 55)]  # nothing showed, so the mouse, before any answer
    assert app.tabs == 2 and out.ok
    assert len(llm.shown) == 2  # one act and one done: the retry cost no answer
    assert "with the mouse" in out.steps[0].actions and not out.steps[0].note


async def test_a_press_that_worked_is_not_clicked_again():
    app, box = App(press_works=True), []
    out = await hands_for(app, Model(act(click(1)), NEW_FILE), box).run("Open a new blank file.")
    assert app.tabs == 2 and box[0].did == [] and out.ok  # one new tab, not two


async def test_when_nothing_happened_the_step_says_so_and_done_is_not_taken():
    app, box = App(press_works=False, click_works=False), []
    llm = Model(act(click(1)), NEW_FILE, ("done", {"ok": False, "summary": "The New tab button does nothing here."}))
    out = await hands_for(app, llm, box).run("Open a new blank file in GenOffice.")
    assert app.tabs == 1
    assert not out.ok and out.summary == "The New tab button does nothing here."
    note = out.steps[0].note
    assert "nothing happened" in note and "NOT done" in note  # the step's own note, in plain words
    assert "You called done" in note and "already there" in note  # and the done was sent back
    assert len(llm.shown) == 3
    # What the model was shown on the look after the step, before it called done.
    assert "nothing happened" in llm.shown[1] and "pressed and then clicked with the mouse" in llm.shown[1]


async def test_done_is_not_taken_after_a_mouse_click_that_changed_nothing_either():
    # No press at all here (an app with no accessibility): the look alone says nothing moved.
    app, box = App(click_works=False), []
    llm = Model(act(click(1)), NEW_FILE, act({"do": "press", "text": "ctrl+n"}), NEW_FILE)
    out = await hands_for(app, llm, box, press=False).run("Open a new blank file.")
    assert "You called done" in out.steps[0].note
    assert box[0].did == [("click", 415, 55), ("press", "ctrl+n")]  # it went another way
    assert out.ok and len(out.steps) == 2


async def test_a_done_that_is_insisted_on_is_taken_the_second_time():
    # The check is said once: a model that looked again and still says done is believed (the
    # thing may have been there all along, which is a fair answer to "open X").
    app, box = App(click_works=False), []
    out = await hands_for(app, Model(act(click(1)), NEW_FILE, NEW_FILE), box, press=False).run("Open a new file.")
    assert out.ok and len(out.steps) == 1


async def test_done_after_a_shortcut_that_shows_nothing_is_not_doubted():
    # ctrl+s, ctrl+c and a media key leave the picture as it was: no second answer for those.
    class Quiet(Hands):
        def press(self, keys):
            self.did.append(("press", keys))

    app, box = App(), []
    llm = Model(act({"do": "press", "text": "ctrl+s"}), ("done", {"ok": True, "summary": "Saved."}))
    op = hands_for(app, llm, box)
    op.make_hands = lambda: box.append(Quiet(app)) or box[-1]
    out = await op.run("Save the file.")
    assert out.ok and len(llm.shown) == 2 and "You called done" not in out.steps[0].note


async def test_finish_is_not_taken_when_its_press_did_nothing():
    app, box = App(press_works=False, click_works=False), []
    llm = Model(act(click(1), finish="A new blank file is open."), ("done", {"ok": False, "summary": "It would not open."}))
    out = await hands_for(app, llm, box).run("Open a new blank file.")
    assert not out.ok and out.summary == "It would not open." and len(llm.shown) == 2


async def test_finish_is_not_taken_when_only_something_else_on_the_screen_moved():
    # The press did nothing, and then a key in the same act moved a caret: the screen did
    # change, but not by the press. The act stops at the press, so finish is not reached.
    app, box = App(press_works=False, click_works=False), []
    llm = Model(
        act(click(1), {"do": "press", "text": "right"}, finish="A new blank file is open."),
        ("done", {"ok": False, "summary": "It would not open."}),
    )
    out = await hands_for(app, llm, box).run("Open a new blank file.")
    assert not out.ok and ("press", "right") not in box[0].did  # the rest of the act waited


# ---------------------------------------------------------------- 2. the same press again
async def test_a_menu_button_that_shows_nothing_is_pressed_once_clicked_once_and_left():
    app, box = App(), []
    again = [act(click(2, label="Font color options"))] * 5
    llm = Model(*again, ("done", {"ok": False, "summary": "The colour menu would not open."}))
    out = await hands_for(app, llm, box).run("Make the headings red.")
    assert app.pressed == ["Font color options"]  # once, not five times
    assert box[0].did == [("click", 620, 105)]  # one retry with the mouse
    assert out.steps[1].note.startswith("skipped") and "nothing happened either time" in out.steps[1].note
    assert not out.ok


async def test_it_may_be_clicked_again_once_the_screen_has_changed():
    app, box = App(), []
    llm = Model(
        act(click(2)),
        act({"do": "type", "element_id": 3, "text": "A1"}),  # a selection is made: the screen moves
        act(click(2)),
    )
    await hands_for(app, llm, box).run("Make the headings red.")
    assert app.pressed == ["Font color options"]  # never pressed without the mouse again
    assert [d for d in box[0].did if d == ("click", 620, 105)] == [("click", 620, 105)] * 2


# ---------------------------------------------------------------- 3. loops of single actions
NAME_BOX = [{"do": "click", "element_id": 3}, {"do": "type", "text": "A1:C1"}, {"do": "press", "text": "enter"}]
A1, C1 = {"do": "click", "box": [100, 180, 60, 20], "label": "grid cell A1"}, {"do": "click", "box": [260, 180, 60, 20], "label": "grid cell C1"}


async def test_the_same_single_actions_in_a_cycle_are_named_and_then_end_the_task():
    """What happened: Name box, Escape, two cells, round and round, each round a little
    different (a colour button here, a tab there), the picture changing a little each time."""
    app, box = App(), []
    rounds = [
        act(*NAME_BOX, {"do": "press", "text": "ctrl+b"}),
        act({"do": "press", "text": "escape"}),
        act(A1, C1),
        act(*NAME_BOX),
        act({"do": "press", "text": "escape"}, A1),
        act(*NAME_BOX, click(2)),
        act(*NAME_BOX, {"do": "press", "text": "ctrl+i"}),
        act(*NAME_BOX),
        act(*NAME_BOX),
    ]
    llm = Model(*rounds)
    out = await hands_for(app, llm, box).run("Make the headings in A1:C1 bold and red.")
    typed = [d for d in box[0].did if d == ("type", "A1:C1")]
    assert len(typed) == 3  # three times, not six
    warned = next(s for s in out.steps if "three separate steps" in s.note)
    assert "typing 'a1:c1'" in warned.note and "does not work here" in warned.note
    assert not out.ok and "typing 'a1:c1'" in out.summary and "same thing" in out.summary
    assert len(llm.shown) <= 8  # ended at the second warning, not after thirty answers


async def test_a_second_way_worn_out_after_the_first_warning_ends_it_too():
    app, box = App(), []
    # No two lists alike (a word of the model's own differs each time), as it was live.
    def name_box(i):
        return act(*NAME_BOX[:2], {"do": "press", "text": "enter", "why": f"try {i}"})

    def cells(i):
        return act(A1, {**C1, "why": f"try {i}"})

    rounds = [name_box(1), cells(1), name_box(2), cells(2), name_box(3), cells(3), cells(4)]
    out = await hands_for(app, Model(*rounds), box).run("Make the headings bold.")
    assert not out.ok and "same thing" in out.summary and "clicking grid cell" in out.summary
    assert len(out.steps) == 5  # the third round of clicking the two cells was not done
    assert "typing 'a1:c1'" in out.steps[-1].note  # the first warning, on the step that earned it


async def test_honest_repeats_are_not_counted():
    # Keys that move through a form, a button used again after choosing something new, and
    # the same button on a screen that moved on (a wizard) are all work going forward.
    app, box = App(), []
    cells = [{"do": "click", "box": [100, 200 + 40 * i, 60, 20], "label": f"grid cell A{i + 2}"} for i in range(5)]
    steps = [act(c, click(2), {"do": "press", "text": "tab"}, {"do": "press", "text": "enter"}) for c in cells]
    out = await hands_for(app, Model(*steps), box, press=False).run("Colour five cells.")
    assert out.ok and not any("three separate steps" in s.note for s in out.steps)

    pages = iter(["white", "black", "#808080", "#404040", "#c0c0c0", "#202020", "#e0e0e0"])

    def page() -> ScreenContext:
        ctx = app.look()
        ctx.screenshot = Image.new("RGB", (1366, 768), next(pages))
        return ctx

    box = []
    op = hands_for(app, Model(*[act(click(5))] * 2, act(click(5), {"do": "wait", "seconds": 0.2}), act(click(5, label="Next"))), box, press=False)

    async def cap():
        return page()

    op.capture = cap
    out = await op.run("Go through the set-up.")
    assert out.ok and len(box[0].did) == 4 and not any("three separate steps" in s.note for s in out.steps)


def test_what_counts_as_the_same_action():
    ctx = App().look()
    assert _action_key({"do": "type", "element_id": 3, "text": " A1:C1 "}, ctx) == ("type", "", "a1:c1")
    assert _action_key({"do": "type", "text": "A1:C1"}, ctx) == ("type", "", "a1:c1")  # wherever it was typed
    assert _action_key({"do": "press", "text": "Escape"}, ctx) == ("press", "", "escape")
    assert _action_key(click(2), ctx) == _action_key(click(2, label="colour menu", why="again"), ctx)
    assert _action_key({"do": "click", "box": [100, 180, 60, 20]}, ctx) == _action_key({"do": "click", "box": [110, 185, 50, 20]}, ctx)
    for honest in ("enter", "tab", "shift+right", "down", "ctrl+v", "backspace"):
        assert _action_key({"do": "press", "text": honest}, ctx) is None
    assert _action_key({"do": "wait", "seconds": 1}, ctx) is None and _action_key({"do": "scroll"}, ctx) is None
    assert _action_key({"do": "type", "text": "7"}, ctx) is None  # one character: data entry


# ---------------------------------------------------------------- 4, 6, 7. what the model is told
def test_a_range_is_selected_with_the_keyboard_and_the_name_box_is_for_excel_and_sheets():
    other = SYSTEM[SYSTEM.index("Any other spreadsheet app") : SYSTEM.index("- Google Docs")]
    assert "click its first cell, then shift+right / shift+down" in other
    assert other.index("shift+right") < other.index("Name box")  # the keyboard way comes first
    assert "only in Excel" in other and "in GenOffice and others it does nothing" in other
    assert "never type it there again" in other
    assert "the Name box selects a range \nthe same way" not in SYSTEM and "the same way. A" not in other
    assert "do NOT click a cell before you format" in SYSTEM  # its own selection, dropped by a click
    assert "(the Name box for a range)" not in SYSTEM


def test_what_was_asked_for_is_not_polished():
    assert "do not tidy, nudge, align or resize" in SYSTEM
    assert "at most ONCE" in SYSTEM and "covers their own data" in SYSTEM


def test_after_a_rename_the_old_name_is_expected_to_be_gone():
    assert "its OLD name is supposed to be gone" in SYSTEM and "don't go looking for it" in SYSTEM
    assert "A rename is one `act` with `finish`" in SYSTEM
    assert "a rename typed into the title" in ACT.parameters["properties"]["finish"]["description"]


RENAME = [{"do": "click", "element_id": 6}, {"do": "press", "text": "ctrl+a"}, {"do": "type", "text": "Sales sample"}, {"do": "press", "text": "enter"}]


async def test_a_rename_is_one_act_and_ends_without_a_hunt_for_the_old_name():
    app, box = App(), []
    llm = Model(act(*RENAME, finish="It's called Sales sample now."))
    out = await hands_for(app, llm, box).run("Rename the file “Untitled spreadsheet” to “Sales sample”.")
    assert out.ok and out.summary == "It's called Sales sample now." and len(llm.shown) == 1


async def test_without_finish_the_step_that_typed_the_new_name_says_the_old_one_is_gone():
    app, box = App(), []
    llm = Model(act(*RENAME), ("done", {"ok": True, "summary": "Renamed."}))
    out = await hands_for(app, llm, box).run("Rename the file “Untitled spreadsheet” to “Sales sample”.")
    assert "the old name is gone from the screen now" in out.steps[0].note and "call done" in out.steps[0].note
    assert "the old name is gone" in llm.shown[1] and out.ok
    # A task that renames nothing gets no such note.
    app, box = App(), []
    out = await hands_for(app, Model(act(*RENAME)), box).run("Type Sales sample into the file name box.")
    assert "old name" not in out.steps[0].note


def test_the_note_names_are_what_the_loop_looks_for():
    assert operator_mod._NOTHING == "nothing happened" and operator_mod.REPEAT_SPAN >= 8


# ---------------------------------------------------------------- 5. its own acts are its own
async def test_a_press_is_marked_as_pyxis_own_before_and_after(monkeypatch):
    from pyxi import hands as hands_mod

    order: list[str] = []
    app, box = App(press_works=True), []
    op = hands_for(app, Model(act(click(1))), box)
    real = op.press
    op.press = lambda target: order.append("press") or real(target)
    monkeypatch.setattr(hands_mod, "acted", lambda: order.append("own"))
    await op.run("Open a new blank file.")
    assert order == ["own", "press", "own"]


async def test_answering_a_card_with_the_mouse_is_not_read_as_taking_over():
    class Resting(Hands):
        rested = 0

        def rest(self):
            self.rested += 1

    app, box = App(), []
    llm = Model(act({"do": "click", "element_id": 5, "risky": True}), act({"do": "press", "text": "alt+f4"}))
    op = hands_for(app, llm, box, press=False)
    op.make_hands = lambda: box.append(Resting(app)) or box[-1]
    await op.run("Go on to the next page, then close the window.")
    assert box[0].rested == 2  # once for each card they answered


# ---------------------------------------------------------------- 8. the whole task, or none
async def test_a_long_task_reaches_the_hands_whole_and_a_huge_one_is_refused_in_words(monkeypatch):
    from types import SimpleNamespace

    from pyxi import hands as hands_mod
    from pyxi.companion import brain as brain_mod

    monkeypatch.setattr(hands_mod, "available", lambda: True)
    got: list[str] = []

    def drive(task, window, come_back=False, after_reading=None):
        got.append(task)
        return "the job"

    me = SimpleNamespace(_operator=None, _ctx=None, _turn_reads=[], _drive=drive, _spawn=lambda job: None)
    long = "In GenOffice, on the sheet General Journal, enter these twelve rows. " + "Row: 1 March, Rent, 1200. " * 40
    assert 600 < len(long) <= brain_mod.HANDS_TASK_CHARS
    assert await brain_mod.Brain._use_computer(me, {"task": long}) is None
    assert got == [long.strip()]  # every word of it: it used to stop at 600 characters

    huge = "Build a 15-sheet workbook. " + "On General Journal, enter 12 rows of sample entries. " * 60
    said = await brain_mod.Brain._use_computer(me, {"task": huge})
    assert len(got) == 1  # nothing was started, and nothing was cut to fit
    assert said.startswith("use_computer: nothing was started") and "too large" in said
    assert "edit_document" in said and "one small part at a time" in said and str(len(huge.strip())) in said
