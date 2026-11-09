// The alarm and timer chime: three soft rising notes, again every couple of seconds, until it's
// dismissed (talking to Pyxi, the dismiss hotkey) or a minute has passed. Made with WebAudio, so
// there's no sound file to ship.

let ac: AudioContext | null = null;
let loop: ReturnType<typeof setInterval> | null = null;
let until = 0;

const NOTES = [784, 988, 1175]; // G5, B5, D6

function ring(): void {
  ac ??= new AudioContext();
  if (ac.state === "suspended") void ac.resume();
  const t0 = ac.currentTime + 0.02;
  NOTES.forEach((hz, i) => {
    const osc = ac!.createOscillator();
    const gain = ac!.createGain();
    osc.type = "sine";
    osc.frequency.value = hz;
    const t = t0 + i * 0.18;
    gain.gain.setValueAtTime(0, t);
    gain.gain.linearRampToValueAtTime(0.35, t + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.001, t + 0.55);
    osc.connect(gain).connect(ac!.destination);
    osc.start(t);
    osc.stop(t + 0.6);
  });
}

export function startChime(maxMs = 60_000): void {
  stopChime();
  until = Date.now() + maxMs;
  ring();
  loop = setInterval(() => (Date.now() > until ? stopChime() : ring()), 2200);
}

export function stopChime(): void {
  if (loop) clearInterval(loop);
  loop = null;
}

export const chiming = (): boolean => loop !== null;
