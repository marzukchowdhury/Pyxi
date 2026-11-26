"""A failure in a turn or a job reaches the user as what happened and what to do next, with
the button that mends it where there is one. Never "my brain hiccupped" for a cause Pyxi
knows, never a raw error on a card, and never "a teammate is on it" for work that cannot start.
"""

from __future__ import annotations

import pytest
from otto.types import ToolCall

from conftest import Recorder
from pyxi import plainwords as pw
from pyxi.agents.jobs import Job
from pyxi.companion import specialists
from pyxi.companion.brain import Brain
from pyxi.connectors.google import GOOGLE_EXPIRED, GoogleExpired
from test_answer_card import Scripted, make_brain, say, settle
from test_errands import connect


@pytest.fixture(autouse=True)
def no_wait_for_silence(monkeypatch):
    monkeypatch.setattr(Brain, "QUIET_BEFORE_SPEAKING_S", 0.0)


def spoken(rec: Recorder) -> str:
    return " ".join(e["text"] for e in rec.of("say"))


class LLMError(Exception):
    pass


class Broken:
    """A model that fails the way the real one does."""

    model = "broken"

    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    async def complete(self, **kw):
        raise self.exc


ALLOWANCE = LLMError(
    "OpenRouter API error: Error code: 429 - {'error': {'message': \"You've used today's Pyxi "
    "beta allowance. It resets at midnight UTC.\"}}"
)


# ---------------------------------------------------------------- a spoken turn
@pytest.mark.parametrize(
    "exc,line",
    [
        (ALLOWANCE, "You've used today's Pyxi beta allowance. It resets at midnight UTC."),
        (LLMError("OpenRouter API error: Connection error."), pw.MODEL_OFFLINE),
        (LLMError("OpenRouter API error: Request timed out."), pw.MODEL_SLOW),
        (LLMError("OpenRouter API error: Error code: 529 - {'error': {'message': 'Overloaded'}}"), pw.MODEL_BUSY),
        (LLMError("OpenRouter API error: Error code: 401 - {'error': {'message': 'No auth credentials found'}}"), pw.MODEL_KEY),
    ],
)
async def test_a_turn_the_model_cannot_answer_says_why_and_what_to_do(settings, monkeypatch, exc, line):
    monkeypatch.delenv("PYXI_BETA", raising=False)
    brain, rec = make_brain(settings, llm=Broken(exc))
    await brain.turn("what's on my screen?")
    await settle(brain)
    assert spoken(rec) == line
    assert "hiccup" not in spoken(rec) and "Error code" not in spoken(rec)


async def test_a_failure_nobody_knows_still_never_reads_out_the_error(settings):
    brain, rec = make_brain(settings, llm=Broken(KeyError("id")))
    await brain.turn("hello")
    await settle(brain)
    assert spoken(rec) == "Oops, my brain hiccupped. Mind trying that again?"


# ---------------------------------------------------------------- mail and calendar in a turn
async def test_mail_with_an_expired_sign_in_shows_the_sign_in_card(settings):
    """The fix of 26 November covered jobs and edit_document. A turn that reads mail or the
    calendar got "gmail_search failed: ('invalid_grant: ..." as a note, and the model read
    out whatever it made of that."""
    llm = Scripted(
        [ToolCall(id="g", name="gmail_search", arguments={"query": "from:sam@example.com"})],
        [say("Your Google sign-in has run out. Press Sign in to Google.")],
    )
    brain, rec = make_brain(settings, llm=llm)
    connect(brain)

    async def refused(method, url, **kw):
        brain.connectors.google.expired = True
        raise GoogleExpired(GOOGLE_EXPIRED)

    brain.connectors.google._api = refused
    await brain.turn("any mail from Sam?")
    await settle(brain)
    [card] = [e for e in rec.of("job_ready") if e["job_id"] == "google-sign-in"]
    assert card["actions"] == ["google"] and card["body"] == GOOGLE_EXPIRED and not card["ok"]
    note = llm.prompts[-1]
    assert "gmail_search failed: Your Google sign-in has run out." in note
    assert "Sign in to Google button is on screen" in note and "invalid_grant" not in note
    # Settings stops calling the account fine.
    assert rec.of("connectors")[-1]["connectors"][0]["needs"] == "Sign in again"


async def test_a_calendar_call_without_the_permission_shows_the_button_too(settings):
    llm = Scripted(
        [ToolCall(id="c", name="calendar_list", arguments={"days": 2})],
        [say("Google needs your OK first.")],
    )
    brain, rec = make_brain(settings, llm=llm)
    connect(brain)

    async def no_scope(method, url, **kw):
        raise RuntimeError("Google said 403: Request had insufficient authentication scopes.")

    brain.connectors.google._api = no_scope
    await brain.turn("what's on tomorrow?")
    await settle(brain)
    [card] = [e for e in rec.of("job_ready") if e["actions"] == ["google"]]
    assert card["title"] == "Google needs your OK" and card["body"] == pw.GOOGLE_PERMISSION
    assert "insufficient" not in llm.prompts[-1] and pw.GOOGLE_PERMISSION in llm.prompts[-1]


async def test_a_tool_error_nobody_knows_is_for_the_model_only(settings):
    brain, _ = make_brain(settings)
    note = await brain._tool_failed("calendar_list", KeyError("items"))
    assert note.startswith("calendar_list failed:") and "never read it out" in note


# ---------------------------------------------------------------- background jobs
def failed_job(brain: Brain, summary: str, role: str = "documents", name: str = "Ledger") -> Job:
    job = Job(agent_id=name.lower(), agent_name=name, role=role, task="Build the pivot", status="failed", summary=summary)
    brain.jobs.jobs[job.id] = job
    return job


async def test_a_job_google_refused_for_a_missing_permission_carries_the_button(settings):
    brain, rec = make_brain(settings)
    await brain._job_done(failed_job(brain, pw.GOOGLE_PERMISSION))
    [card] = rec.of("job_ready")
    assert card["title"] == "Google needs your OK" and card["actions"] == ["google"]
    assert card["body"] == pw.GOOGLE_PERMISSION


async def test_an_errand_that_breaks_shows_plain_words_and_the_button(settings, monkeypatch):
    from pyxi.companion import brain as brain_mod

    async def broken(*a, **kw):
        raise RuntimeError("('invalid_grant: Token has been expired or revoked.', {'error': 'invalid_grant'})")

    monkeypatch.setattr(brain_mod, "run_errand", broken)
    brain, rec = make_brain(settings)
    connect(brain)
    await brain._run_errand("email Sam the notes", "email sam@example.com the notes")
    await settle(brain)
    [card] = rec.of("job_ready")
    assert card["body"] == GOOGLE_EXPIRED and card["actions"] == ["google"]
    assert card["title"] == "Sign in to Google again" and "invalid_grant" not in str(card)


async def test_an_errand_that_breaks_for_no_known_reason_shows_no_python(settings, monkeypatch):
    from pyxi.companion import brain as brain_mod

    async def broken(*a, **kw):
        raise KeyError("threadId")

    monkeypatch.setattr(brain_mod, "run_errand", broken)
    brain, rec = make_brain(settings)
    connect(brain)
    await brain._run_errand("email Sam the notes", "email sam@example.com the notes")
    await settle(brain)
    [card] = rec.of("job_ready")
    assert card["body"] == pw.UNKNOWN and "threadId" not in str(card)


# ---------------------------------------------------------------- known before it starts
@pytest.mark.parametrize("tool,args", [("travel", {"trip": "Lisbon"}), ("money", {"action": "summary"})])
async def test_no_teammate_is_started_on_google_work_once_the_sign_in_is_known_dead(settings, tool, args):
    brain, rec = make_brain(settings)
    connect(brain)
    brain.connectors.google.expired = True
    note = await specialists.handle(brain, tool, args)
    assert note.startswith(f"{tool}: their Google sign-in has run out")
    assert "Don't say anyone is working on it" in note
    assert not brain.jobs.jobs  # nobody was started
    [card] = rec.of("job_ready")
    assert card["actions"] == ["google"]


@pytest.mark.parametrize("tool", ["errand", "subscriptions", "returns"])
async def test_mail_work_is_not_promised_either(settings, tool):
    brain, rec = make_brain(settings)
    connect(brain)
    brain.connectors.google.expired = True
    note = await brain._act(tool, {"task": "email Sam the notes"})
    assert note and note.startswith(f"{tool}: their Google sign-in has run out")
    await settle(brain)
    assert not brain.jobs.jobs and [c["actions"] for c in rec.of("job_ready")] == [["google"]]


async def test_a_spent_budget_says_where_to_raise_it(settings):
    brain, _ = make_brain(settings)
    connect(brain)
    brain.turns.spent_today = lambda: 999.0
    note = await brain._act("errand", {"task": "email Sam"})
    assert "raise the daily limit in Settings" in note
