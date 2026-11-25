/**
 * Shapes a photo proves are needed: a body drawn from its cross-sections (`loft`) and a turned
 * vessel that is hollow (`lathe` with a `wall`).
 *
 * A car body, a shoe, a computer mouse or a hull is neither round nor a flat outline: its
 * section changes along its length, wide and low at the bonnet, tall at the cabin. Built from
 * boxes it is a toy. A loft is that body as plain numbers, [along, width, height, lift, round]
 * at a few stations, joined into one smooth skin. Nothing the model wrote is run.
 */
import * as THREE from "three";
import type { Axis } from "../shared/protocol";

const AROUND = 36; // points round each section
export const LOFT_STEPS = 8; // sections eased in between each written pair

/** The sections a loft is really drawn from (the same sum as `loft_rings` in objects.py). */
export function loftRings(sections: number[][], steps = LOFT_STEPS): number[][] {
  const n = sections.length;
  if (n < 2) return sections.map((s) => [...s]);
  const full = sections.map((s) => [s[0], s[1], s[2], s[3] ?? 0, s[4] ?? 1]);
  const out: number[][] = [];
  for (let i = 0; i < n - 1; i++) {
    const p0 = full[Math.max(0, i - 1)], p1 = full[i], p2 = full[i + 1], p3 = full[Math.min(n - 1, i + 2)];
    const count = steps + (i === n - 2 ? 1 : 0);
    for (let k = 0; k < count; k++) {
      const f = k / steps;
      const row = p1.map((b, j) => {
        const a = p0[j], c = p2[j], d = p3[j];
        return 0.5 * (2 * b + (c - a) * f + (2 * a - 5 * b + 4 * c - d) * f * f + (3 * b - a - 3 * c + d) * f * f * f);
      });
      row[0] = Math.min(Math.max(row[0], p1[0]), p2[0]);
      row[1] = Math.max(0.004, row[1]);
      row[2] = Math.max(0.004, row[2]);
      row[4] = Math.max(0.15, Math.min(1, row[4]));
      out.push(row);
    }
  }
  return out;
}

/** A point on a section's outline: an oval at round 1, squarer as round falls. */
export function sectionPoint(angle: number, halfWidth: number, halfHeight: number, round: number): [number, number] {
  const e = Math.max(0.15, Math.min(1, round)); // exponent 2/n of the superellipse
  const c = Math.cos(angle), s = Math.sin(angle);
  return [halfWidth * Math.sign(c) * Math.abs(c) ** e, halfHeight * Math.sign(s) * Math.abs(s) ** e];
}

/** Where [along, across, up] of a loft lies in the part's own space. */
function place(axis: Axis, along: number, across: number, up: number): [number, number, number] {
  if (axis === "x") return [along, up, across];
  if (axis === "z") return [across, up, along];
  return [across, along, up];
}

/** One smooth skin through the sections, closed flat at both ends. */
export function loftGeometry(sections: number[][], axis: Axis): THREE.BufferGeometry {
  const rings = loftRings(sections);
  const pos: number[] = [];
  const index: number[] = [];
  for (const [along, w, h, lift, round] of rings) {
    for (let k = 0; k < AROUND; k++) {
      const [x, y] = sectionPoint((k / AROUND) * Math.PI * 2, w / 2, h / 2, round);
      pos.push(...place(axis, along, x, y + lift));
    }
  }
  // Faces must look outward whichever way the axes fall: an odd swap of axes flips the winding.
  const flip = axis !== "z";
  const tri = (a: number, b: number, c: number) => (flip ? index.push(a, c, b) : index.push(a, b, c));
  for (let i = 0; i < rings.length - 1; i++) {
    for (let k = 0; k < AROUND; k++) {
      const k1 = (k + 1) % AROUND;
      const a = i * AROUND + k, a1 = i * AROUND + k1, d = (i + 1) * AROUND + k, d1 = (i + 1) * AROUND + k1;
      tri(a, a1, d);
      tri(a1, d1, d);
    }
  }
  // End caps: a fan from the middle of each end section.
  const cap = (ring: number, first: boolean) => {
    const [along, , , lift] = rings[ring];
    const centre = pos.length / 3;
    pos.push(...place(axis, along, 0, lift));
    for (let k = 0; k < AROUND; k++) {
      const a = ring * AROUND + k, a1 = ring * AROUND + ((k + 1) % AROUND);
      if (first) tri(centre, a1, a);
      else tri(centre, a, a1);
    }
  };
  cap(0, true);
  cap(rings.length - 1, false);
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
  g.setIndex(index);
  // Flat caps and a smooth skin: split the cap corners off so the end is not shaded round.
  const out = g.toNonIndexed();
  g.dispose();
  out.computeVertexNormals();
  return smoothSkin(out);
}

/** Smooth shading over the skin, keeping creases sharper than about 50 degrees (the end caps). */
function smoothSkin(g: THREE.BufferGeometry): THREE.BufferGeometry {
  const p = g.getAttribute("position");
  const n = g.getAttribute("normal");
  const sums = new Map<string, { v: THREE.Vector3; faces: THREE.Vector3[] }>();
  const key = (i: number) => `${p.getX(i).toFixed(4)},${p.getY(i).toFixed(4)},${p.getZ(i).toFixed(4)}`;
  const face = new THREE.Vector3();
  for (let i = 0; i < p.count; i++) {
    const k = key(i);
    const e = sums.get(k) ?? { v: new THREE.Vector3(), faces: [] };
    e.faces.push(new THREE.Vector3(n.getX(i), n.getY(i), n.getZ(i)));
    sums.set(k, e);
  }
  const cos = Math.cos((50 * Math.PI) / 180);
  for (let i = 0; i < p.count; i++) {
    face.set(n.getX(i), n.getY(i), n.getZ(i));
    const sum = new THREE.Vector3();
    for (const f of sums.get(key(i))!.faces) if (f.dot(face) > cos) sum.add(f);
    sum.normalize();
    n.setXYZ(i, sum.x, sum.y, sum.z);
  }
  n.needsUpdate = true;
  return g;
}

/**
 * The outline a hollow vessel is turned from: up the outside to the rim, over it, and down
 * the inside to the floor. `outside` is [radius, along] points from the closed end to the rim.
 */
export function hollowProfile(outside: [number, number][], wall: number): [number, number][] {
  const pts = outside.map(([r, a]) => [Math.max(0, r), a] as [number, number]);
  if (pts.length < 2) return pts;
  const rim = pts[pts.length - 1];
  const length = Math.abs(rim[1] - pts[0][1]);
  const widest = Math.max(...pts.map((q) => q[0]));
  // Never so thick that nothing is left inside, and the floor is as thick as the wall.
  const t = Math.min(wall, widest * 0.45, length * 0.45);
  const dir = Math.sign(rim[1] - pts[0][1]) || 1;
  const floor = pts[0][1] + dir * t;
  const inside: [number, number][] = [];
  for (let i = pts.length - 1; i >= 0; i--) {
    const [r, a] = pts[i];
    if ((a - floor) * dir <= 0) break;
    inside.push([Math.max(0.001, r - t), a]);
  }
  const last = inside[inside.length - 1] ?? [Math.max(0.001, rim[0] - t), rim[1]];
  // The inside wall meets the floor, and the floor runs to the middle.
  return [...pts, ...inside, [last[0], floor], [0, floor]];
}
