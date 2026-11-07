// The Memory tab: what Pyxi (and its teammates) remember about you, each with "Forget this",
// and "Wipe everything" (which asks on a card first). Passwords, card numbers, ID numbers and
// keys are never saved in the first place (pyxi/memory_guard.py).
import { useEffect, useState } from "react";
import { brain } from "../shared/socket";
import type { MemoryItem } from "../shared/protocol";

export function Memories() {
  const [items, setItems] = useState<MemoryItem[] | null>(null);
  useEffect(() => {
    const off = brain.on((e) => {
      if (e.type === "memories") setItems(e.memories);
    });
    brain.send({ type: "memories_get" });
    return off;
  }, []);
  if (!items) return null;
  return (
    <div className="file-access memories">
      <h3>Things Pyxi remembers <span className="count">{items.length}</span></h3>
      <ul className="folders">
        {items.map((m) => (
          <li key={`${m.store}:${m.scope}:${m.key}`}>
            <span title={m.store === "teammates" ? `A teammate's memory (${m.scope})` : "Pyxi's memory"}>
              <b>{m.key}</b>: {m.content}
            </span>
            <button className="ghost" aria-label={`Forget ${m.key}`} onClick={() => brain.send({ type: "memory_forget", store: m.store, scope: m.scope, key: m.key })}>Forget this</button>
          </li>
        ))}
        {!items.length && <li className="muted">Nothing yet. Tell Pyxi "remember that…" and it shows up here.</li>}
      </ul>
      <p className="muted">Pyxi never saves passwords, card numbers, ID numbers or keys.</p>
      <button className="danger" onClick={() => brain.send({ type: "memories_wipe" })}>Wipe everything…</button>
    </div>
  );
}
