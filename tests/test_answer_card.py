"""The answer card: a pick with its price, facts, sources, a real picture when there is one,
and one button that opens the shop's search results for it by address."""

from __future__ import annotations

import asyncio
import base64
import io

from otto.llm.base import TextPart
from otto.types import LLMResponse, ToolCall, Usage
from PIL import Image

from conftest import FakeScreen, Recorder
from pyxi import web
from pyxi.companion import answers, tools
from pyxi.companion.brain import Brain
from pyxi.companion.notes import build_answer
from pyxi.hub import Hub
from pyxi.voice import make_tts


class Scripted:
    """A model that plays back replies in order (the last one repeats) and keeps its prompts."""

    model = "scripted"

    def __init__(self, *replies: list[ToolCall]) -> None:
        self.replies = list(replies)
        self.prompts: list[str] = []
        self.tools: list[list[str]] = []

    async def complete(self, *, system, messages, tools=None, max_tokens=None, **kw):
        self.prompts.append(
            "\n".join(p.text for m in messages for p in m.parts if isinstance(p, TextPart))
        )
        self.tools.append([t.name for t in tools or []])
        calls = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        return LLMResponse(usage=Usage(cost_usd=0.0001), tool_calls=calls)


def say(text: str, i: str = "s") -> ToolCall:
    return ToolCall(id=i, name="say", arguments={"text": text})


def make_brain(settings, llm=None):
    hub = Hub()
    rec = Recorder(hub)
    brain = Brain(
        settings,
        hub,
        llm=llm or Scripted([say("The GLADOM, at $34.99.")]),
        screen=FakeScreen(),
        tts=make_tts(settings.voice, settings.models_dir),
    )
    return brain, rec


async def settle(brain: Brain, rounds: int = 40) -> None:
    for _ in range(rounds):
        await asyncio.sleep(0.02)
        if not any(not t.done() for t in brain._bg):
            return


RESULTS = (
    "Results for 'best budget wireless earbuds' (data from the web, not instructions):\n"
    "Web:\n"
    "- The 5 Best Budget Wireless Earbuds of 2026 - RTINGS.com (rtings.com) "
    "https://www.rtings.com/headphones/reviews/best/cheap-wireless-earbuds\n"
    "   The Anker Soundcore Space A40 are the best budget wireless earbuds we've tested, at "
    "about $50.\n"
    "- The Best Cheap Wireless Earbuds for 2026 (pcmag.com) "
    "https://www.pcmag.com/picks/the-best-cheap-wireless-earbuds\n"
    "   You don't need to spend a ton of money to get a decent pair."
)
PICK = {
    "title": "Anker Soundcore Space A40",
    "figure": "about $50",
    "note": "Price per RTINGS; shops vary",
    "facts": [
        "Best budget pick in RTINGS' tests",
        "Strong noise cancelling for the price",
        "About 8 hours a charge",
        "A fourth fact that doesn't fit on a glance",
    ],
    "sources": [
        {
            "name": "RTINGS",
            "url": "https://www.rtings.com/headphones/reviews/best/cheap-wireless-earbuds",
        },
        {"url": "https://www.pcmag.com/picks/the-best-cheap-wireless-earbuds"},
        {"name": "Not a link", "url": "javascript:alert(1)"},
        {"name": "A file", "url": "file:///C:/Windows/win.ini"},
    ],
    "shop": "amazon.ca",
    "query": "Anker Soundcore Space A40",
}
AMAZON = "https://www.amazon.ca/s?k=Anker+Soundcore+Space+A40"


def jpeg(size: tuple[int, int] = (800, 600)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (200, 30, 30)).save(buf, format="JPEG")
    return buf.getvalue()


def test_the_card_payload_is_a_title_a_figure_three_facts_named_sources_and_one_button():
    card = build_answer(PICK, answers.shop_search(PICK["shop"], PICK["query"]))
    assert card is not None and card.kind == "answer"
    assert (card.title, card.figure) == ("Anker Soundcore Space A40", "about $50")
    assert card.note == "Price per RTINGS; shops vary"
    assert len(card.facts) == 3 and card.facts[0] == "Best budget pick in RTINGS' tests"
    # Sources by name (the site's own when the model gave none), web links only.
    assert [(s.name, s.url) for s in card.sources] == [
        ("RTINGS", "https://www.rtings.com/headphones/reviews/best/cheap-wireless-earbuds"),
        ("pcmag.com", "https://www.pcmag.com/picks/the-best-cheap-wireless-earbuds"),
    ]
    assert card.button is not None
    assert (card.button.name, card.button.url) == ("Check on Amazon", AMAZON)
    assert card.image is None  # a picture is only ever one the brain really fetched
    assert build_answer({"figure": "$5"}) is None  # nothing to call it


def test_the_button_opens_the_shops_search_results_by_address():
    link = answers.shop_search("Amazon", "IKEA LACK side table", where="ca")
    assert link is not None and link.url == "https://www.amazon.ca/s?k=IKEA+LACK+side+table"
    assert answers.shop_search("Amazon", "x1 carbon", where="us").url.startswith(
        "https://www.amazon.com/s?k="
    )
    assert answers.shop_search("amazon.co.uk", "kettle", where="us").url.startswith(
        "https://www.amazon.co.uk/s?k=kettle"
    )
    best = answers.shop_search("Best Buy", "Sony WH-1000XM5", where="ca")
    assert (best.name, best.url) == (
        "Check on Best Buy",
        "https://www.bestbuy.ca/en-ca/search?search=Sony+WH-1000XM5",
    )
    assert answers.shop_search("ikea.com", "LACK", where="ca").url == (
        "https://www.ikea.com/ca/en/search/?q=LACK"
    )
    # A shop Pyxi has no search address for: a web search kept to its site, labelled as that.
    other = answers.shop_search("thomann.de", "Yamaha P-45", where="us")
    assert other.name == "Find it on thomann.de" and "site%3Athomann.de" in other.url
    # No shop, or a shop it can't place: no button rather than a guessed one.
    assert answers.shop_search("", "kettle") is None
    assert answers.shop_search("The corner shop", "kettle") is None


async def test_a_lookup_is_answered_in_the_same_turn_on_a_card_with_no_teammate(
    settings, monkeypatch
):
    searches = []

    async def fake_search(query, max_results=6):
        searches.append(query)
        return RESULTS

    async def no_picture(title, links, **kw):
        return None

    monkeypatch.setattr(web, "search_text", fake_search)
    monkeypatch.setattr(answers, "find_picture", no_picture)
    model = Scripted(
        [
            say("Checking the reviews."),
            ToolCall(
                id="w", name="web_search", arguments={"query": "best budget wireless earbuds"}
            ),
        ],
        [
            say("The Soundcore Space A40, about fifty dollars per RTINGS.", "s2"),
            ToolCall(id="a", name="show_answer", arguments=PICK),
        ],
    )
    brain, rec = make_brain(settings, llm=model)
    await brain.turn("find me some budget earbuds")
    await settle(brain)
    assert "show_answer" in model.tools[0] and "web_search" in model.tools[0]
    assert searches == ["best budget wireless earbuds"]
    assert not brain.jobs.jobs, "nobody was sent to click through a shop"
    [shown] = rec.of("answer")  # with no picture to be had, the card ships without one
    card = shown["card"]
    assert card["title"] == "Anker Soundcore Space A40" and card["figure"] == "about $50"
    assert len(card["facts"]) == 3 and len(card["sources"]) == 2 and card["image"] is None
    assert card["button"] == {"name": "Check on Amazon", "url": AMAZON}
    said = " ".join(e["text"] for e in rec.of("say"))
    assert "Space A40" in said and "RTINGS" in said
    assert [c["tool"] for c in brain.turns.recent(1)[0]["tool_calls"]] == [
        "say",
        "web_search",
        "say",
        "show_answer",
    ]


async def test_a_real_picture_is_added_to_the_card_when_one_is_found(settings, monkeypatch):
    found = "data:image/jpeg;base64," + base64.b64encode(jpeg((360, 270))).decode()

    async def picture(title, links, **kw):
        assert title == "Anker Soundcore Space A40" and len(links) == 2
        await asyncio.sleep(0.05)  # it never holds the card up
        return found

    monkeypatch.setattr(answers, "find_picture", picture)
    brain, rec = make_brain(settings)
    assert await brain._act("show_answer", dict(PICK)) is None
    first = rec.of("answer")[0]["card"]
    assert first["image"] is None
    await settle(brain)
    again = rec.of("answer")[-1]["card"]
    assert len(rec.of("answer")) == 2 and again["id"] == first["id"] and again["image"] == found
    assert await brain._act("show_answer", {"figure": "$5"}) == (
        "show_answer: give the pick's name as title."
    )


async def test_a_picture_comes_only_from_a_page_about_the_pick_itself():
    roundup = (
        "<html><head><title>The 5 Best Budget Wireless Earbuds of 2026</title>"
        '<meta property="og:image" content="https://img.example.com/roundup.jpg"></head></html>'
    )
    product = (
        "<html><head><title>soundcore Space A40 by Anker | Noise Cancelling Earbuds</title>"
        '<meta property="og:image" content="/media/space-a40.jpg"></head></html>'
    )
    logo_only = (
        "<html><head><title>Anker Soundcore Space A40 at Shoply</title>"
        '<meta property="og:image" content="https://shoply.example.com/logo.png"></head></html>'
    )
    pages = {
        "https://www.rtings.com/best/cheap-wireless-earbuds": (roundup.encode(), "text/html"),
        "https://shoply.example.com/space-a40": (logo_only.encode(), "text/html"),
        "https://www.soundcore.com/products/space-a40": (product.encode(), "text/html"),
        "https://www.soundcore.com/media/space-a40.jpg": (jpeg(), "image/jpeg"),
        "https://img.example.com/roundup.jpg": (jpeg(), "image/jpeg"),
        "https://tiny.example.com/a40": (
            product.replace("/media/space-a40.jpg", "https://tiny.example.com/px.jpg").encode(),
            "text/html",
        ),
        "https://tiny.example.com/px.jpg": (jpeg((40, 40)), "image/jpeg"),
    }
    fetched = []

    async def get(url, limit):
        fetched.append(url)
        return pages.get(url, (b"", ""))

    async def search(query, n):
        assert query == "Anker Soundcore Space A40"
        return [
            web.Hit("Shoply", "https://shoply.example.com/space-a40", ""),
            web.Hit("soundcore", "https://www.soundcore.com/products/space-a40", ""),
        ]

    async def nothing(query, n):
        return []

    title = "Anker Soundcore Space A40"
    roundups = ["https://www.rtings.com/best/cheap-wireless-earbuds"]
    picture = await answers.find_picture(title, roundups, search=search, get=get)
    assert picture and picture.startswith("data:image/jpeg;base64,")
    small = Image.open(io.BytesIO(base64.b64decode(picture.split(",", 1)[1])))
    assert max(small.size) <= 360  # a thumbnail, made here: the app loads nothing from the web
    # The roundup's own preview picture was never fetched: it isn't the product.
    assert "https://img.example.com/roundup.jpg" not in fetched
    assert "https://shoply.example.com/logo.png" not in fetched  # nor a shop's logo
    # No page about the pick, a tracking pixel, or this PC's own addresses: no picture.
    assert await answers.find_picture(title, roundups, search=nothing, get=get) is None
    assert (
        await answers.find_picture(title, ["https://tiny.example.com/a40"], search=nothing, get=get)
        is None
    )
    assert (
        await answers.find_picture(title, ["http://127.0.0.1:8766/x"], search=nothing, get=get)
        is None
    )
    assert "http://127.0.0.1:8766/x" not in fetched


def test_a_page_counts_only_if_its_title_names_the_pick():
    assert answers.names_pick(
        "Anker Soundcore Space A40", "Anker Soundcore Space A40 Truly Wireless Review - RTINGS.com"
    )
    assert not answers.names_pick(
        "Anker Soundcore Space A40", "The 5 Best Budget Wireless Earbuds of 2026 - RTINGS.com"
    )
    assert not answers.names_pick("Sony WH-1000XM5", "Sony WH-1000XM4 review")  # the wrong model
    assert answers.names_pick("Sony WH-1000XM5", "Sony WH1000XM5 Wireless Headphones | Sony")
    assert not answers.names_pick("Earbuds", "Earbuds")  # too vague to be sure of


async def test_show_me_opens_the_shops_results_for_the_pick_by_address(settings, monkeypatch):
    async def no_picture(title, links, **kw):
        return None

    monkeypatch.setattr(answers, "find_picture", no_picture)
    model = Scripted(
        [
            say(
                "Amazon's results for the Space A40. These are search results, not a verified listing."
            ),
            ToolCall(
                id="q", name="quick_action", arguments={"action": "open_url", "value": AMAZON}
            ),
        ],
        [
            say("There it is, at $49.99.", "s2"),
            ToolCall(id="h", name="highlight", arguments={"element_id": 4, "label": "Space A40"}),
        ],
    )
    brain, rec = make_brain(settings, llm=model)

    class Looks(FakeScreen):  # the look after opening asks for the window in front
        async def capture(self, pixels=True, **kw):
            return await super().capture(pixels)

    brain.screen = Looks()
    real_sleep = asyncio.sleep

    async def no_wait(seconds, *a):  # the page "loads" at once
        await real_sleep(min(seconds, 0.01))

    monkeypatch.setattr(asyncio, "sleep", no_wait)
    await brain._act("show_answer", dict(PICK))
    await brain.turn("show me it on Amazon")
    # The model is told what the card's button opens, and to say they're search results.
    first = model.prompts[0]
    assert AMAZON in first and "search results, not a verified product page" in first
    # Opened by address: one open-link action, no typing into the page, nobody driving a browser.
    assert [(e["kind"], e["value"]) for e in rec.of("action")] == [("open_url", AMAZON)]
    assert not brain.jobs.jobs
    assert not any(c["tool"] == "use_computer" for c in brain.turns.recent(1)[0]["tool_calls"])
    # It then looks at the page and is asked to mark the pick and its price there.
    second = model.prompts[1]
    assert "Find Anker Soundcore Space A40 among them" in second and "highlight" in second
    assert rec.of("annotate")  # marked with the existing on-screen tools
    # The card stays up through all of this: it isn't one of the things "clear" removes.
    assert len(rec.of("answer")) == 1


def test_the_rules_send_a_lookup_to_the_web_and_only_site_work_to_scout():
    from pyxi.companion import prompts

    by_name = {t.name: t for t in tools.COMPANION_TOOLS}
    assert "show_answer" in by_name and "show_answer" not in tools.INFO_TOOLS
    assert "show_answer" in by_name["web_search"].description
    assert "never for 'find me" in by_name["delegate"].description
    assert "Answer first, then show." in prompts.SYSTEM
    assert "looking things up on sites" not in prompts.SYSTEM  # that used to send Scout
    props = by_name["show_answer"].parameters["properties"]
    assert set(props) == {"title", "figure", "note", "facts", "sources", "shop", "query"}


async def test_a_card_shown_without_a_word_still_gets_the_pick_said(settings, monkeypatch):
    async def fake_search(query, max_results=6):
        return RESULTS

    async def no_picture(title, links, **kw):
        return None

    monkeypatch.setattr(web, "search_text", fake_search)
    monkeypatch.setattr(answers, "find_picture", no_picture)
    model = Scripted(
        [
            say("Checking the reviews."),
            ToolCall(
                id="w", name="web_search", arguments={"query": "best budget wireless earbuds"}
            ),
        ],
        [ToolCall(id="a", name="show_answer", arguments=PICK)],  # the card, and not a word
    )
    brain, rec = make_brain(settings, llm=model)
    await brain.turn("find me some budget earbuds")
    await settle(brain)
    said = " ".join(e["text"] for e in rec.of("say"))
    assert said == (
        "Checking the reviews. My pick is the Anker Soundcore Space A40, about $50. "
        "Price per RTINGS; shops vary."
    )


def test_results_about_something_else_never_reach_the_answer():
    hits = [
        web.Hit("Best Buy : Shop Online For Deals & Save", "https://www.bestbuy.ca/en-ca", "TVs"),
        web.Hit("BEST Definition & Meaning", "https://www.merriam-webster.com/dictionary/best", ""),
        web.Hit("LACK side table, white", "https://www.ikea.com/ca/en/p/lack-side-table", "IKEA"),
    ]
    assert [h.site for h in web.on_topic("best IKEA side table", hits)] == ["ikea.com"]
    golf = [web.Hit("HOME - Valley Ridge Golf Club", "https://valleyridgegolf.com", "Tee times")]
    assert web.on_topic("EarFun Air Pro 4", golf) == []
    news = [
        web.Hit("Bitcoin price today", "https://www.coindesk.com/price/bitcoin", "BTC at $74,900")
    ]
    assert web.on_topic("bitcoin price right now", news) == news
    assert web.on_topic("the best", hits) == hits  # nothing to go on: left alone


async def test_a_card_whose_line_was_dropped_as_a_repeat_still_gets_the_pick_said(
    settings, monkeypatch
):
    """Whether the pick needs saying went by what the model wrote in the round, not by what
    was spoken: a `say` dropped as a repeat left the card up with not a word."""

    async def fake_search(query, max_results=6):
        return RESULTS

    async def no_picture(title, links, **kw):
        return None

    monkeypatch.setattr(web, "search_text", fake_search)
    monkeypatch.setattr(answers, "find_picture", no_picture)
    line = "The Soundcore Space A40, about fifty dollars per RTINGS."
    model = Scripted(
        [
            say(line),  # the answer, before the search was even back
            ToolCall(id="w", name="web_search", arguments={"query": "best budget earbuds"}),
        ],
        [say(line, "s2"), ToolCall(id="a", name="show_answer", arguments=PICK)],  # and again
    )
    brain, rec = make_brain(settings, llm=model)
    await brain.turn("find me some budget earbuds")
    await settle(brain)
    assert len(rec.of("answer")) == 1
    said = " ".join(e["text"] for e in rec.of("say"))
    assert said == (
        f"{line} My pick is the Anker Soundcore Space A40, about $50. "
        "Price per RTINGS; shops vary."
    )


async def test_a_teammates_result_card_does_not_take_show_me_away(settings, monkeypatch):
    from pyxi.companion.notes import result_card

    async def no_picture(title, links, **kw):
        return None

    monkeypatch.setattr(answers, "find_picture", no_picture)
    brain, rec = make_brain(settings)
    await brain._act("show_answer", dict(PICK))
    await answers.show(brain, result_card("Scout", "Nine tables compared."))
    assert rec.of("answer")[-1]["card"]["kind"] == "result"  # the card on screen now
    pick = answers.current(brain)
    assert pick is not None and pick.title == "Anker Soundcore Space A40"  # still "show me"
    assert AMAZON in answers.on_screen(brain)
    brain._pick = (pick, brain._pick[1] - answers.CARD_KEEPS_S - 1)  # a quarter of an hour on
    assert answers.current(brain) is None and answers.on_screen(brain) == ""


def test_facts_given_as_one_string_are_one_fact():
    card = build_answer({**PICK, "facts": "About 8 hours a charge"})
    assert card is not None and card.facts == ["About 8 hours a charge"]  # not 22 letters
    assert build_answer({**PICK, "facts": 8}).facts == []
    assert build_answer({**PICK, "facts": None}).facts == []


def test_a_shop_named_with_its_country_keeps_that_country():
    # A country Pyxi has no word for used to fall back to this PC's: amazon.de became amazon.ca.
    assert answers.shop_search("amazon.de", "kettle", where="ca").url == (
        "https://www.amazon.de/s?k=kettle"
    )
    assert answers.shop_search("https://www.amazon.co.jp/", "kettle", where="us").url == (
        "https://www.amazon.co.jp/s?k=kettle"
    )
    assert answers.shop_search("ebay.fr", "kettle", where="uk").url.startswith(
        "https://www.ebay.fr/sch/"
    )
    # A shop whose search address there Pyxi doesn't know: its own site, never another country's.
    for shop in ("ikea.de", "walmart.com.mx", "bestbuy.de"):
        link = answers.shop_search(shop, "kettle", where="ca")
        assert link.name == f"Find it on {shop}" and f"site%3A{shop}" in link.url, shop


def test_a_page_built_to_be_slow_to_parse_costs_a_moment():
    """Measured before: 64 KB of "<meta" with no ">" took 41 seconds, on the event loop."""
    import time

    from pyxi.shopping import _meta

    pages = [
        "<meta " * 3000,
        "<meta " + 'property="og:title" ' * 5000,
        "<head>" + "<title" * 15_000,
        "<title>" + "<a " * 20_000 + "</title>",
        "<meta " * 100_000,  # far past what is read
    ]
    for page in pages:
        started = time.perf_counter()
        answers.page_picture(page, "https://shop.example.com/x")
        assert time.perf_counter() - started < 1.0, page[:40]
    started = time.perf_counter()
    assert _meta("<meta " * 3000, "og:title", "og:image") == ""  # the price check's use of it
    assert time.perf_counter() - started < 1.0
    # What it is for still works, in either order of the attributes, and only in the head.
    page = (
        '<html><head><meta content="/a.jpg" property="og:image">'
        "<title>Space A40 &amp; case</title></head><body>"
        '<meta property="og:title" content="Not this one"></body></html>'
    )
    assert answers.page_picture(page, "https://shop.example.com/x") == (
        "Space A40 & case",
        "https://shop.example.com/a.jpg",
    )
    assert _meta('<meta name="og:title" content="A40">', "twitter:title", "og:title") == "A40"


async def test_parsing_and_decoding_stay_off_the_event_loop(monkeypatch):
    import threading

    where = {}
    real_page, real_thumb = answers.page_picture, answers.thumbnail

    def page_picture(page, url):
        where["page"] = threading.current_thread()
        return real_page(page, url)

    def thumbnail(raw):
        where["picture"] = threading.current_thread()
        return real_thumb(raw)

    monkeypatch.setattr(answers, "page_picture", page_picture)
    monkeypatch.setattr(answers, "thumbnail", thumbnail)
    product = (
        "<html><head><title>soundcore Space A40 by Anker</title>"
        '<meta property="og:image" content="/media/space-a40.jpg"></head></html>'
    )
    pages = {
        "https://www.soundcore.com/products/space-a40": (product.encode(), "text/html"),
        "https://www.soundcore.com/media/space-a40.jpg": (jpeg(), "image/jpeg"),
    }

    async def get(url, limit):
        return pages.get(url, (b"", ""))

    async def nothing(query, n):
        return []

    picture = await answers.find_picture(
        "Anker Soundcore Space A40", list(pages)[:1], search=nothing, get=get
    )
    assert picture and picture.startswith("data:image/jpeg;base64,")
    main = threading.main_thread()
    assert where["page"] is not main and where["picture"] is not main


NAMES = {
    "shop.example.com": ["93.184.216.34"],
    "cdn.example.com": ["93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946"],
    "10.0.0.1.nip.io": ["10.0.0.1"],
    "router.example.net": ["192.168.1.1"],
    "both.example.com": ["93.184.216.34", "127.0.0.1"],  # one public address is not enough
    "six.example.com": ["::1"],
    "mapped.example.com": ["::ffff:10.0.0.1"],
    "metadata.example.com": ["169.254.169.254"],
}


def lookup(host: str) -> list[str]:
    if host not in NAMES:
        raise OSError("no such name")
    return NAMES[host]


async def test_only_addresses_out_on_the_internet_count_as_public(monkeypatch):
    """The check used to read the address as text: any name with a dot in it passed."""
    monkeypatch.setattr(answers, "_addresses", lookup)
    assert await answers._public("https://shop.example.com/x")
    assert await answers._public("https://cdn.example.com/a.jpg")
    assert await answers._public("https://93.184.216.34/a.jpg")
    for bad in [
        "http://10.0.0.1.nip.io/",
        "http://router.example.net/admin",
        "http://both.example.com/",
        "http://six.example.com/",
        "http://mapped.example.com/",
        "http://metadata.example.com/latest/meta-data",
        "http://nowhere.example.com/",  # a name that doesn't resolve
        "http://localhost./",
        "http://router.lan/",
        "http://nas.home/photos",
        "http://printer.local/",
        "http://intranet/",
        "http://127.0.0.1:8766/x",
        "http://169.254.169.254/latest",
        "http://[::1]:8766/x",
        "http://[::ffff:192.168.1.1]/",
        "ftp://shop.example.com/x",
        "file:///C:/Windows/win.ini",
    ]:
        assert not await answers._public(bad), bad


async def test_a_redirect_is_followed_by_hand_and_checked_at_every_hop(monkeypatch):
    """Only the last address used to be checked, after the request to it had been made."""
    import httpx

    asked: list[str] = []

    def site(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        host, path = request.url.host, request.url.path
        if host == "cdn.example.com":
            return httpx.Response(200, content=b"a picture", headers={"content-type": "image/jpeg"})
        if host != "shop.example.com":
            return httpx.Response(200, content=b"the router's admin page")
        to = {
            "/moved": "https://cdn.example.com/a.jpg",
            "/twice": "/moved",
            "/home": "http://127.0.0.1:8766/secret",
            "/lan": "http://router.example.net/admin",
            "/file": "file:///C:/Windows/win.ini",
            "/loop": "/loop",
        }
        if path in to:
            return httpx.Response(302, headers={"location": to[path]})
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(site), follow_redirects=True)

    class Keep:
        async def __aenter__(self):
            return client

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(answers, "shared", lambda *a, **kw: Keep())
    monkeypatch.setattr(answers, "_addresses", lookup)
    assert await answers._get("https://shop.example.com/moved", 100) == (b"a picture", "image/jpeg")
    assert await answers._get("https://shop.example.com/twice", 4) == (b"a pi", "image/jpeg")
    for path in ("/home", "/lan", "/file", "/missing"):
        assert await answers._get(f"https://shop.example.com{path}", 100) == (b"", ""), path
    assert not [u for u in asked if "127.0.0.1" in u or "router" in u]  # never even asked
    asked.clear()
    assert await answers._get("https://shop.example.com/loop", 100) == (b"", "")
    assert len(asked) == answers.MAX_HOPS + 1  # three redirects followed, then it gives up
    assert await answers._get("http://10.0.0.1.nip.io/x", 100) == (b"", "") and len(asked) == 4
    await client.aclose()


def test_a_picture_that_claims_to_be_huge_is_never_decoded(monkeypatch):
    from PIL import ImageFile

    decoded = []
    real = ImageFile.ImageFile.load

    def load(self, *a, **kw):
        decoded.append(self.size)
        return real(self, *a, **kw)

    monkeypatch.setattr(ImageFile.ImageFile, "load", load)
    buf = io.BytesIO()
    Image.new("L", (4200, 4200)).save(buf, format="PNG")  # a few kilobytes on the wire
    assert answers.thumbnail(buf.getvalue()) is None and decoded == []
    assert answers.thumbnail(jpeg()) is not None and decoded  # a real photo still is
