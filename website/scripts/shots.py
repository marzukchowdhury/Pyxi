"""Render the social preview image, and optionally full-page screenshots of the running site.

Run from website/ with the repo's venv (it has Playwright):
  ..\\.venv\\Scripts\\python scripts\\shots.py og                     -> public/og.png
  ..\\.venv\\Scripts\\python scripts\\shots.py pages URL OUT_DIR      -> desktop and phone screenshots

Uses Playwright's Chromium, or the installed Edge or Chrome if that won't start.
"""

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
SITE = HERE.parent
# Scroll reveals wait for an IntersectionObserver, which a full-page capture never triggers.
SHOW_ALL = ".js .reveal { opacity: 1 !important; transform: none !important; transition: none !important; }"


def browser(p):
    for kw in ({}, {"channel": "msedge"}, {"channel": "chrome"}):
        try:
            return p.chromium.launch(**kw)
        except Exception:  # noqa: BLE001 - try the next one
            continue
    raise SystemExit("No Chromium, Edge or Chrome found")


def og() -> None:
    with sync_playwright() as p:
        b = browser(p)
        page = b.new_page(viewport={"width": 1200, "height": 630})
        page.goto((HERE / "og.html").as_uri())
        page.wait_for_load_state("networkidle")
        page.evaluate("document.fonts.ready")
        page.screenshot(path=str(SITE / "public" / "og.png"))
        b.close()
    print("wrote public/og.png")


def pages(base: str, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    sizes = {"desktop": {"width": 1440, "height": 900}, "phone": {"width": 390, "height": 844}}
    with sync_playwright() as p:
        b = browser(p)
        for label, vp in sizes.items():
            ctx = b.new_context(viewport=vp, device_scale_factor=1, is_mobile=label == "phone", has_touch=label == "phone")
            page = ctx.new_page()
            for name in ("", "download", "privacy"):
                page.goto(f"{base.rstrip('/')}/{name}?motion=1")
                page.wait_for_load_state("networkidle")
                page.wait_for_timeout(2600)  # the hero types its first request and points
                page.screenshot(path=str(out / f"{name or 'home'}-{label}-fold.png"))
                page.add_style_tag(content=SHOW_ALL)
                page.screenshot(path=str(out / f"{name or 'home'}-{label}-full.png"), full_page=True)
                print("shot", name or "home", label)
            ctx.close()
        b.close()


if __name__ == "__main__":
    if sys.argv[1:2] == ["og"]:
        og()
    elif sys.argv[1:2] == ["pages"]:
        pages(sys.argv[2], Path(sys.argv[3]))
    else:
        raise SystemExit(__doc__)
