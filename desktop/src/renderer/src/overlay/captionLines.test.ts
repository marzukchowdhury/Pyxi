import { describe, expect, it } from "vitest";
import { type CaptionLine, LINGER_MS, STALE_MS, elapsed, fresh, shown, withCaption } from "./captionLines";

const ev = (id: number, text: string, final = false, translation: string | null = null, pending = false) => ({ id, text, final, translation, pending });

describe("caption lines", () => {
  it("updates the sentence being said in place", () => {
    let lines: CaptionLine[] = [];
    lines = withCaption(lines, ev(1, "We decided"), 0);
    lines = withCaption(lines, ev(1, "We decided to move the launch"), 500);
    lines = withCaption(lines, ev(1, "We decided to move the launch.", true), 900);
    expect(lines).toHaveLength(1);
    expect(lines[0]).toMatchObject({ text: "We decided to move the launch.", final: true, at: 900 });
  });

  it("shows the sentence before with the one being said, and never more than two", () => {
    let lines: CaptionLine[] = [];
    for (const [i, t] of ["One.", "Two.", "Three.", "Four."].entries()) lines = withCaption(lines, ev(i + 1, t, true), i * 100);
    expect(lines.map((l) => l.id)).toEqual([2, 3, 4]);
    expect(shown(lines, false, false).main).toBe("Three. Four.");
  });

  it("shows the original at once and the translation when it comes", () => {
    let lines = withCaption([], ev(1, "Hola a todos"), 0);
    expect(shown(lines, true, true)).toEqual({ main: "Hola a todos", original: "", waiting: false });
    lines = withCaption(lines, ev(1, "Hola a todos.", true, null, true), 400);
    expect(shown(lines, true, true)).toEqual({ main: "Hola a todos.", original: "", waiting: true });
    lines = withCaption(lines, ev(1, "Hola a todos.", true, "Hello everyone.", false), 900);
    expect(shown(lines, true, true)).toEqual({ main: "Hello everyone.", original: "Hola a todos.", waiting: false });
    expect(shown(lines, true, false).original).toBe("");
    // The language was switched off: the words as said again.
    expect(shown(lines, false, false).main).toBe("Hola a todos.");
  });

  it("keeps a translation when the same words arrive again without one", () => {
    let lines = withCaption([], ev(1, "Bonjour.", true, "Hello.", false), 0);
    lines = withCaption(lines, ev(1, "Bonjour.", true, null, false), 100);
    expect(lines[0].translation).toBe("Hello.");
  });

  it("drops a line that turned out to be no speech, and ignores an empty new one", () => {
    let lines = withCaption([], ev(1, "mm"), 0);
    lines = withCaption(lines, ev(1, "", true), 300);
    expect(lines).toEqual([]);
    expect(withCaption([], ev(2, "", true), 0)).toEqual([]);
  });

  it("lets lines go a few seconds after the talking stops", () => {
    const lines = withCaption(withCaption([], ev(1, "Done.", true), 0), ev(2, "Still talk"), 1000);
    expect(fresh(lines, LINGER_MS - 1).map((l) => l.id)).toEqual([1, 2]);
    expect(fresh(lines, LINGER_MS + 1).map((l) => l.id)).toEqual([2]);
    expect(fresh(lines, 1000 + STALE_MS + 1)).toEqual([]); // the sound was cut off mid-sentence
  });

  it("counts the time the way a clock reads", () => {
    expect(elapsed(1000, 1000_000)).toBe("0:00");
    expect(elapsed(1000, (1000 + 725) * 1000)).toBe("12:05");
    expect(elapsed(1000, (1000 + 3725) * 1000)).toBe("1:02:05");
    expect(elapsed(1000, 0)).toBe("0:00");
  });
});
