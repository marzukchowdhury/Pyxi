// What Pyxi did on your behalf (pyxi/audit.py): sent, booked, clicked, moved, written, with the
// time and the teammate, and Undo where there is one. Kept on this PC for 30 days.
import { useEffect, useState } from "react";
import { brain } from "../shared/socket";
import type { AuditEntry } from "../shared/protocol";

const VERB: Record<string, string> = { sent: "Sent", booked: "Booked", clicked: "Clicked", moved: "Moved", wrote: "Wrote", undid: "Undid" };
const when = (ts: number) =>
  new Date(ts * 1000).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });

export function Activity() {
  const [entries, setEntries] = useState<AuditEntry[] | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  useEffect(() => {
    const off = brain.on((e) => {
      if (e.type === "audit") setEntries(e.entries);
    });
    brain.send({ type: "audit_get" });
    const t = setInterval(() => brain.send({ type: "audit_get" }), 15000);
    return () => {
      off();
      clearInterval(t);
    };
  }, []);
  if (!entries) return <p className="muted">Loading…</p>;
  return (
    <div className="activity-log">
      <p className="muted">Everything Pyxi and its teammates did for you: what was sent, booked, clicked, moved or written. Kept on this PC for 30 days.</p>
      {!entries.length && <p className="muted">Nothing yet.</p>}
      <ul>
        {entries.map((e) => (
          <li key={e.id} className={`act act-${e.kind} ${e.undone ? "undone" : ""}`}>
            <div className="act-row" onClick={() => setOpen(open === e.id ? null : e.id)}>
              <span className="act-kind">{VERB[e.kind] ?? e.kind}</span>
              <span className="act-what">{e.what}{e.undone ? " (undone)" : ""}</span>
              <span className="act-meta">{e.agent} · {when(e.ts)}</span>
              {e.can_undo && (
                <button className="ghost" onClick={(ev) => { ev.stopPropagation(); brain.send({ type: "audit_undo", id: e.id }); }}>Undo</button>
              )}
            </div>
            {open === e.id && e.detail && <pre className="act-detail">{e.detail}</pre>}
          </li>
        ))}
      </ul>
    </div>
  );
}
