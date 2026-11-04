"""Reading the pixels (OCR) and putting things where they hide nothing."""

from __future__ import annotations

import time

import pytest
from PIL import Image, ImageDraw, ImageFont

from conftest import fake_context
from wisp.companion.layout import Occupancy, Placed
from wisp.companion.notes import build_card, place_card, place_stage
from wisp.screen import ScreenContext, ocr
from wisp.screen.context import WindowInfo
from wisp.screen.ocr import TextLine, find_text


def line(text: str, x: int, y: int, word_w: int = 60, h: int = 20, id: int = 0) -> TextLine:
    """A fake OCR line: each word word_w px wide with a 10 px space."""
    words, cx = [], x
    for w in text.split():
        words.append((w, (cx, y, word_w, h)))
        cx += word_w + 10
    return TextLine(text, (x, y, cx - 10 - x, h), words, id)


POST = [
    line("Vance listed 4 reasons the GOP lost:", 515, 353, id=900),
    line("1. Iran War", 515, 392, id=901),
    line("2. Cost of living and inflation", 515, 411, id=902),
    line('3. Voters want "America First"', 515, 429, id=903),
]


def test_a_quote_finds_the_words_themselves_not_the_whole_line():
    # "Iran War" is words 2-3 of "1. Iran War": the box starts after "1." and covers two words.
    assert find_text(POST, "Iran War") == (515 + 70, 392, 130, 20)
    # Case, quotes and punctuation don't matter.
    assert find_text(POST, "america first") == (515 + 3 * 70, 429, 130, 20)
    # One letter misread by OCR still lands on the right line.
    assert find_text(POST, "Cost of livinq and inflation") == POST[2].rect
    assert find_text(POST, "Nothing like this is on screen") is None


def test_note_points_and_ticks_land_on_quoted_words_inside_an_image():
    ctx = fake_context()
    ctx.text = POST
    card = build_card(
        ctx,
        {
            "title": "What Vance blamed",
            "items": [
                {"text": "The war comes first", "quote": "Iran War", "mark": "underline"},
                {"text": "Money worries", "element_id": 902},  # an OCR line id works too
            ],
            "sketches": [{"kind": "tick", "quote": "America First"}],
        },
    )
    assert card.items[0].rect == (585, 392, 130, 20) and card.items[0].mark == "underline"
    assert card.items[1].rect == POST[2].rect
    x, y, w, h = find_text(POST, "America First")
    assert card.sketches[0].points == [(x + w + int(h * 0.9), y + h // 2)]  # just after the words


def test_ocr_lines_are_listed_for_the_model_and_resolve_like_elements():
    ctx = fake_context()
    ctx.text = POST
    text = ctx.render()
    assert "Text read from the pixels" in text and '[901] "1. Iran War"' in text
    assert ctx.element(901).name == "1. Iran War"


def busy_screen() -> ScreenContext:
    """Left two thirds: a noisy photo and lines of text. Right third: empty background."""
    img = Image.new("RGB", (1920, 1080), (245, 245, 248))
    d = ImageDraw.Draw(img)
    for y in range(0, 1080, 4):
        for x in range(0, 1280, 8):
            d.rectangle([x, y, x + 3, y + 1], fill=((x * 7) % 255, (y * 3) % 255, 90))
    ctx = ScreenContext(time.time(), WindowInfo("Feed", "browser.exe", (0, 0, 1920, 1080)), [], img)
    ctx.text = [line("A long line of words here", 100, 120 + 40 * i, id=i + 1) for i in range(20)]
    return ctx


def test_a_3d_stage_goes_where_nothing_is():
    ctx = busy_screen()
    x, y, w, h = place_stage(ctx)
    assert x >= 1280 - 16 and x + w <= 1920  # entirely in the empty right third
    occ = Occupancy(ctx)
    assert occ.cost((x, y, w, h)) < occ.cost((100, 100, w, h)) / 50


def test_the_card_keeps_clear_of_the_stage_and_the_notch():
    ctx = busy_screen()
    stage = place_stage(ctx)
    region = POST[1].rect
    cx, cy = place_card(ctx, region, ["one", "two"], "Title", avoid=[stage])
    card = (cx, cy, 300, 200)
    sx, sy, sw, sh = stage
    assert cx + 300 <= sx or cx >= sx + sw or cy + 200 <= sy or cy >= sy + sh
    notch_left = (1920 - 460) // 2
    assert not (cy < 70 and cx < notch_left + 460 and cx + 300 > notch_left)
    assert card


def test_placed_things_expire():
    p = Placed()
    p.put("card", (0, 0, 10, 10), 30)
    p.put("stage", (50, 50, 10, 10), -1)  # already gone
    assert p.others("stage") == [(0, 0, 10, 10)] and p.others("card") == []


@pytest.mark.skipif(not ocr.available(), reason="Windows OCR isn't available here")
def test_windows_ocr_reads_text_in_a_picture():
    img = Image.new("RGB", (900, 300), (0, 0, 0))  # white on black, like the post's image
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("segoeui.ttf", 30)
    except OSError:
        font = ImageFont.load_default()
    d.text((40, 60), "Vance listed 4 reasons the GOP lost:", fill="white", font=font)
    d.text((40, 120), "1. Iran War", fill="white", font=font)
    lines = ocr.read_lines(img, origin=(1000, 500))
    hit = find_text(lines, "Iran War")
    assert hit is not None
    x, y, _, h = hit
    assert 1000 + 60 < x < 1000 + 140 and 500 + 115 < y + h // 2 < 500 + 165  # offset by origin
