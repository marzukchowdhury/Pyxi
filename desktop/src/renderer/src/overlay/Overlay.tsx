import { useCallback, useEffect, useRef } from "react";
import { Recorder } from "../shared/audio";
import { bridge } from "../shared/bridge";
import { brain } from "../shared/socket";
import { Annotations } from "./Annotations";
import { Presence } from "./Presence";
import { Requests } from "./Requests";
import { ScenePanel } from "./ScenePanel";
import { isPrimary, useOverlay, voice } from "./store";
import { Team } from "./Team";
import { Walkthrough } from "./Walkthrough";

const backdrop = new URLSearchParams(location.search).get("backdrop");

export function Overlay() {
  const recorder = useRef(new Recorder());
  const connected = useOverlay((s) => s.connected);
  const paused = useOverlay((s) => s.paused);

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
      }
    });
  }, []);

  useEffect(() => bridge.onDismiss(() => {
    useOverlay.getState().dismissAll();
    brain.send({ type: "clear" });
  }), []);
  useEffect(() => bridge.onPrivacyToggle(() => brain.send({ type: "set", key: "privacy_paused", value: !useOverlay.getState().paused })), []);

  const micLevel = useCallback(() => (recorder.current.active ? recorder.current.level : 0), []);

  return (
    <div className={`overlay ${backdrop ? "with-backdrop" : ""}`}>
      {backdrop && <FakeDesktop />}
      <Annotations />
      <ScenePanel />
      <Walkthrough />
      <Team />
      <Requests />
      <Presence micLevel={micLevel} />
      {isPrimary && !connected && <div className="notice">Waiting for Wisp's brain <code>wisp serve</code></div>}
      {isPrimary && paused && <div className="notice muted">Screen paused<kbd>Ctrl Alt P</kbd></div>}
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
