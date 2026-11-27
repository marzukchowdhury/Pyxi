"""Email, calendar and Google files in their own browser tab when Google is not connected
(pyxi/companion/tabroute.py)."""

from __future__ import annotations

import pytest

from pyxi.companion import tabroute


@pytest.mark.parametrize(
    ("task", "name"),
    [
        ("Find the email Sam sent about the invoice and reply that I'll pay Friday", "Gmail"),
        ("check my inbox for anything from the landlord", "Gmail"),
        ("Unsubscribe me from the Daily Deals newsletter", "Gmail"),
        ("What's on my calendar tomorrow?", "Google Calendar"),
        ("Move my meeting with Alex to 3 pm", "Google Calendar"),
        ("Add a column for tax to my budget spreadsheet", "Google Sheets"),
        ("Fix the headings in my Google Doc", "Google Docs"),
        ("Email the meeting notes to Sam", "Google Calendar"),  # the first thing it names; either tab can do it
    ],
)
def test_a_task_about_one_of_googles_apps_knows_where_it_lives(task, name):
    assert tabroute.place_for(task)[0] == name


@pytest.mark.parametrize("task", ["Post my update on LinkedIn", "Tidy my desktop", "", "Book a table for two"])
def test_any_other_task_is_left_alone(task):
    assert tabroute.place_for(task) is None
    assert tabroute.note(task, True) is None and tabroute.note(task, False) is None


def test_with_pyxi_in_their_browser_the_work_goes_to_their_own_tab():
    said = tabroute.note("Find the email Sam sent about the invoice and reply that I'll pay Friday", True)
    assert said.startswith("errand: not started, because Google isn't connected to Pyxi.")
    assert 'call use_tab with url "https://mail.google.com/mail/"' in said
    # Nothing goes out without them, and they are not sent off to connect something first.
    assert "Nothing is sent, deleted or accepted without their OK" in said
    assert "Don't tell them to connect Google before you start" in said
    assert "search box" in said  # how to get about in Gmail
    assert "  " not in said


def test_without_it_both_ways_forward_are_said_and_nothing_is_claimed():
    said = tabroute.note("What's on my calendar tomorrow?", False)
    assert "use_tab" not in said
    assert "connect my browser" in said and "Connected accounts" in said
    assert "Google Calendar" in said and said.endswith("Claim nothing was done.")


# ------------------------------------------------------------------ at the errand tool itself
async def test_the_errand_tool_sends_mail_work_to_their_tab_when_google_is_not_connected(settings):
    from test_audit_tabwork import on_site

    brain, _ = on_site(settings)  # Pyxi is in their browser; Google is not connected
    assert not brain.connectors.google.connected()
    note = await brain._act("errand", {"task": "Find the email from Sam about the invoice and reply that I'll pay Friday"})
    assert note.startswith("errand: not started, because Google isn't connected to Pyxi.")
    assert 'use_tab with url "https://mail.google.com/mail/"' in note
    # A task that is not about Google's apps gets the old answer.
    other = await brain._act("errand", {"task": "Post a hello in my Discord server"})
    assert "use_tab" not in (other or "")


async def test_without_the_browser_it_says_both_ways_forward(settings):
    from test_brain_and_server import make_brain

    made = make_brain(settings)
    brain = made[0] if isinstance(made, tuple) else made
    note = await brain._act("errand", {"task": "What's on my calendar tomorrow?"})
    assert "connect my browser" in note and note.endswith("Claim nothing was done.")
