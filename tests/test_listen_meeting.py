"""Live meeting notes and live captions, from the words on: who said what, the notes written
part by part, what is saved, and translation that keeps up. The model is a script; nothing
touches a device or the network."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from otto.types import LLMResponse, Usage

from pyxi.agents.scribe import Notes
from pyxi.listen import captions as cap
from pyxi.listen import meeting as m
from pyxi.listen.live import MIC, SYSTEM, Heard

START = datetime(2026, 11, 25, 10, 0).timestamp()
FINAL = {
    "title": "Launch planning",
    "summary": ["The launch moves to 5 December."],
    "decisions": ["Launch on 5 December"],
    "actions": [
        {"who": "Priya", "what": "Send the revised plan", "when": "by Friday", "due": "2026-11-27"},
        {"who": "", "what": "Book the venue", "when": "", "due": ""},
    ],
    "questions": ["Can the supplier deliver on time?"],
    "follow_up": {"subject": "Launch plan", "body": "Hi all,\n\nThanks for today."},
}


class Model:
    """Answers with notes; remembers how much it was given each time."""

    def __init__(self, fail: int = 0, wait: float = 0.0) -> None:
        self.asked: list[tuple[str, str]] = []
        self.fail, self.wait = fail, wait

    async def complete(self, *, system: str, messages: list[Any], **_: Any) -> LLMResponse:
        text = messages[-1].parts[0].text
        self.asked.append((system, text))
        if self.wait:
            await asyncio.sleep(self.wait)
        if self.fail:
            self.fail -= 1
            raise RuntimeError("503 overloaded")
        if '"title"' in system:
            return LLMResponse(text=json.dumps(FINAL), usage=Usage(cost_usd=0.002))
        n = len([a for a in self.asked if '"title"' not in a[0]])
        part = {
            "summary": [f"Part {n} was about item {n}."],
            "decisions": [f"Decision {n}"],
            "actions": [{"who": "Sam", "what": f"Task {n}", "when": "", "due": ""}],
            "questions": [],
        }
        return LLMResponse(text=json.dumps(part), usage=Usage(cost_usd=0.001))


def heard(text: str, at: float, source: str = SYSTEM, final: bool = True) -> Heard:
    return Heard(source, 0, text, final, START + at, 2.0, 0.1)


# ------------------------------------------------------------------ who said what
def test_you_and_others_and_one_speaker_going_on_is_one_paragraph(tmp_path):
    meeting = m.Meeting(Model(), folder=tmp_path, now=START)
    meeting.hear(heard("Good morning everyone.", 1))
    meeting.hear(heard("Let us start with the budget.", 4))
    meeting.hear(heard("Morning. I have the numbers here.", 8, MIC))
    meeting.hear(heard("still being said", 9, final=False))  # not a finished stretch
    meeting.hear(heard("", 9))
    assert [ln.render(START) for ln in meeting.lines] == [
        "[00:00:01] Others: Good morning everyone. Let us start with the budget.",
        "[00:00:08] You: Morning. I have the numbers here.",
    ]


def test_the_microphone_hearing_the_speakers_is_not_you(tmp_path):
    meeting = m.Meeting(Model(), folder=tmp_path, now=START)
    meeting.hear(heard("We decided to move the launch to the fifth of December.", 10))
    # No headphones: the microphone caught most of the same words a moment later.
    assert meeting.hear(heard("decided to move the launch to the fifth of December", 11, MIC)) is None
    # ...and when the echo is written down first, it goes once the real line arrives.
    meeting.hear(heard("Priya will send the revised plan by friday", 40, MIC))
    meeting.hear(heard("Priya will send the revised plan by Friday.", 41))
    meeting.hear(heard("Sounds good to me, I will review it on Monday.", 44, MIC))
    assert [(ln.who, ln.text) for ln in meeting.lines] == [
        ("Others", "We decided to move the launch to the fifth of December."),
        ("Others", "Priya will send the revised plan by Friday."),
        ("You", "Sounds good to me, I will review it on Monday."),
    ]
    assert not m.same_words("Yes.", "Yes.")  # too short to call an echo


# ------------------------------------------------------------------ the notes
async def test_a_short_meeting_is_one_call_and_two_pdfs(tmp_path):
    model = Model()
    meeting = m.Meeting(model, about="the launch", folder=tmp_path, now=START)
    meeting.hear(heard("We decided to move the launch to the fifth of December.", 5))
    meeting.hear(heard("Fine by me.", 9, MIC))
    result = await meeting.finish()
    assert result.ok and not result.partial and result.title == "Notes: Launch planning"
    assert result.body == "1 min. 1 decision, 2 action items, 1 open question."
    assert len(model.asked) == 1 and "the launch" in model.asked[0][1]
    assert "'You' is the user" in model.asked[0][0] and "Never guess which person" in model.asked[0][0]
    notes, script = (Path(f) for f in result.files)
    assert notes.name == "Launch planning 2026-11-25 10.00 notes.pdf"
    assert script.name == "Launch planning 2026-11-25 10.00 transcript.pdf"
    assert notes.read_bytes()[:5] == b"%PDF-" and script.read_bytes()[:5] == b"%PDF-"
    assert not list(tmp_path.glob("*.md")) and not list(tmp_path.glob("*.txt"))  # nothing raw
    assert not list(tmp_path.glob("*.wav"))  # and never any sound
    card = m.card(result.notes)
    assert [s["heading"] for s in card["sections"]] == ["Summary", "Decisions", "Action items", "Open questions"]
    assert card["sections"][2]["lines"] == ["Priya: Send the revised plan (by Friday)", "No owner yet: Book the venue"]
    assert result.cost_usd == 0.002


async def test_a_long_meeting_is_noted_part_by_part_and_the_end_never_reads_it_all(tmp_path):
    model = Model()
    meeting = m.Meeting(model, folder=tmp_path, now=START, part_chars=2000)
    sentence = "We went through the numbers for the quarter once more and agreed on the next steps."
    for i in range(240):  # an hour and a half of it, alternating speakers
        meeting.hear(heard(f"{sentence} Item {i}.", i * 22.0, MIC if i % 2 else SYSTEM))
        await asyncio.sleep(0)
    await asyncio.sleep(0.05)
    during = len(model.asked)
    assert during >= 8  # parts were noted while the meeting went on
    result = await meeting.finish()
    assert result.ok and result.minutes == 88
    whole = sum(len(ln.text) for ln in meeting.lines)
    assert whole > 20000
    biggest = max(len(text) for _, text in model.asked)
    assert biggest < 5000  # no call was ever given more than about a part
    final_system, final_text = model.asked[-1]
    assert "notes of consecutive parts" in final_system and sentence not in final_text
    # Every line went into exactly one part.
    given = "\n".join(text for system, text in model.asked if "consecutive parts" not in system)
    assert all(given.count(f"Item {i}.") == 1 for i in range(240))
    assert result.cost_usd < 0.05  # a few cents at most, with this script's prices


async def test_stop_while_the_notes_are_written_keeps_the_transcript_and_the_parts(tmp_path):
    model = Model()
    meeting = m.Meeting(model, folder=tmp_path, now=START, part_chars=300)
    for i in range(12):
        meeting.hear(heard(f"This is sentence number {i} of a meeting that goes on for a while.", i * 25.0, MIC if i % 2 else SYSTEM))
        await asyncio.sleep(0)
    await asyncio.sleep(0.05)
    assert meeting.parts
    model.wait = 5.0  # the final call hangs
    task = asyncio.create_task(meeting.finish())
    await asyncio.sleep(0.1)
    task.cancel()  # Stop, pressed during the final summary
    result = await task
    assert result.ok and result.partial
    assert "cover only part of it" in result.body and "Stopped before the notes were finished" in result.body
    assert len(result.files) == 2 and all(Path(f).is_file() for f in result.files)
    assert result.notes.decisions[0] == "Decision 1"  # what the parts already had


async def test_no_model_still_saves_the_transcript(tmp_path):
    meeting = m.Meeting(Model(fail=9), folder=tmp_path, now=START)
    meeting.hear(heard("We decided to move the launch.", 5))
    result = await meeting.finish()
    assert not result.ok and result.title == "Transcript saved, no notes yet"
    assert "nothing that was said is lost" in result.body
    assert [Path(f).name for f in result.files] == ["Meeting 2026-11-25 10.00 transcript.pdf"]


async def test_nothing_said_saves_nothing(tmp_path):
    result = await m.Meeting(Model(), folder=tmp_path, now=START).finish()
    assert not result.ok and result.title == "I didn't hear anyone speak" and result.files == []
    assert list(tmp_path.iterdir()) == []


def test_parts_can_be_put_together_without_a_model_and_dates_are_read():
    a = Notes("Meeting notes", ["One."], ["Ship Friday"], [{"who": "Sam", "what": "Write it"}], questions=["Who pays?"])
    b = Notes("Meeting notes", ["Two."], ["Ship Friday."], [{"who": "Sam", "what": "write it."}, {"who": "", "what": "Call them"}])
    merged = m.merge_locally([a, b])
    assert merged.summary == ["One.", "Two."] and merged.decisions == ["Ship Friday"]
    assert [x["what"] for x in merged.actions] == ["Write it", "Call them"] and merged.questions == ["Who pays?"]
    today = datetime(2026, 11, 25, 15, 0)
    assert m.due_date({"due": "2026-11-27"}, today) == datetime(2026, 11, 27, 9, 0)
    assert m.due_date({"due": "2026-11-25"}, today) == datetime(2026, 11, 25, 9, 0)  # today counts
    assert m.due_date({"due": "2026-11-20"}, today) is None  # already past
    assert m.due_date({"due": "Friday"}, today) is None and m.due_date({}, today) is None
    assert m.due_date({"due": "2026-13-40"}, today) is None
    assert m.safe_name('Q4: "plans" / budget?') == "Q4 plans budget"


# ------------------------------------------------------------------ captions
class Screen:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def emit(self, type: str, **data: Any) -> None:
        self.events.append({"type": type, **data})

    def line(self, n: int) -> dict[str, Any]:
        return [e for e in self.events if e["id"] == n][-1]


class Translator:
    def __init__(self, wait: float = 0.0, fail: bool = False) -> None:
        self.asked: list[str] = []
        self.wait, self.fail = wait, fail

    async def complete(self, *, system: str, messages: list[Any], **_: Any) -> LLMResponse:
        text = messages[-1].parts[0].text
        self.asked.append(text)
        self.system = system
        if self.wait:
            await asyncio.sleep(self.wait)
        if self.fail:
            raise RuntimeError("offline")
        lines = [ln for ln in text.split("\n") if ln[:1].isdigit()]
        out = "\n".join(f"{ln.split(':')[0]}: [es] {ln.split(': ', 1)[1]}" for ln in lines)
        return LLMResponse(text=out, usage=Usage(cost_usd=0.0001))


async def test_captions_show_words_as_they_come_with_no_model_call():
    screen, model = Screen(), Translator()
    c = cap.Captions(screen.emit, model)
    await c.hear(Heard(SYSTEM, 7, "We decided", False, 0, 1, 0.2))
    await c.hear(Heard(SYSTEM, 7, "We decided to move the launch.", True, 0, 2, 0.5))
    assert [(e["id"], e["text"], e["final"], e["pending"]) for e in screen.events] == [
        (1, "We decided", False, False),
        (1, "We decided to move the launch.", True, False),
    ]
    assert model.asked == [] and c.lags == [0.2, 0.5]
    await c.close()


async def test_the_original_is_shown_at_once_and_the_translation_follows():
    screen, model = Screen(), Translator(wait=0.05)
    c = cap.Captions(screen.emit, model, target="spanish", both=True)
    await c.hear(Heard(SYSTEM, 0, "Good morning everyone.", True, 0, 2, 0.4, "en"))
    assert screen.line(1) == {"type": "caption", "id": 1, "text": "Good morning everyone.", "final": True, "translation": None, "pending": True}
    await asyncio.sleep(0.15)
    assert screen.line(1)["translation"] == "[es] Good morning everyone." and not screen.line(1)["pending"]
    assert "into Spanish" in model.system and "never answer them" in model.system
    assert c.translation_lags and c.translation_lags[0] < 1.0
    await c.close()


async def test_sentences_that_pile_up_go_in_one_call_so_it_catches_up():
    screen, model = Screen(), Translator(wait=0.1)
    c = cap.Captions(screen.emit, model, target="Spanish")
    await c.hear(Heard(SYSTEM, 0, "One.", True, 0, 1, 0.3, "en"))
    await asyncio.sleep(0.02)  # the first call is under way
    for i, text in enumerate(["Two.", "Three.", "Four."], start=1):
        await c.hear(Heard(SYSTEM, i, text, True, 0, 1, 0.3, "en"))  # very fast speech
    await asyncio.sleep(0.4)
    assert len(model.asked) == 2  # four sentences, two calls
    assert model.asked[1].endswith("2: Two.\n3: Three.\n4: Four.") and model.asked[1].startswith("Before:\nOne.")
    assert [screen.line(n)["translation"] for n in (1, 2, 3, 4)] == ["[es] One.", "[es] Two.", "[es] Three.", "[es] Four."]
    await c.close()


async def test_into_english_is_done_on_this_pc_and_english_needs_nothing():
    screen, model = Screen(), Translator()
    c = cap.Captions(screen.emit, model, target="English")
    await c.hear(Heard(SYSTEM, 0, "Hola a todos.", True, 0, 2, 0.4, "es", "Hello everyone."))
    await c.hear(Heard(SYSTEM, 1, "Thanks.", True, 0, 1, 0.4, "en"))
    await asyncio.sleep(0.05)
    assert screen.line(1)["translation"] == "Hello everyone." and not screen.line(1)["pending"]
    assert screen.line(2)["translation"] is None and not screen.line(2)["pending"]
    assert model.asked == []  # no text left the PC
    await c.close()


async def test_a_translation_that_does_not_come_leaves_the_words_as_said(monkeypatch):
    monkeypatch.setattr(cap, "GIVE_UP_S", 0.1)
    screen, model = Screen(), Translator(fail=True)
    c = cap.Captions(screen.emit, model, target="French")
    await c.hear(Heard(SYSTEM, 0, "Good morning.", True, 0, 2, 0.4, "en"))
    await asyncio.sleep(0.1)
    assert screen.line(1)["translation"] is None and not screen.line(1)["pending"]
    assert "showing what is said" in c.problem
    # Sentences that waited too long behind a slow call are let go, not shown ten seconds late.
    c._waiting.append((9, "Old news.", time.monotonic() - 5))
    c._wake.set()
    await asyncio.sleep(0.05)
    assert screen.line(9) == {"type": "caption", "id": 9, "text": "Old news.", "final": True, "translation": None, "pending": False}
    await c.close()
    await c.hear(Heard(SYSTEM, 5, "After the stop.", True, 0, 1, 0.1))
    assert all(e["text"] != "After the stop." for e in screen.events)  # nothing after Stop


def test_languages_and_the_models_numbered_lines():
    assert cap.language_name("spanish") == "Spanish" and cap.language_name("es") == "Spanish"
    assert cap.language_name("") == "" and cap.language_name("the red one") == ""
    assert cap.language_code("Bangla") == "bn" and cap.language_code("Klingon") == ""
    assert cap.parse_numbered("3: Hola.\n4) Adiós.\nextra\n9: no", [3, 4]) == {3: "Hola.", 4: "Adiós."}
    assert cap.parse_numbered("Hola.", [7]) == {7: "Hola."}  # one sentence, no number given back
    assert cap.parse_numbered("Hola.\nAdiós.", [7, 8]) == {}
