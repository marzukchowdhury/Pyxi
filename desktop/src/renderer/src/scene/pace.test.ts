import { describe, expect, it } from "vitest";
import { keepDrawing } from "./pace";

describe("frames on demand", () => {
  it("draws while playing, while settling, and never at rest", () => {
    expect(keepDrawing(true, 10_000, 0)).toBe(true);
    expect(keepDrawing(false, 1_000, 3_000)).toBe(true);
    expect(keepDrawing(false, 3_001, 3_000)).toBe(false);
  });
});
