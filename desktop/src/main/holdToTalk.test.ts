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
  UiohookKey: { Ctrl: 29, CtrlRight: 3613, Alt: 56, Meta: 3675, F24: 107, V: 47, A: 30 },
  uIOhook: {
    on: (name: string, fn: (e: { keycode: number }) => void) => {
      (hook.handlers[name] ??= []).push(fn);
    },
    start: () => undefined,
    stop: () => undefined,
    keyTap: (key: number) => hook.taps.push(key),
  },
}));

import { startHoldToTalk } from "./holdToTalk";

const CTRL = 29, ALT = 56, WIN = 3675, A = 30, F24 = 107;
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
});
