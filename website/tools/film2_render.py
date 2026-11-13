"""Render the RAM scene of the launch film: film2/ram.html stepped one frame at a time, with
Pyxi's own voice (docs/FILM-MOCKUP.md; made the same way as the sample in .pyxi/film_sample).

    .venv/Scripts/python.exe website/tools/film2_render.py

Needs the website's dev server on http://localhost:5191. Nothing is recorded from a screen: the
page draws every frame from the time it is given, so the picture is exactly 60 frames a second
and the sound sits on the same clock. The voice is Pyxi's (Kokoro). The three steps on screen
are timed to the words Pyxi actually says, found by listening to the voice with faster_whisper.
The music is the sample's stand-in, not a real track.
Output: website/.shots/film2/scene-ram.mp4 (git-ignored).
"""

from __future__ import annotations

import asyncio
import io
import json
import math
import sys
from pathlib import Path

import av
import numpy as np
from PIL import Image

SITE = Path(__file__).resolve().parents[1]
REPO = SITE.parent
OUT = SITE / ".shots" / "film2"
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(SITE / "tools"))
sys.path.insert(0, str(REPO / ".pyxi" / "film_sample"))
from film import RATE, read_wav  # noqa: E402
from render import blip, groove, impact, riser, tone  # noqa: E402  (the sample scene's sounds)

FPS, W, H, BEAT = 60, 1920, 1080, 0.5
URL = "http://localhost:5191/film2/ram.html"


async def voice(text: str) -> Path:
    from pyxi.config import VoiceConfig
    from pyxi.voice.tts import KokoroTTS

    path = OUT / "ram-reply.wav"
    s = await KokoroTTS(VoiceConfig(), REPO / ".pyxi" / "models").synthesize(text)
    path.write_bytes(s.wav)
    return path


def words(path: Path):
    """Every word Pyxi says and when, heard back from the recording."""
    from faster_whisper import WhisperModel

    model = WhisperModel("base.en", device="cpu", compute_type="int8")
    sound = read_wav(path)  # 48 kHz; the model wants 16 kHz, so every three samples become one
    sound = sound[: len(sound) // 3 * 3].reshape(-1, 3).mean(1).astype(np.float32)
    segments, _ = model.transcribe(sound, word_timestamps=True)
    return [(w.word.strip().lower().strip(".,"), w.start, w.end) for s in segments for w in s.words]


async def main():
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        b = await p.chromium.launch(args=["--enable-gpu", "--use-angle=d3d11", "--ignore-gpu-blocklist", "--enable-webgl"])
        page = await b.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        await page.goto(URL, wait_until="networkidle")
        await page.wait_for_selector("body[data-ready]", timeout=20000)
        T = await page.evaluate("T")
        reply = await page.evaluate("import('/src/film2/story.js').then(m => m.STORY.find(s => s.id === 'engine').reply)")

        clip = read_wav(await voice(reply))
        clip = clip / max(1e-6, np.abs(clip).max()) * 0.9
        dur = len(clip) / RATE
        heard = words(OUT / "ram-reply.wav")
        said = " ".join(w for w, _, _ in heard)
        start = lambda word, after=0.0: next(s for w, s, _ in heard if w == word and s >= after)
        reply_at = T["card"] + 1.7  # the model has landed before Pyxi speaks
        s1 = reply_at + start("open")
        s2 = reply_at + start("line")
        s3 = reply_at + start("press")
        end = math.ceil((reply_at + dur + 1.6) / BEAT) * BEAT  # the result is held, then the cut, on a beat
        total = end + 2.75
        n = int(total * RATE)
        speech = np.zeros(n, dtype=np.float32)
        i0 = int(reply_at * RATE)
        speech[i0 : i0 + len(clip)] = clip[: n - i0]
        per = RATE // FPS
        env = np.abs(speech[: n // per * per]).reshape(-1, per).mean(1)
        env = np.convolve(env, np.ones(5) / 5, mode="same") / max(1e-6, env.max())
        cfg = {
            "s1": s1, "s2": s2, "s3": s3, "c": end, "total": total,
            "env": [round(float(v), 3) for v in env],
            "phrases": [
                {"text": "Open the two clips,", "at": s1 - 0.1, "until": s2 - 0.1},
                {"text": "line up the notch,", "at": s2 - 0.1, "until": s3 - 0.25},
                {"text": "and press down until both clips click.", "at": s3 - 0.25, "until": reply_at + dur + 0.5},
            ],
        }
        got = await page.evaluate("cfg => setup(cfg)", cfg)
        assert all(got["fonts"]), f"a font did not load: {got['fonts']}"

        fx = np.zeros(n, dtype=np.float32)

        def put(sig, at, gain=1.0):
            j = int(at * RATE)
            k = min(len(sig), n - j)
            fx[j : j + k] += sig[:k] * gain

        rng = np.random.default_rng(5)
        put(riser(rng, 0.35) * 0.10, T["listen"] - 0.3)
        put(blip([880, 1318.5]), T["listen"])  # it starts listening
        put(riser(rng, 0.5) * 0.14, T["card"] - 0.5)
        put(impact(rng) * 0.3, T["card"])  # the model lands
        for k in range(10):
            put(blip([1568 + 60 * k], each=0.04, gain=0.03, decay=50), T["card"] + 0.15 + k * 0.14)  # parts arriving
        put(blip([660], each=0.07, gain=0.07, decay=30), s1 + 0.25)  # the clips open
        put(blip([523.25, 784], each=0.1, gain=0.08, decay=14), s2 + 0.6)  # the notch meets the ridge
        click = np.concatenate([tone(2200, 0.03, 160), np.zeros(int(0.05 * RATE)), tone(1900, 0.03, 160)])
        put(click, s3 + 0.5, 0.5)  # both clips click shut
        win = int(0.3 * RATE)
        csum = np.concatenate([[0.0], np.cumsum(np.abs(speech), dtype=np.float64)])
        idx = np.arange(n)
        level = ((csum[np.minimum(idx + win // 2, n)] - csum[np.maximum(idx - win // 2, 0)]) / win).astype(np.float32)
        bed = groove(total, T["b"], [T["a1"], T["a2"]], end) * 0.42 * (1 - 0.62 * np.clip(level / 0.04, 0, 1))[:, None]
        mix = np.clip(bed + (fx + speech)[:, None], -1, 1)

        target = OUT / "scene-ram.mp4"
        with av.open(str(target), "w") as out:
            v = out.add_stream("libx264", rate=FPS)
            v.width, v.height, v.pix_fmt = W, H, "yuv420p"
            v.options = {"crf": "16", "preset": "medium", "movflags": "+faststart"}
            a = out.add_stream("aac", rate=RATE)
            a.bit_rate, a.layout = 192000, "stereo"
            frames = int(total * FPS)
            for k in range(frames):
                await page.evaluate("t => { draw(t) }", k / FPS)
                frame = av.VideoFrame.from_image(Image.open(io.BytesIO(await page.screenshot(type="png"))).convert("RGB"))
                frame.pts = k
                for pkt in v.encode(frame):
                    out.mux(pkt)
                if k % 180 == 0:
                    print(f"frame {k} of {frames}", flush=True)
            for pkt in v.encode():
                out.mux(pkt)
            pcm = (mix * 32767).astype(np.int16)
            for i in range(0, len(pcm), 1024):
                af = av.AudioFrame.from_ndarray(np.ascontiguousarray(pcm[i : i + 1024].T.reshape(2, -1)), format="s16p", layout="stereo")
                af.sample_rate, af.pts = RATE, i
                for pkt in a.encode(af):
                    out.mux(pkt)
            for pkt in a.encode():
                out.mux(pkt)
        await b.close()
    report = {"seconds": round(total, 2), "voice_from": round(reply_at, 2), "voice_seconds": round(dur, 2), "steps_at": [round(s1, 2), round(s2, 2), round(s3, 2)], "name_at": end, "heard": said, "page_errors": errors[:3]}
    (OUT / "scene-ram.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    asyncio.run(main())
