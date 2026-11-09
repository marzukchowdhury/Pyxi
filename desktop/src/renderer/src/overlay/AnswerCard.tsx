/**
 * The answer card: the cue card's sibling for an answer that isn't about pixels on screen.
 * A pick with its price, up to three facts, where they came from, a picture when the brain found
 * a real one, and one button that opens the shop's search results for it. The same card carries
 * a teammate's finished result, every line of it. It isn't tied to a window, so it stays up when
 * the page changes, then fades once it has been up a while with nobody talking.
 */
import { useEffect, useRef, useState } from "react";
import { bridge } from "../shared/bridge";
import { Close } from "../shared/icons";
import { trusted } from "../shared/trusted";
import { cardButton, cardImage, cardKicker, cardSources, lingerMs } from "./answerCardText";
import { Interactive } from "./Interactive";
import "./notesCard.css";
import { useOverlay, voice } from "./store";

const CARD_W = 372; // the cue card's width (NotesCard.tsx)

export function AnswerCard() {
  const card = useOverlay((s) => s.answerCard);
  const close = useOverlay((s) => s.closeAnswer);
  const [reading, setReading] = useState(false); // the pointer is on the card
  const [leaving, setLeaving] = useState(false);
  const idleSince = useRef(0);

  useEffect(() => {
    if (!card) return;
    setLeaving(false);
    idleSince.current = 0;
    const linger = lingerMs(card);
    const id = window.setInterval(() => {
      const s = useOverlay.getState();
      if (reading || s.state !== "idle" || voice.busy) {
        idleSince.current = 0;
        setLeaving(false);
        return;
      }
      if (!idleSince.current) idleSince.current = performance.now();
      const idle = performance.now() - idleSince.current;
      if (idle > linger) close();
      else if (idle > linger - 700) setLeaving(true);
    }, 250);
    return () => window.clearInterval(id);
  }, [card?.id, reading, close]);

  if (!card) return null;
  const image = cardImage(card.image);
  const sources = cardSources(card);
  const button = cardButton(card);
  return (
    <Interactive
      className={`notes-card notes-lg answer-card kind-${card.kind} ${card.ok ? "" : "failed"} ${leaving ? "leaving" : ""}`}
      style={{ right: 24, top: 84, width: Math.min(CARD_W, innerWidth - 48), maxHeight: innerHeight - 108 }}
      onEnter={() => setReading(true)}
      onLeave={() => setReading(false)}
    >
      <button className="notes-close" onClick={close} aria-label="Close"><Close /></button>
      {image && <img className="answer-image" src={image} alt="" draggable={false} />}
      <div className="answer-kicker">{cardKicker(card)}</div>
      <div className="notes-title">{card.title}</div>
      {card.figure && <div className="answer-figure">{card.figure}</div>}
      {card.note && <div className="answer-note">{card.note}</div>}
      <div className="notes-rule" />
      {card.facts.map((fact, i) => (
        <div key={`${card.id}-${i}`} className="note-row written">
          <span className="note-bullet">
            <svg width="12" height="19" viewBox="0 0 12 22"><path d="M1.5 11a4.5 4.5 0 1 0 9 0a4.5 4.5 0 1 0 -9 0Z" pathLength={1} /></svg>
          </span>
          <span className="note-text" style={{ animationDelay: `${Math.min(1.2, 0.1 + i * 0.12).toFixed(2)}s` }}>{fact}</span>
        </div>
      ))}
      {sources.length > 0 && (
        <div className="answer-sources">
          <span>{card.kind === "answer" ? "Sources" : "Links"}</span>
          {sources.map((s) => (
            <button key={s.url} title={s.url} onClick={trusted(() => bridge.openExternal(s.url))}>{s.name}</button>
          ))}
        </div>
      )}
      {button && (
        <button className="answer-button" title={button.url} onClick={trusted(() => bridge.openExternal(button.url))}>
          {button.name}
          <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M11 4h5v5M16 4l-7 7M14 12v3a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h3" /></svg>
        </button>
      )}
    </Interactive>
  );
}
