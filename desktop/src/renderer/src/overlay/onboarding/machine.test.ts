import { describe, expect, it } from "vitest";
import { begin, lineFor, needsCode, reduce, screenClear, spoken, STEPS, T, type Tour, type TourEvent } from "./machine";

const run = (t: Tour, ...events: TourEvent[]) => events.reduce(reduce, t);
const wait = (ms: number, speaking = false): TourEvent => ({ type: "tick", ms, speaking });
/** A tour standing at the start of `step` (walked there the short way). */
function at(step: (typeof STEPS)[number], name = "Sam"): Tour {
  if (step === "arrival") return begin();
  let t = run(begin(), { type: "next" }, { type: "name", name });
  for (let i = 0; i < STEPS.length && t.step !== step; i++) t = reduce(t, { type: "skip_step" });
  return t;
}

describe("the tour's order", () => {
  it("walks every step in order and ends finished", () => {
    let t = begin();
    expect([t.step, t.phase]).toEqual(["arrival", "fly"]);
    t = run(t, wait(T.fly));
    expect(t.phase).toBe("hello");
    t = run(t, wait(T.hello));
    expect(t.phase).toBe("ask");
    t = run(t, wait(60_000)); // the question waits for them
    expect([t.step, t.phase]).toEqual(["arrival", "ask"]);
    t = run(t, { type: "name", name: "  Sam   Rivera " });
    expect([t.step, t.phase, t.name]).toEqual(["talk", "wait", "Sam Rivera"]);
    t = run(t, { type: "heard", text: "hello" });
    expect(t.phase).toBe("answered");
    t = run(t, wait(T.talkAnswered));
    expect([t.step, t.phase]).toEqual(["screen", "wait"]);
    t = run(t, { type: "asked" }, { type: "answered" });
    expect(t.phase).toBe("answered");
    t = run(t, wait(T.screenAnswered));
    expect([t.step, t.phase]).toEqual(["about", "form"]);
    t = run(t, { type: "submit" }, { type: "saved" });
    expect(t.phase).toBe("thanks");
    t = run(t, wait(T.thanks));
    expect([t.step, t.phase]).toEqual(["google", "offer"]);
    t = run(t, { type: "connect" }, { type: "connected" });
    expect(t.phase).toBe("connected");
    t = run(t, wait(T.connected));
    expect([t.step, t.phase]).toEqual(["demos", "pick"]);
    t = run(t, { type: "demo", demo: "build" });
    expect([t.phase, t.demo, t.played]).toEqual(["play", "build", ["build"]]);
    t = run(t, wait(T.demo));
    expect([t.phase, t.demo]).toEqual(["pick", null]);
    t = run(t, { type: "next" });
    expect([t.step, t.phase]).toEqual(["landing", "rise"]);
    t = run(t, wait(T.rise));
    expect(t.phase).toBe("glow");
    expect(t.ended).toBeNull();
    t = run(t, wait(T.glow));
    expect(t.ended).toBe("finished");
  });

  it("has the seven steps the brain knows", () => {
    expect(STEPS).toEqual(["arrival", "talk", "screen", "about", "google", "demos", "landing"]);
  });
});

describe("skipping", () => {
  it("Esc leaves each step for the next, and the last one ends it", () => {
    let t = begin();
    const seen = [t.step];
    for (let i = 0; i < 20 && !t.ended; i++) {
      t = reduce(t, { type: "skip_step" });
      if (!t.ended && seen[seen.length - 1] !== t.step) seen.push(t.step);
    }
    expect(seen).toEqual(STEPS);
    expect(t.ended).toBe("finished");
  });

  it("Skip tour ends it from any step, as skipped", () => {
    for (const step of STEPS) expect(reduce(at(step), { type: "skip_tour" }).ended).toBe("skipped");
    expect(reduce(begin(), { type: "skip_tour" }).ended).toBe("skipped");
  });

  it("no name is fine: Enter on the empty box moves on", () => {
    const t = run(begin(), wait(T.fly + T.hello), { type: "name", name: "   " });
    expect([t.step, t.name]).toEqual(["talk", ""]);
    expect(lineFor(run(t, { type: "heard", text: "hi" }))?.text).toBe("Hello.");
  });

  it("Enter during the hello goes straight to the question", () => {
    expect(reduce(begin(), { type: "next" }).phase).toBe("ask");
  });

  it("Esc during a demo only stops the demo", () => {
    const t = run(at("demos"), { type: "demo", demo: "tidy" }, { type: "skip_step" });
    expect([t.step, t.phase, t.demo]).toEqual(["demos", "pick", null]);
  });

  it("nothing moves a tour that has ended", () => {
    const over = reduce(at("google"), { type: "skip_tour" });
    expect(run(over, { type: "next" }, wait(99_000), { type: "connected" })).toBe(over);
  });
});

describe("waiting, and not waiting forever", () => {
  it("offers typing and Next when nobody speaks for about 12 seconds", () => {
    let t = run(at("talk"), wait(T.talkHint - 100));
    expect(t.hint).toBe(false);
    t = run(t, wait(100));
    expect([t.hint, t.step, t.phase]).toEqual([true, "talk", "wait"]); // still waiting, no lecture
    expect(reduce(t, { type: "next" }).step).toBe("screen");
  });

  it("asks once more when their words could not be made out", () => {
    const t = run(at("talk"), { type: "heard", text: "  " });
    expect([t.phase, t.again, t.hint]).toEqual(["wait", true, true]);
    expect(lineFor(t)?.text).toBe("I didn't catch that.");
    expect(run(t, { type: "heard", text: "hello" }).phase).toBe("answered");
  });

  it("lights the keys while they are held", () => {
    const t = run(at("talk"), { type: "keys", down: true });
    expect(t.keys).toBe(true);
    expect(run(t, { type: "keys", down: false }).keys).toBe(false);
  });

  it("the screen step takes one try, and a turn that never ends is not waited for", () => {
    let t = run(at("screen"), wait(T.screenHint));
    expect(t.hint).toBe(true);
    t = run(t, { type: "asked" });
    expect(t.phase).toBe("working");
    t = run(t, { type: "asked" }, wait(T.screenWorking));
    expect(t.phase).toBe("answered");
    expect(run(t, wait(T.screenAnswered)).step).toBe("about");
  });

  it("an answer outside the screen step changes nothing", () => {
    const t = at("google");
    expect(run(t, { type: "asked" }, { type: "answered" }, { type: "heard", text: "hi" })).toEqual(t);
  });

  it("a brain that never says saved does not hold the card", () => {
    const t = run(at("about"), { type: "submit" }, wait(T.saving));
    expect(t.phase).toBe("thanks");
  });

  it("a line still being said holds the step, but not for ever", () => {
    let t = run(at("talk"), { type: "heard", text: "hello" }, wait(T.talkAnswered, true));
    expect([t.step, t.phase]).toEqual(["talk", "answered"]);
    t = run(t, wait(500, true));
    expect(t.step).toBe("talk");
    expect(run(t, wait(100, false)).step).toBe("screen"); // the line ended
    expect(run(t, wait(T.maxSpeaking, true)).step).toBe("screen"); // or it never did
  });

  it("Enter on the about card is the card's own business", () => {
    const t = at("about");
    expect(reduce(t, { type: "next" })).toBe(t);
  });

  it("connecting Google waits for them, and Maybe later moves on", () => {
    let t = run(at("google"), { type: "connect" }, wait(120_000));
    expect([t.step, t.phase]).toEqual(["google", "connecting"]);
    t = reduce(t, { type: "next" });
    expect(t.step).toBe("demos");
  });
});

describe("a child", () => {
  it("ends the tour as underage, whatever is pressed next", () => {
    const t = reduce(at("about"), { type: "underage" });
    expect([t.step, t.phase, t.ended]).toEqual(["about", "underage", null]);
    expect(lineFor(t)?.text).toBe("Pyxi is for people 13 and over.");
    for (const e of [{ type: "next" }, { type: "skip_step" }, { type: "skip_tour" }, wait(T.underage)] as TourEvent[])
      expect(reduce(t, e).ended).toBe("underage");
  });
});

describe("failing", () => {
  it("ends quietly as failed from anywhere", () => {
    for (const step of STEPS) expect(reduce(at(step), { type: "fail" }).ended).toBe("failed");
  });
});

describe("the lines", () => {
  const every: Tour[] = [];
  let t = begin();
  const walk: TourEvent[] = [
    wait(T.fly), wait(T.hello), { type: "name", name: "Sam" }, { type: "heard", text: "" }, { type: "heard", text: "hi" }, wait(T.talkAnswered),
    { type: "asked" }, { type: "answered" }, wait(T.screenAnswered), { type: "submit" }, { type: "saved" }, wait(T.thanks),
    { type: "connect" }, { type: "connected" }, wait(T.connected), { type: "demo", demo: "explain" }, { type: "next" }, { type: "next" }, wait(T.rise),
  ];
  for (const e of walk) {
    every.push(t);
    t = reduce(t, e);
  }
  every.push(t, reduce(at("about"), { type: "underage" }));

  it("says the founder's lines", () => {
    const said = every.map((x) => lineFor(x)).filter(Boolean).map((l) => spoken(l!));
    expect(said).toContain("Hi. I'm Pyxi. I live in the corner of your screen.");
    expect(said).toContain("What should I call you?");
    expect(said).toContain("Hold Ctrl and Alt, and say hello.");
    expect(said).toContain("Hello, Sam. I can hear you. That's all it takes.");
    expect(said).toContain("Now point me at something. Hover anything on your screen, hold Ctrl and Alt, and ask what it is.");
    expect(said).toContain("Want me to help with your calendar, email, Sheets and Docs?");
    expect(said).toContain("I'll be right here. Hold Ctrl and Alt whenever you need me.");
  });

  it("has a line at every moment after the firefly lands", () => {
    for (const x of every.slice(1)) expect(lineFor(x), `${x.step}/${x.phase}`).not.toBeNull();
  });

  it("never uses an em dash or an emoji", () => {
    for (const x of every) {
      const l = lineFor(x);
      if (l) expect(spoken(l)).not.toMatch(/[—–]|\p{Extended_Pictographic}/u);
    }
  });

  it("names the keys this PC really uses", () => {
    expect(lineFor(at("talk"), ["Ctrl", "Shift", "Space"])?.text).toBe("Hold Ctrl and Shift and Space, and say hello.");
    expect(lineFor(at("talk"), [])?.text).toBe("Click the notch, and say hello.");
  });
});

describe("the invite code step", () => {
  const NEW_TESTER = { joined: false, can_join: true, own_key: false };
  /** The code step with its box showing. */
  const box = () => run(begin({ code: true, demo: true }), wait(T.fly));

  it("is only for someone who needs a code", () => {
    expect(needsCode(NEW_TESTER, false)).toBe(true);
    expect(needsCode({ joined: false, can_join: true }, false)).toBe(true); // an older brain: no own_key
    expect(needsCode({ joined: true, can_join: false, own_key: false }, false)).toBe(false); // already joined
    expect(needsCode({ joined: false, can_join: true, own_key: true }, false)).toBe(false); // a key of their own
    expect(needsCode({ joined: false, can_join: false, own_key: false }, false)).toBe(false);
    expect(needsCode(null, false)).toBe(false);
    expect(needsCode(undefined, false)).toBe(false);
  });

  it("is never shown in the developer build", () => {
    expect(needsCode(NEW_TESTER, true)).toBe(false);
  });

  it("comes first when it is needed, and not at all otherwise", () => {
    expect(begin().step).toBe("arrival");
    expect(begin({ code: false, demo: true }).step).toBe("arrival");
    const t = begin({ code: true });
    expect([t.step, t.phase]).toEqual(["code", "fly"]);
    expect(lineFor(box())).toEqual({ id: "code", text: "First, your invite code.", sub: "It came with your invitation. It lets me think." });
    expect(run(box(), wait(600_000)).phase).toBe("enter"); // the box waits for them
  });

  it("a code that works goes on to the name once the brain is back", () => {
    let t = run(box(), { type: "join" });
    expect(t.phase).toBe("joining");
    t = run(t, { type: "joined" });
    expect([t.step, t.phase, lineFor(t)?.text]).toEqual(["code", "joined", "You're in."]);
    // The brain restarts: nothing here ends the tour or moves it on while it is away.
    t = run(t, wait(20_000), { type: "next" }, { type: "skip_step" });
    expect([t.step, t.phase, t.ended]).toEqual(["code", "joined", null]);
    t = run(t, { type: "ready", demo: false });
    expect([t.step, t.phase, t.demoMode]).toEqual(["arrival", "hello", false]);
    t = run(t, wait(T.hello));
    expect([t.step, t.phase]).toEqual(["arrival", "ask"]);
    t = run(t, { type: "name", name: "Sam" }, { type: "heard", text: "hello" }, wait(T.talkAnswered));
    expect([t.step, t.phase]).toEqual(["screen", "wait"]); // joined: the real question
  });

  it("a brain that never comes back ends the tour for another start", () => {
    const t = run(box(), { type: "join" }, { type: "joined" }, wait(T.restart));
    expect(t.ended).toBe("failed");
  });

  it("a code that fails stays there with the server's message, to try again", () => {
    let t = run(box(), { type: "join" }, { type: "join_failed", message: "That invite code isn't valid." });
    expect([t.step, t.phase, t.note]).toEqual(["code", "enter", "That invite code isn't valid."]);
    t = run(t, { type: "join" });
    expect([t.phase, t.note]).toEqual(["joining", ""]);
    t = run(t, { type: "joined" });
    expect(t.phase).toBe("joined");
  });

  it("no answer from the server gives the box back", () => {
    const t = run(box(), { type: "join" }, wait(T.joining));
    expect([t.phase, t.note]).toEqual(["enter", "I couldn't get an answer. Check your internet and try again."]);
  });

  it("\"I don't have one\" says what that means and continues in demo mode", () => {
    let t = run(box(), { type: "no_code" });
    expect([t.step, t.phase]).toEqual(["code", "without"]);
    expect(spoken(lineFor(t)!)).toBe("That's fine. I'll run as a demo until you enter a code in Settings.");
    t = run(t, wait(600_000)); // it waits to be read
    expect(t.phase).toBe("without");
    t = run(t, { type: "next" });
    expect([t.step, t.phase, t.demoMode]).toEqual(["arrival", "hello", true]);
  });

  it("Esc explains first, then goes on; the whole tour can still be skipped", () => {
    let t = run(box(), { type: "skip_step" });
    expect(t.phase).toBe("without");
    expect(run(t, { type: "skip_step" }).step).toBe("arrival");
    expect(run(box(), { type: "skip_tour" }).ended).toBe("skipped");
    t = run(box(), { type: "join" }, { type: "skip_step" }, { type: "no_code" });
    expect(t.phase).toBe("joining"); // not while the code is on its way
  });

  it("never pretends to look at the screen in demo mode", () => {
    let t = run(begin({ demo: true }), { type: "next" }, { type: "name", name: "Sam" }, { type: "heard", text: "hello" }, wait(T.talkAnswered));
    expect([t.step, t.phase]).toEqual(["screen", "demo"]);
    expect(lineFor(t)?.sub).toBe("That one needs your invite code. You can add it in Settings any time.");
    expect(screenClear(t)).toBe(false);
    t = run(t, { type: "asked" }, { type: "answered" }); // a canned answer is not taken for a real one
    expect(t.phase).toBe("demo");
    expect(run(t, wait(T.screenDemo)).step).toBe("about");
    expect(run(t, { type: "next" }).step).toBe("about");
  });

  it("keeps its lines free of em dashes and emoji", () => {
    for (const t of [box(), run(box(), { type: "join" }, { type: "joined" }), run(box(), { type: "no_code" })]) expect(spoken(lineFor(t)!)).not.toMatch(/[—–]|\p{Extended_Pictographic}/u);
  });
});

describe("the dim", () => {
  it("lifts while they ask about their screen and when the tour lands", () => {
    expect(screenClear(at("screen"))).toBe(true);
    expect(screenClear(at("about"))).toBe(false);
    expect(screenClear(run(at("landing"), wait(T.rise)))).toBe(true);
    expect(screenClear(begin())).toBe(false);
  });
});
