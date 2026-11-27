import { describe, expect, it } from "vitest";
import { offlineLabel, offlineWords, SLOW_MS, WAKE_MS } from "./waking";

describe("what Pyxi says while its brain is not connected", () => {
  it("never tells someone who installed it to run a command", () => {
    for (const waited of [0, WAKE_MS - 1, WAKE_MS, 40_000, SLOW_MS - 1, SLOW_MS, 600_000]) {
      const w = offlineWords(waited, false);
      expect(w.command).toBe(false);
      expect(w.text).not.toMatch(/serve|brain/i);
    }
    expect(offlineLabel(false)).toBe("Waking up");
  });

  it("is patient through a slow first start, then says what to do", () => {
    expect(offlineWords(5_000, false)).toEqual({ text: "Waking up", busy: true, command: false });
    expect(offlineWords(35_000, false).busy).toBe(true);
    expect(offlineWords(35_000, false).text).toMatch(/first start/);
    const stuck = offlineWords(SLOW_MS, false);
    expect(stuck.busy).toBe(false);
    expect(stuck.text).toMatch(/tray icon/);
  });

  it("still tells a developer how to start the brain", () => {
    expect(offlineWords(5_000, true).command).toBe(false);
    expect(offlineWords(WAKE_MS, true)).toEqual({ text: "Can't reach Pyxi's brain. Run", busy: false, command: true });
    expect(offlineLabel(true)).toMatch(/pyxi serve/);
  });
});
