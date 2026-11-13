"""Render the whole launch film (docs/FILM-MOCKUP.md): every scene in src/film2/story.js, in order.

    .venv/Scripts/python.exe website/tools/film2_film.py [scene ids...]     (all scenes by default)

Needs the website's dev server on http://localhost:5191. Each scene's page is a pure function
of time; this steps it one frame at a time at 60 frames a second, so the picture never stutters
and the sound sits on the same clock. Pyxi's lines are spoken by its own voice (Kokoro).
The music is generated here and is a stand-in until the founder picks a licensed track.
Output: website/.shots/film2/pyxi-launch-film.mp4 and its report (git-ignored).
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
from render import blip, clap, hat, impact, kick, noise, riser, tone, tt  # noqa: E402  (the sample scene's sounds)

FPS, W, H, BEAT = 60, 1920, 1080, 0.5
BASE = "http://localhost:5191/film2/"
hz = lambda midi: 440.0 * 2 ** ((midi - 69) / 12)


async def speak(tts, sid: str, text: str) -> np.ndarray:
    path = OUT / "voice3" / f"{sid}.wav"
    path.parent.mkdir(exist_ok=True)
    path.write_bytes((await tts.synthesize(text)).wav)
    clip = read_wav(path)
    return clip / max(1e-6, np.abs(clip).max()) * 0.9


def word_times(clip: np.ndarray):
    from faster_whisper import WhisperModel

    sound = clip[: len(clip) // 3 * 3].reshape(-1, 3).mean(1).astype(np.float32)
    segments, _ = WhisperModel("base.en", device="cpu", compute_type="int8").transcribe(sound, word_timestamps=True)
    return [(w.word.strip().lower().strip(".,?!"), w.start) for s in segments for w in s.words]


def saw(f, seconds, decay):
    t = tt(seconds)
    return sum(np.sin(2 * np.pi * f * k * t) / k for k in range(1, 9)) * np.exp(-t * decay) * np.clip(t / 0.004, 0, 1)


def music(total: float, drop: float, changes: list[float], slams: list[float], end: float) -> np.ndarray:
    """A bright, driving stand-in track at 120 beats a minute: four on the floor, claps, off-beat
    hats, a bouncing bass, chord stabs and a hook, with a lift into every new scene and air before the name."""
    rng = np.random.default_rng(21)
    n = int(total * RATE)
    out = np.zeros((n, 2), dtype=np.float32)

    def put(sig, at, gain=1.0, pan=0.5):
        j = int(at * RATE)
        if j < 0 or j >= n:
            return
        k = min(len(sig), n - j)
        out[j : j + k, 0] += (sig[:k] * gain * (1 - pan) * 2).astype(np.float32)
        out[j : j + k, 1] += (sig[:k] * gain * pan * 2).astype(np.float32)

    for at in slams:  # every line of big type lands with a thump
        put(kick(), at, 0.9)
    for k, at in enumerate(np.arange(0.0, drop - 0.01, BEAT)):  # before the beat: a few plucked notes
        put(tone(hz([69, 76, 72, 79, 76][k % 5]), 0.9, 5.0), at, 0.16, 0.35 + 0.3 * (k % 2))
    put(riser(rng, 1.0), drop - 1.0, 0.25)
    put(impact(rng), drop, 0.6)
    bars = [(45, [69, 72, 76]), (41, [69, 72, 77]), (48, [67, 72, 76]), (43, [67, 71, 74])]  # Am F C G
    hook = [76, 79, 81, 79, 76, 72, 74, 76]
    stop = end - BEAT
    step = BEAT / 2
    quiet = lambda at: any(c - 0.5 <= at < c for c in changes)  # the drums step out half a second before a new scene
    for k, at in enumerate(np.arange(drop, stop - 0.01, step)):
        root, chord = bars[int(k // 8) % 4]
        section = int(k // 64) % 3  # every eight bars the track changes a little
        if not quiet(at):
            if k % 2 == 0:
                put(kick(), at, 0.85)
                if (k // 2) % 2 == 1:
                    put(clap(rng), at, 0.36)
            else:
                put(hat(rng), at, 0.16, 0.62)
            if section == 2 and k % 4 == 3:
                put(hat(rng), at + step / 2, 0.1, 0.4)
        bounce = [0, 0, 12, 0, 7, 0, 12, 10][k % 8]
        put(tone(hz(root - 12 + bounce), 0.24, 8.0, (1.0, 0.55, 0.25)), at, 0.3 if k % 2 else 0.2)
        if k % 8 in (3, 6):  # chord stabs, off the beat
            for m in chord:
                put(saw(hz(m), 0.3, 11.0), at, 0.035, 0.3 + 0.2 * (m % 3))
        if section >= 1:  # the hook comes in after the first eight bars
            put(tone(hz(hook[k % 8] + (12 if section == 2 else 0)), 0.5, 8.0), at, 0.085, 0.35 + 0.3 * ((k % 4) / 3))
        else:
            put(tone(hz(chord[[0, 1, 2, 1][k % 4]] + 12), 0.5, 8.0), at, 0.09, 0.3 + 0.4 * ((k % 4) / 3))
    for c in changes:  # the lift into each new scene, and its landing
        put(riser(rng, 0.5), c - 0.5, 0.2)
        put(impact(rng), c, 0.45)
        put(noise(rng, 0.6, 2) * np.exp(-tt(0.6) * 6), c, 0.1)  # a splash of cymbal
    put(riser(rng, BEAT), stop, 0.2)
    put(impact(rng), end, 0.85)
    t = tt(total - end)
    for i, m in enumerate([45, 52, 57, 59, 60, 64]):  # a chord left ringing under the name
        for side, detune in ((0.3, 0.998), (0.7, 1.002)):
            put(np.sin(2 * np.pi * hz(m) * detune * t) * np.exp(-t * 0.8) * np.clip(t / 0.01, 0, 1), end, 0.09 / (1 + 0.2 * i), side)
    put(tone(hz(81), 1.4, 3.2), end + BEAT, 0.14, 0.6)
    return out / max(1e-6, np.abs(out).max())


async def main(only: list[str]):
    from playwright.async_api import async_playwright
    from pyxi.config import VoiceConfig
    from pyxi.voice.tts import KokoroTTS

    tts = KokoroTTS(VoiceConfig(), REPO / ".pyxi" / "models")
    async with async_playwright() as p:
        b = await p.chromium.launch(args=["--enable-gpu", "--use-angle=d3d11", "--ignore-gpu-blocklist", "--enable-webgl"])
        errors = []
        film = await b.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
        ram = await b.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
        for pg, name, flag in ((film, "film.html", "film"), (ram, "ram.html", "ready")):
            pg.on("pageerror", lambda e: errors.append(str(e)))
            await pg.goto(BASE + name, wait_until="networkidle")
            await pg.wait_for_selector(f"body[data-{flag}]", timeout=20000)
            # ask for every face the film uses, so none is still loading when a frame is taken
            await pg.evaluate("""Promise.all(['800 176px "Inter Variable"', '600 44px "Inter Variable"', '600 36px "Fraunces Variable"', 'italic 500 84px "Fraunces Variable"'].map(f => document.fonts.load(f)))""")
        story = await film.evaluate("STORY.map(s => ({id: s.id, reply: s.reply || null}))")
        scenes = [s for s in story if not only or s["id"] in only]

        # pass 1: the voice for every scene, and from it every scene's timeline
        plans, t0 = [], 0.0
        for i, s in enumerate(scenes):
            clip = await speak(tts, s["id"], s["reply"]) if s["reply"] else np.zeros(0, dtype=np.float32)
            dur = len(clip) / RATE
            if s["id"] == "engine":
                T = await ram.evaluate("T")
                heard = word_times(clip)
                start = lambda w: next(x for word, x in heard if word == w)
                reply_at = T["card"] + 1.7
                c = math.ceil((reply_at + dur + 1.6) / BEAT) * BEAT
                plan = {"kind": "ram", "a": [T["a1"], T["a2"]], "listen": T["listen"], "work": T["card"], "replyAt": reply_at, "total": c, "c": c, "at": [],
                        "steps": [reply_at + start("open"), reply_at + start("line"), reply_at + start("press")], "heard": " ".join(w for w, _ in heard)}
            else:
                plan = await film.evaluate("([id, v]) => plan(id, v)", [s["id"], dur])
            plans.append({"id": s["id"], "t0": t0, "plan": plan, "clip": clip, "dur": dur, "flashIn": i > 0 and plans[-1]["plan"]["kind"] != "slides"})
            t0 += plan["total"]
        total = t0
        n = int(total * RATE)

        # the sound: Pyxi's voice, its small sounds, and the track under them
        speech = np.zeros(n, dtype=np.float32)
        fx = np.zeros(n, dtype=np.float32)

        def put(buf, sig, at, gain=1.0):
            j = int(at * RATE)
            if j >= n:
                return
            k = min(len(sig), n - j)
            buf[j : j + k] += sig[:k] * gain

        rng = np.random.default_rng(5)
        slams, changes = [], []
        for sc in plans:
            o, pl = sc["t0"], sc["plan"]
            if o > 0:
                changes.append(o)
            if "replyAt" in pl and len(sc["clip"]):
                put(speech, sc["clip"], o + pl["replyAt"])
            if pl["kind"] == "slides":
                for k, at in enumerate(pl["at"]):
                    if sc["id"] == "end" and k < 2:
                        continue  # the beat of silence
                    put(fx, blip([660 + 110 * k], each=0.06, gain=0.06, decay=30), o + at)
                    if sc["id"] != "end":
                        slams.append(o + at)
                if sc["id"] == "open":
                    put(fx, impact(rng), 0.0, 0.35)  # the notch drops in
                    click = np.concatenate([tone(2200, 0.03, 160), np.zeros(int(0.04 * RATE)), tone(1800, 0.03, 160)])
                    put(fx, click, pl["at"][2], 0.5)  # the two keys go down
                    put(fx, blip([880, 1318.5]), pl["at"][2] + 0.12)  # and it listens
                continue
            slams += [o + a for a in pl["a"]]
            put(fx, riser(rng, 0.35) * 0.10, o + pl["listen"] - 0.3)
            put(fx, blip([880, 1318.5]), o + pl["listen"])  # it starts listening
            put(fx, blip([523.25, 784], each=0.12, gain=0.10, decay=11), o + pl["work"])  # its answer lands
            for at in pl.get("at", [])[2:]:
                put(fx, blip([1568], each=0.05, gain=0.05, decay=40), o + at)
            if pl.get("typeAt"):
                slams.append(o + pl["typeAt"])
            if pl["kind"] == "ram":
                s1, s2, s3 = pl["steps"]
                put(fx, impact(rng) * 0.3, o + pl["work"])
                put(fx, blip([660], each=0.07, gain=0.07, decay=30), o + s1 + 0.25)
                put(fx, blip([523.25, 784], each=0.1, gain=0.08, decay=14), o + s2 + 0.6)
                put(fx, np.concatenate([tone(2200, 0.03, 160), np.zeros(int(0.05 * RATE)), tone(1900, 0.03, 160)]), o + s3 + 0.5, 0.5)
        last = plans[-1]
        name_at = last["t0"] + last["plan"]["at"][2] if last["id"] == "end" else total - 0.5
        drop = plans[1]["t0"] if len(plans) > 1 and plans[0]["id"] == "open" else 2.0
        win = int(0.3 * RATE)
        csum = np.concatenate([[0.0], np.cumsum(np.abs(speech), dtype=np.float64)])
        idx = np.arange(n)
        level = ((csum[np.minimum(idx + win // 2, n)] - csum[np.maximum(idx - win // 2, 0)]) / win).astype(np.float32)
        bed = music(total, drop, [c for c in changes if drop < c < name_at - 1], slams, name_at) * 0.44 * (1 - 0.64 * np.clip(level / 0.04, 0, 1))[:, None]
        mix = np.clip(bed + (fx + speech)[:, None], -1, 1)
        per = RATE // FPS

        target = OUT / "pyxi-launch-film.mp4"
        with av.open(str(target), "w") as out:
            v = out.add_stream("libx264", rate=FPS)
            v.width, v.height, v.pix_fmt = W, H, "yuv420p"
            v.options = {"crf": "17", "preset": "medium", "movflags": "+faststart"}
            a = out.add_stream("aac", rate=RATE)
            a.bit_rate, a.layout = 192000, "stereo"
            k = 0
            for sc in plans:
                pl = sc["plan"]
                # the voice's loudness in this scene, one number a frame: the firefly glows with it
                j0 = int(sc["t0"] * RATE)
                part = np.abs(speech[j0 : j0 + int(pl["total"] * RATE)])
                env = part[: len(part) // per * per].reshape(-1, per).mean(1)
                env = (np.convolve(env, np.ones(5) / 5, mode="same") / max(1e-6, env.max())).round(3).tolist() if env.max() > 0 else []
                if pl["kind"] == "ram":
                    s1, s2, s3 = pl["steps"]
                    ra, d = pl["replyAt"], sc["dur"]
                    got = await ram.evaluate("cfg => setup(cfg)", {"s1": s1, "s2": s2, "s3": s3, "c": pl["c"], "total": pl["c"] + 3, "env": env, "flashIn": sc["flashIn"], "phrases": [
                        {"text": "Open the two clips,", "at": s1 - 0.1, "until": s2 - 0.1},
                        {"text": "line up the notch,", "at": s2 - 0.1, "until": s3 - 0.25},
                        {"text": "and press down until both clips click.", "at": s3 - 0.25, "until": ra + d + 0.5}]})
                    page = ram
                else:
                    got = await film.evaluate("([id, cfg]) => setScene(id, cfg)", [sc["id"], {"voice": sc["dur"], "env": env, "flashIn": sc["flashIn"]}])
                    page = film
                assert all(got["fonts"]), f"a font did not load in {sc['id']}: {got['fonts']}"
                await page.bring_to_front()
                frames = round(pl["total"] * FPS)
                for f in range(frames):
                    await page.evaluate("t => { draw(t) }", f / FPS)
                    frame = av.VideoFrame.from_image(Image.open(io.BytesIO(await page.screenshot(type="png"))).convert("RGB"))
                    frame.pts = k
                    k += 1
                    for pkt in v.encode(frame):
                        out.mux(pkt)
                print(f"{sc['id']:<8} from {sc['t0']:6.2f} s, {pl['total']:5.2f} s", flush=True)
            for pkt in v.encode():
                out.mux(pkt)
            pcm = (mix[: int(k / FPS * RATE)] * 32767).astype(np.int16)
            for i in range(0, len(pcm), 1024):
                af = av.AudioFrame.from_ndarray(np.ascontiguousarray(pcm[i : i + 1024].T.reshape(2, -1)), format="s16p", layout="stereo")
                af.sample_rate, af.pts = RATE, i
                for pkt in a.encode(af):
                    out.mux(pkt)
            for pkt in a.encode():
                out.mux(pkt)
        await b.close()
    report = {"seconds": round(total, 2), "scenes": [{"id": s["id"], "from": round(s["t0"], 2), "seconds": round(s["plan"]["total"], 2), "voice_at": round(s["t0"] + s["plan"]["replyAt"], 2) if "replyAt" in s["plan"] and s["dur"] else None, "voice_seconds": round(s["dur"], 2)} for s in plans], "name_at": round(name_at, 2), "page_errors": errors[:5]}
    (OUT / "pyxi-launch-film.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    asyncio.run(main(sys.argv[1:]))
