/**
 * The notch: a slim glass pill hanging from the top edge of the main screen. It is Pyxi's
 * voice surface, so the chat window can stay closed.
 *
 *   hold Ctrl + Alt   it opens into "Listening" with a live waveform of your voice
 *   let go            "Thinking" (or "Looking at your screen"), then Pyxi's words stream in
 *   hover it          a small tab drops down that says "Open Pyxi": nothing else. Hovering
 *                     used to unfold the chat, which got in the way of the browser tabs
 *                     that sit right under it; now only a click opens anything
 *   click it          it unfolds into everything: chat, team, memory, 3D, voice, settings
 *                     (also Ctrl+Alt+W or the tray icon); Escape or the close button folds it
 *   double-tap Ctrl   it opens with the text box ready to type
 *
 * Teammates at work show as small marks on the pill; errors (mic blocked, no key) surface here
 * instead of failing silently.
 */
import { type FormEvent, useEffect, useLayoutEffect, useRef, useState } from "react";
import { bridge, keysOf } from "../shared/bridge";
import { installPanelStyles } from "./panelStyles";
import { Routines } from "../panel/Connections";
import { Chat, Gallery, Palace, Settings, type Tab, TABS, Team } from "../panel/Panel";
import { Activity } from "../panel/Activity";
import { VoiceStudio } from "../panel/VoiceStudio";
import { Close, Expand, Mic, Send } from "../shared/icons";
import { brain } from "../shared/socket";
import { FileReady } from "./FileReady";
import { Interactive } from "./Interactive";
import { useOverlay, voice } from "./store";

type Mode = "idle" | "peek" | "hint" | "working" | "paused" | "offline" | "notice" | "speaking" | "thinking" | "listening" | "open" | "full" | "result";
// Resting states: the pointer on the notch turns these (and only these) into the small tab.
const RESTING = new Set<Mode>(["idle", "hint", "working", "paused", "offline"]);
const DWELL_MS = 160; // how long the pointer must rest on the top edge: a pass on the way to a tab does nothing

const WIDTH: Record<Mode, number> = {
  idle: 136,
  peek: 184,
  hint: 292,
  working: 320,
  paused: 228,
  offline: 330,
  notice: 460,
  speaking: 540,
  thinking: 240,
  listening: 268,
  open: 440,
  full: 480,
  result: 460,
};

const RESULT_MS = 10000; // how long a finished errand stays up (hovering pauses it)
const FILE_RESULT_MS = 20000; // a new file stays longer: there's something to open or drag out
const FILE_WIDTH = 520;

installPanelStyles();

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
  const watching = useOverlay((s) => s.watching);
  const seeing = useOverlay((s) => s.seeing);
  const captions = useOverlay((s) => s.captions);
  const chat = useOverlay((s) => s.chat);
  const jobs = useOverlay((s) => s.jobs);
  const jobStatus = useOverlay((s) => s.jobStatus);
  const toasts = useOverlay((s) => s.toasts);
  const dismissToast = useOverlay((s) => s.dismissToast);
  const result = toasts[0];
  // A new file gets its own card: the file itself, Open file, Open file location, drag it out.
  const fileResult = !!result && result.ok && result.files.length > 0 && !result.link && !result.actions.includes("update");
  const [left, setLeft] = useState(1); // share of the result's 10 seconds still to go
  const hold = useHoldKeys();

  const [hover, setHover] = useState(false);
  const [full, setFull] = useState(false);
  const [tab, setTab] = useState<Tab>("chat");
  const [pinned, setPinned] = useState(false);
  const [focused, setFocused] = useState(false);
  const [text, setText] = useState("");
  const [now, setNow] = useState(() => performance.now());
  const [hintUntil, setHintUntil] = useState(0);
  const bornAt = useRef(performance.now());
  const leaveTimer = useRef<number>(0);
  const dwellTimer = useRef<number>(0);
  // After you close it, the pointer is still on it: don't reopen until it has left once.
  const suppress = useRef(false);
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
  // The small chat is open only when asked for (double-tap Ctrl) or while you're typing in
  // it, never because the pointer wandered over the top edge. Status states (Listening,
  // Thinking, captions, notices) never catch the mouse.
  const open = pinned || (focused && text.trim() !== "");
  const resting: Mode = full ? "full"
    : result ? "result"
    : open ? "open"
    : state === "listening" ? "listening"
    : state === "transcribing" || state === "thinking" || state === "looking" ? "thinking"
    : state === "speaking" && caption ? "speaking"
    : notice && now - notice.at < NOTICE_MS ? "notice"
    : !connected ? "offline"
    : now < hintUntil ? "hint"
    : running.length ? "working"
    : paused ? "paused"
    : "idle";
  // The pointer resting on it: a small tab to click, and nothing more.
  const mode: Mode = hover && RESTING.has(resting) ? "peek" : resting;

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

  // A finished errand shows for 10 seconds, paused while the pointer is on it.
  useEffect(() => {
    if (!result) return;
    let shown = 0;
    let last = performance.now();
    setLeft(1);
    const id = window.setInterval(() => {
      const t = performance.now();
      if (!over.current.handle && !over.current.body) shown += t - last;
      last = t;
      const limit = fileResult ? FILE_RESULT_MS : RESULT_MS;
      setLeft(Math.max(0, 1 - shown / limit));
      if (shown >= limit) dismissToast(result.job_id);
    }, 80);
    return () => window.clearInterval(id);
  }, [result?.job_id, dismissToast, fileResult]);

  // The full notch: Ctrl+Alt+W / the tray icon toggle it; Escape folds it away.
  const openFull = (t?: Tab) => {
    if (t) setTab(t);
    setFull(true);
    setPinned(false);
    bridge.focusOverlay(); // so its text boxes take typing
  };
  useEffect(() => bridge.onNotchToggle(() => (full ? setFull(false) : openFull())), [full]);
  useEffect(() => bridge.onNotchOpen((t) => openFull(t as Tab)), []);
  useEffect(() => {
    if (!full) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && close();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [full]);

  // Double-tap Ctrl: open with the text box ready.
  useEffect(
    () =>
      bridge.onQuickType(() => {
        setPinned(true);
        requestAnimationFrame(() => requestAnimationFrame(() => input.current?.focus()));
      }),
    [],
  );

  // Two areas take the pointer: the thin strip on the top edge and the open notch itself.
  // It folds only once the pointer has left both (leaving the strip while moving down into
  // the notch must not count).
  const over = useRef({ handle: false, body: false });
  const enter = (where: "handle" | "body") => {
    over.current[where] = true;
    window.clearTimeout(leaveTimer.current);
    if (suppress.current) return;
    window.clearTimeout(dwellTimer.current);
    dwellTimer.current = window.setTimeout(() => setHover(true), DWELL_MS);
  };
  const leave = (where: "handle" | "body") => {
    over.current[where] = false;
    if (over.current.handle || over.current.body) return;
    suppress.current = false;
    window.clearTimeout(dwellTimer.current);
    window.clearTimeout(leaveTimer.current);
    leaveTimer.current = window.setTimeout(() => setHover(false), 220);
  };
  const close = () => {
    setPinned(false);
    setHover(false);
    setFull(false);
    suppress.current = true; // fold all the way, even with the pointer still on it
    input.current?.blur();
    bridge.releaseFocus(); // hand the keyboard back to the app you were in
  };
  const send = (e: FormEvent) => {
    e.preventDefault();
    const t = text.trim();
    if (!t) return;
    voice.stop();
    brain.send({ type: "interrupt" });
    brain.send({ type: "text", text: t });
    setText("");
    setPinned(false);
    input.current?.blur(); // fold away and let the answer play in the notch
    bridge.releaseFocus();
  };

  const level = mode === "listening" ? micLevel : () => voice.level();
  // Say exactly what Pyxi is reading, so you're never unsure what it sees.
  const status = state === "looking" ? `Looking at ${seeing || "your screen"}` : state === "transcribing" ? "Got it" : "Thinking";

  return (
    <div className={`notch-zone ${open ? "is-open" : ""}`}>
      {/* Folded, the only thing that reacts is a thin strip on the top edge, so the browser
          tabs underneath stay clickable. */}
      <Interactive className="notch-handle" onEnter={() => enter("handle")} onLeave={() => leave("handle")} />
      <Interactive
        className="notch-hit"
        active={mode === "open" || mode === "full" || mode === "result" || mode === "peek"}
        // Where the notch is growing to, plus a forgiving margin: while it animates wider, a
        // pointer heading for its far corner must not count as having left.
        hitRect={() => {
          const w = (mode === "result" && fileResult ? FILE_WIDTH : WIDTH[mode]) + 24;
          return { left: innerWidth / 2 - w / 2, right: innerWidth / 2 + w / 2, top: 0, bottom: height + 14 };
        }}
        onEnter={() => enter("body")}
        onLeave={() => leave("body")}
      >
      <div
        className={`notch mode-${mode}`}
        style={{ width: mode === "speaking" && !captions ? 210 : mode === "result" && fileResult ? FILE_WIDTH : WIDTH[mode], height }}
        onClick={() => mode !== "open" && mode !== "full" && openFull()}
      >
        <i className="ear left" />
        <i className="ear right" />
        {/* Looking (a turn, or the Memory Palace in the background) and listening are always
            visible: the notch's own text says Listening / Looking at..., and this dot stays lit
            while background watching is on. */}
        {watching && <i className="notch-watching" title="Memory Palace is on: Pyxi notes which windows you use. Ctrl+Alt+P pauses it." aria-label="Memory Palace is on" />}
        <div ref={inner} className="notch-inner" key={mode}>
          {mode === "idle" && running.length > 0 && <Marks jobs={running.map((j) => j.agent_name)} />}

          {mode === "peek" && (
            <div className="notch-row peek">
              <Firefly />
              <span className="notch-text strong">Open Pyxi</span>
              <span className="notch-peek-key">click</span>
            </div>
          )}

          {mode === "hint" && (
            <div className="notch-row">
              <Firefly />
              <span className="notch-text">
                {hold.verb} {hold.keys.map((k) => <kbd key={k}>{k}</kbd>)} and just talk
              </span>
            </div>
          )}

          {mode === "listening" && (
            <div className="notch-row">
              <Firefly live />
              <span className="notch-text strong">Listening</span>
              <Wave level={level} />
            </div>
          )}

          {mode === "thinking" && (
            <div className="notch-row">
              <Firefly spin />
              <span className="notch-text shimmer">{status}</span>
            </div>
          )}

          {mode === "speaking" && caption && (
            <div className="notch-row top">
              <Firefly live />
              <div className="notch-say">
                {caption.agent !== "Pyxi" && <b>{caption.agent}</b>}
                {captions ? caption.text : <span className="notch-text">{caption.agent === "Pyxi" ? "Speaking" : "Talking"}</span>}
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
              <Firefly spin />
              {now - bornAt.current < WAKE_MS ? (
                <span className="notch-text shimmer">Waking up</span>
              ) : (
                <span className="notch-text">Can't reach Pyxi's brain. Run <kbd>pyxi serve</kbd></span>
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

          {mode === "result" && result && fileResult && (
            <div className="notch-result notch-file">
              <button className="notch-btn file-close" title="Close" onClick={(e) => { e.stopPropagation(); dismissToast(result.job_id); }}><Close /></button>
              <FileReady result={result} />
              {result.actions.includes("send_back") && (
                <div className="result-actions file-extra">
                  <button onClick={(e) => { e.stopPropagation(); brain.send({ type: "text", text: "Send the fixed file back to the person who emailed me the original, as a reply." }); dismissToast(result.job_id); }}>Send it back</button>
                </div>
              )}
              <i className="result-timer" style={{ transform: `scaleX(${left})` }} />
            </div>
          )}

          {mode === "result" && result && !fileResult && (
            <div className={`notch-result ${result.ok ? "" : "failed"}`}>
              <div className="notch-head">
                <i className="result-mark" />
                <b>{result.title}</b>
                <span />
                <button className="notch-btn" title="Close" onClick={(e) => { e.stopPropagation(); dismissToast(result.job_id); }}><Close /></button>
              </div>
              <p className="result-body">{result.body}</p>
              {result.changes.length > 0 && (
                <ul className="result-changes">{result.changes.slice(0, 3).map((c) => <li key={c}>{c}</li>)}</ul>
              )}
              <div className="result-actions">
                {result.link && (
                  <button className="primary" onClick={() => bridge.openExternal(result.link!)}>
                    {result.link.includes("meet.google.com") ? "Join Google Meet" : "Open"}
                  </button>
                )}
                {result.link?.includes("meet.google.com") && <span className="result-link">{result.link.replace("https://", "")}</span>}
                {result.files[0] && <button className="primary" onClick={() => bridge.openFile(result.files[0])}>Open file</button>}
                {result.actions.includes("folder") && result.files[0] && <button onClick={() => bridge.showInFolder(result.files[0])}>Show in folder</button>}
                {result.actions.includes("update") && (
                  <button className="primary" onClick={() => bridge.installUpdate()}>Restart to update</button>
                )}
                {result.actions.includes("calendar") && result.calendar && (
                  <button className="primary" onClick={() => { brain.send({ type: "text", text: result.calendar! }); dismissToast(result.job_id); }}>Add to calendar</button>
                )}
                {result.actions.includes("send_back") && (
                  <button onClick={() => { brain.send({ type: "text", text: "Send the fixed file back to the person who emailed me the original, as a reply." }); dismissToast(result.job_id); }}>Send it back</button>
                )}
              </div>
              <i className="result-timer" style={{ transform: `scaleX(${left})` }} />
            </div>
          )}

          {mode === "full" && (
            <div className="notch-full">
              <div className="notch-head">
                <Firefly live={state === "listening" || state === "speaking"} spin={state === "thinking" || state === "looking" || state === "transcribing"} />
                <b>Pyxi</b>
                <span className={state === "idle" ? "" : "shimmer"}>
                  {state === "idle" ? (connected ? "Ready" : "Waiting for the brain") : state === "listening" ? "Listening" : state === "speaking" ? "Speaking" : status}
                </span>
                <button className="notch-btn" title="Fold away (Esc)" onClick={(e) => { e.stopPropagation(); close(); }}><Close /></button>
              </div>
              <nav className="tabs notch-tabs">
                {TABS.map(([id, label]) => (
                  <button key={id} className={tab === id ? "on" : ""} onClick={() => setTab(id)}>{label}</button>
                ))}
              </nav>
              <main className="body notch-body">
                {tab === "chat" && <Chat />}
                {tab === "team" && <Team />}
                {tab === "routines" && <Routines />}
                {tab === "palace" && <Palace />}
                {tab === "activity" && <Activity />}
                {tab === "gallery" && <Gallery />}
                {tab === "voice" && <VoiceStudio />}
                {tab === "settings" && <Settings />}
              </main>
            </div>
          )}

          {mode === "open" && (
            <div className="notch-open">
              <div className="notch-head">
                <Firefly live={state === "listening" || state === "speaking"} spin={state === "thinking" || state === "looking" || state === "transcribing"} />
                <b>Pyxi</b>
                <span className={state === "idle" ? "" : "shimmer"}>
                  {state === "idle" ? (connected ? "Ready" : "Waiting for the brain") : state === "listening" ? "Listening" : state === "speaking" ? "Speaking" : status}
                </span>
                <button className="notch-btn" title="Everything: team, memory, 3D, voice, settings" onClick={(e) => { e.stopPropagation(); openFull(); }}><Expand /></button>
              </div>
              {running.map((j) => (
                <div key={j.id} className="notch-job">
                  <Marks jobs={[j.agent_name]} />
                  <b>{j.agent_name}</b>
                  <span>{jobStatus[j.id] ?? j.task.split("\n")[0]}</span>
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
                    {l.who === "agent" && l.agent && l.agent !== "Pyxi" && <b>{l.agent}</b>}
                    {l.text}
                  </div>
                ))}
              </div>
              <form className="notch-input" onSubmit={send}>
                <input
                  ref={input}
                  value={text}
                  placeholder="Ask Pyxi anything"
                  onChange={(e) => {
                    // The first key of a new message: Pyxi starts looking at the screen now, not after Enter.
                    if (!text && e.target.value) brain.send({ type: "look_ahead" });
                    setText(e.target.value);
                  }}
                  onMouseDown={() => bridge.focusOverlay()}
                  onFocus={() => setFocused(true)}
                  onBlur={() => {
                    setFocused(false);
                    setPinned(false);
                  }}
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
    </div>
  );
}

/** The firefly in small: a warm dot with a halo that glows while Pyxi talks and flickers while it thinks. */
function Firefly({ live = false, spin = false }: { live?: boolean; spin?: boolean }) {
  return (
    <svg className={`notch-mark ${live ? "live" : ""} ${spin ? "spin" : ""}`} width="14" height="14" viewBox="-7 -7 14 14">
      <circle className="nm-halo" r="6.5" />
      <circle className="nm-core" r="2.6" />
    </svg>
  );
}

const AGENT_COLORS: Record<string, string> = { Scout: "#7dd3fc", Quill: "#fcd34d", Sketch: "#f9a8d4", Pyxi: "#ffc861" };

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
