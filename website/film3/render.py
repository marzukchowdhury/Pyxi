"""Make Pyxi's demo clips: each scene page stepped one frame at a time, with Pyxi's real voice.

    <venv python> website/film3/render.py look  <scene>            a contact sheet, quickly, to check the drawing
    <venv python> website/film3/render.py frame <scene> 5.5 9.0    single frames at full size
    <venv python> website/film3/render.py clip  <scene>            the master, the web loop, the poster, the sheet
    <venv python> website/film3/render.py intro                    the film's opening, where Pyxi says what it is
    <venv python> website/film3/render.py hero                     the film: the opening, the four, the list, the name
    <venv python> website/film3/render.py all

Scenes: setting, post, pivot, desktop (the words are in story.js). Nothing is recorded from a
screen. A scene page draws every frame from the time it is given (draw(t)), so the picture is
exactly 60 frames a second and the sound is placed on the same clock. The voice is Pyxi's own
(Kokoro, the code the app speaks with). The music is a stand-in worked out below, only so the
cuts have a beat to land on: the founder picks the real track.

Run with PYTHONPATH pointing at this checkout's src. Working files go to website/.shots/film3
(git-ignored); what the site uses goes to website/public/clips.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
SITE = HERE.parent
REPO = SITE.parent
WORK = SITE / ".shots" / "film3"
OUT = SITE / "public" / "clips"
sys.path.insert(0, str(SITE / "tools"))
from film import RATE, read_wav  # noqa: E402  (the first film's sound helpers)

FPS, W, H = 60, 1920, 1080
BEAT = 0.5
SPEED = 1.1  # Pyxi speaks a little quicker than its default in the clips
ASKER = "am_michael"  # the person who asks: another of the voice pack's voices, so it is plainly not Pyxi
SCENES = ["setting", "post", "pivot", "desktop"]
WORKERS = 4
RECAP = 4.0  # the film's last list (kit.js: T.R in the film's last scene)
LOOP_BYTES = 1_500_000  # a web loop stays under this
MASTER_BYTES = 4_700_000  # a master that goes in git stays under this
# The frames are JPEGs (full-range colour). Video players expect the limited "TV" range, and
# plain yuv420p: convert once, here, and say so in the file.
TV = "scale=in_range=pc:out_range=tv,format=yuv420p"
TV_TAGS = ["-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709"]


def ffmpeg() -> str:
    found = shutil.which("ffmpeg")
    if not found:
        raise SystemExit("ffmpeg is not on PATH: install it or add it, then run this again")
    return found


def run(*args: str) -> None:
    done = subprocess.run([ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", *args], capture_output=True, text=True)
    if done.returncode:
        raise SystemExit(f"ffmpeg failed: {' '.join(args)}\n{done.stderr}")


# ------------------------------------------------------------------ the voice
async def voice(scene: str, says: list[str], who: str | None = None) -> list[np.ndarray]:
    """Each line Pyxi speaks, in its own voice. Kept between runs unless the words change."""
    folder = WORK / "voice"
    folder.mkdir(parents=True, exist_ok=True)
    clips, tts = [], None
    for i, text in enumerate(says):
        key = hashlib.sha1(f"{SPEED}|{who or ''}|{text}".encode()).hexdigest()[:10]
        path = folder / f"{scene}_{i}_{key}.wav"
        if not path.exists():
            if tts is None:
                from pyxi.config import VoiceConfig
                from pyxi.voice.tts import KokoroTTS

                # the voice pack lives in the main checkout's .pyxi (a worktree has none of its own)
                models = next(d / ".pyxi" / "models" for d in [REPO, *REPO.parents] if (d / ".pyxi" / "models").exists())
                tts = KokoroTTS(VoiceConfig(speed=SPEED), models)
            speech = await tts.synthesize(text, who)
            path.write_bytes(speech.wav)
        clip = read_wav(path)
        # trim the silence either side, so a line starts when it is placed
        loud = np.flatnonzero(np.abs(clip) > 0.012)
        clip = clip[max(0, loud[0] - int(0.02 * RATE)) : loud[-1] + int(0.08 * RATE)]
        clips.append(clip / max(1e-6, np.abs(clip).max()) * 0.9)
    return clips


# ------------------------------------------------------------------ sounds
def tt(seconds):
    return np.arange(int(seconds * RATE)) / RATE


def blip(freqs, each=0.09, gain=0.12, decay=18.0):
    return np.concatenate([np.sin(2 * np.pi * f * tt(each)) * np.exp(-tt(each) * decay) * np.clip(tt(each) / 0.004, 0, 1) for f in freqs]) * gain


def kick():
    t = tt(0.28)
    return np.sin(2 * np.pi * np.cumsum(44 + 120 * np.exp(-t * 34)) / RATE) * np.exp(-t * 11) * np.clip(t / 0.002, 0, 1)


def noise(rng, seconds, sharp=2):
    x = rng.standard_normal(int(seconds * RATE))
    for _ in range(sharp):  # each pass takes away more of the low end
        x = np.diff(x, prepend=0.0)
    return x / max(1e-6, np.abs(x).max())


def hat(rng):
    return noise(rng, 0.05) * np.exp(-tt(0.05) * 110)


def clap(rng):
    t = tt(0.22)
    return noise(rng, 0.22, 1) * (np.exp(-t * 24) + 0.7 * np.exp(-np.abs(t - 0.012) * 400) + 0.6 * np.exp(-np.abs(t - 0.024) * 400)) * 0.6


def tone(f, seconds, decay, rich=(1.0, 0.35, 0.12)):
    t = tt(seconds)
    return sum(a * np.sin(2 * np.pi * f * (k + 1) * t) for k, a in enumerate(rich)) * np.exp(-t * decay) * np.clip(t / 0.004, 0, 1)


def riser(rng, seconds):
    t = tt(seconds)
    return noise(rng, seconds, 1) * (t / seconds) ** 2.4


def impact(rng):
    t = tt(1.2)
    return np.sin(2 * np.pi * np.cumsum(38 + 60 * np.exp(-t * 18)) / RATE) * np.exp(-t * 3.6) + 0.35 * noise(rng, 1.2, 1) * np.exp(-t * 16)


def groove(total: float, drop: float, slams: list[float], end: float) -> np.ndarray:
    """A stand-in track: a few plucked notes, then a beat from `drop`, air before `end`, a chord on it."""
    rng = np.random.default_rng(11)
    n = int(total * RATE)
    out = np.zeros((n, 2), dtype=np.float32)
    hz = lambda midi: 440.0 * 2 ** ((midi - 69) / 12)  # noqa: E731

    def put(sig, at, gain=1.0, pan=0.5):
        j = int(at * RATE)
        if j >= n or j < 0:
            return
        k = min(len(sig), n - j)
        out[j : j + k, 0] += (sig[:k] * gain * (1 - pan) * 2).astype(np.float32)
        out[j : j + k, 1] += (sig[:k] * gain * pan * 2).astype(np.float32)

    for at in slams:  # each line of the opening lands with a thump
        put(kick(), at, 0.9)
        put(noise(rng, 0.03) * np.exp(-tt(0.03) * 160), at, 0.25)
    for k, at in enumerate(np.arange(0.0, drop - 0.01, BEAT)):
        put(tone(hz([69, 76, 72, 79, 76][k % 5]), 0.9, 5.0), at, 0.16, 0.35 + 0.3 * (k % 2))
    put(riser(rng, 0.8), drop - 0.8, 0.22)
    put(impact(rng), drop, 0.55)
    bars = [(33, [69, 72, 76]), (29, [69, 72, 77]), (36, [67, 72, 76]), (31, [67, 71, 74])]  # Am F C G
    stop = end - BEAT
    for k, at in enumerate(np.arange(drop, stop - 0.01, BEAT / 2)):
        root, chord = bars[int(k // 8) % 4]
        if k % 2 == 0:
            put(kick(), at, 0.8)
            if (k // 2) % 2 == 1:
                put(clap(rng), at, 0.34)
        else:
            put(hat(rng), at, 0.14, 0.62)
        put(tone(hz(root), 0.26, 7.0, (1.0, 0.5, 0.22)), at, 0.34 if k % 2 else 0.2)  # the bass leans on the off beat
        put(tone(hz(chord[[0, 1, 2, 1][k % 4]] + (12 if k % 8 >= 6 else 0)), 0.6, 7.5), at, 0.11, 0.3 + 0.4 * ((k % 4) / 3))
    put(riser(rng, BEAT), stop, 0.2)
    put(impact(rng), end, 0.8)
    t = tt(max(0.1, total - end))
    for i, m in enumerate([45, 52, 57, 59, 60, 64]):  # A minor with its ninth, left ringing under the name
        for side, detune in ((0.3, 0.998), (0.7, 1.002)):
            put(np.sin(2 * np.pi * hz(m) * detune * t) * np.exp(-t * 1.0) * np.clip(t / 0.01, 0, 1), end, 0.09 / (1 + 0.2 * i), side)
    put(tone(hz(81), 1.4, 3.2), end + BEAT, 0.14, 0.6)  # one high note for the tagline
    return out / max(1e-6, np.abs(out).max())


def small_sound(kind: str) -> np.ndarray:
    """Pyxi's own small sounds, and the screen's."""
    if kind == "listen":  # the two-note chime when it listens
        return blip([880, 1318.5])
    if kind in ("pin", "card"):  # a card pins
        return blip([523.25, 784], each=0.12, gain=0.10, decay=11)
    if kind == "mark":  # a mark lands on the thing to click
        return blip([987.77], each=0.14, gain=0.09, decay=14)
    if kind == "tick":  # a point is written, a number lands
        return blip([1568], each=0.05, gain=0.04, decay=40)
    if kind == "click":  # the user's own click
        return blip([2200, 1400], each=0.018, gain=0.09, decay=160)
    if kind == "cheer":  # they did it themselves
        return blip([783.99, 1046.5, 1318.5, 1568], each=0.085, gain=0.10, decay=10)
    if kind == "done":
        return blip([659.25, 987.77], each=0.13, gain=0.10, decay=9)
    if kind == "move":  # a file drops into its folder
        return blip([330], each=0.05, gain=0.035, decay=50)
    if kind == "whoosh":
        return riser(np.random.default_rng(3), 0.35) * 0.10
    raise ValueError(kind)


# ------------------------------------------------------------------ the page
class Stage:
    """One headless Chromium with a scene page set up in it."""

    def __init__(self, scene: str):
        self.scene = scene

    async def __aenter__(self):
        from playwright.async_api import async_playwright

        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch()
        self.page = await self.browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
        self.errors: list[str] = []
        self.page.on("pageerror", lambda e: self.errors.append(str(e)))
        self.page.on("console", lambda m: self.errors.append(m.text) if m.type == "error" else None)
        await self.page.goto((HERE / f"{self.scene}.html").as_uri())
        await self.page.evaluate("Promise.all(['800 176px Inter', '600 46px Inter', '500 20px Inter', '600 33px Fraunces', 'italic 500 84px Fraunces'].map(f => document.fonts.load(f)))")
        return self

    async def __aexit__(self, *exc):
        await self.browser.close()
        await self.pw.stop()

    async def setup(self, cfg: dict) -> dict:
        got = await self.page.evaluate("cfg => setup(cfg)", cfg)
        assert all(got["fonts"]), f"a font did not load: {got['fonts']}"
        assert not self.errors, f"the page reported: {self.errors[:3]}"
        return got

    async def shot(self, t: float, path: Path | None = None) -> bytes:
        await self.page.evaluate("t => draw(t)", t)
        data = await self.page.screenshot(type="jpeg", quality=96)
        if path:
            path.write_bytes(data)
        return data


async def frames(scene: str, cfg: dict, env: list[float], times: list[tuple[int, float]], folder: Path) -> None:
    """Draw the frames at `times` (number, seconds) into `folder`, on several pages at once."""
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("*.jpg"):
        old.unlink()

    async def worker(part):
        async with Stage(scene) as st:
            await st.setup(cfg)
            await st.page.evaluate("e => { Film.C.env = e }", env)
            for k, t in part:
                await st.shot(t, folder / f"{k:05d}.jpg")
            assert not st.errors, f"the page reported: {st.errors[:3]}"

    await asyncio.gather(*(worker(times[i::WORKERS]) for i in range(WORKERS)))


def sheet(paths: list[tuple[str, Path | bytes]], target: Path, cols: int = 6, width: int = 480) -> None:
    """A contact sheet: every picture small, in order, with its time written on it."""
    import io

    h = width * H // W
    rows = math.ceil(len(paths) / cols)
    out = Image.new("RGB", (cols * width, rows * h), "#000")
    pen = ImageDraw.Draw(out)
    try:
        font = ImageFont.truetype("arial.ttf", 20)
    except OSError:
        font = ImageFont.load_default()
    for i, (label, src) in enumerate(paths):
        im = Image.open(io.BytesIO(src) if isinstance(src, bytes) else src).convert("RGB").resize((width, h), Image.LANCZOS)
        x, y = (i % cols) * width, (i // cols) * h
        out.paste(im, (x, y))
        pen.rectangle([x, y, x + 64, y + 26], fill="#000")
        pen.text((x + 6, y + 2), label, fill="#fff", font=font)
    target.parent.mkdir(parents=True, exist_ok=True)
    out.save(target, quality=90)


# ------------------------------------------------------------------ one clip
async def plan(scene: str) -> dict:
    """The clip's timeline from the real lengths of the lines, and its sound."""
    async with Stage(scene) as st:
        first = await st.setup({})
    says = first["says"]
    clips = await voice(scene, says)
    durs = [len(c) / RATE for c in clips]
    asked = (await voice(scene + "_ask", [first["ask"]], ASKER))[0]
    ask_dur = len(asked) / RATE
    async with Stage(scene) as st:
        got = await st.setup({"durs": durs, "askDur": ask_dur, "mode": "clip"})
    T = got["T"]
    total = got["total"]
    n = int(total * RATE)
    speech = np.zeros(n, dtype=np.float32)
    for at, clip in zip(T["voice"], clips):
        j = int(at * RATE)
        speech[j : j + len(clip)] += clip[: n - j]
    per = RATE // FPS
    env = np.abs(speech[: n // per * per]).reshape(-1, per).mean(1)
    env = np.convolve(env, np.ones(5) / 5, mode="same") / max(1e-6, env.max())
    # the person's question, in their own voice (the firefly glows only with Pyxi's)
    j = int(T["words"][0] * RATE)
    speech[j : j + len(asked)] += asked[: n - j] * 0.9
    return {"scene": scene, "says": says, "ask": got["ask"], "askDur": ask_dur, "durs": durs, "T": T, "total": total, "speech": speech, "env": [round(float(v), 3) for v in env]}


def film_order() -> list[str]:
    done = subprocess.run(["node", "-e", f"global.window={{}};require({json.dumps(str(HERE / 'story.js'))});console.log(JSON.stringify(window.STORY.film))"], capture_output=True, text=True, check=True)
    return json.loads(done.stdout)


def sound(p: dict, total: float | None = None, hits: tuple = (), extra: tuple = ()) -> np.ndarray:
    """The clip's sound: the stand-in track, Pyxi's small sounds, and its voice on top.
    `hits`: times a low hit lands (the name arriving); `extra`: more small sounds, (kind, at)."""
    T = p["T"]
    total = total or p["total"]
    n = int(total * RATE)
    speech = np.zeros(n, dtype=np.float32)
    k = min(n, len(p["speech"]))
    speech[:k] = p["speech"][:k]
    fx = np.zeros(n, dtype=np.float32)
    low = impact(np.random.default_rng(5)) * 0.5
    for kind, at in [("whoosh", T["listen"] - 0.3), *T.get("sfx", []), *extra, *[("hit", h) for h in hits]]:
        sig = low if kind == "hit" else small_sound(kind)
        j = int(at * RATE)
        if 0 <= j < n:
            m = min(len(sig), n - j)
            fx[j : j + m] += sig[:m]
    win = int(0.3 * RATE)
    csum = np.concatenate([[0.0], np.cumsum(np.abs(speech), dtype=np.float64)])
    idx = np.arange(n)
    level = ((csum[np.minimum(idx + win // 2, n)] - csum[np.maximum(idx - win // 2, 0)]) / win).astype(np.float32)
    bed = groove(total, T["b"], [T["a1"], T["a2"], T["c"] + 0.5], T["c"]) * 0.42 * (1 - 0.62 * np.clip(level / 0.04, 0, 1))[:, None]
    return np.clip(bed + (fx + speech)[:, None], -1, 1)


def write_wav(path: Path, mix: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes((mix * 32767).astype(np.int16).tobytes())


def link(paths: list[Path], folder: Path) -> Path:
    """A run of frames in order, as one numbered folder ffmpeg can read."""
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    for i, src in enumerate(paths):
        os.link(src, folder / f"{i:05d}.jpg")
    return folder


def encode_master(seq: Path, wav: Path, seconds: float, hq: Path, small: Path) -> None:
    """Two masters: one at full quality (kept out of git), one small enough to commit."""
    hq.parent.mkdir(parents=True, exist_ok=True)
    small.parent.mkdir(parents=True, exist_ok=True)
    src = ["-framerate", str(FPS), "-i", str(seq / "%05d.jpg"), "-i", str(wav)]
    tail = ["-vf", TV, *TV_TAGS, "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", "-shortest"]
    run(*src, "-c:v", "libx264", "-preset", "slow", "-crf", "16", *tail, str(hq))
    rate = int((MASTER_BYTES * 8 / seconds - 170_000) / 1000)
    log = str(WORK / "pass")
    run(*src[:4], "-c:v", "libx264", "-preset", "slow", "-b:v", f"{rate}k", "-pass", "1", "-passlogfile", log, "-vf", TV, *TV_TAGS, "-an", "-f", "null", "NUL")
    run(*src, "-c:v", "libx264", "-preset", "slow", "-b:v", f"{rate}k", "-pass", "2", "-passlogfile", log, *tail, str(small))


def encode_loop(seq: Path, seconds: float, stem: Path, fps: int = 30, budget: int = LOOP_BYTES) -> None:
    """A silent loop for the web: 1280 wide, H.264 and WebM, each under the budget."""
    rate = int(budget * 8 / seconds * 0.93 / 1000)
    src = ["-framerate", str(fps), "-i", str(seq / "%05d.jpg")]
    scale = ["-vf", f"scale=1280:720:flags=lanczos,{TV}", *TV_TAGS, "-an"]
    log = str(WORK / "pass")
    x264 = ["-c:v", "libx264", "-preset", "veryslow", "-profile:v", "high", "-b:v", f"{rate}k", "-maxrate", f"{int(rate * 1.6)}k", "-bufsize", f"{rate * 3}k", "-g", str(fps * 4)]
    run(*src, *scale, *x264, "-pass", "1", "-passlogfile", log, "-f", "null", "NUL")
    run(*src, *scale, *x264, "-pass", "2", "-passlogfile", log, "-movflags", "+faststart", str(stem.with_suffix(".mp4")))
    vp9 = ["-c:v", "libvpx-vp9", "-b:v", f"{rate}k", "-row-mt", "1", "-g", str(fps * 4)]
    run(*src, *scale, *vp9, "-deadline", "good", "-cpu-used", "4", "-pass", "1", "-passlogfile", log, "-f", "null", "NUL")
    run(*src, *scale, *vp9, "-deadline", "good", "-cpu-used", "1", "-pass", "2", "-passlogfile", log, str(stem.with_suffix(".webm")))
    for ext in (".mp4", ".webm"):
        size = stem.with_suffix(ext).stat().st_size
        assert size < budget * 1.07, f"{stem.name}{ext} is {size} bytes, over the budget"


async def clip(scene: str) -> dict:
    p = await plan(scene)
    T, total = p["T"], p["total"]
    c = T["c"]
    base = {"durs": p["durs"], "askDur": p["askDur"]}
    folder = WORK / scene
    n = int(round(total * FPS))
    nc = int(round(c * FPS))
    print(f"{scene}: {total:.2f} s, name at {c:.2f} s; lines {[round(d, 2) for d in p['durs']]} at {[round(v, 2) for v in T['voice']]}", flush=True)
    await frames(scene, {**base, "mode": "clip"}, p["env"], [(k, k / FPS) for k in range(n)], folder / "clip")
    # the pieces that differ in the other cuts: a loop's soft end, the film's way in, the film's end card
    tail = int(0.5 * FPS)
    await frames(scene, {**base, "mode": "loop"}, p["env"], [(k, k / FPS) for k in range(nc - tail, nc)], folder / "loop")
    if scene == film_order()[-1]:  # the film ends on this one: after its lesson, the list, then the name
        nr = int(round(T["r"] * FPS))
        await frames(scene, {**base, "mode": "heroEnd"}, p["env"], [(k, k / FPS) for k in range(nr, nr + int((RECAP + 3.5) * FPS))], folder / "heroend")

    write_wav(folder / "sound.wav", sound(p, hits=(T["n"],)))
    meta = {"scene": scene, "total": total, "c": c, "r": T["r"], "durs": p["durs"], "voice": T["voice"], "says": p["says"], "ask": p["ask"], "poster": T.get("poster", c - 1.2), "show": T.get("show", [T["act"], c - 0.6])}
    (folder / "meta.json").write_text(json.dumps(meta, indent=1))
    np.save(folder / "speech.npy", p["speech"])
    (folder / "T.json").write_text(json.dumps(T))
    pack(scene)
    return meta


async def intro() -> None:
    """The film's opening: Pyxi says what it is, in its own voice, and the words land as it does."""
    async with Stage("intro") as st:
        says = (await st.setup({}))["says"]
    clips = await voice("intro", says)
    durs = [len(c) / RATE for c in clips]
    async with Stage("intro") as st:
        got = await st.setup({"durs": durs})
    T, total = got["T"], got["total"]
    n = int(total * RATE)
    speech = np.zeros(n, dtype=np.float32)
    for at, one in zip(T["voice"], clips):
        j = int(at * RATE)
        k = min(len(one), n - j)
        speech[j : j + k] += one[:k]
    per = RATE // FPS
    env = np.abs(speech[: n // per * per]).reshape(-1, per).mean(1)
    env = np.convolve(env, np.ones(5) / 5, mode="same") / max(1e-6, env.max())
    env = [round(float(v), 3) for v in env]
    folder = WORK / "intro"
    print(f"intro: {total:.2f} s; lines {[round(d, 2) for d in durs]} at {[round(v, 2) for v in T['voice']]}", flush=True)
    await frames("intro", {"durs": durs}, env, [(k, k / FPS) for k in range(int(round(total * FPS)))], folder / "clip")
    fx = np.zeros(n, dtype=np.float32)
    for kind, at in T["sfx"]:
        sig = small_sound(kind)
        j = int(at * RATE)
        if 0 <= j < n:
            m = min(len(sig), n - j)
            fx[j : j + m] += sig[:m]
    win = int(0.3 * RATE)
    csum = np.concatenate([[0.0], np.cumsum(np.abs(speech), dtype=np.float64)])
    idx = np.arange(n)
    level = ((csum[np.minimum(idx + win // 2, n)] - csum[np.maximum(idx - win // 2, 0)]) / win).astype(np.float32)
    # only the plucked notes of the stand-in track: the beat waits for the first scene
    bed = groove(total, total + 10, T["slams"], total + 12) * 0.42 * (1 - 0.62 * np.clip(level / 0.04, 0, 1))[:, None]
    np.save(folder / "mix.npy", np.clip(bed + (fx + speech)[:, None], -1, 1))
    (folder / "meta.json").write_text(json.dumps({"total": total, "says": says, "voice": T["voice"]}, indent=1))
    paths = sorted((folder / "clip").glob("*.jpg"))
    sheet([(f"{k / FPS:.1f}", paths[k]) for k in range(0, len(paths), FPS // 2)], WORK / "sheets" / "intro.jpg")


def pack(scene: str) -> None:
    """From a scene's frames and sound: the masters, the web loop, the poster and the sheet."""
    folder = WORK / scene
    meta = json.loads((folder / "meta.json").read_text())
    total, c = meta["total"], meta["c"]
    n, nc, tail = int(round(total * FPS)), int(round(c * FPS)), int(0.5 * FPS)
    OUT.mkdir(parents=True, exist_ok=True)
    seq = link(sorted((folder / "clip").glob("*.jpg")), folder / "seq")
    encode_master(seq, folder / "sound.wav", total, WORK / "masters" / f"{scene}.mp4", OUT / "master" / f"{scene}.mp4")
    body = [folder / "clip" / f"{k:05d}.jpg" for k in range(0, nc - tail, 2)] + [folder / "loop" / f"{k:05d}.jpg" for k in range(nc - tail, nc, 2)]
    encode_loop(link(body, folder / "seq"), c, OUT / f"demo-{scene}")
    # the poster: the payoff, held
    Image.open(folder / "clip" / f"{int(meta['poster'] * FPS):05d}.jpg").resize((1280, 720), Image.LANCZOS).save(OUT / f"demo-{scene}.webp", quality=82, method=6)
    # the sheets: a frame every half second of the master, and the loop's last and first frames side by side
    sheet([(f"{k / FPS:.1f}", folder / "clip" / f"{k:05d}.jpg") for k in range(0, n, FPS // 2)], WORK / "sheets" / f"{scene}.jpg")
    sheet([(f"-{len(body) - i}", body[i]) for i in range(len(body) - 9, len(body), 2)] + [(f"+{i}", body[i]) for i in (0, 1)], WORK / "sheets" / f"{scene}-loop-seam.jpg", cols=7, width=400)
    sizes = {str(f.relative_to(SITE)).replace("\\", "/"): f.stat().st_size for f in [OUT / "master" / f"{scene}.mp4", OUT / f"demo-{scene}.mp4", OUT / f"demo-{scene}.webm", OUT / f"demo-{scene}.webp", WORK / "masters" / f"{scene}.mp4"]}
    print(f"{scene}: done {sizes}", flush=True)


# ------------------------------------------------------------------ the film
def hero() -> None:
    """The film: the four scenes one after another, each opening out of the light the last ended
    in, then the name. And a short silent loop of it for the top of the home page."""
    order = film_order()
    metas = [json.loads((WORK / s / "meta.json").read_text()) for s in order]
    opening = sorted((WORK / "intro" / "clip").glob("*.jpg"))
    paths: list[Path] = list(opening)
    starts = []
    tail = int(0.5 * FPS)
    for m in metas:
        folder = WORK / m["scene"]
        # a scene skips its half second of dark: its first line lands on the cut
        starts.append(len(paths) / FPS - 0.5)
        paths += [folder / "clip" / f"{k:05d}.jpg" for k in range(tail, int(round(m["r"] * FPS)))]
    last = WORK / metas[-1]["scene"]
    paths += sorted((last / "heroend").glob("*.jpg"))
    total = len(paths) / FPS
    # the sound: the opening, then each scene's own, laid at its start; a scene's last chord rings on under the next one's opening
    mix = np.zeros((int(total * RATE), 2), dtype=np.float32)
    first = np.load(WORK / "intro" / "mix.npy")
    mix[: len(first)] += first
    for i, m in enumerate(metas):
        p = {"T": json.loads((WORK / m["scene"] / "T.json").read_text()), "speech": np.load(WORK / m["scene"] / "speech.npy"), "total": m["total"]}
        if i < len(metas) - 1:  # let the ring die away under the next scene
            length = m["r"] + 1.6
            part = sound(p, length)
            fade = np.clip((length - np.arange(len(part)) / RATE) / 1.6, 0, 1) ** 2
            part *= fade[:, None]
        else:  # the list, a note for each row, and a low hit as the name lands
            length = m["r"] + RECAP + 3.5
            part = sound(p, length, hits=(m["r"] + RECAP,), extra=tuple(("pin", m["r"] + 0.5 + k * 0.5) for k in range(4)))
        j = int(starts[i] * RATE)
        k = min(len(part), len(mix) - j)
        mix[j : j + k] += part[:k]
    mix = np.clip(mix, -1, 1)
    wav = WORK / "hero" / "sound.wav"
    write_wav(wav, mix)
    seq = link(paths, WORK / "hero" / "seq")
    hq = WORK / "masters" / "pyxi-film.mp4"
    run("-framerate", str(FPS), "-i", str(seq / "%05d.jpg"), "-i", str(wav), "-c:v", "libx264", "-preset", "slow", "-crf", "17", "-vf", TV, *TV_TAGS, "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", "-shortest", str(hq))
    # the one the site plays with sound: 1280 wide, 30 frames a second, small enough to commit
    web = OUT / "pyxi-film.mp4"
    rate = int((MASTER_BYTES * 8 / total - 110_000) / 1000)
    log = str(WORK / "pass")
    src = ["-framerate", str(FPS), "-i", str(seq / "%05d.jpg"), "-i", str(wav)]
    shape = ["-vf", f"scale=1280:720:flags=lanczos,fps=30,{TV}", *TV_TAGS, "-c:v", "libx264", "-preset", "veryslow", "-b:v", f"{rate}k"]
    run(*src[:4], *shape, "-pass", "1", "-passlogfile", log, "-an", "-f", "null", "NUL")
    run(*src, *shape, "-pass", "2", "-passlogfile", log, "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", "-shortest", str(web))
    # the silent preview: the moment Pyxi does it, from each scene, cut on the beat
    pre: list[Path] = []
    for i, m in enumerate(metas):
        a, b = m["show"]
        pre += paths[int((starts[i] + a) * FPS) : int((starts[i] + b) * FPS) : 2]
    encode_loop(link(pre, WORK / "hero" / "pre"), len(pre) / 30, OUT / "pyxi-film-preview", budget=1_600_000)
    end = paths[-int(1.2 * FPS)]
    Image.open(paths[int((starts[0] + metas[0]["poster"]) * FPS)]).resize((1280, 720), Image.LANCZOS).save(OUT / "pyxi-film.webp", quality=82, method=6)
    sheet([(f"{k / FPS:.0f}", paths[k]) for k in range(0, len(paths), FPS)], WORK / "sheets" / "pyxi-film.jpg", cols=8, width=360)
    sheet([(f"{k / 30:.1f}", pre[k]) for k in range(0, len(pre), 15)], WORK / "sheets" / "pyxi-film-preview.jpg")
    (WORK / "hero" / "meta.json").write_text(json.dumps({"total": total, "starts": starts, "preview": len(pre) / 30, "end": str(end)}, indent=1))
    print(f"film: {total:.2f} s, scenes start at {[round(s, 2) for s in starts]}; preview {len(pre) / 30:.2f} s; {hq.stat().st_size} / {web.stat().st_size} bytes", flush=True)


# ------------------------------------------------------------------ quick looks
async def look(scene: str, step: float = 0.5, mode: str = "clip") -> None:
    async with Stage(scene) as st:
        says = (await st.setup({}))["says"]
        first = await st.setup({})
        durs = [len(c) / RATE for c in await voice(scene, says)]
        cfg = {"mode": mode, "durs": durs}
        if first["ask"]:
            cfg["askDur"] = len((await voice(scene + "_ask", [first["ask"]], ASKER))[0]) / RATE
        got = await st.setup(cfg)
        total = got["total"]
        shots = [(f"{t:.1f}", await st.shot(t)) for t in np.arange(0, total, step)]
        assert not st.errors, st.errors[:3]
    target = WORK / "look" / f"{scene}.jpg"
    sheet(shots, target)
    print(f"{target}  ({total:.2f} s lines {[round(d, 2) for d in durs]}; T = {json.dumps({k: v for k, v in got['T'].items() if k not in ('phrases', 'sfx')})})")


async def frame(scene: str, times: list[float], mode: str = "clip") -> None:
    (WORK / "look").mkdir(parents=True, exist_ok=True)
    async with Stage(scene) as st:
        says = (await st.setup({}))["says"]
        first = await st.setup({})
        cfg = {"mode": mode, "durs": [len(c) / RATE for c in await voice(scene, says)]}
        if first["ask"]:
            cfg["askDur"] = len((await voice(scene + "_ask", [first["ask"]], ASKER))[0]) / RATE
        await st.setup(cfg)
        for t in times:
            await st.shot(t, WORK / "look" / f"{scene}_{t:05.2f}.jpg")
        assert not st.errors, st.errors[:3]
    print("frames in", WORK / "look")


async def main() -> None:
    what = sys.argv[1] if len(sys.argv) > 1 else "all"
    if what == "look":
        await look(sys.argv[2], float(sys.argv[3]) if len(sys.argv) > 3 else 0.5, sys.argv[4] if len(sys.argv) > 4 else "clip")
    elif what == "frame":
        await frame(sys.argv[2], [float(x) for x in sys.argv[3:]])
    elif what == "intro":
        await intro()
    elif what == "clip":
        for s in sys.argv[2:]:
            await clip(s)
    elif what == "pack":  # encode again from frames already drawn
        for s in sys.argv[2:] or SCENES:
            pack(s)
        hero()
    elif what == "hero":
        hero()
    else:
        await intro()
        for s in SCENES:
            await clip(s)
        hero()


if __name__ == "__main__":
    asyncio.run(main())
