/**
 * Close something a moment after it has finished, unless the pointer is resting on it.
 * Moving the pointer off restarts the wait, so nobody loses a model they're still reading.
 */
import { useEffect, useRef } from "react";

export const LINGER_MS = 2500;

export function useLinger(done: boolean, close: () => void, ms = LINGER_MS) {
  const hovered = useRef(false);
  const timer = useRef<number | null>(null);
  const doneRef = useRef(done);
  const closeRef = useRef(close);
  doneRef.current = done;
  closeRef.current = close;

  const stop = () => {
    if (timer.current != null) window.clearTimeout(timer.current);
    timer.current = null;
  };
  const arm = () => {
    stop();
    if (!doneRef.current || hovered.current) return;
    timer.current = window.setTimeout(() => closeRef.current(), ms);
  };

  useEffect(() => {
    arm();
    return stop;
  }, [done]); // eslint-disable-line react-hooks/exhaustive-deps

  return {
    onEnter: () => {
      hovered.current = true;
      stop();
    },
    onLeave: () => {
      hovered.current = false;
      arm();
    },
  };
}
