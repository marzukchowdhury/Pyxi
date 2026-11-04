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
 */
import { UiohookKey, uIOhook } from "uiohook-napi";

export interface HoldToTalkEvents {
  down(): void;
  up(): void;
  cancel(): void;
  doubleTap(): void;
  /** Left clicks, physical screen px. Only forwarded while a guide is waiting for one. */
  click?(x: number, y: number): void;
}

const CTRL = new Set<number>([UiohookKey.Ctrl, UiohookKey.CtrlRight]);
const LEFT_ALT = UiohookKey.Alt;
const HOLD_MS = 200;
const TAP_MS = 220; // a tap is a press shorter than this
const TAP_GAP_MS = 360; // ...and the second tap must start within this of the first

export function startHoldToTalk(events: HoldToTalkEvents): boolean {
  let ctrl = false;
  let alt = false;
  let tainted = false; // another key was pressed during this chord
  let holding = false;
  let timer: NodeJS.Timeout | null = null;
  let ctrlDownAt = 0;
  let ctrlClean = false; // nothing else was pressed while Ctrl was down
  let lastTapAt = 0;

  const clearTimer = () => {
    if (timer) clearTimeout(timer);
    timer = null;
  };

  // An exception thrown inside a hook callback aborts the whole app (napi fatal error), so
  // every handler is guarded.
  const safe = <T,>(fn: (e: T) => void) => (e: T) => {
    try {
      fn(e);
    } catch (err) {
      console.warn("[wisp] keyboard hook handler failed:", err);
    }
  };

  uIOhook.on("keydown", safe((e) => {
    if (CTRL.has(e.keycode)) {
      if (!ctrl) {
        ctrlDownAt = Date.now();
        ctrlClean = !alt;
      }
      ctrl = true;
    } else if (e.keycode === LEFT_ALT) {
      alt = true;
      ctrlClean = false;
      lastTapAt = 0;
    } else {
      ctrlClean = false;
      lastTapAt = 0;
    }
    if (!CTRL.has(e.keycode) && e.keycode !== LEFT_ALT && (ctrl || alt)) {
      tainted = true;
      clearTimer();
      if (holding) {
        holding = false;
        events.cancel();
      }
      return;
    }
    if (ctrl && alt && !tainted && !holding && !timer) {
      timer = setTimeout(() => {
        timer = null;
        if (ctrl && alt && !tainted) {
          holding = true;
          try {
            events.down();
          } catch (err) {
            console.warn("[wisp] push-to-talk start failed:", err);
          }
        }
      }, HOLD_MS);
    }
  }));

  uIOhook.on("keyup", safe((e) => {
    if (CTRL.has(e.keycode)) {
      ctrl = false;
      const now = Date.now();
      if (ctrlClean && !alt && now - ctrlDownAt < TAP_MS) {
        if (lastTapAt && ctrlDownAt - lastTapAt < TAP_GAP_MS) {
          lastTapAt = 0;
          events.doubleTap();
        } else lastTapAt = now;
      } else lastTapAt = 0;
    } else if (e.keycode === LEFT_ALT) alt = false;
    else return;
    clearTimer();
    if (holding) {
      holding = false;
      events.up();
    }
    if (!ctrl && !alt) tainted = false;
  }));

  uIOhook.on("mousedown", safe((e) => {
    if (e.button === 1) events.click?.(e.x, e.y);
  }));

  try {
    uIOhook.start();
    return true;
  } catch (err) {
    console.warn("[wisp] keyboard hook unavailable, hold-to-talk disabled:", err);
    return false;
  }
}

/** Press Ctrl+V in whatever window has focus (used to drop a drafted message in). */
export function pasteText(): void {
  try {
    uIOhook.keyTap(UiohookKey.V, [UiohookKey.Ctrl]);
  } catch (err) {
    console.warn("[wisp] couldn't paste:", err);
  }
}

export function stopHoldToTalk(): void {
  try {
    uIOhook.stop();
  } catch {
    /* not running */
  }
}
