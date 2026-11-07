"""How long from a 3D model reaching the overlay to its first frame on screen, in the browser
preview (cd desktop && npx vite --config vite.web.config.ts, port 5199). Uses the dev-only
``window.__pyxi3d`` marks ObjectView leaves when a model (and each new part count) first draws.

    cold     the first model after the overlay loads (code, three.js, shaders all fresh)
    warm     another model on the same page
    assemble a model arriving part by part, as the streaming builder sends it: the time each
             new part takes to appear, and the frame rate while it assembles

usage: python scripts/first_frame_3d.py [model.json [screenshot.png]]
GPU: headless Chromium through ANGLE D3D11 (the real GPU), like the app.
"""

from __future__ import annotations

import asyncio
import json
import os
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, "src")
from playwright.async_api import Page, async_playwright

from pyxi.scenes.object_gallery import OBJECT_GALLERY

URL = os.environ.get("PYXI_PREVIEW", "http://localhost:5199") + "/overlay.html?backdrop=1&live=1"
GL = ["--use-angle=d3d11", "--enable-gpu", "--ignore-gpu-blocklist"]


async def inject_and_wait(pg: Page, scene: dict, parts: int | None = None) -> float:
    """Send the model; ms until its first frame with ``parts`` parts (default: all) drew."""
    n = parts or len(scene["parts"])
    ev = {"type": "object", "scene": scene, "audio_key": f"{scene['id']}:{time.time()}", "restart": True, "place": None}
    return await pg.evaluate(
        """async ([ev, key]) => {
            const t0 = performance.now();
            window.__pyxi.inject(ev);
            for (let i = 0; i < 600; i++) {
                await new Promise((r) => requestAnimationFrame(r));
                const t = (window.__pyxi3d || {})[key];
                if (t) return t - t0;
            }
            return -1;
        }""",
        [ev, f"{scene['id']}:{n}"],
    )


async def assemble(pg: Page, scene: dict) -> tuple[list[float], float]:
    """Send the model growing a part at a time, 300 ms apart; ms for each step to draw, and
    the frame rate meanwhile."""
    await pg.evaluate("() => { window.__frames = 0; const f = () => { window.__frames++; requestAnimationFrame(f); }; requestAnimationFrame(f); }")
    took: list[float] = []
    start = time.monotonic()
    f0 = await pg.evaluate("() => window.__frames")
    ids: set[str] = set()
    for k in range(1, len(scene["parts"]) + 1):
        parts = [p for p in scene["parts"][:k] if not p.get("parent") or p["parent"] in ids]
        ids = {p["id"] for p in parts}
        partial = {**scene, "parts": parts, "motions": [], "beats": [{"title": "Building", "narration": "", "at": 0, "focus": [], "camera": "iso", "cutaway": None}]}
        ms = await inject_and_wait(pg, partial, len(parts))
        took.append(ms)
        await pg.wait_for_timeout(max(0, 300 - int(ms)))
    fps = (await pg.evaluate("() => window.__frames") - f0) / (time.monotonic() - start)
    return took, fps


async def main() -> None:
    model = json.loads(Path(sys.argv[1]).read_text()) if len(sys.argv) > 1 else OBJECT_GALLERY["engine"]().model_dump(mode="json")
    async with async_playwright() as p:
        b = await p.chromium.launch(args=GL)
        pg = await b.new_page(viewport={"width": 1440, "height": 900})
        await pg.goto(URL)
        await pg.wait_for_timeout(1500)
        cold = await inject_and_wait(pg, {**model, "id": "obj_cold"})
        await pg.wait_for_timeout(1500)
        warm = await inject_and_wait(pg, {**model, "id": "obj_warm"})
        await pg.wait_for_timeout(800)
        steps, fps = await assemble(pg, {**model, "id": "obj_assemble"})
        print(f"parts={len(model['parts'])}  cold first frame {cold:.0f} ms  warm {warm:.0f} ms")
        print(f"assembling: a new part draws in {statistics.median(steps):.0f} ms median, {max(steps):.0f} ms worst; {fps:.0f} fps meanwhile")
        if len(sys.argv) > 2:
            final = {**model, "id": "obj_final"}
            await inject_and_wait(pg, final)
            await pg.wait_for_timeout(3500)
            await pg.screenshot(path=sys.argv[2])
        await b.close()


if __name__ == "__main__":
    asyncio.run(main())
