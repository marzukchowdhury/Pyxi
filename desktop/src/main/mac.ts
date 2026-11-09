/**
 * The Mac-only parts of the main process, kept apart (and free of Electron imports) so they can
 * be tested anywhere: the permissions Pyxi needs, where in System Settings each is granted, the
 * menu bar icon, and the shortcuts that suit a Mac keyboard.
 *
 * Pyxi never changes a setting itself. It reads what's allowed and opens the right page of
 * System Settings for the user to flip the switch.
 */

export type MacPermission = "screen" | "accessibility" | "microphone" | "input";

const PRIVACY = "x-apple.systempreferences:com.apple.preference.security?";

/** The System Settings page for each permission (macOS 13 and later still honour these links). */
export const MAC_SETTINGS: Record<MacPermission, string> = {
  screen: `${PRIVACY}Privacy_ScreenCapture`,
  accessibility: `${PRIVACY}Privacy_Accessibility`,
  microphone: `${PRIVACY}Privacy_Microphone`,
  input: `${PRIVACY}Privacy_ListenEvent`,
};

export interface PermissionRow {
  key: MacPermission;
  title: string;
  why: string;
  /** true granted, false not yet, null: macOS has no way to ask (check the page). */
  granted: boolean | null;
}

/** What each permission is for, in the order the card lists them. */
export function permissionRows(status: {
  screen: string;
  accessibility: boolean;
  microphone: string;
}): PermissionRow[] {
  return [
    {
      key: "screen",
      title: "Screen Recording",
      why: "So Pyxi can see what's on your screen when you ask about it.",
      granted: status.screen === "granted",
    },
    {
      key: "accessibility",
      title: "Accessibility",
      why: "So Pyxi can read buttons and fields, arrange windows, hear hold-to-talk, and use the mouse and keyboard when you allow it.",
      granted: status.accessibility,
    },
    {
      key: "microphone",
      title: "Microphone",
      why: "So you can talk to Pyxi.",
      granted: status.microphone === "granted",
    },
    {
      key: "input",
      title: "Input Monitoring",
      why: "Some macOS versions also ask for this before hold-to-talk can hear the keys.",
      granted: null,
    },
  ];
}

/** Does the first-run card still have something to ask for? */
export function needsPermissions(rows: PermissionRow[]): boolean {
  return rows.some((r) => r.granted === false);
}

/** Only these pages may be opened from the card: never an arbitrary URL from a page. */
export function settingsUrl(key: unknown): string | null {
  return typeof key === "string" && Object.hasOwn(MAC_SETTINGS, key) ? MAC_SETTINGS[key as MacPermission] : null;
}

/** Files that run something when opened on a Mac: shown in Finder instead, never opened. */
export const MAC_RUNNABLE = /\.(app|command|tool|sh|zsh|bash|csh|pkg|mpkg|dmg|scpt|scptd|applescript|workflow|terminal|action|jar|webloc|inetloc|fileloc|prefpane|kext|plugin|dylib)$/i;

/** Global shortcuts for a Mac keyboard: no Pause or End keys on most of them. */
export const MAC_HOTKEYS = {
  talk: ["Control+Alt+Space", "Control+Shift+Space", "Alt+Shift+Space", "F8"],
  panel: ["Control+Alt+W", "Control+Shift+W", "Alt+Shift+W"],
  dismiss: ["Control+Alt+Backspace", "Control+Shift+Backspace"],
  privacy: ["Control+Alt+P", "Control+Shift+P"],
  stop: ["Control+Alt+Escape", "Control+Shift+Escape"],
} as const;

/** "Control+Alt+Space" -> "Control+Option+Space": what the keys are called on a Mac. */
export function macKeyName(accelerator: string | null): string | null {
  return accelerator ? accelerator.replace(/\bAlt\b/g, "Option").replace(/\b(Command|Cmd|Meta|Super)\b/g, "Command") : null;
}

/**
 * The menu bar icon as raw pixels: the firefly (a round body and two wings) in black with
 * alpha, for a template image macOS tints to suit a light or dark menu bar. BGRA, `size` x `size`.
 */
export function trayTemplatePixels(size: number): Buffer {
  const buf = Buffer.alloc(size * size * 4);
  const s = size / 22; // drawn on a 22-point grid
  const shapes = [
    { cx: 11, cy: 13, rx: 4.2, ry: 4.2, a: 1 }, // the glowing body
    { cx: 6.6, cy: 7.4, rx: 4, ry: 2.3, a: 0.75, tilt: -0.55 }, // wings
    { cx: 15.4, cy: 7.4, rx: 4, ry: 2.3, a: 0.75, tilt: 0.55 },
  ];
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      let alpha = 0;
      for (const sh of shapes) {
        // A few samples per pixel for soft edges.
        let hits = 0;
        for (const [ox, oy] of [[0.25, 0.25], [0.75, 0.25], [0.25, 0.75], [0.75, 0.75]]) {
          const px = (x + ox) / s - sh.cx;
          const py = (y + oy) / s - sh.cy;
          const t = sh.tilt ?? 0;
          const u = px * Math.cos(t) + py * Math.sin(t);
          const v = -px * Math.sin(t) + py * Math.cos(t);
          if ((u * u) / (sh.rx * sh.rx) + (v * v) / (sh.ry * sh.ry) <= 1) hits++;
        }
        alpha = Math.max(alpha, (hits / 4) * sh.a);
      }
      const i = (y * size + x) * 4;
      buf[i + 3] = Math.round(alpha * 255); // B, G, R stay 0: black
    }
  }
  return buf;
}
