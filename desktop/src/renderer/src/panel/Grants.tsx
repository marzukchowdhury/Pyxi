// Settings: what Pyxi may do without asking first ("Always allow"), until when, and Revoke.
// Grants last 30 days (pyxi/grants.py); after reading mail, a page or a document Pyxi asks anyway.
import { useEffect, useState } from "react";
import { brain } from "../shared/socket";
import type { Grant } from "../shared/protocol";

const day = (ts: number) => new Date(ts * 1000).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });

export function Grants() {
  const [grants, setGrants] = useState<Grant[] | null>(null);
  useEffect(() => {
    const off = brain.on((e) => {
      if (e.type === "grants") setGrants(e.grants);
    });
    brain.send({ type: "grants_get" });
    return off;
  }, []);
  if (!grants) return null;
  return (
    <div className="file-access grants">
      <ul className="folders">
        {grants.map((g) => (
          <li key={g.kind}>
            <span title={`Allowed on ${day(g.granted)}`}>{g.label}, until {day(g.expires)}</span>
            <button className="ghost" aria-label={`Revoke: ${g.label}`} onClick={() => brain.send({ type: "grant_revoke", kind: g.kind })}>Revoke</button>
          </li>
        ))}
        {!grants.length && <li className="muted">Nothing. Pyxi asks every time.</li>}
      </ul>
      <p className="muted">"Always allow" lasts 30 days. Sending, buying, booking, deleting and posting still ask every time, and so does anything right after Pyxi reads an email, a web page or a document.</p>
    </div>
  );
}
