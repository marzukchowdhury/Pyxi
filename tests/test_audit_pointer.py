"""Pointing at the right thing, on the right screen, while it is still there.

From the trial of 10 November on a PC with three screens. "Pivot table is here" was drawn at
the bottom right of the screen, on the word "pivot" in a notification that had already gone,
while the sheet tab "Pivot" sat at the bottom left. A guide step for the pinned Settings app
was framed on the Start menu's "Search for apps, settings, and documents". With the mouse
over the bare desktop, the model was given the buttons of a window on the second screen under
a picture of the first. And when UI Automation threw part-way, the window and everything read
so far were thrown away, so the model was told "window: ''".
"""

from __future__ import annotations

import sys
import time
import types

from otto.types import LLMResponse, ToolCall, Usage
from PIL import Image

from conftest import fake_context
from pyxi.companion.notes import near_point, target_rect
from pyxi.screen import ScreenContext, UIElement, WindowInfo, context, uia
from pyxi.screen.ocr import TextLine, find_text
from test_brain_and_server import make_brain


def line(text: str, rect: tuple[int, int, int, int], id: int = 0) -> TextLine:
    """An OCR line whose word boxes are spread along it in proportion to their letters."""
    x, y, w, h = rect
    per = w / max(1, len(text))
    words, pos = [], 0
    for word in text.split(" "):
        words.append((word, (round(x + pos * per), y, max(1, round(len(word) * per)), h)))
        pos += len(word) + 1
    return TextLine(text, rect, words, id)


# The two lines of that evening, as OCR read them (screenshot px of a 1366 x 768 picture).
TOAST = line("sales data and then a pivot, into the Google Sheet..", (1107, 699, 223, 10), 4909)
TAB = line("Pivot •", (198, 718, 38, 8), 4911)


# ------------------------------------------------------------------ which of two places
def test_the_sheet_tab_wins_over_the_same_word_inside_a_sentence():
    hit = find_text([TOAST, TAB], "Pivot")
    assert hit is not None and hit[0] < 240 and hit[1] == 718  # the tab, bottom left
    # The same for the Start menu: the pinned app, not the search box's hint.
    hint = line("Q Search for apps, settings, and documents", (421, 127, 200, 11))
    app = line("Settings", (633, 238, 31, 9))
    assert find_text([hint, app], "Settings") == (633, 238, 31, 9)


def test_matches_are_ranked_whole_line_then_start_of_line_then_capitals_then_order():
    middle = line("open the save dialog", (10, 10, 200, 10))
    start = line("save your work often", (10, 40, 200, 10))
    capital = line("then press Save now", (10, 70, 200, 10))
    whole = line("Save", (10, 100, 40, 10))
    assert find_text([middle, start, capital, whole], "Save") == (10, 100, 40, 10)
    assert find_text([middle, start, capital], "Save")[1] == 40  # starts its line
    assert find_text([middle, capital], "Save")[1] == 70  # written the same way
    assert find_text([middle], "Save")[1] == 10  # the only one there is
    # Nothing changes for words that are on screen once, or not at all.
    assert find_text([middle], "save dialog") is not None and find_text([middle], "paste") is None


def test_where_the_model_thought_it_was_settles_a_tie():
    left = line("Save", (100, 300, 40, 10))
    right = line("Save", (900, 300, 40, 10))
    assert find_text([left, right], "Save") == (100, 300, 40, 10)  # reading order, as before
    assert find_text([left, right], "Save", near=(920, 305)) == (900, 300, 40, 10)
    # A whole line still beats a nearer word in a sentence.
    assert find_text([TOAST, TAB], "Pivot", near=(1210, 704))[0] < 240


def test_the_models_box_or_point_says_which_place_was_meant():
    ctx = fake_context()
    ctx.text = [line("Save", (100, 300, 40, 10), 21), line("Save", (900, 300, 40, 10), 22)]
    assert near_point(ctx, {"box": [880, 280, 80, 40]}) == (920, 300)
    assert near_point(ctx, {"x": 915, "y": 306}) == (915, 306) and near_point(ctx, {}) is None
    assert target_rect(ctx, {"quote": "Save"}) == (100, 300, 40, 10)
    assert target_rect(ctx, {"quote": "Save", "box": [880, 280, 80, 40]}) == (900, 300, 40, 10)
    assert target_rect(ctx, {"quote": "Save", "x": 915, "y": 306}) == (900, 300, 40, 10)


async def test_a_guide_steps_box_breaks_the_tie_instead_of_being_thrown_away(settings):
    """Live: guide(quote="Settings", box=[625, 205, 50, 50]). The box was right and unused."""
    brain, _ = make_brain(settings)
    ctx = fake_context()
    ctx.text = [line("Save", (100, 300, 40, 10), 21), line("Save", (900, 300, 40, 10), 22)]
    step = {"quote": "Save", "box": [880, 280, 80, 40]}
    assert await brain._guide_target(ctx, step, "Save") == (900, 300, 40, 10)
    assert await brain._guide_target(ctx, {"quote": "Save"}, "Save") == (100, 300, 40, 10)


# ------------------------------------------------------------------ still there?
class Scripted:
    """Plays back tool calls turn by turn; ``before`` runs just before each reply is given."""

    def __init__(self, *turns, before=None):
        self.turns = list(turns)
        self.before = before
        self.prompts: list[str] = []

    async def complete(self, *, system, messages, tools=None, **_):
        self.prompts.append(messages[-1].parts[0].text)
        if self.before:
            self.before(len(self.prompts))
        calls = self.turns.pop(0) if self.turns else [("say", {"text": "Okay."})]
        return LLMResponse(
            tool_calls=[ToolCall(id=str(i), name=n, arguments=a) for i, (n, a) in enumerate(calls)],
            usage=Usage(),
            stop_reason="tool_use",
        )


def looked_a_while_ago(brain, then_on_screen: list[TextLine]):
    """While the model thinks, seconds pass and the screen moves on."""

    def before(n: int) -> None:
        if n == 1:
            brain._ctx.captured_at -= 10  # the look was taken when they pressed talk
            now = fake_context()
            now.text = then_on_screen
            brain.screen.ctx = now

    return before


async def test_a_pointer_is_not_drawn_on_words_that_have_gone(settings):
    llm = Scripted(
        [("say", {"text": "It's on the Pivot tab."}), ("point", {"quote": "Pivot", "label": "Pivot table is here"})],
        [("say", {"text": "The tab has moved out of view."})],
    )
    brain, rec = make_brain(settings, llm=llm)
    brain.screen.ctx.text = [TAB]
    llm.before = looked_a_while_ago(brain, [line("Sheet1", (132, 718, 48, 8), 30)])
    await brain.turn("go ahead")
    assert rec.of("annotate") == []  # no ring on nothing
    # And the model hears it, so the line that promised a pointer is put right.
    assert "point: couldn't find that on screen" in llm.prompts[1]
    assert [s["text"] for s in rec.of("say")][-1] == "The tab has moved out of view."
    call = next(c for c in brain.turns.recent(1)[0]["tool_calls"] if c["tool"] == "point")
    assert call["outcome"] == "refused"


async def test_a_pointer_follows_words_that_moved(settings):
    llm = Scripted([("say", {"text": "Here."}), ("point", {"quote": "Pivot", "label": "Pivot"})])
    brain, rec = make_brain(settings, llm=llm)
    brain.screen.ctx.text = [TAB]
    llm.before = looked_a_while_ago(brain, [line("Pivot •", (398, 718, 38, 8), 31)])
    await brain.turn("go ahead")
    [mark] = rec.of("annotate")[0]["annotations"]
    assert mark["rect"][0] >= 398 and mark["at"][0] >= 398  # 200 px to the right, with the tab


async def test_a_fresh_look_is_not_read_twice(settings):
    llm = Scripted([("say", {"text": "Here."}), ("point", {"quote": "Pivot", "label": "Pivot"})])
    brain, rec = make_brain(settings, llm=llm)
    brain.screen.ctx.text = [TAB]
    looks = []
    capture = brain.screen.capture

    async def counted(pixels: bool = True):
        looks.append(1)
        return await capture(pixels)

    brain.screen.capture = counted
    await brain.turn("go ahead")
    assert len(looks) == 1 and len(rec.of("annotate")) == 1  # drawn from the look it had


# ------------------------------------------------------------------ the tree that gave out
class _Rect:
    left, top = 607, 100

    def width(self) -> int:
        return 300

    def height(self) -> int:
        return 40


class _Control:
    Name, ProcessId, NativeWindowHandle = "Settings", 0, 1234
    ControlTypeName, IsOffscreen, AutomationId = "ButtonControl", False, ""
    BoundingRectangle = _Rect()

    def GetPropertyValue(self, _):
        return False


class _Auto:
    """Stands in for uiautomation: three good controls, then the error of that evening."""

    class PropertyId:
        IsPasswordProperty = 0

    def __init__(self, good: int = 3):
        self.good = good

    def ControlFromHandle(self, hwnd):
        return _Control()

    def WalkControl(self, top, includeTop=False, maxDepth=25):
        for _ in range(self.good):
            yield _Control(), 1
        raise OSError(-2147220991, "An event was unable to invoke any of the subscribers")


def test_a_tree_that_gives_out_part_way_keeps_the_window_and_what_was_read(monkeypatch):
    monkeypatch.setattr(uia, "process_name", lambda pid: "SystemSettings.exe")
    window, elements = uia._walk(_Auto(), 1.5, 400, 25, follow_mouse=False, named=1234)
    assert window.title == "Settings" and window.hwnd == 1234 and window.tree == "partial"
    assert len(elements) == 3
    window, elements = uia._walk(_Auto(good=0), 1.5, 400, 25, follow_mouse=False, named=1234)
    assert window.tree == "failed" and elements == [] and window.title == "Settings"


def _pretend_uiautomation(monkeypatch):
    class Thread:
        def __init__(self, debug=False):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setitem(
        sys.modules, "uiautomation", types.SimpleNamespace(UIAutomationInitializerInThread=Thread)
    )
    monkeypatch.setattr(uia, "available", lambda: True)
    monkeypatch.setattr(uia.time, "sleep", lambda s: None)


def test_reading_the_window_is_tried_twice(monkeypatch):
    _pretend_uiautomation(monkeypatch)
    tries = []

    def walk(*a, **k):
        tries.append(1)
        if len(tries) == 1:
            raise OSError("an app that was just opening")
        return uia.RawWindow("Settings", "SystemSettings.exe", (0, 0, 800, 600), 7), []

    monkeypatch.setattr(uia, "_walk", walk)
    window, _ = uia.read_foreground()
    assert len(tries) == 2 and window.title == "Settings" and window.tree == "ok"


def test_a_window_that_cant_be_read_is_still_named(monkeypatch):
    """It used to come back as no window at all: title '', handle 0, nowhere on screen."""
    _pretend_uiautomation(monkeypatch)

    def broken(*a, **k):
        raise OSError(-2147220991, "An event was unable to invoke any of the subscribers")

    monkeypatch.setattr(uia, "_walk", broken)
    monkeypatch.setattr(uia.sys, "platform", "win32")
    monkeypatch.setattr(uia, "window_to_read", lambda follow, named: (4321, None))
    monkeypatch.setattr(uia, "window_title", lambda hwnd: "Settings")
    monkeypatch.setattr(uia, "_rect_of", lambda hwnd: (0, 0, 1920, 1032))
    monkeypatch.setattr(uia, "_pid_of", lambda hwnd: 99)
    monkeypatch.setattr(uia, "process_name", lambda pid: "SystemSettings.exe")
    window, elements = uia.read_foreground()
    assert elements == [] and window.tree == "failed"
    assert (window.title, window.hwnd, window.rect) == ("Settings", 4321, (0, 0, 1920, 1032))


def test_the_model_is_told_when_the_tree_could_not_be_read():
    shot = Image.new("RGB", (1600, 900), (20, 20, 20))
    bar = [UIElement(9, "Start", "TaskbarButton", (700, 860, 40, 40))]
    failed = ScreenContext(time.time(), WindowInfo("Settings", "SystemSettings.exe", (0, 0, 1600, 860), 7), bar, shot, tree="failed")
    text = failed.render()
    assert "window: 'Settings'" in text and "could not be read this time" in text
    partial = ScreenContext(time.time(), WindowInfo("Settings", "SystemSettings.exe", (0, 0, 1600, 860), 7), bar, shot, tree="partial")
    assert "stopped part-way" in partial.render()
    # No window at all is said in words, not as "window: ''" (which read as "the desktop").
    nothing = ScreenContext(time.time(), WindowInfo(rect=(0, 0, 1600, 900)), bar, shot, tree="none")
    assert "window: ''" not in nothing.render() and "No window was read on this screen" in nothing.render()
    assert "could not be read" not in fake_context().render()


# ------------------------------------------------------------------ one screen's picture, that screen's controls
class _Shot:
    def __init__(self, mon):
        self.size = (mon["width"], mon["height"])
        self.bgra = bytes(mon["width"] * mon["height"] * 4)


class _ThreeScreens:
    """mss, for a main screen with two tall ones to its right (their tops at y = -840)."""

    monitors = [
        {"left": 0, "top": -840, "width": 4080, "height": 1920},
        {"left": 0, "top": 0, "width": 1920, "height": 1080},
        {"left": 1920, "top": -840, "width": 1080, "height": 1920},
        {"left": 3000, "top": -840, "width": 1080, "height": 1920},
    ]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def grab(self, mon):
        return _Shot(mon)


def _reader(settings, monkeypatch, window, elements, mouse=(500, 500)):
    monkeypatch.setattr(uia, "read_foreground", lambda **kw: (window, elements))
    monkeypatch.setattr(uia, "read_taskbar", lambda *a, **k: [])
    monkeypatch.setattr(uia, "cursor_pos", lambda: mouse)
    monkeypatch.setattr(context.ScreenReader, "private_rects", lambda self, skip=None: [])
    monkeypatch.setattr(context.ocr, "read_lines", lambda shot, origin=(0, 0): [])
    monkeypatch.setitem(sys.modules, "mss", types.SimpleNamespace(MSS=_ThreeScreens, mss=_ThreeScreens))
    return context.ScreenReader(settings.screen)


def test_a_window_on_another_screen_is_never_described_under_this_screens_picture(settings, monkeypatch):
    """Live: `[7492] Button "Minimize" @(2037,-598,32x26)` under a 1366 x 768 screenshot."""
    claude = uia.RawWindow("Claude", "claude.exe", (1920, -840, 1080, 1920), 4321)
    its = [uia.RawElement("Minimize", "Button", (2864, -840, 45, 36), 1, None, False, "")]
    ctx = _reader(settings, monkeypatch, claude, its)._capture_sync(True)
    ctx.model_image(1366)
    text = ctx.render()
    assert ctx.origin == (0, 0) and ctx.elements == []
    assert "Minimize" not in text and "Claude" not in text
    assert "No window was read on this screen" in text
    assert ctx.window.hwnd == 0 and ctx.window.rect == (0, 0, 1920, 1080)
    assert ctx.crop(ctx.view_rect()) is not None  # there is still a view to compare later


def test_a_window_across_two_screens_keeps_only_what_is_in_the_picture(settings, monkeypatch):
    wide = uia.RawWindow("Sheet", "excel.exe", (1500, 100, 900, 600), 55)
    both = [
        uia.RawElement("Save", "Button", (1600, 150, 80, 30), 1, None, False, ""),
        uia.RawElement("Share", "Button", (2200, 150, 80, 30), 1, None, False, ""),
    ]
    ctx = _reader(settings, monkeypatch, wide, both, mouse=(1700, 300))._capture_sync(True)
    assert [e.name for e in ctx.elements] == ["Save"] and ctx.window.title == "Sheet"
    assert ctx.view_rect() == (1500, 100, 420, 600)  # the part of it on this screen


def test_with_the_mouse_on_bare_desktop_the_window_read_is_on_the_mouses_screen(monkeypatch):
    """Never the window in front when that is on another screen."""
    monkeypatch.setattr(uia, "cursor_pos", lambda: (500, 500))
    monkeypatch.setattr(uia, "window_at", lambda x, y: None)
    monkeypatch.setattr(uia, "screen_at", lambda x, y: (0, 0, 1920, 1080))
    monkeypatch.setattr(uia, "desktop_window", lambda: 99)
    monkeypatch.setattr(uia, "top_window_on", lambda screen: 77)
    monkeypatch.setattr(uia, "_real_windows", lambda: [(77, (0, 0, 1920, 1040)), (88, (1920, -840, 3000, 1080))])
    monkeypatch.setattr(uia, "_front_window", lambda: 88)  # in front, but on the second screen
    assert uia.window_to_read(True, None) == (77, None)  # the top window of that screen
    monkeypatch.setattr(uia, "top_window_on", lambda screen: None)
    assert uia.window_to_read(True, None) == (99, (0, 0, 1920, 1080))  # its desktop
    monkeypatch.setattr(uia, "window_at", lambda x, y: 12)
    assert uia.window_to_read(True, None) == (12, None)  # a window under the mouse, as ever
    assert uia.window_to_read(True, 5) == (5, None)  # the one the request named
    assert uia.window_to_read(False, None) == (0, None)  # the one in front, when Pyxi drives


def test_the_window_in_front_is_read_not_one_that_floats_on_top_of_it(monkeypatch):
    """The mouse rests on the taskbar. Word is in front, full screen; a small always-on-top
    window (a video in a corner) is first in the pile. The first fix took the top of the
    pile, so the model got the video window's title and buttons under a picture of Word."""
    word, video = 100, 200
    pile = [(video, (1500, 700, 1900, 925)), (word, (-8, -8, 1928, 1048))]  # topmost first
    monkeypatch.setattr(uia, "_real_windows", lambda: pile)
    monkeypatch.setattr(uia, "cursor_pos", lambda: (900, 1060))  # on the taskbar
    monkeypatch.setattr(uia, "screen_at", lambda x, y: (0, 0, 1920, 1080))
    monkeypatch.setattr(uia, "desktop_window", lambda: 99)
    monkeypatch.setattr(uia, "_front_window", lambda: word)
    assert uia.window_to_read(True, None) == (word, None)
    # In front but on another screen: then the top window of the mouse's screen, as before.
    monkeypatch.setattr(uia, "_real_windows", lambda: [(video, (1500, 700, 1900, 925)), (word, (1920, -840, 3000, 1080))])
    assert uia.window_to_read(True, None) == (video, None)
    # The taskbar itself has the keyboard (they clicked it): not a window to read either.
    monkeypatch.setattr(uia, "_real_windows", lambda: pile)
    monkeypatch.setattr(uia, "_front_window", lambda: 555)
    assert uia.window_to_read(True, None) == (video, None)
    monkeypatch.setattr(uia, "_real_windows", lambda: [])
    assert uia.window_to_read(True, None) == (99, (0, 0, 1920, 1080))  # nothing there: the desktop


class _TouchyControl(_Control):
    """A control whose id cannot be read (the app throws), like the error of that evening."""

    @property
    def AutomationId(self):
        raise OSError(-2147220991, "An event was unable to invoke any of the subscribers")


class _OneBadId(_Auto):
    def __init__(self):
        self.walks = 0

    def WalkControl(self, top, includeTop=False, maxDepth=25):
        self.walks += 1
        for i in range(6):
            yield (_TouchyControl() if i == 4 else _Control()), 1


def test_one_control_whose_id_throws_does_not_cost_the_whole_walk(monkeypatch):
    """AutomationId was read outside the guarded block: one control that threw there ended
    the walk, the second try walked everything again to the same end, and the read came back
    twice as slow (1.54 s for 0.70 s) with no elements at all."""
    monkeypatch.setattr(uia, "process_name", lambda pid: "app.exe")
    auto = _OneBadId()
    window, elements = uia._walk(auto, 1.5, 400, 25, follow_mouse=False, named=1234)
    assert window.tree == "ok" and len(elements) == 6
    assert [e.automation_id for e in elements] == [""] * 6
    # Through the front door: one walk, not two.
    _pretend_uiautomation(monkeypatch)
    auto = _OneBadId()
    for name in ("ControlFromHandle", "WalkControl", "PropertyId"):
        setattr(sys.modules["uiautomation"], name, getattr(auto, name))
    window, elements = uia.read_foreground(hwnd=1234)
    assert auto.walks == 1 and len(elements) == 6 and window.title == "Settings"


# ------------------------------------------------------------------ cut off by an edge
def test_a_row_cut_off_by_the_edge_is_said_to_be(settings):
    """Live: `[6883] ListItem "Recovery" @(432,721,716x14)`, and the model had to work out
    from the numbers that it was half under the bottom of the list."""
    rows = ["Display", "Sound", "Notifications", "Focus", "Power", "Storage"]
    els = [UIElement(1, "System", "List", (607, 220, 1006, 812), 0)]
    els += [UIElement(10 + i, name, "ListItem", (607, 220 + 72 * i, 1006, 72), 1, 1) for i, name in enumerate(rows)]
    els.append(UIElement(30, "Recovery", "ListItem", (607, 1013, 1006, 19), 1, 1))
    els.append(UIElement(40, "Start", "TaskbarButton", (700, 1036, 40, 40)))
    shot = Image.new("RGB", (1920, 1080), (30, 30, 30))
    ctx = ScreenContext(time.time(), WindowInfo("Settings", "SystemSettings.exe", (-8, -8, 1936, 1048), 7), els, shot)
    assert ctx.cut_off() == {30: "bottom"}
    recovery = next(ln for ln in ctx.render().splitlines() if '"Recovery"' in ln)
    assert recovery.endswith("(cut off at the bottom edge: only part of it shows)")
    assert all("cut off" not in ln for ln in ctx.render().splitlines() if '"Recovery"' not in ln)
    # A control that runs past the window is cut off too; one wholly inside is not.
    over = UIElement(50, "Next", "Button", (1700, 1000, 200, 120), 1, 1)
    ctx.elements.append(over)
    assert ctx.cut_off()[50] == "bottom"
    # With no list read around it, the window's own edge says the same (its frame hangs
    # 8 px past what a maximised window shows).
    loose = [UIElement(e.id, e.name, e.role, e.rect, 1, None) for e in els if e.role == "ListItem"]
    bare = ScreenContext(time.time(), ctx.window, loose, shot)
    assert bare.cut_off() == {30: "bottom"}


def test_short_fields_in_plain_view_are_not_called_cut_off():
    """Two one-line fields over a tall message box, all of it on screen: "Name" was listed as
    "(cut off at the top edge: only part of it shows)". Any three controls of one kind in a
    column did it when the first or last was under 60% of the usual height. Short is not
    cut: it has to sit on the edge that would do the cutting."""
    form = [
        UIElement(1, "Contact us", "Pane", (200, 100, 900, 700), 0),
        UIElement(2, "Name", "Edit", (260, 180, 600, 36), 1, 1),
        UIElement(3, "Email", "Edit", (260, 236, 600, 36), 1, 1),
        UIElement(4, "Message", "Edit", (260, 292, 600, 280), 1, 1),
        UIElement(5, "Send", "Button", (260, 600, 120, 40), 1, 1),
    ]
    shot = Image.new("RGB", (1600, 900))
    ctx = ScreenContext(time.time(), WindowInfo("Contact - Chrome", "chrome.exe", (0, 0, 1600, 900), 7), form, shot)
    assert ctx.cut_off() == {} and "cut off" not in ctx.render()
    # The same for a short last row in the middle of the window: a "Show more" under a list.
    rows = [UIElement(10 + i, f"Row {i}", "ListItem", (300, 200 + 60 * i, 500, 60), 1, 1) for i in range(4)]
    rows.append(UIElement(20, "Show more", "ListItem", (300, 440, 500, 24), 1, 1))
    ctx = ScreenContext(time.time(), ctx.window, [form[0], *rows], shot)
    assert ctx.cut_off() == {}


# ------------------------------------------------------------------ several marks, one read
class _SlowWords:
    """A screen whose words can be read again (as ScreenReader.text_now does), and counts."""

    def __init__(self, base, lines):
        self.base, self.lines, self.reads = base, lines, 0

    def __getattr__(self, name):
        return getattr(self.base, name)

    async def capture(self, pixels: bool = True, **_):
        ctx = fake_context()
        ctx.text = list(self.lines)
        ctx.captured_at = time.time() - 4.0  # they pressed talk, spoke, and the model answered
        return ctx

    def text_now(self, ctx):
        self.reads += 1
        return list(self.lines)


async def test_three_marks_in_one_reply_read_the_screen_once(settings):
    """Every point, circle and highlight read the whole screen again, one after the other:
    three marks were three reads (155 ms each at 1080p, 630 ms at 4K), each one blanking
    Pyxi's drawings when screen shares can see them."""
    words = [line("Dark mode", (150, 176, 81, 18), 50), line("Export", (730, 660, 54, 18), 51), line("General", (130, 145, 63, 18), 52)]
    marks = [
        ("say", {"text": "Three things to look at."}),
        ("highlight", {"quote": "Dark mode", "label": "theme"}),
        ("circle", {"quote": "Export", "label": "export"}),
        ("point", {"quote": "General", "label": "here"}),
    ]
    brain, rec = make_brain(settings, llm=Scripted(marks))
    brain.screen = _SlowWords(brain.screen, words)
    await brain.turn("where are these?")
    assert len(rec.of("annotate")) == 3 and brain.screen.reads == 1
    # Kept for about a second only: a mark drawn later is checked against the screen then.
    brain, rec = make_brain(settings, llm=Scripted(marks))
    brain.screen = _SlowWords(brain.screen, words)
    brain.REREAD_KEEPS_S = 0.0
    await brain.turn("where are these?")
    assert len(rec.of("annotate")) == 3 and brain.screen.reads == 3
