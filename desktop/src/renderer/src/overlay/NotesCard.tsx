/**
 * Field notes: a glass notebook card beside what Pyxi is explaining. Each point writes itself
 * in ink the moment Pyxi says it, frames the exact thing it's about with a hand-drawn dashed
 * outline numbered to match, and a thread of light ties the two together, so you never have to
 * guess which paragraph a point means. Sketches draw straight onto the real content. When the
 * answer is a process or a structure, a small hand-drawn diagram builds inside the card, each
 * stroke inking in with the point it belongs to.
 */
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { rectToLocal, toLocal } from "../shared/bridge";
import { Close } from "../shared/icons";
import type { NoteCard, NoteItem, Sketch, SketchShape } from "../shared/protocol";
import { drawShape, fitView, placeText, timeline } from "../scene/sketchPaths";
import { Interactive } from "./Interactive";
import "./notesCard.css";
import { useOverlay, voice } from "./store";

const CARD_W = 372; // keep in step with CARD_W in src/pyxi/companion/notes.py
const MIN_W = 290;
const PAD_X = 24; // the card's side padding (notesCard.css)

/** A process or structure drawn small inside the card (src/pyxi/companion/notes.py NoteDiagram). */
interface Diagram {
  caption: string | null;
  shapes: SketchShape[];
}
type CardWithDiagram = NoteCard & { diagram?: Diagram | null };

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
  const cardEl = useRef<HTMLDivElement>(null);
  const [cardH, setCardH] = useState(0);
  const [reading, setReading] = useState(false); // the pointer is on the card
  const [leaving, setLeaving] = useState(false);
  const idleSince = useRef(0);

  // Once Pyxi has finished explaining, the notes stay a little while to read, then fade.
  // Hovering the card keeps it; anything that changes the screen clears it sooner.
  useEffect(() => {
    if (!notes) return;
    setLeaving(false);
    idleSince.current = 0;
    const linger = Math.min(22000, 7000 + notes.card.items.length * 1800);
    const id = window.setInterval(() => {
      const s = useOverlay.getState();
      if (reading || s.state !== "idle" || voice.busy) {
        idleSince.current = 0;
        setLeaving(false);
        return;
      }
      if (!idleSince.current) idleSince.current = performance.now();
      const idle = performance.now() - idleSince.current;
      if (idle > linger) close();
      else if (idle > linger - 700) setLeaving(true);
    }, 250);
    return () => window.clearInterval(id);
  }, [notes?.card.id, reading, close]);

  // Measure the real card, so it is never pushed off the bottom of the screen.
  useLayoutEffect(() => {
    const el = cardEl.current;
    if (!el) return;
    const measure = () => setCardH((el.parentElement ?? el).offsetHeight); // the whole card, padding included
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    measure();
    return () => ro.disconnect();
  }, [notes?.card.id]);

  const placed = useMemo(() => {
    if (!notes) return null;
    const region = notes.card.region ? rectToLocal(notes.card.region) : null;
    if (notes.card.place) {
      // The brain picked the emptiest spot on screen; just keep it on this display.
      const [px, py] = toLocal(notes.card.place[0], notes.card.place[1]);
      return { left: Math.min(Math.max(12, px), innerWidth - CARD_W - 12), top: Math.min(Math.max(12, py), innerHeight - 200), width: CARD_W };
    }
    if (!region) return { left: innerWidth - CARD_W - 32, top: 90, width: CARD_W };
    // Beside the content, never on top of it: the side with more room, narrowed to fit if needed,
    // else below (or above) it.
    const gap = 26;
    const roomLeft = region.x - gap - 12;
    const roomRight = innerWidth - (region.x + region.w) - gap - 12;
    const top = Math.min(Math.max(70, region.y - 10), innerHeight - 460);
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
    if (region.y - gap > 300) return { left, top: Math.max(16, region.y - gap - 360), width: CARD_W };
    return { left: innerWidth - CARD_W - 16, top, width: CARD_W };
    // The spot too: a card that is still arriving is moved by the brain when a later point
    // marks something under where it first sat (live, it covered the header it explained).
  }, [notes?.card.id, notes?.card.place?.[0], notes?.card.place?.[1]]);

  // Where each written point's bullet sits, so threads can start there.
  useLayoutEffect(() => {
    if (!notes) return;
    const next: Record<number, Pt> = {};
    for (const n of notes.revealed) {
      const el = rows.current.get(n);
      if (!el) continue;
      const r = el.getBoundingClientRect();
      next[n] = [r.left + 10, r.top + 18];
    }
    setAnchors(next);
  }, [notes?.revealed.join(","), notes?.card.id, cardH]);

  if (notes?.card.style === "picture") return <PictureTags items={notes.card.items} revealed={notes.revealed} id={notes.card.id} leaving={leaving} />;
  if (!notes || !placed) return null;
  const { card, revealed } = notes;
  const shown = card.items.filter((i) => revealed.includes(i.n));
  const sketches = card.sketches.filter((s) => s.after === 0 || revealed.includes(s.after));
  const cardRight = placed.left + placed.width;
  const top = cardH ? Math.max(12, Math.min(placed.top, innerHeight - cardH - 14)) : placed.top;

  return (
    <>
      <svg className={`notes-ink ${leaving ? "leaving" : ""}`} width="100%" height="100%">
        {shown.map((item) => item.rect && item.mark !== "none" && (
          <Mark key={`${card.id}-${item.n}`} item={item} seed={hash(card.id) + item.n} from={anchors[item.n]} cardLeft={placed.left} cardRight={cardRight} />
        ))}
        {sketches.map((s, i) => <SketchMark key={`${card.id}-s${i}`} sketch={s} seed={hash(card.id) + 97 * i} />)}
      </svg>
      <Interactive
        className={`notes-card notes-lg ${leaving ? "leaving" : ""}`}
        style={{ left: placed.left, top, width: placed.width, maxHeight: innerHeight - 24 }}
        onEnter={() => setReading(true)}
        onLeave={() => setReading(false)}
      >
        <div ref={cardEl} className="notes-measure">
        <button className="notes-close" onClick={close} aria-label="Close notes"><Close /></button>
        <div className="notes-title">{card.title}</div>
        <div className="notes-rule" />
        {(card as CardWithDiagram).diagram?.shapes.length ? (
          <NoteDiagram key={card.id} diagram={(card as CardWithDiagram).diagram!} revealed={revealed} width={placed.width - PAD_X * 2} />
        ) : null}
        {card.items.map((item) => (
          <div
            key={item.n}
            ref={(el) => { if (el) rows.current.set(item.n, el); }}
            className={`note-row kind-${item.kind} ${revealed.includes(item.n) ? "written" : ""}`}
          >
            <span className="note-bullet">
              {item.kind === "step" ? <b>{item.n}</b> : (
                <svg width="12" height="19" viewBox="0 0 12 22"><path d="M1.5 11a4.5 4.5 0 1 0 9 0a4.5 4.5 0 1 0 -9 0Z" pathLength={1} /></svg>
              )}
            </span>
            <span className="note-text">{item.text}</span>
            {item.rect && item.mark !== "none" && item.kind !== "step" && <span className="note-num">{item.n}</span>}
          </div>
        ))}
        </div>
      </Interactive>
    </>
  );
}

const WEIGHT = { thin: 1.3, normal: 2, bold: 2.9 };
const SHAPE_DEFAULTS: Omit<SketchShape, "kind"> = {
  points: [], at: null, size: null, text: null, color: "ink", fill: false, weight: "normal", dashed: false, flow: false, step: 0, ask: null,
};

/**
 * The card's diagram: sketchboard shapes, cropped to what was drawn. A shape appears with its
 * point (step 0 is there from the start); shapes that arrive together ink in one after another.
 */
function NoteDiagram({ diagram, revealed, width }: { diagram: Diagram; revealed: number[]; width: number }) {
  const scale = width / 100; // grid units to px, before the crop zooms in
  // A missing field must never take the whole overlay down with it.
  const shapes = useMemo(() => diagram.shapes.map((s) => ({ ...SHAPE_DEFAULTS, ...s, points: s.points ?? [] })), [diagram]);
  // As tall as the drawing needs (a row of boxes is a short strip, a hierarchy is taller).
  const height = useMemo(() => {
    const [w, h] = extent(shapes);
    return Math.round(width * Math.min(0.56, Math.max(0.3, (h + 10) / (w + 10))));
  }, [shapes, width]);
  const view = useMemo(() => fitView(shapes, scale, width, height, 1.5), [shapes, scale, width, height]);
  const viewW = Number(view.split(" ")[2]) || width;
  const minText = (13 * viewW) / width; // labels never below 13 px on screen
  const delays = useMemo(() => {
    const out = new Map<number, number>();
    for (const step of new Set(shapes.map((s) => s.step))) {
      for (const [i, d] of timeline(shapes, step, scale).delays) out.set(i, d + (step === 0 ? 0.3 : 0.15));
    }
    return out;
  }, [shapes, scale]);
  const shown = (s: SketchShape) => s.step === 0 || revealed.includes(s.step);
  return (
    <div className="note-diagram">
      <svg className="note-diagram-art" width={width} height={height} viewBox={view}>
        {shapes.map((s, i) => (shown(s) ? <DiagramStroke key={i} shape={s} index={i} scale={scale} delay={delays.get(i) ?? 0} minText={minText} /> : null))}
      </svg>
      {diagram.caption && <div className="note-diagram-caption">{diagram.caption}</div>}
    </div>
  );
}

/** Width and height of what the shapes cover, in grid units. */
function extent(shapes: SketchShape[]): [number, number] {
  let [x0, y0, x1, y1] = [Infinity, Infinity, -Infinity, -Infinity];
  const add = (x: number, y: number) => {
    [x0, y0, x1, y1] = [Math.min(x0, x), Math.min(y0, y), Math.max(x1, x), Math.max(y1, y)];
  };
  for (const s of shapes) {
    s.points.forEach(([x, y]) => add(x, y));
    if (s.at) add(s.at[0], s.at[1]);
    if (s.at && s.size) add(s.at[0] + s.size[0], s.at[1] + s.size[1]);
  }
  return Number.isFinite(x0) ? [x1 - x0, y1 - y0] : [100, 64];
}

function DiagramStroke({ shape, index, scale, delay, minText }: { shape: SketchShape; index: number; scale: number; delay: number; minText: number }) {
  const ink = `var(--sk-${shape.color})`;
  const t = (extra = 0) => ({ animationDelay: `${(delay + extra).toFixed(2)}s` });
  const w = WEIGHT[shape.weight] * Math.max(0.8, scale / 4.2);
  if (shape.kind === "text" || shape.kind === "equation") {
    const p = placeText(shape, scale);
    if (!p) return null;
    const eq = shape.kind === "equation";
    return (
      <g className={`sk-text ${eq ? "eq" : ""}`}>
        <text x={p.x} y={p.y} style={{ ...t(), fontSize: Math.max(p.size, minText), fill: eq ? "var(--sk-amber)" : ink }}>{shape.text}</text>
      </g>
    );
  }
  const drawn = drawShape(shape, scale, index * 7919 + 17);
  if (!drawn) return null;
  const dur = `${Math.min(1.2, 0.45 + drawn.length / scale / 110).toFixed(2)}s`;
  return (
    <g className="sk-shape">
      {shape.fill && drawn.closed && <path className="sk-fill" d={drawn.d} style={{ ...t(0.5), fill: ink }} />}
      <path className={`sk-line ${shape.dashed ? "dashed" : ""}`} d={drawn.d} pathLength={1} style={{ ...t(), animationDuration: dur, stroke: ink, strokeWidth: w }} />
      {drawn.head && <path className="sk-line" d={drawn.head} pathLength={1} style={{ ...t(0.45), stroke: ink, strokeWidth: w }} />}
      {shape.flow && (
        <path className="sk-flow" d={drawn.d}
          style={{ ...t(0.6), stroke: ink, color: ink, strokeWidth: w * 1.4, strokeDasharray: `${(scale * 1.2).toFixed(1)} ${(scale * 3.2).toFixed(1)}`, ["--period" as string]: `${(scale * 4.4).toFixed(1)}px` }} />
      )}
    </g>
  );
}

/**
 * Explaining a picture by writing on it: each point is a small tag pinned to its part with a
 * leader line, and a route (blood, air, a signal) traces itself along the picture as it's said.
 */
function PictureTags({ items, revealed, id, leaving }: { items: NoteItem[]; revealed: number[]; id: string; leaving: boolean }) {
  const shown = items.filter((i) => revealed.includes(i.n) && i.at);
  const latest = revealed[revealed.length - 1];
  return (
    <>
      <svg className={`notes-ink pic-ink ${leaving ? "leaving" : ""}`} width="100%" height="100%">
        {shown.map((it) => {
          const at = toLocal(it.at![0], it.at![1]) as Pt;
          const tag = it.tag ? (toLocal(it.tag[0], it.tag[1]) as Pt) : ([at[0], at[1] - 70] as Pt);
          const route = (it.trace ?? []).map(([x, y]) => toLocal(x, y) as Pt);
          let head = "";
          if (route.length > 1) {
            const [ex, ey] = route[route.length - 1];
            const [px, py] = route[route.length - 2];
            const a = Math.atan2(ey - py, ex - px);
            head = `M ${ex - 12 * Math.cos(a - 0.5)} ${ey - 12 * Math.sin(a - 0.5)} L ${ex} ${ey} L ${ex - 12 * Math.cos(a + 0.5)} ${ey - 12 * Math.sin(a + 0.5)}`;
          }
          return (
            <g key={`${id}-${it.n}`}>
              {route.length > 1 && (
                <>
                  <path className="pic-route" d={through(route)} pathLength={1} />
                  <path className="pic-route head" d={head} pathLength={1} />
                </>
              )}
              <path className="pic-leader" d={`M ${tag[0]} ${tag[1]} L ${at[0]} ${at[1]}`} pathLength={1} />
              <circle className={`pic-dot ${it.n === latest ? "live" : ""}`} cx={at[0]} cy={at[1]} r={4.5} />
            </g>
          );
        })}
      </svg>
      {shown.map((it) => {
        const at = toLocal(it.at![0], it.at![1]);
        const [x, y] = it.tag ? toLocal(it.tag[0], it.tag[1]) : [at[0], at[1] - 70];
        return (
          <div key={`${id}-t${it.n}`} className={`pic-tag ${leaving ? "leaving" : ""}`} style={{ left: x, top: y }}>
            {it.text}
          </div>
        );
      })}
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
