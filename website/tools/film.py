"""Make the Pyxi film: Pyxi's own voice, the film page played once and recorded, sound mixed in.

    .venv/Scripts/python.exe website/tools/film.py [voice] [capture] [encode]     (all three by default)

Needs the website's dev server on http://localhost:5191 (the film page is /film/). Everything is
made here: the lines are in website/src/film/film.js, the voice is Pyxi's default (Kokoro, the
same code path the app speaks with), the picture is the film page recorded frame by frame from a
headless Chromium on the real GPU, and the music is a few soft notes worked out below.
Output: website/.shots/film/pyxi-film.mp4 (the folder is git-ignored).
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import sys
import wave
from pathlib import Path

import numpy as np

SITE = Path(__file__).resolve().parents[1]
REPO = SITE.parent
OUT = SITE / ".shots" / "film"
URL = "http://localhost:5191/film/"
FPS = 30
RATE = 48000
sys.path.insert(0, str(REPO / "src"))


# ------------------------------------------------------------------ the film page
async def page(play):
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        b = await p.chromium.launch(
            args=["--enable-gpu", "--use-angle=d3d11", "--ignore-gpu-blocklist", "--enable-webgl", "--autoplay-policy=no-user-gesture-required"]
        )
        ctx = await b.new_context(viewport={"width": 1920, "height": 1080}, device_scale_factor=1)
        pg = await ctx.new_page()
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        await pg.goto(URL, wait_until="networkidle")
        await pg.evaluate("film.ready")
        result = await play(ctx, pg)
        if errors:
            print("page errors:", errors[:5])
        await b.close()
        return result


# ------------------------------------------------------------------ 1. the voice
async def voice():
    from pyxi.config import VoiceConfig
    from pyxi.voice.tts import KokoroTTS

    lines = await page(lambda ctx, pg: pg.evaluate("film.lines"))
    tts = KokoroTTS(VoiceConfig(), REPO / ".pyxi" / "models")
    (OUT / "voice").mkdir(parents=True, exist_ok=True)
    durations = {}
    for line in lines:
        speech = await tts.synthesize(line["text"])
        (OUT / "voice" / f"{line['id']}.wav").write_bytes(speech.wav)
        durations[line["id"]] = round(speech.duration_s, 3)
        print(f"  {line['id']:<6} {speech.duration_s:5.2f} s  {line['text']}")
    (OUT / "voice.json").write_text(json.dumps(durations, indent=1), encoding="utf-8")
    print(f"voice: {len(lines)} lines, {sum(durations.values()):.1f} s of speech")


# ------------------------------------------------------------------ 2. the picture
async def capture():
    durations = json.loads((OUT / "voice.json").read_text(encoding="utf-8"))
    frames_dir = OUT / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    for old in frames_dir.glob("*.jpg"):
        old.unlink()
    stamps = []

    async def play(ctx, pg):
        cdp = await ctx.new_cdp_session(pg)

        def on_frame(e):
            name = f"{len(stamps):06d}.jpg"
            (frames_dir / name).write_bytes(base64.b64decode(e["data"]))
            stamps.append(e["metadata"]["timestamp"])
            asyncio.ensure_future(cdp.send("Page.screencastFrameAck", {"sessionId": e["sessionId"]}))

        cdp.on("Page.screencastFrame", on_frame)
        await cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 93, "maxWidth": 1920, "maxHeight": 1080, "everyNthFrame": 1})
        await pg.wait_for_timeout(400)
        log = await pg.evaluate("(d) => film.start(d)", durations)
        await pg.wait_for_timeout(300)
        await cdp.send("Page.stopScreencast")
        return log

    log = await page(play)
    log["stamps"] = stamps
    (OUT / "log.json").write_text(json.dumps(log), encoding="utf-8")
    t0 = log["wall0"] / 1000
    inside = [s for s in stamps if t0 <= s <= t0 + log["total"]]
    gaps = np.diff(inside)
    print(f"capture: {log['total']:.1f} s, {len(inside)} frames, {len(inside) / log['total']:.1f} a second, longest gap {gaps.max() * 1000:.0f} ms, gaps over 100 ms: {(gaps > 0.1).sum()}")


# ------------------------------------------------------------------ 3. sound and the file
def read_wav(path: Path) -> np.ndarray:
    """A mono wav as floats at RATE."""
    with wave.open(io.BytesIO(path.read_bytes())) as w:
        rate = w.getframerate()
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
        if w.getnchannels() == 2:
            data = data.reshape(-1, 2).mean(axis=1)
    if rate != RATE:
        import av

        resampler = av.AudioResampler(format="flt", layout="mono", rate=RATE)
        frame = av.AudioFrame.from_ndarray(data.reshape(1, -1), format="flt", layout="mono")
        frame.sample_rate = rate
        parts = [f.to_ndarray().reshape(-1) for f in resampler.resample(frame)]
        parts += [f.to_ndarray().reshape(-1) for f in resampler.resample(None)]
        data = np.concatenate(parts).astype(np.float32)
    return data


def music(seconds: float, rng: np.random.Generator) -> np.ndarray:
    """A soft bed: four slow chords that melt into each other, and a few high notes like fireflies."""
    n = int(seconds * RATE)
    t = np.arange(n) / RATE
    out = np.zeros((n, 2), dtype=np.float32)
    note = lambda semis: 220.0 * 2 ** (semis / 12)  # from A3
    chords = [[-12, -5, 3, 10, 14], [-16, -9, 0, 7, 12], [-9, -2, 4, 11, 16], [-14, -7, 2, 5, 14]]
    bar = 7.5
    for k in range(int(seconds / bar) + 2):
        start = k * bar - 1.5
        env = np.clip((t - start) / 3.0, 0, 1) * np.clip((start + bar + 3.0 - t) / 3.0, 0, 1)
        env = np.sin(env * np.pi / 2) ** 2
        if not env.any():
            continue
        for i, semis in enumerate(chords[k % len(chords)]):
            f = note(semis)
            for side, detune in ((0, 0.9985), (1, 1.0015)):
                wobble = 1 + 0.12 * np.sin(2 * np.pi * (0.11 + 0.03 * i) * t + i + side)
                tone = np.sin(2 * np.pi * f * detune * t) + 0.22 * np.sin(2 * np.pi * 2 * f * detune * t + 0.4)
                out[:, side] += (tone * env * wobble * (0.5 if i == 0 else 0.3 / (1 + 0.25 * i))).astype(np.float32)
    # fireflies: single high notes from the same scale, each a quick glint with a long tail
    scale = [15, 17, 19, 22, 24, 27, 29, 31]
    at = 2.0
    while at < seconds - 4:
        f = note(rng.choice(scale))
        i0 = int(at * RATE)
        length = int(2.6 * RATE)
        tt = np.arange(min(length, n - i0)) / RATE
        ping = np.sin(2 * np.pi * f * tt) * np.exp(-tt * 2.4) * np.clip(tt / 0.012, 0, 1) * 0.16
        pan = rng.uniform(0.15, 0.85)
        out[i0 : i0 + len(tt), 0] += (ping * (1 - pan)).astype(np.float32)
        out[i0 : i0 + len(tt), 1] += (ping * pan).astype(np.float32)
        at += rng.uniform(1.6, 4.2)
    fade = np.clip(t / 2.5, 0, 1) * np.clip((seconds - t) / 3.0, 0, 1)
    return out * fade[:, None] / max(1e-6, np.abs(out).max())


def sound(log) -> np.ndarray:
    total = log["total"]
    n = int(total * RATE)
    speech = np.zeros(n, dtype=np.float32)
    for lid, line in log["lines"].items():
        clip = read_wav(OUT / "voice" / f"{lid}.wav")
        clip = clip / max(1e-6, np.abs(clip).max()) * 0.82
        i0 = int(line["start"] * RATE)
        speech[i0 : i0 + len(clip)] += clip[: n - i0]
    # the music steps back a little whenever Pyxi is talking
    loud = np.abs(speech)
    win = int(0.35 * RATE)
    total_so_far = np.concatenate([[0.0], np.cumsum(loud, dtype=np.float64)])
    ends = np.minimum(np.arange(n) + win // 2, n)
    starts = np.maximum(np.arange(n) - win // 2, 0)
    level = ((total_so_far[ends] - total_so_far[starts]) / win).astype(np.float32)  # a running average
    duck = 1 - 0.55 * np.clip(level / 0.04, 0, 1)
    bed = music(total, np.random.default_rng(7)) * 0.2 * duck[:, None]
    mix = bed + speech[:, None]
    return np.clip(mix / max(1.0, np.abs(mix).max() / 0.95), -1, 1)


def encode():
    import av
    from PIL import Image

    log = json.loads((OUT / "log.json").read_text(encoding="utf-8"))
    stamps = np.array(log["stamps"]) - log["wall0"] / 1000
    mix = sound(log)
    target = OUT / "pyxi-film.mp4"
    with av.open(str(target), "w") as out:
        v = out.add_stream("libx264", rate=FPS)
        v.width, v.height, v.pix_fmt = 1920, 1080, "yuv420p"
        v.options = {"crf": "17", "preset": "medium", "movflags": "+faststart"}
        a = out.add_stream("aac", rate=RATE)
        a.bit_rate = 192000
        a.layout = "stereo"
        last, frame = -1, None
        count = int(log["total"] * FPS)
        for k in range(count):
            i = max(0, int(np.searchsorted(stamps, k / FPS, side="right")) - 1)
            if i != last:
                img = Image.open(OUT / "frames" / f"{i:06d}.jpg").convert("RGB")
                if img.size != (1920, 1080):
                    img = img.resize((1920, 1080), Image.LANCZOS)
                frame = av.VideoFrame.from_image(img)
                last = i
            frame.pts = k
            for packet in v.encode(frame):
                out.mux(packet)
        for packet in v.encode():
            out.mux(packet)
        pcm = (mix * 32767).astype(np.int16)
        step = 1024
        for i in range(0, len(pcm), step):
            chunk = np.ascontiguousarray(pcm[i : i + step].T.reshape(2, -1))
            af = av.AudioFrame.from_ndarray(chunk, format="s16p", layout="stereo")
            af.sample_rate = RATE
            af.pts = i
            for packet in a.encode(af):
                out.mux(packet)
        for packet in a.encode():
            out.mux(packet)
    print(f"encode: {target} ({target.stat().st_size / 1e6:.1f} MB, {log['total']:.1f} s)")


if __name__ == "__main__":
    steps = sys.argv[1:] or ["voice", "capture", "encode"]
    OUT.mkdir(parents=True, exist_ok=True)
    if "voice" in steps:
        asyncio.run(voice())
    if "capture" in steps:
        asyncio.run(capture())
    if "encode" in steps:
        encode()
