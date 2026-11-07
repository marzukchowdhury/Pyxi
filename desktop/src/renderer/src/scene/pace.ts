/**
 * Frames only while something moves. The overlay is a transparent full-screen window, so every
 * frame costs the GPU process real CPU even when the picture is identical; a model or scene
 * left open (paused, finished, waiting) should cost nothing. Canvases run with
 * frameloop="demand": this keeps asking for the next frame while the view is playing, and for a
 * short settling time after anything changes (fades, fly-ins and the camera spring all finish
 * well inside it). Dragging and zooming wake it through OrbitControls.
 */
import { useFrame, useThree } from "@react-three/fiber";
import { createContext, type DependencyList, useCallback, useContext, useEffect, useRef } from "react";

export const SETTLE_MS = 3000;

/** Pure: should the next frame be drawn? */
export function keepDrawing(active: boolean, now: number, until: number): boolean {
  return active || now < until;
}

/** Call inside a Canvas. `active()` is read every frame (a ref, so playback never re-renders
 * React); `deps` changing wakes it for SETTLE_MS. Returns `wake(ms?)` for anything else that
 * starts a short animation (a hover). */
export function usePace(active: () => boolean, deps: DependencyList, settleMs = SETTLE_MS): (ms?: number) => void {
  const invalidate = useThree((s) => s.invalidate);
  const until = useRef(0);
  const wake = useCallback(
    (ms = settleMs) => {
      until.current = Math.max(until.current, performance.now() + ms);
      invalidate();
    },
    [invalidate, settleMs],
  );
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => wake(), deps);
  useFrame(() => {
    if (import.meta.env.DEV) {
      const w = window as unknown as { __pyxiFrames?: number };
      w.__pyxiFrames = (w.__pyxiFrames ?? 0) + 1; // for scripts/frames_3d.py
    }
    if (keepDrawing(active(), performance.now(), until.current)) invalidate();
  });
  return wake;
}

/** For pieces deep in the scene (a hovered node) to wake the canvas. */
export const PaceContext = createContext<(ms?: number) => void>(() => {});
export const useWake = () => useContext(PaceContext);
