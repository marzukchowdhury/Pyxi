"""Rebuild every screenshot in docs/images from scratch, with no personal data in any of them.

Everything is staged: the overlay runs in the browser preview over its mock "Settings" window,
events are injected the way the brain sends them, and 3D scenes come from a demo brain that has
its own empty data folder. Nothing reads the real desktop.

    1. cd desktop && npx vite --config vite.web.config.ts          (the preview, port 5199)
    2. PYXI_DATA_DIR=<empty folder> PYXI_MODELS_DIR=.pyxi/models pyxi serve --demo   (port 8766)
       (and the same PYXI_DATA_DIR for this script, so the walkthrough's pictures can be served)
    3. python scripts/capture_readme.py [only-these-names]

The ghost walkthrough needs a real Scout run on Otto's local demo site, so it runs one first
(headless, a few seconds, no model calls).
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image
from playwright.async_api import Page, async_playwright

OUT = Path("docs/images")
TMP = Path(".pyxi/shots")
URL = "http://localhost:5199"
GL = ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"]
ONLY = set(sys.argv[1:])

# Rects of the mock Settings window's controls (overlay.html?backdrop=1, 1440x900).
DARK, MOTION, EXPORT, APPEARANCE = [533, 338, 104, 22], [533, 373, 124, 22], [530, 409, 76, 33], [325, 334, 160, 28]


def wanted(name: str) -> bool:
    return not ONLY or name in ONLY


async def overlay(b, events: list[dict] | None = None, mouse=(1010, 470)) -> Page:
    pg = await b.new_page(viewport={"width": 1440, "height": 900})
    await pg.goto(f"{URL}/overlay.html?backdrop=1&live=1")
    await pg.wait_for_timeout(1300)
    await pg.mouse.move(*mouse)
    for ev in events or []:
        await pg.evaluate("(e) => window.__pyxi.inject(e)", ev)
        await pg.wait_for_timeout(150)
    return pg


async def show(pg: Page, name: str) -> None:
    """Open a gallery scene or 3D model as the brain sends it (no placement: the preview's one
    screen, rather than whichever real monitor the mouse is on)."""
    from pyxi.scenes import get_scene
    from pyxi.scenes.object_gallery import OBJECT_GALLERY

    if name in OBJECT_GALLERY:
        model = OBJECT_GALLERY[name]().model_dump(mode="json")
        ev = {"type": "object", "scene": model, "audio_key": f"{name}:readme", "restart": True, "place": None}
    else:
        ev = {"type": "scene", "scene": get_scene(name).model_dump(by_alias=True, mode="json")}
    await pg.evaluate("(e) => window.__pyxi.inject(e)", ev)


async def scene_step(pg: Page, name: str, step: int) -> None:
    await show(pg, name)
    await pg.wait_for_timeout(2400)
    play = pg.locator(".step-row .icon.primary")
    if await play.count() and await play.get_attribute("aria-label") == "Pause":
        await play.click()
    await pg.locator(".progress button").nth(step).click()
    await pg.mouse.move(1010, 470)
    await pg.wait_for_timeout(2200)


def crop(src: Path, box: tuple[int, int, int, int], dest: str) -> None:
    Image.open(src).crop(box).save(OUT / dest)


def exploded_scene() -> dict:
    return json.loads(subprocess.check_output([sys.executable, "scripts/sample_exploded_scene.py"]))


def walkthrough_json() -> dict:
    """A real Scout run on Otto's local demo site, turned into a ghost walkthrough."""
    code = r"""
import asyncio, json, sys, tempfile
sys.path.insert(0, "tests")
from conftest import FakeScreen
from otto.demo_site import DemoSite
from otto.demo_site.scripted_demo import demo_provider
from pyxi.companion.brain import Brain
from pyxi.config import PyxiSettings
from pyxi.demo import DemoLLM
from pyxi.hub import Hub
from pyxi.voice import make_tts
from pyxi.walkthrough import build_walkthrough

async def main():
    # The demo brain's own folder, so it can serve the run's screenshots to the walkthrough.
    import os
    s = PyxiSettings(_env_file=None, data_dir=os.environ.get("PYXI_DATA_DIR") or tempfile.mkdtemp(prefix="pyxi_walk_"), voice={"tts": "client"})
    with DemoSite() as site:
        brain = Brain(s, Hub(), llm=DemoLLM(), otto_llm=demo_provider(), screen=FakeScreen(), tts=make_tts(s.voice, s.models_dir))
        brain.jobs.headed = False
        job = await brain.jobs.start("scout", "Fill the partner application on the demo site", start_url=site.url)
        job = await brain.jobs.wait(job.id)
    print(build_walkthrough(brain.otto_store, job.run_id).model_dump_json())
asyncio.run(main())
"""
    out = subprocess.check_output([sys.executable, "-c", code], text=True)
    return json.loads(out.strip().splitlines()[-1])


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    TMP.mkdir(parents=True, exist_ok=True)
    now = time.time()
    async with async_playwright() as p:
        b = await p.chromium.launch(args=GL)

        if wanted("targeting"):
            pg = await overlay(b, [
                {"type": "state", "state": "speaking"},
                {"type": "job", "job": {"id": "j1", "agent_id": "scout", "agent_name": "Scout", "role": "browser", "task": "Fill the partner form", "status": "running", "summary": "", "data": None, "outputs": [], "run_id": None, "cost_usd": 0.01, "steps": 4, "parent": None, "created_at": now}},
                {"type": "job_event", "job_id": "j1", "agent": "Scout", "event": "status", "data": {"message": "Filling in the work email on step one."}},
                {"type": "annotate", "annotations": [
                    {"id": "h1", "kind": "highlight", "rect": DARK, "at": None, "to": None, "label": "Dark mode", "ttl_s": 12},
                    {"id": "c1", "kind": "circle", "rect": APPEARANCE, "at": None, "to": None, "label": None, "ttl_s": 12}]},
                {"type": "say", "agent": "Pyxi", "index": 0, "total": 1, "text": "Right here: hit Export when you're happy with the theme.", "audio": None, "duration_s": 2,
                 "annotations": [{"id": "p1", "kind": "point", "at": [568, 426], "rect": EXPORT, "to": None, "label": "Export", "ttl_s": 9}]},
            ])
            await pg.wait_for_timeout(700)
            await pg.screenshot(path=OUT / "targeting.png")
            await pg.close()

        if wanted("field-notes"):
            card = {"id": "notes_readme", "title": "What this page does", "style": "card", "picture": None,
                    "region": [325, 334, 335, 108], "place": [760, 250], "closing": None, "sketches": [
                        {"kind": "label", "points": [[640, 470]], "text": "the good one", "after": 3},
                        {"kind": "arrow", "points": [[690, 462], [612, 428]], "text": None, "after": 3}],
                    "items": [
                        {"n": 1, "text": "Dark mode flips the whole app to night", "kind": "point", "mark": "frame", "rect": DARK},
                        {"n": 2, "text": "Reduce motion calms the animations", "kind": "point", "mark": "frame", "rect": MOTION},
                        {"n": 3, "text": "Export saves it all to one file", "kind": "point", "mark": "circle", "rect": EXPORT},
                        {"n": 4, "text": "Four menus for one toggle. Classic.", "kind": "point", "mark": "none", "rect": None}]}
            pg = await overlay(b, [{"type": "state", "state": "speaking"}, {"type": "notes", "card": card}])
            for n in (1, 2, 3, 4):
                await pg.evaluate("(n) => window.__pyxi.inject({type: 'say', agent: 'Pyxi', index: n, total: 4, text: '', audio: null, duration_s: 0.1, notes: [n], annotations: []})", n)
                await pg.wait_for_timeout(450)
            await pg.wait_for_timeout(1500)
            await pg.screenshot(path=TMP / "field_notes.png")
            crop(TMP / "field_notes.png", (290, 200, 1290, 660), "field-notes.png")
            await pg.close()

        if wanted("explanation"):
            pg = await overlay(b)
            await scene_step(pg, "https", 4)
            await pg.screenshot(path=OUT / "explanation.png")
            await pg.close()

        if wanted("neural-net"):
            pg = await overlay(b)
            await scene_step(pg, "neural_net", 1)
            await pg.screenshot(path=OUT / "neural-net.png")
            await pg.close()

        if wanted("exploded"):
            pg = await overlay(b, [{"type": "scene", "scene": exploded_scene()}])
            await pg.wait_for_timeout(700)
            await pg.locator(".progress button").nth(1).click()
            await pg.wait_for_timeout(2200)
            await pg.screenshot(path=OUT / "exploded.png")
            await pg.close()

        if wanted("memory-palace"):
            scene = exploded_scene() | {"id": "palace_demo", "title": "Settings: Appearance", "subtitle": "Today at 14:32"}
            moments = [("Figma", "Pricing page"), ("Chrome", "Stripe docs"), ("Figma", "Pricing page v2"), ("Slack", "#design"),
                       ("Settings", "Appearance"), ("Chrome", "Linear"), ("Figma", "Pricing page v3"), ("Settings", "Privacy"),
                       ("Code", "pricing.tsx"), ("Chrome", "localhost:3000"), ("Figma", "Pricing page v4"), ("Settings", "Appearance")]
            timeline = [{"id": i, "ts": now - (12 - i) * 140, "app": a, "title": t} for i, (a, t) in enumerate(moments, start=1)]
            pg = await overlay(b, [{"type": "palace", "snapshot_id": 5, "timeline": timeline, "scene": scene}])
            await pg.wait_for_timeout(800)
            await pg.locator(".holo .progress button").nth(1).click()
            await pg.wait_for_timeout(2200)
            await pg.screenshot(path=OUT / "memory-palace.png")
            await pg.close()

        if wanted("object-model"):
            pg = await overlay(b)
            await show(pg, "ejection_seat")
            await pg.wait_for_timeout(2500)
            await pg.get_by_role("button", name="Beat 3").click()
            await pg.mouse.move(1010, 470)
            await pg.wait_for_timeout(3500)
            await pg.screenshot(path=TMP / "object.png")
            crop(TMP / "object.png", (130, 30, 1090, 770), "object-model.png")
            await pg.close()

        if wanted("walkthrough"):
            walk = walkthrough_json()
            pg = await overlay(b, [{"type": "walkthrough", "walkthrough": walk}])
            step = next(i for i, s in enumerate(walk["steps"]) if s["target_name"] == "Work email")
            await pg.locator(".walk .progress button").nth(step).click()
            await pg.wait_for_timeout(1700)
            await pg.screenshot(path=OUT / "walkthrough.png")
            await pg.close()

        if wanted("notch"):
            pg = await overlay(b, mouse=(720, 400))
            for y in (300, 150, 60, 20, 6):  # hover up into the notch: it opens into a small chat
                await pg.mouse.move(720, y)
                await pg.wait_for_timeout(120)
            await pg.wait_for_timeout(1400)
            await pg.screenshot(path=TMP / "notch.png")
            crop(TMP / "notch.png", (480, 0, 960, 230), "notch.png")
            await pg.close()

        # The panel, at the size of its own window, against the demo brain (demo roster, no data).
        if any(wanted(n) for n in ("panel-chat", "panel-team", "panel-memory", "voice-studio")):
            pg = await b.new_page(viewport={"width": 440, "height": 760}, device_scale_factor=2)
            await pg.goto(f"{URL}/panel.html?live=1")
            await pg.wait_for_timeout(1800)
            if wanted("panel-chat"):
                await pg.screenshot(path=OUT / "panel-chat.png")
            if wanted("panel-team"):
                await pg.get_by_role("button", name="Team", exact=True).click()
                await pg.evaluate("(now) => window.__pyxi.inject({type: 'job', job: {id: 'j1', agent_id: 'scout', agent_name: 'Scout', role: 'browser', task: 'Compare the pricing pages of Linear and Notion', status: 'running', summary: '', data: null, outputs: [], run_id: null, cost_usd: 0.012, steps: 4, parent: null, created_at: now}})", now)
                await pg.wait_for_timeout(600)
                await pg.screenshot(path=OUT / "panel-team.png")
            if wanted("panel-memory"):
                await pg.get_by_role("button", name="Memory", exact=True).click()
                await pg.wait_for_timeout(600)
                await pg.screenshot(path=OUT / "panel-memory.png")
            if wanted("voice-studio"):
                await pg.get_by_role("button", name="Voice", exact=True).click()
                await pg.wait_for_timeout(800)
                await pg.screenshot(path=OUT / "voice-studio.png")
            await pg.close()
        await b.close()
    print("saved:", sorted(f.name for f in OUT.glob("*.png")))


asyncio.run(main())
