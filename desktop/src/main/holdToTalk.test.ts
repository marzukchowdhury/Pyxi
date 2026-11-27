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
    keyTap: (key: number, mods?: number[]) => hook.taps.push(...(mods ?? []), key),
  },
}));

import { DEFAULT_DICTATION, dictationAccelerator, dictationPrefs, type DictationPrefs } from "./dictationPrefs";
import { chordLabel, chordsFrom, dictationChord, dictationState, parseChord, pasteText, startHoldToTalk } from "./holdToTalk";

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
    startHoldToTalk(ev, undefined, { quietTap: true }); // as on Windows (the default there)
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
    startHoldToTalk(ev, prefs({ mode: "toggle" }), { quietTap: true }); // as on Windows
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
    expect(chordsFrom(undefined)).toEqual({ talk: ["ctrl", "alt"], dictate: ["ctrl", "meta"], dictateChosen: false });
    expect(chordsFrom({ talk: "option+cmd" })).toEqual({ talk: ["alt", "meta"], dictate: ["ctrl", "meta"], dictateChosen: false });
    expect(chordsFrom({ talk: "option+cmd" }, { PYXI_TALK_CHORD: "ctrl+cmd" })).toEqual({ talk: ["ctrl", "meta"], dictate: ["ctrl", "alt"], dictateChosen: true });
    expect(chordsFrom({ talk: "nonsense" })).toEqual({ talk: ["ctrl", "alt"], dictate: ["ctrl", "meta"], dictateChosen: false });
    expect(chordsFrom({ dictate: "option+cmd" })).toEqual({ talk: ["ctrl", "alt"], dictate: ["alt", "meta"], dictateChosen: true });
  });

  it("names chords for each system", () => {
    expect(chordLabel(["ctrl", "alt"], "win32")).toBe("Control+Alt");
    expect(chordLabel(["meta", "ctrl"], "win32")).toBe("Control+Win");
    expect(chordLabel(["ctrl", "alt"], "darwin")).toBe("Control+Option");
    expect(chordLabel(["ctrl", "meta"], "darwin")).toBe("Control+Command");
    // Settings' dictation keys read the same way: as on Windows there, the Mac's names on a Mac.
    expect(chordLabel(dictationChord("ctrl+win"), "win32")).toBe(dictationAccelerator("ctrl+win"));
    expect(chordLabel(dictationChord("alt+win"), "win32")).toBe(dictationAccelerator("alt+win"));
    expect(chordLabel(dictationChord("shift+win"), "win32")).toBe(dictationAccelerator("shift+win"));
    expect(chordLabel(dictationChord("shift+win"), "darwin")).toBe("Shift+Command");
  });

  it("a Mac's Control+Command dictates without the Windows F24 tap", () => {
    const { got, ev } = events();
    startHoldToTalk(ev, undefined, { quietTap: false });
    down(CTRL);
    down(WIN); // Command: the same key code as the Windows key
    vi.advanceTimersByTime(250);
    up(WIN);
    expect(got).toEqual(["dictate-down", "dictate-up"]);
    expect(hook.taps).toEqual([]);
  });

  it("a chosen chord replaces the default one", () => {
    const { got, ev } = events();
    startHoldToTalk(ev, undefined, { talk: ["alt", "meta"], dictate: ["ctrl", "meta"], quietTap: false });
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

  it("Settings' dictation keys and press-to-start work on a Mac too, with no F24 tap", () => {
    const { got, ev } = events();
    startHoldToTalk(ev, prefs({ keys: "alt+win", mode: "toggle" }), { quietTap: false });
    down(ALT);
    down(WIN); // Option+Command
    vi.advanceTimersByTime(250);
    up(WIN);
    up(ALT);
    expect(got).toEqual(["dictate-down"]);
    down(ALT);
    down(WIN);
    expect(got).toEqual(["dictate-down", "dictate-up"]);
    up(WIN);
    up(ALT);
    expect(hook.taps).toEqual([]);
  });

  it("a dictation chord chosen in hotkeys.json wins over Settings' keys; off is still off", () => {
    const a = events();
    startHoldToTalk(a.ev, prefs({ keys: "alt+win" }), { dictate: ["ctrl", "alt", "meta"], quietTap: false });
    down(ALT);
    down(WIN);
    vi.advanceTimersByTime(250);
    up(WIN);
    up(ALT);
    expect(a.got).toEqual([]);
    down(CTRL);
    down(ALT);
    down(WIN);
    vi.advanceTimersByTime(250);
    up(WIN);
    up(ALT);
    up(CTRL);
    expect(a.got).toEqual(["dictate-down", "dictate-up"]);
    hook.reset();
    const b = events();
    startHoldToTalk(b.ev, prefs({ enabled: false }), { dictate: ["ctrl", "meta"], quietTap: false });
    down(CTRL);
    down(WIN);
    vi.advanceTimersByTime(250);
    up(WIN);
    up(CTRL);
    expect(b.got).toEqual([]);
  });

  it("pastes with Cmd+V on a Mac and Ctrl+V on Windows", () => {
    pasteText("darwin");
    expect(hook.taps).toEqual([WIN, 47]);
    hook.taps.length = 0;
    pasteText("win32");
    expect(hook.taps).toEqual([CTRL, 47]);
  });
});
