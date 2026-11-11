"""The step-by-step guide, the cue card's opener and a finished job's two cards.

From the trial of 10 November. "Scroll down to Recovery" stayed framed on a sliver at the
bottom edge after they had scrolled and Recovery was in plain view. After Settings was closed,
Pyxi told them twice, by itself, to start over from the Start button (the second time because
a look that had read no window compared the title in front with ''). A step that could not be
placed left the old one live and deaf to every click. An explanation said its facts in the
opener and again in every point. And one finished job came up on two cards with the same words.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from otto.types import LLMResponse, ToolCall, Usage
from PIL import Image, ImageDraw

from conftest import FakeScreen, fake_context
from pyxi import windows
from pyxi.agents.jobs import Job
from pyxi.companion.brain import Brain, card_talks, opener_only, view_scrolled
from pyxi.companion.notes import guide_left, scroll_area, where_they_were
from pyxi.screen import ScreenContext, UIElement, WindowInfo
from pyxi.screen.context import ScreenReader
from pyxi.screen.ocr import TextLine
from test_answer_card import settle
from test_brain_and_server import make_brain
from test_streaming import Streamed

GOAL = "turn on dark mode"


class Scripted:
    """Plays back tool calls turn by turn (``default`` once they run out); keeps its prompts."""

    def __init__(self, *turns, default=None):
        self.turns = list(turns)
        self.default = default or [("say", {"text": "Okay."})]
        self.prompts: list[str] = []

    async def complete(self, *, system, messages, tools=None, **_):
        self.prompts.append(messages[-1].parts[0].text)
        calls = self.turns.pop(0) if self.turns else self.default
        return LLMResponse(
            tool_calls=[ToolCall(id=str(i), name=n, arguments=a) for i, (n, a) in enumerate(calls)],
            usage=Usage(),
            stop_reason="tool_use",
        )


def step(label: str = "Export", element: int = 4, **more) -> list:
    return [
        ("say", {"text": f"Click {label}."}),
        ("guide", {"goal": GOAL, "element_id": element, "label": label, **more}),
    ]


def said(rec) -> list[str]:
    return [e["text"] for e in rec.of("say")]


def screen_changes(brain, shade: int = 20, window: WindowInfo | None = None) -> None:
    """What is on screen is no longer what it was."""
    now = fake_context()
    now.screenshot = Image.new("RGB", (1600, 900), (shade, shade, shade))
    now.window = window or brain.screen.ctx.window
    brain.screen.ctx = now


def quiet(brain) -> None:
    for task in (brain._watcher, getattr(brain, "_step_watcher", None)):
        if task and not task.done():
            task.cancel()


class Desk(FakeScreen):
    """A pretend screen that knows its windows: which are open and which one is in front."""

    reads_windows = True

    def __init__(self) -> None:
        super().__init__()
        self.titles = {11: "Settings", 22: "Inbox - Mail"}
        self.front = 11
        self.tucked: set[int] = set()
        self.ctx.window = WindowInfo("Settings", "SystemSettings.exe", (0, 0, 1600, 900), 11)

    def window_title(self, hwnd: int) -> str | None:
        return self.titles.get(hwnd)

    async def capture(self, pixels: bool = True, **_) -> ScreenContext:
        return await super().capture(pixels)


@pytest.fixture
def desk(monkeypatch) -> Desk:
    desk = Desk()
    monkeypatch.setattr(windows, "available", lambda: True)
    monkeypatch.setattr(windows, "foreground", lambda: desk.front)
    monkeypatch.setattr(windows, "is_minimized", lambda hwnd: hwnd in desk.tucked)
    monkeypatch.setattr(windows, "same_app", lambda a, b: False)
    monkeypatch.setattr(windows, "open_windows", lambda minimized=False: [(h, t, "") for h, t in desk.titles.items()])
    monkeypatch.setattr(windows, "named_window", lambda text, open_now: None)
    return desk


def brain_at(settings, desk: Desk, llm):
    brain, rec = make_brain(settings, llm=llm)
    brain.screen = desk
    return brain, rec


# ------------------------------------------------------------------ automatic turns
async def test_a_look_that_read_no_window_starts_nothing_by_itself(settings):
    """Live: "(The page changed after step 6...)" 0.6 s after the mark, and "start over from
    the Start button" a second time. A scripted model got ten such turns in twelve seconds."""
    llm = Scripted(default=step("Start button"))
    brain, rec = make_brain(settings, llm=llm)
    brain.screen.ctx.window = WindowInfo()  # the tree failed: no title, no handle
    brain.screen.foreground_title = lambda: "Claude"
    await brain.turn("guide me there")
    await asyncio.sleep(2.4)
    assert len(llm.prompts) == 1 and brain._guide is not None and brain._guide.step == 1
    assert [e["text"] for e in rec.of("user")] == ["guide me there"]
    quiet(brain)


async def test_when_they_have_left_the_window_the_guide_stops_and_waits_for_their_words(settings, desk):
    llm = Scripted(step("Advanced startup"), default=step("Start button", 3))
    brain, rec = brain_at(settings, desk, llm)
    await brain.turn("guide me there")
    assert brain._guide.hwnd == 11 and brain._guide.title == "Settings"
    # They close Settings: the desktop is what's left, and nothing is in front.
    del desk.titles[11]
    desk.front = 0
    screen_changes(brain, window=WindowInfo())
    await brain.guide_clicked("elsewhere")
    assert len(llm.prompts) == 1  # no look, no model, no "start over from the Start button"
    assert brain._guide is None and rec.of("guide")[-1]["active"] is False
    assert "Looks like you've left Settings, so I'll stop here. Say carry on" in " ".join(said(rec))
    assert "walk-through toward 'turn on dark mode' stopped at step 1" in brain.history[-1][0]
    # And nothing more comes by itself: every later click finds no guide to wake.
    await brain.guide_clicked("elsewhere")
    await brain.guide_clicked("moved")
    assert len(llm.prompts) == 1 and len([s for s in said(rec) if s.startswith("Looks like")]) == 1


async def test_tucked_away_or_gone_to_another_open_window_is_leaving_too(settings, desk):
    for leave in ("tuck", "switch"):
        llm = Scripted(step(), default=step("Dark mode", 3))
        brain, rec = brain_at(settings, desk, llm)
        desk.titles, desk.front, desk.tucked = {11: "Settings", 22: "Inbox - Mail"}, 11, set()
        await brain.turn("guide me there")
        if leave == "tuck":
            desk.tucked, desk.front = {11}, 22
        else:
            desk.front = 22  # a window that was already open when the step was drawn
        screen_changes(brain, 20 if leave == "tuck" else 90)
        await brain.guide_clicked("elsewhere")
        assert len(llm.prompts) == 1 and brain._guide is None, leave
        assert "you've left Settings" in " ".join(said(rec))


async def test_a_window_the_step_itself_opened_is_not_leaving(settings, desk):
    """Start menu, then Settings: the menu closes and a new window opens. That is the path."""
    llm = Scripted(step("Settings"), default=step("System", 3))
    brain, rec = brain_at(settings, desk, llm)
    desk.titles, desk.front = {11: "Search", 22: "Inbox - Mail"}, 11
    desk.ctx.window = WindowInfo("Search", "SearchHost.exe", (0, 0, 1600, 900), 11)
    await brain.turn("guide me there")
    desk.titles = {22: "Inbox - Mail", 33: "Settings"}  # the menu is gone, Settings is new
    desk.front = 33
    screen_changes(brain, window=WindowInfo("Settings", "SystemSettings.exe", (0, 0, 1600, 900), 33))
    await brain.guide_clicked("elsewhere")
    assert len(llm.prompts) == 2 and brain._guide is not None and brain._guide.label == "System"
    assert not any("you've left" in s for s in said(rec))
    quiet(brain)


def test_who_has_left():
    known = {11, 22}
    here = dict(exists=True, minimized=False, same_app=False)
    assert not guide_left(11, 11, known, **here)  # still in it
    assert not guide_left(11, 44, known, exists=True, minimized=False, same_app=True)  # its own dialog
    assert guide_left(11, 22, known, **here)  # went to something that was already open
    assert not guide_left(11, 33, known, **here)  # a window that has opened since
    assert guide_left(11, 22, known, exists=True, minimized=True, same_app=False)  # tucked away
    assert guide_left(11, 0, known, exists=False, minimized=False, same_app=False)  # closed: the desktop
    assert not guide_left(11, 0, known, **here)  # nothing in front for a moment, the window still there
    assert not guide_left(0, 22, known, **here)  # no window to go by: nobody can tell
    assert where_they_were("Settings") == "Settings"
    assert where_they_were("Untitled spreadsheet - Google Sheets - Google Chrome") == "Google Chrome"
    assert where_they_were("") == "that window"


async def test_two_turns_by_itself_at_most_then_it_waits(settings):
    labels = iter(["Dark mode", "Settings", "General", "Export"])
    ids = {"Dark mode": 3, "Settings": 1, "General": 2, "Export": 4}

    class Wandering(Scripted):
        async def complete(self, **kw):
            label = next(labels)
            self.default = step(label, ids[label])
            return await super().complete(**kw)

    llm = Wandering()
    brain, rec = make_brain(settings, llm=llm)
    brain.screen.foreground_title = lambda: "Settings - App"
    await brain.turn("guide me there")
    for _ in range(3):  # three times the page changes with nothing done: Pyxi's own guesses
        await brain.guide_clicked("moved")
    assert len(llm.prompts) == 3  # the request and two turns by itself: the third is not taken
    assert brain._guide is None and rec.of("guide")[-1]["active"] is False
    assert "I've lost track of where you are, so I'll stop pointing." in " ".join(said(rec))


async def test_a_click_beside_the_mark_is_not_a_guess_and_gets_five_before_pyxi_gives_up(settings):
    """Live, eight steps in twenty were done a little off the mark. Counted with Pyxi's own
    guesses, three of those in a row ended the walk-through with "I've lost track"."""
    n = iter(range(100))

    class Following(Scripted):
        async def complete(self, **kw):
            self.default = step(f"Step {next(n)}", 1 + next(n) % 4)
            return await super().complete(**kw)

    llm = Following()
    brain, rec = make_brain(settings, llm=llm)
    brain.screen.foreground_title = lambda: "Settings - App"
    await brain.turn("guide me there")
    for shade in (20, 60, 100, 140, 180):
        screen_changes(brain, shade)
        await brain.guide_clicked("elsewhere")
    assert len(llm.prompts) == 6 and brain._guide is not None and brain._guide.step == 6
    assert not any("lost track" in s for s in said(rec))
    screen_changes(brain, 220)
    await brain.guide_clicked("elsewhere")  # the sixth with not one step done on its mark
    assert len(llm.prompts) == 6 and brain._guide is None
    assert "I've lost track of where you are" in " ".join(said(rec))


async def test_a_step_done_in_between_starts_the_count_again(settings):
    llm = Scripted(default=step())
    brain, rec = make_brain(settings, llm=llm)
    brain.screen.foreground_title = lambda: "Settings - App"

    async def off_the_mark(shade: int, label: str, element: int) -> None:
        llm.default = step(label, element)
        screen_changes(brain, shade)
        await brain.guide_clicked("elsewhere")

    await brain.turn("guide me there")
    await off_the_mark(20, "Dark mode", 3)
    await off_the_mark(60, "General", 2)
    llm.default = step("Export", 4)
    await brain.guide_clicked("clicked")  # a real step: they are following again
    await off_the_mark(100, "Dark mode", 3)
    await off_the_mark(140, "General", 2)
    assert len(llm.prompts) == 6 and brain._guide is not None
    assert not any("lost track" in s for s in said(rec))
    quiet(brain)


async def test_the_same_step_is_not_given_again_when_nothing_was_done(settings):
    """Live: "start over from the Start button", then the same again seven seconds later."""
    llm = Scripted(step(), step(), [("say", {"text": "Want to carry on from Export?"})])
    brain, rec = make_brain(settings, llm=llm)
    brain.screen.foreground_title = lambda: "Settings - App"
    await brain.turn("guide me there")
    screen_changes(brain)
    await brain.guide_clicked("elsewhere")
    assert "'Export' is the step you just gave" in llm.prompts[2]
    assert [g["step"] for g in rec.of("guide") if g["active"]] == [1]  # not marked a second time
    # A refused step with nothing after it ends the guide, and the overlay is told.
    assert brain._guide is None and rec.of("guide")[-1]["active"] is False
    assert said(rec)[-1] == "Want to carry on from Export?"


async def test_the_same_button_four_times_running_is_not_called_done(settings):
    """Next, Next, Next, Next is a wizard, not a circle. It ended the guide with a note that
    "the task is done as far as the screen can show", which was simply untrue."""
    llm = Scripted(default=step("Next"))
    brain, _ = make_brain(settings, llm=llm)
    brain.screen.foreground_title = lambda: "Settings - App"
    await brain.turn("guide me there")
    for _ in range(4):
        await brain.guide_clicked("clicked")
    assert brain._guide is not None and brain._guide.step == 5
    assert not any("task is done" in p for p in llm.prompts)
    quiet(brain)


async def test_the_next_step_prompt_agrees_with_the_guide_tool(settings):
    """It said "carry on from where I am" whatever was on screen; the tool said to stop."""
    llm = Scripted(default=step())
    brain, _ = make_brain(settings, llm=llm)
    await brain.turn("guide me there")
    llm.default = step("Dark mode", 3)
    await brain.guide_clicked("done")
    prompt = llm.prompts[1]
    assert "If I went a different way in the same app, carry on from where I am." in prompt
    assert "don't guess and don't start over: say one short line and don't call guide" in prompt
    quiet(brain)


# ------------------------------------------------------------------ a step that isn't a click
def settings_list() -> ScreenContext:
    rows = ["Display", "Sound", "Notifications", "Focus", "Power", "Storage"]
    els = [UIElement(1, "System", "List", (607, 220, 1006, 812), 0)]
    els += [UIElement(10 + i, name, "ListItem", (607, 220 + 72 * i, 1006, 72), 1, 1) for i, name in enumerate(rows)]
    els.append(UIElement(30, "Recovery", "ListItem", (607, 1013, 1006, 19), 1, 1))
    shot = Image.new("RGB", (1920, 1080), (30, 30, 30))
    return ScreenContext(time.time(), WindowInfo("Settings", "SystemSettings.exe", (-8, -8, 1936, 1048), 7), els, shot)


def test_a_scroll_step_is_marked_on_the_list_not_on_the_row_under_its_edge():
    ctx = settings_list()
    assert scroll_area(ctx, 30, (607, 1013, 1006, 19)) == (607, 220, 1006, 812)
    # Nothing to go by (a box, a quote): the part of the window that is on screen.
    assert scroll_area(ctx, None, (607, 1013, 1006, 19)) == (0, 0, 1920, 1040)


async def test_scroll_down_to_recovery_frames_the_list(settings):
    llm = Scripted([("say", {"text": "Scroll down a little."}), ("guide", {"goal": GOAL, "action": "scroll", "direction": "down", "element_id": 30, "label": "Recovery"})])
    brain, rec = make_brain(settings, llm=llm)
    brain.screen.ctx = settings_list()
    await brain.turn("guide me there")
    [mark] = rec.of("annotate")[0]["annotations"]
    assert mark["rect"] == [607, 220, 1006, 812] and mark["action"] == "scroll" and mark["hint"] == "down"
    quiet(brain)


async def test_a_scroll_step_with_no_target_takes_the_window(settings):
    llm = Scripted([("say", {"text": "Scroll down."}), ("guide", {"goal": GOAL, "action": "scroll", "direction": "down", "label": "the page"})])
    brain, rec = make_brain(settings, llm=llm)
    await brain.turn("guide me there")
    assert rec.of("annotate")[0]["annotations"][0]["rect"] == [0, 0, 1600, 900]
    quiet(brain)


async def test_a_scroll_step_is_seen_done_when_the_view_changes(settings):
    """Live: the frame and "scroll down" stayed where they were after the scroll."""
    scroll = [("say", {"text": "Scroll down."}), ("guide", {"goal": GOAL, "action": "scroll", "direction": "down", "label": "Recovery"})]
    llm = Scripted(scroll, default=step("Recovery", 3))
    brain, _ = make_brain(settings, llm=llm)
    brain.screen.foreground_title = lambda: "Settings - App"
    await brain.turn("guide me there")
    await asyncio.sleep(1.7)  # the mark is up, and how the window looks is noted
    assert len(llm.prompts) == 1  # nothing happens while nothing changes
    screen_changes(brain)  # they scroll
    for _ in range(60):
        if len(llm.prompts) > 1:
            break
        await asyncio.sleep(0.1)
    assert "The screen changed after step 1 (scroll 'Recovery'), so I think I did it." in llm.prompts[1]
    await settle(brain)
    assert brain._guide is not None and (brain._guide.step, brain._guide.action) == (2, "click")
    quiet(brain)


async def test_it_looks_only_once_they_have_touched_something_and_gone_quiet(settings):
    scroll = [("say", {"text": "Type it."}), ("guide", {"goal": GOAL, "action": "press", "text": "Enter", "label": "Enter"})]
    llm = Scripted(scroll, default=step("Recovery", 3))
    brain, _ = make_brain(settings, llm=llm)
    brain.screen.foreground_title = lambda: "Settings - App"
    idle = {"s": 30.0}
    brain.screen.input_idle_s = lambda: idle["s"]
    looks = []
    capture = brain.screen.capture

    async def counted(pixels: bool = True):
        looks.append(1)
        return await capture(pixels)

    brain.screen.capture = counted
    await brain.turn("guide me there")
    await asyncio.sleep(2.6)
    # The turn's own look, and how the window looked as the mark went up: for a click off
    # the mark, and for this (it was a third look, taken once Pyxi had finished talking).
    assert len(looks) == 2
    idle["s"] = 0.1  # they are at the keyboard
    screen_changes(brain)
    brain.screen.capture = counted
    await asyncio.sleep(1.4)
    assert len(looks) == 2 and len(llm.prompts) == 1  # not while they are still at it
    idle["s"] = 1.5  # and now they have stopped
    for _ in range(60):
        if len(llm.prompts) > 1:
            break
        await asyncio.sleep(0.1)
    assert "so I think I did it" in llm.prompts[1]
    await settle(brain)
    quiet(brain)


def drawn_list(scroll: int = 0, lit: int | None = None, tint: int = 60, tip: bool = False) -> Image.Image:
    """A made-up settings window: a side list that stays put, and rows of "words" (bars of
    different lengths) that scroll. ``lit``: the row the mouse rests on."""
    img = Image.new("RGB", (1600, 900), (32, 32, 32))
    d = ImageDraw.Draw(img)
    for i in range(12):
        d.rectangle([40, 120 + 36 * i, 40 + 60 + 13 * (i % 5), 132 + 36 * i], fill=(220, 220, 220))
    for i in range(24):
        y = 150 + 72 * i - scroll
        if y < 100 or y > 900:
            continue
        shade = 44 + (tint if lit == i else 0)
        d.rectangle([420, y, 1400, y + 66], fill=(shade, shade, shade))
        d.rectangle([470, y + 14, 470 + 90 + 37 * (i * 7 % 6), y + 28], fill=(250, 250, 250))
        d.rectangle([470, y + 40, 470 + 200 + 53 * (i * 5 % 7), y + 50], fill=(170, 170, 170))
    if tip:
        d.rectangle([800, 400, 980, 428], fill=(70, 70, 70))
        d.rectangle([808, 408, 960, 420], fill=(240, 240, 240))
    return img


def test_a_hover_tint_a_tooltip_or_a_video_frame_is_not_a_scroll():
    """`_same_view` calls six pixels of 480 x 270 a change. For a scroll step that made a
    row's hover colour "the screen changed, so I think I did it": thirteen turns by itself
    in forty seconds, and "Scroll down to Recovery." fourteen times."""
    plain = drawn_list()
    assert not view_scrolled(plain, drawn_list())
    assert not view_scrolled(plain, drawn_list(lit=3))  # the row under the mouse lights up
    assert not view_scrolled(plain, drawn_list(lit=3, tint=12))
    assert not view_scrolled(drawn_list(lit=1), drawn_list(lit=2))  # the mouse moved a row down
    assert not view_scrolled(drawn_list(lit=0), drawn_list(lit=8))  # ...or to the far end
    assert not view_scrolled(plain, drawn_list(tip=True))
    noise = drawn_list()
    ImageDraw.Draw(noise).rectangle([700, 300, 1000, 500], fill=(200, 60, 60))  # a video frame
    assert not view_scrolled(plain, noise)
    # A scroll is the same content, moved: by a wheel notch, a few pixels, or most of a page.
    for by in (144, 37, 100, 360):
        assert view_scrolled(plain, drawn_list(by)), by
    assert view_scrolled(plain, Image.new("RGB", (1600, 900), (250, 250, 250)))  # a jump: all new
    sideways = [p.rotate(90, expand=True) for p in (plain, drawn_list(144))]
    assert view_scrolled(*sideways)


async def test_resting_the_mouse_on_a_row_does_not_start_a_turn(settings):
    scroll = [("say", {"text": "Scroll down to Recovery."}), ("guide", {"goal": GOAL, "action": "scroll", "direction": "down", "label": "Recovery"})]
    llm = Scripted(default=scroll)
    brain, rec = make_brain(settings, llm=llm)
    brain.screen.foreground_title = lambda: "Settings - App"
    brain.screen.ctx.screenshot = drawn_list()
    idle = {"s": 30.0}
    brain.screen.input_idle_s = lambda: idle["s"]
    await brain.turn("guide me there")
    await asyncio.sleep(1.8)
    for row in (2, 5):  # they move the mouse and rest it, twice: a row lights up each time
        idle["s"] = 0.1
        now = fake_context()
        now.screenshot = drawn_list(lit=row)
        brain.screen.ctx = now
        await asyncio.sleep(0.8)
        idle["s"] = 1.5
        await asyncio.sleep(1.6)
    assert len(llm.prompts) == 1 and brain._guide is not None and brain._guide.step == 1
    assert sum("Scroll down" in s for s in said(rec)) == 1
    # And when they do scroll, it is seen.
    idle["s"] = 0.1
    now = fake_context()
    now.screenshot = drawn_list(144)
    brain.screen.ctx = now
    await asyncio.sleep(0.8)
    idle["s"] = 1.5
    for _ in range(60):
        if len(llm.prompts) > 1:
            break
        await asyncio.sleep(0.1)
    assert len(llm.prompts) > 1 and "so I think I did it" in llm.prompts[1]
    await settle(brain)
    quiet(brain)


async def test_a_turn_from_seeing_the_screen_change_counts_like_any_other_by_itself(settings):
    """"Seen" set the count of automatic turns back to nought, so there was no end to them.
    (A step seen done that leads to a new step is taken off the count again: that is how
    every step goes in a window whose clicks can't be heard. What still ends it is the page
    changing by itself, twice, with nothing new to give in between.)"""
    labels = iter(["Dark mode", "Settings", "General", "Export"])
    ids = {"Dark mode": 3, "Settings": 1, "General": 2, "Export": 4}

    class Wandering(Scripted):
        async def complete(self, **kw):
            label = next(labels)
            self.default = step(label, ids[label])
            return await super().complete(**kw)

    llm = Wandering()
    brain, rec = make_brain(settings, llm=llm)
    brain.screen.foreground_title = lambda: "Settings - App"
    await brain.turn("guide me there")
    await brain.guide_clicked("seen")
    assert brain._guide_autos == 0 and brain._guide.step == 2  # seen done, and a new step: following
    await brain.guide_clicked("moved")
    await brain.guide_clicked("moved")
    assert brain._guide_autos == 2 and len(llm.prompts) == 4
    await brain.guide_clicked("moved")  # the third in a row: not taken
    assert len(llm.prompts) == 4
    assert brain._guide is None
    assert "I've lost track of where you are" in " ".join(said(rec))


async def test_the_same_scroll_is_asked_once_more_and_not_a_third_time(settings):
    """It was left out of the refusal, so "Scroll down to Recovery." came again and again.
    Then it was refused outright, and a scroll that had not gone far enough ended the
    walk-through. They did scroll: the same scroll may be asked for once more, as the same
    step, and not again after that."""
    scroll = [("say", {"text": "Scroll down to Recovery."}), ("guide", {"goal": GOAL, "action": "scroll", "direction": "down", "label": "Recovery"})]
    llm = Scripted(scroll, scroll, scroll, [("say", {"text": "Still below. Want to keep going?"})])
    brain, rec = make_brain(settings, llm=llm)
    brain.screen.foreground_title = lambda: "Settings - App"
    await brain.turn("guide me there")
    await brain.guide_clicked("seen")
    assert brain._guide is not None and brain._guide.again and brain._guide.step == 1
    assert [g["step"] for g in rec.of("guide") if g["active"]] == [1, 1]
    await brain.guide_clicked("seen")
    assert "'Recovery' is the step you just gave" in llm.prompts[3]
    assert [g["step"] for g in rec.of("guide") if g["active"]] == [1, 1]
    assert brain._guide is None and rec.of("guide")[-1]["active"] is False


async def test_a_click_step_seen_changed_is_still_not_given_twice(settings):
    llm = Scripted(step(), step(), [("say", {"text": "Want to carry on from Export?"})])
    brain, _ = make_brain(settings, llm=llm)
    brain.screen.foreground_title = lambda: "Settings - App"
    await brain.turn("guide me there")
    await brain.guide_clicked("seen")
    assert "'Export' is the step you just gave" in llm.prompts[2]
    assert brain._guide is None


# ------------------------------------------------------------------ a key on screen
KEY = "OPENAI_API_KEY=sk-proj-abcdefghijklmnopqrstuvwxyz012345"


def page_with_a_key() -> Image.Image:
    img = Image.new("RGB", (1600, 900), (240, 240, 250))
    d = ImageDraw.Draw(img)
    for i in range(0, 700, 9):  # stands in for the key's letters
        d.rectangle([300 + i, 304, 304 + i, 320], fill=(30, 30, 30))
    return img


class KeyOnScreen(FakeScreen):
    """Does what ScreenReader does: a look for the model has secrets blacked out of its
    pixels; a glance (to see whether anything changed) is the pixels as they are."""

    def __init__(self) -> None:
        super().__init__()
        self.glances = 0
        self.ctx = self.look()

    def look(self) -> ScreenContext:
        ctx = fake_context()
        ctx.screenshot = page_with_a_key()
        ctx.text = [TextLine(KEY, (300, 300, 700, 24), [(KEY, (300, 300, 700, 24))], id=50)]
        ctx.text = ScreenReader._hide_secrets(ctx.screenshot, ctx.origin, ctx.elements, ctx.text)
        return ctx

    async def capture(self, pixels: bool = True, **_) -> ScreenContext:
        self.ctx = self.look()
        return self.ctx

    def glance(self, rect):
        self.glances += 1
        x, y, w, h = rect
        return page_with_a_key().crop((x, y, x + w, y + h))


async def test_a_stray_click_on_a_page_showing_a_key_starts_nothing(settings):
    """How the window looked was cut from the look the model got (the key blacked out) and
    compared with a plain glance (the key in it): on such a page every click that changed
    nothing "changed the screen", and Pyxi started a turn."""
    llm = Scripted(default=step())
    brain, _ = make_brain(settings, llm=llm)
    brain.screen = KeyOnScreen()
    await brain.turn("guide me there")
    assert brain.screen.glances == 1  # how it looks, taken the way it will be taken again
    for _ in range(3):
        await brain.guide_clicked("elsewhere")  # a click that changed nothing at all
    assert len(llm.prompts) == 1 and brain._guide is not None and brain._guide.step == 1
    assert brain.screen.glances >= 4  # each click: is the page still arriving, then did it change
    # The glance has the key in it, so it goes nowhere: the model got the look with the key
    # blacked out and nothing else, and nothing of the glance is kept with the turn.
    assert len(brain._images) == 1
    turn = brain.turns.recent(1)[0]
    assert "sk-proj" not in str(turn["screen_text"]) and "sk-proj" not in str(turn["tool_calls"])
    # A click that does change the page is still heard.
    brain.screen.glance = lambda rect: Image.new("RGB", (rect[2], rect[3]), (20, 20, 20))
    llm.default = step("Dark mode", 3)
    await brain.guide_clicked("elsewhere")
    assert len(llm.prompts) == 2
    quiet(brain)


async def test_a_click_step_is_not_watched_for_changes(settings):
    llm = Scripted(default=step())
    brain, _ = make_brain(settings, llm=llm)
    await brain.turn("guide me there")
    watcher = getattr(brain, "_step_watcher", None)
    assert watcher is None or watcher.done()
    quiet(brain)


# ------------------------------------------------------------------ a look that read no window
async def test_with_no_window_read_a_click_elsewhere_is_still_heard(settings):
    """The "did anything change" picture was cut from a window 0 px wide, so there was none,
    and every click off the mark was ignored."""
    llm = Scripted(default=step())
    brain, _ = make_brain(settings, llm=llm)
    brain.screen.ctx.window = WindowInfo()
    await brain.turn("guide me there")
    assert brain._guide_glance is not None and brain._guide_view == (0, 0, 1600, 900)
    llm.default = step("Dark mode", 3)
    screen_changes(brain, window=WindowInfo())
    await brain.guide_clicked("elsewhere")
    assert len(llm.prompts) == 2 and brain._guide.label == "Dark mode"
    quiet(brain)


async def test_a_key_to_press_with_no_window_read_is_marked_on_the_screen(settings):
    llm = Scripted([("say", {"text": "Press Escape."}), ("guide", {"goal": GOAL, "action": "press", "text": "Escape", "label": "Escape"})])
    brain, rec = make_brain(settings, llm=llm)
    brain.screen.ctx.window = WindowInfo()
    await brain.turn("guide me there")
    assert rec.of("annotate")[0]["annotations"][0]["rect"] == [0, 0, 1600, 900]  # it was 0, 0, 0, 0
    quiet(brain)


# ------------------------------------------------------------------ a step that couldn't be placed
async def test_a_step_that_cant_be_placed_ends_the_guide(settings):
    """The old step stayed live, already answered, so every later click and Done was ignored
    and its mark could sit on a target that was gone for fifteen minutes."""
    llm = Scripted(
        step(),
        [("say", {"text": "Now click Save."}), ("guide", {"goal": GOAL, "element_id": 999, "label": "Save"})],
        [("say", {"text": "Save is at the top right."})],
    )
    brain, rec = make_brain(settings, llm=llm)
    brain.screen.foreground_title = lambda: "Settings - App"
    await brain.turn("guide me there")
    await brain.guide_clicked("clicked")
    assert "guide: couldn't find 'Save' on screen" in llm.prompts[2]
    assert brain._guide is None and rec.of("guide")[-1]["active"] is False
    assert said(rec)[-1] == "Save is at the top right."


async def test_a_step_refused_and_then_placed_keeps_the_guide(settings):
    llm = Scripted(
        step(),
        [("guide", {"goal": GOAL, "element_id": 999, "label": "Save"})],
        step("Dark mode", 3),
    )
    brain, rec = make_brain(settings, llm=llm)
    brain.screen.foreground_title = lambda: "Settings - App"
    await brain.turn("guide me there")
    await brain.guide_clicked("clicked")
    assert brain._guide is not None and brain._guide.label == "Dark mode"
    assert rec.of("guide")[-1]["active"] is True
    quiet(brain)


# ------------------------------------------------------------------ the opener of a cue card
OPENER = (
    "It's automated: her name is on it, and the address is PwC's job-alerts system. "
    "The slot is in the body: Wednesday 11 November, 8 AM Pacific."
)
CARD = {
    "title": "Who sent this, and when",
    "closing": "Want me to draft that reply?",
    "items": [
        {"text": "Sent under Dana's name", "element_id": 3, "say": "The sender name is Dana Reyes, but the mail comes from a jobalerts address."},
        {"text": "Interview: tomorrow, 8 AM Pacific", "element_id": 4, "say": "The date and time are in the body: Wednesday the 11th, 8 AM Pacific."},
    ],
}


async def spoken_after(brain, rec, text: str) -> str:
    await brain.turn(text)
    for _ in range(100):
        if any("draft" in s for s in said(rec)):
            break
        await asyncio.sleep(0.02)
    return " ".join(said(rec))


async def test_the_opener_is_one_sentence_when_the_points_speak_for_themselves(settings):
    """Live: "8 AM Pacific" in the opener, and again from the point about the time."""
    llm = Scripted([("say", {"text": OPENER}), ("explain_on_screen", CARD)])
    brain, rec = make_brain(settings, llm=llm)
    heard = await spoken_after(brain, rec, "what is this email?")
    assert heard.startswith("It's automated: her name is on it")
    assert heard.count("8 AM Pacific") == 1 and "The slot is in the body" not in heard
    assert "jobalerts address" in heard and heard.endswith("Want me to draft that reply?")
    quiet(brain)


async def test_the_same_when_the_reply_arrives_in_pieces(settings):
    llm = Streamed([("say", {"text": OPENER}), ("explain_on_screen", CARD)])
    brain, rec = make_brain(settings, llm=llm)
    heard = await spoken_after(brain, rec, "what is this email?")
    assert said(rec)[0] == "It's automated: her name is on it, and the address is PwC's job-alerts system."
    assert heard.count("8 AM Pacific") == 1 and "The slot is in the body" not in heard
    assert heard.count("jobalerts address") == 1
    quiet(brain)


async def test_an_opener_before_anything_else_is_said_whole(settings):
    plain = {"title": "Gist", "closing": "Want me to draft that reply?", "items": [{"text": "Dark mode flips it", "element_id": 3}]}
    for make in (Scripted, Streamed):
        calls = [("say", {"text": OPENER}), ("explain_on_screen", plain)]
        llm = Scripted(calls) if make is Scripted else Streamed(calls)
        brain, rec = make_brain(settings, llm=llm)
        heard = await spoken_after(brain, rec, "what is this email?")
        # The points have no sentences of their own here (the card's words are read): the
        # opener is what explains, so all of it is said.
        assert heard.count("8 AM Pacific") == 1 and "The slot is in the body" in heard, make
        quiet(brain)
    llm = Streamed([("say", {"text": OPENER}), ("pin_note", {"element_id": 3, "note": "here"})])
    brain, rec = make_brain(settings, llm=llm)
    await brain.turn("note that")
    assert "The slot is in the body" in " ".join(said(rec))  # no card at all: nothing is held back
    quiet(brain)


def test_what_the_opener_is():
    assert opener_only("It's a repost. Here's what to look at. And more.") == "It's a repost."
    assert opener_only("Sure. It's a LinkedIn profile. He is an accountant.") == "Sure. It's a LinkedIn profile."
    assert opener_only("Big if true, but read it twice. [note:1]") == "Big if true, but read it twice. [note:1]"
    assert opener_only("A failing test. [note:1] Then the fix.") == "A failing test. [note:1]"
    assert opener_only("One line only.") == "One line only." and opener_only("") == ""
    assert card_talks([{"text": "a", "say": "A."}]) and not card_talks([{"text": "a"}])
    assert not card_talks(None) and not card_talks([{"text": "a", "say": "  "}])


# ------------------------------------------------------------------ one finished job, one place for its words
@pytest.fixture
def no_wait_for_silence(monkeypatch):
    monkeypatch.setattr(Brain, "QUIET_BEFORE_SPEAKING_S", 0.0)


def finished(brain, summary: str, agent: str = "Scout", role: str = "browser") -> Job:
    job = Job(agent_id=agent.lower(), agent_name=agent, role=role, task="Look it up.", status="done", summary=summary)
    brain.jobs.jobs[job.id] = job
    return job


async def test_one_long_sentence_is_not_a_result_that_was_cut(settings, no_wait_for_silence):
    """A sentence over 220 characters was broken at a comma into "sentences" for the voice,
    the notice took the first two pieces, and so the whole thing "didn't fit": a second card
    came up beside the notice with the same words."""
    long = (
        "Made a pivot table on tab Pivot 2 with the sum of Sales by Region, made a pivot table "
        "on tab Pivot 3 with the sum of Sales by Region, and added a column chart titled Sales "
        "by Region on the Pivot tab at E2, using the East, North and South rows only. It "
        "updates when the data does."
    )
    assert len(long.split(". ")[0]) > 220
    brain, rec = make_brain(settings)
    await brain._job_done(finished(brain, long))
    await settle(brain)
    [ready] = rec.of("job_ready")
    assert " ".join(ready["body"].split()) == long
    assert rec.of("answer") == []


async def test_when_the_card_holds_the_result_the_notice_is_its_first_sentence(settings, no_wait_for_silence):
    summary = "Nine IKEA tables on Amazon.ca, from $34.99.\n" + "\n".join(
        f"- Table {n}, ${30 + n}.99, 4.{n} stars" for n in range(1, 10)
    )
    brain, rec = make_brain(settings)
    await brain._job_done(finished(brain, summary))
    await settle(brain)
    [ready] = rec.of("job_ready")
    [card] = rec.of("answer")
    assert len(card["card"]["facts"]) == 10  # all of it, on the card
    assert ready["title"] == "Scout finished"
    assert ready["body"] == "Nine IKEA tables on Amazon.ca, from $34.99."  # not the paragraph again
    assert "Table 2" not in ready["body"]
