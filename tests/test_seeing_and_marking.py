"""Seeing every window in the picture, and drawing marks where things are now.

From the trial of 13 November. With two windows side by side, "explain me this diagram" was
answered in words and nothing was drawn: the picture showed both windows and the model had
the controls of one. On a post, a card's marks sat 100 to 500 px from what they were about
(the look was 15 s old and the page had moved), and the card itself sat on the header its
first point explained. An explaining call with nothing in it left the screen bare with no
second try, and a second call in the same answer took the first card's place.
"""

from __future__ import annotations

import asyncio
import sys
import types

from otto.types import ToolCall

from conftest import fake_context
from pyxi.companion.brain import one_card
from pyxi.companion.notes import (
    EMPTY_CARD,
    build_card,
    card_size,
    covers_marks,
    merge_calls,
    place_card,
    settle_marks,
)
from pyxi.screen import UIElement, context, uia
from pyxi.screen.ocr import find_places, page_shift
from test_audit_pointer import Scripted, _ThreeScreens, line
from test_brain_and_server import make_brain
from test_streaming import Streamed

SCREEN = (0, 0, 1920, 1080)
LEFT, RIGHT = (0, 0, 960, 1040), (960, 0, 960, 1040)


def ltrb(box):
    return (box[0], box[1], box[0] + box[2], box[1] + box[3])


def overlap(a, b) -> int:
    w = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
    h = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
    return max(0, w) * max(0, h)


# ------------------------------------------------------------------ 1. every window in the picture
def test_two_windows_side_by_side_are_both_in_view():
    pile = [(11, ltrb(LEFT)), (22, ltrb(RIGHT))]
    [(hwnd, part, above)] = uia.windows_in_view(pile, SCREEN, main=11)
    assert hwnd == 22 and part == RIGHT and above == [LEFT]
    # Whichever one the mouse is over, the other one is the one to add.
    assert [w[0] for w in uia.windows_in_view(pile, SCREEN, main=22)] == [11]


def test_a_window_behind_another_is_not_read():
    full = (-8, -8, 1936, 1056)  # maximised: its frame hangs past the screen
    pile = [(11, ltrb(full)), (22, ltrb(RIGHT)), (33, ltrb((100, 100, 800, 600)))]
    assert uia.windows_in_view(pile, SCREEN, main=11) == []
    # One that only peeks out from behind the window in front is not read either...
    peeking = [(11, ltrb((0, 0, 1500, 1040))), (22, ltrb((700, 0, 1220, 1040)))]
    assert uia.windows_in_view(peeking, SCREEN, main=11) == []
    # ...nor a sliver, nor a window on another screen.
    small = [(11, ltrb(LEFT)), (22, ltrb((1000, 100, 180, 120))), (33, ltrb((1920, 0, 1080, 1920)))]
    assert uia.windows_in_view(small, SCREEN, main=11) == []


def test_the_windows_most_in_view_come_first_and_there_are_at_most_three():
    quarter = [(i, ltrb(((i % 3) * 640, (i // 3) * 520, 640, 520))) for i in range(6)]
    assert len(uia.windows_in_view(quarter, SCREEN, main=0, limit=3)) == 3
    pile = [(11, ltrb((0, 0, 700, 1040))), (22, ltrb((700, 0, 400, 1040))), (33, ltrb((1100, 0, 820, 1040)))]
    assert [w[0] for w in uia.windows_in_view(pile, SCREEN, main=11)] == [33, 22]


def button(name, rect, depth=1, parent=None):
    return uia.RawElement(name, "Button", rect, depth, parent, False, "")


class SplitScreen:
    """A pretend screen with a browser on the left half (under the mouse) and others beside it."""

    def __init__(self, monkeypatch, settings, others, clock=None, mouse=(400, 500)):
        self.read: list[int] = []
        self.budgets: list[float] = []
        main = uia.RawWindow("Feed - Browser", "browser.exe", LEFT, 11)
        its = [button("Like", (100, 600, 80, 30)), button("Share", (300, 600, 80, 30))]
        monkeypatch.setattr(uia, "read_foreground", lambda **kw: (main, its))
        monkeypatch.setattr(uia, "read_taskbar", lambda *a, **k: [])
        monkeypatch.setattr(uia, "cursor_pos", lambda: mouse)
        monkeypatch.setattr(context.ScreenReader, "private_rects", lambda self, skip=None: [])
        monkeypatch.setattr(context.ocr, "read_lines", lambda shot, origin=(0, 0): [])
        monkeypatch.setitem(sys.modules, "mss", types.SimpleNamespace(MSS=_ThreeScreens, mss=_ThreeScreens))
        self.windows = {w.hwnd: (w, els) for w, els in others}
        pile = [(11, ltrb(LEFT)), *[(w.hwnd, ltrb(w.rect)) for w, _ in others]]
        monkeypatch.setattr(uia, "other_windows", lambda screen, main=0, limit=3: uia.windows_in_view(pile, screen, main, limit))
        monkeypatch.setattr(uia, "named_window", lambda hwnd: self.windows[hwnd][0])

        def read_window(hwnd, *, budget_s, max_elements=400, max_depth=25):
            self.read.append(hwnd)
            self.budgets.append(budget_s)
            if clock is not None:
                clock[0] += 0.7  # each one takes this long
            return self.windows[hwnd]

        monkeypatch.setattr(uia, "read_window", read_window)
        if clock is not None:
            monkeypatch.setattr(context.time, "monotonic", lambda: clock[0])
        self.reader = context.ScreenReader(settings.screen)

    def look(self, **kw):
        return self.reader._capture_sync(True, **kw)


def heart_page():
    window = uia.RawWindow("Heart diagram - Example Atlas", "atlas.exe", RIGHT, 22)
    return window, [
        uia.RawElement("Diagram of the heart", "Image", (1040, 200, 700, 520), 1, None, False, ""),
        button("Next", (1700, 900, 90, 34)),
    ]


def test_a_look_reads_both_windows_of_a_split_screen(settings, monkeypatch):
    """Live: the picture showed both windows, and the model had the controls of one."""
    ctx = SplitScreen(monkeypatch, settings, [heart_page()]).look()
    assert [e.name for e in ctx.elements] == ["Like", "Share", "Diagram of the heart", "Next"]
    ids = [e.id for e in ctx.elements]
    assert ids == sorted(set(ids)) and ids == list(range(ids[0], ids[0] + 4))  # one run of ids
    # The window under the mouse stays the one the look is "of": nothing downstream changes.
    assert (ctx.window.title, ctx.window.process, ctx.window.hwnd) == ("Feed - Browser", "browser.exe", 11)
    assert ctx.view_rect() == LEFT
    assert [(o.process, o.hwnd, o.rect) for o in ctx.others] == [("atlas.exe", 22, RIGHT)]
    assert [e.hwnd for e in ctx.elements] == [0, 0, 22, 22]
    # The picture the model marks on is found by its id, like any control of the first window.
    picture = next(e for e in ctx.elements if e.role == "Image")
    assert ctx.element(picture.id).rect == (1040, 200, 700, 520)


def test_the_model_is_told_which_window_each_group_is_in(settings, monkeypatch):
    ctx = SplitScreen(monkeypatch, settings, [heart_page()]).look()
    ctx.model_image(1366)
    text = ctx.render()
    assert text.startswith("Foreground app: browser | window: 'Feed - Browser'")
    assert "This picture shows 2 windows." in text
    first, second = text.index("Window 1, the one under the mouse: browser"), text.index("Window 2: atlas")
    assert first < text.index('"Like"') < second < text.index('"Diagram of the heart"')
    # Where the second window is, in the screenshot's own px (1920 wide shown as 1366).
    assert "Window 2: atlas 'Heart diagram - Example Atlas' @(683,0,683x740)" in text
    # One window is said as it always was.
    alone = SplitScreen(monkeypatch, settings, []).look()
    assert "Window 1" not in alone.render() and "This picture shows" not in alone.render()


def test_a_private_window_beside_it_is_neither_read_nor_named(settings, monkeypatch):
    vault = uia.RawWindow("Vault - Bitwarden", "bitwarden.exe", RIGHT, 22)
    split = SplitScreen(monkeypatch, settings, [(vault, [button("Copy password", (1100, 300, 120, 30))])])
    ctx = split.look()
    assert split.read == [] and ctx.others == []
    assert [e.name for e in ctx.elements] == ["Like", "Share"]
    assert "Bitwarden" not in ctx.render() and "password" not in ctx.render().lower()


def test_reading_stops_when_the_looks_time_is_used_up(settings, monkeypatch):
    def page(hwnd, x):
        return uia.RawWindow(f"Page {hwnd}", "pages.exe", (x, 0, 320, 1040), hwnd), [button(f"Go {hwnd}", (x + 20, 300, 80, 30))]

    clock = [100.0]
    split = SplitScreen(monkeypatch, settings, [page(22, 960), page(33, 1280), page(44, 1600)], clock=clock)
    ctx = split.look()
    # 0.7 s each: two fit in the 1.5 s, the third is named and left unread.
    assert split.read == [22, 33] and all(b <= 0.9 for b in split.budgets)
    assert [(o.hwnd, o.tree) for o in ctx.others] == [(22, "ok"), (33, "ok"), (44, "failed")]
    assert "Go 44" not in ctx.render() and "Its controls were not read this time" in ctx.render()
    assert clock[0] - 100.0 <= context.ScreenReader.LOOK_BUDGET_S


def test_controls_hidden_behind_the_window_in_front_are_left_out(settings, monkeypatch):
    wide = uia.RawWindow("Notes - Editor", "editor.exe", (700, 0, 1220, 1040), 22)
    its = [button("Hidden", (760, 300, 80, 30)), button("Seen", (1200, 300, 80, 30))]
    main_first = SplitScreen(monkeypatch, settings, [(wide, its)])
    # The browser (0..960) is in front of the editor's left edge.
    monkeypatch.setattr(uia, "other_windows", lambda screen, main=0, limit=3: [(22, (700, 0, 1220, 1040), [LEFT])])
    ctx = main_first.look()
    assert [e.name for e in ctx.elements if e.hwnd == 22] == ["Seen"]


def test_while_pyxi_drives_only_the_window_being_worked_in_is_read(settings, monkeypatch):
    split = SplitScreen(monkeypatch, settings, [heart_page()])
    ctx = split.look(follow_mouse=False)
    assert split.read == [] and ctx.others == [] and len(ctx.elements) == 2


def test_each_windows_rows_are_cut_by_its_own_edges(settings, monkeypatch):
    """A control of the second window is not "cut off" for lying outside the first."""
    ctx = SplitScreen(monkeypatch, settings, [heart_page()]).look()
    assert ctx.cut_off() == {}
    tall = uia.RawWindow("Heart diagram - Example Atlas", "atlas.exe", RIGHT, 22)
    low = uia.RawElement("Diagram of the heart", "Image", (1040, 700, 700, 520), 1, None, False, "")
    ctx = SplitScreen(monkeypatch, settings, [(tall, [low])]).look()
    assert list(ctx.cut_off().values()) == ["bottom"]


def test_another_window_is_read_in_one_call_or_not_at_all(monkeypatch):
    monkeypatch.setattr(uia, "available", lambda: True)
    monkeypatch.setattr(uia, "named_window", lambda hwnd: uia.RawWindow("Page", "pages.exe", RIGHT, hwnd))
    monkeypatch.setitem(sys.modules, "uiautomation", types.SimpleNamespace())
    walked = []
    monkeypatch.setattr(uia, "_walk", lambda *a, **k: walked.append(1))
    monkeypatch.setattr(uia, "read_in_one_call", lambda auto, hwnd, budget_s, n, depth: [button("Go", (1000, 300, 80, 30))])
    window, got = uia.read_window(22, budget_s=0.5)
    assert window.tree == "ok" and [e.name for e in got] == ["Go"]
    monkeypatch.setattr(uia, "read_in_one_call", lambda *a: None)  # too slow, or it failed
    window, got = uia.read_window(22, budget_s=0.5)
    assert (window.tree, got, walked) == ("failed", [], [])  # never the slow walk
    assert uia.read_window(22, budget_s=0)[0].tree == "failed"


# ------------------------------------------------------------------ 2. marks where things are now
NAME = line("Rosa Lindqvist", (420, 300, 150, 18), 101)
CAPTION = line("Field day at the river with the whole class", (420, 520, 420, 18), 102)
LIKES = line("Liked by 48 people", (420, 640, 170, 16), 103)
SIDE = [line(f"Sidebar entry number {i}", (40, 200 + 40 * i, 200, 16), 110 + i) for i in range(5)]
POST = {
    "title": "What this post is",
    "items": [
        {"text": "Who posted it", "quote": "Rosa Lindqvist", "mark": "frame"},
        {"text": "What it shows", "quote": "Field day at the river", "mark": "underline"},
        {"text": "How it landed", "quote": "Liked by 48 people", "mark": "underline"},
    ],
}


def moved(lines, dy, dx=0):
    return [line(ln.text, (ln.rect[0] + dx, ln.rect[1] + dy, ln.rect[2], ln.rect[3]), ln.id) for ln in lines]


def post_context(extra=()):
    ctx = fake_context("browser.exe", "Feed - Browser")
    ctx.elements = []
    ctx.text = [NAME, CAPTION, LIKES, *SIDE, *extra]
    return ctx


def test_how_far_the_page_moved_is_read_off_the_words():
    before = [NAME, CAPTION, LIKES, *SIDE]
    assert page_shift(before, before) == (0, 0)
    assert page_shift(before, moved(before, 110)) == (0, 110)
    # The middle scrolled and the side bar stayed: near the marks, the page moved.
    scrolled = [*moved([NAME, CAPTION, LIKES], 110), *SIDE]
    assert page_shift(before, scrolled, around=(420, 300, 420, 360)) == (0, 110)
    assert page_shift(before, scrolled) == (0, 0)  # asked of the whole screen, the side bar wins
    half = [*moved(SIDE[:2], 110), *moved(SIDE[2:4], -40), SIDE[4], NAME]
    assert page_shift([*SIDE, NAME], half) is None  # no one move that most of it made
    assert page_shift(before, [line("Something else entirely", (10, 10, 200, 16))]) is None


def test_marks_follow_a_page_that_moved_under_them():
    """Live: a video loaded above the post, and every mark was drawn about 100 px too high."""
    ctx = post_context()
    card = build_card(ctx, POST)
    was = [it.rect for it in card.items]
    assert settle_marks(ctx, POST, card, moved(ctx.text, 110)) == []
    assert [it.rect for it in card.items] == [(x, y + 110, w, h) for x, y, w, h in was]
    assert [it.mark for it in card.items] == ["frame", "underline", "underline"]
    # Nothing moved: nothing changes.
    still = build_card(ctx, POST)
    assert settle_marks(ctx, POST, still, list(ctx.text)) == [] and [it.rect for it in still.items] == was


def test_a_point_whose_words_have_gone_keeps_its_text_and_loses_its_mark():
    ctx = post_context()
    card = build_card(ctx, POST)
    now = [ln for ln in moved(ctx.text, 110) if ln.text != LIKES.text]
    assert settle_marks(ctx, POST, card, now) == [3]
    last = card.items[2]
    assert (last.text, last.mark, last.rect) == ("How it landed", "none", None)
    assert card.items[0].rect == (420, 410, 150, 18)  # the others moved with the page


def test_a_mark_with_no_words_to_check_goes_with_the_page_and_a_new_page_drops_it():
    ctx = post_context()
    boxed = {"title": "Post", "items": [*POST["items"][:2], {"text": "The photo", "box": [600, 340, 200, 150]}]}
    card = build_card(ctx, boxed)
    assert card.items[2].rect == (600, 340, 200, 150)
    settle_marks(ctx, boxed, card, moved(ctx.text, -60))
    assert card.items[2].rect == (600, 280, 200, 150)
    # They went to another page while the model was thinking: no mark is drawn on it.
    card = build_card(ctx, boxed)
    other = [line(f"A different page line {i}", (100, 100 + 30 * i, 300, 16)) for i in range(8)]
    assert settle_marks(ctx, boxed, card, other) == [1, 2, 3]
    assert [it.mark for it in card.items] == ["none"] * 3 and [it.text for it in card.items][2] == "The photo"


def test_a_mark_that_scrolled_out_of_the_picture_is_dropped():
    ctx = post_context()
    card = build_card(ctx, POST)
    assert settle_marks(ctx, POST, card, moved(ctx.text, 300)) == [3]  # 640 + 300 is off a 900 px screen
    assert card.items[0].rect == (420, 600, 150, 18)


def test_a_short_quote_that_is_on_the_page_twice_goes_to_the_one_by_the_other_points():
    """A name is in the post's header and again in a comment far below (or a list above)."""
    above = line("Rosa Lindqvist", (1200, 60, 150, 18), 90)  # first in reading order
    ctx = post_context([above])
    ctx.text.sort(key=lambda ln: (ln.rect[1], ln.rect[0]))
    assert len(find_places(ctx.text, "Rosa Lindqvist")) == 2
    card = build_card(ctx, POST)
    assert card.items[0].rect == NAME.rect  # by the caption and the likes, not top right
    # Alone on its card there is nothing to go by: reading order, as before.
    alone = build_card(ctx, {"title": "Who", "items": [POST["items"][0]]})
    assert alone.items[0].rect == above.rect
    # What the model itself gave still decides: its box, or the element it named.
    told = {"title": "Who", "items": [{**POST["items"][0], "box": [1150, 40, 250, 60]}, *POST["items"][1:]]}
    assert build_card(ctx, told).items[0].rect == above.rect
    ctx.elements = [UIElement(7, "Post", "Group", (400, 280, 500, 400), 1)]
    named = {"title": "Who", "items": [{**POST["items"][0], "element_id": 7}]}
    assert build_card(ctx, named).items[0].rect == NAME.rect


def test_words_on_screen_twice_now_are_not_guessed_at_when_neither_is_where_the_page_went():
    ctx = post_context()
    card = build_card(ctx, POST)
    # The page moved 110 px; "Liked by 48 people" is now twice on screen, neither 110 px down.
    now = [ln for ln in moved(ctx.text, 110) if ln.text != LIKES.text]
    now += [line(LIKES.text, (420, 200, 170, 16)), line(LIKES.text, (1000, 500, 170, 16))]
    assert settle_marks(ctx, POST, card, now) == [3]


def stale_look(brain, now_lines):
    """While the model thinks, seconds pass and the page moves."""

    def before(n: int) -> None:
        if n == 1:
            brain._ctx.captured_at -= 15
            now = fake_context()
            now.text = now_lines
            brain.screen.ctx = now

    return before


async def test_a_card_drawn_seconds_after_the_look_marks_where_things_are_now(settings):
    llm = Scripted([("say", {"text": "It's a class trip."}), ("explain_on_screen", POST)])
    brain, rec = make_brain(settings, llm=llm)
    brain.screen.ctx = post_context()
    llm.before = stale_look(brain, [ln for ln in moved(post_context().text, 110) if ln.text != LIKES.text])
    await brain.turn("what is this post?")
    [card] = [e["card"] for e in rec.of("notes")]
    assert card["items"][0]["rect"] == [420, 410, 150, 18] and card["items"][1]["rect"][1] == 630
    assert card["items"][2]["rect"] is None and card["items"][2]["mark"] == "none"
    assert card["items"][2]["text"] == "How it landed"  # still on the card to read
    # And the card is placed by where the marks are now.
    w, h = card_size([i["text"] for i in card["items"]], card["title"])
    box = (*card["place"], w, h)
    assert all(not overlap(box, tuple(i["rect"])) for i in card["items"] if i["rect"])
    await asyncio.sleep(0)


async def test_a_fresh_look_is_not_read_again_for_a_card(settings):
    llm = Scripted([("say", {"text": "It's a class trip."}), ("explain_on_screen", POST)])
    brain, rec = make_brain(settings, llm=llm)
    brain.screen.ctx = post_context()
    looks = []
    capture = brain.screen.capture

    async def counted(pixels: bool = True):
        looks.append(1)
        return await capture(pixels)

    brain.screen.capture = counted
    await brain.turn("what is this post?")
    assert len(looks) == 1 and rec.of("notes")[0]["card"]["items"][0]["rect"] == list(NAME.rect)


async def test_a_card_that_arrives_in_pieces_is_checked_with_one_read_a_second(settings):
    llm = Streamed([("say", {"text": "It's a class trip."}), ("explain_on_screen", POST)])
    brain, rec = make_brain(settings, llm=llm)
    brain.screen.ctx = post_context()
    shifted = moved(post_context().text, 110)
    looks = []
    complete = llm.complete

    async def late(**kw):
        brain._ctx.captured_at -= 15
        now = fake_context()
        now.text = shifted
        brain.screen.ctx = now
        capture = brain.screen.capture

        async def counted(pixels: bool = True):
            looks.append(1)
            return await capture(pixels)

        brain.screen.capture = counted
        return await complete(**kw)

    llm.complete = late
    await brain.turn("what is this post?")
    cards = [e["card"] for e in rec.of("notes")]
    assert len({c["id"] for c in cards}) == 1 and len(cards) >= 2
    for c in cards:  # every version of it, from the first point on, is where things are now
        assert c["items"][0]["rect"] == [420, 410, 150, 18]
    assert len(looks) == 1  # the pieces came within a second: one read served them all


# ------------------------------------------------------------------ 3. the card never covers its own marks
def tall_post():
    """Four marks down a post that fills most of the screen, with text all around."""
    ctx = fake_context("browser.exe", "Feed - Browser")
    ctx.elements = []
    marks = [(60, 80, 420, 60), (1100, 80, 440, 60), (60, 420, 1480, 40), (60, 780, 420, 60)]
    ctx.text = [line(f"Line {i} of the post body text", (60, 80 + 24 * i, 1480, 18), 200 + i) for i in range(32)]
    return ctx, marks


def test_the_card_keeps_clear_of_each_thing_its_points_mark():
    """Live: the card sat on the post's header, which its first point was about."""
    ctx, marks = tall_post()
    region = (60, 80, 1480, 760)  # the box around all four: most of the screen
    texts = ["Who posted it", "What it shows", "Why it matters", "How it landed"]
    place = place_card(ctx, region, texts, "What this post is", marks=marks)
    w, h = card_size(texts, "What this post is")
    assert place is not None and all(not overlap((*place, w, h), m) for m in marks)
    # Kept clear of the box around them only (as it was), it lands on the header's mark.
    was = place_card(ctx, region, texts, "What this post is")
    assert overlap((*was, w, h), marks[0]) > 0
    # With room beside what it explains, it still goes beside all of it, as before.
    small = [(300, 300, 200, 30), (300, 360, 200, 30)]
    beside = place_card(ctx, (300, 300, 200, 90), texts[:2], "Post", marks=small)
    w, h = card_size(texts[:2], "Post")
    assert not overlap((*beside, w, h), (300, 300, 200, 90))


def test_a_card_knows_when_its_spot_covers_one_of_its_marks():
    ctx = post_context()
    card = build_card(ctx, POST)
    assert not covers_marks(card.place, card)
    assert covers_marks((400, 290), card) and not covers_marks(None, card)


async def test_a_card_still_arriving_moves_off_what_a_later_point_marks(settings):
    brain, rec = make_brain(settings)
    ctx = post_context()
    brain._ctx = ctx
    first = build_card(ctx, {"title": "Post", "items": POST["items"][:1]})
    first.place = (400, 500)  # where the caption is: fine while the caption is not marked
    brain._turn_card = first
    await brain._show_card_so_far("Post", POST["items"][:1])
    assert rec.of("notes")[-1]["card"]["place"] == [400, 500]  # stays put while it can
    await brain._show_card_so_far("Post", POST["items"])
    card = rec.of("notes")[-1]["card"]
    assert card["id"] == first.id and card["place"] != [400, 500]
    w, h = card_size([i["text"] for i in card["items"]], card["title"])
    assert all(not overlap((*card["place"], w, h), tuple(i["rect"])) for i in card["items"])


def test_a_card_is_placed_with_room_for_the_points_still_to_come():
    ctx = post_context()
    one = {"title": "Post", "items": POST["items"][:1]}
    roomy = build_card(ctx, one, rows=4)
    w, h = card_size(["Who posted it", *["x" * 40] * 3], "Post")
    assert roomy.place is not None and not overlap((*roomy.place, w, h), NAME.rect)


# ------------------------------------------------------------------ 4. an explaining call with nothing in it
async def test_an_empty_explaining_call_is_sent_back_once(settings):
    """Live, 14:47: explain_on_picture with no arguments. One sentence, nothing drawn."""
    llm = Scripted(
        [("say", {"text": "Here is how the blood flows."}), ("explain_on_picture", {})],
        [("explain_on_screen", POST)],
    )
    brain, rec = make_brain(settings, llm=llm)
    brain.screen.ctx = post_context()
    await brain.turn("show me how the blood flows")
    assert len(llm.prompts) == 2 and EMPTY_CARD in llm.prompts[1]
    assert "Call it again now, complete" in llm.prompts[1]
    assert len(rec.of("notes")) == 1 and len(rec.of("notes")[0]["card"]["items"]) == 3
    call = next(c for c in brain.turns.recent(1)[0]["tool_calls"] if c["tool"] == "explain_on_picture")
    assert call["outcome"] == "refused"
    await asyncio.sleep(0)


async def test_an_empty_call_is_not_asked_for_a_third_time(settings):
    llm = Scripted(
        [("say", {"text": "Here it is."}), ("explain_on_screen", {"items": []})],
        [("explain_on_screen", {"title": "Flow", "items": [{"text": "  "}]})],
        [("explain_on_screen", POST)],
    )
    brain, rec = make_brain(settings, llm=llm)
    await brain.turn("explain this diagram")
    assert len(llm.prompts) == 2 and rec.of("notes") == []


def test_two_explaining_calls_in_one_answer_are_one_call():
    say = ToolCall(id="s", name="say", arguments={"text": "Here."})
    full = ToolCall(id="a", name="explain_on_screen", arguments=POST)
    empty = ToolCall(id="b", name="explain_on_screen", arguments={"title": "More", "items": []})
    # The empty one is left out, wherever it comes: it can neither refuse nor replace.
    assert one_card([say, full, empty]) == [say, full]
    assert [c.id for c in one_card([empty, say, full])] == ["s", "a"]
    # Two real ones: the points of both, in order, in the first one's place.
    more = {"title": "And", "items": [{"text": "A fourth thing", "quote": "Sidebar entry number 1"}], "closing": "That's all of it."}
    [_, merged] = one_card([say, full, ToolCall(id="c", name="explain_on_screen", arguments=more)])
    assert merged.id == "a" and [i["text"] for i in merged.arguments["items"]][2:] == ["How it landed", "A fourth thing"]
    assert merged.arguments["title"] == "What this post is" and merged.arguments["closing"] == "That's all of it."
    # One call is left exactly as it is, empty or not; so is everything that is not a card.
    assert one_card([say, empty]) == [say, empty] and one_card([say, full]) == [say, full]
    # Two empty ones are one empty one: it is sent back once, not twice.
    assert one_card([empty, say, ToolCall(id="d", name="explain_on_picture", arguments={})]) == [empty, say]


def test_merging_keeps_each_sketch_with_its_own_point():
    a = {"title": "One", "items": [{"text": "a"}, {"text": "b"}], "sketches": [{"kind": "tick", "after": 2}]}
    b = {"items": [{"text": "c"}], "sketches": [{"kind": "cross", "after": 1}, {"kind": "label", "after": 0}]}
    merged = merge_calls([a, b])
    assert [s["after"] for s in merged["sketches"]] == [2, 3, 0]
    assert merge_calls([{"items": []}, b])["items"] == [{"text": "c"}]
    # A picture's tags and a card cannot be one thing: the first kind is kept.
    assert merge_calls([a, {"style": "on_picture", "items": [{"text": "aorta"}]}])["items"] == a["items"]


async def test_a_second_call_with_nothing_in_it_leaves_the_first_card_alone(settings):
    """Live, 14:48: four points, then a call with none, and the user saw nothing."""
    llm = Scripted(
        [
            ("say", {"text": "It's a class trip."}),
            ("explain_on_screen", POST),
            ("explain_on_screen", {"title": "More", "items": []}),
        ]
    )
    brain, rec = make_brain(settings, llm=llm)
    brain.screen.ctx = post_context()
    await brain.turn("what is this post?")
    [card] = [e["card"] for e in rec.of("notes")]
    assert len(card["items"]) == 3 and rec.of("clear") == []
    assert len(llm.prompts) == 1  # nothing was refused, so nothing is asked again
    tools = [c["tool"] for c in brain.turns.recent(1)[0]["tool_calls"]]
    assert tools.count("explain_on_screen") == 1
    await asyncio.sleep(0)


async def test_an_empty_call_in_a_later_round_never_removes_the_card(settings):
    brain, rec = make_brain(settings)
    brain._ctx = post_context()
    assert await brain._act("explain_on_screen", POST) is None
    shown = brain._turn_card
    note = await brain._act("explain_on_screen", {"items": []})
    assert "stays as it is" in note and not note.startswith(EMPTY_CARD)
    assert brain._turn_card is shown and len(rec.of("notes")) == 1 and rec.of("clear") == []
    assert brain._placed.others("stage") != []  # its place is still kept clear of


async def test_two_cards_in_one_answer_are_shown_as_one(settings):
    more = {"title": "And", "items": [{"text": "A side note", "quote": "Sidebar entry number 1"}]}
    for make in (Scripted, Streamed):
        calls = [("say", {"text": "It's a class trip."}), ("explain_on_screen", POST), ("explain_on_screen", more)]
        llm = Scripted(calls) if make is Scripted else Streamed(calls)
        brain, rec = make_brain(settings, llm=llm)
        brain.screen.ctx = post_context()
        await brain.turn("what is this post?")
        cards = [e["card"] for e in rec.of("notes")]
        assert len({c["id"] for c in cards}) == 1, make  # one card on screen, never a second on top
        assert [i["text"] for i in cards[-1]["items"]] == ["Who posted it", "What it shows", "How it landed", "A side note"]
        assert [i["n"] for i in cards[-1]["items"]] == [1, 2, 3, 4]
        # It only ever grows: a later version never has fewer points than one before it.
        sizes = [len(c["items"]) for c in cards]
        assert sizes == sorted(sizes), make
        await asyncio.sleep(0)


def test_a_picture_none_of_whose_parts_were_found_gets_a_card_to_read():
    """Tags with nowhere to go are not drawn: the answer was spoken to a bare screen."""
    ctx = post_context()
    heart = {
        "style": "on_picture",
        "picture": [600, 100, 600, 500],
        "title": "How blood flows",
        "items": [{"text": "right atrium", "part": "right atrium"}, {"text": "to the lungs", "part": "pulmonary artery"}],
    }
    card = build_card(ctx, heart)  # no survey answer, and no rough spots from the model
    assert card.style == "card" and [it.text for it in card.items] == ["right atrium", "to the lungs"]
    assert card.place is not None and card.region == (600, 100, 600, 500)
    # With even one part found, it is tags on the picture, as before.
    heart["items"][0]["at"] = [800, 300]
    pinned = build_card(ctx, heart)
    assert pinned.style == "picture" and pinned.items[0].at == (800, 300)


def test_a_pictures_tags_go_with_the_page_once_however_often_it_is_checked():
    ctx = post_context()
    heart = {
        "style": "on_picture",
        "picture": [600, 100, 600, 500],
        "items": [{"text": "right atrium", "at": [800, 300]}, {"text": "aorta", "at": [900, 200]}],
    }
    now = moved(ctx.text, 80)
    first = build_card(ctx, {**heart, "items": heart["items"][:1]})
    settle_marks(ctx, heart, first, now)
    assert first.items[0].at == (800, 380) and first.picture == (600, 180, 600, 500)
    both = build_card(ctx, heart, keep=first)  # the second tag arrives: the first stays put
    settle_marks(ctx, heart, both, now, keep=first)
    assert [it.at for it in both.items] == [(800, 380), (900, 280)]
