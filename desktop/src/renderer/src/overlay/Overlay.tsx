import { stopChime } from "../shared/chime";
import { useCallback, useEffect, useRef } from "react";
import { Recorder } from "../shared/audio";
import { bridge } from "../shared/bridge";
import { brain } from "../shared/socket";
import { Annotations } from "./Annotations";
import { AnswerCard } from "./AnswerCard";
import { guideClick } from "./guideMarks";
import { NotesCard } from "./NotesCard";
import { Notch } from "./Notch";
import { ObjectPanel } from "./ObjectPanel";
import { SketchBoard } from "./SketchBoard";
import { Presence } from "./Presence";
import { PriceTag } from "./PriceTag";
import { Requests } from "./Requests";
import { ScenePanel } from "./ScenePanel";
import { isPrimary, useOverlay, voice } from "./store";
import { Team } from "./Team";
import { Walkthrough } from "./Walkthrough";

const backdrop = new URLSearchParams(location.search).get("backdrop");

export function Overlay() {
  const recorder = useRef(new Recorder());
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
        const hint = /NotAllowed|Permission/i.test(reason)
          ? "Windows blocked the microphone. Turn on Settings > Privacy & security > Microphone > Let desktop apps access your microphone."
          : /NotFound|Overconstrained/i.test(reason)
            ? "No microphone was found. Plug one in or pick a default input in Windows Sound settings."
            : "The microphone couldn't start.";
        brain.send({ type: "notice", message: `${hint} (${reason})` });
      }
    });
  }, []);

  // Hold Ctrl + Alt: start on press, send on release, discard if another key interrupts.
  useEffect(() => {
    if (!isPrimary) return;
    const rec = recorder.current;
    const offDown = bridge.onPttDown(async () => {
      if (rec.active) return;
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
        brain.send({ type: "notice", message: `The microphone couldn't start. Check Settings > Privacy & security > Microphone. (${reason})` });
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

  // Hold Ctrl + Windows: dictation. Record while held, then the brain types what was said
  // where the cursor is (it never becomes a question for Pyxi). The brain is told while the
  // keys are down, so a job that ends meanwhile is not announced over the dictation.
  useEffect(() => {
    if (!isPrimary) return;
    const rec = new Recorder();
    let drop = false;
    const offDown = bridge.onDictateDown(async () => {
      if (rec.active || recorder.current.active) return;
      voice.stop();
      drop = false;
      brain.send({ type: "dictating", on: true });
      useOverlay.setState({ listening: true, state: "listening" });
      try {
        await rec.start((data, mime) => {
          useOverlay.setState({ listening: false, state: "idle" });
          if (drop) {
            drop = false;
            brain.send({ type: "dictating", on: false });
          } else brain.send({ type: "dictate", data, mime }); // the brain takes it from here
        }, false);
      } catch (err) {
        useOverlay.setState({ listening: false, state: "idle" });
        brain.send({ type: "dictating", on: false });
        const reason = err instanceof Error ? `${err.name}: ${err.message}` : String(err);
        brain.send({ type: "notice", message: `The microphone couldn't start. Check Settings > Privacy & security > Microphone. (${reason})` });
      }
    });
    const offUp = bridge.onDictateUp(() => rec.stop());
    const offCancel = bridge.onDictateCancel(() => {
      drop = true;
      rec.stop();
    });
    return () => {
      offDown();
      offUp();
      offCancel();
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
      } else if (what === "elsewhere" && performance.now() - lastElsewhere > 1500) {
        lastElsewhere = performance.now();
        brain.send({ type: "guide_clicked", how: "elsewhere" });
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
      <Presence micLevel={micLevel} />
      {isPrimary && <Notch micLevel={micLevel} />}
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
