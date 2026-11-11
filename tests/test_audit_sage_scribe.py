"""Sage and Scribe, after the audit of the five specialists.

Sage: a quiz on a named topic is on that topic, long notes say how much of them was read,
.markdown notes are read, and a count said in words is a count. Scribe: the transcript
outlives a failed notes call, a follow-up that can't be drafted or was sent is said aloud,
and the recording never goes to the cloud transcriber.

Scripted models, a fake Whisper and a fake Gmail: no network, no model, no real files.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from pyxi.agents import sage, scribe
from pyxi.companion import specialists
from pyxi.companion.brain import Brain
from test_answer_card import make_brain, settle
from test_connectors_and_docs import Script
from test_mailwatch import connected, until
from test_scribe import NOTES, STANDUP, FakeModel, FakeSTT, recording

HEART = '{"title": "The heart", "cards": [{"q": "How many chambers?", "a": "Four."}]}'


@pytest.fixture(autouse=True)
def no_wait_for_silence(monkeypatch):
    monkeypatch.setattr(Brain, "QUIET_BEFORE_SPEAKING_S", 0.0)


def said(rec) -> list[str]:
    return [e["text"] for e in rec.of("say")]


def history_deck(settings) -> sage.Deck:
    deck = sage.Deck(
        "French Revolution",
        "History week 3.pptx",
        [sage.Card("When did the Bastille fall?", "14 July 1789.")],
    )
    sage.save_deck(settings.data_dir, deck)
    return deck


# ================================================================== Sage
async def test_a_quiz_on_a_topic_is_on_that_topic(settings):
    """Audit: "quiz me on the heart", with only a French Revolution deck saved, was a quiz
    on the French Revolution. A topic deck was only ever made when no deck existed at all."""
    history_deck(settings)
    llm = Script(HEART, HEART)
    brain, _rec, _ = connected(settings, [], llm=llm)
    out = await specialists.handle(brain, "quiz", {"action": "start", "deck": "the heart"})
    assert out.startswith("quiz: on The heart, 1 questions") and "How many chambers?" in out
    assert len(llm.prompts) == 1 and "the heart" in llm.prompts[0]
    # Asked again, the deck made a moment ago is found: no second model call.
    again = await specialists.handle(brain, "quiz", {"action": "start", "deck": "the heart"})
    assert again.startswith("quiz: on The heart") and len(llm.prompts) == 1
    # A deck they do have is still found by a word of its title or its file.
    theirs = await specialists.handle(brain, "quiz", {"action": "start", "deck": "the revolution"})
    assert theirs.startswith("quiz: on French Revolution") and len(llm.prompts) == 1


def test_which_deck_they_mean(settings):
    """Words in any script name a topic (the audit's 生物学 got the newest deck); words like
    "slides" and "chapter" point at a deck without naming one, so the newest still answers."""
    assert sage.find_deck(settings.data_dir, "the heart", strict=True) is None  # no decks
    history_deck(settings)
    for topic in ("the heart", "生物学", "Économie"):
        assert sage.find_deck(settings.data_dir, topic, strict=True) is None, topic
        assert sage.find_deck(settings.data_dir, topic).title == "French Revolution"  # as before
    for vague in ("", "my slides", "chapter 3", "the last one", "that deck again"):
        assert sage.find_deck(settings.data_dir, vague, strict=True).title == "French Revolution"
    sage.save_deck(settings.data_dir, sage.Deck("细胞", r"C:\notes\生物学.pdf", [sage.Card("q", "a")]))
    assert sage.find_deck(settings.data_dir, "生物学", strict=True).title == "细胞"
    assert sage.find_deck(settings.data_dir, "history", strict=True).title == "French Revolution"
    # The topic word wins over a word any deck's file could have.
    assert sage.find_deck(settings.data_dir, "the history slides", strict=True).title == (
        "French Revolution"
    )


def biology_decks(settings) -> None:
    sage.save_deck(
        settings.data_dir,
        sage.Deck(
            "The Cardiovascular System",
            "C:/notes/Lecture 5.pptx",
            [
                sage.Card("How many chambers does the heart have?", "Four."),
                sage.Card("What does the mitral valve stop?", "Blood flowing back in the heart."),
            ],
        ),
    )
    sage.save_deck(
        settings.data_dir,
        sage.Deck(
            "Cell Structure and Function",
            "C:/notes/BIO101 week3.pdf",
            [
                sage.Card("Which organelle makes ATP?", "The mitochondrion."),
                sage.Card("What do ribosomes make?", "Proteins."),
            ],
        ),
    )


def test_their_own_deck_is_found_by_another_form_of_the_word(settings):
    """Review: with a deck called "Cell Structure and Function" from BIO101 week3.pdf,
    "quiz me on cells" (and on biology, organelles, the heart) matched nothing, so their own
    notes were passed over and a deck was made up from general knowledge."""
    biology_decks(settings)
    cells, heart = "Cell Structure and Function", "The Cardiovascular System"
    for said, title in (
        ("cells", cells),  # the plural of a word in the title
        ("cell", cells),
        ("biology", cells),  # the subject the course code BIO101 stands for
        ("my biology notes", cells),
        ("organelles", cells),  # what its cards are about
        ("ribosome", cells),
        ("the heart", heart),  # in its cards, in neither its title nor its file
        ("cardio", heart),
        ("the cardiovascular stuff", heart),
        ("the circulatory system", heart),
        ("lecture 5", heart),
        ("week 3", cells),
    ):
        found = sage.find_deck(settings.data_dir, said, strict=True)
        assert found is not None and found.title == title, said
    # Nothing of theirs is about these: no deck, so one can be made (and said to be made up).
    for said in ("anatomy", "the french revolution", "history", "celery", "生物学"):
        assert sage.find_deck(settings.data_dir, said, strict=True) is None, said
    assert sage.singular("cells") == "cell" and sage.singular("theories") == "theory"
    assert sage.singular("classes") == "class" and sage.singular("class") == "class"
    assert sage.about("biological", ["biology"]) and not sage.about("history", ["histology"])
    assert sage.about("chemistry", ["chem200"]) and not sage.about("weekend", ["week3"])


async def test_a_quiz_uses_their_notes_and_says_when_the_cards_are_not_theirs(settings):
    """Review, the same fault end to end: "quiz me on cells" built a general-knowledge deck
    beside their own. Now their deck is used, and a topic none of their decks covers says
    the cards are from general knowledge, not their notes."""
    biology_decks(settings)
    # A deck made up for "cells" by the build that had the fault is still on the PC, newer.
    sage.save_deck(
        settings.data_dir, sage.Deck("Cells", "cells.topic", [sage.Card("A made-up card?", "Yes.")])
    )
    llm = Script('{"title": "Anatomy", "cards": [{"q": "How many bones?", "a": "206."}]}')
    brain, _rec, _ = connected(settings, [], llm=llm)
    out = await specialists.handle(brain, "quiz", {"action": "start", "deck": "cells"})
    assert out.startswith("quiz: on Cell Structure and Function, 2 questions.")
    assert "general knowledge" not in out and llm.prompts == []  # their notes, no model call
    out = await specialists.handle(brain, "quiz", {"action": "start", "deck": "the heart"})
    assert out.startswith("quiz: on The Cardiovascular System") and llm.prompts == []
    out = await specialists.handle(brain, "quiz", {"action": "start", "deck": "anatomy"})
    assert out.startswith("quiz: on Anatomy, 1 questions.") and len(llm.prompts) == 1
    assert "from general knowledge, not their notes" in out
    again = await specialists.handle(brain, "quiz", {"action": "start", "deck": "anatomy"})
    assert "from general knowledge, not their notes" in again and len(llm.prompts) == 1


async def test_a_topic_deck_with_no_notes_at_all_needs_no_caveat(settings):
    """Nothing of theirs to mistake it for, and the French Revolution case still holds."""
    llm = Script(HEART, HEART)
    brain, _rec, _ = connected(settings, [], llm=llm)
    out = await specialists.handle(brain, "quiz", {"action": "start", "deck": "the heart"})
    assert out.startswith("quiz: on The heart") and "general knowledge" not in out
    history_deck(settings)
    out = await specialists.handle(brain, "quiz", {"action": "start", "deck": "the heart"})
    assert out.startswith("quiz: on The heart") and "French Revolution" not in out
    assert "from general knowledge, not their notes" in out  # now they have notes of their own


async def test_when_no_topic_deck_can_be_made_their_latest_is_named_as_such(settings):
    history_deck(settings)
    brain, _rec, _ = connected(settings, [], llm=Script("Sorry, I can't."))
    out = await specialists.handle(brain, "quiz", {"action": "start", "deck": "the heart"})
    assert out.startswith("quiz: on French Revolution, 1 questions.")
    assert "no cards on 'the heart'" in out and "latest deck" in out


async def test_long_notes_say_how_much_of_them_was_read(settings, tmp_path, monkeypatch):
    """Audit: of a 230,000-character textbook, chapters 1 to 61 of 200 were read, and the
    notice said "4 cards" as if they covered the book."""
    monkeypatch.setattr(sage, "out_dir", lambda: tmp_path / "Study")
    book = tmp_path / "textbook.txt"
    chapters = [f"Chapter {i}. " + f"Fact about topic number {i}. " * 40 for i in range(1, 201)]
    book.write_text("\n\n".join(chapters), encoding="utf-8")
    reply = '{"title": "Textbook", "cards": [{"q": "Q%d?", "a": "A."}]}'
    brain, rec, _ = connected(settings, [], llm=Script(*[reply % i for i in range(8)]))
    await specialists.handle(brain, "make_flashcards", {"files": [str(book)]})
    [ready] = await until(rec, "job_ready")
    assert len(brain.llm.prompts) == sage.MAX_PARTS
    assert ready["body"].startswith("4 cards. Only the first part of textbook.txt was read")
    assert ready["notes"] and "cover the beginning" in ready["notes"][0]
    job = next(iter(brain.jobs.jobs.values()))
    assert "Only the first part of textbook.txt was read" in job.summary
    # Pages and slides say where the cards stop.
    assert sage.read_up_to("[page 30] x\n[page 31] y") == "page 31" and sage.read_up_to("x") == ""
    # Short notes are read whole, and nothing is said about parts.
    short = tmp_path / "short.txt"
    short.write_text("The heart has four chambers. " * 10, encoding="utf-8")
    await specialists.handle(brain, "make_flashcards", {"files": [str(short)]})
    ready = (await until(rec, "job_ready", 2))[-1]
    assert ready["body"].startswith("1 cards. Say") and ready["notes"] == []


async def test_markdown_notes_are_read(settings, tmp_path, monkeypatch):
    """Audit: bio.markdown was accepted, then failed with "I can't read .markdown files." """
    monkeypatch.setattr(sage, "out_dir", lambda: tmp_path / "Study")
    notes = tmp_path / "bio.markdown"
    notes.write_text("# The heart\n\n" + "The heart has four chambers. " * 20, encoding="utf-8")
    llm = Script(HEART)
    brain, rec, _ = connected(settings, [], llm=llm)
    await specialists.handle(brain, "make_flashcards", {"files": [str(notes)]})
    [ready] = await until(rec, "job_ready")
    assert ready["ok"] and ready["title"] == "Flashcards: The heart"
    assert "The heart has four chambers." in llm.prompts[0]


async def test_a_count_said_in_words_is_a_count(settings, tmp_path, monkeypatch):
    """Audit: count "twenty" raised ValueError inside the tool, before any job started."""
    monkeypatch.setattr(sage, "out_dir", lambda: tmp_path / "Study")
    said_as = {12: 12, "12": 12, "twenty": 20, "about 15 cards": 15, "twenty five": 25,
               "a dozen": 12, "a few": 7, None: 7, 0: 7, "": 7, True: 7}  # fmt: skip
    assert {k: sage.as_count(k, 7) for k in said_as} == said_as
    notes = tmp_path / "n.txt"
    notes.write_text("The heart has four chambers. " * 10, encoding="utf-8")
    brain, rec, _ = connected(settings, [], llm=Script(HEART))
    out = await specialists.handle(
        brain, "make_flashcards", {"files": [str(notes)], "count": "twelve"}
    )
    assert out.startswith("Started job_")
    await until(rec, "job_ready")
    assert next(iter(brain.jobs.jobs.values())).params["count"] == 12
    assert brain.llm.prompts[0].startswith("About 12 cards")
    saved = sage.save_deck(settings.data_dir, sage.Deck("T", "t.txt", [sage.Card(f"q{i}", "a") for i in range(9)]))
    # Clearly the newest deck (two files written in the same instant have no order of their own).
    import os
    import time

    os.utime(saved, (time.time() + 5, time.time() + 5))
    out = await specialists.handle(brain, "quiz", {"action": "start", "questions": "three"})
    assert "3 questions" in out


# ================================================================== Scribe
class Busy:
    """A model that is over its limit."""

    prompts: list[str] = []

    async def complete(self, **kw):
        raise RuntimeError("HTTP 429: rate limit exceeded")


@pytest.mark.parametrize("llm", [Script("not notes at all"), Busy()])
async def test_the_transcript_outlives_a_failed_notes_call(settings, tmp_path, monkeypatch, llm):
    """Audit: the recording was transcribed, the notes call failed, and nothing was saved:
    asking again meant listening to the whole recording again."""
    monkeypatch.setattr(scribe, "out_dir", lambda: tmp_path / "Meetings")
    rec_path = recording(tmp_path / "standup.wav", 2.0)
    brain, rec, _ = connected(settings, [], llm=llm)
    brain.stt = FakeSTT()
    await specialists.handle(brain, "meeting_notes", {"file": str(rec_path)})
    [ready] = await until(rec, "job_ready")
    assert not ready["ok"] and ready["title"] == "Transcript saved, no notes yet"
    assert "saved as standup transcript.txt" in ready["body"]
    [kept] = [Path(f) for f in ready["files"]]
    assert kept.parent == tmp_path / "Meetings" and "[00:00:23] Priya, can you" in kept.read_text(
        encoding="utf-8"
    )
    assert [p.name for p in (tmp_path / "Meetings").iterdir()] == ["standup transcript.txt"]
    await settle(brain)
    assert said(rec)[0] == "Transcript saved, no notes yet."
    if isinstance(llm, Busy):  # why, in plain words, not the model's error
        assert ready["body"].startswith("The AI model is busy right now.")


async def test_a_follow_up_that_cannot_be_drafted_is_said(settings, tmp_path, monkeypatch):
    """Audit: asked to send the follow-up with Gmail not connected, the notes said "nothing
    is sent unless you allow it" and no card ever came. Nothing said why."""
    monkeypatch.setattr(scribe, "out_dir", lambda: tmp_path / "Meetings")
    rec_path = recording(tmp_path / "standup.wav", 1.0)
    brain, rec = make_brain(settings, llm=Script(json.dumps(NOTES)))  # Google isn't connected
    brain.stt = FakeSTT()
    await specialists.handle(
        brain, "meeting_notes", {"file": str(rec_path), "to": ["priya@example.com"]}
    )
    ready = await until(rec, "job_ready", 2)
    assert [r["title"] for r in ready] == [
        "Notes: Beta launch standup",
        "The follow-up needs Gmail connected",
    ]
    assert "Gmail is connected in Settings" in ready[1]["body"] and not rec.of("confirm")
    for _ in range(150):
        if len(said(rec)) >= 2:
            break
        await asyncio.sleep(0.02)
    assert sorted(said(rec)) == ["Notes: Beta launch standup.", "The follow-up needs Gmail connected."]
    # No `to`: they didn't ask for it to be sent, so there is nothing to say.
    brain2, rec2 = make_brain(
        settings.model_copy(update={"data_dir": tmp_path / "d2"}), llm=Script(json.dumps(NOTES))
    )
    brain2.stt = FakeSTT()
    await specialists.handle(brain2, "meeting_notes", {"file": str(rec_path)})
    await until(rec2, "job_ready")
    await brain2.jobs.wait_all()
    await settle(brain2)
    assert [r["title"] for r in rec2.of("job_ready")] == ["Notes: Beta launch standup"]


async def test_the_follow_up_they_allowed_is_said_when_it_is_sent(settings, tmp_path, monkeypatch):
    """Audit: "Follow-up sent" was a notice on the notch for a few seconds and no more."""
    monkeypatch.setattr(scribe, "out_dir", lambda: tmp_path / "Meetings")
    rec_path = recording(tmp_path / "call.wav", 1.0)
    brain, rec, fake = connected(settings, [], llm=Script(json.dumps(NOTES)))
    brain.stt = FakeSTT()
    sent: list[dict] = []

    async def api(method, url, **kw):
        if method == "POST":
            sent.append(kw["json"])
            return {"id": "sent1"}
        return await fake(method, url, **kw)

    brain.connectors.google._api = api  # type: ignore[method-assign]
    await specialists.handle(
        brain, "meeting_notes", {"file": str(rec_path), "to": ["priya@example.com"]}
    )
    [card] = await until(rec, "confirm")
    assert sent == []
    brain.hub.resolve(card["request_id"], True)
    for _ in range(150):
        if "Follow-up sent." in said(rec):
            break
        await asyncio.sleep(0.02)
    assert len(sent) == 1 and "Follow-up sent." in said(rec)


async def test_a_recording_never_goes_to_the_cloud_transcriber(tmp_path, monkeypatch):
    """Push-to-talk now listens in the cloud by default (voice/stt.py). A meeting must not:
    with the cloud allowed and reachable, Scribe still only uses the model on this PC."""
    import httpx

    from pyxi.config import VoiceConfig
    from pyxi.voice import stt as stt_mod

    monkeypatch.setenv("OPENROUTER_API_KEY", "not-a-real-key")
    cloud: list[str] = []

    async def to_the_cloud(self, samples, language, hints=None):
        cloud.append(language)
        raise AssertionError("a recording was sent to the cloud")

    def no_http(*a, **k):
        raise AssertionError("an HTTP client was opened")

    monkeypatch.setattr(stt_mod.CloudEars, "transcribe", to_the_cloud)
    monkeypatch.setattr(httpx, "AsyncClient", no_http)
    path = recording(tmp_path / "standup.wav", 1.0)
    for language in ("en", "auto", "bn", "es"):
        ears = stt_mod.Transcriber(VoiceConfig(stt_device="cpu"))
        assert ears.listens_in_the_cloud()  # what push-to-talk would do
        model = FakeModel(STANDUP)
        ears._models[ears.model_for(language)] = model  # the model on this PC, never loaded
        segments = await scribe.transcribe(ears, path, language)
        assert len(segments) == len(STANDUP) and model.audio_seconds > 0.9
    assert cloud == []
