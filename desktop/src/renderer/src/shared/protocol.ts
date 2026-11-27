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
  | "tube" | "limb" | "blob" | "extrude" | "lathe" | "loft" | "gear" | "spring" | "plane" | "arrow" | "marker" | "kit" | "asset";
export type Material =
  | "metal" | "brushed_metal" | "dark_metal" | "gold" | "plastic" | "rubber" | "glass" | "painted"
  | "fabric" | "wood" | "ceramic" | "skin" | "emissive"
  | "silicon" | "pcb" | "solder" | "copper" | "anodized";
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
  /** Fine detail drawn on the part (scene/surface.ts): pins, traces, cells, fins, brushed,
   * vents, grille, knurl, panel_lines. `density` makes it finer (2) or coarser (0.5). */
  surface?: string | null;
  density?: number | null;
  /** shape "limb": how thick it is at each point of `path`; `squash` flattens it side to side. */
  radii?: number[] | null;
  squash?: number | null;
  /** shape "blob": [x, y, z, rx, ry, rz] lumps melted into one skin; `blend` is how softly. */
  lumps?: number[][] | null;
  blend?: number | null;
  /** shape "lathe": hollow and open at its +axis end, with walls this thick (a mug, a pot). */
  wall?: number | null;
  /** shape "extrude": edges rounded over, as a share of the thickness (0 sharp, 0.5 right over). */
  bevel?: number | null;
  /** shape "loft": [along, width, height, lift, round] cross-sections joined into one skin. */
  sections?: number[][] | null;
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
  /** A typed diagram (src/pyxi/scenes/diagram.py): content only, laid out by scene/diagramLayout.ts.
   * When it is here, `shapes` is empty. */
  diagram?: DiagramSpec | null;
}

export type DiagramType = "steps" | "flowchart" | "timeline" | "comparison" | "tree" | "loop";
export interface DiagramItem {
  id: string;
  label: string;
  step: number;
  focus?: boolean;
  /** comparison in two columns */
  side?: "left" | "right" | null;
  /** timeline: the date or moment, as it should read */
  when?: string | null;
  /** comparison on two axes: 0 to 100, x left to right and y bottom to top */
  x?: number | null;
  y?: number | null;
}
export interface DiagramLink {
  from: string;
  to: string;
  label?: string | null;
}
export interface DiagramSpec {
  type: DiagramType;
  items: DiagramItem[];
  links: DiagramLink[];
  columns?: [string, string] | null;
  axes?: { x: [string, string]; y: [string, string] } | null;
  center?: string | null;
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
  /** Built from a photo: the photo, small (a data URL), kept beside the model to compare. */
  photo?: string | null;
  /** The angle the photo was taken from, [degrees round from the front, degrees above level]:
   * the model first opens seen from there. */
  photo_view?: [number, number] | null;
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

/** One question on the card Pixel shows before it fills a PDF form: a place to write, or tick
 * boxes that are one choice ("choice") or several ("multi"). `value` is what is known already:
 * from the form itself, their saved details, or what they said in chat (`from`). */
export interface FormField {
  id: string;
  label: string;
  section: string;
  kind: "text" | "choice" | "multi";
  page?: number;
  options?: string[];
  value: string | string[];
  from?: "form" | "saved" | "said" | "";
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
  /** Whether a key of theirs is kept on this PC for each cloud voice. Never the key itself. */
  own_keys?: Partial<Record<"elevenlabs" | "gemini", boolean>>;
  languages: Record<string, string>;
  error: string | null;
}

/** Pyxi's own voice being downloaded (the Voice tab's button, or a first start). */
export interface VoicePack {
  state: "downloading" | "ready" | "failed";
  /** 0 to 100 while downloading. */
  percent: number;
  /** One plain line: that it is ready, or why it failed and what to do. */
  message: string;
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
  /** The Pyxi account (pyxi/account.py). */
  account?: AccountState;
  /** The first-run tour: due at this start (never seen, or broke off), or playing right now. */
  onboarding?: { due: boolean; active: boolean };
}

/**
 * The Pyxi account: sign in with Google, a 3-day trial, then a plan (pyxi/account.py; the
 * server's side is the contract in .pyxi/ACCOUNTS-CONTRACT.md). The brain keeps it and sends
 * the whole of it in one event, `account`. It never holds a token of any kind.
 */
export type AccountPlan = "trial" | "starter" | "pro" | "max" | "none";
export type PaidPlan = "starter" | "pro" | "max";
export type AccountStatus = "trial" | "active" | "past_due" | "ended";
/** Why the server is not answering for this account: each has its own card and one button. */
export type AccountGate = "trial_ended" | "subscription_ended" | "payment_failed" | "allowance_used" | "signed_out";
export interface AccountState {
  /** Does the server offer "Continue with Google"? null: not asked yet, or not reached. */
  offered: boolean | null;
  /** Are invite codes still taken? */
  invite_codes: boolean;
  signed_in: boolean;
  /** They signed out (or the sign-in ran out): the sign-in step comes up by itself. */
  ask_sign_in: boolean;
  email: string;
  plan: AccountPlan | null;
  status: AccountStatus | null;
  trial_ends_at: string | null;
  /** Whole days of trial left; 0 on the last day. */
  days_left: number | null;
  /** When the paid period renews, or ends when `cancels`. */
  renews_at: string | null;
  cancels: boolean;
  /** This month's allowance (model cost; shown as a share, never as money). */
  allowance: { used_usd: number; limit_usd: number; resets_at: string | null } | null;
  /** What Pyxi is in the middle of for the account ("" when nothing). */
  busy: "" | "sign_in" | "checkout" | "portal" | "sign_out";
  gate: AccountGate | null;
  /** The plan they pressed on the website's pricing page before downloading, until they have a
   * paid plan. It arrives at the end of the Google sign-in (pyxi/account_signin.py). */
  picked?: { plan: PaidPlan; yearly: boolean } | null;
}
/** The account, and (when something was just tried) how that went, in words for the user. */
export interface AccountEvent {
  type: "account";
  account: AccountState;
  what?: "config" | "sign_in" | "sign_out" | "checkout" | "portal" | "plan";
  ok?: boolean;
  message?: string;
  /** It may work on another try (no internet, the server was down): offer "Try again". */
  retry?: boolean;
  /** They closed the sign-in themselves: nothing to say. */
  cancelled?: boolean;
  /** There is no plan to manage yet: show the plans. */
  pick?: boolean;
}
export type AccountMessage =
  /** `get`: what is known (asking the server when needed). `config`: ask the server again
   * whether sign-in is on. `refresh`: ask the server about the plan and allowance now. */
  | { type: "account"; action: "get" | "config" | "sign_in" | "cancel_sign_in" | "sign_out" | "portal" }
  | { type: "account"; action: "refresh"; force?: boolean }
  /** Open Stripe Checkout for this plan in the browser. */
  | { type: "account"; action: "checkout"; plan: PaidPlan; yearly: boolean };

/**
 * The first-run tour (pyxi/onboarding.py; played by overlay/onboarding). The overlay runs it
 * and tells the brain where it is; the brain keeps the marker, voices the lines, hands over
 * their first spoken words, and saves what they chose to tell it. Nothing here leaves the PC.
 */
export type OnboardingStep = "code" | "arrival" | "talk"| "screen" | "about" | "google" | "demos" | "landing";
export type OnboardingEnding = "finished" | "skipped" | "underage" | "failed";
/** What they told the tour. Every field is optional; the brain keeps only what looks right. */
export interface OnboardingDetails {
  name?: string;
  email?: string;
  /** YYYY-MM-DD. */
  date_of_birth?: string;
  likes?: string[];
  use?: string;
}
export type OnboardingEvent =
  /** Play it. `have`: what is already saved (someone updating), so the card can show it.
   * `code`: it opens with the step before the tour (the brain agrees they need it): "Continue
   * with Google" when the server has `sign_in` on, else the invite code. `offline`: the server
   * could not be asked. `only`: that step by itself, for someone who signed out. */
  | { type: "onboarding"; event: "start"; replay: boolean; code?: boolean; only?: boolean; sign_in?: boolean; invite_codes?: boolean; offline?: boolean; have:Partial<Record<"first_name" | "email" | "date_of_birth", string>> }
  /** Which step it is on, told to every screen (the notch and the firefly may be on another). */
  | { type: "onboarding"; event: "step"; step: OnboardingStep }
  /** One of the tour's lines in Pyxi's voice (`audio` null: quiet was asked for, or no voice yet). */
  | { type: "onboarding"; event: "voice"; line: string; text: string; audio: string | null }
  /** Their words in the "say hello" step ("" when they could not be made out). */
  | { type: "onboarding"; event: "heard"; text: string }
  /** What was kept. `underage`: nothing was, and the tour is over. */
  | { type: "onboarding"; event: "saved"; ok: boolean; underage: boolean; errors: Record<string, string>; saved: string[] }
  | { type: "onboarding"; event: "ended"; how: OnboardingEnding };
export type OnboardingMessage =
  /** The overlay is about to play it (ignored when it isn't due); `replay` from Settings.
   * `code`: it would open with the invite code step, which is not yet a try at the tour. */
  | { type: "onboarding"; action: "start" | "replay"; code?: boolean }
  /** Only the sign-in step: they signed out after the tour was over. */
  | { type: "onboarding"; action: "signin" }
  | { type: "onboarding"; action: "step"; step: OnboardingStep }
  | { type: "onboarding"; action: "say"; line: string; text: string }
  | { type: "onboarding"; action: "save"; details: OnboardingDetails }
  | { type: "onboarding"; action: "finish"; how: OnboardingEnding }
  /** Settings: delete what the tour kept. */
  | { type: "onboarding"; action: "forget" };

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

export type DictationOutcome = "typed" | "copied" | "password" | "nothing" | "cancelled" | "failed";
/**
 * What Pyxi is hearing right now (src/pyxi/companion/listening.py). While either is set, a sign
 * with a Stop button is on screen: the meeting's pill, or the caption bar itself.
 */
export interface MeetingSign {
  /** Unix seconds the listening began (for the elapsed time). */
  since: number;
  /** "listening": the call is being heard. "writing": it has stopped and the notes are being written. */
  state: "listening" | "writing";
  lines: number;
  /** One plain line under the sign, e.g. the microphone couldn't be heard. */
  note: string;
}
export interface CaptionState {
  /** The language captions are translated into ("" for captions as spoken). */
  translate: string;
  /** Show the original above the translation. */
  both: boolean;
  /** Text size, 0 (small) to 4. */
  size: number;
  edge: "bottom" | "top";
  /** The screen the bar belongs on, in physical px (null: the main one). */
  screen: Rect | null;
  /** Whose words they are: Pyxi's own model, or Windows Live Captions. */
  source: "pyxi" | "windows";
  note: string;
  languages: string[];
}

export type BrainEvent =
  | ({ type: "hello" } & Hello)
  | { type: "listening"; meeting: MeetingSign | null; captions: CaptionState | null }
  // One caption line: updated under the same id while it is being said, then final. With
  // translation on, the final comes again with `translation` when it is ready (`pending` until then).
  | { type: "caption"; id: number; text: string; final: boolean; translation: string | null; pending: boolean }
  | ({ type: "settings" } & Hello)
  | { type: "state"; state: BrainState }
  | { type: "user"; text: string; turn_id: string }
  | { type: "transcript"; text: string; seconds: number }
  | { type: "say"; agent: string; index: number; total: number; text: string; audio: string | null; duration_s: number; annotations: Annotation[]; notes?: number[] }
  | { type: "notes"; card: NoteCard }
  | { type: "answer"; card: AnswerCard }
  | { type: "action"; kind: "open_url" | "copy" | "insert" | "insert_table" | "dictate" | "dictate_keep"; value: string }
  /** How a dictation ended: typed where the cursor is, copied (no text box had the keyboard),
   * or nothing at all (a password box, silence, Esc). `note` is the line for the sign. */
  | { type: "dictated"; text: string; how: DictationOutcome; note: string; seconds: number; tidied?: boolean }
  | { type: "alarm"; title: string; label: string; at: string }
  /** A result card. `actions` name its buttons (overlay/cardButtons.ts shows two at most):
   * "google", the account's ("choose_plan"...), "settings" or "settings:<part>" (Open Settings,
   * at Your files, Connected accounts, the budget or the Voice tab), and "retry" (Try again),
   * which comes with `retry`: what will be run again, in words. The brain sends "retry" only
   * for a failure where nothing was done. */
  | { type: "job_ready"; job_id: string; agent: string; ok: boolean; title: string; body: string; link?: string | null; files: string[]; changes: string[]; notes: string[]; actions: string[]; calendar?: string | null; retry?: string | null }
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
  | { type: "ask"; request_id: string; job_id: string; agent: string; question: string; handover?: boolean; secret?: boolean; fields?: FormField[] }
  | { type: "request_closed"; request_id: string }
  // "Is this the address you meant?" (src/pyxi/companion/addresscheck.py): answered with
  // { addresses: string[] } in the same order, or false for "Never mind".
  | { type: "check_address"; request_id: string; job_id: string; agent: string; voice: boolean; addresses: { address: string; suggestion?: string | null; problem?: string | null; heard?: string | null }[] }
  // Words said while that card is up: "yes", a spoken correction (fills the box), "no", "never mind".
  | { type: "address_voice"; action: "confirm" | "fill" | "edit" | "cancel"; address?: string; heard?: string }
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
  | ({ type: "voice_pack" } & VoicePack)
  /** How saving or removing a cloud voice's key went. The key is never sent back. */
  | { type: "voice_key"; engine: string; ok: boolean; message: string }
  | { type: "error"; message: string }
  | { type: "notice"; message: string }
  | { type: "private_apps"; apps: string[]; built_in: string[] }
  | { type: "grants"; grants: Grant[] }
  | ({ type: "browser"; code?: string; expires_in?: number; setup?: BrowserSetup } & BrowserState)
  | { type: "audit"; entries: AuditEntry[] }
  | { type: "memories"; memories: MemoryItem[] }
  | { type: "beta"; ok: boolean; message: string }
  | AccountEvent
  | { type: "profile"; profile: Record<string, string>; saved?: boolean }
  | ({ type: "file_access" } & FileAccessState)
  | ({ type: "libreoffice" } & ConverterState)
  | OnboardingEvent;

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
  /** `picture`: one pasted or dropped into the chat box with the words (a data URL, made small). */
  | { type: "text"; text: string; picture?: { data: string } }
  | { type: "ptt_start" }
  | { type: "look_ahead" }
  | { type: "beta_join"; code: string }
  | { type: "beta_leave" }
  | AccountMessage
  | { type: "profile_get" }
  | { type: "profile_save"; profile: Record<string, string> }
  | { type: "file_access_get" }
  | { type: "private_apps_get" }
  | { type: "grants_get" }
  | { type: "browser_get" }
  | { type: "browser_pair" }
  | { type: "browser_setup"; browser: string }
  /** "Do it in my browser" on the card of a job a site turned Scout away from. */
  | { type: "own_tab"; job_id: string }
  | { type: "browser_forget"; id: string }
  | { type: "browser_site_revoke"; host: string }
  | { type: "audit_get" }
  | { type: "stop_everything" }
  // The meeting sign's Stop and the caption bar's own buttons (their own press, never Pyxi's).
  | { type: "listen"; what: "meeting" | "captions"; action: "start" | "stop" | "toggle" | "set" | "size" | "move"; translate?: string; both?: boolean; delta?: number }
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
  | { type: "dictate"; data: string; mime: string; polish?: boolean; here?: boolean }
  | { type: "dictating"; on: boolean; here?: boolean }
  | { type: "dictate_cancel" }
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
  | { type: "voice_preview"; engine: string; voice: string; speed?: number }
  /** "Try again" on a failure card: the brain runs what that card stands for, once. */
  | { type: "retry"; job_id: string }
  /** Download Pyxi's own voice (`repair`: fetch it afresh over the one that is here). */
  | { type: "voice_pack"; repair?: boolean }
  /** Keep a cloud voice's key in the protected store on this PC, or (`forget`) remove it. */
  | { type: "voice_key"; engine: string; key?: string; forget?: boolean }
  | OnboardingMessage;
