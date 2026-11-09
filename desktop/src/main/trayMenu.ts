// The tray icon's right-click menu, laid out the way people expect from a small always-on helper
// (HeyClicky does the same): open it, its settings, pause it, then report a bug, ask for a
// feature, see what's new, check for updates, quit. The hotkey-only actions (talk, dismiss
// drawings) stay on their hotkeys and in Settings.
import { release } from "node:os";
import type { MenuItemConstructorOptions } from "electron";

export const RELEASES = "https://github.com/marzukchowdhury/Pyxi/releases";
const ISSUES = "https://github.com/marzukchowdhury/Pyxi/issues/new";

/** "Windows 10.0.26100" or "macOS (Darwin 24.1.0)": which system, for a bug report. */
export function systemName(platform: string = process.platform): string {
  return platform === "darwin" ? `macOS (Darwin ${release()})` : `Windows ${release()}`;
}

/** A new GitHub issue, filled in with what helps (the version, the system), never anything personal. */
export function issueUrl(kind: "bug" | "feature", version: string): string {
  const body =
    kind === "bug"
      ? `What happened:\n\n\nWhat you expected:\n\n\nSteps to see it again:\n1. \n\n---\nPyxi ${version}, ${systemName()}`
      : `What would you like Pyxi to do?\n\n\nWhy would it help?\n\n\n---\nPyxi ${version}`;
  const params = new URLSearchParams({
    labels: kind === "bug" ? "bug" : "enhancement",
    title: kind === "bug" ? "Bug: " : "Idea: ",
    body,
  });
  return `${ISSUES}?${params.toString()}`;
}

export interface TrayActions {
  version: string;
  hotkey: (name: "panel" | "stop") => string;
  paused: boolean;
  muted: boolean;
  openPyxi: () => void;
  openSettings: () => void;
  togglePause: () => void;
  setMuted: (muted: boolean) => void;
  stopEverything: () => void;
  openUrl: (url: string) => void;
  checkForUpdates: () => void;
  quit: () => void;
  /** On a Mac: the permissions card (Screen Recording, Accessibility, Microphone). */
  openPermissions?: () => void;
}

export function trayTemplate(a: TrayActions): MenuItemConstructorOptions[] {
  return [
    { label: `Open Pyxi  (${a.hotkey("panel")})`, click: a.openPyxi },
    { label: "Open Settings", click: a.openSettings },
    { label: a.paused ? "Resume Pyxi" : "Pause Pyxi", click: a.togglePause },
    { label: "Mute Pyxi's voice", type: "checkbox", checked: a.muted, click: (item) => a.setMuted(item.checked) },
    { label: `Stop everything  (${a.hotkey("stop")})`, click: a.stopEverything },
    ...(a.openPermissions ? [{ label: "Permissions…", click: a.openPermissions }] : []),
    { type: "separator" },
    { label: "Report a Bug…", click: () => a.openUrl(issueUrl("bug", a.version)) },
    { label: "Request a Feature…", click: () => a.openUrl(issueUrl("feature", a.version)) },
    { label: "What's New", click: () => a.openUrl(`${RELEASES}/tag/v${a.version}`) },
    { label: "Check for Updates…", click: a.checkForUpdates },
    { type: "separator" },
    { label: "Quit Pyxi", click: a.quit },
  ];
}
