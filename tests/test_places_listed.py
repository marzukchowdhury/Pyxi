"""Places on Maps are chosen from the whole list of results, not the few in view.

Live, 15 November: asked for a Japanese place for dinner, Pyxi picked from the three results on
screen, and the pick was shut that day.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from pyxi.companion import answers, tabwork

TAB = (
    "Address: https://www.google.com/maps/search/japanese+restaurant\n"
    "Sushi One 4.7 (812) $20-30 Closed, opens Wed 11:30 a.m.\n"
    "Ramen Two 4.5 (2,104) $10-20 Open until 10 p.m.\n"
    "Izakaya Three 4.6 (350) $30-50\n"
)


def _brain(connected: bool = True) -> SimpleNamespace:
    return SimpleNamespace(browser=SimpleNamespace(connected=connected))


@pytest.mark.asyncio
async def test_the_whole_list_is_handed_over(monkeypatch) -> None:
    async def read(_brain, name, _args):
        assert name == "read_tab"
        return TAB

    monkeypatch.setattr(tabwork, "handle", read)
    listed = await answers.places_listed(_brain())
    assert "Ramen Two" in listed and "Izakaya Three" in listed
    assert "ALL of these" in listed and "closed" in listed


@pytest.mark.asyncio
async def test_nothing_when_the_browser_is_not_linked_or_the_tab_is_not_maps(monkeypatch) -> None:
    async def other(_brain, _name, _args):
        return "Address: https://example.com/maps/search/x\nSome page\n"

    monkeypatch.setattr(tabwork, "handle", other)
    assert await answers.places_listed(_brain()) == ""
    assert await answers.places_listed(_brain(connected=False)) == ""

    async def refused(_brain, _name, _args):
        return "read_tab: no tab is linked."

    monkeypatch.setattr(tabwork, "handle", refused)
    assert await answers.places_listed(_brain()) == ""

    async def broke(_brain, _name, _args):
        raise RuntimeError("link dropped")

    monkeypatch.setattr(tabwork, "handle", broke)
    assert await answers.places_listed(_brain()) == ""
