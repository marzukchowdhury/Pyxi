/**
 * Surface detail: the fine things that make a made object read as real (a pin grid under a chip,
 * traces on a board, the cell grid of a die, heat-sink fins, brushed metal, vents, a grille, a
 * knurled grip, panel seams). The builder only names one (`surface` on a part); it is drawn here
 * as a small generated tile of colour, bumps and shine that repeats over the part. Nothing is
 * modelled and nothing is downloaded, so it costs no generation time and next to no GPU: three
 * small textures a kind, made once and shared.
 *
 * The tiles are plain numbers (no canvas), so they can be made and checked anywhere.
 */
import * as THREE from "three";

export const SURFACES = ["pins", "traces", "cells", "fins", "brushed", "vents", "grille", "knurl", "panel_lines"] as const;
export type Surface = (typeof SURFACES)[number];

type RGB = [number, number, number];

/** The part's own material under the detail: colour (sRGB, 0..1), roughness and metalness. */
export interface Look {
  color: RGB;
  rough: number;
  metal: number;
}

interface Texel extends Look {
  /** Height, 0 (deepest) to 1 (proud): the bumps are worked out from it. */
  h: number;
}

interface Spec {
  /** Tile width and height in pixels. */
  px: number;
  /** Tiles across the part's longest side at density 1. */
  across: number;
  /** How tall a tile is next to its width on the part (0.25: four rows to a tile's width). */
  aspect: number;
  /** How strongly the height turns the light. */
  bump: number;
  texel(u: number, v: number, base: Look): Texel;
}

const GOLD: RGB = [0.86, 0.69, 0.33];
const DARK: RGB = [0.045, 0.05, 0.06];
const fract = (x: number) => x - Math.floor(x);
const clamp = (x: number, lo = 0, hi = 1) => Math.max(lo, Math.min(hi, x));
const lerp = (a: number, b: number, t: number) => a + (b - a) * t;
const mix = (a: RGB, b: RGB, t: number): RGB => [lerp(a[0], b[0], t), lerp(a[1], b[1], t), lerp(a[2], b[2], t)];
const shade = (c: RGB, k: number): RGB => [clamp(c[0] * k), clamp(c[1] * k), clamp(c[2] * k)];
const smooth = (lo: number, hi: number, x: number) => {
  const t = clamp((x - lo) / (hi - lo));
  return t * t * (3 - 2 * t);
};
const hash = (n: number) => fract(Math.sin(n * 127.1 + 311.7) * 43758.5453);
/** 0 at a whole number, 1 half way between two. */
const tri = (x: number) => 1 - Math.abs(2 * fract(x) - 1);
/** 1 near the edge of a repeating cell, 0 inside it. */
const rim = (f: number) => 1 - smooth(0.07, 0.19, Math.min(fract(f), 1 - fract(f)));

/** Die blocks shimmer in different colours, the way thin films on silicon do. */
const FILMS: RGB[] = [[0.36, 0.44, 0.8], [0.2, 0.62, 0.64], [0.76, 0.58, 0.32], [0.58, 0.36, 0.68]];

// Traces: twelve lanes that run along the tile and step two lanes over and back, together, like
// a bus turning a corner. Some lanes are empty, some stop short at a plated hole.
const LANES = 12;
const EMPTY = new Set([3, 8]);
const SHORT = new Map<number, [number, number]>([[1, [0.12, 0.7]], [6, [0.3, 0.95]], [10, [0.05, 0.5]]]);
function laneAt(k: number, u: number): { v: number; slope: boolean } {
  const rise = 0.08 + (LANES - 1 - k) * 0.0345; // upper lanes turn first, so none cross
  const fall = 0.5 + k * 0.0345;
  const up = clamp((u - rise) * LANES * 0.5);
  const down = clamp((u - fall) * LANES * 0.5);
  return { v: (k + 0.5) / LANES + (2 / LANES) * (up - down), slope: (up > 0 && up < 1) || (down > 0 && down < 1) };
}
const wrapDist = (a: number, b: number) => Math.abs(fract(a - b + 0.5) - 0.5);

// Panel seams: two rows of big plates with one joint each, staggered. Few and fine: a casing
// has a handful of seams, and more than that reads as brickwork.
const SEAMS: [number, number[]][] = [[0.04, [0.38]], [0.55, [0.8]]];

const SPECS: Record<Surface, Spec> = {
  pins: {
    px: 128, across: 14, aspect: 1, bump: 5,
    texel(u, v, base) {
      const d = Math.hypot(u - 0.5, v - 0.5);
      // On a board the pins are gold; on bare metal they are that metal, in a dark bed.
      const metal = base.metal > 0.5;
      const pin = metal ? shade(base.color, 1.12) : GOLD;
      const bed: Look = metal ? { color: DARK, rough: 0.8, metal: 0 } : base;
      const on = 1 - smooth(0.27, 0.31, d);
      const shadow = 0.7 + 0.3 * smooth(0.3, 0.46, d);
      return {
        h: d < 0.31 ? Math.sqrt(Math.max(0, 1 - (d / 0.31) ** 2)) : 0,
        color: mix(shade(bed.color, shadow), pin, on),
        rough: lerp(bed.rough, 0.28, on),
        metal: lerp(bed.metal, 1, on),
      };
    },
  },
  cells: {
    px: 256, across: 2, aspect: 1, bump: 1.3,
    texel(u, v, base) {
      const bu = u * 2, bv = v * 2;
      const k = (Math.floor(bu) % 2) + 2 * (Math.floor(bv) % 2);
      const x = fract(bu), y = fract(bv);
      const inside = smooth(0.03, 0.055, Math.min(x, 1 - x, y, 1 - y)); // 0 in the lane between blocks
      const line =
        k === 0 ? Math.max(rim(x * 6), rim(y * 6)) // logic: a fine grid
        : k === 1 ? Math.max(rim(x * 16), rim(y * 2) * 0.8) // memory: tight columns
        : k === 2 ? Math.max(rim(x * 3), rim(y * 3), rim(y * 18) * 0.45) // cores
        : Math.max(rim(y * 12), rim(x * 3) * 0.8); // cache: rows
      const block = shade(mix(base.color, FILMS[k], 0.4), 1 - 0.5 * line);
      return {
        h: inside * (1 - 0.6 * line),
        color: mix(shade(base.color, 0.4), block, inside),
        rough: lerp(0.5, 0.2 + 0.2 * line, inside),
        metal: lerp(0.2, 0.75, inside),
      };
    },
  },
  traces: {
    px: 256, across: 3, aspect: 1, bump: 1.4,
    texel(u, v, base) {
      let copper = 0, pad = 0, hole = 0;
      for (let k = 0; k < LANES; k++) {
        if (EMPTY.has(k)) continue;
        const span = SHORT.get(k);
        const lane = laneAt(k, u);
        const width = (lane.slope ? 0.26 : 0.18) / LANES;
        if (!span || (u >= span[0] && u <= span[1])) copper = Math.max(copper, 1 - smooth(width, width + 0.006, wrapDist(v, lane.v)));
        for (const end of span ?? []) {
          const d = Math.hypot(u - end, wrapDist(v, laneAt(k, end).v));
          pad = Math.max(pad, 1 - smooth(0.024, 0.03, d));
          hole = Math.max(hole, 1 - smooth(0.009, 0.013, d));
        }
      }
      // Copper under the solder mask is the board's own colour, lighter; a plated hole is bare.
      let color = mix(base.color, mix(base.color, [1, 1, 0.82], 0.24), copper);
      color = mix(mix(color, GOLD, pad), DARK, hole);
      return {
        h: Math.max(copper * 0.6, pad) * (1 - hole),
        color,
        rough: lerp(lerp(base.rough, Math.max(0.2, base.rough - 0.1), copper), 0.3, pad),
        metal: lerp(base.metal, 1, pad * (1 - hole)),
      };
    },
  },
  fins: {
    px: 128, across: 22, aspect: 1, bump: 4,
    texel(u, _v, base) {
      const top = smooth(0, 0.09, u) * (1 - smooth(0.5, 0.59, u));
      return { h: top, color: shade(base.color, 0.2 + 0.8 * top), rough: lerp(Math.min(1, base.rough + 0.3), base.rough, top), metal: base.metal };
    },
  },
  brushed: {
    px: 128, across: 4, aspect: 1, bump: 0.3,
    texel(u, v, base) {
      // Hair-fine streaks along the part: each row its own brightness, drifting slowly along
      // it. Faint: brushed metal is a sheen that stretches the highlights, not stripes.
      const row = Math.floor(v * 128);
      const at = u * 4, i = Math.floor(at) % 4;
      const n = lerp(hash(row * 9 + i), hash(row * 9 + ((i + 1) % 4)), smooth(0, 1, fract(at)));
      return { h: n, color: shade(base.color, 0.95 + 0.1 * n), rough: clamp(base.rough + 0.06 + 0.16 * (n - 0.5), 0.05, 1), metal: base.metal };
    },
  },
  vents: {
    px: 128, across: 5, aspect: 0.25, bump: 3,
    texel(u, v, base) {
      const x = (u - 0.5) * 4, y = v - 0.5;
      const d = Math.hypot(x - clamp(x, -1.45, 1.45), y); // a slot with round ends
      const open = 1 - smooth(0.24, 0.3, d);
      return {
        h: 1 - open,
        color: mix(shade(base.color, 0.8 + 0.2 * smooth(0.3, 0.45, d)), DARK, open),
        rough: lerp(base.rough, 0.9, open),
        metal: lerp(base.metal, 0, open),
      };
    },
  },
  grille: {
    px: 128, across: 26, aspect: 1.732, bump: 3,
    texel(u, v, base) {
      // Holes in a honeycomb: one in the middle of the tile's lower half, one on each side above.
      let d = Infinity;
      for (const [cu, cv] of [[0.5, 0.25], [0, 0.75], [1, 0.75], [0, -0.25], [1, -0.25], [0.5, 1.25]]) d = Math.min(d, Math.hypot(u - cu, (v - cv) * 1.732));
      const open = 1 - smooth(0.31, 0.37, d);
      return { h: 1 - open, color: mix(base.color, DARK, open), rough: lerp(base.rough, 0.9, open), metal: lerp(base.metal, 0, open) };
    },
  },
  knurl: {
    px: 128, across: 30, aspect: 1, bump: 16, // long gentle slopes need a strong hand
    texel(u, v, base) {
      const h = Math.min(tri(u + v), tri(u - v)); // little pyramids between crossed grooves
      return { h, color: shade(base.color, 0.55 + 0.45 * h), rough: base.rough, metal: base.metal };
    },
  },
  panel_lines: {
    px: 256, across: 1, aspect: 1, bump: 2.6,
    texel(u, v, base) {
      let seam = 0, rivet = 0;
      SEAMS.forEach(([at, joints], i) => {
        seam = Math.max(seam, 1 - smooth(0.003, 0.006, wrapDist(v, at)));
        const next = SEAMS[(i + 1) % SEAMS.length][0] + (i === SEAMS.length - 1 ? 1 : 0);
        const inRow = fract(v - at) < next - at;
        if (inRow) for (const j of joints) seam = Math.max(seam, 1 - smooth(0.003, 0.006, wrapDist(u, j)));
        // A row of small fasteners just inside each plate.
        rivet = Math.max(rivet, 1 - smooth(0.005, 0.008, Math.hypot(wrapDist(u * 6, 0.5) / 6, wrapDist(v, at + 0.03))));
      });
      return {
        h: (1 - seam) * (0.8 + 0.2 * rivet),
        color: shade(base.color, (1 - 0.55 * seam) * (1 - 0.12 * rivet)),
        rough: clamp(base.rough + 0.2 * seam),
        metal: base.metal,
      };
    },
  },
};

/** How a kind is laid on a part: tiles across its longest side, and a tile's height to width. */
export function surfaceLayout(kind: Surface): { across: number; aspect: number } {
  return { across: SPECS[kind].across, aspect: SPECS[kind].aspect };
}

export interface Tile {
  px: number;
  /** RGBA, sRGB colour. */
  color: Uint8Array;
  /** RGBA, a tangent-space normal map. */
  normal: Uint8Array;
  /** RGBA: green is roughness and blue is metalness, as three.js reads them. */
  shine: Uint8Array;
}

const byte = (x: number) => Math.round(clamp(x) * 255);

/** One repeating tile of a surface, for a part whose own material is `base`. */
export function surfaceTile(kind: Surface, base: Look): Tile {
  const spec = SPECS[kind];
  const n = spec.px;
  const color = new Uint8Array(n * n * 4), normal = new Uint8Array(n * n * 4), shine = new Uint8Array(n * n * 4);
  const height = new Float32Array(n * n);
  for (let y = 0; y < n; y++) {
    for (let x = 0; x < n; x++) {
      const t = spec.texel((x + 0.5) / n, (y + 0.5) / n, base);
      const i = (y * n + x) * 4;
      height[y * n + x] = t.h;
      color[i] = byte(t.color[0]); color[i + 1] = byte(t.color[1]); color[i + 2] = byte(t.color[2]); color[i + 3] = 255;
      shine[i] = 255; shine[i + 1] = byte(t.rough); shine[i + 2] = byte(t.metal); shine[i + 3] = 255;
    }
  }
  // The same slope whatever the tile's resolution; the tile repeats, so its edges wrap.
  const k = spec.bump * (n / 128) * 0.5;
  for (let y = 0; y < n; y++) {
    for (let x = 0; x < n; x++) {
      const at = (xx: number, yy: number) => height[((yy + n) % n) * n + ((xx + n) % n)];
      const dx = (at(x + 1, y) - at(x - 1, y)) * k, dy = (at(x, y + 1) - at(x, y - 1)) * k;
      const len = Math.hypot(dx, dy, 1);
      const i = (y * n + x) * 4;
      normal[i] = byte(0.5 - (0.5 * dx) / len); normal[i + 1] = byte(0.5 - (0.5 * dy) / len); normal[i + 2] = byte(0.5 + 0.5 / len); normal[i + 3] = 255;
    }
  }
  return { px: n, color, normal, shine };
}

/**
 * Lay a surface on any shape: each face is mapped flat along the two directions it mostly lies
 * in (a box gets its six sides, a cylinder its ends and wall), in real units, so the detail is
 * the same size on every side and its long direction follows the part's long side. Returns a new
 * geometry (the faces no longer share corners, so the pattern never smears across an edge).
 *
 * `scale` is the mesh's scale (unit shapes are stretched to the part's box) and `size` the
 * part's box.
 */
export function surfaceUVs(source: THREE.BufferGeometry, scale: [number, number, number], size: [number, number, number], kind: Surface, density = 1): THREE.BufferGeometry {
  const g = source.index ? source.toNonIndexed() : source.clone();
  const pos = g.getAttribute("position");
  const uv = new Float32Array(pos.count * 2);
  const { across, aspect } = surfaceLayout(kind);
  const period = Math.max(1e-6, Math.max(...size)) / (across * clamp(density || 1, 0.25, 4));
  const p = [new THREE.Vector3(), new THREE.Vector3(), new THREE.Vector3()];
  const n = new THREE.Vector3(), e = new THREE.Vector3();
  const s = new THREE.Vector3(...scale);
  for (let i = 0; i + 2 < pos.count; i += 3) {
    for (let c = 0; c < 3; c++) p[c].fromBufferAttribute(pos, i + c).multiply(s);
    n.subVectors(p[1], p[0]).cross(e.subVectors(p[2], p[0]));
    const a = [Math.abs(n.x), Math.abs(n.y), Math.abs(n.z)];
    const facing = a[0] >= a[1] && a[0] >= a[2] ? 0 : a[1] >= a[2] ? 1 : 2;
    let [along, cross] = [0, 1, 2].filter((d) => d !== facing);
    if (size[cross] > size[along]) [along, cross] = [cross, along]; // the pattern runs the long way
    for (let c = 0; c < 3; c++) {
      uv[(i + c) * 2] = p[c].getComponent(along) / period + 0.5;
      uv[(i + c) * 2 + 1] = p[c].getComponent(cross) / (period * aspect) + 0.5;
    }
  }
  g.setAttribute("uv", new THREE.BufferAttribute(uv, 2));
  return g;
}

interface Maps {
  map: THREE.DataTexture;
  normalMap: THREE.DataTexture;
  shine: THREE.DataTexture;
}

const made = new Map<string, Maps>();
const KEEP = 40; // tiles are tiny, but a long session of many models should not grow for ever

function texture(data: Uint8Array, px: number, srgb: boolean): THREE.DataTexture {
  const t = new THREE.DataTexture(data, px, px, THREE.RGBAFormat, THREE.UnsignedByteType);
  t.wrapS = t.wrapT = THREE.RepeatWrapping;
  t.magFilter = THREE.LinearFilter;
  t.minFilter = THREE.LinearMipmapLinearFilter;
  t.generateMipmaps = true;
  t.anisotropy = 4;
  if (srgb) t.colorSpace = THREE.SRGBColorSpace;
  t.needsUpdate = true;
  return t;
}

/** Draw a surface with a material: its colour, bumps and shine now come from the tile. */
export function applySurface(m: THREE.MeshStandardMaterial, kind: Surface, base: Look): void {
  const key = `${kind}|${base.color.map(byte).join(",")}|${byte(base.rough)}|${byte(base.metal)}`;
  let maps = made.get(key);
  if (!maps) {
    const tile = surfaceTile(kind, base);
    maps = { map: texture(tile.color, tile.px, true), normalMap: texture(tile.normal, tile.px, false), shine: texture(tile.shine, tile.px, false) };
    made.set(key, maps);
    if (made.size > KEEP) {
      // The oldest goes; a material still using it just has it sent to the GPU again.
      const [old, gone] = made.entries().next().value as [string, Maps];
      made.delete(old);
      gone.map.dispose(); gone.normalMap.dispose(); gone.shine.dispose();
    }
  }
  m.map = maps.map;
  m.normalMap = maps.normalMap;
  m.roughnessMap = maps.shine;
  m.metalnessMap = maps.shine;
  // The tile carries the part's colour, roughness and metalness: the material only passes them on.
  m.color.set("#ffffff");
  m.roughness = 1;
  m.metalness = 1;
}
