// Audio: a queue for Wisp's sentences (with "this sentence started" callbacks, so drawings
// land exactly when the words do), a level meter for the buddy's mouth, and push-to-talk.

type Clip = { b64: string | null; text: string; onStart?: () => void; onEnd?: () => void };

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

export class AudioQueue {
  private queue: Clip[] = [];
  private playing = false;
  private current: AudioBufferSourceNode | null = null;
  private analyser: AnalyserNode | null = null;
  private gen = 0;

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

  stop(): void {
    this.gen++;
    this.queue = [];
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
    clip.onStart?.();
    try {
      if (clip.b64) await this.playWav(clip.b64);
      else if (clip.text) await speakWithOs(clip.text);
    } catch (err) {
      console.warn("audio failed", err);
    }
    if (gen !== this.gen) return;
    clip.onEnd?.();
    void this.next();
  }

  private async playWav(b64: string): Promise<void> {
    const ac = audioCtx();
    const buffer = await ac.decodeAudioData(b64ToBuffer(b64));
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

function speakWithOs(text: string): Promise<void> {
  return new Promise((resolve) => {
    if (!("speechSynthesis" in window)) return resolve();
    const u = new SpeechSynthesisUtterance(text);
    u.rate = 1.05;
    u.onend = () => resolve();
    u.onerror = () => resolve();
    speechSynthesis.speak(u);
  });
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
  async start(onDone: (b64: string, mime: string) => void, autoStop = true): Promise<void> {
    if (this.active) return;
    this.stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
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
  }

  stop(): void {
    if (this.active) this.rec?.stop();
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
