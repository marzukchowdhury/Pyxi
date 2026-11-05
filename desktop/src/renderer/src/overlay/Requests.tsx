// Approval and question toasts from teammates: "Can Scout click Submit?" right on screen.
import { useState } from "react";
import { Interactive } from "./Interactive";
import { useOverlay } from "./store";

export function Requests() {
  const requests = useOverlay((s) => s.requests);
  const answer = useOverlay((s) => s.answer);
  if (!requests.length) return null;
  return (
    <div className="requests">
      {requests.map((r) => (
        <Interactive key={r.request_id} className="request">
          {r.kind === "confirm" ? (
            <>
              <div className="req-title">{r.agent} needs your OK</div>
              <div className="req-action">{r.action}</div>
              <div className="req-reason">{r.reason}</div>
              <div className="req-buttons">
                <button className="yes" onClick={() => answer(r.request_id, true)}>Allow</button>
                {r.always && <button onClick={() => answer(r.request_id, "always")}>{r.always}</button>}
                <button onClick={() => answer(r.request_id, false)}>Don't</button>
              </div>
            </>
          ) : (
            <Ask id={r.request_id} agent={r.agent} question={r.question ?? ""} onAnswer={answer} />
          )}
        </Interactive>
      ))}
    </div>
  );
}

function Ask({ id, agent, question, onAnswer }: { id: string; agent: string; question: string; onAnswer: (id: string, v: unknown) => void }) {
  const [text, setText] = useState("");
  return (
    <>
      <div className="req-title">{agent} has a question</div>
      <div className="req-action">{question}</div>
      <form className="req-buttons" onSubmit={(e) => { e.preventDefault(); onAnswer(id, text); }}>
        <input autoFocus value={text} onChange={(e) => setText(e.target.value)} placeholder="Your answer" />
        <button className="yes" type="submit">Send</button>
        <button type="button" onClick={() => onAnswer(id, null)}>Skip</button>
      </form>
    </>
  );
}
