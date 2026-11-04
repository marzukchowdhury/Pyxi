// Overlay state. Every overlay window (one per display) receives every brain event; only the
// primary display's overlay plays audio and records the microphone, so nothing doubles up.
import { create } from "zustand";
import { AudioQueue } from "../shared/audio";
import { currentDisplay, onThisDisplay } from "../shared/bridge";
import type { Annotation, BrainEvent, BrainState, Job, Pin, SceneSpec, TimelineMoment, Walkthrough } from "../shared/protocol";
import { brain } from "../shared/socket";

export const display = currentDisplay();
export const isPrimary = display.primary;
export const voice = new AudioQueue();

export interface LiveAnnotation extends Annotation {
  bornAt: number;
}

interface Clip {
  audio: string | null;
  text: string;
}

interface Request {
  kind: "confirm" | "ask";
  request_id: string;
  agent: string;
  action?: string;
  reason?: string;
  question?: string;
}

interface OverlayState {
  connected: boolean;
  state: BrainState;
  paused: boolean;
  caption: { text: string; agent: string } | null;
  annotations: LiveAnnotation[];
  flyTo: { x: number; y: number; label: string | null; until: number } | null;
  scene: { spec: SceneSpec; step: number; playing: boolean; hologram: boolean } | null;
  palace: { snapshotId: number; timeline: TimelineMoment[] } | null;
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
  setStep(step: number): void;
  setPlaying(p: boolean): void;
  closeScene(): void;
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
    voice.stop();
    set({ scene: null, palace: null });
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
    set({ annotations: [], scene: null, palace: null, walkthrough: null, caption: null, flyTo: null, pins: [] });
  },
}));

function onDisplay(a: Annotation): boolean {
  const p = a.at ?? (a.rect ? [a.rect[0] + a.rect[2] / 2, a.rect[1] + a.rect[3] / 2] : null);
  return !!p && onThisDisplay(p[0], p[1]);
}

function addAnnotations(list: Annotation[]): void {
  const now = performance.now();
  const mine = list.filter(onDisplay).map((a) => ({ ...a, bornAt: now }));
  if (!mine.length) return;
  const point = mine.find((a) => a.kind === "point" && a.at);
  useOverlay.setState((s) => ({
    annotations: [...s.annotations.filter((a) => now - a.bornAt < a.ttl_s * 1000), ...mine],
    flyTo: point?.at ? { x: point.at[0], y: point.at[1], label: point.label, until: now + 3400 } : s.flyTo,
  }));
}

function sceneOnThisDisplay(spec: SceneSpec): boolean {
  const r = spec.anchor?.rect;
  if (r) return onThisDisplay(r[0] + r[2] / 2, r[1] + r[3] / 2);
  const rects = spec.nodes.map((n) => n.screen_rect).filter(Boolean) as number[][];
  if (rects.length) return onThisDisplay(rects[0][0] + rects[0][2] / 2, rects[0][1] + rects[0][3] / 2);
  return isPrimary;
}

brain.onStatus((up) => useOverlay.setState({ connected: up }));

brain.on((e: BrainEvent) => {
  switch (e.type) {
    case "hello":
    case "settings":
      useOverlay.setState({ state: e.state, paused: e.privacy_paused, jobs: Object.fromEntries(e.jobs.map((j) => [j.id, j])) });
      break;
    case "state":
      useOverlay.setState({ state: e.state });
      if (e.state === "idle") setTimeout(() => !voice.busy && useOverlay.setState({ caption: null }), 2500);
      break;
    case "say":
      if (isPrimary) {
        voice.enqueue({
          b64: e.audio,
          text: e.text,
          onStart: () => {
            useOverlay.setState({ caption: { text: e.text, agent: e.agent } });
            addAnnotations(e.annotations);
          },
        });
      } else addAnnotations(e.annotations);
      if (!isPrimary) useOverlay.setState({ caption: { text: e.text, agent: e.agent } });
      break;
    case "stop_audio":
      if (isPrimary) voice.stop();
      break;
    case "annotate":
      addAnnotations(e.annotations);
      break;
    case "clear":
      useOverlay.setState({ annotations: [], flyTo: null });
      break;
    case "capture":
      useOverlay.setState({ captureFlash: performance.now() });
      break;
    case "scene":
      if (sceneOnThisDisplay(e.scene))
        useOverlay.setState({ scene: { spec: e.scene, step: 0, playing: e.scene.auto_play, hologram: e.scene.mode === "hologram" }, palace: null });
      break;
    case "palace":
      if (isPrimary || sceneOnThisDisplay(e.scene))
        useOverlay.setState({
          scene: { spec: e.scene, step: 0, playing: false, hologram: true },
          palace: { snapshotId: e.snapshot_id, timeline: e.timeline },
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
    case "pins":
      useOverlay.setState({ pins: e.pins.filter((p) => onThisDisplay(p.rect[0], p.rect[1])) });
      break;
  }
});
