// What the caption bar shows, worked out from the caption events (no drawing here, so it can
// be tested). The bar holds two lines at most: the sentence being said and, while there is
// room, the one before it. A line leaves a few seconds after it was last touched.

export interface CaptionLine {
  id: number;
  text: string;
  final: boolean;
  translation: string | null;
  /** A translation is on its way: the original is shown meanwhile. */
  pending: boolean;
  /** When it last changed (ms, the caller's clock). */
  at: number;
}

export interface CaptionEvent {
  id: number;
  text: string;
  final: boolean;
  translation: string | null;
  pending: boolean;
}

/** How long a finished line stays once nothing new is said. */
export const LINGER_MS = 6000;
/** A line still being said that stopped changing (the sound was cut off): it goes too. */
export const STALE_MS = 12000;
const KEEP = 3;

/** Fold one event into the lines: the same id is the same line, updated. */
export function withCaption(lines: CaptionLine[], e: CaptionEvent, now: number): CaptionLine[] {
  const old = lines.find((l) => l.id === e.id);
  if (!e.text && !old) return lines;
  if (!e.text) return lines.filter((l) => l.id !== e.id); // it turned out to be no speech
  const line: CaptionLine = {
    id: e.id,
    text: e.text,
    final: e.final,
    // A late update of the words never throws away a translation that already came.
    translation: e.translation ?? (old && old.text === e.text ? old.translation : null),
    pending: e.pending,
    at: now,
  };
  const rest = lines.filter((l) => l.id !== e.id);
  return [...rest, line].sort((a, b) => a.id - b.id).slice(-KEEP);
}

/** Drop lines whose time is up. */
export function fresh(lines: CaptionLine[], now: number): CaptionLine[] {
  return lines.filter((l) => now - l.at < (l.final ? LINGER_MS : STALE_MS));
}

export interface Shown {
  /** The large line(s): the translation where there is one, else the words as said. */
  main: string;
  /** The smaller line above, with "show the original too": the words as said. */
  original: string;
  /** The last sentence is still being translated (drawn a little dimmer). */
  waiting: boolean;
}

/** The text for the bar. `translating`: a language is chosen; `both`: original shown too. */
export function shown(lines: CaptionLine[], translating: boolean, both: boolean): Shown {
  const last = lines.slice(-2);
  if (!last.length) return { main: "", original: "", waiting: false };
  const main = last.map((l) => (translating && l.translation ? l.translation : l.text)).join(" ");
  const translated = last.filter((l) => translating && l.translation);
  const original = both && translated.length ? translated.map((l) => l.text).join(" ") : "";
  const tail = last[last.length - 1];
  return { main, original, waiting: translating && tail.final && tail.pending && !tail.translation };
}

/** "12:05", or "1:02:05" past the hour. */
export function elapsed(sinceUnix: number, nowMs: number): string {
  const s = Math.max(0, Math.floor(nowMs / 1000 - sinceUnix));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const two = (n: number) => String(n).padStart(2, "0");
  return h ? `${h}:${two(m)}:${two(s % 60)}` : `${m}:${two(s % 60)}`;
}

/** Caption text sizes, in px, for the bar's smaller and bigger buttons. */
export const SIZES = [17, 21, 26, 32, 40];
