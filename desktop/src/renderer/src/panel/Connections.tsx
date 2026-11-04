/**
 * Connected accounts. Each one shows whether it's live and, if not, the exact steps to connect
 * it. Google and LinkedIn sign in through their own pages in your browser; Wisp never sees a
 * password. Nothing is ever sent or posted without an approval card.
 */
import { useEffect, useState } from "react";
import { brain } from "../shared/socket";
import { usePanel } from "./store";

const SIGN_IN = new Set(["google", "linkedin"]);
const GLYPH: Record<string, string> = { google: "G", discord: "D", linkedin: "in" };

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
            <span className={`conn-glyph g-${c.id}`}>{GLYPH[c.id] ?? c.name[0]}</span>
            <div className="conn-text">
              <b>{c.name}</b>
              <span>{c.detail}</span>
            </div>
            {c.connected ? (
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
