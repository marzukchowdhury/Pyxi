/**
 * The voice studio. Pick who Pyxi sounds like: every voice has a signature (a little waveform
 * drawn from its name, so no two look alike) and a personality, and tapping one plays a short
 * line in character. Engine, speed, language and captions apply from the next sentence.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { AudioQueue } from "../shared/audio";
import { bridge } from "../shared/bridge";
import { Play, Stop } from "../shared/icons";
import type { VoiceInfo, VoicePublic } from "../shared/protocol";
import { brain } from "../shared/socket";
import { usePanel } from "./store";

const player = new AudioQueue();

const ENGINES: { id: VoicePublic["engine"]; name: string; blurb: string; site?: string; env?: string }[] = [
  { id: "kokoro", name: "On this PC", blurb: "Free and private. Works offline." },
  { id: "elevenlabs", name: "ElevenLabs", blurb: "The most human-sounding. Uses your ElevenLabs credits.", site: "https://elevenlabs.io/app/settings/api-keys", env: "ELEVENLABS_API_KEY" },
  { id: "gemini", name: "Google Gemini", blurb: "Expressive, acts out the tone of each line. Uses your Google AI key.", site: "https://aistudio.google.com/apikey", env: "GEMINI_API_KEY" },
];
const SPEEDS = [0.8, 0.9, 1, 1.1, 1.25];

export function VoiceStudio() {
  const hello = usePanel((s) => s.hello);
  const catalog = usePanel((s) => s.voiceCatalog);
  const live = usePanel((s) => s.voice) ?? hello?.voice ?? null;
  const preview = usePanel((s) => s.voicePreview);
  const [playing, setPlaying] = useState<string | null>(null);
  const [engine, setEngine] = useState<VoicePublic["engine"]>(live?.engine ?? "kokoro");

  useEffect(() => brain.send({ type: "voice_catalog" }), []);
  useEffect(() => {
    if (live) setEngine(live.engine);
  }, [live?.engine]);

  // Play previews as they arrive.
  useEffect(() => {
    if (!preview) return;
    player.stop();
    player.enqueue({ b64: preview.audio, text: preview.text, onEnd: () => setPlaying(null) });
  }, [preview]);

  if (!live) return <p className="muted">Waiting for Pyxi's brain…</p>;
  const set = (key: string, value: unknown) => brain.send({ type: "set", key, value });
  const voices = catalog?.[engine] ?? [];
  const current = [...(catalog?.[live.engine] ?? [])].find((v) => v.id === live.voice);
  const ready = live.available[engine];
  const info = ENGINES.find((e) => e.id === engine)!;

  const listen = (v: VoiceInfo) => {
    if (playing === v.id) {
      player.stop();
      setPlaying(null);
      return;
    }
    setPlaying(v.id);
    brain.send({ type: "voice_preview", engine: v.engine, voice: v.id, speed: live.speed });
  };
  const choose = (v: VoiceInfo) => {
    if (live.engine !== v.engine) set("voice_engine", v.engine);
    set("voice", v.id);
    listen(v);
  };

  return (
    <div className="studio">
      <section className="now">
        <Signature id={live.voice} big active={playing === live.voice} />
        <div className="now-text">
          <div className="now-kicker">Pyxi sounds like</div>
          <div className="now-name">{current?.name ?? live.voice}<span>{current?.vibe}</span></div>
          <div className="now-engine">{ENGINES.find((e) => e.id === live.engine)?.name}{current?.accent ? ` · ${current.accent}` : ""}</div>
        </div>
        {current && (
          <button className={`round ${playing === current.id ? "on" : ""}`} onClick={() => listen(current)} aria-label="Hear it">
            {playing === current.id ? <Stop /> : <Play />}
          </button>
        )}
      </section>
      {live.error && <div className="studio-note warn">{live.error}</div>}

      <div className="segmented">
        {ENGINES.map((e) => (
          <button key={e.id} className={engine === e.id ? "on" : ""} onClick={() => setEngine(e.id)}>
            {e.name}
            {!live.available[e.id] && <i title="Needs a key" />}
          </button>
        ))}
      </div>
      <p className="studio-blurb">{info.blurb}</p>

      {!ready && info.env ? (
        <div className="studio-setup">
          <b>Connect {info.name}</b>
          <ol>
            <li>Create an API key on the {info.name} website.</li>
            <li>Add <code>{info.env}=your-key</code> to <code>pyxi/.env</code>.</li>
            <li>Restart Pyxi. Your voices appear here.</li>
          </ol>
          <button className="primary" onClick={() => bridge.openExternal(info.site!)}>Open {info.name}</button>
        </div>
      ) : !ready ? (
        <div className="studio-setup"><b>The on-device voice isn't installed</b><p>Run <code>pyxi setup-voice</code> once (about 350 MB).</p></div>
      ) : (
        <div className="voice-grid">
          {voices.map((v) => (
            <button key={v.id} className={`voice-card ${live.engine === v.engine && live.voice === v.id ? "on" : ""} ${playing === v.id ? "playing" : ""}`} onClick={() => choose(v)} title={`Use ${v.name}`}>
              <Signature id={v.id} active={playing === v.id} />
              <b>{v.name}</b>
              <span>{v.vibe}{v.accent ? ` · ${v.accent}` : ""}</span>
            </button>
          ))}
          {!voices.length && <p className="muted">Loading voices…</p>}
        </div>
      )}

      <h4>Speed</h4>
      <div className="chips-row">
        {SPEEDS.map((s) => (
          <button key={s} className={Math.abs(live.speed - s) < 0.01 ? "on" : ""} onClick={() => set("voice_speed", s)}>{s === 1 ? "1x" : `${s}x`}</button>
        ))}
      </div>

      <h4>Language</h4>
      <label className="select-row">
        <span>Speak to me in</span>
        <select value={live.language} onChange={(e) => set("voice_language", e.target.value)}>
          {Object.entries(live.languages).map(([code, name]) => <option key={code} value={code}>{name}</option>)}
        </select>
      </label>
      <p className="studio-blurb">
        {live.language === "auto"
          ? "Pyxi listens for the language you speak and answers in it. The first time, it downloads a multilingual listening model (about 480 MB)."
          : live.language === "bn" && live.engine === "kokoro"
            ? "Pyxi can read Bengali, but the on-device voice can't speak it yet. Pick ElevenLabs for a Bengali voice."
            : "Pyxi listens and answers in this language."}
      </p>

      <h4>Captions</h4>
      <label className="toggle-row">
        <span>Show what Pyxi says in the notch while it talks</span>
        <input type="checkbox" className="switch" checked={live.captions} onChange={(e) => set("captions", e.target.checked)} />
      </label>
    </div>
  );
}

/** A voice's signature: a symmetric little waveform seeded by its id. Bars breathe while it plays. */
function Signature({ id, big = false, active = false }: { id: string; big?: boolean; active?: boolean }) {
  const bars = useMemo(() => {
    let h = 2166136261;
    for (const c of id) h = Math.imul(h ^ c.charCodeAt(0), 16777619);
    const rand = () => ((h = Math.imul(h ^ (h >>> 15), 2246822507) >>> 0) % 1000) / 1000;
    const n = 9;
    const half = Array.from({ length: Math.ceil(n / 2) }, (_, i) => 0.25 + 0.75 * rand() * (0.55 + 0.45 * Math.sin(((i + 1) / (n / 2)) * Math.PI * 0.5)));
    return [...half.slice(1).reverse(), ...half];
  }, [id]);
  const hue = useMemo(() => {
    let s = 0;
    for (const c of id) s = (s * 31 + c.charCodeAt(0)) % 360;
    return 190 + (s % 70); // ice to violet, Pyxi's family of colours
  }, [id]);
  const ref = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    if (!active) return;
    let raf = 0;
    const tick = (t: number) => {
      const level = player.level();
      ref.current?.querySelectorAll("i").forEach((el, i) => {
        const wobble = 0.65 + 0.35 * Math.sin(t / 120 + i * 1.3);
        (el as HTMLElement).style.transform = `scaleY(${(0.35 + Math.min(1, level * 1.8) * wobble).toFixed(3)})`;
      });
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(raf);
      ref.current?.querySelectorAll("i").forEach((el) => ((el as HTMLElement).style.transform = ""));
    };
  }, [active]);
  return (
    <span ref={ref} className={`signature ${big ? "big" : ""} ${active ? "active" : ""}`} style={{ ["--h" as string]: hue }}>
      {bars.map((b, i) => <i key={i} style={{ height: `${Math.round(b * 100)}%` }} />)}
    </span>
  );
}
