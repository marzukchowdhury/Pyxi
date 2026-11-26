"""The newer features (meeting notes, photo to 3D, the hands on the desktop, decks) when they
fail: each says what happened in plain words and what to do, and none offers a retry for a
cause that a retry cannot mend."""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

from pyxi import hands
from pyxi import plainwords as pw
from pyxi.companion import listening, photomodel
from pyxi.connectors.google import GOOGLE_EXPIRED, GoogleExpired
from pyxi.listen import meeting as meet_mod
from pyxi.scenes import photo
from test_answer_card import make_brain
from test_operator import operator


class LLMError(Exception):
    pass


ALLOWANCE = LLMError(
    "OpenRouter API error: Error code: 429 - {'error': {'message': \"You've used today's Pyxi "
    "beta allowance. It resets at midnight UTC.\"}}"
)


# ---------------------------------------------------------------- meeting notes
def test_a_failed_save_is_not_reported_as_a_failure_to_listen():
    """Documents not writable gave "I couldn't listen to this PC's sound just now." on the
    notes card: wrong, and nothing to do about it."""
    denied = PermissionError(13, "Access is denied", r"C:\Users\sam\Documents\Pyxi\Meetings")
    denied.winerror = 5
    line = listening._plain(denied, "I couldn't save the notes.")
    assert line.startswith("Windows won't let me open or save Meetings.") and "listen" not in line
    assert listening._plain(KeyError("lines"), "I couldn't save the notes.") == "I couldn't save the notes."
    # A sentence of Pyxi's own is kept, and the listening's own fallback is still the default.
    own = "I couldn't listen to this PC's sound: Windows has no speakers or headphones to listen to right now."
    assert listening._plain(RuntimeError(own)) == own
    assert listening._plain(KeyError("x")) == "I couldn't listen to this PC's sound just now."


def test_notes_that_cannot_be_saved_still_keep_what_was_said(settings):
    start = time.time() - 600
    meeting = SimpleNamespace(
        started=start,
        about="Budget review",
        lines=[meet_mod.Line(start + 5, "Others", "Let's move the launch to Friday."), meet_mod.Line(start + 9, "You", "Agreed.")],
    )
    brain = SimpleNamespace(settings=settings)
    result = listening._notes_lost(brain, meeting, OSError(28, "No space left on device"))
    assert not result.ok and result.title == "The notes didn't work out"
    assert result.body.startswith(pw.DISK_FULL)
    assert "so nothing is lost" in result.body and "Ask me to summarise that file" in result.body
    [kept] = result.files
    assert "Let's move the launch to Friday." in Path(kept).read_text(encoding="utf-8")
    assert "Errno" not in result.body


def test_notes_with_no_model_say_how_to_get_them_later(tmp_path):
    start = time.time() - 300
    m = meet_mod.Meeting.__new__(meet_mod.Meeting)
    m.folder, m.started, m.about, m.problem, m.cost = tmp_path, start, "Standup", pw.MODEL_BUSY, 0.0
    m.lines = [meet_mod.Line(start + 3, "Others", "Ship it on Friday.")]
    result = m._save(None, True)
    assert result.title == "Transcript saved, no notes yet"
    assert result.body.startswith("The AI model is busy right now. Try again in a minute.")
    assert "Ask me to summarise that file later" in result.body


async def test_calendar_entries_that_fail_on_the_sign_in_say_so_with_the_button(settings):
    """It read "I couldn't listen to this PC's sound just now." under "The calendar entries
    didn't go in"."""
    line = listening._plain(GoogleExpired(GOOGLE_EXPIRED), "Google Calendar didn't take them.")
    assert line == GOOGLE_EXPIRED
    brain, rec = make_brain(settings)
    await brain._notify("cal", ok=False, title="The calendar entries didn't go in", body=line)
    [card] = rec.of("job_ready")
    assert card["actions"] == ["google"]  # the card carries the button, whoever reports it
    assert listening._plain(KeyError("id"), "Google Calendar didn't take them.") == "Google Calendar didn't take them."


async def test_a_start_press_that_is_not_counted_says_so(settings, monkeypatch):
    brain, rec = make_brain(settings)
    listening.setup(brain)
    monkeypatch.setattr(hands, "recently_acted", lambda *a, **kw: True)
    await brain.listening.on_message({"type": "listen", "what": "captions", "action": "start"})
    notices = [e["message"] for e in rec.of("notice")]
    assert notices and "Press it again to start listening" in notices[-1]


# ---------------------------------------------------------------- photo to 3D
async def test_a_model_that_cannot_be_built_for_a_known_reason_says_the_reason(settings, monkeypatch):
    from test_photo_companion import photo_brain, settle

    async def no_allowance(*a, **kw):
        raise ALLOWANCE

    monkeypatch.setattr(photo, "model_from_picture", no_allowance)
    brain, rec = photo_brain(settings, chat=[], modeller=[])
    brain._ctx = brain.screen.ctx
    assert await photomodel.handle(brain, {"source": "screen", "element_id": 3}) is None
    await settle(brain, rec, kind="say")
    said = " ".join(e["text"] for e in rec.of("say"))
    assert "I couldn't build that model." in said and "beta allowance" in said
    assert "try again" not in said.lower()  # a retry can't mend that
    assert photomodel.state(brain).building == ""


def test_a_spent_budget_says_where_to_raise_it():
    import inspect

    assert "raise the daily limit in Settings" in inspect.getsource(photomodel)


# ---------------------------------------------------------------- the hands on the desktop
async def test_hands_that_cannot_reach_the_model_say_why(monkeypatch):
    import asyncio

    async def no_wait(_):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_wait)

    class Offline:
        model = "offline"

        async def complete(self, **kw):
            raise LLMError("OpenRouter API error: Connection error.")

    op = operator(Offline(), [])
    out = await op.run("open the Downloads folder")
    assert not out.ok and out.summary == f"I had to stop there. {pw.MODEL_OFFLINE}"
    assert "train of thought" not in out.summary

    class Odd(Offline):
        async def complete(self, **kw):
            raise KeyError("choices")

    out = await operator(Odd(), []).run("open the Downloads folder")
    assert out.summary == "I lost my train of thought there. Want me to try again?"


# ---------------------------------------------------------------- decks
def test_a_slide_whose_layout_broke_is_said_without_pythons_words():
    from pyxi.agents import deck_design as design

    w = design.DeckWriter(design.THEMES["paper"], "Check")

    def broken(s):
        raise IndexError("list index out of range")

    w._draw_chart = broken
    w.add({"layout": "chart", "title": "Sales by month", "bullets": ["Up in March"]})
    [problem] = w.problems
    assert "the chart layout didn't fit what was on it, so it is a plain slide" in problem
    assert "Ask me to redo that slide" in problem and "list index" not in problem
    assert len(w.prs.slides) >= 1  # the slide is still there, drawn plainly
