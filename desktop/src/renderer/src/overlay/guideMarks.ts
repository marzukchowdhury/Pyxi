// Guide marks, the parts that are plain arithmetic: where a label goes so it stays on screen,
// and what a click somewhere on the desktop means for the step being waited on.

export interface Box {
  x: number;
  y: number;
  w: number;
  h: number;
}

const MARGIN = 8;

/**
 * Where a drawing's label sits (the middle of its top edge, CSS px): under the thing it names,
 * or above it when under would run off the bottom, and never off either side. A step framed
 * at the bottom of a window used to put its pill, and its Done button, on the taskbar or past
 * the edge of the screen.
 */
export function labelSpot(r: Box, gap: number, size: { w: number; h: number }, view: { w: number; h: number }): { x: number; y: number } {
  let y = r.y + r.h + gap;
  if (y + size.h > view.h - MARGIN) {
    const above = r.y - gap - size.h;
    // A frame as tall as the screen has no room above either: inside its bottom edge then.
    y = above >= MARGIN ? above : Math.max(MARGIN, view.h - MARGIN - size.h);
  }
  const half = size.w / 2;
  const x = Math.min(Math.max(r.x + r.w / 2, MARGIN + half), Math.max(MARGIN + half, view.w - MARGIN - half));
  return { x, y };
}

/** About how big a label pill is (12 px text, ~7 px a character), with its Done button or not. */
export function labelSize(text: string, done = false): { w: number; h: number } {
  return { w: text.length * 7 + 44 + (done ? 64 : 0), h: 30 };
}

/**
 * What a click anywhere on the desktop (physical px) means for the guide step marked on this
 * screen: "clicked" on the mark of a click step, "elsewhere" off it, nothing when this screen
 * has no mark. Every screen's overlay is told of every click and only the one holding the mark
 * answers, so a step on a second screen hears its click too.
 *
 * "within": a click inside the area of a scroll, look or key step. "Scroll down to Recovery"
 * frames the whole list, and people scroll and then click Recovery, which is inside it. That
 * click was nothing at all, and the walk-through sat on a page they had already left. Inside
 * a field they are to type in (or the start of a drag) a click is still not a step.
 */
export function guideClick(
  target: { rect?: readonly number[] | null; action?: string | null } | undefined,
  x: number,
  y: number,
): "clicked" | "elsewhere" | "within" | null {
  if (!target?.rect) return null;
  const [rx, ry, rw, rh] = target.rect;
  // A mark is drawn a little outside its control, and people aim at the drawing: a click
  // on the frame itself (or a hair outside it) is a click on the step.
  const pad = 12;
  const inside = x >= rx - pad && y >= ry - pad && x <= rx + rw + pad && y <= ry + rh + pad;
  const clicky = !target.action || target.action.endsWith("click");
  if (!inside) return "elsewhere";
  if (clicky) return "clicked";
  return target.action === "type" || target.action === "drag" ? null : "within";
}
