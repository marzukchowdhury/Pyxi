/**
 * Hand-drawn geometry for sketchboards. Shapes arrive on a 100 x 64 grid; these turn them into
 * SVG paths in board pixels with a steady human wobble (seeded, so a redraw looks the same).
 */
import type { SketchShape } from "../shared/protocol";

export type Pt = [number, number];
export const GRID = { w: 100, h: 64 };
const TEXT_GRID = 3.9; // label size in grid units
const EQ_GRID = 5.4; // equation size in grid units

export function rng(seed: number) {
  let t = seed >>> 0 || 1;
  return () => {
    t += 0x6d2b79f5;
    let r = Math.imul(t ^ (t >>> 15), 1 | t);
    r ^= r + Math.imul(r ^ (r >>> 7), 61 | r);
    return ((r ^ (r >>> 14)) >>> 0) / 4294967296;
  };
}

const f = (n: number) => n.toFixed(1);

/** A smooth path through points (Catmull-Rom as cubic Béziers). */
export function smooth(pts: Pt[]): string {
  if (pts.length < 2) return "";
  let d = `M ${f(pts[0][0])} ${f(pts[0][1])}`;
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[i - 1] ?? pts[i];
    const p1 = pts[i];
    const p2 = pts[i + 1];
    const p3 = pts[i + 2] ?? p2;
    d += ` C ${f(p1[0] + (p2[0] - p0[0]) / 6)} ${f(p1[1] + (p2[1] - p0[1]) / 6)}, ${f(p2[0] - (p3[0] - p1[0]) / 6)} ${f(p2[1] - (p3[1] - p1[1]) / 6)}, ${f(p2[0])} ${f(p2[1])}`;
  }
  return d;
}

/** Straight-ish strokes between vertices: each edge bows a hair, the way a pen does. */
function inked(pts: Pt[], wobble: number, rand: () => number, closed: boolean): string {
  const j = () => (rand() - 0.5) * wobble;
  const v = pts.map(([x, y]) => [x + j(), y + j()] as Pt);
  if (closed) v.push([v[0][0] + j(), v[0][1] + j()]);
  let d = `M ${f(v[0][0])} ${f(v[0][1])}`;
  for (let i = 1; i < v.length; i++) {
    const [x0, y0] = v[i - 1];
    const [x1, y1] = v[i];
    const len = Math.hypot(x1 - x0, y1 - y0) || 1;
    const bow = (rand() - 0.5) * Math.min(wobble * 1.6, len * 0.04);
    const mx = (x0 + x1) / 2 + (-(y1 - y0) / len) * bow;
    const my = (y0 + y1) / 2 + ((x1 - x0) / len) * bow;
    d += ` Q ${f(mx)} ${f(my)} ${f(x1)} ${f(y1)}`;
  }
  if (closed && v.length > 2) {
    // The pen runs a little past where it started.
    const [ax, ay] = v[v.length - 1];
    const [bx, by] = v[1];
    d += ` L ${f(ax + (bx - ax) * 0.12)} ${f(ay + (by - ay) * 0.12)}`;
  }
  return d;
}

function roughEllipse(cx: number, cy: number, rx: number, ry: number, rand: () => number): string {
  const pts: Pt[] = [];
  const start = rand() * Math.PI * 2;
  for (let k = 0; k <= 16; k++) {
    const a = start + (k / 16) * Math.PI * 2.08; // overlap the start a touch
    const wob = 1 + (rand() - 0.5) * 0.05;
    pts.push([cx + Math.cos(a) * rx * wob, cy + Math.sin(a) * ry * wob]);
  }
  return smooth(pts);
}

function roughRect(x: number, y: number, w: number, h: number, rand: () => number, wobble: number): string {
  return inked([[x, y], [x + w, y], [x + w, y + h], [x, y + h]], wobble, rand, true);
}

export interface Drawn {
  d: string; // the stroke
  head?: string; // arrowhead, drawn after the stroke
  closed: boolean;
  length: number; // board px, for timing
}

export function length(pts: Pt[]): number {
  let n = 0;
  for (let i = 1; i < pts.length; i++) n += Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]);
  return n;
}

/** The stroke for a shape, in board px (scale = board px per grid unit). */
export function drawShape(shape: SketchShape, scale: number, seed: number): Drawn | null {
  const rand = rng(seed);
  const wobble = Math.max(0.6, scale * 0.22);
  const pts = shape.points.map(([x, y]) => [x * scale, y * scale] as Pt);
  switch (shape.kind) {
    case "line":
    case "polyline":
      return pts.length >= 2 ? { d: inked(pts, wobble, rand, false), closed: false, length: length(pts) } : null;
    case "polygon":
      return pts.length >= 3 ? { d: inked(pts, wobble, rand, true), closed: true, length: length([...pts, pts[0]]) } : null;
    case "curve":
      return pts.length >= 3 ? { d: smooth(pts), closed: false, length: length(pts) * 1.05 } : null;
    case "arrow": {
      if (pts.length < 2) return null;
      const body = pts.length === 2 ? inked(pts, wobble, rand, false) : smooth(pts);
      const [tx, ty] = pts[pts.length - 1];
      const [px, py] = pts[pts.length - 2];
      const a = Math.atan2(ty - py, tx - px);
      const size = Math.max(7, scale * 2.1);
      const wing = (s: number): Pt => [tx - Math.cos(a + s) * size, ty - Math.sin(a + s) * size];
      const [l, r] = [wing(0.5), wing(-0.5)];
      const head = `M ${f(l[0])} ${f(l[1])} L ${f(tx)} ${f(ty)} L ${f(r[0])} ${f(r[1])}`;
      return { d: body, head, closed: false, length: length(pts) };
    }
    case "rect": {
      if (!shape.at || !shape.size) return null;
      const [x, y, w, h] = [shape.at[0] * scale, shape.at[1] * scale, shape.size[0] * scale, shape.size[1] * scale];
      return { d: roughRect(x, y, w, h, rand, wobble), closed: true, length: 2 * (w + h) };
    }
    case "ellipse": {
      if (!shape.at || !shape.size) return null;
      const [w, h] = [shape.size[0] * scale, shape.size[1] * scale];
      const [cx, cy] = [shape.at[0] * scale + w / 2, shape.at[1] * scale + h / 2];
      return { d: roughEllipse(cx, cy, w / 2, h / 2, rand), closed: true, length: Math.PI * (w + h) * 0.55 };
    }
    default:
      return null;
  }
}

/** Where a label goes and how big, kept inside the board (models misjudge text width). */
export function placeText(shape: SketchShape, scale: number): { x: number; y: number; size: number; width: number } | null {
  if (!shape.at || !shape.text) return null;
  const grid = shape.kind === "equation" ? EQ_GRID : TEXT_GRID;
  const width = shape.text.length * grid * 0.47; // Caveat is narrow
  const x = Math.max(1.5, Math.min(shape.at[0], GRID.w - 1.5 - width));
  const y = Math.max(grid + 0.5, Math.min(shape.at[1], GRID.h - 1));
  return { x: x * scale, y: y * scale, size: grid * scale, width: width * scale };
}

/** How long a shape takes to ink in, in seconds. */
export function drawSeconds(shape: SketchShape, scale: number): number {
  if (shape.kind === "text" || shape.kind === "equation") return 0.35 + Math.min(0.9, (shape.text?.length ?? 0) * 0.045);
  const d = drawShape(shape, scale, 1);
  const grid = d ? d.length / scale : 20;
  return 0.45 + Math.min(0.85, grid / 110) + (shape.kind === "arrow" ? 0.2 : 0);
}

/** Start time of every shape within its step, and when the step's drawing is finished. */
export function timeline(shapes: SketchShape[], step: number, scale: number): { delays: Map<number, number>; total: number } {
  const delays = new Map<number, number>();
  let t = 0.1;
  let end = 0;
  shapes.forEach((s, i) => {
    if (s.step !== step) return;
    delays.set(i, t);
    const dur = drawSeconds(s, scale);
    end = Math.max(end, t + dur);
    t += Math.min(dur * 0.7, 0.55); // the next stroke starts before this one ends
  });
  return { delays, total: end + 0.25 };
}
