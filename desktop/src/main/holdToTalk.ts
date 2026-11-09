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
 * Holding Ctrl + (left) Windows the same way dictates: the words are typed where the cursor is.
 *
 * On a Mac the same chords are Control + Option (talk) and Control + Command (dictate). Either
 * chord can be changed (see `chordsFrom`): any two or three of ctrl, alt/option, meta/cmd/win.
 * The hook needs the Accessibility permission there (and Input Monitoring on some macOS
 * versions); without it `startHoldToTalk` returns false and the shortcuts still work.
 */
import { UiohookKey, uIOhook } from "uiohook-napi";

export interface HoldToTalkEvents {
  down(): void;
  up(): void;
  cancel(): void;
  doubleTap(): void;
  /** Hold Ctrl + (left) Windows: dictation. Same rules as talking: hold, release, any other key cancels. */
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
): { talk: Mod[]; dictate: Mod[] } {
  const talk = parseChord(env.PYXI_TALK_CHORD) ?? parseChord(saved?.talk) ?? ["ctrl", "alt"];
  let dictate = parseChord(env.PYXI_DICTATE_CHORD) ?? parseChord(saved?.dictate) ?? ["ctrl", "meta"];
  if (same(talk, dictate)) dictate = same(talk, ["ctrl", "meta"]) ? ["ctrl", "alt"] : ["ctrl", "meta"];
  return { talk, dictate };
}

const same = (a: Mod[], b: Mod[]) => a.length === b.length && a.every((m) => b.includes(m));

/** How a chord is written for this platform: "Control+Alt" on Windows, "Control+Option" on a Mac. */
export function chordLabel(mods: Mod[], platform: string = process.platform): string {
  const mac = platform === "darwin";
  const names: Record<Mod, string> = { ctrl: "Control", alt: mac ? "Option" : "Alt", meta: mac ? "Command" : "Win" };
  const order: Mod[] = ["ctrl", "alt", "meta"];
  return order.filter((m) => mods.includes(m)).map((m) => names[m]).join("+");
}

export function startHoldToTalk(events: HoldToTalkEvents, options: HoldToTalkOptions = {}): boolean {
  const talkChord = options.talk ?? ["ctrl", "alt"];
  const dictateChord = options.dictate ?? ["ctrl", "meta"];
  const quietTap = options.quietTap ?? process.platform === "win32";
  const held: Record<Mod, boolean> = { ctrl: false, alt: false, meta: false };
  let tainted = false; // another key was pressed during this chord
  let holding: Chord | null = null;
  let timer: NodeJS.Timeout | null = null;
  let ctrlDownAt = 0;
  let ctrlClean = false; // nothing else was pressed while Ctrl was down
  let lastTapAt = 0;

  const anyHeld = () => held.ctrl || held.alt || held.meta;
  const isHeld = (chord: Mod[]) => (["ctrl", "alt", "meta"] as Mod[]).every((m) => held[m] === chord.includes(m));
  const clearTimer = () => {
    if (timer) clearTimeout(timer);
    timer = null;
  };
  const chord = (): Chord | null =>
    isHeld(talkChord) ? "talk" : isHeld(dictateChord) && events.dictateDown ? "dictate" : null;
  const fire = (fn: (() => void) | undefined, what: string) => {
    try {
      fn?.();
    } catch (err) {
      console.warn(`[pyxi] ${what} failed:`, err);
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
    const modifier = CTRL.has(e.keycode) || e.keycode === LEFT_ALT || e.keycode === LEFT_WIN;
    if (CTRL.has(e.keycode)) {
      if (!held.ctrl) {
        ctrlDownAt = Date.now();
        ctrlClean = !held.alt && !held.meta;
      }
      held.ctrl = true;
    } else {
      if (e.keycode === LEFT_ALT) held.alt = true;
      if (e.keycode === LEFT_WIN) held.meta = true;
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
    if (chord() && !tainted && !holding && !timer) {
      const wanted = chord();
      timer = setTimeout(() => {
        timer = null;
        if (chord() === wanted && !tainted) {
          holding = wanted;
          if (wanted === "dictate") {
            if (quietTap && dictateChord.includes("meta")) {
              try {
                uIOhook.keyTap(QUIET_KEY);
              } catch {
                /* Start may open on release; dictation still works */
              }
            }
            fire(events.dictateDown, "dictation start");
          } else fire(events.down, "push-to-talk start");
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
