"""A place for later is not searched "open now".

Live, 15 November, 2:53 in the morning: "suggest me some Japanese place for tomorrow's dinner"
was searched on Maps as "open now", found next to nothing, and had to be stopped and corrected.
"""

from __future__ import annotations

import pytest

from pyxi.companion.answers import place_for_later

NOW = "https://www.google.com/maps/search/japanese+restaurant+open+now"
PLAIN = "https://www.google.com/maps/search/japanese+restaurant"


@pytest.mark.parametrize(
    "asked",
    [
        "Can you open Google Maps and suggest me some Japanese place for tomorrow's dinner? My budget is $30.",
        "a Japanese restaurant for tonight's dinner",
        "somewhere for sushi on Friday",
        "a ramen place for the weekend",
        "where can we eat at 7 pm",
    ],
)
def test_later_drops_open_now(asked: str) -> None:
    assert place_for_later(NOW, asked) == PLAIN


@pytest.mark.parametrize(
    "asked",
    [
        "coffee near me",
        "best pizza open now",
        "is anything still open? I want sushi tonight",  # their own "open" wins
        "a pharmacy nearby",
    ],
)
def test_now_keeps_it(asked: str) -> None:
    assert place_for_later(NOW, asked) == NOW


def test_other_addresses_are_left_alone() -> None:
    for url in (PLAIN, "https://example.com/search/open+now", "https://www.google.com/maps/search/coffee+open+now+near+me"):
        assert place_for_later(url, "coffee near me") == url
    assert place_for_later("https://www.google.com/maps/search/sushi%20open%20now", "sushi tomorrow") == (
        "https://www.google.com/maps/search/sushi"
    )
