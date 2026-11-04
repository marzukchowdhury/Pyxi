/**
 * Part geometry and materials for object scenes. Every shape fills its part's bounding box
 * (`size`), with its long axis along `axis`, so the model never has to stack rotations just to
 * lay a cylinder on its side.
 */
import * as THREE from "three";
import { mergeGeometries } from "three/examples/jsm/utils/BufferGeometryUtils.js";
import type { Axis, Material, Part } from "../shared/protocol";

export interface Built {
  geometry: THREE.BufferGeometry;
  /** Mesh scale: unit shapes are stretched to the box; shapes built at true size use [1,1,1]. */
  scale: [number, number, number];
}

const TAU = Math.PI * 2;

/** Rotate a geometry whose long axis is +y so it lies along `axis` instead. */
function alongAxis(g: THREE.BufferGeometry, axis: Axis): THREE.BufferGeometry {
  if (axis === "x") g.rotateZ(-Math.PI / 2);
  else if (axis === "z") g.rotateX(Math.PI / 2);
  return g;
}

/** Rotate a geometry whose long axis is +z (extrusions) so it lies along `axis`. */
function fromZ(g: THREE.BufferGeometry, axis: Axis): THREE.BufferGeometry {
  if (axis === "y") g.rotateX(Math.PI / 2);
  else if (axis === "x") g.rotateY(-Math.PI / 2);
  return g;
}

const idx = { x: 0, y: 1, z: 2 } as const;
const across = (size: number[], axis: Axis) => {
  const others = [0, 1, 2].filter((i) => i !== idx[axis]);
  return Math.min(size[others[0]], size[others[1]]);
};

function gearShape(teeth: number): THREE.Shape {
  const outer = 0.5;
  const root = 0.41;
  const s = new THREE.Shape();
  const n = teeth * 4;
  for (let i = 0; i <= n; i++) {
    const a = (i / n) * TAU;
    // Trapezoid teeth: root, rise, tip, fall.
    const r = i % 4 === 1 || i % 4 === 2 ? outer : root;
    const x = Math.cos(a) * r;
    const y = Math.sin(a) * r;
    if (i === 0) s.moveTo(x, y);
    else s.lineTo(x, y);
  }
  const hole = new THREE.Path();
  hole.absarc(0, 0, 0.11, 0, TAU, true);
  s.holes.push(hole);
  return s;
}

/** A 3D arrow for flows and forces: a tube along `path` (or the axis) with a cone at the end. */
export function arrowGeometry(p: Part): THREE.BufferGeometry {
  const len = Math.max(0.2, p.size[idx[p.axis]]);
  const run = p.path && p.path.length >= 2 ? p.path : [axisPoint([0, -len / 2, 0], p.axis), axisPoint([0, len / 2, 0], p.axis)];
  const pts = run.map((v) => new THREE.Vector3(...v));
  const r = p.radius ?? Math.max(0.05, Math.min(0.2, across(p.size, p.axis) / 4));
  const head = Math.max(r * 3.2, 0.12);
  const tip = pts[pts.length - 1];
  const dir = tip.clone().sub(pts[pts.length - 2]).normalize();
  // The shaft stops where the head begins.
  const shaftEnd = tip.clone().addScaledVector(dir, -head * 0.9);
  const shaftPts = [...pts.slice(0, -1), shaftEnd];
  const shaft = shaftPts.length > 2
    ? new THREE.TubeGeometry(new THREE.CatmullRomCurve3(shaftPts), 48, r, 12, false)
    : new THREE.TubeGeometry(new THREE.LineCurve3(shaftPts[0], shaftPts[1]), 4, r, 12, false);
  const cone = new THREE.ConeGeometry(r * 2.4, head, 24, 1);
  cone.translate(0, head / 2, 0);
  cone.applyQuaternion(new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 1, 0), dir));
  cone.translate(shaftEnd.x, shaftEnd.y, shaftEnd.z);
  return mergeGeometries([shaft.toNonIndexed(), cone.toNonIndexed()]) ?? shaft;
}

function axisPoint(v: number[], axis: Axis): number[] {
  // [0, d, 0] along y becomes the same distance along the chosen axis.
  const d = v[1];
  return axis === "x" ? [d, 0, 0] : axis === "z" ? [0, 0, d] : [0, d, 0];
}

export function buildGeometry(p: Part): Built {
  const [sx, sy, sz] = p.size;
  const unit: [number, number, number] = [sx, sy, sz];
  switch (p.shape) {
    case "sphere":
      return { geometry: new THREE.SphereGeometry(0.5, 48, 32), scale: unit };
    case "cylinder": {
      // taper = radius at the +axis end / radius at the -axis end.
      const t = p.taper ?? 1;
      const top = 0.5 * Math.min(1, t);
      const bottom = 0.5 * Math.min(1, t > 0 ? 1 / t : 1);
      return { geometry: alongAxis(new THREE.CylinderGeometry(top, bottom, 1, 48, 1), p.axis), scale: unit };
    }
    case "cone":
      return { geometry: alongAxis(new THREE.ConeGeometry(0.5, 1, 48, 1), p.axis), scale: unit };
    case "capsule": {
      // Built at true size so the round caps stay round.
      const len = p.size[idx[p.axis]];
      const r = across(p.size, p.axis) / 2;
      const g = alongAxis(new THREE.CapsuleGeometry(r, Math.max(0.001, len - 2 * r), 10, 32), p.axis);
      return { geometry: g, scale: [1, 1, 1] };
    }
    case "torus": {
      const thick = p.size[idx[p.axis]];
      const d = across(p.size, p.axis);
      const g = new THREE.TorusGeometry(Math.max(0.001, (d - thick) / 2), thick / 2, 20, 64);
      return { geometry: fromZ(g, p.axis), scale: [1, 1, 1] };
    }
    case "ring": {
      // A flat washer: a rectangle spun around the axis.
      const h = p.size[idx[p.axis]];
      const r = across(p.size, p.axis) / 2;
      const ri = r * 0.62;
      const pts = [new THREE.Vector2(ri, -h / 2), new THREE.Vector2(r, -h / 2), new THREE.Vector2(r, h / 2), new THREE.Vector2(ri, h / 2), new THREE.Vector2(ri, -h / 2)];
      return { geometry: alongAxis(new THREE.LatheGeometry(pts, 64), p.axis), scale: [1, 1, 1] };
    }
    case "wedge": {
      const s = new THREE.Shape([new THREE.Vector2(-0.5, -0.5), new THREE.Vector2(0.5, -0.5), new THREE.Vector2(-0.5, 0.5)]);
      const g = new THREE.ExtrudeGeometry(s, { depth: 1, bevelEnabled: false });
      g.translate(0, 0, -0.5);
      return { geometry: g, scale: unit };
    }
    case "gear": {
      const g = new THREE.ExtrudeGeometry(gearShape(p.teeth ?? 16), { depth: 1, bevelEnabled: false, curveSegments: 4 });
      g.translate(0, 0, -0.5);
      return { geometry: fromZ(g, p.axis), scale: unit };
    }
    case "spring": {
      const len = p.size[idx[p.axis]];
      const d = across(p.size, p.axis);
      const wire = Math.max(0.006, d * 0.06);
      const R = Math.max(0.001, d / 2 - wire);
      const turns = p.turns ?? 8;
      const pts: THREE.Vector3[] = [];
      const steps = Math.ceil(turns * 28);
      for (let i = 0; i <= steps; i++) {
        const u = i / steps;
        const a = u * turns * TAU;
        pts.push(new THREE.Vector3(Math.cos(a) * R, -len / 2 + u * len, Math.sin(a) * R));
      }
      const g = new THREE.TubeGeometry(new THREE.CatmullRomCurve3(pts), steps, wire, 10, false);
      return { geometry: alongAxis(g, p.axis), scale: [1, 1, 1] };
    }
    case "tube": {
      const pts = (p.path ?? [[0, -0.5, 0], [0, 0.5, 0]]).map((v) => new THREE.Vector3(...v));
      const curve = new THREE.CatmullRomCurve3(pts, false, "centripetal");
      const r = p.radius ?? Math.max(0.01, across(p.size, p.axis) / 2);
      return { geometry: new THREE.TubeGeometry(curve, Math.max(16, pts.length * 24), r, 14, false), scale: [1, 1, 1] };
    }
    case "extrude": {
      const prof = p.profile ?? [[-0.5, -0.5], [0.5, -0.5], [0.5, 0.5], [-0.5, 0.5]];
      const depth = p.size[idx[p.axis === "y" ? "y" : p.axis]];
      const shape = new THREE.Shape(prof.map(([x, y]) => new THREE.Vector2(x, y)));
      const bevel = Math.min(0.04, depth * 0.25);
      const g = new THREE.ExtrudeGeometry(shape, { depth: Math.max(0.001, depth - bevel * 2), bevelEnabled: true, bevelSize: bevel, bevelThickness: bevel, bevelSegments: 2 });
      g.translate(0, 0, -depth / 2 + bevel);
      return { geometry: fromZ(g, p.axis), scale: [1, 1, 1] };
    }
    case "arrow":
      return { geometry: arrowGeometry(p), scale: [1, 1, 1] };
    case "plane":
      return { geometry: new THREE.BoxGeometry(1, 1, 1), scale: [sx, Math.max(0.004, sy), sz] };
    case "rounded_box":
    case "box":
    default:
      return { geometry: new THREE.BoxGeometry(1, 1, 1), scale: unit };
  }
}

interface Preset {
  color: string;
  metalness: number;
  roughness: number;
  clearcoat?: number;
  sheen?: number;
  opacity?: number;
  emissive?: boolean;
}

export const MATERIALS: Record<Material, Preset> = {
  metal: { color: "#c9ced6", metalness: 1, roughness: 0.26 },
  brushed_metal: { color: "#b4b9c1", metalness: 0.9, roughness: 0.46 },
  dark_metal: { color: "#3d424b", metalness: 0.85, roughness: 0.38 },
  gold: { color: "#d8ae55", metalness: 1, roughness: 0.24 },
  plastic: { color: "#dde1e7", metalness: 0, roughness: 0.42, clearcoat: 0.3 },
  rubber: { color: "#24272c", metalness: 0, roughness: 0.92 },
  glass: { color: "#d3e8ff", metalness: 0.1, roughness: 0.05, clearcoat: 1, opacity: 0.45 },
  painted: { color: "#9aa5b2", metalness: 0.2, roughness: 0.34, clearcoat: 0.7 },
  fabric: { color: "#8b8f7a", metalness: 0, roughness: 1, sheen: 1 },
  wood: { color: "#a4743f", metalness: 0, roughness: 0.72 },
  ceramic: { color: "#f2f0ea", metalness: 0, roughness: 0.22, clearcoat: 0.5 },
  skin: { color: "#e2b08e", metalness: 0, roughness: 0.6, sheen: 0.4 },
  emissive: { color: "#ffb347", metalness: 0, roughness: 1, emissive: true },
};

export function makeMaterial(p: Part, style: string): THREE.MeshPhysicalMaterial {
  const preset = MATERIALS[p.material] ?? MATERIALS.plastic;
  const color = new THREE.Color(p.color ?? preset.color);
  const m = new THREE.MeshPhysicalMaterial({
    color,
    metalness: preset.metalness,
    roughness: preset.roughness,
    clearcoat: preset.clearcoat ?? 0,
    clearcoatRoughness: 0.2,
    sheen: preset.sheen ?? 0,
    sheenColor: color.clone().lerp(new THREE.Color("#ffffff"), 0.4),
    envMapIntensity: p.material === "glass" ? 2.2 : 1.1,
    specularIntensity: p.material === "glass" ? 1 : 0.5,
    side: p.shape === "plane" || p.shape === "extrude" ? THREE.DoubleSide : THREE.FrontSide,
  });
  if (preset.emissive) {
    // Glow without clipping to yellow-white: a hot colour stays that colour.
    m.color.multiplyScalar(0.35);
    m.emissive = color.clone();
    m.emissiveIntensity = 1.15;
  }
  if (style === "blueprint") {
    m.color.set("#1d3557");
    m.metalness = 0;
    m.roughness = 1;
    m.clearcoat = 0;
    m.sheen = 0;
  }
  // The part's own opacity (and its motions) multiply this: glass stays glassy when it fades.
  m.userData.factor = (preset.opacity ?? 1) * (style === "xray" ? 0.28 : style === "blueprint" ? 0.55 : 1);
  m.userData.baseEmissive = m.emissiveIntensity;
  return m;
}
