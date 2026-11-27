"""Small faults found in a first-run walkthrough on 27 November 2026, each with its own check."""

from __future__ import annotations

from otto.types import ToolCall

from pyxi import plainwords
from pyxi.companion.brain import _kept, _no_made_up_account


def test_a_browser_that_lost_its_last_tab_is_said_in_plain_words():
    # Live: a booking ended on a card that read "No open tabs."
    class NavigationError(Exception):
        pass

    line = plainwords.plain_failure(NavigationError("No open tabs."), job=True)
    assert line == plainwords.BROWSER_CLOSED and "tabs" not in line


def test_a_password_pyxi_made_is_not_kept_in_the_turns_record():
    made = ToolCall(id="c", name="quick_action", arguments={"action": "copy", "value": "Vq7-not-a-real-one"})
    assert _kept(made, "Pyxi, make a password over here.") == {"action": "copy", "value": "(not kept)"}
    assert _kept(made, "Give me a strong passphrase") == {"action": "copy", "value": "(not kept)"}
    # Anything else that is copied is kept as it was, and so is every other tool.
    note = ToolCall(id="c", name="quick_action", arguments={"action": "copy", "value": "Dear team, welcome."})
    assert _kept(note, "Write a welcome note and copy it") == note.arguments
    said = ToolCall(id="c", name="say", arguments={"text": "Here is your password."})
    assert _kept(said, "make a password") == said.arguments


def test_an_account_nobody_said_is_taken_off_a_google_address():
    # Live: "switch my account to..." was misheard, and Gmail was opened for an address the
    # model had put together itself.
    made_up = "https://mail.google.com/mail/?authuser=bluejay42@example.com"
    assert _no_made_up_account(made_up, "Switch my account to bluejay42.", set()) == "https://mail.google.com/mail/"
    assert _no_made_up_account(made_up + "&x=2", "open my mail", set()) == "https://mail.google.com/mail/?x=2"
    # An address they said in full, their own address, or an account number stays.
    said = "https://mail.google.com/mail/?authuser=sam@example.com"
    assert _no_made_up_account(said, "open Gmail as sam@example.com", set()) == said
    assert _no_made_up_account(said, "open my mail", {"Sam@example.com"}) == said
    numbered = "https://docs.google.com/spreadsheets/create?authuser=1"
    assert _no_made_up_account(numbered, "a new sheet", set()) == numbered
    assert _no_made_up_account("https://www.youtube.com", "open YouTube", set()) == "https://www.youtube.com"


async def test_remembering_something_is_never_done_in_silence(settings):
    from test_brain_and_server import make_brain

    brain = make_brain(settings)[0]
    brain._calls = []
    note = await brain._act("remember", {"key": "sister_birthday", "content": "Their sister's birthday is 3 March."})
    assert note == "remember: saved. Say so in one short line, with what you'll remember."
    # Already said in the same breath: nothing more is asked of the model.
    brain._calls = [{"round": 1, "tool": "say", "arguments": {"text": "Noted."}}]
    assert await brain._act("remember", {"key": "pet", "content": "Their dog is called Biscuit."}) is None
