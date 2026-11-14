/**
 * The first-run tour, played on the user's real desktop: no setup window and no slides. A
 * firefly flies in, says hello in large type (and in Pyxi's voice), and each thing is learned
 * by doing it once. See machine.ts for the steps and docs/PRIVACY.md for what is kept.
 *
 * - It draws on one screen: the one the cursor is on when it starts.
 * - Nothing here catches the mouse except its own cards (Interactive), so the PC stays usable.
 * - Every step can be skipped (Esc, or the step's own "Next"); "Skip tour" is always on it.
 * - Any error, or the brain going away, ends it quietly: the brain offers it again next start.
 * - The lines are always shown; the voice is extra (quiet, or no voice yet: captions carry it).
 */
import { Component, type FormEvent, type ReactNode, useEffect, useLayoutEffect, useRef, useState } from "react";
import { bridge, cursorToLocal, keysOf } from "../../shared/bridge";
import type { ConnectorStatus, OnboardingEnding } from "../../shared/protocol";
import { brain } from "../../shared/socket";
import { Interactive } from "../Interactive";
import { isPrimary, useOverlay, voice } from "../store";
import { begin, type Demo, DEMOS, lineFor, needsCode, reduce, screenClear, spoken, STEPS, type Tour, type TourEvent } from "./machine";
import { type About, birthdayFromIso, details, EMPTY_ABOUT, lights, LIKES, MONTHS, problems, tooYoung, USES } from "./validate";
import "./onboarding.css";

const TICK_MS = 100;
const START_DELAY_MS = 1200; // after the brain says hello: let the app settle first
const LEAVE_MS = 700; // the fade once it has ended
/** Lines that are shown but not spoken (Pyxi is listening or answering then). */
const UNSPOKEN = new Set(["screen-working", "about-thanks", "code-joined"]);
const VOICE_OWNER = "tour";
/** "Start the tour": with the invite code first, for someone who needs one (never in the
 * developer build, as in Settings). The brain has the last word on it. */
const askToStart = () => brain.send({ type: "onboarding", action: "start", code: needsCode(useOverlay.getState().beta, import.meta.env.DEV) });
/** It starts by itself in the app. A browser preview (used by checks and recordings against a
 * brain with an empty data folder) only does when asked: ?tour=1. */
const AUTO = bridge.electron || new URLSearchParams(location.search).get("tour") === "1";

type Have = Partial<Record<"first_name" | "email" | "date_of_birth", string>>;

/** Where the cursor last was on this screen (null: on another one), and whether we ever heard. */
const pointer: { at: [number, number] | null; heard: boolean } = { at: null, heard: false };
bridge.onCursor((p) => {
  pointer.heard = true;
  pointer.at = cursorToLocal(p);
});

export function Onboarding() {
  const [run, setRun] = useState<{ id: number; have: Have; code: boolean; anchor: [number, number] } | null>(null);
  const runs = useRef(0);
  const asked = useRef(false);
  const due = useOverlay((s) => s.tourDue);

  // The brain says the tour is due (it waits for the brain: no hello, no tour). The main screen
  // asks for it, once.
  useEffect(() => {
    if (!due || !isPrimary || asked.current || !AUTO) return;
    asked.current = true;
    window.setTimeout(askToStart, START_DELAY_MS);
  }, [due]);

  useEffect(
    () =>
      brain.on((e) => {
        if (e.type !== "onboarding" || e.event !== "start") return;
        // It plays on the screen the cursor is on (the main one when nobody knows).
        const mine = pointer.heard ? pointer.at !== null : isPrimary;
        if (!mine) return;
        const at = pointer.at;
        setRun((now) => now ?? { id: ++runs.current, have: e.have ?? {}, code: !!e.code && !e.replay, anchor: at ? [at[0] + 36, at[1] - 30] : [innerWidth * 0.5 - 300, innerHeight * 0.26] });
      }),
    [],
  );

  if (!run) return null;
  const over = (how: OnboardingEnding, tell: boolean) => {
    if (tell) brain.send({ type: "onboarding", action: "finish", how });
    voice.stop(VOICE_OWNER);
    bridge.releaseFocus();
    useOverlay.setState({ touring: false, tourFirefly: false });
    setRun(null);
  };
  return (
    <Guard key={run.id} onError={() => over("failed", true)}>
      <Play have={run.have} code={run.code} anchor={run.anchor} onOver={over} />
    </Guard>
  );
}

/** Anything thrown while drawing the tour ends it quietly; the app is never in its way. */
class Guard extends Component<{ children: ReactNode; onError: () => void }, { broken: boolean }> {
  state = { broken: false };
  static getDerivedStateFromError() {
    return { broken: true };
  }
  componentDidCatch(error: unknown) {
    console.warn("the tour stopped", error);
    this.props.onError();
  }
  render() {
    return this.state.broken ? null : this.props.children;
  }
}

function Play({ have, code, anchor, onOver }: { have: Have; code: boolean; anchor: [number, number]; onOver: (how: OnboardingEnding, tell: boolean) => void }) {
  const [tour, setTour] = useState<Tour>(() => begin({ code, demo: useOverlay.getState().demo }));
  const send = (e: TourEvent) => setTour((t) => reduce(t, e));
  const [about, setAbout] = useState<About>(() => ({ ...EMPTY_ABOUT, email: have.email ?? "", birthday: birthdayFromIso(have.date_of_birth) }));
  const [google, setGoogle] = useState<{ status: ConnectorStatus | null; note: string }>({ status: null, note: "" });
  const [leaving, setLeaving] = useState(false);
  const [hold, setHold] = useState<string[]>(["Ctrl", "Alt"]);
  const stage = useRef<HTMLDivElement>(null);
  const [place, setPlace] = useState<[number, number]>(anchor);
  const tourRef = useRef(tour);
  tourRef.current = tour;
  const toldBrain = useRef(false); // the brain ended it itself: no need to say so back

  useEffect(() => {
    void bridge.hotkeys().then((h) => setHold(h.hold ? keysOf(h.hold) : h.talk ? keysOf(h.talk) : []));
    // Hooks for the browser preview and UI checks (never in the installed app).
    if (!window.pyxiBridge || import.meta.env.DEV) (window as unknown as { __pyxiTour?: unknown }).__pyxiTour = { send, state: () => tourRef.current };
  }, []);

  // The clock. A line still being said holds the phase that ends with it.
  useEffect(() => {
    let last = performance.now();
    const id = window.setInterval(() => {
      const now = performance.now();
      send({ type: "tick", ms: Math.min(1000, now - last), speaking: voice.busy });
      last = now;
    }, TICK_MS);
    return () => window.clearInterval(id);
  }, []);

  // What happens outside the tour's own cards.
  useEffect(() => {
    const offs = [
      bridge.onPttDown(() => send({ type: "keys", down: true })),
      bridge.onPttUp(() => send({ type: "keys", down: false })),
      bridge.onPttCancel(() => send({ type: "keys", down: false })),
      // The brain went away: try next start. Not in the invite code step: a code that worked
      // restarts the brain, and the tour waits there for it to come back.
      brain.onStatus((up) => !up && tourRef.current.step !== "code" && send({ type: "fail" })),
      brain.on((e) => {
        if (tourRef.current.step === "code") {
          // The beta server's answer to their code (the same one Settings shows).
          if (e.type === "beta") send(e.ok ? { type: "joined" } : { type: "join_failed", message: e.message });
          // The brain is back from its restart: ask for the tour again. Joined now, it starts
          // with no code step, and the tour here carries on from "You're in." to its hello.
          else if (e.type === "hello") brain.send({ type: "onboarding", action: "start", code: needsCode(e.beta, import.meta.env.DEV) });
          else if (e.type === "onboarding" && e.event === "start" && !e.replay && (!e.code || tourRef.current.phase === "joined")) send({ type: "ready", demo: useOverlay.getState().demo });
        }
        if (e.type === "user") send({ type: "asked" });
        else if (e.type === "turn_done" || e.type === "error") send({ type: "answered" });
        else if (e.type === "connectors") {
          const g = e.connectors.find((c) => c.id === "google") ?? null;
          setGoogle({ status: g, note: e.message?.startsWith("Couldn't") ? "That didn't connect. You can try again, or do it later from Settings." : "" });
          if (g?.connected && !g.needs && tourRef.current.phase === "connecting") send({ type: "connected" });
          else if (e.message?.startsWith("Couldn't")) setTour((t) => (t.step === "google" && t.phase === "connecting" ? { ...t, phase: "offer", elapsed: 0 } : t));
        } else if (e.type === "onboarding") {
          if (e.event === "heard") send({ type: "heard", text: e.text });
          else if (e.event === "saved") send(e.underage ? { type: "underage" } : { type: "saved" });
          else if (e.event === "ended" && e.how === "failed") {
            toldBrain.current = true;
            send({ type: "fail" });
          } else if (e.event === "voice" && lineFor(tourRef.current, hold)?.id === e.line) {
            // Pyxi's own voice for the line that is on screen now (a line that has gone is dropped).
            voice.enqueue({ b64: e.audio, text: e.text, owner: VOICE_OWNER });
          }
        }
      }),
    ];
    return () => offs.forEach((off) => off());
  }, [hold]);

  // Tell the brain which step this is; give the keyboard to the steps that are typed into.
  useEffect(() => {
    brain.send({ type: "onboarding", action: "step", step: tour.step });
    if (tour.step === "google") brain.send({ type: "connectors" });
    const typed = tour.step === "code" || tour.step === "about" || tour.step === "google" || tour.step === "demos";
    if (typed) bridge.focusOverlay();
    else if (tour.step !== "arrival") bridge.releaseFocus();
  }, [tour.step]);

  // Say the line that is on screen (shown either way; the voice arrives as an event).
  const line = lineFor(tour, hold);
  useEffect(() => {
    voice.stop(VOICE_OWNER);
    if (line && !UNSPOKEN.has(line.id)) brain.send({ type: "onboarding", action: "say", line: line.id, text: spoken(line) });
  }, [line?.id]);

  // The notch and the cursor's own firefly know the tour is on.
  const ownFirefly = tour.step !== "talk" && tour.step !== "screen";
  useEffect(() => {
    useOverlay.setState({ touring: true, tourFirefly: ownFirefly });
  }, [ownFirefly]);
  useEffect(() => () => useOverlay.setState({ touring: false, tourFirefly: false }), []);

  // Enter moves on, Esc skips the step (when the tour has the keyboard).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const field = e.target instanceof HTMLElement && /^(INPUT|SELECT|TEXTAREA|BUTTON)$/.test(e.target.tagName);
      if (e.key === "Escape") {
        e.preventDefault();
        if (tourRef.current.step === "about" && tourRef.current.phase === "form") skipAbout();
        else send({ type: "skip_step" });
      } else if (e.key === "Enter" && !field) send({ type: "next" });
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // Over: fade, then tell the brain how it ended.
  useEffect(() => {
    if (!tour.ended) return;
    setLeaving(true);
    const how = tour.ended;
    const id = window.setTimeout(() => onOver(how, !toldBrain.current), LEAVE_MS);
    return () => window.clearTimeout(id);
  }, [tour.ended]);

  // The landing: once the firefly has flown up into the notch, the notch glows once.
  useEffect(() => {
    if (tour.step === "landing" && tour.phase === "glow") useOverlay.setState({ notchGlow: performance.now() });
  }, [tour.step, tour.phase]);

  // Keep the whole stage on this screen, clear of the notch and the taskbar, whatever its size.
  useLayoutEffect(() => {
    const el = stage.current;
    if (!el) return;
    const fit = () => {
      const { width, height } = el.getBoundingClientRect();
      // Asking about their screen: the words step aside to the bottom corner, out of the way.
      const aside = screenClear(tourRef.current) && (tourRef.current.step === "screen" || tourRef.current.step === "google");
      const x = aside ? 64 : Math.max(40, Math.min(anchor[0], innerWidth - width - 40));
      const y = aside ? innerHeight - height - 96 : Math.max(120, Math.min(anchor[1], innerHeight - height - 64));
      setPlace((p) => (Math.abs(p[0] - x) < 0.5 && Math.abs(p[1] - y) < 0.5 ? p : [x, y]));
    };
    const ro = new ResizeObserver(fit);
    ro.observe(el);
    window.addEventListener("resize", fit);
    fit();
    return () => {
      ro.disconnect();
      window.removeEventListener("resize", fit);
    };
  }, [anchor, tour.step, tour.phase]);

  const saveAbout = (e?: FormEvent) => {
    e?.preventDefault();
    if (Object.keys(problems(about)).length) return; // the card says what looks wrong
    if (tooYoung(about.birthday)) {
      // A child: nothing they typed leaves this card, and the tour ends.
      setAbout(EMPTY_ABOUT);
      setTour((t) => reduce({ ...t, name: "" }, { type: "underage" }));
      return;
    }
    brain.send({ type: "onboarding", action: "save", details: details(tour.name, about) });
    send({ type: "submit" });
  };
  /** "Not now": only what they asked to be called is kept. */
  const skipAbout = () => {
    const name = tourRef.current.name;
    if (name) brain.send({ type: "onboarding", action: "save", details: { name } });
    send({ type: "skip_step" });
  };

  const flying = tour.phase === "fly";
  const dots: Tour["step"][] = code ? ["code", ...STEPS] : STEPS;
  const rising = tour.step === "landing";
  const clear = screenClear(tour) || leaving;
  return (
    <div className={`ob ${leaving ? "leaving" : ""} ${clear ? "clear" : ""} step-${tour.step} phase-${tour.phase}`} style={{ ["--ax" as string]: `${place[0]}px`, ["--ay" as string]: `${place[1]}px` }}>
      <div className={`ob-dim ${clear ? "clear" : ""}`} />
      <div ref={stage} className="ob-stage" style={{ left: place[0], top: place[1] }}>
        <div className="ob-head">
          <div className={`ob-fly ${flying ? "flying" : ""} ${rising ? "rising" : ""} ${ownFirefly ? "" : "away"}`}><Bug /></div>
          {line && (
            <div className="ob-say" key={line.id} aria-live="polite">
              <div className="ob-line">{line.text}</div>
              {line.sub && <div className="ob-sub">{line.sub}</div>}
            </div>
          )}
        </div>

        {tour.step === "code" && (tour.phase === "enter" || tour.phase === "joining") && (
          <CodeBox
            busy={tour.phase === "joining"}
            note={tour.note}
            onCode={(invite) => {
              brain.send({ type: "beta_join", code: invite }); // the same message Settings sends
              send({ type: "join" });
            }}
            onNone={() => send({ type: "no_code" })}
          />
        )}
        {((tour.step === "code" && tour.phase === "without") || (tour.step === "screen" && tour.phase === "demo")) && (
          <Interactive className="ob-row ob-go">
            <button className="ob-btn" onClick={() => send({ type: "next" })}>Continue</button>
          </Interactive>
        )}

        {tour.step === "arrival" && tour.phase === "ask" && <NameBox onName={(name) => send({ type: "name", name })} />}

        {(tour.step === "talk" || tour.step === "screen") && tour.phase === "wait" && (
          <div className="ob-keys">
            {hold.map((k) => <kbd key={k} className={tour.keys ? "held" : ""}>{k}</kbd>)}
            {tour.keys && <span className="ob-listening">Listening</span>}
            {tour.hint && (
              <Interactive className="ob-hint">
                {tour.step === "talk" && <span>or double-tap <kbd>Ctrl</kbd> to type</span>}
                <button className="ob-link" onClick={() => send({ type: "next" })}>Next</button>
              </Interactive>
            )}
          </div>
        )}

        {tour.step === "about" && tour.phase !== "underage" && (
          <AboutCard name={tour.name} about={about} busy={tour.phase !== "form"} onChange={setAbout} onSave={saveAbout} onSkip={skipAbout} />
        )}
        {tour.phase === "underage" && (
          <Interactive className="ob-row">
            <button className="ob-btn" onClick={() => send({ type: "next" })}>Close</button>
          </Interactive>
        )}

        {tour.step === "google" && (
          <GoogleCard
            status={google.status}
            note={google.note}
            phase={tour.phase}
            onConnect={() => {
              setGoogle((g) => ({ ...g, note: "" }));
              brain.send({ type: "connect", id: "google" }); // the same sign-in Settings uses
              send({ type: "connect" });
            }}
            onLater={() => send({ type: "next" })}
          />
        )}

        {tour.step === "demos" && <Demos playing={tour.demo} played={tour.played} onPlay={(demo) => send({ type: "demo", demo })} onNext={() => send({ type: "next" })} />}

        {tour.step !== "landing" && tour.phase !== "underage" && (
          <div className="ob-foot">
            <span className="ob-dots" aria-label={`Step ${dots.indexOf(tour.step) + 1} of ${dots.length}`}>
              {dots.map((s, i) => <i key={s} className={i < dots.indexOf(tour.step) ? "done" : i === dots.indexOf(tour.step) ? "on" : ""} />)}
            </span>
            <Interactive className="ob-skip">
              <button onClick={() => send({ type: "skip_tour" })}>Skip tour</button>
            </Interactive>
          </div>
        )}
      </div>
    </div>
  );
}

/** The firefly, large: the tour's one character. */
function Bug() {
  return (
    <svg width="64" height="64" viewBox="-32 -32 64 64" aria-hidden="true">
      <defs>
        <radialGradient id="ob-halo"><stop offset="0" stopColor="#ffd98a" stopOpacity=".55" /><stop offset="1" stopColor="#ffc861" stopOpacity="0" /></radialGradient>
        <radialGradient id="ob-tail"><stop offset="0" stopColor="#fff8e0" /><stop offset=".55" stopColor="#ffd27a" /><stop offset="1" stopColor="#ffb648" /></radialGradient>
      </defs>
      <circle className="ob-bug-halo" r="30" fill="url(#ob-halo)" />
      <ellipse className="ob-bug-wing l" cx="-9" cy="-11" rx="10" ry="4.4" transform="rotate(38 -9 -11)" />
      <ellipse className="ob-bug-wing r" cx="9" cy="-11" rx="10" ry="4.4" transform="rotate(-38 9 -11)" />
      <ellipse className="ob-bug-body" cx="0" cy="-4" rx="3.6" ry="6.5" />
      <ellipse className="ob-bug-tail" cx="0" cy="5" rx="5.4" ry="7" fill="url(#ob-tail)" />
    </svg>
  );
}

function NameBox({ onName }: { onName: (name: string) => void }) {
  const [name, setName] = useState("");
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => {
    bridge.focusOverlay(); // the overlay can't take typing on its own
    const id = window.setTimeout(() => input.current?.focus(), 80);
    return () => window.clearTimeout(id);
  }, []);
  return (
    <Interactive className="ob-name">
      <form onSubmit={(e) => { e.preventDefault(); onName(name); }}>
        <input
          ref={input}
          value={name}
          maxLength={40}
          placeholder="Your name"
          aria-label="What should I call you?"
          autoComplete="off"
          spellCheck={false}
          onMouseDown={() => bridge.focusOverlay()}
          onChange={(e) => setName(e.target.value)}
        />
        <button type="submit" className="ob-enter" aria-label="Continue"><kbd>Enter</kbd></button>
      </form>
    </Interactive>
  );
}

/** One box for the invite code (shown in capitals; any case and stray spaces are fine), the
 * beta server's own words when it says no, and a quiet way on without one. */
function CodeBox({ busy, note, onCode, onNone }: { busy: boolean; note: string; onCode: (code: string) => void; onNone: () => void }) {
  const [code, setCode] = useState("");
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => {
    bridge.focusOverlay(); // the overlay can't take typing on its own
    const id = window.setTimeout(() => input.current?.focus(), 80);
    return () => window.clearTimeout(id);
  }, []);
  const tidy = code.replace(/\s+/g, "");
  return (
    <Interactive className="ob-name ob-code">
      <form onSubmit={(e) => { e.preventDefault(); if (tidy && !busy) onCode(tidy); }}>
        <input
          ref={input}
          value={code}
          maxLength={60}
          placeholder="FIREFLY-ANNA"
          aria-label="Your invite code"
          autoComplete="off"
          autoCapitalize="characters"
          spellCheck={false}
          onMouseDown={() => bridge.focusOverlay()}
          onChange={(e) => setCode(e.target.value.toUpperCase())}
        />
        <button type="submit" className="ob-enter" aria-label="Join" disabled={busy}><kbd>Enter</kbd></button>
      </form>
      <p className={note ? "ob-wrong" : "ob-code-wait"} role="status">{busy ? "Checking your code." : note}</p>
      <button type="button" className="ob-none" disabled={busy} onClick={onNone}>I don't have one</button>
    </Interactive>
  );
}

function AboutCard({ name, about, busy, onChange, onSave, onSkip }: { name: string; about: About; busy: boolean; onChange: (a: About) => void; onSave: (e?: FormEvent) => void; onSkip: () => void }) {
  const [checked, setChecked] = useState(false); // problems show once they try to go on
  const wrong = problems(about);
  const n = lights(name, about);
  const set = (patch: Partial<About>) => onChange({ ...about, ...patch });
  const toggle = (like: string) => set({ likes: about.likes.includes(like) ? about.likes.filter((l) => l !== like) : [...about.likes, like] });
  const first = useRef<HTMLInputElement>(null);
  useEffect(() => {
    const id = window.setTimeout(() => first.current?.focus(), 120);
    return () => window.clearTimeout(id);
  }, []);
  return (
    <Interactive className="ob-card ob-about">
      <form onSubmit={(e) => { setChecked(true); onSave(e); }} onMouseDown={() => bridge.focusOverlay()}>
        <div className="ob-fields">
          <div className="ob-col">
            <label className="ob-field">
              <span className="ob-label">Email <em>optional</em></span>
              <span className="ob-why">to fill forms for you and send you your files</span>
              <input ref={first} type="text" inputMode="email" autoComplete="off" spellCheck={false} placeholder="you@example.com" value={about.email} onChange={(e) => set({ email: e.target.value })} />
              {checked && wrong.email && <span className="ob-wrong">{wrong.email}</span>}
            </label>
            <div className="ob-field">
              <span className="ob-label">Date of birth <em>optional</em></span>
              <span className="ob-why">forms and bookings often ask</span>
              <div className="ob-date">
                <input aria-label="Day" inputMode="numeric" placeholder="Day" maxLength={2} value={about.birthday.day} onChange={(e) => set({ birthday: { ...about.birthday, day: e.target.value.replace(/\D/g, "") } })} />
                <select aria-label="Month" value={about.birthday.month} onChange={(e) => set({ birthday: { ...about.birthday, month: e.target.value } })}>
                  <option value="">Month</option>
                  {MONTHS.map((m, i) => <option key={m} value={String(i + 1)}>{m}</option>)}
                </select>
                <input aria-label="Year" inputMode="numeric" placeholder="Year" maxLength={4} value={about.birthday.year} onChange={(e) => set({ birthday: { ...about.birthday, year: e.target.value.replace(/\D/g, "") } })} />
              </div>
              {checked && wrong.birthday && <span className="ob-wrong">{wrong.birthday}</span>}
            </div>
            <div className="ob-field">
              <span className="ob-label">What do you mostly use your computer for? <em>optional</em></span>
              <span className="ob-why">so my suggestions fit what you do</span>
              <div className="ob-chips">
                {USES.map(([id, label]) => (
                  <button type="button" key={id} className={about.use === id ? "on" : ""} aria-pressed={about.use === id} onClick={() => set({ use: about.use === id ? "" : id })}>{label}</button>
                ))}
              </div>
            </div>
          </div>
          <div className="ob-col">
            <div className="ob-field">
              <span className="ob-label">A few things you like <em>optional</em></span>
              <span className="ob-why">so my examples are about things you care about</span>
              <div className="ob-chips">
                {LIKES.map((like) => (
                  <button type="button" key={like} className={about.likes.includes(like) ? "on" : ""} aria-pressed={about.likes.includes(like)} onClick={() => toggle(like)}>{like}</button>
                ))}
              </div>
              <input type="text" autoComplete="off" placeholder="or your own: chess, baking" maxLength={120} value={about.own} onChange={(e) => set({ own: e.target.value })} />
            </div>
          </div>
          <Jar n={n} />
        </div>
        <div className="ob-actions">
          {/* Saved here and nowhere else; what Pyxi remembers does go with a request to the model (docs/PRIVACY.md). */}
          <span className="ob-private">Saved only on this PC. You can change or delete it in Settings.</span>
          <button type="button" className="ob-btn quiet" disabled={busy} onClick={onSkip}>Not now</button>
          <button type="submit" className="ob-btn primary" disabled={busy}>{n > (name ? 1 : 0) ? "Keep these" : "Continue"}</button>
        </div>
      </form>
    </Interactive>
  );
}

/** Where each light sits in the jar (five at most), so a new one always has its own place. */
const SPOTS: [number, number][] = [[70, 132], [48, 104], [92, 96], [60, 70], [86, 150]];

/** The jar: every answer adds a firefly to it. */
function Jar({ n }: { n: number }) {
  return (
    <div className="ob-jar" aria-label={`${n} of 5 lights in the jar`}>
      <svg width="140" height="190" viewBox="0 0 140 190">
        <defs>
          <radialGradient id="ob-jar-glow"><stop offset="0" stopColor="#ffd27a" stopOpacity=".5" /><stop offset="1" stopColor="#ffc861" stopOpacity="0" /></radialGradient>
        </defs>
        <ellipse className="ob-jar-warm" cx="70" cy="112" rx="56" ry="64" fill="url(#ob-jar-glow)" style={{ opacity: Math.min(1, n * 0.2) }} />
        <rect className="ob-jar-lid" x="42" y="8" width="56" height="13" rx="4" />
        <path className="ob-jar-glass" d="M46 24 h48 v9 c20 7 30 22 30 40 v78 a22 22 0 0 1 -22 22 h-64 a22 22 0 0 1 -22 -22 v-78 c0 -18 10 -33 30 -40 z" />
        <path className="ob-jar-shine" d="M29 82 v62" />
        {SPOTS.slice(0, n).map(([x, y], i) => (
          <g key={i} className="ob-jar-fly" style={{ ["--d" as string]: `${i * 0.7}s` }} transform={`translate(${x} ${y})`}>
            <g className="ob-jar-pop">
              <circle className="h" r="11" />
              <circle className="c" r="3.4" />
            </g>
          </g>
        ))}
      </svg>
      <span>{n === 0 ? "an empty jar" : n === 1 ? "1 light" : `${n} lights`}</span>
    </div>
  );
}

function GoogleCard({ status, note, phase, onConnect, onLater }: { status: ConnectorStatus | null; note: string; phase: string; onConnect: () => void; onLater: () => void }) {
  const connected = !!status?.connected && !status.needs;
  const ready = !!status && !status.connected && status.detail.startsWith("Ready");
  const waiting = phase === "connecting";
  return (
    <Interactive className="ob-card ob-google">
      <ul>
        <li><b>Your calendar</b> what's on today, and new events when you ask</li>
        <li><b>Your email</b> find a message, draft a reply for you to read first</li>
        <li><b>Sheets and Docs</b> make them, fill them in, tidy them up</li>
      </ul>
      <p className="ob-promise">It never sends, deletes or pays without your <b>Allow</b>.</p>
      {note && <p className="ob-wrong">{note}</p>}
      <div className="ob-actions">
        <span className="ob-private">
          {connected ? `Already connected${status?.detail && status.detail !== "Connected" ? ` as ${status.detail}` : ""}.`
            : waiting ? "Finish signing in in your browser. It opened on this PC."
            : status && !ready ? "Google isn't set up on this PC yet. Settings, Connected accounts, shows how."
            : "You can connect it any time from Settings."}
        </span>
        {connected || phase === "connected" ? (
          <button className="ob-btn primary" onClick={onLater}>Continue</button>
        ) : (
          <>
            <button className="ob-btn quiet" onClick={onLater}>Maybe later</button>
            {ready && <button className="ob-btn primary" disabled={waiting} onClick={onConnect}>{waiting ? "Check your browser" : "Connect Google"}</button>}
          </>
        )}
      </div>
    </Interactive>
  );
}

const DEMO_TITLES: Record<Demo, [title: string, ask: string]> = {
  explain: ["Explain something", "\"What's this about?\""],
  build: ["Build it in 3D", "\"Show me how a wind turbine works.\""],
  tidy: ["Do it for me", "\"Tidy my desktop.\""],
};

/** Three tiny demos, drawn here with made-up content; a tap plays one. */
function Demos({ playing, played, onPlay, onNext }: { playing: Demo | null; played: Demo[]; onPlay: (d: Demo) => void; onNext: () => void }) {
  return (
    <Interactive className="ob-card ob-demos">
      <div className="ob-tiles">
        {DEMOS.map((d) => (
          <button key={d} className={`ob-tile demo-${d} ${playing === d ? "playing" : ""} ${played.includes(d) ? "played" : ""}`} onClick={() => onPlay(d)}>
            <span className="ob-screen">
              {d === "explain" && <ExplainDemo />}
              {d === "build" && <BuildDemo />}
              {d === "tidy" && <TidyDemo />}
            </span>
            <b>{DEMO_TITLES[d][0]}<i className="ob-play" aria-hidden="true" /></b>
            <span className="ob-ask">{DEMO_TITLES[d][1]}</span>
          </button>
        ))}
      </div>
      <div className="ob-actions">
        <span className="ob-private strong">Ask me anything, any time.</span>
        <button className="ob-btn primary" onClick={onNext}>Continue</button>
      </div>
    </Interactive>
  );
}

function ExplainDemo() {
  return (
    <>
      <span className="d-page">{[92, 78, 88, 60, 84, 70].map((w, i) => <i key={i} className={i === 2 ? "hit" : ""} style={{ width: `${w}%` }} />)}</span>
      <span className="d-thread" />
      <span className="d-note"><u>The gist</u><i style={{ width: "86%" }} /><i style={{ width: "64%" }} /><i style={{ width: "74%" }} /></span>
    </>
  );
}

function BuildDemo() {
  return (
    <span className="d-model">
      <span className="d-tower" />
      <span className="d-hub">{[0, 1, 2].map((i) => <i key={i} style={{ transform: `rotate(${i * 120}deg)` }} />)}</span>
      <span className="d-ground" />
    </span>
  );
}

function TidyDemo() {
  return (
    <>
      {[0, 1, 2, 3, 4, 5].map((i) => <span key={i} className={`d-file f${i}`} />)}
      <span className="d-folder a"><u>Photos</u></span>
      <span className="d-folder b"><u>School</u></span>
    </>
  );
}
