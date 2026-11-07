/**
 * The task tray: background work, small, in the top-right corner. One chip per job (a teammate,
 * a booking, an errand) with what it's doing right now. Click a chip to watch it live (the
 * teammate's browser, frame by frame) or stop it; minimize the tray and the work carries on.
 * Nothing here ever needs the mouse to keep working.
 */
import { useEffect, useState } from "react";
import { bridge } from "../shared/bridge";
import { brain } from "../shared/socket";
import { Interactive } from "./Interactive";
import { useOverlay } from "./store";

const COLORS: Record<string, string> = { scout: "#7cc7ff", quill: "#f0a9c9", sketch: "#86e0c3", pyxi: "#ffc861" };

export function Team() {
  const jobs = useOverlay((s) => s.jobs);
  const status = useOverlay((s) => s.jobStatus);
  const frames = useOverlay((s) => s.jobFrames);
  const [now, setNow] = useState(Date.now() / 1000);
  const [open, setOpen] = useState<string | null>(null);
  const [mini, setMini] = useState(false);
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(t);
  }, []);

  const visible = Object.values(jobs)
    .filter((j) => j.role !== "model")
    .filter((j) => j.status === "running" || j.status === "queued" || now - (j.ended_at ?? j.created_at ?? 0) < 12)
    .slice(-4);
  if (!visible.length) return null;
  const working = visible.filter((j) => j.status === "running" || j.status === "queued").length;

  if (mini)
    return (
      <Interactive className="tray mini">
        <button className="tray-mini" onClick={() => setMini(false)} title="Show background tasks">
          <i />
          {working ? `${working} working` : "Done"}
        </button>
      </Interactive>
    );

  const shown = open ? visible.find((j) => j.id === open) : undefined;
  return (
    <Interactive className="tray">
      <div className="tray-head">
        <span>{working ? `Working in the background · ${working}` : "Background tasks"}</span>
        <button onClick={() => setMini(true)} title="Minimize (they keep working)">–</button>
      </div>
      {visible.map((j) => {
        const live = j.status === "running" || j.status === "queued";
        return (
          <button
            key={j.id}
            className={`tray-chip ${live ? "working" : j.status} ${open === j.id ? "open" : ""}`}
            style={{ "--c": COLORS[j.agent_id] ?? "#9ecbff" } as React.CSSProperties}
            onClick={() => setOpen(open === j.id ? null : j.id)}
          >
            <i className="tray-dot" />
            <span className="tray-text">
              <b>{j.agent_name}</b> {chipTitle(j.task)}
              <small>{live ? (status[j.id] ?? (j.steps ? `step ${j.steps}` : "starting")) : (j.summary || j.status).split(". ")[0]}</small>
            </span>
          </button>
        );
      })}
      {shown && (
        <div className="tray-view">
          {frames[shown.id] ? (
            <img src={`data:image/jpeg;base64,${frames[shown.id]}`} alt={`What ${shown.agent_name} sees`} />
          ) : (
            <p>
              {shown.status === "done" && shown.summary
                ? shown.summary
                : `${shown.agent_name} is working without a screen to show. ${status[shown.id] ?? ""}`}
            </p>
          )}
          {boardingPass(shown.data) && (
            <button className="tray-link" onClick={() => bridge.openExternal(boardingPass(shown.data)!)}>
              Boarding pass
            </button>
          )}
          {(shown.status === "running" || shown.status === "queued") && (
            <button className="tray-stop" onClick={() => brain.send({ type: "job_control", job_id: shown.id, action: "stop" })}>Stop</button>
          )}
        </div>
      )}
    </Interactive>
  );
}

/** The chip's headline: "Booking a dental cleaning", "Filling job application at Northwind",
 * "Checking in Skyward Air SK 482", "Explaining your Maplewave bill". */
function chipTitle(task: string): string {
  const line = task.split("\n").find((l) => /^(Book|Fill|Check in): /.test(l)) ?? task.split("\n")[0];
  return line
    .replace(/^Book: /, "Booking ")
    .replace(/^Fill: /, "Filling ")
    .replace(/^Check in: /, "Checking in ")
    .slice(0, 48);
}

/** A finished check-in's boarding pass page. */
function boardingPass(data: unknown): string | null {
  const url = (data as { boarding_pass?: unknown } | null)?.boarding_pass;
  return typeof url === "string" && /^https?:\/\//.test(url) ? url : null;
}
