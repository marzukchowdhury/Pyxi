/**
 * Shapes for living things: bodies that swell, taper and bend, and lumps that melt into one skin.
 *
 * A model made of boxes, cylinders and balls reads as a machine however it is arranged. A cat, a
 * heart or a tree needs two things those shapes cannot give: a form whose thickness changes along
 * a curved spine (`limb`), and several rounded masses joined without a seam (`blob`). Both are
 * built here from plain numbers in the part (a spine with radii, a list of lumps); nothing the
 * model wrote is run.
 */
import * as THREE from "three";
import { edgeTable, triTable } from "three/examples/jsm/objects/MarchingCubes.js";

const AROUND = 20; // points round a limb
const CAP = 5; // rings in each rounded end

/** The radius at `u` (0..1) along a spine whose radii are spread evenly over it, eased between. */
export function radiusAt(radii: number[], u: number): number {
  if (radii.length === 1) return radii[0];
  const at = Math.min(1, Math.max(0, u)) * (radii.length - 1);
  const i = Math.min(radii.length - 2, Math.floor(at));
  const f = at - i;
  // Catmull-Rom through the radii, so a swelling is a curve and not a row of cones.
  const p0 = radii[Math.max(0, i - 1)], p1 = radii[i], p2 = radii[i + 1], p3 = radii[Math.min(radii.length - 1, i + 2)];
  const r = 0.5 * (2 * p1 + (p2 - p0) * f + (2 * p0 - 5 * p1 + 4 * p2 - p3) * f * f + (3 * p1 - p0 - 3 * p2 + p3) * f * f * f);
  return Math.max(Math.min(p1, p2) * 0.5, r, 0.004);
}

function spine(path: number[][]): THREE.Curve<THREE.Vector3> {
  const pts = path.map((v) => new THREE.Vector3(v[0], v[1], v[2]));
  return pts.length > 2 ? new THREE.CatmullRomCurve3(pts, false, "centripetal") : new THREE.LineCurve3(pts[0], pts[1]);
}

/**
 * A body along a spine: round in section, `radii` thick along its length, with rounded ends.
 * `squash` under 1 flattens it side to side (a fish, a leaf, a fin).
 */
export function limbGeometry(path: number[][], radii: number[], squash = 1): THREE.BufferGeometry {
  const curve = spine(path);
  const steps = Math.min(120, Math.max(24, path.length * 14));
  const pos: number[] = [];
  const index: number[] = [];
  const rings: number[] = []; // first vertex of each ring
  const t = new THREE.Vector3(), n = new THREE.Vector3(), b = new THREE.Vector3(), c = new THREE.Vector3(), prev = new THREE.Vector3();
  const q = new THREE.Quaternion();

  const ring = (centre: THREE.Vector3, r: number) => {
    rings.push(pos.length / 3);
    for (let k = 0; k < AROUND; k++) {
      const a = (k / AROUND) * Math.PI * 2;
      const x = Math.cos(a) * r * squash, y = Math.sin(a) * r;
      pos.push(centre.x + n.x * x + b.x * y, centre.y + n.y * x + b.y * y, centre.z + n.z * x + b.z * y);
    }
  };
  const apex = (at: THREE.Vector3) => { pos.push(at.x, at.y, at.z); return pos.length / 3 - 1; };

  // The first frame: "side to side" is the world's x wherever the spine allows it.
  curve.getTangent(0, t).normalize();
  n.set(1, 0, 0).addScaledVector(t, -t.x);
  if (n.lengthSq() < 0.01) n.set(0, 0, 1).addScaledVector(t, -t.z);
  n.normalize();
  b.crossVectors(t, n).normalize();

  // The rounded start: from the tip back to the first full ring.
  const r0 = radiusAt(radii, 0), r1 = radiusAt(radii, 1);
  curve.getPoint(0, c);
  const tip0 = apex(c.clone().addScaledVector(t, -r0));
  for (let j = CAP - 1; j >= 1; j--) {
    const a = (j / CAP) * (Math.PI / 2);
    ring(c.clone().addScaledVector(t, -r0 * Math.sin(a)), r0 * Math.cos(a));
  }
  for (let i = 0; i <= steps; i++) {
    const u = i / steps;
    prev.copy(t);
    curve.getPoint(u, c);
    curve.getTangent(u, t).normalize();
    if (i > 0) {
      // Carry the frame along without twisting it.
      q.setFromUnitVectors(prev, t);
      n.applyQuaternion(q).addScaledVector(t, -n.dot(t)).normalize();
      b.crossVectors(t, n).normalize();
    }
    ring(c, radiusAt(radii, u));
  }
  for (let j = 1; j < CAP; j++) {
    const a = (j / CAP) * (Math.PI / 2);
    ring(c.clone().addScaledVector(t, r1 * Math.sin(a)), r1 * Math.cos(a));
  }
  const tip1 = apex(c.clone().addScaledVector(t, r1));

  for (let k = 0; k < AROUND; k++) {
    const k1 = (k + 1) % AROUND;
    index.push(tip0, rings[0] + k1, rings[0] + k);
    for (let i = 0; i < rings.length - 1; i++) {
      const a = rings[i] + k, a1 = rings[i] + k1, d = rings[i + 1] + k, d1 = rings[i + 1] + k1;
      index.push(a, a1, d, a1, d1, d);
    }
    const last = rings[rings.length - 1];
    index.push(tip1, last + k, last + k1);
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
  g.setIndex(index);
  g.computeVertexNormals();
  return g;
}

/** One rounded mass or one stretch of spine, as the blob's field sees it. */
interface Lump {
  c: [number, number, number];
  /** An egg: 1 / radius on each axis, and its smallest radius. */
  inv?: [number, number, number];
  small?: number;
  /** A stretch of spine from `c` to `e`, `r0` thick at the start and `r1` at the end. */
  e?: [number, number, number];
  r0?: number;
  r1?: number;
}

function soften(a: number, b: number, k: number): number {
  if (k <= 0) return Math.min(a, b);
  const h = Math.max(k - Math.abs(a - b), 0) / k;
  return Math.min(a, b) - h * h * k * 0.25;
}

/** How far a point is outside the skin (negative inside): every lump, joined softly. */
function field(lumps: Lump[], k: number, x: number, y: number, z: number): number {
  let d = Infinity;
  for (const l of lumps) {
    let v: number;
    if (l.inv) {
      const dx = (x - l.c[0]) * l.inv[0], dy = (y - l.c[1]) * l.inv[1], dz = (z - l.c[2]) * l.inv[2];
      v = (Math.sqrt(dx * dx + dy * dy + dz * dz) - 1) * l.small!;
    } else {
      const ex = l.e![0] - l.c[0], ey = l.e![1] - l.c[1], ez = l.e![2] - l.c[2];
      const px = x - l.c[0], py = y - l.c[1], pz = z - l.c[2];
      const h = Math.min(1, Math.max(0, (px * ex + py * ey + pz * ez) / Math.max(1e-9, ex * ex + ey * ey + ez * ez)));
      const qx = px - ex * h, qy = py - ey * h, qz = pz - ez * h;
      v = Math.sqrt(qx * qx + qy * qy + qz * qz) - (l.r0! + (l.r1! - l.r0!) * h);
    }
    d = d === Infinity ? v : soften(d, v, k);
  }
  return d;
}

// Each cube edge: the corner it starts from (as x, y, z steps) and the axis it runs along.
const EDGES: [number, number, number, number][] = [
  [0, 0, 0, 0], [1, 0, 0, 1], [0, 1, 0, 0], [0, 0, 0, 1],
  [0, 0, 1, 0], [1, 0, 1, 1], [0, 1, 1, 0], [0, 0, 1, 1],
  [0, 0, 0, 2], [1, 0, 0, 2], [1, 1, 0, 2], [0, 1, 0, 2],
];
const CORNERS: [number, number, number][] = [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0], [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1]];

/**
 * Several rounded lumps (`[x, y, z, rx, ry, rz]`) and, if given, a spine with radii, melted into
 * one smooth closed skin. `blend` is how far they run together (0 a hard join, 1 very soft).
 */
export function blobGeometry(lumpList: number[][], path: number[][] | null, radii: number[] | null, blend = 0.5, detail = 46): THREE.BufferGeometry {
  const lumps: Lump[] = [];
  const lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
  const grow = (c: number[], r: number[]) => { for (let i = 0; i < 3; i++) { lo[i] = Math.min(lo[i], c[i] - r[i]); hi[i] = Math.max(hi[i], c[i] + r[i]); } };
  const smalls: number[] = [];
  for (const l of lumpList) {
    const r = [Math.max(0.01, l[3]), Math.max(0.01, l[4] ?? l[3]), Math.max(0.01, l[5] ?? l[3])];
    const small = Math.min(r[0], r[1], r[2]);
    lumps.push({ c: [l[0], l[1], l[2]], inv: [1 / r[0], 1 / r[1], 1 / r[2]], small });
    smalls.push(small);
    grow(l, r);
  }
  if (path && path.length >= 2) {
    const curve = spine(path);
    const rs = radii && radii.length ? radii : [0.2];
    const n = Math.max(2, (path.length - 1) * 5);
    let a = curve.getPoint(0);
    for (let i = 1; i <= n; i++) {
      const e = curve.getPoint(i / n);
      const r0 = radiusAt(rs, (i - 1) / n), r1 = radiusAt(rs, i / n);
      lumps.push({ c: [a.x, a.y, a.z], e: [e.x, e.y, e.z], r0, r1 });
      grow([a.x, a.y, a.z], [r0, r0, r0]);
      grow([e.x, e.y, e.z], [r1, r1, r1]);
      a = e;
    }
    smalls.push(...rs);
  }
  const g = new THREE.BufferGeometry();
  if (!lumps.length) return g;
  smalls.sort((p, q) => p - q);
  const k = Math.min(1, Math.max(0, blend)) * 0.9 * smalls[Math.floor(smalls.length / 2)];
  const extent = Math.max(hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]);
  const cell = Math.max(extent / detail, 1e-4);
  const pad = cell * 1.5 + k * 0.3;
  const o = [lo[0] - pad, lo[1] - pad, lo[2] - pad];
  const nx = Math.min(72, Math.ceil((hi[0] - lo[0] + 2 * pad) / cell)), ny = Math.min(72, Math.ceil((hi[1] - lo[1] + 2 * pad) / cell)), nz = Math.min(72, Math.ceil((hi[2] - lo[2] + 2 * pad) / cell));
  const sx = nx + 1, sy = ny + 1;
  const at = (i: number, j: number, m: number) => i + sx * (j + sy * m);
  const values = new Float32Array(sx * sy * (nz + 1));
  for (let m = 0; m <= nz; m++) for (let j = 0; j <= ny; j++) for (let i = 0; i <= nx; i++) values[at(i, j, m)] = field(lumps, k, o[0] + i * cell, o[1] + j * cell, o[2] + m * cell);

  const pos: number[] = [];
  const index: number[] = [];
  const made = new Map<number, number>(); // one vertex for each grid edge the skin crosses
  const vertex = (i: number, j: number, m: number, axis: number): number => {
    const key = at(i, j, m) * 3 + axis;
    const have = made.get(key);
    if (have !== undefined) return have;
    const a = values[at(i, j, m)];
    const b = values[at(i + (axis === 0 ? 1 : 0), j + (axis === 1 ? 1 : 0), m + (axis === 2 ? 1 : 0))];
    const f = Math.abs(a - b) < 1e-12 ? 0.5 : a / (a - b);
    const p = [o[0] + i * cell, o[1] + j * cell, o[2] + m * cell];
    p[axis] += f * cell;
    pos.push(p[0], p[1], p[2]);
    made.set(key, pos.length / 3 - 1);
    return pos.length / 3 - 1;
  };
  const corner = [0, 0, 0, 0, 0, 0, 0, 0];
  const edge = new Array<number>(12);
  for (let m = 0; m < nz; m++) for (let j = 0; j < ny; j++) for (let i = 0; i < nx; i++) {
    let cube = 0;
    for (let c = 0; c < 8; c++) {
      corner[c] = values[at(i + CORNERS[c][0], j + CORNERS[c][1], m + CORNERS[c][2])];
      if (corner[c] < 0) cube |= 1 << c;
    }
    const bits = edgeTable[cube];
    if (!bits) continue;
    for (let e = 0; e < 12; e++) if (bits & (1 << e)) edge[e] = vertex(i + EDGES[e][0], j + EDGES[e][1], m + EDGES[e][2], EDGES[e][3]);
    for (let t = cube * 16; triTable[t] !== -1; t += 3) index.push(edge[triTable[t]], edge[triTable[t + 1]], edge[triTable[t + 2]]);
  }

  // Normals from the field itself (smooth across every join), and every face turned outwards.
  const normal = new Float32Array(pos.length);
  const h = cell * 0.5;
  for (let v = 0; v < pos.length; v += 3) {
    const x = pos[v], y = pos[v + 1], z = pos[v + 2];
    const gx = field(lumps, k, x + h, y, z) - field(lumps, k, x - h, y, z);
    const gy = field(lumps, k, x, y + h, z) - field(lumps, k, x, y - h, z);
    const gz = field(lumps, k, x, y, z + h) - field(lumps, k, x, y, z - h);
    const len = Math.hypot(gx, gy, gz) || 1;
    normal[v] = gx / len; normal[v + 1] = gy / len; normal[v + 2] = gz / len;
  }
  for (let t = 0; t < index.length; t += 3) {
    const a = index[t] * 3, b = index[t + 1] * 3, c = index[t + 2] * 3;
    const ux = pos[b] - pos[a], uy = pos[b + 1] - pos[a + 1], uz = pos[b + 2] - pos[a + 2];
    const vx = pos[c] - pos[a], vy = pos[c + 1] - pos[a + 1], vz = pos[c + 2] - pos[a + 2];
    const fx = uy * vz - uz * vy, fy = uz * vx - ux * vz, fz = ux * vy - uy * vx;
    if (fx * (normal[a] + normal[b] + normal[c]) + fy * (normal[a + 1] + normal[b + 1] + normal[c + 1]) + fz * (normal[a + 2] + normal[b + 2] + normal[c + 2]) < 0) {
      const swap = index[t + 1];
      index[t + 1] = index[t + 2];
      index[t + 2] = swap;
    }
  }
  g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
  g.setAttribute("normal", new THREE.BufferAttribute(normal, 3));
  g.setIndex(index);
  return g;
}
