"""Does a 3D model or scene left open stop drawing? Counts the canvas's frames per second
(the dev-only ``window.__pyxiFrames`` that scene/pace.ts keeps) in the browser preview while
it plays, after it's paused, and after a drag.

usage: python scripts/frames_3d.py   (preview on 5199, or set PYXI_PREVIEW)
"""

from __future__ import annotations

import asyncio
import os
import sys
import time

sys.path.insert(0, "src")
from playwright.async_api import Page, async_playwright

from pyxi.scenes import get_scene
from pyxi.scenes.object_gallery import OBJECT_GALLERY

URL = os.environ.get("PYXI_PREVIEW", "http://localhost:5199") + "/overlay.html?backdrop=1&live=1"
GL = ["--use-angle=d3d11", "--enable-gpu", "--ignore-gpu-blocklist"]


async def fps(pg: Page, seconds: float = 2.0) -> float:
    a = await pg.evaluate("() => window.__pyxiFrames || 0")
    await pg.wait_for_timeout(int(seconds * 1000))
    b = await pg.evaluate("() => window.__pyxiFrames || 0")
    return (b - a) / seconds


async def run(pg: Page, kind: str) -> dict[str, float]:
    if kind == "model":
        ev = {"type": "object", "scene": OBJECT_GALLERY["engine"]().model_dump(mode="json"), "audio_key": f"e:{time.time()}", "restart": True, "place": None}
        pause = pg.locator(".object-panel button[aria-label='Pause'], button[aria-label='Pause']").first
    else:
        ev = {"type": "scene", "scene": get_scene("https").model_dump(by_alias=True, mode="json")}
        pause = pg.locator(".step-row .icon.primary[aria-label='Pause']").first
    await pg.evaluate("(e) => window.__pyxi.inject(e)", ev)
    await pg.wait_for_timeout(1500)
    out = {"playing": await fps(pg)}
    await pause.dispatch_event("click")
    await pg.wait_for_timeout(4000)  # the settling time runs out
    out["paused"] = await fps(pg, 3.0)
    box = await pg.locator("canvas").first.bounding_box()
    cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    await pg.mouse.move(cx, cy)
    await pg.mouse.down()
    await pg.mouse.move(cx + 120, cy + 30, steps=12)
    await pg.mouse.up()
    out["during drag"] = await fps(pg, 0.5)
    await pg.wait_for_timeout(4000)
    out["after drag"] = await fps(pg, 3.0)
    if os.environ.get("SHOT"):
        await pg.screenshot(path=f"{os.environ['SHOT']}_{kind}_paused.png")
    play = pg.locator("button[aria-label='Play']").first
    await play.dispatch_event("click")
    await pg.wait_for_timeout(600)
    out["play again"] = await fps(pg)
    return out


async def main() -> None:
    async with async_playwright() as p:
        b = await p.chromium.launch(args=GL)
        for kind in ("model", "scene"):
            pg = await b.new_page(viewport={"width": 1440, "height": 900})
            await pg.goto(URL)
            await pg.wait_for_timeout(1200)
            r = await run(pg, kind)
            print(f"{kind:6s} " + "  ".join(f"{k} {v:.0f} fps" for k, v in r.items()), flush=True)
            await pg.close()
        await b.close()


if __name__ == "__main__":
    asyncio.run(main())
