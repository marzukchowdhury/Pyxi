import { describe, expect, it } from "vitest";
import type { SketchShape } from "../shared/protocol";
import { drawShape, fitView, placeText, timeline } from "./sketchPaths";

const shape = (s: Partial<SketchShape>): SketchShape => ({
  kind: "line", points: [], at: null, size: null, text: null, color: "ink", fill: false,
  weight: "normal", dashed: false, flow: false, step: 0, ask: null, ...s,
});

describe("sketch paths", () => {
  it("draws the same wobble every time, and arrows get a head", () => {
    const arrow = shape({ kind: "arrow", points: [[38, 40], [38, 56]] });
    const a = drawShape(arrow, 5, 42)!;
    expect(drawShape(arrow, 5, 42)!.d).toBe(a.d);
    expect(a.head).toMatch(/^M .* L 190\.0 280\.0 L /); // the tip is the last point, in px
    expect(drawShape(shape({ kind: "polygon", points: [[1, 1], [2, 2]] }), 5, 1)).toBeNull();
  });

  it("keeps long labels inside the board", () => {
    const p = placeText(shape({ kind: "text", text: "A label that is far too long", at: [95, 70] }), 5)!;
    expect(p.x + p.width).toBeLessThanOrEqual(100 * 5);
    expect(p.y).toBeLessThanOrEqual(64 * 5);
  });

  it("staggers a step's strokes and ignores other steps", () => {
    const shapes = [
      shape({ kind: "line", points: [[0, 0], [50, 0]], step: 0 }),
      shape({ kind: "text", text: "Lift", at: [10, 10], step: 0 }),
      shape({ kind: "line", points: [[0, 5], [50, 5]], step: 1 }),
    ];
    const t = timeline(shapes, 0, 5);
    expect([...t.delays.keys()]).toEqual([0, 1]);
    expect(t.delays.get(1)!).toBeGreaterThan(t.delays.get(0)!);
    expect(t.total).toBeGreaterThan(t.delays.get(1)!);
  });
});

describe("fitting the view to the drawing", () => {
  it("zooms into a drawing that only uses one corner, but not past the limit", () => {
    const corner = [shape({ kind: "rect", at: [5, 5], size: [20, 12], step: 0 })];
    const [, , w] = fitView(corner, 5, 500, 320).split(" ").map(Number);
    expect(w).toBeCloseTo(500 / 1.4, 0); // capped at 1.4x
    const full = [shape({ kind: "line", points: [[0, 0], [100, 64]], step: 0 })];
    const [, , w2] = fitView(full, 5, 500, 320).split(" ").map(Number);
    expect(w2).toBeGreaterThanOrEqual(500); // already full: no zoom in
  });
});
