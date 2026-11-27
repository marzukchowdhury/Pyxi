"""Look at models built from photos in the REAL viewer: each one drawn by the overlay (three.js,
in headless Chromium on the GPU) from the photo's own angle, saved beside its photo.

usage: python scripts/photo_to_3d_look.py runs_dir pictures_dir [name ...]
       (browser preview running: cd desktop && npx vite --config vite.web.config.ts; or set
       PYXI_PREVIEW to its address)

For each runs_dir/<name>/model.json writes runs_dir/<name>/look.png: the photo on the left,
the overlay's own render on the right. Also runs_dir/<name>/viewer.png, the stage as the user
gets it (labels, photo thumbnail, bar), and viewer_compare.png after a click on the thumbnail.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import sys
import time
from pathlib import Path

from PIL import Image
from playwright.async_api import async_playwright

URL = os.environ.get("PYXI_PREVIEW", "http://localhost:5199") + "/overlay.html?live=1"
GL = ["--use-angle=d3d11", "--enable-gpu", "--ignore-gpu-blocklist"]
STAGE = (300, 120, 780, 620)


async def main() -> None:
    runs, pictures = Path(sys.argv[1]), Path(sys.argv[2])
    names = sys.argv[3:] or sorted(p.name for p in runs.iterdir() if (p / "model.json").exists())
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=GL)
        for name in names:
            model = json.loads((runs / name / "model.json").read_text(encoding="utf-8"))
            page = await browser.new_page(viewport={"width": 1400, "height": 860})
            await page.goto(URL)
            await page.wait_for_timeout(1200)
            # Paused on the first beat, no labels in the plain look: the object itself.
            bare = {**model, "auto_play": False, "parts": [{**q, "label": None} for q in model["parts"]]}
            event = {"type": "object", "scene": bare, "audio_key": f"{name}:{time.time()}", "restart": True, "place": list(STAGE)}
            await page.evaluate("(e) => window.__pyxi.inject(e)", event)
            await page.wait_for_timeout(4200)  # the camera swings in and settles
            canvas = page.locator(".object-view canvas").first
            box = await canvas.bounding_box()
            shot = await page.screenshot(clip=box, omit_background=False)
            render = Image.open(io.BytesIO(shot)).convert("RGB")
            photo_file = next((f for f in pictures.iterdir() if f.stem == name), None)
            side = 620
            sheet = Image.new("RGB", (side * 2 + 20, side), "white")
            if photo_file is not None:
                photo = Image.open(photo_file).convert("RGBA")
                flat = Image.new("RGB", photo.size, (236, 238, 241))
                flat.paste(photo, mask=photo.getchannel("A"))
                flat.thumbnail((side, side))
                sheet.paste(flat, ((side - flat.width) // 2, (side - flat.height) // 2))
            render.thumbnail((side, side))
            sheet.paste(render, (side + 20 + (side - render.width) // 2, (side - render.height) // 2))
            sheet.save(runs / name / "look.png")
            # The stage as the user gets it, then the compare view.
            await page.evaluate("(e) => window.__pyxi.inject(e)", {**event, "scene": {**model, "auto_play": False}, "audio_key": f"{name}:v:{time.time()}"})
            await page.wait_for_timeout(3800)
            stage = await page.locator(".object-stage").first.bounding_box()
            clip = {"x": max(0, stage["x"] - 10), "y": max(0, stage["y"] - 10), "width": stage["width"] + 20, "height": stage["height"] + 20}
            await page.screenshot(path=str(runs / name / "viewer.png"), clip=clip)
            thumb = page.locator(".object-photo").first
            if await thumb.count():
                await thumb.dispatch_event("click")
                await page.wait_for_timeout(2600)
                await page.screenshot(path=str(runs / name / "viewer_compare.png"), clip=clip)
            await page.close()
            print("looked at", name, flush=True)
        await browser.close()


asyncio.run(main())
