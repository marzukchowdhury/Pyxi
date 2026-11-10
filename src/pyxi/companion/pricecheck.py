"""Cheaper elsewhere: the brain's side of :mod:`pyxi.shopping`.

"Is this cheaper anywhere?" on a product page: Pyxi says one short line, Scout works out the
exact product (from the store page's own data, then what's on screen), looks it up at other
stores in the background (web search, then each store's page, read without a window and
without the mouse), and a small tag lands next to the price: "$24 less at Best Buy", with the
link, the shipping and when it was checked. When it can't prove a match, the tag says so.

With "Check prices while I shop" on in Settings (off by default), the same check runs by itself
when a store's product page comes up, and the tag only appears when there's a real saving.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
from otto.llm.base import ToolSpec

from pyxi import shopping, web
from pyxi.shopping import Offer, PriceCheck

if TYPE_CHECKING:
    from pyxi.companion.brain import Brain

log = logging.getLogger(__name__)

TOOL = ToolSpec(
    "check_prices",
    "Find out if the product on screen is cheaper at another store. Scout works in the background "
    "(no window, no mouse): it reads the store page for the exact model and barcode, checks other "
    "stores, and pins a tag by the price ('$24 less at Best Buy', with the link and when it was "
    "checked). Only exact matches count. Use it for 'is this cheaper anywhere?', 'can I get this "
    "for less?', 'best price for this?'. Say a few words ('Checking other stores.') and nothing "
    "more, never a price: the tag gives the answer.",
    {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "The product's full name as the page shows it.",
            },
            "brand": {"type": "string"},
            "model": {
                "type": "string",
                "description": "Model or part number if it's on screen (e.g. WH-1000XM5, SM-S938U).",
            },
            "size": {
                "type": "string",
                "description": "The chosen size, capacity, colour or pack, e.g. '256GB, Black'.",
            },
            "price": {"type": "string", "description": "The price shown, e.g. '$399.99'."},
            "url": {
                "type": "string",
                "description": "The page's address if you can see it (Pyxi reads the address bar too).",
            },
        },
        "required": ["name"],
    },
)
TOOLS = {"check_prices"}
BROWSERS = {"chrome", "msedge", "firefox", "brave", "opera", "vivaldi", "arc"}
RECHECK_S = 6 * 3600  # the auto check looks at a page again after this long
SETTINGS_FILE = "shopping.json"


# ---------------------------------------------------------------------------------- settings


def load(data_dir: Path) -> dict[str, Any]:
    try:
        raw = json.loads((data_dir / SETTINGS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    return {"auto": bool(raw.get("auto", False))}


def save(data_dir: Path, prefs: dict[str, Any]) -> dict[str, Any]:
    clean = {"auto": bool(prefs.get("auto", False))}
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / SETTINGS_FILE).write_text(json.dumps(clean), encoding="utf-8")
    return clean


# ---------------------------------------------------------------------------------- the page


def browser_url(hwnd: int | None = None) -> str:
    """The address in the browser's address bar (Chrome, Edge, Brave, Firefox), or ''."""
    if __import__("sys").platform == "darwin":  # Accessibility, else AppleScript (macos/uia.py)
        from pyxi import windows
        from pyxi.screen import uia

        hwnd = hwnd or windows.foreground()
        return (uia.browser_url(hwnd) or "") if hwnd else ""
    try:
        import uiautomation as auto
    except ImportError:
        return ""
    try:
        with auto.UIAutomationInitializerInThread(debug=False):
            if not hwnd:
                from pyxi import windows

                hwnd = windows.foreground()
            if not hwnd:
                return ""
            top = auto.ControlFromHandle(hwnd)
            bar = top.EditControl(
                searchDepth=14, RegexName=r"(?i)address|search with|enter address|search or enter"
            )
            if not bar.Exists(0.4, 0.1):
                return ""
            value = str(bar.GetValuePattern().Value or "").strip()
    except Exception as exc:  # UIA can throw on a window that's closing
        log.debug("address bar read failed: %s", exc)
        return ""
    if not value or " " in value:
        return ""
    return value if re.match(r"https?://", value) else "https://" + value


class Reader:
    """Reads store pages politely: one page per store, no retries, a short timeout, and bot
    checks skipped rather than solved. A page that turns plain requests away is opened once by
    Scout's background browser (no window, the cookie banner declined), never logged into."""

    def __init__(
        self,
        settings: Any = None,
        *,
        use_browser: bool = True,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.transport = transport
        self.use_browser = use_browser and settings is not None
        self._client: httpx.AsyncClient | None = None
        self._browser: Any = None
        self._browser_lock = asyncio.Lock()
        self._last: dict[str, float] = {}

    async def __aenter__(self) -> Reader:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def _gap(self, host: str) -> None:
        wait = self._last.get(host, 0) + 1.5 - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        self._last[host] = time.monotonic()

    async def fetch(self, url: str) -> str | None:
        host = shopping.domain(url)
        await self._gap(host)
        if self._client is None:
            self._client = httpx.AsyncClient(
                headers={**web.HEADERS, "Accept": "text/html,application/xhtml+xml"},
                timeout=web.TIMEOUT,
                follow_redirects=True,
                transport=self.transport,
            )
        try:
            r = await self._client.get(url)
            page = r.text[:3_000_000] if r.status_code == 200 else None
            if page is not None and _sign_in(str(r.url)):
                return None
        except httpx.HTTPError as exc:
            log.info("price page %s: %s", host, type(exc).__name__)
            page = None
        if page and not shopping.looks_blocked(page) and shopping.parse_product_page(page, url):
            return page
        if not self.use_browser:
            return page
        return await self._scout(url) or page

    async def _scout(self, url: str) -> str | None:
        async with self._browser_lock:  # one background tab at a time
            try:
                if self._browser is None:
                    from otto.browser.session import BrowserSession
                    from otto.core.guardrails import Guardrails

                    from pyxi.llm import otto_settings

                    s = otto_settings(self.settings, headless=True)
                    self._browser = BrowserSession(s, Guardrails(s.safety))
                    await self._browser.start()
                await self._browser.navigate(url)
                await self._browser.dismiss_consent_banner()
                page = self._browser.page
                if _sign_in(page.url):
                    return None
                return await page.content()
            except Exception as exc:
                log.info("scout couldn't read %s: %s", shopping.domain(url), str(exc)[:120])
                return None

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None


def _sign_in(url: str) -> bool:
    return bool(re.search(r"/(?:ap/)?sign-?in|/login|/account/login|captcha", url, re.I))


def from_hint(args: dict[str, Any], url: str) -> Offer:
    """The product as the model read it off the screen (the fallback when the page won't say)."""
    name = str(args.get("name") or "").strip()
    size = str(args.get("size") or "").strip()
    title = f"{name} {size}".strip() if size and size.lower() not in name.lower() else name
    cur = shopping.site_currency(url) if url else "USD"
    price = shopping.parse_price(str(args.get("price") or ""), cur)
    return Offer(
        title=title,
        url=url,
        store=shopping.store_name(url) if url else str(args.get("store") or ""),
        price=price[0] if price else None,
        currency=price[1] if price else cur,
        brand=str(args.get("brand") or "").strip(),
        model=str(args.get("model") or "").strip(),
        source="screen",
    )


def merge(page: Offer | None, hint: Offer) -> Offer:
    """The page's own data wins (it's exact); the screen fills in what the page left out."""
    if page is None:
        return hint
    page.brand = page.brand or hint.brand
    page.model = page.model or hint.model
    if page.price is None:
        page.price, page.currency = hint.price, hint.currency
    if hint.title and not shopping.specs(page.title) and shopping.specs(hint.title):
        page.title = f"{page.title} {hint.title}"  # the chosen size lives in the screen's words
    return page


# ---------------------------------------------------------------------------------- the check


async def run_check(
    brain: Brain,
    hint: dict[str, Any],
    url: str,
    *,
    auto: bool = False,
    reader: Reader | None = None,
    search: Any = None,
) -> PriceCheck | None:
    hint_offer = from_hint(hint, url)
    label = hint_offer.title or (shopping.store_name(url) if url else "")
    job = await brain.jobs.track(
        "Scout", f"Price check: {(label or 'this item')[:60]}", role="shopping"
    )

    async def status(line: str) -> None:
        await brain.hub.emit(
            "job_event", job_id=job.id, agent="Scout", event="status", data={"message": line}
        )

    own_reader = reader is None
    reader = reader or Reader(brain.settings)
    try:
        product = hint_offer
        if url and shopping.is_store_page(url):
            await status("Reading this page")
            page = await reader.fetch(url)
            parsed = shopping.parse_product_page(page, url) if page else None
            product = merge(parsed, hint_offer)
        if not product.title:
            await brain.jobs.settle(job, ok=False, summary="I couldn't tell which product this is.")
            return None
        if auto and not (product.model or product.gtin or shopping.model_numbers(product.title)):
            # No model number or barcode: nothing could prove a match, so stay out of the way.
            await brain.jobs.settle(job, ok=False, summary="No model number to compare on.")
            return None
        await status("Looking for the same model elsewhere")
        check = await shopping.compare(
            product,
            search=search or (lambda q: web.search(q, 8)),
            fetch=reader.fetch,
            progress=status,
        )
    except Exception as exc:
        log.exception("price check failed")
        await brain.jobs.settle(
            job, ok=False, summary=f"The price check hit a snag: {str(exc)[:120]}"
        )
        return None
    finally:
        if own_reader:
            await reader.close()
    log.info(
        "price check %r: %s (%s)",
        product.title[:60],
        check.verdict,
        ", ".join(f"{c.store}={c.outcome}" for c in check.checked),
    )
    await brain.jobs.settle(
        job, ok=check.verdict != "unverified", summary=check.summary(), data=check.public()
    )
    brain.history = [
        *brain.history[-11:],
        (f"(you checked other stores' prices for {product.title[:120]})", f"({check.summary()})"),
    ]
    if not auto or check.verdict == "cheaper":
        ctx = getattr(brain, "_ctx", None)
        if auto or ctx is None:  # the page they're on now, not the one from the last question
            try:
                ctx = await brain.screen.capture(pixels=True)
            except Exception:
                ctx = None
        await show_tag(brain, check, hint, ctx)
        _state(brain)["tag_up"] = True
    return check


def _anchor(ctx: Any, check: PriceCheck, hint: dict[str, Any]) -> tuple[int, int] | None:
    """Beside the price on screen if OCR can find it, else the top right of the page."""
    if ctx is None:
        return None
    p = check.product
    quotes = [str(hint.get("price") or "").strip()]
    if p.price is not None:
        quotes += [shopping.money(p.price, p.currency), f"{p.price:,.2f}", f"{p.price:.2f}"]
    for q in [q for q in quotes if q]:
        try:
            rect = ctx.find_text(q)
        except Exception:
            rect = None
        if rect:
            x, y, w, h = rect
            return x + w + 14, y + h // 2
    wx, wy, ww, _wh = ctx.window.rect
    if ww > 0:
        return wx + ww - 380, wy + 150
    return None


async def show_tag(
    brain: Brain, check: PriceCheck, hint: dict[str, Any] | None = None, ctx: Any = None
) -> None:
    best = check.best
    tag = {
        "id": "price_" + secrets.token_hex(3),
        "verdict": check.verdict,
        "headline": check.headline(),
        "detail": check.detail(),
        "url": best.url if best else None,
        "store": best.store if best else None,
        "product": check.product.title[:120],
        "checked_at": check.checked_at,
        "at": _anchor(ctx, check, hint or {}),
    }
    await brain.hub.emit("price_tag", tag=tag)


# ---------------------------------------------------------------------------------- tool + auto


async def handle(brain: Brain, name: str, args: dict[str, Any]) -> str | None:
    if name != "check_prices":
        return None
    if not str(args.get("name") or "").strip():
        return "check_prices: say which product (its name as the page shows it)."
    url = str(args.get("url") or "").strip()
    if not url:
        ctx = getattr(brain, "_ctx", None)
        hwnd = ctx.window.hwnd if ctx and ctx.window.hwnd else None
        url = await asyncio.to_thread(browser_url, hwnd)
    if url and not re.match(r"https?://", url):
        url = "https://" + url
    if url:
        _state(brain)["seen"][url.split("#")[0]] = time.time()
    brain._spawn(run_check(brain, args, url))
    return (
        "Started: Scout is checking other stores in the background. Say a few words "
        "('Checking other stores.') and nothing more; the tag by the price gives the answer."
    )


def _state(brain: Brain) -> dict[str, Any]:
    st = getattr(brain, "_price_watch", None)
    if st is None:
        st = {"seen": {}, "busy": False, "title": ""}
        brain._price_watch = st  # type: ignore[attr-defined]
    return st


async def on_window(brain: Brain, app: str) -> None:
    """The foreground window or page changed. A tag belongs to its page, so it goes; and with
    "Check prices while I shop" on, a store's product page gets a quiet check."""
    st = _state(brain)
    if app.lower() in ("pyxi", "electron"):
        return  # clicking the tag itself
    if st.get("tag_up"):
        st["tag_up"] = False
        await brain.hub.emit("price_tag", tag=None)
    if not load(brain.settings.data_dir)["auto"] or app.lower() not in BROWSERS or st["busy"]:
        return
    url = await asyncio.to_thread(browser_url, None)
    if not url or not shopping.looks_like_product_url(url):
        return
    now = time.time()
    key = url.split("#")[0]
    if now - st["seen"].get(key, 0) < RECHECK_S:
        return
    st["seen"][key] = now
    st["busy"] = True
    try:
        await run_check(brain, {"name": ""}, key, auto=True)
    finally:
        st["busy"] = False
