"""Aiming at the parts of a picture: where exactly is the right atrium in this heart diagram?

A picture has no words for OCR to find and no accessibility tree, and on the full screenshot
the chat model's pointing is off by a chamber or two. So when Wisp explains a picture, the
picture alone is cropped at full resolution, zoomed, gridded with numbered lines every 100 px,
and a vision model surveys it: every recognisable part and a point well inside it. Measured on
an unlabelled heart diagram, this lands on the right part where the full-screen guess often
doesn't. It runs while Wisp says its opener, so it costs no waiting.
"""

from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass

from otto.llm.base import ImagePart, Message, TextPart, ToolSpec
from PIL import Image, ImageDraw

log = logging.getLogger(__name__)

ZOOM_TO = 1000  # long side of the picture as the aiming model sees it

SURVEY = ToolSpec(
    "parts",
    "Report the parts you can see in the picture.",
    {
        "type": "object",
        "properties": {
            "parts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "x": {"type": "number"},
                        "y": {"type": "number"},
                    },
                    "required": ["name", "x", "y"],
                },
            }
        },
        "required": ["parts"],
    },
)


@dataclass
class Survey:
    """Located parts, in physical screen px."""

    points: dict[str, tuple[int, int]]

    def find(self, name: str) -> tuple[int, int] | None:
        return match(name, self.points)


def gridded(picture: Image.Image) -> tuple[Image.Image, float]:
    """The picture zoomed so its long side is ``ZOOM_TO`` px, with numbered grid lines every
    100 px (models read positions off a grid far better than off a bare image)."""
    scale = ZOOM_TO / max(picture.width, picture.height)
    img = picture.convert("RGB").resize(
        (max(1, round(picture.width * scale)), max(1, round(picture.height * scale))),
        Image.LANCZOS,
    )
    draw = ImageDraw.Draw(img, "RGBA")
    for x in range(100, img.width, 100):
        draw.line([(x, 0), (x, img.height)], fill=(0, 255, 255, 100))
        draw.text((x + 2, 2), str(x), fill=(255, 255, 0, 255))
    for y in range(100, img.height, 100):
        draw.line([(0, y), (img.width, y)], fill=(0, 255, 255, 100))
        draw.text((2, y + 2), str(y), fill=(255, 255, 0, 255))
    return img, scale


def _words(name: str) -> list[str]:
    stop = {"the", "of", "a", "an", "and", "to", "in", "on", "its"}
    return [w for w in re.findall(r"[a-z0-9]+", name.lower()) if w not in stop]


_SIDES = {
    "left",
    "right",
    "upper",
    "lower",
    "superior",
    "inferior",
    "anterior",
    "posterior",
    "front",
    "back",
    "top",
    "bottom",
    "inner",
    "outer",
}


def match(name: str, points: dict[str, tuple[int, int]]) -> tuple[int, int] | None:
    """The located part a name means: the same name, then one inside the other ('vena cava' in
    'superior vena cava'), then the most shared words. Names that disagree on a side ('left'
    vs 'right') never match: the left ventricle is not the right one."""
    want = _words(name)
    if not want or not points:
        return None
    best, best_score = None, 0.0
    for k, v in points.items():
        have = _words(k)
        sides_w, sides_h = set(want) & _SIDES, set(have) & _SIDES
        if not have or (sides_w and sides_h and sides_w != sides_h):
            continue
        a, b = " ".join(want), " ".join(have)
        if a == b:
            return v
        if a in b or b in a:
            score = 0.9
        else:
            # 'right atrium' is not 'right ventricle', nor 'pulmonary artery' 'pulmonary vein':
            # more than half the words must be shared, and not just the side.
            core = (set(want) & set(have)) - _SIDES
            score = len(set(want) & set(have)) / max(len(set(want)), len(set(have))) if core else 0
        if score > best_score:
            best, best_score = v, score
    return best if best_score > 0.5 else None


CONFIRM = ToolSpec(
    "found",
    "Say whether the control is in this picture, and where.",
    {
        "type": "object",
        "properties": {
            "here": {"type": "boolean"},
            "x": {"type": "number"},
            "y": {"type": "number"},
        },
        "required": ["here"],
    },
)


async def confirm_target(
    llm, crop: Image.Image, origin: tuple[int, int], label: str
) -> tuple[tuple[int, int] | None, object | None]:
    """Before clicking where the model only guessed (no element id, no words to find): zoom
    into the spot and make sure the thing is really there. Returns its exact centre on screen,
    or None when it isn't (so Wisp doesn't click a bookmark instead of a spreadsheet cell)."""
    img, scale = gridded(crop)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    prompt = (
        f"This {img.width}x{img.height} image is a zoomed part of a screen; grid lines every 100 px "
        f"are numbered in yellow. Is '{label}' here? If yes, give the exact centre of it in this "
        "image's px. If it isn't clearly here, say here=false. Call `found`."
    )
    try:
        res = await llm.complete(
            system="You check where a control is on screen, precisely and honestly.",
            messages=[Message("user", [ImagePart(buf.getvalue(), "image/jpeg"), TextPart(prompt)])],
            tools=[CONFIRM],
            max_tokens=2000,
        )
    except Exception as exc:
        log.info("confirming a click target failed: %s", exc)
        return None, None
    for call in res.tool_calls:
        a = call.arguments
        if call.name == "found" and a.get("here"):
            try:
                x, y = float(a["x"]), float(a["y"])
            except (KeyError, TypeError, ValueError):
                return None, res.usage
            if 0 <= x <= img.width and 0 <= y <= img.height:
                return (origin[0] + round(x / scale), origin[1] + round(y / scale)), res.usage
    return None, res.usage


async def survey(
    llm,
    picture: Image.Image,
    origin: tuple[int, int],
    question: str,
    names: list[str] | None = None,
) -> tuple[Survey, object | None]:
    """Locate the parts of ``picture`` (a crop of the physical screenshot whose top-left is at
    ``origin`` on screen). Returns the survey and the call's usage; never raises."""
    img, scale = gridded(picture)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    wanted = f" Be sure to include: {', '.join(names)}." if names else ""
    prompt = (
        f"This {img.width}x{img.height} image is a picture from the user's screen; grid lines every "
        f"100 px are numbered in yellow. They asked: {question!r}. List every distinct part someone "
        f"would point to when explaining it (up to 20), each with a point well inside that part, in "
        f"this image's px. Use the usual names.{wanted} Call `parts`."
    )
    try:
        res = await llm.complete(
            system="You locate things in pictures precisely.",
            messages=[Message("user", [ImagePart(buf.getvalue(), "image/jpeg"), TextPart(prompt)])],
            tools=[SURVEY],
            max_tokens=4000,
        )
    except Exception as exc:
        log.info("aiming failed: %s", exc)
        return Survey({}), None
    points: dict[str, tuple[int, int]] = {}
    for call in res.tool_calls:
        for p in call.arguments.get("parts", []) if call.name == "parts" else []:
            try:
                x, y = float(p["x"]), float(p["y"])
            except (KeyError, TypeError, ValueError):
                continue
            if 0 <= x <= img.width and 0 <= y <= img.height and str(p.get("name", "")).strip():
                points[str(p["name"]).strip()] = (
                    origin[0] + round(x / scale),
                    origin[1] + round(y / scale),
                )
    return Survey(points), res.usage
