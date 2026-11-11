"""A browser task is not done because it says so.

From the audit after the trial of 10 November. In a tab of the user's own browser, `done`
with "ok": "false" (or with no "ok" at all) counted as success, and `done` right after a click
that changed nothing on the page was accepted and said aloud as Pyxi's own line. Scout's rules
told it to finish with success=true and "the best answer you have", and only the first
sentence of its result is read out, so a caveat further down was never heard.
"""

from __future__ import annotations

import types

import pytest

from pyxi.agents import jobs as jobs_mod
from pyxi.agents.jobs import BROWSER_RULES, Job, honest_result
from pyxi.companion import tabwork
from test_brain_and_server import make_brain
from test_browserlink import SITE, PretendBrowser, _brain, act, work


@pytest.fixture
def quick(monkeypatch):
    monkeypatch.setattr(tabwork, "SETTLE_S", 0.0)
    monkeypatch.setattr(tabwork, "READ_ACCESS_WAIT_S", 0.0)
    monkeypatch.setattr(tabwork, "ACT_ACCESS_WAIT_S", 0.0)


def on_site(settings, frozen: bool = False):
    brain = _brain(settings)
    browser = PretendBrowser(brain.browser)
    brain.browser.allow_site(SITE)
    browser.frozen = frozen
    return brain, browser


# ------------------------------------------------------------------ in their own tab
async def test_done_counts_only_when_ok_is_true(settings, quick):
    for args in (
        {"ok": "false", "summary": "I couldn't find that button."},
        {"ok": "true", "summary": "It's pressed."},  # a word, not a yes
        {"summary": "I couldn't find that button."},
        {"ok": None, "summary": "Hm."},
    ):
        brain, _ = on_site(settings)
        job = await work(brain, "Press the button", ("done", args))
        assert job.status == "failed", args
    brain, _ = on_site(settings)
    job = await work(brain, "Read the page", ("done", {"ok": True, "summary": "It says Compose."}))
    assert job.status == "done" and brain.said == ["It says Compose."]


async def test_done_after_a_click_that_changed_nothing_is_questioned_once(settings, quick):
    brain, _ = on_site(settings, frozen=True)
    job = await work(
        brain,
        "Go to the next page",
        act({"do": "click", "ref": "e12"}),
        ("done", {"ok": True, "summary": "You're on the next page now."}),
        ("done", {"ok": False, "summary": "The Next button didn't do anything, so you're still on the first page."}),
    )
    seen = brain._tab_llm.seen
    assert "Not taken as done yet: the page reads exactly as it did before your last action" in seen[2]
    assert job.status == "failed" and "You're on the next page now." not in brain.said
    assert brain.said == ["The Next button didn't do anything, so you're still on the first page."]


async def test_it_is_asked_once_and_a_second_done_is_its_answer(settings, quick):
    brain, _ = on_site(settings, frozen=True)
    job = await work(
        brain,
        "Like the post",
        act({"do": "click", "ref": "e12"}),
        ("done", {"ok": True, "summary": "Liked."}),
        ("done", {"ok": True, "summary": "Liked: the button shows as pressed."}),
    )
    assert job.status == "done" and brain.said == ["Liked: the button shows as pressed."]
    assert sum("Not taken as done yet" in s for s in brain._tab_llm.seen[2].splitlines()) == 1


async def test_done_is_taken_at_once_when_the_page_did_change_or_nothing_was_clicked(settings, quick):
    brain, _ = on_site(settings)  # the page moves on after the click
    job = await work(brain, "Next", act({"do": "click", "ref": "e12"}), ("done", {"ok": True, "summary": "On page two."}))
    assert job.status == "done" and len(brain._tab_llm.seen) == 2
    brain, _ = on_site(settings, frozen=True)  # only reading: nothing to doubt
    job = await work(brain, "What does it say?", ("done", {"ok": True, "summary": "It says Compose."}))
    assert job.status == "done" and len(brain._tab_llm.seen) == 1
    brain, _ = on_site(settings, frozen=True)  # typing into a box doesn't change the page
    job = await work(
        brain, "Fill in To", act({"do": "type", "ref": "e1", "text": "dana@example.com"}),
        ("done", {"ok": True, "summary": "Filled in."}),
    )
    assert job.status == "done" and len(brain._tab_llm.seen) == 2


# ------------------------------------------------------------------ Scout
def test_a_result_that_says_it_found_nothing_is_not_a_success():
    assert honest_result("I couldn't find a price for that model anywhere.", True) == (
        "I couldn't find a price for that model anywhere.",
        False,
    )
    assert honest_result("No results came up for that name on the site.", True)[1] is False
    assert honest_result("Unable to reach the shop's page.", True)[1] is False
    # It found the next best thing: that is a result, and it is called what it is.
    text, ok = honest_result("I couldn't find the 2 TB model, but the 1 TB one is $89 at Newegg.", True)
    assert ok and text.startswith("Best guess: I couldn't find the 2 TB model, but")


def test_an_answer_it_isnt_sure_of_is_called_a_best_guess_up_front():
    text, ok = honest_result("The GLADOM is $34.99. I couldn't confirm it's in stock.", True)
    assert ok and text == "Best guess: The GLADOM is $34.99. I couldn't confirm it's in stock."
    assert honest_result("It's probably the 2019 model.", True)[0].startswith("Best guess: ")
    # Said once: a summary that already starts that way is left alone.
    assert honest_result("Best guess: it's probably the 2019 model.", True)[0] == "Best guess: it's probably the 2019 model."


def test_a_plain_result_and_a_plain_failure_are_left_as_they_are():
    nine = (
        "I compared the IKEA tables on the first Amazon.ca results page. I skipped the sponsored "
        "ads and left out non-table items, which aren't IKEA. Nothing was purchased.\n"
        "- LACK side table, white, $43.43"
    )
    assert honest_result(nine, True) == (nine, True)
    assert honest_result("The GLADOM tray table, at $34.99 on Amazon.ca.", True)[1] is True
    assert honest_result("I had to stop: the site wouldn't load.", False) == ("I had to stop: the site wouldn't load.", False)
    assert honest_result("", True) == ("", True)


def test_scouts_rules_no_longer_ask_for_a_guess_to_be_filed_as_found():
    assert "finish with the best answer you have" not in BROWSER_RULES
    assert "Finish with success=true whenever" not in BROWSER_RULES
    assert "success=false" in BROWSER_RULES and 'start the summary with "Best guess:"' in BROWSER_RULES


class _Otto:
    """Stands in for Otto: no browser, just the result a run would hand back."""

    result: tuple[str, bool] = ("", True)

    def __init__(self, settings, llm=None, controls=None):
        pass

    async def run(self, task, **kw):
        summary, success = self.result
        return types.SimpleNamespace(
            run_id="run_1",
            summary=summary,
            data=None,
            outputs=[],
            usage=types.SimpleNamespace(cost_usd=0.01),
            steps=4,
            success=success,
            status=types.SimpleNamespace(value="succeeded" if success else "failed"),
        )


async def _scout(settings, monkeypatch, summary: str, success: bool = True, kind: str = "task") -> Job:
    brain, _ = make_brain(settings)
    monkeypatch.setattr(jobs_mod, "Otto", _Otto)
    monkeypatch.setattr(_Otto, "result", (summary, success))
    agent = brain.registry.get("scout")
    job = Job(agent_id=agent.id, agent_name=agent.name, role=agent.role, task="Find the price.", kind=kind)
    await brain.jobs._run_browser(job, agent)
    return job


async def test_scouts_job_is_held_to_what_its_summary_says(settings, monkeypatch):
    job = await _scout(settings, monkeypatch, "I couldn't find a price for that model anywhere.")
    assert job.status == "failed"
    job = await _scout(settings, monkeypatch, "The GLADOM is $34.99. I couldn't confirm it's in stock.")
    assert job.status == "done" and job.summary.startswith("Best guess: The GLADOM is $34.99.")
    job = await _scout(settings, monkeypatch, "The GLADOM tray table, at $34.99 on Amazon.ca.")
    assert (job.status, job.summary) == ("done", "The GLADOM tray table, at $34.99 on Amazon.ca.")
    # A booking answers for itself (a confirmation, a card they approved): left alone.
    job = await _scout(settings, monkeypatch, "I couldn't find an earlier slot, so nothing changed.", kind="booking")
    assert job.status == "done" and not job.summary.startswith("Best guess")
