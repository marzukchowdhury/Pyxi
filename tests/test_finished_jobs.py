"""Finished background work is always told, once, and in full.

The morning this was written for: Scout finished comparing nine IKEA tables at 12:23:04 and
Pyxi said nothing; asked a minute later, it had only the first two of the nine.
"""

from __future__ import annotations

import asyncio

from conftest import Recorder
from pyxi.agents.jobs import Job
from pyxi.companion import answers
from pyxi.companion.brain import Brain
from pyxi.companion.notes import result_card
from test_answer_card import Scripted, make_brain, say, settle

TABLES = [
    "LACK side table, white, $43.43, 4.6 stars (251 ratings)",
    "KNARREVIK nightstand, black, $39.96, 4.4 stars (88 ratings)",
    "LISABO desk, ash veneer, $199.00, 4.5 stars (61 ratings)",
    "GLADOM tray table, dark grey, $34.99, 4.7 stars (1,204 ratings)",
    "LINNMON table top with ADILS legs, $74.98, 4.3 stars (317 ratings)",
    "TROTTEN sit/stand desk, $329.00, 4.2 stars (45 ratings)",
    "VITTSJO laptop table, black-brown, $59.00, 4.6 stars (142 ratings)",
    "MICKE desk, white, $119.00, 4.5 stars (903 ratings)",
    "KRAGSTA coffee table, round, $169.00, 4.8 stars (77 ratings)",
]
# The shape of the real summary: a paragraph, then the list, then the two picks with links.
SUMMARY = (
    'I compared the IKEA tables on the first Amazon.ca results page for "IKEA table". I skipped '
    "the sponsored ads (woJKod, LYNCOHOME, HedoAjim, kevinplus, LumFurn) and left out non-table "
    "items (TV stand, desks, nightstands) and the VASAGLE tables, which aren't IKEA. Nothing "
    "was purchased.\n\nThe 9 IKEA tables:\n"
    + "\n".join(f"{i}. {t}" for i, t in enumerate(TABLES, start=1))
    + "\n\nBest reviewed: KRAGSTA coffee table https://www.amazon.ca/dp/B0KRAGSTA1\n"
    "Best value: GLADOM tray table https://www.amazon.ca/dp/B0GLADOM22"
)


def scout_job(brain: Brain, summary: str = SUMMARY, status: str = "done") -> Job:
    job = Job(
        agent_id="scout",
        agent_name="Scout",
        role="browser",
        task="On Amazon.ca, compare the IKEA tables and find the best value.",
        status=status,
        summary=summary,
    )
    brain.jobs.jobs[job.id] = job
    return job


def spoken(rec: Recorder) -> str:
    """Everything said so far, as one string (a line goes out a sentence at a time)."""
    assert all(e["agent"] == "Pyxi" for e in rec.of("say"))
    return " ".join(e["text"] for e in rec.of("say"))


# ------------------------------------------------------------------ finished jobs are told
async def test_a_finished_job_is_spoken_once_with_the_whole_result_on_a_card(settings):
    brain, rec = make_brain(settings)
    job = scout_job(brain)
    await brain._job_done(job)
    await settle(brain)
    # One short line: the card has the rest.
    assert spoken(rec) == "Scout's done. The full result is on the card."
    [shown] = rec.of("answer")
    card = shown["card"]
    assert card["kind"] == "result" and card["agent"] == "Scout" and card["ok"]
    for table in TABLES:  # nine items are nine rows, none cut
        assert table in card["facts"], table
    assert [s["url"] for s in card["sources"]] == [
        "https://www.amazon.ca/dp/B0KRAGSTA1",
        "https://www.amazon.ca/dp/B0GLADOM22",
    ]
    assert not any("https://" in row for row in card["facts"])  # links are links, not text
    assert rec.of("job_ready")[0]["title"] == "Scout finished"  # the notification still comes
    # Told once: the same job reported again says and shows nothing more.
    await brain._job_done(job)
    await settle(brain)
    assert spoken(rec) == "Scout's done. The full result is on the card."
    assert len(rec.of("answer")) == 1


async def test_a_job_that_ends_while_pyxi_is_talking_is_announced_afterwards(settings):
    brain, rec = make_brain(settings)
    job = scout_job(brain)
    async with brain._lock:  # a turn is under way (Pyxi is answering something else)
        await brain._job_done(job)
        await asyncio.sleep(0.6)
        assert not rec.of("say"), "it must not talk over the turn"
        assert rec.of("job_ready") and rec.of("answer")  # the notification and card don't wait
    await brain.set_state("listening")  # and now the user is holding the talk key
    await asyncio.sleep(0.6)
    assert not rec.of("say"), "it must not talk over the user"
    await brain.set_state("idle")
    await asyncio.sleep(0.6)
    assert spoken(rec) == "Scout's done. The full result is on the card."


async def test_a_result_they_already_asked_for_is_not_announced_again(settings):
    model = Scripted([say("Scout's back: the GLADOM tray table is the best value, at $34.99.")])
    brain, rec = make_brain(settings, llm=model)
    await brain.set_state("listening")  # they're already asking as the job ends
    await brain._job_done(scout_job(brain))
    await brain.turn("what's Scout up to?")
    await asyncio.sleep(0.6)
    assert spoken(rec) == "Scout's back: the GLADOM tray table is the best value, at $34.99."


async def test_a_line_dropped_by_the_talk_key_is_said_again(settings):
    brain, rec = make_brain(settings)
    real = brain.tts.synthesize
    presses = []

    async def pressed_mid_line(text, voice=None):
        if not presses:
            presses.append(text)
            brain.interrupt()  # push-to-talk while the line was being voiced: it's dropped
        return await real(text, voice)

    brain.tts.synthesize = pressed_mid_line  # type: ignore[method-assign]
    await brain._job_done(scout_job(brain))
    await asyncio.sleep(0.8)
    assert presses and spoken(rec) == "Scout's done. The full result is on the card."


async def test_a_failed_job_says_so_and_a_stopped_one_stays_quiet(settings):
    brain, rec = make_brain(settings)
    await brain._job_done(scout_job(brain, "That site wouldn't load.", status="failed"))
    await settle(brain)
    assert spoken(rec) == "Scout couldn't finish that. That site wouldn't load."
    assert not rec.of("answer")  # the notification already holds all of a one-line reason
    await brain._job_done(scout_job(brain, "Stopped, as you asked.", status="stopped"))
    await settle(brain)
    assert "Stopped" not in spoken(rec) and len(rec.of("say")) == 2  # they stopped it themselves


async def test_the_next_turn_has_the_whole_result(settings):
    model = Scripted([say("The GLADOM tray table, at $34.99 with 4.7 stars.")])
    brain, _rec = make_brain(settings, llm=model)
    await brain._job_done(scout_job(brain))
    await settle(brain)
    await brain.turn("what did Scout find?")
    prompt = model.prompts[-1]
    for table in TABLES:  # it used to stop at 600 characters: two tables of nine
        assert table in prompt, table
    assert "https://www.amazon.ca/dp/B0GLADOM22" in prompt  # and the link, for "take me there"


def test_the_spoken_line_is_short_and_never_reads_out_addresses_or_scouts_own_words():
    line = answers.spoken_line(
        "Scout", ok=True, title="Scout finished", summary=SUMMARY, on_card=True
    )
    assert line == "Scout's done. The full result is on the card."  # not "I compared..."
    said = answers.spoken_line(
        "Scout",
        ok=True,
        title="Scout finished",
        summary="The GLADOM tray table is the best value at $34.99. Details follow.",
        on_card=False,
    )
    assert said == "Scout's done. The GLADOM tray table is the best value at $34.99."
    assert "http" not in answers.spoken_line(
        "Scout", ok=True, title="Scout finished", summary="See https://x.example/a.", on_card=False
    )
    assert (
        answers.spoken_line(
            "Quill", ok=True, title="Your draft is ready", summary="Dear Sam", on_card=False
        )
        == "Your draft is ready."
    )
    assert answers.spoken_line(
        "Pyxi", ok=False, title="Which Sam?", summary="Sam Lee or Sam Rahman?", on_card=False
    ).startswith("Which Sam?")


def test_a_result_card_keeps_every_line():
    card = result_card("Scout", SUMMARY)
    assert card.title == "Scout's result" and len(card.facts) == 13  # intro, heading, 9, 2 picks
    assert card.facts[2] == TABLES[0] and card.facts[10] == TABLES[8]
    assert card.sources[0].name == "amazon.ca" and card.sources[1].name == "amazon.ca (2)"
    failed = result_card("Ledger", "", ok=False)
    assert failed.title == "Ledger couldn't finish" and failed.facts
