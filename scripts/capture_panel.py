import asyncio

from playwright.async_api import async_playwright

from pyxi.brainkey import client_key


async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": 440, "height": 760}, device_scale_factor=2)
        await pg.goto(f"http://localhost:5199/panel.html?key={client_key()}")
        await pg.wait_for_timeout(1500)
        await pg.screenshot(path=".pyxi/shots/panel_chat.png")
        for tab, name in (("Team", "team"), ("Memory", "memory"), ("3D Gallery", "gallery")):
            await pg.get_by_role("button", name=tab, exact=True).click()
            await pg.wait_for_timeout(500)
            await pg.screenshot(path=f".pyxi/shots/panel_{name}.png")
        await b.close()


asyncio.run(main())
