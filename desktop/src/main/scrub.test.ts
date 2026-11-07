import { describe, expect, it } from "vitest";
import { scrubLine } from "./scrub";

describe("log scrubbing", () => {
  it("removes emails, phones, user names in paths and tokens", () => {
    const line =
      "error: C:\\Users\\Taysseer\\AppData\\Roaming\\Pyxi failed for sam@uni.edu, call +1 604-555-0199, token sk-or-v1-" + "a".repeat(40);
    expect(scrubLine(line)).toBe(
      "error: C:\\Users\\<user>\\AppData\\Roaming\\Pyxi failed for <email>, call <phone>, token [hidden]",
    );
    expect(scrubLine("file:///c:/users/omen/Desktop/x")).toBe("file:///c:/users/<user>/Desktop/x");
  });
  it("keeps versions, dates, times and addresses on this PC", () => {
    const line = "2026-11-07T12:30:00Z checking (this is 0.1.6) http://127.0.0.1:8766/api/health";
    expect(scrubLine(line)).toBe(line);
  });
  it("hides a launch key it's told about", () => {
    expect(scrubLine("got abcdefghijklmnopqrstuvwx in a url", ["abcdefghijklmnopqrstuvwx"])).toBe("got [hidden] in a url");
  });
});
