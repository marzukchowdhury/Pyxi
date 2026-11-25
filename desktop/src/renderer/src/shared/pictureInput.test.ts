import { describe, expect, it } from "vitest";
import { fitWithin, pictureIn, seeThrough } from "./pictureInput";

const file = (type: string, size = 1000, name = "x") => ({ type, size, name });
const item = (f: { type: string; size: number } | null, kind = "file") => ({ kind, type: f?.type ?? "text/plain", getAsFile: () => f });

describe("a picture put in the chat box", () => {
  it("finds a pasted picture among what was pasted", () => {
    const png = file("image/png");
    expect(pictureIn({ items: [item(null, "string"), item(png)] })).toBe(png);
  });

  it("finds a dropped image file, and leaves other files alone", () => {
    const jpg = file("image/jpeg", 5_000_000, "chair.jpg");
    expect(pictureIn({ files: [file("application/pdf"), jpg] })).toBe(jpg);
    expect(pictureIn({ files: [file("application/pdf"), file("text/plain")] })).toBeNull();
  });

  it("pasted words are not a picture, nor is an empty file or an SVG", () => {
    expect(pictureIn({ items: [item(null, "string")] })).toBeNull();
    expect(pictureIn({ files: [file("image/png", 0)] })).toBeNull();
    expect(pictureIn({ files: [file("image/svg+xml")] })).toBeNull();
    expect(pictureIn(null)).toBeNull();
  });

  it("a 20-megapixel photo is sent small, in the same shape; a small one is left as it is", () => {
    expect(fitWithin(5472, 3648)).toEqual([1600, 1067]);
    expect(fitWithin(3648, 5472)).toEqual([1067, 1600]);
    expect(fitWithin(640, 480)).toEqual([640, 480]);
    expect(fitWithin(40000, 20)[1]).toBe(1);
  });

  it("a cut-out keeps its see-through background; a solid picture with a few soft pixels does not", () => {
    const solid = new Uint8ClampedArray(100 * 100 * 4).fill(255);
    expect(seeThrough(solid)).toBe(false);
    const fewSoft = solid.slice();
    for (let i = 0; i < 5; i++) fewSoft[i * 4 + 3] = 0;
    expect(seeThrough(fewSoft)).toBe(false);
    const cutOut = solid.slice();
    for (let i = 0; i < 5000; i++) cutOut[i * 4 + 3] = 0;
    expect(seeThrough(cutOut)).toBe(true);
  });
});
