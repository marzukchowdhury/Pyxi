// Overlay state. Every overlay window (one per display) receives every brain event; only the
// primary display's overlay plays audio and records the microphone, so nothing doubles up.
import { create } from "zustand";
import { AudioQueue, RemoteVoice, type Voice } from "../shared/audio";
import { bridge, currentDisplay, onThisDisplay } from "../shared/bridge";
import type { Annotation, BrainEvent, BrainState, Job, NoteCard, ObjectScene, Pin, SceneSpec, Sketchboard, TimelineMoment, Walkthrough } from "../shared/protocol";
import { brain } from "../shared/socket";

export const display = currentDisplay();
export const isPrimary = display.primary;
// One voice: only the main screen's overlay plays sound. Other monitors' overlays hand their
// clips (a model's narration, a drawing's steps) to it, so everything Wisp says is one queue.
export const voice: Voice = isPrimary ? new AudioQueue() : new RemoteVoice();
if (isPrimary) {
  bridge.onVoicePlay((c) => {
    const done = (kind: "end" | "cancel") => {
      bridge.voiceEvent({ id: c.id, from: c.from, kind });
    };
    voice.enqueue({
      b64: c.b64,
      text: c.text,
      owner: `remote:${c.id}`,
      onStart: () => bridge.voiceEvent({ id: c.id, from: c.from, kind: "start" }),
      onEnd: () => done("end"),
      onCancel: () => done("cancel"),
    });
  });
  bridge.onVoiceStop((m) => {
    if (!m.ids) voice.stop();
    else m.ids.forEach((id) => voice.stop(`remote:${id}`));
  });
}
// Lines the main screen is saying, kept here until it starts each one (to reveal points in sync).
const pendingSays = new Map<string, { notes?: number[]; annotations: Annotation[] }>();
const sayKey = (e: { index: number; total: number; text: string }) => `${e.index}/${e.total}:${e.text.slice(0, 60)}`;
if (!isPrimary) {
  bridge.onSayStarted((key) => {
    const e = pendingSays.get(key);
    if (!e) return;
    pendingSays.delete(key);
    addAnnotations(e.annotations);
    reveal(e.notes);
  });
}

export interface LiveAnnotation extends Annotation {
  bornAt: number;
}

interface Clip {
  audio: string | null;
  text: string;
}

export interface ChatLine {
  id: number;
  who: "user" | "agent" | "system";
  agent?: string;
  text: string;
}

interface Request {
  kind: "confirm" | "ask";
  request_id: string;
  agent: string;
  action?: string;
  reason?: string;
  question?: string;
  /** A third button, e.g. "Always allow" (answers "always"). */
  always?: string;
}

interface OverlayState {
  connected: boolean;
  state: BrainState;
  paused: boolean;
  caption: { text: string; agent: string } | null;
  annotations: LiveAnnotation[];
  flyTo: { x: number; y: number; label: string | null; until: number } | null;
  scene: { spec: SceneSpec; step: number; playing: boolean; hologram: boolean; place: [number, number, number, number] | null } | null;
  palace: { snapshotId: number; timeline: TimelineMoment[] } | null;
  /** A model built from parts. `audioKey` changes when the narration does; `version` on every edit. */
  sketch: { board: Sketchboard; audioKey: string; place: [number, number, number, number] | null } | null;
  object: { spec: ObjectScene; audioKey: string; version: number; restart: boolean; place: [number, number, number, number] | null } | null;
  sceneAudio: Record<string, Record<number, Clip>>;
  walkthrough: { walk: Walkthrough; step: number; playing: boolean } | null;
  walkAudio: Record<number, Clip>;
  jobs: Record<string, Job>;
  jobStatus: Record<string, string>;
  handoff: { source: string; target: string; at: number } | null;
  requests: Request[];
  pins: Pin[];
  captureFlash: number;
  listening: boolean;
  /** The notch's short conversation: the last few exchanges, sentences of one reply merged. */
  chat: ChatLine[];
  notice: { text: string; at: number } | null;
  /** The app Wisp last looked at ("Chrome"), shown while it reads the screen. */
  seeing: string;
  /** Show Wisp's words in the notch while it talks (voice settings). */
  captions: boolean;
  /** The note card on screen; `revealed` holds the point numbers written so far. */
  notes: { card: NoteCard; revealed: number[]; markers: boolean; at: number } | null;
  guide: { step: number; total: number | null; goal: string; label: string; action: string } | null;
  /** Finished background jobs, shown quietly as cards (newest first). */
  toasts: { job_id: string; agent: string; ok: boolean; title: string; body: string; link?: string | null; files: string[]; changes: string[]; notes: string[]; actions: string[] }[];
  dismissToast(id: string): void;
  closeNotes(): void;
  setStep(step: number): void;
  setPlaying(p: boolean): void;
  closeScene(): void;
  closeObject(): void;
  closeSketch(): void;
  toggleHologram(): void;
  setWalkStep(step: number): void;
  closeWalkthrough(): void;
  answer(id: string, value: unknown): void;
  dismissAll(): void;
}

export const useOverlay = create<OverlayState>((set, get) => ({
  connected: false,
  state: "idle",
  paused: false,
  caption: null,
  annotations: [],
  flyTo: null,
  scene: null,
  palace: null,
  object: null,
  sketch: null,
  sceneAudio: {},
  walkthrough: null,
  walkAudio: {},
  jobs: {},
  jobStatus: {},
  handoff: null,
  requests: [],
  pins: [],
  captureFlash: 0,
  listening: false,
  chat: [],
  notice: null,
  seeing: "",
  captions: true,
  notes: null,
  guide: null,
  toasts: [],
  dismissToast: (id) => set({ toasts: get().toasts.filter((t) => t.job_id !== id) }),
  closeNotes: () => set({ notes: null }),
  setStep: (step) => {
    const s = get().scene;
    if (!s) return;
    const clamped = Math.max(0, Math.min(step, s.spec.steps.length - 1));
    set({ scene: { ...s, step: clamped } });
  },
  setPlaying: (playing) => {
    const s = get().scene;
    if (s) set({ scene: { ...s, playing } });
  },
  closeScene: () => {
    voice.stop("scene");
    set({ scene: null, palace: null });
  },
  closeSketch: () => {
    voice.stop("sketch");
    set({ sketch: null });
  },
  closeObject: () => {
    voice.stop("object");
    set({ object: null });
  },
  toggleHologram: () => {
    const s = get().scene;
    if (s) set({ scene: { ...s, hologram: !s.hologram } });
  },
  setWalkStep: (step) => {
    const w = get().walkthrough;
    if (w) set({ walkthrough: { ...w, step: Math.max(0, Math.min(step, w.walk.steps.length - 1)) } });
  },
  closeWalkthrough: () => {
    voice.stop();
    set({ walkthrough: null });
  },
  answer: (id, value) => {
    brain.send({ type: "answer", request_id: id, value });
    set({ requests: get().requests.filter((r) => r.request_id !== id) });
  },
  dismissAll: () => {
    voice.stop();
    brain.send({ type: "interrupt" });
    if (get().guide) brain.send({ type: "guide_cancel" });
    set({ annotations: [], scene: null, palace: null, object: null, sketch: null, notes: null, guide: null, walkthrough: null, caption: null, flyTo: null, pins: [] });
  },
}));

/** "chrome.exe" -> "Chrome", "WINWORD" -> "Word". */
const APP_NAMES: Record<string, string> = { winword: "Word", excel: "Excel", powerpnt: "PowerPoint", msedge: "Edge", code: "VS Code", explorer: "File Explorer" };
export function prettyApp(app: string): string {
  const base = app.replace(/\.exe$/i, "").trim();
  const known = APP_NAMES[base.toLowerCase()];
  if (known) return known;
  return base ? base[0].toUpperCase() + base.slice(1) : "";
}

/** Write the next note point(s): the ones the sentence names, or (without markers) the next one. */
// Wisp starts talking before the cue card has finished arriving: points named in a sentence
// that's already playing are remembered and shown the moment the card lands.
let earlyNotes: number[] = [];

/** Clear the spoken caption once Wisp has really finished talking (not at a fixed moment, which
 * left it stuck on screen whenever audio was still playing then). */
let captionTimer: number | undefined;
function clearCaptionWhenQuiet(): void {
  window.clearTimeout(captionTimer);
  const check = () => {
    const { state } = useOverlay.getState();
    if (voice.busy || (state !== "idle" && state !== "listening")) captionTimer = window.setTimeout(check, 600);
    else {
      const shown = useOverlay.getState().caption;
      captionTimer = window.setTimeout(() => {
        // Only the caption we were waiting on: a new line may have started meanwhile.
        if (!voice.busy && useOverlay.getState().caption === shown) useOverlay.setState({ caption: null });
      }, 2500);
    }
  };
  check();
}

function reveal(numbers: number[] | undefined): void {
  const n = useOverlay.getState().notes;
  if (!n) {
    if (numbers?.length) earlyNotes = [...new Set([...earlyNotes, ...numbers])];
    return;
  }
  const markers = n.markers || !!numbers?.length;
  let next = numbers?.length ? numbers : [];
  if (!markers) {
    const pending = n.card.items.map((i) => i.n).filter((x) => !n.revealed.includes(x));
    next = pending.slice(0, 1);
  }
  if (!next.length && n.markers === markers) return;
  useOverlay.setState({ notes: { ...n, markers, revealed: [...new Set([...n.revealed, ...next])] } });
}

function onDisplay(a: Annotation): boolean {
  const p = a.at ?? (a.rect ? [a.rect[0] + a.rect[2] / 2, a.rect[1] + a.rect[3] / 2] : null);
  return !!p && onThisDisplay(p[0], p[1]);
}

function addAnnotations(list: Annotation[]): void {
    const now = performance.now();
  const mine = list.filter(onDisplay).map((a) => ({ ...a, bornAt: now }));
  // A new click target replaces the old one.
  if (mine.some((a) => a.guide)) useOverlay.setState((s) => ({ annotations: s.annotations.filter((a) => !a.guide) }));
  if (!mine.length) return;
  const point = mine.find((a) => a.kind === "point" && a.at);
  useOverlay.setState((s) => ({
    annotations: [...s.annotations.filter((a) => now - a.bornAt < a.ttl_s * 1000), ...mine],
    flyTo: point?.at ? { x: point.at[0], y: point.at[1], label: point.label, until: now + 3400 } : s.flyTo,
  }));
}

/** Is a brain-placed rect on this overlay's display? Unplaced things go to the main screen. */
function placedHere(place: [number, number, number, number] | null | undefined): boolean {
  return place ? onThisDisplay(place[0] + place[2] / 2, place[1] + place[3] / 2) : isPrimary;
}

function sceneOnThisDisplay(spec: SceneSpec, place?: [number, number, number, number] | null): boolean {
  if (place) return placedHere(place);
  const r = spec.anchor?.rect;
  if (r) return onThisDisplay(r[0] + r[2] / 2, r[1] + r[3] / 2);
  const rects = spec.nodes.map((n) => n.screen_rect).filter(Boolean) as number[][];
  if (rects.length) return onThisDisplay(rects[0][0] + rects[0][2] / 2, rects[0][1] + rects[0][3] / 2);
  return isPrimary;
}

brain.onStatus((up) => {
  useOverlay.setState({ connected: up });
  if (!up) document.documentElement.classList.remove("capturing");
});

let lineId = 0;
let replyOpen = false; // later sentences of the same reply join the open line
function pushLine(line: Omit<ChatLine, "id">): void {
  useOverlay.setState((s) => {
    const last = s.chat[s.chat.length - 1];
    if (line.who === "agent" && replyOpen && last?.who === "agent" && last.agent === line.agent) {
      return { chat: [...s.chat.slice(0, -1), { ...last, text: `${last.text} ${line.text}` }] };
    }
    return { chat: [...s.chat.slice(-11), { ...line, id: ++lineId }] };
  });
  replyOpen = line.who === "agent";
}

brain.on((e: BrainEvent) => {
  switch (e.type) {
    case "hello":
    case "settings":
      useOverlay.setState({ state: e.state, paused: e.privacy_paused, captions: e.voice?.captions ?? true, jobs: Object.fromEntries(e.jobs.map((j) => [j.id, j])) });
      break;
    case "state":
      useOverlay.setState({ state: e.state });
      if (e.state === "idle") clearCaptionWhenQuiet();
      break;
    case "user":
      replyOpen = false;
      earlyNotes = [];
      pendingSays.clear();
      pushLine({ who: "user", text: e.text });
      break;
    case "turn_done":
      replyOpen = false;
      break;
    case "error":
      replyOpen = false;
      pushLine({ who: "system", text: e.message });
      useOverlay.setState({ notice: { text: e.message, at: performance.now() } });
      break;
    case "say":
      if (e.text) pushLine({ who: "agent", agent: e.agent, text: e.text });
      if (isPrimary) {
        voice.enqueue({
          b64: e.audio,
          text: e.text,
          onStart: () => {
            useOverlay.setState({ caption: { text: e.text, agent: e.agent } });
            addAnnotations(e.annotations);
            reveal(e.notes);
            bridge.sayStarted(sayKey(e)); // other monitors reveal their points now too
          },
        });
      } else pendingSays.set(sayKey(e), { notes: e.notes, annotations: e.annotations });
      break;
    case "stop_audio":
      voice.stop(); // every window: the main screen's player, and anything queued from here
      pendingSays.clear();
      break;
    case "annotate":
      addAnnotations(e.annotations);
      break;
    case "clear":
      // The screen moved on (another window, or Wisp was asked to clear): drop everything that
      // points at specific pixels.
      useOverlay.setState({ annotations: [], flyTo: null, notes: null, sketch: null });
      break;
    case "capture_hide":
      document.documentElement.classList.add("capturing");
      // Never stay invisible if the "show" is lost (a reconnect mid-capture).
      window.setTimeout(() => document.documentElement.classList.remove("capturing"), 1500);
      break;
    case "capture_show":
      document.documentElement.classList.remove("capturing");
      break;
    case "capture":
      useOverlay.setState({ captureFlash: performance.now(), seeing: e.excluded ? "" : prettyApp(e.app) });
      break;
    case "scene":
      if (!sceneOnThisDisplay(e.scene, e.place)) {
        useOverlay.setState({ scene: null, object: null }); // the new one opens on another screen
        break;
      }
      useOverlay.setState({ scene: { spec: e.scene, step: 0, playing: e.scene.auto_play, hologram: e.scene.mode === "hologram", place: e.place ?? null }, palace: null, object: null });
      break;
    case "object":
      // Models open on the screen the brain placed them on (the one you're looking at); an edit
      // keeps the same id so the view keeps its camera.
      if (!placedHere(e.place)) {
        useOverlay.setState({ object: null, scene: null }); // the new model opens on another screen
        break;
      }
      useOverlay.setState((s) => ({
          object: { spec: e.scene, audioKey: e.audio_key, restart: e.restart, place: e.place ?? null, version: (s.object?.spec.id === e.scene.id ? s.object.version : 0) + 1 },
          scene: null,
          palace: null,
        }));
      break;
    case "palace":
      if (isPrimary || sceneOnThisDisplay(e.scene))
        useOverlay.setState({
          scene: { spec: e.scene, step: 0, playing: false, hologram: true, place: null },
          palace: { snapshotId: e.snapshot_id, timeline: e.timeline },
        });
      break;
    case "sketch":
      // A new drawing replaces the old one, on the screen you're looking at.
      useOverlay.setState({
        sketch: placedHere(e.place) ? { board: e.board, audioKey: e.audio_key, place: e.place ?? null } : null,
      });
      break;
    case "scene_audio":
      useOverlay.setState((s) => ({
        sceneAudio: { ...s.sceneAudio, [e.scene_id]: { ...(s.sceneAudio[e.scene_id] ?? {}), [e.step]: { audio: e.audio, text: e.text } } },
      }));
      break;
    case "walkthrough":
      if (isPrimary) useOverlay.setState({ walkthrough: { walk: e.walkthrough, step: 0, playing: true }, walkAudio: {} });
      break;
    case "walkthrough_audio":
      useOverlay.setState((s) => ({ walkAudio: { ...s.walkAudio, [e.step]: { audio: e.audio, text: e.text } } }));
      break;
    case "job":
      useOverlay.setState((s) => ({ jobs: { ...s.jobs, [e.job.id]: e.job } }));
      break;
    case "job_event":
      if (e.event === "status" && typeof e.data.message === "string")
        useOverlay.setState((s) => ({ jobStatus: { ...s.jobStatus, [e.job_id]: e.data.message as string } }));
      break;
    case "handoff":
      useOverlay.setState({ handoff: { source: e.source, target: e.target, at: performance.now() } });
      break;
    case "confirm":
      if (isPrimary) useOverlay.setState((s) => ({ requests: [...s.requests, { kind: "confirm", ...e }] }));
      break;
    case "ask":
      if (isPrimary) useOverlay.setState((s) => ({ requests: [...s.requests, { kind: "ask", ...e }] }));
      break;
    case "notes": {
      const r = e.card.region;
      const mine = r ? onThisDisplay(r[0] + r[2] / 2, r[1] + r[3] / 2) : isPrimary;
      const cur = useOverlay.getState().notes;
      if (cur && cur.card.id === e.card.id) {
        // The same card, filling in point by point: keep what's already been revealed.
        if (mine) useOverlay.setState({ notes: { ...cur, card: e.card } });
        break;
      }
      const early = earlyNotes.filter((x) => e.card.items.some((i: { n: number }) => i.n === x));
      earlyNotes = [];
      if (mine) useOverlay.setState({ notes: { card: e.card, revealed: early, markers: early.length > 0, at: performance.now() } });
      break;
    }
    case "guide":
      useOverlay.setState({
        guide: e.active ? { step: e.step ?? 1, total: e.total ?? null, goal: e.goal ?? "", label: e.label ?? "", action: e.action ?? "click" } : null,
      });
      if (!e.active) useOverlay.setState((s) => ({ annotations: s.annotations.filter((a) => !a.guide) }));
      break;
    case "job_ready":
      if (isPrimary) {
        const { type: _type, ...toast } = e;
        // Results drop down from the notch, one at a time (newest first).
        useOverlay.setState((s) => ({ toasts: [toast, ...s.toasts.filter((t) => t.job_id !== e.job_id)].slice(0, 3) }));
      }
      break;
    case "action":
      if (isPrimary) bridge.runAction(e.kind, e.value);
      break;
    case "pins":
      useOverlay.setState({ pins: e.pins.filter((p) => onThisDisplay(p.rect[0], p.rect[1])) });
      break;
  }
});
