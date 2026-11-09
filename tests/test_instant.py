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


def test_google_pages_open_in_the_account_pyxi_is_connected_to():
    # Firefox signed in as ...21 first; Pyxi is connected as ...23, and only that account's
    # sheets can be edited by Ledger.
    from pyxi.companion.actions import in_account

    me = "marzuk23@example.com"
    assert in_account("https://sheets.new", me) == (
        "https://docs.google.com/spreadsheets/create?authuser=marzuk23%40example.com"
    )
    assert in_account("https://mail.google.com", me) == (
        "https://mail.google.com/mail/?authuser=marzuk23%40example.com"
    )
    assert in_account("https://docs.google.com/spreadsheets/d/x/edit#gid=0", me).endswith(
        "edit?authuser=marzuk23%40example.com#gid=0"
    )
    assert in_account("https://www.youtube.com", me) == "https://www.youtube.com"
    assert in_account("https://sheets.new", None) == "https://sheets.new"  # not connected


def test_speech_hearing_bank_for_blank_still_takes_the_instant_route():
    hit = match("Open a bank spreadsheet.")
    assert hit is not None and hit.value == "https://sheets.new"


async def test_one_request_never_opens_the_same_page_twice(settings):
    # "Create a new dot" (misheard): the model opened docs.new six times in 40 s.
    from test_brain_and_server import make_brain
    from test_connectors_and_docs import Script

    again = [("say", {"text": "Opening a new doc."}), ("quick_action", {"action": "open_url", "value": "https://docs.new"})]
    llm = Script(*[list(again) for _ in range(6)])
    brain, rec = make_brain(settings, llm=llm)
    await brain.turn("make me a doc thing for the team")
    assert len(rec.of("action")) == 1
    assert len(llm.prompts) <= 2  # no round after round of opening it again


def test_a_plain_play_request_goes_straight_to_the_player():
    from pyxi.companion.instant import play_request as p

    assert p("Play Blinding Lights by The Weeknd.") == ("Blinding Lights by The Weeknd", "youtube_music")
    assert p("can you play some lofi on Spotify") == ("some lofi", "spotify")
    assert p("play billie jean in my youtube music app") == ("billie jean", "youtube_music")
    for more in ("Play it again.", "play it a bit quieter", "play billie jean and turn it down", "Play a game with me"):
        assert p(more) is None, more


async def test_play_skips_the_model(settings):
    from test_brain_and_server import make_brain
    from test_connectors_and_docs import Script

    llm = Script()
    brain, rec = make_brain(settings, llm=llm)
    asked = []

    async def fake_play(args):
        asked.append(args)

    brain._play_music = fake_play
    await brain.turn("Play Blinding Lights by The Weeknd")
    assert asked == [{"query": "Blinding Lights by The Weeknd", "app": "youtube_music"}] and not llm.prompts


def test_their_folders_and_more_apps_open_at_once():
    from pyxi.companion import instant
    from pyxi.companion.actions import app_target

    hit = instant.match("Open my Downloads folder.")
    assert hit and hit.action == "open_app" and app_target(hit.value) == "shell:Downloads"
    assert app_target("my documents folder") == "shell:Personal"
    assert instant.match("open the clock app").value == "clock"
    assert instant.match("open the recycle bin") is not None
