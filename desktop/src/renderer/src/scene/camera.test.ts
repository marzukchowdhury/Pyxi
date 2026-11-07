import { describe, expect, it } from "vitest";
import { fromOrbit, nearestAngle, orbitSpring, smoothDamp, stepOrbit, toOrbit } from "./camera";

describe("camera moves", () => {
  it("smoothDamp reaches the target without overshooting", () => {
    const v = { v: 0 };
    let x = 0;
    let max = 0;
    for (let i = 0; i < 240; i++) {
      x = smoothDamp(x, 10, v, 0.8, 1 / 60);
      max = Math.max(max, x);
    }
    expect(x).toBeGreaterThan(9.95);
    expect(max).toBeLessThanOrEqual(10);
  });

  it("starts gently: the first frame moves less than a plain lerp would", () => {
    const x = smoothDamp(0, 10, { v: 0 }, 0.8, 1 / 60);
    expect(x).toBeGreaterThan(0);
    expect(x).toBeLessThan(10 * (1 - Math.exp(-2.4 / 60)));
  });

  it("turns the short way round", () => {
    expect(nearestAngle(3, -3)).toBeCloseTo(-3 + Math.PI * 2);
    expect(nearestAngle(-3, 3)).toBeCloseTo(3 - Math.PI * 2);
    expect(nearestAngle(0, 1)).toBeCloseTo(1);
  });

  it("orbit coordinates round-trip", () => {
    const [x, y, z] = fromOrbit(toOrbit(1.2, 0.8, -2.5));
    expect(x).toBeCloseTo(1.2);
    expect(y).toBeCloseTo(0.8);
    expect(z).toBeCloseTo(-2.5);
  });

  it("swings round the subject instead of cutting through it", () => {
    // From a side view to the opposite side: a straight line passes through the target.
    const s = orbitSpring(toOrbit(10, 0, 0.01));
    const goal = toOrbit(-10, 0, 0.01);
    let closest = Infinity;
    for (let i = 0; i < 300; i++) {
      const o = stepOrbit(s, goal, 0.9, 1 / 60);
      closest = Math.min(closest, o.radius);
    }
    expect(closest).toBeGreaterThan(9.9);
    const [x] = fromOrbit(s.orbit);
    expect(x).toBeLessThan(-9.8);
  });
});
