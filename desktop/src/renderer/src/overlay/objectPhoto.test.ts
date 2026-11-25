import { describe, expect, it } from "vitest";
import { photoDirection } from "../scene/camera";
import { MAX_PHOTO_CHARS, photoAngle, safePhoto } from "./objectPhoto";

describe("the photo kept beside a model built from it", () => {
  it("shows a small JPEG or PNG that Pyxi made", () => {
    expect(safePhoto("data:image/jpeg;base64,/9j/4AAQSkZJRg==")).toBe("data:image/jpeg;base64,/9j/4AAQSkZJRg==");
    expect(safePhoto("data:image/png;base64,iVBORw0KGgo=")).not.toBeNull();
  });

  it("shows nothing for anything else", () => {
    expect(safePhoto(null)).toBeNull();
    expect(safePhoto(undefined)).toBeNull();
    expect(safePhoto("")).toBeNull();
    expect(safePhoto("https://example.com/chair.jpg")).toBeNull();
    expect(safePhoto("data:image/svg+xml;base64,PHN2Zz48c2NyaXB0Pg==")).toBeNull();
    expect(safePhoto("data:text/html;base64,PGgxPg==")).toBeNull();
    expect(safePhoto('data:image/jpeg;base64,AAAA" onerror="x')).toBeNull();
    expect(safePhoto("data:image/jpeg;base64," + "A".repeat(MAX_PHOTO_CHARS))).toBeNull();
  });

  it("the angle a photo was taken from is two finite numbers, never straight up or down", () => {
    expect(photoAngle([40, 20])).toEqual([40, 20]);
    expect(photoAngle([0, 120])).toEqual([0, 89]);
    expect(photoAngle([0])).toBeNull();
    expect(photoAngle(["a", 2])).toBeNull();
    expect(photoAngle([Number.NaN, 2])).toBeNull();
    expect(photoAngle(null)).toBeNull();
  });
});

describe("looking at a model the way its photo was taken", () => {
  it("0 is from straight in front, 90 from its +x side, and up is up", () => {
    const close = (a: number[], b: number[]) => a.forEach((v, i) => expect(v).toBeCloseTo(b[i], 6));
    close(photoDirection(0, 0), [0, 0, 1]);
    close(photoDirection(90, 0), [1, 0, 0]);
    close(photoDirection(-90, 0), [-1, 0, 0]);
    close(photoDirection(180, 0), [0, 0, -1]);
    expect(photoDirection(0, 30)[1]).toBeCloseTo(0.5, 6);
    expect(Math.hypot(...photoDirection(37, 21))).toBeCloseTo(1, 6);
    // Never quite straight down: the heading would flip.
    expect(photoDirection(0, 500)[2]).toBeGreaterThan(0);
  });
});
