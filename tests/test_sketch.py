"""Sketchboards: diagrams that draw themselves while Wisp talks."""

from __future__ import annotations

import asyncio

from test_brain_and_server import make_brain
from test_connectors_and_docs import Script
from wisp.scenes.sketch import Sketchboard

HOVER = {
    "title": "Push air down, lift up",
    "subtitle": "Side view, nose left",
    "anchor": {"element_id": 1},
    "steps": [
        {"narration": "The jet has a big fan behind the cockpit."},
        {"narration": "It blasts air straight down."},
        {"narration": "Pushing air down pushes the jet up."},
        {"narration": "When lift equals weight, it hangs there."},
    ],
    "shapes": [
        {
            "kind": "polygon",
            "points": [
                [8, 34],
                [22, 29],
                [66, 29],
                [80, 20],
                [86, 20],
                [84, 31],
                [92, 34],
                [22, 38],
            ],
            "fill": True,
            "step": 0,
        },
        {"kind": "rect", "at": [34, 30], "size": [8, 7], "color": "ice", "step": 0},
        {"kind": "text", "text": "Lift fan", "at": [30, 25], "step": 0},
        {"kind": "arrow", "points": [[38, 40], [38, 56]], "color": "ice", "flow": True, "step": 1},
        {"kind": "arrow", "points": [[38, 20], [38, 6]], "color": "amber", "step": 2},
        {"kind": "equation", "text": "Lift = weight", "at": [58, 12], "step": 3},
    ],
}


def test_a_broken_board_is_repaired_not_rejected():
    raw = {**HOVER, "shapes": [*HOVER["shapes"], {"kind": "arrow", "points": [[1, 1]]}, "junk"]}
    raw["shapes"].append({"kind": "text", "text": "Way off", "at": [400, -9], "step": 12})
    board = Sketchboard.model_validate(raw)
    assert len(board.shapes) == 7  # the one-point arrow and the junk are gone
    off = board.shapes[-1]
    assert off.at == [100.0, 0.0] and off.step == 3  # clamped to the grid and the last step
    assert any("malformed" in n for n in board.notes)


async def test_a_sketch_draws_first_speaks_once_and_stays_off_what_it_explains(settings):
    llm = Script(
        [
            ("say", {"text": "Yep, it really can. Here's the trick. And a lot more words."}),
            ("sketch_diagram", HOVER),
        ]
    )
    brain, rec = make_brain(settings, llm=llm)
    await brain.turn("is this real? can a jet actually hover?")
    for _ in range(100):
        if len(rec.of("scene_audio")) >= 4:
            break
        await asyncio.sleep(0.02)
    [ev] = rec.of("sketch")
    names = [e["type"] for e in rec.events if e["type"] in ("sketch", "say")]
    assert names[0] == "sketch"  # the drawing goes up before Wisp starts talking
    # The verdict and the hook are said; the rest is left to the drawing.
    assert " ".join(s["text"] for s in rec.of("say")) == "Yep, it really can. Here's the trick."
    anchor = ev["board"]["anchor"]["rect"]
    assert anchor == [100, 100, 800, 600]  # the thread knows what it points at
    x, y, w, h = ev["place"]
    ax, ay, aw, ah = anchor
    assert x >= ax + aw or x + w <= ax or y >= ay + ah or y + h <= ay  # never covers it
    assert [a["step"] for a in rec.of("scene_audio")] == [0, 1, 2, 3]
    assert all(a["scene_id"] == ev["audio_key"] for a in rec.of("scene_audio"))


def test_the_opener_keeps_the_verdict_and_drops_filler():
    from wisp.companion.brain import visual_opener

    assert visual_opener("There you go. Real. Only the B can do it. More, more, more.") == (
        "Real. Only the B can do it."
    )
    assert visual_opener("Sure!") == "Sure!"  # nothing else to say: better than silence


async def test_a_drawing_without_a_reply_narrates_itself_instead_of_a_canned_line(settings):
    brain, rec = make_brain(settings, llm=Script([("sketch_diagram", HOVER)]))
    await brain.turn("can a jet hover?")
    await asyncio.sleep(0.05)
    assert rec.of("sketch") and not rec.of("say")


async def test_a_board_without_steps_is_narrated_by_the_reply_not_dropped(settings):
    # Haiku sometimes leaves out `steps`: the drawing must still appear, told by the reply.
    board = {k: v for k, v in HOVER.items() if k != "steps"}
    reply = "Yes, but only the B version. It has a lift fan. The nozzle points down. Lift equals weight."
    brain, rec = make_brain(
        settings, llm=Script([("say", {"text": reply}), ("sketch_diagram", board)])
    )
    await brain.turn("can this jet hover?")
    for _ in range(100):
        if len(rec.of("scene_audio")) >= 4:
            break
        await asyncio.sleep(0.02)
    [ev] = rec.of("sketch")
    assert [s["narration"] for s in ev["board"]["steps"]] == [
        "Yes, but only the B version.",
        "It has a lift fan.",
        "The nozzle points down.",
        "Lift equals weight.",
    ]
    assert not rec.of("say")  # said once, by the drawing, not twice


async def test_a_rejected_board_is_retried_without_repeating_the_reply(settings):
    llm = Script(
        [("say", {"text": "Real. Watch this."}), ("sketch_diagram", {"title": "x", "shapes": []})],
        [("say", {"text": "Real. Watch this."}), ("sketch_diagram", HOVER)],
    )
    brain, rec = make_brain(settings, llm=llm)
    await brain.turn("can this jet hover?")
    await asyncio.sleep(0.1)
    assert rec.of("sketch")  # the second try drew it
    assert " ".join(s["text"] for s in rec.of("say")) == "Real. Watch this."  # said once
