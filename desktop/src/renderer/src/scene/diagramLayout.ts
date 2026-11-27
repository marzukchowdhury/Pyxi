/**
 * Typed diagrams: the model names a type and gives the content (short labels, the links, the one
 * or two items that matter); this file places every box, label and connector, so a diagram is
 * tidy by construction. Nothing here guesses at pixels: it works on a frame 100 units wide whose
 * height fits the content (at most H_MAX), and the board scales that to the screen.
 *
 * Promises, held for any input inside the limits and checked in diagramLayout.test.ts:
 * no two boxes overlap, no two labels overlap, a label sits inside its own box or clear of every
 * box, connectors never run through a box or a label, and nothing leaves the frame. A label that
 * will not fit at one size is wrapped, then set smaller; outside the limits it is cut short with
 * an ellipsis rather than allowed to collide.
 *
 * The type catalogue and the taste (at most nine items, one accent on at most two of them, thin
 * lines, right-angled connectors, nothing drawn that carries no meaning) follow the diagram-design
 * skill by Cathryn Lavery (MIT, github.com/cathrynlavery/diagram-design), written again for
 * Pyxi's own board.
 */
import type { DiagramItem, DiagramSpec } from "../shared/protocol";

export type Pt = [number, number];
export interface Rect { x: number; y: number; w: number; h: number }

export interface DNode extends Rect {
  item: number; // index into the spec's items
  shape: "box" | "pill" | "decision" | "dot" | "plain";
  step: number;
  focus: boolean;
}
export interface DText extends Rect {
  ax: number; // where the text is anchored (x); the rect is its ink
  anchor: "start" | "middle" | "end";
  lines: string[];
  size: number;
  role: "label" | "when" | "head" | "axis" | "link" | "center";
  step: number;
  focus: boolean;
  item: number | null;
}
export interface DWire {
  points: Pt[];
  arrow: boolean;
  curve: boolean; // a smooth arc through the points (loops), not right-angled runs
  role: "link" | "rule"; // a rule is the diagram's own frame: a baseline, an axis, a divider
  step: number;
  to: number | null; // the item it leads into, for the order things ink in
}
export interface DiagramLayout {
  w: number;
  h: number;
  size: number; // the label size used, in frame units
  nodes: DNode[];
  texts: DText[];
  wires: DWire[];
  notes: string[]; // what had to be dropped or cut (only outside the limits)
}

export const FRAME_W = 100;
export const H_MAX = 92;
export const H_EASY = 66; // a board this tall or less is tried first: wide, like the freehand board
export const MAX_ITEMS = 9;
export const SIZES = [3.1, 2.85, 2.6]; // label sizes tried first, in frame units
export const SMALL = [2.4, 2.2]; // only when the longest labels allowed leave no other way
const H_MIN = 26; // a two-box diagram still gets a board, not a ribbon
const M = 3; // margin
const LINE = 1.22;
const PADX = 1.6;
const PADY = 1.2;
const SMALL_TEXT = 2.2; // dates, axis names, yes/no on a link
export const POINT = 1.8; // how far the pointed ends of a choice cut in

// ---------------------------------------------------------------- text

// How wide each character is, in hundredths of the type size: measured in Chromium from the
// fonts the board uses. Labels are Inter Semi Bold (dates and yes/no are Medium, a hair
// narrower); headings take the wider of Fraunces Semi Bold and Medium Italic.
const MEASURED = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 .,:;'!?$%&/()-+…";
const INTER = [
  57, 62, 58, 62, 59, 39, 63, 61, 26, 26, 57, 26, 90, 61, 61, 62, 62, 40, 55, 35, 61, 59, 84, 57, 59, 57,
  73, 66, 74, 72, 61, 59, 75, 75, 28, 58, 70, 57, 92, 76, 77, 65, 77, 65, 65, 66, 74, 73, 102, 72, 71, 65,
  66, 42, 62, 64, 67, 61, 64, 58, 64, 64, 25, 32, 32, 32, 33, 33, 32, 54, 65, 100, 66, 38, 37, 37, 47, 67, 96,
];
const FRAUNCES = [
  56, 61, 52, 62, 53, 40, 58, 64, 32, 32, 62, 32, 96, 64, 60, 63, 61, 48, 49, 39, 63, 56, 83, 59, 57, 51,
  74, 71, 70, 80, 66, 61, 77, 85, 41, 51, 79, 63, 93, 77, 80, 70, 80, 75, 61, 70, 75, 72, 106, 73, 69, 64,
  68, 47, 62, 56, 63, 59, 62, 53, 62, 62, 22, 27, 28, 30, 31, 18, 33, 53, 60, 78, 76, 50, 37, 36, 41, 56, 91,
];
const widths = (w: number[]) => new Map([...MEASURED].map((ch, i) => [ch, w[i] / 100]));
const ADVANCE = { sans: widths(INTER), serif: widths(FRAUNCES) };

/** Width of a line in frame units, a touch over what the browser will draw (kerning only ever
 * tightens these fonts), so a line never turns out wider than the room it was given. */
export function textWidth(text: string, size: number, serif = false): number {
  const table = serif ? ADVANCE.serif : ADVANCE.sans;
  let em = 0;
  for (const ch of text) {
    const code = ch.codePointAt(0) ?? 0;
    // Not measured: an accented letter is about its plain one; other scripts are given room.
    em += table.get(ch) ?? (code > 0xffff ? 1.4 : code > 0x2e7f ? 1.05 : code > 0x24f ? 0.85 : 0.75);
  }
  return em * size * 1.015;
}

/** Cut a line to fit, ending in an ellipsis. */
function clipLine(text: string, maxW: number, size: number, serif = false): string {
  if (textWidth(text, size, serif) <= maxW) return text;
  let s = text;
  while (s.length > 1 && textWidth(s.trimEnd() + "…", size, serif) > maxW) s = s.slice(0, -1);
  return s.trimEnd() + "…";
}

/** Break a label into at most maxLines lines no wider than maxW, as evenly as it can. null when it
 * cannot be done (a single word is too wide, or it needs more lines). */
export function wrap(label: string, maxW: number, size: number, maxLines: number): string[] | null {
  const words = label.split(/\s+/).filter(Boolean);
  if (!words.length) return [""];
  if (words.some((w) => textWidth(w, size) > maxW)) return null;
  const width = (a: number, b: number) => textWidth(words.slice(a, b).join(" "), size);
  for (let k = 1; k <= Math.min(maxLines, words.length); k++) {
    // The best way to cut the words into exactly k lines: the widest line as narrow as possible.
    let best = null as number[] | null;
    let bestW = Infinity;
    const cut = (from: number, left: number, cuts: number[], widest: number) => {
      if (left === 1) {
        const w = Math.max(widest, width(from, words.length));
        if (w <= maxW && w < bestW) [best, bestW] = [[...cuts], w];
        return;
      }
      for (let to = from + 1; to <= words.length - (left - 1); to++) {
        const w = width(from, to);
        if (w > maxW) break;
        cut(to, left - 1, [...cuts, to], Math.max(widest, w));
      }
    };
    cut(0, k, [], 0);
    if (best) {
      const edges = [0, ...best, words.length];
      return edges.slice(1).map((to, i) => words.slice(edges[i], to).join(" "));
    }
  }
  return null;
}

/** Outside the limits: fill the lines greedily and cut what is left. Never fails. */
function wrapClipped(label: string, maxW: number, size: number, maxLines: number): string[] {
  const words = label.split(/\s+/).filter(Boolean);
  const lines: string[] = [];
  let line = "";
  for (let i = 0; i < words.length; i++) {
    const next = line ? `${line} ${words[i]}` : words[i];
    if (textWidth(next, size) <= maxW) {
      line = next;
    } else if (lines.length === maxLines - 1) {
      return [...lines, clipLine(`${next} ${words.slice(i + 1).join(" ")}`, maxW, size)];
    } else if (line) {
      lines.push(line);
      line = textWidth(words[i], size) <= maxW ? words[i] : "";
      if (!line) {
        if (lines.length === maxLines - 1) return [...lines, clipLine(words.slice(i).join(" "), maxW, size)];
        lines.push(clipLine(words[i], maxW, size));
      }
    } else {
      if (lines.length === maxLines - 1) return [...lines, clipLine(words.slice(i).join(" "), maxW, size)];
      lines.push(clipLine(words[i], maxW, size));
    }
  }
  if (line) lines.push(line);
  return lines.length ? lines : [""];
}

/** Every label wrapped at one size, or null when any of them will not fit. */
function fitLabels(labels: string[], maxW: number | number[], maxLines: number, size: number, clip: boolean): string[][] | null {
  const out: string[][] = [];
  for (let i = 0; i < labels.length; i++) {
    const w = Math.max(1, typeof maxW === "number" ? maxW : maxW[i]);
    const lines = wrap(labels[i], w, size, maxLines) ?? (clip ? wrapClipped(labels[i], w, size, maxLines) : null);
    if (!lines) return null;
    out.push(lines);
  }
  return out;
}

const linesH = (n: number, size: number) => n * size * LINE;
const most = (lines: string[][]) => Math.max(1, ...lines.map((l) => l.length));

function block(lines: string[], size: number, ax: number, y: number, anchor: DText["anchor"], role: DText["role"], step: number, focus: boolean, item: number | null): DText {
  const serif = role === "head" || role === "center";
  const w = Math.max(...lines.map((l) => textWidth(l, size, serif)));
  const x = anchor === "middle" ? ax - w / 2 : anchor === "end" ? ax - w : ax;
  return { x, y, w, h: linesH(lines.length, size), ax, anchor, lines, size, role, step, focus, item };
}

/** A node's label, centred in it. */
function labelIn(node: Rect, lines: string[], size: number, it: DiagramItem, i: number): DText {
  return block(lines, size, node.x + node.w / 2, node.y + (node.h - linesH(lines.length, size)) / 2, "middle", "label", it.step, !!it.focus, i);
}

// ---------------------------------------------------------------- the entry point

type Attempt = (size: number, clip: boolean, maxH: number) => DiagramLayout | null;

/** Lay a typed diagram out. Never throws and never overlaps: outside the limits it drops what is
 * over the count and cuts labels short, and says so in `notes`. */
export function layoutDiagram(raw: DiagramSpec): DiagramLayout {
  const notes: string[] = [];
  const seen = new Set<string>();
  const items = (raw.items ?? [])
    .map((it, i) => ({ ...it, id: String(it?.id ?? i + 1), label: String(it?.label ?? "").trim(), step: Math.max(0, Math.floor(Number(it?.step) || 0)) }))
    .filter((it) => it.label && !seen.has(it.id) && seen.add(it.id));
  if (items.length > MAX_ITEMS) notes.push(`showing ${MAX_ITEMS} of ${items.length} items`);
  let focal = 0;
  const kept = items.slice(0, MAX_ITEMS).map((it) => ({ ...it, focus: !!it.focus && ++focal <= 2 }));
  const spec: DiagramSpec = { ...raw, items: kept, links: raw.links ?? [] };
  const attempts = ATTEMPTS[spec.type]?.(spec) ?? stepsAttempts(spec);
  let found: DiagramLayout | null = null;
  // A wide board at a comfortable size first; then a taller board; then smaller type.
  const smallest = SMALL[SMALL.length - 1];
  search: for (const [sizes, maxH] of [[SIZES, H_EASY], [SIZES, 80], [SIZES, H_MAX], [SMALL, H_MAX]] as [number[], number][])
    for (const attempt of attempts)
      for (const size of sizes) if ((found = attempt(size, false, maxH))) break search;
  found ??= attempts[attempts.length - 1](smallest, true, H_MAX) ?? stepsAttempts(spec).pop()!(smallest, true, H_MAX)!;
  found.notes = [...notes, ...found.notes];
  return settle(found);
}

/** Short diagrams are centred in a frame that is still a board. */
function settle(l: DiagramLayout): DiagramLayout {
  if (l.h >= H_MIN) return l;
  const dy = (H_MIN - l.h) / 2;
  for (const r of [...l.nodes, ...l.texts]) r.y += dy;
  for (const w of l.wires) w.points = w.points.map(([x, y]) => [x, y + dy]);
  return { ...l, h: H_MIN };
}

const empty = (size: number, h: number): DiagramLayout => ({ w: FRAME_W, h, size, nodes: [], texts: [], wires: [], notes: [] });
const wire = (points: Pt[], step: number, to: number | null, o: Partial<DWire> = {}): DWire => ({ points: tidy(points), arrow: true, curve: false, role: "link", step, to, ...o });
const rule = (points: Pt[], step = 0, to: number | null = null): DWire => wire(points, step, to, { arrow: false, role: "rule" });

/** Drop repeated points and points in the middle of a straight run. */
function tidy(points: Pt[]): Pt[] {
  const out: Pt[] = [];
  for (const p of points) {
    const a = out[out.length - 1];
    if (a && Math.abs(a[0] - p[0]) < 0.01 && Math.abs(a[1] - p[1]) < 0.01) continue;
    const b = out[out.length - 2];
    if (a && b && ((Math.abs(a[0] - b[0]) < 0.01 && Math.abs(p[0] - a[0]) < 0.01) || (Math.abs(a[1] - b[1]) < 0.01 && Math.abs(p[1] - a[1]) < 0.01))) out.pop();
    out.push(p);
  }
  return out;
}

// ---------------------------------------------------------------- steps

/** A process in order: boxes left to right, wrapping onto a second or third row. */
function stepsAttempts(spec: DiagramSpec): Attempt[] {
  const rowsFor = [1, 2, 3].filter((r) => r === 1 || spec.items.length > r);
  return rowsFor.map((rows) => (size, clip, maxH) => {
    const items = spec.items;
    const n = items.length;
    const cols = Math.ceil(n / rows);
    if (cols > 5 && !(clip && rows === 3)) return null;
    const [gap, rowGap] = [5.5, 7];
    const bw = Math.min(30, (FRAME_W - 2 * M - (cols - 1) * gap) / cols);
    const lines = fitLabels(items.map((i) => i.label), bw - 2 * PADX, 3, size, clip);
    if (!lines) return null;
    const bh = Math.max(7, linesH(most(lines), size) + 2 * PADY);
    const h = 2 * M + rows * bh + (rows - 1) * rowGap;
    if (h > maxH && !clip) return null;
    const out = empty(size, h);
    const x0 = (FRAME_W - (cols * bw + (cols - 1) * gap)) / 2;
    items.forEach((it, i) => {
      const node: DNode = { x: x0 + (i % cols) * (bw + gap), y: M + Math.floor(i / cols) * (bh + rowGap), w: bw, h: bh, item: i, shape: "box", step: it.step, focus: !!it.focus };
      out.nodes.push(node);
      out.texts.push(labelIn(node, lines[i], size, it, i));
    });
    for (let i = 1; i < n; i++) {
      const [a, b] = [out.nodes[i - 1], out.nodes[i]];
      const step = Math.max(a.step, b.step);
      if (a.y === b.y) out.wires.push(wire([[a.x + a.w, a.y + a.h / 2], [b.x, b.y + b.h / 2]], step, i));
      else {
        // Down from the end of one row, back along the space between rows, into the next.
        const y = a.y + a.h + rowGap / 2 - 0.6;
        out.wires.push(wire([[a.x + a.w / 2, a.y + a.h], [a.x + a.w / 2, y], [b.x + b.w / 2, y], [b.x + b.w / 2, b.y]], step, i));
      }
    }
    return out;
  });
}

// ---------------------------------------------------------------- timeline

/** Events in order along one line, their names above and below it in turn (and on two heights
 * a side when there are many), each tied to its dot by a hairline. */
function timelineAttempts(spec: DiagramSpec): Attempt[] {
  return [1, 2].map((levels) => (size, clip, maxH) => {
    const items = spec.items;
    const n = items.length;
    const span = FRAME_W - 2 * M;
    const cycle = 2 * levels; // how far apart two names at the same height are, in events
    // Two names at the same height never touch, and (on two heights) a far name's hairline
    // passes clear between the near names either side of it.
    const sameHeight = n > 1 ? (cycle * span - 2 * (n - 1)) / (n - 1 + cycle) : 34;
    const hairline = levels === 2 && n > 2 ? (2 * span - 1.5 * (n - 1)) / ((n - 1) / 2 + 2) : 34;
    const bw = Math.min(34, sameHeight, hairline);
    const dx = n > 1 ? (span - bw) / (n - 1) : 0;
    const lines = fitLabels(items.map((i) => i.label), bw, 3, size, clip);
    if (!lines) return null;
    if (!clip && items.some((i) => i.when && textWidth(i.when.trim(), SMALL_TEXT) > bw)) return null;
    const whenH = items.some((i) => i.when) ? linesH(1, SMALL_TEXT) + 0.4 : 0;
    const blockH = whenH + linesH(most(lines), size);
    const [drop, between] = [3, 1.6];
    const half = drop + levels * blockH + (levels - 1) * between;
    const h = 2 * M + 2 * half;
    if (h > maxH && !clip) return null;
    const out = empty(size, h);
    const base = M + half;
    out.wires.push(rule([[M, base], [FRAME_W - M, base]]));
    items.forEach((it, i) => {
      const x = n > 1 ? M + bw / 2 + i * dx : FRAME_W / 2;
      const r = it.focus ? 1.3 : 0.9;
      const above = i % 2 === 0;
      const level = levels === 2 ? Math.floor(i / 2) % 2 : 0;
      const near = drop + level * (blockH + between); // from the line to the near edge of the block
      const mine = linesH(lines[i].length, size) + (it.when ? whenH : 0);
      const top = above ? base - near - mine : base + near;
      out.nodes.push({ x: x - r, y: base - r, w: 2 * r, h: 2 * r, item: i, shape: "dot", step: it.step, focus: !!it.focus });
      out.wires.push(rule([[x, above ? base - r : base + r], [x, above ? base - near + 0.5 : base + near - 0.5]], it.step, i));
      if (it.when) out.texts.push(block([clipLine(it.when.trim(), bw, SMALL_TEXT)], SMALL_TEXT, x, top, "middle", "when", it.step, false, i));
      out.texts.push(block(lines[i], size, x, top + (it.when ? whenH : 0), "middle", "label", it.step, !!it.focus, i));
    });
    return out;
  });
}

// ---------------------------------------------------------------- comparison

function comparisonAttempts(spec: DiagramSpec): Attempt[] {
  return spec.axes ? [quadrant(spec)] : [columns(spec)];
}

/** Two named columns under one rule, a divider between them: a T-chart. */
function columns(spec: DiagramSpec): Attempt {
  return (size, clip, maxH) => {
    const items = spec.items;
    const sided = items.every((i) => i.side === "left" || i.side === "right");
    const side = (i: number) => (sided ? items[i].side : i < Math.ceil(items.length / 2) ? "left" : "right");
    const [gutter, colW] = [6, (FRAME_W - 2 * M - 6) / 2];
    const lines = fitLabels(items.map((i) => i.label), colW - 2 * PADX, 3, size, clip);
    if (!lines) return null;
    // The two names share one size: a touch bigger than the items if both fit, else smaller.
    const names = (spec.columns ?? ["", ""]).map((name) => String(name ?? "").trim());
    const headFits = (hs: number) => names.every((name) => textWidth(name, hs, true) <= colW - 2 * PADX);
    const headSize = [size + 0.5, size, ...SIZES, ...SMALL].find((hs) => hs <= size + 0.5 && headFits(hs)) ?? SMALL[SMALL.length - 1];
    const rh = linesH(most(lines), size) + 1.8;
    const rowGap = 1.2;
    const ruleY = M + linesH(1, headSize) + 1;
    const y0 = ruleY + 1.6;
    const count = { left: 0, right: 0 };
    const out = empty(size, 0);
    items.forEach((it, i) => {
      const s = side(i) as "left" | "right";
      const node: DNode = { x: s === "left" ? M : M + colW + gutter, y: y0 + count[s]++ * (rh + rowGap), w: colW, h: rh, item: i, shape: "plain", step: it.step, focus: !!it.focus };
      out.nodes.push(node);
      out.texts.push(labelIn(node, lines[i], size, it, i));
    });
    const rows = Math.max(1, count.left, count.right);
    out.h = y0 + rows * rh + (rows - 1) * rowGap + M;
    if (out.h > maxH && !clip) return null;
    names.forEach((name, k) => {
      const text = clipLine(name, colW - 2 * PADX, headSize, true);
      if (text) out.texts.push(block([text], headSize, M + colW / 2 + k * (colW + gutter), M, "middle", "head", 0, false, null));
    });
    out.wires.push(rule([[M, ruleY], [FRAME_W - M, ruleY]]), rule([[FRAME_W / 2, ruleY], [FRAME_W / 2, out.h - M]]));
    return out;
  };
}

/** Two axes crossing in the middle, items as dots in the quarter they belong to. Each quarter
 * has four places; an item takes the free one nearest to where it was put. */
function quadrant(spec: DiagramSpec): Attempt {
  return (size, clip, maxH) => {
    const items = spec.items;
    const cw = (FRAME_W - 2 * M) / 4;
    const lines = fitLabels(items.map((i) => i.label), cw - 2.4, 3, size, clip);
    if (!lines) return null;
    const strip = linesH(1, SMALL_TEXT) + 0.8; // room for an axis name
    const dot = 3.3; // the dot and the air under it
    const ch = dot + linesH(most(lines), size) + 0.8;
    const axisY = M + strip + 2 * ch + strip;
    const h = axisY + 1.2 + 2 * ch + strip + M;
    if (h > maxH && !clip) return null;
    const out = empty(size, h);
    const rowY = (row: number) => (row < 2 ? M + strip + row * ch : axisY + 1.2 + (row - 2) * ch);
    const taken = new Set<number>();
    const clamp = (v: unknown) => Math.max(0, Math.min(100, Number(v ?? 50) || 0));
    items.forEach((it, i) => {
      // Where it was put, in cells: x left to right, y bottom to top (so row 0 is the top).
      const [fx, fy] = [clamp(it.x) / 25, (100 - clamp(it.y)) / 25];
      const quarter = (c: number, r: number) => (c < 2 ? 0 : 1) + (r < 2 ? 0 : 2);
      const home = quarter(Math.min(3, Math.floor(fx)), Math.min(3, Math.floor(fy)));
      let best = -1;
      let bestD = Infinity;
      for (let cell = 0; cell < 16; cell++) {
        if (taken.has(cell)) continue;
        const [c, r] = [cell % 4, Math.floor(cell / 4)];
        const d = Math.hypot(c + 0.5 - fx, r + 0.5 - fy) + (quarter(c, r) === home ? 0 : 100);
        if (d < bestD) [best, bestD] = [cell, d];
      }
      taken.add(best);
      const [c, r] = [best % 4, Math.floor(best / 4)];
      const [cx, y] = [M + (c + 0.5) * cw, rowY(r)];
      const rad = it.focus ? 1.2 : 0.9;
      out.nodes.push({ x: cx - rad, y: y + 1.4 - rad, w: 2 * rad, h: 2 * rad, item: i, shape: "dot", step: it.step, focus: !!it.focus });
      out.texts.push(block(lines[i], size, cx, y + dot, "middle", "label", it.step, !!it.focus, i));
    });
    out.wires.push(rule([[M, axisY], [FRAME_W - M, axisY]]), rule([[FRAME_W / 2, M], [FRAME_W / 2, h - M]]));
    const name = (v: unknown, room: number) => clipLine(String(v ?? "").trim(), room, SMALL_TEXT);
    const [xl, xr] = spec.axes?.x ?? ["", ""];
    const [yl, yh] = spec.axes?.y ?? ["", ""];
    const side = FRAME_W / 2 - M - 2.4;
    const add = (text: string, ax: number, y: number, anchor: DText["anchor"]) => text && out.texts.push(block([text], SMALL_TEXT, ax, y, anchor, "axis", 0, false, null));
    add(name(xl, side), M, axisY - strip + 0.2, "start");
    add(name(xr, side), FRAME_W - M, axisY - strip + 0.2, "end");
    add(name(yh, side), FRAME_W / 2 + 1.2, M, "start");
    add(name(yl, side), FRAME_W / 2 + 1.2, h - M - linesH(1, SMALL_TEXT), "start");
    return out;
  };
}

// ---------------------------------------------------------------- tree

interface Tree { root: number; kids: number[][]; depth: number[]; order: number[]; deepest: number }

/** Parent to child links as a tree. Anything left without a parent hangs off the root, and a
 * second parent is ignored, so a muddled list still draws as one tree. */
function readTree(spec: DiagramSpec): Tree {
  const n = spec.items.length;
  const at = new Map(spec.items.map((it, i) => [it.id, i]));
  const parent = new Array<number>(n).fill(-1);
  const kids: number[][] = spec.items.map(() => []);
  const under = (a: number, b: number): boolean => { // is a under b (or b itself)?
    for (let p = a; p >= 0; p = parent[p]) if (p === b) return true;
    return false;
  };
  for (const l of spec.links) {
    const [p, c] = [at.get(String(l.from)), at.get(String(l.to))];
    if (p == null || c == null || p === c || parent[c] >= 0 || under(p, c)) continue;
    parent[c] = p;
    kids[p].push(c);
  }
  const root = Math.max(0, parent.findIndex((p) => p < 0));
  for (let i = 0; i < n; i++) if (i !== root && parent[i] < 0) { parent[i] = root; kids[root].push(i); }
  const depth = new Array<number>(n).fill(0);
  const order: number[] = [];
  const walk = (i: number, d: number) => { depth[i] = d; order.push(i); kids[i].forEach((k) => walk(k, d + 1)); };
  walk(root, 0);
  return { root, kids, depth, order, deepest: Math.max(...depth) };
}

function treeAttempts(spec: DiagramSpec): Attempt[] {
  const tree = readTree(spec);
  return [treeDown(spec, tree), treeOutline(spec, tree)];
}

/** Root on top, children below, joined by a drop, a bar and a drop: never a diagonal. */
function treeDown(spec: DiagramSpec, t: Tree): Attempt {
  return (size, clip, maxH) => {
    if (clip) return null; // the outline is the one that always fits
    const items = spec.items;
    const leaves = t.order.filter((i) => !t.kids[i].length);
    const slot = Math.min(34, (FRAME_W - 2 * M) / leaves.length);
    const bw = Math.min(30, slot - 3);
    if (bw < 14) return null;
    const lines = fitLabels(items.map((i) => i.label), bw - 2 * PADX, 3, size, false);
    if (!lines) return null;
    const bh = Math.max(6.5, linesH(most(lines), size) + 2 * PADY);
    const gap = 6;
    const levels = t.deepest + 1;
    const h = 2 * M + levels * bh + (levels - 1) * gap;
    if (h > maxH) return null;
    const out = empty(size, h);
    const x0 = (FRAME_W - slot * leaves.length) / 2;
    const cx = new Array<number>(items.length).fill(0);
    let leaf = 0;
    const place = (i: number): number => {
      if (!t.kids[i].length) return (cx[i] = x0 + slot * (leaf++ + 0.5));
      const xs = t.kids[i].map(place);
      return (cx[i] = (xs[0] + xs[xs.length - 1]) / 2);
    };
    place(t.root);
    const nodeOf = new Map<number, DNode>();
    for (const i of t.order) {
      const node: DNode = { x: cx[i] - bw / 2, y: M + t.depth[i] * (bh + gap), w: bw, h: bh, item: i, shape: "box", step: items[i].step, focus: !!items[i].focus };
      nodeOf.set(i, node);
      out.nodes.push(node);
      out.texts.push(labelIn(node, lines[i], size, items[i], i));
    }
    for (const p of t.order)
      for (const c of t.kids[p]) {
        const [a, b] = [nodeOf.get(p)!, nodeOf.get(c)!];
        const bar = a.y + a.h + gap / 2;
        out.wires.push(wire([[cx[p], a.y + a.h], [cx[p], bar], [cx[c], bar], [cx[c], b.y]], Math.max(a.step, b.step), c, { arrow: false }));
      }
    return out;
  };
}

/** One row each, indented by depth, like a folder list. Fits any tree of nine. */
function treeOutline(spec: DiagramSpec, t: Tree): Attempt {
  return (size, clip, maxH) => {
    const items = spec.items;
    const span = FRAME_W - 2 * M;
    const deepest = Math.max(1, t.deepest);
    const want = Math.max(...items.map((i) => textWidth(i.label, size))) + 2 * PADX;
    const indent = Math.min(7, (span - want) / deepest);
    if (indent < 3.5 && !clip) return null;
    const step = Math.max(3.5, Math.min(7, indent));
    const bw = Math.max(8, Math.min(want, span - deepest * step));
    const [rh, gap] = [linesH(1, size) + 1.7, 1.3];
    const h = 2 * M + items.length * rh + (items.length - 1) * gap;
    if (h > maxH && !clip) return null;
    const out = empty(size, h);
    const x0 = (FRAME_W - (t.deepest * step + bw)) / 2;
    const nodeOf = new Map<number, DNode>();
    t.order.forEach((i, row) => {
      const node: DNode = { x: x0 + t.depth[i] * step, y: M + row * (rh + gap), w: bw, h: rh, item: i, shape: "box", step: items[i].step, focus: !!items[i].focus };
      nodeOf.set(i, node);
      out.nodes.push(node);
      out.texts.push(labelIn(node, [clipLine(items[i].label, bw - 2 * PADX, size)], size, items[i], i));
    });
    for (const p of t.order)
      for (const c of t.kids[p]) {
        const [a, b] = [nodeOf.get(p)!, nodeOf.get(c)!];
        out.wires.push(wire([[a.x + 2, a.y + a.h], [a.x + 2, b.y + b.h / 2], [b.x, b.y + b.h / 2]], Math.max(a.step, b.step), c, { arrow: false }));
      }
    return out;
  };
}

// ---------------------------------------------------------------- loop

/** A cycle: stations round an oval, clockwise from the top, each arc ending on the next
 * station's edge. An optional name in the middle says what the cycle is. */
function loopAttempts(spec: DiagramSpec): Attempt[] {
  return [26, 24, 22, 21, 20, 18.5, 17.5].map((bw) => (size, clip, maxH) => {
    const items = spec.items;
    const n = items.length;
    const lines = fitLabels(items.map((i) => i.label), bw - 2 * PADX, 3, size, clip);
    if (!lines) return null;
    const bh = Math.max(6.5, linesH(most(lines), size) + 2 * PADY);
    const rx = (FRAME_W - 2 * M - bw) / 2;
    const ryMax = Math.min(rx, (maxH - 2 * M - bh) / 2);
    const turn = (2 * Math.PI) / n;
    for (let ry = Math.min(14, ryMax); ry <= ryMax + 1e-6; ry += 1.5) {
      for (const start of n === 2 ? [Math.PI] : [-Math.PI / 2, -Math.PI / 2 + turn / 2]) {
        const h = 2 * M + 2 * ry + bh;
        const [cx, cy] = [FRAME_W / 2, h / 2];
        const on = (a: number): Pt => [cx + rx * Math.cos(a), cy + ry * Math.sin(a)];
        const boxes = items.map((_, i) => { const [x, y] = on(start + i * turn); return { x: x - bw / 2, y: y - bh / 2, w: bw, h: bh }; });
        if (boxes.some((a, i) => boxes.some((b, j) => j > i && !apart(a, b, 2.5)))) continue;
        const arcs = boxes.map((a, i) => arc(on, start + i * turn, turn, a, boxes[(i + 1) % n]));
        if (arcs.some((p) => !p)) continue;
        const out = empty(size, h);
        items.forEach((it, i) => {
          const node: DNode = { ...boxes[i], item: i, shape: "box", step: it.step, focus: !!it.focus };
          out.nodes.push(node);
          out.texts.push(labelIn(node, lines[i], size, it, i));
        });
        arcs.forEach((points, i) => {
          const j = (i + 1) % n;
          out.wires.push(wire(points!, Math.max(items[i].step, items[j].step), j, { curve: true }));
        });
        const center = String(spec.center ?? "").trim();
        if (center) {
          const [roomW, roomH] = [2 * rx - bw - 5, 2 * ry - bh - 5];
          const cl = wrap(center, roomW, size, 3) ?? (clip ? wrapClipped(center, Math.max(4, roomW), size, 2) : null);
          if (!cl || linesH(cl.length, size) > roomH) {
            if (!clip) continue;
          } else out.texts.push(block(cl, size, cx, cy - linesH(cl.length, size) / 2, "middle", "center", 0, false, null));
        }
        return out;
      }
    }
    return null;
  });
}

/** The stretch of the oval between two stations, from the edge of one to the edge of the next. */
function arc(on: (a: number) => Pt, from: number, sweep: number, a: Rect, b: Rect): Pt[] | null {
  const N = 72;
  const pts: Pt[] = [];
  for (let k = 0; k <= N; k++) pts.push(on(from + (sweep * k) / N));
  const inside = (p: Pt, r: Rect, pad: number) => p[0] > r.x - pad && p[0] < r.x + r.w + pad && p[1] > r.y - pad && p[1] < r.y + r.h + pad;
  let i0 = 0;
  while (i0 <= N && inside(pts[i0], a, 0.7)) i0++;
  let i1 = N;
  while (i1 >= 0 && inside(pts[i1], b, 0.9)) i1--;
  if (i1 - i0 < 2) return null;
  const kept = pts.slice(i0, i1 + 1);
  let len = 0;
  for (let k = 1; k < kept.length; k++) len += Math.hypot(kept[k][0] - kept[k - 1][0], kept[k][1] - kept[k - 1][1]);
  if (len < 3) return null;
  // A handful of points is plenty for a smooth curve.
  const stride = Math.max(1, Math.floor(kept.length / 8));
  const thin = kept.filter((_, k) => k % stride === 0);
  if (thin[thin.length - 1] !== kept[kept.length - 1]) thin.push(kept[kept.length - 1]);
  return thin;
}

function apart(a: Rect, b: Rect, gap: number): boolean {
  return a.x + a.w + gap <= b.x || b.x + b.w + gap <= a.x || a.y + a.h + gap <= b.y || b.y + b.h + gap <= a.y;
}

// ---------------------------------------------------------------- flowchart

interface Cell { item: number; rank: number; w: number; x: number; ups: number[]; downs: number[] }
interface Seg { a: number; b: number; edge: number; xs: number; xt: number; track: number; w: number }

/** A decision with branches, top to bottom. Items sit on levels (as far down as their longest
 * way from the start); a link that skips a level gets its own lane through it. The shape says
 * what an item is: rounded ends for where it starts and stops, pointed ends for a choice. */
function flowchartAttempts(spec: DiagramSpec): Attempt[] {
  const items = spec.items;
  const n = items.length;
  const at = new Map(items.map((it, i) => [it.id, i]));
  const edges: { u: number; v: number; label: string }[] = [];
  const have = new Set<string>();
  for (const l of spec.links) {
    const [u, v] = [at.get(String(l.from)), at.get(String(l.to))];
    if (u == null || v == null || u === v || have.has(`${u}>${v}`) || have.has(`${v}>${u}`)) continue;
    have.add(`${u}>${v}`);
    edges.push({ u, v, label: String(l.label ?? "").trim() });
  }
  // A flowchart runs one way: a link that would loop back is left out.
  const outOf: number[][] = items.map(() => []);
  edges.forEach((e, k) => outOf[e.u].push(k));
  const state = new Array<number>(n).fill(0);
  const keep = edges.map(() => true);
  const visit = (u: number) => {
    state[u] = 1;
    for (const k of outOf[u]) {
      const v = edges[k].v;
      if (state[v] === 1) keep[k] = false;
      else if (state[v] === 0) visit(v);
    }
    state[u] = 2;
  };
  const entered = new Set(edges.map((e) => e.v));
  for (let i = 0; i < n; i++) if (!entered.has(i) && state[i] === 0) visit(i);
  for (let i = 0; i < n; i++) if (state[i] === 0) visit(i);
  const E = edges.filter((_, k) => keep[k]);
  const dropped = edges.length - E.length;

  // Levels: the longest way down from a start.
  const rank = new Array<number>(n).fill(0);
  const waiting = new Array<number>(n).fill(0);
  E.forEach((e) => waiting[e.v]++);
  const queue = items.map((_, i) => i).filter((i) => !waiting[i]);
  for (let q = 0; q < queue.length; q++)
    for (const e of E) if (e.u === queue[q]) { rank[e.v] = Math.max(rank[e.v], rank[e.u] + 1); if (!--waiting[e.v]) queue.push(e.v); }
  const R = Math.max(...rank) + 1;

  // Cells: one per item, plus a lane on every level a long link passes through.
  const cells: Cell[] = items.map((_, i) => ({ item: i, rank: rank[i], w: 0, x: 0, ups: [], downs: [] }));
  const segs: Seg[] = [];
  E.forEach((e, k) => {
    let prev = e.u;
    for (let r = rank[e.u] + 1; r < rank[e.v]; r++) {
      cells.push({ item: -1, rank: r, w: 0, x: 0, ups: [], downs: [] });
      segs.push({ a: prev, b: cells.length - 1, edge: k, xs: 0, xt: 0, track: -1, w: 0 });
      prev = cells.length - 1;
    }
    segs.push({ a: prev, b: e.v, edge: k, xs: 0, xt: 0, track: -1, w: 0 });
  });
  for (const s of segs) { cells[s.a].downs.push(s.b); cells[s.b].ups.push(s.a); }
  const ranks: number[][] = Array.from({ length: R }, () => []);
  cells.forEach((c, i) => ranks[c.rank].push(i));
  // Order each level by where its neighbours are, a few passes down and up, to untangle links.
  const pos = new Array<number>(cells.length).fill(0);
  const index = (r: number) => ranks[r].forEach((c, i) => (pos[c] = (i + 0.5) / ranks[r].length));
  for (let r = 0; r < R; r++) index(r);
  const sortBy = (r: number, pick: (c: Cell) => number[]) => {
    const key = new Map(ranks[r].map((c) => { const nb = pick(cells[c]); return [c, nb.length ? nb.reduce((s, p) => s + pos[p], 0) / nb.length : pos[c]]; }));
    ranks[r] = ranks[r].map((c, i) => ({ c, i })).sort((p, q) => key.get(p.c)! - key.get(q.c)! || p.i - q.i).map((p) => p.c);
    index(r);
  };
  for (let pass = 0; pass < 3; pass++) {
    for (let r = 1; r < R; r++) sortBy(r, (c) => c.ups);
    if (pass < 2) for (let r = R - 2; r >= 0; r--) sortBy(r, (c) => c.downs);
  }

  const exits = items.map((_, i) => cells[i].downs.length);
  const enters = items.map((_, i) => cells[i].ups.length);
  const shape = (i: number): DNode["shape"] => (exits[i] >= 2 ? "decision" : !exits[i] || !enters[i] ? "pill" : "box");
  const [GX, LANE] = [4, 1.5];

  // The second try is the same drawing with less air in it, for the tallest charts.
  return [false, true].map((tight) => (size, clip, maxH) => {
    let bw = 30;
    for (const ids of ranks) {
      const real = ids.filter((c) => cells[c].item >= 0).length;
      if (real) bw = Math.min(bw, (FRAME_W - 2 * M - GX * (ids.length - 1) - LANE * (ids.length - real)) / real);
    }
    if (bw < 14 && !clip) return null;
    bw = Math.max(6, bw);
    const room = items.map((_, i) => bw - 2 * PADX - (shape(i) === "decision" ? 2 * POINT : 0));
    const lines = fitLabels(items.map((i) => i.label), room, 3, size, clip);
    if (!lines) return null;
    const bh = Math.max(tight ? 5.6 : 6.2, linesH(most(lines), size) + 2 * (tight ? 0.75 : PADY));
    for (const c of cells) c.w = c.item >= 0 ? bw : LANE;

    // Across: each cell under its parents (children of one parent fan out round it), then
    // pushed apart and kept inside the frame.
    const between = (a: number, b: number) => (cells[a].w + cells[b].w) / 2 + GX;
    ranks.forEach((ids, r) => {
      if (r === 0) {
        let x = 0;
        ids.forEach((c, i) => { x += i ? between(ids[i - 1], c) : 0; cells[c].x = x; });
        const shift = FRAME_W / 2 - x / 2;
        ids.forEach((c) => (cells[c].x += shift));
      } else {
        const want = (c: number) => {
          const ups = cells[c].ups;
          return ups.reduce((s, p) => { const sibs = cells[p].downs.slice().sort((a, b) => pos[a] - pos[b]); return s + cells[p].x + (sibs.indexOf(c) - (sibs.length - 1) / 2) * (bw + GX); }, 0) / ups.length;
        };
        ids.forEach((c, i) => (cells[c].x = Math.max(want(c), i ? cells[ids[i - 1]].x + between(ids[i - 1], c) : -Infinity)));
        for (let i = ids.length - 1; i >= 0; i--) cells[ids[i]].x = Math.min(cells[ids[i]].x, i === ids.length - 1 ? FRAME_W - M - cells[ids[i]].w / 2 : cells[ids[i + 1]].x - between(ids[i], ids[i + 1]));
        ids.forEach((c, i) => (cells[c].x = Math.max(cells[c].x, i ? cells[ids[i - 1]].x + between(ids[i - 1], c) : M + cells[c].w / 2)));
      }
    });

    // Where each link leaves and lands: its own point on the edge, in the order of where it goes.
    const flat = (c: number): [number, number] => { // the straight part of a cell's top and bottom edge
      const cell = cells[c];
      if (cell.item < 0) return [cell.x, cell.x];
      const cutIn = shape(cell.item) === "decision" ? POINT + 0.6 : shape(cell.item) === "pill" ? 1.6 : 0.8;
      return [cell.x - cell.w / 2 + cutIn, cell.x + cell.w / 2 - cutIn];
    };
    const fan = (c: number, list: Seg[], set: (s: Seg, x: number) => void) => {
      const [x0, x1] = flat(c);
      list.forEach((s, k) => set(s, x0 + ((x1 - x0) * (k + 1)) / (list.length + 1)));
    };
    cells.forEach((_, c) => {
      fan(c, segs.filter((s) => s.a === c).sort((p, q) => cells[p.b].x - cells[q.b].x), (s, x) => (s.xs = x));
      fan(c, segs.filter((s) => s.b === c).sort((p, q) => cells[p.a].x - cells[q.a].x), (s, x) => (s.xt = x));
    });
    for (const s of segs) {
      // An only link between two cells that overlap goes straight down.
      if (cells[s.a].downs.length !== 1 || cells[s.b].ups.length !== 1) continue;
      const [a0, a1] = flat(s.a);
      const [b0, b1] = flat(s.b);
      const [lo, hi] = [Math.max(a0, b0), Math.min(a1, b1)];
      if (hi - lo >= 0 && (cells[s.a].item < 0 || cells[s.b].item < 0 || hi - lo >= 3)) s.xs = s.xt = cells[s.a].item < 0 ? cells[s.a].x : cells[s.b].item < 0 ? cells[s.b].x : (lo + hi) / 2;
    }

    // Down: each space between levels has room for the yes/no beside a link, one track per
    // link that turns a corner there, and the last straight run into the next box.
    const [ROW, TRACK, RUN] = [linesH(1, SMALL_TEXT) + 0.4, 1.2, tight ? 1.9 : 2.2];
    const notes: string[] = [];
    const tops: number[] = [];
    const gaps: { segs: Seg[]; bends: Seg[]; label: number; tags: Tag[] }[] = [];
    let y = M;
    for (let r = 0; r < R; r++) {
      tops.push(y);
      const here = segs.filter((s) => cells[s.a].rank === r);
      const bends = here.filter((s) => Math.abs(s.xs - s.xt) > 0.05);
      for (const s of here) {
        // A hair's difference is a straight line, not a slant.
        if (!bends.includes(s)) cells[s.b].item < 0 ? (s.xs = s.xt) : (s.xt = s.xs);
        s.w = cells[s.b].w;
      }
      assignTracks(bends);
      const tags = placeTags(here.filter((s) => cells[s.a].item >= 0 && E[s.edge].label).map((s) => ({ seg: s, text: E[s.edge].label })), here, notes);
      const rows = Math.max(0, ...tags.filter((t) => !t.low).map((t) => t.row + 1));
      const label = rows ? rows * ROW + 0.3 : 0.8;
      gaps.push({ segs: here, bends, label, tags });
      y += bh + Math.max(tight ? 3.8 : 4.4, label + bends.length * TRACK + (tags.some((t) => t.low) ? ROW + 0.5 : RUN));
    }
    const h = tops[R - 1] + bh + M;
    if (h > maxH && !clip) return null;
    const trackY = (r: number, s: Seg) => tops[r] + bh + gaps[r].label + (s.track + 0.5) * TRACK;

    const out = empty(size, h);
    out.notes.push(...notes);
    if (dropped) out.notes.push(`left out ${dropped} link${dropped > 1 ? "s" : ""} that looped back`);
    items.forEach((it, i) => {
      const c = cells[i];
      const node: DNode = { x: c.x - bw / 2, y: tops[c.rank], w: bw, h: bh, item: i, shape: shape(i), step: it.step, focus: !!it.focus };
      out.nodes.push(node);
      out.texts.push(labelIn(node, lines[i], size, it, i));
    });
    E.forEach((e, k) => {
      const points: Pt[] = [];
      for (const s of segs.filter((s) => s.edge === k)) {
        const r = cells[s.a].rank;
        points.push([s.xs, cells[s.a].item < 0 ? tops[r] : tops[r] + bh], [s.xs, tops[r] + bh]);
        if (s.track >= 0) points.push([s.xs, trackY(r, s)], [s.xt, trackY(r, s)]);
        points.push([s.xt, tops[r + 1]]);
      }
      out.wires.push(wire(points, Math.max(items[e.u].step, items[e.v].step), e.v));
    });

    gaps.forEach((g, r) => {
      for (const t of g.tags) {
        const e = E[t.seg.edge];
        const top = t.low ? tops[r + 1] - 0.5 - linesH(1, SMALL_TEXT) : tops[r] + bh + 0.35 + t.row * ROW;
        out.texts.push(block([t.text], SMALL_TEXT, t.x, top, t.dir > 0 ? "start" : "end", "link", Math.max(items[e.u].step, items[e.v].step), false, null));
      }
    });
    return out;
  });
}

interface Tag { seg: Seg; text: string; x: number; dir: 1 | -1; row: number; low: boolean }

/** Where the yes/no on each link goes, in one space between two levels. Beside where the link
 * leaves, on the side it is heading; if another link or label is in the way, the other side; then
 * a second row; then beside where it lands. Only when none of those is free is it cut short. */
function placeTags(wanted: { seg: Seg; text: string }[], here: Seg[], notes: string[]): Tag[] {
  const tags: Tag[] = [];
  const pad = 0.9;
  const room = (x: number, dir: 1 | -1, lines: number[], row: number, low: boolean): number => {
    let edge = dir > 0 ? FRAME_W - M : M;
    for (const v of lines) if (dir > 0 ? v > x + 0.01 : v < x - 0.01) edge = dir > 0 ? Math.min(edge, v - 0.7) : Math.max(edge, v + 0.7);
    for (const t of tags) {
      if (t.low !== low || t.row !== row) continue;
      const w = textWidth(t.text, SMALL_TEXT);
      const [x0, x1] = t.dir > 0 ? [t.x, t.x + w] : [t.x - w, t.x];
      if (dir > 0 ? x1 > x : x0 < x) edge = dir > 0 ? Math.min(edge, x0 - 0.8) : Math.max(edge, x1 + 0.8);
    }
    return dir > 0 ? edge - (x + pad) : x - pad - edge;
  };
  const leaves = here.map((s) => s.xs);
  const lands = here.map((s) => s.xt);
  for (const { seg, text } of wanted.slice().sort((p, q) => p.seg.xs - q.seg.xs)) {
    const need = textWidth(text, SMALL_TEXT);
    const first: 1 | -1 = seg.xt < seg.xs - 0.05 ? -1 : 1;
    const spots: { x: number; dir: 1 | -1; row: number; low: boolean; room: number }[] = [];
    for (const row of [0, 1]) for (const dir of [first, -first] as (1 | -1)[]) spots.push({ x: seg.xs, dir, row, low: false, room: room(seg.xs, dir, leaves, row, false) });
    for (const dir of [first, -first] as (1 | -1)[]) spots.push({ x: seg.xt, dir, row: 0, low: true, room: room(seg.xt, dir, lands, 0, true) });
    const spot = spots.find((s) => s.room >= need) ?? spots.slice(0, 2).sort((p, q) => q.room - p.room)[0];
    if (spot.room < textWidth("no", SMALL_TEXT)) {
      notes.push(`no room for the label "${text}"`);
      continue;
    }
    tags.push({ seg, text: clipLine(text, spot.room, SMALL_TEXT), x: spot.x + spot.dir * pad, dir: spot.dir, row: spot.row, low: spot.low });
  }
  return tags;
}

/** Give each link that turns a corner between two levels its own height to turn at, in the
 * order that crosses the fewest other links and never runs one along another. */
function assignTracks(bends: Seg[]): void {
  const k = bends.length;
  if (!k) return;
  const cost = (order: number[]): number => {
    let c = 0;
    for (let i = 0; i < k; i++)
      for (let j = 0; j < k; j++) {
        if (i === j) continue;
        const [a, b] = [bends[i], bends[j]];
        const [lo, hi] = [Math.min(a.xs, a.xt), Math.max(a.xs, a.xt)];
        const aboveB = order[i] < order[j]; // a turns higher up than b
        // b's way down from its box crosses a's run across when a turns above where b turns.
        if (b.xs > lo + 0.05 && b.xs < hi - 0.05 && aboveB) c += 1;
        // b's way into its target crosses a's run across when a turns below where b turns.
        if (b.xt > lo + 0.05 && b.xt < hi - 0.05 && !aboveB) c += 1;
        // a leaves where b lands: the two runs would lie on each other unless b lands lower.
        if (Math.abs(a.xs - b.xt) < 0.8 && !aboveB) c += 100;
      }
    return c;
  };
  let best = bends.map((_, i) => i);
  let bestCost = cost(best);
  const order = best.slice();
  const permute = (at: number) => {
    if (at === k) {
      const c = cost(order);
      if (c < bestCost) [best, bestCost] = [order.slice(), c];
      return;
    }
    for (let i = at; i < k; i++) {
      [order[at], order[i]] = [order[i], order[at]];
      permute(at + 1);
      [order[at], order[i]] = [order[i], order[at]];
    }
  };
  if (k <= 7) permute(0);
  bends.forEach((s, i) => (s.track = best[i]));
  // Two links that swap sides (each leaves where the other lands) cannot both win: move one
  // landing a little along its box, toward where that link comes from.
  for (const a of bends)
    for (const b of bends)
      if (a !== b && Math.abs(a.xs - b.xt) < 0.8 && a.track > b.track && b.w > 6) b.xt = a.xs + (b.xs >= b.xt ? 1.1 : -1.1);
}

const ATTEMPTS: Record<string, (spec: DiagramSpec) => Attempt[]> = {
  steps: stepsAttempts,
  flowchart: flowchartAttempts,
  timeline: timelineAttempts,
  comparison: comparisonAttempts,
  tree: treeAttempts,
  loop: loopAttempts,
};

// ---------------------------------------------------------------- inking order

export type Mark = { kind: "node" | "text" | "wire"; index: number };

/** Start time of everything a step draws, and when that step's ink is dry. A link inks in
 * first, then the box it leads to, then that box's words: the eye follows the idea. */
export function diagramTimeline(l: DiagramLayout, step: number): { delays: Map<string, number>; total: number } {
  const marks: { key: string; at: number; dur: number }[] = [];
  const add = (kind: Mark["kind"], index: number, at: number, dur: number) => marks.push({ key: `${kind}${index}`, at, dur });
  l.wires.forEach((w, i) => {
    if (w.step !== step) return;
    let len = 0;
    for (let k = 1; k < w.points.length; k++) len += Math.hypot(w.points[k][0] - w.points[k - 1][0], w.points[k][1] - w.points[k - 1][1]);
    add("wire", i, w.to == null ? -1 : w.to - 0.5, 0.35 + Math.min(0.5, len / 90));
  });
  l.nodes.forEach((n, i) => n.step === step && add("node", i, n.item, n.shape === "dot" ? 0.3 : 0.5));
  l.texts.forEach((t, i) => t.step === step && add("text", i, t.item == null ? -0.8 : t.item + 0.25, 0.3 + Math.min(0.5, t.lines.join(" ").length * 0.025)));
  marks.sort((a, b) => a.at - b.at);
  const delays = new Map<string, number>();
  let t = 0.1;
  let end = 0;
  for (const m of marks) {
    delays.set(m.key, t);
    end = Math.max(end, t + m.dur);
    t += Math.min(m.dur * 0.6, 0.4);
  }
  return { delays, total: end + 0.25 };
}
