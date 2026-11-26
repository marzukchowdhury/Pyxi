// The buttons on a result card (the notch's `job_ready` card), as plain data so they can be
// tested without a screen. A card has at most two: the one that mends a failure first, then
// what opens the result.
// (Nothing is imported here: the tests load it without the app around it.)

/** What a card needs to know to choose its buttons (a toast in overlay/store.ts). */
export interface CardFacts {
  ok: boolean;
  actions: string[];
  /** The label of the Pyxi account's button for these actions ("Choose a plan"), if there is one
   * (shared/account.ts cardButton). */
  account?: string | null;
  link?: string | null;
  files: string[];
  calendar?: string | null;
  /** What "Try again" runs again, in words (from the brain). Without it there is no such button. */
  retry?: string | null;
}

export type CardButtonKind = "update" | "google" | "account" | "settings" | "retry" | "link" | "file" | "calendar" | "folder" | "send_back";

export interface CardButtonSpec {
  kind: CardButtonKind;
  label: string;
  primary: boolean;
  /** Shown when the pointer rests on the button. */
  hint?: string;
}

export const MAX_BUTTONS = 2;

/** Where "Open Settings" goes: the tab, and the part of Settings to bring into view. */
export interface SettingsTarget {
  tab: "settings" | "voice";
  section: "files" | "accounts" | "budget" | null;
}

const SECTIONS = new Set(["files", "accounts", "budget"]);

/** "settings", "settings:files", "settings:voice"... as a place in the panel (null: no such action). */
export function settingsTarget(actions: string[]): SettingsTarget | null {
  const found = actions.find((a) => a === "settings" || a.startsWith("settings:"));
  if (!found) return null;
  const part = found.split(":")[1] ?? "";
  if (part === "voice") return { tab: "voice", section: null };
  return { tab: "settings", section: SECTIONS.has(part) ? (part as SettingsTarget["section"]) : null };
}

/** The line that says what Try again will run, so pressing it is never a surprise. */
export function retryLine(what: string): string {
  return `Try again runs this once more: ${what}`;
}

/** The card's buttons, most useful first, never more than two. */
export function cardButtons(r: CardFacts): CardButtonSpec[] {
  const has = (a: string) => r.actions.includes(a);
  const out: CardButtonSpec[] = [];
  if (has("update")) out.push({ kind: "update", label: "Restart to update", primary: true });
  // What mends a failure: one of these at most (the brain gives a line one button).
  if (has("google")) out.push({ kind: "google", label: "Sign in to Google", primary: true });
  if (r.account) out.push({ kind: "account", label: r.account, primary: true });
  const settings = settingsTarget(r.actions);
  if (settings) out.push({ kind: "settings", label: settings.tab === "voice" ? "Open Voice settings" : "Open Settings", primary: true });
  // Try again: only on a failure, and only when the brain said what it would run again.
  if (has("retry") && !r.ok && r.retry) out.push({ kind: "retry", label: "Try again", primary: true, hint: retryLine(r.retry) });
  // What opens the result.
  if (r.link) out.push({ kind: "link", label: r.link.includes("meet.google.com") ? "Join Google Meet" : "Open", primary: true });
  if (r.files[0]) out.push({ kind: "file", label: "Open file", primary: true });
  if (has("calendar") && r.calendar) out.push({ kind: "calendar", label: "Add to calendar", primary: true });
  if (has("folder") && r.files[0]) out.push({ kind: "folder", label: "Show in folder", primary: false });
  if (has("send_back")) out.push({ kind: "send_back", label: "Send it back", primary: false });
  return out.slice(0, MAX_BUTTONS);
}
