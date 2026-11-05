import { useEffect, useRef, useState } from "react";
import { bridge, keysOf } from "../shared/bridge";
import { Cube, Mark, Mic, Minus, Plus, Replay, Send, Thread } from "../shared/icons";
import type { Agent, Job } from "../shared/protocol";
import { brain } from "../shared/socket";
import { answer, getJson, usePanel } from "./store";
import { Connections, Routines } from "./Connections";
import { VoiceStudio } from "./VoiceStudio";

export type Tab = "chat" | "team" | "palace" | "gallery" | "voice" | "settings";
export const TABS: [Tab, string][] = [["chat", "Chat"], ["team", "Team"], ["palace", "Memory"], ["gallery", "3D"], ["voice", "Voice"], ["settings", "Settings"]];
const STATE_LABEL: Record<string, string> = {
  idle: "Ready when you are", listening: "Listening…", transcribing: "Catching that…", looking: "Taking a look…",
  thinking: "Thinking…", speaking: "Talking",
};

export function Panel() {
  const [tab, setTab] = useState<Tab>("chat");
  const { connected, hello, state, spentToday, requests } = usePanel();
  return (
    <div className="panel">
      <header className="titlebar">
        <div className={`mark state-${state}`}><Mark size={22} /></div>
        <div className="who">
          <div className="name">Blip</div>
          <div className="status">{connected ? STATE_LABEL[state] ?? state : "Brain offline: run blip serve"}</div>
        </div>
        <div className="spacer" />
        {hello && (
          <div className="meter" title="Model spend today (all turns and agent jobs)">
            <span>${spentToday.toFixed(3)}</span>
            <i style={{ width: `${Math.min(100, (spentToday / hello.daily_budget) * 100)}%` }} />
          </div>
        )}
        <button className="win" onClick={() => bridge.togglePanel()} title="Hide (Ctrl+Alt+W)"><Minus /></button>
      </header>
      {hello?.demo && <div className="demo-banner">Demo mode: no API key found, so Blip uses a scripted brain. Add OPENROUTER_API_KEY (or ANTHROPIC_API_KEY) to blip/.env, or run <code>blip serve --local</code>.</div>}
      {requests.map((r) => (
        <div key={r.request_id} className="approval">
          <b>{r.agent}</b> {r.kind === "confirm" ? "wants to" : "asks"}: {r.text}
          <div>
            <button className="primary" onClick={() => answer(r.request_id, r.kind === "confirm" ? true : prompt(r.text) ?? "")}>{r.kind === "confirm" ? "Allow" : "Answer"}</button>
            <button onClick={() => answer(r.request_id, r.kind === "confirm" ? false : null)}>{r.kind === "confirm" ? "Don't" : "Skip"}</button>
          </div>
        </div>
      ))}
      <nav className="tabs">
        {TABS.map(([id, label]) => (
          <button key={id} className={tab === id ? "on" : ""} onClick={() => setTab(id)}>{label}</button>
        ))}
      </nav>
      <main className="body">
        {tab === "chat" && <Chat />}
        {tab === "team" && <Team />}
        {tab === "palace" && <Palace />}
        {tab === "gallery" && <Gallery />}
        {tab === "voice" && <VoiceStudio />}
        {tab === "settings" && <Settings />}
      </main>
    </div>
  );
}

// ---------------------------------------------------------------------------- chat
const SUGGESTIONS = ["How does HTTPS work?", "Explode this panel", "How did I get here?", "Have Scout fill in the demo form", "Show me how you did it"];

/** The talk gesture: hold Ctrl + Alt when the keyboard hook is running, else the shortcut. */
function useTalkKeys(): { verb: string; keys: string[] } {
  const [talk, setTalk] = useState({ verb: "Hold", keys: ["Ctrl", "Alt"] });
  useEffect(() => {
    void bridge.hotkeys().then((h) => setTalk(h.hold ? { verb: "Hold", keys: keysOf(h.hold) } : { verb: "Press", keys: keysOf(h.talk) }));
  }, []);
  return talk;
}

export function Chat() {
  const { chat, agents, state } = usePanel();
  const talk = useTalkKeys();
  const [text, setText] = useState("");
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => {
    end.current?.scrollIntoView({ behavior: "smooth" });
  }, [chat.length]);
  const color = (name?: string) => agents.find((a) => a.name === name)?.color ?? "#a78bfa";
  const send = (t: string) => {
    if (!t.trim()) return;
    brain.send({ type: "text", text: t.trim() });
    setText("");
  };
  return (
    <div className="chat">
      <div className="lines">
        {!chat.length && (
          <div className="hello">
            <div className="big-mark"><Mark size={46} /></div>
            <h2>Hi, I'm Blip.</h2>
            <p>
              {talk.keys.length ? <>{talk.verb} {talk.keys.map((k, i) => <span key={k}>{i > 0 && "+"}<kbd>{k}</kbd></span>)} and ask</> : "Click the mic below and ask"} about anything on your screen, or type below. I'll point, draw, explain in 3D, or send a teammate to do it.
            </p>
            <div className="chips">{SUGGESTIONS.map((s) => <button key={s} onClick={() => send(s)}>{s}</button>)}</div>
          </div>
        )}
        {chat.map((l) => (
          <div key={l.id} className={`line ${l.who}`}>
            {l.who === "agent" && <span className="avatar" style={{ ["--c" as string]: color(l.agent) }} />}
            <div className="text">
              {l.who === "agent" && l.agent !== "Blip" && <div className="from" style={{ color: color(l.agent) }}>{l.agent}</div>}
              {l.text}
            </div>
          </div>
        ))}
        {(state === "thinking" || state === "looking") && <div className="line agent pending"><span className="avatar" /><div className="text shimmer-text">{state === "looking" ? "Looking at your screen" : "Thinking"}</div></div>}
        <div ref={end} />
      </div>
      <form className="composer" onSubmit={(e) => { e.preventDefault(); send(text); }}>
        <button type="button" className={`mic ${state === "listening" ? "live" : ""}`} onClick={() => bridge.ptt()} title={`Talk (${talk.keys.join("+") || "click"})`}><Mic /></button>
        <input value={text} onChange={(e) => setText(e.target.value)} placeholder="Ask Blip anything" />
        <button type="submit" className="send" disabled={!text.trim()} aria-label="Send"><Send /></button>
      </form>
    </div>
  );
}

// ---------------------------------------------------------------------------- team
export function Team() {
  const { agents, jobs, frames, jobLog } = usePanel();
  const [creating, setCreating] = useState(false);
  const list = Object.values(jobs).sort((a, b) => b.created_at - a.created_at);
  return (
    <div className="team">
      <div className="section-head"><h3>Your team</h3><button onClick={() => setCreating(!creating)}>{creating ? "Cancel" : <><Plus width={14} height={14} /> New teammate</>}</button></div>
      {creating && <NewAgent onDone={() => setCreating(false)} />}
      <div className="agents">{agents.map((a) => <AgentCard key={a.id} agent={a} busy={list.some((j) => j.agent_id === a.id && j.status === "running")} />)}</div>
      <div className="section-head"><h3>Jobs</h3></div>
      {!list.length && <p className="muted">No jobs yet. Try: “Scout, look up the pricing on linear.app, then Quill, write me a summary.”</p>}
      {list.map((j) => <JobCard key={j.id} job={j} frame={frames[j.id]} log={jobLog[j.id] ?? []} />)}
    </div>
  );
}

function AgentCard({ agent, busy }: { agent: Agent; busy: boolean }) {
  return (
    <div className="agent" style={{ "--c": agent.color } as React.CSSProperties}>
      <svg className={`agent-mark ${busy ? "busy" : ""}`} width="14" height="26" viewBox="0 0 12 22"><path d="M6 1 10.5 11 6 21 1.5 11Z" /></svg>
      <div className="agent-info">
        <div className="agent-name">{agent.name} <span className="role">{agent.role}</span></div>
        <div className="agent-persona">{agent.persona}</div>
        <div className="skills">{agent.skills.map((s) => <span key={s}>{s}</span>)}</div>
      </div>
      {!agent.builtin && <button className="x" onClick={() => brain.send({ type: "agent_delete", id: agent.id })}>✕</button>}
    </div>
  );
}

function NewAgent({ onDone }: { onDone: () => void }) {
  const [name, setName] = useState("");
  const [role, setRole] = useState("browser");
  const [persona, setPersona] = useState("");
  const [voice, setVoice] = useState("af_bella");
  return (
    <form className="new-agent" onSubmit={(e) => { e.preventDefault(); brain.send({ type: "agent_create", name, role, persona, voice }); onDone(); }}>
      <input required value={name} onChange={(e) => setName(e.target.value)} placeholder="Name, e.g. Pixel" />
      <select value={role} onChange={(e) => setRole(e.target.value)}>
        <option value="browser">Browser agent</option><option value="writer">Writer</option><option value="explainer">3D explainer</option>
      </select>
      <textarea required value={persona} onChange={(e) => setPersona(e.target.value)} placeholder="Personality and focus, e.g. 'A patient shopping scout who always checks three stores.'" />
      <select value={voice} onChange={(e) => setVoice(e.target.value)}>
        {["af_bella", "af_heart", "af_nicole", "am_adam", "am_puck", "am_michael", "bf_emma", "bm_george"].map((v) => <option key={v}>{v}</option>)}
      </select>
      <button className="primary" type="submit">Create</button>
    </form>
  );
}

function JobCard({ job, frame, log }: { job: Job; frame?: string; log: string[] }) {
  const [message, setMessage] = useState("");
  const running = job.status === "running";
  const draft = (job.data as { draft?: string } | null)?.draft;
  return (
    <div className={`job ${job.status}`}>
      <div className="job-head">
        <b>{job.agent_name}</b><span className={`badge ${job.status}`}>{job.status}</span>
        <span className="muted">{job.steps ? `${job.steps} steps · ` : ""}${job.cost_usd.toFixed(3)}</span>
      </div>
      <div className="job-task">{job.task}</div>
      {frame && running && <img className="frame" src={`data:image/jpeg;base64,${frame}`} alt="live view" />}
      {running && log.length > 0 && <div className="job-log">{log.slice(-4).map((l, i) => <div key={i}>{l}</div>)}</div>}
      {job.summary && !running && <div className="job-summary">{draft ? <pre>{draft}</pre> : job.summary}</div>}
      <div className="job-actions">
        {running && (
          <>
            <button onClick={() => brain.send({ type: "job_control", job_id: job.id, action: "pause" })}>Pause</button>
            <button onClick={() => brain.send({ type: "job_control", job_id: job.id, action: "resume" })}>Resume</button>
            <button onClick={() => brain.send({ type: "job_control", job_id: job.id, action: "stop" })}>Stop</button>
            <form onSubmit={(e) => { e.preventDefault(); brain.send({ type: "job_control", job_id: job.id, action: "message", text: message }); setMessage(""); }}>
              <input value={message} onChange={(e) => setMessage(e.target.value)} placeholder={`Tell ${job.agent_name}…`} />
            </form>
          </>
        )}
        {job.role === "browser" && job.run_id && !running && (
          <button className="primary" onClick={() => brain.send({ type: "walkthrough", job_id: job.id })}><Replay width={15} height={15} /> Ghost walkthrough</button>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------- memory palace
interface Moment { id: number; ts: number; app: string; title: string; element_count: number }
interface Activity { id: number; ts: number; kind: string; app: string; headline: string }

export function Palace() {
  const { hello } = usePanel();
  const [moments, setMoments] = useState<Moment[]>([]);
  const [activity, setActivity] = useState<Activity[]>([]);
  const [query, setQuery] = useState("");
  const [when, setWhen] = useState("");
  const refresh = () => {
    void getJson<Moment[]>("/api/timeline?limit=40").then(setMoments);
    void getJson<Activity[]>("/api/activity?minutes=240").then((a) => setActivity(a.slice(-60).reverse()));
  };
  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 5000);
    return () => clearInterval(t);
  }, []);
  const set = (key: string, value: unknown) => brain.send({ type: "set", key, value });
  return (
    <div className="palace">
      {hello && !hello.ambient_memory && (
        <div className="palace-optin">
          <b>The Memory Palace is off</b>
          <p>When on, Blip records window titles, what you focus and where UI elements are (no keystrokes, no pixels unless you allow them) so you can time-travel and ask how you got somewhere. Private apps are never recorded, and it all stays on this computer.</p>
          <button className="primary" onClick={() => set("ambient_memory", true)}>Turn on the Memory Palace</button>
        </div>
      )}
      <div className="palace-hero">
        <h3>Memory Palace</h3>
        <p>Blip quietly keeps a spatial history of your screen (what was where, and what you did), so you can travel back in time or ask how you got here. It all stays on this computer.</p>
        <form className="travel" onSubmit={(e) => { e.preventDefault(); brain.send({ type: "time_travel", query, when }); }}>
          <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="What were you looking at? e.g. pricing page" />
          <input value={when} onChange={(e) => setWhen(e.target.value)} placeholder="When? e.g. 20 minutes ago" />
          <button className="primary" type="submit">Travel back</button>
        </form>
        <button className="causal" onClick={() => brain.send({ type: "text", text: "How did I get here?" })}><Thread width={16} height={16} /> How did I get here?</button>
      </div>
      <div className="cols">
        <div>
          <h4>Moments</h4>
          {!moments.length && <p className="muted">Nothing yet. Use your computer for a bit and Blip will build it up.</p>}
          {moments.map((m) => (
            <button key={m.id} className="moment-row" onClick={() => brain.send({ type: "palace_seek", snapshot_id: m.id })}>
              <span className="t">{new Date(m.ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>
              <span className="a">{m.app}</span>
              <span className="w">{m.title}</span>
            </button>
          ))}
        </div>
        <div>
          <h4>Activity</h4>
          {activity.map((a) => (
            <div key={a.id} className={`act ${a.kind}`}><span className="t">{new Date(a.ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</span> {a.headline}</div>
          ))}
        </div>
      </div>
      {hello && (
        <div className="privacy-box">
          <label><input type="checkbox" checked={hello.ambient_memory} onChange={(e) => set("ambient_memory", e.target.checked)} /> Build my Memory Palace (window, focus and UI metadata)</label>
          <label><input type="checkbox" checked={hello.keep_screenshots} onChange={(e) => set("keep_screenshots", e.target.checked)} /> Also keep small screenshots, so time travel shows real pixels</label>
          <button className="danger" onClick={() => confirm("Forget everything Blip remembers about your screen?") && set("forget_screen", true)}>Forget everything</button>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------- gallery
const BLURBS: Record<string, string> = {
  https: "Animated packets between browser, DNS, server and a certificate authority.",
  blip: "A look inside Blip itself: ears, eyes, brain, voice and agents.",
  neural_net: "Layers of neurons, a forward pass, and learning from mistakes.",
  git: "Branches as lanes in time, merging back into main.",
};

export function Gallery() {
  const { hello } = usePanel();
  return (
    <div className="gallery">
      <p className="muted">Ready-made scenes that show off the 3D layer. Click one and it floats over your screen with narration. Orbit with the mouse, step with the arrows.</p>
      {Object.entries(hello?.gallery ?? {}).map(([name, title]) => (
        <button key={name} className={`scene-card s-${name}`} onClick={() => brain.send({ type: "show_scene", name })}>
          <div className="scene-art"><Cube /></div>
          <div><b>{title}</b><span>{BLURBS[name]}</span></div>
        </button>
      ))}
      <button className="scene-card s-explode" onClick={() => brain.send({ type: "text", text: "Explode this panel" })}>
        <div className="scene-art"><Cube /></div>
        <div><b>Explode what's on my screen</b><span>Peel the window you're using apart into a 3D hierarchy, built from the real UI.</span></div>
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------- settings
/** Discord, OBS and Meet can show Blip (default), or never see it (privacy). */
function ShareToggle() {
  const [on, setOn] = useState(true);
  useEffect(() => {
    void bridge.shareVisible().then(setOn);
    return bridge.onShareVisible(setOn);
  }, []);
  return (
    <label>
      <input type="checkbox" checked={on} onChange={(e) => { setOn(e.target.checked); bridge.setShareVisible(e.target.checked); }} /> Show Blip in screen shares (Discord, OBS, Meet). Turn off to keep its drawings out of every capture.
    </label>
  );
}

export function Settings() {
  const { hello } = usePanel();
  const [keys, setKeys] = useState<Record<string, string | null>>({});
  useEffect(() => {
    void bridge.hotkeys().then(setKeys);
  }, []);
  const k = (name: string) => keysOf(keys[name]).join("+") || "unavailable";
  if (!hello) return <p className="muted">Waiting for Blip's brain…</p>;
  const set = (key: string, value: unknown) => brain.send({ type: "set", key, value });
  return (
    <div className="settings">
      <div className="kv"><span>Brain</span><b>{hello.model}{hello.local_mode ? " (local)" : ""}{hello.demo ? " · demo" : ""}</b></div>
      <div className="kv"><span>Voice</span><b>{hello.tts}</b></div>
      <div className="kv"><span>Spent today</span><b>${hello.spent_today.toFixed(3)} of ${hello.daily_budget.toFixed(2)}</b></div>
      <label><input type="checkbox" checked={hello.privacy_paused} onChange={(e) => set("privacy_paused", e.target.checked)} /> Pause screen sharing (Ctrl+Alt+P)</label>
      <label><input type="checkbox" defaultChecked onChange={(e) => set("jobs_headed", e.target.checked)} /> Show teammates' browser windows</label>
      <ShareToggle />
      <h4>Connected accounts</h4>
      <Connections />
      <h4>Routines</h4>
      <Routines />
      <h4>Hotkeys</h4>
      {keys.hold && <div className="kv"><span>Talk (hold, release to send)</span><kbd>{k("hold")}</kbd></div>}
      <div className="kv"><span>Type to Blip</span><kbd>Ctrl, Ctrl</kbd></div>
      <div className="kv"><span>Talk (press to start or stop)</span><kbd>{k("talk")}</kbd></div>
      <div className="kv"><span>Show or hide this panel</span><kbd>{k("panel")}</kbd></div>
      <div className="kv"><span>Dismiss drawings and scenes</span><kbd>{k("dismiss")}</kbd></div>
      <div className="kv"><span>Pause screen sharing</span><kbd>{k("privacy")}</kbd></div>
      <h4>Privacy</h4>
      <p className="muted">Blip only looks at your screen when you talk to it, plus lightweight UI metadata for the Memory Palace. Password managers, banking and password fields are never captured. Everything is stored locally in <code>.blip/</code>. Blip v{hello.version}.</p>
    </div>
  );
}
