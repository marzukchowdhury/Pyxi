"""Cut the "Just ask" film from the real takes (website/.shots/film2/takes/, see film_take.py).

    .venv/Scripts/python.exe website/tools/film_cut.py

Each scene is: the request as a caption, in quotes, then the real output, with the waiting cut
out (jump cuts, never a speed-up) and the frame brought in on the part that matters. Anything
personal in a take is blurred here. Pyxi's own voice (Kokoro) says the few lines between scenes,
over the same soft music as the first film. Output: website/.shots/film2/pyxi-just-ask.mp4.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import av
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

SITE = Path(__file__).resolve().parents[1]
REPO = SITE.parent
FILM = SITE / ".shots" / "film2"
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(Path(__file__).parent))
from film import RATE, music, read_wav  # noqa: E402  (the first film's sound helpers)

FPS, W, H = 30, 1920, 1080
BG = (7, 8, 11)
INK, DIM, FLY, ICE = (244, 247, 251), (150, 158, 172), (255, 200, 97), (158, 203, 255)
FONTS = Path("C:/Windows/Fonts")
font = lambda size, bold=True: ImageFont.truetype(str(FONTS / ("seguisb.ttf" if bold else "segoeui.ttf")), size)
CONTENT = (0, 110, 1920, 1040)  # a take without the browser's own bars and the taskbar

# One scene: the request, the take, where to look (a crop of the take), the pieces of the take to
# keep (start, end in take seconds; the last one is the output and is held), what to blur, and
# the line Pyxi says over it (None: let the result speak).
SCENES = [
    dict(ask="Pyxi, can you open a new tab on Mozilla Firefox and open YouTube and play Billie Jean by Michael Jackson?",
         take="e-song", crop=CONTENT, cuts=[(19.6, 21.0), (31.0, 34.6)], say=None),
    dict(ask="What's this post about?", take="s2-post", crop=(760, 470, 1920, 1031),
         cuts=[(14.0, 15.2), (21.6, 23.0), (25.6, 27.0), (29.6, 33.4)],
         blur=[(1010, 110, 1920, 566), (1500, 566, 1920, 1040), (760, 470, 1010, 1040)], keep=(1040, 566, 1500, 1040),
         say="Anything you can see, I can read with you."),
    dict(ask="I have a biology test tomorrow. Can you explain this cell diagram to me?", take="d-cell",
         crop=(250, 300, 1150, 735), cuts=[(23.0, 25.5), (29.5, 32.0), (35.5, 39.6)],
         say="Stuck on homework? I explain it right on the page."),
    dict(ask="How does a jet engine work? Build it in 3D and walk me through it.", take="d-engine",
         crop=(420, 180, 1920, 906), cuts=[(12.0, 14.6), (21.0, 23.6), (31.0, 33.6), (36.5, 40.6)],
         say="Ask how something works, and I build it for you."),
    dict(ask="Draw me a graph of how 100 dollars grows at 7 percent a year over 30 years.", take="d-graph",
         crop=(0, 340, 1340, 989), cuts=[(24.0, 25.6), (27.6, 29.6), (33.6, 37.8)],
         say="Numbers are easier as a picture."),
    dict(ask="Make a sample sales spreadsheet with regions and products, then create a pivot table of sales by region.",
         take="e-sheet", crop=(0, 130, 1500, 856), cuts=[(21.0, 22.4), (33.0, 38.0)],
         say="The boring part of your job is a sentence now."),
    dict(ask="Go through my Gmail and find the subscriptions I'm paying for.", take="e-subs",
         crop=(1000, 110, 1920, 555), cuts=[(21.5, 23.3), (33.0, 37.0)],
         say="I read the receipts, so you don't have to."),
    dict(ask="I want Thai food tonight, under 25 dollars each. Which places near me are open now, and which one is best by its reviews?",
         take="d-dinner", crop=CONTENT, cuts=[(19.2, 20.6), (29.0, 35.2)],
         say="Hungry? I check what's open, and what people say."),
]
QUICK = ["Set an alarm for seven.", "Remind me in ten minutes to stretch.", "What's the weather tomorrow?", "Split my screen in two."]
VOICE = {
    "intro": "Hi. I'm the small light in the corner of your screen. Ask me for something.",
    "quick": "And a few hundred other things.",
    "outro": "That voice you've been listening to? That's me. I'm Pyxi.",
}
for i, sc in enumerate(SCENES):
    if sc["say"]:
        VOICE[f"sc{i}"] = sc["say"]


async def voices():
    from pyxi.config import VoiceConfig
    from pyxi.voice.tts import KokoroTTS

    tts = KokoroTTS(VoiceConfig(), REPO / ".pyxi" / "models")
    out = FILM / "voice2"
    out.mkdir(exist_ok=True)
    durations = {}
    for k, text in VOICE.items():
        s = await tts.synthesize(text)
        (out / f"{k}.wav").write_bytes(s.wav)
        durations[k] = s.duration_s
    return durations


# ------------------------------------------------------------------ drawing
ease_out = lambda u: 1 - (1 - min(1, max(0, u))) ** 4
ease_in = lambda u: min(1, max(0, u)) ** 4
STAGE = (80, 56, 1840, 908)  # where the take sits in the frame
SW, SH = STAGE[2] - STAGE[0], STAGE[3] - STAGE[1]
mask = Image.new("L", (SW, SH), 0)
ImageDraw.Draw(mask).rounded_rectangle([0, 0, SW, SH], 26, fill=255)


def wrap(text, f, width):
    lines, cur = [], ""
    for word in text.split():
        trial = (cur + " " + word).strip()
        if f.getlength(trial) > width and cur:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    return lines + [cur]


def caption(img, text, shown=1.0, y_bottom=1046, size=38, quote=True):
    """The request, in quotes, in a pill; `shown` is how many of its words have arrived (0..1)."""
    f = font(size)
    text = f"\u201c{text}\u201d" if quote else text
    lines = wrap(text, f, 1500)
    words_total = len(text.split())
    upto = max(1, round(words_total * shown))
    lh = int(size * 1.32)
    w = int(max(f.getlength(l) for l in lines)) + 72
    h = lh * len(lines) + 34
    x0, y0 = (W - w) // 2, y_bottom - h
    d = ImageDraw.Draw(img, "RGBA")
    d.rounded_rectangle([x0, y0, x0 + w, y0 + h], 24, fill=(10, 12, 18, 236), outline=(255, 200, 97, 120), width=2)
    seen = 0
    for i, line in enumerate(lines):
        x = (W - f.getlength(line)) / 2
        for word in line.split():
            seen += 1
            d.text((x, y0 + 15 + i * lh), word, font=f, fill=INK if seen <= upto else (90, 96, 108))
            x += f.getlength(word + " ")


def brand(img):
    d = ImageDraw.Draw(img, "RGBA")
    d.ellipse([1778, 24, 1790, 36], fill=(255, 240, 204))
    d.ellipse([1772, 18, 1796, 42], outline=(255, 200, 97, 110), width=3)
    d.text((1804, 12), "Pyxi", font=font(28), fill=INK)


class Take:
    def __init__(self, sc):
        self.dir = FILM / "takes" / sc["take"]
        self.stamps = np.array(json.loads((self.dir / "take.json").read_text(encoding="utf-8"))["stamps"])
        self.sc = sc
        self.last = (-1, None)

    def at(self, t):
        i = max(0, int(np.searchsorted(self.stamps, t, side="right")) - 1)
        if i != self.last[0]:
            im = Image.open(self.dir / f"{i:06d}.jpg").convert("RGB")
            if self.sc.get("blur"):
                soft = im.filter(ImageFilter.GaussianBlur(16))
                keep = im.crop(self.sc["keep"])
                for box in self.sc["blur"]:
                    im.paste(soft.crop(box), box[:2])
                im.paste(keep, self.sc["keep"][:2])
            self.last = (i, im.crop(self.sc["crop"]).resize((SW, SH), Image.LANCZOS))
        return self.last[1]


def place(img, shot, dx=0.0, alpha=1.0):
    """Put a shot on the stage, slid sideways by dx px and faded, for the cut between scenes."""
    if alpha <= 0:
        return
    m = mask if alpha >= 1 else mask.point(lambda v: int(v * alpha))
    img.paste(shot, (STAGE[0] + int(dx), STAGE[1]), m)


def title(lines, sub=None, glow=1.0):
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img, "RGBA")
    cx, cy = W // 2, 330
    for r, a in ((70, 14), (44, 30), (24, 70)):
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 200, 97, int(a * glow)))
    d.ellipse([cx - 9, cy - 9, cx + 9, cy + 9], fill=(255, 240, 204))
    y = 450
    for text, size, color in lines:
        f = font(size)
        d.text(((W - f.getlength(text)) / 2, y), text, font=f, fill=color)
        y += int(size * 1.3)
    if sub:
        f = font(34, bold=False)
        d.text(((W - f.getlength(sub)) / 2, y + 24), sub, font=f, fill=DIM)
    return img


# ------------------------------------------------------------------ the timeline
def build(dur):
    """A list of (seconds, draw(t) -> Image) segments, and where each voice line starts."""
    segs, says, t = [], {}, 0.0

    def add(length, draw):
        nonlocal t
        segs.append((t, length, draw))
        t += length

    says["intro"] = 0.5
    add(dur["intro"] + 1.0, lambda u: title([("Things I asked my computer today.", 66, INK)], glow=0.6 + 0.4 * ease_out(u * 3)))
    for i, sc in enumerate(SCENES):
        take = Take(sc)
        first = sc["cuts"][0][0]
        words = len(sc["ask"].split())
        ask_len = max(1.3, words * 0.16 + 0.7)

        def ask(u, take=take, first=first, sc=sc, ask_len=ask_len):
            img = Image.new("RGB", (W, H), BG)
            place(img, take.at(first), dx=230 * (1 - ease_out(u * ask_len / 0.3)), alpha=0.35 + 0.65 * ease_out(u * ask_len / 0.3))
            ImageDraw.Draw(img, "RGBA").rectangle([0, 0, W, H], fill=(7, 8, 11, 120))
            caption(img, sc["ask"], shown=min(1, u * ask_len / (ask_len - 0.6)), y_bottom=620 + caption_h(sc["ask"]) // 2, size=46)
            brand(img)
            return img

        add(ask_len, ask)
        if sc["say"]:
            says[f"sc{i}"] = t + 0.25
        need = (dur[f"sc{i}"] + 0.9) if sc["say"] else 0
        got = sum(b - a for a, b in sc["cuts"])
        hold = max(1.6, need - got + (sc["cuts"][-1][1] - sc["cuts"][-1][0]))  # the output stays to be read
        for k, (a, b) in enumerate(sc["cuts"]):
            last = k == len(sc["cuts"]) - 1
            length = max(b - a, hold) if last else b - a

            def shot(u, take=take, a=a, b=b, length=length, last=last, sc=sc):
                img = Image.new("RGB", (W, H), BG)
                tt = min(b, a + u * length)
                out = ease_in((u * length - (length - 0.2)) / 0.2) if last else 0  # leaves moving left
                place(img, take.at(tt), dx=-230 * out, alpha=1 - 0.7 * out)
                caption(img, sc["ask"], size=30, y_bottom=1052)
                brand(img)
                return img

            add(length, shot)
    says["quick"] = t + 0.2
    each = max(0.75, (dur["quick"] + 0.6) / len(QUICK))
    for q in QUICK:
        def quick(u, q=q):
            img = Image.new("RGB", (W, H), BG)
            caption(img, q, y_bottom=600, size=62)
            brand(img)
            return img
        add(each, quick)
    quiet = t  # one beat with no music and no voice before the last line
    add(0.9, lambda u: Image.new("RGB", (W, H), BG))
    says["outro"] = t + 0.2
    add(dur["outro"] + 2.6, lambda u: title([("Pyxi. Just ask.", 110, INK)], sub="Free beta for Windows  \u00b7  pyxi-site.vercel.app", glow=ease_out(u * 4)))
    return segs, says, t, quiet


def caption_h(text, size=46):
    return int(size * 1.32) * len(wrap(f"\u201c{text}\u201d", font(size), 1500)) + 34


def main():
    dur = asyncio.run(voices())
    segs, says, total, quiet = build(dur)
    n = int(total * RATE)
    speech = np.zeros(n, dtype=np.float32)
    for k, start in says.items():
        clip = read_wav(FILM / "voice2" / f"{k}.wav")
        clip = clip / max(1e-6, np.abs(clip).max()) * 0.82
        i0 = int(start * RATE)
        speech[i0 : i0 + len(clip)] += clip[: n - i0]
    tt = np.arange(n) / RATE
    win = int(0.35 * RATE)
    csum = np.concatenate([[0.0], np.cumsum(np.abs(speech), dtype=np.float64)])
    level = ((csum[np.minimum(np.arange(n) + win // 2, n)] - csum[np.maximum(np.arange(n) - win // 2, 0)]) / win).astype(np.float32)
    bed = music(total, np.random.default_rng(3)) * 0.22 * (1 - 0.55 * np.clip(level / 0.04, 0, 1))[:, None]
    bed *= (1 - np.clip((tt - (quiet - 0.5)) / 0.5, 0, 1) * np.clip(((quiet + 1.4) - tt) / 0.5, 0, 1))[:, None]  # the beat of silence
    mix = np.clip(bed + speech[:, None], -1, 1)

    target = FILM / "pyxi-just-ask.mp4"
    with av.open(str(target), "w") as out:
        v = out.add_stream("libx264", rate=FPS)
        v.width, v.height, v.pix_fmt = W, H, "yuv420p"
        v.options = {"crf": "18", "preset": "medium", "movflags": "+faststart"}
        a = out.add_stream("aac", rate=RATE)
        a.bit_rate, a.layout = 192000, "stereo"
        k = 0
        for start, length, draw in segs:
            while k / FPS < start + length:
                frame = av.VideoFrame.from_image(draw(min(1.0, max(0.0, (k / FPS - start) / length))))
                frame.pts = k
                for p in v.encode(frame):
                    out.mux(p)
                k += 1
        for p in v.encode():
            out.mux(p)
        pcm = (mix * 32767).astype(np.int16)
        for i in range(0, len(pcm), 1024):
            af = av.AudioFrame.from_ndarray(np.ascontiguousarray(pcm[i : i + 1024].T.reshape(2, -1)), format="s16p", layout="stereo")
            af.sample_rate, af.pts = RATE, i
            for p in a.encode(af):
                out.mux(p)
        for p in a.encode():
            out.mux(p)
    print(f"{target} {total:.1f} s, {target.stat().st_size / 1e6:.1f} MB")
    print("scene starts:", [round(s, 1) for s, _, _ in segs])


if __name__ == "__main__":
    main()
