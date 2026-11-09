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
from pyxi.companion import answers
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

