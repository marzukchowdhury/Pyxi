"""On-screen drawings and the inline speech markers that trigger them."""

from __future__ import annotations

import re
import secrets
from typing import Literal

from pydantic import BaseModel, Field

from blip.screen import ScreenContext

Kind = Literal["point", "circle", "highlight", "arrow", "click"]
# [point:12] etc. draw on element 12; [note:2] reveals point 2 of the note card on that sentence.
MARKER = re.compile(r"\[(point|circle|highlight|note):(\d+)\]", re.IGNORECASE)


class Annotation(BaseModel):
    """A drawing in *physical global screen pixels*; the overlay converts to its own CSS px."""

    id: str = Field(default_factory=lambda: "ann_" + secrets.token_hex(3))
    kind: Kind
    rect: tuple[int, int, int, int] | None = None  # x, y, w, h
    at: tuple[int, int] | None = None
    to: tuple[int, int] | None = None
    label: str | None = None
    ttl_s: float = 9.0
    guide: bool = False  # a click target Blip is waiting for you to click
    # Guide steps: what to do there (click, type, press, scroll, drag, look), what to type or
    # which keys, and where this step sits ("step 2 of 5").
    action: str | None = None
    hint: str | None = None
    step: int | None = None
    total: int | None = None


def resolve_rect(
    ctx: ScreenContext | None, element_id: int | None, box: list[float] | None
) -> tuple[int, int, int, int] | None:
    if ctx is None:
        return None
    if element_id is not None and (e := ctx.element(int(element_id))):
        return e.rect
    if box and len(box) == 4:
        x0, y0 = ctx.to_screen(box[0], box[1])
        return x0, y0, int(box[2] * ctx.model_scale), int(box[3] * ctx.model_scale)
    return None


def resolve_point(
    ctx: ScreenContext | None, element_id: int | None, xy: list[float] | tuple[float, float] | None
) -> tuple[int, int] | None:
    if ctx is None:
        return None
    if element_id is not None and (e := ctx.element(int(element_id))):
        return e.center
    if xy and len(xy) == 2:
        return ctx.to_screen(xy[0], xy[1])
    return None


def from_marker(ctx: ScreenContext | None, kind: str, element_id: int) -> Annotation | None:
    if kind.lower() == "note":
        return None
    e = ctx.element(element_id) if ctx else None
    if e is None:
        return None
    label = e.name[:30] if e.name else None
    if kind.lower() == "point":
        return Annotation(kind="point", at=e.center, rect=e.rect, label=label)
    return Annotation(kind=kind.lower(), rect=e.rect, label=label)  # type: ignore[arg-type]


def split_markers(text: str) -> tuple[str, list[tuple[str, int]]]:
    """Return speakable text and the markers it contained, in order."""
    markers = [(m.group(1), int(m.group(2))) for m in MARKER.finditer(text)]
    clean = re.sub(r"\s{2,}", " ", MARKER.sub("", text)).strip()
    return clean, markers
