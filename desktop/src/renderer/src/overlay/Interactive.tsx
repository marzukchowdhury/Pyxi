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
  inside: boolean;
  onEnter?: () => void;
  onLeave?: () => void;
}

const entries = new Set<Entry>();
let interactive = false;
const PAD = 2;

function sync(): void {
  const want = [...entries].some((e) => e.always || e.inside);
  if (want !== interactive) {
    interactive = want;
    bridge.setInteractive(want);
  }
}

function hitTest(x: number, y: number): void {
  for (const e of entries) {
    const r = e.el.getBoundingClientRect();
    const inside = r.width > 0 && x >= r.left - PAD && x <= r.right + PAD && y >= r.top - PAD && y <= r.bottom + PAD;
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
  bridge.onCursor((p) => {
    const local = cursorToLocal(p);
    if (local) hitTest(local[0], local[1]);
    else hitTest(-1e6, -1e6); // the cursor is on another display
  });
}

interface Props {
  children: ReactNode;
  className?: string;
  style?: CSSProperties;
  always?: boolean;
  onEnter?: () => void;
  onLeave?: () => void;
}

export function Interactive({ children, className, style, always = false, onEnter, onLeave }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const entry = useRef<Entry | null>(null);
  const handlers = useRef({ onEnter, onLeave });
  handlers.current = { onEnter, onLeave };

  useEffect(() => {
    startFeed();
    const el = ref.current;
    if (!el) return;
    const e: Entry = {
      el,
      always,
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
    if (entry.current) {
      entry.current.always = always;
      sync();
    }
  }, [always]);

  return (
    <div ref={ref} className={className} style={style}>
      {children}
    </div>
  );
}
