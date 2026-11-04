/**
 * Precise, luminous annotations. Geometry is exact (hairlines, true ellipses, rounded frames
 * with viewfinder corners), motion is fast and springy, and light does the emphasis.
 */
import { useEffect, useRef, useState } from "react";
import { rectToLocal, toLocal } from "../shared/bridge";
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
    const r = rectToLocal(a.rect);
    x = r.x + r.w / 2;
    y = r.y + r.h + (a.kind === "circle" ? 22 : 14);
  } else if (a.at) {
    [x, y] = toLocal(a.at[0], a.at[1]);
    y += 22;
  }
  return <div className={`ann-label ${side ? "side" : ""}`} style={{ left: x, top: y }}><i />{a.label}</div>;
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
