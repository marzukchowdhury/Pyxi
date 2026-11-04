/**
 * Object-scene motion, as pure functions: given the scene and two clocks, where is every part?
 *
 * Two clocks keep "video" timing and "machine" timing apart:
 *   story  advances with the narrated beats and waits while a sentence is still being spoken.
 *          Drives one-off keyframes (the canopy flying off) and anything with a start/end.
 *   free   always runs while playing. Drives open-ended spins, oscillations, pulses and looped
 *          keyframes, so an engine never freezes while Wisp finishes a sentence.
 */
import type { Axis, Key, Motion, ObjectScene, Part, Vec3 } from "../shared/protocol";

export interface PartState {
  position: Vec3;
  rotation: Vec3; // radians, XYZ order
  scale: Vec3;
  opacity: number;
  glow: number; // extra emissive from pulse motions, 0..1
}

const DEG = Math.PI / 180;
const AXIS: Record<Axis, 0 | 1 | 2> = { x: 0, y: 1, z: 2 };

export const EASE: Record<Motion["ease"], (u: number) => number> = {
  linear: (u) => u,
  in: (u) => u * u * u,
  out: (u) => 1 - Math.pow(1 - u, 3),
  in_out: (u) => (u < 0.5 ? 4 * u * u * u : 1 - Math.pow(-2 * u + 2, 3) / 2),
  spring: (u) => 1 - Math.cos(u * Math.PI * 4.5) * Math.exp(-u * 6),
};

/** Uses the free clock unless the motion is pinned to the story (has a start or an end). */
export function usesStory(m: Motion): boolean {
  if (m.kind === "keyframes") return !m.loop;
  return m.end != null || m.start > 0;
}

function catmull(p0: number, p1: number, p2: number, p3: number, u: number): number {
  const u2 = u * u;
  const u3 = u2 * u;
  return 0.5 * (2 * p1 + (-p0 + p2) * u + (2 * p0 - 5 * p1 + 4 * p2 - p3) * u2 + (-p0 + 3 * p1 - 3 * p2 + p3) * u3);
}

type VecProp = "position" | "rotation" | "scale";

/** Value of one property along the keys at time t (held before the first and after the last). */
export function sampleVec(keys: Key[], prop: VecProp, t: number, ease: (u: number) => number, smooth: boolean): Vec3 | null {
  const ks = keys.filter((k) => k[prop] != null);
  if (!ks.length) return null;
  if (t <= ks[0].t) return [...(ks[0][prop] as Vec3)];
  const last = ks[ks.length - 1];
  if (t >= last.t) return [...(last[prop] as Vec3)];
  let i = 0;
  while (i < ks.length - 2 && t > ks[i + 1].t) i++;
  const a = ks[i];
  const b = ks[i + 1];
  const u = ease((t - a.t) / Math.max(1e-6, b.t - a.t));
  const va = a[prop] as Vec3;
  const vb = b[prop] as Vec3;
  if (!smooth || ks.length < 3) return [0, 1, 2].map((j) => va[j] + (vb[j] - va[j]) * u) as Vec3;
  // Paths through 3+ points curve smoothly through each one instead of kinking at the keys.
  const v0 = (ks[i - 1] ?? a)[prop] as Vec3;
  const v3 = (ks[i + 2] ?? b)[prop] as Vec3;
  return [0, 1, 2].map((j) => catmull(v0[j], va[j], vb[j], v3[j], u)) as Vec3;
}

export function sampleNum(keys: Key[], t: number, ease: (u: number) => number): number | null {
  const ks = keys.filter((k) => k.opacity != null);
  if (!ks.length) return null;
  if (t <= ks[0].t) return ks[0].opacity as number;
  const last = ks[ks.length - 1];
  if (t >= last.t) return last.opacity as number;
  let i = 0;
  while (i < ks.length - 2 && t > ks[i + 1].t) i++;
  const a = ks[i];
  const b = ks[i + 1];
  const u = ease((t - a.t) / Math.max(1e-6, b.t - a.t));
  return (a.opacity as number) + ((b.opacity as number) - (a.opacity as number)) * u;
}

/** Wrap t into a looping key range. */
export function loopTime(keys: Key[], t: number): number {
  if (keys.length < 2) return t;
  const t0 = keys[0].t;
  const span = keys[keys.length - 1].t - t0;
  return span > 0 ? t0 + (((t - t0) % span) + span) % span : t;
}

export function baseState(p: Part): PartState {
  return {
    position: [...p.position],
    rotation: p.rotation.map((d) => d * DEG) as Vec3,
    scale: [1, 1, 1],
    opacity: p.hidden ? 0 : p.opacity,
    glow: 0,
  };
}

/** Every part's local transform at (story, free) seconds. */
export function evaluate(scene: ObjectScene, story: number, free: number): Map<string, PartState> {
  const states = new Map<string, PartState>();
  for (const p of scene.parts) states.set(p.id, baseState(p));
  const hidden = new Set(scene.parts.filter((p) => p.hidden).map((p) => p.id));
  // Keyframes first (they replace values), then additive motions on top.
  const ordered = [...scene.motions].sort((a, b) => Number(a.kind !== "keyframes") - Number(b.kind !== "keyframes"));
  for (const m of ordered) {
    const s = states.get(m.part);
    if (!s) continue;
    const clock = usesStory(m) ? story : free;
    if (m.kind === "keyframes") {
      const t = m.loop ? loopTime(m.keys, clock) : clock;
      const ease = EASE[m.ease] ?? EASE.in_out;
      const pos = sampleVec(m.keys, "position", t, ease, true);
      const rot = sampleVec(m.keys, "rotation", t, ease, false);
      const scl = sampleVec(m.keys, "scale", t, ease, false);
      const op = sampleNum(m.keys, t, ease);
      if (pos) s.position = pos;
      if (rot) s.rotation = rot.map((d) => d * DEG) as Vec3;
      if (scl) s.scale = scl;
      if (op != null && !hidden.has(m.part)) s.opacity = op;
      continue;
    }
    if (clock < m.start || (m.end != null && clock > m.end)) {
      // A spin that has ended holds its final angle; oscillations and pulses rest at zero.
      if (m.kind === "spin" && m.end != null && clock > m.end) s.rotation[AXIS[m.axis]] += 2 * Math.PI * (m.speed * (m.end - m.start) + m.phase);
      continue;
    }
    const local = clock - m.start;
    const ax = AXIS[m.axis];
    if (m.kind === "spin") {
      s.rotation[ax] += 2 * Math.PI * (m.speed * local + m.phase);
    } else if (m.kind === "oscillate") {
      const d = m.amount * Math.sin(2 * Math.PI * (local / m.period + m.phase));
      if (m.prop === "position") s.position[ax] += d;
      else if (m.prop === "rotation") s.rotation[ax] += d * DEG;
      else s.scale[ax] *= 1 + d;
    } else if (m.kind === "pulse") {
      const w = 0.5 + 0.5 * Math.sin(2 * Math.PI * (local / m.period + m.phase));
      s.glow = Math.max(s.glow, w);
      const k = 1 + 0.05 * w;
      s.scale = [s.scale[0] * k, s.scale[1] * k, s.scale[2] * k];
    }
  }
  return states;
}

/** Last moment anything on the story clock changes. */
export function storyDuration(scene: ObjectScene): number {
  let end = 0;
  for (const m of scene.motions) {
    if (!usesStory(m)) continue;
    if (m.kind === "keyframes") end = Math.max(end, ...m.keys.map((k) => k.t));
    else end = Math.max(end, m.end ?? m.start);
  }
  for (const b of scene.beats) end = Math.max(end, b.at);
  return end;
}

/** Parts sorted so every parent comes before its children. */
export function children(scene: ObjectScene): Map<string | null, Part[]> {
  const kids = new Map<string | null, Part[]>();
  for (const p of scene.parts) {
    const k = p.parent ?? null;
    kids.set(k, [...(kids.get(k) ?? []), p]);
  }
  return kids;
}
