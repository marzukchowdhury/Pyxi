"""Check the built home page in headless Chromium, at a desktop and a phone size.

    npm run build
    ..\\.venv\\Scripts\\python scripts/home_check.py

Serves dist/ itself, loads the home page, and reports for each size: what was fetched before any
video played and how heavy it was (as stored, and as it would travel gzipped), whether any video
file was fetched before it was needed, how far the layout shifted (CLS), console errors, and
whether the clips start on scroll and the film plays from its button. Full-page screenshots go to
.shots/home/. Exits 1 if a check fails.
"""

from __future__ import annotations

import asyncio
import functools
import gzip
import http.server
import sys
import threading
from pathlib import Path

SITE = Path(__file__).resolve().parents[1]
DIST = SITE / "dist"
OUT = SITE / ".shots" / "home"
BUDGET = 1_500_000  # first load, before any video plays
SIZES = {"desktop": (1440, 900), "phone": (375, 812)}
TEXT = (".html", ".js", ".css", ".json", ".svg", ".txt", ".xml", ".webmanifest")


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def serve() -> tuple[http.server.ThreadingHTTPServer, str]:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(DIST)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


async def check(browser, base: str, name: str, size: tuple[int, int], reduced: bool = False) -> list[str]:
    bad: list[str] = []
    ctx = await browser.new_context(viewport={"width": size[0], "height": size[1]}, device_scale_factor=1, reduced_motion="reduce" if reduced else "no-preference")
    page = await ctx.new_page()
    errors: list[str] = []
    fetched: list[tuple[str, int, int]] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)

    async def on_response(resp):
        url = resp.url
        if not url.startswith(base):
            return  # the version check goes to GitHub; it is not part of the page's own weight
        try:
            body = await resp.body()
        except Exception:
            return
        path = url[len(base) :].split("?")[0]
        packed = len(gzip.compress(body, 6)) if path.endswith(TEXT) or path == "/" else len(body)
        fetched.append((path, len(body), packed))

    page.on("response", on_response)
    await page.add_init_script(
        "window.__cls = 0; window.__late = 0; window.__worst = [0, '']; new PerformanceObserver(l => { for (const e of l.getEntries()) if (!e.hadRecentInput) { window.__cls += e.value; if (e.startTime > 1000) window.__late += e.value; if (e.value > window.__worst[0]) window.__worst = [e.value, e.sources.map(s => s.node && s.node.nodeType === 1 ? s.node.tagName.toLowerCase() + '.' + s.node.className : 'text').join(', ') + ' at ' + Math.round(e.startTime) + ' ms']; } }).observe({ type: 'layout-shift', buffered: true });"
    )
    await page.goto(base + "/", wait_until="networkidle")
    await page.wait_for_timeout(1500)
    raw = sum(f[1] for f in fetched)
    packed = sum(f[2] for f in fetched)
    videos = [f[0] for f in fetched if f[0].endswith((".mp4", ".webm"))]
    first_videos = [v for v in videos if "pyxi-film-preview" not in v]
    still = sum(f[2] for f in fetched if not f[0].endswith((".mp4", ".webm")))
    print(f"\n== {name} {size[0]}x{size[1]}{' (reduced motion)' if reduced else ''}")
    print(f"first load: {len(fetched)} files, {raw / 1e6:.2f} MB as stored, {packed / 1e6:.2f} MB gzipped; without the hero preview that started playing: {still / 1e6:.2f} MB")
    for path, a, b in sorted(fetched, key=lambda f: -f[2])[:8]:
        print(f"   {b / 1e3:8.1f} kB  {path}")
    if still > BUDGET:
        bad.append(f"{name}: first load is {still} bytes, over {BUDGET}")
    if first_videos:
        bad.append(f"{name}: a clip was fetched before it was on screen: {first_videos}")
    if reduced and videos:
        bad.append(f"{name}: with reduced motion a video was fetched without being asked for: {videos}")

    if not reduced:
        # every clip starts when it is scrolled to, and stops when it is left
        n = await page.locator(".show video").count()
        for i in range(n):
            v = page.locator(".show video").nth(i)
            await v.scroll_into_view_if_needed()
            await page.wait_for_timeout(1400)
            if await v.evaluate("v => v.paused || v.readyState < 2"):
                bad.append(f"{name}: clip {i + 1} did not start on screen")
        await page.evaluate("scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(900)
        playing = await page.evaluate("[...document.querySelectorAll('.show video')].filter(v => !v.paused).length")
        if playing:
            bad.append(f"{name}: {playing} clips still playing off screen")
        await page.evaluate("scrollTo(0, 0)")
        await page.wait_for_timeout(600)
        # the film: the button swaps the silent preview for the full film with controls and sound
        await page.locator(".film-play").click()
        await page.wait_for_timeout(1500)
        film = await page.evaluate("(() => { const f = document.querySelector('.film-full'); return { hidden: f.hidden, paused: f.paused, muted: f.muted, controls: f.controls, t: f.currentTime }; })()")
        if film["hidden"] or film["paused"] or film["muted"] or not film["controls"] or film["t"] <= 0:
            bad.append(f"{name}: the film did not play with sound and controls: {film}")
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(300)
        if not await page.evaluate("document.querySelector('.film-full').hidden"):
            bad.append(f"{name}: Escape did not bring the preview back")
    else:
        toggles = await page.locator(".clip-toggle:visible").count()
        if toggles < 5:
            bad.append(f"{name}: with reduced motion only {toggles} play buttons are shown")

    cls = await page.evaluate("window.__cls")
    worst = await page.evaluate("window.__worst")
    print(f"largest single shift: {worst[0]:.4f} ({worst[1]})")
    wide = await page.evaluate("document.documentElement.scrollWidth - innerWidth")
    height = await page.evaluate("document.documentElement.scrollHeight")
    late = await page.evaluate("window.__late")
    # The web fonts swap in during the first second and nudge the text (every page of the site
    # does this). After that nothing may move: a video arriving must not push the page about.
    print(f"layout shift {cls:.4f} in all, {late:.4f} after the first second; page {height} px tall; sideways overflow {wide} px; console errors {len(errors)}")
    if cls > 0.1 or late > 0.02:
        bad.append(f"{name}: layout shift {cls:.3f} in all, {late:.3f} after the first second")
    if wide > 1:
        bad.append(f"{name}: the page is {wide} px wider than the screen")
    # The version check asks GitHub, which turns a test server away now and then; the page keeps
    # the version it was built with. That is not the page's error.
    errors = [e for e in errors if "api.github.com" not in e and "net::ERR_FAILED" not in e]
    if errors:
        bad.append(f"{name}: console errors: {errors[:3]}")
    if not reduced:
        OUT.mkdir(parents=True, exist_ok=True)
        await page.evaluate("document.querySelectorAll('.reveal').forEach(e => e.classList.add('in', 'is-in', 'on'))")
        await page.evaluate("scrollTo(0, 0)")
        await page.wait_for_timeout(500)
        await page.screenshot(path=str(OUT / f"home-{name}-top.png"))
        await page.screenshot(path=str(OUT / f"home-{name}.png"), full_page=True)
        print("screenshots:", OUT / f"home-{name}.png")
    await ctx.close()
    return bad


async def main() -> int:
    from playwright.async_api import async_playwright

    if not (DIST / "index.html").exists():
        print("build the site first: npm run build")
        return 1
    server, base = serve()
    bad: list[str] = []
    async with async_playwright() as p:
        # The film is H.264, which Playwright's own Chromium cannot play: use the installed Edge or
        # Chrome when there is one, the way a visitor's browser would.
        browser = None
        for channel in ("msedge", "chrome", None):
            try:
                browser = await p.chromium.launch(channel=channel, args=["--autoplay-policy=no-user-gesture-required"])
                print("browser:", channel or "Playwright's Chromium (no H.264: the film check will fail)")
                break
            except Exception:
                continue
        for name, size in SIZES.items():
            bad += await check(browser, base, name, size)
        bad += await check(browser, base, "desktop", SIZES["desktop"], reduced=True)
        await browser.close()
    server.shutdown()
    print("\n" + ("\n".join("FAIL " + b for b in bad) if bad else "all checks passed"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
