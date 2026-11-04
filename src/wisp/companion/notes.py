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
    place: tuple[int, int] | None = None  # top-left in screen px: the emptiest spot near the region


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
    card = NoteCard(
        title=str(args.get("title", "")).strip()[:60] or "Notes",
        items=items,
        sketches=sketches,
        region=region,
    )
    card.place = place_card(ctx, region, len(items))
    return card


def _dpi_scale() -> float:
    try:
        import ctypes

        return ctypes.windll.user32.GetDpiForSystem() / 96.0  # type: ignore[attr-defined]
    except Exception:
        return 1.0


def _overlap(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> int:
    w = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
    h = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
    return max(0, w) * max(0, h)


def place_card(
    ctx: ScreenContext | None, region: tuple[int, int, int, int] | None, n_items: int
) -> tuple[int, int] | None:
    """Find where the card covers the least of what's on screen: beside, below or above what it
    explains, scored against every visible piece of text and control (not just the region)."""
    if ctx is None or region is None or ctx.screenshot is None:
        return None
    k = _dpi_scale()
    w, h = int(300 * k), int((96 + 52 * n_items) * k)
    x0, y0 = ctx.to_screen(0, 0)
    x1, y1 = ctx.to_screen(ctx.screenshot.width, ctx.screenshot.height)
    screen_area = (x1 - x0) * (y1 - y0)
    content = [
        e.rect
        for e in ctx.elements
        if e.name.strip() and 0 < e.rect[2] * e.rect[3] < screen_area * 0.15
    ]
    rx, ry, rw, rh = region
    gap = int(26 * k)
    step = int(36 * k)
    # Try every spot on a grid near what's being explained; the best one covers the least text
    # and controls while staying close (a long thread across the screen is hard to follow).
    candidates = [
        (x1 - w - 16, min(max(y0 + 16, ry), y1 - h - 16)),
        (rx - gap - w, ry),
        (rx + rw + gap, ry),
    ]
    for cy in range(max(y0 + 8, ry - h - 300), min(y1 - h - 8, ry + rh + 300), step):
        for cx in range(max(x0 + 8, rx - w - 500), min(x1 - w - 8, rx + rw + 500), step):
            candidates.append((cx, cy))
    best, best_score = None, None
    for cx, cy in candidates:
        if cx < x0 + 4 or cy < y0 + 4 or cx + w > x1 - 4 or cy + h > y1 - 4:
            continue
        box = (cx, cy, w, h)
        if _overlap(box, (rx - gap // 2, ry - gap // 2, rw + gap, rh + gap)) > 0:
            continue
        covered = sum(_overlap(box, r) for r in content)
        dx = max(0, rx - (cx + w), cx - (rx + rw))
        dy = max(0, ry - (cy + h), cy - (ry + rh))
        score = covered * 4 + (dx + dy) * h * 0.2  # hiding words is worse than a longer thread
        if best_score is None or score < best_score:
            best, best_score = (cx, cy), score
    return best


class Guide(BaseModel):
    goal: str
    label: str
    rect: tuple[int, int, int, int]
    step: int = 1
