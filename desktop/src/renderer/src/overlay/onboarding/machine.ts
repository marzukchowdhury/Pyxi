/**
 * The first-run tour as a step machine: a plain reducer, so its order, its timeouts and every
 * way out can be tested without a screen (machine.test.ts). Onboarding.tsx feeds it what
 * happens (a key held, words heard, a clock tick) and draws what it says.
 *
 *   arrival  the firefly flies in, says hello, asks what to call them
 *   talk     "Hold Ctrl and Alt, and say hello": their first words, answered by name
 *   screen   "point me at something": one real question about their screen
 *   about    the jar: email, date of birth, what they like, what the PC is for
 *   google   connect Google, or maybe later
 *   demos    three tiny demos to tap, or skip
 *   landing  the firefly flies up into the notch; the dim lifts
 *
 * Every step can be skipped (Esc), the whole tour can be skipped from any step, and nothing
 * waits forever: a step that needs them offers "Next" after a while.
 */

export type Step = "arrival" | "talk" | "screen" | "about" | "google" | "demos" | "landing";
export const STEPS: Step[] = ["arrival", "talk", "screen", "about", "google", "demos", "landing"];

export type Phase =
  // arrival
  | "fly" | "hello" | "ask"
  // talk and screen
  | "wait" | "working" | "answered"
  // about
  | "form" | "saving" | "thanks" | "underage"
  // google
  | "offer" | "connecting" | "connected"
  // demos
  | "pick" | "play"
  // landing
  | "rise" | "glow";

export type Ending = "finished" | "skipped" | "underage" | "failed";
export type Demo = "explain" | "build" | "tidy";
export const DEMOS: Demo[] = ["explain", "build", "tidy"];

export interface Tour {
  step: Step;
  phase: Phase;
  /** What they asked to be called ("" when they skipped it). */
  name: string;
  /** Milliseconds in this phase (not counted while Pyxi is still speaking a line that ends it). */
  elapsed: number;
  /** The quiet way on ("or double-tap Ctrl to type", "Next") is showing. */
  hint: boolean;
  /** The talk keys are held right now. */
  keys: boolean;
  /** Their words were not made out: ask once more. */
  again: boolean;
  demo: Demo | null;
  played: Demo[];
  /** Set once: the tour is over, and how. */
  ended: Ending | null;
}

export type TourEvent =
  /** The clock. `speaking`: one of Pyxi's lines is still being said. */
  | { type: "tick"; ms: number; speaking?: boolean }
  | { type: "name"; name: string }
  | { type: "keys"; down: boolean }
  /** Their words in the talk step, as the brain heard them ("" when it could not). */
  | { type: "heard"; text: string }
  /** A real turn began (their question), and ended. */
  | { type: "asked" }
  | { type: "answered" }
  | { type: "submit" }
  | { type: "saved" }
  | { type: "underage" }
  | { type: "connect" }
  | { type: "connected" }
  | { type: "demo"; demo: Demo }
  /** Enter, or the step's own way on. */
  | { type: "next" }
  /** Esc: leave this step. */
  | { type: "skip_step" }
  | { type: "skip_tour" }
  | { type: "fail" };

/** How long things take, in milliseconds. */
export const T = {
  fly: 1700, // the firefly's way in
  hello: 3400, // "Hi. I'm Pyxi. I live in the corner of your screen."
  talkHint: 12000, // then: "or double-tap Ctrl to type", and Next
  talkAnswered: 3000, // "Hello, Sam. I can hear you."
  screenHint: 15000,
  screenWorking: 45000, // a real turn that never ends is not waited for
  screenAnswered: 4500,
  saving: 4000, // the brain not answering "saved" never holds the tour
  thanks: 1500,
  underage: 9000,
  connected: 2600,
  demo: 5200,
  rise: 2600, // up into the notch
  glow: 2400,
  maxSpeaking: 15000, // a line still being said holds a phase this long at most
} as const;

export function begin(): Tour {
  return { step: "arrival", phase: "fly", name: "", elapsed: 0, hint: false, keys: false, again: false, demo: null, played: [], ended: null };
}

const FIRST_PHASE: Record<Step, Phase> = { arrival: "fly", talk: "wait", screen: "wait", about: "form", google: "offer", demos: "pick", landing: "rise" };

function enter(t: Tour, step: Step): Tour {
  return { ...t, step, phase: FIRST_PHASE[step], elapsed: 0, hint: false, again: false, demo: null };
}

function phase(t: Tour, to: Phase): Tour {
  return { ...t, phase: to, elapsed: 0 };
}

/** The step after this one. */
function onward(t: Tour): Tour {
  const i = STEPS.indexOf(t.step);
  return i < STEPS.length - 1 ? enter(t, STEPS[i + 1]) : { ...t, ended: "finished" };
}

/** One line, at most 40 characters: what they typed, tidied. */
export function tidyName(raw: string): string {
  return raw.replace(/\s+/g, " ").trim().slice(0, 40).trim();
}

/** Phases that end by themselves once their time is up (and the line has been said). */
function timeout(t: Tour): number | null {
  switch (`${t.step}/${t.phase}`) {
    case "arrival/fly": return T.fly;
    case "arrival/hello": return T.hello;
    case "talk/answered": return T.talkAnswered;
    case "screen/working": return T.screenWorking;
    case "screen/answered": return T.screenAnswered;
    case "about/saving": return T.saving;
    case "about/thanks": return T.thanks;
    case "about/underage": return T.underage;
    case "google/connected": return T.connected;
    case "demos/play": return T.demo;
    case "landing/rise": return T.rise;
    case "landing/glow": return T.glow;
    default: return null;
  }
}

function timedOut(t: Tour): Tour {
  switch (`${t.step}/${t.phase}`) {
    case "arrival/fly": return phase(t, "hello");
    case "arrival/hello": return phase(t, "ask");
    case "screen/working": return phase(t, "answered");
    case "about/saving": return phase(t, "thanks");
    case "about/underage": return { ...t, ended: "underage" };
    case "demos/play": return { ...phase(t, "pick"), demo: null };
    case "landing/rise": return phase(t, "glow");
    case "landing/glow": return { ...t, ended: "finished" };
    default: return onward(t); // talk/answered, screen/answered, about/thanks, google/connected
  }
}

function tick(t: Tour, ms: number, speaking: boolean): Tour {
  const limit = timeout(t);
  // A line still being said holds the phase (its words are the point), but never for long.
  const next = { ...t, elapsed: t.elapsed + ms };
  const held = speaking && limit !== null && next.elapsed < limit + T.maxSpeaking && t.phase !== "play" && t.phase !== "working" && t.phase !== "fly";
  if (limit !== null && next.elapsed >= limit && !held) return timedOut(next);
  if (t.phase === "wait" && !t.hint && next.elapsed >= (t.step === "talk" ? T.talkHint : T.screenHint)) return { ...next, hint: true };
  return next;
}

export function reduce(t: Tour, e: TourEvent): Tour {
  if (t.ended) return t;
  switch (e.type) {
    case "tick":
      return tick(t, e.ms, !!e.speaking);
    case "fail":
      return { ...t, ended: "failed" };
    case "skip_tour":
      // A child's tour has already ended for its own reason.
      return { ...t, ended: t.phase === "underage" ? "underage" : "skipped" };
    case "keys":
      return { ...t, keys: e.down };
    case "name":
      return t.step === "arrival" ? enter({ ...t, name: tidyName(e.name) }, "talk") : t;
    case "heard":
      if (t.step !== "talk" || t.phase !== "wait") return t;
      return e.text.trim() ? phase({ ...t, again: false }, "answered") : { ...t, again: true, hint: true };
    case "asked":
      return t.step === "screen" && t.phase === "wait" ? phase(t, "working") : t;
    case "answered":
      return t.step === "screen" && t.phase === "working" ? phase(t, "answered") : t;
    case "submit":
      return t.step === "about" && t.phase === "form" ? phase(t, "saving") : t;
    case "saved":
      return t.step === "about" && t.phase === "saving" ? phase(t, "thanks") : t;
    case "underage":
      return t.step === "about" && t.phase !== "underage" ? phase(t, "underage") : t;
    case "connect":
      return t.step === "google" && t.phase === "offer" ? phase(t, "connecting") : t;
    case "connected":
      return t.step === "google" && t.phase !== "connected" ? phase(t, "connected") : t;
    case "demo":
      return t.step === "demos" ? { ...phase(t, "play"), demo: e.demo, played: t.played.includes(e.demo) ? t.played : [...t.played, e.demo] } : t;
    case "next":
      if (t.phase === "underage") return { ...t, ended: "underage" };
      if (t.step === "arrival" && t.phase !== "ask") return phase(t, "ask"); // straight to the question
      if (t.step === "arrival") return enter(t, "talk"); // no name given
      if (t.step === "about" && t.phase === "form") return t; // the card's own button submits it
      if (t.step === "demos" && t.phase === "play") return { ...phase(t, "pick"), demo: null };
      if (t.step === "landing") return { ...t, ended: "finished" };
      return onward(t);
    case "skip_step":
      if (t.phase === "underage") return { ...t, ended: "underage" };
      if (t.step === "demos" && t.phase === "play") return { ...phase(t, "pick"), demo: null };
      if (t.step === "landing") return { ...t, ended: "finished" };
      return onward(t);
  }
}

export interface Line {
  /** Stable per line, so it is said once. */
  id: string;
  text: string;
  /** A second, smaller line under it. */
  sub?: string;
}

/** What Pyxi is saying at this moment of the tour: shown in large type, and spoken. */
export function lineFor(t: Tour, keys: string[] = ["Ctrl", "Alt"]): Line | null {
  const hold = keys.length ? `Hold ${keys.join(" and ")}` : "Click the notch";
  const named = t.name ? `, ${t.name}` : "";
  switch (`${t.step}/${t.phase}`) {
    case "arrival/hello": return { id: "hello", text: "Hi. I'm Pyxi.", sub: "I live in the corner of your screen." };
    case "arrival/ask": return { id: "ask-name", text: "What should I call you?" };
    case "talk/wait":
      return t.again
        ? { id: "talk-again", text: "I didn't catch that.", sub: `${hold}, and say hello once more.` }
        : { id: "talk", text: `${hold}, and say hello.` };
    case "talk/answered": return { id: "talk-answered", text: `Hello${named}.`, sub: "I can hear you. That's all it takes." };
    case "screen/wait": return { id: "screen", text: "Now point me at something.", sub: `Hover anything on your screen, ${hold[0].toLowerCase()}${hold.slice(1)}, and ask what it is.` };
    case "screen/working": return { id: "screen-working", text: "Looking." };
    case "screen/answered": return { id: "screen-answered", text: "That's it.", sub: "Anything you can see, you can ask me about." };
    case "about/form": return { id: "about", text: "A few things about you,", sub: "so I can help." };
    case "about/saving":
    case "about/thanks": return { id: "about-thanks", text: `Thank you${named}.` };
    case "about/underage": return { id: "underage", text: "Pyxi is for people 13 and over.", sub: "I haven't kept anything you typed. Come back when you're older." };
    case "google/offer":
    case "google/connecting": return { id: "google", text: "Want me to help with your calendar, email, Sheets and Docs?" };
    case "google/connected": return { id: "google-connected", text: "Connected.", sub: "Ask me what's on your calendar any time." };
    case "demos/pick":
    case "demos/play": return { id: "demos", text: "A few things I can do.", sub: "Tap one, or carry on." };
    case "landing/rise":
    case "landing/glow": return { id: "landing", text: "I'll be right here.", sub: `${hold} whenever you need me.` };
    default: return null;
  }
}

/** The words to speak for a line (the big line and the small one, as one sentence run). */
export function spoken(line: Line): string {
  return line.sub ? `${line.text} ${line.sub}` : line.text;
}

/** Does this moment leave the screen clear (no dim), so they can use what is under it? */
export function screenClear(t: Tour): boolean {
  return t.step === "screen" || (t.step === "landing" && t.phase === "glow");
}
