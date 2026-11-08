// The overlay is click-through by default. Wrapping something in <Interactive> makes the
// window catch the mouse only while the pointer is over it (or always, with `always`).
//
// Hover is hit-tested here from the cursor feed (Electron's main process streams the cursor
// position) instead of relying on forwarded mouse events, which don't reach a click-through
// window on every Windows setup. Once the cursor is over an interactive element the window
// stops ignoring the mouse, so clicks, scrolling and dragging work normally.
import { type CSSProperties, type ReactNode, useEffect, useRef } from "react";
import { bridge, cursorToLocal } from "../shared/bridge";

interface Entry {
  el: HTMLElement;
  always: boolean;
  active: boolean; // false: purely visual, the mouse goes straight through
  rect?: () => Box; // where it will be, for things that animate their size
  inside: boolean;
  onEnter?: () => void;
  onLeave?: () => void;
}

type Box = { left: number; top: number; right: number; bottom: number };

const entries = new Set<Entry>();
if (import.meta.env.DEV) (window as unknown as { __pyxiHits: Set<Entry> }).__pyxiHits = entries; // for debugging hover
let interactive = false;
const PAD = 2;

function sync(): void {
  const want = [...entries].some((e) => e.active && (e.always || e.inside));
  if (want !== interactive) {
    interactive = want;
    bridge.setInteractive(want);
  }
}

function hitTest(x: number, y: number): void {
  for (const e of entries) {
    const r = e.rect?.() ?? e.el.getBoundingClientRect();
    const inside = e.active && r.right > r.left && x >= r.left - PAD && x <= r.right + PAD && y >= r.top - PAD && y <= r.bottom + PAD;
    if (inside !== e.inside) {
      e.inside = inside;
      if (inside) e.onEnter?.();
      else e.onLeave?.();
    }
  }
  sync();
}

let feeding = false;
function startFeed(): void {
  if (feeding) return;
  feeding = true;
  // At most once a frame, with the latest position: measuring every element on every cursor
  // message (several a frame) was most of the overlay's work while the mouse moved.
  let latest: [number, number] = [-1e6, -1e6];
  let queued = false;
  bridge.onCursor((p) => {
    latest = cursorToLocal(p) ?? [-1e6, -1e6]; // off this display: nothing here is hovered
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => {
      queued = false;
      hitTest(latest[0], latest[1]);
    });
  });
}

interface Props {
  children?: ReactNode;
  className?: string;
  style?: CSSProperties;
  always?: boolean;
  /** When false the element is drawn but never catches the mouse. */
  active?: boolean;
  /** Hit-test this box instead of the element's current one (for growing, animating things). */
  hitRect?: () => Box;
  onEnter?: () => void;
  onLeave?: () => void;
}

export function Interactive({ children, className, style, always = false, active = true, hitRect, onEnter, onLeave }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const entry = useRef<Entry | null>(null);
  const handlers = useRef({ onEnter, onLeave, hitRect });
  handlers.current = { onEnter, onLeave, hitRect };

  useEffect(() => {
    startFeed();
    const el = ref.current;
    if (!el) return;
    const e: Entry = {
      el,
      always,
      active,
      rect: () => (handlers.current.hitRect ?? (() => el.getBoundingClientRect()))(),
      inside: false,
      onEnter: () => handlers.current.onEnter?.(),
      onLeave: () => handlers.current.onLeave?.(),
    };
    entry.current = e;
    entries.add(e);
    sync();
    return () => {
      entries.delete(e);
      if (e.inside) e.onLeave?.();
      sync();
    };
  }, []);

  useEffect(() => {
    const e = entry.current;
    if (!e) return;
    e.always = always;
    e.active = active;
    if (!active && e.inside) {
      e.inside = false;
      e.onLeave?.();
    }
    sync();
  }, [always, active]);

  return (
    <div ref={ref} className={className} style={style}>
      {children}
    </div>
  );
}
