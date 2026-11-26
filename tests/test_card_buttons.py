"""The two general buttons on a result card: Try again and Open Settings.

Try again re-runs the same request or job, says what it will re-run, and is only ever offered
for a failure where nothing was done: it can never send, post, pay or change something a
second time. Open Settings opens Pyxi's own Settings at the part that mends the failure."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from otto.types import LLMResponse, ToolCall, Usage

from pyxi import plainwords as pw
from pyxi.agents.jobs import Job
from pyxi.connectors import Tool, tool
from test_brain_and_server import make_brain


# ---------------------------------------------------------------- which line gets which button
@pytest.mark.parametrize(
    "line",
    [
        pw.OFFLINE, pw.MODEL_OFFLINE, pw.MODEL_BUSY, pw.MODEL_SLOW, pw.SLOW, pw.GOOGLE_BUSY,
        pw.GOOGLE_SLOW, pw.MODEL_DOWN, pw.GOOGLE_DOWN, pw.BETA_DOWN,
        "LinkedIn is asking me to slow down. Try again in a few minutes.",
    ],
)  # fmt: skip
def test_failures_that_pass_get_try_again(line):
    assert pw.action_for(line) == "retry"


@pytest.mark.parametrize(
    ("line", "part"),
    [
        # File access: the converter that is switched on under Your files.
        ("report.doc is an old Office file, and turning it into a PDF needs the free LibreOffice "
         "converter. Turn it on in Settings, under Your files, and ask again.", "files"),
        # "Connect in Settings".
        ("Gmail isn't connected. Connect Google in Settings and ask again.", "accounts"),
        ("Google isn't connected. Connect it in Settings, then ask again.", "accounts"),
        (pw.GOOGLE_REFUSED, "accounts"),
        ("Your LinkedIn sign-in has run out. Open Settings, press Connect next to LinkedIn under "
         "Connected accounts, then ask me once more.", "accounts"),
        # The budget.
        (pw.BUDGET_JOB, "budget"),
        ("I've used up today's budget, so I'm taking a breather. You can raise it in settings.", "budget"),
        # The voice pack.
        (pw.VOICE_PACK_LATER, "voice"),
    ],
)  # fmt: skip
def test_failures_settings_mends_get_open_settings(line, part):
    assert pw.action_for(line) == f"settings:{part}"


def test_the_buttons_that_were_there_keep_their_lines():
    from pyxi.connectors.google import GOOGLE_EXPIRED

    assert pw.action_for(GOOGLE_EXPIRED) == "google"  # not "settings", though it names Settings
    assert pw.action_for(pw.GOOGLE_PERMISSION) == "google"
    assert pw.action_for("Your 3-day trial has ended. Choose a plan to keep going.") == "choose_plan"
    # Nothing to press for these: a file to close, something that just went wrong.
    assert pw.action_for("budget.xlsx is open in another app. Close it there, then ask again.") == ""
    assert pw.action_for(pw.UNKNOWN) == "" and pw.action_for("") == ""
    assert pw.action_for(pw.GOOGLE_NOT_YOURS) == ""


# ---------------------------------------------------------------- the brain decides on Try again
def _job(**over) -> Job:
    base = {
        "agent_id": "scout", "agent_name": "Scout", "role": "browser", "status": "failed",
        "task": "Compare the prices of two laptops on example.com", "summary": pw.MODEL_BUSY,
    }  # fmt: skip
    return Job(**{**base, **over})


async def test_a_job_that_did_nothing_offers_try_again_and_says_what(settings):
    brain, rec = make_brain(settings)
    job = _job()
    await brain._job_done(job)
    card = rec.of("job_ready")[-1]
    assert card["actions"] == ["retry"] and card["ok"] is False
    assert card["retry"] == "Scout: Compare the prices of two laptops on example.com"
    assert card["title"] == "Scout couldn't finish" and card["body"] == pw.MODEL_BUSY


@pytest.mark.parametrize(
    "over",
    [
        {"steps": 3},  # Scout had already clicked through pages
        {"kind": "booking"},  # a booking may have been made
        {"params": {"form": True}},  # a form may have been sent
        {"role": "documents", "agent_name": "Ledger"},  # a file may have been changed
        {"role": "files", "agent_name": "Pixel"},
        {"role": "atlas", "agent_name": "Atlas"},  # mail may have been sent
        {"role": "writer", "agent_name": "Quill", "files": ["letter.docx"]},  # a copy may be saved
    ],
)
async def test_a_job_that_may_have_done_something_never_offers_it(settings, over):
    brain, rec = make_brain(settings)
    job = _job(**over)
    assert not brain.job_untouched(job)
    await brain._job_done(job)
    card = rec.of("job_ready")[-1]
    assert "retry" not in card["actions"] and not card.get("retry")
    assert await brain.retry(job.id) is False  # and nothing is there to press


async def test_try_again_restarts_the_same_job_once(settings, monkeypatch):
    brain, _ = make_brain(settings)
    started: list[tuple] = []

    async def start(agent, task, **kw):
        started.append((agent, task, kw.get("start_url")))

    monkeypatch.setattr(brain.jobs, "start", start)
    job = _job(start_url="https://example.com/laptops")
    await brain._job_done(job)
    assert await brain.retry(job.id) is True
    assert started == [("scout", job.task, "https://example.com/laptops")]
    assert await brain.retry(job.id) is False and len(started) == 1  # pressed twice: once
    assert await brain.retry("no-such-card") is False


async def test_settings_buttons_go_on_a_failed_jobs_card_whatever_it_did(settings):
    brain, rec = make_brain(settings)
    await brain._job_done(
        _job(role="atlas", agent_name="Atlas", summary="Gmail isn't connected. Connect Google in Settings and ask again.")
    )
    card = rec.of("job_ready")[-1]
    assert card["actions"] == ["settings:accounts"] and card["title"] == "Atlas couldn't finish"
    assert not card.get("retry")


async def test_a_card_never_gets_try_again_by_its_words_alone(settings):
    brain, rec = make_brain(settings)
    await brain._notify("x1", ok=False, title="The follow-up didn't send", body=pw.OFFLINE)
    assert rec.of("job_ready")[-1]["actions"] == [] and not rec.of("job_ready")[-1]["retry"]
    # Open Settings needs no such care: it changes nothing.
    await brain._notify("x2", ok=False, title="Couldn't do that yet", body=pw.VOICE_PACK_LATER)
    assert rec.of("job_ready")[-1]["actions"] == ["settings:voice"]


# ---------------------------------------------------------------- a turn
class Offline:
    """A model that can't be reached, after ``first`` (tool calls) if given."""

    def __init__(self, first=None) -> None:
        self.first, self.asked = first, 0

    async def complete(self, **_):
        self.asked += 1
        if self.first and self.asked == 1:
            return LLMResponse(
                tool_calls=[ToolCall(id="1", name=n, arguments=a) for n, a in self.first],
                usage=Usage(),
                stop_reason="tool_use",
            )
        raise httpx.ConnectError("[Errno 11001] getaddrinfo failed")


async def test_a_question_that_never_reached_the_model_can_be_asked_again(settings):
    llm = Offline()
    brain, rec = make_brain(settings, llm=llm)
    await brain.turn("What is the capital of Peru?")
    card = rec.of("job_ready")[-1]
    assert card["actions"] == ["retry"] and card["retry"] == "What is the capital of Peru?"
    assert card["body"] == pw.OFFLINE
    assert " ".join(e["text"] for e in rec.of("say")) == pw.OFFLINE  # said too, a sentence at a time
    asked = llm.asked
    assert await brain.retry(card["job_id"]) is True  # the same words, asked again
    assert llm.asked > asked
    assert brain.turns.recent(1)[0]["user_text"] == "What is the capital of Peru?"


async def test_once_a_tool_has_run_the_turn_is_not_offered_again(settings):
    brain, rec = make_brain(settings, llm=Offline(first=[("remember", {"key": "city", "value": "Lima"})]))
    await brain.turn("Remember that I live in Lima, then tell me the weather")
    assert brain._calls  # something was done before the model went away
    assert not [c for c in rec.of("job_ready") if "retry" in c["actions"]]


async def test_a_routine_is_never_offered_again(settings):
    brain, rec = make_brain(settings, llm=Offline())
    await brain.turn("(Routine: check my email)", synthetic=True)
    assert not [c for c in rec.of("job_ready") if "retry" in c["actions"]]


# ---------------------------------------------------------------- an errand
def _mail_tools(sent: list[str]) -> list[Tool]:
    async def search(args):
        sent.append("searched")
        return "No mail found."

    return [Tool(tool("gmail_search", "Search mail.", {"q": {"type": "string"}}), search, info=True)]


async def test_an_errand_that_failed_before_any_tool_can_be_tried_again(settings, monkeypatch):
    brain, rec = make_brain(settings)
    ran: list[str] = []
    monkeypatch.setattr(brain.connectors, "tools", lambda: _mail_tools(ran))
    monkeypatch.setattr(brain, "_errand_llm", lambda: Offline())
    await brain._run_errand("Email Sam Lee that the heater is fixed", "tell Sam the heater is fixed")
    card = next(c for c in rec.of("job_ready") if not c["ok"])
    assert card["actions"] == ["retry"] and card["retry"] == "Email Sam Lee that the heater is fixed"
    assert ran == []
    again: list[tuple] = []

    async def errand(task, words, **kw):
        again.append((task, words))

    monkeypatch.setattr(brain, "_run_errand", errand)
    assert await brain.retry(card["job_id"]) is True
    assert again == [("Email Sam Lee that the heater is fixed", "tell Sam the heater is fixed")]


async def test_an_errand_that_had_started_working_is_not_offered_again(settings, monkeypatch):
    brain, rec = make_brain(settings)
    ran: list[str] = []
    monkeypatch.setattr(brain.connectors, "tools", lambda: _mail_tools(ran))
    monkeypatch.setattr(brain, "_errand_llm", lambda: Offline(first=[("gmail_search", {"q": "heater"})]))
    await brain._run_errand("Email Sam Lee that the heater is fixed", "tell Sam the heater is fixed")
    await asyncio.sleep(0)
    card = next(c for c in rec.of("job_ready") if not c["ok"])
    assert ran == ["searched"]  # one of its tools ran, so it may have done something
    assert card["actions"] == [] and not card["retry"] and card["body"] == pw.OFFLINE


# ---------------------------------------------------------------- the budget
async def test_the_daily_budget_stop_has_a_card_with_open_settings(settings):
    brain, rec = make_brain(settings)
    brain.turns.add_spend("test", settings.budget.daily_cost_usd + 1)
    await brain.turn("What is the capital of Peru?")
    card = rec.of("job_ready")[-1]
    assert card["actions"] == ["settings:budget"] and card["title"] == "Today's budget is used up"
    assert await brain.retry(card["job_id"]) is False
