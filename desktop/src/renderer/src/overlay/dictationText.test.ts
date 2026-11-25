import { describe, expect, it } from "vitest";
import { DICTATION_MAX_MS, type DictationView, lingerMs, micProblem, OWN_NOTES, preview, signText, takesOver, tooQuiet } from "./dictationText";

const listening = (mode: "hold" | "toggle"): DictationView => ({ phase: "listening", mode, keys: "Control+Win", at: 0 });
const done = (how: Parameters<typeof takesOver>[1], text = "", note = ""): DictationView => ({ phase: "done", how, text, note, at: 0 });

describe("the dictation sign", () => {
  it("says it is listening, and how to end or cancel", () => {
    expect(signText(listening("hold"))).toMatchObject({ title: "Listening", note: "Let go to type it. Esc cancels.", tone: "live" });
    expect(signText(listening("toggle")).note).toBe("Press Ctrl + Win again to type it. Esc cancels.");
  });

  it("counts a long dictation and warns before the five minutes are up", () => {
    expect(signText(listening("hold"), 75_000).title).toBe("Listening  1:15");
    const nearly = signText(listening("toggle"), DICTATION_MAX_MS - 8_400);
    expect(nearly.note).toBe("9 seconds left. Then it is typed.");
  });

  it("shows what was typed, on one line", () => {
    const typed = signText(done("typed", "Dear Dana,\n\nThe form is attached. "));
    expect(typed).toMatchObject({ title: "Typed", body: "Dear Dana, ↵ The form is attached.", note: "", tone: "good" });
    const long = preview("word ".repeat(200));
    expect(long.length).toBeLessThan(165);
    expect(long).toContain("…");
  });

  it("says so when the words were copied, refused or never heard", () => {
    expect(signText(done("copied", "Hello there ", "No text box had the keyboard, so I copied it."))).toMatchObject({ title: "Copied", body: "Hello there", tone: "plain" });
    expect(signText(done("password", "", "That is a password box, so I typed nothing."))).toMatchObject({ title: "Nothing typed", body: "", tone: "warn" });
    expect(signText(done("nothing", "", OWN_NOTES.nothing)).title).toBe("Nothing typed");
    expect(signText(done("cancelled", "", OWN_NOTES.cancelled))).toMatchObject({ title: "Cancelled", note: "Nothing was typed." });
  });

  it("stays long enough to read, and no longer than nine seconds", () => {
    expect(lingerMs(listening("hold"))).toBe(Infinity);
    expect(lingerMs(done("typed", "Hi "))).toBe(2200);
    expect(lingerMs(done("copied", "Hi ", "No text box had the keyboard, so I copied it. Press Ctrl+V where you want it."))).toBeGreaterThan(4000);
    expect(lingerMs(done("typed", "word ".repeat(300)))).toBeLessThanOrEqual(9000);
  });

  it("no line uses a long dash", () => {
    for (const note of Object.values(OWN_NOTES)) expect(note).not.toMatch(/[—–]/);
    for (const reason of ["NotAllowedError: x", "NotFoundError: x", "NotReadableError: x", "Error: x"]) expect(micProblem(reason)).not.toMatch(/[—–]/);
  });
});

describe("what is never sent to be written down", () => {
  it("a slip of the keys and plain silence", () => {
    expect(tooQuiet(0.6, 120)).toBe(true); // let go while the microphone was still opening
    expect(tooQuiet(0.01, 4000)).toBe(true); // nobody spoke
    expect(tooQuiet(0.08, 900)).toBe(false); // a quiet voice still counts
  });
});

describe("the microphone not starting", () => {
  it("says what is wrong in each case", () => {
    expect(micProblem("NotFoundError: Requested device not found")).toMatch(/No microphone was found/);
    expect(micProblem("NotReadableError: Could not start audio source")).toMatch(/in use by another app/);
    expect(micProblem("NotAllowedError: Permission denied")).toMatch(/Windows blocked the microphone/);
    expect(micProblem("TypeError: boom")).toMatch(/couldn't start/);
  });
});

describe("a result that arrives while a new dictation is recorded", () => {
  it("does not replace the listening sign, unless it is a refusal", () => {
    expect(takesOver(listening("hold"), "typed")).toBe(false);
    expect(takesOver(listening("hold"), "password")).toBe(true);
    expect(takesOver({ phase: "working", at: 0 }, "typed")).toBe(true);
    expect(takesOver(null, "copied")).toBe(true);
  });
});
