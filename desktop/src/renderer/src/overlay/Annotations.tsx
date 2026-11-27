/**
 * Precise, luminous annotations. Geometry is exact (hairlines, true ellipses, rounded frames
 * with viewfinder corners), motion is fast and springy, and light does the emphasis.
 */
import { useEffect, useRef, useState } from "react";
import { bridge, cursorToLocal, rectToLocal, toLocal } from "../shared/bridge";
import { brain } from "../shared/socket";
import { type Box, labelSize, labelSpot } from "./guideMarks";
import { Interactive } from "./Interactive";
import type { Pin } from "../shared/protocol";
import { type LiveAnnotation, useOverlay } from "./store";

function useNow(ms = 200): number {
  const [now, setNow] = useState(performance.now());
  useEffect(() => {
    const t = setInterval(() => setNow(performance.now()), ms);
    return () => clearInterval(t);
  }, [ms]);
  return now;
}

export function Annotations() {
  const annotations = useOverlay((s) => s.annotations);
  const pins = useOverlay((s) => s.pins);
  const now = useNow();
  const live = annotations.filter((a) => now - a.bornAt < a.ttl_s * 1000);
  return (
    <>
      <svg className="annotations" width="100%" height="100%">
        <defs>
          <filter id="lum" x="-30%" y="-30%" width="160%" height="160%">
            <feGaussianBlur stdDeviation="3" result="b" />
            <feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge>
          </filter>
          <linearGradient id="arrow-g" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0" stopColor="#9ecbff" stopOpacity="0.15" />
            <stop offset="1" stopColor="#eaf3ff" stopOpacity="1" />
          </linearGradient>
        </defs>
        {live.map((a) => (
          <g key={a.id} className={`ann ann-${a.kind} ${now - a.bornAt > a.ttl_s * 1000 - 500 ? "leaving" : ""}`}>
            <Shape a={a} />
          </g>
        ))}
      </svg>
      {live.filter((a) => a.label && a.kind !== "arrow").map((a) => <Label key={`l${a.id}`} a={a} />)}
      {pins.map((p) => <PinNote key={p.id} pin={p} />)}
      <Cheer />
    </>
  );
}

function Shape({ a }: { a: LiveAnnotation }) {
  if (a.kind === "point" && a.at) {
    const [x, y] = toLocal(a.at[0], a.at[1]);
    return (
      <g transform={`translate(${x} ${y})`}>
        <circle className="ring-wave" r="8" />
        <circle className="ring-wave delay" r="8" />
        <circle className="ring-core" r="11" />
        <circle className="dot-core" r="2.6" />
      </g>
    );
  }
  if ((a.kind === "highlight" || a.kind === "circle") && a.rect) {
    const r = rectToLocal(a.rect);
    if (a.kind === "circle") {
      const rx = r.w / 2 + Math.max(14, r.w * 0.12);
      const ry = r.h / 2 + Math.max(12, r.h * 0.3);
      return <DrawOn d={ellipsePath(r.x + r.w / 2, r.y + r.h / 2, rx, ry)} className="stroke-lum" />;
    }
    const pad = 6;
    const x = r.x - pad, y = r.y - pad, w = r.w + pad * 2, h = r.h + pad * 2, c = Math.min(14, w / 4, h / 4);
    return (
      <g className="frame-in" style={{ transformOrigin: `${x + w / 2}px ${y + h / 2}px` }}>
        <rect x={x} y={y} width={w} height={h} rx="9" className="frame-fill" />
        <rect x={x} y={y} width={w} height={h} rx="9" className="frame-line" />
        <path className="corners" filter="url(#lum)"
          d={`M${x} ${y + c}V${y + 4}Q${x} ${y} ${x + 4} ${y}H${x + c} M${x + w - c} ${y}H${x + w - 4}Q${x + w} ${y} ${x + w} ${y + 4}V${y + c}
             M${x + w} ${y + h - c}V${y + h - 4}Q${x + w} ${y + h} ${x + w - 4} ${y + h}H${x + w - c} M${x + c} ${y + h}H${x + 4}Q${x} ${y + h} ${x} ${y + h - 4}V${y + h - c}`} />
      </g>
    );
  }
  if (a.kind === "click" && a.rect) return <GuideShape a={a} />;
  if (a.kind === "arrow" && a.at && a.to) {
    const [x1, y1] = toLocal(a.at[0], a.at[1]);
    const [x2, y2] = toLocal(a.to[0], a.to[1]);
    const mx = (x1 + x2) / 2 + (y2 - y1) * 0.16;
    const my = (y1 + y2) / 2 - (x2 - x1) * 0.16;
    const ang = Math.atan2(y2 - my, x2 - mx);
    const head = (d: number) => `${x2 - 11 * Math.cos(ang + d)} ${y2 - 11 * Math.sin(ang + d)}`;
    return (
      <>
        <DrawOn d={`M ${x1} ${y1} Q ${mx} ${my} ${x2} ${y2}`} className="stroke-arrow" />
        <path className="arrow-head" d={`M ${head(0.5)} L ${x2} ${y2} L ${head(-0.5)}`} />
      </>
    );
  }
  return null;
}

function ellipsePath(cx: number, cy: number, rx: number, ry: number): string {
  // Start at the top-left so the stroke draws on clockwise, like a deliberate gesture.
  return `M ${cx - rx * 0.7} ${cy - ry * 0.71} A ${rx} ${ry} 0 1 1 ${cx - rx * 0.72} ${cy - ry * 0.69}`;
}

/** A path that draws itself on in one fast, eased stroke. */
function DrawOn({ d, className }: { d: string; className: string }) {
  const ref = useRef<SVGPathElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const len = el.getTotalLength();
    el.style.strokeDasharray = `${len}`;
    el.style.strokeDashoffset = `${len}`;
    el.getBoundingClientRect();
    el.style.transition = "stroke-dashoffset 520ms cubic-bezier(.2,.8,.2,1)";
    el.style.strokeDashoffset = "0";
  }, [d]);
  return <path ref={ref} d={d} className={className} filter="url(#lum)" />;
}

function Label({ a }: { a: LiveAnnotation }) {
  let x = 0;
  let y = 0;
  const side = a.kind === "highlight" && !!a.rect;
  if (a.rect && side) {
    const r = rectToLocal(a.rect);
    x = r.x + r.w + 16; // beside the frame, so it never covers the content below
    y = r.y + r.h / 2;
  } else if (a.rect) {
    // Under the thing, or above it when under is off the screen (a row at the bottom edge).
    const gap = a.kind === "click" ? 20 : a.kind === "circle" ? 22 : 14;
    if (a.kind === "click") return <GuideLabel a={a} r={rectToLocal(a.rect)} gap={gap} />;
    ({ x, y } = labelSpot(rectToLocal(a.rect), gap, labelSize(a.label ?? ""), { w: innerWidth, h: innerHeight }));
  } else if (a.at) {
    const [px, py] = toLocal(a.at[0], a.at[1]);
    ({ x, y } = labelSpot({ x: px, y: py, w: 0, h: 0 }, 22, labelSize(a.label ?? ""), { w: innerWidth, h: innerHeight }));
  }
  return <div className={`ann-label ${side ? "side" : ""}`} style={{ left: x, top: y }}><i />{a.label}</div>;
}

// Where the pointer is, so a guide's arrow can start from it (eyes are already there).
let lastCursor: [number, number] | null = null;
bridge.onCursor((p) => {
  lastCursor = cursorToLocal(p);
});

const VERB: Record<string, string> = {
  click: "Click",
  double_click: "Double-click",
  right_click: "Right-click",
  look: "Look at",
};

/** A guide step on the real screen: the mark, an arrow that sweeps in from your pointer (or,
 * for a drag, from the thing to where it goes), and for scrolling a chevron that nudges. */
function GuideShape({ a }: { a: LiveAnnotation }) {
  const r = rectToLocal(a.rect!);
  const pad = 5;
  const x = r.x - pad, y = r.y - pad, w = r.w + pad * 2, h = r.h + pad * 2;
  const cx = x + w / 2, cy = y + h / 2;
  // The pointer as it was when this step appeared: the arrow doesn't chase the mouse around.
  const from = useRef<[number, number] | null>(lastCursor ?? [innerWidth / 2, innerHeight * 0.8]).current;
  // A key to press has nothing on screen to point at: just the step's pill, no box round the
  // whole window.
  if (a.action === "press") return null;
  let arrow = "";
  let head = "";
  const curve = (x1: number, y1: number, x2: number, y2: number) => {
    const mx = (x1 + x2) / 2 + (y2 - y1) * 0.18;
    const my = (y1 + y2) / 2 - (x2 - x1) * 0.18;
    const ang = Math.atan2(y2 - my, x2 - mx);
    const tip = (d: number) => `${x2 - 13 * Math.cos(ang + d)} ${y2 - 13 * Math.sin(ang + d)}`;
    arrow = `M ${x1} ${y1} Q ${mx} ${my} ${x2} ${y2}`;
    head = `M ${tip(0.5)} L ${x2} ${y2} L ${tip(-0.5)}`;
  };
  if (a.action === "drag" && a.to) {
    const [tx, ty] = toLocal(a.to[0], a.to[1]);
    curve(cx, cy, tx, ty);
  } else if (from && Math.hypot(from[0] - cx, from[1] - cy) > 90) {
    // Stop at the frame's edge, on the side facing the pointer.
    const dx = from[0] - cx, dy = from[1] - cy;
    const k = Math.min(Math.abs((w / 2 + 8) / (dx || 1e-6)), Math.abs((h / 2 + 8) / (dy || 1e-6)));
    curve(from[0], from[1], cx + dx * k, cy + dy * k);
  }
  const dir = a.action === "scroll" ? (a.hint ?? "down") : null;
  const rot = { down: 0, up: 180, left: 90, right: -90 }[dir as "down"] ?? 0;
  return (
    <g className="click-target">
      <rect x={x} y={y} width={w} height={h} rx="10" className="click-frame" />
      <rect x={x} y={y} width={w} height={h} rx="10" className="click-pulse" style={{ transformOrigin: `${cx}px ${cy}px` }} />
      {arrow && (
        <g className={`guide-arrow ${a.action === "drag" ? "drag" : ""}`}>
          <DrawOn d={arrow} className="stroke-arrow" />
          <path className="arrow-head" d={head} />
        </g>
      )}
      {dir ? (
        <g className="guide-scroll" transform={`translate(${cx} ${cy}) rotate(${rot})`}>
          <path d="M -14 -8 L 0 6 L 14 -8" />
          <path d="M -14 4 L 0 18 L 14 4" className="late" />
        </g>
      ) : (
        (!a.action || a.action.endsWith("click")) && (
          <path className="click-cursor" transform={`translate(${x + w - 6} ${y + h - 4})`} d="M0 0 L0 15 L4 11 L7 18 L10 16.6 L7 10 L12.5 10 Z" />
        )
      )}
    </g>
  );
}

/** "2/5 · Type DATABASE_URL in Key", with a Done button for steps a click can't finish. */
function GuideLabel({ a, r, gap }: { a: LiveAnnotation; r: Box; gap: number }) {
  const action = a.action ?? "click";
  const what = a.label && a.label !== "here" ? a.label : "";
  const text =
    action === "type" ? `Type “${a.hint ?? ""}”${what ? ` in ${what}` : ""}`
    : action === "press" ? `Press ${a.hint ?? what}`
    : action === "scroll" ? `Scroll ${a.hint ?? "down"}${what ? ` to ${what}` : ""}`
    : action === "drag" ? `Drag ${what || "this"} to the arrow`
    : `${VERB[action] ?? "Click"} ${what || "here"}`;
  const needsDone = !action.endsWith("click");
  // On screen, always: its Done button is the only way to finish a step that isn't a click.
  const { x, y } = labelSpot(r, gap, labelSize(text, needsDone), { w: innerWidth, h: innerHeight });
  const done = () => {
    useOverlay.setState((s) => ({ annotations: s.annotations.filter((n) => !n.guide) }));
    brain.send({ type: "guide_clicked", how: "done" });
  };
  const step = a.step ?? useOverlay.getState().guide?.step;
  return (
    <Interactive className="ann-label click-label guide-label" style={{ left: x, top: y }} active={needsDone}>
      {step ? <span className="click-step">{a.total ? `${step}/${a.total}` : step}</span> : <i />}
      <span>{text}</span>
      {needsDone && (
        <button className="guide-done" onClick={done}>
          Done
        </button>
      )}
    </Interactive>
  );
}

/** They finished a guide on their own: a ring of light and a quiet well done, then gone. */
function Cheer() {
  const cheer = useOverlay((s) => s.cheer);
  if (!cheer) return null;
  return (
    <div className="cheer" key={cheer.at} style={{ left: cheer.x, top: cheer.y }}>
      <i className="cheer-ring" />
      <i className="cheer-ring late" />
      {Array.from({ length: 10 }, (_, k) => (
        <i key={k} className="cheer-spark" style={{ ["--a" as string]: `${k * 36}deg` }} />
      ))}
      <div className="cheer-pill">You did it yourself</div>
    </div>
  );
}

function PinNote({ pin }: { pin: Pin }) {
  const r = rectToLocal(pin.rect);
  return (
    <div className="pin" style={{ left: r.x + r.w + 10, top: r.y + r.h / 2, ["--pin" as string]: pin.color }}>
      <i />
      {pin.note}
    </div>
  );
}
