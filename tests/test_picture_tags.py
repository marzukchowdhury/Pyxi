"""Explaining a picture on the picture: parts found by name, tags placed clear of each other."""

import time

from PIL import Image

from blip.companion.aim import Survey, gridded, match
from blip.companion.notes import build_card, tag_size
from blip.partial_json import picture_head
from blip.screen.context import ScreenContext, WindowInfo

HEART = {
    "Right atrium": (430, 500),
    "Right ventricle": (480, 575),
    "Left atrium": (580, 480),
    "Left ventricle": (593, 555),
    "Superior vena cava": (403, 440),
    "Aorta (aortic arch)": (463, 399),
}


def screen() -> ScreenContext:
    ctx = ScreenContext(
        time.time(),
        WindowInfo("Heart", "firefox.exe", (0, 0, 1366, 768)),
        [],
        Image.new("RGB", (1366, 768), "white"),
    )
    ctx.model_scale = 1.0
    return ctx


def test_names_match_but_never_the_other_side():
    assert match("right atrium", HEART) == (430, 500)
    assert match("the left ventricle", HEART) == (593, 555)
    assert match("aorta", HEART) == (463, 399)
    assert match("vena cava", HEART) == (403, 440)
    # 'left' never finds the right one, and an unknown part finds nothing.
    assert match("left atrium", {"Right atrium": (1, 1)}) is None
    assert match("mitral valve", HEART) is None
    # Sharing one word of two isn't the same part.
    assert match("right atrium", {"Right ventricle": (1, 1)}) is None
    assert match("pulmonary artery", {"Pulmonary veins": (1, 1)}) is None
    assert match("left pulmonary veins", {"Pulmonary veins (left)": (2, 2)}) == (2, 2)


def test_the_streaming_call_names_its_picture_early():
    raw = '{"style": "on_picture", "picture": [228, 314, 533, 355], "title": "How bl'
    assert picture_head(raw) == {"style": "on_picture", "picture": [228.0, 314.0, 533.0, 355.0]}
    assert picture_head('{"style": "on_picture", "picture": [228, 31') is None
    assert picture_head('{"style": "card", "picture": [1, 2, 3, 4]}') is None


def test_grid_zooms_the_picture_to_a_readable_size():
    img, scale = gridded(Image.new("RGB", (500, 250)))
    assert max(img.size) == 1000 and scale == 2.0


def test_tags_pin_to_found_parts_and_never_overlap():
    args = {
        "style": "on_picture",
        "picture": [228, 314, 533, 355],
        "title": "Blood's route",
        "closing": "Want the left side next?",
        "items": [
            {
                "text": "body to atrium",
                "say": "Blood comes home.",
                "part": "superior vena cava",
                "flow": ["superior vena cava", "right atrium"],
            },
            {"text": "right atrium", "say": "It waits here.", "part": "right atrium"},
            {
                "text": "right ventricle",
                "say": "Then the big squeeze.",
                "part": "right ventricle",
                "flow": ["right atrium", "right ventricle"],
            },
            {"text": "left ventricle", "say": "The strongest wall.", "part": "left ventricle"},
            {"text": "mystery", "say": "A guess.", "part": "mitral valve", "at": [600, 520]},
        ],
    }
    card = build_card(screen(), args, survey=Survey(HEART))
    assert card.style == "picture" and card.place is None
    by_text = {it.text: it for it in card.items}
    assert by_text["right atrium"].at == (430, 500)
    assert by_text["body to atrium"].trace == [(403, 440), (430, 500)]
    assert by_text["mystery"].at == (600, 520)  # not found: the chat model's own guess
    boxes = []
    for it in card.items:
        assert it.tag is not None
        w, h = tag_size(it.text)
        x, y = it.tag[0] - w // 2, it.tag[1] - h // 2
        assert x >= 0 and x + w <= 1366 and y >= 0 and y + h <= 768
        boxes.append((x, y, w, h))
    for i, a in enumerate(boxes):
        for b in boxes[i + 1 :]:
            assert (
                a[0] + a[2] <= b[0]
                or b[0] + b[2] <= a[0]
                or a[1] + a[3] <= b[1]
                or b[1] + b[3] <= a[1]
            )


def test_tags_already_shown_stay_put_as_more_arrive():
    base = {"style": "on_picture", "picture": [228, 314, 533, 355], "title": "t"}
    one = [{"text": "right atrium", "say": "s", "part": "right atrium"}]
    first = build_card(screen(), {**base, "items": one}, survey=Survey(HEART))
    more = [*one, {"text": "left atrium", "say": "s", "part": "left atrium"}]
    second = build_card(screen(), {**base, "items": more}, survey=Survey(HEART), keep=first)
    assert second.items[0].tag == first.items[0].tag
