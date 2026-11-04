import { describe, expect, it } from "vitest";
import type { Motion, ObjectScene, Part } from "../shared/protocol";
import { evaluate, loopTime, sampleVec, storyDuration, usesStory } from "./motion";

const part = (id: string, extra: Partial<Part> = {}): Part => ({
  id, shape: "box", size: [1, 1, 1], axis: "y", position: [0, 0, 0], rotation: [0, 0, 0], parent: null,
  material: "plastic", color: null, opacity: 1, label: null, note: null, casing: false, hidden: false,
  taper: null, path: null, radius: null, profile: null, teeth: null, turns: null, ...extra,
});
const motion = (m: Partial<Motion>): Motion => ({
  part: "a", kind: "keyframes", keys: [], axis: "y", prop: "position", amount: 0.3, speed: 0.5, period: 1,
  phase: 0, loop: false, start: 0, end: null, ease: "linear", ...m,
});
const key = (t: number, extra = {}) => ({ t, position: null, rotation: null, scale: null, opacity: null, ...extra });
const scene = (motions: Motion[], parts = [part("a")]): ObjectScene => ({
  id: "s", title: "t", subtitle: null, style: "studio", parts, motions, beats: [{ title: "b", narration: "", at: 0, focus: [], camera: "iso", cutaway: null }],
  cutaway: false, auto_play: true,
});

describe("keyframes", () => {
  it("holds before the first key and after the last", () => {
    const keys = [key(1, { position: [0, 0, 0] }), key(2, { position: [10, 0, 0] })];
    expect(sampleVec(keys, "position", 0, (u) => u, false)).toEqual([0, 0, 0]);
    expect(sampleVec(keys, "position", 1.5, (u) => u, false)).toEqual([5, 0, 0]);
    expect(sampleVec(keys, "position", 9, (u) => u, false)).toEqual([10, 0, 0]);
  });

  it("passes exactly through every key on a curved path", () => {
    const keys = [key(0, { position: [0, 0, 0] }), key(1, { position: [1, 2, 0] }), key(2, { position: [3, 1, 0] })];
    const at1 = sampleVec(keys, "position", 1, (u) => u, true)!;
    expect(at1[0]).toBeCloseTo(1);
    expect(at1[1]).toBeCloseTo(2);
  });

  it("loops over the key range", () => {
    const keys = [key(0), key(2)];
    expect(loopTime(keys, 5)).toBeCloseTo(1);
  });

  it("fades opacity without unhiding hidden parts", () => {
    const fade = motion({ keys: [key(0, { opacity: 0 }), key(1, { opacity: 1 })] });
    expect(evaluate(scene([fade]), 0.5, 0).get("a")!.opacity).toBeCloseTo(0.5);
    expect(evaluate(scene([fade], [part("a", { hidden: true })]), 1, 0).get("a")!.opacity).toBe(0);
  });
});

describe("machine motions", () => {
  it("open-ended motions run on the free clock, bounded ones on the story", () => {
    expect(usesStory(motion({ kind: "spin" }))).toBe(false);
    expect(usesStory(motion({ kind: "spin", end: 4 }))).toBe(true);
    expect(usesStory(motion({ keys: [key(0)], loop: true }))).toBe(false);
  });

  it("piston and crank stay in step", () => {
    const s = scene(
      [motion({ part: "crank", kind: "spin", axis: "z", speed: 1 }), motion({ part: "piston", kind: "oscillate", axis: "y", amount: 0.5, period: 1 })],
      [part("crank"), part("piston", { position: [0, 2, 0] })],
    );
    const st = evaluate(s, 0, 0.25);
    expect(st.get("crank")!.rotation[2]).toBeCloseTo(Math.PI / 2); // pin at the top...
    expect(st.get("piston")!.position[1]).toBeCloseTo(2.5); // ...piston at the top
  });

  it("a finished spin holds its last angle", () => {
    const s = scene([motion({ kind: "spin", axis: "y", speed: 0.25, start: 1, end: 3 })]);
    expect(evaluate(s, 10, 0).get("a")!.rotation[1]).toBeCloseTo(Math.PI);
  });

  it("story duration covers bounded motions and beats", () => {
    const s = scene([motion({ keys: [key(0), key(7)] }), motion({ kind: "spin" })]);
    expect(storyDuration(s)).toBe(7);
  });
});
