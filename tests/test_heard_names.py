"""The ears are told the names that are likely to be said.

Live, across the founder's trials: "GenOffice" was heard as "Jen Office", "TechLand" as
"Deckland", "SAFAR" as "Supper", and "marz at the rate of gmail.com" as "Mars at the rate of
Chimeel.com". The listening model on the PC takes a short list of words to expect; it had a
fixed one. It now gets the programs and pages that are open and what is on the desktop too.
"""

from __future__ import annotations

from pyxi.voice import stt


def test_the_standing_words_come_first_and_names_are_added_once_as_many_as_fit():
    words = stt.hotwords(["GenOffice", "Mozilla Firefox", "TechLand BD", "gmail", "x", "A, B", "GenOffice"])
    assert words.startswith(stt.HOTWORDS)
    assert words.endswith("GenOffice, Mozilla Firefox, TechLand BD")  # no repeat, no "x", no list in a name
    assert stt.hotwords(None) == stt.HOTWORDS and stt.hotwords([]) == stt.HOTWORDS
    many = stt.hotwords([f"Program number {i}" for i in range(200)])
    assert len(many) <= stt.MAX_HINT_CHARS and many.startswith(stt.HOTWORDS)


def test_an_address_said_aloud_is_written_as_an_address():
    tidy = stt._tidy
    assert tidy("open Gmail in marz 23 at the rate of gmail.com") == "open Gmail in marz 23@gmail.com"
    assert tidy("send it to tarek at the rate gmail dot com please") == "send it to tarek@gmail.com please"
    # The same words that are not an address stay as they are.
    assert tidy("at the rate of ten per hour") == "at the rate of ten per hour"
    assert tidy("what is the rate of exchange dot com bubble") == "what is the rate of exchange dot com bubble"
    assert tidy("(no speech)") == ""


async def test_the_brain_gives_the_ears_what_is_open_and_on_the_desktop(settings, monkeypatch):
    from pyxi import windows
    from pyxi.companion import actions
    from test_brain_and_server import make_brain

    monkeypatch.setattr(windows, "available", lambda: True)
    monkeypatch.setattr(
        windows,
        "open_windows",
        lambda minimized=False: [
            (1, "Inbox (42) - me@example.com - Gmail - Mozilla Firefox", ""),
            (2, "Untitled Spreadsheet.xlsx - GenOffice", ""),
            (3, "TechLand BD | Trusted Computer Store", ""),
        ],
    )
    monkeypatch.setattr(actions, "_desktop_shortcuts", lambda: [("Bank statements", "x"), ("OBS Studio", "y")])
    brain, _ = make_brain(settings)
    names = brain._names_in_reach()
    assert {"Gmail", "Mozilla Firefox", "GenOffice", "TechLand BD", "Bank statements", "OBS Studio"} <= set(names)
    assert not any("@" in n or "42" in n for n in names)  # no addresses, no counts
