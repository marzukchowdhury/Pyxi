import { describe, expect, it } from "vitest";
import { MAC_RUNNABLE, MAC_SETTINGS, macKeyName, needsPermissions, permissionRows, settingsUrl, trayTemplatePixels } from "./mac";

describe("the Mac permissions card", () => {
  const rows = (screen: string, accessibility: boolean, microphone: string) =>
    permissionRows({ screen, accessibility, microphone });

  it("lists what each permission is for, and what's still missing", () => {
    const r = rows("denied", true, "not-determined");
    expect(r.map((x) => [x.key, x.granted])).toEqual([
      ["screen", false],
      ["accessibility", true],
      ["microphone", false],
      ["input", null], // macOS can't be asked about this one
    ]);
    expect(needsPermissions(r)).toBe(true);
    expect(needsPermissions(rows("granted", true, "granted"))).toBe(false);
    // Copy rules: no em dashes in anything the user reads.
    expect(r.every((x) => !x.why.includes("—") && !x.title.includes("—"))).toBe(true);
  });

  it("only ever opens its own System Settings pages", () => {
    expect(settingsUrl("screen")).toBe("x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture");
    expect(settingsUrl("accessibility")).toBe(MAC_SETTINGS.accessibility);
    expect(settingsUrl("https://evil.example")).toBeNull();
    expect(settingsUrl("toString")).toBeNull();
    expect(settingsUrl(42)).toBeNull();
  });
});

describe("Mac odds and ends", () => {
  it("never opens things that run", () => {
    for (const f of ["Installer.pkg", "x.app", "run.command", "a.sh", "Pyxi.dmg", "s.scpt", "w.workflow"]) {
      expect(MAC_RUNNABLE.test(f)).toBe(true);
    }
    for (const f of ["report.pdf", "photo.heic", "notes.txt"]) expect(MAC_RUNNABLE.test(f)).toBe(false);
  });

  it("names keys the Mac way", () => {
    expect(macKeyName("Control+Alt+Space")).toBe("Control+Option+Space");
    expect(macKeyName("Command+Shift+P")).toBe("Command+Shift+P");
    expect(macKeyName(null)).toBeNull();
  });

  it("draws a template icon: black, see-through around the firefly", () => {
    const px = trayTemplatePixels(22);
    expect(px.length).toBe(22 * 22 * 4);
    const alpha = (x: number, y: number) => px[(y * 22 + x) * 4 + 3];
    expect(alpha(11, 13)).toBe(255); // the body
    expect(alpha(0, 21)).toBe(0); // a corner
    for (let i = 0; i < px.length; i += 4) expect(px[i] + px[i + 1] + px[i + 2]).toBe(0);
    expect(trayTemplatePixels(44).length).toBe(44 * 44 * 4);
  });
});
