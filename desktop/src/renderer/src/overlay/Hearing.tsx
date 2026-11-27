/**
 * What Pyxi is hearing, on screen for as long as it hears it.
 *
 * - The meeting sign: a small pill ("Taking notes 12:05", Stop) while a call is being noted,
 *   and "Writing the notes" after it. Stop is one click.
 * - The caption bar: the words of whatever the PC is playing, two lines at most, near the
 *   bottom of the screen the sound's window is on. The mouse goes straight through the words;
 *   only its own small buttons (language, smaller, bigger, move, stop) catch it.
 *
 * Both come from the brain's `listening` event; the words come as `caption` events and are
 * kept nowhere.
 */
import { useEffect, useRef, useState } from "react";
import { onThisDisplay } from "../shared/bridge";
import type { CaptionState, MeetingSign } from "../shared/protocol";
import { brain } from "../shared/socket";
import { type CaptionLine, SIZES, elapsed, fresh, shown, withCaption } from "./captionLines";
import { Interactive } from "./Interactive";
import { isPrimary } from "./store";
import "./hearing.css";

export function Hearing() {
  const [meeting, setMeeting] = useState<MeetingSign | null>(null);
  const [captions, setCaptions] = useState<CaptionState | null>(null);
  const [lines, setLines] = useState<CaptionLine[]>([]);

  useEffect(
    () =>
      brain.on((e) => {
        if (e.type === "listening") {
          setMeeting(e.meeting);
          setCaptions(e.captions);
          if (!e.captions) setLines([]);
        } else if (e.type === "caption") {
          setLines((ls) => withCaption(ls, e, performance.now()));
        }
      }),
    [],
  );
  // The brain went away (closed, restarted): nothing is being heard any more.
  useEffect(
    () =>
      brain.onStatus((up) => {
        if (up) return;
        setMeeting(null);
        setCaptions(null);
        setLines([]);
      }),
    [],
  );
  // Lines leave a few seconds after the talking stops.
  useEffect(() => {
    if (!lines.length) return;
    const t = setInterval(() => setLines((ls) => (fresh(ls, performance.now()).length === ls.length ? ls : fresh(ls, performance.now()))), 500);
    return () => clearInterval(t);
  }, [lines.length > 0]);

  const here = captions ? (captions.screen ? onThisDisplay(captions.screen[0] + captions.screen[2] / 2, captions.screen[1] + captions.screen[3] / 2) : isPrimary) : false;
  return (
    <>
      {meeting && isPrimary && <MeetingPill meeting={meeting} raised={here && captions?.edge === "bottom"} />}
      {captions && here && <CaptionBar captions={captions} lines={lines} />}
    </>
  );
}

function MeetingPill({ meeting, raised }: { meeting: MeetingSign; raised: boolean }) {
  const [, tick] = useState(0);
  useEffect(() => {
    const t = setInterval(() => tick((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, []);
  const writing = meeting.state === "writing";
  return (
    <Interactive className={`hear-pill ${writing ? "writing" : ""} ${raised ? "above-captions" : ""}`}>
      <i className="hear-dot" />
      <div className="hear-pill-text">
        <b>{writing ? "Writing the notes" : "Taking notes"}</b>
        {!writing && <span className="hear-time">{elapsed(meeting.since, Date.now())}</span>}
        {meeting.note && <small>{meeting.note}</small>}
      </div>
      <button
        className="hear-stop"
        title={writing ? "Stop writing and save what there is" : "Stop listening and write the notes"}
        onClick={() => brain.send({ type: "listen", what: "meeting", action: "stop" })}
      >
        Stop
      </button>
    </Interactive>
  );
}

function CaptionBar({ captions, lines }: { captions: CaptionState; lines: CaptionLine[] }) {
  const [menu, setMenu] = useState(false);
  const closing = useRef<ReturnType<typeof setTimeout> | null>(null);
  const size = SIZES[Math.max(0, Math.min(SIZES.length - 1, captions.size))];
  const translating = !!captions.translate;
  const text = shown(lines, translating, captions.both);
  const send = (msg: { action: "stop" | "set" | "size" | "move"; translate?: string; both?: boolean; delta?: number }) =>
    brain.send({ type: "listen", what: "captions", ...msg });
  const pick = (translate: string) => {
    setMenu(false);
    send({ action: "set", translate });
  };
  // The menu closes a moment after the pointer has left both it and the buttons.
  const leave = () => {
    if (closing.current) clearTimeout(closing.current);
    closing.current = setTimeout(() => setMenu(false), 700);
  };
  const enter = () => {
    if (closing.current) clearTimeout(closing.current);
  };
  useEffect(() => () => {
    if (closing.current) clearTimeout(closing.current);
  }, []);
  return (
    <div className={`cap-bar ${captions.edge}`} style={{ ["--cap-size" as string]: `${size}px` }}>
      <div className={`cap-plate ${text.main ? "" : "empty"}`}>
        {text.original && <div className="cap-original"><span>{text.original}</span></div>}
        <div className={`cap-text ${text.waiting ? "waiting" : ""}`}>
          <span>{text.main || (captions.note ? captions.note : "Listening for speech")}</span>
        </div>
      </div>
      <Interactive className="cap-tools" onLeave={leave} onEnter={enter}>
        {menu && (
          // (Its own hit area: it opens outside the row of buttons.)
          <Interactive className="cap-menu" onLeave={leave} onEnter={enter}>
            <button className={translating ? "" : "on"} onClick={() => pick("")}>As spoken</button>
            {captions.languages.map((name) => (
              <button key={name} className={captions.translate === name ? "on" : ""} onClick={() => pick(name)}>
                {name}
              </button>
            ))}
            <label>
              <input type="checkbox" checked={captions.both} onChange={(e) => send({ action: "set", both: e.target.checked })} />
              Show the original too
            </label>
          </Interactive>
        )}
        <button title="Language" className={translating ? "on" : ""} onClick={() => setMenu((m) => !m)}>
          {translating ? captions.translate.slice(0, 2).toUpperCase() : "Aa"}
        </button>
        <button title="Smaller text" onClick={() => send({ action: "size", delta: -1 })}>A-</button>
        <button title="Bigger text" onClick={() => send({ action: "size", delta: 1 })}>A+</button>
        <button title="Move the captions" onClick={() => send({ action: "move" })}>Move</button>
        <button title="Stop the captions" className="cap-stop" onClick={() => send({ action: "stop" })}>Stop</button>
      </Interactive>
    </div>
  );
}
