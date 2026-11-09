import { describe, expect, it, vi } from "vitest";
import type { AnswerCard, BrainEvent } from "../shared/protocol";

// The store talks to the brain, the Electron bridge and the speakers as it loads: stand-ins.
const wire = vi.hoisted(() => ({ handlers: [] as ((e: unknown) => void)[] }));
vi.mock("../shared/socket", () => ({
  brain: { on: (fn: (e: unknown) => void) => void wire.handlers.push(fn), onStatus: () => () => {}, send: () => {} },
}));
vi.mock("../shared/bridge", () => ({
  bridge: new Proxy({}, { get: () => () => () => {} }),
  currentDisplay: () => ({ id: 0, primary: true }),
  onThisDisplay: () => true,
  toLocal: (x: number, y: number) => [x, y],
}));
vi.mock("../shared/audio", () => {
  class Quiet {
    busy = false;
    muted = false;
    enqueue(): void {}
    stop(): void {}
  }
  return { AudioQueue: Quiet, RemoteVoice: Quiet };
});
vi.mock("../shared/chime", () => ({ startChime: () => {} }));

const { useOverlay } = await import("./store");
const fromBrain = (e: unknown) => wire.handlers.forEach((fn) => fn(e as BrainEvent));

const pick: AnswerCard = {
  id: "answer_a1",
  kind: "answer",
  title: "Anker Soundcore Space A40",
  figure: "$49.99",
  note: null,
  facts: [],
  sources: [],
  button: null,
  image: null,
  agent: null,
  ok: true,
};

describe("the answer card on screen", () => {
  it("takes its picture when it arrives, as the same card", () => {
    fromBrain({ type: "answer", card: pick });
    fromBrain({ type: "answer", card: { ...pick, image: "data:image/jpeg;base64,/9j/4AAQ" } });
    expect(useOverlay.getState().answerCard?.image).toBe("data:image/jpeg;base64,/9j/4AAQ");
  });

  it("stays closed when its picture arrives after they closed it", () => {
    fromBrain({ type: "answer", card: pick });
    useOverlay.getState().closeAnswer();
    fromBrain({ type: "answer", card: { ...pick, image: "data:image/jpeg;base64,/9j/4AAQ" } });
    expect(useOverlay.getState().answerCard).toBeNull();
    // and after "dismiss everything" too
    const other = { ...pick, id: "answer_b2" };
    fromBrain({ type: "answer", card: other });
    expect(useOverlay.getState().answerCard?.id).toBe("answer_b2"); // a new card still shows
    useOverlay.getState().dismissAll();
    fromBrain({ type: "answer", card: { ...other, image: "data:image/jpeg;base64,/9j/4AAQ" } });
    expect(useOverlay.getState().answerCard).toBeNull();
  });
});
