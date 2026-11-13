// Pixel's card before it fills a PDF form: every question the form asks, section by section,
// with what is already known typed in. What is typed here goes straight into the matching
// boxes of the form on this PC; nothing is written until "Fill it in" is pressed.
import { useMemo, useState } from "react";
import type { FormField } from "../shared/protocol";
import { trusted } from "../shared/trusted";
import { type Answers, answered, changes, firstOpen, pick, progress, sections, start } from "./formAnswers";

const FROM: Record<string, string> = { saved: "from your saved details", form: "already in the form", said: "what you told Pyxi" };

export function FormAsk({ id, agent, question, fields, onAnswer }: { id: string; agent: string; question: string; fields: FormField[]; onAnswer: (id: string, v: unknown) => void }) {
  const all = useMemo(() => sections(fields), [fields]);
  const [answers, setAnswers] = useState<Answers>(() => start(fields));
  const [open, setOpen] = useState(() => firstOpen(all, start(fields)));
  const [remember, setRemember] = useState(true);
  const set = (fid: string, value: string | string[]) => setAnswers((a) => ({ ...a, [fid]: value }));
  const changed = changes(fields, answers);
  return (
    <>
      <div className="req-title">{agent} needs your answers</div>
      <div className="req-action">{question}</div>
      <div className="form-note">Type what applies and leave the rest empty. Nothing is written until you press Fill it in.</div>
      <div className="form-body">
        {all.map((s, i) => (
          <section key={`${s.name}-${i}`} className={`form-section${open === i ? " open" : ""}`}>
            <button type="button" className="form-head" aria-expanded={open === i} onClick={() => setOpen(open === i ? -1 : i)}>
              <span>{s.name}</span>
              <em>{progress(s, answers)}</em>
            </button>
            {open === i && (
              <div className="form-rows">
                {s.fields.map((f) => (
                  <Row key={f.id} field={f} value={answers[f.id]} onChange={(v) => set(f.id, v)} />
                ))}
              </div>
            )}
          </section>
        ))}
      </div>
      <label className="form-remember">
        <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} />
        <span>Remember my name, phone and address on this PC for the next form</span>
      </label>
      <div className="req-buttons">
        <button className="yes" onClick={trusted(() => onAnswer(id, { answers, remember }))}>
          {changed ? `Fill it in (${changed} ${changed === 1 ? "answer" : "answers"})` : "Fill it in"}
        </button>
        <button type="button" onClick={() => onAnswer(id, null)}>Cancel</button>
      </div>
    </>
  );
}

function Row({ field, value, onChange }: { field: FormField; value: string | string[] | undefined; onChange: (v: string | string[]) => void }) {
  const hint = field.from && answered(field.value) ? FROM[field.from] : "";
  if (field.kind === "text") {
    return (
      <label className="form-row">
        <span className="form-label">{field.label}</span>
        <input type="text" value={typeof value === "string" ? value : ""} maxLength={2000} title={hint} className={hint ? "known" : ""} onChange={(e) => onChange(e.target.value)} />
      </label>
    );
  }
  const chosen = (o: string) => (Array.isArray(value) ? value.includes(o) : value === o);
  return (
    <div className="form-row">
      <span className="form-label">{field.label}{field.kind === "multi" ? " (any that apply)" : ""}</span>
      <div className="form-chips" role="group" aria-label={field.label}>
        {(field.options ?? []).map((o) => (
          <button key={o} type="button" aria-pressed={chosen(o)} className={chosen(o) ? "on" : ""} onClick={() => onChange(pick(field, value, o))}>
            {o}
          </button>
        ))}
      </div>
    </div>
  );
}
