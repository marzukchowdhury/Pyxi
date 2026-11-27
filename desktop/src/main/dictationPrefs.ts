// Dictation's settings: on or off, which keys, hold or press, and whether a long dictation may
// have one quick model call to tidy it. Kept with the app's other interface settings (ui.json),
// because the keys belong to the app: the brain only hears "they are dictating".

export type DictationKeys = "ctrl+win" | "alt+win" | "shift+win";
export type DictationMode = "hold" | "toggle";

export interface DictationPrefs {
  /** Off: the keys do nothing and the microphone is never opened for dictation. */
  enabled: boolean;
  keys: DictationKeys;
  /** "hold": speak while the keys are down. "toggle": press once to start, again to stop. */
  mode: DictationMode;
  /** One quick model call tidies dictations of 30 words or more (never shorter ones). */
  polish: boolean;
  /** Off (the usual): dictation is written down on this PC, the quickest. On: by the cloud
   * listener, which is better with names and accents and about two seconds slower. */
  cloud: boolean;
}

export const DEFAULT_DICTATION: DictationPrefs = { enabled: true, keys: "ctrl+win", mode: "hold", polish: true, cloud: false };

const KEYS: readonly DictationKeys[] = ["ctrl+win", "alt+win", "shift+win"];

/** Whatever was saved (or sent by a page), made into settings that can be trusted. */
export function dictationPrefs(raw: unknown, base: DictationPrefs = DEFAULT_DICTATION): DictationPrefs {
  const r = (typeof raw === "object" && raw !== null ? raw : {}) as Record<string, unknown>;
  return {
    enabled: typeof r.enabled === "boolean" ? r.enabled : base.enabled,
    keys: KEYS.includes(r.keys as DictationKeys) ? (r.keys as DictationKeys) : base.keys,
    mode: r.mode === "hold" || r.mode === "toggle" ? r.mode : base.mode,
    polish: typeof r.polish === "boolean" ? r.polish : base.polish,
    cloud: typeof r.cloud === "boolean" ? r.cloud : base.cloud,
  };
}

/** "ctrl+win" -> "Control+Win", the way the other hotkeys are written. */
export function dictationAccelerator(keys: DictationKeys): string {
  return keys === "ctrl+win" ? "Control+Win" : keys === "alt+win" ? "Alt+Win" : "Shift+Win";
}
