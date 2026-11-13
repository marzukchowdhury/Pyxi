// Approval and question toasts from teammates: "Can Scout click Submit?" right on screen.
import { useState } from "react";
import { FormAsk } from "./FormAsk";
import { Interactive } from "./Interactive";
import { trusted } from "../shared/trusted";
import { useOverlay } from "./store";

export function Requests() {
  const requests = useOverlay((s) => s.requests);
  const answer = useOverlay((s) => s.answer);
  if (!requests.length) return null;
  return (
    <div className="requests">
      {requests.map((r) => (
        <Interactive key={r.request_id} className={r.fields ? "request form" : "request"}>
          {r.fields ? (
            <FormAsk id={r.request_id} agent={r.agent} question={r.question ?? ""} fields={r.fields} onAnswer={answer} />
          ) : r.kind === "confirm" ? (
            <>
              <div className="req-title">{r.agent} needs your OK</div>
              <div className="req-action">{r.action}</div>
              <div className="req-reason">{r.reason}</div>
              <div className="req-buttons">
                <button className="yes" onClick={trusted(() => answer(r.request_id, true))}>Allow</button>
                {r.always && <button onClick={trusted(() => answer(r.request_id, "always"))}>{r.always}</button>}
                <button onClick={trusted(() => answer(r.request_id, false))}>Don't</button>
              </div>
            </>
          ) : r.handover ? (
            <>
              <div className="req-title">Your turn</div>
              <div className="req-action">{r.question}</div>
              <div className="req-reason">{r.agent} has the browser window open for you and will read the confirmation once you're done.</div>
              <div className="req-buttons">
                <button className="yes" onClick={trusted(() => answer(r.request_id, "done"))}>Done</button>
                <button onClick={trusted(() => answer(r.request_id, null))}>Cancel</button>
              </div>
            </>
          ) : (
            <Ask id={r.request_id} agent={r.agent} question={r.question ?? ""} secret={!!r.secret} onAnswer={answer} />
          )}
        </Interactive>
      ))}
    </div>
  );
}

function Ask({ id, agent, question, secret, onAnswer }: { id: string; agent: string; question: string; secret: boolean; onAnswer: (id: string, v: unknown) => void }) {
  const [text, setText] = useState("");
  return (
    <>
      <div className="req-title">{secret ? `${agent} needs a password` : `${agent} has a question`}</div>
      <div className="req-action">{question}</div>
      {secret && <div className="req-reason">It goes straight to {agent} on this PC. Pyxi's model never sees it, and it isn't saved.</div>}
      <form className="req-buttons" onSubmit={(e) => { e.preventDefault(); if (!e.nativeEvent.isTrusted) return; onAnswer(id, text); setText(""); }}>
        <input
          autoFocus
          type={secret ? "password" : "text"}
          autoComplete={secret ? "off" : undefined}
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={secret ? "Password" : "Your answer"}
        />
        <button className="yes" type="submit">Send</button>
        <button type="button" onClick={() => onAnswer(id, null)}>Skip</button>
      </form>
    </>
  );
}
