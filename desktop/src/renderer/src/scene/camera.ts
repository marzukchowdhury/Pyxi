/**
 * Camera moves between beats. The camera orbits its subject rather than gliding in a straight
 * line (a straight chord from a side view to a top view cuts through the model and swings the
 * horizon); each coordinate follows a critically damped spring, so a move starts gently, never
 * overshoots and settles without the last-moment creep of a plain lerp.
 */

/** One critically damped spring step (Game Programming Gems 4, "smooth damp"). */
export function smoothDamp(current: number, target: number, velocity: { v: number }, smoothTime: number, dt: number): number {
  const st = Math.max(1e-4, smoothTime);
  const omega = 2 / st;
  const x = omega * dt;
  const exp = 1 / (1 + x + 0.48 * x * x + 0.235 * x * x * x);
  const change = current - target;
  const temp = (velocity.v + omega * change) * dt;
  velocity.v = (velocity.v - omega * temp) * exp;
  let out = target + (change + temp) * exp;
  // Never overshoot the target.
  if (target - current > 0 === out > target) {
    out = target;
    velocity.v = (out - target) / Math.max(dt, 1e-6);
  }
  return out;
}

/** The same angle as `to`, unwrapped to be the shortest turn away from `from`. */
export function nearestAngle(from: number, to: number): number {
  const TAU = Math.PI * 2;
  let d = (to - from) % TAU;
  if (d > Math.PI) d -= TAU;
  if (d < -Math.PI) d += TAU;
  return from + d;
}

export interface Orbit {
  radius: number;
  /** Around the vertical axis, radians (0 = looking down -z from +z). */
  theta: number;
  /** From straight up, radians (0 = top, PI = below). */
  phi: number;
}

/** Offset from the target (camera minus target) to orbit coordinates. */
export function toOrbit(x: number, y: number, z: number): Orbit {
  const radius = Math.max(1e-6, Math.hypot(x, y, z));
  return { radius, theta: Math.atan2(x, z), phi: Math.acos(Math.min(1, Math.max(-1, y / radius))) };
}

export function fromOrbit(o: Orbit): [number, number, number] {
  const s = Math.sin(o.phi) * o.radius;
  return [s * Math.sin(o.theta), Math.cos(o.phi) * o.radius, s * Math.cos(o.theta)];
}

const POLE = 0.06; // keep clear of straight up/down, where the heading flips

export interface OrbitSpring {
  orbit: Orbit;
  vr: { v: number };
  vt: { v: number };
  vp: { v: number };
}

export function orbitSpring(o: Orbit): OrbitSpring {
  return { orbit: { ...o }, vr: { v: 0 }, vt: { v: 0 }, vp: { v: 0 } };
}

/** Moves the spring one frame toward `goal`. Distance eases on a slightly slower spring than
 * the angle, so a move reads as "swing round, then settle in" rather than a zoom. */
export function stepOrbit(s: OrbitSpring, goal: Orbit, smoothTime: number, dt: number): Orbit {
  const o = s.orbit;
  o.theta = smoothDamp(o.theta, nearestAngle(o.theta, goal.theta), s.vt, smoothTime, dt);
  o.phi = smoothDamp(o.phi, Math.min(Math.PI - POLE, Math.max(POLE, goal.phi)), s.vp, smoothTime, dt);
  // Distance in log space: going from 4 to 40 units feels as even as 40 to 4.
  const lr = { v: s.vr.v };
  const r = Math.exp(smoothDamp(Math.log(o.radius), Math.log(Math.max(1e-4, goal.radius)), lr, smoothTime * 1.15, dt));
  s.vr.v = lr.v;
  o.radius = r;
  return o;
}
