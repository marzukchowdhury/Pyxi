/**
 * Wisp's presence: a thin, softly glowing elongated diamond that lives beside the cursor.
 * Not a character; a quiet sign that something intelligent is paying attention.
 *
 *   idle       barely there, breathing very slowly
 *   listening  a gentle pulse that follows your voice
 *   thinking   a shimmer of light travelling through it
 *   acting     a precise ring that expands around it (speaking, teammates at work)
 *
 * When Wisp targets something, a thin luminous thread runs from the presence to the target,
 * recomputed every frame so it stays attached while you move the mouse.
 */
import { useEffect, useRef } from "react";
import { bridge, cursorToLocal, toLocal } from "../shared/bridge";
import { display, useOverlay, voice } from "./store";

const OFFSET = { x: 18, y: 6 };

export function Presence({ micLevel }: { micLevel: () => number }) {
  const root = useRef<HTMLDivElement>(null);
  const thread = useRef<SVGPathElement>(null);
  const spark = useRef<SVGCircleElement>(null);
  const ring = useRef<SVGCircleElement>(null);
  const goal = useRef({ x: innerWidth - 80, y: innerHeight - 120 });
  const pos = useRef({ ...goal.current, vx: 0, vy: 0 });
  const onScreen = useRef(display.primary);
  const state = useOverlay((s) => s.state);
  const paused = useOverlay((s) => s.paused);
  const caption = useOverlay((s) => s.caption);
  const working = useOverlay((s) => Object.values(s.jobs).some((j) => j.status === "running"));
  const target = useOverlay((s) => s.flyTo);

  useEffect(
    () =>
      bridge.onCursor((p) => {
        const local = cursorToLocal(p);
        onScreen.current = local !== null;
        if (local) goal.current = { x: local[0] + OFFSET.x, y: local[1] + OFFSET.y };
      }),
    [],
  );

  useEffect(() => {
    let raf = 0;
    const tick = () => {
      const p = pos.current;
      // A stiff, well-damped spring: it keeps up with the cursor and settles without wobble.
      p.vx = (p.vx + (goal.current.x - p.x) * 0.22) * 0.62;
      p.vy = (p.vy + (goal.current.y - p.y) * 0.22) * 0.62;
      p.x += p.vx;
      p.y += p.vy;
      const level = Math.max(voice.level(), micLevel());
      const el = root.current;
      if (el) {
        el.style.transform = `translate3d(${p.x}px, ${p.y}px, 0)`;
        el.style.setProperty("--level", level.toFixed(3));
        el.style.opacity = onScreen.current ? "" : "0";
      }
      if (ring.current) ring.current.setAttribute("r", (15 + level * 9).toFixed(2));
      // The thread: presence -> target, a gentle curve, alive only while the target is fresh.
      const t = useOverlay.getState().flyTo;
      const path = thread.current;
      if (path) {
        const now = performance.now();
        if (t && now < t.until) {
          const [tx, ty] = toLocal(t.x, t.y);
          const sx = p.x + 6;
          const sy = p.y + 14;
          const mx = (sx + tx) / 2 + (ty - sy) * 0.18;
          const my = (sy + ty) / 2 - (tx - sx) * 0.18;
          path.setAttribute("d", `M ${sx} ${sy} Q ${mx} ${my} ${tx} ${ty}`);
          const age = (now - (t.until - 3400)) / 1000;
          path.style.opacity = String(Math.min(1, age * 4) * Math.min(1, (t.until - now) / 500));
          if (spark.current && age < 1.2) {
            const k = Math.min(1, age / 0.55);
            const e = 1 - Math.pow(1 - k, 3);
            const u = 1 - e;
            spark.current.setAttribute("cx", String(u * u * sx + 2 * u * e * mx + e * e * tx));
            spark.current.setAttribute("cy", String(u * u * sy + 2 * u * e * my + e * e * ty));
            spark.current.style.opacity = k < 1 ? "1" : "0";
          }
        } else path.style.opacity = "0";
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [micLevel]);

  const mode = paused ? "paused" : state === "listening" ? "listening"
    : state === "thinking" || state === "looking" || state === "transcribing" ? "thinking"
    : state === "speaking" || working ? "acting" : "idle";

  return (
    <>
      <svg className="tether" width="100%" height="100%">
        <defs>
          <linearGradient id="thread-g" gradientUnits="userSpaceOnUse" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stopColor="#eaf3ff" stopOpacity="0.85" />
            <stop offset="1" stopColor="#9ecbff" stopOpacity="0.9" />
          </linearGradient>
          <filter id="soft-glow" x="-50%" y="-50%" width="200%" height="200%">
            <feGaussianBlur stdDeviation="2.4" result="b" />
            <feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge>
          </filter>
        </defs>
        <path ref={thread} className="thread-line" filter="url(#soft-glow)" style={{ opacity: 0 }} />
        {target && <circle ref={spark} r="2.6" className="thread-spark" filter="url(#soft-glow)" />}
      </svg>
      <div ref={root} className={`presence mode-${mode}`}>
        <svg className="presence-svg" width="40" height="56" viewBox="-14 -6 40 56">
          <defs>
            <linearGradient id="diamond-fill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stopColor="#f2f7ff" stopOpacity="0.85" />
              <stop offset="0.55" stopColor="#9ecbff" stopOpacity="0.28" />
              <stop offset="1" stopColor="#9ecbff" stopOpacity="0.04" />
            </linearGradient>
            <linearGradient id="shimmer" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stopColor="#ffffff" stopOpacity="0" />
              <stop offset="0.5" stopColor="#ffffff" stopOpacity="0.95" />
              <stop offset="1" stopColor="#ffffff" stopOpacity="0" />
              <animate attributeName="y1" values="-1;1" dur="1.15s" repeatCount="indefinite" />
              <animate attributeName="y2" values="0;2" dur="1.15s" repeatCount="indefinite" />
            </linearGradient>
          </defs>
          <circle ref={ring} className="presence-ring" cx="6" cy="22" r="15" />
          <path className="diamond" d="M6 2 10.6 22 6 42 1.4 22Z" />
          <path className="diamond-shimmer" d="M6 2 10.6 22 6 42 1.4 22Z" />
        </svg>
        {caption && (
          <div className="caption" key={caption.text}>
            {caption.agent !== "Wisp" && <span className="caption-agent">{caption.agent}</span>}
            {caption.text}
          </div>
        )}
      </div>
    </>
  );
}
