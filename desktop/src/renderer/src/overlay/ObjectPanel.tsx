/**
 * A 3D model floating on the desktop: no window, no background, just the object, its callouts
 * and a slim glass caption bar. Beats play like a short video: the story clock runs each beat's
 * animation while Wisp narrates it, waits if the sentence runs long, then moves on.
 *
 * Drag to orbit. Click a labelled part to ask about it. The cutaway button ghosts the casing.
 */
import { useEffect, useRef, useState } from "react";
import { Close, Cube, Next, Pause, Play, Prev, Replay } from "../shared/icons";
import type { Part } from "../shared/protocol";
import { rectToLocal } from "../shared/bridge";
import { brain } from "../shared/socket";
import { type Clock, ObjectView } from "../scene/ObjectView";
import { storyDuration } from "../scene/motion";
import { Interactive } from "./Interactive";
import { useLinger } from "./linger";
import { useOverlay, voice } from "./store";

const STAGE = { w: 780, h: 560 };
const CAPTION = 140; // the caption bar under the canvas, in CSS px
const AUDIO_WAIT_MS = 9000; // Wisp's own voice for this step; after that the words show silently

export function ObjectPanel() {
  const object = useOverlay((s) => s.object);
  const audio = useOverlay((s) => (object ? s.sceneAudio[object.audioKey] : undefined));
  const closeObject = useOverlay((s) => s.closeObject);
  const clock = useRef<Clock>({ story: 0, free: 0, playing: true, limit: 0 });
  const [beat, setBeat] = useState(0);
  const [playing, setPlaying] = useState(true);
  const [cutaway, setCutaway] = useState(false);
  const [finished, setFinished] = useState(false); // last beat told and its animation run out
  const spoken = useRef(new Set<number>()); // beats whose narration has finished
  const started = useRef("");
  const spec = object?.spec;
  if (import.meta.env.DEV) (window as unknown as { __wispClock: unknown }).__wispClock = clock;

  // New model (or an edit that replaced the explanation): start from the top.
  useEffect(() => {
    if (!object) return;
    if (object.version === 1 || object.restart) {
      voice.stop();
      clock.current = { story: 0, free: object.version === 1 ? 0 : clock.current.free, playing: object.spec.auto_play, limit: 0 };
      spoken.current = new Set();
      started.current = "";
      setBeat(0);
      setFinished(false);
      setPlaying(object.spec.auto_play);
    }
    setCutaway(object.spec.cutaway);
  }, [object]);

  // Narrate the current beat once its audio arrives (or fall back to the OS voice).
  useEffect(() => {
    if (!object || !spec || !playing) return;
    const b = spec.beats[beat];
    const key = `${object.audioKey}:${beat}`;
    if (!b || started.current === key) return;
    const speak = (clip?: { audio: string | null; text: string }) => {
      if (started.current === key) return;
      started.current = key;
      if (!b.narration) {
        spoken.current.add(beat);
        return;
      }
      voice.enqueue({ b64: clip?.audio ?? null, text: clip?.text ?? b.narration, onEnd: () => spoken.current.add(beat) });
    };
    const clip = audio?.[beat];
    if (clip || !b.narration) {
      speak(clip);
      return;
    }
    const t = setTimeout(() => speak(), AUDIO_WAIT_MS);
    return () => clearTimeout(t);
  }, [object, spec, beat, audio, playing]);

  // The beat clock: let the story run up to the next beat, and cross only once the words are done.
  useEffect(() => {
    if (!spec) return;
    const end = storyDuration(spec);
    const id = window.setInterval(() => {
      const c = clock.current;
      c.playing = playing;
      const next = spec.beats[beat + 1];
      c.limit = next ? next.at : end;
      if (!playing) return;
      if (next && c.story >= next.at - 0.001 && spoken.current.has(beat)) setBeat(beat + 1);
      else if (!next && c.story >= end - 0.001 && spoken.current.has(beat)) setFinished(true);
    }, 60);
    return () => window.clearInterval(id);
  }, [spec, beat, playing]);

  // A beat can switch the cutaway on or off.
  useEffect(() => {
    const b = spec?.beats[beat];
    if (b?.cutaway != null) setCutaway(b.cutaway);
  }, [spec, beat]);

  // Done means gone: the model leaves on its own once the story ends (resting the pointer on it keeps it).
  const linger = useLinger(finished && playing, closeObject);

  if (!object || !spec) return null;
  const current = spec.beats[Math.min(beat, spec.beats.length - 1)];
  const hasCasing = spec.parts.some((p) => p.casing);

  const seek = (i: number) => {
    const b = spec.beats[Math.max(0, Math.min(i, spec.beats.length - 1))];
    if (!b) return;
    voice.stop();
    const i2 = spec.beats.indexOf(b);
    // Move the limit with the clock, or the next frame clamps the story back to the old beat.
    clock.current.limit = spec.beats[i2 + 1]?.at ?? storyDuration(spec);
    clock.current.story = b.at;
    spoken.current.delete(i2);
    started.current = "";
    setBeat(i2);
    setFinished(false);
    setPlaying(true);
  };
  const toggle = () => {
    if (playing) voice.stop();
    else started.current = "";
    setPlaying(!playing);
  };
  const ask = (part: Part) => {
    const name = part.label ?? part.id.replace(/_/g, " ");
    brain.send({ type: "text", text: `In the ${spec.title} model, tell me more about the ${name}.` });
  };

  // Where the brain found the emptiest stretch of screen; failing that, centred a little left.
  const place = object.place ? rectToLocal(object.place) : null;
  const width = place ? Math.round(place.w) : STAGE.w;
  const height = place ? Math.max(220, Math.round(place.h - CAPTION)) : STAGE.h;
  const left = place ? Math.round(place.x) : Math.max(16, Math.round((innerWidth - STAGE.w) / 2 - Math.min(160, innerWidth * 0.08)));
  // Never let the caption run off the bottom of the screen.
  const top = place ? Math.max(8, Math.min(Math.round(place.y), innerHeight - height - CAPTION - 8)) : Math.max(70, (innerHeight - STAGE.h - 120) / 2);
  return (
    <Interactive className="object-stage" style={{ left, top, width }} onEnter={linger.onEnter} onLeave={linger.onLeave}>
      <div className="object-tools">
        {hasCasing && (
          <button className={`icon ${cutaway ? "primary" : ""}`} onClick={() => setCutaway(!cutaway)} title={cutaway ? "Put the casing back" : "Show the inside"}><Cube /></button>
        )}
        <button className="icon" onClick={() => seek(0)} title="Replay"><Replay /></button>
        <button className="icon" onClick={closeObject} title="Close"><Close /></button>
      </div>
      <ObjectView scene={spec} clock={clock} beat={current} beatIndex={beat} cutaway={cutaway} width={width} height={height} onPick={ask} />
      <div className="object-caption">
        <div className="progress">
          {spec.beats.map((_, i) => (
            <button key={i} className={i < beat ? "done" : i === beat ? "on" : ""} onClick={() => seek(i)} aria-label={`Beat ${i + 1}`} />
          ))}
        </div>
        <div className="step-row">
          <div className="step-text" key={`${spec.id}-${beat}-${object.version}`}>
            <div className="kicker">{spec.title}<span>{beat + 1} / {spec.beats.length}</span></div>
            <div className="step-title">{current.title}</div>
            <div className="step-narration">{current.narration}</div>
          </div>
          <div className="buttons">
            <button className="icon" onClick={() => seek(beat - 1)} disabled={beat === 0} aria-label="Previous"><Prev /></button>
            <button className="icon primary" onClick={toggle} aria-label={playing ? "Pause" : "Play"}>{playing ? <Pause /> : <Play />}</button>
            <button className="icon" onClick={() => seek(beat + 1)} disabled={beat >= spec.beats.length - 1} aria-label="Next"><Next /></button>
          </div>
        </div>
      </div>
    </Interactive>
  );
}
