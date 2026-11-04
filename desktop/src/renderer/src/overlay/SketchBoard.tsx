/**
 * A sketchboard: a diagram that inks itself in while Wisp explains it. Each step's strokes draw
 * as its sentence is spoken; the board waits for both the words and the ink before moving on,
 * then leaves on its own (resting the pointer on it keeps it). A thread ties it to the real
 * thing on screen it's about. Labels can be clicked to ask about them; the cube makes it 3D.
 */
import "@fontsource/caveat/500.css";
import "@fontsource/caveat/700.css";
import { useEffect, useMemo, useRef, useState } from "react";
import { rectToLocal } from "../shared/bridge";
import { Close, Cube, Replay } from "../shared/icons";
import type { SketchShape, Sketchboard } from "../shared/protocol";
import { brain } from "../shared/socket";
import { GRID, drawShape, placeText, smooth, timeline } from "../scene/sketchPaths";
import { Interactive } from "./Interactive";
import { useLinger } from "./linger";
import { useOverlay, voice } from "./store";

const AUDIO_WAIT_MS = 2500; // how long to wait for Wisp's voice before using the OS voice
const CHROME = 112; // header and footer around the drawing, CSS px
const WEIGHT = { thin: 1.3, normal: 2.1, bold: 3.1 };

export function SketchBoard() {
  const sketch = useOverlay((s) => s.sketch);
  const audio = useOverlay((s) => (sketch ? s.sceneAudio[sketch.audioKey] : undefined));
  const close = useOverlay((s) => s.closeSketch);
  const [step, setStep] = useState(0);
  const [done, setDone] = useState(false);
  const [take, setTake] = useState(0); // bumps on replay so every stroke draws again
  const spoken = useRef(new Set<number>());
  const inkedAt = useRef<number[]>([]);
  const started = useRef("");
  const board = sketch?.board;
  const key = sketch ? `${sketch.audioKey}#${take}` : "";

  // A new board (or a replay) starts from the top.
  useEffect(() => {
    spoken.current = new Set();
    inkedAt.current = [];
    started.current = "";
    setStep(0);
    setDone(false);
  }, [key]);

  // Size: the brain's empty spot, keeping the drawing's 100 x 64 shape.
  const place = sketch?.place ? rectToLocal(sketch.place) : null;
  const width = Math.round(place ? Math.min(place.w, ((place.h - CHROME) * GRID.w) / GRID.h) : Math.min(560, innerWidth - 32));
  const scale = width / GRID.w;
  const height = Math.round(GRID.h * scale);
  const left = place ? Math.round(place.x + (place.w - width) / 2) : Math.round(innerWidth - width - 40);
  const top = place ? Math.round(Math.max(8, Math.min(place.y, innerHeight - height - CHROME - 8))) : 90;

  const timing = useMemo(
    () => (board ? board.steps.map((_, i) => timeline(board.shapes, i, scale)) : []),
    [board, scale],
  );

  // When a step starts, note when its ink will be dry.
  useEffect(() => {
    if (!board || !timing[step]) return;
    inkedAt.current[step] = performance.now() + timing[step].total * 1000;
  }, [board, step, timing, key]);

  // Say the step's sentence once its audio arrives (or fall back to the OS voice).
  useEffect(() => {
    if (!sketch || !board) return;
    const s = board.steps[step];
    const k = `${key}:${step}`;
    if (!s || started.current === k) return;
    const speak = (clip?: { audio: string | null; text: string }) => {
      if (started.current === k) return;
      started.current = k;
      voice.enqueue({ b64: clip?.audio ?? null, text: clip?.text ?? s.narration, onEnd: () => spoken.current.add(step) });
    };
    const clip = take === 0 ? audio?.[step] : undefined; // a replay is quiet: the ink tells it
    if (take > 0) {
      spoken.current.add(step);
      started.current = k;
      return;
    }
    if (clip) return speak(clip);
    const t = setTimeout(() => speak(), AUDIO_WAIT_MS);
    return () => clearTimeout(t);
  }, [sketch, board, step, audio, key, take]);

  // Move on once the words are said and the ink is dry; after the last step, we're done.
  useEffect(() => {
    if (!board) return;
    const id = window.setInterval(() => {
      const dry = inkedAt.current[step];
      if (!spoken.current.has(step) || dry == null || performance.now() < dry) return;
      if (step < board.steps.length - 1) setStep(step + 1);
      else setDone(true);
    }, 120);
    return () => window.clearInterval(id);
  }, [board, step, key]);

  const linger = useLinger(done, close);
  if (!sketch || !board) return null;

  const ask = (shape: SketchShape) => {
    const what = shape.ask ?? shape.text ?? "";
    brain.send({ type: "text", text: `In the "${board.title}" drawing, tell me more about ${what}.` });
  };
  const to3d = () => brain.send({ type: "text", text: `Build ${board.title} as a 3D model and explain it.` });
  const current = board.steps[Math.min(step, board.steps.length - 1)];

  return (
    <>
      <Thread board={board} box={{ x: left, y: top, w: width, h: height + CHROME }} />
      <Interactive className="sketch-board" style={{ left, top, width }} onEnter={linger.onEnter} onLeave={linger.onLeave}>
        <div className="sketch-head">
          <div className="sketch-title">{board.title}</div>
          {board.subtitle && <div className="sketch-sub">{board.subtitle}</div>}
          <div className="sketch-tools">
            <button className="icon" onClick={to3d} title="See it in 3D"><Cube /></button>
            <button className="icon" onClick={() => setTake(take + 1)} title="Draw it again"><Replay /></button>
            <button className="icon" onClick={close} title="Close"><Close /></button>
          </div>
        </div>
        <svg className="sketch-art" width={width} height={height} viewBox={`0 0 ${width} ${height}`} key={key}>
          {board.shapes.map((shape, i) =>
            shape.step <= step ? (
              <Stroke key={i} shape={shape} index={i} scale={scale} delay={shape.step === step ? timing[step]?.delays.get(i) ?? 0 : -1} onAsk={ask} />
            ) : null,
          )}
        </svg>
        <div className="sketch-foot">
          <div className="sketch-dots">
            {board.steps.map((_, i) => <i key={i} className={i < step || done ? "done" : i === step ? "on" : ""} />)}
          </div>
          <div className="sketch-caption" key={`${key}-${step}`}>{current.narration}</div>
        </div>
      </Interactive>
    </>
  );
}

/** One shape. delay < 0 means it was drawn in an earlier step: show it finished. */
function Stroke({ shape, index, scale, delay, onAsk }: { shape: SketchShape; index: number; scale: number; delay: number; onAsk: (s: SketchShape) => void }) {
  const ink = `var(--sk-${shape.color})`;
  const late = delay < 0;
  const t = (extra = 0) => ({ animationDelay: late ? "0s" : `${(delay + extra).toFixed(2)}s`, animationDuration: late ? "0s" : undefined });
  const w = WEIGHT[shape.weight] * Math.max(0.8, scale / 5.6);

  if (shape.kind === "text" || shape.kind === "equation") {
    const p = placeText(shape, scale);
    if (!p) return null;
    const eq = shape.kind === "equation";
    return (
      <g className={`sk-text ${eq ? "eq" : "ask"}`} onClick={eq ? undefined : () => onAsk(shape)}>
        {eq && <path className="sk-swash" d={smooth([[p.x - 4, p.y - p.size * 0.1], [p.x + p.width * 0.5, p.y - p.size * 0.16], [p.x + p.width + 4, p.y - p.size * 0.12]])} style={{ ...t(0.35), strokeWidth: p.size * 0.36 }} pathLength={1} />}
        <text x={p.x} y={p.y} style={{ ...t(), fontSize: p.size, fill: eq ? "var(--sk-amber)" : ink }}>{shape.text}</text>
      </g>
    );
  }
  const drawn = drawShape(shape, scale, index * 7919 + 17);
  if (!drawn) return null;
  const dur = late ? undefined : `${Math.min(1.3, 0.45 + drawn.length / scale / 110).toFixed(2)}s`;
  return (
    <g className="sk-shape">
      {shape.fill && drawn.closed && <path className="sk-fill" d={drawn.d} style={{ ...t(0.5), fill: ink }} />}
      <path className={`sk-line ${shape.dashed ? "dashed" : ""}`} d={drawn.d} pathLength={1} style={{ ...t(), animationDuration: dur, stroke: ink, strokeWidth: w }} />
      {drawn.head && <path className="sk-line" d={drawn.head} pathLength={1} style={{ ...t(0.45), stroke: ink, strokeWidth: w }} />}
      {shape.flow && <path className="sk-flow" d={drawn.d} style={{ ...t(0.6), animationDuration: undefined, stroke: ink, color: ink, strokeWidth: w * 1.4, strokeDasharray: `${(scale * 1.2).toFixed(1)} ${(scale * 3.2).toFixed(1)}`, ["--period" as string]: `${(scale * 4.4).toFixed(1)}px` }} />}
    </g>
  );
}

/** A thread from the board to what it explains, and a soft ring around that thing. */
function Thread({ board, box }: { board: Sketchboard; box: { x: number; y: number; w: number; h: number } }) {
  const r = board.anchor?.rect ? rectToLocal(board.anchor.rect) : null;
  if (!r) return null;
  const [ax, ay] = [r.x + r.w / 2, r.y + r.h / 2];
  const bx = ax < box.x ? box.x : ax > box.x + box.w ? box.x + box.w : ax;
  const by = ay < box.y ? box.y : ay > box.y + box.h ? box.y + box.h : Math.max(box.y + 30, Math.min(ay, box.y + box.h - 30));
  // Leave from the board's nearest edge and arrive at the thing's nearest edge.
  const ex = Math.max(r.x, Math.min(bx, r.x + r.w));
  const ey = Math.max(r.y, Math.min(by, r.y + r.h));
  const mid: [number, number] = [(bx + ex) / 2, (by + ey) / 2 - Math.min(60, Math.abs(bx - ex) * 0.18)];
  return (
    <svg className="sketch-thread" width="100%" height="100%">
      <rect className="sk-anchor" x={r.x - 5} y={r.y - 5} width={r.w + 10} height={r.h + 10} rx={14} />
      <path className="sk-thread-line" d={smooth([[bx, by], mid, [ex, ey]])} pathLength={1} />
    </svg>
  );
}
