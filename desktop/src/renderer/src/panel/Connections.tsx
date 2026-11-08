/**
 * Connected accounts. Each one shows whether it's live and, if not, the exact steps to connect
 * it. Google and LinkedIn sign in through their own pages in your browser; Pyxi never sees a
 * password. Nothing is ever sent or posted without an approval card.
 */
import { useEffect, useState } from "react";
import { BrandLogo } from "../shared/brands";
import { Clock } from "../shared/icons";
import { brain } from "../shared/socket";
import { usePanel } from "./store";

const SIGN_IN = new Set(["google", "linkedin"]);

export function Connections() {
  const list = usePanel((s) => s.connectors);
  const message = usePanel((s) => s.connectorMessage);
  const [open, setOpen] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  useEffect(() => brain.send({ type: "connectors" }), []);
  useEffect(() => setBusy(null), [list]);
  if (!list) return null;
  return (
    <div className="connections">
      {list.map((c) => (
        <div key={c.id} className={`conn ${c.connected ? "on" : ""}`}>
          <div className="conn-row">
            <BrandLogo id={c.id} />
            <div className="conn-text">
              <b>{c.name}</b>
              <span>{c.detail}</span>
            </div>
            {c.connected && c.needs && SIGN_IN.has(c.id) ? (
              <button className="primary" disabled={busy === c.id} onClick={() => { setBusy(c.id); brain.send({ type: "connect", id: c.id }); }}>
                {busy === c.id ? "Check your browser" : c.needs}
              </button>
            ) : c.connected ? (
              SIGN_IN.has(c.id) && <button className="ghost" onClick={() => brain.send({ type: "disconnect", id: c.id })}>Disconnect</button>
            ) : SIGN_IN.has(c.id) && c.detail.startsWith("Ready") ? (
              <button className="primary" disabled={busy === c.id} onClick={() => { setBusy(c.id); brain.send({ type: "connect", id: c.id }); }}>
                {busy === c.id ? "Check your browser" : "Connect"}
              </button>
            ) : (
              <button className="ghost" onClick={() => setOpen(open === c.id ? null : c.id)}>{open === c.id ? "Hide" : "Set up"}</button>
            )}
          </div>
          {!c.connected && open === c.id && (
            <ol className="conn-steps">{c.setup.map((s) => <li key={s}>{s}</li>)}</ol>
          )}
        </div>
      ))}
      {message && <p className="muted">{message}</p>}
      <p className="muted">Reading is instant. Sending, posting and invites always show you an approval card first.</p>
    </div>
  );
}

/** Routines Pyxi runs on a schedule while it's open. Ask for one in plain words. */
export function Routines() {
  const list = usePanel((s) => s.routines);
  useEffect(() => brain.send({ type: "routines" }), []);
  if (!list) return null;
  if (!list.length)
    return <p className="muted">None yet. Try: "Every weekday at 9, check my email for anything from my professor and tell me."</p>;
  return (
    <div className="connections">
      {list.map((r) => (
        <div key={r.id} className="conn">
          <div className="conn-row">
            <span className="conn-glyph g-routine"><Clock /></span>
            <div className="conn-text">
              <b>{r.task}</b>
              <span>{r.when}, next {new Date(r.next_run * 1000).toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" })}</span>
            </div>
            <button className="ghost" onClick={() => brain.send({ type: "routine_delete", id: r.id })}>Delete</button>
          </div>
        </div>
      ))}
    </div>
  );
}
