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

export function startHoldToTalk(events: HoldToTalkEvents): boolean {
  let ctrl = false;
  let alt = false;
  let win = false;
  let tainted = false; // another key was pressed during this chord
  let holding: Chord | null = null;
  let timer: NodeJS.Timeout | null = null;
  let ctrlDownAt = 0;
  let ctrlClean = false; // nothing else was pressed while Ctrl was down
  let lastTapAt = 0;

  const clearTimer = () => {
    if (timer) clearTimeout(timer);
    timer = null;
  };
  const chord = (): Chord | null =>
    ctrl && alt && !win ? "talk" : ctrl && win && !alt && events.dictateDown ? "dictate" : null;
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
      if (!ctrl) {
        ctrlDownAt = Date.now();
        ctrlClean = !alt && !win;
      }
      ctrl = true;
    } else {
      if (e.keycode === LEFT_ALT) alt = true;
      if (e.keycode === LEFT_WIN) win = true;
      ctrlClean = false;
      lastTapAt = 0;
    }
    if (!modifier && (ctrl || alt || win)) {
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
            try {
              uIOhook.keyTap(QUIET_KEY);
            } catch {
              /* Start may open on release; dictation still works */
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
      ctrl = false;
      const now = Date.now();
      if (ctrlClean && !alt && !win && now - ctrlDownAt < TAP_MS) {
        if (lastTapAt && ctrlDownAt - lastTapAt < TAP_GAP_MS) {
          lastTapAt = 0;
          events.doubleTap();
        } else lastTapAt = now;
      } else lastTapAt = 0;
    } else if (e.keycode === LEFT_ALT) alt = false;
    else if (e.keycode === LEFT_WIN) win = false;
    else return;
    clearTimer();
    if (holding) release("up");
    if (!ctrl && !alt && !win) tainted = false;
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

/** Press Ctrl+V in whatever window has focus (used to drop a drafted message in). */
export function pasteText(): void {
  try {
    uIOhook.keyTap(UiohookKey.V, [UiohookKey.Ctrl]);
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
