"""Screenshot library models in the browser preview, to check them by eye and write part rules.

    python scripts/library/serve.py &                         (the models, on 8799)
    python scripts/library/look.py <out-dir> [ids...] [--dark] [--views]
--views: also front, side and top shots, each part tagged with its id.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from playwright.async_api import async_playwright

from pyxi.scenes import library
from pyxi.scenes.objects import Beat

PREVIEW = os.environ.get("PYXI_PREVIEW", "http://localhost:5199")
URL = f"{PREVIEW}/overlay.html?backdrop=1&live=1&backend=http://127.0.0.1:8799"
GL = ["--use-angle=d3d11", "--enable-gpu", "--ignore-gpu-blocklist"]
DARK = (".with-backdrop{background:linear-gradient(180deg,#14171d,#0c0e12)!important}"
        ".fake-window{background:#1b1f27!important;color:#d9dee8}.fake-bar{background:#222732!important}")


async def shot(b, model_id: str, out: Path, dark: bool, camera: str, tag_ids: bool) -> None:
    m = library.get(model_id)
    scene = m.scene()
    if tag_ids:
        for p in scene.parts:
            p.label = p.id
    scene.beats = [Beat(title=m.title, narration="", camera=camera)]
    scene.auto_play = False  # nothing to play: paused, or the panel tidies itself away
    pg = await b.new_page(viewport={"width": 1440, "height": 900})
    errors: list[str] = []
    pg.on("pageerror", lambda e: errors.append(str(e)[:200]))
    pg.on("console", lambda m: m.type == "error" and "WebSocket" not in m.text and errors.append(m.text[:300]))
    await pg.goto(URL)
    await pg.wait_for_timeout(1200)
    if dark:
        await pg.add_style_tag(content=DARK)
    ev = {"type": "object", "scene": scene.model_dump(mode="json"), "audio_key": f"{model_id}:{time.time()}", "restart": True, "place": None}
    await pg.evaluate("(e) => window.__pyxi.inject(e)", ev)
    await pg.mouse.move(1300, 850)
    await pg.wait_for_timeout(5000)
    await pg.screenshot(path=out)
    if errors:
        print(f"  {model_id}: {errors[:2]}")
    await pg.close()


async def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    out = Path(args[0])
    out.mkdir(parents=True, exist_ok=True)
    ids = args[1:] or list(library.models())
    dark, views = "--dark" in sys.argv, "--views" in sys.argv
    async with async_playwright() as p:
        b = await p.chromium.launch(args=GL)
        for mid in ids:
            await shot(b, mid, out / f"{mid}.png", dark, "iso", False)
            if views:
                for cam in ("front", "side", "top"):
                    await shot(b, mid, out / f"{mid}_{cam}.png", dark, cam, True)
            print(mid, flush=True)
        await b.close()


if __name__ == "__main__":
    asyncio.run(main())
