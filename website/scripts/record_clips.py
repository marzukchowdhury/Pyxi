"""Record the website's motion clips from Pyxi's real overlay, the same staged scenes as the README
screenshots (scripts/capture_readme.py), but moving.

Frames come straight from Chrome's compositor (CDP screencast, PNG, real time), then Playwright's
own ffmpeg encodes them to WebM (VP8). Nothing reads the real desktop.

  1. cd desktop && npx vite --config vite.web.config.ts      (the overlay preview, port 5199)
  2. ..\\.venv\\Scripts\\python scripts\\record_clips.py [name ...]   (from website/)
     names: point notes explain chip
"""

from __future__ import annotations

import asyncio
import base64
import glob
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image
from playwright.async_api import Page, async_playwright

SITE = Path(__file__).resolve().parents[1]
ROOT = SITE.parent
sys.path[:0] = [str(ROOT / "scripts"), str(SITE / "scripts")]
from capture_chip import ai_chip  # noqa: E402
from capture_readme import APPEARANCE, DARK, EXPORT, MOTION, show  # noqa: E402

URL = "http://localhost:5199"
OUT = SITE / "public" / "clips"
TMP = SITE / ".shots" / "frames"
GL = ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"]
FFMPEG = sorted(glob.glob(os.path.expandvars(r"%LOCALAPPDATA%\ms-playwright\ffmpeg-*\ffmpeg-win64.exe")))[-1]


class Recorder:
    """Record one clip: a fresh context with Playwright's video recorder, trimmed to the scene."""

    def __init__(self, b, name: str) -> None:
        self.b, self.name = b, name

    async def __aenter__(self) -> Page:
        TMP.mkdir(parents=True, exist_ok=True)
        self.ctx = await self.b.new_context(viewport={"width": 1440, "height": 900}, record_video_dir=str(TMP), record_video_size={"width": 1440, "height": 900})
        self.page = await self.ctx.new_page()
        self.t0 = time.time()
        await self.page.goto(f"{URL}/overlay.html?backdrop=1&live=1")
        await self.page.wait_for_timeout(1500)
        await self.page.mouse.move(1250, 720)
        self.start = time.time() - self.t0  # trim the page load off the front
        return self.page

    async def __aexit__(self, *exc: object) -> None:
        video = self.page.video
        OUT.mkdir(parents=True, exist_ok=True)
        await self.page.screenshot(path=str(TMP / f"{self.name}.png"))
        Image.open(TMP / f"{self.name}.png").convert("RGB").resize((1280, 800), Image.LANCZOS).save(OUT / f"{self.name}.webp", "WEBP", quality=86)
        await self.ctx.close()
        raw = Path(await video.path())
        OUT.mkdir(parents=True, exist_ok=True)
        dest = OUT / f"{self.name}.webm"
        shutil.copyfile(raw, TMP / f"{self.name}-raw.webm")
        dest.write_bytes(raw.read_bytes())
        print(f"{self.name}: {dest.stat().st_size // 1024} KB, scene starts at {self.start:.1f}s")


async def inject(pg: Page, ev: dict) -> None:
    await pg.evaluate("(e) => window.__pyxi.inject(e)", ev)


async def glide(pg: Page, to: tuple[int, int], steps: int = 40) -> None:
    await pg.mouse.move(*to, steps=steps)


async def point(b) -> None:
    async with Recorder(b, "point") as pg:
        await glide(pg, (1010, 480), 30)
        await inject(pg, {"type": "state", "state": "listening"})
        await pg.wait_for_timeout(1300)
        await inject(pg, {"type": "state", "state": "thinking"})
        await pg.wait_for_timeout(900)
        await inject(pg, {"type": "state", "state": "speaking"})
        await inject(pg, {"type": "annotate", "annotations": [
            {"id": "h1", "kind": "highlight", "rect": DARK, "at": None, "to": None, "label": "Dark mode", "ttl_s": 12},
            {"id": "c1", "kind": "circle", "rect": APPEARANCE, "at": None, "to": None, "label": None, "ttl_s": 12}]})
        await inject(pg, {"type": "say", "agent": "Pyxi", "index": 0, "total": 1, "text": "Right here: hit Export when you're happy with the theme.", "audio": None, "duration_s": 3,
                          "annotations": [{"id": "p1", "kind": "point", "at": [568, 426], "rect": EXPORT, "to": None, "label": "Export", "ttl_s": 9}]})
        await pg.wait_for_timeout(1600)
        await glide(pg, (900, 560), 50)
        await pg.wait_for_timeout(2600)


async def notes(b) -> None:
    card = {"id": "notes_site", "title": "What this page does", "style": "card", "picture": None,
            "region": [325, 334, 335, 108], "place": [760, 250], "closing": None, "sketches": [
                {"kind": "label", "points": [[640, 470]], "text": "the good one", "after": 3},
                {"kind": "arrow", "points": [[690, 462], [612, 428]], "text": None, "after": 3}],
            "items": [
                {"n": 1, "text": "Dark mode flips the whole app to night", "kind": "point", "mark": "frame", "rect": DARK},
                {"n": 2, "text": "Reduce motion calms the animations", "kind": "point", "mark": "frame", "rect": MOTION},
                {"n": 3, "text": "Export saves it all to one file", "kind": "point", "mark": "circle", "rect": EXPORT},
                {"n": 4, "text": "Four menus for one toggle. Classic.", "kind": "point", "mark": "none", "rect": None}]}
    async with Recorder(b, "notes") as pg:
        await glide(pg, (1010, 470), 10)
        await inject(pg, {"type": "state", "state": "speaking"})
        await inject(pg, {"type": "notes", "card": card})
        await pg.wait_for_timeout(900)
        for n in (1, 2, 3, 4):
            await pg.evaluate("(n) => window.__pyxi.inject({type: 'say', agent: 'Pyxi', index: n, total: 4, text: '', audio: null, duration_s: 0.1, notes: [n], annotations: []})", n)
            await pg.wait_for_timeout(1300)
        await pg.wait_for_timeout(2200)


async def explain(b) -> None:
    async with Recorder(b, "explain") as pg:
        await glide(pg, (1010, 470), 10)
        await show(pg, "https")
        await pg.wait_for_timeout(2600)
        for step in (1, 2, 3, 4):
            await pg.locator(".progress button").nth(step).click()
            await pg.mouse.move(1010, 470)
            await pg.wait_for_timeout(2300)


async def chip(b) -> None:
    async with Recorder(b, "chip") as pg:
        await glide(pg, (1010, 470), 10)
        model = ai_chip().model_dump(mode="json")
        await inject(pg, {"type": "object", "scene": model, "audio_key": "chip:site", "restart": True, "place": None})
        await pg.wait_for_timeout(12500)


async def layers(b) -> None:
    """The three layers of the "how it works" section: the screen alone, the controls Pyxi reads
    (their exact boxes, measured in the page), and Pyxi's drawings alone on a transparent page."""
    import json

    ctx = await b.new_context(viewport={"width": 1440, "height": 900})
    pg = await ctx.new_page()
    await pg.goto(f"{URL}/overlay.html?backdrop=1&live=1")
    await pg.wait_for_timeout(1500)
    await pg.mouse.move(1430, 890)
    await pg.add_style_tag(content=".presence, .tether, .notch { display: none !important; }")
    await pg.wait_for_timeout(400)
    await pg.screenshot(path=str(TMP / "screen.png"))
    Image.open(TMP / "screen.png").convert("RGB").resize((1280, 800), Image.LANCZOS).save(OUT / "layer-screen.webp", "WEBP", quality=88)
    names = ["Settings - App", "General", "Appearance", "Privacy", "Advanced", "Dark mode", "Reduce motion", "Export"]
    boxes = await pg.evaluate("""(names) => names.map((n) => {
        const el = [...document.querySelectorAll('body *')].reverse().find((e) => e.children.length < 3 && e.textContent.trim() === n);
        if (!el) return null;
        const t = el.closest('button, label, li, a, [role]') || el;
        const r = t.getBoundingClientRect();
        return {name: n, x: r.left, y: r.top, w: r.width, h: r.height};
    })""", names)
    (OUT / "layer-marks.json").write_text(json.dumps([x for x in boxes if x], indent=1), encoding="utf-8")
    await ctx.close()
    # Pyxi's own drawings, nothing else: the overlay with no backdrop, on a transparent page.
    ctx = await b.new_context(viewport={"width": 1440, "height": 900})
    pg = await ctx.new_page()
    await pg.goto(f"{URL}/overlay.html?live=1")
    await pg.wait_for_timeout(1500)
    await pg.mouse.move(1010, 480)
    await inject(pg, {"type": "state", "state": "speaking"})
    await inject(pg, {"type": "annotate", "annotations": [
        {"id": "h1", "kind": "highlight", "rect": DARK, "at": None, "to": None, "label": "Dark mode", "ttl_s": 30},
        {"id": "c1", "kind": "circle", "rect": APPEARANCE, "at": None, "to": None, "label": None, "ttl_s": 30}]})
    await inject(pg, {"type": "say", "agent": "Pyxi", "index": 0, "total": 1, "text": "Right here: hit Export when you're happy with the theme.", "audio": None, "duration_s": 3,
                      "annotations": [{"id": "p1", "kind": "point", "at": [568, 426], "rect": EXPORT, "to": None, "label": "Export", "ttl_s": 30}]})
    await pg.wait_for_timeout(1800)
    await pg.screenshot(path=str(OUT / "layer-pyxi.png"), omit_background=True)
    await ctx.close()
    print("layers: screen, marks", len([x for x in boxes if x]), "boxes, pyxi drawings")


CLIPS = {"point": point, "notes": notes, "explain": explain, "chip": chip, "layers": layers}


async def main(names: list[str]) -> None:
    async with async_playwright() as p:
        b = await p.chromium.launch(args=GL)
        for name in names or list(CLIPS):
            await CLIPS[name](b)
        await b.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
