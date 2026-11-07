import { describe, expect, it } from "vitest";
import { goodMomentToUpdate, IDLE_SECONDS } from "./updateTiming";

const idleBrain = async () => false;
const busyBrain = async () => true;

describe("installing a downloaded update by itself", () => {
  it("waits while they're using the PC", async () => {
    expect(await goodMomentToUpdate(10, idleBrain)).toBe(false);
    expect(await goodMomentToUpdate(IDLE_SECONDS - 1, idleBrain)).toBe(false);
  });
  it("goes once they've stepped away and Pyxi has nothing on", async () => {
    expect(await goodMomentToUpdate(IDLE_SECONDS, idleBrain)).toBe(true);
  });
  it("never cuts off a job, a reply or a question in progress", async () => {
    expect(await goodMomentToUpdate(IDLE_SECONDS * 4, busyBrain)).toBe(false);
  });
  it("waits when it can't tell", async () => {
    expect(await goodMomentToUpdate(IDLE_SECONDS * 4, async () => Promise.reject(new Error("no brain")))).toBe(false);
  });
});
