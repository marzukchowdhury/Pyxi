/**
 * Where is every part at a given moment? The same rules as Pyxi's renderer
 * (desktop/src/renderer/src/scene/motion.ts and camera.ts), as plain functions.
 *
 * Two clocks keep "video" timing and "machine" timing apart:
 *   story  advances with the narrated steps and waits while a step is still being read.
 *          Drives one-off keyframes (the canopy flying off) and anything with a start or an end.
 *   free   always runs while playing. Drives open-ended spins, oscillations, pulses and looped
 *          keyframes, so an engine never freezes between steps.
 */
const DEG = Math.PI / 180;
const AXIS = { x: 0, y: 1, z: 2 };

export const EASE = {
  linear: (u) => u,
  in: (u) => u * u * u,
  out: (u) => 1 - Math.pow(1 - u, 3),
  in_out: (u) => (u < 0.5 ? 4 * u * u * u : 1 - Math.pow(-2 * u + 2, 3) / 2),
  spring: (u) => 1 - Math.cos(u * Math.PI * 4.5) * Math.exp(-u * 6),
};

/** Fill in everything the app's own defaults would have filled in. */
export function normalise(scene) {
  return {
    style: "studio",
    cutaway: false,
    ...scene,
    parts: scene.parts.map((p) => ({
      shape: "box",
      size: [1, 1, 1],
      axis: "y",
      position: [0, 0, 0],
      rotation: [0, 0, 0],
      material: "plastic",
      opacity: 1,
      ...p,
    })),
    motions: (scene.motions ?? []).map((m) => ({
      kind: "keyframes",
      keys: [],
      axis: "y",
      prop: "position",
      amount: 0.3,
      speed: 0.5,
      period: 1.2,
      phase: 0,
      loop: false,
      start: 0,
      end: null,
      ease: "in_out",
      ...m,
    })),
    beats: (scene.beats ?? []).map((b) => ({
      at: 0,
      focus: [],
      camera: "iso",
      cutaway: null,
      ...b,
    })),
  };
}

/** Uses the free clock unless the motion is pinned to the story (has a start or an end). */
export function usesStory(m) {
  if (m.kind === "keyframes") return !m.loop;
  return m.end != null || m.start > 0;
}

function catmull(p0, p1, p2, p3, u) {
  const u2 = u * u;
  const u3 = u2 * u;
  return (
    0.5 *
    (2 * p1 +
      (-p0 + p2) * u +
      (2 * p0 - 5 * p1 + 4 * p2 - p3) * u2 +
      (-p0 + 3 * p1 - 3 * p2 + p3) * u3)
  );
}
function span(ks, t) {
  let i = 0;
  while (i < ks.length - 2 && t > ks[i + 1].t) i++;
  return i;
}
function sampleVec(keys, prop, t, ease, smooth) {
  const ks = keys.filter((k) => k[prop] != null);
  if (!ks.length) return null;
  if (t <= ks[0].t) return [...ks[0][prop]];
  const last = ks[ks.length - 1];
  if (t >= last.t) return [...last[prop]];
  const i = span(ks, t);
  const a = ks[i];
  const b = ks[i + 1];
  const u = ease((t - a.t) / Math.max(1e-6, b.t - a.t));
  const va = a[prop];
  const vb = b[prop];
  if (!smooth || ks.length < 3) return [0, 1, 2].map((j) => va[j] + (vb[j] - va[j]) * u);
  // paths through 3+ points curve smoothly through each one instead of kinking at the keys
  const v0 = (ks[i - 1] ?? a)[prop];
  const v3 = (ks[i + 2] ?? b)[prop];
  return [0, 1, 2].map((j) => catmull(v0[j], va[j], vb[j], v3[j], u));
}
function sampleNum(keys, t, ease) {
  const ks = keys.filter((k) => k.opacity != null);
  if (!ks.length) return null;
  if (t <= ks[0].t) return ks[0].opacity;
  const last = ks[ks.length - 1];
  if (t >= last.t) return last.opacity;
  const i = span(ks, t);
  const a = ks[i];
  const b = ks[i + 1];
  const u = ease((t - a.t) / Math.max(1e-6, b.t - a.t));
  return a.opacity + (b.opacity - a.opacity) * u;
}
function loopTime(keys, t) {
  if (keys.length < 2) return t;
  const t0 = keys[0].t;
  const len = keys[keys.length - 1].t - t0;
  return len > 0 ? t0 + ((((t - t0) % len) + len) % len) : t;
}

/** Every part's local transform at (story, free) seconds: id -> {position, rotation, scale, opacity, glow}. */
export function evaluate(scene, story, free) {
  const states = new Map();
  for (const p of scene.parts)
    states.set(p.id, {
      position: [...p.position],
      rotation: p.rotation.map((d) => d * DEG),
      scale: [1, 1, 1],
      opacity: p.hidden ? 0 : p.opacity,
      glow: 0,
    });
  const hidden = new Set(scene.parts.filter((p) => p.hidden).map((p) => p.id));
  // keyframes first (they replace values), then additive motions on top
  const ordered = [...scene.motions].sort(
    (a, b) => Number(a.kind !== "keyframes") - Number(b.kind !== "keyframes"),
  );
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
      if (rot) s.rotation = rot.map((d) => d * DEG);
      if (scl) s.scale = scl;
      if (op != null && !hidden.has(m.part)) s.opacity = op;
      continue;
    }
    if (clock < m.start || (m.end != null && clock > m.end)) {
      // a spin that has ended holds its final angle; oscillations and pulses rest at zero
      if (m.kind === "spin" && m.end != null && clock > m.end)
        s.rotation[AXIS[m.axis]] += 2 * Math.PI * (m.speed * (m.end - m.start) + m.phase);
      continue;
    }
    const local = clock - m.start;
    const ax = AXIS[m.axis];
    if (m.kind === "spin") s.rotation[ax] += 2 * Math.PI * (m.speed * local + m.phase);
    else if (m.kind === "oscillate") {
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
export function storyDuration(scene) {
  let end = 0;
  for (const m of scene.motions) {
    if (!usesStory(m)) continue;
    if (m.kind === "keyframes") end = Math.max(end, ...m.keys.map((k) => k.t));
    else end = Math.max(end, m.end ?? m.start);
  }
  for (const b of scene.beats) end = Math.max(end, b.at);
  return end;
}

// ---- the camera: it orbits its subject on critically damped springs, never a straight line
/** One critically damped spring step ("smooth damp"). `velocity` is { v }. */
export function smoothDamp(current, target, velocity, smoothTime, dt) {
  const omega = 2 / Math.max(1e-4, smoothTime);
  const x = omega * dt;
  const exp = 1 / (1 + x + 0.48 * x * x + 0.235 * x * x * x);
  const change = current - target;
  const temp = (velocity.v + omega * change) * dt;
  velocity.v = (velocity.v - omega * temp) * exp;
  let out = target + (change + temp) * exp;
  if (target - current > 0 === out > target) {
    out = target; // never overshoot
    velocity.v = 0;
  }
  return out;
}
function nearestAngle(from, to) {
  const TAU = Math.PI * 2;
  let d = (to - from) % TAU;
  if (d > Math.PI) d -= TAU;
  if (d < -Math.PI) d += TAU;
  return from + d;
}
/** Offset from the target to orbit coordinates: theta round the vertical, phi from straight up. */
export function toOrbit(x, y, z) {
  const radius = Math.max(1e-6, Math.hypot(x, y, z));
  return {
    radius,
    theta: Math.atan2(x, z),
    phi: Math.acos(Math.min(1, Math.max(-1, y / radius))),
  };
}
export function fromOrbit(o) {
  const s = Math.sin(o.phi) * o.radius;
  return [s * Math.sin(o.theta), Math.cos(o.phi) * o.radius, s * Math.cos(o.theta)];
}
export const orbitSpring = (o) => ({ orbit: { ...o }, vr: { v: 0 }, vt: { v: 0 }, vp: { v: 0 } });
/** Moves the spring one frame toward `goal`: swing round first, then settle in. */
export function stepOrbit(s, goal, smoothTime, dt) {
  const POLE = 0.06;
  const o = s.orbit;
  o.theta = smoothDamp(o.theta, nearestAngle(o.theta, goal.theta), s.vt, smoothTime, dt);
  o.phi = smoothDamp(
    o.phi,
    Math.min(Math.PI - POLE, Math.max(POLE, goal.phi)),
    s.vp,
    smoothTime,
    dt,
  );
  // distance in log space: going from 4 to 40 units feels as even as 40 to 4
  o.radius = Math.exp(
    smoothDamp(
      Math.log(o.radius),
      Math.log(Math.max(1e-4, goal.radius)),
      s.vr,
      smoothTime * 1.15,
      dt,
    ),
  );
  return o;
}
