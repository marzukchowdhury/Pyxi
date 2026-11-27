"""The companion's side of "make a 3D model of this picture" (the work is in scenes/photo.py).

Kept out of brain.py; the brain calls :func:`handle` for the ``model_from_photo`` tool. This
part finds the picture the user means (on screen, in the chat box, or a file), turns away what
can't be used with the reason in words, starts the build in the background, and says the few
things only it can know: that the picture has several things in it and which one it took,
that it does not model people, that there is no object in a page of text.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pyxi import files_access
from pyxi.companion.annotations import resolve_rect
from pyxi.scenes import photo
from pyxi.scenes.objects import ObjectScene

if TYPE_CHECKING:
    from pyxi.companion.brain import Brain
    from pyxi.screen.context import ScreenContext

log = logging.getLogger(__name__)

KEEP_S = 300.0  # how long "that picture" still means the last one asked about
_PICTURE_ROLES = ("image", "graphic", "img", "picture", "figure")
_FOLDERS = {"desktop": "Desktop", "downloads": "Downloads", "download": "Downloads", "pictures": "Pictures", "photos": "Pictures", "documents": "Documents"}


class Asked:
    """What the brain remembers between asks: the last picture (so "the chair, then" needs no
    second look at the screen), what was read in it (so asking twice does not pay for the look
    twice), and the picture being built right now (so asking twice in a row is one build)."""

    def __init__(self) -> None:
        self.picture: photo.Picture | None = None
        self.at = 0.0
        self.seen: tuple[str, str, photo.Seen] | None = None  # (picture, the thing asked for, what was read)
        self.building: str = ""  # digest + thing asked for

    def keep(self, picture: photo.Picture) -> None:
        self.picture, self.at = picture, time.monotonic()

    def recent(self) -> photo.Picture | None:
        return self.picture if self.picture is not None and time.monotonic() - self.at < KEEP_S else None


def state(brain: Brain) -> Asked:
    if getattr(brain, "_photo_asked", None) is None:
        brain._photo_asked = Asked()
    return brain._photo_asked


# ------------------------------------------------------------------ finding the picture
MAX_CHAT_BYTES = 24 * 1024 * 1024  # a pasted picture, after the app has made it smaller


async def from_chat(raw: Any) -> tuple[photo.Picture | None, str]:
    """The picture that came with a typed message: ``{"data": base64}`` for one pasted or
    dropped as pixels, ``{"path": ...}`` for a dropped file. Returns the picture, or None and
    why it can't be used (empty when there was no picture at all)."""
    if not isinstance(raw, dict):
        return None, ""
    try:
        if isinstance(raw.get("path"), str) and raw["path"].strip():
            path = Path(raw["path"].strip().strip('"')).expanduser()
            return await asyncio.to_thread(photo.load_picture, path, kind="attached"), ""
        data = raw.get("data")
        if not isinstance(data, str) or not data:
            return None, ""
        if len(data) > MAX_CHAT_BYTES * 4 // 3:
            return None, "That picture is too big for me. Try a smaller copy of it."
        import base64
        import binascii

        try:
            blob = base64.b64decode(data.split(",", 1)[-1], validate=False)
        except (binascii.Error, ValueError):
            return None, "I couldn't open that picture. A JPG or a PNG works."
        return await asyncio.to_thread(photo.load_picture, blob, kind="attached"), ""
    except photo.PictureError as exc:
        return None, exc.say
def picture_on_screen(ctx: ScreenContext) -> tuple[int, int, int, int] | None:
    """The picture someone most likely means when they say "this" at a screen: the biggest
    image the window itself says is an image, if it is a fair share of the window."""
    _, _, ww, wh = ctx.window.rect
    window = max(1, ww * wh)
    best: tuple[int, tuple[int, int, int, int]] | None = None
    for e in ctx.elements:
        role = (e.role or "").lower()
        if not any(r in role for r in _PICTURE_ROLES):
            continue
        _, _, w, h = e.rect
        if w < 120 or h < 120 or w * h < 0.04 * window:
            continue
        if best is None or w * h > best[0]:
            best = (w * h, e.rect)
    return best[1] if best else None


def from_screen(ctx: ScreenContext | None, args: dict[str, Any]) -> photo.Picture:
    if ctx is None or ctx.screenshot is None:
        raise photo.PictureError(
            "I can't see your screen right now, so there's no picture for me to work from. "
            "Drop the picture into the chat box instead."
        )
    if ctx.excluded:
        raise photo.PictureError("That window is private, so I can't look at the picture in it.")
    rect = resolve_rect(ctx, args.get("element_id"), args.get("box"))
    if rect is None:
        rect = picture_on_screen(ctx) or ctx.view_rect()
    crop = ctx.crop(rect, pad=6) if rect else None
    if crop is None:
        raise photo.PictureError("I can't find a picture on your screen. Point at it, or drop it into the chat box.")
    return photo.load_picture(crop.copy(), kind="screen")


async def from_file(brain: Brain, args: dict[str, Any]) -> photo.Picture | str:
    """The picture file they named, or a note for the model saying why there is none."""
    given = str(args.get("path") or "").strip().strip('"')
    if given:
        path = Path(given).expanduser()
        if path.is_file():
            return await asyncio.to_thread(photo.load_picture, path)
        if not args.get("name"):
            args = {**args, "name": path.stem}
    name = str(args.get("name") or "").strip().strip('"')
    if not name:
        return "model_from_photo: which picture file? Ask them for its name or to drop it into the chat box."
    from pyxi.companion import filework

    access = await filework.consent(brain)
    if isinstance(access, str):
        return f"model_from_photo: {access}"
    folder = _FOLDERS.get(str(args.get("folder") or "").strip().lower())
    hit = await asyncio.to_thread(find_picture, name, access, folder)
    if hit is None:
        where = f" in {folder}" if folder else ""
        return f"model_from_photo: there is no picture called {name!r}{where}. Tell them, and ask where it is."
    return await asyncio.to_thread(photo.load_picture, hit)


def find_picture(name: str, access: files_access.FileAccess, folder: str | None = None) -> Path | None:
    """A picture file called ``name`` in the folders they allow: the exact name first, then one
    that starts with it, then one that has it in it; the newest of equals."""
    want = Path(name).stem.lower() if Path(name).suffix.lower() in photo.PICTURE_TYPES else name.lower()
    folders = [f for f in access.folders if not folder or Path(f).name.lower() == folder.lower()] or access.folders
    hits = files_access.search_walk(want, folders, 40, kinds=photo.PICTURE_TYPES)
    best: tuple[int, float, Path] | None = None
    for h in hits:
        p = Path(h.path)
        if not files_access.allowed(p, access):
            continue
        stem = p.stem.lower()
        rank = 3 if stem == want else 2 if stem.startswith(want) else 1 if want in stem else 0
        if rank and (best is None or (rank, h.modified) > best[:2]):
            best = (rank, h.modified, p)
    return best[2] if best else None


# ------------------------------------------------------------------ what Pyxi says
def _either(things: list[str]) -> str:
    things = [t for t in things if t][:3]
    if not things:
        return ""
    if len(things) == 1:
        return f"the {things[0]}"
    return ", ".join(f"the {t}" for t in things[:-1]) + f" or the {things[-1]}"


def not_an_object(seen: photo.Seen) -> str:
    """Why nothing is built from this picture, and what would work, in one or two sentences."""
    choice = _either(seen.objects)
    if seen.kind == "person":
        if choice:
            return f"I don't make models of people, but I can do a thing in the picture: {choice}. Which one?"
        return "I don't make models of people. Show me a picture of a thing and I'll model that."
    if seen.kind == "text":
        return "That picture is words, with no object in it for me to model. Show me a photo of a thing."
    if seen.kind == "scene" and choice:
        return f"That's a wide view with no one thing in it. Which should I model: {choice}?"
    return "I can't make out a thing to model in that picture. Try a clearer one, with the thing filling the frame."


def heads_up(seen: photo.Seen, picture: photo.Picture, named: bool) -> str:
    """What is worth saying before the model appears: that it chose one of several things, or
    that a blurry picture will give a rough model. Empty when there is nothing to say."""
    lines = []
    if seen.several and not named:
        lines.append(f"There's more than one thing in that picture, so I'm building the {seen.subject}. Tell me if you meant another.")
    if picture.blurry or "blur" in seen.quality.lower():
        lines.append("The picture is blurry, so this one will be rough.")
    return " ".join(lines)


# ------------------------------------------------------------------ the builder, thinking harder
_HARDER = {"low": "medium", "medium": "high"}


def build_llm(brain: Brain) -> Any:
    """The 3D builder, one notch more careful than for a model built from words: turning a
    photo's flat measurements back into a solid thing is the hard part, and at the usual
    effort the parts came out stacked beside each other. The look at the picture and the
    comparison stay at the usual effort, which keeps the whole thing near half a minute."""
    from pyxi.stream_llm import StreamingChat

    base = brain._builder_llm()
    if not isinstance(base, StreamingChat):
        return base  # some other provider (or a test's stand-in): as it is
    if getattr(brain, "_photo_llm", None) is None:
        effort = brain.settings.effort_3d
        brain._photo_llm = StreamingChat(base.model, reasoning={"effort": _HARDER.get(effort, effort)})
    return brain._photo_llm


# ------------------------------------------------------------------ the tool
async def handle(brain: Brain, args: dict[str, Any]) -> str | None:
    """``model_from_photo``: find the picture, then build in the background. A string is a
    note back to the model (nothing was started); None means the build is on its way."""
    if brain.turns.spent_today() >= brain.settings.budget.daily_cost_usd:
        return "model_from_photo: today's budget is used up. Tell them, and that they can raise the daily limit in Settings."
    asked = state(brain)
    source = str(args.get("source") or "screen")
    want = " ".join(str(args.get("object") or "").split())[:120]
    pointed = args.get("element_id") is not None or bool(args.get("box"))
    try:
        attached = getattr(brain, "_attached", None)
        named = source == "file" or bool(args.get("path") or args.get("name"))
        if attached is not None and (source == "attached" or not (pointed or named)):
            # A picture put in the chat box with these words is the one they mean, even if the
            # model said "screen" without pointing at anything there.
            picture = attached
        elif named:
            got = await from_file(brain, args)
            if isinstance(got, str):
                return got
            picture = got
        elif source == "attached" and not pointed and (again := asked.recent()) is not None:
            picture = again  # "the chair, then": the picture they were just asked about
        elif source == "attached":
            return (
                "model_from_photo: there is no picture in the chat box. If the picture is on "
                "their screen call it again with source screen; otherwise ask them to drop it in."
            )
        else:
            picture = await asyncio.to_thread(from_screen, brain._ctx, args)
    except photo.PictureError as exc:
        return f"model_from_photo: nothing was built. Tell them this, in your own short words: {exc.say}"
    key = f"{picture.digest}:{want.lower()}"
    if asked.building == key:
        return "model_from_photo: that same picture is already being built. Tell them it is on its way; do not start another."
    asked.keep(picture)
    asked.building = key
    brain._visual_turn = True
    brain._spawn(_run(brain, picture, want, " ".join(str(args.get("show") or "").split())[:300], key))
    return None


async def _run(brain: Brain, picture: photo.Picture, want: str, show: str, key: str) -> None:
    asked = state(brain)
    job = await brain.jobs.track("Pyxi", "Making a 3D model from the picture", role="model")
    await brain.hub.emit("job_event", job_id=job.id, agent="Pyxi", event="status", data={"message": "is looking at the picture"})
    shown_partial = False
    scene_id = ""

    async def assembling(partial: ObjectScene) -> None:
        nonlocal shown_partial, scene_id
        if brain._object is not None and brain._object.id != partial.id and shown_partial:
            return  # something else took the stage meanwhile
        scene_id = partial.id
        await brain.show_object(partial, restart=False, background=True, keep_place=shown_partial)
        shown_partial = True

    async def read(seen: photo.Seen) -> bool:
        asked.seen = (picture.digest, want.lower(), seen)
        if seen.kind != "object":
            await brain.say_line(not_an_object(seen))
            return False
        await brain.hub.emit("job_event", job_id=job.id, agent="Pyxi", event="status", data={"message": f"is building the {seen.subject} in 3D"})
        if line := heads_up(seen, picture, bool(want)):
            brain._spawn(brain.say_line(line))
        return True

    earlier = asked.seen[2] if asked.seen and asked.seen[:2] == (picture.digest, want.lower()) else None
    try:
        result = await photo.model_from_picture(
            brain._builder_llm(), picture, want=want, show=show, seen=earlier, on_seen=read, on_parts=assembling, build_llm=build_llm(brain)
        )
    except Exception as exc:
        log.exception("photo to 3D failed")
        from pyxi import plainwords

        # A cause Pyxi knows (the AI model busy or out of reach, the allowance used up) is
        # said as that: "want me to try again?" was offered for failures a retry can't mend.
        known = plainwords.explain(exc)
        why = f"I couldn't build that model. {known.line}" if known else ""
        await brain.jobs.settle(job, ok=False, summary=why or "The model wouldn't come together.")
        await brain.say_line(
            why or "Hmm, that model wouldn't come together. Want me to try again?", record=False
        )
        return
    finally:
        if asked.building == key:
            asked.building = ""
    brain.turns.add_spend("model_3d", result.cost_usd)
    seen = result.seen
    log.warning(
        "photo to 3D %r: %.1fs, $%.3f, %s; %s",
        seen.subject,
        result.seconds,
        result.cost_usd,
        ", ".join(f"{k} {v:.1f}s" for k, v in result.timings.items()),
        "; ".join(result.checks)[:500],
    )
    if seen.kind != "object":
        await brain.jobs.settle(job, ok=False, summary="No object to model in that picture.", cost=result.cost_usd)
        return
    await brain.jobs.settle(
        job,
        ok=result.model is not None,
        # (A failed one used to read just "chair" in the tray.)
        summary=seen.subject if result.model is not None else f"The model of {seen.subject or 'that'} wouldn't come together.",
        cost=result.cost_usd,
    )
    if result.model is None:
        await brain.say_line("Hmm, that model wouldn't come together. Want me to try again?", record=False)
        return
    if shown_partial and brain._object is not None and brain._object.id != result.model.id:
        return  # they moved on to something else while it was being checked
    # Pyxi's own line first: the model's narration must never talk over it.
    if brain._speech:
        await asyncio.gather(brain._speech, return_exceptions=True)
    wait = brain._talking_until - time.monotonic()
    if wait > 0:
        await asyncio.sleep(min(wait, 12.0))
    await brain.show_object(result.model, restart=True, background=True)
