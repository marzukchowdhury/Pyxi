import { stopChime } from "../shared/chime";
import { useCallback, useEffect, useRef } from "react";
import { Recorder } from "../shared/audio";
import { bridge } from "../shared/bridge";
import type { DictationOutcome } from "../shared/protocol";
import { brain } from "../shared/socket";
import { Annotations } from "./Annotations";
import { AnswerCard } from "./AnswerCard";
import { DictationSign } from "./DictationSign";
import { DICTATION_MAX_MS, type DictationView, micBlockedCard, micProblem, OWN_NOTES, tooQuiet } from "./dictationText";
import { guideClick } from "./guideMarks";
import { Hearing } from "./Hearing";
import { NotesCard } from "./NotesCard";
import { Notch } from "./Notch";
import { ObjectPanel } from "./ObjectPanel";
import { Onboarding } from "./onboarding/Onboarding";
import { SketchBoard } from "./SketchBoard";
import { Presence } from "./Presence";
import { PriceTag } from "./PriceTag";
import { Requests } from "./Requests";
import { ScenePanel } from "./ScenePanel";
import { isPrimary, useOverlay, voice } from "./store";
import { Team } from "./Team";
import { Walkthrough } from "./Walkthrough";

const backdrop = new URLSearchParams(location.search).get("backdrop");

/** A microphone that Windows blocked: the notice's words on a card, with the button that opens
 * the Windows microphone page. Any other microphone problem keeps its notice and no card. */
function showMicCard(reason: string): void {
  const card = micBlockedCard(reason);
  if (card) useOverlay.setState((s) => ({ toasts: [card, ...s.toasts.filter((t) => t.job_id !== card.job_id)].slice(0, 3) }));
}

export function Overlay() {
  const recorder = useRef(new Recorder());
  const dictation = useRef(new Recorder()); // the same microphone path, kept apart from a question's
  const cancelDictation = useRef<() => void>(() => {});
  const discard = useRef(false);
  const connected = useOverlay((s) => s.connected);

  // Push-to-talk: the hotkey toggles recording; silence after speech also ends it.
  useEffect(() => {
    if (!isPrimary) return;
    return bridge.onPtt(async () => {
      const rec = recorder.current;
      if (rec.active) {
        rec.stop();
        return;
      }
      if (dictation.current.busy) return; // dictating: the microphone is taken, finish that first
      voice.stop();
      // No "interrupt" here: ptt_start quiets Pyxi's voice and has her hands wait for the
      // words. An interrupt sent first stopped the mouse for good, so every request spoken
      // while she worked ended that work with "Stopped, as you asked".
      brain.send({ type: "ptt_start" });
      useOverlay.setState({ listening: true, state: "listening" });
      try {
        await rec.start((data, mime) => {
          useOverlay.setState({ listening: false });
          brain.send({ type: "audio", data, mime });
        });
      } catch (err) {
        console.warn("microphone unavailable", err);
        useOverlay.setState({ listening: false });
        brain.send({ type: "ptt_cancel" });
        const reason = err instanceof Error ? `${err.name}: ${err.message}` : String(err);
        // What to do about it, in the same words dictation uses: never the browser's own
        // error after it ("... (NotAllowedError: Permission denied)").
        brain.send({ type: "notice", message: micProblem(reason) });
        showMicCard(reason); // blocked by Windows: the card has Open microphone settings
      }
    });
  }, []);

  // Hold Ctrl + Alt: start on press, send on release, discard if another key interrupts.
  useEffect(() => {
    if (!isPrimary) return;
    const rec = recorder.current;
    const offDown = bridge.onPttDown(async () => {
      if (rec.active || dictation.current.busy) return; // (dictating: finish that first)
      stopChime(); // talking to Pyxi is "I'm up"
      voice.stop();
      brain.send({ type: "ptt_start" }); // (no "interrupt": see the toggle key above)
      useOverlay.setState({ listening: true, state: "listening" });
      try {
        await rec.start((data, mime) => {
          useOverlay.setState({ listening: false });
          if (discard.current) {
            discard.current = false;
            brain.send({ type: "ptt_cancel" });
          } else brain.send({ type: "audio", data, mime });
        }, false);
      } catch (err) {
        useOverlay.setState({ listening: false });
        brain.send({ type: "ptt_cancel" });
        const reason = err instanceof Error ? `${err.name}: ${err.message}` : String(err);
        console.warn("microphone unavailable", err);
        brain.send({ type: "notice", message: micProblem(reason) }); // blocked, none, or in use: each says what to do
        showMicCard(reason);
      }
    });
    const offUp = bridge.onPttUp(() => rec.stop());
    const offCancel = bridge.onPttCancel(() => {
      discard.current = true;
      rec.stop();
    });
    return () => {
      offDown();
      offUp();
      offCancel();
    };
  }, []);

  // Dictation (hold Ctrl + Windows, or a press to start and a press to stop): record, then the
  // brain types what was said where the cursor is. It never becomes a question for Pyxi, and
  // Pyxi's own state is left alone: the sign (DictationSign) is all that shows. The brain is
  // told while it runs, so a job that ends meanwhile is not announced over the dictation.
  useEffect(() => {
    if (!isPrimary) return;
    const rec = dictation.current;
    let drop = false; // this recording is not to be written down
    let late: number | undefined; // the brain never answered
    const show = (view: DictationView) => useOverlay.setState({ dictation: view });
    const ended = (how: DictationOutcome, note: string) => show({ phase: "done", how, text: "", note, at: performance.now() });
    const offDown = bridge.onDictateDown(async (how) => {
      if (rec.busy) return;
      // One thing at a time with the microphone: a question being asked is not cut off.
      if (recorder.current.busy) return ended("failed", OWN_NOTES.asking);
      if (!useOverlay.getState().connected) return ended("failed", OWN_NOTES.offline);
      voice.stop(); // Pyxi's own voice would be written down with theirs
      drop = false;
      brain.send({ type: "dictating", on: true, here: how.here });
      show({ phase: "listening", mode: how.mode, keys: how.keys, at: performance.now() });
      bridge.dictationState("recording");
      try {
        await rec.start((data, mime) => {
          if (drop) {
            // Cancelled, or refused (a password box): whoever dropped it has set the sign.
            drop = false;
            brain.send({ type: "dictating", on: false });
          } else if (tooQuiet(rec.peak, rec.ms)) {
            brain.send({ type: "dictating", on: false });
            ended("nothing", OWN_NOTES.nothing);
          } else {
            brain.send({ type: "dictate", data, mime, polish: how.polish, here: how.here }); // the brain takes it from here
            show({ phase: "working", at: performance.now() });
            bridge.dictationState("working");
            clearTimeout(late);
            late = window.setTimeout(() => {
              if (useOverlay.getState().dictation?.phase !== "working") return;
              brain.send({ type: "dictate_cancel" });
              ended("failed", OWN_NOTES.silent);
            }, 95_000);
          }
        }, false, DICTATION_MAX_MS);
      } catch (err) {
        console.warn("dictation: microphone unavailable", err);
        brain.send({ type: "dictating", on: false });
        const reason = err instanceof Error ? `${err.name}: ${err.message}` : String(err);
        ended("failed", micProblem(reason));
        showMicCard(reason);
      }
    });
    const offUp = bridge.onDictateUp(() => rec.stop());
    const cancel = () => {
      const now = useOverlay.getState().dictation;
      if (rec.busy || now?.phase === "listening") {
        // (Listening but no longer recording: they let go a moment ago and the sound is
        // still being packed up. It is dropped all the same.)
        drop = true;
        rec.stop();
        ended("cancelled", OWN_NOTES.cancelled);
      } else if (now?.phase === "working") {
        brain.send({ type: "dictate_cancel" });
        ended("cancelled", OWN_NOTES.cancelled);
      }
    };
    cancelDictation.current = cancel;
    const offCancel = bridge.onDictateCancel(cancel);
    // Whatever ended it (the brain's answer, a refusal, Esc, the microphone): a recording still
    // running is dropped, the keys are told it is over, and nothing waits for an answer.
    const offStore = useOverlay.subscribe((s, before) => {
      if (s.dictation === before.dictation || (s.dictation && s.dictation.phase !== "done")) return;
      clearTimeout(late);
      if (rec.busy || before.dictation?.phase === "listening") {
        drop = true;
        rec.stop();
      }
      bridge.dictationState("idle");
    });
    return () => {
      offDown();
      offUp();
      offCancel();
      offStore();
      clearTimeout(late);
    };
  }, []);

  // Guides: a click on the mark is the step done (for click steps); a click anywhere else may
  // be them going another way, so the brain checks whether the screen changed.
  useEffect(() => {
    let lastElsewhere = 0;
    return bridge.onGlobalClick(({ x, y }) => {
      // Every screen's overlay hears every click; only the one holding the mark answers.
      const what = guideClick(useOverlay.getState().annotations.find((a) => a.guide && a.rect), x, y);
      if (what === "clicked") {
        useOverlay.setState((s) => ({ annotations: s.annotations.filter((a) => !a.guide) }));
        brain.send({ type: "guide_clicked", how: "clicked" });
      } else if (what && performance.now() - lastElsewhere > 1500) {
        lastElsewhere = performance.now();
        brain.send({ type: "guide_clicked", how: what });
      }
    });
  }, []);

  useEffect(() => bridge.onDismiss(() => {
    stopChime();
    useOverlay.getState().dismissAll();
    brain.send({ type: "clear" });
  }), []);
  // Tell the brain whether the overlay is visible to captures, so it can blank it for its own.
  useEffect(() => {
    if (!isPrimary) return;
    const tell = (value: boolean) => brain.send({ type: "set", key: "overlay_in_capture", value });
    void bridge.shareVisible().then(tell);
    const offStatus = brain.onStatus((up) => up && void bridge.shareVisible().then(tell));
    const offChange = bridge.onShareVisible(tell);
    return () => {
      offStatus();
      offChange();
    };
  }, []);

  useEffect(() => bridge.onMuteToggle((muted) => {
    voice.muted = muted;
    if (muted) voice.stop();
  }), []);
  useEffect(() => bridge.onPrivacyToggle(() => brain.send({ type: "set", key: "privacy_paused", value: !useOverlay.getState().paused })), []);

  const micLevel = useCallback(() => (recorder.current.active ? recorder.current.level : 0), []);
  const dictationLevel = useCallback(() => (dictation.current.active ? dictation.current.level : 0), []);
  const stopDictation = useCallback(() => {
    cancelDictation.current();
    bridge.releaseFocus(); // the click took the keyboard: back to the app they were in
  }, []);

  return (
    <div className={`overlay ${backdrop ? "with-backdrop" : ""}`}>
      {backdrop && <FakeDesktop />}
      <Annotations />
      <NotesCard />
      <AnswerCard />
      <ScenePanel />
      <ObjectPanel />
      <SketchBoard />
      <Walkthrough />
      <PriceTag />
      <Team />
      <Requests />
      <Hearing />
      <Onboarding />
      <Presence micLevel={micLevel} />
      {isPrimary && <Notch micLevel={micLevel} />}
      {isPrimary && <DictationSign level={dictationLevel} onCancel={stopDictation} />}
      {/* The main screen shows connection and privacy state in the notch. */}
      {!isPrimary && !connected && <div className="notice">Waiting for Pyxi's brain <code>pyxi serve</code></div>}
    </div>
  );
}

/** Browser-preview only: a pretend desktop so the overlay has something to sit on. */
function FakeDesktop() {
  return (
    <div className="fake-desktop">
      <div className="fake-window">
        <div className="fake-bar">Settings - App</div>
        <div className="fake-body">
          <div className="fake-side">{["General", "Appearance", "Privacy", "Advanced"].map((t) => <div key={t}>{t}</div>)}</div>
          <div className="fake-main">
            <h3>Appearance</h3>
            <label><input type="checkbox" defaultChecked /> Dark mode</label>
            <label><input type="checkbox" /> Reduce motion</label>
            <button>Export</button>
          </div>
        </div>
      </div>
    </div>
  );
}
