"""The hands stop going up and down a page.

Live, 15 November: on a Maps list of restaurants, down, up, down, up for two and a half minutes
and 27 cents, ending "this was taking a lot of tries (22 looks)". Every scroll changed the screen,
so none of it counted as the same thing again.
"""

from __future__ import annotations

from test_operator import Scripted, operator


def _scroll(way: str):
    return ("act", {"actions": [{"do": "scroll", "direction": way}]})


async def test_back_and_forth_is_told_once_then_stopped():
    box = []
    llm = Scripted(*[_scroll(w) for w in ("down", "up", "down", "up", "down", "up", "down", "up")])
    out = await operator(llm, box).run("find the best rated place in the list")
    assert not out.ok and "up and down" in out.summary
    notes = [s.note for s in out.steps if s.actions == "(back and forth)"]
    assert len(notes) == 1 and "Do not scroll back again" in notes[0]
    scrolls = [d for d in box[0].did if d[0] == "scroll"]
    assert len(scrolls) <= 4  # not the eight it asked for


async def test_reading_down_a_page_and_coming_back_once_is_honest_work():
    box = []
    llm = Scripted(
        _scroll("down"), _scroll("down"), _scroll("down"), _scroll("up"),
        ("done", {"ok": True, "summary": "Found it."}),
    )
    out = await operator(llm, box).run("read the list")
    assert out.ok and out.summary == "Found it."
    assert not [s for s in out.steps if s.actions == "(back and forth)"]
