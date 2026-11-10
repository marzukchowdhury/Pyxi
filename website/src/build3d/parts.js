/**
 * Part geometry and materials for the live 3D stage: the same shapes and the same material
 * presets as Pyxi's own renderer (desktop/src/renderer/src/scene/parts.ts and kit.ts), so a model
 * looks here the way it looks on a desktop. Every shape fills its part's box (`size`), with its
 * long axis along `axis`.
 */
import * as THREE from "three";
import { RoundedBoxGeometry } from "three/examples/jsm/geometries/RoundedBoxGeometry.js";
import { mergeGeometries } from "three/examples/jsm/utils/BufferGeometryUtils.js";

const TAU = Math.PI * 2;
const IDX = { x: 0, y: 1, z: 2 };

/** Rotate a geometry whose long axis is +y so it lies along `axis`. */
function alongAxis(g, axis) {
  if (axis === "x") g.rotateZ(-Math.PI / 2);
  else if (axis === "z") g.rotateX(Math.PI / 2);
  return g;
}
/** Rotate a geometry whose long axis is +z (extrusions) so it lies along `axis`. */
function fromZ(g, axis) {
  if (axis === "y") g.rotateX(Math.PI / 2);
  else if (axis === "x") g.rotateY(-Math.PI / 2);
  return g;
}
const across = (size, axis) => {
  const others = [0, 1, 2].filter((i) => i !== IDX[axis]);
  return Math.min(size[others[0]], size[others[1]]);
};

function gearShape(teeth) {
  const s = new THREE.Shape();
  const n = teeth * 4;
  for (let i = 0; i <= n; i++) {
    const a = (i / n) * TAU;
    const r = i % 4 === 1 || i % 4 === 2 ? 0.5 : 0.41; // trapezoid teeth
    if (i === 0) s.moveTo(Math.cos(a) * r, Math.sin(a) * r);
    else s.lineTo(Math.cos(a) * r, Math.sin(a) * r);
  }
  const hole = new THREE.Path();
  hole.absarc(0, 0, 0.11, 0, TAU, true);
  s.holes.push(hole);
  return s;
}

function axisPoint(d, axis) {
  return axis === "x" ? [d, 0, 0] : axis === "z" ? [0, 0, d] : [0, d, 0];
}
/** A 3D arrow for flows and forces: a tube along `path` (or the axis) with a cone at the end. */
function arrowGeometry(p) {
  const len = Math.max(0.2, p.size[IDX[p.axis]]);
  const run =
    p.path && p.path.length >= 2
      ? p.path
      : [axisPoint(-len / 2, p.axis), axisPoint(len / 2, p.axis)];
  const pts = run.map((v) => new THREE.Vector3(...v));
  const r = p.radius ?? Math.max(0.05, Math.min(0.2, across(p.size, p.axis) / 4));
  const head = Math.max(r * 3.2, 0.12);
  const tip = pts[pts.length - 1];
  const dir = tip.clone().sub(pts[pts.length - 2]).normalize();
  const shaftEnd = tip.clone().addScaledVector(dir, -head * 0.9);
  const shaftPts = [...pts.slice(0, -1), shaftEnd];
  const shaft =
    shaftPts.length > 2
      ? new THREE.TubeGeometry(new THREE.CatmullRomCurve3(shaftPts), 48, r, 12, false)
      : new THREE.TubeGeometry(new THREE.LineCurve3(shaftPts[0], shaftPts[1]), 4, r, 12, false);
  const cone = new THREE.ConeGeometry(r * 2.4, head, 24, 1);
  cone.translate(0, head / 2, 0);
  cone.applyQuaternion(
    new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 1, 0), dir),
  );
  cone.translate(shaftEnd.x, shaftEnd.y, shaftEnd.z);
  return mergeGeometries([shaft.toNonIndexed(), cone.toNonIndexed()]) ?? shaft;
}

/** A unit cylinder whose rims are softened by a small bevel. */
function beveledCylinder(top, bottom) {
  const e = 0.035;
  return new THREE.LatheGeometry(
    [
      new THREE.Vector2(0, -0.5),
      new THREE.Vector2(Math.max(0, bottom - e), -0.5),
      new THREE.Vector2(bottom, -0.5 + e),
      new THREE.Vector2(top, 0.5 - e),
      new THREE.Vector2(Math.max(0, top - e), 0.5),
      new THREE.Vector2(0, 0.5),
    ],
    48,
  );
}

/** { geometry, scale }: unit shapes are stretched to the box, shapes built at true size use 1. */
export function buildGeometry(p) {
  const [sx, sy, sz] = p.size;
  const unit = [sx, sy, sz];
  const one = [1, 1, 1];
  switch (p.shape) {
    case "sphere":
      return { geometry: new THREE.SphereGeometry(0.5, 48, 32), scale: unit };
    case "cylinder": {
      const t = p.taper ?? 1; // radius at the +axis end / radius at the -axis end
      const top = 0.5 * Math.min(1, t);
      const bottom = 0.5 * Math.min(1, t > 0 ? 1 / t : 1);
      return { geometry: alongAxis(beveledCylinder(top, bottom), p.axis), scale: unit };
    }
    case "cone":
      return {
        geometry: alongAxis(new THREE.ConeGeometry(0.5, 1, 48, 1), p.axis),
        scale: unit,
      };
    case "capsule": {
      const len = p.size[IDX[p.axis]];
      const r = across(p.size, p.axis) / 2;
      return {
        geometry: alongAxis(
          new THREE.CapsuleGeometry(r, Math.max(0.001, len - 2 * r), 10, 32),
          p.axis,
        ),
        scale: one,
      };
    }
    case "torus": {
      const thick = p.size[IDX[p.axis]];
      const d = across(p.size, p.axis);
      return {
        geometry: fromZ(
          new THREE.TorusGeometry(Math.max(0.001, (d - thick) / 2), thick / 2, 20, 64),
          p.axis,
        ),
        scale: one,
      };
    }
    case "ring": {
      const h = p.size[IDX[p.axis]];
      const r = across(p.size, p.axis) / 2;
      const ri = r * 0.62;
      const pts = [
        [ri, -h / 2],
        [r, -h / 2],
        [r, h / 2],
        [ri, h / 2],
        [ri, -h / 2],
      ].map(([a, b]) => new THREE.Vector2(a, b));
      return { geometry: alongAxis(new THREE.LatheGeometry(pts, 64), p.axis), scale: one };
    }
    case "wedge": {
      const s = new THREE.Shape([
        new THREE.Vector2(-0.5, -0.5),
        new THREE.Vector2(0.5, -0.5),
        new THREE.Vector2(-0.5, 0.5),
      ]);
      const g = new THREE.ExtrudeGeometry(s, { depth: 1, bevelEnabled: false });
      g.translate(0, 0, -0.5);
      return { geometry: g, scale: unit };
    }
    case "gear": {
      const g = new THREE.ExtrudeGeometry(gearShape(p.teeth ?? 16), {
        depth: 1,
        bevelEnabled: false,
        curveSegments: 4,
      });
      g.translate(0, 0, -0.5);
      return { geometry: fromZ(g, p.axis), scale: unit };
    }
    case "spring": {
      const len = p.size[IDX[p.axis]];
      const d = across(p.size, p.axis);
      const wire = Math.max(0.006, d * 0.06);
      const R = Math.max(0.001, d / 2 - wire);
      const turns = p.turns ?? 8;
      const pts = [];
      const steps = Math.ceil(turns * 28);
      for (let i = 0; i <= steps; i++) {
        const u = i / steps;
        const a = u * turns * TAU;
        pts.push(new THREE.Vector3(Math.cos(a) * R, -len / 2 + u * len, Math.sin(a) * R));
      }
      const g = new THREE.TubeGeometry(new THREE.CatmullRomCurve3(pts), steps, wire, 10, false);
      return { geometry: alongAxis(g, p.axis), scale: one };
    }
    case "tube": {
      const pts = (
        p.path ?? [
          [0, -0.5, 0],
          [0, 0.5, 0],
        ]
      ).map((v) => new THREE.Vector3(...v));
      const curve = new THREE.CatmullRomCurve3(pts, false, "centripetal");
      const r = p.radius ?? Math.max(0.01, across(p.size, p.axis) / 2);
      return {
        geometry: new THREE.TubeGeometry(curve, Math.max(16, pts.length * 24), r, 14, false),
        scale: one,
      };
    }
    case "extrude": {
      const prof = p.profile ?? [
        [-0.5, -0.5],
        [0.5, -0.5],
        [0.5, 0.5],
        [-0.5, 0.5],
      ];
      const depth = p.size[IDX[p.axis]];
      const shape = new THREE.Shape(prof.map(([x, y]) => new THREE.Vector2(x, y)));
      const bevel = Math.min(0.04, depth * 0.25);
      const g = new THREE.ExtrudeGeometry(shape, {
        depth: Math.max(0.001, depth - bevel * 2),
        bevelEnabled: true,
        bevelSize: bevel,
        bevelThickness: bevel,
        bevelSegments: 2,
      });
      g.translate(0, 0, -depth / 2 + bevel);
      return { geometry: fromZ(g, p.axis), scale: one };
    }
    case "lathe": {
      // [[along, radius], ...] turned round the axis, closed at both ends, at true size
      const prof = (
        p.profile ?? [
          [-0.5, 0.5],
          [0.5, 0.5],
        ]
      ).map(([a, r]) => new THREE.Vector2(Math.max(0, r), a));
      if (prof[0].x > 0) prof.unshift(new THREE.Vector2(0, prof[0].y));
      if (prof[prof.length - 1].x > 0)
        prof.push(new THREE.Vector2(0, prof[prof.length - 1].y));
      const pts =
        prof.length >= 4
          ? new THREE.SplineCurve(prof).getSpacedPoints(Math.min(96, prof.length * 8))
          : prof;
      pts[0].x = 0;
      pts[pts.length - 1].x = 0;
      return { geometry: alongAxis(new THREE.LatheGeometry(pts, 64), p.axis), scale: one };
    }
    case "arrow":
      return { geometry: arrowGeometry(p), scale: one };
    case "marker":
      return { geometry: new THREE.SphereGeometry(0.01, 4, 2), scale: one };
    case "plane":
      return {
        geometry: new THREE.BoxGeometry(1, 1, 1),
        scale: [sx, Math.max(0.004, sy), sz],
      };
    case "rounded_box":
      return {
        geometry: new RoundedBoxGeometry(sx, sy, sz, 3, Math.min(sx, sy, sz) * 0.2),
        scale: one,
      };
    default: {
      // real parts are never razor-edged: a hairline bevel catches a highlight along every edge
      return {
        geometry: new RoundedBoxGeometry(sx, sy, sz, 2, Math.min(sx, sy, sz) * 0.06),
        scale: one,
      };
    }
  }
}

// ---- the parts kit (only the pieces the site's models use)
function merge(gs) {
  const flat = gs.map((g) => (g.index ? g.toNonIndexed() : g));
  for (const g of flat)
    if (!g.getAttribute("uv"))
      g.setAttribute(
        "uv",
        new THREE.Float32BufferAttribute(
          new Float32Array(g.getAttribute("position").count * 2),
          2,
        ),
      );
  return mergeGeometries(flat) ?? flat[0];
}
function missile(axis) {
  const body = new THREE.LatheGeometry(
    [
      [0, -0.5],
      [0.05, -0.5],
      [0.06, -0.46],
      [0.06, 0.3],
      [0.05, 0.4],
      [0.03, 0.47],
      [0, 0.5],
    ].map(([r, y]) => new THREE.Vector2(r, y)),
    32,
  );
  const fins = [];
  for (let i = 0; i < 4; i++) {
    const s = new THREE.Shape([
      new THREE.Vector2(0.06, -0.5),
      new THREE.Vector2(0.16, -0.5),
      new THREE.Vector2(0.06, -0.33),
    ]);
    fins.push(
      new THREE.ExtrudeGeometry(s, { depth: 0.008, bevelEnabled: false })
        .translate(0, 0, -0.004)
        .rotateY((i / 4) * TAU),
    );
    const c = new THREE.Shape([
      new THREE.Vector2(0.06, 0.12),
      new THREE.Vector2(0.11, 0.15),
      new THREE.Vector2(0.11, 0.19),
      new THREE.Vector2(0.06, 0.24),
    ]);
    fins.push(
      new THREE.ExtrudeGeometry(c, { depth: 0.006, bevelEnabled: false })
        .translate(0, 0, -0.003)
        .rotateY((i / 4) * TAU + Math.PI / 4),
    );
  }
  const seeker = new THREE.SphereGeometry(0.03, 16, 12).translate(0, 0.485, 0);
  const band = (y) => new THREE.CylinderGeometry(0.062, 0.062, 0.02, 32).translate(0, y, 0);
  return [
    { geometry: alongAxis(body, axis), material: "painted", main: true, color: "#e9ecef" },
    { geometry: alongAxis(merge(fins), axis), material: "painted", color: "#b8bec6" },
    { geometry: alongAxis(merge([seeker, band(0.05), band(-0.25)]), axis), material: "dark_metal" },
  ];
}
/** A kit piece as layers, each with its own material, fitted to the part's box. Null if unknown. */
export function buildKit(p) {
  if (p.kit !== "missile") return null;
  // a missile lies along its longest side, whatever axis was written
  const order = [0, 1, 2].sort((a, b) => p.size[a] - p.size[b]);
  const axis =
    p.size[order[1]] < p.size[order[2]] * 0.8 ? ["x", "y", "z"][order[2]] : p.axis;
  const layers = missile(axis);
  const all = new THREE.Box3();
  for (const l of layers) {
    l.geometry.computeBoundingBox();
    all.union(l.geometry.boundingBox);
  }
  const s = all.getSize(new THREE.Vector3());
  const c = all.getCenter(new THREE.Vector3());
  const k = [0, 1, 2].map((i) => p.size[i] / Math.max(1e-6, s.getComponent(i)));
  for (const l of layers)
    l.geometry.translate(-c.x, -c.y, -c.z).scale(k[0], k[1], k[2]).computeVertexNormals();
  return layers;
}

// ---- materials
export const MATERIALS = {
  metal: { color: "#c9ced6", metalness: 1, roughness: 0.22 },
  brushed_metal: { color: "#b4b9c1", metalness: 0.92, roughness: 0.4 },
  dark_metal: { color: "#474c56", metalness: 0.85, roughness: 0.34, clearcoat: 0.2 },
  gold: { color: "#e0b25a", metalness: 1, roughness: 0.2 },
  plastic: { color: "#dde1e7", metalness: 0, roughness: 0.38, clearcoat: 0.35 },
  rubber: { color: "#2a2d33", metalness: 0, roughness: 0.88, sheen: 0.4 },
  glass: { color: "#d3e8ff", metalness: 0.1, roughness: 0.04, clearcoat: 1, opacity: 0.42 },
  painted: { color: "#9aa5b2", metalness: 0.25, roughness: 0.3, clearcoat: 0.85 },
  fabric: { color: "#8b8f7a", metalness: 0, roughness: 0.95, sheen: 1 },
  wood: { color: "#a4743f", metalness: 0, roughness: 0.64, clearcoat: 0.15 },
  ceramic: { color: "#f2f0ea", metalness: 0, roughness: 0.18, clearcoat: 0.7 },
  skin: { color: "#e2b08e", metalness: 0, roughness: 0.55, sheen: 0.5 },
  emissive: { color: "#ffb347", metalness: 0, roughness: 1, emissive: true },
};

/** Edge light: a thin cool fresnel rim on every surface, so silhouettes read on any backdrop. */
const RIM = new THREE.Color("#9ecbff");
function addRim(m, strength) {
  const rim = { value: strength };
  m.userData.rim = rim;
  m.onBeforeCompile = (shader) => {
    shader.uniforms.rimStrength = rim;
    shader.uniforms.rimColor = { value: RIM };
    shader.fragmentShader = shader.fragmentShader
      .replace(
        "#include <common>",
        "#include <common>\nuniform float rimStrength;\nuniform vec3 rimColor;",
      )
      .replace(
        "#include <emissivemap_fragment>",
        `#include <emissivemap_fragment>
        {
          float facing = clamp(abs(dot(normal, normalize(vViewPosition))), 0.0, 1.0);
          float rim = pow(1.0 - facing, 3.2);
          totalEmissiveRadiance += rimColor * rim * rimStrength;
        }`,
      );
  };
  m.customProgramCacheKey = () => "pyxi-rim";
}

export function makeMaterial(p, style) {
  const preset = MATERIALS[p.material] ?? MATERIALS.plastic;
  const color = new THREE.Color(p.color ?? preset.color);
  const m = new THREE.MeshPhysicalMaterial({
    color,
    metalness: preset.metalness,
    roughness: preset.roughness,
    clearcoat: preset.clearcoat ?? 0,
    clearcoatRoughness: 0.12,
    sheen: preset.sheen ?? 0,
    sheenRoughness: 0.6,
    sheenColor: color.clone().lerp(new THREE.Color("#ffffff"), 0.4),
    envMapIntensity: p.material === "glass" ? 2.2 : preset.metalness > 0.5 ? 1.25 : 1,
    specularIntensity: p.material === "glass" ? 1 : 0.8,
    side: p.shape === "plane" || p.shape === "extrude" ? THREE.DoubleSide : THREE.FrontSide,
  });
  if (preset.emissive) {
    // glow without clipping to yellow-white: a hot colour stays that colour
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
  if (!preset.emissive && p.shape !== "arrow")
    addRim(
      m,
      style === "blueprint" ? 0.9 : style === "xray" ? 0.7 : preset.metalness > 0.5 ? 0.28 : 0.2,
    );
  // the part's own opacity (and its motions) multiply this: glass stays glassy when it fades
  m.userData.factor =
    (preset.opacity ?? 1) * (style === "xray" ? 0.28 : style === "blueprint" ? 0.55 : 1);
  m.userData.baseEmissive = m.emissiveIntensity;
  return m;
}
