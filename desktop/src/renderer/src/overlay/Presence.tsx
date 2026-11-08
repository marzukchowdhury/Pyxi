/**
 * Pyxi's presence: a little firefly that lives beside the cursor.
 * A warm core, a soft halo and two faint wings; it hovers when you're still and leaves a
 * short trail of light when you move.
 *
 *   idle       hovering, glowing slowly on and off
 *   listening  the glow swells with your voice
 *   thinking   a quick flicker, with a mote of light circling it
 *   acting     wings buzzing and a dotted ring turning (speaking, teammates at work)
 *
 * When Pyxi targets something, a thin thread of light runs from the firefly to the target,
 * recomputed every frame so it stays attached while you move the mouse.
 *
 * Resting: a few seconds after the mouse stops, with Pyxi idle, the firefly settles and holds
 * still, and its frame loop stops. The overlay is a transparent window over the whole screen,
 * and every frame it draws costs the GPU process about a third of a core, so a firefly that
 * hovered forever kept Pyxi busy while nothing happened. Any mouse move or state change wakes it.
 */
import { useEffect, useRef } from "react";
import { bridge, cursorToLocal, toLocal } from "../shared/bridge";
import { display, isPrimary, useOverlay, voice } from "./store";

const OFFSET = { x: 18, y: 6 };
/** Where the firefly's core sits inside the presence box. */
const CORE = { x: 6, y: 16 };
const TRAIL = 12;
/** Still this long (Pyxi idle, no voice, no thread) and the firefly settles. */
const REST_MS = 4000;
/** How often the firefly is drawn while it moves (about 30 a second), and its spring's step. */
const FRAME_MS = 30;
const STEP_MS = 1000 / 60;

export function Presence({ micLevel }: { micLevel: () => number }) {
  const root = useRef<HTMLDivElement>(null);
  const thread = useRef<SVGPathElement>(null);
  const spark = useRef<SVGCircleElement>(null);
  const ring = useRef<SVGCircleElement>(null);
  const halo = useRef<SVGCircleElement>(null);
  const coreEl = useRef<SVGEllipseElement>(null);
  /** The firefly's mood, for the frame loop (the idle glow is drawn there, not by CSS). */
  const modeRef = useRef("idle");
  const trail = useRef<(SVGCircleElement | null)[]>([]);
  const history = useRef<{ x: number; y: number }[]>([]);
  const goal = useRef({ x: innerWidth - 80, y: innerHeight - 120 });
  const pos = useRef({ ...goal.current, vx: 0, vy: 0 });
  const onScreen = useRef(display.primary);
  const state = useOverlay((s) => s.state);
  const paused = useOverlay((s) => s.paused);
  const caption = useOverlay((s) => s.caption);
  const working = useOverlay((s) => Object.values(s.jobs).some((j) => j.status === "running"));
  const target = useOverlay((s) => s.flyTo);
  const driving = useOverlay((s) => s.driving);
  /** Starts the frame loop if it's asleep (set by the loop's effect). */
  const wake = useRef<() => void>(() => undefined);
  /** Pyxi is doing something (not idle), so the firefly never rests. */
  const busy = useRef(false);

  useEffect(
    () =>
      bridge.onCursor((p) => {
        const local = cursorToLocal(p);
        const was = onScreen.current;
        onScreen.current = local !== null;
        if (local) goal.current = { x: local[0] + OFFSET.x, y: local[1] + OFFSET.y };
        // A screen the cursor isn't on draws nothing, except once to hide the firefly.
        if (local || was) wake.current();
      }),
    [],
  );

  useEffect(() => {
    let raf = 0;
    let stillSince = performance.now();
    let hover = 0; // 0 at rest, 1 hovering: eases, so settling and waking never jump
    let lastDraw = 0;
    let lastLevel = -1;
    let lastGlow = -1;
    const tick = (time: number) => {
      // Drawn about 30 times a second, not 60: each frame repaints a window the size of the
      // screen, and following the cursor at 60 cost a fifth of a core in the GPU process.
      if (lastDraw && time - lastDraw < FRAME_MS) {
        raf = requestAnimationFrame(tick);
        return;
      }
      const steps = lastDraw ? Math.min(4, Math.max(1, Math.round((time - lastDraw) / STEP_MS))) : 1;
      lastDraw = time;
      const p = pos.current;
      // A stiff, well-damped spring: it keeps up with the cursor and settles without wobble.
      // Stepped at its usual 60 a second, so it moves the same however often it's drawn.
      for (let i = 0; i < steps; i++) {
        p.vx = (p.vx + (goal.current.x - p.x) * 0.22) * 0.62;
        p.vy = (p.vy + (goal.current.y - p.y) * 0.22) * 0.62;
        p.x += p.vx;
        p.y += p.vy;
      }
      const level = Math.max(voice.level(), micLevel());
      const t = useOverlay.getState().flyTo;
      const threadLive = !!t && performance.now() < t.until;
      const moving = Math.hypot(p.vx, p.vy) > 0.05 || Math.hypot(goal.current.x - p.x, goal.current.y - p.y) > 0.5;
      if (moving || level > 0.002 || busy.current || threadLive) stillSince = time;
      const resting = !onScreen.current || time - stillSince > REST_MS;
      hover += ((resting ? 0 : 1) - hover) * (1 - Math.pow(0.94, steps));
      // Hovering: a slow, lazy figure of eight, the way a firefly hangs in the air.
      const hx = p.x + Math.sin(time / 820) * 2.2 * hover;
      const hy = p.y + Math.sin(time / 1270) * 2.8 * hover;
      const el = root.current;
      if (el) {
        el.style.transform = `translate3d(${hx}px, ${hy}px, 0)`;
        // The glow follows the voice; untouched while it's steady (it restyles the whole bug).
        if (Math.abs(level - lastLevel) > 0.004) {
          lastLevel = level;
          el.style.setProperty("--level", level.toFixed(3));
        }
        el.style.opacity = onScreen.current ? "" : "0";
        // Near an edge, labels by the firefly flip to the side with room.
        el.classList.toggle("near-right", p.x > innerWidth - 240);
        el.classList.toggle("near-bottom", p.y > innerHeight - 90);
        el.classList.toggle("resting", resting);
      }
      // Idle and awake, the glow breathes slowly (3.6 s). Drawn here with the rest of the frame:
      // as a CSS animation it ran 60 times a second and re-rendered the core's three glows.
      const glow = modeRef.current === "idle" && !resting ? 0.5 - 0.5 * Math.cos((time / 3600) * Math.PI * 2) : -1;
      if ((glow < 0) !== (lastGlow < 0) || Math.abs(glow - lastGlow) > 0.03) {
        lastGlow = glow;
        if (halo.current) halo.current.style.opacity = glow < 0 ? "" : (0.55 + 0.45 * glow).toFixed(2);
        if (coreEl.current) coreEl.current.style.opacity = glow < 0 ? "" : (0.78 + 0.22 * glow).toFixed(2);
      }
      if (ring.current) ring.current.setAttribute("r", (13 + level * 9).toFixed(2));
      if (halo.current) halo.current.setAttribute("r", (10 + level * 8).toFixed(2));
      // The trail: where the core has just been, fading out behind it. Still means no trail.
      const core = { x: hx + CORE.x, y: hy + CORE.y };
      const h = history.current;
      h.unshift(core);
      if (h.length > TRAIL) h.length = TRAIL;
      const speed = Math.hypot(p.vx, p.vy);
      trail.current.forEach((dot, i) => {
        const at = h[Math.min(h.length - 1, i + 1)];
        if (!dot || !at) return;
        dot.setAttribute("cx", at.x.toFixed(1));
        dot.setAttribute("cy", at.y.toFixed(1));
        const fade = 1 - i / TRAIL;
        dot.setAttribute("r", (0.6 + 2.2 * fade).toFixed(2));
        dot.style.opacity = onScreen.current ? (Math.min(1, speed / 6) * fade * 0.7).toFixed(3) : "0";
      });
      // The thread: firefly -> target, a gentle curve, alive only while the target is fresh.
      const path = thread.current;
      if (path) {
        const now = performance.now();
        if (t && now < t.until) {
          const [tx, ty] = toLocal(t.x, t.y);
          const sx = core.x;
          const sy = core.y;
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
      // Settled (or on another screen) and the trail has faded: stop drawing until woken.
      if (resting && (hover < 0.02 || !onScreen.current) && speed < 0.05 && !threadLive) {
        raf = 0;
        lastDraw = 0;
        return;
      }
      raf = requestAnimationFrame(tick);
    };
    wake.current = () => {
      stillSince = performance.now();
      if (!raf) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(raf);
      raf = 0;
      wake.current = () => undefined;
    };
  }, [micLevel]);

  const mode = paused ? "paused" : state === "listening" ? "listening"
    : state === "thinking" || state === "looking" || state === "transcribing" ? "thinking"
    : state === "speaking" || working ? "acting" : "idle";
  // Anything but idle keeps the firefly awake; any change (a target, a caption) wakes it.
  busy.current = (mode !== "idle" && mode !== "paused") || !!driving;
  modeRef.current = driving ? "driving" : mode;
  useEffect(() => wake.current(), [mode, driving, target, caption, paused]);
  // While Pyxi has the mouse, the firefly rides with it and says what it's doing.

  return (
    <>
      <svg className="tether" width="100%" height="100%">
        <defs>
          <linearGradient id="thread-g" gradientUnits="userSpaceOnUse" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stopColor="#fff3cf" stopOpacity="0.9" />
            <stop offset="1" stopColor="#ffc861" stopOpacity="0.9" />
          </linearGradient>
          <filter id="soft-glow" x="-50%" y="-50%" width="200%" height="200%">
            <feGaussianBlur stdDeviation="2.4" result="b" />
            <feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge>
          </filter>
        </defs>
        <g className={`ff-trail ${paused ? "off" : ""}`}>
          {Array.from({ length: TRAIL - 1 }, (_, i) => (
            <circle key={i} ref={(el) => { trail.current[i] = el; }} r="1" style={{ opacity: 0 }} />
          ))}
        </g>
        <path ref={thread} className="thread-line" filter="url(#soft-glow)" style={{ opacity: 0 }} />
        {target && <circle ref={spark} r="2.6" className="thread-spark" filter="url(#soft-glow)" />}
      </svg>
      <div ref={root} className={`presence mode-${driving ? "driving" : mode}`}>
        <svg className="presence-svg" width="60" height="60" viewBox="-24 -24 48 48">
          <defs>
            <radialGradient id="ff-tail" cx="0.45" cy="0.4" r="0.65">
              <stop offset="0" stopColor="#fffbea" />
              <stop offset="0.55" stopColor="#ffe08a" />
              <stop offset="1" stopColor="#ffb02e" />
            </radialGradient>
            <radialGradient id="ff-halo">
              <stop offset="0" stopColor="#ffe08a" stopOpacity="0.55" />
              <stop offset="0.45" stopColor="#ffc861" stopOpacity="0.2" />
              <stop offset="1" stopColor="#ffc861" stopOpacity="0" />
            </radialGradient>
          </defs>
          <circle ref={ring} className="presence-ring" r="13" />
          <circle ref={halo} className="ff-halo" r="10" fill="url(#ff-halo)" />
          {/* A real firefly's trick: a dark little body (it reads on white pages) and a glowing tail (it reads on dark ones). */}
          <g className="ff-bug" transform="rotate(-30)">
            <path className="ff-feeler" d="M-0.7 -9.2 Q-1.6 -11.6 -3.4 -12.6 M0.7 -9.2 Q1.6 -11.6 3.4 -12.6" />
            <g transform="rotate(-38 -2.4 -5)"><ellipse className="ff-wing" cx="-2.4" cy="-5" rx="3.8" ry="1.7" /></g>
            <g transform="rotate(38 2.4 -5)"><ellipse className="ff-wing" cx="2.4" cy="-5" rx="3.8" ry="1.7" /></g>
            <ellipse className="ff-body" cx="0" cy="-5" rx="2.3" ry="2.6" />
            <circle className="ff-body" cx="0" cy="-8.3" r="1.6" />
            <ellipse ref={coreEl} className="ff-core" cx="0" cy="0" rx="3" ry="3.7" />
          </g>
          <g className="ff-orbit"><circle r="10" fill="none" /><circle className="mote" cx="8.5" cy="0" r="1.2" /></g>
        </svg>
        {driving && (
          <div className="driving-tag" key={driving}>
            <b>{driving}</b>
            <span>Esc or move the mouse to stop</span>
          </div>
        )}
        {/* The main screen speaks through the notch; other screens keep a caption by the cursor. */}
        {caption && !isPrimary && (
          <div className="caption" key={caption.text}>
            {caption.agent !== "Pyxi" && <span className="caption-agent">{caption.agent}</span>}
            {caption.text}
          </div>
        )}
      </div>
    </>
  );
}
