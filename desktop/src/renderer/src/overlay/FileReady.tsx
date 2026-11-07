// The result card for a new file (a PDF Pixel made, a sheet Ledger edited): the file itself lands in
// the notch (Windows' real thumbnail when there is one, else a badge for its type), with Open file
// and Open file location, and you can drag it straight out into an email, a chat or a folder.
import { useEffect, useState } from "react";
import { bridge, type FileInfo } from "../shared/bridge";
import type { BrainEvent } from "../shared/protocol";
import { cleanBody, kindOf, readyTitle, sizeText } from "./fileReadyText";

type Ready = Omit<Extract<BrainEvent, { type: "job_ready" }>, "type">;

// The teammates' own colours (agents/registry.py), for the glint as the file lands.
const AGENT_COLOR: Record<string, string> = {
  Pixel: "#b9a3ff",
  Ledger: "#8fd694",
  Quill: "#f0a9c9",
  Scout: "#7cc7ff",
  Sketch: "#86e0c3",
};

function Tile({ path, info, index, total, color }: { path: string; info: FileInfo | null; index: number; total: number; color: string }) {
  const kind = kindOf(info?.name ?? path);
  // A fanned stack for several files: the first on top, the next two peeking out behind it.
  const fan = total > 1 ? (index - (Math.min(total, 3) - 1) / 2) * 7 : 0;
  return (
    <div
      className={`file-tile ${info?.thumb ? "has-thumb" : ""}`}
      style={{ "--kind": kind.color, "--glint": color, "--fan": `${fan}deg`, "--i": index, zIndex: 3 - index } as React.CSSProperties}
    >
      {info?.thumb ? <img src={info.thumb} alt="" draggable={false} /> : (
        <div className="file-page">
          <i /><i /><i /><i />
        </div>
      )}
      <span className="file-badge">{kind.badge}</span>
    </div>
  );
}

export function FileReady({ result, color }: { result: Ready; color?: string }) {
  const files = result.files.slice(0, 12);
  const [infos, setInfos] = useState<(FileInfo | null)[]>([]);
  const [dragging, setDragging] = useState(false);
  useEffect(() => {
    let live = true;
    void Promise.all(files.slice(0, 3).map((f) => bridge.fileInfo(f).catch(() => null))).then((all) => live && setInfos(all));
    return () => {
      live = false;
    };
  }, [result.job_id]);
  const first = files[0];
  const info = infos[0] ?? null;
  const glint = color ?? AGENT_COLOR[result.agent] ?? "#9ecbff";
  const body = cleanBody(result.body);
  const where = info?.folderName ? (files.length > 1 ? `All in ${info.folderName}` : `In ${info.folderName}`) : "";
  return (
    <div className="file-ready" onClick={(e) => e.stopPropagation()}>
      <div
        className={`file-stack ${dragging ? "dragging" : ""}`}
        draggable
        title="Drag it into an email, a chat or a folder"
        onDragStart={(e) => {
          e.preventDefault(); // Windows does the real drag, with the real file
          setDragging(true);
          bridge.dragFile(first);
          window.setTimeout(() => setDragging(false), 600);
        }}
        onDoubleClick={() => bridge.openFile(first)}
      >
        {files.slice(0, 3).map((f, i) => (
          <Tile key={f} path={f} info={infos[i] ?? null} index={i} total={files.length} color={glint} />
        )).reverse()}
        {files.length > 3 && <span className="file-more">+{files.length - 3}</span>}
        <i className="file-glint" style={{ "--glint": glint } as React.CSSProperties} />
      </div>
      <div className="file-meta">
        <div className="file-title">
          <i className="file-spark" style={{ background: glint, boxShadow: `0 0 12px ${glint}` }} />
          <b>{readyTitle(files)}</b>
        </div>
        <div className="file-name" title={info ? `${info.folder}\\${info.name}` : first}>{info?.name ?? first.split(/[\\/]/).pop()}</div>
        <div className="file-where">
          {[info && files.length === 1 ? sizeText(info.size) : "", where].filter(Boolean).join(" · ")}
        </div>
        {body && <p className="file-note">{body}</p>}
        <div className="file-actions">
          <button className="primary" onClick={() => bridge.openFile(first)}>
            <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M11 4h5v5M16 4l-7 7M14 12v3a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h3" /></svg>
            Open file
          </button>
          <button onClick={() => bridge.showInFolder(first)}>
            <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M3 6.5a1.5 1.5 0 0 1 1.5-1.5h3l1.6 1.8h6.4A1.5 1.5 0 0 1 17 8.3v6.2a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 3 14.5Z" /></svg>
            Open file location
          </button>
        </div>
        <div className="file-hint">or drag it anywhere</div>
      </div>
    </div>
  );
}
