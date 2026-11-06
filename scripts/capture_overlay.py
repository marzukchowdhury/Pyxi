"""Full-resolution captures of the overlay in the browser preview (headless Chromium)."""

import asyncio
import json
import sys

from playwright.async_api import async_playwright


async def main(kind: str, arg: str, steps: list[int], out: str):
    async with async_playwright() as p:
        b = await p.chromium.launch(
            args=[
                "--use-angle=swiftshader",
                "--enable-unsafe-swiftshader",
                "--autoplay-policy=no-user-gesture-required",
            ]
        )
        pg = await b.new_page(viewport={"width": 1440, "height": 900})
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        await pg.goto("http://localhost:5199/overlay.html?backdrop=1")
        await pg.wait_for_timeout(1200)
        await pg.mouse.move(1010, 470)
        if kind == "scene":
            await pg.evaluate(
                """async (name) => { const ws = new WebSocket('ws://127.0.0.1:8766/ws'); await new Promise(r => ws.onopen = r); ws.send(JSON.stringify({type: 'show_scene', name})); }""",
                arg,
            )
            await pg.wait_for_timeout(2200)
            play = pg.locator(".step-row .icon.primary")
            if await play.count() and await play.get_attribute("aria-label") == "Pause":
                await play.click()
            for st in steps:
                await pg.locator(".progress button").nth(st).click()
                await pg.mouse.move(1010, 470)
                await pg.wait_for_timeout(1900)
                await pg.screenshot(path=f"{out}_{st}.png")
        else:  # inject events (json list) and shoot
            for ev in json.loads(arg):
                await pg.evaluate("(e) => window.__pyxi.inject(e)", ev)
                await pg.wait_for_timeout(150)
            await pg.mouse.move(1010, 470)
            await pg.wait_for_timeout(steps[0] if steps else 900)
            await pg.screenshot(path=f"{out}.png")
        if errors:
            print("page errors:", errors[:5])
        await b.close()


kind, arg = sys.argv[1], sys.argv[2]
steps = [int(x) for x in sys.argv[3].split(",")] if len(sys.argv) > 3 and sys.argv[3] else []
asyncio.run(
    main(
        kind,
        arg,
        steps,
        sys.argv[4]
        if len(sys.argv) > 4
        else ".pyxi/shots/" + (arg if kind == "scene" else "events"),
    )
)
