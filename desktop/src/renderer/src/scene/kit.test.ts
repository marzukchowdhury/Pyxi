import { describe, expect, it } from "vitest";
import * as THREE from "three";
import { buildKit, kitAxis } from "./kit";

describe("kit pieces stand the right way whatever axis the builder wrote", () => {
  it("a car wheel sized thin across the car turns about that side", () => {
    // What the builder wrote for the BMW: a wheel left on the default axis.
    expect(kitAxis("wheel", [1.6, 1.6, 0.5], "y")).toBe("z");
    expect(kitAxis("wheel", [0.5, 1.6, 1.6], "y")).toBe("x");
  });

  it("a missile lies along its longest side", () => {
    expect(kitAxis("missile", [2.4, 0.36, 0.36], "y")).toBe("x");
  });

  it("an even box keeps the axis it was given", () => {
    expect(kitAxis("wheel", [1, 1, 1], "x")).toBe("x");
    expect(kitAxis("chip", [1, 0.1, 1], "y")).toBe("y");
  });

  it("the built wheel is round in the face the builder sized round", () => {
    const layers = buildKit("wheel", [1.6, 1.6, 0.5], "y")!;
    const tyre = layers[0].geometry;
    tyre.computeBoundingBox();
    const s = tyre.boundingBox!.getSize(new THREE.Vector3());
    expect(s.x).toBeCloseTo(1.6, 1);
    expect(s.y).toBeCloseTo(1.6, 1);
    expect(s.z).toBeLessThan(0.6);
  });
});
