import { inflateSync } from "node:zlib";
import { describe, expect, it } from "vitest";
import { MAC_RUNNABLE, MAC_SETTINGS, macKeyName, needsPermissions, permissionRows, settingsUrl, trayTemplatePixels, trayTemplatePng } from "./mac";

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

  it("packs the same pixels into a PNG that holds them exactly", () => {
    for (const size of [22, 44]) {
      const png = trayTemplatePng(size);
      expect(png.subarray(0, 8)).toEqual(Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]));
      expect(png.subarray(12, 16).toString("ascii")).toBe("IHDR");
      expect([png.readUInt32BE(16), png.readUInt32BE(20), png[24], png[25]]).toEqual([size, size, 8, 6]);
      const idat = png.indexOf("IDAT");
      const rows = inflateSync(png.subarray(idat + 4, idat + 4 + png.readUInt32BE(idat - 4)));
      const px = trayTemplatePixels(size);
      for (let y = 0; y < size; y++) {
        expect(rows[y * (size * 4 + 1)]).toBe(0);
        expect(rows.subarray(y * (size * 4 + 1) + 1, (y + 1) * (size * 4 + 1))).toEqual(px.subarray(y * size * 4, (y + 1) * size * 4));
      }
      expect(png.subarray(-8, -4).toString("ascii")).toBe("IEND");
    }
  });
});
