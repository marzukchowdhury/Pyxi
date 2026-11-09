"""Answer first, then show.

"Find me budget earbuds", "what's a good standing desk?", "cheapest way to do X": Pyxi answers
in the same turn from a web search (a pick, its price, where the price came from), and puts
the answer on a card: the name, the figure, up to three facts, the sources by name, a picture
when a real one can be had, and one button that opens the shop's search results for the pick
by address. Nobody clicks through a shop for that; Scout is for work that needs a site worked
step by step.
"""

from __future__ import annotations

import asyncio
import base64
import io
import ipaddress
import logging
import re
import time
from typing import TYPE_CHECKING, Any
from urllib.parse import quote_plus, urljoin, urlparse

import httpx
from otto.llm.base import ToolSpec

from pyxi import web
from pyxi.companion.notes import AnswerCard, CardLink, build_answer, site_name
from pyxi.httpshared import shared

if TYPE_CHECKING:
    from pyxi.companion.brain import Brain

log = logging.getLogger(__name__)

TOOL = ToolSpec(
    "show_answer",
    "Put the answer you just found on a card beside your spoken line: for a pick or a lookup "
    "answered from web_search ('find me budget earbuds', 'what's a good standing desk?', 'which "
    "robot vacuum is best under $300?'). Call it in the same reply as the `say` that gives the "
    "pick, its price and who says so: the card never replaces saying it. Never before the search "
    "results are back, and only what the results said: never a price, a fact or a link from "
    "memory. Its one button opens the shop's search results for the pick by address (search "
    "results, not a verified product page: say so if they ask to see it). Not for news, facts "
    "or fact-checks: those are just said.",
    {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "The pick, by its full name: 'Anker Soundcore Space A40'.",
            },
            "figure": {
                "type": "string",
                "description": "Its price or the headline number, exactly as a result gave it: "
                "'$49.99', 'about $60', '4.6 stars'. Leave it out if no result gave one.",
            },
            "note": {
                "type": "string",
                "description": "One short line on where that figure is from and how sure you "
                "are: 'Price per RTINGS, checked this month; shops vary'.",
            },
            "facts": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Up to three facts that decide it, under 12 words each: "
                "'10 hours a charge, 50 with the case'.",
            },
            "sources": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "'RTINGS', 'PCMag'."},
                        "url": {"type": "string", "description": "Its link from the results."},
                    },
                    "required": ["url"],
                },
                "description": "The results you actually used, two or three at most.",
            },
            "shop": {
                "type": "string",
                "description": "Where they'd buy it, as a name or the shop's address: 'Amazon', "
                "'amazon.ca', 'Best Buy', 'IKEA'. The one they named, else the obvious one; "
                "'none' only for something no shop sells.",
            },
            "query": {
                "type": "string",
                "description": "What to search for at that shop: the pick's brand and model.",
            },
        },
        "required": ["title", "shop"],
    },
)
TOOLS = {"show_answer"}
CARD_KEEPS_S = 15 * 60  # how long "show me" still means the pick on the card


# ---------------------------------------------------------------------------------- shops

# A shop's own search page, by address. {q} is the query, {tld} the country's ending.
_SHOP_SEARCH: dict[str, tuple[str, str]] = {
    "amazon": ("Amazon", "https://www.amazon.{tld}/s?k={q}"),
    "bestbuy": ("Best Buy", "https://www.bestbuy.com/site/searchpage.jsp?st={q}"),
    "bestbuy.ca": ("Best Buy", "https://www.bestbuy.ca/en-ca/search?search={q}"),
    "walmart": ("Walmart", "https://www.walmart.{tld}/search?q={q}"),
    "target": ("Target", "https://www.target.com/s?searchTerm={q}"),
    "ebay": ("eBay", "https://www.ebay.{tld}/sch/i.html?_nkw={q}"),
    "ikea": ("IKEA", "https://www.ikea.com/{cc}/en/search/?q={q}"),
    "costco": ("Costco", "https://www.costco.{tld}/CatalogSearch?keyword={q}"),
    "newegg": ("Newegg", "https://www.newegg.{tld}/p/pl?d={q}"),
    "etsy": ("Etsy", "https://www.etsy.com/search?q={q}"),
    "homedepot": ("Home Depot", "https://www.homedepot.com/s/{q}"),
    "homedepot.ca": ("Home Depot", "https://www.homedepot.ca/search?q={q}"),
    "wayfair": ("Wayfair", "https://www.wayfair.{tld}/keyword.php?keyword={q}"),
    "canadiantire": ("Canadian Tire", "https://www.canadiantire.ca/en/search-results.html?q={q}"),
}
# Shops with a site per country, and the ending each country uses there.
_TLDS = {"us": "com", "ca": "ca", "uk": "co.uk", "au": "com.au"}
_ONLY_COM_CA = {"walmart", "costco", "newegg", "wayfair"}
_CANADA = ("Vancouver", "Toronto", "Edmonton", "Winnipeg", "Halifax", "St_Johns", "Regina",
           "Montreal", "Whitehorse", "Yellowknife", "Moncton", "Iqaluit")  # fmt: skip


def country() -> str:
    """'ca', 'uk', 'au' or 'us', from this PC's time zone: enough to pick amazon.ca over .com."""
    try:
        from pyxi.localzone import local_zone

        zone = local_zone() or ""
    except Exception:
        zone = ""
    if zone.startswith("America/") and zone.split("/")[-1] in _CANADA:
        return "ca"
    if zone == "Europe/London":
        return "uk"
    if zone.startswith("Australia/"):
        return "au"
    return "us"


def shop_search(shop: str, query: str, where: str | None = None) -> CardLink | None:
    """'Check on Amazon' and the address of that shop's search results for the query. A shop
    Pyxi has no search address for gets a web search limited to its site; no shop at all gets
    none (the card then has no button, rather than one that guesses)."""
    query = " ".join(str(query or "").split())[:120]
    shop = " ".join(str(shop or "").split()).lower()
    if not query or not shop:
        return None
    cc = where or country()
    host = urlparse(shop if "://" in shop else "https://" + shop).netloc.removeprefix("www.")
    host = host if "." in host else ""
    key = re.sub(r"[^a-z0-9]", "", (host.split(".")[0] if host else shop))
    if host.endswith(".ca"):
        cc = "ca"
    elif host.endswith(".co.uk"):
        cc = "uk"
    elif host.endswith(".com.au"):
        cc = "au"
    elif host.endswith(".com") and key != "ikea":  # ikea.com is every country's site
        cc = "us"
    known = _SHOP_SEARCH.get(f"{key}.{cc}") or _SHOP_SEARCH.get(key)
    if known is None:
        if not host:
            return None
        url = f"https://duckduckgo.com/?q={quote_plus(f'{query} site:{host}')}"
        return CardLink(name=f"Find it on {host}"[:40], url=url)
    name, pattern = known
    tld = _TLDS.get(cc, "com")
    if key in _ONLY_COM_CA and cc not in ("us", "ca"):
        tld = "com"
    path = {"ca": "ca", "uk": "gb", "au": "au"}.get(cc, "us")  # ikea.com/ca/en/...
    return CardLink(
        name=f"Check on {name}", url=pattern.format(q=quote_plus(query), tld=tld, cc=path)
    )


# ---------------------------------------------------------------------------------- the tool


async def handle(brain: Brain, name: str, args: dict[str, Any]) -> str | None:
    if name != "show_answer":
        return None
    button = shop_search(
        str(args.get("shop") or ""), str(args.get("query") or args.get("title") or "")
    )
    card = build_answer(args, button)
    if card is None:
        return "show_answer: give the pick's name as title."
    await show(brain, card)
    if not str(getattr(brain, "_round_say", "") or "").strip():
        brain._speak(spoken_pick(card))  # a card alone is not an answer: the pick is said too
    brain._spawn(add_picture(brain, card))
    return None


def spoken_pick(card: AnswerCard) -> str:
    """The card's pick as one spoken line, for when the model showed it without saying it."""
    line = f"My pick is the {card.title}" + (f", {card.figure}" if card.figure else "") + "."
    return f"{line} {card.note.rstrip('.')}." if card.note else line


async def show(brain: Brain, card: AnswerCard) -> None:
    brain._answer = (card, time.monotonic())  # type: ignore[attr-defined]
    await brain.hub.emit("answer", card=card.model_dump(mode="json"))


def current(brain: Brain) -> AnswerCard | None:
    """The answer card they can still be asking about ("show me", "open it")."""
    card, at = getattr(brain, "_answer", None) or (None, 0.0)
    if card is None or card.kind != "answer" or time.monotonic() - at > CARD_KEEPS_S:
        return None
    return card


def on_screen(brain: Brain) -> str:
    """What the model is told about the card it put up, so "show me" opens the right place."""
    card = current(brain)
    if card is None:
        return ""
    line = f"An answer card for your pick: {card.title}" + (
        f" ({card.figure})" if card.figure else ""
    )
    if card.button:
        line += (
            f". If they want to see it ('show me', 'open it', 'take me there'): quick_action "
            f"open_url {card.button.url} (the shop's search results for it, opened by address; "
            "say plainly they're search results, not a verified product page), then mark your "
            "pick and its price on the page."
        )
    return line + "."


def opened_note(brain: Brain, calls: list[Any]) -> str:
    """After the card's shop results were opened and looked at: what to do with the page."""
    card = current(brain)
    if card is None or card.button is None:
        return ""
    opened = [str(c.arguments.get("value") or "") for c in calls if c.name == "quick_action"]
    if card.button.url not in opened:
        return ""
    return (
        f"The shop's search results for your pick are open on their screen now (you're looking "
        f"at them). Find {card.title} among them and mark it and its price with highlight (an "
        "element id, or the exact words as quote). Say in one short line what the page shows, "
        "and that these are search results, not a verified product page. If it isn't among "
        "them, or the price differs from the card, say so plainly. Don't open it again."
    )


# ---------------------------------------------------------------------------------- pictures
# A search result carries a title, a site, a link and a snippet: no picture. A real one can be
# had from a page that is about the pick itself (the maker's page, a shop's listing, a review of
# just that product): the picture that page itself gives as its preview. A roundup's preview
# picture is not the product, so a page only counts when its own title names the pick.

_GENERIC = {
    "the",
    "and",
    "for",
    "with",
    "new",
    "best",
    "true",
    "wireless",
    "bluetooth",
    "black",
    "white",
}
_NOT_A_PHOTO = re.compile(r"logo|favicon|sprite|placeholder|default[-_]|avatar|/icons?/", re.I)
PICTURE_SIZE = (360, 270)
PICTURE_BUDGET_S = 9.0


def names_pick(pick: str, page_title: str) -> bool:
    """Does this page's title name the pick? Every model number in the pick's name has to be
    there ('A40', 'WH-1000XM5'), and most of its other words."""

    def words(text: str) -> list[str]:
        return [w for w in re.findall(r"[a-z0-9]+", text.lower().replace("-", "")) if len(w) > 1]

    want = [w for w in words(pick) if w not in _GENERIC]
    have = set(words(page_title))
    if len(want) < 2:
        return False
    if any(w not in have for w in want if any(c.isdigit() for c in w)):
        return False
    return sum(w in have for w in want) / len(want) >= 0.6


def _public(url: str) -> bool:
    """A web address out on the internet: never this PC, the home network or a bare name."""
    p = urlparse(url)
    host = (p.hostname or "").lower()
    if (
        p.scheme not in ("http", "https")
        or "." not in host
        or host.endswith((".local", ".internal"))
    ):
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return True
    return ip.is_global


def page_picture(page: str, url: str) -> tuple[str, str]:
    """(the page's title, the picture it gives as its own preview), either may be ''."""
    from pyxi.shopping import _meta

    title = _meta(page, "og:title") or web._text(
        (re.search(r"<title[^>]*>(.*?)</title>", page, re.S | re.I) or [None, ""])[1]
    )
    image = _meta(page, "og:image:secure_url", "og:image", "twitter:image", "twitter:image:src")
    return title, urljoin(url, image) if image else ""


def thumbnail(raw: bytes) -> str | None:
    """A fetched picture as a small JPEG data: address the card can show (the app loads nothing
    from the web itself). None for anything that isn't a real photo-sized picture."""
    from PIL import Image

    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception:
        return None
    w, h = img.size
    if min(w, h) < 160 or max(w, h) / max(1, min(w, h)) > 2.4:
        return None  # an icon, a banner or a tracking pixel
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        flat = Image.new("RGB", img.size, (255, 255, 255))
        flat.paste(img, mask=img.split()[-1])
        img = flat
    img = img.convert("RGB")
    img.thumbnail(PICTURE_SIZE, Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=82)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


async def _get(url: str, limit: int) -> tuple[bytes, str]:
    """Up to ``limit`` bytes of a page or picture and its content type; nothing on any failure."""
    async with (
        shared("web", headers=web.HEADERS, timeout=web.TIMEOUT, follow_redirects=True) as c,
        c.stream("GET", url) as r,
    ):
        if r.status_code != 200 or not _public(str(r.url)):
            return b"", ""
        got = bytearray()
        async for chunk in r.aiter_bytes():
            got += chunk
            if len(got) >= limit:  # a page's head is enough; a huge "picture" isn't one
                break
        return bytes(got[:limit]), r.headers.get("content-type", "")


async def find_picture(
    title: str, links: list[str], *, search: Any = None, get: Any = None
) -> str | None:
    """A picture of the pick from a page that is about it: the sources first, then what a web
    search for the pick's own name turns up. None when no page names it or gives a real photo."""
    get = get or _get
    tried: set[str] = set()

    async def from_page(url: str) -> str | None:
        if url in tried or not _public(url):
            return None
        tried.add(url)
        try:
            raw, kind = await get(url, 600_000)
            if not raw or "html" not in (kind or "html"):
                return None
            page_title, image = page_picture(raw.decode("utf-8", "replace"), url)
            if not image or not names_pick(title, page_title) or _NOT_A_PHOTO.search(image):
                return None
            if not _public(image):
                return None
            data, kind = await get(image, 4_000_000)
            return thumbnail(data) if data and kind.startswith("image/") else None
        except (httpx.HTTPError, ValueError) as exc:
            log.info("no picture from %s: %s", site_name(url), type(exc).__name__)
            return None

    for url in links[:3]:
        if picture := await from_page(url):
            return picture
    try:
        hits = await (search or web.search)(title, 6)
    except Exception:
        hits = []
    for hit in hits[:5]:
        if picture := await from_page(hit.url):
            return picture
    return None


async def add_picture(brain: Brain, card: AnswerCard) -> None:
    """Runs after the card is up, so a picture never makes the answer wait. If one turns up
    and that card is still the one showing, it's sent again with the picture on it."""
    try:
        picture = await asyncio.wait_for(
            find_picture(card.title, [s.url for s in card.sources]), PICTURE_BUDGET_S
        )
    except Exception:  # a picture is a nicety: never an error (a timeout included)
        log.info("no picture for %r", card.title[:60], exc_info=True)
        return
    shown, _ = getattr(brain, "_answer", None) or (None, 0.0)
    if picture and shown is not None and shown.id == card.id:
        card.image = picture
        await brain.hub.emit("answer", card=card.model_dump(mode="json"))

