"""A photo made into a 3D model (scenes/photo.py): the picture is made safe, the thing is cut
out and measured from the pixels, the model is held against the photo, and fixes are bounded.

Every picture here is drawn in the test; no model and no network is used."""

from __future__ import annotations

import io

import numpy as np
import pytest
from otto.types import LLMResponse, ToolCall, Usage
from PIL import Image, ImageDraw, ImageFilter

from pyxi.scenes import photo
from pyxi.scenes.objects import ObjectScene


# ------------------------------------------------------------------ drawn pictures
def backdrop(w: int = 900, h: int = 700) -> Image.Image:
    """A studio backdrop: light at the top, darker at the bottom."""
    ramp = np.linspace(238, 205, h)[:, None] * np.ones((1, w))
    return Image.fromarray(np.stack([ramp, ramp, ramp + 4], axis=2).astype(np.uint8))


def mug_picture() -> Image.Image:
    """A blue mug with a yellow handle and a soft shadow under it."""
    img = backdrop()
    d = ImageDraw.Draw(img)
    d.ellipse([250, 560, 640, 620], fill=(190, 190, 194))  # shadow
    d.ellipse([500, 260, 720, 480], fill=(232, 190, 40))  # handle: a ring
    d.ellipse([545, 305, 675, 435], fill=(222, 222, 226))  # ... with the backdrop through it
    d.rounded_rectangle([280, 180, 560, 590], radius=30, fill=(46, 104, 190))
    return img


def as_bytes(img: Image.Image, fmt: str = "PNG", **kw) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format=fmt, **kw)
    return buf.getvalue()


MUG_SEEN = {
    "kind": "object",
    "objects": ["blue mug"],
    "subject": "blue ceramic mug",
    "box": [300, 240, 810, 850],
    "facing": "toward_viewer",
    "elevation": 5,
    "depth": 1.0,
    "parts": [
        {"id": "body", "name": "body", "box": [311, 257, 622, 843], "shape": "lathe", "material": "ceramic"},
        {"id": "handle", "name": "handle", "box": [622, 371, 800, 686], "shape": "tube", "material": "ceramic", "form": "C loop on the right"},
        {"id": "base", "name": "underside", "box": [311, 800, 622, 843], "shape": "cylinder", "material": "ceramic", "hidden": True},
    ],
    "hidden": "the far side mirrors this one",
    "details": ["yellow handle"],
}

MUG_MODEL = {
    "title": "Mug",
    "parts": [
        {"id": "body", "shape": "cylinder", "size": [2.8, 4.1, 2.8], "material": "ceramic"},
        {"id": "handle", "shape": "torus", "axis": "z", "size": [2.2, 2.2, 0.45], "position": [1.5, 0.2, 0], "material": "ceramic"},
    ],
}
SLAB = {"title": "Slab", "parts": [{"id": "body", "shape": "box", "size": [9, 1.2, 3], "material": "ceramic"}]}


def seen_mug() -> tuple[photo.Picture, photo.Seen]:
    picture = photo.load_picture(mug_picture())
    return picture, photo.measure(picture, photo.parse_seen(MUG_SEEN))


# ------------------------------------------------------------------ 1. the picture
def test_a_20_megapixel_photo_is_made_small_before_anything_else():
    big = Image.effect_noise((5472, 3648), 40).convert("RGB")
    picture = photo.load_picture(as_bytes(big, "JPEG", quality=70))
    assert max(picture.image.size) <= photo.MODEL_SIDE
    assert picture.original == (5472, 3648)
    assert any("made smaller" in n for n in picture.notes)
    assert len(picture.jpeg()) < 600_000  # what the model is sent
    assert picture.thumbnail().startswith("data:image/jpeg;base64,") and len(picture.thumbnail()) < 120_000


def test_a_see_through_png_keeps_its_own_outline():
    img = Image.new("RGBA", (400, 300), (0, 0, 0, 0))
    ImageDraw.Draw(img).ellipse([100, 50, 300, 250], fill=(200, 40, 40, 255))
    picture = photo.load_picture(as_bytes(img))
    assert picture.cutout is not None and picture.image.mode == "RGB"
    outline = photo.subject_mask(picture)
    assert outline.trusted and outline.why == "its own cut-out"
    assert outline.box == pytest.approx((0.25, 1 / 6, 0.75, 5 / 6), abs=0.02)
    assert outline.aspect == pytest.approx(1.0, abs=0.05)
    # What was see-through is a plain backdrop for the model to look at, not black.
    assert picture.image.getpixel((5, 5)) == (236, 238, 241)


@pytest.mark.parametrize(
    ("source", "says"),
    [
        (b"", "empty"),
        (b"this is not a picture at all", "couldn't open"),
        (as_bytes(Image.new("RGB", (30, 30), "red")), "too small"),
        (as_bytes(Image.new("RGBA", (300, 300), (0, 0, 0, 0))), "see-through all over"),
    ],
)
def test_a_picture_that_cannot_be_used_is_turned_away_in_words(source, says):
    with pytest.raises(photo.PictureError) as exc:
        photo.load_picture(source)
    assert says in exc.value.say and "Traceback" not in exc.value.say


def test_a_file_that_is_not_a_picture_or_not_there_is_turned_away(tmp_path):
    notes = tmp_path / "notes.txt"
    notes.write_text("hello")
    with pytest.raises(photo.PictureError, match="isn't a picture"):
        photo.load_picture(notes)
    with pytest.raises(photo.PictureError, match="can't find"):
        photo.load_picture(tmp_path / "chair.jpg")
    renamed = tmp_path / "words.png"
    renamed.write_text("hello")
    with pytest.raises(photo.PictureError, match="couldn't open"):
        photo.load_picture(renamed)
    good = tmp_path / "mug.png"
    mug_picture().save(good)
    assert photo.load_picture(good).image.size == (900, 700)


def test_a_blurry_picture_is_noticed_and_a_sharp_one_is_not():
    sharp = mug_picture()
    ImageDraw.Draw(sharp).text((300, 300), "MUG " * 20, fill=(255, 255, 255))
    for x in range(0, 900, 12):  # some fine detail, as any real photo has
        ImageDraw.Draw(sharp).line([x, 0, x, 700], fill=(120, 120, 130))
    assert not photo.load_picture(sharp).blurry
    assert photo.load_picture(sharp.filter(ImageFilter.GaussianBlur(9))).blurry


def test_the_same_picture_has_the_same_fingerprint():
    a, b = photo.load_picture(as_bytes(mug_picture())), photo.load_picture(as_bytes(mug_picture()))
    c = photo.load_picture(as_bytes(backdrop()))
    assert a.digest == b.digest and a.digest != c.digest


# ------------------------------------------------------------------ 2. cut out and measured
def test_the_thing_is_cut_out_of_a_shaded_backdrop_shadow_and_all():
    outline = photo.subject_mask(photo.load_picture(mug_picture()))
    assert outline.trusted
    # The mug and its handle, not the soft shadow under it and not the backdrop's shading.
    assert outline.box == pytest.approx((280 / 900, 180 / 700, 720 / 900, 590 / 700), abs=0.025)
    assert outline.aspect == pytest.approx(440 / 410, abs=0.08)
    h, w = outline.mask.shape
    assert not outline.mask[int(0.95 * h), int(0.5 * w)]  # under the shadow
    assert not outline.mask[int(370 / 700 * h), int(610 / 900 * w)]  # the hole in the handle
    assert outline.mask[int(0.5 * h), int(420 / 900 * w)]  # the body


def test_a_busy_background_is_said_to_be_untrustworthy_not_guessed_at():
    rng = np.random.default_rng(7)
    busy = Image.fromarray(rng.integers(0, 255, (60, 80, 3), dtype=np.uint8)).resize((800, 600), Image.NEAREST)
    ImageDraw.Draw(busy).rectangle([300, 200, 500, 400], fill=(200, 30, 30))
    outline = photo.subject_mask(photo.load_picture(busy))
    assert not outline.trusted and "busy" in outline.why


def test_a_thing_that_fills_the_picture_gives_no_outline_to_trust():
    img = backdrop()
    ImageDraw.Draw(img).rectangle([0, 100, 900, 700], fill=(30, 90, 40))
    outline = photo.subject_mask(photo.load_picture(img))
    assert not outline.trusted


def test_one_of_two_things_is_cut_out_when_told_where_it_is():
    img = backdrop()
    d = ImageDraw.Draw(img)
    d.rectangle([80, 250, 330, 550], fill=(190, 40, 40))
    d.ellipse([560, 200, 820, 460], fill=(40, 60, 180))
    picture = photo.load_picture(img)
    both = photo.subject_mask(picture)
    assert both.box[0] < 0.12 and both.box[2] > 0.88  # with no hint, both
    ball = photo.subject_mask(picture, (560 / 900, 200 / 700, 820 / 900, 460 / 700))
    assert ball.trusted and ball.box == pytest.approx((560 / 900, 200 / 700, 820 / 900, 460 / 700), abs=0.02)


def test_sizes_places_and_colours_come_from_the_pixels_not_the_model():
    picture, seen = seen_mug()
    assert seen.outline is not None and seen.outline.trusted
    assert seen.aspect == pytest.approx(440 / 410, abs=0.08)
    body, handle, base = seen.parts
    # The body is 280 of the thing's 440 px wide and all of its height, left of the middle.
    assert body.wide == pytest.approx(280 / 440, abs=0.05) and body.tall == pytest.approx(1.0, abs=0.04)
    assert body.at[0] < -0.1 and handle.at[0] > 0.3
    assert handle.wide == pytest.approx(160 / 440, abs=0.06)

    def rgb(c: str) -> tuple[int, ...]:
        return tuple(int(c[i : i + 2], 16) for i in (1, 3, 5))

    assert rgb(body.color) == pytest.approx((46, 104, 190), abs=8)  # the blue that was drawn
    assert rgb(handle.color) == pytest.approx((232, 190, 40), abs=10)  # not the backdrop in its hole
    assert base.color is None  # hidden: nothing to sample
    assert seen.colors[0][0] == body.color or rgb(seen.colors[0][0]) == pytest.approx((46, 104, 190), abs=10)


def test_the_main_colour_is_the_paint_not_the_glint_or_the_shadow():
    paint = np.tile(np.array([[200, 30, 35]], dtype=np.float32), (800, 1))
    shade = np.tile(np.array([[90, 12, 14]], dtype=np.float32), (100, 1))
    glint = np.tile(np.array([[255, 250, 250]], dtype=np.float32), (60, 1))
    r, g, b = photo.dominant_color(np.concatenate([paint, shade, glint]))
    assert (r, g, b) == pytest.approx((200, 30, 35), abs=4)
    assert photo.dominant_color(np.zeros((2, 3))) is None
    # A white thing with a red flash is white.
    white = np.tile(np.array([[240, 240, 238]], dtype=np.float32), (700, 1))
    flash = np.tile(np.array([[210, 20, 30]], dtype=np.float32), (300, 1))
    assert photo.dominant_color(np.concatenate([white, flash])) == pytest.approx((240, 240, 238), abs=4)


def test_a_careless_description_is_cleaned_not_fatal():
    seen = photo.parse_seen(
        {
            "kind": "banana",
            "subject": "",
            "objects": ["mug", "", 7],
            "box": [900, 100, 100, 950, 5],
            "facing": "sideways",
            "elevation": "above",
            "depth": "deep",
            "parts": [
                {"id": "Body One!", "name": "", "box": [0, 0, 500, 500], "shape": "prism", "material": "unobtainium", "count": 400},
                {"id": "body_one", "box": [0, 0, 500, 500]},  # the same id again
                {"id": "flat", "box": [10, 10, 10, 400]},  # no area
                "nonsense",
                {"box": [0, 0, 5, 5]},
            ],
        }
    )
    assert seen.kind == "object" and seen.subject
    assert seen.box == pytest.approx((0.1, 0.1, 0.9, 0.95))
    assert seen.view == (0.0, 24.0) and seen.depth == 1.0
    [part] = seen.parts
    assert (part.id, part.shape, part.material, part.count) == ("body_one", "box", "plastic", 24)
    assert photo.parse_seen({"kind": "object", "elevation": 400}).elevation == 88.0


def test_the_builder_is_told_the_measurements_and_the_angle():
    picture, seen = seen_mug()
    brief = photo.brief_for(seen, show="how the handle is joined")
    body = seen.parts[0]
    assert "blue ceramic mug" in brief and body.color in brief
    assert f"wide {body.wide:.2f}" in brief and "RIGHT of the picture is at +x" in brief
    assert "hidden from this angle" in brief and "how the handle is joined" in brief
    assert f"{seen.aspect:.2f} times as wide" in brief
    turned = photo.parse_seen({**MUG_SEEN, "facing": "toward_left", "elevation": 30})
    photo.measure(picture, turned)
    turned_brief = photo.brief_for(turned)
    assert "front points to the picture's left" in turned_brief and "40 degrees" in turned_brief
    assert "further BACK" in turned_brief  # looking down on it: up in the picture is back


def test_seen_parts_are_painted_with_their_sampled_colours():
    _, seen = seen_mug()
    model = ObjectScene.model_validate(
        {
            "title": "Mug",
            "parts": [
                {"id": "body", "shape": "cylinder", "color": "#ffffff"},
                {"id": "handle", "shape": "torus", "position": [2, 0, 0], "mirror": True},
                {"id": "base", "shape": "cylinder", "color": "#123456"},
                {"id": "coffee", "shape": "cylinder", "material": "glass", "color": "#221111"},
                {"id": "bolt", "shape": "sphere"},
            ],
        }
    )
    painted = {p.id: p.color for p in photo.paint(model, seen).parts}
    assert painted["body"] == seen.parts[0].color
    assert painted["handle"] == painted["handle_twin"] == seen.parts[1].color  # a mirrored copy too
    assert painted["base"] == "#123456"  # hidden in the photo: the builder's own colour stays
    assert painted["coffee"] == "#221111" and painted["bolt"] is None


# ------------------------------------------------------------------ 4. held against the photo
def test_a_right_model_scores_well_and_a_slab_is_caught_without_a_model_call():
    picture, seen = seen_mug()
    good, _ = photo.compare(photo.paint(ObjectScene.model_validate(MUG_MODEL), seen), picture, seen)
    bad, _ = photo.compare(ObjectScene.model_validate(SLAB), picture, seen)
    assert good.overlap > 0.75 and not good.wrong
    assert good.stretch == pytest.approx(1.0, abs=0.15)
    assert bad.wrong and bad.score < good.score - 0.3
    assert any("too wide" in f for f in bad.findings)
    assert good.color is not None and good.cells >= 3


def test_the_angle_is_found_even_when_left_and_right_were_read_the_wrong_way():
    """An L-shaped thing photographed with its foot to the right, read as turned the other way."""
    img = backdrop()
    d = ImageDraw.Draw(img)
    d.rectangle([300, 150, 380, 550], fill=(60, 60, 70))
    d.rectangle([300, 470, 640, 550], fill=(60, 60, 70))
    picture = photo.load_picture(img)
    model = ObjectScene.model_validate(
        {
            "title": "L",
            "parts": [
                {"id": "post", "shape": "box", "size": [0.8, 4, 0.8], "position": [0, 2, 0]},
                {"id": "foot", "shape": "box", "size": [0.8, 0.8, 3.4], "position": [0, 0.4, 1.3]},
            ],
        }
    )
    seen = photo.parse_seen({"kind": "object", "subject": "an L", "objects": ["L"], "facing": "left", "elevation": 0})
    photo.measure(picture, seen)
    assert seen.view[0] == 90
    wrong_side = photo.overlap(seen.outline.mask, photo.raster.render(model, 90, 0, 96).mask)
    found = photo.find_view(model, seen)
    assert found[0] < -60  # the mirror of what was read
    assert photo.overlap(seen.outline.mask, photo.raster.render(model, found[0], found[1], 96).mask) > wrong_side + 0.2


def test_without_an_outline_to_trust_only_the_proportion_is_compared():
    rng = np.random.default_rng(3)
    busy = Image.fromarray(rng.integers(0, 255, (60, 80, 3), dtype=np.uint8)).resize((800, 600), Image.NEAREST)
    picture = photo.load_picture(busy)
    seen = photo.parse_seen({"kind": "object", "subject": "a box", "objects": ["box"], "box": [300, 300, 700, 700]})
    photo.measure(picture, seen)
    match, _ = photo.compare(ObjectScene.model_validate(SLAB), picture, seen)
    assert match.overlap is None and match.color is None and not match.close
    assert match.view == seen.view and match.wrong  # a slab is still far too wide for a square


def test_fixes_change_only_known_fields_of_known_parts():
    model = ObjectScene.model_validate(MUG_MODEL)
    fixed = photo.apply_review(
        model,
        {
            "update": [
                {"id": "body", "size": [3, 5, 3], "color": "#aa0000", "id_": "x", "label": "Hacked", "hidden": True},
                {"id": "ghost", "size": [9, 9, 9]},
            ],
            "remove": ["handle", "nothing"],
            "add_parts": [{"id": "rim", "shape": "torus", "size": [3, 0.2, 3], "position": [0, 2.5, 0]}, {"shape": "box"}],
        },
    )
    assert fixed is not None and [p.id for p in fixed.parts] == ["body", "rim"]
    body = fixed.parts[0]
    assert body.size == [3, 5, 3] and body.color == "#aa0000"
    assert body.label is None and not body.hidden  # not fields a fix may touch
    assert fixed.id == model.id
    assert photo.apply_review(model, {"update": [{"id": "ghost", "size": [1, 1, 1]}]}) is None
    assert photo.apply_review(model, {"remove": ["body", "handle"]}) is None  # never down to nothing
    assert photo.apply_review(model, {"update": [{"id": "body", "size": "huge"}]}) is not None  # repaired, not fatal


class Looks:
    """A stand-in for the model: plays back tool calls in order and counts the calls."""

    def __init__(self, *replies) -> None:
        self.replies = list(replies)
        self.calls: list[str] = []
        self.images = 0

    async def complete(self, *, system, messages, tools=None, **_):
        self.calls.append(tools[0].name if tools else "")
        self.images += sum(1 for p in messages[-1].parts if getattr(p, "type", "") == "image")
        reply = self.replies.pop(0) if self.replies else None
        if isinstance(reply, Exception):
            raise reply
        calls = [ToolCall(id="1", name=reply[0], arguments=reply[1])] if reply else []
        return LLMResponse(tool_calls=calls, usage=Usage(cost_usd=0.01), stop_reason="tool_use")


TALLER = {"verdict": "fix", "problems": ["too squat"], "update": [{"id": "body", "size": [2.8, 4.1, 2.8]}]}
SQUASH = {"verdict": "fix", "problems": ["make it flat"], "update": [{"id": "body", "size": [12, 0.5, 2.8]}]}


async def test_a_fix_that_helps_is_kept_and_one_that_hurts_is_thrown_away():
    picture, seen = seen_mug()
    squat = ObjectScene.model_validate({**MUG_MODEL, "parts": [{**MUG_MODEL["parts"][0], "size": [2.8, 2.0, 2.8]}, MUG_MODEL["parts"][1]]})
    result = photo.PhotoResult(model=None, seen=seen)
    fixed = await photo.review(Looks(("review_model", TALLER), ("review_model", {"verdict": "good"})), squat, picture, seen, result)
    assert fixed.parts[0].size[1] == 4.1 and result.match.score > result.first_match.score
    assert any(c.startswith("fixed: too squat") for c in result.checks)

    result = photo.PhotoResult(model=None, seen=seen)
    kept = await photo.review(Looks(("review_model", SQUASH)), ObjectScene.model_validate(MUG_MODEL), picture, seen, result)
    assert kept.parts[0].size == [2.8, 4.1, 2.8]  # as built
    assert any("thrown away" in c for c in result.checks) and result.rounds == 1


async def test_never_more_than_two_looks_and_a_failed_look_keeps_the_model():
    picture, seen = seen_mug()
    squat = ObjectScene.model_validate({**MUG_MODEL, "parts": [{**MUG_MODEL["parts"][0], "size": [2.8, 1.0, 2.8]}, MUG_MODEL["parts"][1]]})
    nudge = {"verdict": "fix", "problems": ["a little taller"], "update": [{"id": "body", "size": [2.8, 1.3, 2.8]}]}
    llm = Looks(*[("review_model", nudge)] * 5)
    result = photo.PhotoResult(model=None, seen=seen)
    await photo.review(llm, squat, picture, seen, result)
    assert result.rounds == photo.MAX_ROUNDS == 2 and len(llm.calls) == 2

    result = photo.PhotoResult(model=None, seen=seen)
    model = ObjectScene.model_validate(MUG_MODEL)
    assert await photo.review(Looks(RuntimeError("offline")), model, picture, seen, result) is model
    assert any("could not be looked at" in c for c in result.checks)


async def test_the_whole_way_from_a_picture_to_a_model_with_its_photo_beside_it():
    picture = photo.load_picture(mug_picture())
    llm = Looks(("describe_picture", MUG_SEEN), ("build_3d_model", {**MUG_MODEL, "beats": [{"title": "A mug", "narration": "A blue mug."}]}), ("review_model", {"verdict": "good"}))
    heard: list[photo.Seen] = []

    async def on_seen(seen: photo.Seen) -> None:
        heard.append(seen)

    result = await photo.model_from_picture(llm, picture, on_seen=on_seen)
    assert llm.calls == ["describe_picture", "build_3d_model", "review_model"]
    assert llm.images == 3  # the photo for the read, the photo for the builder, the sheet for the look
    model = result.model
    assert model is not None and heard and heard[0].subject == "blue ceramic mug"
    assert model.photo.startswith("data:image/jpeg;base64,") and len(model.photo_view) == 2
    assert {p.id: p.color for p in model.parts}["body"] == result.seen.parts[0].color  # painted from the pixels
    assert result.cost_usd == pytest.approx(0.03) and set(result.timings) == {"read", "build", "compare"}
    # Asked again: the look at the picture is not paid for twice.
    again = Looks(("build_3d_model", MUG_MODEL), ("review_model", {"verdict": "good"}))
    second = await photo.model_from_picture(again, picture, seen=result.seen)
    assert again.calls == ["build_3d_model", "review_model"] and second.model is not None


@pytest.mark.parametrize("kind", ["person", "text", "scene", "nothing"])
async def test_nothing_is_built_from_a_person_a_page_of_text_or_no_thing_at_all(kind):
    llm = Looks(("describe_picture", {"kind": kind, "objects": ["chair"], "subject": "x"}))
    result = await photo.model_from_picture(llm, photo.load_picture(mug_picture()))
    assert result.model is None and result.seen.kind == kind
    assert llm.calls == ["describe_picture"]  # one cheap look, then it stops


async def test_the_build_can_be_stopped_after_the_look():
    llm = Looks(("describe_picture", MUG_SEEN))

    async def no(_seen: photo.Seen) -> bool:
        return False

    result = await photo.model_from_picture(llm, photo.load_picture(mug_picture()), on_seen=no)
    assert result.model is None and llm.calls == ["describe_picture"]


async def test_a_model_that_never_answers_reads_as_nothing_to_model():
    seen = await photo.read_picture(Looks(None, None), photo.load_picture(mug_picture()))
    assert seen.kind == "nothing"


def test_the_sheet_the_model_looks_at_is_one_small_picture():
    picture, seen = seen_mug()
    _, shot = photo.compare(ObjectScene.model_validate(MUG_MODEL), picture, seen)
    sheet = Image.open(io.BytesIO(photo.sheet(picture, shot)))
    assert sheet.width > sheet.height and sheet.width <= 1000
