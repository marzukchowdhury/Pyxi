import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// A stand-in for the low-level keyboard hook: tests press and release keys through it.
const hook = vi.hoisted(() => {
  const handlers: Record<string, ((e: { keycode: number }) => void)[]> = {};
  return {
    handlers,
    taps: [] as number[],
    reset() {
      for (const k of Object.keys(handlers)) delete handlers[k];
      this.taps.length = 0;
    },
  };
});

vi.mock("uiohook-napi", () => ({
  UiohookKey: { Ctrl: 29, CtrlRight: 3613, Alt: 56, Meta: 3675, Shift: 42, Escape: 1, F24: 107, V: 47, A: 30 },
  uIOhook: {
    on: (name: string, fn: (e: { keycode: number }) => void) => {
      (hook.handlers[name] ??= []).push(fn);
    },
    start: () => undefined,
    stop: () => undefined,
    keyTap: (key: number) => hook.taps.push(key),
  },
}));

import { DEFAULT_DICTATION, dictationAccelerator, dictationPrefs, type DictationPrefs } from "./dictationPrefs";
import { dictationState, startHoldToTalk } from "./holdToTalk";

const CTRL = 29, ALT = 56, WIN = 3675, SHIFT = 42, ESC = 1, A = 30, F24 = 107;
const prefs = (over: Partial<DictationPrefs>) => () => ({ ...DEFAULT_DICTATION, ...over });
const down = (keycode: number) => hook.handlers.keydown?.forEach((fn) => fn({ keycode }));
const up = (keycode: number) => hook.handlers.keyup?.forEach((fn) => fn({ keycode }));

function events() {
  const got: string[] = [];
  const add = (name: string) => () => got.push(name);
  return {
    got,
    ev: {
      down: add("talk-down"),
      up: add("talk-up"),
      cancel: add("talk-cancel"),
      doubleTap: add("double-tap"),
      dictateDown: add("dictate-down"),
      dictateUp: add("dictate-up"),
      dictateCancel: add("dictate-cancel"),
    },
  };
}

describe("hold Ctrl + Windows to dictate", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    hook.reset();
  });
  afterEach(() => vi.useRealTimers());

  it("starts after a short hold and sends on release", () => {
    const { got, ev } = events();
    startHoldToTalk(ev);
    down(CTRL);
    down(WIN);
    vi.advanceTimersByTime(250);
    expect(got).toEqual(["dictate-down"]);
    expect(hook.taps).toEqual([F24]); // so Windows doesn't open Start when the key goes up
    down(F24); // our own tap comes back through the hook: ignored
    up(F24);
    up(WIN);
    expect(got).toEqual(["dictate-down", "dictate-up"]);
  });

  it("a quick tap never starts it, and another key cancels it", () => {
    const { got, ev } = events();
    startHoldToTalk(ev);
    down(CTRL);
    down(WIN);
    vi.advanceTimersByTime(100);
    up(WIN);
    up(CTRL);
    vi.advanceTimersByTime(300);
    expect(got).toEqual([]);
    down(CTRL);
    down(WIN);
    vi.advanceTimersByTime(250);
    down(A); // Ctrl+Win+A is someone's shortcut, not dictation
    expect(got).toEqual(["dictate-down", "dictate-cancel"]);
  });

  it("talking (Ctrl + Alt) still works and adding Windows cancels it", () => {
    const { got, ev } = events();
    startHoldToTalk(ev);
    down(CTRL);
    down(ALT);
    vi.advanceTimersByTime(250);
    up(ALT);
    expect(got).toEqual(["talk-down", "talk-up"]);
    up(CTRL);
    down(CTRL);
    down(ALT);
    vi.advanceTimersByTime(250);
    down(WIN);
    expect(got).toEqual(["talk-down", "talk-up", "talk-down", "talk-cancel"]);
    expect(hook.taps).toEqual([]);
  });

  it("Esc while holding cancels, like any other key", () => {
    const { got, ev } = events();
    startHoldToTalk(ev);
    down(CTRL);
    down(WIN);
    vi.advanceTimersByTime(250);
    down(ESC);
    expect(got).toEqual(["dictate-down", "dictate-cancel"]);
    up(ESC);
    up(WIN);
    up(CTRL);
    expect(got).toEqual(["dictate-down", "dictate-cancel"]); // letting go types nothing
  });
});

describe("dictation settings", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    hook.reset();
  });
  afterEach(() => vi.useRealTimers());
  const press = (...keys: number[]) => keys.forEach(down);
  const let_go = (...keys: number[]) => keys.forEach(up);

  it("switched off, the keys do nothing and talking still works", () => {
    const { got, ev } = events();
    startHoldToTalk(ev, prefs({ enabled: false }));
    press(CTRL, WIN);
    vi.advanceTimersByTime(400);
    let_go(WIN, CTRL);
    expect(got).toEqual([]);
    expect(hook.taps).toEqual([]);
    press(CTRL, ALT);
    vi.advanceTimersByTime(250);
    let_go(ALT, CTRL);
    expect(got).toEqual(["talk-down", "talk-up"]);
  });

  it("other keys can be chosen, and the old ones then do nothing", () => {
    const { got, ev } = events();
    startHoldToTalk(ev, prefs({ keys: "alt+win" }));
    press(CTRL, WIN);
    vi.advanceTimersByTime(300);
    let_go(WIN, CTRL);
    expect(got).toEqual([]);
    press(ALT, WIN);
    vi.advanceTimersByTime(250);
    let_go(WIN, ALT);
    expect(got).toEqual(["dictate-down", "dictate-up"]);
  });

  it("Shift + Windows dictates only when chosen; otherwise Shift is just another key", () => {
    const a = events();
    startHoldToTalk(a.ev, prefs({ keys: "shift+win" }));
    press(SHIFT, WIN);
    vi.advanceTimersByTime(250);
    let_go(WIN, SHIFT);
    expect(a.got).toEqual(["dictate-down", "dictate-up"]);
    hook.reset();
    const b = events();
    startHoldToTalk(b.ev); // the usual keys
    press(CTRL, WIN);
    vi.advanceTimersByTime(250);
    down(SHIFT);
    expect(b.got).toEqual(["dictate-down", "dictate-cancel"]);
  });

  it("press once to start, press again to stop", () => {
    const { got, ev } = events();
    startHoldToTalk(ev, prefs({ mode: "toggle" }));
    press(CTRL, WIN);
    vi.advanceTimersByTime(100);
    let_go(WIN, CTRL); // under 200 ms: nothing, in this mode too
    vi.advanceTimersByTime(300);
    expect(got).toEqual([]);
    press(CTRL, WIN);
    vi.advanceTimersByTime(250);
    expect(got).toEqual(["dictate-down"]);
    let_go(WIN, CTRL); // letting go does not end it
    vi.advanceTimersByTime(5000);
    down(A); // typing meanwhile does not end it either
    up(A);
    expect(got).toEqual(["dictate-down"]);
    press(CTRL, WIN); // the second press stops it at once
    expect(got).toEqual(["dictate-down", "dictate-up"]);
    vi.advanceTimersByTime(1000); // still held: it must not start another
    let_go(WIN, CTRL);
    expect(got).toEqual(["dictate-down", "dictate-up"]);
    expect(hook.taps).toEqual([F24, F24]); // Start stays shut both times
  });

  it("Esc cancels a dictation started by a press, and one being written down", () => {
    const { got, ev } = events();
    startHoldToTalk(ev, prefs({ mode: "toggle" }));
    down(ESC); // nothing is running: Esc is just Esc
    up(ESC);
    expect(got).toEqual([]);
    press(CTRL, WIN);
    vi.advanceTimersByTime(250);
    let_go(WIN, CTRL);
    down(ESC);
    up(ESC);
    expect(got).toEqual(["dictate-down", "dictate-cancel"]);
    press(CTRL, WIN); // cancelled: this press starts a new one rather than stopping
    vi.advanceTimersByTime(250);
    let_go(WIN, CTRL);
    expect(got).toEqual(["dictate-down", "dictate-cancel", "dictate-down"]);
    press(CTRL, WIN);
    let_go(WIN, CTRL);
    dictationState("working"); // the overlay: the words are being written down
    down(ESC);
    expect(got).toEqual(["dictate-down", "dictate-cancel", "dictate-down", "dictate-up", "dictate-cancel"]);
    down(ESC); // once
    expect(got).toHaveLength(5);
  });

  it("a recording that ended by itself is not 'stopped' by the next press", () => {
    const { got, ev } = events();
    startHoldToTalk(ev, prefs({ mode: "toggle" }));
    press(CTRL, WIN);
    vi.advanceTimersByTime(250);
    let_go(WIN, CTRL);
    dictationState("recording");
    dictationState("idle"); // five minutes were up, or the microphone failed
    press(CTRL, WIN);
    vi.advanceTimersByTime(250);
    expect(got).toEqual(["dictate-down", "dictate-down"]);
  });

  it("saved settings are checked before they are used", () => {
    expect(dictationPrefs(null)).toEqual(DEFAULT_DICTATION);
    expect(dictationPrefs({ enabled: "yes", keys: "ctrl+c", mode: "always", polish: 1 })).toEqual(DEFAULT_DICTATION);
    expect(dictationPrefs({ enabled: false, keys: "alt+win", mode: "toggle", polish: false, cloud: true })).toEqual({ enabled: false, keys: "alt+win", mode: "toggle", polish: false, cloud: true });
    expect(dictationPrefs({ polish: false }, { ...DEFAULT_DICTATION, mode: "toggle" })).toEqual({ ...DEFAULT_DICTATION, mode: "toggle", polish: false });
    expect(dictationAccelerator("ctrl+win")).toBe("Control+Win");
    expect(dictationAccelerator("shift+win")).toBe("Shift+Win");
  });
});
