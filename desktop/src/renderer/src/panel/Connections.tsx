/**
 * Connected accounts. Each one shows whether it's live and, if not, the exact steps to connect
 * it. Google and LinkedIn sign in through their own pages in your browser; Pyxi never sees a
 * password. Nothing is ever sent or posted without an approval card.
 */
import { useEffect, useState } from "react";
import { BrandLogo } from "../shared/brands";
import { Clock } from "../shared/icons";
import { brain } from "../shared/socket";
import { usePanel } from "./store";

const SIGN_IN = new Set(["google", "linkedin"]);

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
            <BrandLogo id={c.id} />
            <div className="conn-text">
              <b>{c.name}</b>
              <span>{c.detail}</span>
            </div>
            {c.connected && c.needs && SIGN_IN.has(c.id) ? (
              <button className="primary" disabled={busy === c.id} onClick={() => { setBusy(c.id); brain.send({ type: "connect", id: c.id }); }}>
                {busy === c.id ? "Check your browser" : c.needs}
              </button>
            ) : c.connected ? (
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

/** Routines Pyxi runs on a schedule while it's open. Ask for one in plain words. */
const DAY_CHOICES: [string, string][] = [["every day", "Every day"], ["weekdays", "Weekdays"], ["weekends", "Weekends"]];
const ONE_DAY: [string, string][] = [["mon", "M"], ["tue", "T"], ["wed", "W"], ["thu", "T"], ["fri", "F"], ["sat", "S"], ["sun", "S"]];
const IDEAS = [
  "Check my email for anything from my professor and tell me",
  "Tell me what's on my calendar today",
  "Remind me to drink some water",
];

/** The Routines tab: what Pyxi does on a schedule. Set one up here or just ask Pyxi out loud. */
export function Routines() {
  const list = usePanel((s) => s.routines);
  const [task, setTask] = useState("");
  const [days, setDays] = useState<string[]>(["every day"]);
  const [time, setTime] = useState("09:00");
  const [hourly, setHourly] = useState(false);
  useEffect(() => brain.send({ type: "routines" }), []);
  const pick = (d: string) =>
    setDays((now) => {
      if (DAY_CHOICES.some(([id]) => id === d)) return [d];
      const single = now.filter((x) => !DAY_CHOICES.some(([id]) => id === x));
      const next = single.includes(d) ? single.filter((x) => x !== d) : [...single, d];
      return next.length ? next : ["every day"];
    });
  const add = () => {
    if (!task.trim()) return;
    brain.send({ type: "routine_add", task: task.trim(), days, time, every_minutes: hourly ? 60 : null });
    setTask("");
  };
  return (
    <div className="routines">
      <div className="routine-form">
        <input
          value={task}
          placeholder="What should Pyxi do?"
          onChange={(e) => setTask(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && add()}
        />
        <div className="chips-row">
          {DAY_CHOICES.map(([id, label]) => (
            <button key={id} className={days.includes(id) ? "on" : ""} onClick={() => pick(id)}>{label}</button>
          ))}
        </div>
        <div className="chips-row days">
          {ONE_DAY.map(([id, label]) => (
            <button key={id} title={id} className={days.includes(id) ? "on" : ""} onClick={() => pick(id)}>{label}</button>
          ))}
        </div>
        <div className="routine-when">
          <label><input type="checkbox" checked={hourly} onChange={(e) => setHourly(e.target.checked)} /> Every hour</label>
          {!hourly && <input type="time" value={time} onChange={(e) => setTime(e.target.value)} />}
          <button className="primary" disabled={!task.trim()} onClick={add}>Add routine</button>
        </div>
        {!task && (
          <div className="routine-ideas">
            {IDEAS.map((idea) => (
              <button key={idea} className="ghost" onClick={() => setTask(idea)}>{idea}</button>
            ))}
          </div>
        )}
      </div>
      {list && !list.length && <p className="muted">None yet. Add one above, or just ask Pyxi: "Every weekday at 9, check my email for anything from my professor."</p>}
      {list && list.length > 0 && (
        <div className="connections">
          {list.map((r) => (
            <div key={r.id} className={`conn ${r.enabled ? "on" : "off"}`}>
              <div className="conn-row">
                <span className="conn-glyph g-routine"><Clock /></span>
                <div className="conn-text">
                  <b>{r.task}</b>
                  <span>
                    {r.when}
                    {r.enabled
                      ? `, next ${new Date(r.next_run * 1000).toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" })}`
                      : ", paused"}
                  </span>
                </div>
                <label className="routine-switch" title={r.enabled ? "On" : "Off"}>
                  <input type="checkbox" checked={r.enabled} onChange={(e) => brain.send({ type: "routine_toggle", id: r.id, enabled: e.target.checked })} />
                </label>
                <button className="ghost" title="Do it now" onClick={() => brain.send({ type: "routine_run", id: r.id })}>Run now</button>
                <button className="ghost" onClick={() => brain.send({ type: "routine_delete", id: r.id })}>Delete</button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
