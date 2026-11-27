"""A walk-through that doesn't stick, and marks that sit on what is clicked.

From the morning of 11 November, on the build of the night before. "Guide me to the boot
settings": step 2 was "scroll down to Recovery". They scrolled, and clicked Recovery, before
Pyxi had finished saying so, and nothing came: the scroll was looked for against a picture
taken after the sentence ended. Then they said "Done." and that was taken for "okay, got
it", answered with nothing, and the walk-through ended without a word.

And from every walk-through in their history: eight steps in twenty were "you clicked
something else" when they had done the step. A switch was marked on the words beside it, a
Start menu icon on its caption, a menu row on its words alone; and the first letters typed
into a field were "something else" too ("there's a stray V in the code box").
"""

from __future__ import annotations

import asyncio
import time

from PIL import Image, ImageDraw

from conftest import FakeScreen, fake_context
from pyxi.companion.brain import (
    same_goal,
    scroll_words,
    step_said_done,
    view_replaced,
    view_scrolled,
)
from pyxi.companion.notes import Guide, step_rect
from pyxi.screen import ScreenContext, UIElement, WindowInfo
from pyxi.screen.ocr import TextLine
from test_answer_card import settle
from test_audit_guide import GOAL, Scripted, drawn_list, quiet, said, screen_changes, step
from test_brain_and_server import make_brain

SCROLL = [
    ("say", {"text": "Scroll down to Recovery."}),
    ("guide", {"goal": GOAL, "action": "scroll", "direction": "down", "label": "Recovery", "quote": "Recovery"}),
]


def line(text: str, rect: tuple[int, int, int, int], id: int = 50) -> TextLine:
    x, y, w, h = rect
    words = text.split()
    each = w // max(len(words), 1)
    return TextLine(text, rect, [(wd, (x + i * each, y, each - 4, h)) for i, wd in enumerate(words)], id=id)


async def until(done, tries: int = 80) -> None:
    for _ in range(tries):
        if done():
            return
        await asyncio.sleep(0.1)


# ------------------------------------------------------------------ "done", said aloud
def test_what_counts_as_done_said_aloud():
    for words in (
        "Done.", "done", "Okay, done.", "Pyxi, done", "I did it", "I've done that", "I clicked it",
        "I scrolled down", "Yeah I have done that now", "I'm done", "I am done", "It's open", "Next",
        "next step", "What's next?", "Okay, what now?", "Done, what's next?", "Now what?", "Then?",
        "carry on", "Continue", "go ahead", "Okay", "Got it", "Okay, got it.", "Yes",
    ):
        assert step_said_done(words), words
    for words in (
        "Okay. Thank you.", "Thanks", "what is this", "where is it", "I can't find it", "stop",
        "no", "wait", "not done", "I'm not done", "it's not there", "what does next mean",
        "done with this, open YouTube", "can you do it for me", "",
    ):
        assert not step_said_done(words), words


async def test_done_said_aloud_is_the_step_done(settings):
    llm = Scripted(SCROLL, default=step("Recovery", 3))
    brain, rec = make_brain(settings, llm=llm)
    brain.screen.foreground_title = lambda: "Settings - App"
    await brain.turn("guide me there")
    quiet(brain)
    await brain.turn("Done.")
    assert len(llm.prompts) == 2
    assert "About step 1 (scroll 'Recovery') I said: \"Done.\"" in llm.prompts[1]
    assert brain._guide is not None and (brain._guide.step, brain._guide.label) == (2, "Recovery")
    assert [e["text"] for e in rec.of("user")] == ["guide me there", "Done."]  # their own words
    assert said(rec)[-1] == "Click Recovery."
    quiet(brain)


async def test_okay_mid_walk_through_is_not_silence(settings):
    llm = Scripted(default=step())
    brain, _ = make_brain(settings, llm=llm)
    brain.screen.foreground_title = lambda: "Settings - App"
    await brain.turn("guide me there")
    llm.default = step("Dark mode", 3)
    await brain.turn("Okay, got it.")
    assert len(llm.prompts) == 2 and brain._guide is not None and brain._guide.step == 2
    # With no walk-through under way it still asks for nothing, and gets nothing.
    llm.default = [("say", {"text": "Sure."})]
    await brain.turn("leave it there for now")  # a reply with no step ends it
    assert brain._guide is None
    before = len(llm.prompts)
    await brain.turn("Okay, got it.")
    assert len(llm.prompts) == before
    quiet(brain)


async def test_thanks_still_ends_it_with_one_word(settings):
    llm = Scripted(default=step())
    brain, rec = make_brain(settings, llm=llm)
    await brain.turn("guide me there")
    await brain.turn("Okay. Thank you.")
    assert len(llm.prompts) == 1 and brain._guide is None
    assert said(rec)[-1] in ("Anytime.", "You're welcome.", "Any time.")


async def test_done_said_over_a_next_step_that_was_cut_short_still_gets_one(settings):
    """They click the mark and say "done" as well. Pressing talk cut the turn their click had
    started: it marked nothing, so the walk-through ended, and "done" found nothing to do."""
    llm = Scripted(default=step())
    brain, _ = make_brain(settings, llm=llm)
    brain.screen.foreground_title = lambda: "Settings - App"
    await brain.turn("guide me there")
    first = brain._guide
    brain._guide_advanced = first  # their click was heard, and its turn began...
    await brain._end_guide()  # ...and was cut short: no step came of it
    brain._guide = None
    llm.default = step("Dark mode", 3)
    brain._guide = first  # (as it was when they spoke)
    await brain.turn("Done.")
    assert brain._guide is not None and brain._guide is not first and brain._guide.label == "Dark mode"
    assert brain._guide.step == 2
    quiet(brain)


async def test_done_said_while_the_next_step_is_on_its_way_adds_nothing(settings):
    llm = Scripted(default=step())
    brain, _ = make_brain(settings, llm=llm)
    brain.screen.foreground_title = lambda: "Settings - App"
    await brain.turn("guide me there")
    llm.default = step("Dark mode", 3)
    click = asyncio.ensure_future(brain.guide_clicked("clicked"))
    await asyncio.sleep(0.05)
    await asyncio.gather(click, brain.turn("Done."))
    assert len(llm.prompts) == 2 and brain._guide.step == 2  # one next step, not two
    quiet(brain)


async def test_asking_where_it_is_keeps_the_count(settings):
    llm = Scripted(default=step())
    brain, _ = make_brain(settings, llm=llm)
    brain.screen.foreground_title = lambda: "Settings - App"
    await brain.turn("guide me there")
    llm.default = step("Dark mode", 3)
    await brain.guide_clicked("clicked")
    assert brain._guide.step == 2
    await brain.turn("where is that? I can't see it")  # the same step, shown again
    assert brain._guide is not None and brain._guide.step == 2
    llm.default = step("Export", 4, goal="open the Vercel dashboard and add a variable")
    await brain.turn("now show me how to add a variable in Vercel")  # something else: from 1
    assert brain._guide.step == 1
    quiet(brain)


def test_the_same_goal_in_other_words():
    live = Guide(goal="Open UEFI firmware settings (BIOS) from Windows Recovery, without the Delete key", label="System", rect=(0, 0, 10, 10))
    assert same_goal(live, "Open UEFI firmware settings from Windows Recovery")
    assert same_goal(live, live.goal)
    assert not same_goal(live, "Change the Facebook password")
    assert not same_goal(None, "anything")


# ------------------------------------------------------------------ quicker than Pyxi's sentence
async def test_a_scroll_made_while_pyxi_was_still_talking_is_seen(settings):
    llm = Scripted(SCROLL, default=step("Recovery", 3))
    brain, _ = make_brain(settings, llm=llm)
    brain.screen.foreground_title = lambda: "Settings - App"
    brain.screen.ctx.screenshot = drawn_list()
    touched = {"at": time.monotonic() - 30.0}
    brain.screen.input_idle_s = lambda: time.monotonic() - touched["at"]
    await brain.turn("guide me there")
    # The mark is up and Pyxi is still saying the step: they scroll at once, and then wait
    # for Pyxi, hands off the mouse.
    await asyncio.sleep(0.2)
    now = fake_context()
    now.screenshot = drawn_list(144)
    brain.screen.ctx = now
    touched["at"] = time.monotonic()
    await until(lambda: len(llm.prompts) > 1)
    assert len(llm.prompts) > 1 and "so I think I did it" in llm.prompts[1]
    await settle(brain)
    assert brain._guide is not None and brain._guide.step == 2
    quiet(brain)


def dark_list(first: float = 0, lit: int | None = None) -> Image.Image:
    """A list as Windows Settings draws one: dark rows a shade lighter than the window, one
    line of words in each (a bar, a different length for each row). ``first``: the row at
    the top, so 2 is the list scrolled by two whole rows."""
    img = Image.new("RGB", (720, 430), (32, 32, 32))
    d = ImageDraw.Draw(img)
    for i in range(30):
        top = 20 + 72 * (i - first)
        if top < -72 or top > 430:
            continue
        shade = 43 + (30 if lit == i else 0)
        d.rectangle([30, top, 690, top + 60], fill=(shade, shade, shade), outline=(58, 58, 58))
        d.rectangle([60, top + 24, 60 + 50 + 23 * (i * 7 % 9), top + 38], fill=(255, 255, 255))
    return img


def test_a_real_list_scrolled_by_whole_rows_is_a_scroll():
    """Measured on real pixels: scrolled by 2, 6 or 11 rows, 2% of the view changed, in ten
    thin stripes (each row lands where another row was, so only the words differ). That was
    "too sparse to be a scroll", and the walk-through sat on "scroll down to Recovery"."""
    plain = dark_list()
    for rows in (0.5, 1, 2, 6, 11):
        assert view_scrolled(plain, dark_list(rows)), rows
    assert not view_scrolled(plain, dark_list())
    assert not view_scrolled(plain, dark_list(lit=2))  # the row under the mouse
    assert not view_scrolled(dark_list(lit=0), dark_list(lit=4))  # the mouse moved down the list
    assert not view_scrolled(dark_list(lit=1), dark_list(lit=2))


def test_another_page_is_told_from_a_hover():
    plain = dark_list()
    assert view_replaced(plain, dark_list(6))  # other words in every row
    assert view_replaced(plain, Image.new("RGB", (720, 430), (245, 245, 245)))  # all new
    assert not view_replaced(plain, dark_list())
    assert not view_replaced(plain, dark_list(lit=2))
    assert not view_replaced(dark_list(lit=0), dark_list(lit=4))
    video = dark_list()
    ImageDraw.Draw(video).rectangle([200, 100, 520, 300], fill=(200, 60, 60))
    assert not view_replaced(plain, video)  # one thick stripe: something playing on the page


async def test_a_click_that_is_never_heard_is_still_seen_done(settings):
    """A window run as administrator (Task Manager, the Registry Editor): the hook that hears
    clicks hears nothing there, so a click step waited for ever. Now the page having plainly
    become another page, once they have been quiet a while, is the step done."""
    llm = Scripted(default=step())
    brain, _ = make_brain(settings, llm=llm)
    brain.screen.foreground_title = lambda: "Settings - App"
    brain.screen.ctx.screenshot = dark_list().resize((1600, 900))
    touched = {"at": time.monotonic() - 30.0}
    brain.screen.input_idle_s = lambda: time.monotonic() - touched["at"]
    await brain.turn("guide me there")
    # The mouse comes to rest on a row, which lights up: not a step.
    hover = fake_context()
    hover.screenshot = dark_list(lit=2).resize((1600, 900))
    brain.screen.ctx = hover
    touched["at"] = time.monotonic()
    await asyncio.sleep(4.2)
    assert len(llm.prompts) == 1 and brain._guide.step == 1
    # They click (nobody hears it) and the next page comes up.
    llm.default = step("Dark mode", 3)
    page = fake_context()
    page.screenshot = dark_list(4).resize((1600, 900))
    brain.screen.ctx = page
    touched["at"] = time.monotonic()
    await asyncio.sleep(1.5)
    assert len(llm.prompts) == 1  # not at once: a click that was heard gets there first
    await until(lambda: len(llm.prompts) > 1)
    assert len(llm.prompts) == 2 and "so I think I did it" in llm.prompts[1]
    await settle(brain)
    assert brain._guide is not None and (brain._guide.step, brain._guide.label) == (2, "Dark mode")
    quiet(brain)


async def test_steps_seen_done_one_after_another_are_someone_following(settings):
    n = iter(range(100))

    class Following(Scripted):
        async def complete(self, **kw):
            i = next(n)
            self.default = step(f"Step {i}", 1 + i % 4)
            return await super().complete(**kw)

    llm = Following()
    brain, rec = make_brain(settings, llm=llm)
    brain.screen.foreground_title = lambda: "Settings - App"
    await brain.turn("guide me there")
    for _ in range(5):
        await brain.guide_clicked("seen")
    assert brain._guide is not None and brain._guide.step == 6
    assert not any("lost track" in s for s in said(rec))
    quiet(brain)


class Readable(FakeScreen):
    """A pretend screen whose words can be read off one part of it (ScreenReader.words_in)."""

    def __init__(self) -> None:
        super().__init__()
        self.lines: list[TextLine] = []
        self.reads = 0

    def words_in(self, rect):
        self.reads += 1
        return list(self.lines)


async def scrolled(brain, by: int, idle: dict) -> None:
    idle["s"] = 0.1
    now = fake_context()
    now.screenshot = drawn_list(by)
    brain.screen.ctx = now
    await asyncio.sleep(0.8)
    idle["s"] = 1.2


async def test_a_notch_of_the_wheel_that_shows_nothing_new_is_not_a_turn(settings):
    """They are still on their way to it: no turn, and no "a bit more", until it is in view."""
    llm = Scripted(SCROLL, default=step("Recovery", 3))
    brain, rec = make_brain(settings, llm=llm)
    brain.screen = Readable()
    brain.screen.foreground_title = lambda: "Settings - App"
    brain.screen.ctx.screenshot = drawn_list()
    idle = {"s": 30.0}
    brain.screen.input_idle_s = lambda: idle["s"]
    await brain.turn("guide me there")
    assert brain._guide.words == "Recovery"
    await asyncio.sleep(1.5)
    brain.screen.lines = [line("Display", (470, 200, 70, 18)), line("Sound", (470, 272, 60, 18), 51)]
    await scrolled(brain, 72, idle)
    await asyncio.sleep(1.5)
    assert brain.screen.reads == 1 and len(llm.prompts) == 1  # looked, read, and kept quiet
    idle["s"] = 0.1  # still scrolling
    brain.screen.lines = [line("Recovery", (470, 600, 90, 18))]  # and there it is
    await scrolled(brain, 288, idle)
    await until(lambda: len(llm.prompts) > 1)
    assert len(llm.prompts) == 2 and "so I think I did it" in llm.prompts[1]
    await settle(brain)
    assert sum("Scroll down" in s for s in said(rec)) == 1
    quiet(brain)


async def test_a_row_still_half_under_the_edge_is_not_in_view(settings):
    brain, _ = make_brain(settings, llm=Scripted())
    brain.screen = Readable()
    view = (0, 0, 1600, 900)
    brain.screen.lines = [line("Recovery", (470, 886, 90, 18))]
    assert await brain._words_showing(view, "Recovery") is False
    brain.screen.lines = [line("Recovery", (470, 700, 90, 18))]
    assert await brain._words_showing(view, "Recovery") is True
    brain.screen.lines = []  # nothing came back at all: the reader failed, so can't tell
    assert await brain._words_showing(view, "Recovery") is None
    brain.screen = FakeScreen()  # a screen that can't be read
    assert await brain._words_showing(view, "Recovery") is None


async def test_scrolled_and_stopped_with_nothing_in_view_still_gets_an_answer(settings):
    """What it holds back for must never be a wait with no end: they have stopped, so they
    are waiting for Pyxi."""
    llm = Scripted(SCROLL, SCROLL, default=step("Recovery", 3))
    brain, _ = make_brain(settings, llm=llm)
    brain.screen = Readable()
    brain.screen.foreground_title = lambda: "Settings - App"
    brain.screen.ctx.screenshot = drawn_list()
    idle = {"s": 30.0}
    brain.screen.input_idle_s = lambda: idle["s"]
    await brain.turn("guide me there")
    await asyncio.sleep(1.5)
    brain.screen.lines = [line("Display", (470, 200, 70, 18))]
    await scrolled(brain, 72, idle)
    idle["s"] = 9.0
    await until(lambda: len(llm.prompts) > 1, tries=90)
    assert len(llm.prompts) == 2 and "so I think I did it" in llm.prompts[1]
    await settle(brain)
    # "A bit more": the same scroll, once more, as the same step.
    assert brain._guide is not None and brain._guide.again and brain._guide.step == 1
    quiet(brain)


async def test_a_click_inside_the_list_of_a_scroll_step_is_heard(settings):
    """They scrolled and clicked Recovery, which is inside the list the scroll step framed.
    A page that is simply another page is no scroll, so the watcher saw nothing either."""
    llm = Scripted(SCROLL, default=step("Restart now", 4))
    brain, _ = make_brain(settings, llm=llm)
    brain.screen.foreground_title = lambda: "Settings - App"
    brain.screen.ctx.screenshot = drawn_list()
    brain.screen.input_idle_s = lambda: 30.0
    await brain.turn("guide me there")
    await brain.guide_clicked("within")  # a click in the list that changed nothing: no turn
    assert len(llm.prompts) == 1 and brain._guide.step == 1
    screen_changes(brain, 90)  # the Recovery page
    await brain.guide_clicked("within")
    assert len(llm.prompts) == 2 and "I clicked something inside the marked area" in llm.prompts[1]
    assert brain._guide is not None and (brain._guide.step, brain._guide.label) == (2, "Restart now")
    quiet(brain)


def test_the_words_a_scroll_is_for():
    ctx = fake_context()
    assert scroll_words(ctx, {"quote": "Recovery", "element_id": 3}, "Recovery") == "Recovery"
    assert scroll_words(ctx, {"element_id": 3}, "the switch") == "Dark mode"  # the control's name
    ctx.elements.append(UIElement(9, "Recovery Reset, advanced startup, go back", "ListItem", (0, 880, 900, 19), 1, 1))
    assert scroll_words(ctx, {"element_id": 9}, "Recovery (scroll down)") == "Recovery"
    assert scroll_words(ctx, {"element_id": 9}, "the last row") == ""
    assert scroll_words(ctx, {}, "the page") == ""


# ------------------------------------------------------------------ typing is not "something else"
async def test_a_click_beside_the_field_while_typing_is_not_something_else(settings):
    typing = [("say", {"text": "Type the code."}), ("guide", {"goal": GOAL, "action": "type", "text": "K7QP-2MXD", "element_id": 5, "label": "Code box"})]
    llm = Scripted(typing, default=step("Export", 4))
    brain, _ = make_brain(settings, llm=llm)
    brain.screen.foreground_title = lambda: "Settings - App"
    idle = {"s": 30.0}
    brain.screen.input_idle_s = lambda: idle["s"]
    await brain.turn("guide me there")
    await asyncio.sleep(1.4)
    idle["s"] = 0.1  # a click beside the mark, and the first letter
    screen_changes(brain, 30)
    await brain.guide_clicked("elsewhere")
    await asyncio.sleep(1.0)
    assert len(llm.prompts) == 1 and brain._guide.step == 1  # still typing: nothing yet
    screen_changes(brain, 60)  # the rest of the code
    idle["s"] = 2.5  # and they have stopped
    await until(lambda: len(llm.prompts) > 1)
    assert len(llm.prompts) == 2 and "so I think I did it" in llm.prompts[1]
    await settle(brain)
    quiet(brain)


# ------------------------------------------------------------------ the mark is the control
def screen_with(elements: list[UIElement], words: list[TextLine]) -> ScreenContext:
    shot = Image.new("RGB", (1600, 900), (30, 30, 30))
    ctx = ScreenContext(time.time(), WindowInfo("Extensions", "chrome.exe", (0, 0, 1600, 900), 7), elements, shot)
    ctx.text = words
    return ctx


def test_a_switch_named_by_the_words_beside_it_is_marked_on_the_switch():
    """Live: element_id was the Developer mode switch, quote the words to its left. The quote
    was looked for inside the switch, not found, then found beside it, and the mark went on
    the words. A click on the switch was "something else"."""
    switch = UIElement(1, "Developer mode", "Button", (1500, 80, 44, 24), 0)
    ctx = screen_with([switch], [line("Developer mode", (1370, 84, 118, 16))])
    assert step_rect(ctx, {"element_id": 1, "quote": "Developer mode"}) == (1500, 80, 44, 24)
    assert step_rect(ctx, {"element_id": 1}) == (1500, 80, 44, 24)
    # A settings row: the words at the left, the switch at the far right of the same row.
    far = UIElement(2, "Night light", "Button", (1400, 300, 44, 24), 0)
    ctx = screen_with([far], [line("Night light", (420, 304, 90, 16))])
    assert step_rect(ctx, {"element_id": 2, "quote": "Night light"}) == (1400, 300, 44, 24)


def test_a_row_is_marked_whole_and_a_big_pane_on_the_words_in_it():
    row = UIElement(1, "Settings & privacy", "MenuItem", (1200, 300, 340, 52), 0)
    pane = UIElement(2, "Menu", "Pane", (1180, 100, 380, 700), 0)
    words = [line("Settings & privacy", (1260, 318, 140, 16))]
    ctx = screen_with([row, pane], words)
    assert step_rect(ctx, {"element_id": 1, "quote": "Settings & privacy"}) == (1200, 300, 340, 52)
    assert step_rect(ctx, {"element_id": 2, "quote": "Settings & privacy"}) == (1260, 318, 134, 16)


def test_words_far_from_the_control_given_still_win():
    wrong = UIElement(1, "Search", "Edit", (100, 20, 300, 30), 0)
    ctx = screen_with([wrong], [line("Load unpacked", (60, 140, 110, 16))])
    assert step_rect(ctx, {"element_id": 1, "quote": "Load unpacked"}) == (60, 140, 106, 16)


def test_an_icon_and_its_caption_are_one_mark():
    """Live: the Start menu's tree had one element. "Settings" was given as a box on the icon
    and the caption under it as the quote; the mark went on the caption, and a click on the
    icon was "something else"."""
    ctx = screen_with([], [line("Settings", (890, 330, 60, 14)), line("Search for apps, settings, and documents", (600, 178, 260, 16), 51)])
    rect = step_rect(ctx, {"quote": "Settings", "box": [884, 270, 70, 70]})
    assert rect is not None
    x, y, w, h = rect
    assert y <= 270 and y + h >= 344 and x <= 890 and x + w >= 946  # the icon and the word
    # A box somewhere else is only where the model thought it was: the words alone.
    assert step_rect(ctx, {"quote": "Settings", "box": [200, 700, 70, 70]}) == (890, 330, 56, 14)
    # Nothing exact to go by: the caller zooms in on the guessed box and checks it.
    assert step_rect(ctx, {"box": [884, 270, 70, 70]}) is None
    assert step_rect(None, {"quote": "Settings"}) is None


async def test_the_switch_is_what_gets_marked(settings):
    switch = [("say", {"text": "Turn on Developer mode."}), ("guide", {"goal": GOAL, "element_id": 3, "quote": "Developer mode", "label": "Developer mode switch"})]
    brain, rec = make_brain(settings, llm=Scripted(switch))
    brain.screen.ctx.text = [line("Developer mode", (400, 176, 118, 16))]  # beside the control (140..340)
    await brain.turn("guide me there")
    assert rec.of("annotate")[0]["annotations"][0]["rect"] == [140, 170, 200, 30]
    quiet(brain)


# ------------------------------------------------------------------ a step read from an old look
class Reread(FakeScreen):
    """A screen whose words can be read again (ScreenReader.text_now), seconds after the look."""

    def __init__(self, at_look: list[TextLine], now: list[TextLine]) -> None:
        super().__init__()
        self.at_look, self.now = at_look, now

    async def capture(self, pixels: bool = True, **_) -> ScreenContext:
        ctx = fake_context()
        ctx.text = list(self.at_look)
        ctx.captured_at = time.time() - 5.0  # the model took that long to answer
        self.ctx = ctx
        return ctx

    def text_now(self, ctx):
        return list(self.now)


async def test_a_step_for_a_screen_that_has_gone_is_not_marked(settings):
    """They don't wait: the step was read from a look five seconds old, and by the time the
    mark was drawn they had clicked on. It went up on a page that was no longer there."""
    export = line("Export", (730, 660, 54, 18))
    llm = Scripted(step(), step("Dark mode", 3))
    brain, rec = make_brain(settings, llm=llm)
    brain.screen = Reread([export, line("Dark mode", (150, 176, 81, 18), 51)], [line("Dark mode", (150, 176, 81, 18), 51)])
    await brain.turn("guide me there")
    assert "guide: 'Export' was there when you looked" in llm.prompts[1] and "look_again" in llm.prompts[1]
    marks = [a for e in rec.of("annotate") for a in e["annotations"]]
    assert [m["label"] for m in marks] == ["Dark mode"]  # the step for the screen as it is
    quiet(brain)


async def test_a_step_whose_control_has_moved_is_marked_where_it_is_now(settings):
    export = line("Export", (730, 660, 54, 18))
    brain, rec = make_brain(settings, llm=Scripted(step()))
    brain.screen = Reread([export], [line("Export", (730, 760, 54, 18))])  # the page grew above it
    await brain.turn("guide me there")
    assert rec.of("annotate")[0]["annotations"][0]["rect"] == [700, 750, 120, 40]
    quiet(brain)


# ------------------------------------------------------------------ a page that is still arriving
class Loading(FakeScreen):
    """A pretend screen that can be glanced at: it is still filling in for a few looks."""

    def __init__(self, frames: int) -> None:
        super().__init__()
        self.frames, self.glances = frames, 0

    def glance(self, rect):
        self.glances += 1
        shade = min(self.glances, self.frames) * 35
        return Image.new("RGB", (rect[2], rect[3]), (shade, shade, shade))


async def test_the_look_waits_for_a_page_that_is_still_arriving(settings):
    brain, _ = make_brain(settings, llm=Scripted())
    view = (0, 0, 1600, 900)
    for frames, glances in ((1, 2), (3, 4), (99, 7)):  # at once; after two more looks; never
        brain.screen = Loading(frames)
        started = time.monotonic()
        await brain._settled(view)
        assert brain.screen.glances == glances
        assert 0.7 < time.monotonic() - started < 3.2
    brain.screen = FakeScreen()  # nothing cheap to compare: the old fixed wait
    started = time.monotonic()
    await brain._settled(view)
    assert 0.7 < time.monotonic() - started < 1.2
