/**
 * What the overlay still draws by the cursor, now that the firefly has its own window
 * (firefly/Firefly.tsx):
 *
 * - the thread: when Pyxi targets something, a thin line of light runs from the firefly to the
 *   target, kept attached while you move the mouse;
 * - the driving tag ("Esc or move the mouse to stop") while Pyxi has the mouse;
 * - on screens other than the main one, the caption of what Pyxi is saying.
 *
 * Nothing here draws while none of those shows, so moving the mouse costs the overlay nothing.
 * The main screen's overlay also tells the firefly window Pyxi's mood and voice level.
 */
import { useEffect, useRef, useState } from "react";
import { Firefly } from "../firefly/Firefly";
import { bridge, cursorToLocal, type FireflyState, toLocal } from "../shared/bridge";
import { isPrimary, useOverlay, voice } from "./store";

/** From the cursor tip to the labels' corner, and to the firefly's core (the thread starts there). */
const OFFSET = { x: 18, y: 6 };
const CORE = { x: 24, y: 22 };
/** How often the voice level goes to the firefly while it's changing (about 30 a second). */
const LEVEL_MS = 33;

export function Presence({ micLevel }: { micLevel: () => number }) {
  const root = useRef<HTMLDivElement>(null);
  const thread = useRef<SVGPathElement>(null);
  const spark = useRef<SVGCircleElement>(null);
  const cursor = useRef<[number, number] | null>(null);
  const state = useOverlay((s) => s.state);
  const paused = useOverlay((s) => s.paused);
  const caption = useOverlay((s) => s.caption);
  const working = useOverlay((s) => Object.values(s.jobs).some((j) => j.status === "running"));
  const target = useOverlay((s) => s.flyTo);
  const driving = useOverlay((s) => s.driving);
  // Hidden for Pyxi's own screenshots, and while the first-run tour draws a firefly of its own.
  const capturing = useOverlay((s) => s.capturing || s.tourFirefly);
  const tags = !!driving || (!!caption && !isPrimary);
  const tagsRef = useRef(tags);
  tagsRef.current = tags;
  /** Starts the thread's frame loop if it's asleep. */
  const wake = useRef<() => void>(() => undefined);

  const place = () => {
    const at = cursor.current;
    const el = root.current;
    if (!el) return;
    el.style.opacity = at ? "" : "0";
    if (!at) return;
    const [x, y] = [at[0] + OFFSET.x, at[1] + OFFSET.y];
    el.style.transform = `translate3d(${x}px, ${y}px, 0)`;
    // Near an edge, the labels flip to the side with room.
    el.classList.toggle("near-right", x > innerWidth - 240);
    el.classList.toggle("near-bottom", y > innerHeight - 90);
  };

  useEffect(
    () =>
      bridge.onCursor((p) => {
        cursor.current = cursorToLocal(p);
        if (tagsRef.current) place();
        wake.current();
      }),
    [],
  );
  useEffect(() => {
    if (tags) place();
  }, [tags]);

  // The thread: firefly -> target, a gentle curve, alive only while the target is fresh.
  useEffect(() => {
    let raf = 0;
    const tick = () => {
      const t = useOverlay.getState().flyTo;
      const path = thread.current;
      const now = performance.now();
      const at = cursor.current;
      if (!path || !t || now >= t.until || !at) {
        if (path) path.style.opacity = "0";
        raf = 0;
        return;
      }
      const [tx, ty] = toLocal(t.x, t.y);
      const sx = at[0] + CORE.x;
      const sy = at[1] + CORE.y;
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
      raf = requestAnimationFrame(tick);
    };
    wake.current = () => {
      const t = useOverlay.getState().flyTo;
      if (!raf && t && performance.now() < t.until) raf = requestAnimationFrame(tick);
    };
    wake.current();
    // The line is only ever on screen while its frame loop runs. With no fresh target (a
    // clear, or the next turn, took it away mid-flight) nothing was left to fade it, and it
    // stayed drawn across the screen until the next time Pyxi pointed at something: "clear
    // the drawings" froze it instead of removing it.
    const hide = () => {
      if (thread.current) thread.current.style.opacity = "0";
      if (spark.current) spark.current.style.opacity = "0";
    };
    if (!raf) hide();
    // And at its end whatever the frames did: a window that isn't being painted gets none.
    const t0 = useOverlay.getState().flyTo;
    const end = t0 ? window.setTimeout(hide, Math.max(0, t0.until - performance.now()) + 60) : 0;
    return () => {
      cancelAnimationFrame(raf);
      raf = 0;
      window.clearTimeout(end);
      wake.current = () => undefined;
    };
  }, [target]);

  const mode: FireflyState["mode"] = driving ? "driving" : paused ? "paused" : state === "listening" ? "listening"
    : state === "thinking" || state === "looking" || state === "transcribing" ? "thinking"
    : state === "speaking" || working ? "acting" : "idle";

  // The firefly's mood, from the main screen only. The voice level only matters while Pyxi
  // listens or speaks, so it's sent then, about 30 times a second while it changes.
  const level = useRef(0);
  const [inlineState, setInline] = useState<FireflyState | undefined>(undefined);
  useEffect(() => {
    if (!isPrimary) return;
    const send = (lv: number) => {
      const s: FireflyState = { mode, level: lv, hidden: capturing };
      if (bridge.electron) bridge.fireflyState(s);
      else setInline(s);
    };
    level.current = 0;
    send(0);
    if (mode !== "listening" && mode !== "acting") return;
    const id = window.setInterval(() => {
      const lv = Math.round(Math.max(voice.level(), micLevel()) * 200) / 200;
      if (lv !== level.current) {
        level.current = lv;
        send(lv);
      }
    }, LEVEL_MS);
    return () => window.clearInterval(id);
  }, [mode, capturing, micLevel, setInline]);

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
        <path ref={thread} className="thread-line" filter="url(#soft-glow)" style={{ opacity: 0 }} />
        {target && <circle ref={spark} r="2.6" className="thread-spark" filter="url(#soft-glow)" />}
      </svg>
      {tags && (
        <div ref={root} className="presence">
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
      )}
      {/* A browser preview has no firefly window: the firefly draws here instead. */}
      {!bridge.electron && isPrimary && <Firefly inline state={inlineState} />}
    </>
  );
}

