"""Sage, the study teammate: slides, PDFs and notes into flashcards and a quiz, and a quiz out
loud one question per turn.

The sample files are made here: a real .pptx lecture (python-pptx) and a real PDF (reportlab),
so reading goes through the same code as the user's own files. The model is scripted.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from pptx import Presentation
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from pyxi.agents import sage
from pyxi.companion import specialists
from test_connectors_and_docs import Script
from test_mailwatch import connected, until

SLIDES = [
    ("Lecture 4: The heart", "BIOL 112, Dr. Chen"),
    ("Four chambers", "Two atria receive blood. Two ventricles pump it out."),
    (
        "Valves",
        "The mitral valve stops blood flowing back from the left ventricle into the left atrium.",
    ),
    (
        "Two circuits",
        "Pulmonary circuit: right ventricle to the lungs. Systemic circuit: left ventricle to the body.",
    ),
]

DECK = {
    "title": "The heart",
    "cards": [
        {
            "q": "How many chambers does the heart have?",
            "a": "Four: two atria and two ventricles.",
            "where": "slide 2",
        },
        {
            "q": "What does the mitral valve stop?",
            "a": "Blood flowing back from the left ventricle into the left atrium.",
            "where": "slide 3",
        },
        {
            "q": "Where does the right ventricle pump blood?",
            "a": "To the lungs (the pulmonary circuit).",
            "where": "slide 4",
        },
        {
            "q": "Where does the left ventricle pump blood?",
            "a": "To the body — the systemic circuit.",
            "where": "slide 4",
        },
        {"q": "how many chambers does the heart have", "a": "Four.", "where": ""},  # a repeat
        {"q": "", "a": "no question"},
    ],
    "quiz": [
        {
            "q": "Which chamber pumps blood to the body?",
            "choices": ["Right atrium", "Left ventricle", "Right ventricle", "Left atrium"],
            "answer": 1,
            "why": "Slide 4.",
        },
        {"q": "Broken", "choices": ["only one"], "answer": 0},
        {"q": "Out of range", "choices": ["a", "b"], "answer": 5},
    ],
}


def lecture(path: Path) -> Path:
    prs = Presentation()
    for title, body in SLIDES:
        s = prs.slides.add_slide(prs.slide_layouts[1])
        s.shapes.title.text = title
        s.placeholders[1].text = body
    prs.save(str(path))
    return path


def handout(path: Path) -> Path:
    c = canvas.Canvas(str(path), pagesize=letter)
    for i, (title, body) in enumerate(SLIDES):
        c.drawString(72, 720, title)
        c.drawString(72, 700, body[:90])
        if i < len(SLIDES) - 1:
            c.showPage()
    c.save()
    return path


def test_cards_are_checked_and_repeats_dropped():
    title, cards, quiz = sage.parse("Here: " + json.dumps(DECK))
    assert title == "The heart" and len(cards) == 5 and len(quiz) == 1
    assert "—" not in cards[3].a and cards[3].a == "To the body, the systemic circuit."
    assert [c.q for c in sage.merge(cards)] == [c["q"] for c in DECK["cards"][:4]]
    assert sage.parse("nope") == ("", [], [])


def test_long_notes_split_at_slides_and_pages():
    text = "\n".join(f"[page {i}] " + "The heart pumps blood. " * 60 for i in range(1, 40))
    chunks = sage.parts(text, size=6000, most=4)
    assert len(chunks) == 4 and all(c.startswith("[page") for c in chunks)
    assert all(len(c) <= 6000 + 1500 for c in chunks)


async def test_slides_become_flashcards_a_quiz_and_files(settings, tmp_path, monkeypatch):
    monkeypatch.setattr(sage, "out_dir", lambda: tmp_path / "Study")
    deck_file = lecture(tmp_path / "Lecture 4 slides.pptx")
    llm = Script(json.dumps(DECK))
    brain, rec, _ = connected(settings, [], llm=llm)
    out = await specialists.handle(
        brain, "make_flashcards", {"files": [str(deck_file)], "count": 8}
    )
    assert out.startswith("Started job_") and "Sage" in out
    [ready] = await until(rec, "job_ready")
    assert ready["ok"] and ready["agent"] == "Sage" and ready["title"] == "Flashcards: The heart"
    assert ready["body"].startswith("4 cards and a 1-question quiz.") and "quiz me" in ready["body"]
    assert "mitral valve stops blood flowing back" in llm.prompts[0]  # it read the real slides
    quiz_md, flash = (Path(f) for f in ready["files"])
    assert (
        quiz_md.name == "Lecture 4 slides quiz.md"
        and flash.name == "Lecture 4 slides flashcards.csv"
    )
    rows = list(csv.reader(flash.open(encoding="utf-8-sig")))
    assert rows[1] == [
        "What does the mitral valve stop?",
        "Blood flowing back from the left ventricle into the left atrium. (slide 3)",
    ]
    md = quiz_md.read_text(encoding="utf-8")
    assert "   B) Left ventricle" in md and "## Answers\n\n1. B) Left ventricle. Slide 4." in md
    assert sage.find_deck(settings.data_dir, "heart").title == "The heart"


async def test_a_pdf_handout_reads_too(settings, tmp_path, monkeypatch):
    monkeypatch.setattr(sage, "out_dir", lambda: tmp_path / "Study")
    pdf = handout(tmp_path / "heart handout.pdf")
    llm = Script(json.dumps(DECK))
    brain, rec, _ = connected(settings, [], llm=llm)
    await specialists.handle(brain, "make_flashcards", {"files": [str(pdf)]})
    [ready] = await until(rec, "job_ready")
    assert ready["ok"] and "[page 3]" in llm.prompts[0] and "mitral valve" in llm.prompts[0]


async def test_a_quiz_out_loud_keeps_score_and_repeats_the_misses(settings):
    deck = sage.Deck(
        "The heart",
        "Lecture 4 slides.pptx",
        [sage.Card(**{k: c[k] for k in ("q", "a", "where")}) for c in DECK["cards"][:4]],
    )
    sage.save_deck(settings.data_dir, deck)
    brain, _rec, _ = connected(settings, [])
    first = await specialists.handle(brain, "quiz", {"action": "start", "questions": 3})
    assert "on The heart, 3 questions" in first and "Question 1 of 3:" in first
    assert "for you only until they reply" in first
    asked = []
    for right in (True, False, True):
        asked.append(brain.study_quiz.current().q)
        out = await specialists.handle(
            brain, "quiz", {"action": "answer", "right": right, "said": "something"}
        )
    assert "That was the last one: 2 of 3 right" in out and "go again on the 1 they missed" in out
    again = await specialists.handle(brain, "quiz", {"action": "again"})
    assert "1 questions" in again and asked[1] in again  # only the one they missed
    stop = await specialists.handle(brain, "quiz", {"action": "stop"})
    assert "stopped at 0 of 1" in stop and brain.study_quiz is None
    none = await specialists.handle(brain, "quiz", {"action": "answer", "right": True})
    assert "no question waiting" in none


async def test_no_deck_and_bad_files_answer_straight_away(settings, tmp_path):
    brain, _rec, _ = connected(settings, [])
    assert "no flashcards yet" in await specialists.handle(brain, "quiz", {"action": "start"})
    assert "can't find" in await specialists.handle(
        brain, "make_flashcards", {"files": [str(tmp_path / "x.pdf")]}
    )
    song = tmp_path / "song.mp3"
    song.write_bytes(b"x")
    assert "can't read song.mp3" in await specialists.handle(
        brain, "make_flashcards", {"files": [str(song)]}
    )
    assert not brain.jobs.jobs


def test_a_reply_cut_off_part_way_keeps_the_whole_cards():
    raw = json.dumps(DECK)[:-200]  # ran out of tokens inside the quiz
    title, cards, quiz = sage.parse(raw)
    assert title == "The heart" and len(cards) == 5 and quiz == []
