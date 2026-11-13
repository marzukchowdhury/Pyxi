// TypeScript mirror of the brain's events (src/pyxi/*). Coordinates are physical screen px.

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
  /** Guide steps: what to do at the mark, what to type or press, and "step 2 of 5". */
  action?: "click" | "double_click" | "right_click" | "type" | "press" | "scroll" | "drag" | "look" | null;
  hint?: string | null;
  step?: number | null;
  total?: number | null;
}

export interface NoteItem {
  n: number;
  text: string;
  kind: "point" | "step" | "formula" | "aside";
  mark: "frame" | "underline" | "circle" | "none";
  rect: Rect | null;
  /** On a picture: the spot on the part, where its tag sits, and a route traced along it. */
  at?: [number, number] | null;
  tag?: [number, number] | null;
  trace?: [number, number][];
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
  /** "picture": the points are tags pinned onto the picture itself, with no card. */
  style?: "card" | "picture";
}

/** A named link on an answer card: a source ("RTINGS") or its one button ("Check on Amazon"). */
export interface CardLink {
  name: string;
  url: string;
}

/**
 * An answer card (src/pyxi/companion/notes.py AnswerCard): a pick with its price, up to three
 * facts, its sources and one button; or a teammate's finished result, every line of it.
 */
export interface AnswerCard {
  id: string;
  kind: "answer" | "result";
  title: string;
  figure: string | null;
  note: string | null;
  facts: string[];
  sources: CardLink[];
  button: CardLink | null;
  /** A data: address made by the brain from a real picture of the pick, when one was found. */
  image: string | null;
  agent: string | null;
  ok: boolean;
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

// ---- object scenes (src/pyxi/scenes/objects.py): models built from parts, animated on a timeline
export type Vec3 = [number, number, number];
export type PartShape =
  | "box" | "rounded_box" | "sphere" | "cylinder" | "cone" | "capsule" | "torus" | "ring" | "wedge"
  | "tube" | "limb" | "blob" | "extrude" | "lathe" | "gear" | "spring" | "plane" | "arrow" | "marker" | "kit" | "asset";
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
  /** shape "limb": how thick it is at each point of `path`; `squash` flattens it side to side. */
  radii?: number[] | null;
  squash?: number | null;
  /** shape "blob": [x, y, z, rx, ry, rz] lumps melted into one skin; `blend` is how softly. */
  lumps?: number[][] | null;
  blend?: number | null;
  /** Part of a living thing (never drawn hard-edged; set by the builder, repaired by Pyxi). */
  organic?: boolean;
  /** shape "asset": a piece of a real model from Pyxi's library (its id, and the part's name in it). */
  asset?: string | null;
  node?: string | null;
  /** shape "kit": a ready-made detailed piece (scene/kit.ts). */
  kit?: string | null;
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

export type SketchInk = "ink" | "ice" | "amber" | "rose" | "mint" | "violet" | "muted";
export interface SketchShape {
  kind: "line" | "polyline" | "polygon" | "curve" | "arrow" | "rect" | "ellipse" | "text" | "equation";
  points: [number, number][];
  at: [number, number] | null;
  size: [number, number] | null;
  text: string | null;
  color: SketchInk;
  fill: boolean;
  weight: "thin" | "normal" | "bold";
  dashed: boolean;
  flow: boolean;
  step: number;
  ask: string | null;
}
export interface Sketchboard {
  id: string;
  title: string;
  subtitle: string | null;
  shapes: SketchShape[];
  steps: { narration: string; title: string | null }[];
  anchor: { element_id: number | null; quote: string | null; rect: [number, number, number, number] | null } | null;
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
  /** Who made the real model it uses, when it uses one (shown small under the stage). */
  credit?: string | null;
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
  role: "companion" | "browser" | "writer" | "explainer" | "files" | "documents";
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
  ended_at?: number | null;
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
  /** Speech is turned into text on this PC only (less accurate; nothing of the voice is sent). */
  listen_here?: boolean;
  available: Record<"kokoro" | "elevenlabs" | "gemini", boolean> & { bengali?: boolean };
  languages: Record<string, string>;
  error: string | null;
}

export interface RoutineInfo {
  id: string;
  task: string;
  when: string;
  next_run: number;
  last_run: number | null;
  enabled: boolean;
}

export interface ConnectorStatus {
  id: string;
  name: string;
  connected: boolean;
  detail: string;
  setup: string[];
  /** Connected, but one more OK is needed: the button's label ("Allow Sheets"). */
  needs?: string;
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
  beta?: { joined: boolean; can_join: boolean; own_key?: boolean };
}

/** A standing "Always allow" (pyxi/grants.py). Times are Unix seconds. */
export interface Grant {
  kind: string;
  label: string;
  granted: number;
  expires: number;
}

/** Pyxi in your browser: paired browsers and the sites Pyxi may act on (pyxi/browserlink.py). */
export interface BrowserState {
  connected: boolean;
  browsers: { id: string; browser: string; connected: boolean; paired: number }[];
  sites: { host: string; granted: number; expires: number }[];
}

/** Which browser Pyxi would put its extension into, and how (pyxi/companion/browsersetup.py). */
export interface BrowserSetup {
  browser: string; // "Chrome", "Edge", "Firefox"... or "" when none is open
  key: string;
  kind: "chromium" | "firefox" | "";
  checked?: boolean; // the guided steps were checked in this browser
  addon?: boolean | null; // Firefox: a signed add-on is published (null: not known yet)
  notice?: string; // the line about the browser's developer mode notice
  steps?: string[]; // the same thing by hand
  running: boolean;
}

/** One line of the activity log (pyxi/audit.py). */
export interface AuditEntry {
  id: string;
  ts: number;
  kind: string;
  agent: string;
  what: string;
  detail: string;
  undone: boolean;
  can_undo: boolean;
}

/** Something Pyxi or a teammate remembers (Settings can forget it). */
export interface MemoryItem {
  store: string;
  scope: string;
  key: string;
  content: string;
  at: number;
}

export type BrainEvent =
  | ({ type: "hello" } & Hello)
  | ({ type: "settings" } & Hello)
  | { type: "state"; state: BrainState }
  | { type: "user"; text: string; turn_id: string }
  | { type: "transcript"; text: string; seconds: number }
  | { type: "say"; agent: string; index: number; total: number; text: string; audio: string | null; duration_s: number; annotations: Annotation[]; notes?: number[] }
  | { type: "notes"; card: NoteCard }
  | { type: "answer"; card: AnswerCard }
  | { type: "action"; kind: "open_url" | "copy" | "insert" | "insert_table"; value: string }
  | { type: "alarm"; title: string; label: string; at: string }
  | { type: "job_ready"; job_id: string; agent: string; ok: boolean; title: string; body: string; link?: string | null; files: string[]; changes: string[]; notes: string[]; actions: string[]; calendar?: string | null }
  | { type: "guide"; active: boolean; step?: number; total?: number | null; goal?: string; label?: string; action?: string; finished?: boolean; at?: [number, number] | null }
  | { type: "stop_audio" }
  | { type: "annotate"; annotations: Annotation[] }
  | { type: "clear" }
  | { type: "capture_hide" }
  | { type: "capture_show" }
  | { type: "capture"; app: string; window: string; excluded: string | null; elements: number }
  | { type: "scene"; scene: SceneSpec; place?: [number, number, number, number] | null }
  | { type: "sketch"; board: Sketchboard; audio_key: string; place?: [number, number, number, number] | null }
  | { type: "object"; scene: ObjectScene; audio_key: string; restart: boolean; place?: [number, number, number, number] | null }
  | { type: "scene_audio"; scene_id: string; step: number; text: string; audio: string | null; duration_s: number }
  | { type: "palace"; snapshot_id: number; timeline: TimelineMoment[]; scene: SceneSpec }
  | { type: "walkthrough"; walkthrough: Walkthrough }
  | { type: "walkthrough_audio"; walkthrough_id: string; step: number; text: string; audio: string | null }
  | { type: "job"; job: Job }
  | { type: "job_event"; job_id: string; agent: string; event: string; data: Record<string, unknown> }
  | { type: "job_frame"; job_id: string; image: string }
  | { type: "handoff"; source: string; target: string; task: string }
  | { type: "confirm"; request_id: string; job_id: string; agent: string; action: string; reason: string; always?: string }
  | { type: "ask"; request_id: string; job_id: string; agent: string; question: string; handover?: boolean; secret?: boolean }
  | { type: "request_closed"; request_id: string }
  | { type: "driving"; doing: string | null }
  | { type: "agents"; agents: Agent[] }
  | { type: "pins"; app: string; pins: Pin[] }
  | { type: "price_tag"; tag: PriceTagInfo | null }
  | { type: "shopping"; auto: boolean }
  | { type: "turn_done"; turn_id: string; cost_usd: number; tokens: number; spent_today: number; daily_budget: number }
  | { type: "voice_catalog"; catalog: Record<string, VoiceInfo[]>; voice: VoicePublic }
  | { type: "connectors"; connectors: ConnectorStatus[]; message?: string }
  | { type: "routines"; routines: RoutineInfo[] }
  | { type: "voice_preview"; engine: string; voice: string; text: string; audio: string | null; error: string | null }
  | { type: "error"; message: string }
  | { type: "notice"; message: string }
  | { type: "private_apps"; apps: string[]; built_in: string[] }
  | { type: "grants"; grants: Grant[] }
  | ({ type: "browser"; code?: string; expires_in?: number; setup?: BrowserSetup } & BrowserState)
  | { type: "audit"; entries: AuditEntry[] }
  | { type: "memories"; memories: MemoryItem[] }
  | { type: "beta"; ok: boolean; message: string }
  | { type: "profile"; profile: Record<string, string>; saved?: boolean }
  | ({ type: "file_access" } & FileAccessState)
  | ({ type: "libreoffice" } & ConverterState);

/** LibreOffice on demand, for exact Office layouts and old .doc/.xls/.ppt files. */
export interface ConverterState {
  state: "missing" | "downloading" | "checking" | "unpacking" | "ready" | "failed";
  progress: number;
  version: string;
  message: string;
  asked?: boolean;
  allowed?: boolean;
}

/** A "cheaper elsewhere" tag: where it sits (physical screen px, beside the price) and what it says. */
export interface PriceTagInfo {
  id: string;
  verdict: "cheaper" | "best_here" | "unverified";
  headline: string;
  detail: string;
  url: string | null;
  store: string | null;
  product: string;
  checked_at: number;
  at: [number, number] | null;
}

/** "Let Pyxi search my files" (off until they allow it), and the folders it may look in. */
export interface FileAccessState {
  enabled: boolean;
  folders: string[];
  asked: boolean;
}

export type ClientMessage =
  | { type: "text"; text: string }
  | { type: "ptt_start" }
  | { type: "look_ahead" }
  | { type: "beta_join"; code: string }
  | { type: "beta_leave" }
  | { type: "profile_get" }
  | { type: "profile_save"; profile: Record<string, string> }
  | { type: "file_access_get" }
  | { type: "private_apps_get" }
  | { type: "grants_get" }
  | { type: "browser_get" }
  | { type: "browser_pair" }
  | { type: "browser_setup"; browser: string }
  | { type: "browser_forget"; id: string }
  | { type: "browser_site_revoke"; host: string }
  | { type: "audit_get" }
  | { type: "stop_everything" }
  | { type: "memories_get" }
  | { type: "memory_forget"; store: string; scope: string; key: string }
  | { type: "memories_wipe" }
  | { type: "audit_undo"; id: string }
  | { type: "grant_revoke"; kind: string }
  | { type: "private_apps_save"; apps: string[] }
  | { type: "libreoffice_get" }
  | { type: "libreoffice_set"; allowed: boolean }
  | { type: "shopping_get" }
  | { type: "shopping_save"; auto: boolean }
  | { type: "file_access_save"; enabled: boolean; folders: string[] }
  | { type: "ptt_cancel" }
  | { type: "audio"; data: string; mime: string }
  | { type: "dictate"; data: string; mime: string }
  | { type: "dictating"; on: boolean }
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
  | { type: "guide_clicked"; how?: "clicked" | "done" | "elsewhere" | "within" }
  | { type: "connectors" }
  | { type: "routines" }
  | { type: "routine_delete"; id: string }
  | { type: "routine_add"; task: string; days: string[]; time: string; every_minutes: number | null }
  | { type: "routine_toggle"; id: string; enabled: boolean }
  | { type: "routine_run"; id: string }
  | { type: "connect"; id: string }
  | { type: "disconnect"; id: string }
  | { type: "guide_cancel" }
  | { type: "object_closed"; by_them?: boolean }
  | { type: "voice_preview"; engine: string; voice: string; speed?: number };
