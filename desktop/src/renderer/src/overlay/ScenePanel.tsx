/**
 * The explanation surface. Not a floating card: a glass plate whose top edge dissolves into
 * the desktop, with the 3D content rising *above* the plate's boundary, and a thread of light
 * tying it back to the element it explains. On open it grows out of that element. In
 * hologram mode the real UI stays visible through a soft vignette, and exploded views start
 * flat on the actual pixels before lifting off.
 *
 * Steps are narrated (brain audio, OS voice as fallback) and auto-advance; you can orbit,
 * step, switch modes or dismiss. In Memory Palace mode a scrubber moves through time.
 */
import { type CSSProperties, useEffect, useMemo, useRef, useState } from "react";
import { backendUrl, rectToLocal } from "../shared/bridge";
import { Close, Collapse, Expand, Next, Pause, Play, Prev } from "../shared/icons";
import type { SceneNode } from "../shared/protocol";
import { brain } from "../shared/socket";
import { SceneView, type Viewport } from "../scene/SceneView";
import { Interactive } from "./Interactive";
import { useLinger } from "./linger";
import { display, isPrimary, useOverlay, voice } from "./store";

const PLATE = { w: 760, h: 560, rise: 70, text: 178 }; // canvas rises above the plate; text lives in the bottom band

export function ScenePanel() {
  const scene = useOverlay((s) => s.scene);
  const palace = useOverlay((s) => s.palace);
  const audio = useOverlay((s) => (scene ? s.sceneAudio[scene.spec.id] : undefined));
  const { setStep, setPlaying, closeScene, toggleHologram } = useOverlay.getState();
  const [hover, setHover] = useState<SceneNode | null>(null);
  const narrated = useRef("");
  // The last step told and nothing left to play: go away by itself (a memory palace stays, it is explored).
  const done = !!scene && !palace && !scene.playing && narrated.current === `${scene.spec.id}:${scene.spec.steps.length - 1}`;
  const linger = useLinger(done, closeScene);

  // Narrate the current step, then advance (auto-play).
  useEffect(() => {
    if (!scene || !isPrimary) return;
    const key = `${scene.spec.id}:${scene.step}`;
    if (!scene.playing || narrated.current === key) return;
    const step = scene.spec.steps[scene.step];
    const clip = audio?.[scene.step];
    const advance = () => {
      const s = useOverlay.getState().scene;
      if (!s || s.spec.id !== scene.spec.id || !s.playing || s.step !== scene.step) return;
      if (s.step < s.spec.steps.length - 1) setTimeout(() => setStep(s.step + 1), 550);
      else setPlaying(false);
    };
    if (clip || !step?.narration) {
      narrated.current = key;
      if (step?.narration) voice.enqueue({ b64: clip?.audio ?? null, text: clip?.text ?? step.narration, onEnd: advance });
      else setTimeout(advance, 2400);
      return;
    }
    const t = setTimeout(() => {
      if (narrated.current === key) return;
      narrated.current = key;
      voice.enqueue({ b64: null, text: step.narration, onEnd: advance });
    }, 9000); // Wisp's own voice; after that the words show silently
    return () => clearTimeout(t);
  }, [scene, audio, setStep, setPlaying]);

  const screenViewport: Viewport = useMemo(
    () => ({ width: innerWidth, height: innerHeight, scale: display.scaleFactor, origin: display.physOrigin }),
    [],
  );

  if (!scene) return null;
  const { spec, step, playing, hologram } = scene;
  const current = spec.steps[step];
  const total = spec.steps.length;
  const anchor = spec.anchor?.rect ? rectToLocal(spec.anchor.rect) : null;
  const flatStart = hologram && spec.layout === "exploded" && step === 0;

  // Plate placement: beside the anchor if there is one (right side preferred), else the emptiest
  // stretch of screen the brain found, else bottom right.
  const place = scene.place ? rectToLocal(scene.place) : null;
  let left = place ? Math.round(place.x) : innerWidth - PLATE.w - 40;
  let top = place ? Math.round(Math.min(place.y + PLATE.rise, innerHeight - PLATE.h - 16)) : innerHeight - PLATE.h - 48;
  if (anchor) {
    const right = anchor.x + anchor.w + 36;
    left = right + PLATE.w < innerWidth - 16 ? right : Math.max(16, anchor.x - PLATE.w - 36);
    top = Math.min(Math.max(PLATE.rise + 16, anchor.y - 30), innerHeight - PLATE.h - 16);
  }
  const origin = anchor ? `${anchor.x + anchor.w / 2 - left}px ${anchor.y + anchor.h / 2 - top}px` : "85% 100%";

  const controls = (
    <div className="step-bar">
      <div className="progress">
        {spec.steps.map((_, i) => (
          <button key={i} className={i < step ? "done" : i === step ? "on" : ""} onClick={() => setStep(i)} aria-label={`Step ${i + 1}`}>
            {i === step && playing && <i key={`${spec.id}-${step}`} />}
          </button>
        ))}
      </div>
      <div className="step-row">
        <div className="step-text" key={`${spec.id}-${step}`}>
          <div className="step-title">{current?.title}</div>
          <div className="step-narration">{current?.narration}</div>
        </div>
        <div className="buttons">
          <button className="icon" onClick={() => setStep(step - 1)} disabled={step === 0} aria-label="Previous"><Prev /></button>
          <button className="icon primary" onClick={() => { if (!playing) narrated.current = ""; setPlaying(!playing); }} aria-label={playing ? "Pause" : "Play"}>
            {playing ? <Pause /> : <Play />}
          </button>
          <button className="icon" onClick={() => setStep(step + 1)} disabled={step >= total - 1} aria-label="Next"><Next /></button>
        </div>
      </div>
    </div>
  );

  const titleBlock = (
    <div>
      <div className="kicker">{palace ? "Memory Palace" : spec.layout === "exploded" ? "Inside the interface" : "Explaining"}<span>{step + 1} / {total}</span></div>
      <div className="explain-title">{spec.title}</div>
      {spec.subtitle && <div className="explain-sub">{spec.subtitle}</div>}
    </div>
  );
  const tools = (
    <div className="buttons">
      <button className="icon" onClick={toggleHologram} aria-label={hologram ? "Dock" : "Expand"}>{hologram ? <Collapse /> : <Expand />}</button>
      <button className="icon" onClick={closeScene} aria-label="Close"><Close /></button>
    </div>
  );
  const header = <div className="explain-head">{titleBlock}{tools}</div>;

  if (hologram) {
    return (
      <Interactive className={`holo ${flatStart ? "flat" : ""}`} always onEnter={linger.onEnter} onLeave={linger.onLeave}>
        <div className="holo-canvas">
          <SceneView scene={spec} step={step} hologram viewport={screenViewport} cropsBase={backendUrl} onHover={setHover} />
        </div>
        <div className="holo-head">{header}</div>
        {hover && <Tip node={hover} />}
        <div className="holo-foot">{controls}</div>
        {palace && <Scrubber current={palace.snapshotId} timeline={palace.timeline} />}
      </Interactive>
    );
  }

  const style = { left, top, width: PLATE.w, height: PLATE.h, transformOrigin: origin } as CSSProperties;
  const canvasH = PLATE.h - PLATE.text + PLATE.rise;
  return (
    <>
      {anchor && <AnchorTether anchor={anchor} plate={{ left, top, w: PLATE.w, h: PLATE.h }} />}
      <Interactive className="explain" style={style} onEnter={linger.onEnter} onLeave={linger.onLeave}>
        <div className="plate" />
        <div className="horizon" style={{ top: PLATE.h - PLATE.text - 34 }} />
        <div className="explain-canvas" style={{ top: -PLATE.rise, height: canvasH }}>
          <SceneView scene={spec} step={step} hologram={false} viewport={{ ...screenViewport, width: PLATE.w, height: canvasH }}
            cropsBase={backendUrl} onHover={setHover} />
        </div>
        {hover && <Tip node={hover} />}
        <div className="explain-text" style={{ height: PLATE.text }}>
          <div className="explain-head">{titleBlock}{tools}</div>
          {controls}
        </div>
        {palace && <Scrubber current={palace.snapshotId} timeline={palace.timeline} />}
      </Interactive>
    </>
  );
}

function Tip({ node }: { node: SceneNode }) {
  return <div className="node-tip"><b>{node.label}</b>{node.detail && <span>{node.detail}</span>}</div>;
}

/** A soft frame on the explained element and a thread of light to the plate. */
function AnchorTether({ anchor, plate }: { anchor: { x: number; y: number; w: number; h: number }; plate: { left: number; top: number; w: number; h: number } }) {
  const ax = plate.left > anchor.x ? anchor.x + anchor.w : anchor.x;
  const ay = anchor.y + anchor.h / 2;
  const px = plate.left > anchor.x ? plate.left : plate.left + plate.w;
  const py = plate.top + 64;
  const mx = (ax + px) / 2;
  return (
    <svg className="anchor-tether" width="100%" height="100%">
      <rect x={anchor.x - 5} y={anchor.y - 5} width={anchor.w + 10} height={anchor.h + 10} rx="10" className="anchor-frame" />
      <path d={`M ${ax + (px > ax ? 5 : -5)} ${ay} C ${mx} ${ay}, ${mx} ${py}, ${px} ${py}`} className="anchor-line" />
      <circle cx={px} cy={py} r="3" className="anchor-dot" />
    </svg>
  );
}

function Scrubber({ current, timeline }: { current: number; timeline: { id: number; ts: number; app: string; title: string }[] }) {
  const index = timeline.findIndex((t) => t.id === current);
  const seek = (i: number) => {
    const t = timeline[i];
    if (t) brain.send({ type: "palace_seek", snapshot_id: t.id });
  };
  const here = timeline[index];
  return (
    <div className="scrubber">
      <button className="icon" onClick={() => seek(index - 1)} disabled={index <= 0} aria-label="Earlier"><Prev /></button>
      <div className="scrub-track">
        {timeline.map((t, i) => (
          <button key={t.id} className={`tick ${t.id === current ? "on" : ""}`} onClick={() => seek(i)} title={`${t.app} · ${t.title}`}>
            <i />
          </button>
        ))}
      </div>
      <button className="icon" onClick={() => seek(index + 1)} disabled={index >= timeline.length - 1} aria-label="Later"><Next /></button>
      {here && (
        <div className="scrub-label">
          <b>{new Date(here.ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</b>
          <span>{here.app}</span>
        </div>
      )}
    </div>
  );
}
