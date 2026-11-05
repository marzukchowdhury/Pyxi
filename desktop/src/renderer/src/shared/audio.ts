// Audio: a queue for Blip's sentences (with "this sentence started" callbacks, so drawings
// land exactly when the words do), a level meter for the buddy's mouth, and push-to-talk.

import { bridge } from "./bridge";

/** ``onCancel``: the clip was stopped before it finished (an interruption), so whatever was
 * waiting for ``onEnd`` (a model's next beat, a drawing's next step) can pause instead of hanging. */
export type Clip = {
  b64: string | null;
  text: string;
  onStart?: () => void;
  onEnd?: () => void;
  onCancel?: () => void;
  /** Who queued it (a model, a drawing), so it can stop its own lines without cutting Blip off. */
  owner?: string;
};

/** What the overlay needs from a voice: the real player, or a stand-in on other monitors. */
export interface Voice {
  muted: boolean;
  readonly busy: boolean;
  enqueue(clip: Clip): void;
  /** No owner: stop everything (an interruption). With an owner: only that owner's clips. */
  stop(owner?: string): void;
  level(): number;
}

let ctx: AudioContext | null = null;
function audioCtx(): AudioContext {
  ctx ??= new AudioContext();
  if (ctx.state === "suspended") void ctx.resume();
  return ctx;
}

function b64ToBuffer(b64: string): ArrayBuffer {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes.buffer;
}

export class AudioQueue implements Voice {
  private queue: Clip[] = [];
  private playingClip: Clip | null = null;
  private playing = false;
  private current: AudioBufferSourceNode | null = null;
  private analyser: AnalyserNode | null = null;
  private gen = 0;
  /** Muted: nothing is heard, but clips still take their real time, so drawings stay in sync. */
  muted =
    new URLSearchParams(location.search).get("mute") === "1" ||
    // a browser preview is silent unless asked (?sound=1)
    (!window.blipBridge && new URLSearchParams(location.search).get("sound") !== "1");

  /** 0..1 loudness of whatever is playing right now (drives the buddy's mouth). */
  level(): number {
    if (!this.analyser || !this.playing) return 0;
    const data = new Uint8Array(this.analyser.fftSize);
    this.analyser.getByteTimeDomainData(data);
    let sum = 0;
    for (const v of data) sum += ((v - 128) / 128) ** 2;
    return Math.min(1, Math.sqrt(sum / data.length) * 4);
  }

  get busy(): boolean {
    return this.playing || this.queue.length > 0;
  }

  enqueue(clip: Clip): void {
    this.queue.push(clip);
    if (!this.playing) void this.next();
  }

  private cutCurrent = false;

  stop(owner?: string): void {
    if (owner !== undefined) {
      const mine = this.queue.filter((c) => c.owner === owner);
      this.queue = this.queue.filter((c) => c.owner !== owner);
      mine.forEach((c) => c.onCancel?.());
      if (this.playingClip?.owner === owner) {
        this.cutCurrent = true;
        try {
          this.current?.stop();
        } catch {
          /* already stopped */
        }
      }
      return;
    }
    this.gen++;
    const dropped = [...(this.playingClip ? [this.playingClip] : []), ...this.queue];
    this.playingClip = null;
    this.queue = [];
    dropped.forEach((c) => c.onCancel?.());
    try {
      this.current?.stop();
    } catch {
      /* already stopped */
    }
    speechSynthesis?.cancel();
    this.playing = false;
  }

  private async next(): Promise<void> {
    const clip = this.queue.shift();
    if (!clip) {
      this.playing = false;
      return;
    }
    this.playing = true;
    const gen = this.gen;
    this.playingClip = clip;
    clip.onStart?.();
    try {
      // Blip has one voice. With no audio (muted, or the voice failed) the words still take
      // their time on screen, but no other voice ever stands in.
      if (this.muted || !clip.b64) await this.silent(clip);
      else await this.playWav(clip.b64, gen);
    } catch (err) {
      console.warn("audio failed", err);
    }
    if (gen !== this.gen) return;
    this.playingClip = null;
    if (this.cutCurrent) {
      this.cutCurrent = false;
      clip.onCancel?.();
    } else clip.onEnd?.();
    void this.next();
  }

  private async silent(clip: Clip): Promise<void> {
    let seconds = clip.text.split(/\s+/).length * 0.33;
    if (clip.b64) {
      try {
        seconds = (await audioCtx().decodeAudioData(b64ToBuffer(clip.b64))).duration;
      } catch {
        /* keep the estimate */
      }
    }
    await new Promise((r) => setTimeout(r, Math.max(300, seconds * 1000)));
  }

  private async playWav(b64: string, gen: number): Promise<void> {
    const ac = audioCtx();
    const buffer = await ac.decodeAudioData(b64ToBuffer(b64));
    if (gen !== this.gen) return; // stopped while decoding: this sentence must not start
    const src = ac.createBufferSource();
    src.buffer = buffer;
    this.analyser ??= ac.createAnalyser();
    this.analyser.fftSize = 512;
    src.connect(this.analyser);
    this.analyser.connect(ac.destination);
    this.current = src;
    await new Promise<void>((resolve) => {
      src.onended = () => resolve();
      src.start();
    });
  }
}

/** The voice on a second monitor's overlay: every clip is played by the main screen's queue
 * (through Electron's main process), so Blip has exactly one voice, however many monitors. */
export class RemoteVoice implements Voice {
  muted = false;
  private pending = new Map<string, Clip>();
  private n = 0;

  constructor() {
    bridge.onVoiceEvent((m) => {
      const clip = this.pending.get(m.id);
      if (!clip) return;
      if (m.kind === "start") return clip.onStart?.();
      this.pending.delete(m.id);
      if (m.kind === "end") clip.onEnd?.();
      else clip.onCancel?.();
    });
  }

  get busy(): boolean {
    return this.pending.size > 0;
  }

  level(): number {
    return 0;
  }

  enqueue(clip: Clip): void {
    const id = `${Date.now().toString(36)}-${this.n++}`;
    this.pending.set(id, clip);
    bridge.voicePlay({ id, b64: clip.b64, text: clip.text });
  }

  stop(owner?: string): void {
    const ids = [...this.pending].filter(([, c]) => owner === undefined || c.owner === owner).map(([id]) => id);
    bridge.voiceStop(owner === undefined ? undefined : ids);
    const dropped = ids.map((id) => this.pending.get(id)!);
    ids.forEach((id) => this.pending.delete(id));
    dropped.forEach((c) => c.onCancel?.());
  }
}

/** Push-to-talk: records until toggled off, or until ~1.2 s of silence after speech. */
export class Recorder {
  private rec: MediaRecorder | null = null;
  private chunks: Blob[] = [];
  private stream: MediaStream | null = null;
  private raf = 0;
  level = 0;
  get active(): boolean {
    return this.rec?.state === "recording";
  }

  /** ``autoStop``: end on ~1.2 s of silence after speech (toggle mode). Hold mode passes false. */
  private starting = false;
  private stopAsked = false;

  async start(onDone: (b64: string, mime: string) => void, autoStop = true): Promise<void> {
    if (this.active || this.starting) return; // a second press while the mic opens is ignored
    this.starting = true;
    this.stopAsked = false;
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
    } finally {
      this.starting = false;
    }
    const mime = MediaRecorder.isTypeSupported("audio/webm;codecs=opus") ? "audio/webm;codecs=opus" : "audio/webm";
    this.rec = new MediaRecorder(this.stream, { mimeType: mime });
    this.chunks = [];
    this.rec.ondataavailable = (e) => e.data.size && this.chunks.push(e.data);
    this.rec.onstop = async () => {
      cancelAnimationFrame(this.raf);
      this.stream?.getTracks().forEach((t) => t.stop());
      const blob = new Blob(this.chunks, { type: mime });
      const buf = new Uint8Array(await blob.arrayBuffer());
      let bin = "";
      for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode(...buf.subarray(i, i + 0x8000));
      onDone(btoa(bin), mime);
    };
    this.rec.start(250);
    this.watchSilence(autoStop);
    // Released while the mic was still opening (a quick tap): stop now, don't record a minute.
    if (this.stopAsked) {
      this.stopAsked = false;
      this.rec.stop();
    }
  }

  stop(): void {
    if (this.starting) this.stopAsked = true;
    else if (this.active) this.rec?.stop();
  }

  private watchSilence(autoStop: boolean): void {
    const ac = audioCtx();
    const src = ac.createMediaStreamSource(this.stream!);
    const an = ac.createAnalyser();
    an.fftSize = 1024;
    src.connect(an);
    const data = new Uint8Array(an.fftSize);
    let spoke = false;
    let quietSince = performance.now();
    const started = performance.now();
    const tick = () => {
      if (!this.active) return;
      an.getByteTimeDomainData(data);
      let sum = 0;
      for (const v of data) sum += ((v - 128) / 128) ** 2;
      this.level = Math.min(1, Math.sqrt(sum / data.length) * 6);
      const now = performance.now();
      if (this.level > 0.12) {
        spoke = true;
        quietSince = now;
      }
      if ((autoStop && spoke && now - quietSince > 1200) || now - started > 60000) this.stop();
      else this.raf = requestAnimationFrame(tick);
    };
    this.raf = requestAnimationFrame(tick);
  }
}
