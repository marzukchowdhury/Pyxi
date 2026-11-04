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
from wisp.companion.layout import Occupancy
from wisp.screen import ScreenContext

ItemKind = Literal["point", "step", "formula", "aside"]
Mark = Literal["frame", "underline", "circle", "none"]


class NoteItem(BaseModel):
    n: int
    text: str
    kind: ItemKind = "point"
    mark: Mark = "frame"
    rect: tuple[int, int, int, int] | None = None  # physical screen px of what it's about
    say: str | None = None  # the sentence Wisp speaks as this point appears


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
    closing: str | None = None  # said after the last point: a related fact and an offer


def _union(rects: list[tuple[int, int, int, int]]) -> tuple[int, int, int, int] | None:
    if not rects:
        return None
    x0 = min(r[0] for r in rects)
    y0 = min(r[1] for r in rects)
    x1 = max(r[0] + r[2] for r in rects)
    y1 = max(r[1] + r[3] for r in rects)
    return x0, y0, x1 - x0, y1 - y0


def target_rect(ctx: ScreenContext | None, raw: dict[str, Any]) -> tuple[int, int, int, int] | None:
    """What a point is about. Quoted words win: OCR finds those exact words on screen (inside
    the element, if one was given), so an underline sits under the words themselves."""
    if ctx is None:
        return None
    elem = resolve_rect(ctx, raw.get("element_id"), None)
    quote = str(raw.get("quote") or "").strip()
    if quote and (hit := ctx.find_text(quote, elem)):
        return hit
    return elem or resolve_rect(ctx, None, raw.get("box"))


def build_card(
    ctx: ScreenContext | None,
    args: dict[str, Any],
    avoid: list[tuple[int, int, int, int]] | None = None,
) -> NoteCard:
    """Turn the model's tool call (quotes, element ids, screenshot px) into screen-exact geometry."""
    items: list[NoteItem] = []
    for i, raw in enumerate(args.get("items", [])[:7], start=1):
        if not isinstance(raw, dict) or not str(raw.get("text", "")).strip():
            continue
        rect = target_rect(ctx, raw)
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
                say=(str(raw.get("say") or "").strip()[:240] or None),
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
        kind = raw.get("kind", "path")
        wants_target = raw.get("quote") or raw.get("element_id") is not None
        if not pts and wants_target and (r := target_rect(ctx, raw)):
            x, y, w, h = r
            # Ticks and crosses go just after the words; labels and arrows at their middle.
            after = kind in ("tick", "cross")
            pts = [(x + w + int(h * 0.9), y + h // 2)] if after else [(x + w // 2, y + h // 2)]
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
        closing=(str(args.get("closing") or "").strip()[:300] or None),
        items=items,
        sketches=sketches,
        region=region,
    )
    card.place = place_card(ctx, region, [it.text for it in items], card.title, avoid)
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


def card_size(texts: list[str], title: str = "") -> tuple[int, int]:
    """The note card's size in screen px (handwriting fits ~24 characters a line, titles ~18)."""
    k = _dpi_scale()
    lines = sum(max(1, -(-len(t) // 24)) for t in texts)
    title_lines = max(1, -(-len(title) // 18))
    return int(300 * k), int((58 + 34 * title_lines + 27 * lines + 9 * len(texts)) * k)


def place_card(
    ctx: ScreenContext | None,
    region: tuple[int, int, int, int] | None,
    texts: list[str],
    title: str = "",
    avoid: list[tuple[int, int, int, int]] | None = None,
) -> tuple[int, int] | None:
    """The spot near what's being explained that hides the least: no text (OCR'd too, so text
    in images counts), no busy pixels, never the notch or Wisp's other things on screen."""
    if ctx is None or region is None or ctx.screenshot is None:
        return None
    k = _dpi_scale()
    w, h = card_size(texts, title)
    rx, ry, rw, rh = region
    gap = int(26 * k)
    occ = Occupancy(ctx, [*(avoid or []), (rx - gap // 2, ry - gap // 2, rw + gap, rh + gap)], k=k)
    x0, y0, sw, sh = occ.bounds()
    x1, y1 = x0 + sw, y0 + sh
    step = int(24 * k)
    candidates = [(rx - gap - w, ry), (rx + rw + gap, ry), (rx, ry + rh + gap), (rx, ry - gap - h)]
    for cy in range(y0 + 8, y1 - h - 8, step):
        for cx in range(x0 + 8, x1 - w - 8, step):
            candidates.append((cx, cy))
    best, best_score = None, None
    for cx, cy in candidates:
        covered = occ.cost((cx, cy, w, h))
        if covered == float("inf"):
            continue
        dx = max(0, rx - (cx + w), cx - (rx + rw))
        dy = max(0, ry - (cy + h), cy - (ry + rh))
        score = covered + (dx + dy) * h * 0.35  # hiding words is worse than a longer thread
        if best_score is None or score < best_score:
            best, best_score = (cx, cy), score
    return best


STAGE_SIZES = ((1040, 700), (900, 620), (780, 560), (660, 500), (540, 440))  # CSS px, biggest first
PLATE_SIZE = ((760, 650),)  # an explain_in_3d plate: canvas, rise and step text
BOARD_SIZES = (
    (620, 560),
    (540, 505),
    (470, 460),
    (410, 420),
)  # sketchboards incl. title and caption


def place_stage(
    ctx: ScreenContext | None,
    avoid: list[tuple[int, int, int, int]] | None = None,
    sizes: tuple[tuple[int, int], ...] = STAGE_SIZES,
) -> tuple[int, int, int, int] | None:
    """Where a 3D model should float: the biggest spot that hides the least of what's on screen
    (text, images, video, controls), clear of the notch and of any note card. Returns
    [x, y, w, h] in screen px, caption included."""
    if ctx is None or ctx.screenshot is None:
        return None
    k = _dpi_scale()
    occ = Occupancy(ctx, avoid, k=k)
    x0, y0, sw, sh = occ.bounds()
    x1, y1 = x0 + sw, y0 + sh
    screen_area = max(1, sw * sh)
    best: tuple[float, tuple[int, int, int, int]] | None = None
    for w_css, h_css in sizes:
        w, h = int(w_css * k), int(h_css * k)
        if w > sw - 16 or h > sh - 16:
            continue
        step = int(32 * k)
        for cy in range(y0 + 8, y1 - h - 8 + 1, step):
            for cx in range(x0 + 8, x1 - w - 8 + 1, step):
                covered = occ.cost((cx, cy, w, h))
                if covered == float("inf"):
                    continue
                # Mostly: hide as little as possible. A bigger stage wins near-ties.
                score = covered / (w * h) - (w * h) / screen_area * 0.6
                if best is None or score < best[0]:
                    best = (score, (cx, cy, w, h))
    return best[1] if best else None


class Guide(BaseModel):
    goal: str
    label: str
    rect: tuple[int, int, int, int]
    step: int = 1
