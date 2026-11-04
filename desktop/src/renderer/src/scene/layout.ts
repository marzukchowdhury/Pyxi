// Layout engines: SceneSpec -> 3D positions. Pure functions (unit-tested in layout.test.ts).
// World units: ~1 unit per "object"; exploded views use 1 unit = 100 CSS px so the z=0 plane
// can be matched 1:1 with the real screen.
import type { SceneEdge, SceneNode, SceneSpec } from "../shared/protocol";

export type Vec3 = [number, number, number];

export interface Placed {
  id: string;
  pos: Vec3;
  /** Exploded/screen nodes: where the piece sits flat on the real screen (z = 0) before it lifts off. */
  start?: Vec3;
  size: [number, number];
}

export interface Layout {
  nodes: Map<string, Placed>;
  center: Vec3;
  radius: number;
  /** Width, height and depth of the scene's bounding box. */
  extent: Vec3;
}

const SPACING = 3.2;

export function computeLayout(scene: SceneSpec, viewport?: { width: number; height: number; scale: number; origin: { x: number; y: number } }): Layout {
  const nodes = scene.nodes;
  let placed: Placed[];
  switch (scene.layout) {
    case "tree":
      placed = tree(nodes);
      break;
    case "orbit":
      placed = orbit(nodes, scene.edges);
      break;
    case "stack":
      placed = stack(nodes);
      break;
    case "grid":
      placed = grid(nodes);
      break;
    case "timeline":
      placed = timeline(nodes);
      break;
    case "exploded":
      placed = exploded(nodes, viewport);
      break;
    case "free":
      placed = nodes.map((n, i) => ({ id: n.id, pos: n.position ?? [i * SPACING, 0, 0], size: unit(n) }));
      break;
    default:
      placed = flow(nodes, scene.edges);
  }
  if (scene.layout !== "exploded") placed = recenter(placed);
  const map = new Map(placed.map((p) => [p.id, p]));
  return { nodes: map, ...bounds(placed) };
}

function unit(n: SceneNode): [number, number] {
  const s = n.size ?? 1;
  return n.shape === "card" ? [2.4 * s, 1.3 * s] : n.shape === "chip" ? [1.8 * s, 0.6 * s] : [1.2 * s, 1.2 * s];
}

/** Longest-path layering: sources on the left, each edge moves one column right. */
export function flow(nodes: SceneNode[], edges: SceneEdge[]): Placed[] {
  const ids = nodes.map((n) => n.id);
  const layer = new Map(ids.map((id) => [id, 0]));
  const forward = acyclic(ids, edges.filter((e) => !e.bidirectional));
  for (let pass = 0; pass < ids.length; pass++) {
    let changed = false;
    for (const e of forward) {
      const next = (layer.get(e.from) ?? 0) + 1;
      if (next > (layer.get(e.to) ?? 0) && next < ids.length) {
        layer.set(e.to, next);
        changed = true;
      }
    }
    if (!changed) break;
  }
  const columns = new Map<number, SceneNode[]>();
  for (const n of nodes) columns.set(layer.get(n.id)!, [...(columns.get(layer.get(n.id)!) ?? []), n]);
  const out: Placed[] = [];
  for (const [col, members] of columns) {
    members.forEach((n, i) => {
      const y = ((members.length - 1) / 2 - i) * 3.1;
      out.push({ id: n.id, pos: [col * SPACING * 1.5, y, (i % 2) * 0.5 - 0.25], size: unit(n) });
    });
  }
  return out;
}

/** Drop edges that close a cycle (DFS back edges, visiting nodes in the order the author listed them). */
export function acyclic(ids: string[], edges: SceneEdge[]): SceneEdge[] {
  const out = new Map<string, SceneEdge[]>();
  for (const e of edges) out.set(e.from, [...(out.get(e.from) ?? []), e]);
  const state = new Map<string, 0 | 1 | 2>(); // 0 new, 1 on stack, 2 done
  const keep: SceneEdge[] = [];
  const visit = (id: string) => {
    state.set(id, 1);
    for (const e of out.get(id) ?? []) {
      const s = state.get(e.to) ?? 0;
      if (s === 1) continue; // back edge: skip for layering
      keep.push(e);
      if (s === 0) visit(e.to);
    }
    state.set(id, 2);
  };
  for (const id of ids) if (!state.get(id)) visit(id);
  return keep;
}

/** Tidy tree: each leaf gets a slot; parents sit above the middle of their children. */
export function tree(nodes: SceneNode[]): Placed[] {
  const children = new Map<string | null, SceneNode[]>();
  for (const n of nodes) {
    const key = n.parent ?? null;
    children.set(key, [...(children.get(key) ?? []), n]);
  }
  const out: Placed[] = [];
  let slot = 0;
  const visit = (n: SceneNode, depth: number): number => {
    const kids = children.get(n.id) ?? [];
    let x: number;
    if (!kids.length) x = slot++ * 2.6;
    else {
      const xs = kids.map((k) => visit(k, depth + 1));
      x = (Math.min(...xs) + Math.max(...xs)) / 2;
    }
    out.push({ id: n.id, pos: [x, -depth * 2.6, -depth * 0.8], size: unit(n) });
    return x;
  };
  (children.get(null) ?? []).forEach((root) => visit(root, 0));
  return out;
}

/** A hub in the middle (most connected, or largest) with satellites on a ring. */
export function orbit(nodes: SceneNode[], edges: SceneEdge[]): Placed[] {
  const degree = new Map(nodes.map((n) => [n.id, 0]));
  for (const e of edges) {
    degree.set(e.from, (degree.get(e.from) ?? 0) + 1);
    degree.set(e.to, (degree.get(e.to) ?? 0) + 1);
  }
  const hub = [...nodes].sort((a, b) => (degree.get(b.id)! + b.size) - (degree.get(a.id)! + a.size))[0];
  const rest = nodes.filter((n) => n !== hub);
  // The ring stands upright, facing the viewer (a wheel around the hub), with a little depth
  // wobble so it still reads as 3D. A flat ring would put satellites between camera and hub.
  const r = Math.max(3.8, rest.length * 0.78);
  return [
    { id: hub.id, pos: [0, 0, 0], size: unit(hub) },
    ...rest.map((n, i) => {
      const a = (i / rest.length) * Math.PI * 2 + Math.PI / 2;
      return { id: n.id, pos: [Math.cos(a) * r * 1.25, Math.sin(a) * r * 0.78, Math.sin(a * 2) * 0.9] as Vec3, size: unit(n) };
    }),
  ];
}

/** Layers stacked vertically (network stacks, architecture tiers). */
export function stack(nodes: SceneNode[]): Placed[] {
  const levels = new Map<number, SceneNode[]>();
  nodes.forEach((n, i) => {
    const d = n.depth ?? i;
    levels.set(d, [...(levels.get(d) ?? []), n]);
  });
  const out: Placed[] = [];
  [...levels.keys()].sort((a, b) => a - b).forEach((d, row) => {
    const members = levels.get(d)!;
    members.forEach((n, i) => out.push({ id: n.id, pos: [(i - (members.length - 1) / 2) * 3, -row * 1.9, 0], size: [2.6, 1.1] }));
  });
  return out;
}

/** Groups become columns (in first-seen order), members spread vertically: great for networks. */
export function grid(nodes: SceneNode[]): Placed[] {
  const groups: string[] = [];
  for (const n of nodes) if (!groups.includes(n.group ?? "")) groups.push(n.group ?? "");
  const out: Placed[] = [];
  groups.forEach((g, col) => {
    const members = nodes.filter((n) => (n.group ?? "") === g);
    members.forEach((n, i) => out.push({ id: n.id, pos: [col * SPACING * 1.1, ((members.length - 1) / 2 - i) * 1.6, 0], size: unit(n) }));
  });
  return out;
}

/** Order left to right; each group gets its own lane (git branches, causal chains). */
export function timeline(nodes: SceneNode[]): Placed[] {
  const lanes: string[] = [];
  for (const n of nodes) if (!lanes.includes(n.group ?? "")) lanes.push(n.group ?? "");
  return nodes.map((n, i) => {
    const lane = lanes.indexOf(n.group ?? "");
    return { id: n.id, pos: [i * 2.8, -lane * 2.4, lane * -0.6], size: unit(n) };
  });
}

/**
 * Exploded views: every piece starts exactly where it is on screen (z = 0, 1 unit = 100 CSS px,
 * origin at the window centre) and lifts off by hierarchy depth.
 */
export function exploded(nodes: SceneNode[], viewport?: { width: number; height: number; scale: number; origin: { x: number; y: number } }): Placed[] {
  const vp = viewport ?? { width: 1600, height: 900, scale: 1, origin: { x: 0, y: 0 } };
  const rects = nodes.map((n) => n.screen_rect ?? [0, 0, 100, 60]);
  return nodes.map((n, i) => {
    const [x, y, w, h] = rects[i];
    const cx = ((x - vp.origin.x) / vp.scale + w / vp.scale / 2 - vp.width / 2) / 100;
    const cy = -((y - vp.origin.y) / vp.scale + h / vp.scale / 2 - vp.height / 2) / 100;
    const depth = n.depth ?? 0;
    const start: Vec3 = [cx, cy, 0.002 * i];
    const pos: Vec3 = [cx * (1 + depth * 0.06), cy * (1 + depth * 0.06), depth * 1.4 + 0.002 * i];
    return { id: n.id, pos, start, size: [Math.max(0.05, w / vp.scale / 100), Math.max(0.05, h / vp.scale / 100)] };
  });
}

function recenter(placed: Placed[]): Placed[] {
  if (!placed.length) return placed;
  const c = bounds(placed).center;
  return placed.map((p) => ({ ...p, pos: [p.pos[0] - c[0], p.pos[1] - c[1], p.pos[2] - c[2]] as Vec3 }));
}

export function bounds(placed: Placed[]): { center: Vec3; radius: number; extent: Vec3 } {
  if (!placed.length) return { center: [0, 0, 0], radius: 1, extent: [2, 2, 2] };
  const min = [Infinity, Infinity, Infinity];
  const max = [-Infinity, -Infinity, -Infinity];
  for (const p of placed) {
    for (let k = 0; k < 3; k++) {
      min[k] = Math.min(min[k], p.pos[k] - (k < 2 ? p.size[k] / 2 : 0));
      max[k] = Math.max(max[k], p.pos[k] + (k < 2 ? p.size[k] / 2 : 0));
    }
  }
  const center: Vec3 = [(min[0] + max[0]) / 2, (min[1] + max[1]) / 2, (min[2] + max[2]) / 2];
  const radius = Math.max(1.5, Math.hypot(max[0] - min[0], max[1] - min[1], max[2] - min[2]) / 2);
  const extent: Vec3 = [max[0] - min[0] + 1.6, max[1] - min[1] + 1.6, max[2] - min[2]]; // + room for labels
  return { center, radius, extent };
}

/** Bounds of a subset (for "focus" camera moves). */
export function focusBounds(layout: Layout, ids: string[]): { center: Vec3; radius: number; extent: Vec3 } {
  const subset = ids.map((id) => layout.nodes.get(id)).filter((p): p is Placed => !!p);
  return subset.length ? bounds(subset) : { center: layout.center, radius: layout.radius, extent: layout.extent };
}

/** Camera distance that fits a box of `extent` in view, for a vertical FOV (deg) and aspect ratio. */
export function fitDistance(extent: Vec3, fovDeg: number, aspect: number, margin = 1.12): number {
  const v = Math.tan(((fovDeg / 2) * Math.PI) / 180);
  const h = v * aspect;
  return Math.max(extent[0] / 2 / h, extent[1] / 2 / v) * margin + extent[2] / 2;
}
