/**
 * The parts kit: ready-made, detailed pieces the 3D builder can place instead of plain shapes
 * (a wheel with a treaded tyre and a spoked rim, a RAM stick with chips and gold contacts, a
 * graphics card with fans...). Each piece fills its part's box, its long side along `axis`,
 * and is made of a few layers, each with its own material, so it reads as the real thing.
 */
import * as THREE from "three";
import { RoundedBoxGeometry } from "three/examples/jsm/geometries/RoundedBoxGeometry.js";
import { mergeGeometries } from "three/examples/jsm/utils/BufferGeometryUtils.js";
import type { Axis, Material } from "../shared/protocol";

export const KITS = [
  "wheel", "missile", "propeller", "jet_engine", "ram_stick", "gpu", "chip", "pc_fan", "screen",
  "seat", "person", "bolt", "house",
] as const;
export type Kit = (typeof KITS)[number];

export interface KitLayer {
  geometry: THREE.BufferGeometry;
  material: Material;
  /** Use the part's own colour (its main body); others keep the material's colour. */
  main?: boolean;
  color?: string;
}

const TAU = Math.PI * 2;

/** Merge pieces into one geometry (all non-indexed, so mixed sources combine). */
function merge(gs: THREE.BufferGeometry[]): THREE.BufferGeometry {
  const flat = gs.map((g) => (g.index ? g.toNonIndexed() : g));
  for (const g of flat) {
    if (!g.getAttribute("uv")) g.setAttribute("uv", new THREE.Float32BufferAttribute(new Float32Array(g.getAttribute("position").count * 2), 2));
  }
  return mergeGeometries(flat) ?? flat[0];
}

const box = (w: number, h: number, d: number, x = 0, y = 0, z = 0, r = 0) => {
  const g = r > 0 ? new RoundedBoxGeometry(w, h, d, 2, Math.min(r, w / 2, h / 2, d / 2)) : new THREE.BoxGeometry(w, h, d);
  return g.translate(x, y, z);
};
const cyl = (rt: number, rb: number, h: number, seg = 32) => new THREE.CylinderGeometry(rt, rb, h, seg);

/** Lay a geometry built with its long side along +y onto the part's axis. */
function onAxis(g: THREE.BufferGeometry, axis: Axis) {
  if (axis === "x") g.rotateZ(-Math.PI / 2);
  else if (axis === "z") g.rotateX(Math.PI / 2);
  return g;
}

/** Fit a layer set built in a unit-ish frame into the part's box [sx, sy, sz]. */
function fit(layers: KitLayer[], size: [number, number, number]): KitLayer[] {
  const all = new THREE.Box3();
  for (const l of layers) {
    l.geometry.computeBoundingBox();
    all.union(l.geometry.boundingBox!);
  }
  const s = all.getSize(new THREE.Vector3());
  const c = all.getCenter(new THREE.Vector3());
  const k = [size[0] / Math.max(1e-6, s.x), size[1] / Math.max(1e-6, s.y), size[2] / Math.max(1e-6, s.z)];
  for (const l of layers) l.geometry.translate(-c.x, -c.y, -c.z).scale(k[0], k[1], k[2]).computeVertexNormals();
  return layers;
}

function wheel(axis: Axis): KitLayer[] {
  // Built with the axle along y, then turned onto the axis.
  const tyre = new THREE.LatheGeometry(
    [[0.36, -0.17], [0.44, -0.18], [0.49, -0.13], [0.5, 0], [0.49, 0.13], [0.44, 0.18], [0.36, 0.17]].map(([r, y]) => new THREE.Vector2(r, y)),
    64,
  );
  const tread: THREE.BufferGeometry[] = [];
  for (let i = 0; i < 36; i++) {
    const a = (i / 36) * TAU;
    tread.push(box(0.035, 0.3, 0.05).rotateY(-a).translate(Math.cos(a) * 0.5, 0, Math.sin(a) * 0.5));
  }
  const rim = [cyl(0.37, 0.37, 0.24, 48).translate(0, 0, 0), cyl(0.09, 0.09, 0.3, 24)];
  const spokes: THREE.BufferGeometry[] = [];
  for (let i = 0; i < 5; i++) {
    const a = (i / 5) * TAU;
    spokes.push(box(0.29, 0.05, 0.07).translate(0.18, 0.13, 0).rotateY(a));
  }
  const nuts = Array.from({ length: 5 }, (_, i) => cyl(0.018, 0.018, 0.04, 6).translate(Math.cos((i / 5) * TAU + 0.6) * 0.055, 0.16, Math.sin((i / 5) * TAU + 0.6) * 0.055));
  return [
    { geometry: onAxis(merge([tyre, ...tread]), axis), material: "rubber" },
    { geometry: onAxis(merge([rim[0].scale(1, 0.3, 1).translate(0, 0.08, 0), rim[1], ...spokes]), axis), material: "brushed_metal", main: true },
    { geometry: onAxis(merge(nuts), axis), material: "metal" },
  ];
}

function missile(axis: Axis): KitLayer[] {
  const body = new THREE.LatheGeometry(
    [[0, -0.5], [0.05, -0.5], [0.06, -0.46], [0.06, 0.3], [0.05, 0.4], [0.03, 0.47], [0, 0.5]].map(([r, y]) => new THREE.Vector2(r, y)),
    32,
  );
  const fins: THREE.BufferGeometry[] = [];
  for (let i = 0; i < 4; i++) {
    const s = new THREE.Shape([new THREE.Vector2(0.06, -0.5), new THREE.Vector2(0.16, -0.5), new THREE.Vector2(0.06, -0.33)]);
    fins.push(new THREE.ExtrudeGeometry(s, { depth: 0.008, bevelEnabled: false }).translate(0, 0, -0.004).rotateY((i / 4) * TAU));
    const c = new THREE.Shape([new THREE.Vector2(0.06, 0.12), new THREE.Vector2(0.11, 0.15), new THREE.Vector2(0.11, 0.19), new THREE.Vector2(0.06, 0.24)]);
    fins.push(new THREE.ExtrudeGeometry(c, { depth: 0.006, bevelEnabled: false }).translate(0, 0, -0.003).rotateY((i / 4) * TAU + Math.PI / 4));
  }
  const seeker = new THREE.SphereGeometry(0.03, 16, 12).translate(0, 0.485, 0);
  const bands = [cyl(0.062, 0.062, 0.02).translate(0, 0.05, 0), cyl(0.062, 0.062, 0.02).translate(0, -0.25, 0)];
  return [
    { geometry: onAxis(body, axis), material: "painted", main: true, color: "#e9ecef" },
    { geometry: onAxis(merge(fins), axis), material: "painted", color: "#b8bec6" },
    { geometry: onAxis(merge([seeker, ...bands]), axis), material: "dark_metal" },
  ];
}

function propeller(axis: Axis, blades: number): KitLayer[] {
  const n = Math.max(2, Math.min(8, blades || 3));
  const bl: THREE.BufferGeometry[] = [];
  for (let i = 0; i < n; i++) {
    const s = new THREE.Shape();
    s.moveTo(0.06, -0.035);
    s.quadraticCurveTo(0.3, -0.07, 0.48, -0.025);
    s.quadraticCurveTo(0.5, 0.0, 0.47, 0.02);
    s.quadraticCurveTo(0.3, 0.06, 0.06, 0.035);
    const g = new THREE.ExtrudeGeometry(s, { depth: 0.012, bevelEnabled: true, bevelSize: 0.004, bevelThickness: 0.004, bevelSegments: 1 });
    g.rotateX(Math.PI / 2).rotateX(0.35); // a little pitch, like a real blade
    bl.push(g.rotateY((i / n) * TAU));
  }
  const hub = merge([cyl(0.07, 0.07, 0.08), new THREE.ConeGeometry(0.07, 0.12, 24).translate(0, 0.1, 0)]);
  return [
    { geometry: onAxis(merge(bl), axis), material: "painted", main: true, color: "#2b2f36" },
    { geometry: onAxis(hub, axis), material: "brushed_metal" },
  ];
}

function jetEngine(axis: Axis): KitLayer[] {
  const nacelle = new THREE.LatheGeometry(
    [[0.42, -0.5], [0.46, -0.45], [0.5, -0.1], [0.5, 0.3], [0.47, 0.45], [0.42, 0.5], [0.4, 0.48], [0.4, -0.48]].map(([r, y]) => new THREE.Vector2(r, y)),
    64,
  );
  const fan: THREE.BufferGeometry[] = [];
  for (let i = 0; i < 22; i++) fan.push(box(0.34, 0.02, 0.06).translate(0.21, 0, 0).rotateX(0.5).rotateY((i / 22) * TAU).translate(0, 0.4, 0));
  const spinner = new THREE.ConeGeometry(0.1, 0.18, 32).translate(0, 0.5, 0);
  const core = merge([cyl(0.22, 0.28, 0.5).translate(0, -0.4, 0), new THREE.ConeGeometry(0.16, 0.25, 32).rotateX(Math.PI).translate(0, -0.72, 0)]);
  return [
    { geometry: onAxis(nacelle, axis), material: "painted", main: true, color: "#d7dce2" },
    { geometry: onAxis(merge(fan), axis), material: "brushed_metal" },
    { geometry: onAxis(merge([spinner, core]), axis), material: "dark_metal" },
  ];
}

function ramStick(): KitLayer[] {
  const chips: THREE.BufferGeometry[] = [];
  for (let i = 0; i < 8; i++) chips.push(box(0.085, 0.13, 0.012, -0.4 + i * 0.114, 0.03, 0.01, 0.004));
  const pins: THREE.BufferGeometry[] = [];
  for (let i = 0; i < 60; i++) if (i !== 33) pins.push(box(0.009, 0.05, 0.003, -0.48 + i * 0.0163, -0.115, 0.004));
  const pcb = new THREE.Shape([new THREE.Vector2(-0.5, -0.14), new THREE.Vector2(0.06, -0.14), new THREE.Vector2(0.06, -0.12), new THREE.Vector2(0.08, -0.12), new THREE.Vector2(0.08, -0.14), new THREE.Vector2(0.5, -0.14), new THREE.Vector2(0.5, 0.14), new THREE.Vector2(-0.5, 0.14)]);
  const board = new THREE.ExtrudeGeometry(pcb, { depth: 0.008, bevelEnabled: false }).translate(0, 0, -0.004);
  return [
    { geometry: board, material: "plastic", main: true, color: "#1f6b3a" },
    { geometry: merge(chips), material: "plastic", color: "#16181c" },
    { geometry: merge(pins), material: "gold" },
  ];
}

function gpu(): KitLayer[] {
  const shroud = box(1, 0.2, 0.42, 0, 0, 0, 0.03);
  const fans: THREE.BufferGeometry[] = [];
  const rings: THREE.BufferGeometry[] = [];
  for (const x of [-0.3, 0.02]) {
    rings.push(new THREE.TorusGeometry(0.15, 0.012, 8, 48).rotateX(Math.PI / 2).translate(x, -0.105, 0));
    for (let i = 0; i < 9; i++) fans.push(box(0.12, 0.006, 0.04).translate(0.075, 0, 0).rotateZ(0.0).rotateY((i / 9) * TAU + 0.2).translate(x, -0.11, 0));
    fans.push(cyl(0.04, 0.04, 0.012).translate(x, -0.11, 0));
  }
  const pcb = box(0.96, 0.02, 0.4, 0.0, 0.11, 0.0);
  const fingers: THREE.BufferGeometry[] = [];
  for (let i = 0; i < 40; i++) fingers.push(box(0.008, 0.004, 0.035, -0.25 + i * 0.012, 0.11, -0.215));
  const bracket = box(0.02, 0.24, 0.5, -0.5, 0.0, 0.0);
  const heatpipes = [0, 1, 2].map((i) => cyl(0.012, 0.012, 0.9).rotateZ(Math.PI / 2).translate(0, 0.12 + 0.001 * i, -0.12 + i * 0.05));
  return [
    { geometry: shroud, material: "plastic", main: true, color: "#2a2d33" },
    { geometry: merge(fans), material: "plastic", color: "#111316" },
    { geometry: merge([...rings, ...heatpipes]), material: "brushed_metal", color: "#b0b6bf" },
    { geometry: pcb, material: "plastic", color: "#1d2f22" },
    { geometry: merge([...fingers]), material: "gold" },
    { geometry: bracket, material: "brushed_metal" },
  ];
}

function chip(): KitLayer[] {
  const substrate = box(1, 0.06, 1, 0, 0, 0, 0.01);
  const lid = box(0.62, 0.05, 0.62, 0, 0.055, 0, 0.02);
  const die = box(0.4, 0.01, 0.4, 0, 0.085, 0);
  const balls: THREE.BufferGeometry[] = [];
  for (let i = 0; i < 12; i++) for (let j = 0; j < 12; j++) balls.push(new THREE.SphereGeometry(0.018, 6, 4).translate(-0.44 + i * 0.08, -0.04, -0.44 + j * 0.08));
  const caps: THREE.BufferGeometry[] = [];
  for (let i = 0; i < 10; i++) caps.push(box(0.04, 0.02, 0.02, -0.42 + i * 0.09, 0.04, 0.44), box(0.04, 0.02, 0.02, -0.42 + i * 0.09, 0.04, -0.44));
  return [
    { geometry: substrate, material: "plastic", main: true, color: "#1e5a35" },
    { geometry: lid, material: "brushed_metal", color: "#c3c8cf" },
    { geometry: die, material: "dark_metal", color: "#2a2f3a" },
    { geometry: merge(balls), material: "metal", color: "#d9d9d9" },
    { geometry: merge(caps), material: "ceramic", color: "#8a6a4a" },
  ];
}

function pcFan(axis: Axis): KitLayer[] {
  const frame = new THREE.Shape([new THREE.Vector2(-0.5, -0.5), new THREE.Vector2(0.5, -0.5), new THREE.Vector2(0.5, 0.5), new THREE.Vector2(-0.5, 0.5)]);
  const hole = new THREE.Path();
  hole.absarc(0, 0, 0.47, 0, TAU, true);
  frame.holes.push(hole);
  const f = new THREE.ExtrudeGeometry(frame, { depth: 0.25, bevelEnabled: true, bevelSize: 0.02, bevelThickness: 0.02 }).translate(0, 0, -0.125).rotateX(-Math.PI / 2);
  const blades: THREE.BufferGeometry[] = [];
  for (let i = 0; i < 7; i++) blades.push(box(0.3, 0.012, 0.16).translate(0.27, 0, 0).rotateX(0.45).rotateY((i / 7) * TAU));
  const hub = cyl(0.16, 0.16, 0.18, 32);
  return [
    { geometry: onAxis(f, axis), material: "plastic", main: true, color: "#1f2227" },
    { geometry: onAxis(merge([...blades, hub]), axis), material: "plastic", color: "#2c3038" },
  ];
}

function screen(): KitLayer[] {
  return [
    { geometry: box(1, 0.62, 0.04, 0, 0, 0, 0.02), material: "dark_metal", main: true, color: "#1b1e24" },
    { geometry: box(0.95, 0.56, 0.005, 0, 0.01, 0.022), material: "emissive", color: "#3d7bd9" },
  ];
}

function seat(): KitLayer[] {
  return [
    { geometry: merge([box(0.8, 0.14, 0.8, 0, 0, 0.05, 0.06), box(0.8, 0.9, 0.14, 0, 0.5, -0.38, 0.06)]), material: "fabric", main: true, color: "#3c4148" },
    { geometry: box(0.5, 0.22, 0.12, 0, 1.06, -0.4, 0.05), material: "fabric", color: "#2c3036" },
    { geometry: merge([box(0.06, 0.5, 0.06, -0.3, -0.3, 0.05), box(0.06, 0.5, 0.06, 0.3, -0.3, 0.05), box(0.7, 0.04, 0.6, 0, -0.55, 0.05)]), material: "dark_metal" },
  ];
}

function person(): KitLayer[] {
  const torso = new THREE.CapsuleGeometry(0.16, 0.38, 8, 20).translate(0, 0.28, 0);
  const head = new THREE.SphereGeometry(0.12, 24, 18).translate(0, 0.72, 0);
  const limb = (x: number, y: number, len: number, ang = 0) => new THREE.CapsuleGeometry(0.055, len, 6, 12).rotateZ(ang).translate(x, y, 0);
  return [
    { geometry: merge([torso, limb(-0.23, 0.26, 0.42, 0.12), limb(0.23, 0.26, 0.42, -0.12)]), material: "fabric", main: true, color: "#4a5a6e" },
    { geometry: merge([limb(-0.09, -0.3, 0.55), limb(0.09, -0.3, 0.55)]), material: "fabric", color: "#2d3440" },
    { geometry: head, material: "skin" },
  ];
}

function bolt(axis: Axis): KitLayer[] {
  const head = cyl(0.22, 0.22, 0.14, 6).translate(0, 0.43, 0);
  const shank = cyl(0.1, 0.1, 0.86).translate(0, -0.07, 0);
  const pts: THREE.Vector3[] = [];
  for (let i = 0; i <= 400; i++) {
    const u = i / 400;
    const a = u * 14 * TAU;
    pts.push(new THREE.Vector3(Math.cos(a) * 0.1, -0.5 + u * 0.72, Math.sin(a) * 0.1));
  }
  const thread = new THREE.TubeGeometry(new THREE.CatmullRomCurve3(pts), 400, 0.018, 6, false);
  return [{ geometry: onAxis(merge([head, shank, thread]), axis), material: "metal", main: true }];
}

function house(): KitLayer[] {
  const walls = box(1, 0.55, 0.7, 0, -0.17, 0);
  const roofShape = new THREE.Shape([new THREE.Vector2(-0.56, 0), new THREE.Vector2(0.56, 0), new THREE.Vector2(0, 0.36)]);
  const roof = new THREE.ExtrudeGeometry(roofShape, { depth: 0.8, bevelEnabled: false }).translate(0, 0.1, -0.4);
  const chimney = box(0.1, 0.22, 0.1, 0.28, 0.3, 0.1);
  const door = box(0.16, 0.3, 0.02, 0, -0.29, 0.355);
  const windows = [box(0.18, 0.15, 0.02, -0.3, -0.12, 0.355), box(0.18, 0.15, 0.02, 0.3, -0.12, 0.355), box(0.02, 0.15, 0.18, 0.505, -0.12, 0)];
  return [
    { geometry: walls, material: "painted", main: true, color: "#e8e0d2" },
    { geometry: merge([roof, chimney]), material: "ceramic", color: "#8e4b3a" },
    { geometry: door, material: "wood", color: "#6b4a2f" },
    { geometry: merge(windows), material: "glass" },
  ];
}

/**
 * The axis a piece's own shape says it has, whatever the builder wrote: a wheel, fan or propeller
 * turns about its thinnest side, a missile, engine or bolt lies along its longest one. Otherwise a
 * wheel left on the default axis is squashed flat into its box and lies on its side.
 */
export function kitAxis(kit: string, size: [number, number, number], axis: Axis): Axis {
  const names: Axis[] = ["x", "y", "z"];
  const order = [0, 1, 2].sort((a, b) => size[a] - size[b]);
  const clear = (a: number, b: number) => size[a] < size[b] * 0.8;
  if (kit === "wheel" || kit === "pc_fan" || kit === "propeller") {
    return clear(order[0], order[1]) ? names[order[0]] : axis;
  }
  if (kit === "missile" || kit === "jet_engine" || kit === "bolt") {
    return clear(order[1], order[2]) ? names[order[2]] : axis;
  }
  return axis;
}

/** The kit piece, fitted to the part's box. */
export function buildKit(kit: string, size: [number, number, number], axis: Axis, teeth?: number | null): KitLayer[] | null {
  axis = kitAxis(kit, size, axis);
  const layers =
    kit === "wheel" ? wheel(axis)
    : kit === "missile" ? missile(axis)
    : kit === "propeller" ? propeller(axis, teeth ?? 3)
    : kit === "jet_engine" ? jetEngine(axis)
    : kit === "ram_stick" ? ramStick()
    : kit === "gpu" ? gpu()
    : kit === "chip" ? chip()
    : kit === "pc_fan" ? pcFan(axis)
    : kit === "screen" ? screen()
    : kit === "seat" ? seat()
    : kit === "person" ? person()
    : kit === "bolt" ? bolt(axis)
    : kit === "house" ? house()
    : null;
  return layers ? fit(layers, size) : null;
}
