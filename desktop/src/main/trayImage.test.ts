import { describe, expect, it } from "vitest";
import { TRAY_PNG_16, TRAY_PNG_32, TRAY_PNG_64 } from "./trayImage";

// The tray icon must be a real PNG: an SVG came out empty in Electron and the icon was invisible.
describe("tray icon", () => {
  it.each([
    [16, TRAY_PNG_16],
    [32, TRAY_PNG_32],
    [64, TRAY_PNG_64],
  ])("is a %i px PNG", (size, b64) => {
    const buf = Buffer.from(b64, "base64");
    expect(buf.subarray(0, 8).toString("hex")).toBe("89504e470d0a1a0a");
    expect(buf.readUInt32BE(16)).toBe(size); // width, from the IHDR chunk
    expect(buf.readUInt32BE(20)).toBe(size); // height
  });
});
