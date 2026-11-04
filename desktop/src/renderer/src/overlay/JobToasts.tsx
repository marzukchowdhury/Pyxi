/**
 * Quiet results from background work. When a teammate finishes, nobody talks: a card slides in
 * at the top right with what was done, the file, and the next steps (open it, show it in its
 * folder, send it back). It stays until you dismiss it.
 */
import { useState } from "react";
import { bridge } from "../shared/bridge";
import { Close } from "../shared/icons";
import { brain } from "../shared/socket";
import { Interactive } from "./Interactive";
import { useOverlay } from "./store";

const fileName = (path: string) => path.split(/[\\/]/).pop() ?? path;

export function JobToasts() {
  const toasts = useOverlay((s) => s.toasts);
  const dismiss = useOverlay((s) => s.dismissToast);
  if (!toasts.length) return null;
  return (
    <div className="toasts">
      {toasts.map((t) => <Toast key={t.job_id} t={t} onClose={() => dismiss(t.job_id)} />)}
    </div>
  );
}

function Toast({ t, onClose }: { t: ReturnType<typeof useOverlay.getState>["toasts"][number]; onClose: () => void }) {
  const [more, setMore] = useState(false);
  const shown = more ? t.changes : t.changes.slice(0, 3);
  const file = t.files[0];
  return (
    <Interactive className={`toast ${t.ok ? "" : "failed"}`}>
      <div className="toast-head">
        <span className="toast-mark" />
        <div className="toast-title">
          <b>{t.title}</b>
          <span>{t.agent}</span>
        </div>
        <button className="toast-x" onClick={onClose} aria-label="Dismiss"><Close /></button>
      </div>
      <p className="toast-body">{t.body}</p>
      {shown.length > 0 && (
        <ul className="toast-changes">
          {shown.map((c) => <li key={c}>{c}</li>)}
          {t.changes.length > 3 && (
            <li className="toast-more"><button onClick={() => setMore(!more)}>{more ? "Show fewer" : `+${t.changes.length - 3} more`}</button></li>
          )}
        </ul>
      )}
      {t.notes.length > 0 && <div className="toast-note"><b>Worth a look:</b> {t.notes.join(" ")}</div>}
      {file && (
        <button className="toast-file" onClick={() => bridge.openFile(file)} title={file}>
          <span className="doc-glyph" />
          {fileName(file)}
        </button>
      )}
      <div className="toast-actions">
        {t.actions.includes("open") && file && <button className="primary" onClick={() => bridge.openFile(file)}>Open</button>}
        {t.actions.includes("folder") && file && <button onClick={() => bridge.showInFolder(file)}>Show in folder</button>}
        {t.actions.includes("send_back") && (
          <button onClick={() => { brain.send({ type: "text", text: `Send ${fileName(file ?? "the fixed file")} back to the person who emailed me the original, as a reply.` }); onClose(); }}>
            Send it back
          </button>
        )}
        {t.actions.includes("walkthrough") && <button onClick={() => { brain.send({ type: "walkthrough", job_id: t.job_id }); onClose(); }}>Show me how</button>}
      </div>
    </Interactive>
  );
}
