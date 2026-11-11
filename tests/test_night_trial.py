"""The founder's trial of 10 November, 8 PM: what a finished job says and shows.

Three things from that evening. Ledger's notice carried the sheet's address inside its sentence,
which made it "too long", so the same words came up a second time on a card beside it. A guide
step was framed on screen while Pyxi said "Hmm, I'm not sure what to say to that." And a result
that arrived after an Allow card was a ten second notice and nothing else.
"""

from __future__ import annotations

import pytest
from otto.types import ToolCall

from pyxi.agents.jobs import Job
from pyxi.companion.brain import Brain, guide_step_words
from test_answer_card import Scripted, make_brain, settle


@pytest.fixture(autouse=True)
def no_wait_for_silence(monkeypatch):
    monkeypatch.setattr(Brain, "QUIET_BEFORE_SPEAKING_S", 0.0)


LINK = "https://docs.google.com/spreadsheets/d/1gX0omQm85beTaXMbT58/edit"


def ledger_job(brain: Brain, summary: str) -> Job:
    job = Job(
        agent_id="ledger",
        agent_name="Ledger",
        role="documents",
        task="On the Pivot tab, insert a column chart of the pivot table summary.",
        status="done",
        summary=summary,
    )
    job.data = {"changes": ["Added a column chart"], "notes": [], "links": [LINK], "outputs": []}
    brain.jobs.jobs[job.id] = job
    return job


async def test_ledgers_link_is_a_button_and_its_result_is_shown_once(settings):
    brain, rec = make_brain(settings)
    job = ledger_job(
        brain,
        "Set 21 cells on Sheet1; made a pivot table on tab Pivot: sum of Sales by Region. "
        f"Edited in Google (File > Version history has the old one): {LINK}",
    )
    await brain._job_done(job)
    await settle(brain)
    [ready] = rec.of("job_ready")
    assert ready["link"] == LINK and "link" in ready["actions"]
    assert "http" not in ready["body"] and "Version history" not in ready["body"]
    assert ready["body"].startswith("Set 21 cells on Sheet1")
    assert rec.of("answer") == []  # no second copy of the same words on a card
    assert [s["text"] for s in rec.of("say")] == ["Ledger's done."]
    # The model still has the address, for "this sheet" a minute later.
    assert LINK in brain.history[-1][0]


async def test_a_new_google_file_says_so_without_its_address(settings):
    brain, rec = make_brain(settings)
    job = ledger_job(brain, f"Made a budget sheet. It's a new file in your Google Drive: {LINK}")
    await brain._job_done(job)
    await settle(brain)
    [ready] = rec.of("job_ready")
    assert ready["body"] == "Made a budget sheet. It's a new file in your Google Drive."
    assert ready["link"] == LINK


async def test_a_long_result_from_ledger_still_gets_its_card(settings):
    brain, rec = make_brain(settings)
    job = ledger_job(
        brain,
        "Set 21 cells on Sheet1. Made a pivot table on tab Pivot. Added a chart on tab Pivot. "
        f"Sorted by Sales. Edited in Google (File > Version history has the old one): {LINK}",
    )
    await brain._job_done(job)
    await settle(brain)
    assert len(rec.of("answer")) == 1  # four sentences don't fit the notice: the card holds them


def test_a_guide_step_with_no_words_is_said_as_it_stands():
    step = {"tool": "guide", "arguments": {"action": "click", "label": "Advanced startup: Restart now"}}
    assert guide_step_words([step]) == "Click Advanced startup: Restart now."
    scroll = {"tool": "guide", "arguments": {"action": "scroll", "direction": "down", "label": "Recovery (scroll down)"}}
    assert guide_step_words([scroll]) == "Scroll down to Recovery."
    assert guide_step_words([{"tool": "guide", "arguments": {"action": "press", "text": "ctrl+s"}}]) == "Press ctrl+s."
    assert guide_step_words([{"tool": "guide", "arguments": {"done": True}}]) == "That's it, you're done."
    assert guide_step_words([{"tool": "look_again", "arguments": {}}]) == ""


async def test_pyxi_says_the_step_it_marked(settings):
    guide = ToolCall(id="g", name="guide", arguments={"goal": "Open the firmware settings", "action": "click", "label": "Recovery"})
    brain, rec = make_brain(settings, llm=Scripted([guide]))
    await brain.turn("how do I get to the BIOS settings?")
    said = [s["text"] for s in rec.of("say")]
    assert said == ["Click Recovery."]


async def test_a_result_that_comes_after_an_allow_card_is_said(settings):
    brain, rec = make_brain(settings)
    await brain._notify("job_1", ok=True, title="Done 3 more", body="Renamed 3 files in Invoices.", say=True)
    await brain._notify("job_2", ok=True, title="Check-in opens tomorrow", body="I'll do it then.")
    await settle(brain)
    assert [s["text"] for s in rec.of("say")] == ["Done 3 more."]
    assert [r["title"] for r in rec.of("job_ready")] == ["Done 3 more", "Check-in opens tomorrow"]


async def test_a_file_that_is_not_there_is_said_and_no_teammate_is_started(settings, tmp_path):
    """Audit: the path was mistyped by one letter, Quill was started with no file at all, wrote
    a "draft" from the task line, and the card said "Your draft is ready"."""
    brain, _ = make_brain(settings)
    real = tmp_path / "Resume.docx"
    real.write_bytes(b"x")
    note = await brain._act(
        "delegate", {"agent": "quill", "task": "Fix the grammar in Resume.docx", "files": [str(tmp_path / "Resum.docx")]}
    )
    assert note and note.startswith("delegate: I can't find Resum.docx")
    assert not brain.jobs.jobs
    # Ditto repeats what it was shown: a task in words gives it nothing to go on.
    note = await brain._act("delegate", {"agent": "ditto", "task": "Rename my invoices"})
    assert note and note.startswith("delegate: Ditto repeats what it has watched")
    assert not brain.jobs.jobs


async def test_a_pdfs_corrections_are_not_called_a_fixed_document(settings, tmp_path):
    brain, rec = make_brain(settings)
    listed = tmp_path / "Resume (corrections by Pyxi).txt"
    listed.write_text("1. exel -> Excel", encoding="utf-8")
    job = Job(agent_id="quill", agent_name="Quill", role="writer", task="Check the grammar in Resume.pdf", status="done")
    job.summary = "Resume.pdf is a PDF, and a PDF's layout can't be edited. 1 correction to make."
    job.data = {"fixed": [str(listed)], "changes": ["exel -> Excel"], "notes": []}
    brain.jobs.jobs[job.id] = job
    await brain._job_done(job)
    await settle(brain)
    [ready] = rec.of("job_ready")
    assert ready["title"] == "Corrections for your PDF"
    assert "The PDF itself is unchanged" in ready["body"] and "fixed" not in ready["body"]


async def test_one_document_that_fails_does_not_hide_the_one_that_was_fixed(settings, tmp_path, monkeypatch):
    from types import SimpleNamespace

    from pyxi.agents import jobs as jobs_mod

    good, bad = tmp_path / "Letter.docx", tmp_path / "Broken.docx"

    async def fix(doc, task, llm, mode="fix"):
        if doc == bad:
            raise RuntimeError("Broken.docx isn't a Word file I can open.")
        return SimpleNamespace(
            path=tmp_path / "Letter (fixed by Pyxi).docx", changes=["teh -> the"], notes=[],
            changed=14, total=20, cost_usd=0.0, listed=False,
        )

    monkeypatch.setattr(jobs_mod, "fix_document", fix)
    brain, _ = make_brain(settings)
    job = Job(agent_id="quill", agent_name="Quill", role="writer", task="Fix the grammar in both", status="running")
    await brain.jobs._fix_documents(job, [good, bad], llm=None)
    assert job.status == "done" and job.outputs == [str(tmp_path / "Letter (fixed by Pyxi).docx")]
    assert "But Broken.docx" in job.summary and job.data["count"] == 14
    # When nothing at all could be done, the job fails as before.
    job = Job(agent_id="quill", agent_name="Quill", role="writer", task="Fix it", status="running")
    with pytest.raises(RuntimeError):
        await brain.jobs._fix_documents(job, [bad], llm=None)


async def test_what_they_say_is_not_a_subscription_comes_off_and_stays_off(settings):
    """Live: "It's not a subscription I'm paying", and all Pyxi could do was run the same search
    and bring the same list back, The Startup Club and all."""
    from test_mailwatch import connected, receipts, until

    brain, rec, _ = connected(settings, receipts())
    await brain._watch_subscriptions()
    await until(rec, "job_ready")
    before = [s.service for s in brain.mailwatch.subscriptions()]
    assert "Netflix" in before
    note = await brain._act("subscriptions", {"action": "not_one", "service": "netflix"})
    assert note and note.startswith("subscriptions: Netflix is off the list for good")
    assert "Netflix" not in [s.service for s in brain.mailwatch.subscriptions()]
    # The next search leaves it out too.
    await brain._watch_subscriptions()
    assert "Netflix" not in [s.service for s in brain.mailwatch.subscriptions()]
    assert len(brain.mailwatch.subscriptions()) == len(before) - 1
    # A name that is not on the list is said, not guessed at.
    note = await brain._act("subscriptions", {"action": "not_one", "service": "Blorbo Plus"})
    assert note and "nothing on the last list matches" in note


async def test_the_subscription_list_is_said_and_put_on_a_card(settings):
    """Live: "What did Pyxi do? I didn't see anything." The list was a ten second notice."""
    from test_mailwatch import connected, receipts, until

    brain, rec, _ = connected(settings, receipts())
    await brain._watch_subscriptions()
    await until(rec, "answer")
    await settle(brain)
    said = " ".join(s["text"] for s in rec.of("say"))
    assert said == "$110.45 a month in subscriptions. The full result is on the card."
    [card] = rec.of("answer")
    assert "Netflix" in str(card)
