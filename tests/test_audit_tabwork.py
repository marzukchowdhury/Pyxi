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
        {"ok": "yes", "summary": "It's pressed."},
        {"ok": 1, "summary": "It's pressed."},
        {"summary": "I couldn't find that button."},
        {"ok": None, "summary": "Hm."},
    ):
        brain, _ = on_site(settings)
        job = await work(brain, "Press the button", ("done", args))
        assert job.status == "failed", args
    brain, _ = on_site(settings)
    job = await work(brain, "Read the page", ("done", {"ok": True, "summary": "It says Compose."}))
    assert job.status == "done" and brain.said == ["It says Compose."]


async def test_true_written_as_a_word_is_still_true(settings, quick):
    """The first fix took only the bare value, so a model that writes "ok": "true" (some do)
    had every finished task filed as failed."""
    for word in ("true", "True", " TRUE "):
        brain, _ = on_site(settings)
        job = await work(brain, "Read the page", ("done", {"ok": word, "summary": "It says Compose."}))
        assert job.status == "done" and brain.said == ["It says Compose."], word


async def test_a_right_done_is_not_answered_with_your_actions_arent_working(settings, quick):
    """"Pause the video": one click whose effect is not in what the page reads as, then
    done. The look after "(done?)" was counted as a second unchanged look, so the model was
    told "your actions aren't working. Try a different way", clicked Pause again (playing
    it), and could end in "I got stuck"."""
    brain, browser = on_site(settings, frozen=True)
    job = await work(
        brain,
        "Pause the video",
        act({"do": "click", "ref": "e12"}),
        ("done", {"ok": True, "summary": "It's paused."}),
        ("done", {"ok": True, "summary": "It's paused."}),
    )
    seen = brain._tab_llm.seen
    assert len(seen) == 3 and "Not taken as done yet" in seen[2]
    assert "aren't working" not in seen[2] and "hasn't changed for" not in seen[2]
    assert job.status == "done" and brain.said == ["It's paused."]
    assert len([1 for n, _ in browser.acted() if n == "click"]) == 1  # Pause was pressed once


async def test_the_question_is_not_a_look_towards_getting_stuck(settings, quick):
    brain, _ = on_site(settings, frozen=True)
    click = act({"do": "click", "ref": "e12"})
    other = act({"do": "click", "ref": "e9"})
    job = await work(
        brain,
        "Go on",
        click,
        ("done", {"ok": True, "summary": "Done."}),
        other,  # asked, it tries another way: that is the second look with nothing new
        ("done", {"ok": False, "summary": "Neither button does anything."}),
    )
    seen = brain._tab_llm.seen
    assert "hasn't changed for" not in seen[2]
    assert "The page hasn't changed for 2 looks" in seen[3]  # counted from the clicks only
    assert job.status == "failed" and "stuck" not in job.summary


class _OneLookLeft(tabwork.TabOperator):
    """A page that reads the same whatever is done to it, and an action that always works."""

    async def _look(self):
        return {"tab": {"id": 5, "url": "https://video.example/watch", "title": "A video"}, "scroll": {"y": 0}, "text": "A video.", "items": []}

    async def _do(self, a, page, n):
        return 'clicked "Pause"', ""


async def test_a_doubt_on_the_last_step_takes_the_done(settings):
    """Asked on its last allowed step, there was no step left to answer in, and a finished
    task ended as "That took more steps than I expected, so I stopped where it is"."""
    from test_browserlink import Scripted

    llm = Scripted(act({"do": "click", "ref": "7"}), ("done", {"ok": True, "summary": "It's paused."}))
    op = _OneLookLeft(llm, None, None, None, None, settle_s=0, max_steps=2)
    out = await op._run("pause the video", "", "")
    assert (out.ok, out.summary) == (True, "It's paused.")
    assert len(llm.seen) == 2


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
def test_a_result_that_says_the_job_could_not_be_done_is_not_a_success():
    assert honest_result("I couldn't open the shop's site: it kept timing out.", True) == (
        "I couldn't open the shop's site: it kept timing out.",
        False,
    )
    assert honest_result("Unable to reach the shop's page.", True)[1] is False
    assert honest_result("I was unable to complete the checkout form.", True)[1] is False
    # It got somewhere all the same: that is a result.
    assert honest_result("I couldn't reach Newegg, but Amazon has the 1 TB one at $89.", True)[1] is True


def test_an_answer_that_nothing_was_there_is_an_answer():
    """A review of the first version: "No recalls were found" and "I didn't find any unpaid
    tickets" were called failures and spoken as "Scout couldn't finish that." They are what
    was asked for. Whether a search came up empty is the teammate's own flag to set."""
    for answer in (
        "No recalls were found for the 2019 Honda Civic on NHTSA's site.",
        "No outages found: GitHub's status page shows all systems operational.",
        "I didn't find any unpaid parking tickets for plate 7ABC123.",
        "No direct flights came up for Friday. The shortest is Delta via Atlanta, $212.",
        "I couldn't find a price for that model anywhere.",
    ):
        assert honest_result(answer, True) == (answer, True)


def test_an_answer_it_isnt_sure_of_is_called_a_best_guess_up_front():
    text, ok = honest_result("The price appears to be $34.99, though the page was unclear.", True)
    assert ok and text.startswith("Best guess: The price appears to be $34.99")
    assert honest_result("I'm not sure, but it looks like the 2019 model.", True)[0].startswith("Best guess: ")
    # Said once: a summary that already starts that way is left alone.
    assert honest_result("Best guess: it seems to be the 2019 model.", True)[0] == "Best guess: it seems to be the 2019 model."
    # A firm answer with an aside after it is not a guess (a review of the first version).
    for firm in (
        "The GLADOM is $34.99. I couldn't confirm it's in stock.",
        "The Sony WH-1000XM5 is $298 at Amazon. I couldn't find a lower price at Best Buy or Walmart.",
        "The store is open until 9 PM tonight. You might be able to order ahead.",
        "Your package arrives Thursday, says UPS. I think you'll need to sign for it.",
    ):
        assert honest_result(firm, True) == (firm, True)


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
    job = await _scout(settings, monkeypatch, "I couldn't open the shop's site: it kept timing out.")
    assert job.status == "failed"
    job = await _scout(settings, monkeypatch, "The price appears to be $34.99, though the page was unclear.")
    assert job.status == "done" and job.summary.startswith("Best guess: The price appears to be $34.99")
    job = await _scout(settings, monkeypatch, "No recalls were found for the 2019 Honda Civic.")
    assert (job.status, job.summary) == ("done", "No recalls were found for the 2019 Honda Civic.")
    job = await _scout(settings, monkeypatch, "The GLADOM tray table, at $34.99 on Amazon.ca.")
    assert (job.status, job.summary) == ("done", "The GLADOM tray table, at $34.99 on Amazon.ca.")
    # A booking answers for itself (a confirmation, a card they approved): left alone.
    job = await _scout(settings, monkeypatch, "I couldn't find an earlier slot, so nothing changed.", kind="booking")
    assert job.status == "done" and not job.summary.startswith("Best guess")
