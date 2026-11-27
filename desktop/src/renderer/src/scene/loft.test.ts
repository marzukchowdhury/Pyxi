import * as THREE from "three";
import { describe, expect, it } from "vitest";
import type { Part } from "../shared/protocol";
import { hollowProfile, loftGeometry, loftRings, sectionPoint } from "./loft";
import { buildGeometry } from "./parts";

const part = (over: Partial<Part>): Part => ({
  id: "p", shape: "box", size: [1, 1, 1], axis: "y", position: [0, 0, 0], rotation: [0, 0, 0], parent: null,
  material: "plastic", color: null, opacity: 1, label: null, note: null, casing: false, hidden: false,
  taper: null, path: null, radius: null, profile: null, teeth: null, turns: null, ...over,
});

function box(g: THREE.BufferGeometry): THREE.Box3 {
  g.computeBoundingBox();
  return g.boundingBox!;
}

/** Six times the volume inside a closed mesh: positive when its faces look outward. */
function signedVolume(g: THREE.BufferGeometry): number {
  const flat = g.index ? g.toNonIndexed() : g;
  const p = flat.getAttribute("position");
  const a = new THREE.Vector3(), b = new THREE.Vector3(), c = new THREE.Vector3();
  let v = 0;
  for (let i = 0; i < p.count; i += 3) {
    a.fromBufferAttribute(p, i); b.fromBufferAttribute(p, i + 1); c.fromBufferAttribute(p, i + 2);
    v += a.dot(b.cross(c));
  }
  return v;
}

// A car body from the side: low bonnet, tall cabin, boot.
const CAR = [[-4, 3.2, 1.2, 0, 0.4], [-2, 3.6, 1.5, 0.1, 0.4], [0, 3.6, 2.6, 0.6, 0.5], [2.4, 3.6, 2.4, 0.5, 0.5], [4, 3.3, 1.4, 0.1, 0.4]];

describe("a loft: a body drawn from its cross-sections", () => {
  it("passes through every written section and never doubles back", () => {
    const rings = loftRings(CAR);
    expect(rings).toHaveLength((CAR.length - 1) * 8 + 1);
    CAR.forEach((s, i) => s.forEach((v, j) => expect(rings[i * 8][j]).toBeCloseTo(v, 6)));
    for (let i = 1; i < rings.length; i++) expect(rings[i][0]).toBeGreaterThanOrEqual(rings[i - 1][0]);
  });

  it("a section is an oval at round 1 and nearly a rectangle at 0.2", () => {
    const at45 = (round: number) => sectionPoint(Math.PI / 4, 1, 1, round);
    expect(Math.hypot(...at45(1))).toBeCloseTo(1, 6);
    expect(at45(0.2)[0]).toBeGreaterThan(0.9);
    expect(sectionPoint(0, 2, 1, 0.3)).toEqual([2, 0]);
  });

  it.each(["x", "y", "z"] as const)("along %s it fills the box its sections give, faces outward", (axis) => {
    const g = loftGeometry(CAR, axis);
    const b = box(g);
    const size = b.getSize(new THREE.Vector3()).toArray();
    const along = "xyz".indexOf(axis);
    expect(size[along]).toBeCloseTo(8, 3);
    const across = axis === "x" ? 2 : 0, up = axis === "y" ? 2 : 1;
    expect(size[across]).toBeGreaterThan(3.55);
    expect(size[across]).toBeLessThan(3.75);
    // The cabin lifts the top well above the bonnet, and nothing drops below the floor line.
    expect(b.max.toArray()[up]).toBeGreaterThan(1.8);
    expect(b.min.toArray()[up]).toBeGreaterThan(-0.85);
    expect(signedVolume(g)).toBeGreaterThan(0);
    const n = g.getAttribute("normal");
    for (let i = 0; i < n.count; i += 97) expect(Math.hypot(n.getX(i), n.getY(i), n.getZ(i))).toBeCloseTo(1, 3);
  });

  it("is what a loft part is drawn as, and a loft with no sections is still a solid", () => {
    const g = buildGeometry(part({ shape: "loft", axis: "x", sections: CAR, size: [8, 3, 3.6] })).geometry;
    expect(box(g).getSize(new THREE.Vector3()).x).toBeCloseTo(8, 3);
    const bare = buildGeometry(part({ shape: "loft", axis: "x", size: [4, 2, 1] })).geometry;
    expect(box(bare).getSize(new THREE.Vector3()).x).toBeCloseTo(4, 3);
  });
});

describe("a hollow turned vessel", () => {
  it("goes up the outside, over the rim and down to a floor as thick as the wall", () => {
    const walls = hollowProfile([[1, 0], [1, 2]], 0.1);
    expect(walls[0]).toEqual([1, 0]);
    expect(walls[1]).toEqual([1, 2]);
    expect(walls[2][0]).toBeCloseTo(0.9, 6);
    expect(walls[2][1]).toBe(2);
    const last = walls[walls.length - 1];
    expect(last[0]).toBe(0);
    expect(last[1]).toBeCloseTo(0.1, 6);
  });

  it("a wall thicker than the vessel still leaves a hollow", () => {
    const walls = hollowProfile([[0.2, 0], [0.2, 1]], 5);
    expect(Math.min(...walls.slice(2, -1).map((q) => q[0]))).toBeGreaterThan(0);
    expect(walls[walls.length - 1][1]).toBeLessThan(0.5);
  });

  it("a mug with a wall is open at the top: there are faces looking in at the axis", () => {
    const mug = part({ shape: "lathe", profile: [[-1, 0.8], [-0.9, 0.85], [0, 0.85], [1, 0.85]], wall: 0.08, size: [1.7, 2, 1.7] });
    const hollow = buildGeometry(mug).geometry;
    const solid = buildGeometry({ ...mug, wall: null }).geometry;
    const inward = (g: THREE.BufferGeometry) => {
      const p = g.getAttribute("position"), n = g.getAttribute("normal");
      let count = 0;
      // Half way up, near the wall: a normal pointing at the axis is the inside of the cup.
      for (let i = 0; i < p.count; i++) {
        const r = Math.hypot(p.getX(i), p.getZ(i));
        if (Math.abs(p.getY(i)) < 0.5 && r > 0.5 && (p.getX(i) * n.getX(i) + p.getZ(i) * n.getZ(i)) / r < -0.5) count++;
      }
      return count;
    };
    expect(inward(hollow)).toBeGreaterThan(20);
    expect(inward(solid)).toBe(0);
    // As wide and as tall as its outline says.
    expect(box(hollow).getSize(new THREE.Vector3()).x).toBeCloseTo(1.7, 1);
    expect(box(hollow).getSize(new THREE.Vector3()).y).toBeCloseTo(2, 1);
  });
});

describe("an outline with soft edges", () => {
  const outline: [number, number][] = [[-2, -0.5], [2, -0.5], [2, 0.5], [-2, 0.5]];
  it("keeps its written size, and its edge is rounded over", () => {
    const sharp = buildGeometry(part({ shape: "extrude", axis: "z", profile: outline, size: [4, 1, 1.6] })).geometry;
    const soft = buildGeometry(part({ shape: "extrude", axis: "z", profile: outline, size: [4, 1, 1.6], bevel: 0.4 })).geometry;
    const a = box(sharp).getSize(new THREE.Vector3()), b = box(soft).getSize(new THREE.Vector3());
    expect(b.z).toBeCloseTo(1.6, 2);
    expect(b.x).toBeCloseTo(4, 2);
    expect(a.z).toBeCloseTo(1.6, 2);
    // At the front face a soft outline has drawn in from its full width; a sharp one has not.
    const widthAtFace = (g: THREE.BufferGeometry) => {
      const p = g.getAttribute("position");
      let w = 0;
      for (let i = 0; i < p.count; i++) if (p.getZ(i) > 0.79) w = Math.max(w, Math.abs(p.getX(i)));
      return w;
    };
    expect(widthAtFace(soft)).toBeLessThan(1.8);
    expect(widthAtFace(sharp)).toBeGreaterThan(1.95);
  });
});
