import { describe, expect, it } from "vitest";
import * as THREE from "three";
import { blobGeometry, limbGeometry, radiusAt } from "./organic";
import { buildGeometry } from "./parts";
import type { Part } from "../shared/protocol";

/** A skin with no holes: every edge is shared by exactly two faces, each going the other way. */
function closed(g: THREE.BufferGeometry): boolean {
  const index = g.getIndex()!.array;
  const seen = new Map<string, number>();
  for (let t = 0; t < index.length; t += 3) {
    for (let e = 0; e < 3; e++) {
      const a = index[t + e], b = index[t + ((e + 1) % 3)];
      if (a === b) return false;
      const key = `${a}>${b}`;
      if (seen.has(key)) return false; // the same edge twice the same way: a fold
      seen.set(key, 1);
    }
  }
  for (const key of seen.keys()) {
    const [a, b] = key.split(">");
    if (!seen.has(`${b}>${a}`)) return false;
  }
  return true;
}

const finite = (g: THREE.BufferGeometry) => Array.from(g.getAttribute("position").array).every(Number.isFinite) && Array.from(g.getAttribute("normal").array).every(Number.isFinite);
const box = (g: THREE.BufferGeometry) => { g.computeBoundingBox(); return g.boundingBox!; };

/** Share of vertices whose normal points away from `centre`. */
function outward(g: THREE.BufferGeometry, centre: [number, number, number]): number {
  const p = g.getAttribute("position"), n = g.getAttribute("normal");
  let out = 0;
  for (let i = 0; i < p.count; i++) {
    if ((p.getX(i) - centre[0]) * n.getX(i) + (p.getY(i) - centre[1]) * n.getY(i) + (p.getZ(i) - centre[2]) * n.getZ(i) > 0) out++;
  }
  return out / p.count;
}

describe("a limb: a body that swells, tapers and bends", () => {
  const leg = () => limbGeometry([[0, 1.2, 0], [0, 0, 0.15], [0, -1.3, 0]], [0.4, 0.25, 0.2]);

  it("is one closed skin with real numbers in it", () => {
    const g = leg();
    expect(finite(g)).toBe(true);
    expect(closed(g)).toBe(true);
    expect(g.getIndex()!.count / 3).toBeLessThan(8000);
  });

  it("is as thick as its radii say, with rounded ends past the spine", () => {
    const b = box(leg());
    expect(b.max.y).toBeCloseTo(1.2 + 0.4, 1); // the top end, rounded off
    expect(b.min.y).toBeCloseTo(-1.3 - 0.2, 1);
    expect(b.max.x).toBeCloseTo(0.4, 1); // widest at the top
    expect(b.max.x - b.min.x).toBeLessThan(0.85);
  });

  it("faces outwards", () => {
    expect(outward(limbGeometry([[0, -1, 0], [0, 1, 0]], [0.5]), [0, 0, 0])).toBeGreaterThan(0.98);
  });

  it("squash flattens it side to side and leaves its depth", () => {
    const b = box(limbGeometry([[0, 0, -2], [0, 0.2, 0], [0, 0, 2]], [0.2, 1, 0.3], 0.3));
    expect(b.max.x - b.min.x).toBeLessThan(0.7); // a fish seen from above is thin
    expect(b.max.y - b.min.y).toBeGreaterThan(1.8); // and tall from the side
  });

  it("copes with a spine of two points, a sharp bend, and one radius", () => {
    for (const g of [limbGeometry([[0, 0, 0], [1, 0, 0]], [0.2]), limbGeometry([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], [0.1, 0.3]), limbGeometry([[0, 0, 0], [0, 0, 0.001]], [0.5])]) {
      expect(finite(g)).toBe(true);
      expect(closed(g)).toBe(true);
    }
  });

  it("thickness between two radii never dips far below the thinner one", () => {
    for (let u = 0; u <= 1; u += 0.05) expect(radiusAt([1, 0.1, 1, 0.05], u)).toBeGreaterThanOrEqual(0.025);
    expect(radiusAt([0.3], 0.7)).toBe(0.3);
  });
});

describe("a blob: lumps melted into one skin", () => {
  it("one lump is that egg, closed and facing out", () => {
    const g = blobGeometry([[1, 2, 3, 1, 0.5, 0.8]], null, null);
    const b = box(g);
    expect(finite(g)).toBe(true);
    expect(closed(g)).toBe(true);
    expect(b.max.x).toBeCloseTo(2, 1);
    expect(b.min.y).toBeCloseTo(1.5, 1);
    expect(b.max.z - b.min.z).toBeCloseTo(1.6, 1);
    expect(outward(g, [1, 2, 3])).toBeGreaterThan(0.98);
  });

  it("two lumps that overlap become one piece, wider at the join than either alone", () => {
    const g = blobGeometry([[-0.8, 0, 0, 1, 1, 1], [0.8, 0, 0, 1, 1, 1]], null, null, 0.8);
    expect(closed(g)).toBe(true);
    // At x = 0 two hard balls meet at a waist of radius 0.6; melted, the waist is fuller.
    const p = g.getAttribute("position");
    let waist = 0;
    for (let i = 0; i < p.count; i++) if (Math.abs(p.getX(i)) < 0.06) waist = Math.max(waist, Math.hypot(p.getY(i), p.getZ(i)));
    expect(waist).toBeGreaterThan(0.66);
    expect(waist).toBeLessThan(1.05);
  });

  it("a spine is melted into the lumps: a neck out of a body", () => {
    const g = blobGeometry([[0, 0, 0, 1.2, 1, 1.4]], [[0, 0.6, 1], [0, 1.8, 2.2]], [0.5, 0.35], 0.5);
    const b = box(g);
    expect(closed(g)).toBe(true);
    expect(b.max.y).toBeGreaterThan(2.0);
    expect(b.max.z).toBeGreaterThan(2.4);
  });

  it("stays a sensible size to draw, for small and large and many lumps", () => {
    const many = Array.from({ length: 24 }, (_, i) => [Math.cos(i) * 3, Math.sin(i * 1.7) * 2, i * 0.3 - 3, 0.9, 0.7, 0.8]);
    for (const g of [blobGeometry(many, null, null), blobGeometry([[0, 0, 0, 0.02, 0.02, 0.02]], null, null), blobGeometry([[0, 0, 0, 25, 3, 3]], null, null)]) {
      expect(finite(g)).toBe(true);
      expect(closed(g)).toBe(true);
      expect(g.getIndex()!.count / 3).toBeLessThan(40000);
      expect(g.getIndex()!.count / 3).toBeGreaterThan(100);
    }
  });

  it("nothing to draw is an empty shape, not an error", () => {
    expect(blobGeometry([], null, null).getAttribute("position")).toBeUndefined();
  });
});

describe("parts drawn with them", () => {
  const part = (extra: Partial<Part>): Part => ({
    id: "p", shape: "box", size: [1, 1, 1], axis: "y", position: [0, 0, 0], rotation: [0, 0, 0], parent: null, material: "skin", color: null,
    opacity: 1, label: null, note: null, casing: false, hidden: false, taper: null, path: null, radius: null, profile: null, teeth: null, turns: null, ...extra,
  });

  it("a limb part and a blob part build at their true size", () => {
    const limb = buildGeometry(part({ shape: "limb", path: [[0, 1, 0], [0, -1, 0]], radii: [0.3, 0.2] }));
    const blob = buildGeometry(part({ shape: "blob", lumps: [[0, 0, 0, 1, 1, 1], [0, 1.2, 0, 0.7, 0.7, 0.7]] }));
    expect(limb.scale).toEqual([1, 1, 1]);
    expect(blob.scale).toEqual([1, 1, 1]);
    expect(box(blob.geometry).max.y).toBeCloseTo(1.9, 1);
    expect(closed(limb.geometry)).toBe(true);
  });

  it("a limb with no radii takes the tube's radius, and one with nothing is still drawn", () => {
    expect(box(buildGeometry(part({ shape: "limb", path: [[0, 0, 0], [2, 0, 0]], radius: 0.25 })).geometry).max.y).toBeCloseTo(0.25, 1);
    expect(finite(buildGeometry(part({ shape: "limb" })).geometry)).toBe(true);
    expect(finite(buildGeometry(part({ shape: "blob" })).geometry)).toBe(true);
  });
});
