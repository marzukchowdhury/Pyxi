// "Just to be sure, is this the address you meant?" Before Pyxi sends mail to an address they
// said or typed, searches mail from it or types it into a form, the address is shown large,
// name and domain in their own colours, digits tinted and look-alikes (l, 1, I, 0, O, rn)
// marked, in a face that tells them apart. The address is its own text box: click anywhere in
// it and the caret is there. Enter or "Yes, that's it" carries on; Esc or "Never mind" stops.
import { useEffect, useRef, useState } from "react";
import "./addressCheck.css";
import { type AddressItem, type Char, onSpoken, parts, problem, sizeFor, tidy } from "./addressParts";

interface Props {
  id: string;
  items: AddressItem[];
  voice: boolean;
  onAnswer: (id: string, value: unknown) => void;
}

export function AddressCheck({ id, items, voice, onAnswer }: Props) {
  const [values, setValues] = useState(() => items.map((i) => i.address));
  const [note, setNote] = useState("");
  const [heard, setHeard] = useState(() => items.map((i) => i.heard ?? null)); // the raw words, per row
  const boxes = useRef<(HTMLInputElement | null)[]>([]);
  const yes = useRef<HTMLButtonElement>(null);
  const active = useRef(0); // the row last typed in: a spoken correction goes there
  const wrong = (i: number, all = values) => problem(tidy(all[i])) ?? (all[i] === items[i].address ? (items[i].problem ?? null) : null);
  const firstWrong = values.findIndex((_, i) => wrong(i));
  const live = useRef({ values, firstWrong });
  live.current = { values, firstWrong };

  const set = (i: number, v: string) => setValues((all) => all.map((old, j) => (j === i ? v : old)));
  const edit = (i: number, caret?: number) => {
    const box = boxes.current[i];
    if (!box) return;
    box.focus();
    const at = caret ?? box.value.length;
    box.setSelectionRange(at, at);
  };
  const confirm = () => {
    const { values: now, firstWrong: bad } = live.current;
    if (bad >= 0) return edit(bad);
    onAnswer(id, { addresses: now.map(tidy) });
  };

  // Ready for Enter when it reads right; ready to type when it can't be an address.
  useEffect(() => {
    if (firstWrong >= 0) edit(firstWrong);
    else yes.current?.focus();
  }, []);

  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape" && e.isTrusted) onAnswer(id, false);
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [id, onAnswer]);

  // "Yes", "no", "never mind" or a correction, said instead of clicked.
  useEffect(
    () =>
      onSpoken((s) => {
        if (s.action === "confirm") confirm();
        else if (s.action === "cancel") onAnswer(id, false);
        else if (s.action === "edit") edit(Math.max(live.current.firstWrong, active.current, 0));
        else if (s.address) {
          const i = live.current.firstWrong >= 0 ? live.current.firstWrong : active.current;
          set(i, s.address);
          setHeard((all) => all.map((old, j) => (j === i ? (s.heard ?? old) : old)));
          setNote("I heard a correction. Say yes or press Yes if it's right now.");
          yes.current?.focus();
        }
      }),
    [id, onAnswer],
  );

  const many = items.length > 1;
  return (
    <form
      className="addr"
      aria-labelledby={`${id}-ask`}
      onSubmit={(e) => {
        e.preventDefault();
        if (e.nativeEvent.isTrusted) confirm();
      }}
    >
      <div className="addr-head">
        <span className="addr-fly" aria-hidden="true" />
        <span className="req-title">{voice ? "Pyxi heard an address" : "Before Pyxi goes on"}</span>
      </div>
      <div className="req-action addr-ask" id={`${id}-ask`}>
        {many ? "Just to be sure, are these the addresses you meant?" : "Just to be sure, is this the address you meant?"}
      </div>
      <div className="addr-rows">
        {items.map((item, i) => (
          <Row
            key={i}
            item={item}
            heard={heard[i]}
            value={values[i]}
            wrong={wrong(i)}
            label={many ? `Email address ${i + 1} of ${items.length}` : "Email address"}
            hook={(el) => (boxes.current[i] = el)}
            onFocus={() => (active.current = i)}
            onChange={(v) => {
              set(i, v);
              setNote("");
            }}
          />
        ))}
      </div>
      {note && <div className="addr-note" role="status">{note}</div>}
      <div className="addr-foot">I'll carry on with what you asked once it's right.</div>
      <div className="req-buttons addr-buttons">
        <button ref={yes} className="yes" type="submit" aria-disabled={firstWrong >= 0}>
          {many ? "Yes, that's them" : "Yes, that's it"}
        </button>
        <button type="button" onClick={() => edit(Math.max(firstWrong, 0))}>Fix it</button>
        <button type="button" className="addr-leave" onClick={() => onAnswer(id, false)}>Never mind</button>
      </div>
    </form>
  );
}

function Row({ item, heard, value, wrong, label, hook, onFocus, onChange }: { item: AddressItem; heard: string | null; value: string; wrong: string | null; label: string; hook: (el: HTMLInputElement | null) => void; onFocus: () => void; onChange: (v: string) => void }) {
  const layer = useRef<HTMLDivElement>(null);
  const p = parts(value);
  const size = sizeFor(value);
  // Spelled out for the name only: every "example.com" has an l, and the line would never go.
  const alike = [...new Map(p.name.filter((c) => c.alike).map((c) => [c.alike, c])).values()].slice(0, 3);
  const offer = item.suggestion && item.suggestion.toLowerCase() !== value.trim().toLowerCase() ? item.suggestion : null;
  const changed = value.trim() !== item.address;
  return (
    <div className="addr-row">
      <div className={`addr-field ${size}${wrong ? " bad" : ""}`}>
        <div className="addr-layer" ref={layer} aria-hidden="true">
          {p.name.map(glyph)}
          {p.at >= 0 && <span className="addr-at">@</span>}
          {p.domain.map((c) => glyph(c, "dom"))}
          {!value && <span className="addr-empty">name@example.com</span>}
        </div>
        <input
          ref={hook}
          className="addr-input"
          type="text"
          inputMode="email"
          value={value}
          aria-label={label}
          aria-invalid={!!wrong}
          spellCheck={false}
          autoComplete="off"
          autoCapitalize="off"
          autoCorrect="off"
          maxLength={254}
          title="Click to change it"
          onFocus={onFocus}
          onChange={(e) => onChange(e.target.value)}
          onScroll={(e) => {
            if (layer.current) layer.current.scrollLeft = e.currentTarget.scrollLeft;
          }}
        />
      </div>
      {size !== "small" && p.at > 0 && !wrong && (
        <div className={`addr-labels ${size}`} aria-hidden="true">
          <span style={{ width: `${p.name.length}ch` }}><i>name</i></span>
          <span style={{ width: "1ch" }} />
          <span className="dom" style={{ width: `${p.domain.length}ch` }}><i>domain</i></span>
        </div>
      )}
      {wrong && <div className="addr-wrong" role="alert">{wrong} Type the right one here.</div>}
      {heard && <div className="addr-heard">I heard: <q>{heard}</q></div>}
      {changed && !wrong && item.address && <div className="addr-heard">It was <span className="addr-was">{item.address}</span> <button type="button" className="addr-undo" onClick={() => onChange(item.address)}>Put it back</button></div>}
      {alike.length > 0 && !wrong && (
        <div className="addr-alike">
          Easy to mix up:{" "}
          {alike.map((c, i) => (
            <span key={c.alike}>
              {i > 0 && ", "}
              <b className={c.kind === "digit" ? "digit" : ""}>{c.alike?.startsWith("r then") ? "rn" : c.alike?.startsWith("two v") ? "vv" : c.ch}</b> is {c.alike}
            </span>
          ))}
        </div>
      )}
      {offer && (
        <button type="button" className="addr-chip" onClick={() => onChange(offer)}>
          Did you mean <b>{offer}</b>?
        </button>
      )}
    </div>
  );
}

function glyph(c: Char, part: "dom" | number = 0) {
  return (
    <span key={c.at} className={`${c.kind}${part === "dom" ? " dom" : ""}${c.alike ? " alike" : ""}`}>
      {c.ch}
    </span>
  );
}
