/**
 * Pyxi's presence: a little firefly that lives beside the cursor, in its own small window.
 * A warm core, a soft halo and two faint wings; it hovers for a moment when you stop.
 *
 *   idle       hovering, glowing slowly on and off
 *   listening  the glow swells with your voice
 *   thinking   a quick flicker, with a mote of light circling it
 *   acting     wings buzzing and a dotted ring turning (speaking, teammates at work)
 *
 * Why its own window: drawn in the full-screen overlay, every frame of it made Windows recompose
 * a transparent window the size of the screen. Here, while the cursor moves, the spring only moves
 * a 160 px window and nothing is redrawn (that's what costs); the hover wobble and the glow are
 * drawn inside it once it stops. The core sits at the window's centre. (The overlay version
 * left a short trail of light behind it; a trail needs a redraw every frame, so it's gone.)
 *
 * Resting: a few seconds after the mouse stops, with Pyxi idle, the firefly settles and holds
 * still, and its frame loop stops. Any mouse move or mood change wakes it.
 *
 * Coordinates are screen DIP (the cursor feed's), so one window serves every screen. In a
 * browser preview (`inline`) it draws inside the overlay page instead and reads the page's own
 * mouse, with its mood passed in.
 */
import { useEffect, useRef, useState } from "react";
import { bridge, type FireflyState } from "../shared/bridge";
import "../shared/firefly.css";

/** From the cursor tip to the firefly's core (the old overlay offset plus the core's place). */
const FROM_CURSOR = { x: 24, y: 22 };
const SIZE = 160;
const HALF = SIZE / 2;
/** Still this long (Pyxi idle, no voice) and the firefly settles. */
const REST_MS = 4000;
/** How often it's drawn while it moves (about 30 a second), and its spring's step. */
const FRAME_MS = 30;
const STEP_MS = 1000 / 60;

const IDLE: FireflyState = { mode: "idle", level: 0, hidden: false };

export function Firefly({ inline, state: given }: { inline?: boolean; state?: FireflyState }) {
  const win = useRef<HTMLDivElement>(null);
  const root = useRef<HTMLDivElement>(null);
  const ring = useRef<SVGCircleElement>(null);
  const halo = useRef<SVGCircleElement>(null);
  const coreEl = useRef<SVGEllipseElement>(null);
  const goal = useRef<{ x: number; y: number } | null>(null);
  const pos = useRef({ x: 0, y: 0, vx: 0, vy: 0 });
  const [mood, setMood] = useState<FireflyState>(given ?? IDLE);
  const moodRef = useRef<FireflyState>(mood);
  /** Starts the frame loop if it's asleep (set by the loop's effect). */
  const wake = useRef<() => void>(() => undefined);

  useEffect(() => {
    if (given) setMood(given);
  }, [given]);
  useEffect(() => {
    if (inline) return;
    return bridge.onFireflyState((s) => setMood(s));
  }, [inline]);
  useEffect(() => {
    moodRef.current = mood;
    wake.current();
  }, [mood]);

  useEffect(
    () =>
      bridge.onCursor((p) => {
        const first = goal.current === null;
        goal.current = { x: p.x + FROM_CURSOR.x, y: p.y + FROM_CURSOR.y };
        if (first) Object.assign(pos.current, goal.current, { vx: 0, vy: 0 });
        wake.current();
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
    let at = { x: Number.NaN, y: Number.NaN }; // the window's top-left, as last moved
    let still = false; // the inside has been put back to plain for moving
    const tick = (time: number) => {
      if (lastDraw && time - lastDraw < FRAME_MS) {
        raf = requestAnimationFrame(tick);
        return;
      }
      const target = goal.current;
      if (!target) {
        raf = 0;
        return;
      }
      const steps = lastDraw ? Math.min(4, Math.max(1, Math.round((time - lastDraw) / STEP_MS))) : 1;
      lastDraw = time;
      const p = pos.current;
      // A stiff, well-damped spring: it keeps up with the cursor and settles without wobble.
      // Stepped at its usual 60 a second, so it moves the same however often it's drawn.
      for (let i = 0; i < steps; i++) {
        p.vx = (p.vx + (target.x - p.x) * 0.22) * 0.62;
        p.vy = (p.vy + (target.y - p.y) * 0.22) * 0.62;
        p.x += p.vx;
        p.y += p.vy;
      }
      const m = moodRef.current;
      const level = m.level;
      const busy = m.mode !== "idle" && m.mode !== "paused";
      const moving = Math.hypot(p.vx, p.vy) > 0.05 || Math.hypot(target.x - p.x, target.y - p.y) > 0.5;
      if (moving || level > 0.002 || busy) stillSince = time;
      const resting = time - stillSince > REST_MS;
      // The window follows the spring in whole pixels.
      const ox = Math.round(p.x) - HALF;
      const oy = Math.round(p.y) - HALF;
      if (ox !== at.x || oy !== at.y) {
        at = { x: ox, y: oy };
        if (inline) {
          if (win.current) win.current.style.transform = `translate3d(${ox}px, ${oy}px, 0)`;
        } else bridge.fireflyMove(ox, oy);
      }
      const el = root.current;
      // The glow follows the voice; untouched while it's steady (it restyles the whole bug).
      if (el && Math.abs(level - lastLevel) > 0.004) {
        lastLevel = level;
        el.style.setProperty("--level", level.toFixed(3));
        if (ring.current) ring.current.setAttribute("r", (13 + level * 9).toFixed(2));
        if (halo.current) halo.current.setAttribute("r", (10 + level * 8).toFixed(2));
      }
      if (moving) {
        // Following the cursor, only the window moves and nothing inside it is redrawn: every
        // redraw of a see-through window costs Windows a fixed price per frame (about a fifth
        // of a core at 30 a second), while moving the finished window is nearly free.
        hover = 0;
        if (el && !still) {
          still = true;
          el.style.transform = "";
          el.classList.remove("resting");
        }
        raf = requestAnimationFrame(tick);
        return;
      }
      hover += ((resting ? 0 : 1) - hover) * (1 - Math.pow(0.94, steps));
      // Stopped: a slow, lazy figure of eight, the way a firefly hangs in the air, until it rests.
      const hx = Math.sin(time / 820) * 2.2 * hover + (p.x - ox - HALF);
      const hy = Math.sin(time / 1270) * 2.8 * hover + (p.y - oy - HALF);
      if (el) {
        still = false;
        el.style.transform = `translate3d(${hx.toFixed(2)}px, ${hy.toFixed(2)}px, 0)`;
        el.classList.toggle("resting", resting);
      }
      // Idle and awake, the glow breathes slowly (3.6 s), drawn here with the rest of the frame.
      const glow = m.mode === "idle" && !resting ? 0.5 - 0.5 * Math.cos((time / 3600) * Math.PI * 2) : -1;
      if ((glow < 0) !== (lastGlow < 0) || Math.abs(glow - lastGlow) > 0.03) {
        lastGlow = glow;
        if (halo.current) halo.current.style.opacity = glow < 0 ? "" : (0.55 + 0.45 * glow).toFixed(2);
        if (coreEl.current) coreEl.current.style.opacity = glow < 0 ? "" : (0.78 + 0.22 * glow).toFixed(2);
      }
      const speed = Math.hypot(p.vx, p.vy);
      // Settled: stop drawing until woken.
      if (resting && hover < 0.02 && speed < 0.05) {
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
  }, [inline]);

  return (
    <div ref={win} className={`ff-window ${mood.hidden ? "hidden" : ""}`} style={inline ? { zIndex: 50 } : undefined}>
      <div ref={root} className={`ff-root mode-${mood.mode}`}>
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
      </div>
    </div>
  );
}
