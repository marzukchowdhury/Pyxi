// The overlay is click-through by default. Wrapping something in <Interactive> makes the
// window catch the mouse only while the pointer is over it.
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

export function Interactive({ children, className, style, always = false }: { children: ReactNode; className?: string; style?: CSSProperties; always?: boolean }) {
  const inside = useRef(false);
  useEffect(() => {
    if (always) {
      acquire();
      return release;
    }
    return () => {
      if (inside.current) release();
    };
  }, [always]);
  return (
    <div
      className={className}
      style={style}
      onMouseEnter={() => {
        if (!always && !inside.current) {
          inside.current = true;
          acquire();
        }
      }}
      onMouseLeave={() => {
        if (!always && inside.current) {
          inside.current = false;
          release();
        }
      }}
    >
      {children}
    </div>
  );
}
