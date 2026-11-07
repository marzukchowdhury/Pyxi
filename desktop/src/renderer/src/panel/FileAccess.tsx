import { useEffect, useState } from "react";
import { brain } from "../shared/socket";
import type { ConverterState, FileAccessState } from "../shared/protocol";

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
      <Converter />
    </div>
  );
}

/** LibreOffice on demand: exact layouts for Office files as PDFs, and old .doc/.xls/.ppt files. */
function Converter() {
  const [lo, setLo] = useState<ConverterState | null>(null);
  useEffect(() => {
    const off = brain.on((e) => {
      if (e.type === "libreoffice") setLo((prev) => ({ ...(prev ?? {}), ...e }) as ConverterState);
    });
    brain.send({ type: "libreoffice_get" });
    return off;
  }, []);
  if (!lo) return null;
  const busy = lo.state === "downloading" || lo.state === "checking" || lo.state === "unpacking";
  const set = (allowed: boolean) => brain.send({ type: "libreoffice_set", allowed });
  return (
    <div className="converter">
      <div className="kv">
        <span>Exact Office layouts (LibreOffice)</span>
        <b>
          {lo.state === "ready"
            ? `Ready${lo.version ? `, ${lo.version}` : ""}`
            : busy
              ? `${lo.message}${lo.state === "downloading" ? ` ${Math.round(lo.progress * 100)}%` : ""}`
              : lo.state === "failed"
                ? "Didn't finish"
                : lo.allowed
                  ? "On, downloads when needed"
                  : "Off"}
        </b>
      </div>
      {busy && <progress max={1} value={lo.state === "downloading" ? lo.progress : undefined} />}
      {lo.state === "failed" && lo.message && <p className="muted">{lo.message}</p>}
      <p className="muted">
        Without it, Pixel still turns Office files into PDFs with their text, lists, tables and pictures. With it (a free, one-time download of about 380 MB from The Document Foundation, into Pyxi's own folder, no admin rights), the PDF matches the original exactly and old .doc, .xls and .ppt files work.
      </p>
      {lo.state !== "ready" && !busy && (
        <div className="booking-row">
          <button className="primary" onClick={() => set(true)}>{lo.state === "failed" ? "Try again" : "Download it now"}</button>
          {lo.allowed && <button className="ghost" onClick={() => set(false)}>Don't download it</button>}
        </div>
      )}
    </div>
  );
}
