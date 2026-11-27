/**
 * What Pyxi says while its brain is not connected: at start-up, and for the few seconds the
 * brain restarts by itself (after signing in, after an update).
 *
 * Someone who installed Pyxi never starts the brain by hand, so the installed app never tells
 * them to: it says it is waking up, then that the first start takes longer, and only after a
 * long wait what to do about it. `pyxi serve` is for a developer running the app from source.
 */
export const WAKE_MS = 15_000; // a normal start is well inside this
export const SLOW_MS = 120_000; // a first start (cold disk, antivirus reading new files) can take a minute

export interface OfflineWords {
  text: string;
  /** Still expected to come up: shown with the shimmer, not as a fault. */
  busy: boolean;
  /** Show the `pyxi serve` command after the text (developing only). */
  command: boolean;
}

export function offlineWords(waitedMs: number, dev: boolean): OfflineWords {
  if (waitedMs < WAKE_MS) return { text: "Waking up", busy: true, command: false };
  if (dev) return { text: "Can't reach Pyxi's brain. Run", busy: false, command: true };
  if (waitedMs < SLOW_MS) return { text: "Still waking up. The first start takes a little longer.", busy: true, command: false };
  return { text: "Pyxi didn't start. Quit it from its tray icon, then open it again.", busy: false, command: false };
}

/** The same, for the small labels (the panel's header, the other screens' notice). */
export function offlineLabel(dev: boolean): string {
  return dev ? "Brain offline: run pyxi serve" : "Waking up";
}
