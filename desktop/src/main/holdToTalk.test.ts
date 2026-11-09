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
    keyTap: (key: number, mods?: number[]) => hook.taps.push(...(mods ?? []), key),
  },
}));

import { chordLabel, chordsFrom, parseChord, pasteText, startHoldToTalk } from "./holdToTalk";

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
    startHoldToTalk(ev, { quietTap: true }); // as on Windows (the default there)
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

describe("chords on a Mac, and chosen by the user", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    hook.reset();
  });
  afterEach(() => vi.useRealTimers());

  it("reads chords written any way", () => {
    expect(parseChord("ctrl+option")).toEqual(["ctrl", "alt"]);
    expect(parseChord("Control + Command")).toEqual(["ctrl", "meta"]);
    expect(parseChord("alt+cmd+ctrl")).toEqual(["alt", "meta", "ctrl"]);
    expect(parseChord("ctrl")).toBeNull(); // one key alone is a tap, not a chord
    expect(parseChord("ctrl+ctrl")).toBeNull();
    expect(parseChord("ctrl+space")).toBeNull();
    expect(parseChord(undefined)).toBeNull();
  });

  it("the environment wins, then the saved file, then the defaults; never the same chord twice", () => {
    expect(chordsFrom(undefined)).toEqual({ talk: ["ctrl", "alt"], dictate: ["ctrl", "meta"] });
    expect(chordsFrom({ talk: "option+cmd" })).toEqual({ talk: ["alt", "meta"], dictate: ["ctrl", "meta"] });
    expect(chordsFrom({ talk: "option+cmd" }, { PYXI_TALK_CHORD: "ctrl+cmd" })).toEqual({ talk: ["ctrl", "meta"], dictate: ["ctrl", "alt"] });
    expect(chordsFrom({ talk: "nonsense" })).toEqual({ talk: ["ctrl", "alt"], dictate: ["ctrl", "meta"] });
  });

  it("names chords for each system", () => {
    expect(chordLabel(["ctrl", "alt"], "win32")).toBe("Control+Alt");
    expect(chordLabel(["meta", "ctrl"], "win32")).toBe("Control+Win");
    expect(chordLabel(["ctrl", "alt"], "darwin")).toBe("Control+Option");
    expect(chordLabel(["ctrl", "meta"], "darwin")).toBe("Control+Command");
  });

  it("a Mac's Control+Command dictates without the Windows F24 tap", () => {
    const { got, ev } = events();
    startHoldToTalk(ev, { quietTap: false });
    down(CTRL);
    down(WIN); // Command: the same key code as the Windows key
    vi.advanceTimersByTime(250);
    up(WIN);
    expect(got).toEqual(["dictate-down", "dictate-up"]);
    expect(hook.taps).toEqual([]);
  });

  it("a chosen chord replaces the default one", () => {
    const { got, ev } = events();
    startHoldToTalk(ev, { talk: ["alt", "meta"], dictate: ["ctrl", "meta"], quietTap: false });
    down(CTRL);
    down(ALT);
    vi.advanceTimersByTime(250);
    expect(got).toEqual([]); // Ctrl+Alt isn't the talk chord any more
    up(CTRL);
    up(ALT);
    down(ALT);
    down(WIN);
    vi.advanceTimersByTime(250);
    up(ALT);
    expect(got).toEqual(["talk-down", "talk-up"]);
  });

  it("pastes with Cmd+V on a Mac and Ctrl+V on Windows", () => {
    pasteText("darwin");
    expect(hook.taps).toEqual([WIN, 47]);
    hook.taps.length = 0;
    pasteText("win32");
    expect(hook.taps).toEqual([CTRL, 47]);
  });
});
