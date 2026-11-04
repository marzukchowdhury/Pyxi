import { create } from "zustand";
import { backendUrl } from "../shared/bridge";
import type { Agent, BrainEvent, BrainState, ConnectorStatus, Hello, Job, RoutineInfo, VoiceInfo, VoicePublic } from "../shared/protocol";
import { brain } from "../shared/socket";

export interface ChatLine {
  id: string;
  who: "user" | "agent" | "system";
  agent?: string;
  text: string;
  ts: number;
}

interface Request {
  request_id: string;
  kind: "confirm" | "ask";
  agent: string;
  text: string;
}

interface PanelState {
  connected: boolean;
  hello: Hello | null;
  state: BrainState;
  chat: ChatLine[];
  agents: Agent[];
  jobs: Record<string, Job>;
  frames: Record<string, string>;
  jobLog: Record<string, string[]>;
  requests: Request[];
  spentToday: number;
  lastTurnCost: number;
  voice: VoicePublic | null;
  voiceCatalog: Record<string, VoiceInfo[]> | null;
  voicePreview: { audio: string | null; text: string; at: number } | null;
  connectors: ConnectorStatus[] | null;
  connectorMessage: string | null;
  routines: RoutineInfo[] | null;
}

export const usePanel = create<PanelState>(() => ({
  connected: false,
  hello: null,
  state: "idle",
  chat: [],
  agents: [],
  jobs: {},
  frames: {},
  jobLog: {},
  requests: [],
  spentToday: 0,
  lastTurnCost: 0,
  voice: null,
  voiceCatalog: null,
  voicePreview: null,
  connectors: null,
  connectorMessage: null,
  routines: null,
}));

let seq = 0;
function push(line: Omit<ChatLine, "id" | "ts">): void {
  usePanel.setState((s) => {
    const last = s.chat[s.chat.length - 1];
    // Merge consecutive sentences from the same speaker into one bubble.
    if (last && line.who === "agent" && last.who === "agent" && last.agent === line.agent && Date.now() - last.ts < 8000) {
      return { chat: [...s.chat.slice(0, -1), { ...last, text: `${last.text} ${line.text}`, ts: Date.now() }] };
    }
    return { chat: [...s.chat.slice(-199), { ...line, id: `c${seq++}`, ts: Date.now() }] };
  });
}

brain.onStatus((up) => usePanel.setState({ connected: up }));
brain.on((e: BrainEvent) => {
  switch (e.type) {
    case "hello":
    case "settings":
      usePanel.setState({ hello: e, state: e.state, agents: e.agents, spentToday: e.spent_today, voice: e.voice,
        jobs: { ...usePanel.getState().jobs, ...Object.fromEntries(e.jobs.map((j) => [j.id, j])) } });
      break;
    case "state":
      usePanel.setState({ state: e.state });
      break;
    case "user":
      push({ who: "user", text: e.text });
      break;
    case "say":
      if (e.text) push({ who: "agent", agent: e.agent, text: e.text });
      break;
    case "handoff":
      push({ who: "system", text: `${e.source} handed off to ${e.target}: ${e.task}` });
      break;
    case "error":
      push({ who: "system", text: e.message });
      break;
    case "agents":
      usePanel.setState({ agents: e.agents });
      break;
    case "job":
      usePanel.setState((s) => ({ jobs: { ...s.jobs, [e.job.id]: e.job } }));
      break;
    case "job_frame":
      usePanel.setState((s) => ({ frames: { ...s.frames, [e.job_id]: e.image } }));
      break;
    case "job_event":
      if (e.event === "status" || e.event === "action") {
        const text = e.event === "status" ? String(e.data.message) : `${e.data.ok ? "✓" : "✗"} ${e.data.tool}: ${String(e.data.message).slice(0, 120)}`;
        usePanel.setState((s) => ({ jobLog: { ...s.jobLog, [e.job_id]: [...(s.jobLog[e.job_id] ?? []).slice(-40), text] } }));
      }
      break;
    case "confirm":
      usePanel.setState((s) => ({ requests: [...s.requests, { request_id: e.request_id, kind: "confirm", agent: e.agent, text: `${e.action}: ${e.reason}` }] }));
      break;
    case "ask":
      usePanel.setState((s) => ({ requests: [...s.requests, { request_id: e.request_id, kind: "ask", agent: e.agent, text: e.question }] }));
      break;
    case "routines":
      usePanel.setState({ routines: e.routines });
      break;
    case "connectors":
      usePanel.setState((s) => ({ connectors: e.connectors, connectorMessage: e.message ?? s.connectorMessage }));
      break;
    case "voice_catalog":
      usePanel.setState({ voiceCatalog: e.catalog, voice: e.voice });
      break;
    case "voice_preview":
      usePanel.setState({ voicePreview: { audio: e.audio, text: e.text, at: Date.now() } });
      if (e.error) usePanel.setState((s) => ({ voice: s.voice ? { ...s.voice, error: e.error } : s.voice }));
      break;
    case "turn_done":
      usePanel.setState({ spentToday: e.spent_today, lastTurnCost: e.cost_usd });
      break;
  }
});

export function answer(request_id: string, value: unknown): void {
  brain.send({ type: "answer", request_id, value });
  usePanel.setState((s) => ({ requests: s.requests.filter((r) => r.request_id !== request_id) }));
}

export async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(backendUrl + path);
  return (await res.json()) as T;
}
