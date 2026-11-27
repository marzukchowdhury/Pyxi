"""What the clips sound like, for someone who cannot hear them: a transcript and the loudness.

    <venv python> website/film3/listen.py [file.mp4 ...]      (the four masters and the film by default)

For each file: its length, what a speech recogniser hears in it (faster_whisper, run on this PC,
with the time each phrase starts), and the loudness of each second in dB. Compare the transcript
with `says` in story.js.
"""

from __future__ import annotations

import sys
from pathlib import Path

import av
import numpy as np

SITE = Path(__file__).resolve().parents[1]
DEFAULT = [SITE / "public" / "clips" / "master" / f"{s}.mp4" for s in ("setting", "post", "pivot", "desktop")] + [SITE / "public" / "clips" / "pyxi-film.mp4"]


def samples(path: Path, rate: int = 16000) -> np.ndarray:
    out = []
    with av.open(str(path)) as f:
        resample = av.AudioResampler(format="flt", layout="mono", rate=rate)
        for frame in f.decode(audio=0):
            out += [r.to_ndarray().reshape(-1) for r in resample.resample(frame)]
    return np.concatenate(out).astype(np.float32)


def main() -> None:
    from faster_whisper import WhisperModel

    model = WhisperModel("base.en", device="cpu", compute_type="int8")
    for path in [Path(a) for a in sys.argv[1:]] or DEFAULT:
        pcm = samples(path)
        seconds = len(pcm) / 16000
        with av.open(str(path)) as f:
            v = f.streams.video[0]
            shape = f"{v.width}x{v.height} at {float(v.average_rate):.0f} fps, {v.codec_context.name}, {f.streams.audio[0].codec_context.name}"
        print(f"\n{path.name}: {seconds:.2f} s, {shape}, {path.stat().st_size / 1e6:.2f} MB")
        heard, _ = model.transcribe(pcm, language="en", vad_filter=False)
        for s in heard:
            print(f"  {s.start:6.2f}  {s.text.strip()}")
        per = [20 * np.log10(max(1e-5, float(np.sqrt(np.mean(pcm[i : i + 16000] ** 2))))) for i in range(0, len(pcm), 16000)]
        print("  loudness a second (dB):", " ".join(f"{d:.0f}" for d in per))


if __name__ == "__main__":
    main()
