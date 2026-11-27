// Dictation's sign: a small pill at the bottom of the main screen. While the microphone is open
// it says so, moves with the voice and offers Cancel; then it shows what was typed, or why
// nothing was (a password box, no text box, silence). It never takes the keyboard.
import { useEffect, useRef, useState } from "react";
import { Close } from "../shared/icons";
import { lingerMs, signText } from "./dictationText";
import { Interactive } from "./Interactive";
import { useOverlay } from "./store";

const BARS = 5;

export function DictationSign({ level, onCancel }: { level: () => number; onCancel: () => void }) {
  const view = useOverlay((s) => s.dictation);
  const bars = useRef<HTMLSpanElement>(null);
  const [elapsed, setElapsed] = useState(0);

  // A finished sign leaves by itself (only if it is still the one showing).
  useEffect(() => {
    if (view?.phase !== "done") return;
    const t = window.setTimeout(() => {
      if (useOverlay.getState().dictation === view) useOverlay.setState({ dictation: null });
    }, lingerMs(view));
    return () => clearTimeout(t);
  }, [view]);

  // While listening: the bars follow the voice, and the clock counts.
  useEffect(() => {
    if (view?.phase !== "listening") return;
    setElapsed(0);
    let raf = 0;
    let shown = 0;
    const tick = (t: number) => {
      const l = Math.min(1, level() * 1.6);
      bars.current?.querySelectorAll("i").forEach((el, i) => {
        const wobble = 0.55 + 0.45 * Math.sin(t / 110 + i * 1.7);
        (el as HTMLElement).style.transform = `scaleY(${(0.22 + l * wobble * 0.78).toFixed(3)})`;
      });
      const now = performance.now() - view.at;
      if (now - shown >= 1000) {
        shown = now;
        setElapsed(now);
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [view, level]);

  if (!view) return null;
  const text = signText(view, view.phase === "listening" ? elapsed : 0);
  const live = view.phase !== "done";
  return (
    <div className="dictation-wrap">
      <Interactive className={`dictation tone-${text.tone}`} active={live}>
        <div className="dictation-head" role="status" aria-live="polite">
          {view.phase === "listening" ? (
            <span ref={bars} className="dictation-bars" aria-hidden>
              {Array.from({ length: BARS }, (_, i) => <i key={i} />)}
            </span>
          ) : (
            <span className={`dictation-dot ${view.phase === "working" ? "spin" : ""}`} aria-hidden />
          )}
          <b>{text.title}</b>
          {live && (
            <button type="button" className="dictation-x" title="Cancel dictation" aria-label="Cancel dictation" onClick={onCancel}>
              <Close />
            </button>
          )}
        </div>
        {text.body && <div className="dictation-body">{text.body}</div>}
        {text.note && <div className="dictation-note">{text.note}</div>}
      </Interactive>
    </div>
  );
}
