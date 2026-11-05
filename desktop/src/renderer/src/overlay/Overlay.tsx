import { useCallback, useEffect, useRef } from "react";
import { Recorder } from "../shared/audio";
import { bridge } from "../shared/bridge";
import { brain } from "../shared/socket";
import { Annotations } from "./Annotations";
import { NotesCard } from "./NotesCard";
import { Notch } from "./Notch";
import { ObjectPanel } from "./ObjectPanel";
import { SketchBoard } from "./SketchBoard";
import { Presence } from "./Presence";
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
      brain.send({ type: "interrupt" });
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
      voice.stop();
      brain.send({ type: "interrupt" });
      brain.send({ type: "ptt_start" });
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

  // Guides: a click on the mark is the step done (for click steps); a click anywhere else may
  // be them going another way, so the brain checks whether the screen changed.
  useEffect(() => {
    let lastElsewhere = 0;
    return bridge.onGlobalClick(({ x, y }) => {
      const target = useOverlay.getState().annotations.find((a) => a.guide && a.rect);
      if (!target?.rect) return;
      const [rx, ry, rw, rh] = target.rect;
      const pad = 6;
      const inside = x >= rx - pad && y >= ry - pad && x <= rx + rw + pad && y <= ry + rh + pad;
      const clicky = !target.action || target.action.endsWith("click");
      if (inside && clicky) {
        useOverlay.setState((s) => ({ annotations: s.annotations.filter((a) => !a.guide) }));
        brain.send({ type: "guide_clicked", how: "clicked" });
      } else if (!inside && performance.now() - lastElsewhere > 1500) {
        lastElsewhere = performance.now();
        brain.send({ type: "guide_clicked", how: "elsewhere" });
      }
    });
  }, []);

  useEffect(() => bridge.onDismiss(() => {
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
      <ScenePanel />
      <ObjectPanel />
      <SketchBoard />
      <Walkthrough />
      <Team />
      <Requests />
      <Presence micLevel={micLevel} />
      {isPrimary && <Notch micLevel={micLevel} />}
      {/* The main screen shows connection and privacy state in the notch. */}
      {!isPrimary && !connected && <div className="notice">Waiting for Blip's brain <code>blip serve</code></div>}
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
