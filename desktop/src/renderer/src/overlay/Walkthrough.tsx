/**
 * Ghost Walkthrough: replays an agent's run as a tutorial. A ghost pointer glides to each
 * element the agent touched, a soft ring blooms on it, and the agent's own narration explains
 * why. "Try it yourself" opens the page so you can follow along.
 */
import { useEffect, useRef, useState } from "react";
import { brainUrl, bridge } from "../shared/bridge";
import { Close, Next, Prev } from "../shared/icons";
import { Interactive } from "./Interactive";
import { useOverlay, voice } from "./store";

export function Walkthrough() {
  const wt = useOverlay((s) => s.walkthrough);
  const audio = useOverlay((s) => s.walkAudio);
  const { setWalkStep, closeWalkthrough } = useOverlay.getState();
  const [box, setBox] = useState({ w: 1, h: 1 });
  const spoken = useRef("");

  useEffect(() => {
    if (!wt || !wt.playing) return;
    const key = `${wt.walk.id}:${wt.step}`;
    if (spoken.current === key) return;
    const step = wt.walk.steps[wt.step];
    const clip = audio[wt.step];
    const go = () => {
      spoken.current = key;
      voice.enqueue({
        b64: clip?.audio ?? null,
        text: clip?.text ?? step.narration,
        onEnd: () => {
          const s = useOverlay.getState().walkthrough;
          if (s && s.playing && s.step === wt.step && s.step < s.walk.steps.length - 1) setTimeout(() => setWalkStep(s.step + 1), 700);
        },
      });
    };
    if (clip) go();
    else {
      const t = setTimeout(go, 2500);
      return () => clearTimeout(t);
    }
  }, [wt, audio, setWalkStep]);

  if (!wt) return null;
  const step = wt.walk.steps[wt.step];
  const [iw, ih] = step.image_size ?? [1280, 800];
  const sx = box.w / iw;
  const sy = box.h / ih;
  const tb = step.target_box;
  const target = tb ? { x: (tb[0] + tb[2] / 2) * sx, y: (tb[1] + tb[3] / 2) * sy, w: tb[2] * sx, h: tb[3] * sy } : null;
  const total = wt.walk.steps.length;

  return (
    <Interactive className="walk" always>
      <div className="walk-surface">
        <div className="explain-head">
          <div>
            <div className="kicker">Ghost walkthrough<span>{wt.step + 1} / {total}</span></div>
            <div className="explain-title">{wt.walk.title}</div>
          </div>
          <div className="buttons"><button className="icon" onClick={closeWalkthrough} aria-label="Close"><Close /></button></div>
        </div>
        <div className="walk-stage">
          {step.image && (
            <img src={brainUrl(`/file?path=${encodeURIComponent(step.image)}`)} alt=""
              onLoad={(e) => setBox({ w: e.currentTarget.clientWidth, h: e.currentTarget.clientHeight })} />
          )}
          <div className="walk-dim" />
          {target && (
            <div key={wt.step} className="walk-target" style={{ left: target.x - target.w / 2 - 6, top: target.y - target.h / 2 - 6, width: target.w + 12, height: target.h + 12 }}>
              <i className="bloom" /><i className="bloom late" />
            </div>
          )}
          <div className="ghost" style={{ left: target ? target.x : box.w / 2, top: target ? target.y : box.h * 0.6 }}>
            <svg width="16" height="28" viewBox="0 0 12 22"><path d="M1.5 11a4.5 4.5 0 1 0 9 0a4.5 4.5 0 1 0 -9 0Z" /></svg>
          </div>
        </div>
        <div className="step-bar">
          <div className="progress">
            {wt.walk.steps.map((_, i) => <button key={i} className={i < wt.step ? "done" : i === wt.step ? "on" : ""} onClick={() => setWalkStep(i)} />)}
          </div>
          <div className="step-row">
            <div className="step-text" key={wt.step}>
              <div className="step-title">{step.narration}</div>
              <div className="step-narration mono">{step.detail}{step.url ? `  ·  ${new URL(step.url).pathname}` : ""}</div>
            </div>
            <div className="buttons">
              <button className="icon" onClick={() => setWalkStep(wt.step - 1)} disabled={wt.step === 0} aria-label="Back"><Prev /></button>
              <button className="icon" onClick={() => setWalkStep(wt.step + 1)} disabled={wt.step >= total - 1} aria-label="Next"><Next /></button>
              {step.url && <button className="pill" onClick={() => bridge.openExternal(step.url)}>Try it yourself</button>}
            </div>
          </div>
        </div>
      </div>
    </Interactive>
  );
}
