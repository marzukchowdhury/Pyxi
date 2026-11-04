"""Where to put things so they never cover what the user is looking at.

Every look at the screen becomes a cost map. Covering a line of text costs the most (OCR
lines, so text inside images counts too), covering busy pixels (photos, video, charts, memes)
costs a lot, covering a control costs a little, and empty background costs nothing. The notch
and anything Wisp already has on screen (a note card, a 3D stage) are off limits. Note cards
and 3D stages then take the cheapest spot, so they settle into the real empty space, whatever
app is in front.

Costs are summed from an integral image, so scoring a candidate spot is O(1) and the whole
search takes a few milliseconds.
"""

from __future__ import annotations

import time

import numpy as np

from wisp.screen import ScreenContext

Rect = tuple[int, int, int, int]

CELL = 16  # physical px per cost cell
TEXT, BUSY, CONTROL = 6.0, 2.0, 1.0
BLOCKED = 1e4  # per cell: the notch, Wisp's own things


class Occupancy:
    def __init__(self, ctx: ScreenContext, avoid: list[Rect] | None = None, *, k: float = 1.0):
        shot = ctx.screenshot
        assert shot is not None
        self.ox, self.oy = ctx.origin
        self.w, self.h = shot.width, shot.height
        gw, gh = -(-self.w // CELL), -(-self.h // CELL)
        cost = np.zeros((gh, gw), dtype=np.float64)
        cost += BUSY * _busy(shot, gw, gh)
        area = self.w * self.h
        for e in ctx.elements:
            if e.name.strip() and 0 < e.rect[2] * e.rect[3] < area * 0.15:
                self._paint(cost, e.rect, CONTROL)
        for ln in ctx.text:
            self._paint(cost, ln.rect, TEXT)
        # The notch lives at the top centre.
        notch_w, notch_h = int(460 * k), int(70 * k)
        self._paint(cost, (self.ox + (self.w - notch_w) // 2, self.oy, notch_w, notch_h), BLOCKED)
        for r in avoid or []:
            self._paint(cost, r, BLOCKED)
        self.integral = np.zeros((gh + 1, gw + 1))
        self.integral[1:, 1:] = cost.cumsum(0).cumsum(1)

    def _paint(self, cost: np.ndarray, r: Rect, weight: float) -> None:
        x, y, w, h = r[0] - self.ox, r[1] - self.oy, r[2], r[3]
        c0, r0 = max(0, x // CELL), max(0, y // CELL)
        c1, r1 = min(cost.shape[1], -(-(x + w) // CELL)), min(cost.shape[0], -(-(y + h) // CELL))
        if c1 > c0 and r1 > r0:
            cost[int(r0) : int(r1), int(c0) : int(c1)] += weight

    def cost(self, box: Rect) -> float:
        """What it costs to cover this box (physical global px). Off screen is blocked."""
        x, y, w, h = box[0] - self.ox, box[1] - self.oy, box[2], box[3]
        if x < 0 or y < 0 or x + w > self.w or y + h > self.h:
            return float("inf")
        gh, gw = self.integral.shape[0] - 1, self.integral.shape[1] - 1
        c0, r0 = x // CELL, y // CELL
        c1, r1 = min(gw, -(-(x + w) // CELL)), min(gh, -(-(y + h) // CELL))
        s = self.integral
        return float(s[r1, c1] - s[r0, c1] - s[r1, c0] + s[r0, c0]) * CELL * CELL

    def bounds(self) -> Rect:
        return self.ox, self.oy, self.w, self.h


def _busy(shot, gw: int, gh: int) -> np.ndarray:
    """0..1 per cell: how much is going on there (local contrast). Flat background is 0."""
    gray = np.asarray(shot.convert("L"), dtype=np.float32)
    h, w = gray.shape
    pad = np.zeros((gh * CELL, gw * CELL), dtype=np.float32)
    pad[:h, :w] = gray
    if h < gh * CELL:
        pad[h:, :w] = gray[-1:, :]
    if w < gw * CELL:
        pad[:, w:] = pad[:, w - 1 : w]
    cells = pad.reshape(gh, CELL, gw, CELL)
    std = cells.std(axis=(1, 3))
    return np.clip((std - 6.0) / 30.0, 0.0, 1.0)


class Placed:
    """What Wisp has on screen right now, so new things keep clear of it."""

    def __init__(self) -> None:
        self._items: dict[str, tuple[Rect, float]] = {}

    def put(self, kind: str, rect: Rect | None, seconds: float) -> None:
        if rect is None:
            self._items.pop(kind, None)
        else:
            self._items[kind] = (rect, time.monotonic() + seconds)

    def others(self, kind: str) -> list[Rect]:
        now = time.monotonic()
        return [r for k, (r, until) in self._items.items() if k != kind and until > now]

    def clear(self) -> None:
        self._items.clear()
