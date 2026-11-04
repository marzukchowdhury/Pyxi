/**
 * The notch: a slim glass pill hanging from the top edge of the main screen. It is Wisp's
 * voice surface, so the chat window can stay closed.
 *
 *   hold Ctrl + Alt   it opens into "Listening" with a live waveform of your voice
 *   let go            "Thinking" (or "Looking at your screen"), then Wisp's words stream in
 *   hover it          it unfolds into a small chat: recent lines, a text box, the mic
 *   click it          it stays open until you click it again or press Escape
 *   double-tap Ctrl   it opens with the text box ready to type
 *
 * Teammates at work show as small marks on the pill; errors (mic blocked, no key) surface here
 * instead of failing silently.
 */
import { type FormEvent, useEffect, useLayoutEffect, useRef, useState } from "react";
import { bridge, keysOf } from "../shared/bridge";
import { Expand, Mic, Send } from "../shared/icons";
import { brain } from "../shared/socket";
import { Interactive } from "./Interactive";
import { useOverlay, voice } from "./store";

type Mode = "idle" | "hint" | "working" | "paused" | "offline" | "notice" | "speaking" | "thinking" | "listening" | "open";

const WIDTH: Record<Mode, number> = {
  idle: 136,
  hint: 292,
  working: 320,
  paused: 228,
  offline: 330,
  notice: 460,
  speaking: 540,
  thinking: 240,
  listening: 268,
  open: 440,
};

const NOTICE_MS = 7000;
const HINT_MS = 6500;
const WAKE_MS = 15000; // after this long without the brain, say how to start it

function useHoldKeys(): { verb: string; keys: string[] } {
  const [k, setK] = useState({ verb: "Hold", keys: ["Ctrl", "Alt"] });
  useEffect(() => {
    void bridge.hotkeys().then((h) =>
      setK(h.hold ? { verb: "Hold", keys: keysOf(h.hold) } : h.talk ? { verb: "Press", keys: keysOf(h.talk) } : { verb: "Click", keys: [] }),
    );
  }, []);
  return k;
}

export function Notch({ micLevel }: { micLevel: () => number }) {
  const state = useOverlay((s) => s.state);
  const caption = useOverlay((s) => s.caption);
  const notice = useOverlay((s) => s.notice);
  const connected = useOverlay((s) => s.connected);
  const paused = useOverlay((s) => s.paused);
  const seeing = useOverlay((s) => s.seeing);
  const captions = useOverlay((s) => s.captions);
  const chat = useOverlay((s) => s.chat);
  const jobs = useOverlay((s) => s.jobs);
  const jobStatus = useOverlay((s) => s.jobStatus);
  const hold = useHoldKeys();

  const [hover, setHover] = useState(false);
  const [pinned, setPinned] = useState(false);
  const [focused, setFocused] = useState(false);
  const [text, setText] = useState("");
  const [now, setNow] = useState(() => performance.now());
  const [hintUntil, setHintUntil] = useState(0);
  const bornAt = useRef(performance.now());
  const leaveTimer = useRef<number>(0);
  const input = useRef<HTMLInputElement>(null);
  const inner = useRef<HTMLDivElement>(null);
  const lines = useRef<HTMLDivElement>(null);
  const [height, setHeight] = useState(10);

  // Introduce the gesture once, the first time the brain connects.
  const introduced = useRef(false);
  useEffect(() => {
    if (connected && !introduced.current) {
      introduced.current = true;
      setHintUntil(performance.now() + HINT_MS);
    }
  }, [connected]);

  // A coarse clock, so time-limited modes (hint, notice) fold away on their own.
  useEffect(() => {
    const id = window.setInterval(() => setNow(performance.now()), 500);
    return () => window.clearInterval(id);
  }, []);

  const running = Object.values(jobs).filter((j) => j.status === "running" || j.status === "queued");
  const open = hover || pinned || focused;
  const mode: Mode = open ? "open"
    : state === "listening" ? "listening"
    : state === "transcribing" || state === "thinking" || state === "looking" ? "thinking"
    : state === "speaking" && caption ? "speaking"
    : notice && now - notice.at < NOTICE_MS ? "notice"
    : !connected ? "offline"
    : now < hintUntil ? "hint"
    : running.length ? "working"
    : paused ? "paused"
    : "idle";

  // Height follows the content (CSS can't animate to `auto`).
  useLayoutEffect(() => {
    const el = inner.current;
    if (!el) return;
    // +1 for the bottom hairline, which sits inside the box.
    const measure = () => setHeight(mode === "idle" ? 10 : Math.ceil(el.scrollHeight) + 1);
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    measure();
    return () => ro.disconnect();
  }, [mode]);

  useEffect(() => {
    if (mode === "open") lines.current?.scrollTo({ top: lines.current.scrollHeight });
  }, [mode, chat.length, chat[chat.length - 1]?.text]);

  // Double-tap Ctrl: open with the text box ready.
  useEffect(
    () =>
      bridge.onQuickType(() => {
        setPinned(true);
        requestAnimationFrame(() => requestAnimationFrame(() => input.current?.focus()));
      }),
    [],
  );

  const enter = () => {
    window.clearTimeout(leaveTimer.current);
    setHover(true);
  };
  const leave = () => {
    window.clearTimeout(leaveTimer.current);
    leaveTimer.current = window.setTimeout(() => setHover(false), 320);
  };
  const close = () => {
    setPinned(false);
    setHover(false);
    input.current?.blur();
  };
  const send = (e: FormEvent) => {
    e.preventDefault();
    const t = text.trim();
    if (!t) return;
    voice.stop();
    brain.send({ type: "interrupt" });
    brain.send({ type: "text", text: t });
    setText("");
  };

  const level = mode === "listening" ? micLevel : () => voice.level();
  // Say exactly what Wisp is reading, so you're never unsure what it sees.
  const status = state === "looking" ? `Looking at ${seeing || "your screen"}` : state === "transcribing" ? "Got it" : "Thinking";

  return (
    <Interactive className={`notch-zone ${open ? "is-open" : ""}`} onEnter={enter} onLeave={leave}>
      <div
        className={`notch mode-${mode}`}
        style={{ width: mode === "speaking" && !captions ? 210 : WIDTH[mode], height }}
        onClick={() => mode !== "open" && setPinned(true)}
      >
        <i className="ear left" />
        <i className="ear right" />
        <div ref={inner} className="notch-inner" key={mode}>
          {mode === "idle" && running.length > 0 && <Marks jobs={running.map((j) => j.agent_name)} />}

          {mode === "hint" && (
            <div className="notch-row">
              <Diamond />
              <span className="notch-text">
                {hold.verb} {hold.keys.map((k) => <kbd key={k}>{k}</kbd>)} and just talk
              </span>
            </div>
          )}

          {mode === "listening" && (
            <div className="notch-row">
              <Diamond live />
              <span className="notch-text strong">Listening</span>
              <Wave level={level} />
            </div>
          )}

          {mode === "thinking" && (
            <div className="notch-row">
              <Diamond spin />
              <span className="notch-text shimmer">{status}</span>
            </div>
          )}

          {mode === "speaking" && caption && (
            <div className="notch-row top">
              <Diamond live />
              <div className="notch-say">
                {caption.agent !== "Wisp" && <b>{caption.agent}</b>}
                {captions ? caption.text : <span className="notch-text">{caption.agent === "Wisp" ? "Speaking" : "Talking"}</span>}
              </div>
              <Wave level={level} small />
            </div>
          )}

          {mode === "notice" && notice && (
            <div className="notch-row top">
              <span className="notch-alert" />
              <div className="notch-say">{notice.text}</div>
            </div>
          )}

          {mode === "offline" && (
            <div className="notch-row">
              <Diamond spin />
              {now - bornAt.current < WAKE_MS ? (
                <span className="notch-text shimmer">Waking up</span>
              ) : (
                <span className="notch-text">Can't reach Wisp's brain. Run <kbd>wisp serve</kbd></span>
              )}
            </div>
          )}

          {mode === "paused" && (
            <div className="notch-row">
              <span className="notch-paused" />
              <span className="notch-text">Screen paused</span>
            </div>
          )}

          {mode === "working" && (
            <div className="notch-row">
              <Marks jobs={running.map((j) => j.agent_name)} />
              <span className="notch-text">
                <b>{running[0].agent_name}</b> {jobStatus[running[0].id] ?? "is on it"}
              </span>
            </div>
          )}

          {mode === "open" && (
            <div className="notch-open">
              <div className="notch-head">
                <Diamond live={state === "listening" || state === "speaking"} spin={state === "thinking" || state === "looking" || state === "transcribing"} />
                <b>Wisp</b>
                <span className={state === "idle" ? "" : "shimmer"}>
                  {state === "idle" ? (connected ? "Ready" : "Waiting for the brain") : state === "listening" ? "Listening" : state === "speaking" ? "Speaking" : status}
                </span>
                <button className="notch-btn" title="Open the full panel" onClick={() => bridge.togglePanel()}><Expand /></button>
              </div>
              {running.map((j) => (
                <div key={j.id} className="notch-job">
                  <Marks jobs={[j.agent_name]} />
                  <b>{j.agent_name}</b>
                  <span>{jobStatus[j.id] ?? j.task}</span>
                </div>
              ))}
              <div ref={lines} className="notch-lines">
                {chat.length === 0 && (
                  <div className="notch-empty">
                    Ask about anything on your screen. I can point, draw, explain it in 3D, or send a teammate.
                  </div>
                )}
                {chat.slice(-8).map((l) => (
                  <div key={l.id} className={`nl ${l.who}`}>
                    {l.who === "agent" && l.agent && l.agent !== "Wisp" && <b>{l.agent}</b>}
                    {l.text}
                  </div>
                ))}
              </div>
              <form className="notch-input" onSubmit={send}>
                <input
                  ref={input}
                  value={text}
                  placeholder="Ask Wisp anything"
                  onChange={(e) => setText(e.target.value)}
                  onMouseDown={() => bridge.focusOverlay()}
                  onFocus={() => setFocused(true)}
                  onBlur={() => setFocused(false)}
                  onKeyDown={(e) => e.key === "Escape" && close()}
                />
                {text.trim() ? (
                  <button type="submit" className="notch-btn primary" aria-label="Send"><Send /></button>
                ) : (
                  <button type="button" className={`notch-btn ${state === "listening" ? "live" : ""}`} title="Talk" onClick={() => bridge.ptt()}><Mic /></button>
                )}
              </form>
              <div className="notch-foot">
                <span>{hold.verb} {hold.keys.map((k) => <kbd key={k}>{k}</kbd>)} to talk, tap <kbd>Ctrl</kbd> twice to type</span>
                {pinned && <button onClick={close}>Close</button>}
              </div>
            </div>
          )}
        </div>
      </div>
    </Interactive>
  );
}

function Diamond({ live = false, spin = false }: { live?: boolean; spin?: boolean }) {
  return (
    <svg className={`notch-mark ${live ? "live" : ""} ${spin ? "spin" : ""}`} width="10" height="18" viewBox="0 0 12 22">
      <path d="M6 1 10.5 11 6 21 1.5 11Z" />
    </svg>
  );
}

const AGENT_COLORS: Record<string, string> = { Scout: "#7dd3fc", Quill: "#fcd34d", Sketch: "#f9a8d4", Wisp: "#c4b5fd" };

function Marks({ jobs }: { jobs: string[] }) {
  return (
    <span className="notch-marks">
      {jobs.slice(0, 4).map((name, i) => (
        <i key={`${name}-${i}`} style={{ ["--c" as string]: AGENT_COLORS[name] ?? "#9ecbff", animationDelay: `${i * 0.2}s` }} />
      ))}
    </span>
  );
}

/** A small live waveform: bars that follow the loudness, each with its own phase. */
function Wave({ level, small = false }: { level: () => number; small?: boolean }) {
  const bars = useRef<(HTMLElement | null)[]>([]);
  useEffect(() => {
    let raf = 0;
    let smooth = 0;
    const tick = (t: number) => {
      smooth += (level() - smooth) * 0.35;
      bars.current.forEach((b, i) => {
        if (!b) return;
        const wobble = 0.55 + 0.45 * Math.sin(t / 140 + i * 1.7);
        const h = 0.18 + Math.min(1, smooth * 1.6) * wobble * (1 - Math.abs(i - 3) * 0.12);
        b.style.transform = `scaleY(${h.toFixed(3)})`;
      });
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [level]);
  return (
    <span className={`notch-wave ${small ? "small" : ""}`}>
      {Array.from({ length: 7 }, (_, i) => <i key={i} ref={(el) => { bars.current[i] = el; }} />)}
    </span>
  );
}
