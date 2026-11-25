"""A photo, made into a 3D model.

The user shows Pyxi a picture of a thing (on screen, a file, or dropped into the chat box) and
gets a model of it in the same viewer as every other model: parts, materials, beats. The model
is still data (an :class:`~pyxi.scenes.objects.ObjectScene`); nothing a model writes is run.

How a picture becomes a model, in four steps, each of which can be checked on its own:

1. ``load_picture``: the picture, made safe to work with. A 20-megapixel photo is made small,
   a see-through PNG keeps its cut-out as the outline, a speck or an empty file is turned away
   with the reason in words.
2. ``read_picture``: one look by the model, into a structured description: what the thing is,
   its parts and where each is in the picture (boxes), what it is made of, which way it faces,
   what cannot be seen. Then the numbers are taken here, in code, from the pixels: the
   outline (``subject_mask``), each part's share of the whole (``measure``), each part's
   colour (``sample_color``). The model is never asked for a proportion or a hex colour.
3. ``build_from_picture``: the usual 3D builder, with the photo and those measurements.
4. ``compare``: the model is drawn from the photo's own angle (scenes/raster.py) and held
   against it: first by numbers (outline shape, overlap, colour), then, side by side, by the
   builder, which gets at most two rounds of fixes. A fix is kept only if the numbers did not
   get worse.

The method follows img2threejs (Apache-2.0), which rebuilds an object from one reference image
in stages with a render-against-reference review: read the reference before modelling, take
what can be measured from pixels, check with cheap numbers before spending a model call, and
bound the corrections. None of its code is used: there the model writes Three.js; here it
writes Pyxi's parts.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from otto.llm.base import ImagePart, LLMProvider, Message, ToolSpec
from PIL import Image, ImageDraw, ImageFilter, ImageOps
from pydantic import ValidationError

from pyxi.scenes import raster
from pyxi.scenes.builder import BuildResult, build_model
from pyxi.scenes.check import floating, tidy
from pyxi.scenes.model_examples import LIVING_EXAMPLES, PHOTO_EXAMPLES
from pyxi.scenes.objects import Material, ObjectScene, Part

MODEL_SIDE = 1024  # the longest side of the picture the model is sent
WORK_SIDE = 192  # the longest side the outline and colours are worked out on
MIN_SIDE = 40  # smaller than this there is nothing to make out
MAX_FILE_BYTES = 80 * 1024 * 1024
MAX_PIXELS = 200_000_000
THUMB_SIDE = 420  # the photo kept beside the model
PICTURE_TYPES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".heic", ".heif", ".avif"}
_BACKDROP = (236, 238, 241)  # what a see-through picture is laid on for the model to look at
_BLUR = ImageFilter.BoxBlur(1)  # a light blur, so grain and JPEG blocks are not taken for edges


class PictureError(Exception):
    """This picture can't be used. ``say`` is why, in words Pyxi can say as they are."""

    def __init__(self, say: str) -> None:
        super().__init__(say)
        self.say = say


@dataclass
class Picture:
    image: Image.Image  # RGB, longest side MODEL_SIDE at most
    cutout: np.ndarray | None = None  # the outline a see-through picture came with (bool, image-sized)
    source: str = "file"
    original: tuple[int, int] = (0, 0)
    blurry: bool = False
    notes: list[str] = field(default_factory=list)
    digest: str = ""

    def jpeg(self, side: int = MODEL_SIDE, quality: int = 82) -> bytes:
        img = self.image
        if max(img.size) > side:
            img = img.copy()
            img.thumbnail((side, side), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality)
        return buf.getvalue()

    def thumbnail(self) -> str:
        """The photo as a small picture the overlay can show beside the model (a data URL)."""
        return "data:image/jpeg;base64," + base64.b64encode(self.jpeg(THUMB_SIDE, 78)).decode()


def sharpness(image: Image.Image) -> float:
    """How much fine edge a picture has (the spread of its second differences, on a small grey
    copy). A sharp photo of anything scores in the hundreds; a smear scores under about 25."""
    grey = image.convert("L")
    grey.thumbnail((256, 256), Image.BILINEAR)
    g = np.asarray(grey, dtype=np.float32)
    if g.shape[0] < 3 or g.shape[1] < 3:
        return 0.0
    lap = g[1:-1, 1:-1] * 4 - g[:-2, 1:-1] - g[2:, 1:-1] - g[1:-1, :-2] - g[1:-1, 2:]
    return float(lap.var())


BLURRY_BELOW = 22.0


def load_picture(source: bytes | Image.Image | str | Path, *, kind: str = "file") -> Picture:
    """Open a picture and make it safe and small. Raises :class:`PictureError` with words to say."""
    digest = ""
    try:
        if isinstance(source, str | Path):
            path = Path(source)
            if path.suffix.lower() not in PICTURE_TYPES:
                raise PictureError(f"{path.name} isn't a picture I can open. A JPG or a PNG works.")
            if not path.is_file():
                raise PictureError(f"I can't find a picture at {path.name}.")
            if path.stat().st_size > MAX_FILE_BYTES:
                raise PictureError("That picture file is too big for me. Try a smaller copy of it.")
            source = path.read_bytes()
        if isinstance(source, bytes | bytearray):
            if not source:
                raise PictureError("That picture is empty.")
            digest = hashlib.sha1(source).hexdigest()[:16]
            _heic()
            img = Image.open(io.BytesIO(source))
        else:
            img = source
        if img.width * img.height > MAX_PIXELS:
            raise PictureError("That picture is too big for me. Try a smaller copy of it.")
        original = img.size
        # A big JPEG is decoded straight to a smaller size: a 20-megapixel photo opens in a blink.
        if getattr(img, "format", "") == "JPEG":
            img.draft("RGB", (MODEL_SIDE * 2, MODEL_SIDE * 2))
        if getattr(img, "is_animated", False):
            img.seek(0)
        img = ImageOps.exif_transpose(img)
    except PictureError:
        raise
    except Exception as exc:  # not a picture, or cut short, or a kind Pillow doesn't know
        raise PictureError("I couldn't open that picture. A JPG or a PNG works.") from exc
    if min(img.size) < MIN_SIDE:
        raise PictureError("That picture is too small for me to make anything out. Show me a bigger one.")
    notes: list[str] = []
    cutout = None
    if img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info):
        img = img.convert("RGBA")
        if max(img.size) > MODEL_SIDE:
            img.thumbnail((MODEL_SIDE, MODEL_SIDE), Image.LANCZOS)
        alpha = np.asarray(img.getchannel("A")) > 96
        share = float(alpha.mean())
        if share < 0.004:
            raise PictureError("That picture is see-through all over: there's nothing in it.")
        if share < 0.97:
            cutout = alpha
            notes.append("see-through background: its own outline is used")
        base = Image.new("RGB", img.size, _BACKDROP)
        base.paste(img, mask=img.getchannel("A"))
        img = base
    else:
        img = img.convert("RGB")
        if max(img.size) > MODEL_SIDE:
            img.thumbnail((MODEL_SIDE, MODEL_SIDE), Image.LANCZOS)
    if max(original) > MODEL_SIDE:
        notes.append(f"made smaller from {original[0]}x{original[1]}")
    blurry = sharpness(img) < BLURRY_BELOW
    if blurry:
        notes.append("blurry")
    if not digest:
        digest = hashlib.sha1(img.tobytes()).hexdigest()[:16]
    return Picture(img, cutout, kind, original, blurry, notes, digest)


def _heic() -> None:
    """iPhone photos, when the reader for them is installed."""
    try:
        import pillow_heif

        pillow_heif.register_heif_opener()
    except Exception:
        pass


# ------------------------------------------------------------------ the outline, from pixels
@dataclass
class Outline:
    mask: np.ndarray  # bool, at work size: where the thing is
    box: tuple[float, float, float, float]  # x0, y0, x1, y1 as shares of the picture (0..1)
    trusted: bool  # False: the background was too busy to cut the thing out by colour
    why: str = ""

    @property
    def aspect(self) -> float:
        """Width over height of the thing's outline, in picture pixels."""
        return _aspect(self.mask, self.box)


def _aspect(mask: np.ndarray, box: tuple[float, float, float, float]) -> float:
    h, w = mask.shape
    return max(1e-6, (box[2] - box[0]) * w) / max(1e-6, (box[3] - box[1]) * h)


def _small(image: Image.Image, side: int = WORK_SIDE) -> np.ndarray:
    img = image.copy()
    img.thumbnail((side, side), Image.BILINEAR)
    return np.asarray(img, dtype=np.float32)


def _biggest(mask: np.ndarray) -> np.ndarray:
    """The largest connected piece of a mask, with any other piece at least a twentieth its
    size (a lamp's shade and base can be two pieces in a photo; a speck of noise is not a part)."""
    h, w = mask.shape
    label = np.zeros((h, w), dtype=np.int32)
    sizes: list[int] = [0]
    flat = mask.ravel()
    lab = label.ravel()
    for start in np.flatnonzero(flat):
        if lab[start]:
            continue
        n = len(sizes)
        lab[start] = n
        stack, count = [int(start)], 0
        while stack:
            i = stack.pop()
            count += 1
            y, x = divmod(i, w)
            for j in ((i - 1) if x else -1, (i + 1) if x < w - 1 else -1, (i - w) if y else -1, (i + w) if y < h - 1 else -1):
                if j >= 0 and flat[j] and not lab[j]:
                    lab[j] = n
                    stack.append(j)
        sizes.append(count)
    if len(sizes) == 1:
        return mask
    top = max(sizes)
    keep = [i for i, s in enumerate(sizes) if i and s >= 0.05 * top]
    return np.isin(label, keep)


def _bbox(mask: np.ndarray) -> tuple[float, float, float, float]:
    ys, xs = np.nonzero(mask)
    if not len(xs):
        return 0.0, 0.0, 1.0, 1.0
    h, w = mask.shape
    return float(xs.min() / w), float(ys.min() / h), float((xs.max() + 1) / w), float((ys.max() + 1) / h)


def subject_mask(picture: Picture, within: tuple[float, float, float, float] | None = None) -> Outline:
    """Cut the thing out of its background, from the pixels alone.

    A see-through picture brings its own outline. Otherwise the background is whatever the
    edge of the picture is made of, grown inwards for as long as the colour changes gently (so
    a studio backdrop that shades from light to dark, or a soft shadow, is all background) and
    stopping at a sharp step, which is the thing's edge. On a busy background that fails, and
    the answer says so (``trusted`` False) instead of pretending: nothing is then measured
    against the outline.

    ``within``: where the model said the thing is (shares of the picture); the cut-out is
    made inside that box with a little room, so other things in the picture are left out."""
    rgb = _small(picture.image)
    h, w = rgb.shape[:2]
    area = np.ones((h, w), dtype=bool)
    if within is not None:
        x0, y0, x1, y1 = within
        pad_x, pad_y = 0.06 * (x1 - x0) + 0.01, 0.06 * (y1 - y0) + 0.01
        area[:] = False
        area[
            max(0, int((y0 - pad_y) * h)) : min(h, math.ceil((y1 + pad_y) * h)),
            max(0, int((x0 - pad_x) * w)) : min(w, math.ceil((x1 + pad_x) * w)),
        ] = True
    if picture.cutout is not None:
        small = Image.fromarray(picture.cutout.astype(np.uint8) * 255).resize((w, h), Image.BILINEAR)
        mask = (np.asarray(small) > 127) & area
        if mask.any():
            mask = _biggest(mask)
            return Outline(mask, _bbox(mask), True, "its own cut-out")
    soft, far, plain = _backdrop(rgb)
    edge = np.zeros((h, w), dtype=bool)
    edge[0, :] = edge[-1, :] = edge[:, 0] = edge[:, -1] = True
    # Background starts at the picture's edge, where the edge is the backdrop's colour (a thing
    # that runs off the picture must not be taken for background at the place it leaves).
    reached = edge & (far < 46)
    if reached.sum() < 0.3 * edge.sum():
        return Outline(area, within or (0.0, 0.0, 1.0, 1.0), False, "the background is too busy to cut the thing out")
    step = 13.0 + (0.0 if plain else 8.0)  # how much colour change still counts as "gently"
    for _ in range(h + w):
        grown = reached.copy()
        for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            moved = np.roll(reached, (dy, dx), axis=(0, 1))
            if dy == 1:
                moved[0] = False
            elif dy == -1:
                moved[-1] = False
            elif dx == 1:
                moved[:, 0] = False
            else:
                moved[:, -1] = False
            near = np.abs(soft - np.roll(soft, (dy, dx), axis=(0, 1))).sum(axis=2) < step
            # A soft shadow is the backdrop getting darker by degrees: followed. A different
            # colour reached by degrees (a blurred edge) is not.
            grown |= moved & near & (far < 170)
        if (grown == reached).all():
            break
        reached = grown
    mask = area & ~reached
    if mask.any():
        mask = _biggest(mask)
        # A hole right through the thing (inside a handle, between chair legs) shows the
        # backdrop but cannot be reached from the edge. It is taken out when it is plainly
        # backdrop and not most of the thing (a white mug on white stays whole).
        holes = mask & (far < 22)
        if 0 < holes.sum() < 0.3 * mask.sum():
            mask = mask & ~holes
    share = float(mask.sum()) / max(1, int(area.sum()))
    box = _bbox(mask)
    touching = float(mask[edge].mean())
    if share < 0.03:
        return Outline(mask, box if mask.any() else (0.0, 0.0, 1.0, 1.0), False, "the thing and its background are too alike to tell apart")
    if share > 0.85 or touching > 0.3:
        return Outline(mask, box, False, "the thing fills the picture or runs off it, so its outline can't be cut out")
    return Outline(mask, box, True, "cut out from a plain background")


def _backdrop(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray, bool]:
    """What the picture's background is, judged from its edge: the picture lightly blurred (so
    grain and JPEG blocks are not taken for edges), how far every pixel's colour is from the
    nearest of the edge's main colours, and whether the edge is plain (one or two colours)."""
    soft = np.asarray(Image.fromarray(rgb.astype(np.uint8)).filter(_BLUR), dtype=np.float32)
    ring = np.concatenate([soft[0, :], soft[-1, :], soft[:, 0], soft[:, -1], soft[1, :], soft[-2, :], soft[:, 1], soft[:, -2]])
    bins = (ring // 24).astype(np.int32)
    keys = bins[:, 0] * 144 + bins[:, 1] * 12 + bins[:, 2]
    values, counts = np.unique(keys, return_counts=True)
    order = np.argsort(-counts)
    colours, covered = [], 0
    for i in order[:4]:
        colours.append(ring[keys == values[i]].mean(axis=0))
        covered += counts[i]
        if covered >= 0.8 * len(ring):
            break
    far = np.min([np.abs(soft - c).sum(axis=2) for c in colours], axis=0)
    return soft, far, covered >= 0.8 * len(ring) and len(colours) <= 3


# ------------------------------------------------------------------ colours, from pixels
def to_hex(rgb: tuple[float, float, float] | np.ndarray) -> str:
    r, g, b = (int(max(0, min(255, round(float(v))))) for v in rgb)
    return f"#{r:02x}{g:02x}{b:02x}"


def dominant_color(pixels: np.ndarray) -> tuple[float, float, float] | None:
    """The colour most of these pixels are (n, 3). The brightest and darkest tenths go first
    (a glint and a shadow are light, not paint), then the fullest of a coarse set of colour
    bins is taken (a strong colour counting for a little more than a grey), and its own
    pixels averaged: two-coloured things give the bigger colour, not a mix that is on neither."""
    if len(pixels) < 4:
        return None
    px = pixels.astype(np.float32)
    lum = px @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    lo, hi = np.percentile(lum, [10, 90])
    mid = px[(lum >= lo) & (lum <= hi)]
    if len(mid) < 4:
        mid = px
    bins = (mid // 32).astype(np.int32)
    keys = bins[:, 0] * 64 + bins[:, 1] * 8 + bins[:, 2]
    # A strong colour counts for more than a grey of the same area: on a glossy thing the
    # greys are the room reflected in it, and the colour is the thing.
    strength = 0.8 + 0.4 * (mid.max(axis=1) - mid.min(axis=1)) / 255.0
    values, inverse = np.unique(keys, return_inverse=True)
    counts = np.bincount(inverse, weights=strength)
    top = values[counts.argmax()]
    centre = np.array([top // 64, (top // 8) % 8, top % 8], dtype=np.float32) * 32 + 16
    close = mid[np.abs(mid - centre).sum(axis=1) < 72]
    chosen = close if len(close) >= 4 else mid
    # Of that colour, the better-lit half: a photo shows paint times light, and the model is
    # lit again when it is drawn, so the average would come out a shade too dark.
    light = chosen @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    lit = chosen[light >= np.percentile(light, 45)]
    r, g, b = (lit if len(lit) >= 4 else chosen).mean(axis=0)
    return float(r), float(g), float(b)


def palette(
    picture: Picture, outline: Outline, most: int = 5, box: tuple[float, float, float, float] | None = None
) -> list[tuple[str, float]]:
    """The thing's main colours, each with its share of the outline, biggest first. Without a
    trustworthy outline: whatever inside ``box`` is not the colour of the picture's edge."""
    rgb = _small(picture.image)
    if outline.trusted and outline.mask.any():
        px = rgb[outline.mask]
    else:
        h, w = rgb.shape[:2]
        x0, y0, x1, y1 = box or (0.0, 0.0, 1.0, 1.0)
        inside = np.zeros((h, w), dtype=bool)
        inside[int(y0 * h) : math.ceil(y1 * h), int(x0 * w) : math.ceil(x1 * w)] = True
        unlike = inside & (_backdrop(rgb)[1] > 60)
        px = rgb[unlike] if unlike.sum() >= 0.1 * inside.sum() else rgb[inside]
    if len(px) < 8:
        return []
    bins = (px // 48).astype(np.int32)
    keys = bins[:, 0] * 36 + bins[:, 1] * 6 + bins[:, 2]
    values, counts = np.unique(keys, return_counts=True)
    out: list[tuple[str, float]] = []
    for i in np.argsort(-counts)[:most]:
        share = counts[i] / len(px)
        if share < 0.04:
            break
        out.append((to_hex(px[keys == values[i]].mean(axis=0)), round(float(share), 2)))
    return out


# ------------------------------------------------------------------ the description
SHAPES = ("lathe", "loft", "extrude", "box", "rounded_box", "cylinder", "cone", "sphere", "capsule", "tube", "torus", "limb", "blob", "wheel")
FACINGS = {
    # Which way the thing's front points in the picture -> how far round the camera stands from
    # straight in front of it (degrees; + is towards the thing's own +x side).
    "toward_viewer": 0.0,
    "toward_left": 40.0,
    "left": 90.0,
    "away_left": 140.0,
    "away": 180.0,
    "away_right": -140.0,
    "right": -90.0,
    "toward_right": -40.0,
}
HEIGHTS = {"below": -15.0, "level": 5.0, "above": 24.0, "high": 50.0, "top": 82.0}
_MATERIALS: tuple[str, ...] = Material.__args__  # type: ignore[attr-defined]

READ_SYSTEM = """\
You look at one picture for Pyxi, which will build a 3D model of the thing in it. You do not \
build anything: you describe what is there with one describe_picture call, exactly and briefly. \
Another step measures sizes and colours from the pixels, so give boxes, never sizes or colours.

- `kind`: what the picture is mainly of. "object" (a thing: a mug, a car, a plant, a building, \
an animal), "person" (a person or a face is the subject), "text" (a document, a screenshot of \
words, a chart: no physical thing to model), "scene" (a wide view with no one main thing), \
"nothing" (blank, or too unclear to tell).
- People: Pyxi never models a real person or a face. If a person is the subject, say kind \
"person", do not describe them, and list in `objects` only the things in the picture that \
could be modelled instead (the chair they sit on, the bike, the guitar). If the user asked \
for one of those things, describe that thing as kind "object" and leave the person out of \
`parts` entirely.
- `objects`: the separate things in the picture, the main one first (most central and \
largest), 6 at most, two or three words each. `subject` is the one you describe: the one the \
user asked for if they named one, else the main one. Always ONE thing: of a pair of shoes \
or a row of chairs, describe the nearest one alone (and `box` is that one's).
- `box`: where the subject is, [left, top, right, bottom], each 0-1000 across the picture, \
tight around it (its shadow and its reflection are not part of it).
- `facing`: toward_viewer for almost everything: a mug, a vase, a lamp, a plant, a building, \
a chair or an appliance seen from its front, and anything lying flat and seen from above (a \
game controller, a phone, a keyboard, a book), wherever its handle, spout or cable points. \
The builder then lays its parts out left and right as the picture shows them. Give another \
value ONLY for a thing that is long from front to back (a car, a shoe, a boat, a bicycle, a \
four-legged animal, a sofa seen from its end) photographed from the side or at three \
quarters: then say where its FRONT (the bonnet, the toe, the head) points in the picture: \
toward_left (part way to the picture's left, and towards the camera), left (a clean side \
view, front to the left), away_left, away, away_right, right, toward_right.
- `elevation`: how far above level the camera is, in degrees: 0 level (no top surface \
shows), 90 straight down, negative from below. Judge it from a round or square top: a \
circle seen as an ellipse a third as tall as it is wide is 20 degrees, half as tall 30, \
two thirds 42. Most product photos are 10 to 30.
- `parts`: the 4-12 pieces the thing is really made of, biggest first, each with an `id` \
(short, snake_case), a `name`, its `box` in the picture (same 0-1000 scale, tight), the \
`shape` that would make it (lathe: round in cross-section, a mug body, a bottle, a lamp base, \
a pot; loft: a body whose section changes along it, a car body, a shoe, a computer mouse; \
extrude: a flat outline with thickness, a seat panel, a wing, a leaf, a wall; tube: a bent \
pipe or wire or a handle; limb: a stem, branch, leg or tail that swells and tapers; blob: a \
soft rounded mass; wheel; box, rounded_box, cylinder, cone, sphere, capsule, torus), its \
`material`, `count` (how many of this piece the real thing has, counting those out of \
sight: a chair has 4 legs even when 3 show) and `form`: a few words on its shape that a \
modeller needs ("tapers to the rim", "C-shaped loop on the right", "splayed outwards"). \
A piece that is hidden from this angle but must exist gets `hidden` true and your best box \
for where it would be.
- `depth`: how deep the thing is front to back, as a share of its width in real life (a mug \
1.0, a sofa 0.45, a wall clock 0.1). This is the one number the picture cannot give.
- `hidden`: one line on what cannot be seen from here and is assumed (usually: the far side \
mirrors this one).
- `details`: up to 5 small things that make it THIS one and not any other (a logo's place \
and colour, three stripes, a red sole, a gold rim), each a few words.
- `quality`: "clear", or what is wrong with the picture (blurry, tiny, cut off, dark).
"""


def describe_tool() -> ToolSpec:
    box = {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4}
    return ToolSpec(
        "describe_picture",
        "Describe the thing in the picture for the 3D builder.",
        {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["object", "person", "text", "scene", "nothing"]},
                "objects": {"type": "array", "items": {"type": "string"}},
                "subject": {"type": "string", "description": "The thing described, e.g. 'white ceramic mug'."},
                "living": {"type": "boolean", "description": "Grown, not made: an animal, a plant, a fruit."},
                "box": box,
                "facing": {"type": "string", "enum": list(FACINGS)},
                "elevation": {"type": "number"},
                "depth": {"type": "number"},
                "parts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "name": {"type": "string"},
                            "box": box,
                            "shape": {"type": "string", "enum": list(SHAPES)},
                            "material": {"type": "string", "enum": list(_MATERIALS)},
                            "count": {"type": "integer"},
                            "form": {"type": "string"},
                            "hidden": {"type": "boolean"},
                        },
                        "required": ["id", "name", "box", "shape", "material"],
                    },
                },
                "hidden": {"type": "string"},
                "details": {"type": "array", "items": {"type": "string"}},
                "quality": {"type": "string"},
            },
            "required": ["kind", "objects", "subject"],
        },
    )


@dataclass
class SeenPart:
    id: str
    name: str
    box: tuple[float, float, float, float]  # shares of the picture
    shape: str = "box"
    material: str = "plastic"
    count: int = 1
    form: str = ""
    hidden: bool = False
    color: str | None = None  # from the pixels (set by measure)
    # From the pixels, as shares of the whole thing's outline: how wide, how tall, and where
    # its middle is from the thing's middle (+x right, +y up).
    wide: float = 0.0
    tall: float = 0.0
    at: tuple[float, float] = (0.0, 0.0)


@dataclass
class Seen:
    kind: str
    subject: str
    objects: list[str] = field(default_factory=list)
    living: bool = False
    box: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0)
    facing: str = "toward_viewer"
    elevation: float = 15.0  # degrees the camera is above level
    depth: float = 1.0
    parts: list[SeenPart] = field(default_factory=list)
    hidden: str = ""
    details: list[str] = field(default_factory=list)
    quality: str = "clear"
    # Worked out here:
    aspect: float = 1.0  # width over height of the thing in the picture
    outline: Outline | None = None
    colors: list[tuple[str, float]] = field(default_factory=list)
    cost_usd: float = 0.0
    seconds: float = 0.0

    @property
    def view(self) -> tuple[float, float]:
        """Where the photo was taken from: (degrees round from the front, degrees above level)."""
        return FACINGS.get(self.facing, 0.0), self.elevation

    @property
    def several(self) -> bool:
        return len(self.objects) > 1


def _share_box(raw: Any) -> tuple[float, float, float, float] | None:
    if not isinstance(raw, list | tuple) or len(raw) < 4:
        return None
    try:
        x0, y0, x1, y1 = (max(0.0, min(1000.0, float(v))) / 1000.0 for v in raw[:4])
    except (TypeError, ValueError):
        return None
    x0, x1 = sorted((x0, x1))
    y0, y1 = sorted((y0, y1))
    if x1 - x0 < 0.004 or y1 - y0 < 0.004:
        return None
    return x0, y0, x1, y1


def _words(raw: Any, most: int) -> str:
    return " ".join(str(raw or "").split())[:most]


def parse_seen(args: dict[str, Any]) -> Seen:
    """The model's description, cleaned: unknown words become the nearest known one, boxes are
    put in range, and anything unusable is dropped without failing the whole read."""
    kind = str(args.get("kind") or "object")
    if kind not in ("object", "person", "text", "scene", "nothing"):
        kind = "object"
    seen = Seen(kind=kind, subject=_words(args.get("subject"), 80) or "the thing in the picture")
    seen.objects = [_words(o, 40) for o in args.get("objects") or [] if _words(o, 40)][:6]
    seen.living = bool(args.get("living"))
    seen.box = _share_box(args.get("box")) or (0.0, 0.0, 1.0, 1.0)
    seen.facing = args.get("facing") if args.get("facing") in FACINGS else "toward_viewer"
    try:
        # In degrees; a word ("above") from an older or a careless answer still counts.
        raw_el = args.get("elevation", args.get("height"))
        seen.elevation = HEIGHTS[raw_el] if raw_el in HEIGHTS else max(-30.0, min(88.0, float(raw_el)))
    except (TypeError, ValueError):
        seen.elevation = 15.0
    try:
        seen.depth = max(0.03, min(8.0, float(args.get("depth") or 1.0)))
    except (TypeError, ValueError):
        seen.depth = 1.0
    seen.hidden = _words(args.get("hidden"), 240)
    seen.details = [_words(d, 80) for d in args.get("details") or [] if _words(d, 80)][:5]
    seen.quality = _words(args.get("quality"), 60) or "clear"
    ids: set[str] = set()
    for raw in (args.get("parts") or [])[:14]:
        if not isinstance(raw, dict):
            continue
        box = _share_box(raw.get("box"))
        pid = "".join(c if c.isalnum() or c == "_" else "_" for c in str(raw.get("id") or "").lower())[:24].strip("_")
        if box is None or not pid or pid in ids:
            continue
        ids.add(pid)
        material = str(raw.get("material") or "plastic")
        try:
            count = max(1, min(24, int(raw.get("count") or 1)))
        except (TypeError, ValueError):
            count = 1
        seen.parts.append(
            SeenPart(
                id=pid,
                name=_words(raw.get("name"), 40) or pid.replace("_", " "),
                box=box,
                shape=raw.get("shape") if raw.get("shape") in SHAPES else "box",
                material=material if material in _MATERIALS else "plastic",
                count=count,
                form=_words(raw.get("form"), 90),
                hidden=bool(raw.get("hidden")),
            )
        )
    return seen


def _overlap(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0 or h <= 0:
        return 0.0
    inter = w * h
    return inter / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter)


def measure(picture: Picture, seen: Seen) -> Seen:
    """Take from the pixels what the model was not asked for: the thing's outline and how wide
    it is for its height, each part's share of the whole and where it sits, and each part's
    colour. The model's boxes say which pixels; the numbers come from the picture."""
    outline = subject_mask(picture, seen.box if seen.several or seen.box != (0.0, 0.0, 1.0, 1.0) else None)
    if outline.trusted and _overlap(outline.box, seen.box) < 0.35 and seen.box != (0.0, 0.0, 1.0, 1.0):
        # What was cut out is not what the model was looking at.
        outline = Outline(outline.mask, outline.box, False, "the cut-out did not match where the thing is")
    seen.outline = outline
    whole = outline.box if outline.trusted else seen.box
    rgb = _small(picture.image)
    h, w = rgb.shape[:2]
    seen.aspect = max(1e-6, (whole[2] - whole[0]) * w) / max(1e-6, (whole[3] - whole[1]) * h)
    seen.colors = palette(picture, outline, box=seen.box)
    ww, wh = whole[2] - whole[0], whole[3] - whole[1]
    cx, cy = (whole[0] + whole[2]) / 2, (whole[1] + whole[3]) / 2
    unlike: np.ndarray | None = None  # where the picture is not its background's colour
    for part in seen.parts:
        # A part cannot stick out of the thing it is part of.
        x0, y0 = max(part.box[0], whole[0]), max(part.box[1], whole[1])
        x1, y1 = min(part.box[2], whole[2]), min(part.box[3], whole[3])
        if x1 - x0 < 0.004 or y1 - y0 < 0.004:
            x0, y0, x1, y1 = part.box
        part.wide = round((x1 - x0) / ww, 3)
        part.tall = round((y1 - y0) / wh, 3)
        part.at = (round(((x0 + x1) / 2 - cx) / ww, 3), round((cy - (y0 + y1) / 2) / wh, 3))
        if part.hidden:
            continue
        own = np.zeros((h, w), dtype=bool)
        own[int(y0 * h) : max(int(y0 * h) + 1, math.ceil(y1 * h)), int(x0 * w) : max(int(x0 * w) + 1, math.ceil(x1 * w))] = True
        if outline.trusted:
            own &= outline.mask
        else:
            # No cut-out. Whatever in the box is not the colour of the picture's edge is the
            # likelier part of the thing (a handle is a thin loop round a hole full of
            # background); failing that, the middle of the box is the surest part of it.
            if unlike is None:
                unlike = _backdrop(rgb)[1] > 60
            mx, my = 0.2 * (x1 - x0), 0.2 * (y1 - y0)
            inner = np.zeros((h, w), dtype=bool)
            inner[int((y0 + my) * h) : math.ceil((y1 - my) * h), int((x0 + mx) * w) : math.ceil((x1 - mx) * w)] = True
            if (own & unlike).sum() >= max(8, 0.12 * own.sum()):
                own &= unlike
            elif (own & inner).sum() >= 8:
                own &= inner
        # Pixels that belong to a smaller part lying inside this one are that part's, not this one's.
        for other in seen.parts:
            if other is part or other.hidden:
                continue
            ob = other.box
            if (ob[2] - ob[0]) * (ob[3] - ob[1]) < 0.6 * (x1 - x0) * (y1 - y0) and _overlap(ob, (x0, y0, x1, y1)) > 0:
                without = own.copy()
                without[int(ob[1] * h) : math.ceil(ob[3] * h), int(ob[0] * w) : math.ceil(ob[2] * w)] = False
                if without.sum() >= 12:
                    own = without
        # A sliver (a cable, a thin leg) is a few blurred pixels, half of them background:
        # no colour is better than a wrong one, and the builder still sees the photo.
        colour = dominant_color(rgb[own]) if own.sum() >= 24 else None
        part.color = to_hex(colour) if colour else None
    return seen


async def read_picture(llm: LLMProvider, picture: Picture, want: str = "") -> Seen:
    """Step 2: the model's one look, then the measurements. ``want``: the thing the user named
    ("the chair"), when the picture has several."""
    started = time.monotonic()
    ask = "Describe the thing in this picture."
    if want:
        ask += f" The user asked for: {want[:120]}."
    seen: Seen | None = None
    cost = 0.0
    for attempt in range(2):
        response = await llm.complete(
            system=READ_SYSTEM,
            messages=[Message.user(ask if not attempt else ask + " Call describe_picture now.", ImagePart(picture.jpeg(), "image/jpeg"))],
            tools=[describe_tool()],
            max_tokens=1800,
        )
        cost += response.usage.cost_usd
        call = next((c for c in response.tool_calls if c.name == "describe_picture"), None)
        if call is not None:
            seen = parse_seen(dict(call.arguments or {}))
            break
    if seen is None:
        seen = Seen(kind="nothing", subject="")
    if seen.kind == "object":
        await asyncio.to_thread(measure, picture, seen)  # pixels: off the loop, so the voice keeps going
    seen.cost_usd, seen.seconds = cost, time.monotonic() - started
    return seen


# ------------------------------------------------------------------ the brief for the builder
def _signed(v: float) -> str:
    return f"{v:+.2f}"


def brief_for(seen: Seen, show: str = "") -> str:
    """What the builder is told: the thing, the angle, and the measurements, in a table."""
    az, el = seen.view
    sure = seen.outline is not None and seen.outline.trusted  # the thing could be cut out
    lines = [
        f"{seen.subject}, built from the photo attached. Build THIS one: the same parts, the "
        "same proportions, the same colours. Not a generic one.",
        "",
        (
            "The photo's angle: taken from straight in front of it, about "
            f"{el:.0f} degrees above level. Build it upright as the picture shows it: what is on "
            "the RIGHT of the picture is at +x, what is on the left at -x, up is +y, and the "
            "side facing the camera is +z."
            if not az
            else "The photo's angle: the thing's front points to the picture's "
            f"{'left' if az > 0 else 'right'}{'' if abs(az) in (90.0,) else (', and towards the camera' if abs(az) < 90 else ', and away from the camera')}: "
            f"the camera stands {abs(az):.0f} degrees round from straight in front of it, "
            f"towards its {'+x' if az > 0 else '-x'} side, about {el:.0f} degrees above level. "
            "Build it upright and square to the axes with its FRONT facing +z (its length along "
            "z if it is long front to back, like a car or a shoe), so that seen from that angle "
            "the model looks like the photo."
        ),
        f"Measured from the picture: its outline is {seen.aspect:.2f} times as wide as it is "
        f"tall, seen from that angle. Front to back it is about {seen.depth:.2f} of its width "
        "(an estimate: the photo cannot show depth).",
    ]
    if seen.parts:
        lines += [
            "",
            "Its parts, measured from the picture. `wide` and `tall` are shares of the whole "
            "thing's width and height in the picture, `at` is the part's middle from the whole's "
            "middle (+x right, +y up, same shares), colours are sampled from the photo's pixels. "
            "These are places in the flat PICTURE, seen from that angle; turn them back into the "
            "model's x, y, z yourself"
            + (
                ": the camera looks down on it, so of two parts on its top face the one higher "
                "in the picture is further BACK (-z), not higher up:"
                if el >= 18
                else ":"
            ),
        ]
        for p in seen.parts:
            n = f" x{p.count}" if p.count > 1 else ""
            colour = f", colour {'' if sure else 'roughly '}{p.color}" if p.color else ""
            where = "hidden from this angle, assumed" if p.hidden else f"wide {p.wide:.2f}, tall {p.tall:.2f}, at ({_signed(p.at[0])}, {_signed(p.at[1])})"
            form = f". {p.form}" if p.form else ""
            lines.append(f"- {p.id}{n} ({p.name}; {p.shape}, {p.material}{colour}): {where}{form}")
    if seen.colors:
        lines += ["", "Its colours by share of its surface: " + ", ".join(f"{c} {round(s * 100)}%" for c, s in seen.colors) + "."]
    if seen.details:
        lines += ["", "What makes it this one: " + "; ".join(seen.details) + "."]
    if seen.hidden:
        lines += ["", f"Not visible from here: {seen.hidden}"]
    lines += [
        "",
        "Rules for a model from a photo:",
        "- One part (or one mirrored or `around` set) for each part listed, with the SAME id, the "
        "listed material, and "
        + ("the listed colour as its `color`" if sure else "its colour as you see it in the photo (the listed ones are rough: the background got into them)")
        + ". Add the small details the photo shows as extra parts, in the photo's colours.",
        "- Solid and whole, as it stands in the photo: no casing, no cutaway, nothing see-through "
        "that is not glass in the photo.",
        "- Keep the measured shares: a part listed as 0.30 wide is 0.30 of the model's width "
        "from that angle. Check the whole against the outline ratio before you answer.",
        "- Shapes that look like the real thing: a round body is a `lathe` with its true outline "
        "(a vessel that is open, a mug, a pot, a shade, gets `wall`); a body whose section "
        "changes is a `loft`; a flat piece is an `extrude` of its true outline (with `bevel` for "
        "soft edges); a handle, pipe or wire is a `tube` along its real curve. Never a stack of "
        "boxes for something that is curved in the photo.",
        "- If one of the examples is the same kind of thing (a shoe, a car or van, a chair or "
        "stool), start from it: keep how it is put together (which part is a loft, where the "
        "wheels and legs sit, what touches what) and change its sections, sizes and colours "
        "until it is the one in the photo, adding the parts this one has and dropping those it "
        "lacks. A sound shape with the photo's proportions beats a pile of flat panels placed "
        "where the picture has them.",
        "- Every part touches the part it belongs to, as in the examples: legs reach up into "
        "the seat, a handle's two ends sink into the body, windows and lights sit in the body's "
        "surface, wheels tuck into the body. Work out where a part's end is (its position plus "
        "or minus half its size) and put the next part there. Nothing hangs in the air.",
        "- Model the thing, not the photograph: no glints, reflections, highlights or shadows "
        "as parts, and nothing of the background or what it stands on. A dark liquid is dark "
        "glossy plastic, not glass.",
        "- It stands still: no motions unless it has wheels or a fan. 10-22 parts: the big "
        "forms right first, then the few details that make it this one.",
        "- 3 beats, a 2-4 word title and one short spoken sentence each: what it is, the "
        "detail worth noticing, and an offer (to take it apart or explain a part). The first "
        "beat's camera is iso. Labels on the 3-5 parts that matter.",
    ]
    if show:
        lines += ["", f"They also asked: {show[:300]}"]
    return "\n".join(lines)


def paint(model: ObjectScene, seen: Seen) -> ObjectScene:
    """Give every part that was seen in the photo the colour sampled there. The builder is told
    the colours, but told is not done: this is what makes "sampled from the pixels" true.
    A copy made by mirror or `around` (legs_twin, leaf_3) takes its original's colour."""
    by_id = {p.id: p for p in seen.parts if p.color and not p.hidden}
    # Only where the thing could be cut out of its background: on a busy photo a part's box
    # holds as much street as shoe, and the builder, who sees the photo, chooses better.
    if not by_id or seen.outline is None or not seen.outline.trusted:
        return model
    changed = False
    data = model.model_dump()
    for part in data["parts"]:
        if part["material"] in ("glass", "emissive") or part["shape"] in ("arrow", "marker", "asset"):
            continue
        pid = part["id"]
        match = by_id.get(pid)
        if match is None:
            stem = pid.rsplit("_", 1)[0]
            if pid.endswith("_twin") or pid.rsplit("_", 1)[-1].isdigit():
                match = by_id.get(stem)
        if match is not None and part.get("color") != match.color:
            part["color"] = match.color
            changed = True
    if not changed:
        return model
    data["notes"] = [*model.notes]
    return ObjectScene.model_validate(data)


# ------------------------------------------------------------------ compare: numbers first
GRID = 48  # both outlines are put on a grid this size to be compared


def _fit_mask(mask: np.ndarray, size: int = GRID) -> np.ndarray:
    """A mask cropped to what is in it and stretched onto a square grid, so two outlines can be
    laid over each other whatever size and shape their pictures were."""
    ys, xs = np.nonzero(mask)
    if not len(xs):
        return np.zeros((size, size), dtype=bool)
    crop = mask[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1]
    img = Image.fromarray(crop.astype(np.uint8) * 255).resize((size, size), Image.BILINEAR)
    return np.asarray(img) > 127


def _mask_aspect(mask: np.ndarray) -> float:
    ys, xs = np.nonzero(mask)
    if not len(xs):
        return 1.0
    return (xs.max() - xs.min() + 1) / (ys.max() - ys.min() + 1)


def overlap(a: np.ndarray, b: np.ndarray) -> float:
    """How much two outlines agree once each is stretched to the same square (0 to 1)."""
    fa, fb = _fit_mask(a), _fit_mask(b)
    union = (fa | fb).sum()
    return float((fa & fb).sum()) / union if union else 0.0


def _lab(rgb: np.ndarray) -> np.ndarray:
    """sRGB 0..255 to CIE Lab, so a colour difference is one number that matches the eye."""
    c = rgb.astype(np.float64) / 255.0
    c = np.where(c > 0.04045, ((c + 0.055) / 1.055) ** 2.4, c / 12.92)
    xyz = c @ np.array([[0.4124, 0.2126, 0.0193], [0.3576, 0.7152, 0.1192], [0.1805, 0.0722, 0.9505]])
    xyz = xyz / np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], axis=-1)


def _cells(rgb: np.ndarray, mask: np.ndarray, n: int = 6) -> tuple[np.ndarray, np.ndarray]:
    """The picture inside its outline, as an n x n grid of average colours, and which cells are
    mostly inside the outline."""
    ys, xs = np.nonzero(mask)
    colours = np.zeros((n, n, 3))
    full = np.zeros((n, n), dtype=bool)
    if not len(xs):
        return colours, full
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    for i in range(n):
        for j in range(n):
            ya, yb = y0 + (y1 - y0) * i // n, max(y0 + (y1 - y0) * i // n + 1, y0 + (y1 - y0) * (i + 1) // n)
            xa, xb = x0 + (x1 - x0) * j // n, max(x0 + (x1 - x0) * j // n + 1, x0 + (x1 - x0) * (j + 1) // n)
            inside = mask[ya:yb, xa:xb]
            if inside.mean() > 0.5:
                full[i, j] = True
                colours[i, j] = rgb[ya:yb, xa:xb][inside].mean(axis=0)
    return colours, full


_ZONES = (("top left", "top", "top right"), ("left", "middle", "right"), ("bottom left", "bottom", "bottom right"))


@dataclass
class Match:
    """How the model, drawn from the photo's angle, compares with the photo."""

    view: tuple[float, float]  # the angle it was drawn from (found by trying those near the read one)
    overlap: float | None  # outline agreement 0..1; None when the photo gave no trustworthy outline
    aspect_photo: float
    aspect_model: float
    color: float | None  # average colour difference inside the outline (Lab: 0 same, 10 close, 30 other)
    cells: int  # how many grid cells that colour figure rests on
    findings: list[str] = field(default_factory=list)

    @property
    def stretch(self) -> float:
        """How many times too wide (over 1) or too narrow (under 1) the model is."""
        return self.aspect_model / max(self.aspect_photo, 1e-6)

    @property
    def score(self) -> float:
        """One number, higher is closer, for "did this fix help"."""
        shape = self.overlap if self.overlap is not None else 0.7
        return shape - 0.5 * abs(math.log(self.stretch)) - (0.004 * self.color if self.color is not None else 0.0)

    @property
    def wrong(self) -> bool:
        """Plainly not the thing in the photo, by the numbers alone."""
        return abs(math.log(self.stretch)) > math.log(1.3) or (self.overlap is not None and self.overlap < 0.55)

    @property
    def close(self) -> bool:
        """Close enough by every number that a look by the model would be spent for little."""
        return (
            self.overlap is not None
            and self.overlap >= 0.86
            and abs(math.log(self.stretch)) < math.log(1.08)
            and self.color is not None
            and self.cells >= 6
            and self.color < 12
        )

    def words(self) -> str:
        shape = f"outlines agree {self.overlap:.2f}" if self.overlap is not None else "no clean outline to compare"
        colour = f", colour off by {self.color:.0f}" if self.color is not None else ""
        return f"{shape}, {self.aspect_model:.2f} wide for its height against the photo's {self.aspect_photo:.2f}{colour}"


def _candidates(view: tuple[float, float]) -> list[tuple[float, float]]:
    """The angles worth trying round the one that was read: a way round, up and down, and the
    same from the other side (left and right are easy to say the wrong way round)."""
    az, el = view
    turns = [az + da for da in (0, -20, 20, -40, 40)]
    turns += [-a for a in turns if abs(a) > 1]
    out = [(a, max(-30.0, min(85.0, el + de))) for a in turns for de in (0, -10, 10, -20, 20)]
    return list(dict.fromkeys(out))


def find_view(model: ObjectScene, seen: Seen) -> tuple[float, float]:
    """The angle from which the model's outline best matches the photo's, near the angle the
    photo was read as taken from. A photo's angle is read by eye ("a little above, turned
    left"); this settles it to within about ten degrees. Straying from the read angle costs
    a little, so a wrong model is not excused by a strange angle."""
    outline = seen.outline
    if outline is None or not outline.trusted or not outline.mask.any():
        return seen.view
    cloud = raster.points(model, detail=96)
    best, best_fit = seen.view, -1e9
    for view in _candidates(seen.view):
        mask = raster.shoot(cloud, view[0], view[1], 96).mask
        fit = overlap(outline.mask, mask) - 0.5 * abs(math.log(_mask_aspect(mask) / max(seen.aspect, 1e-6)))
        fit -= 0.0012 * min(abs(view[0] - seen.view[0]), abs(view[0] + seen.view[0])) + 0.005 * abs(view[1] - seen.view[1])
        if fit > best_fit + 1e-9:
            best, best_fit = view, fit
    return best


def compare(
    model: ObjectScene, picture: Picture, seen: Seen, size: int = 192, view: tuple[float, float] | None = None
) -> tuple[Match, raster.Shot]:
    """Draw the model from the photo's angle and measure it against the photo. Without a
    trustworthy outline from the photo only the width-to-height ratio is compared.
    ``view``: the angle to draw from (found with :func:`find_view` when not given)."""
    outline = seen.outline
    trusted = outline is not None and outline.trusted and outline.mask.any()
    view = view or find_view(model, seen)
    shot = raster.shoot(raster.points(model, detail=size), view[0], view[1], size)
    match = Match(view, overlap(outline.mask, shot.mask) if trusted else None, seen.aspect, _mask_aspect(shot.mask), None, 0)
    if abs(math.log(match.stretch)) > math.log(1.12):
        match.findings.append(
            f"From the photo's angle the model is {match.aspect_model:.2f} times as wide as it is tall; "
            f"the photo's is {match.aspect_photo:.2f}. The model is too "
            + ("wide for its height (or too short)." if match.stretch > 1 else "narrow for its height (or too tall).")
        )
    if trusted:
        fp, fm = _fit_mask(outline.mask, 3 * 8), _fit_mask(shot.mask, 3 * 8)
        for i in range(3):
            for j in range(3):
                a = fp[i * 8 : i * 8 + 8, j * 8 : j * 8 + 8].mean()
                b = fm[i * 8 : i * 8 + 8, j * 8 : j * 8 + 8].mean()
                if a - b > 0.3:
                    match.findings.append(f"{_ZONES[i][j].capitalize()} of the outline: the photo has something there that the model lacks.")
                elif b - a > 0.3:
                    match.findings.append(f"{_ZONES[i][j].capitalize()} of the outline: the model has something there that the photo does not.")
        photo_rgb = _small(picture.image)
        pc, pf = _cells(photo_rgb, outline.mask)
        mc, mf = _cells(shot.rgb.astype(np.float32), shot.mask)
        both = pf & mf
        match.cells = int(both.sum())
        if match.cells >= 3:
            match.color = float(np.linalg.norm(_lab(pc[both]) - _lab(mc[both]), axis=1).mean())
    return match, shot


def sheet(picture: Picture, shot: raster.Shot, side: int = 448) -> bytes:
    """The photo and the model's picture side by side, for one look by the model."""
    photo = picture.image.copy()
    photo.thumbnail((side, side), Image.LANCZOS)
    drawn = Image.fromarray(shot.rgb).resize((side, side), Image.LANCZOS)
    out = Image.new("RGB", (side * 2 + 24, side + 34), (255, 255, 255))
    out.paste(photo, ((side - photo.width) // 2, 28 + (side - photo.height) // 2))
    out.paste(drawn, (side + 24, 28))
    draw = ImageDraw.Draw(out)
    draw.text((8, 8), "PHOTO", fill=(20, 20, 20))
    draw.text((side + 32, 8), "MODEL (plain shading, same angle)", fill=(20, 20, 20))
    buf = io.BytesIO()
    out.save(buf, format="JPEG", quality=84)
    return buf.getvalue()


# ------------------------------------------------------------------ compare: one look, and fixes
FIX_SYSTEM = """\
You built a 3D model of the thing in a photo. You are shown the photo (left) and a plain \
picture of your model from the same angle (right: flat shading only, no textures or shine, so \
judge shape, proportion, parts and colour, not gloss). Compare them the way a modeller checks \
a blockout against the reference, in this order: the silhouette, the proportions of each part \
against the whole, whether every part of the thing is there and none is extra, where each \
part sits, the colours. Some measurements made from the pixels are given; they are facts.

Then call review_model once.
- `verdict`: "good" when someone would say at a glance it is the thing in the photo; "fix" \
when a change below would make it clearly closer.
- `problems`: what is wrong, most important first, a few words each (3 at most).
- Fix with the fewest changes that matter: `update` moves, resizes, recolours or reshapes a \
part (give its id and only the fields that change: position, size, rotation, color, material, \
taper, profile, path, radius, radii, sections, lumps, wall, bevel); `remove` takes parts \
away; `add_parts` adds parts the photo shows and the model lacks (whole parts, same format as \
the model). Positions use the model's space, y up; a child's position is relative to its parent.
- Change what the comparison shows to be wrong and nothing else. Never rebuild the whole \
model, never change ids.
"""


def review_tool() -> ToolSpec:
    from pyxi.scenes.objects import _inline, _pyxi_only

    part = _pyxi_only(_inline(Part.model_json_schema()))
    patch = {
        "type": "object",
        "properties": {k: v for k, v in part["properties"].items() if k in _PATCHABLE or k == "id"},
        "required": ["id"],
    }
    return ToolSpec(
        "review_model",
        "Say how the model compares with the photo, and fix what is wrong.",
        {
            "type": "object",
            "properties": {
                "verdict": {"type": "string", "enum": ["good", "fix"]},
                "problems": {"type": "array", "items": {"type": "string"}},
                "update": {"type": "array", "items": patch},
                "remove": {"type": "array", "items": {"type": "string"}},
                "add_parts": {"type": "array", "items": part},
            },
            "required": ["verdict"],
        },
    )


_PATCHABLE = {
    "position", "rotation", "size", "color", "material", "taper", "profile", "path", "radius",
    "radii", "sections", "lumps", "wall", "bevel", "squash", "axis", "surface",
}  # fmt: skip


def outline_of(model: ObjectScene) -> str:
    """The model as the reviewer needs it: every part's id, shape, place and size, and the
    numbers its shape is drawn from, on one line each."""
    lines = []
    for p in model.parts:
        if not raster.drawn(p):
            continue
        row: dict[str, Any] = {"id": p.id, "shape": p.shape, "position": [round(v, 2) for v in p.position], "size": [round(v, 2) for v in p.size]}
        if p.parent:
            row["parent"] = p.parent
        if any(p.rotation):
            row["rotation"] = [round(v) for v in p.rotation]
        if p.axis != "y":
            row["axis"] = p.axis
        row["color"] = p.color or raster.MATERIAL_COLORS.get(p.material)
        for key in ("profile", "path", "radii", "sections", "lumps"):
            value = getattr(p, key)
            if value:
                row[key] = [[round(x, 2) for x in v] if isinstance(v, list) else round(v, 2) for v in value]
        for key in ("radius", "wall", "taper", "bevel", "kit"):
            if getattr(p, key) is not None:
                row[key] = getattr(p, key)
        lines.append(json.dumps(row, separators=(",", ":")))
    return "\n".join(lines)


def apply_review(model: ObjectScene, args: dict[str, Any]) -> ObjectScene | None:
    """The reviewer's fixes on the model, or None when there is nothing usable in them. Only
    known fields of known parts change; the model is checked whole again afterwards."""
    data = model.model_dump()
    remove = {str(r) for r in args.get("remove") or [] if isinstance(r, str)}
    by_id = {p["id"]: p for p in data["parts"]}
    changed = bool(remove & set(by_id))
    for patch in args.get("update") or []:
        if not isinstance(patch, dict) or patch.get("id") not in by_id or patch["id"] in remove:
            continue
        for key, value in patch.items():
            if key in _PATCHABLE and value is not None:
                by_id[patch["id"]][key] = value
                changed = True
    parts = [p for p in data["parts"] if p["id"] not in remove]
    for extra in (args.get("add_parts") or [])[:12]:
        if isinstance(extra, dict) and extra.get("id") and extra["id"] not in by_id:
            parts.append(extra)
            changed = True
    if not changed or not parts:
        return None
    data["parts"] = parts
    data["motions"] = [m for m in data["motions"] if m["part"] not in remove]
    data["notes"] = []
    try:
        return ObjectScene.model_validate(data)
    except ValidationError:
        return None


@dataclass
class PhotoResult:
    model: ObjectScene | None
    seen: Seen
    match: Match | None = None
    first_match: Match | None = None  # before any fix
    rounds: int = 0  # looks by the model at the comparison
    cost_usd: float = 0.0
    seconds: float = 0.0
    timings: dict[str, float] = field(default_factory=dict)
    checks: list[str] = field(default_factory=list)
    shot: raster.Shot | None = None


MAX_ROUNDS = 2
OnParts = Callable[[ObjectScene], Awaitable[None]]


async def review(
    llm: LLMProvider, model: ObjectScene, picture: Picture, seen: Seen, result: PhotoResult
) -> ObjectScene:
    """Step 4: hold the model against the photo and fix it, two looks at most. Never makes it
    worse by the numbers: a fix that lowers the score is thrown away, and that ends it."""
    match, shot = await asyncio.to_thread(compare, model, picture, seen)
    result.first_match = result.match = match
    result.shot = shot
    result.checks.append(f"against the photo: {match.words()}")
    for _ in range(MAX_ROUNDS):
        if match.close:
            result.checks.append("close by every measure: shown without another look")
            break
        loose = sorted({pid for f in floating(model) for pid in f.ids})
        facts = [*match.findings]
        if loose:
            facts.append("Hanging in the air, apart from the rest: " + ", ".join(loose) + ".")
        ask = (
            f"The thing: {seen.subject}.\n\nMeasured from the pixels:\n"
            + ("\n".join(f"- {f}" for f in facts) if facts else "- Outline and proportions agree.")
            + f"\n\nYour model's parts:\n{outline_of(model)}"
        )
        result.rounds += 1
        try:
            response = await llm.complete(
                system=FIX_SYSTEM,
                messages=[Message.user(ask, ImagePart(sheet(picture, shot), "image/jpeg"))],
                tools=[review_tool()],
                max_tokens=3500,
            )
        except Exception as exc:  # the model as built is still worth showing
            result.checks.append(f"could not be looked at: {type(exc).__name__}")
            break
        result.cost_usd += response.usage.cost_usd
        call = next((c for c in response.tool_calls if c.name == "review_model"), None)
        args = dict(call.arguments or {}) if call else {}
        problems = [_words(p, 80) for p in args.get("problems") or []][:3]
        if args.get("verdict") != "fix":
            result.checks.append("looked at beside the photo: good" if call else "no answer from the look")
            break
        fixed = apply_review(model, args)
        if fixed is None:
            result.checks.append("asked for a fix but gave none that could be used")
            break
        # From the same angle as before: a fix is judged on the model, not on a luckier angle.
        # The numbers only veto: a fix they cannot tell from the model before it is kept (the
        # look saw a wrong colour or a swapped part, which an outline does not show), and one
        # that plainly makes the outline worse is not.
        after, after_shot = await asyncio.to_thread(compare, fixed, picture, seen, 192, match.view)
        # A model that is plainly wrong already has little to lose: there the look is trusted
        # further, since putting a floating shade back on its neck can cost a little overlap.
        if after.score < match.score - (0.15 if match.wrong else 0.06):
            result.checks.append(f"fix thrown away, it made the match worse ({after.words()}): " + "; ".join(problems))
            break
        result.checks.append("fixed: " + "; ".join(problems) + f" -> {after.words()}")
        model, match, shot = fixed, after, after_shot
        result.match, result.shot = match, shot
        # A second look only where the numbers can say whether it helped, and say it has not yet.
        if match.overlap is None or (not match.wrong and not after.findings):
            break
    return model


async def model_from_picture(
    llm: LLMProvider,
    picture: Picture,
    *,
    want: str = "",
    show: str = "",
    seen: Seen | None = None,
    on_seen: Callable[[Seen], Awaitable[bool | None]] | None = None,
    on_parts: OnParts | None = None,
    build_llm: LLMProvider | None = None,
) -> PhotoResult:
    """The whole thing: read, measure, build, compare and fix. ``seen``: a read made earlier of
    this same picture (asking twice does not pay for the look twice). ``on_seen`` is told what
    was read before anything is built, and can stop it by returning False. ``build_llm``:
    the model that does the modelling itself, when that is not ``llm`` (the same builder
    thinking harder: the look and the review stay quick)."""
    started = time.monotonic()
    if seen is None:
        seen = await read_picture(llm, picture, want)
        cost = seen.cost_usd
    else:
        cost = 0.0
    result = PhotoResult(model=None, seen=seen, cost_usd=cost)
    result.timings["read"] = time.monotonic() - started
    if on_seen is not None and await on_seen(seen) is False:
        result.seconds = time.monotonic() - started
        return result
    if seen.kind != "object":
        result.seconds = time.monotonic() - started
        return result
    t = time.monotonic()
    brief = brief_for(seen, show)
    built: BuildResult = await build_model(
        build_llm or llm,
        brief,
        on_parts=on_parts,
        images=[picture.jpeg()],
        examples=LIVING_EXAMPLES if seen.living else PHOTO_EXAMPLES,
        check=False,
    )
    result.cost_usd += built.cost_usd
    result.timings["build"] = time.monotonic() - t
    result.checks += [f"build: {e}" for e in built.errors]
    if built.model is None:
        result.seconds = time.monotonic() - started
        return result
    model, done = tidy(built.model, open_up=False)
    result.checks += done
    # A photo model is the outside of the thing, whole: never opened up or made see-through.
    if model.cutaway or any(p.casing for p in model.parts):
        data = model.model_dump()
        data.update(cutaway=False, parts=[{**p, "casing": False} for p in data["parts"]], beats=[{**b, "cutaway": None} for b in data["beats"]])
        model = ObjectScene.model_validate(data)
    model = paint(model, seen)
    t = time.monotonic()
    model = await review(llm, model, picture, seen, result)
    result.timings["compare"] = time.monotonic() - t
    view = result.match.view if result.match else seen.view
    result.model = model.model_copy(update={"photo": picture.thumbnail(), "photo_view": [round(view[0], 1), round(view[1], 1)]})
    result.seconds = time.monotonic() - started
    return result
