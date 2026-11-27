// What the dictation sign says. Kept apart from the drawing so every line can be tested.
import type { DictationOutcome } from "../shared/protocol";

/** Where a dictation is. The brain's "dictated" event ends it; so do the microphone failing,
 * silence, and Esc, which the overlay decides by itself. */
export type DictationView =
  | { phase: "listening"; mode: "hold" | "toggle"; keys: string; at: number }
  | { phase: "working"; at: number }
  | { phase: "done"; how: DictationOutcome; text: string; note: string; at: number };

/** The longest one dictation runs before it is stopped and typed. */
export const DICTATION_MAX_MS = 5 * 60 * 1000;

/** Notes the overlay writes itself (the brain writes the rest, see pyxi/dictation.py NOTES). */
export const OWN_NOTES = {
  nothing: "I didn't hear anything to type.",
  cancelled: "Nothing was typed.",
  asking: "I'm listening to your question right now. Dictate when that is done.",
  offline: "Pyxi's brain isn't running, so I can't write that down.",
  silent: "It took too long to write that down. Try again?",
} as const;

/** A recording that is a slip of the keys or plain silence: never sent to be written down. */
export function tooQuiet(peak: number, ms: number): boolean {
  return ms < 350 || peak < 0.03;
}

/** Did Windows (its microphone privacy switch) keep the microphone from starting? */
export function micBlocked(reason: string): boolean {
  return /NotAllowed|Permission|SecurityError/i.test(reason);
}

/** The card for a blocked microphone: the same words as the notice, and the one button that
 * opens the Windows page where it is let through ("mic_settings", overlay/cardButtons.ts). */
export function micBlockedCard(reason: string): { job_id: string; agent: string; ok: boolean; title: string; body: string; files: string[]; changes: string[]; notes: string[]; actions: string[] } | null {
  if (!micBlocked(reason)) return null;
  return { job_id: "mic-blocked", agent: "Pyxi", ok: false, title: "The microphone is blocked", body: micProblem(reason), files: [], changes: [], notes: [], actions: ["mic_settings"] };
}

/** Why the microphone didn't start, in words that say what to do. */
export function micProblem(reason: string): string {
  if (micBlocked(reason))
    return "Windows blocked the microphone. Turn on Settings > Privacy & security > Microphone > Let desktop apps access your microphone.";
  if (/NotFound|Overconstrained|DevicesNotFound/i.test(reason)) return "No microphone was found. Plug one in, or pick an input in Windows Sound settings.";
  if (/NotReadable|TrackStart|Abort/i.test(reason)) return "The microphone is in use by another app, or it didn't start. Close what is using it and try again.";
  return "The microphone couldn't start. Check Settings > Privacy & security > Microphone.";
}

const TITLES: Record<DictationOutcome, string> = {
  typed: "Typed",
  copied: "Copied",
  password: "Nothing typed",
  nothing: "Nothing typed",
  cancelled: "Cancelled",
  failed: "Dictation didn't work",
};

export interface SignText {
  title: string;
  /** What was typed or copied, shortened (empty when nothing was). */
  body: string;
  note: string;
  tone: "live" | "busy" | "good" | "plain" | "warn";
}

const pretty = (keys: string) => keys.replace("Control", "Ctrl").replace(/\+/g, " + ");

/** One line of what was typed: line breaks shown as a mark, the middle of a long one left out. */
export function preview(text: string, max = 150): string {
  const flat = text.trim().replace(/\n+/g, " ↵ ").replace(/[ \t]+/g, " ");
  if (flat.length <= max) return flat;
  const cut = Math.ceil(max * 0.6);
  // Whole words on both sides of the gap.
  const head = flat.slice(0, cut).replace(/\s+\S*$/, "") || flat.slice(0, cut);
  const tail = flat.slice(flat.length - (max - cut)).replace(/^\S*\s+/, "");
  return `${head} … ${tail}`;
}

export function signText(view: DictationView, elapsedMs = 0): SignText {
  if (view.phase === "listening") {
    const left = DICTATION_MAX_MS - elapsedMs;
    const note =
      left < 20_000
        ? `${Math.max(1, Math.ceil(left / 1000))} seconds left. Then it is typed.`
        : view.mode === "hold"
          ? "Let go to type it. Esc cancels."
          : `Press ${pretty(view.keys)} again to type it. Esc cancels.`;
    return { title: elapsedMs >= 10_000 ? `Listening  ${clock(elapsedMs)}` : "Listening", body: "", note, tone: "live" };
  }
  if (view.phase === "working") return { title: "Writing it down", body: "", note: "Esc cancels.", tone: "busy" };
  const tone = view.how === "typed" ? "good" : view.how === "copied" ? "plain" : view.how === "cancelled" || view.how === "nothing" ? "plain" : "warn";
  return { title: TITLES[view.how], body: view.text ? preview(view.text) : "", note: view.note, tone };
}

function clock(ms: number): string {
  const s = Math.floor(ms / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/** How long a finished sign stays: long enough to read what it says. */
export function lingerMs(view: DictationView): number {
  if (view.phase !== "done") return Infinity;
  const words = `${view.note} ${view.text ? preview(view.text) : ""}`.trim().split(/\s+/).length;
  return Math.min(9000, Math.max(view.how === "typed" && !view.note ? 2200 : 4000, 1200 + words * 260));
}

/** Should a "dictated" event from the brain replace what the sign shows now? While a new
 * dictation is being recorded, only a refusal does (a password box is in front): the result of
 * the one before is already in the app. */
export function takesOver(now: DictationView | null, how: DictationOutcome): boolean {
  return now?.phase !== "listening" || how === "password";
}
