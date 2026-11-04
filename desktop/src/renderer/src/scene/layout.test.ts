import { describe, expect, it } from "vitest";
import type { SceneSpec } from "../shared/protocol";
import { computeLayout, exploded, flow, focusBounds, tree } from "./layout";

const node = (id: string, extra: Partial<SceneSpec["nodes"][number]> = {}) => ({ id, label: id, shape: "box" as const, size: 1, glow: false, ...extra });
const edge = (from: string, to: string) => ({ from, to, kind: "flow" as const, bidirectional: false });

describe("flow layout", () => {
  it("puts each edge one column to the right", () => {
    const placed = new Map(flow([node("a"), node("b"), node("c")], [edge("a", "b"), edge("b", "c")]).map((p) => [p.id, p.pos]));
    expect(placed.get("a")![0]).toBeLessThan(placed.get("b")![0]);
    expect(placed.get("b")![0]).toBeLessThan(placed.get("c")![0]);
  });

  it("survives cycles", () => {
    expect(flow([node("a"), node("b")], [edge("a", "b"), edge("b", "a")])).toHaveLength(2);
  });
});

describe("tree layout", () => {
  it("centres parents over their children and puts depth downwards", () => {
    const p = new Map(tree([node("root"), node("l", { parent: "root" }), node("r", { parent: "root" })]).map((x) => [x.id, x.pos]));
    expect(p.get("root")![0]).toBeCloseTo((p.get("l")![0] + p.get("r")![0]) / 2);
    expect(p.get("l")![1]).toBeLessThan(p.get("root")![1]);
  });
});

describe("exploded layout", () => {
  it("starts every piece exactly on its screen position, then lifts by depth", () => {
    const vp = { width: 1600, height: 900, scale: 1, origin: { x: 0, y: 0 } };
    const [whole, child] = exploded(
      [node("w", { screen_rect: [0, 0, 1600, 900], depth: 0 }), node("c", { screen_rect: [700, 400, 200, 100], depth: 2 })],
      vp,
    );
    expect(whole.start![0]).toBeCloseTo(0);
    expect(whole.start![1]).toBeCloseTo(0);
    expect(child.start![0]).toBeCloseTo(0); // centred element lands at the origin
    expect(child.size).toEqual([2, 1]); // 200 x 100 CSS px = 2 x 1 units
    expect(child.pos[2]).toBeGreaterThan(whole.pos[2]);
  });

  it("respects the display scale factor", () => {
    const [p] = exploded([node("a", { screen_rect: [0, 0, 400, 200] })], { width: 800, height: 450, scale: 2, origin: { x: 0, y: 0 } });
    expect(p.size).toEqual([2, 1]);
  });
});

describe("computeLayout", () => {
  it("recentres non-exploded scenes and computes focus bounds", () => {
    const scene: SceneSpec = {
      id: "s", title: "t", layout: "flow", mode: "card", theme: "aurora", auto_play: true,
      nodes: [node("a"), node("b")], edges: [edge("a", "b")], steps: [],
    };
    const layout = computeLayout(scene);
    expect(Math.abs(layout.center[0])).toBeLessThan(1e-6);
    expect(focusBounds(layout, ["b"]).center[0]).toBeGreaterThan(0);
  });
});
