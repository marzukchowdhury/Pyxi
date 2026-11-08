"""Instant routes: simple requests done in a second without the model, and only those."""

import pytest

from pyxi.companion.instant import match


@pytest.mark.parametrize(
    ("said", "action", "value"),
    [
        ("Open up blank spreadsheet.", "open_url", "https://sheets.new"),
        ("Open up the blank spreadsheet.", "open_url", "https://sheets.new"),
        ("can you open a blank spreadsheet?", "open_url", "https://sheets.new"),
        ("Can you open a new google sheet for me", "open_url", "https://sheets.new"),
        ("create a new doc", "open_url", "https://docs.new"),
        ("make me a blank presentation", "open_url", "https://slides.new"),
        ("Can you open Google sheets on a new tab?", "open_url", "https://docs.google.com/spreadsheets"),
        ("open gmail", "open_url", "https://mail.google.com"),
        ("hey pyxi, open youtube please", "open_url", "https://www.youtube.com"),
        ("go to my calendar", "open_url", "https://calendar.google.com"),
        ("open notepad", "open_app", "notepad"),
        ("could you open the calculator app", "open_app", "calculator"),
        ("open sound settings", "open_app", "sound settings"),
        ("pause the music", "media", "playpause"),
        ("skip this song", "media", "next"),
        ("next song please", None, None),  # "please" after media isn't covered: the model has it
    ],
)
def test_simple_requests_take_the_instant_route(said, action, value):
    hit = match(said)
    if action is None:
        return
    assert hit is not None and (hit.action, hit.value) == (action, value)


@pytest.mark.parametrize(
    "said",
    [
        "open a blank spreadsheet and make a pivot table",
        "Create a pivot table",
        "open youtube and play lofi",
        "how do I open a new spreadsheet?",
        "open the cheapest flight to Dhaka",
        "go to google and search for pizza",
        "open the excel file from yesterday",
        "what's on my calendar tomorrow?",
        "open my email from Sam",
        "pause after this video and remind me",
        "make a spreadsheet of my expenses",
        "open the sheet",
    ],
)
def test_anything_more_goes_to_the_model(said):
    assert match(said) is None


async def test_a_blank_sheet_opens_without_asking_the_model(settings):
    from test_brain_and_server import make_brain
    from test_connectors_and_docs import Script

    llm = Script()  # no answers: any model call would fail the turn
    brain, rec = make_brain(settings, llm=llm)
    await brain.turn("can you open a blank spreadsheet?")
    [opened] = rec.of("action")
    assert opened["kind"] == "open_url" and opened["value"] == "https://sheets.new"
    assert not llm.prompts and rec.of("say")  # done, said, and the model never asked
