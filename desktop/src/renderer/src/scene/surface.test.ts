import { describe, expect, it } from "vitest";
import * as THREE from "three";
import { flatView, isFlat, THREE_QUARTER } from "./camera";
import { buildKit } from "./kit";
import { buildGeometry, makeMaterial, MATERIALS, surfaceOf } from "./parts";
import { type Look, SURFACES, surfaceLayout, surfaceTile, surfaceUVs } from "./surface";
import type { Part } from "../shared/protocol";

const BOARD: Look = { color: [0.12, 0.35, 0.21], rough: 0.52, metal: 0 };
const STEEL: Look = { color: [0.7, 0.72, 0.75], rough: 0.4, metal: 0.92 };

const part = (over: Partial<Part>): Part => ({
  id: "p", shape: "box", size: [4, 0.4, 2], axis: "y", position: [0, 0, 0], rotation: [0, 0, 0], parent: null,
  material: "plastic", color: null, opacity: 1, label: null, note: null, casing: false, hidden: false,
  taper: null, path: null, radius: null, profile: null, teeth: null, turns: null, ...over,
});

const at = (data: Uint8Array, px: number, u: number, v: number) => {
  const i = (Math.floor(v * px) * px + Math.floor(u * px)) * 4;
  return [data[i], data[i + 1], data[i + 2], data[i + 3]];
};

describe("a surface tile: colour, bumps and shine as plain numbers", () => {
  it.each(SURFACES)("%s is a whole, sensible tile", (kind) => {
    const t = surfaceTile(kind, BOARD);
    expect([128, 256]).toContain(t.px);
    for (const data of [t.color, t.normal, t.shine]) expect(data.length).toBe(t.px * t.px * 4);
    let detail = 0, up = 0;
    const first = at(t.color, t.px, 0, 0);
    for (let i = 0; i < t.px * t.px; i++) {
      const n = [t.normal[i * 4] / 255 * 2 - 1, t.normal[i * 4 + 1] / 255 * 2 - 1, t.normal[i * 4 + 2] / 255 * 2 - 1];
      // Every bump is a real direction: a unit vector (to a byte's rounding), never facing down
      // or lying flat in the surface.
      expect(Math.hypot(n[0], n[1], n[2])).toBeGreaterThan(0.97);
      expect(Math.hypot(n[0], n[1], n[2])).toBeLessThan(1.03);
      expect(n[2]).toBeGreaterThan(0.2);
      if (n[2] > 0.995) up++;
      expect(t.color[i * 4 + 3]).toBe(255);
      if (Math.abs(t.color[i * 4] - first[0]) + Math.abs(t.color[i * 4 + 1] - first[1]) + Math.abs(t.color[i * 4 + 2] - first[2]) > 6) detail++;
    }
    // There is something drawn, and something turns the light: not a flat colour.
    expect(detail).toBeGreaterThan(t.px * t.px * 0.02);
    expect(up).toBeLessThan(t.px * t.px);
  });

  it.each(SURFACES)("%s repeats without a seam", (kind) => {
    const t = surfaceTile(kind, STEEL);
    const n = t.px;
    // Stepping over the tile's edge is no bigger a jump than the biggest step inside it.
    const jump = (data: Uint8Array, a: number, b: number) => Math.abs(data[a * 4] - data[b * 4]) + Math.abs(data[a * 4 + 1] - data[b * 4 + 1]) + Math.abs(data[a * 4 + 2] - data[b * 4 + 2]);
    let inside = 0, wrapX = 0, wrapY = 0;
    for (let y = 0; y < n; y++) {
      for (let x = 0; x + 1 < n; x++) inside = Math.max(inside, jump(t.color, y * n + x, y * n + x + 1));
      wrapX = Math.max(wrapX, jump(t.color, y * n + n - 1, y * n));
    }
    for (let x = 0; x < n; x++) {
      for (let y = 0; y + 1 < n; y++) inside = Math.max(inside, jump(t.color, y * n + x, (y + 1) * n + x));
      wrapY = Math.max(wrapY, jump(t.color, (n - 1) * n + x, x));
    }
    expect(wrapX).toBeLessThanOrEqual(inside);
    expect(wrapY).toBeLessThanOrEqual(inside);
  });

  it("pins are gold on a board, and the board shows between them", () => {
    const t = surfaceTile("pins", BOARD);
    const [r, g, b] = at(t.color, t.px, 0.5, 0.5);
    expect(r).toBeGreaterThan(190);
    expect(r).toBeGreaterThan(g);
    expect(g).toBeGreaterThan(b); // gold
    expect(at(t.shine, t.px, 0.5, 0.5)[2]).toBe(255); // the pin is metal
    const corner = at(t.color, t.px, 0.02, 0.02);
    expect(corner[1]).toBeGreaterThan(corner[0]); // green board
    expect(Math.abs(corner[1] - Math.round(BOARD.color[1] * 255))).toBeLessThan(6);
    expect(at(t.shine, t.px, 0.02, 0.02)[2]).toBe(0); // and the board is not
    // On bare metal the pins are that metal, in a dark bed.
    const m = surfaceTile("pins", STEEL);
    expect(at(m.color, m.px, 0.5, 0.5)[0]).toBeGreaterThan(170);
    expect(at(m.color, m.px, 0.02, 0.02)[0]).toBeLessThan(40);
  });

  it("fins have dark grooves, and a vent slot and a grille hole are dark and dull", () => {
    const fins = surfaceTile("fins", STEEL);
    expect(at(fins.color, fins.px, 0.3, 0.5)[0]).toBeGreaterThan(at(fins.color, fins.px, 0.8, 0.5)[0] * 3);
    for (const [kind, u, v] of [["vents", 0.5, 0.5], ["grille", 0.5, 0.25]] as const) {
      const t = surfaceTile(kind, STEEL);
      expect(at(t.color, t.px, u, v)[0]).toBeLessThan(30);
      expect(at(t.shine, t.px, u, v)[1]).toBeGreaterThan(200); // rough
      expect(at(t.shine, t.px, u, v)[2]).toBeLessThan(20); // not metal
    }
  });

  it("a die's blocks are different colours, with lanes between them", () => {
    const t = surfaceTile("cells", { color: [0.17, 0.19, 0.24], rough: 0.16, metal: 0.8 });
    const blocks = [[0.27, 0.27], [0.77, 0.27], [0.27, 0.77], [0.77, 0.77]].map(([u, v]) => at(t.color, t.px, u, v).slice(0, 3).join());
    expect(new Set(blocks).size).toBe(4);
    const lane = at(t.color, t.px, 0.5, 0.27), block = at(t.color, t.px, 0.27, 0.27);
    expect(lane[0] + lane[1] + lane[2]).toBeLessThan(block[0] + block[1] + block[2]);
  });
});

describe("laying a surface on a shape", () => {
  const shapes: Partial<Part>[] = [
    { shape: "box" },
    { shape: "rounded_box", size: [3, 4, 2] },
    { shape: "cylinder", size: [2, 3, 2] },
    { shape: "sphere", size: [2, 2, 2] },
    { shape: "cone", size: [2, 3, 2] },
    { shape: "lathe", axis: "x", size: [4, 1.6, 1.6], profile: [[-2, 0.5], [-1, 0.8], [1, 0.8], [2, 0.4]] },
    { shape: "extrude", size: [4, 0.2, 2], profile: [[-2, -1], [2, -1], [2, 1], [0, 1.4], [-2, 1]] },
    { shape: "torus", size: [3, 3, 0.6], axis: "z" },
    { shape: "tube", path: [[0, 0, 0], [1, 1, 0], [2, 1, 1]], radius: 0.2 },
    { shape: "gear", teeth: 12, size: [2, 0.3, 2] },
    { shape: "plane", size: [6, 0.02, 6] },
  ];

  it("every shape gets finite detail coordinates of a sensible size", () => {
    for (const s of shapes) {
      for (const kind of SURFACES) {
        const p = part({ ...s, surface: kind });
        const built = buildGeometry(p);
        const uv = built.geometry.getAttribute("uv"), pos = built.geometry.getAttribute("position");
        expect(built.geometry.getIndex(), `${s.shape} ${kind}`).toBeNull(); // faces no longer share corners
        expect(uv.count).toBe(pos.count);
        expect(pos.count % 3).toBe(0);
        expect(Array.from(uv.array).every(Number.isFinite), `${s.shape} ${kind}`).toBe(true);
        expect(Array.from(pos.array).every(Number.isFinite)).toBe(true);
        expect(Array.from(built.geometry.getAttribute("normal").array).every(Number.isFinite)).toBe(true);
        // The detail repeats over the part some sensible number of times: never one smeared
        // tile, never thousands of specks.
        let lo = Infinity, hi = -Infinity;
        for (let i = 0; i < uv.count; i++) { lo = Math.min(lo, uv.getX(i)); hi = Math.max(hi, uv.getX(i)); }
        const { across } = surfaceLayout(kind);
        expect(hi - lo, `${s.shape} ${kind}`).toBeGreaterThan(across * 0.2);
        expect(hi - lo, `${s.shape} ${kind}`).toBeLessThan(across * 1.6);
      }
    }
  });

  it("the pattern runs along the part's long side, the same size on every face", () => {
    // A 4 x 0.4 x 2 box of pins: 14 across the long side, so 7 across the short one.
    const g = surfaceUVs(new THREE.BoxGeometry(4, 0.4, 2), [1, 1, 1], [4, 0.4, 2], "pins");
    const uv = g.getAttribute("uv"), pos = g.getAttribute("position");
    const spanOn = (face: (i: number) => boolean, get: (i: number) => number) => {
      let lo = Infinity, hi = -Infinity;
      for (let i = 0; i < pos.count; i++) if (face(i)) { lo = Math.min(lo, get(i)); hi = Math.max(hi, get(i)); }
      return hi - lo;
    };
    const top = (i: number) => Math.abs(pos.getY(i) - 0.2) < 1e-6 && Math.abs(pos.getY(i - (i % 3)) - 0.2) < 1e-6 && Math.abs(pos.getY(i - (i % 3) + 1) - 0.2) < 1e-6 && Math.abs(pos.getY(i - (i % 3) + 2) - 0.2) < 1e-6;
    expect(spanOn(top, (i) => uv.getX(i))).toBeCloseTo(14, 5);
    expect(spanOn(top, (i) => uv.getY(i))).toBeCloseTo(7, 5);
    // Twice as fine at density 2; a stretched unit shape is measured at its real size.
    const fine = surfaceUVs(new THREE.BoxGeometry(4, 0.4, 2), [1, 1, 1], [4, 0.4, 2], "pins", 2);
    let hi = -Infinity, lo = Infinity;
    for (let i = 0; i < fine.getAttribute("uv").count; i++) { hi = Math.max(hi, fine.getAttribute("uv").getX(i)); lo = Math.min(lo, fine.getAttribute("uv").getX(i)); }
    expect(hi - lo).toBeCloseTo(28, 5);
    const unit = surfaceUVs(new THREE.BoxGeometry(1, 1, 1), [4, 0.4, 2], [4, 0.4, 2], "pins");
    hi = -Infinity; lo = Infinity;
    for (let i = 0; i < unit.getAttribute("uv").count; i++) { hi = Math.max(hi, unit.getAttribute("uv").getX(i)); lo = Math.min(lo, unit.getAttribute("uv").getX(i)); }
    expect(hi - lo).toBeCloseTo(14, 5);
  });

  it("only a part that asks for it, and can carry it, gets detail", () => {
    expect(surfaceOf(part({ surface: "fins" }))).toBe("fins");
    expect(surfaceOf(part({}))).toBeNull();
    expect(surfaceOf(part({ surface: "sparkles" }))).toBeNull(); // not a surface Pyxi knows
    expect(surfaceOf(part({ surface: "fins", shape: "arrow" }))).toBeNull();
    expect(surfaceOf(part({ surface: "fins", material: "emissive" }))).toBeNull();
    const plain = makeMaterial(part({ material: "pcb" }), "studio");
    expect(plain.map).toBeNull();
    expect(plain.normalMap).toBeNull();
    const m = makeMaterial(part({ material: "pcb", surface: "traces" }), "studio");
    expect(m.map).not.toBeNull();
    expect(m.normalMap).not.toBeNull();
    expect(m.roughnessMap).toBe(m.metalnessMap);
    expect(m.map!.wrapS).toBe(THREE.RepeatWrapping);
    expect(m.map!.colorSpace).toBe(THREE.SRGBColorSpace);
    // Two parts alike share one set of tiles; a different colour has its own.
    expect(makeMaterial(part({ material: "pcb", surface: "traces" }), "studio").map).toBe(m.map);
    expect(makeMaterial(part({ material: "pcb", color: "#16181c", surface: "traces" }), "studio").map).not.toBe(m.map);
    // A blueprint is lines only.
    expect(makeMaterial(part({ material: "pcb", surface: "traces" }), "blueprint").map).toBeNull();
  });

  it("the kit's chip, RAM and graphics card carry real materials and detail", () => {
    const chip = buildKit("chip", [3, 0.4, 3], "y")!;
    expect(chip.map((l) => [l.material, l.surface])).toEqual(expect.arrayContaining([["pcb", "traces"], ["silicon", "cells"], ["solder", undefined]]));
    for (const kit of ["chip", "ram_stick", "gpu"]) {
      for (const l of buildKit(kit, [4, 1, 2], "y")!.filter((l) => l.surface)) {
        const uv = l.geometry.getAttribute("uv");
        expect(Array.from(uv.array).every(Number.isFinite)).toBe(true);
        expect(Array.from(uv.array).some((v) => Math.abs(v) > 1)).toBe(true); // really laid on, and repeating
      }
    }
  });
});

describe("real materials for electronics", () => {
  it("each is what it says", () => {
    for (const name of ["silicon", "pcb", "solder", "copper", "anodized"] as const) {
      const m = MATERIALS[name];
      expect(m.color).toMatch(/^#[0-9a-f]{6}$/);
      expect(m.metalness).toBeGreaterThanOrEqual(0);
      expect(m.metalness).toBeLessThanOrEqual(1);
      expect(m.roughness).toBeGreaterThan(0.05);
      expect(m.roughness).toBeLessThanOrEqual(1);
    }
    const lum = (hex: string) => new THREE.Color(hex).getHSL({ h: 0, s: 0, l: 0 }).l;
    expect(lum(MATERIALS.silicon.color)).toBeLessThan(0.3); // a die is dark
    expect(lum(MATERIALS.anodized.color)).toBeLessThan(0.3);
    expect(MATERIALS.pcb.metalness).toBe(0); // a board is lacquer, not metal
    expect(MATERIALS.copper.metalness).toBe(1);
    expect(MATERIALS.solder.metalness).toBe(1);
    const pcb = new THREE.Color(MATERIALS.pcb.color), copper = new THREE.Color(MATERIALS.copper.color);
    expect(pcb.g).toBeGreaterThan(pcb.r); // green
    expect(copper.r).toBeGreaterThan(copper.b); // warm
  });
});

describe("how a flat thing is first seen", () => {
  it("a chip or a board is flat; an engine or a fridge is not", () => {
    expect(isFlat([9, 0.9, 9])).toBe(true);
    expect(isFlat([10, 1.5, 6])).toBe(true);
    expect(isFlat([10, 3, 4])).toBe(false);
    expect(isFlat([3, 6, 2.6])).toBe(false);
    expect(isFlat([0, 0, 0])).toBe(false);
  });

  it("never opens from straight above, and its three-quarter views are lifted", () => {
    const chip: [number, number, number] = [9, 0.9, 9];
    expect(flatView("top", chip, true)).toEqual(THREE_QUARTER);
    expect(flatView("iso", chip, true)).toEqual(THREE_QUARTER);
    expect(flatView("close", chip, false)).toEqual(THREE_QUARTER);
    expect(flatView("top", chip, false)).toBeNull(); // a later beat may look straight down
    expect(flatView("below", chip, true)).toBeNull();
    expect(flatView("side", chip, true)).toBeNull();
    for (const view of ["iso", "top", "front", "side"]) expect(flatView(view, [10, 3, 4], true)).toBeNull();
    // Three quarters: well above (30 to 50 degrees up), in front and to one side.
    const [x, y, z] = THREE_QUARTER;
    const up = (Math.atan2(y, Math.hypot(x, z)) * 180) / Math.PI;
    expect(up).toBeGreaterThan(30);
    expect(up).toBeLessThan(50);
    expect(x).toBeGreaterThan(0.3);
    expect(z).toBeGreaterThan(0.3);
  });
});
