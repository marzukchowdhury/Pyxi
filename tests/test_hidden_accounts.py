"""LinkedIn and Discord need a developer app or a bot token that a customer cannot make. They
are hidden everywhere unless that set-up is already on this PC, and a request for one of them
gets one plain line and an offer, never set-up steps."""

from __future__ import annotations

import re
import time

import pytest

from pyxi import secretstore
from pyxi.companion import prompts
from pyxi.companion.tools import COMPANION_TOOLS
from pyxi.connectors import NOT_YET, Connectors
from test_brain_and_server import make_brain

DEV_WORDS = re.compile(r"(?i)\.env|developer|bot token|client[ _]id|client[ _]secret|DISCORD_BOT_TOKEN")


@pytest.fixture
def customer(monkeypatch):
    for name in ("LINKEDIN_CLIENT_ID", "LINKEDIN_CLIENT_SECRET", "DISCORD_BOT_TOKEN"):
        monkeypatch.delenv(name, raising=False)


def _hub(tmp_path) -> Connectors:
    return Connectors(tmp_path, tmp_path / "downloads")


def test_a_customer_sees_only_google_in_connected_accounts(tmp_path, customer):
    hub = _hub(tmp_path)
    assert [s["id"] for s in hub.statuses()] == ["google"]
    assert [c.id for c in hub.hidden()] == ["discord", "linkedin"]
    assert hub.tools() == []
    # Nothing a customer is shown tells them to edit a file or make a developer app.
    assert not any(DEV_WORDS.search(line) for line in NOT_YET.values())
    # The developer's own command still lists all three.
    assert [s["id"] for s in hub.statuses(everything=True)] == ["google", "discord", "linkedin"]


def test_the_developer_set_up_brings_each_one_back(tmp_path, customer, monkeypatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "made-up")
    hub = _hub(tmp_path)
    assert [s["id"] for s in hub.statuses()] == ["google", "discord"]
    assert {t.spec.name for t in hub.tools()} == {"discord_channels", "discord_read", "discord_send"}
    monkeypatch.setenv("LINKEDIN_CLIENT_ID", "made-up")
    assert [s["id"] for s in hub.statuses()] == ["google", "discord"]  # the secret too, or nothing
    monkeypatch.setenv("LINKEDIN_CLIENT_SECRET", "made-up")
    assert [s["id"] for s in hub.statuses()] == ["google", "discord", "linkedin"]
    assert hub.hidden() == [] and hub.prompt_note().count("- ") == 1
    assert "Discord" in hub.prompt_note() and "LinkedIn" in hub.prompt_note()


def test_a_saved_linkedin_sign_in_keeps_it_on_show(tmp_path, customer):
    hub = _hub(tmp_path)
    secretstore.write_json(
        hub.linkedin.token_path,
        {"access_token": "made-up", "expires_at": time.time() + 86400 * 3, "sub": "1", "name": "Sam Lee"},
    )
    assert "linkedin" in [s["id"] for s in hub.statuses()]
    assert [t.spec.name for t in hub.tools()] == ["linkedin_post"]


def test_the_prompt_and_the_tools_do_not_offer_them(tmp_path, customer):
    system = prompts.system_prompt("kind", "Scout")
    rule = next(line for line in system.splitlines() if line.startswith("- Connected accounts"))
    assert "LinkedIn" not in rule and "Discord" not in rule
    errand = next(t for t in COMPANION_TOOLS if t.name == "errand")
    assert "LinkedIn" not in errand.description and "Discord" not in errand.description
    note = _hub(tmp_path).prompt_note()
    # What the model is told instead: the one line to say, and the offer.
    assert NOT_YET["linkedin"] in note and NOT_YET["discord"] in note
    assert "paste" in NOT_YET["linkedin"] and "paste" in NOT_YET["discord"]


@pytest.mark.parametrize(
    ("asked", "account"),
    [
        ("Post on LinkedIn that we are hiring a designer", "linkedin"),
        ("share this article to my LinkedIn", "linkedin"),
        ("Send a message to the Discord saying the build is ready", "discord"),
        ("read the latest in the #general Discord channel", "discord"),
    ],
)
def test_asking_for_one_gets_the_plain_line(tmp_path, customer, asked, account):
    assert _hub(tmp_path).not_yet(asked) == NOT_YET[account]


def test_naming_the_site_in_other_work_is_not_refused(tmp_path, customer, monkeypatch):
    hub = _hub(tmp_path)
    assert hub.not_yet("Email Sam Lee the link to my LinkedIn profile") == ""
    assert hub.not_yet("Put the Discord launch in my calendar for Friday at 3") == ""
    # Set up on this PC: nothing is refused.
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "made-up")
    assert hub.not_yet("Send a message to the Discord saying the build is ready") == ""


async def test_the_brain_starts_no_errand_and_gives_the_line(settings, customer):
    brain, rec = make_brain(settings)
    note = await brain._act("errand", {"task": "Post on LinkedIn: we are hiring a designer."})
    assert note.startswith("errand: not started.") and NOT_YET["linkedin"] in note
    assert not DEV_WORDS.search(note)
    assert not rec.of("job") and not brain.jobs.recent(5)
    # A tool of a hidden account that the model called anyway: the same line, nothing sent.
    note = await brain._act("discord_send", {"channel": "general", "text": "hello"})
    assert NOT_YET["discord"] in note and not rec.of("confirm")


def test_settings_cannot_start_a_hidden_sign_in(tmp_path, customer):
    # server.py only connects what `shown` lists, so a stray "connect linkedin" does nothing.
    assert {c.id for c in _hub(tmp_path).shown()} & {"google", "linkedin"} == {"google"}
