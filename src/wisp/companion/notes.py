"""Field notes: explanations written onto the screen, next to the thing being explained.

A note card sits beside the content. Its points write themselves in ink as Wisp says them (the
``[note:N]`` speech marker reveals point N on that sentence), each point can frame the exact
paragraph, row or control it is about with a hand-drawn dashed outline numbered to match, and a
thread of light joins the two. Sketches add freehand marks on top of real content: the rise and
run on a chart, a tick next to the right answer, an arrow from cause to effect.

Guides are the interactive version: Wisp marks the one control to click next, waits for your
real click on it, looks at the new screen and gives the next step.
"""

from __future__ import annotations

import secrets
from typing import Any, Literal

from pydantic import BaseModel, Field

from wisp.companion.annotations import resolve_point, resolve_rect
from wisp.screen import ScreenContext

ItemKind = Literal["point", "step", "formula", "aside"]
Mark = Literal["frame", "underline", "circle", "none"]


class NoteItem(BaseModel):
    n: int
    text: str
    kind: ItemKind = "point"
    mark: Mark = "frame"
    rect: tuple[int, int, int, int] | None = None  # physical screen px of what it's about


class Sketch(BaseModel):
    kind: Literal["path", "arrow", "label", "tick", "cross"] = "path"
    points: list[tuple[int, int]] = Field(default_factory=list)  # physical screen px
    text: str | None = None
    after: int = 0  # appears with note item N (0 = straight away)


class NoteCard(BaseModel):
    id: str = Field(default_factory=lambda: "notes_" + secrets.token_hex(3))
    title: str
    items: list[NoteItem]
    sketches: list[Sketch] = Field(default_factory=list)
    region: tuple[int, int, int, int] | None = (
        None  # union of the targets, to place the card beside
    )


def _union(rects: list[tuple[int, int, int, int]]) -> tuple[int, int, int, int] | None:
    if not rects:
        return None
    x0 = min(r[0] for r in rects)
    y0 = min(r[1] for r in rects)
    x1 = max(r[0] + r[2] for r in rects)
    y1 = max(r[1] + r[3] for r in rects)
    return x0, y0, x1 - x0, y1 - y0


def build_card(ctx: ScreenContext | None, args: dict[str, Any]) -> NoteCard:
    """Turn the model's tool call (element ids, screenshot px) into screen-exact geometry."""
    items: list[NoteItem] = []
    for i, raw in enumerate(args.get("items", [])[:7], start=1):
        if not isinstance(raw, dict) or not str(raw.get("text", "")).strip():
            continue
        rect = resolve_rect(ctx, raw.get("element_id"), raw.get("box"))
        kind = (
            raw.get("kind") if raw.get("kind") in ("point", "step", "formula", "aside") else "point"
        )
        mark = (
            raw.get("mark")
            if raw.get("mark") in ("frame", "underline", "circle", "none")
            else "frame"
        )
        items.append(
            NoteItem(
                n=i,
                text=str(raw["text"]).strip()[:120],
                kind=kind,
                mark=mark if rect else "none",
                rect=rect,
            )
        )
    sketches: list[Sketch] = []
    for raw in args.get("sketches", [])[:8]:
        if not isinstance(raw, dict):
            continue
        pts = [p for p in (resolve_point(ctx, None, xy) for xy in raw.get("points", [])[:40]) if p]
        if raw.get("element_id") is not None and (c := resolve_point(ctx, raw["element_id"], None)):
            pts = pts or [c]
        kind = raw.get("kind", "path")
        if kind not in ("path", "arrow", "label", "tick", "cross") or not pts:
            continue
        sketches.append(
            Sketch(
                kind=kind,
                points=pts,
                text=(raw.get("text") or None),
                after=int(raw.get("after", 0) or 0),
            )
        )
    region = _union(
        [it.rect for it in items if it.rect] + [(x, y, 1, 1) for s in sketches for x, y in s.points]
    )
    return NoteCard(
        title=str(args.get("title", "")).strip()[:60] or "Notes",
        items=items,
        sketches=sketches,
        region=region,
    )


class Guide(BaseModel):
    goal: str
    label: str
    rect: tuple[int, int, int, int]
    step: int = 1
