"""A price that is the point of the question comes from a shop they can buy at.

Live, 12 November: "the best IEM earphones, my budget is $30" was answered with a pick "at
about $25", a US review's price. On Amazon.ca, where they shop, it was $56. The names here are
made up.
"""

from __future__ import annotations

import pytest
from otto.types import ToolCall

from pyxi import web
from pyxi.companion import answers
from pyxi.companion.brain import Brain
from test_answer_card import RESULTS, Scripted, make_brain, say, settle

REVIEW = {
    "title": "Wren Two",
    "figure": "about $25",
    "note": "Price per a review",
    "facts": ["Best under $30 in one test"],
    "sources": [{"name": "Earbud Review", "url": "https://www.earbudreview.example/best-under-30"}],
    "shop": "amazon",
    "query": "Wren Two",
}


@pytest.fixture(autouse=True)
def in_canada(monkeypatch):
    monkeypatch.setattr(Brain, "QUIET_BEFORE_SPEAKING_S", 0.0)
    monkeypatch.setattr(answers, "country", lambda: "ca")


def test_a_budget_is_heard_and_other_numbers_are_not():
    assert answers.budget_in("Can you find me the best earbuds? My budget is $30.") == 30
    assert answers.budget_in("find me something, my budget is 30 bucks") == 30
    assert answers.budget_in("a desk under 150 dollars") == 150
    assert answers.budget_in("best earbuds under $50") == 50
    assert answers.budget_in("no more than £1,200 for a laptop") == 1200
    for words in ("be there in under 30 minutes", "I paid $20 for this, what is it?", "which is the best robot vacuum?", ""):
        assert answers.budget_in(words) is None, words
    assert answers.price_bound("find the cheapest place to get this") and not answers.price_bound("which is best?")


def test_whose_price_it_is():
    theirs = answers.price_from_their_shops
    assert not theirs(REVIEW, "ca")  # a review on a .example site, a bare $
    assert theirs({**REVIEW, "sources": [{"url": "https://www.amazon.ca/s?k=wren+two"}]}, "ca")
    assert theirs({**REVIEW, "figure": "CA$28.08", "sources": []}, "ca")  # says whose dollars
    assert theirs(REVIEW, "us")  # at home, a bare $ is theirs
    assert not theirs({**REVIEW, "figure": "CA$28"}, "us")


def test_the_shops_own_search_carries_the_budget():
    assert answers.shop_filter_url("ca", 30) == "https://www.amazon.ca/s?k={q}&rh=p_36%3A-3000&s=review-rank"
    assert answers.shop_filter_url("uk", None) == "https://www.amazon.co.uk/s?k={q}&s=price-asc-rank"
    assert "Canada" in answers.where_line() and "amazon.ca" in answers.where_line()


class Pretend:
    def __init__(self, said: str, calls=()) -> None:
        self._user_text, self._calls = said, list(calls)


def test_a_review_price_for_a_budget_is_sent_back_once():
    brain = Pretend("Can you find me the best earbuds? My budget is $30.")
    note = answers.unchecked_price(brain, REVIEW)
    assert note.startswith("show_answer:") and "budget of 30" in note and "Canada" in note
    assert "earbudreview.example" in note and "is being opened" in note
    assert answers.shop_url_for(brain, REVIEW) == "https://www.amazon.ca/s?k=Wren+Two&rh=p_36%3A-3000&s=review-rank"
    assert answers.unchecked_price(brain, REVIEW) is None  # once: then it has looked, or says so


def test_a_price_is_not_sent_back_when_it_is_theirs_or_not_the_point():
    budget = "Can you find me the best earbuds? My budget is $30."
    assert answers.unchecked_price(Pretend("Which earbuds are best?"), REVIEW) is None  # no budget
    assert answers.unchecked_price(Pretend(budget), {**REVIEW, "figure": "CA$28.08"}) is None
    # A pick said to fit the budget with no price at all is no better than a wrong price.
    assert answers.unchecked_price(Pretend(budget), {**REVIEW, "figure": "4.8 stars"}) is not None
    opened = Pretend(budget)
    opened._opened_urls = {"https://www.amazon.ca/s?k=wired+earbuds&rh=p_36%3A-3000"}
    assert answers.unchecked_price(opened, REVIEW) is None  # their shop's results are on screen
    looked = Pretend(budget, [{"tool": "look_again"}])
    assert answers.unchecked_price(looked, REVIEW) is None  # read off the shop's page this turn


async def test_in_a_turn_the_wrong_price_is_never_said_and_the_model_is_sent_to_the_shop(settings, monkeypatch):
    async def fake_search(query, max_results=6):
        return RESULTS

    monkeypatch.setattr(web, "search_text", fake_search)
    llm = Scripted(
        [ToolCall(id="w", name="web_search", arguments={"query": "best earbuds under $30"})],
        [say("My pick is the Wren Two at about $25.", "s1"), ToolCall(id="a", name="show_answer", arguments=REVIEW)],
        [say("Nothing I can confirm under $30 at Amazon.ca yet.", "s2")],
    )
    brain, rec = make_brain(settings, llm=llm)
    opened: list[tuple[str, str]] = []

    async def fake_quick(action, value):
        opened.append((action, value))

    monkeypatch.setattr(brain, "_quick_action", fake_quick)
    await brain.turn("Can you find me the best earbuds? My budget is $30.")
    await settle(brain)
    said = " ".join(s["text"] for s in rec.of("say"))
    assert "$25" not in said and "Nothing I can confirm" in said
    assert not rec.of("answer")  # the card was not shown
    # Their shop's search for the pick, capped at the budget, was opened by Pyxi itself.
    assert opened == [("open_url", "https://www.amazon.ca/s?k=Wren+Two&rh=p_36%3A-3000&s=review-rank")]
    assert "not from a shop they can buy at" in llm.prompts[-1]
    assert "They are in Canada" in llm.prompts[0]


async def test_with_no_budget_the_same_pick_is_said_and_shown(settings, monkeypatch):
    async def fake_search(query, max_results=6):
        return RESULTS

    monkeypatch.setattr(web, "search_text", fake_search)
    llm = Scripted(
        [ToolCall(id="w", name="web_search", arguments={"query": "best earbuds"})],
        [say("My pick is the Wren Two at about $25.", "s1"), ToolCall(id="a", name="show_answer", arguments=REVIEW)],
    )
    brain, rec = make_brain(settings, llm=llm)
    await brain.turn("Which earbuds should I get?")
    await settle(brain)
    assert "Wren Two at about $25" in " ".join(s["text"] for s in rec.of("say"))
