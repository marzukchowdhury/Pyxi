/**
 * Hold-to-talk: hold Ctrl + (left) Alt to talk, release to send. Electron's globalShortcut
 * can't see modifier-only chords or key releases, so this uses a low-level keyboard hook.
 *
 * Rules that keep it from firing by accident:
 * - Only the LEFT Alt counts. AltGr (right Alt) sends Ctrl+Alt on many keyboards and would
 *   otherwise trigger while people type accented characters.
 * - The chord must be held ~200 ms before listening starts, so quick shortcuts don't trigger.
 * - Pressing any other key while holding cancels (Ctrl+Alt+Del, Ctrl+Alt+T, etc.).
 *
 * Double-tapping Ctrl (two quick taps, nothing else pressed) opens the notch's text box.
 *
 * Holding Ctrl + (left) Windows the same way dictates: the words are typed where the cursor is.
 * Settings can change the keys (Alt + Windows, Shift + Windows), turn dictation off, or make it
 * "press once to start, press again to stop". In that mode the first press must still be held
 * ~200 ms, any press of the keys stops it, and Esc cancels it. Esc also cancels a dictation
 * that is being written down (the overlay says when one is, see dictationState).
 *
 * On a Mac the same chords are Control + Option (talk) and Control + Command (dictate). Either
 * chord can be changed (see `chordsFrom`): any two or three of ctrl, alt/option, meta/cmd/win.
 * The hook needs the Accessibility permission there (and Input Monitoring on some macOS
 * versions); without it `startHoldToTalk` returns false and the shortcuts still work.
 */
import { UiohookKey, uIOhook } from "uiohook-napi";
import { DEFAULT_DICTATION, type DictationKeys, type DictationPrefs } from "./dictationPrefs";

export interface HoldToTalkEvents {
  down(): void;
  up(): void;
  cancel(): void;
  doubleTap(): void;
  /** The dictation keys: start, stop (type what was said), cancel (type nothing). */
  dictateDown?(): void;
  dictateUp?(): void;
  dictateCancel?(): void;
  /** Left clicks, physical screen px. Only forwarded while a guide is waiting for one. */
  click?(x: number, y: number): void;
  /** The mouse moved (or dragged). Called very often while it moves: keep it cheap. */
  move?(): void;
}

/** The modifiers a chord can be made of. "alt" is Option on a Mac, "meta" is Windows or Command. */
export type Mod = "ctrl" | "alt" | "meta";
/** ...and Shift, which only Settings' "Shift + Windows" dictation keys use. */
export type Key = Mod | "shift";

export interface HoldToTalkOptions {
  talk?: Mod[];
  dictate?: Mod[];
  /** Tap F24 when dictation starts (Windows opens Start when the Windows key goes up alone). */
  quietTap?: boolean;
}

/** libuiohook's mouse moved and mouse dragged events (uiohook-napi names only the first). */
const MOUSE_MOVES = new Set<number>([9, 10]);

const CTRL = new Set<number>([UiohookKey.Ctrl, UiohookKey.CtrlRight]);
const LEFT_ALT = UiohookKey.Alt;
const LEFT_WIN = UiohookKey.Meta;
const LEFT_SHIFT = UiohookKey.Shift;
const ESCAPE = UiohookKey.Escape;
// Tapped once when dictation starts: Windows opens Start when the Windows key goes up with
// nothing pressed in between, and F24 is a key no app uses. The hook ignores it.
const QUIET_KEY = UiohookKey.F24;
const HOLD_MS = 200;
const TAP_MS = 220; // a tap is a press shorter than this
const TAP_GAP_MS = 360; // ...and the second tap must start within this of the first

type Chord = "talk" | "dictate";

const MOD_NAMES: Record<string, Mod> = {
  ctrl: "ctrl",
  control: "ctrl",
  alt: "alt",
  option: "alt",
  opt: "alt",
  meta: "meta",
  win: "meta",
  windows: "meta",
  cmd: "meta",
  command: "meta",
  super: "meta",
};

/** "ctrl+option" -> ["ctrl", "alt"]. Null unless it's two or three different modifiers. */
export function parseChord(text: string | null | undefined): Mod[] | null {
  if (!text) return null;
  const mods = text
    .toLowerCase()
    .split("+")
    .map((p) => p.trim())
    .filter(Boolean)
    .map((p) => MOD_NAMES[p]);
  if (mods.some((m) => !m)) return null;
  const unique = [...new Set(mods)] as Mod[];
  return unique.length >= 2 && unique.length === mods.length ? unique : null;
}

/** Talk and dictate chords: the user's choice (env, then saved settings), else the defaults. */
export function chordsFrom(
  saved: { talk?: string; dictate?: string } | undefined,
  env: Record<string, string | undefined> = {},
): { talk: Mod[]; dictate: Mod[]; dictateChosen: boolean } {
  const talk = parseChord(env.PYXI_TALK_CHORD) ?? parseChord(saved?.talk) ?? ["ctrl", "alt"];
  const chosen = parseChord(env.PYXI_DICTATE_CHORD) ?? parseChord(saved?.dictate);
  let dictate = chosen ?? ["ctrl", "meta"];
  const clash = same(talk, dictate);
  if (clash) dictate = same(talk, ["ctrl", "meta"]) ? ["ctrl", "alt"] : ["ctrl", "meta"];
  // Chosen here (or moved out of the talk chord's way): Settings' dictation keys don't apply.
  return { talk, dictate, dictateChosen: chosen !== null || clash };
}

const same = (a: Mod[], b: Mod[]) => a.length === b.length && a.every((m) => b.includes(m));

/** How a chord is written for this platform: "Control+Alt" on Windows, "Control+Option" on a Mac. */
export function chordLabel(mods: Key[], platform: string = process.platform): string {
  const mac = platform === "darwin";
  const names: Record<Key, string> = { ctrl: "Control", alt: mac ? "Option" : "Alt", shift: "Shift", meta: mac ? "Command" : "Win" };
  const order: Key[] = ["ctrl", "alt", "shift", "meta"];
  return order.filter((m) => mods.includes(m)).map((m) => names[m]).join("+");
}

/** Settings' dictation keys as a chord. "win" is the Windows key, and Command on a Mac. */
export function dictationChord(keys: DictationKeys): Key[] {
  return keys === "ctrl+win" ? ["ctrl", "meta"] : keys === "alt+win" ? ["alt", "meta"] : ["shift", "meta"];
}

// A dictation started by a press ("toggle") is running, or one is being written down: what the
// overlay last reported, and what Esc acts on.
let toggled = false;
let working = false;

/** The overlay says where a dictation is: recording, being written down, or over. Recording
 * that ended by itself (five minutes, no microphone, a password box) ends the press-to-start
 * state too, so the next press starts a new one. */
export function dictationState(state: "idle" | "recording" | "working"): void {
  if (state !== "recording") toggled = false;
  working = state === "working";
}

export function startHoldToTalk(
  events: HoldToTalkEvents,
  dictation: () => DictationPrefs = () => DEFAULT_DICTATION,
  options: HoldToTalkOptions = {},
): boolean {
  const talkChord: Key[] = options.talk ?? ["ctrl", "alt"];
  // A dictation chord chosen in hotkeys.json (or the environment) wins; otherwise Settings' keys.
  const dictateChord = (): Key[] => options.dictate ?? dictationChord(dictation().keys);
  const wantQuietTap = options.quietTap ?? process.platform === "win32";
  const held: Record<Key, boolean> = { ctrl: false, alt: false, meta: false, shift: false };
  let tainted = false; // another key was pressed during this chord
  let holding: Chord | null = null;
  let timer: NodeJS.Timeout | null = null;
  let waitingFor: Chord | null = null; // the chord the timer was started for
  let ctrlDownAt = 0;
  let ctrlClean = false; // nothing else was pressed while Ctrl was down
  let lastTapAt = 0;
  toggled = false;
  working = false;

  const anyHeld = () => held.ctrl || held.alt || held.meta || held.shift;
  const isHeld = (chord: Key[]) => (["ctrl", "alt", "meta", "shift"] as Key[]).every((m) => held[m] === chord.includes(m));
  const clearTimer = () => {
    if (timer) clearTimeout(timer);
    timer = null;
  };
  // Shift is one of the keys only when Settings made it so; otherwise it is "another key".
  const shiftCounts = () => dictateChord().includes("shift");
  const chord = (): Chord | null => {
    if (isHeld(talkChord)) return "talk";
    if (!dictation().enabled || !events.dictateDown) return null;
    return isHeld(dictateChord()) ? "dictate" : null;
  };
  const fire = (fn: (() => void) | undefined, what: string) => {
    try {
      fn?.();
    } catch (err) {
      console.warn(`[pyxi] ${what} failed:`, err);
    }
  };
  // Only Windows opens Start when its key goes up alone, and only a chord with that key needs it.
  const quietTap = () => {
    if (!wantQuietTap || !dictateChord().includes("meta")) return;
    try {
      uIOhook.keyTap(QUIET_KEY);
    } catch {
      /* Start may open on release; dictation still works */
    }
  };
  const release = (how: "up" | "cancel") => {
    const was = holding;
    holding = null;
    if (was === "talk") fire(how === "up" ? events.up : events.cancel, "push-to-talk");
    else if (was === "dictate") fire(how === "up" ? events.dictateUp : events.dictateCancel, "dictation");
  };

  // An exception thrown inside a hook callback aborts the whole app (napi fatal error), so
  // every handler is guarded.
  const safe = <T,>(fn: (e: T) => void) => (e: T) => {
    try {
      fn(e);
    } catch (err) {
      console.warn("[pyxi] keyboard hook handler failed:", err);
    }
  };

  uIOhook.on("keydown", safe((e) => {
    if (e.keycode === QUIET_KEY) return; // our own, see above
    // Esc, alone: a dictation started by a press, or one being written down, is dropped.
    if (e.keycode === ESCAPE && (toggled || working) && !held.ctrl && !held.alt && !held.meta) {
      toggled = false;
      working = false;
      fire(events.dictateCancel, "dictation cancel");
      return;
    }
    const isShift = e.keycode === LEFT_SHIFT && shiftCounts();
    const modifier = CTRL.has(e.keycode) || e.keycode === LEFT_ALT || e.keycode === LEFT_WIN || isShift;
    if (CTRL.has(e.keycode)) {
      if (!held.ctrl) {
        ctrlDownAt = Date.now();
        ctrlClean = !held.alt && !held.meta;
      }
      held.ctrl = true;
    } else {
      if (e.keycode === LEFT_ALT) held.alt = true;
      if (e.keycode === LEFT_WIN) held.meta = true;
      if (isShift) held.shift = true;
      ctrlClean = false;
      lastTapAt = 0;
    }
    if (!modifier && anyHeld()) {
      tainted = true;
      clearTimer();
      if (holding) release("cancel");
      return;
    }
    if (holding && holding !== chord()) {
      tainted = true; // Ctrl+Alt+Win, or the chord changed mid-way: neither
      release("cancel");
      return;
    }
    // Started by a press: the next press of the keys stops it at once and types the words.
    if (toggled && chord() === "dictate" && !tainted && !holding) {
      toggled = false;
      tainted = true; // still held: this press must not start another
      clearTimer();
      quietTap();
      fire(events.dictateUp, "dictation stop");
      return;
    }
    // A third key made it another chord (a chosen Control+Option+Command): wait for that one.
    if (timer && chord() !== waitingFor) clearTimer();
    if (chord() && !tainted && !holding && !timer) {
      const wanted = chord();
      waitingFor = wanted;
      timer = setTimeout(() => {
        timer = null;
        if (chord() === wanted && !tainted) {
          if (wanted === "dictate") {
            quietTap();
            if (dictation().mode === "toggle") {
              toggled = true;
              tainted = true; // letting go is not the end of it; the next press is
            } else holding = wanted;
            fire(events.dictateDown, "dictation start");
          } else {
            holding = wanted;
            fire(events.down, "push-to-talk start");
          }
        }
      }, HOLD_MS);
    }
  }));

  uIOhook.on("keyup", safe((e) => {
    if (e.keycode === QUIET_KEY) return;
    if (CTRL.has(e.keycode)) {
      held.ctrl = false;
      const now = Date.now();
      if (ctrlClean && !held.alt && !held.meta && now - ctrlDownAt < TAP_MS) {
        if (lastTapAt && ctrlDownAt - lastTapAt < TAP_GAP_MS) {
          lastTapAt = 0;
          events.doubleTap();
        } else lastTapAt = now;
      } else lastTapAt = 0;
    } else if (e.keycode === LEFT_ALT) held.alt = false;
    else if (e.keycode === LEFT_WIN) held.meta = false;
    else if (e.keycode === LEFT_SHIFT && held.shift) held.shift = false;
    else return;
    clearTimer();
    if (holding) release("up");
    if (!anyHeld()) tainted = false;
  }));

  uIOhook.on("mousedown", safe((e) => {
    if (e.button === 1) events.click?.(e.x, e.y);
  }));

  if (events.move) {
    const move = events.move;
    uIOhook.on("input", safe((e: { type: number }) => {
      if (MOUSE_MOVES.has(e.type)) move();
    }));
  }

  try {
    uIOhook.start();
    return true;
  } catch (err) {
    console.warn("[pyxi] keyboard hook unavailable, hold-to-talk disabled:", err);
    return false;
  }
}

/** Paste in whatever window has focus (used to drop a drafted message in): Ctrl+V, Cmd+V on a Mac. */
export function pasteText(platform: string = process.platform): void {
  try {
    uIOhook.keyTap(UiohookKey.V, [platform === "darwin" ? UiohookKey.Meta : UiohookKey.Ctrl]);
  } catch (err) {
    console.warn("[pyxi] couldn't paste:", err);
  }
}

export function stopHoldToTalk(): void {
  try {
    uIOhook.stop();
  } catch {
    /* not running */
  }
}
