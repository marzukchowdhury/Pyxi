// TypeScript mirror of the brain's events (src/wisp/*). Coordinates are physical screen px.

export type Rect = [number, number, number, number];
export type BrainState = "idle" | "listening" | "transcribing" | "looking" | "thinking" | "speaking";

export interface Annotation {
  id: string;
  kind: "point" | "circle" | "highlight" | "arrow" | "click";
  rect: Rect | null;
  at: [number, number] | null;
  to: [number, number] | null;
  label: string | null;
  ttl_s: number;
  guide?: boolean;
}

export interface NoteItem {
  n: number;
  text: string;
  kind: "point" | "step" | "formula" | "aside";
  mark: "frame" | "underline" | "circle" | "none";
  rect: Rect | null;
}

export interface Sketch {
  kind: "path" | "arrow" | "label" | "tick" | "cross";
  points: [number, number][];
  text: string | null;
  after: number;
}

export interface NoteCard {
  id: string;
  title: string;
  items: NoteItem[];
  sketches: Sketch[];
  region: Rect | null;
  place?: [number, number] | null;
}

export type Shape =
  | "box" | "sphere" | "cylinder" | "cone" | "torus" | "capsule" | "card" | "chip" | "screen"
  | "server" | "user" | "database" | "cloud";

export interface SceneNode {
  id: string;
  label: string;
  detail?: string | null;
  shape: Shape;
  color?: string | null;
  size: number;
  parent?: string | null;
  group?: string | null;
  position?: [number, number, number] | null;
  screen_rect?: Rect | null;
  image?: string | null;
  depth?: number | null;
  glow: boolean;
}

export interface SceneEdge {
  from: string;
  to: string;
  label?: string | null;
  kind: "flow" | "arrow" | "link" | "dashed";
  bidirectional: boolean;
  color?: string | null;
}

export interface SceneStep {
  title: string;
  narration: string;
  focus: string[];
  reveal?: string[] | null;
  edges?: string[] | null;
  camera: "overview" | "focus" | "top" | "side" | "front";
}

export interface SceneSpec {
  id: string;
  title: string;
  subtitle?: string | null;
  layout: "flow" | "tree" | "orbit" | "stack" | "grid" | "timeline" | "exploded" | "free";
  mode: "card" | "hologram";
  theme: "aurora" | "blueprint" | "candy" | "mono";
  anchor?: { element_id?: number | null; rect?: Rect | null } | null;
  nodes: SceneNode[];
  edges: SceneEdge[];
  steps: SceneStep[];
  auto_play: boolean;
}

// ---- object scenes (src/wisp/scenes/objects.py): models built from parts, animated on a timeline
export type Vec3 = [number, number, number];
export type PartShape =
  | "box" | "rounded_box" | "sphere" | "cylinder" | "cone" | "capsule" | "torus" | "ring" | "wedge"
  | "tube" | "extrude" | "gear" | "spring" | "plane";
export type Material =
  | "metal" | "brushed_metal" | "dark_metal" | "gold" | "plastic" | "rubber" | "glass" | "painted"
  | "fabric" | "wood" | "ceramic" | "skin" | "emissive";
export type Axis = "x" | "y" | "z";

export interface Part {
  id: string;
  shape: PartShape;
  size: Vec3;
  axis: Axis;
  position: Vec3;
  rotation: Vec3;
  parent: string | null;
  material: Material;
  color: string | null;
  opacity: number;
  label: string | null;
  note: string | null;
  casing: boolean;
  hidden: boolean;
  taper: number | null;
  path: Vec3[] | null;
  radius: number | null;
  profile: [number, number][] | null;
  teeth: number | null;
  turns: number | null;
}

export interface Key {
  t: number;
  position: Vec3 | null;
  rotation: Vec3 | null;
  scale: Vec3 | null;
  opacity: number | null;
}

export interface Motion {
  part: string;
  kind: "keyframes" | "spin" | "oscillate" | "pulse";
  keys: Key[];
  axis: Axis;
  prop: "position" | "rotation" | "scale";
  amount: number;
  speed: number;
  period: number;
  phase: number;
  loop: boolean;
  start: number;
  end: number | null;
  ease: "linear" | "in" | "out" | "in_out" | "spring";
}

export interface Beat {
  title: string;
  narration: string;
  at: number;
  focus: string[];
  camera: "iso" | "front" | "back" | "side" | "left" | "top" | "below" | "close" | "wide";
  cutaway: boolean | null;
}

export interface ObjectScene {
  id: string;
  title: string;
  subtitle: string | null;
  style: "studio" | "sticker" | "blueprint" | "xray";
  parts: Part[];
  motions: Motion[];
  beats: Beat[];
  cutaway: boolean;
  auto_play: boolean;
}

export interface TimelineMoment {
  id: number;
  ts: number;
  app: string;
  title: string;
}

export interface Agent {
  id: string;
  name: string;
  role: "companion" | "browser" | "writer" | "explainer";
  persona: string;
  voice: string;
  color: string;
  skills: string[];
  builtin: boolean;
}

export interface Job {
  id: string;
  agent_id: string;
  agent_name: string;
  role: string;
  task: string;
  status: "queued" | "running" | "done" | "failed" | "stopped";
  summary: string;
  data: unknown;
  outputs: string[];
  run_id: string | null;
  cost_usd: number;
  steps: number;
  parent: string | null;
  created_at: number;
}

export interface GhostStep {
  index: number;
  narration: string;
  url: string;
  image: string | null;
  image_size: [number, number] | null;
  action: string;
  detail: string;
  target_name: string | null;
  target_box: [number, number, number, number] | null;
}

export interface Walkthrough {
  id: string;
  title: string;
  summary: string;
  steps: GhostStep[];
}

export interface Pin {
  id: number;
  note: string;
  color: string;
  rect: Rect;
}

export interface VoiceInfo {
  id: string;
  name: string;
  vibe: string;
  engine: "kokoro" | "elevenlabs" | "gemini";
  accent: string;
  lang: string;
  gender: string;
  sample: string;
}

export interface VoicePublic {
  engine: "kokoro" | "elevenlabs" | "gemini";
  voice: string;
  speed: number;
  language: string;
  captions: boolean;
  available: Record<"kokoro" | "elevenlabs" | "gemini", boolean>;
  languages: Record<string, string>;
  error: string | null;
}

export interface ConnectorStatus {
  id: string;
  name: string;
  connected: boolean;
  detail: string;
  setup: string[];
}

export interface Hello {
  state: BrainState;
  agents: Agent[];
  jobs: Job[];
  spent_today: number;
  daily_budget: number;
  model: string;
  local_mode: boolean;
  tts: string;
  voice: VoicePublic | null;
  privacy_paused: boolean;
  ambient_memory: boolean;
  keep_screenshots: boolean;
  version: string;
  demo: boolean;
  gallery: Record<string, string>;
}

export type BrainEvent =
  | ({ type: "hello" } & Hello)
  | ({ type: "settings" } & Hello)
  | { type: "state"; state: BrainState }
  | { type: "user"; text: string; turn_id: string }
  | { type: "transcript"; text: string; seconds: number }
  | { type: "say"; agent: string; index: number; total: number; text: string; audio: string | null; duration_s: number; annotations: Annotation[]; notes?: number[] }
  | { type: "notes"; card: NoteCard }
  | { type: "action"; kind: "open_url" | "copy" | "insert"; value: string }
  | { type: "guide"; active: boolean; step?: number; goal?: string; label?: string }
  | { type: "stop_audio" }
  | { type: "annotate"; annotations: Annotation[] }
  | { type: "clear" }
  | { type: "capture"; app: string; window: string; excluded: string | null; elements: number }
  | { type: "scene"; scene: SceneSpec }
  | { type: "object"; scene: ObjectScene; audio_key: string; restart: boolean }
  | { type: "scene_audio"; scene_id: string; step: number; text: string; audio: string | null; duration_s: number }
  | { type: "palace"; snapshot_id: number; timeline: TimelineMoment[]; scene: SceneSpec }
  | { type: "walkthrough"; walkthrough: Walkthrough }
  | { type: "walkthrough_audio"; walkthrough_id: string; step: number; text: string; audio: string | null }
  | { type: "job"; job: Job }
  | { type: "job_event"; job_id: string; agent: string; event: string; data: Record<string, unknown> }
  | { type: "job_frame"; job_id: string; image: string }
  | { type: "handoff"; source: string; target: string; task: string }
  | { type: "confirm"; request_id: string; job_id: string; agent: string; action: string; reason: string }
  | { type: "ask"; request_id: string; job_id: string; agent: string; question: string }
  | { type: "agents"; agents: Agent[] }
  | { type: "pins"; app: string; pins: Pin[] }
  | { type: "turn_done"; turn_id: string; cost_usd: number; tokens: number; spent_today: number; daily_budget: number }
  | { type: "voice_catalog"; catalog: Record<string, VoiceInfo[]>; voice: VoicePublic }
  | { type: "connectors"; connectors: ConnectorStatus[]; message?: string }
  | { type: "voice_preview"; engine: string; voice: string; text: string; audio: string | null; error: string | null }
  | { type: "error"; message: string };

export type ClientMessage =
  | { type: "text"; text: string }
  | { type: "ptt_start" }
  | { type: "ptt_cancel" }
  | { type: "audio"; data: string; mime: string }
  | { type: "interrupt" }
  | { type: "answer"; request_id: string; value: unknown }
  | { type: "job_control"; job_id: string; action: string; text?: string }
  | { type: "show_scene"; name: string }
  | { type: "palace_seek"; snapshot_id: number }
  | { type: "time_travel"; query: string; when?: string }
  | { type: "walkthrough"; job_id?: string }
  | { type: "clear" }
  | { type: "agent_create"; name: string; role: string; persona: string; voice?: string }
  | { type: "agent_delete"; id: string }
  | { type: "set"; key: string; value: unknown }
  | { type: "notice"; message: string }
  | { type: "voice_catalog" }
  | { type: "guide_clicked" }
  | { type: "connectors" }
  | { type: "connect"; id: string }
  | { type: "disconnect"; id: string }
  | { type: "guide_cancel" }
  | { type: "voice_preview"; engine: string; voice: string; speed?: number };
