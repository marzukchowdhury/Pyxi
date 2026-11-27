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


# ------------------------------------------------------------------ a site that turned Scout away
async def test_a_bot_check_ends_on_a_button_not_on_wait_and_try_again(settings):
    """Live: Expedia blocked Scout's browser, and the card said to wait a while."""
    from pyxi import plainwords
    from pyxi.agents.jobs import Job
    from test_audit_tabwork import on_site
    from test_brain_and_server import make_brain

    def blocked() -> Job:
        return Job(
            agent_id="scout", agent_name="Scout", role="browser", status="failed",
            task="Search Expedia.ca for round-trip flights from Vancouver to Dhaka",
            start_url="https://www.expedia.ca/",
            summary="Expedia's bot check page blocked my browser on the homepage, so I have no fares.",
        )

    # Pyxi is not in their browser yet: the button adds it.
    made = make_brain(settings)
    brain, rec = made[0], made[1]
    await brain._job_done(blocked())
    card = rec.of("job_ready")[-1]
    assert card["actions"] == ["browser_setup"] and card["body"] == plainwords.SHUT_OUT_SETUP
    assert not card["ok"] and "wait a while" not in card["body"]

    # Pyxi is in their browser: the button does the same task there.
    there, _ = on_site(settings)
    seen = []
    there.hub.connect(lambda text: seen.append(text) or None)
    job = blocked()
    there.jobs.jobs[job.id] = job
    await there._job_done(job)
    import json

    ready = [json.loads(t) for t in seen if '"job_ready"' in t][-1]
    assert ready["actions"] == ["own_tab"] and ready["body"] == plainwords.SHUT_OUT_TAB


async def test_do_it_in_my_browser_runs_the_same_task_in_their_tab(settings, monkeypatch):
    from pyxi.agents.jobs import Job
    from pyxi.companion import tabwork
    from test_audit_tabwork import on_site

    brain, _ = on_site(settings)
    job = Job(agent_id="scout", agent_name="Scout", role="browser", status="failed", task="Find flights to Dhaka", start_url="https://www.expedia.ca/")
    brain.jobs.jobs[job.id] = job
    asked = []

    async def handle(brain_, name, args):
        asked.append((name, args))

    monkeypatch.setattr(tabwork, "handle", handle)
    await brain.in_own_tab(job.id)
    await brain.in_own_tab("job_nope")  # a card whose job is gone does nothing
    assert asked == [("use_tab", {"task": "Find flights to Dhaka", "url": "https://www.expedia.ca/"})]


# ------------------------------------------------------------------ "on my Firefox" means their Firefox
async def test_asked_for_in_their_own_browser_it_never_goes_to_scouts(settings):
    from pyxi.companion import tabwork
    from test_audit_tabwork import on_site
    from test_brain_and_server import make_brain

    brain, _ = on_site(settings)  # their browser is connected
    args = {"agent": "scout", "task": "Find round-trip flights to Dhaka", "start_url": "https://www.expedia.ca/"}
    for said in [
        "Can you again go through Expedia on Mozilla Firefox?",
        "Do it in my browser this time.",
        "Search Expedia using Chrome for flights to Dhaka",
        "Check my orders on Amazon",  # signed-in work is their own tab's too
    ]:
        brain._user_text = said
        note = tabwork.their_browser_instead(brain, args)
        assert note and note.startswith("delegate: not started.") and 'url "https://www.expedia.ca/"' in note, said
    # A plain research request still goes to Scout, and so does a writer's job.
    brain._user_text = "Find me the three best laptops under a thousand dollars."
    assert tabwork.their_browser_instead(brain, args) is None
    brain._user_text = "Do it in my browser this time."
    assert tabwork.their_browser_instead(brain, {"agent": "quill", "task": "Write a note"}) is None
    # With no browser connected there is nothing to turn to.
    made = make_brain(settings)
    alone = made[0]
    alone._user_text = "Do it in my browser this time."
    assert tabwork.their_browser_instead(alone, args) is None
