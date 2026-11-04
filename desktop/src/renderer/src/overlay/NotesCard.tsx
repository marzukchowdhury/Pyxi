/**
 * Field notes: a glass notebook card beside what Wisp is explaining. Each point writes itself
 * in ink the moment Wisp says it, frames the exact thing it's about with a hand-drawn dashed
 * outline numbered to match, and a thread of light ties the two together, so you never have to
 * guess which paragraph a point means. Sketches draw straight onto the real content.
 */
import "@fontsource/caveat/500.css";
import "@fontsource/caveat/700.css";
import { useLayoutEffect, useMemo, useRef, useState } from "react";
import { rectToLocal, toLocal } from "../shared/bridge";
import { Close } from "../shared/icons";
import type { NoteItem, Sketch } from "../shared/protocol";
import { Interactive } from "./Interactive";
import { useOverlay } from "./store";

const CARD_W = 300;
const MIN_W = 230;

type Pt = [number, number];

/** A tiny seeded PRNG, so a frame wobbles the same way on every render. */
function rng(seed: number): () => number {
  let s = seed >>> 0 || 1;
  return () => ((s = Math.imul(s ^ (s >>> 15), 2246822507) ^ Math.imul(s ^ (s >>> 13), 3266489909)) >>> 0) / 4294967296;
}

/** Smooth path through points (Catmull-Rom as cubic Beziers). */
function through(pts: Pt[]): string {
  if (pts.length < 2) return "";
  let d = `M ${pts[0][0].toFixed(1)} ${pts[0][1].toFixed(1)}`;
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[i - 1] ?? pts[i];
    const p1 = pts[i];
    const p2 = pts[i + 1];
    const p3 = pts[i + 2] ?? p2;
    const c1: Pt = [p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6];
    const c2: Pt = [p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6];
    d += ` C ${c1[0].toFixed(1)} ${c1[1].toFixed(1)}, ${c2[0].toFixed(1)} ${c2[1].toFixed(1)}, ${p2[0].toFixed(1)} ${p2[1].toFixed(1)}`;
  }
  return d;
}

/** A hand-drawn rounded rectangle: wobbles a little, and overshoots where the pen started. */
function roughRect(x: number, y: number, w: number, h: number, seed: number): string {
  const r = Math.min(14, w / 4, h / 4);
  const rand = rng(seed);
  const jit = () => (rand() - 0.5) * 2.2;
  const pts: Pt[] = [];
  const corner = (cx: number, cy: number, a0: number) => {
    for (let k = 0; k <= 3; k++) {
      const a = a0 + (k / 3) * (Math.PI / 2);
      pts.push([cx + Math.cos(a) * r + jit(), cy + Math.sin(a) * r + jit()]);
    }
  };
  const edge = (x0: number, y0: number, x1: number, y1: number) => {
    const n = Math.max(1, Math.round(Math.hypot(x1 - x0, y1 - y0) / 70));
    for (let k = 1; k < n; k++) pts.push([x0 + ((x1 - x0) * k) / n + jit(), y0 + ((y1 - y0) * k) / n + jit()]);
  };
  pts.push([x + r + 10, y + jit()]);
  edge(x + r + 10, y, x + w - r, y);
  corner(x + w - r, y + r, -Math.PI / 2);
  edge(x + w, y + r, x + w, y + h - r);
  corner(x + w - r, y + h - r, 0);
  edge(x + w - r, y + h, x + r, y + h);
  corner(x + r, y + h - r, Math.PI / 2);
  edge(x, y + h - r, x, y + r);
  corner(x + r, y + r, Math.PI);
  pts.push([x + r + 22, y + 1.5 + jit()]); // the pen overshoots its start, like a real hand
  return through(pts);
}

function roughEllipse(cx: number, cy: number, rx: number, ry: number, seed: number): string {
  const rand = rng(seed);
  const pts: Pt[] = [];
  for (let k = 0; k <= 14; k++) {
    const a = -Math.PI / 2 + (k / 13) * Math.PI * 2.08;
    const wob = 1 + (rand() - 0.5) * 0.06;
    pts.push([cx + Math.cos(a) * rx * wob, cy + Math.sin(a) * ry * wob]);
  }
  return through(pts);
}

function roughLine(x0: number, y0: number, x1: number, seed: number): string {
  const rand = rng(seed);
  const pts: Pt[] = [];
  for (let k = 0; k <= 6; k++) pts.push([x0 + ((x1 - x0) * k) / 6, y0 + (rand() - 0.5) * 3 + Math.sin(k) * 0.8]);
  return through(pts);
}

export function NotesCard() {
  const notes = useOverlay((s) => s.notes);
  const close = useOverlay((s) => s.closeNotes);
  const rows = useRef(new Map<number, HTMLDivElement>());
  const [anchors, setAnchors] = useState<Record<number, Pt>>({});

  const placed = useMemo(() => {
    if (!notes) return null;
    const region = notes.card.region ? rectToLocal(notes.card.region) : null;
    if (!region) return { left: innerWidth - CARD_W - 32, top: 90, width: CARD_W };
    // Beside the content, never on top of it: the side with more room, narrowed to fit if needed,
    // else below (or above) it.
    const gap = 26;
    const roomLeft = region.x - gap - 12;
    const roomRight = innerWidth - (region.x + region.w) - gap - 12;
    const top = Math.min(Math.max(70, region.y - 10), innerHeight - 380);
    const best = Math.max(roomLeft, roomRight);
    if (best >= MIN_W) {
      const width = Math.min(CARD_W, best);
      return roomLeft >= roomRight
        ? { left: region.x - gap - width, top, width }
        : { left: region.x + region.w + gap, top, width };
    }
    const below = region.y + region.h + gap;
    const left = Math.min(Math.max(16, region.x), innerWidth - CARD_W - 16);
    if (innerHeight - below > 240) return { left, top: below, width: CARD_W };
    if (region.y - gap > 260) return { left, top: Math.max(16, region.y - gap - 300), width: CARD_W };
    return { left: innerWidth - CARD_W - 16, top, width: CARD_W };
  }, [notes?.card.id]);

  // Where each written point's bullet sits, so threads can start there.
  useLayoutEffect(() => {
    if (!notes) return;
    const next: Record<number, Pt> = {};
    for (const n of notes.revealed) {
      const el = rows.current.get(n);
      if (!el) continue;
      const r = el.getBoundingClientRect();
      next[n] = [r.left + 9, r.top + 15];
    }
    setAnchors(next);
  }, [notes?.revealed.join(","), notes?.card.id]);

  if (!notes || !placed) return null;
  const { card, revealed } = notes;
  const shown = card.items.filter((i) => revealed.includes(i.n));
  const sketches = card.sketches.filter((s) => s.after === 0 || revealed.includes(s.after));
  const cardRight = placed.left + placed.width;

  return (
    <>
      <svg className="notes-ink" width="100%" height="100%">
        {shown.map((item) => item.rect && item.mark !== "none" && (
          <Mark key={`${card.id}-${item.n}`} item={item} seed={hash(card.id) + item.n} from={anchors[item.n]} cardLeft={placed.left} cardRight={cardRight} />
        ))}
        {sketches.map((s, i) => <SketchMark key={`${card.id}-s${i}`} sketch={s} seed={hash(card.id) + 97 * i} />)}
      </svg>
      <Interactive className="notes-card" style={{ left: placed.left, top: placed.top, width: placed.width }}>
        <button className="notes-close" onClick={close} aria-label="Close notes"><Close /></button>
        <div className="notes-title">{card.title}</div>
        <div className="notes-rule" />
        {card.items.map((item) => (
          <div
            key={item.n}
            ref={(el) => { if (el) rows.current.set(item.n, el); }}
            className={`note-row kind-${item.kind} ${revealed.includes(item.n) ? "written" : ""}`}
          >
            <span className="note-bullet">
              {item.kind === "step" ? <b>{item.n}</b> : (
                <svg width="10" height="16" viewBox="0 0 12 22"><path d="M6 1 10.5 11 6 21 1.5 11Z" pathLength={1} /></svg>
              )}
            </span>
            <span className="note-text">{item.text}</span>
            {item.rect && item.mark !== "none" && item.kind !== "step" && <span className="note-num">{item.n}</span>}
          </div>
        ))}
      </Interactive>
    </>
  );
}

function hash(s: string): number {
  let h = 2166136261;
  for (const c of s) h = Math.imul(h ^ c.charCodeAt(0), 16777619);
  return h >>> 0;
}

/** The outline around what a point is about, its number, and the thread back to the card. */
function Mark({ item, seed, from, cardLeft, cardRight }: { item: NoteItem; seed: number; from?: Pt; cardLeft: number; cardRight: number }) {
  const r = rectToLocal(item.rect!);
  const pad = 6;
  const x = r.x - pad;
  const y = r.y - pad;
  const w = r.w + pad * 2;
  const h = r.h + pad * 2;
  const d =
    item.mark === "circle" ? roughEllipse(r.x + r.w / 2, r.y + r.h / 2, r.w / 2 + 14, r.h / 2 + 12, seed)
    : item.mark === "underline" ? roughLine(r.x, r.y + r.h + 5, r.x + r.w, seed)
    : roughRect(x, y, w, h, seed);
  // Thread: from the card's edge beside the bullet to the nearest side of the frame.
  const leftOfFrame = !!from && cardRight < x;
  const rightOfFrame = !!from && cardLeft > x + w;
  const sx = leftOfFrame ? cardRight - 4 : cardLeft + 4;
  const tx = leftOfFrame ? x : x + w;
  const ty = Math.min(Math.max(from?.[1] ?? y, y + 8), y + h - 8);
  const mid = (sx + tx) / 2;
  const thread = from && (leftOfFrame || rightOfFrame) ? `M ${sx} ${from[1]} C ${mid} ${from[1]}, ${mid} ${ty}, ${tx} ${ty}` : "";
  const id = `ink-${seed}`;
  return (
    <g className="ink-mark">
      <defs>
        <mask id={id} maskUnits="userSpaceOnUse" x="0" y="0" width="100%" height="100%">
          <path d={d} className="ink-reveal" pathLength={1} />
        </mask>
      </defs>
      <path d={d} className={`ink-line ${item.mark === "frame" ? "dashed" : ""}`} mask={`url(#${id})`} />
      {thread && <path d={thread} className="ink-thread" pathLength={1} />}
      {item.mark === "frame" && (
        <g className="ink-badge" transform={`translate(${x + 2} ${y + 1})`}>
          <circle r="11" />
          <text y="5.5" textAnchor="middle">{item.n}</text>
        </g>
      )}
    </g>
  );
}

function SketchMark({ sketch, seed }: { sketch: Sketch; seed: number }) {
  const pts = sketch.points.map(([px, py]) => toLocal(px, py) as Pt);
  if (!pts.length) return null;
  const [x, y] = pts[0];
  if (sketch.kind === "label") {
    const tilt = (rng(seed)() - 0.5) * 6;
    return <text className="ink-label" x={x} y={y} transform={`rotate(${tilt} ${x} ${y})`}>{sketch.text}</text>;
  }
  if (sketch.kind === "tick")
    return <path className="ink-stroke" d={through([[x - 10, y], [x - 3, y + 8], [x + 13, y - 12]])} pathLength={1} />;
  if (sketch.kind === "cross")
    return (
      <g>
        <path className="ink-stroke" d={`M ${x - 9} ${y - 9} L ${x + 9} ${y + 9}`} pathLength={1} />
        <path className="ink-stroke late" d={`M ${x + 9} ${y - 9} L ${x - 9} ${y + 9}`} pathLength={1} />
      </g>
    );
  const d = pts.length > 1 ? through(pts) : "";
  if (sketch.kind === "arrow" && pts.length > 1) {
    const [ex, ey] = pts[pts.length - 1];
    const [px, py] = pts[pts.length - 2];
    const a = Math.atan2(ey - py, ex - px);
    const head = `M ${ex - 13 * Math.cos(a - 0.45)} ${ey - 13 * Math.sin(a - 0.45)} L ${ex} ${ey} L ${ex - 13 * Math.cos(a + 0.45)} ${ey - 13 * Math.sin(a + 0.45)}`;
    return (
      <g>
        <path className="ink-stroke" d={d} pathLength={1} />
        <path className="ink-stroke late" d={head} pathLength={1} />
        {sketch.text && <text className="ink-label" x={(pts[0][0] + ex) / 2 + 8} y={(pts[0][1] + ey) / 2 - 10}>{sketch.text}</text>}
      </g>
    );
  }
  return (
    <g>
      <path className="ink-stroke" d={d} pathLength={1} />
      {sketch.text && <text className="ink-label" x={pts[pts.length - 1][0] + 10} y={pts[pts.length - 1][1] - 8}>{sketch.text}</text>}
    </g>
  );
}
