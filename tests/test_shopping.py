"""Cheaper elsewhere: price parsing, exact-product matching and the lookup, on saved sample
pages (tests/fixtures/shopping). Nothing here reaches a real store."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from conftest import Recorder
from pyxi import shopping
from pyxi.companion import pricecheck
from pyxi.hub import Hub
from pyxi.shopping import Offer, parse_price, parse_product_page, same_item

PAGES = Path(__file__).parent / "fixtures" / "shopping"

URLS = {
    "https://www.amazon.com/Sony-WH-1000XM5/dp/B09XS7JWHH": "amazon_sony_xm5.html",
    "https://www.bestbuy.com/site/sony-wh-1000xm5/6505727.p?skuId=6505727": "bestbuy_sony_xm5.html",
    "https://www.walmart.com/ip/Sony-WH-1000XM4/411289227": "walmart_sony_xm4.html",
    "https://www.target.com/p/sony-wh-1000xm5-renewed/-/A-88112233": "target_sony_xm5_renewed.html",
    "https://www.newegg.com/p/0TH-000U-00123": "newegg_sony_xm5.html",
    "https://www.costco.com/sony-wh-1000xm5-silver.product.4000212345.html": "costco_sony_xm5_silver.html",
    "https://www.bhphotovideo.com/c/product/1706200-REG/sony_wh1000xm5_b.html": "bhphoto_bot_check.html",
    "https://www.staples.com/sony-wh-1000xm5/product_24512345": "staples_sony_xm5_soldout.html",
    "https://www.rtings.com/headphones/reviews/sony/wh-1000xm5": "review_page.html",
}
AMAZON = next(iter(URLS))


def page(name: str) -> str:
    return (PAGES / name).read_text(encoding="utf-8")


def parsed(url: str) -> Offer:
    offer = parse_product_page(page(URLS[url]), url)
    assert offer is not None
    return offer


# ---------------------------------------------------------------------------------- prices


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("$1,299.99", (1299.99, "USD")),
        ("$24", (24.0, "USD")),
        ("US $12.50", (12.5, "USD")),
        ("CA$ 30", (30.0, "CAD")),
        ("C$49.95", (49.95, "CAD")),
        ("£19.99", (19.99, "GBP")),
        ("1.299,99 €", (1299.99, "EUR")),
        ("24.99 USD", (24.99, "USD")),
        ("Was $399.99 Now $349.99", (349.99, "USD")),
        ("Reg. $59.99 Sale $44.99", (44.99, "USD")),
        ("329.99", (329.99, "USD")),  # a bare JSON-LD price
        ("$399<sup>99</sup>", (399.0, "USD")),
        ("4.5 out of 5 stars", None),
        ("Model 2024, 3 colours", None),
        ("", None),
    ],
)
def test_prices_are_read_the_way_stores_write_them(text, want):
    assert parse_price(text) == want


def test_a_dollar_sign_on_a_canadian_store_is_canadian_dollars():
    assert parse_price("$34.99", "CAD") == (34.99, "CAD")
    assert shopping.site_currency("https://www.bestbuy.ca/en-ca/product/x/1") == "CAD"


def test_money_reads_cleanly():
    assert shopping.money(24.0) == "$24"
    assert shopping.money(70.5) == "$70.50"
    assert shopping.money(1299.99, "GBP") == "£1,299.99"


# ---------------------------------------------------------------------------------- pages


def test_json_ld_gives_the_exact_product():
    o = parsed("https://www.bestbuy.com/site/sony-wh-1000xm5/6505727.p?skuId=6505727")
    assert o.store == "Best Buy"
    assert (o.price, o.currency) == (329.99, "USD")
    assert o.model == "WH1000XM5/B"
    assert o.gtin == "00027242923782"
    assert o.brand == "Sony"
    assert o.in_stock is True and o.condition == "new"
    assert o.shipping == 0.0  # "Free shipping on this item."
    assert o.source == "json-ld"


def test_a_page_without_structured_data_still_gives_price_model_and_delivery():
    o = parsed(AMAZON)
    assert o.store == "Amazon"
    assert o.price == 399.99
    assert o.model == "WH1000XM5/B"
    assert o.shipping == 0.0
    assert "WH-1000XM5" in o.title


def test_the_items_price_not_a_protection_plans():
    o = parse_product_page(page("amazon_ca_with_addon.html"), "https://www.amazon.ca/dp/B09XS7JWHH")
    assert o is not None
    assert o.title == "Sony WH-1000XM5 Premium Noise Cancelling Wireless Headphones, Black"
    assert (o.price, o.currency) == (348.0, "CAD")


def test_open_graph_product_tags_and_paid_shipping():
    o = parsed("https://www.newegg.com/p/0TH-000U-00123")
    assert (o.store, o.price, o.brand) == ("Newegg", 339.0, "Sony")
    assert o.shipping == 4.99
    assert o.total == pytest.approx(343.99)


def test_graph_lists_refurbished_and_sold_out():
    renewed = parsed("https://www.target.com/p/sony-wh-1000xm5-renewed/-/A-88112233")
    assert renewed.condition == "refurbished" and renewed.price == 279.99
    sold = parsed("https://www.staples.com/sony-wh-1000xm5/product_24512345")
    assert sold.in_stock is False


def test_sloppy_json_and_canadian_prices_with_shipping_details():
    o = parse_product_page(
        page("bestbuy_ca_sony_xm5.html"), "https://www.bestbuy.ca/en-ca/product/sony/16297495"
    )
    assert o is not None
    assert (o.price, o.currency, o.shipping) == (399.99, "CAD", 0.0)
    assert o.store == "Best Buy"


def test_bot_checks_are_recognised_and_never_parsed():
    html = page("bhphoto_bot_check.html")
    assert shopping.looks_blocked(html)
    assert parse_product_page(html, "https://www.bhphotovideo.com/c/product/1") is None
    assert not shopping.looks_blocked(page("bestbuy_sony_xm5.html"))


def test_which_addresses_are_store_product_pages():
    assert shopping.looks_like_product_url(AMAZON)
    assert shopping.looks_like_product_url(
        "https://www.bestbuy.com/site/sony/6505727.p?skuId=6505727"
    )
    assert shopping.looks_like_product_url("https://www.walmart.com/ip/Sony/411289227")
    assert not shopping.looks_like_product_url("https://www.amazon.com/s?k=headphones")
    assert not shopping.looks_like_product_url("https://www.ebay.com/itm/1234567890")
    assert not shopping.is_store_page("https://www.rtings.com/headphones/reviews/sony/wh-1000xm5")
    assert shopping.store_key("https://smile.amazon.co.uk/dp/B0") == "amazon"
    # Review sites publish product prices too (affiliate ones): never a store. The brand's own is.
    assert not shopping.is_store_page("https://www.soundguys.com/product/sony-wh-1000xm5/")
    assert shopping.is_store_page("https://electronics.sony.com/audio/p/wh1000xm5-b", "Sony")
    assert not shopping.is_store_page("https://electronics.sony.com/audio/p/wh1000xm5-b", "Bose")


# ---------------------------------------------------------------------------------- matching


def test_model_numbers_and_sizes_in_titles():
    assert shopping.model_numbers("Sony WH-1000XM5/B Headphones, USB-C, 2-Pack, 256GB, 5G") == {
        "WH1000XM5B",
        "WH1000XM5",
    }
    assert shopping.model_numbers("Apple iPhone 17 Pro 256GB") == set()
    assert shopping.specs('Samsung 65" QN90D 4K TV') == {"screen": 65.0}
    assert shopping.specs("Lenovo Yoga 2-in-1 14 inch, 1TB SSD, 5G") == {
        "screen": 14.0,
        "storage": 1000.0,
    }
    assert shopping.specs("Tide Pods, 81 Count") == {"pack": 81.0}
    assert shopping.specs("Stanley Quencher 40 oz") == {"weight": pytest.approx(1134.0, abs=1)}


def test_the_same_model_from_two_stores_matches():
    amazon, bestbuy = parsed(AMAZON), parsed(list(URLS)[1])
    v = same_item(amazon, bestbuy)
    assert v.same is True and "model" in v.why


def test_the_barcode_proves_it():
    a = Offer("Some Blender", gtin="027242923782")
    b = Offer("Blender, Black", gtin=shopping.norm_gtin("0027242923782"))
    a.gtin = shopping.norm_gtin(a.gtin)
    assert same_item(a, b).same is True


@pytest.mark.parametrize(
    ("other", "why"),
    [
        ("Sony WH-1000XM4 Wireless Headphones, Black", "model"),  # last year's model
        ("Sony WH-1000XM5 Wireless Headphones, Silver", "colour"),
        ("Apple iPhone 17 Pro 256GB Black", "version"),
        ("Apple iPhone 17 128GB Black", "storage"),
        ("Apple iPhone 17 256GB Black 2-Pack", "pack"),
    ],
)
def test_lookalikes_are_different_items(other, why):
    want = (
        Offer("Apple iPhone 17 256GB Black", brand="Apple")
        if "iPhone" in other
        else Offer("Sony WH-1000XM5 Wireless Headphones, Black", brand="Sony")
    )
    v = same_item(want, Offer(other))
    assert v.same is False, v
    assert why in v.why


def test_refurbished_is_not_new():
    want = parsed(AMAZON)
    v = same_item(want, parsed("https://www.target.com/p/sony-wh-1000xm5-renewed/-/A-88112233"))
    assert v.same is False and "refurbished" in v.why


def test_when_nothing_proves_it_pyxi_says_it_cannot_tell():
    want = Offer("Stanley Quencher H2.0 FlowState Tumbler 40 oz Rose Quartz", brand="Stanley")
    v = same_item(
        want, Offer("Stanley Quencher H2.0 FlowState Tumbler 40 oz Rose Quartz", brand="Stanley")
    )
    assert v.same is None
    accessory = same_item(parsed(AMAZON), Offer("Hard Case for Sony WH-1000XM5 Headphones"))
    assert accessory.same is not True
    # Sony's own page sells every colour under WH1000XM5/L, /B, /S: which one is this?
    sony = same_item(
        Offer("Sony WH-1000XM5 Headphones Black", model="WH-1000XM5"),
        Offer("WH-1000XM5 Wireless Noise Cancelling Headphones", model="WH1000XM5/L"),
    )
    assert sony.same is None and "colour" in sony.why
    sony_ca = same_item(
        Offer("Sony WH-1000XM5 Headphones Black", model="WH-1000XM5"), Offer("WH1000XM5/L")
    )
    assert sony_ca.same is None
    laptops = same_item(
        Offer("HP Victus 15 i7-14700HX RTX4060 15-fa1234"),
        Offer("HP Omen 16 i7-14700HX RTX4060 16-ah0097"),
    )
    assert laptops.same is not True  # sharing a chip isn't the same laptop


# ---------------------------------------------------------------------------------- lookup


def fake_web(urls: list[str]):
    asked: list[str] = []
    fetched: list[str] = []

    async def search(q: str):
        asked.append(q)
        return [SimpleNamespace(url=u) for u in urls]

    async def fetch(url: str):
        fetched.append(url)
        return page(URLS[url]) if url in URLS else None

    return search, fetch, asked, fetched


async def test_the_cheapest_exact_match_wins_and_lookalikes_are_ignored():
    search, fetch, asked, fetched = fake_web(list(URLS))
    product = parsed(AMAZON)
    check = await shopping.compare(product, search=search, fetch=fetch, max_stores=8)
    assert asked[0] == "Sony WH1000XM5"  # the model number, not the marketing name
    assert AMAZON not in fetched  # never the store they're on
    assert not any("rtings" in u for u in fetched)  # reviews aren't stores
    assert check.verdict == "cheaper"
    assert check.best is not None and check.best.store == "Best Buy"
    assert check.savings == pytest.approx(70.0)
    assert check.headline() == "$70 less at Best Buy"
    detail = check.detail()
    assert "Best Buy: $329.99, free shipping" in detail and "Checked" in detail
    outcomes = {c.store: c.outcome for c in check.checked}
    assert outcomes["Walmart"] == "different"  # cheaper, but the XM4
    assert outcomes["Target"] == "different"  # refurbished
    assert outcomes["Costco"] == "different"  # silver
    assert outcomes["B&H Photo"] == "blocked"  # a bot check: skipped, not solved
    assert outcomes["Staples"] == "out_of_stock"
    assert outcomes["Newegg"] == "match"
    assert "—" not in detail and "—" not in check.headline()


async def test_no_exact_match_means_a_plain_cannot_verify():
    urls = [u for u in URLS if "walmart" in u or "costco" in u or "bhphoto" in u]
    search, fetch, *_ = fake_web(urls)
    check = await shopping.compare(parsed(AMAZON), search=search, fetch=fetch)
    assert check.verdict == "unverified" and check.best is None
    assert check.headline() == "Couldn't verify a better price"
    assert "none had this exact item" in check.detail()


async def test_already_the_best_price():
    product = parsed(AMAZON)
    product.price = 319.0
    search, fetch, *_ = fake_web([u for u in URLS if "bestbuy" in u or "newegg" in u])
    check = await shopping.compare(product, search=search, fetch=fetch)
    assert check.verdict == "best_here"
    assert check.headline() == "Best price I found"


async def test_one_page_per_store_and_a_cap_on_pages():
    many = [f"https://www.bestbuy.com/site/x/{i}.p?skuId={i}" for i in range(5)] + list(URLS)
    search, fetch, _, fetched = fake_web(many)
    await shopping.compare(parsed(AMAZON), search=search, fetch=fetch, max_stores=3)
    assert len(fetched) == 3
    assert sum("bestbuy" in u for u in fetched) == 1


async def test_reader_skips_sign_in_pages_and_needs_no_browser_when_the_page_is_readable():
    def handler(request: httpx.Request) -> httpx.Response:
        if "login" in request.url.path:
            return httpx.Response(200, text="<html>Sign in</html>")
        return httpx.Response(
            200, text=page("bestbuy_sony_xm5.html"), headers={"content-type": "text/html"}
        )

    async with pricecheck.Reader(None, transport=httpx.MockTransport(handler)) as reader:
        html = await reader.fetch("https://www.bestbuy.com/site/x/6505727.p")
        assert html and "WH1000XM5" in html
        assert await reader.fetch("https://www.example.com/account/login") is None


# ---------------------------------------------------------------------------------- Pyxi side


class FakeJobs:
    def __init__(self) -> None:
        self.settled: list[tuple[bool, str, object]] = []

    async def track(self, agent_name, task, role="errand"):
        self.task = (agent_name, task, role)
        return SimpleNamespace(id="job_1")

    async def settle(self, job, *, ok, summary, data=None, cost=0.0):
        self.settled.append((ok, summary, data))


def fake_brain(settings, ctx=None):
    hub = Hub()
    rec = Recorder(hub)
    spawned: list = []
    brain = SimpleNamespace(
        hub=hub,
        jobs=FakeJobs(),
        settings=settings,
        history=[],
        _ctx=ctx,
        screen=SimpleNamespace(capture=None),
        _spawn=spawned.append,
    )
    return brain, rec, spawned


async def test_a_check_pins_one_tag_by_the_price(settings):
    from conftest import fake_context
    from pyxi.screen.ocr import TextLine

    ctx = fake_context("chrome.exe", "Amazon.com: Sony WH-1000XM5 - Google Chrome")
    ctx.text = [TextLine("$399.99", (900, 300, 90, 24), [("$399.99", (900, 300, 90, 24))], 50)]
    brain, rec, _ = fake_brain(settings, ctx)
    search, fetch, *_ = fake_web(list(URLS))
    reader = SimpleNamespace(fetch=fetch)
    check = await pricecheck.run_check(
        brain, {"name": "Sony WH-1000XM5", "price": "$399.99"}, AMAZON, reader=reader, search=search
    )
    assert check is not None and check.verdict == "cheaper"
    assert brain.jobs.task == ("Scout", "Price check: Sony WH-1000XM5", "shopping")
    tags = rec.of("price_tag")
    assert len(tags) == 1
    tag = tags[0]["tag"]
    assert tag["headline"] == "$70 less at Best Buy"
    assert tag["url"].startswith("https://www.bestbuy.com/")
    assert tag["at"] == [900 + 90 + 14, 312]  # just right of the price on screen
    ok, summary, data = brain.jobs.settled[-1]
    assert ok and summary.startswith("$70 less at Best Buy") and data["best"]["store"] == "Best Buy"
    assert any(e["data"]["message"] == "Checking Best Buy" for e in rec.of("job_event"))
    assert "Best Buy" in brain.history[-1][1]


async def test_the_screen_fills_in_when_the_store_page_wont_load(settings):
    brain, _rec, _ = fake_brain(settings)

    search, fetch, *_ = fake_web(list(URLS))

    async def fetch_others(url):
        return None if url == AMAZON else await fetch(url)

    reader = SimpleNamespace(fetch=fetch_others)
    brain.screen.capture = lambda **k: _none()
    check = await pricecheck.run_check(
        brain,
        {
            "name": "Sony WH-1000XM5 Wireless Headphones",
            "brand": "Sony",
            "model": "WH-1000XM5",
            "size": "Black",
            "price": "$399.99",
        },
        AMAZON,
        reader=reader,
        search=search,
    )
    assert check is not None and check.product.source == "screen"
    assert check.verdict == "cheaper" and check.best.store == "Best Buy"


async def _none():
    return None


async def test_the_tool_starts_in_the_background_and_reads_the_address_bar(settings, monkeypatch):
    brain, _, spawned = fake_brain(settings)
    monkeypatch.setattr(pricecheck, "browser_url", lambda hwnd=None: AMAZON)
    note = await pricecheck.handle(brain, "check_prices", {"name": "Sony WH-1000XM5"})
    assert note.startswith("Started") and "one short line" in note
    assert len(spawned) == 1
    spawned[0].close()
    assert "say which product" in await pricecheck.handle(brain, "check_prices", {})


async def test_auto_checks_are_off_by_default_and_quiet_when_on(settings, monkeypatch):
    brain, _rec, _ = fake_brain(settings)
    calls = []

    async def fake_run(b, hint, url, *, auto=False, **kw):
        calls.append((url, auto))

    monkeypatch.setattr(pricecheck, "run_check", fake_run)
    monkeypatch.setattr(pricecheck, "browser_url", lambda hwnd=None: AMAZON)
    assert pricecheck.load(settings.data_dir) == {"auto": False}
    await pricecheck.on_window(brain, "chrome")
    assert calls == []
    pricecheck.save(settings.data_dir, {"auto": True})
    await pricecheck.on_window(brain, "chrome")
    await pricecheck.on_window(brain, "chrome")  # the same page again: not re-checked
    assert calls == [(AMAZON, True)]
    await pricecheck.on_window(brain, "notepad")  # not a browser
    assert len(calls) == 1
    monkeypatch.setattr(
        pricecheck, "browser_url", lambda hwnd=None: "https://www.amazon.com/s?k=tv"
    )
    brain._price_watch["seen"].clear()
    await pricecheck.on_window(brain, "chrome")  # a search page, not a product
    assert len(calls) == 1


async def test_auto_mode_stays_silent_unless_there_is_a_saving(settings):
    brain, rec, _ = fake_brain(settings)
    search, fetch, *_ = fake_web([u for u in URLS if "walmart" in u])
    brain.screen.capture = lambda **k: _none()
    check = await pricecheck.run_check(
        brain, {"name": ""}, AMAZON, auto=True, reader=SimpleNamespace(fetch=fetch), search=search
    )
    assert check is not None and check.verdict == "unverified"
    assert rec.of("price_tag") == []


async def test_the_tag_goes_when_the_page_changes(settings):
    brain, rec, _ = fake_brain(settings)
    pricecheck._state(brain)["tag_up"] = True
    await pricecheck.on_window(brain, "pyxi")  # clicking the tag itself
    assert rec.of("price_tag") == []
    await pricecheck.on_window(brain, "chrome")
    assert [e["tag"] for e in rec.of("price_tag")] == [None]


def test_the_check_is_offered_to_the_model():
    from pyxi.companion.tools import COMPANION_TOOLS

    tool = next(t for t in COMPANION_TOOLS if t.name == "check_prices")
    assert "exact" in tool.description.lower()


async def test_the_same_item_in_another_currency_is_named_but_not_compared():
    url = "https://www.bestbuy.ca/en-ca/product/sony/16297495"
    URLS[url] = "bestbuy_ca_sony_xm5.html"
    try:
        search, fetch, *_ = fake_web([url])
        check = await shopping.compare(parsed(AMAZON), search=search, fetch=fetch)
    finally:
        del URLS[url]
    assert check.verdict == "unverified"
    assert check.checked[0].outcome == "other_currency"
    assert "Best Buy has it for $399.99 CAD, a different currency" in check.detail()
