"""Answer first, then show.

"Find me budget earbuds", "what's a good standing desk?", "cheapest way to do X": Pyxi answers
in the same turn from a web search (a pick, its price, where the price came from), and puts
the answer on a card: the name, the figure, up to three facts, the sources by name, a picture
when a real one can be had, and one button that opens the shop's search results for the pick
by address. Nobody clicks through a shop for that; Scout is for work that needs a site worked
step by step.

The same card carries a teammate's finished result in full, with one short spoken line, so a
job that ends while nobody is looking at the notch is never missed (``spoken_line``).
"""

from __future__ import annotations

import asyncio
import base64
import io
import ipaddress
import json
import logging
import re
import socket
import time
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qsl, quote_plus, unquote_plus, urljoin, urlparse

import httpx
from otto.llm.base import ToolSpec

from pyxi import safety, web
from pyxi.companion.notes import AnswerCard, CardLink, build_answer, site_name
from pyxi.httpshared import shared
from pyxi.voice import split_sentences

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


# Sites whose search address is common knowledge (or in the tables above): one written out
# for them is not a guess.
_SEARCHABLE = set(_SHOP_SEARCH) | {
    "google", "bing", "duckduckgo", "youtube", "wikipedia", "reddit", "github", "stackoverflow",
    "x", "twitter", "linkedin", "facebook", "instagram", "pinterest", "spotify", "imdb", "yelp",
    "tripadvisor", "booking", "expedia", "airbnb", "kayak", "skyscanner", "daraz", "aliexpress",
    "alibaba", "flipkart", "microsoft", "apple", "netflix", "twitch", "soundcloud", "npmjs", "pypi",
}  # fmt: skip
_SEARCH_WORDS = {
    "q", "query", "search", "s", "k", "keyword", "keywords", "term", "text", "st", "d",
    "searchterm", "search_query", "_nkw", "w",
}  # fmt: skip
_TWO_PART_ENDINGS = {"co", "com", "org", "net", "gov", "ac", "edu"}


def _site_key(host: str) -> str:
    """'www.techlandbd.com' -> 'techlandbd', 'en.wikipedia.org' -> 'wikipedia',
    'www.amazon.co.uk' -> 'amazon'."""
    parts = host.lower().removeprefix("www.").split(".")
    if len(parts) >= 3 and parts[-2] in _TWO_PART_ENDINGS and len(parts[-1]) == 2:
        parts = parts[:-1]
    return parts[-2] if len(parts) >= 2 else parts[0]


def guessed_search(url: str, seen: str) -> str | None:
    """A search address the model made up for a site Pyxi knows no search address for, and
    what to open instead: a web search limited to that site. None when the address is fine:
    not a search, a site whose search address is known, or an address that is in ``seen``
    (their words, the screen, what was read).

    Live: on a shop's page, "find it on this website" opened the-shop/search?q=IEM, which
    that shop doesn't have: "404 Page Not Found"."""
    parts = urlparse(url)
    host = parts.netloc.lower().removeprefix("www.")
    if not host or _site_key(host).replace("-", "") in _SEARCHABLE:
        return None
    asked = [v for k, v in parse_qsl(parts.query) if k.lower() in _SEARCH_WORDS and v.strip()]
    path = [p for p in parts.path.lower().split("/") if p]
    if not asked and not any("search" in p for p in path):
        return None
    here = (host + parts.path).rstrip("/").lower()
    if here in " ".join(str(seen or "").lower().split()):
        return None  # that page is really there: it was said, shown or read
    words = asked[0] if asked else (path[-1] if path and "search" not in path[-1] else "")
    words = " ".join(unquote_plus(words).replace("-", " ").split())[:120]
    if not words:
        return None
    return f"https://duckduckgo.com/?q={quote_plus(f'{words} site:{host}')}"


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
    own = ""  # the shop's own ending, when they named a country Pyxi has no word for
    if host.endswith(".ca"):
        cc = "ca"
    elif host.endswith(".co.uk"):
        cc = "uk"
    elif host.endswith(".com.au"):
        cc = "au"
    elif host.endswith(".com"):
        cc = cc if key == "ikea" else "us"  # ikea.com is every country's site
    elif host:
        own = host.split(".", 1)[1]  # amazon.de stays amazon.de, never this PC's country
    known = _SHOP_SEARCH.get(f"{key}.{cc}") or _SHOP_SEARCH.get(key)
    if known is not None and own and ("{tld}" not in known[1] or key in _ONLY_COM_CA):
        known = None  # no search address known for that country: search its own site instead
    if known is None:
        if not host:
            return None
        url = f"https://duckduckgo.com/?q={quote_plus(f'{query} site:{host}')}"
        return CardLink(name=f"Find it on {host}"[:40], url=url)
    name, pattern = known
    tld = own or _TLDS.get(cc, "com")
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
    brain._unsaid_pick = card  # type: ignore[attr-defined]  # until the round has spoken (say_pick)
    brain._spawn(add_picture(brain, card))
    return None


def say_pick(brain: Brain, said_from: int) -> None:
    """A card alone is not an answer: the pick is said too. Called once the round's calls have
    run, and it goes by what was really spoken in the round (``said_from``: how much had been
    said when it began), not by what the model wrote: a `say` dropped as a repeat is not heard."""
    card, brain._unsaid_pick = getattr(brain, "_unsaid_pick", None), None  # type: ignore[attr-defined]
    if card is None or len(brain._said) > said_from or brain._pending_say:
        return
    brain._speak(spoken_pick(card))


def spoken_pick(card: AnswerCard) -> str:
    """The card's pick as one spoken line, for when the model showed it without saying it."""
    line = f"My pick is the {card.title}" + (f", {card.figure}" if card.figure else "") + "."
    return f"{line} {card.note.rstrip('.')}." if card.note else line


async def show(brain: Brain, card: AnswerCard) -> None:
    brain._answer = (card, time.monotonic())  # type: ignore[attr-defined]  # the card that is up
    if card.kind == "answer":
        # Kept apart: a teammate's result card takes the pick's place on screen, and "show me"
        # still means the pick.
        brain._pick = brain._answer  # type: ignore[attr-defined]
    await brain.hub.emit("answer", card=card.model_dump(mode="json"))


def current(brain: Brain) -> AnswerCard | None:
    """The pick they can still be asking about ("show me", "open it")."""
    card, at = getattr(brain, "_pick", None) or (None, 0.0)
    if card is None or time.monotonic() - at > CARD_KEEPS_S:
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


def _global(address: str) -> bool:
    """An address out on the internet: not loopback, private, link-local or reserved."""
    try:
        ip = ipaddress.ip_address(address.split("%")[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped  # ::ffff:10.0.0.1 is 10.0.0.1
    return ip.is_global


def _plausible(url: str) -> bool:
    """A web address that could be out on the internet, going by how it is written: never a
    bare name, this PC, or a private address. Cheap; ``_public`` also looks the name up."""
    p = urlparse(url)
    host = (p.hostname or "").lower().rstrip(".")
    if (
        p.scheme not in ("http", "https")
        or "." not in host
        or host.endswith((".local", ".internal", ".localhost", ".lan", ".home", ".home.arpa"))
    ):
        return False
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return True
    return _global(host)


def _addresses(host: str) -> list[str]:
    """Every address a name resolves to. Blocking: called off the event loop."""
    return [str(info[4][0]) for info in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)]


async def _public(url: str) -> bool:
    """A web address out on the internet: never this PC or the home network. A name says
    nothing by itself ("router.lan", "nas.home", "10.0.0.1.nip.io"), so it is looked up, and
    every address it has must be a public one."""
    if not _plausible(url):
        return False
    host = (urlparse(url).hostname or "").rstrip(".")
    try:
        ipaddress.ip_address(host)
        return True  # written as a number: _plausible has checked it
    except ValueError:
        pass
    try:
        found = await asyncio.to_thread(_addresses, host)
    except (OSError, UnicodeError):
        return False
    return bool(found) and all(_global(a) for a in found)


HEAD_KEEPS = 100_000  # characters of a page read for its title and picture
_HEAD_END = re.compile(r"</head\s*>", re.I)
_TITLE = re.compile(r"<title[^<>]*>", re.I)
_TITLE_END = re.compile(r"</title", re.I)


def page_picture(page: str, url: str) -> tuple[str, str]:
    """(the page's title, the picture it gives as its own preview), either may be ''. Only
    the page's head is read (it is where both are), and never more than ``HEAD_KEEPS``
    characters: a page built to be slow to parse costs a moment, not the event loop."""
    from pyxi.shopping import _meta

    end = _HEAD_END.search(page, 0, HEAD_KEEPS)
    page = page[: end.start() if end else HEAD_KEEPS]
    title = _meta(page, "og:title")
    if not title and (start := _TITLE.search(page)):
        stop = _TITLE_END.search(page, start.end())
        title = web._text(page[start.end() : stop.start()][:300]) if stop else ""
    image = _meta(page, "og:image:secure_url", "og:image", "twitter:image", "twitter:image:src")
    return title, urljoin(url, image) if image else ""


PICTURE_MAX_SIDE = 4000  # a "picture" larger than this is never decoded


def thumbnail(raw: bytes) -> str | None:
    """A fetched picture as a small JPEG data: address the card can show (the app loads nothing
    from the web itself). None for anything that isn't a real photo-sized picture."""
    from PIL import Image

    try:
        img = Image.open(io.BytesIO(raw))  # reads the header only
        if max(img.size) > PICTURE_MAX_SIDE:
            return None  # its size is checked before it is decoded: a few bytes can claim gigabytes
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


MAX_HOPS = 3  # redirects followed for one page or picture


async def _get(url: str, limit: int) -> tuple[bytes, str]:
    """Up to ``limit`` bytes of a page or picture and its content type; nothing on any failure.
    Redirects are followed by hand, ``MAX_HOPS`` at most, and every address on the way has to
    be a public one: a page that sends the request on to this PC or the home network gets
    nothing fetched for it."""
    async with shared("web", headers=web.HEADERS, timeout=web.TIMEOUT, follow_redirects=True) as c:
        for _ in range(MAX_HOPS + 1):
            if not await _public(url):
                return b"", ""
            async with c.stream("GET", url, follow_redirects=False) as r:
                if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                    url = urljoin(url, r.headers["location"])
                    continue
                if r.status_code != 200:
                    return b"", ""
                got = bytearray()
                async for chunk in r.aiter_bytes():
                    got += chunk
                    if len(got) >= limit:  # a page's head is enough; a huge "picture" isn't one
                        break
                return bytes(got[:limit]), r.headers.get("content-type", "")
    return b"", ""  # still being sent on after MAX_HOPS


async def find_picture(
    title: str, links: list[str], *, search: Any = None, get: Any = None
) -> str | None:
    """A picture of the pick from a page that is about it: the sources first, then what a web
    search for the pick's own name turns up. None when no page names it or gives a real photo."""
    get = get or _get
    tried: set[str] = set()

    async def from_page(url: str) -> str | None:
        if url in tried or not _plausible(url):
            return None
        tried.add(url)
        try:
            raw, kind = await get(url, 600_000)
            if not raw or "html" not in (kind or "html"):
                return None
            # Parsing and decoding are work for another thread: speech and the app's
            # connection share this one.
            page_title, image = await asyncio.to_thread(
                page_picture, raw.decode("utf-8", "replace"), url
            )
            if not image or not names_pick(title, page_title) or _NOT_A_PHOTO.search(image):
                return None
            if not _plausible(image):
                return None
            data, kind = await get(image, 4_000_000)
            if not data or not kind.startswith("image/"):
                return None
            return await asyncio.to_thread(thumbnail, data)
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


# ---------------------------------------------------------------------------------- finished jobs

_UNSPEAKABLE = re.compile(r"https?://|www\.|[A-Za-z]:\\|[/\\]\w+[/\\]|\w+@\w+\.|[{}\[\]<>|=]")
_FIRST_PERSON = re.compile(r"^\W*(i|i'm|i've|i'll|i'd|my|we|we're|we've)\b", re.I)


def headline(summary: str, limit: int = 140) -> str:
    """The first sentence of a result if it can be said aloud as Pyxi's own: short, no
    addresses or paths, and not the teammate talking about itself ("I compared...")."""
    first = next(iter(split_sentences(" ".join(str(summary or "").split()))), "")
    if not first or len(first) > limit or _UNSPEAKABLE.search(first) or _FIRST_PERSON.match(first):
        return ""
    return first if first[-1:] in ".!?" else first + "."


RESULT_KEEPS = 6000  # characters of a finished job's result that later turns can see


def for_later(job: Any) -> str:
    """A finished job's result as later turns see it: all of it (it used to stop at 600
    characters, two items into a list of nine), plus what a browser teammate collected. It is
    what a teammate read on the web or in files and it sits in the conversation, so it is
    fenced like a search result: a line inside it can't pass for the user or for instructions."""
    text = (job.summary or job.status or "").strip()
    out = text[:RESULT_KEEPS] + (" (cut here: it was longer)" if len(text) > RESULT_KEEPS else "")
    if job.role == "browser" and job.data:
        try:
            out += "\nCollected: " + json.dumps(job.data, default=str)[:2000]
        except (TypeError, ValueError):
            pass
    return safety.fence(f"{job.agent_name}'s result", out)


# Teammates whose work is a change the user can see where they asked for it (a sheet edited,
# files converted). When it works, "done" is all there is to say: the founder, live, after
# hearing each edit read back to him: "It's all the same thing again and again. When you're
# done with the work, just tell that you're done. That's all."
# (Not Ditto: its job ends with a plan waiting on a card, and "Ditto's done." was said while
# nothing had been renamed yet. It says its own title, "Ready to do 3 more.")
DOERS = {"documents", "files"}
# A result that worked, with something they need to hear all the same.
_CAVEAT = re.compile(
    r"(?i)\b(but|not|no|none|nothing|couldn'?t|can'?t|didn'?t|doesn'?t|won'?t|wasn'?t|isn'?t|instead|"
    r"however|although|except|failed|unable|warn\w*|errors?|wrong|mistake|stopped|only|still|already)\b"
)


def spoken_line(
    agent: str, *, ok: bool, title: str, summary: str, on_card: bool, role: str = ""
) -> str:
    """The one short line Pyxi says when background work ends. The details stay on the card."""
    generic = title in (f"{agent} finished", f"{agent} couldn't finish", "")
    if ok and role in DOERS:
        return f"{agent}'s done."
    if ok and agent == "Pyxi" and generic and not _CAVEAT.search(summary or ""):
        # Its own mouse and keyboard, and they watched it happen: "GenOffice is open and its
        # window is showing on screen" tells them nothing, and was still being said when the
        # founder asked for the next thing ("you're still talking when I'm talking").
        return "Done."
    if not ok:
        why = headline(summary, 160)
        lead = (
            f"{agent} couldn't finish that."
            if generic
            else (title if title[-1:] in ".!?" else f"{title}.")
        )
        return (
            f"{lead} {why}"
            if why and why.rstrip(".") != title.rstrip(".")
            else (f"{lead} The card says why." if on_card else lead)
        )
    if not generic:
        line = title if title[-1:] in ".!?" else f"{title}."
    else:
        line = f"{agent}'s done. {headline(summary)}".strip()
    return f"{line} The full result is on the card." if on_card else line
