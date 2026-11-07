"""Sage, study: lecture slides, PDFs and notes into flashcards and a quiz, and a quiz out loud.

* **Flashcards and a quiz from the file.** The slides (.pptx), PDF, Word file or notes are read
  here (:func:`read_notes`: tables and speaker notes too, scanned pages with OCR), and one
  small model call per part writes question-and-answer cards and multiple-choice questions,
  each pointing back at its slide or page. Nothing is invented: a card has to come from the file.
* **Saved where they can use them.** ``Documents\\Pyxi\\Study\\<name> flashcards.csv`` (Anki
  and Quizlet import it as front/back), ``<name> quiz.md`` (questions first, answers at the
  end), and the deck itself in ``.pyxi/study`` for the spoken quiz.
* **A quiz out loud.** :class:`Quiz` asks the cards one at a time in Pyxi's voice; the user
  answers by voice; the companion model judges the answer (generous with wording, strict on
  facts) and the quiz keeps the score and the ones they missed for another round.
* Diagrams on a slide are explained on the picture itself with ``explain_on_screen`` (style
  'on_picture'); that's the companion's job, not this module's.
"""

from __future__ import annotations

import csv
import json
import logging
import random
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pyxi import safety

if TYPE_CHECKING:
    from pyxi.agents.jobs import Job, JobManager
    from pyxi.agents.registry import AgentProfile

log = logging.getLogger(__name__)

READABLE = {".pdf", ".pptx", ".docx", ".txt", ".md", ".markdown", ".rtf", ".html", ".htm", ".csv"}
PART_CHARS = 18000
MAX_PARTS = 4  # a 200-page textbook is a few decks, not one

SYSTEM = """You make study material from course notes. Reply with JSON only:
{"title": "the topic, a few words",
 "cards": [{"q": "a question", "a": "the answer, one or two short sentences", "where": "slide 4 / page 2 / ''"}],
 "quiz": [{"q": "question", "choices": ["A", "B", "C", "D"], "answer": 0, "why": "one line"}]}
Rules: every card and question comes from the notes, nothing outside them. Cover the important
ideas evenly: definitions, how and why, cause and effect, the steps of processes, formulas
with what each symbol means, the numbers worth remembering. One fact per card. Questions are
specific ("What does the mitral valve stop?"), never "What is on slide 3?". Wrong choices are
plausible. Plain text, no markdown, no em dashes."""


@dataclass
class Card:
    q: str
    a: str
    where: str = ""


@dataclass
class Question:
    q: str
    choices: list[str]
    answer: int
    why: str = ""


@dataclass
class Deck:
    title: str
    source: str
    cards: list[Card] = field(default_factory=list)
    quiz: list[Question] = field(default_factory=list)

    @property
    def slug(self) -> str:
        # Unicode letters keep two decks apart (生物学, Économie): they share a file only if
        # their names do.
        return re.sub(r"[\W_]+", "-", Path(self.source).stem.lower()).strip("-")[:60] or "deck"


def clean(text: Any) -> str:
    return re.sub(r"\s*[—–]\s*", ", ", str(text or "")).strip()


def parse(raw: str) -> tuple[str, list[Card], list[Question]]:
    """The deck in the model's reply. A reply cut off part way (a long deck that ran out of
    tokens) still gives every card that arrived whole."""
    from pyxi.partial_json import complete_items, partial_string

    raw = raw or ""
    m = re.search(r"\{.*\}", raw, re.S)
    try:
        data = json.loads(m.group(0)) if m else None
    except ValueError:
        data = None
    if not isinstance(data, dict):
        title = partial_string(raw, "title")
        data = {
            "title": title[0] if title and title[1] else "",
            "cards": complete_items(raw, "cards"),
            "quiz": complete_items(raw, "quiz"),
        }
    cards = [
        Card(clean(c.get("q")), clean(c.get("a")), clean(c.get("where")))
        for c in data.get("cards") or []
        if isinstance(c, dict) and str(c.get("q") or "").strip() and str(c.get("a") or "").strip()
    ]
    quiz = []
    for q in data.get("quiz") or []:
        if not isinstance(q, dict):
            continue
        choices = [clean(c) for c in q.get("choices") or [] if str(c).strip()]
        try:
            ans = int(q.get("answer"))
        except (TypeError, ValueError):
            continue
        if str(q.get("q") or "").strip() and len(choices) >= 2 and 0 <= ans < len(choices):
            quiz.append(Question(clean(q["q"]), choices, ans, clean(q.get("why"))))
    return clean(data.get("title"))[:80], cards, quiz


def spread(quiz: list[Question], seed: int = 0) -> list[Question]:
    """Models put most right answers in the same place (every one a B). Each question's right
    answer goes to a letter from a shuffled round of A, B, C, D, so they're spread evenly."""
    rng = random.Random(seed)
    out, letters = [], []
    for q in quiz:
        if not letters:
            letters = list(range(4))
            rng.shuffle(letters)
        at = letters.pop() % len(q.choices)
        rest = [c for j, c in enumerate(q.choices) if j != q.answer]
        rest.insert(at, q.choices[q.answer])
        out.append(Question(q.q, rest, at, q.why))
    return out


def parts(text: str, size: int = PART_CHARS, most: int = MAX_PARTS) -> list[str]:
    """Split at slide or page markers (or paragraphs), never mid-sentence."""
    pieces = re.split(r"(?=\n?\[(?:page|slide) \d+\])|(?=\n(?:Slide|Page) \d+\b)|\n\s*\n", text)
    out, cur = [], ""
    for p in pieces:
        if not p or not p.strip():
            continue
        if len(cur) + len(p) > size and cur:
            out.append(cur)
            cur = ""
        cur += ("\n" if cur else "") + p.strip()
    if cur:
        out.append(cur)
    return out[:most]


def merge(cards: list[Card]) -> list[Card]:
    seen, out = set(), []
    for c in cards:
        key = re.sub(r"\W+", " ", c.q.lower()).strip()
        if key and key not in seen:
            seen.add(key)
            out.append(c)
    return out


# ------------------------------------------------------------------------------ files
def unique(folder: Path, name: str) -> Path:
    path, n = folder / name, 2
    while path.exists():
        path = folder / f"{Path(name).stem} ({n}){Path(name).suffix}"
        n += 1
    return path


def out_dir() -> Path:
    return Path.home() / "Documents" / "Pyxi" / "Study"


def write_csv(deck: Deck, folder: Path) -> Path:
    path = unique(folder, f"{Path(deck.source).stem} flashcards.csv")
    with path.open("w", encoding="utf-8-sig", newline="") as f:  # BOM: Excel reads accents
        w = csv.writer(f)
        for c in deck.cards:
            w.writerow([c.q, c.a + (f" ({c.where})" if c.where else "")])
    return path


def write_quiz(deck: Deck, folder: Path) -> Path:
    path = unique(folder, f"{Path(deck.source).stem} quiz.md")
    lines = [f"# Quiz: {deck.title}", "", f"From {Path(deck.source).name}.", ""]
    for i, q in enumerate(deck.quiz, 1):
        lines.append(f"{i}. {q.q}")
        lines += [f"   {'ABCDEFGH'[j]}) {c}" for j, c in enumerate(q.choices)]
        lines.append("")
    lines += ["## Answers", ""]
    for i, q in enumerate(deck.quiz, 1):
        lines.append(
            f"{i}. {'ABCDEFGH'[q.answer]}) {q.choices[q.answer]}" + (f". {q.why}" if q.why else "")
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def save_deck(data_dir: Path, deck: Deck) -> Path:
    folder = data_dir / "study"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{deck.slug}.json"
    path.write_text(json.dumps(asdict(deck), indent=1, ensure_ascii=False), encoding="utf-8")
    return path


def load_decks(data_dir: Path) -> list[Deck]:
    """Saved decks, newest first."""
    out = []
    files = sorted(
        (data_dir / "study").glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    for p in files:
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            out.append(
                Deck(
                    d["title"],
                    d["source"],
                    [Card(**c) for c in d.get("cards") or []],
                    [Question(**q) for q in d.get("quiz") or []],
                )
            )
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return out


def find_deck(data_dir: Path, said: str = "") -> Deck | None:
    decks = load_decks(data_dir)
    words = [
        w
        for w in re.findall(r"[a-z0-9]{3,}", said.lower())
        if w not in {"quiz", "the", "deck", "flashcards", "cards", "about", "with"}
    ]
    if words:
        for d in decks:
            hay = f"{d.title} {Path(d.source).stem}".lower()
            if any(w in hay for w in words):
                return d
    return decks[0] if decks else None


# ------------------------------------------------------------------------------ the spoken quiz
@dataclass
class Quiz:
    deck: Deck
    order: list[int]
    at: int = 0
    right: int = 0
    missed: list[int] = field(default_factory=list)

    @classmethod
    def start(
        cls, deck: Deck, n: int = 10, only: list[int] | None = None, seed: int | None = None
    ) -> Quiz:
        idx = list(only if only is not None else range(len(deck.cards)))
        random.Random(seed).shuffle(idx)
        return cls(deck, idx[: max(1, min(n, len(idx)))])

    @property
    def done(self) -> bool:
        return self.at >= len(self.order)

    def current(self) -> Card:
        return self.deck.cards[self.order[self.at]]

    def ask(self) -> str:
        c = self.current()
        return (
            f"Question {self.at + 1} of {len(self.order)}: {c.q}\n"
            f"(The answer, for you only until they reply: {c.a.rstrip('.') if c.where else c.a}"
            + (f", from {c.where}" if c.where else "")
            + ")"
        )

    def mark(self, right: bool) -> None:
        if self.done:
            return
        if right:
            self.right += 1
        else:
            self.missed.append(self.order[self.at])
        self.at += 1

    def score(self) -> str:
        return f"{self.right} of {len(self.order)}"


# ------------------------------------------------------------------------------ reading
def _plain(markup: str) -> str:
    import html

    return html.unescape(re.sub(r"<[^>]+>", "", markup.replace("<br/>", "\n"))).strip()


def read_notes(path: Path, max_chars: int = 80000) -> str:
    """What to study from: slides with their tables and speaker notes, Word files with their
    tables, PDFs page by page (scanned pages read with OCR), anything else as text."""
    ext = path.suffix.lower()
    if ext in (".pptx", ".docx"):
        from pyxi.agents import pixel_render

        slides = ext == ".pptx"
        blocks = pixel_render.pptx_blocks(path) if slides else pixel_render.docx_blocks(path)[0]
        lines, n = (["[slide 1]"] if slides else []), 1
        for b in blocks:
            if b.kind == "br" and slides:
                n += 1
                lines.append(f"[slide {n}]")
            elif b.kind in ("h", "p", "pre"):
                lines.append(_plain(b.text))
            elif b.kind == "li":
                lines.append("- " + _plain(b.text))
            elif b.kind == "table":
                lines += [" | ".join(_plain(c) for c in row) for row in b.rows]
        text = "\n".join(line for line in lines if line.strip())
    elif ext == ".pdf":
        from pyxi.agents import pixel_pdf

        texts, _ = pixel_pdf.page_texts(path, ocr=True)
        text = f"PDF, {len(texts)} pages.\n" + "\n".join(
            f"[page {i}] {t}" for i, t in enumerate(texts, 1)
        )
    else:
        from pyxi import files_access

        text = files_access.read_text(path, max_chars)
    return text[:max_chars]


# ------------------------------------------------------------------------------ the job
async def run(manager: JobManager, job: Job, agent: AgentProfile) -> None:
    import asyncio

    from otto.llm.base import Message

    paths = [Path(f) for f in job.files if Path(f).is_file()]
    if not paths:
        raise RuntimeError("I couldn't find those notes.")
    want = int(job.params.get("count") or 20)
    focus = str(job.params.get("focus") or "").strip()
    llm = manager.llm_factory(agent)
    folder = Path(job.params.get("folder") or out_dir())
    folder.mkdir(parents=True, exist_ok=True)
    outputs: list[str] = []
    decks: list[Deck] = []
    unread = 0  # files with notes in them that the model's reply had no cards for
    for path in paths:
        await manager._status(job, f"Reading {path.name}")
        text = await asyncio.to_thread(read_notes, path, 80000)
        chunks = parts(text)
        if not chunks or len(text.strip()) < 40:
            continue
        per = max(4, round(want / len(chunks)))
        title, cards, quiz = "", [], []
        for i, chunk in enumerate(chunks):
            await manager._status(
                job,
                f"Making cards from {path.name}"
                + (f", part {i + 1} of {len(chunks)}" if len(chunks) > 1 else ""),
            )
            ask = f"About {per} cards and {min(10, max(3, per // 2))} quiz questions."
            if focus:
                ask += f" Focus on: {focus}."
            resp = await llm.complete(
                system=SYSTEM + safety.READ_ONLY_RULE,
                messages=[
                    Message.user(f"{ask}\n\n{safety.fence('notes from ' + path.name, chunk)}")
                ],
                max_tokens=4000,
            )
            job.cost_usd += resp.usage.cost_usd
            t, c, q = parse(resp.text)
            if not c:
                log.warning("sage: no cards in the reply for %s: %r", path.name, resp.text[:300])
            title, cards, quiz = title or t, cards + c, quiz + q
        quiz = spread(quiz[:15], seed=len(path.name))
        deck = Deck(title or path.stem, str(path), merge(cards)[: max(want, 4) + 10], quiz)
        if not deck.cards:
            unread += 1
            continue
        if deck.quiz:  # no empty quiz file when the reply had no questions
            outputs.append(str(write_quiz(deck, folder)))
        outputs.append(str(write_csv(deck, folder)))
        save_deck(manager.settings.data_dir, deck)
        decks.append(deck)
    if not decks and unread:
        job.status = "failed"
        job.summary = "The flashcards didn't come out right. Try again in a moment."
        job.data = {"title": "No flashcards this time", "body": job.summary}
        return
    if not decks:
        job.status, job.summary = "failed", "I couldn't find anything to study in those files."
        job.data = {"title": "Nothing to make cards from", "body": job.summary}
        return
    job.outputs = outputs
    n_cards = sum(len(d.cards) for d in decks)
    n_quiz = sum(len(d.quiz) for d in decks)
    title = decks[0].title if len(decks) == 1 else f"{len(decks)} decks"
    quiz = f" and a {n_quiz}-question quiz" if n_quiz else ""
    job.summary = f"Made {n_cards} flashcards{quiz} on {title}, saved in {folder}. " + " ".join(
        f"Q: {c.q} A: {c.a}" for c in decks[0].cards[:5]
    )
    job.data = {
        "title": f"Flashcards: {title}"[:70],
        "body": f'{n_cards} cards{quiz}. Say "quiz me" and I\'ll ask you out loud.',
        "changes": [c.q for c in decks[0].cards[:5]],
        "card": {
            "title": title,
            "sections": [{"heading": c.q, "lines": [c.a]} for c in decks[0].cards[:8]],
        },
        "actions": ["open", "folder"],
        "outputs": outputs,
        "decks": [d.slug for d in decks],
    }
    job.status = "done"
