/**
 * Teammates at work, as a quiet status HUD: a small luminous mark per agent (in its own
 * colour), a live line of what it's doing, and a thread of light when one hands work to
 * another. Presence, not characters.
 */
import { useEffect, useState } from "react";
import { useOverlay } from "./store";

const COLORS: Record<string, string> = { scout: "#7cc7ff", quill: "#f0a9c9", sketch: "#86e0c3" };
const ROW = 46;

export function Team() {
  const jobs = useOverlay((s) => s.jobs);
  const status = useOverlay((s) => s.jobStatus);
  const handoff = useOverlay((s) => s.handoff);
  const [now, setNow] = useState(Date.now() / 1000);
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(t);
  }, []);

  const visible = Object.values(jobs)
    .filter((j) => j.status === "running" || j.status === "queued" || now - (j.created_at ?? 0) < 20)
    .slice(-4);
  if (!visible.length) return null;
  const idx = (name: string) => visible.findIndex((j) => j.agent_name === name);
  const live = handoff && performance.now() - handoff.at < 2400 && idx(handoff.source) >= 0 && idx(handoff.target) >= 0;
  const y = (i: number) => i * ROW + 16;

  return (
    <div className="hud">
      {live && (
        <svg className="hud-thread" width="60" height={visible.length * ROW}>
          <path d={`M 12 ${y(idx(handoff.source))} C -14 ${y(idx(handoff.source))}, -14 ${y(idx(handoff.target))}, 12 ${y(idx(handoff.target))}`} />
        </svg>
      )}
      {visible.map((j) => {
        const working = j.status === "running" || j.status === "queued";
        return (
          <div key={j.id} className={`hud-row ${working ? "working" : j.status}`} style={{ "--c": COLORS[j.agent_id] ?? "#9ecbff" } as React.CSSProperties}>
            <svg className="hud-mark" width="12" height="22" viewBox="0 0 12 22"><path d="M6 1 10.5 11 6 21 1.5 11Z" /></svg>
            <div className="hud-text">
              <div className="hud-name">{j.agent_name}<span>{working ? (j.steps ? `step ${j.steps}` : "starting") : j.status}</span></div>
              <div className="hud-status" key={status[j.id]}>{working ? status[j.id] ?? "Getting oriented" : j.summary.split(". ")[0]}</div>
            </div>
          </div>
        );
      })}
    </div>
  );
}
