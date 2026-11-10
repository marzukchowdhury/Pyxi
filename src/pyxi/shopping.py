"""Cheaper elsewhere: the exact product on this page, and what other stores charge for it.

Three pieces, all plain code with no model call:

* :func:`parse_product_page` reads a store's product page the way search engines do: the
  schema.org data stores publish for them (JSON-LD, microdata, Open Graph product tags), with
  the visible page text as a last resort. That gives the name, brand, model number, barcode
  (GTIN/UPC), price, shipping, stock and condition.
* :func:`same_item` decides whether two listings are the same thing, strictly. A matching
  barcode or model number is proof; a different size, pack, colour, generation or a
  refurbished unit is a different item. Lookalikes never count, and "can't tell" is a result.
* :func:`compare` finds other stores' pages (web search), reads them politely (one page per
  store, a few at a time, no retries, CAPTCHA pages skipped, never a login) and picks the
  cheapest exact match.

Pages are data, never instructions.
"""

from __future__ import annotations

import asyncio
import html as htmlmod
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from urllib.parse import urlparse

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------------- stores

STORES = {
    "amazon": "Amazon",
    "bestbuy": "Best Buy",
    "walmart": "Walmart",
    "target": "Target",
    "costco": "Costco",
    "newegg": "Newegg",
    "bhphotovideo": "B&H Photo",
    "adorama": "Adorama",
    "microcenter": "Micro Center",
    "homedepot": "Home Depot",
    "lowes": "Lowe's",
    "staples": "Staples",
    "apple": "Apple",
    "samsung": "Samsung",
    "dell": "Dell",
    "lenovo": "Lenovo",
    "hp": "HP",
    "gamestop": "GameStop",
    "kohls": "Kohl's",
    "macys": "Macy's",
    "nordstrom": "Nordstrom",
    "sephora": "Sephora",
    "ulta": "Ulta",
    "wayfair": "Wayfair",
    "ikea": "IKEA",
    "crutchfield": "Crutchfield",
    "rei": "REI",
    "dickssportinggoods": "Dick's",
    "officedepot": "Office Depot",
    "canadiantire": "Canadian Tire",
    "thesource": "The Source",
    "londondrugs": "London Drugs",
    "visions": "Visions",
    "memoryexpress": "Memory Express",
    "canadacomputers": "Canada Computers",
    "currys": "Currys",
    "argos": "Argos",
    "johnlewis": "John Lewis",
    "mediamarkt": "MediaMarkt",
    "jbhifi": "JB Hi-Fi",
}
# Marketplaces of used and grey-market listings, deal blogs, reviews and price trackers: their
# prices aren't a store's offer for the new item, so they're never the answer.
NOT_STORES = (
    "ebay.",
    "aliexpress.",
    "temu.",
    "facebook.",
    "craigslist.",
    "kijiji.",
    "reddit.",
    "youtube.",
    "slickdeals.",
    "rtings.",
    "camelcamelcamel.",
    "pricespy.",
    "google.",
    "bing.",
    "duckduckgo.",
    "wikipedia.",
    "cnet.",
    "theverge.",
    "techradar.",
    "pcmag.",
    "tomsguide.",
    "wirecutter",
)
PRODUCT_PATH = re.compile(
    r"/(dp|gp/product|ip|p|product|products|site|sku|item|itm|pd|catalog/product|buy)/"
    r"|/A-\d+|skuId=|\.p\?|/\d{6,}(?:\.html?)?(?:$|\?)",
    re.I,
)


def domain(url: str) -> str:
    return urlparse(url if "://" in url else "https://" + url).netloc.lower().removeprefix("www.")


def store_key(url: str) -> str:
    """'bestbuy.ca' -> 'bestbuy', 'smile.amazon.co.uk' -> 'amazon'."""
    parts = domain(url).split(".")
    for p in parts:
        if p in STORES:
            return p
    if len(parts) >= 3 and parts[-2] in ("co", "com"):
        return parts[-3]
    return parts[-2] if len(parts) >= 2 else parts[0]


def store_name(url: str, site_name: str = "") -> str:
    key = store_key(url)
    if key in STORES:
        return STORES[key]
    return site_name.strip() or key.capitalize()


def is_store_page(url: str, brand: str = "") -> bool:
    """A page at a store we can compare: a known store, or the brand's own shop (sony.com for
    Sony). Review sites, deal blogs and marketplaces also publish product prices (affiliate
    prices, used listings), so an unknown site never counts, however shop-like it looks."""
    d = domain(url)
    if not d or any(bad in d for bad in NOT_STORES):
        return False
    if store_key(url) in STORES:
        return True
    b = re.sub(r"[^a-z0-9]", "", brand.lower())
    return len(b) >= 2 and store_key(url) == b


def looks_like_product_url(url: str) -> bool:
    """Cheap test for the auto check: a known store's product page, by its address alone."""
    d = domain(url)
    if not d or any(bad in d for bad in NOT_STORES):
        return False
    return store_key(url) in STORES and bool(PRODUCT_PATH.search(url))


# ---------------------------------------------------------------------------------- prices

SYMBOLS = {"$": "USD", "US$": "USD", "C$": "CAD", "CA$": "CAD", "CAD$": "CAD", "A$": "AUD",
           "AU$": "AUD", "£": "GBP", "€": "EUR", "¥": "JPY", "₹": "INR"}  # fmt: skip
CODES = {"USD", "CAD", "AUD", "GBP", "EUR", "JPY", "INR", "NZD"}
SIGN = {
    "USD": "$",
    "CAD": "$",
    "AUD": "$",
    "NZD": "$",
    "GBP": "£",
    "EUR": "€",
    "JPY": "¥",
    "INR": "₹",
}

_PRICE = re.compile(
    r"(?P<pre>US\$|CA\$|CAD\$|C\$|AU\$|A\$|[$£€¥₹]|\b(?:USD|CAD|AUD|GBP|EUR|NZD)\b)?\s*"
    r"(?P<num>(?:\d{1,3}(?:[,.  ]\d{3})+|\d+)(?:[.,]\d{1,2})?)(?!\d)"
    r"\s*(?P<post>[€£]|\b(?:USD|CAD|AUD|GBP|EUR|NZD)\b)?"
)


def _number(raw: str) -> float | None:
    s = re.sub(r"[\s  ]", " ", raw).strip()
    # The last separator followed by 1-2 digits is the decimal point; the rest group thousands.
    m = re.match(r"^(.*?)([.,])(\d{1,2})$", s)
    if m:
        whole = re.sub(r"[.,\s]", "", m.group(1))
        if whole.isdigit() or whole == "":
            return float(f"{whole or 0}.{m.group(3)}")
    whole = re.sub(r"[.,\s]", "", s)
    return float(whole) if whole.isdigit() else None


def parse_price(text: str, default_currency: str = "USD") -> tuple[float, str] | None:
    """The first price in some text: '$1,299.99', 'CA$ 30', '1.299,99 €', '24.99 USD'.

    A bare number counts only when nothing else is there (a JSON-LD "price": "24.99"); in
    prose, a price needs its currency sign or code, so "4.5 stars" and "Model 2024" aren't
    prices. "Was $X Now $Y" gives the now price. Returns (amount, ISO currency) or None."""
    if text is None:
        return None
    t = htmlmod.unescape(str(text)).replace(" ", " ").strip()
    if not t:
        return None
    now = re.search(r"\b(?:now|sale|today|our price|your price)\b[:\s]*", t, re.I)
    if now and re.search(r"\b(?:was|reg(?:ular)?|list|msrp)\b", t[: now.start()], re.I):
        t = t[now.end() :]
    bare = re.fullmatch(r"\d+(?:[.,]\d+)*", t)
    for m in _PRICE.finditer(t):
        pre, post = m.group("pre"), m.group("post")
        if not (pre or post or bare):
            continue
        value = _number(m.group("num"))
        if value is None:
            continue
        sym = (pre or post or "").strip()
        currency = SYMBOLS.get(sym) or (sym if sym in CODES else default_currency)
        if sym == "$" and default_currency in ("CAD", "AUD", "NZD"):
            currency = default_currency  # "$" on a .ca store is Canadian dollars
        return value, currency
    return None


def money(amount: float, currency: str = "USD") -> str:
    sign = SIGN.get(currency, "")
    num = f"{amount:,.0f}" if abs(amount - round(amount)) < 0.005 else f"{amount:,.2f}"
    return f"{sign}{num}" if sign else f"{num} {currency}"


def site_currency(url: str) -> str:
    d = domain(url)
    if d.endswith(".ca"):
        return "CAD"
    if d.endswith((".co.uk", ".uk")):
        return "GBP"
    if d.endswith(".com.au"):
        return "AUD"
    if d.endswith((".de", ".fr", ".it", ".es", ".nl", ".ie")):
        return "EUR"
    return "USD"


# ---------------------------------------------------------------------------------- listings


@dataclass
class Offer:
    """One store's listing: what the item is, and what it costs there."""

    title: str
    url: str = ""
    store: str = ""
    price: float | None = None
    currency: str = "USD"
    shipping: float | None = None  # 0.0 is free; None is unknown
    in_stock: bool | None = None
    brand: str = ""
    model: str = ""  # the maker's model or part number (MPN)
    gtin: str = ""  # UPC / EAN, normalised to 14 digits
    sku: str = ""
    condition: str = "new"  # new, used, refurbished
    color: str = ""
    source: str = ""  # where the facts came from: "json-ld", "microdata", "meta", "text", "screen"
    checked_at: float = field(default_factory=time.time)

    @property
    def total(self) -> float | None:
        if self.price is None:
            return None
        return self.price + (self.shipping or 0.0)

    def public(self) -> dict:
        return {
            "store": self.store,
            "title": self.title,
            "url": self.url,
            "price": self.price,
            "currency": self.currency,
            "shipping": self.shipping,
            "in_stock": self.in_stock,
            "model": self.model,
            "checked_at": self.checked_at,
        }


def norm_gtin(raw: object) -> str:
    digits = re.sub(r"\D", "", str(raw or ""))
    return digits.zfill(14) if len(digits) in (8, 12, 13, 14) else ""


def _first(v: object) -> object:
    return v[0] if isinstance(v, list) and v else v


def _name_of(v: object) -> str:
    v = _first(v)
    if isinstance(v, dict):
        return str(v.get("name") or "")
    return str(v or "")


def _ld_blocks(page: str) -> list[object]:
    out: list[object] = []
    for raw in re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', page, re.S | re.I
    ):
        raw = raw.strip().removeprefix("<!--").removesuffix("-->").strip()
        try:
            out.append(json.loads(raw))
        except ValueError:
            try:  # some stores leave raw newlines or a trailing comma in it
                out.append(json.loads(re.sub(r",\s*([}\]])", r"\1", raw.replace("\n", " "))))
            except ValueError:
                continue
    return out


def _walk(node: object):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def _types(node: dict) -> set[str]:
    t = node.get("@type")
    return {str(x).split("/")[-1] for x in (t if isinstance(t, list) else [t]) if x}


def _condition(raw: object) -> str:
    s = str(raw or "").lower()
    if "refurb" in s or "renewed" in s or "reconditioned" in s:
        return "refurbished"
    if "used" in s or "damaged" in s or "open box" in s or "openbox" in s:
        return "used"
    return "new"


def _stock(raw: object) -> bool | None:
    s = str(raw or "").lower()
    if not s:
        return None
    if any(k in s for k in ("outofstock", "soldout", "discontinued", "out of stock")):
        return False
    if any(
        k in s
        for k in (
            "instock",
            "limitedavailability",
            "onlineonly",
            "preorder",
            "backorder",
            "instoreonly",
        )
    ):
        return True
    return None


def _offer_from_ld(node: dict, url: str) -> Offer | None:
    name = htmlmod.unescape(str(node.get("name") or "")).strip()
    if not name:
        return None
    gtin = ""
    for key in ("gtin14", "gtin13", "gtin12", "gtin8", "gtin", "upc", "ean"):
        if node.get(key):
            gtin = norm_gtin(node[key])
            if gtin:
                break
    offers = node.get("offers")
    offer_list = [
        o for o in _walk(offers) if isinstance(o, dict) and _types(o) & {"Offer", "AggregateOffer"}
    ]
    cur_default = site_currency(url)
    price = currency = None
    shipping: float | None = None
    stock: bool | None = None
    condition = _condition(node.get("itemCondition"))
    seller = ""
    for o in offer_list:
        raw = o.get("price")
        if raw in (None, "") and isinstance(o.get("priceSpecification"), (dict, list)):
            raw = _first(o["priceSpecification"]).get("price")  # type: ignore[union-attr]
        if raw in (None, ""):
            raw = o.get("lowPrice")
        parsed = (
            parse_price(str(raw), str(o.get("priceCurrency") or cur_default))
            if raw not in (None, "")
            else None
        )
        if parsed is None:
            continue
        value, cur = parsed
        if o.get("priceCurrency"):
            cur = str(o["priceCurrency"]).upper()
        if price is None or value < price:
            price, currency = value, cur
            stock = _stock(o.get("availability"))
            condition = _condition(o.get("itemCondition") or node.get("itemCondition"))
            seller = _name_of(o.get("seller"))
            ship = o.get("shippingDetails")
            for s in _walk(ship):
                if isinstance(s, dict) and "shippingRate" in s:
                    rate = _first(s["shippingRate"])
                    if isinstance(rate, dict) and rate.get("value") not in (None, ""):
                        try:
                            shipping = float(str(rate["value"]).replace(",", ""))
                        except ValueError:
                            pass
                    break
    model = str(node.get("mpn") or node.get("model") or "")
    if isinstance(node.get("model"), dict):
        model = str(node.get("mpn") or _name_of(node["model"]))
    color = node.get("color")
    return Offer(
        title=name,
        url=url,
        store=store_name(url, seller if store_key(url) not in STORES else ""),
        price=price,
        currency=currency or cur_default,
        shipping=shipping,
        in_stock=stock,
        brand=htmlmod.unescape(_name_of(node.get("brand"))).strip(),
        model=htmlmod.unescape(model).strip(),
        gtin=gtin,
        sku=str(node.get("sku") or ""),
        condition=condition,
        color=str(_first(color) or "") if isinstance(color, (str, list)) else "",
        source="json-ld",
    )


# Never [^>]: on a page with many "<meta" and no ">" that is quadratic (64 KB took 41 seconds).
_META_TAG = re.compile(r"<meta[^<>]+>", re.I)
_META_CONTENT = re.compile(r"""content=["']([^"']*)["']""", re.I)


def _meta(page: str, *names: str) -> str:
    """The content of the first <meta> tag carrying one of these names (property, name or
    itemprop), names tried in order. Each tag is found once, then looked at by itself."""
    tags = _META_TAG.findall(page)
    for n in names:
        named = re.compile(rf"""(?:property|name|itemprop)=["']{re.escape(n)}["']""", re.I)
        for tag in tags:
            if named.search(tag) and (m := _META_CONTENT.search(tag)) and m.group(1).strip():
                return htmlmod.unescape(m.group(1)).strip()
    return ""


def _itemprop(page: str, prop: str) -> str:
    m = re.search(
        rf'itemprop=["\']{prop}["\'][^>]*content=["\']([^"\']+)["\']', page, re.I
    ) or re.search(rf'content=["\']([^"\']+)["\'][^>]*itemprop=["\']{prop}["\']', page, re.I)
    if m:
        return htmlmod.unescape(m.group(1)).strip()
    m = re.search(rf'itemprop=["\']{prop}["\'][^>]*>(.*?)<', page, re.I | re.S)
    return _strip(m.group(1)) if m else ""


def _strip(fragment: str) -> str:
    return re.sub(r"\s+", " ", htmlmod.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


BLOCKED = re.compile(
    r"captcha|robot check|are you a (?:human|robot)|unusual traffic|access denied|"
    r"request blocked|verify you are human|px-captcha|cf-chl|attention required|"
    r"enable javascript and cookies to continue|pardon our interruption",
    re.I,
)


def looks_blocked(page: str) -> bool:
    """A bot check or block page instead of the product. We skip it; never try to get past it."""
    head = page[:60_000]
    if re.search(r'type=["\']application/ld\+json["\']', head, re.I) and '"Product"' in page:
        return False
    return bool(BLOCKED.search(head)) and len(re.sub(r"<[^>]+>", "", head)) < 40_000


_BOX = (
    r'(?:id|class)=["\'][^"\']*(?:{names})[^"\']*["\'][^>]*>((?:(?!</?(?:section|form)).){{0,400}})'
)
# The item's own price box, by the names stores give it: the strongest names first.
PRICE_BOXES = (
    _BOX.format(names="corePrice|priceToPay|apex_desktop|priceView-customer-price"),
    _BOX.format(names="price-current|sale-price|our-price|final-price|product-price"),
    r'class=["\'][^"\']*(?:price|Price)[^"\']*["\'][^>]*>((?:(?!</?(?:div|section)).){0,200})',
)
ADD_ON = re.compile(r"warranty|protection plan|accessor|attach|add to your order|bundle", re.I)


def _main_price(page: str, currency: str) -> tuple[float, str] | None:
    """The item's price from its price box; never a protection plan's or an accessory's."""
    for pattern in PRICE_BOXES:
        for m in re.finditer(pattern, page, re.S):
            tag_start = page.rfind("<", 0, m.start())
            if ADD_ON.search(page[max(0, tag_start - 120) : m.start() + 40]):
                continue
            if p := parse_price(_strip(m.group(1)), currency):
                return p
    return None


def _shipping_from_text(text: str, currency: str) -> float | None:
    free = re.search(r"\bfree (?:standard )?(?:shipping|delivery)\b(.{0,40})", text, re.I)
    if free and not re.search(
        r"\b(?:over|orders? of|minimum|members?|with)\b", free.group(1), re.I
    ):
        return 0.0
    m = re.search(r"\b(?:shipping|delivery)\b[^$£€\d]{0,20}([$£€]\s?\d+(?:[.,]\d{2})?)", text, re.I)
    if m and (p := parse_price(m.group(1), currency)):
        return p[0]
    return None


def parse_product_page(page: str, url: str) -> Offer | None:
    """The product on a store page, or None if it isn't one (or the price can't be found)."""
    if not page or looks_blocked(page):
        return None
    offer: Offer | None = None
    for block in _ld_blocks(page):
        for node in _walk(block):
            types = _types(node)
            if "Product" in types or "ProductGroup" in types:
                found = _offer_from_ld(node, url)
                if found and (offer is None or (offer.price is None and found.price is not None)):
                    offer = found
    cur = site_currency(url)
    body_text = _strip(re.sub(r"<(script|style|noscript)\b.*?</\1>", " ", page, flags=re.S | re.I))
    if offer is None:
        # The product's own title box first: a page's first <h1> is sometimes a pop-up's.
        named = re.search(r'id=["\']productTitle["\'][^>]*>(.*?)<', page, re.S | re.I)
        t = re.search(r"<title[^>]*>(.*?)</title>", page, re.S | re.I)
        h1 = re.search(r"<h1\b[^>]*>(.*?)</h1>", page, re.S | re.I)
        title = (
            (_strip(named.group(1)) if named else "")
            or _itemprop(page, "name")
            or _meta(page, "og:title", "twitter:title")
            or (_strip(t.group(1)) if t else "")
            or (_strip(h1.group(1)) if h1 else "")
        )
        if not title:
            return None
        offer = Offer(title=title, url=url, store="", currency=cur, source="meta")
        raw = _itemprop(page, "price") or _meta(page, "product:price:amount", "og:price:amount")
        code = _itemprop(page, "priceCurrency") or _meta(
            page, "product:price:currency", "og:price:currency"
        )
        if raw and (p := parse_price(raw, code.upper() or cur)):
            offer.price, offer.currency = p[0], (code.upper() or p[1])
            offer.source = "microdata" if _itemprop(page, "price") else "meta"
        else:
            # Last resort: the price box on the page (not a protection plan's or add-on's).
            if p := _main_price(page, cur):
                offer.price, offer.currency, offer.source = p[0], p[1], "text"
        offer.brand = _itemprop(page, "brand") or _meta(page, "product:brand", "og:brand")
        offer.model = _itemprop(page, "mpn") or _itemprop(page, "model")
        m = re.search(
            r"\b(?:item )?model(?: number| no\.?)?[:\s]+([A-Z0-9][A-Z0-9/-]{3,24})\b",
            body_text,
            re.I,
        )
        if not offer.model and m and model_numbers(m.group(1)):
            offer.model = m.group(1)
        for key in ("gtin13", "gtin12", "gtin14", "gtin8", "gtin"):
            if g := norm_gtin(_itemprop(page, key)):
                offer.gtin = g
                break
        offer.in_stock = _stock(
            _itemprop(page, "availability")
            or _meta(page, "product:availability", "og:availability")
        )
        offer.condition = _condition(
            _itemprop(page, "itemCondition") or _meta(page, "product:condition")
        )
        offer.store = store_name(url, _meta(page, "og:site_name"))
    offer.title = re.sub(r"^[\w.-]+\.(?:com|ca|co\.uk|com\.au)\s*:\s*", "", offer.title, flags=re.I)
    offer.title = (
        re.sub(r"\s*[|:-]\s*(?:" + re.escape(offer.store) + r").*$", "", offer.title, flags=re.I)
        or offer.title
    )
    if offer.shipping is None:
        offer.shipping = _shipping_from_text(body_text[:20_000], offer.currency)
    if offer.in_stock is None and re.search(
        r"\b(?:out of stock|sold out|currently unavailable|no longer available)\b",
        body_text[:20_000],
        re.I,
    ):
        offer.in_stock = False
    if offer.condition == "new" and re.search(r"\b(?:refurbished|renewed)\b", offer.title, re.I):
        offer.condition = "refurbished"
    return offer


# ---------------------------------------------------------------------------------- matching

_UNITS = {
    "gb": ("storage", 1.0), "tb": ("storage", 1000.0),
    "in": ("screen", 1.0), "inch": ("screen", 1.0), '"': ("screen", 1.0), "”": ("screen", 1.0),
    "ml": ("volume", 1.0), "l": ("volume", 1000.0), "fl oz": ("volume", 29.5735), "floz": ("volume", 29.5735),
    "oz": ("weight", 28.3495), "g": ("weight", 1.0), "kg": ("weight", 1000.0), "lb": ("weight", 453.592),
    "lbs": ("weight", 453.592), "mah": ("battery", 1.0), "w": ("power", 1.0), "mm": ("size_mm", 1.0),
    "cm": ("size_mm", 10.0), "ft": ("length", 30.48), "qt": ("volume", 946.353),
}  # fmt: skip
_SPEC = re.compile(
    r"(?<![\w.])(\d+(?:\.\d+)?)\s*[- ]?\s*(fl\.? ?oz|floz|gb|tb|inch(?:es)?|in\b|[\"”]|ml|mah|lbs?|kg|qt|oz|mm|cm|ft|g\b|l\b|w\b)",
    re.I,
)
_PACK = re.compile(
    r"(?:(\d+)\s*[- ]?\s*(?:pack|pk|count|ct|pcs|pieces|piece|rolls?|pods?)\b|\bpack of (\d+)|\bset of (\d+)|\b(\d+)\s*x\s+)",
    re.I,
)
# Words that make a different product of the same line: "iPhone 17" is not "iPhone 17 Pro".
VARIANT_WORDS = {
    "pro", "max", "plus", "mini", "ultra", "lite", "air", "se", "xl", "xxl", "fe", "slim",
    "kids", "junior", "jr", "deluxe", "premium", "studio", "neo", "edge", "fold", "flip", "go",
    "oled", "qled", "wifi", "cellular", "lte", "5g", "gen", "case", "refill", "replacement",
    "bundle", "kit", "only", "body", "renewed", "refurbished", "used",
}  # fmt: skip
COLORS = {
    "black", "white", "silver", "gray", "grey", "blue", "red", "green", "pink", "purple", "gold",
    "yellow", "orange", "beige", "brown", "navy", "graphite", "midnight", "starlight", "titanium",
    "rose", "teal", "cream", "charcoal", "sage", "lavender", "coral", "platinum", "bronze",
}  # fmt: skip
STOPWORDS = {
    "the", "and", "with", "for", "a", "an", "of", "in", "by", "new", "free", "shipping", "sale",
    "wireless", "edition", "model", "latest", "version", "&", "-", "|", "+", "on", "to",
}  # fmt: skip
_NOT_MODEL = re.compile(
    r"^(?:\d+(?:gb|tb|mb|mah|mm|cm|ml|w|hz|mp|k|p|in|oz|lb|lbs|g|kg|ft|v|pack|pk|ct|count|pcs)|"
    r"(?:usb|wi|bt|ddr|pcie|gen|hdmi|wifi|lte|ip)\w*|\d{4}|[a-z]\d|\d[a-z]|"
    r"(?:19|20)\d\d(?:model|edition|release)?)$",
    re.I,
)


def norm_model(raw: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(raw or "").upper())


def model_numbers(text: str) -> set[str]:
    """Model and part numbers in a title: 'WH-1000XM5', 'SM-S938U', 'MQ8N3LL/A', 'B0CX23V2ZK'.

    A model number mixes letters and digits and is at least 4 characters once its dashes are
    gone; sizes ('256GB'), years and standards ('USB-C', 'Wi-Fi 6') are not model numbers."""
    out = set()
    for tok in re.findall(r"[A-Za-z0-9]+(?:[-/.][A-Za-z0-9]+)*", text or ""):
        n = norm_model(tok)
        if len(n) < 4 or not re.search(r"\d", n) or not re.search(r"[A-Z]", n):
            continue
        if _NOT_MODEL.match(tok.replace("-", "").replace(".", "")):
            continue
        out.add(n)
        if "/" in tok and len(base := norm_model(tok.split("/")[0])) >= 4:
            out.add(base)  # WH1000XM5/B is the WH1000XM5 in black; MQ8N3LL/A the MQ8N3LL
    return out


def specs(text: str) -> dict[str, float]:
    """Sizes in a title, in base units: {'storage': 256, 'screen': 65, 'pack': 2}."""
    t = (text or "").lower().replace("”", '"')
    # "5G" is a network and "2-in-1" a laptop style, not 5 grams or a 2-inch screen.
    t = re.sub(r"\b\d\s*-?\s*in\s*-?\s*1\b|\b[345]g\b", " ", t)
    found: dict[str, float] = {}
    for num, unit in _SPEC.findall(t):
        u = re.sub(r"[\s.]", "", unit.lower())
        u = "in" if u.startswith("inch") else u
        if u not in _UNITS:
            continue
        kind, mult = _UNITS[u]
        found.setdefault(kind, round(float(num) * mult, 1))
    for m in _PACK.finditer(t):
        n = next((g for g in m.groups() if g), None)
        if n and 1 < int(n) < 1000:
            found.setdefault("pack", float(n))
    return found


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if w not in STOPWORDS}


def _colors(offer: Offer) -> set[str]:
    words = _words(offer.title) | _words(offer.color)
    return words & COLORS


@dataclass
class Verdict:
    same: bool | None  # True: the exact item; False: a different item; None: can't tell
    why: str


def same_item(want: Offer, other: Offer) -> Verdict:
    """Is ``other`` exactly the item ``want`` is? Strict: proof, or it isn't counted."""
    if other.condition != want.condition:
        return Verdict(False, f"that one is {other.condition}")
    sw, so = specs(want.title), specs(other.title)
    for kind in sw.keys() & so.keys():
        if abs(sw[kind] - so[kind]) > 0.05 * max(sw[kind], 1):
            return Verdict(False, f"a different {kind if kind != 'pack' else 'pack size'}")
    if ("pack" in sw) != ("pack" in so):
        return Verdict(False, "a different pack size")
    cw, co = _colors(want), _colors(other)
    if cw and co and not (cw & co):
        return Verdict(False, f"a different colour ({', '.join(sorted(co))})")
    if want.gtin and other.gtin and want.gtin == other.gtin:
        return Verdict(True, "same barcode (UPC)")
    mw = ({norm_model(want.model)} if want.model else set()) | model_numbers(want.title)
    mo = ({norm_model(other.model)} if other.model else set()) | model_numbers(other.title)
    mw.discard("")
    mo.discard("")
    if want.model and other.model:
        a, b = norm_model(want.model), norm_model(other.model)
        if a == b:
            return Verdict(True, "same model number")
        # One store adds a region or packaging suffix (MQ8N3LL/A vs MQ8N3): close, not proof.
        if a.startswith(b) or b.startswith(a):
            # A colour or region suffix on one store's part number (WH1000XM5/B vs WH1000XM5):
            # the same item when both titles carry the shorter number too.
            short = min(a, b, key=len)
            if cw and not co and len(b) > len(a):
                # Their part number carries a suffix (often the colour) and their title names
                # no colour, while the one on screen does: can't tell which colour it is.
                return Verdict(None, f"can't tell which colour {other.model} is")
            if short in model_numbers(want.title) | {a} and short in model_numbers(other.title) | {
                b
            }:
                return Verdict(True, "same model number")
            return Verdict(None, f"model {other.model} is close to {want.model}, not the same")
        if not (mw & mo):
            return Verdict(False, f"a different model ({other.model})")
    if mw and mo:
        shared = mw & mo
        if not shared:
            # Both name a model, and they differ: a different generation or variant.
            return Verdict(False, f"a different model ({sorted(mo)[0]})")
        # A shared number is proof when one title's numbers are all in the other, unless the
        # other also names a rival model of the same family (a "fits XM4 and XM5" accessory).
        # Laptops sharing only a chip number ("RTX4060") aren't proven the same.
        extra = {m for m in mo - mw if any(m[:4] == x[:4] and not m.startswith(x) for x in shared)}
        accessory = (_words(other.title) - _words(want.title)) & {
            "case", "cover", "replacement", "refill", "for", "compatible", "protector", "skin",
        }  # fmt: skip
        suffixed = {m for m in mo - mw if any(m.startswith(x) for x in shared)}
        if cw and not co and suffixed:
            return Verdict(None, f"can't tell which colour {sorted(suffixed)[0]} is")
        if (mw <= mo or mo <= mw) and not extra and not accessory:
            return Verdict(True, "same model number")
        return Verdict(None, "the model numbers only partly match")
    if want.gtin and other.gtin and want.gtin != other.gtin:
        return Verdict(None, "a different barcode, and no model number to check")
    # No identifier to compare: only call it the same item when the words leave no doubt.
    ww, wo = _words(want.title), _words(other.title)
    brand = (want.brand or "").lower()
    if brand and brand not in (other.brand or "").lower() and brand not in wo:
        return Verdict(False, "a different brand")
    if (wo - ww) & VARIANT_WORDS or (ww - wo) & VARIANT_WORDS:
        v = sorted(((wo - ww) | (ww - wo)) & VARIANT_WORDS)[0]
        return Verdict(False, f"a different version ('{v}')")
    core = ww - COLORS
    if core and core <= wo and sw == so and len(core) >= 3:
        return Verdict(None, "same name, but no model number or barcode to confirm it")
    return Verdict(None, "can't confirm it's the same item")


# ---------------------------------------------------------------------------------- lookup


@dataclass
class Checked:
    store: str
    url: str
    outcome: str  # "match", "different", "unsure", "blocked", "unreadable", "out_of_stock",
    # "other_currency"
    note: str = ""
    offer: Offer | None = None


@dataclass
class PriceCheck:
    product: Offer
    best: Offer | None = None  # the cheapest exact match elsewhere, cheaper than here
    savings: float = 0.0
    checked: list[Checked] = field(default_factory=list)
    checked_at: float = field(default_factory=time.time)
    verdict: str = "unverified"  # "cheaper", "best_here", "unverified"

    @property
    def matches(self) -> list[Checked]:
        return [c for c in self.checked if c.outcome == "match"]

    def headline(self) -> str:
        if self.verdict == "cheaper" and self.best:
            return f"{money(self.savings, self.best.currency)} less at {self.best.store}"
        if self.verdict == "best_here":
            return "Best price I found"
        return "Couldn't verify a better price"

    def detail(self) -> str:
        when = time.strftime("%I:%M %p", time.localtime(self.checked_at)).lstrip("0").lower()
        if self.verdict == "cheaper" and self.best:
            b = self.best
            ship = (
                ", free shipping"
                if b.shipping == 0
                else f" + {money(b.shipping, b.currency)} shipping"
                if b.shipping
                else ", shipping not shown"
            )
            return f"{b.store}: {money(b.price or 0, b.currency)}{ship}. Same {self._proof()}. Checked {when}."
        n = len(
            {
                c.store
                for c in self.checked
                if c.outcome in ("match", "different", "unsure", "out_of_stock", "other_currency")
            }
        )
        if self.verdict == "best_here":
            others = ", ".join(sorted({c.store for c in self.matches}))
            return f"Same item at {others} costs the same or more. Checked {when}."
        elsewhere = [c for c in self.checked if c.outcome == "other_currency" and c.offer]
        if elsewhere:
            c = elsewhere[0]
            return (
                f"{c.store} has it for {money(c.offer.price or 0, c.offer.currency)} "
                f"{c.offer.currency}, a different currency, so I can't compare it. Checked {when}."
            )
        unsure = [c for c in self.checked if c.outcome == "unsure"]
        if unsure:
            return f"I found similar listings but couldn't confirm the exact model. Checked {when}."
        if n == 0:
            return f"No other store's page I could read had it. Checked {when}."
        return f"{n} other store{'s' if n != 1 else ''} checked, none had this exact item. Checked {when}."

    def _proof(self) -> str:
        m = next((c for c in self.matches if c.offer is self.best), None)
        return m.note.removeprefix("same ") if m and m.note else "model"

    def summary(self) -> str:
        return f"{self.headline()}. {self.detail()}"

    def public(self) -> dict:
        return {
            "verdict": self.verdict,
            "headline": self.headline(),
            "detail": self.detail(),
            "product": self.product.public(),
            "best": self.best.public() if self.best else None,
            "savings": round(self.savings, 2),
            "checked_at": self.checked_at,
            "checked": [
                {"store": c.store, "url": c.url, "outcome": c.outcome, "note": c.note,
                 "price": c.offer.price if c.offer else None}
                for c in self.checked
            ],
        }  # fmt: skip


def queries(product: Offer) -> list[str]:
    """What to search for: the model number with the brand, then the barcode, then the name."""
    out = []
    models = sorted(model_numbers(product.title))
    model = product.model or (models[0] if models else "")
    brand = product.brand or (product.title.split() or [""])[0]
    if model:
        # The base number: stores write the colour or region suffix (/B, LL/A) differently.
        base = model.split("/")[0] if len(norm_model(model.split("/")[0])) >= 4 else model
        out.append(base if brand.lower() in base.lower() else f"{brand} {base}".strip())
    if product.gtin:
        out.append(product.gtin.lstrip("0"))
    if not out or len(out) < 2:
        name = re.sub(r"\s*\(.*?\)", "", product.title)
        out.append(" ".join(name.split()[:12]) + " price")
    return list(dict.fromkeys(out))


Fetch = Callable[[str], Awaitable[str | None]]
Search = Callable[[str], Awaitable[list]]  # list of hits with .url


def candidate_urls(hits: list, exclude: str, limit: int, brand: str = "") -> list[str]:
    """One product page per store, never the store they're already on."""
    seen, out = {store_key(exclude)} if exclude else set(), []
    for h in hits:
        url = getattr(h, "url", h)
        if not isinstance(url, str) or not url.startswith("http") or not is_store_page(url, brand):
            continue
        key = store_key(url)
        if key in seen:
            continue
        seen.add(key)
        out.append(url)
        if len(out) >= limit:
            break
    return out


async def compare(
    product: Offer,
    *,
    search: Search,
    fetch: Fetch,
    max_stores: int = 6,
    progress: Callable[[str], Awaitable[None]] | None = None,
    min_saving: float = 1.0,
) -> PriceCheck:
    """Look the exact product up at other stores and pick the cheapest exact match."""
    result = PriceCheck(product)
    hits: list = []
    for q in queries(product)[:2]:
        try:
            hits += await search(q)
        except Exception as exc:  # a search engine hiccup: the other query may still work
            log.info("price search %r failed: %s", q, exc)
    brand = product.brand or (product.title.split() or [""])[0]
    urls = candidate_urls(hits, product.url, max_stores, brand)
    if not urls:
        result.verdict = "unverified"
        return result
    gate = asyncio.Semaphore(3)  # a few at a time, and one page per store: polite

    async def look(url: str) -> Checked:
        store = store_name(url)
        async with gate:
            if progress:
                await progress(f"Checking {store}")
            try:
                page = await fetch(url)
            except Exception as exc:
                log.info("price page %s failed: %s", url, exc)
                page = None
        if page is None:
            return Checked(store, url, "unreadable", "the page wouldn't load")
        if looks_blocked(page):
            return Checked(
                store, url, "blocked", "the store asked for a bot check, so I skipped it"
            )
        offer = parse_product_page(page, url)
        if offer is None or offer.price is None:
            return Checked(store, url, "unreadable", "no price on the page")
        verdict = same_item(product, offer)
        if verdict.same is False:
            return Checked(offer.store, url, "different", verdict.why, offer)
        if verdict.same is None:
            return Checked(offer.store, url, "unsure", verdict.why, offer)
        if offer.in_stock is False:
            return Checked(offer.store, url, "out_of_stock", "out of stock", offer)
        return Checked(offer.store, url, "match", verdict.why, offer)

    result.checked = list(await asyncio.gather(*(look(u) for u in urls)))
    result.checked_at = time.time()
    for c in result.checked:  # the same item in another currency isn't comparable
        if c.outcome == "match" and c.offer and c.offer.currency != product.currency:
            c.outcome, c.note = "other_currency", f"priced in {c.offer.currency}"
    matches = [
        c.offer
        for c in result.matches
        if c.offer and c.offer.total is not None and c.offer.currency == product.currency
    ]
    if not matches:
        result.verdict = "unverified"
        return result
    best = min(matches, key=lambda o: o.total or 0)
    here = product.total
    if here is None:
        result.verdict = "unverified"
        return result
    saving = here - (best.total or 0)
    if saving >= max(min_saving, 0.01 * here):
        result.best, result.savings, result.verdict = best, saving, "cheaper"
    else:
        result.verdict = "best_here"
    return result
