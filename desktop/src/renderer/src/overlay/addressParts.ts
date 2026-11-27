// The address check card's helpers: an address a character at a time, so a slip is easy to
// spot (digits tinted, look-alikes marked), what is wrong with one that can't be an address,
// and the words said aloud while the card is up (src/pyxi/companion/addresscheck.py).

/** One address on the card, as the brain sends it. */
export interface AddressItem {
  address: string;
  /** The likely address when this one is a slip away from it: a one-tap chip. */
  suggestion?: string | null;
  /** Why it can't be an address, in a few words: the card opens ready to type. */
  problem?: string | null;
  /** The raw words, when it was heard by voice. */
  heard?: string | null;
}

export interface Char {
  ch: string;
  /** Where it is in the whole address (the caret goes here on a click). */
  at: number;
  kind: "letter" | "digit" | "mark";
  /** Set on characters that look like another one: what this one really is. */
  alike?: string;
}

export interface Parts {
  /** Before the @. */
  name: Char[];
  /** After the @; empty when there is no @ yet. */
  domain: Char[];
  /** Where the @ is, or -1. */
  at: number;
}

const ALIKE: Record<string, string> = {
  l: "small L",
  I: "capital i",
  "1": "the digit one",
  O: "capital o",
  "0": "zero",
};

/** The address split at its (last) @, each character with what it is. */
export function parts(address: string): Parts {
  const at = address.lastIndexOf("@");
  const chars: Char[] = [...address].map((ch, i) => ({
    ch,
    at: i,
    kind: /[0-9]/.test(ch) ? "digit" : /[A-Za-z]/.test(ch) ? "letter" : "mark",
    ...(ALIKE[ch] ? { alike: ALIKE[ch] } : {}),
  }));
  // "rn" reads as "m" in most faces, and "vv" as "w".
  for (let i = 0; i + 1 < chars.length; i++) {
    const pair = address.slice(i, i + 2);
    const says = pair === "rn" ? "r then n, not m" : pair === "vv" ? "two v's, not w" : "";
    if (says) for (const c of [chars[i], chars[i + 1]]) c.alike ??= says;
  }
  if (at < 0) return { name: chars, domain: [], at };
  return { name: chars.slice(0, at), domain: chars.slice(at + 1), at };
}

/** What stops this from being an address, in a few words; null when the shape is right.
 * (The same checks, in the same words, as addresscheck.problem in the brain.) */
export function problem(address: string): string | null {
  const a = address.trim();
  if (!a) return "It is empty.";
  if (/\s/.test(a)) return "There is a space in it.";
  const ats = a.split("@").length - 1;
  if (ats === 0) return "There is no @ in it.";
  if (ats > 1) return "There is more than one @.";
  const [local, domain] = a.split("@");
  if (!local) return "Nothing comes before the @.";
  if (!domain) return "Nothing comes after the @.";
  if (!domain.includes(".")) return "The part after the @ needs a dot, like example.com.";
  if (a.includes("..")) return "There are two dots in a row.";
  if (local.startsWith(".") || local.endsWith(".") || /^[.-]|[.-]$/.test(domain)) return "A dot is in the wrong place.";
  if (!/^[A-Za-z0-9._%+'-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$/.test(a)) {
    const tail = domain.slice(domain.lastIndexOf(".") + 1);
    return /^[A-Za-z]{2,}$/.test(tail) ? "It has a character an address can't have." : "The ending after the last dot looks wrong.";
  }
  return null;
}

/** What is typed in the box, as it will be sent: no spaces around it, nothing pasted with it. */
export function tidy(typed: string): string {
  return typed.trim().replace(/^<|>$/g, "").replace(/^mailto:/i, "").replace(/[.,;:!?]+$/, "");
}

/** A size for the address that keeps it on the card: long ones are drawn smaller. */
export function sizeFor(address: string): "big" | "mid" | "small" {
  return address.length <= 24 ? "big" : address.length <= 34 ? "mid" : "small";
}

// ---- words said while the card is up ("yes", a correction, "never mind")
export interface Spoken {
  action: "confirm" | "fill" | "edit" | "cancel";
  address?: string;
  heard?: string;
}

const listeners = new Set<(s: Spoken) => void>();

export function onSpoken(fn: (s: Spoken) => void): () => void {
  listeners.add(fn);
  return () => void listeners.delete(fn);
}

export function spoken(s: Spoken): void {
  for (const fn of [...listeners]) fn(s);
}
