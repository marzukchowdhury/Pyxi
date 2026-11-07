import { useEffect, useState } from "react";
import { brain } from "../shared/socket";
import type { FileAccessState } from "../shared/protocol";

/** "Let Pyxi search my files": off until they turn it on, and only in the folders listed here. */
export function FileAccess() {
  const [access, setAccess] = useState<FileAccessState | null>(null);
  const [adding, setAdding] = useState("");
  useEffect(() => {
    const off = brain.on((e) => {
      if (e.type === "file_access") setAccess({ enabled: e.enabled, folders: e.folders, asked: e.asked });
    });
    brain.send({ type: "file_access_get" });
    return off;
  }, []);
  if (!access) return null;
  const save = (next: Partial<FileAccessState>) => {
    const merged = { ...access, ...next };
    setAccess(merged);
    brain.send({ type: "file_access_save", enabled: merged.enabled, folders: merged.folders });
  };
  const add = () => {
    const path = adding.trim().replace(/^"|"$/g, "");
    if (path && !access.folders.some((f) => f.toLowerCase() === path.toLowerCase())) save({ folders: [...access.folders, path] });
    setAdding("");
  };
  return (
    <div className="file-access">
      <label>
        <input type="checkbox" checked={access.enabled} onChange={(e) => save({ enabled: e.target.checked })} /> Let Pyxi search my files
      </label>
      <p className="muted">
        Pyxi finds files by name (with the Windows Search index) and reads them when you ask, so Pixel and Ledger can convert and edit them. It only looks in these folders, and files stay on this PC.
      </p>
      <ul className="folders">
        {access.folders.map((f) => (
          <li key={f}>
            <code title={f}>{f}</code>
            <button className="ghost" aria-label={`Stop searching ${f}`} onClick={() => save({ folders: access.folders.filter((x) => x !== f) })}>Remove</button>
          </li>
        ))}
        {!access.folders.length && <li className="muted">No folders yet.</li>}
      </ul>
      <form className="folder-add" onSubmit={(e) => { e.preventDefault(); add(); }}>
        <input value={adding} placeholder="Add a folder, e.g. D:\Work" onChange={(e) => setAdding(e.target.value)} />
        <button type="submit" disabled={!adding.trim()}>Add</button>
      </form>
    </div>
  );
}
