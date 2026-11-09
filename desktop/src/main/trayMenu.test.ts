import { describe, expect, it } from "vitest";
import { issueUrl, RELEASES, trayTemplate, type TrayActions } from "./trayMenu";

const noop = () => undefined;
const actions = (over: Partial<TrayActions> = {}): TrayActions => ({
  version: "0.1.10",
  hotkey: (name) => (name === "panel" ? "Ctrl+Alt+W" : "Ctrl+Alt+S"),
  paused: false,
  muted: false,
  openPyxi: noop,
  openSettings: noop,
  togglePause: noop,
  setMuted: noop,
  stopEverything: noop,
  openUrl: noop,
  checkForUpdates: noop,
  quit: noop,
  ...over,
});

describe("the tray menu", () => {
  it("reads like a small app's menu, quit last", () => {
    const labels = trayTemplate(actions()).map((i) => i.label ?? "---");
    expect(labels).toEqual([
      "Open Pyxi  (Ctrl+Alt+W)",
      "Open Settings",
      "Pause Pyxi",
      "Mute Pyxi's voice",
      "Stop everything  (Ctrl+Alt+S)",
      "---",
      "Report a Bug…",
      "Request a Feature…",
      "What's New",
      "Check for Updates…",
      "---",
      "Quit Pyxi",
    ]);
  });

  it("says Resume while paused", () => {
    expect(trayTemplate(actions({ paused: true }))[2].label).toBe("Resume Pyxi");
  });

  it("opens a filled-in report and this version's notes", () => {
    const opened: string[] = [];
    const menu = trayTemplate(actions({ openUrl: (u) => opened.push(u) }));
    for (const label of ["Report a Bug…", "What's New"]) {
      const item = menu.find((i) => i.label === label)!;
      (item.click as () => void)();
    }
    const bug = new URL(opened[0]);
    expect(bug.searchParams.get("labels")).toBe("bug");
    expect(bug.searchParams.get("body")).toContain("Pyxi 0.1.10");
    expect(opened[1]).toBe(`${RELEASES}/tag/v0.1.10`);
    expect(issueUrl("feature", "0.1.10")).toContain("labels=enhancement");
  });
});
