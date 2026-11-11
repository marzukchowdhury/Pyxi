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


def test_site_searches_open_at_once():
    from pyxi.companion import instant

    hit = instant.match("Search YouTube for lo-fi study music.")
    assert hit.value == "https://www.youtube.com/results?search_query=lo-fi+study+music"
    assert instant.match("google cheap flights to Toronto").value.startswith("https://www.google.com/search?q=cheap+flights")
    assert instant.match("find wireless earbuds on Amazon").value.startswith("https://www.amazon.ca/s?k=")
    assert instant.match("Find a coffee place near me that is open now") is None  # Maps with picks: the model


def test_a_plain_command_after_a_reason():
    from pyxi.companion import instant

    assert instant.match("I need to check my email, open Gmail.").value == "https://mail.google.com"
    assert instant.match("I want to watch something. Go to YouTube.").value == "https://www.youtube.com"
    assert instant.match("Open Gmail and summarize my latest email") is None  # more to it: the model


# ---------------------------------------------------------------------- any program on the PC
def test_a_program_is_found_by_the_name_they_say(monkeypatch):
    """Live: "open Gen Office" got "GenOffice, coming up" and nothing opened: the opener only
    knew a short list. Anything in the Start menu or on the desktop opens now."""
    from pyxi.companion import actions

    start = [
        ("GenOffice", "shell:AppsFolder/com.genoffice.app"),
        ("Visual Studio Code", "shell:AppsFolder/Microsoft.VisualStudioCode"),
        ("Google Chrome", "shell:AppsFolder/Chrome"),
        ("Office Language Preferences", "shell:AppsFolder/SETLANG"),
        ("Steam", "shell:AppsFolder/Steam"),
        ("Steam Support Center", "shell:AppsFolder/SteamSupport"),
        ("Uninstall GenOffice", "shell:AppsFolder/uninstall"),
    ]
    monkeypatch.setattr(actions, "_start_apps", lambda: start)
    monkeypatch.setattr(actions, "_desktop_shortcuts", lambda: [("OBS Studio", "C:/Users/x/Desktop/OBS Studio.lnk")])
    monkeypatch.setattr(actions, "_INSTALLED", (0.0, {}))
    found = actions.installed_app
    assert found("Gen Office") == ("GenOffice", "shell:AppsFolder/com.genoffice.app")
    assert found("the gen office app")[0] == "GenOffice"
    assert found("vs code")[0] == "Visual Studio Code"  # how people say it
    assert found("chrome")[0] == "Google Chrome"  # most of its name
    assert found("obs studio")[0] == "OBS Studio"  # only on the desktop
    assert found("steam")[0] == "Steam"  # its exact name, though another starts with it
    assert found("office")[0] == "GenOffice"  # most of that name, and a word of the long one is not it
    assert found("language") is None and found("support") is None
    assert found("uninstall genoffice") is None and found("zzz") is None and found("") is None
    # Live: "open Jen Office" (misheard) was answered "I don't see anything called Jen Office".
    assert found("Jen Office")[0] == "GenOffice"
    assert found("visual studio cod")[0] == "Visual Studio Code"
    assert found("stream") is None or found("stream")[0] == "Steam"  # near enough to one name only
    assert found("blorbo") is None


async def test_opening_a_program_pyxi_has_no_word_for(monkeypatch):
    from pyxi.companion import actions

    started = []
    monkeypatch.setattr(actions, "installed_app", lambda name: ("GenOffice", "shell:AppsFolder/com.genoffice.app") if "gen" in name.lower() else None)
    monkeypatch.setattr(actions.os, "startfile", started.append, raising=False)
    assert await actions.open_app("Gen Office") == "Opened GenOffice."
    assert started == ["shell:AppsFolder/com.genoffice.app"]
    note = await actions.open_app("Blorbo")
    assert note.startswith("open_app: there's no program called 'Blorbo'") and len(started) == 1


async def test_an_address_that_does_not_exist_is_not_opened(settings, monkeypatch):
    """Live: "on techlandbd.com, find earphones" was written down as "ontechlandbd.com", and
    Pyxi opened it: "Server Not Found". The word stuck to the front is taken off when that
    makes a real site; a name with no site at all is not opened, and the tool says so."""
    from pyxi.companion import actions
    from test_brain_and_server import make_brain

    real = {"techlandbd.com", "www.google.com", "online.com"}

    async def resolves(host):
        return host in real

    monkeypatch.setattr(actions, "_resolves", resolves)
    assert await actions.real_site("https://ontechlandbd.com") == "https://techlandbd.com"
    assert await actions.real_site("https://online.com/x") == "https://online.com/x"  # a real name is left alone
    assert await actions.real_site("https://nosuchshopatall.com") is None
    assert await actions.real_site("http://localhost:3000") == "http://localhost:3000"
    assert await actions.real_site("http://192.168.0.1/") == "http://192.168.0.1/"
    # Offline every name fails, which says nothing about this one: opened as it is.
    real.clear()
    assert await actions.real_site("https://ontechlandbd.com") == "https://ontechlandbd.com"

    # And through the brain: the right site is opened, a missing one is refused.
    real.update({"techlandbd.com", "www.google.com"})
    brain, rec = make_brain(settings)
    brain._user_text = "on techlandbd.com find earphones"
    assert await brain._quick_action("open_url", "ontechlandbd.com") is None
    assert rec.of("action")[-1]["value"] == "https://techlandbd.com"
    note = await brain._quick_action("open_url", "nosuchshopatall.com")
    assert note and note.startswith("open_url: there is no site at nosuchshopatall.com")


async def test_a_misheard_site_is_answered_with_the_real_one_near_it(settings, monkeypatch):
    """Live: "Amazon.ca" was written down as "amaazon.ka". Nothing is opened at a name that
    does not exist; the real address one mishearing away is offered to the model, which opens
    it when it is plainly the site they named."""
    from pyxi.companion import actions
    from test_brain_and_server import make_brain

    # (amaazon.ca is there too, as it really is: someone has registered the look-alike.)
    real = {"amazon.ca", "amaazon.ca", "www.google.com", "google.com"}

    async def resolves(host):
        return host in real

    monkeypatch.setattr(actions, "_resolves", resolves)
    assert await actions.near_site("https://amaazon.ka") == "https://amazon.ca"
    # And a look-alike that exists is never opened in place of the site everyone knows.
    assert await actions.real_site("https://amaazon.ca") == "https://amazon.ca"
    assert await actions.real_site("https://www.gooogle.com/maps") == "https://www.google.com/maps"
    assert await actions.real_site("https://amazon.ca") == "https://amazon.ca"
    assert await actions.near_site("https://www.amazon.ka/deals") == "https://amazon.ca/deals"
    assert await actions.near_site("https://gooogle.kom") == "https://google.com"  # one "o" too many, not two
    assert await actions.near_site("https://nosuchshopatall.com") is None
    brain, rec = make_brain(settings)
    brain._user_text = "open amaazon.ka"
    note = await brain._quick_action("open_url", "amaazon.ka")
    assert note and "there is no site at amaazon.ka" in note and "https://amazon.ca" in note
    assert rec.of("action") == []  # and nothing was opened
