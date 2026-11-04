// The overlay is click-through by default. Wrapping something in <Interactive> makes the
// window catch the mouse only while the pointer is over it (or always, with `always`).
import { type CSSProperties, type ReactNode, useEffect, useRef } from "react";
import { bridge } from "../shared/bridge";

let holders = 0;
function acquire(): void {
  if (holders++ === 0) bridge.setInteractive(true);
}
function release(): void {
  holders = Math.max(0, holders - 1);
  if (holders === 0) bridge.setInteractive(false);
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
  const hovering = useRef(false);
  const held = useRef(false);
  const alwaysRef = useRef(always);
  // Hold the mouse exactly while (always || hovering); one acquire per component at most.
  const sync = () => {
    const want = alwaysRef.current || hovering.current;
    if (want && !held.current) acquire();
    if (!want && held.current) release();
    held.current = want;
  };
  useEffect(() => {
    alwaysRef.current = always;
    sync();
  }, [always]);
  useEffect(
    () => () => {
      if (held.current) release();
      held.current = false;
    },
    [],
  );
  return (
    <div
      className={className}
      style={style}
      onMouseEnter={() => {
        hovering.current = true;
        sync();
        onEnter?.();
      }}
      onMouseLeave={() => {
        hovering.current = false;
        sync();
        onLeave?.();
      }}
    >
      {children}
    </div>
  );
}
