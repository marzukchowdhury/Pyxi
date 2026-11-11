import { describe, expect, it } from "vitest";
import { guideClick, labelSize, labelSpot } from "./guideMarks";

const view = { w: 1920, h: 1080 };

describe("where a mark's label goes", () => {
  it("sits under the thing when there is room", () => {
    expect(labelSpot({ x: 600, y: 300, w: 200, h: 40 }, 20, { w: 180, h: 30 }, view)).toEqual({ x: 700, y: 360 });
  });

  it("goes above a row at the bottom edge instead of onto the taskbar or off the screen", () => {
    // Live: "Scroll down to Recovery", framed on a 19 px sliver at y=1013 of 1080. Its pill and
    // its Done button were drawn at y=1052, over the taskbar.
    const spot = labelSpot({ x: 607, y: 1013, w: 1006, h: 19 }, 20, labelSize("Scroll down to Recovery", true), view);
    expect(spot.y + 30).toBeLessThanOrEqual(1013);
    expect(spot.y).toBeGreaterThanOrEqual(8);
  });

  it("stays inside a frame as tall as the screen", () => {
    const spot = labelSpot({ x: 0, y: 0, w: 1920, h: 1080 }, 20, { w: 200, h: 30 }, view);
    expect(spot.y).toBe(1080 - 8 - 30);
  });

  it("never runs off either side", () => {
    const size = { w: 300, h: 30 };
    expect(labelSpot({ x: 0, y: 500, w: 20, h: 20 }, 14, size, view).x).toBe(158);
    expect(labelSpot({ x: 1900, y: 500, w: 20, h: 20 }, 14, size, view).x).toBe(1920 - 158);
  });
});

describe("a click somewhere on the desktop, during a guide", () => {
  const step = { rect: [100, 100, 80, 30], action: "click" };

  it("on the mark of a click step is the step done", () => {
    expect(guideClick(step, 120, 110)).toBe("clicked");
    expect(guideClick({ rect: step.rect, action: "double_click" }, 104, 128)).toBe("clicked");
    expect(guideClick({ rect: step.rect }, 96, 96)).toBe("clicked"); // a few px of grace
  });

  it("off the mark is them going another way", () => {
    expect(guideClick(step, 500, 500)).toBe("elsewhere");
    expect(guideClick({ rect: step.rect, action: "type" }, 500, 500)).toBe("elsewhere");
  });

  it("inside a field they are to type in is not the step yet", () => {
    expect(guideClick({ rect: step.rect, action: "type" }, 120, 110)).toBeNull();
  });

  it("says nothing on a screen that has no mark, so one screen answers when all are told", () => {
    // The click is sent to every screen's overlay (it used to go to the main one only, and a
    // step marked on a second screen never heard its click).
    expect(guideClick(undefined, 120, 110)).toBeNull();
    expect(guideClick({ rect: null, action: "click" }, 120, 110)).toBeNull();
  });
});
