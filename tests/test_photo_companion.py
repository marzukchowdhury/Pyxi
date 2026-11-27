"""'Make a 3D model of this picture', as the companion handles it (companion/photomodel.py):
which picture is meant, what is said when there is nothing to model, and that asking twice
is one build. Drawn pictures and a stand-in model: no model call, no network."""

from __future__ import annotations

import asyncio
import base64
import io
import time

import pytest
from PIL import Image

from pyxi import files_access
from pyxi.companion import photomodel
from pyxi.companion.tools import COMPANION_TOOLS, MODEL_FROM_PHOTO
from pyxi.scenes import photo
from pyxi.screen import ScreenContext, UIElement, WindowInfo
from test_brain_and_server import make_brain
from test_connectors_and_docs import Script
from test_photo_model import MUG_MODEL, MUG_SEEN, as_bytes, mug_picture

BUILT = {**MUG_MODEL, "beats": [{"title": "A mug", "narration": "A blue mug with a yellow handle."}]}
GOOD = ("review_model", {"verdict": "good"})


def page_with_a_picture() -> ScreenContext:
    """A browser page: some chrome, a small icon, and the mug photo as its one big picture."""
    shot = Image.new("RGB", (1600, 900), (250, 250, 252))
    shot.paste(mug_picture().resize((720, 560)), (400, 200))
    els = [
        UIElement(1, "Shop - Browser", "Pane", (0, 0, 1600, 900), 0),
        UIElement(2, "logo", "Image", (20, 20, 48, 48), 1, 1),
        UIElement(3, "Blue mug", "Image", (400, 200, 720, 560), 1, 1),
        UIElement(4, "Add to basket", "Button", (1200, 700, 200, 50), 1, 1),
    ]
    return ScreenContext(time.time(), WindowInfo("Shop - Browser", "browser.exe", (0, 0, 1600, 900)), els, shot)


def photo_brain(settings, chat, modeller):
    settings.model_3d = "anthropic/claude-sonnet-5.5"
    brain, rec = make_brain(settings, llm=Script(*chat))
    brain._model_llm = Script(*modeller)
    brain.screen.ctx = page_with_a_picture()
    return brain, rec


async def settle(brain, rec, kind: str = "object", tries: int = 300) -> None:
    for _ in range(tries):
        if rec.of(kind) and not photomodel.state(brain).building:
            break
        await asyncio.sleep(0.02)
    await asyncio.sleep(0.05)


# ------------------------------------------------------------------ the tool is there when asked for
def test_the_tool_is_offered_for_3d_asks_and_only_with_a_builder(settings):
    assert MODEL_FROM_PHOTO in COMPANION_TOOLS and MODEL_FROM_PHOTO.parameters["required"] == ["source"]
    settings.model_3d = "anthropic/claude-sonnet-5.5"
    brain, _ = make_brain(settings)
    brain._user_text = "what is this picture of?"
    assert "model_from_photo" not in {t.name for t in brain._companion_tools()}
    for ask in ("make a 3D model of this", "turn this photo into 3d", "make a model of the chair in this picture"):
        brain._user_text = ask
        assert "model_from_photo" in {t.name for t in brain._companion_tools()}, ask
    # The answer to "which thing in it?" has no "3D" in it: the tool stays while that picture is fresh,
    # and while a picture sits in the chat box.
    brain._user_text = "the chair, please"
    assert "model_from_photo" not in {t.name for t in brain._companion_tools()}
    photomodel.state(brain).keep(photo.load_picture(mug_picture()))
    names = {t.name for t in brain._companion_tools()}
    assert "model_from_photo" in names and "build_3d_model" not in names
    photomodel.state(brain).at -= photomodel.KEEP_S + 1  # five minutes on, it is forgotten
    assert "model_from_photo" not in {t.name for t in brain._companion_tools()}
    brain._attached = photo.load_picture(mug_picture())
    assert "model_from_photo" in {t.name for t in brain._companion_tools()}
    brain._attached, brain._user_text = None, "make a 3D model of this"
    settings.model_3d = None  # no builder: nothing to read, measure and build with
    assert "model_from_photo" not in {t.name for t in brain._companion_tools()}


def test_the_rules_tell_a_picture_from_a_name(settings):
    from pyxi.companion import prompts

    text = prompts.system_prompt("", "") if callable(getattr(prompts, "system_prompt", None)) else ""
    assert "model_from_photo" in text and "never" in text.lower()
    assert "people" in MODEL_FROM_PHOTO.description and "person" in MODEL_FROM_PHOTO.description


# ------------------------------------------------------------------ which picture
def test_this_means_the_big_picture_in_the_window_not_its_icons():
    ctx = page_with_a_picture()
    assert photomodel.picture_on_screen(ctx) == (400, 200, 720, 560)
    picture = photomodel.from_screen(ctx, {})
    assert picture.source == "screen" and picture.image.size[0] == pytest.approx(732, abs=4)
    pointed = photomodel.from_screen(ctx, {"element_id": 2})  # they pointed at the logo: too small
    assert pointed.image.size[0] < 80
    ctx.elements = ctx.elements[:1]  # a page that names no picture: the whole window is looked at
    assert photomodel.from_screen(ctx, {}).image.size[0] == 1024


def test_no_screen_or_a_private_window_is_said_plainly():
    with pytest.raises(photo.PictureError, match="can't see your screen"):
        photomodel.from_screen(None, {})
    ctx = page_with_a_picture()
    ctx.excluded = "Bank"
    with pytest.raises(photo.PictureError, match="private"):
        photomodel.from_screen(ctx, {})


async def test_a_picture_from_the_chat_box_pasted_or_dropped(tmp_path):
    data = "data:image/png;base64," + base64.b64encode(as_bytes(mug_picture())).decode()
    picture, problem = await photomodel.from_chat({"data": data})
    assert picture is not None and picture.source == "attached" and problem == ""
    file = tmp_path / "mug.png"
    mug_picture().save(file)
    picture, problem = await photomodel.from_chat({"path": str(file)})
    assert picture is not None and problem == ""
    assert await photomodel.from_chat(None) == (None, "")
    assert await photomodel.from_chat({"data": ""}) == (None, "")
    none, problem = await photomodel.from_chat({"data": "!!!not base64!!!"})
    assert none is None and "couldn't open" in problem
    none, problem = await photomodel.from_chat({"data": "A" * (photomodel.MAX_CHAT_BYTES * 2)})
    assert none is None and "too big" in problem
    none, problem = await photomodel.from_chat({"path": str(tmp_path / "gone.png")})
    assert none is None and "can't find" in problem


def test_a_picture_file_is_found_by_the_name_they_said(tmp_path):
    desktop, pictures = tmp_path / "Desktop", tmp_path / "Pictures"
    desktop.mkdir()
    pictures.mkdir()
    for folder, name in ((desktop, "chair.jpg"), (desktop, "chair notes.txt"), (pictures, "old chair.png"), (desktop, "armchair-2.png")):
        (folder / name).write_bytes(b"x")
    access = files_access.FileAccess(enabled=True, folders=[str(desktop), str(pictures)])
    assert photomodel.find_picture("chair", access).name == "chair.jpg"  # the exact name, and a picture
    assert photomodel.find_picture("chair.jpg", access).name == "chair.jpg"
    assert photomodel.find_picture("chair", access, "Pictures").name == "old chair.png"
    assert photomodel.find_picture("sofa", access) is None
    outside = tmp_path / "Secret"
    outside.mkdir()
    (outside / "sofa.png").write_bytes(b"x")
    assert photomodel.find_picture("sofa", access) is None  # never outside the folders they allowed


# ------------------------------------------------------------------ what Pyxi says
def test_it_says_why_there_is_nothing_to_model_and_what_would_work():
    person = photo.Seen(kind="person", subject="", objects=["wooden chair", "guitar"])
    line = photomodel.not_an_object(person)
    assert "don't make models of people" in line and "the wooden chair or the guitar" in line and line.endswith("?")
    assert "people" in photomodel.not_an_object(photo.Seen(kind="person", subject=""))
    assert "words" in photomodel.not_an_object(photo.Seen(kind="text", subject=""))
    assert "clearer" in photomodel.not_an_object(photo.Seen(kind="nothing", subject=""))
    scene = photomodel.not_an_object(photo.Seen(kind="scene", subject="", objects=["boat", "pier", "gull", "bench"]))
    assert "Which should I model: the boat, the pier or the gull?" in scene
    for said in (line, scene):
        assert "—" not in said and len(said) < 160


def test_it_says_when_it_chose_one_of_several_or_the_picture_is_blurry():
    sharp = photo.load_picture(mug_picture())
    several = photo.Seen(kind="object", subject="red mug", objects=["red mug", "smart speaker"])
    assert "more than one thing" in photomodel.heads_up(several, sharp, named=False) and "red mug" in photomodel.heads_up(several, sharp, named=False)
    assert photomodel.heads_up(several, sharp, named=True) == ""  # they said which: nothing to add
    one = photo.Seen(kind="object", subject="red mug", objects=["red mug"])
    assert photomodel.heads_up(one, sharp, named=False) == ""
    sharp.blurry = True
    assert "blurry" in photomodel.heads_up(one, sharp, named=False)


# ------------------------------------------------------------------ end to end
async def test_a_picture_on_screen_becomes_a_model_with_the_photo_beside_it(settings):
    brain, rec = photo_brain(
        settings,
        chat=[[("model_from_photo", {"source": "screen", "element_id": 3}), ("say", {"text": "On it, give me half a minute."})]],
        modeller=[[("describe_picture", MUG_SEEN)], [("build_3d_model", BUILT)], [GOOD]],
    )
    await brain.turn("make a 3D model of this")
    await settle(brain, rec)
    scene = rec.of("object")[-1]["scene"]
    assert scene["title"] == "Mug" and scene["photo"].startswith("data:image/jpeg;base64,")
    assert len(scene["photo_view"]) == 2 and not scene["cutaway"]
    assert rec.of("object")[-1]["restart"]  # the finished one plays its beats
    assert rec.of("say")[0]["text"].endswith("give me half a minute.")  # said while it builds
    # The picture the modeller was sent is the crop of the photo, not the whole screen.
    assert "blue ceramic mug" in brain._model_llm.prompts[1] and "Measured from the picture" in brain._model_llm.prompts[1]
    jobs = [e for e in rec.of("job_event") if e.get("event") == "status"]
    assert any("blue ceramic mug" in (e["data"].get("message") or "") for e in jobs)
    assert brain._object is not None and brain._object.photo


class Slow(Script):
    """A modeller that takes a moment to answer, as a real one does."""

    async def complete(self, **kw):
        await asyncio.sleep(0.08)
        return await super().complete(**kw)


async def test_asking_twice_in_a_row_is_one_build(settings):
    brain, rec = photo_brain(settings, chat=[], modeller=[])
    brain._model_llm = Slow([("describe_picture", MUG_SEEN)], [("build_3d_model", BUILT)], [GOOD])
    brain._ctx = brain.screen.ctx
    first = await photomodel.handle(brain, {"source": "screen", "element_id": 3})
    second = await photomodel.handle(brain, {"source": "screen", "element_id": 3})
    assert first is None and "already being built" in second
    await settle(brain, rec)
    assert len(brain._model_llm.prompts) == 3  # one look, one build, one comparison
    # Once it is done, asking again builds again, without paying for the look a second time.
    brain._model_llm = Script([("build_3d_model", BUILT)], [GOOD])
    assert await photomodel.handle(brain, {"source": "screen", "element_id": 3}) is None
    await settle(brain, rec)
    assert len(brain._model_llm.prompts) == 2 and len([e for e in rec.of("object") if e["restart"]]) == 2


async def test_a_person_in_the_picture_is_not_modelled_and_the_answer_can_name_a_thing(settings):
    person = {"kind": "person", "objects": ["wooden chair"], "subject": "a person sitting"}
    brain, rec = photo_brain(settings, chat=[], modeller=[[("describe_picture", person)], [("describe_picture", MUG_SEEN)], [("build_3d_model", BUILT)], [GOOD]])
    brain._ctx = brain.screen.ctx
    assert await photomodel.handle(brain, {"source": "screen", "element_id": 3}) is None
    await settle(brain, rec, kind="say")
    assert not rec.of("object")
    assert any("don't make models of people" in e["text"] and "wooden chair" in e["text"] for e in rec.of("say"))
    assert len(brain._model_llm.prompts) == 1  # one look and no build
    # "The chair, then": the same picture, with no second look at the screen needed.
    brain._ctx = None
    assert await photomodel.handle(brain, {"source": "attached", "object": "the chair"}) is None
    await settle(brain, rec)
    assert rec.of("object") and "The user asked for: the chair" in brain._model_llm.prompts[1]


async def test_a_page_of_text_and_no_picture_at_all_end_in_a_sentence_not_a_model(settings):
    words = {"kind": "text", "objects": [], "subject": ""}
    brain, rec = photo_brain(settings, chat=[], modeller=[[("describe_picture", words)]])
    brain._ctx = brain.screen.ctx
    brain._ctx.elements = brain._ctx.elements[:1]  # nothing on the page says it is a picture
    assert await photomodel.handle(brain, {"source": "screen"}) is None
    await settle(brain, rec, kind="say")
    assert not rec.of("object") and any("no object in it" in e["text"] for e in rec.of("say"))
    # Nothing in the chat box, and they said it was there.
    fresh, _ = photo_brain(settings, chat=[], modeller=[])
    note = await photomodel.handle(fresh, {"source": "attached"})
    assert note.startswith("model_from_photo: there is no picture in the chat box")
    # No screen to look at.
    fresh._ctx = None
    note = await photomodel.handle(fresh, {"source": "screen"})
    assert "nothing was built" in note and "can't see your screen" in note
    assert photomodel.state(fresh).building == ""


async def test_a_picture_in_the_chat_box_is_seen_by_the_chat_model_and_modelled(settings):
    brain, rec = photo_brain(
        settings,
        chat=[[("model_from_photo", {"source": "attached"}), ("say", {"text": "On it."})]],
        modeller=[[("describe_picture", MUG_SEEN)], [("build_3d_model", BUILT)], [GOOD]],
    )
    picture = photo.load_picture(as_bytes(mug_picture()), kind="attached")
    await brain.turn("Make a 3D model of this picture.", picture=picture)
    await settle(brain, rec)
    assert "They put a picture in the chat box" in brain.llm.prompts[0]
    assert rec.of("object")[-1]["scene"]["photo"]
    # Said to be "on screen" with nothing pointed at: the picture in the chat box is still the one.
    brain._model_llm = Script([("build_3d_model", BUILT)], [GOOD])
    assert await photomodel.handle(brain, {"source": "screen"}) is None
    await settle(brain, rec)
    assert photomodel.state(brain).picture is picture
    # Pointed at something on screen: that, not the chat box.
    brain._ctx = brain.screen.ctx
    brain._model_llm = Script([("describe_picture", MUG_SEEN)], [("build_3d_model", BUILT)], [GOOD])
    assert await photomodel.handle(brain, {"source": "screen", "element_id": 3}) is None
    await settle(brain, rec)
    assert photomodel.state(brain).picture.source == "screen"
    # A picture that could not be opened is said, not silently dropped.
    brain2, _ = photo_brain(settings, chat=[[("say", {"text": "That picture would not open."})]], modeller=[])
    await brain2.turn("make a 3d model of this", picture_problem="I couldn't open that picture. A JPG or a PNG works.")
    assert "could not be" in brain2.llm.prompts[0] and "couldn't open that picture" in brain2.llm.prompts[0]


async def test_a_build_that_fails_says_so_and_frees_the_picture_for_another_try(settings):
    brain, rec = photo_brain(settings, chat=[], modeller=[[("describe_picture", MUG_SEEN)], "no tool call", "still none"])
    brain._ctx = brain.screen.ctx
    assert await photomodel.handle(brain, {"source": "screen", "element_id": 3}) is None
    await settle(brain, rec, kind="say")
    assert any("wouldn't come together" in e["text"] for e in rec.of("say"))
    assert photomodel.state(brain).building == "" and not [e for e in rec.of("object") if e["restart"]]


async def test_the_days_budget_is_respected(settings):
    brain, _ = photo_brain(settings, chat=[], modeller=[])
    brain._ctx = brain.screen.ctx
    brain.turns.add_spend("model_3d", brain.settings.budget.daily_cost_usd + 1)
    assert "budget" in await photomodel.handle(brain, {"source": "screen", "element_id": 3})


def test_the_builder_thinks_one_notch_harder_for_a_photo(settings, monkeypatch):
    from pyxi.stream_llm import StreamingChat

    settings.model_3d = "anthropic/claude-sonnet-5.5"
    brain, _ = make_brain(settings)
    assert isinstance(brain._builder_llm(), StreamingChat)
    harder = photomodel.build_llm(brain)
    assert harder is not brain._builder_llm() and harder.model == brain._builder_llm().model
    assert harder.reasoning == {"effort": "medium"} and brain._builder_llm().reasoning == {"effort": "low"}
    assert photomodel.build_llm(brain) is harder  # made once
    brain._model_llm = Script()
    assert photomodel.build_llm(brain) is brain._model_llm  # a stand-in is used as it is


def test_the_picture_sent_with_typed_words_reaches_the_turn(settings):
    """The chat box sends {"type": "text", "text": ..., "picture": {"data": ...}}."""
    from fastapi.testclient import TestClient

    from pyxi.server import create_app
    from test_brain_and_server import KEY, KEYED

    brain, _ = make_brain(settings)
    turns: list[dict] = []

    async def turn(text, **kw):
        turns.append({"text": text, **kw})

    brain.turn = turn
    app = create_app(settings, demo=True, brain=brain, key=KEY)
    data = "data:image/png;base64," + base64.b64encode(as_bytes(mug_picture())).decode()
    with TestClient(app, headers=KEYED) as client, client.websocket_connect(f"/ws?key={KEY}") as ws:
        ws.send_json({"type": "text", "text": "Make a 3D model of this picture.", "picture": {"data": data}})
        ws.send_json({"type": "text", "text": "and this one", "picture": {"data": "not a picture"}})
        ws.send_json({"type": "text", "text": "hello"})
        for _ in range(200):
            if len(turns) >= 3:
                break
            time.sleep(0.03)
    with_picture, broken, plain = turns
    assert with_picture["text"] == "Make a 3D model of this picture."
    assert with_picture["picture"].source == "attached" and with_picture["picture"].image.size == (900, 700)
    assert broken["picture"] is None and "couldn't open" in broken["picture_problem"]
    assert plain["text"] == "hello" and plain["picture"] is None and plain["picture_problem"] == ""


def test_a_thumbnail_is_a_small_jpeg_and_nothing_else():
    thumb = photo.load_picture(mug_picture()).thumbnail()
    raw = base64.b64decode(thumb.split(",", 1)[1])
    img = Image.open(io.BytesIO(raw))
    assert img.format == "JPEG" and max(img.size) <= photo.THUMB_SIDE and len(thumb) < 60_000
