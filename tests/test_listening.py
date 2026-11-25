"""Meeting notes with no bot, and live captions, from "take notes on this meeting" to the card.

A real brain, with a pretend capture the test plays sound into, a scripted speech model and a
scripted notes model. Nothing listens to this PC, and notes are saved in a temp folder.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from otto.types import LLMResponse, Usage

from pyxi.companion import listening
from pyxi.listen.live import MIC, SYSTEM, LiveEars
from test_brain_and_server import make_brain
from test_connectors_and_docs import Script
from test_listen_sound import FakeCapture, FakeSTT, hush, said, talk
from test_mailwatch import until

FRIDAY = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d")
NOTES = {
    "title": "Launch planning",
    "summary": ["The launch moves to 5 December."],
    "decisions": ["Launch on 5 December"],
    "actions": [
        {"who": "Priya", "what": "Send the revised plan", "when": "by Friday", "due": FRIDAY},
        {"who": "", "what": "Book the venue", "when": "", "due": ""},
    ],
    "questions": ["Can the supplier deliver on time?"],
    "follow_up": {"subject": "Launch plan", "body": "Hi all,\n\nThanks for today."},
}


class NotesModel:
    def __init__(self, wait: float = 0.0) -> None:
        self.asked: list[str] = []
        self.wait = wait

    async def complete(self, *, system: str, messages: list[Any], **_: Any) -> LLMResponse:
        self.asked.append(messages[-1].parts[0].text)
        if self.wait:
            await asyncio.sleep(self.wait)
        return LLMResponse(text=json.dumps(NOTES), usage=Usage(cost_usd=0.002))


def hearing(settings, tmp_path, texts, llm=None, no_mic=False):
    """A brain whose ears are pretend ones. The chat model is a script that fails the test
    if it is asked anything it wasn't given a reply for."""
    brain, rec = make_brain(settings, llm=llm or Script())
    ls: listening.Listening = brain.listening
    FakeCapture.made = []

    def make(source: str, sink: Any) -> FakeCapture:
        c = FakeCapture(source, sink)
        if no_mic and source == MIC:
            c.problem = "I couldn't open the microphone."
        return c

    ls.ears = LiveEars(FakeSTT(), gate=ls._gate, make_capture=make, transcribe=said(texts))
    ls.folder = tmp_path / "Meetings"
    brain._listen_llm = NotesModel()
    return brain, rec, ls


def capture_of(source: str) -> FakeCapture:
    return [c for c in FakeCapture.made if c.source == source and not c.problem][-1]


async def say_into(ls: listening.Listening, source: str, n: int, seconds: float = 1.6) -> None:
    """Someone says the nth scripted sentence, then pauses."""
    ls.ears.transcribe.finals = n
    capture_of(source).play(np.concatenate([talk(seconds, seed=n + 1), hush(1.0)]))
    await asyncio.sleep(0.05)
    await ls.ears.flush(source)
    await asyncio.sleep(0.02)


async def notes_card(rec, n: int = 1) -> dict[str, Any]:
    for _ in range(300):
        cards = [e for e in rec.of("job_ready") if e["agent"] == "Scribe"]
        if len(cards) >= n:
            return cards[n - 1]
        await asyncio.sleep(0.02)
    raise AssertionError(f"no notes card: {[e['type'] for e in rec.events][-12:]}")


# ------------------------------------------------------------------ meeting notes
async def test_take_notes_on_this_meeting_then_the_end_of_the_meeting(settings, tmp_path):
    brain, rec, ls = hearing(settings, tmp_path, ["We decided to move the launch.", "Fine by me."])
    await brain.turn("Pyxi, take notes on this meeting")
    sign = rec.of("listening")[-1]
    assert sign["meeting"]["state"] == "listening" and sign["captions"] is None
    assert {c.source for c in FakeCapture.made} == {SYSTEM, MIC} and all(c.started for c in FakeCapture.made)
    # Said with no model call, and shown, not spoken: her voice is not in the call's notes.
    lines = rec.of("say")
    assert " ".join(e["text"] for e in lines) == "Taking notes. Say stop when the meeting is over."
    assert all(e["audio"] is None for e in lines) and brain._hushed()
    assert "Taking notes on a meeting now" in ls.prompt_note()
    await say_into(ls, SYSTEM, 0)
    await say_into(ls, MIC, 1)
    assert [(ln.who, ln.text) for ln in ls.meeting.lines] == [
        ("Others", "We decided to move the launch."),
        ("You", "Fine by me."),
    ]
    await brain.turn("that's the end of the meeting")
    card = await notes_card(rec)
    assert card["ok"] and card["title"] == "Notes: Launch planning"
    assert card["body"].startswith("1 min. 1 decision, 2 action items, 1 open question.")
    assert [Path(f).suffix for f in card["files"]] == [".pdf", ".pdf"] and "open" in card["actions"]
    assert all(Path(f).parent == tmp_path / "Meetings" and Path(f).is_file() for f in card["files"])
    job = next(j for j in brain.jobs.jobs.values() if j.role == "meetings")
    assert job.status == "done" and job.cost_usd == 0.002
    assert [s["heading"] for s in job.data["card"]["sections"]] == ["Summary", "Decisions", "Action items", "Open questions"]
    # Nothing is heard any more, the sign is gone, and nothing but the two PDFs was written.
    assert all(c.stopped for c in FakeCapture.made) and not ls.active() and not brain._hushed()
    assert rec.of("listening")[-1] == {**rec.of("listening")[-1], "meeting": None, "captions": None}
    assert sorted(p.suffix for p in (tmp_path / "Meetings").iterdir()) == [".pdf", ".pdf"]
    from pyxi import made

    assert set(made.load(settings.data_dir)) >= {str(Path(f).resolve()) for f in card["files"]}
    # The one model call saw the transcript with its two speakers, never a name it made up.
    [asked] = brain._listen_llm.asked
    assert "Others: We decided to move the launch." in asked and "You: Fine by me." in asked


async def test_two_meetings_in_a_row_are_two_sets_of_notes(settings, tmp_path):
    brain, rec, ls = hearing(settings, tmp_path, ["First meeting.", "Second meeting."])
    for n in (0, 1):
        assert (await ls.start_meeting()).startswith("Taking notes now")
        await say_into(ls, SYSTEM, n)
        assert [ln.text for ln in ls.meeting.lines] == [("First meeting.", "Second meeting.")[n]]
        await ls.stop_meeting()
        await notes_card(rec, n + 1)
    assert len(list((tmp_path / "Meetings").glob("*notes*.pdf"))) == 2
    assert "Second meeting." in brain._listen_llm.asked[1] and "First meeting." not in brain._listen_llm.asked[1]


async def test_asking_twice_and_stopping_nothing(settings, tmp_path):
    _brain, rec, ls = hearing(settings, tmp_path, ["x"])
    assert "no notes are being taken" in await ls.stop_meeting()
    await ls.start_meeting()
    assert "already taking notes" in await ls.start_meeting()
    assert len([c for c in FakeCapture.made if c.source == SYSTEM]) == 1
    await ls.stop_meeting()
    card = await notes_card(rec)
    assert not card["ok"] and card["title"] == "I didn't hear anyone speak" and card["files"] == []
    assert not (tmp_path / "Meetings").exists() or not list((tmp_path / "Meetings").iterdir())


async def test_no_microphone_still_takes_notes_and_says_so(settings, tmp_path):
    _brain, rec, ls = hearing(settings, tmp_path, ["Only the others."], no_mic=True)
    note = await ls.start_meeting()
    assert "can't hear your microphone" in note
    assert rec.of("listening")[-1]["meeting"]["note"].startswith("I can't hear your microphone")
    await say_into(ls, SYSTEM, 0)
    assert [ln.who for ln in ls.meeting.lines] == ["Others"]
    await ls.stop_all()


async def test_no_speakers_means_no_listening_and_a_plain_reason(settings, tmp_path):
    brain, rec, ls = hearing(settings, tmp_path, ["x"])
    ls.ears.make_capture = lambda source, sink: _broken(source, sink)
    await brain.turn("take notes on this call")
    assert not ls.active() and rec.of("listening") == []
    assert "couldn't listen to this PC's sound" in rec.of("say")[-1]["text"]


def _broken(source: str, sink: Any) -> FakeCapture:
    c = FakeCapture(source, sink)
    c.problem = "I couldn't listen to this PC's sound: Windows has no speakers or headphones to listen to right now."
    return c


async def test_stop_pressed_while_the_notes_are_written_saves_what_there_is(settings, tmp_path):
    brain, rec, ls = hearing(settings, tmp_path, ["We decided to move the launch."])
    brain._listen_llm.wait = 5.0  # the notes take a while
    await ls.start_meeting()
    await say_into(ls, SYSTEM, 0)
    await ls.on_message({"type": "listen", "what": "meeting", "action": "stop"})  # the sign's Stop
    assert rec.of("listening")[-1]["meeting"]["state"] == "writing" and not brain._hushed()
    await asyncio.sleep(0.1)
    await ls.on_message({"type": "listen", "what": "meeting", "action": "stop"})  # ...and again
    card = await notes_card(rec)
    assert not card["ok"] and card["title"] == "Transcript saved, no notes yet"
    assert "Stopped before the notes were finished" in card["body"]
    assert [Path(f).name.endswith("transcript.pdf") for f in card["files"]] == [True]
    assert rec.of("listening")[-1]["meeting"] is None


async def test_stop_everything_stops_the_listening_at_once_and_still_writes_the_notes(settings, tmp_path):
    brain, rec, ls = hearing(settings, tmp_path, ["We decided to move the launch."])
    await ls.start_meeting()
    await ls.start_captions()
    await say_into(ls, SYSTEM, 0)
    message = await brain.stop_everything()
    assert "the captions" in message and "listening to the meeting" in message
    card = await notes_card(rec)
    assert card["ok"] and all(c.stopped for c in FakeCapture.made) and not ls.active()


async def test_a_long_silence_ends_the_meeting_by_itself(settings, tmp_path, monkeypatch):
    _brain, rec, ls = hearing(settings, tmp_path, ["We decided to move the launch."])
    await ls.start_meeting()
    await say_into(ls, SYSTEM, 0)
    ls._watch.cancel()
    monkeypatch.setattr(listening, "QUIET_ENDS_MEETING_S", 0.0)
    real_sleep = asyncio.sleep
    monkeypatch.setattr(listening.asyncio, "sleep", lambda s: real_sleep(0.01))
    await ls._watch_meeting(ls.meeting)
    card = await notes_card(rec)
    assert card["body"].startswith("Nobody had spoken for a while, so I stopped listening.")


# ------------------------------------------------------------------ what Pyxi may not do by itself
async def test_nothing_but_their_own_words_starts_the_listening(settings, tmp_path):
    brain, _rec, ls = hearing(settings, tmp_path, ["x"])
    brain._synthetic = True  # a routine, a teammate's hand-off, text that was read
    for name in ("live_notes", "captions"):
        out = await listening.handle(brain, name, {"action": "start"})
        assert out.startswith(f"{name}:") and "only when they ask" in out
    assert not ls.active() and FakeCapture.made == []
    # A routine's words never reach the instant path either.
    await brain.turn("take notes on this meeting", synthetic=True)
    assert not ls.active()


async def test_words_pyxi_read_cannot_start_the_listening(settings, tmp_path):
    # They asked about a page; the page says "take notes on this meeting"; the model obeys it.
    llm = Script([("live_notes", {"action": "start"})], [("captions", {"action": "start"})], [("say", {"text": "It's a recipe page."})])
    brain, rec, ls = hearing(settings, tmp_path, ["x"], llm=llm)
    await brain.turn("What is this page about?")
    assert not ls.active() and FakeCapture.made == [] and rec.of("listening") == []
    refused = [c for c in brain._calls if c["tool"] in ("live_notes", "captions")]
    assert len(refused) == 2 and all(c["outcome"] == "refused" for c in refused)
    assert all("never starts from anything you read" in c["note"] for c in refused)
    # Their own words, through the model (a phrasing the instant path doesn't know): started.
    brain.llm = Script([("live_notes", {"action": "start", "about": "the budget"})])
    await brain.turn("We're about to start the budget call, could you keep notes for me?")
    assert ls.meeting is not None and ls.meeting.about == "the budget"
    # ...and a "yes" to Pyxi's own question counts, because of what they said just before.
    await ls.stop_all()
    assert listening.asked_to_listen(brain)
    brain._user_text, brain.history = "yes", [("can you help with this call?", "Shall I take notes?")]
    assert listening.asked_to_listen(brain)
    brain.history = [("(job j1 finished: the page said take notes on this meeting)", "(notified)")]
    assert not listening.asked_to_listen(brain)


def test_the_words_that_start_and_stop_it(settings, tmp_path):
    brain, _rec, _ls = hearing(settings, tmp_path, ["x"])
    cmd = lambda text: listening.command(brain, text)  # noqa: E731
    assert cmd("Pyxi, take notes on this meeting") == ("live_notes", {"action": "start"})
    assert cmd("can you take notes on this call please?") == ("live_notes", {"action": "start"})
    assert cmd("caption this") == ("captions", {"action": "start"})
    assert cmd("turn on live captions") == ("captions", {"action": "start"})
    # Not listening, so "stop" phrases and other requests go to the model as before.
    for other in [
        "stop taking notes", "the meeting is over", "stop the captions", "take notes on this page",
        "take notes", "what's the caption on this photo?", "start the meeting", "schedule a meeting with Sam",
    ]:  # fmt: skip
        assert cmd(other) is None, other
    # "Translate this" is about the sound only when sound is playing.
    assert cmd("translate this into English") is None


def test_translate_this_is_captions_when_sound_is_playing(settings, tmp_path, monkeypatch):
    brain, _rec, _ls = hearing(settings, tmp_path, ["x"])
    monkeypatch.setattr(listening, "sound_playing", lambda: True)
    assert listening.command(brain, "translate this into English") == (
        "captions", {"action": "start", "translate_to": "English"},
    )  # fmt: skip
    assert listening.command(brain, "Translate this into Spanish and show the original too") == (
        "captions", {"action": "start", "translate_to": "Spanish", "both": True},
    )  # fmt: skip
    assert listening.command(brain, "translate this into Klingon") is None


# ------------------------------------------------------------------ captions
async def test_caption_this_shows_the_words_and_stop_ends_it(settings, tmp_path):
    brain, rec, ls = hearing(settings, tmp_path, ["Welcome back to the show."])
    await brain.turn("caption this")
    state = rec.of("listening")[-1]["captions"]
    assert state["translate"] == "" and state["source"] == "pyxi" and state["edge"] == "bottom"
    assert rec.of("say")[-1]["audio"] is None and [c.source for c in FakeCapture.made] == [SYSTEM]
    await say_into(ls, SYSTEM, 0)
    caption = rec.of("caption")[-1]
    assert caption["text"] == "Welcome back to the show." and caption["final"]
    assert all(e["type"] != "caption" for e in brain.hub.history)  # the words are kept nowhere
    await ls.on_message({"type": "listen", "what": "captions", "action": "stop"})  # the bar's Stop
    assert rec.of("listening")[-1]["captions"] is None and FakeCapture.made[0].stopped
    assert not brain._hushed()


async def test_captions_and_meeting_notes_together_share_one_capture(settings, tmp_path):
    brain, rec, ls = hearing(settings, tmp_path, ["We decided to move the launch."])
    await ls.start_meeting()
    await ls.start_captions()
    assert len([c for c in FakeCapture.made if c.source == SYSTEM]) == 1
    sign = rec.of("listening")[-1]
    assert sign["meeting"]["state"] == "listening" and sign["captions"] is not None
    await say_into(ls, SYSTEM, 0)
    assert rec.of("caption")[-1]["text"] == "We decided to move the launch."
    assert [ln.text for ln in ls.meeting.lines] == ["We decided to move the launch."]
    await ls.stop_captions()
    assert not capture_of(SYSTEM).stopped and brain._hushed()  # the notes go on
    await ls.stop_meeting()
    await notes_card(rec)
    assert capture_of(SYSTEM).stopped


async def test_the_bars_own_buttons(settings, tmp_path):
    brain, rec, ls = hearing(settings, tmp_path, ["x"])
    await ls.start_captions()
    send = lambda **m: ls.on_message({"type": "listen", "what": "captions", **m})  # noqa: E731
    await send(action="size", delta=1)
    await send(action="size", delta=1)
    await send(action="size", delta=1)  # it stops at the biggest
    assert rec.of("listening")[-1]["captions"]["size"] == 4
    await send(action="move")
    assert rec.of("listening")[-1]["captions"]["edge"] == "top"
    await send(action="set", translate="French", both=True)
    state = rec.of("listening")[-1]["captions"]
    assert state["translate"] == "French" and state["both"] and ls.ears.language == "auto"
    await send(action="set", translate="")
    assert rec.of("listening")[-1]["captions"]["translate"] == ""
    await ls.stop_captions()
    # The size, the place and "both" are remembered for next time.
    again = listening.Listening(brain)
    assert again.prefs["size"] == 4 and again.prefs["edge"] == "top" and again.prefs["both"] is True


async def test_an_unknown_language_is_asked_about_not_guessed(settings, tmp_path):
    brain, _rec, ls = hearing(settings, tmp_path, ["x"])
    brain._user_text = "subtitles in the usual one please"
    out = await listening.handle(brain, "captions", {"action": "start", "translate_to": "the usual one"})
    assert out.startswith("captions:") and "isn't a language I know" in out and not ls.active()


async def test_pyxis_own_voice_is_never_heard(settings, tmp_path):
    brain, _rec, ls = hearing(settings, tmp_path, ["x"])
    import time

    assert not ls._gate(SYSTEM) and not ls._gate(MIC)
    brain._talking_until = time.monotonic() + 2  # a clip of hers is still playing
    assert ls._gate(SYSTEM) and not ls._gate(MIC)
    brain._talking_until = 0.0
    brain.state = "listening"  # they hold the talk key: those words are for Pyxi, not the notes
    assert ls._gate(MIC) and not ls._gate(SYSTEM)
    brain.state = "idle"

    # A model's narration and a walk-through are words only while the PC is listened to.
    class Voice:
        asked: list[str] = []

        async def synthesize(self, text: str, voice: str | None = None) -> Any:
            from pyxi.voice.tts import Speech

            Voice.asked.append(text)
            return Speech(text=text, wav=b"RIFF", duration_s=1.0)

    brain.tts = Voice()
    assert (await brain._narration("Here is the engine.")).wav == b"RIFF"
    brain._talking_until = time.monotonic() + 30  # she is mid-sentence when they ask
    await ls.start_captions()
    assert brain._talking_until == 0.0 and not ls._gate(SYSTEM)  # cut off, so the sound is heard
    quiet = await brain._narration("And this is the piston.")
    assert quiet.wav is None and quiet.text == "And this is the piston." and quiet.duration_s >= 1.5
    assert Voice.asked == ["Here is the engine."]
    await ls.stop_captions()


async def test_a_new_meeting_starts_while_the_last_ones_notes_are_still_being_written(settings, tmp_path):
    brain, rec, ls = hearing(settings, tmp_path, ["First meeting.", "Second meeting."])
    brain._listen_llm.wait = 0.4
    await ls.start_meeting()
    await say_into(ls, SYSTEM, 0)
    await ls.stop_meeting()
    assert ls.meeting_state == "writing" and not brain._hushed()
    assert (await ls.start_meeting()).startswith("Taking notes now")  # no "ask again in a moment"
    assert ls.meeting_state == "listening" and brain._hushed()
    await say_into(ls, SYSTEM, 1)
    await notes_card(rec)  # the first meeting's notes arrive while the second is being heard
    assert ls.meeting is not None and [ln.text for ln in ls.meeting.lines] == ["Second meeting."]
    assert rec.of("listening")[-1]["meeting"]["state"] == "listening"
    await ls.stop_meeting()
    await notes_card(rec, 2)
    first, second = brain._listen_llm.asked
    assert "First meeting." in first and "Second meeting." not in first
    assert "Second meeting." in second and "First meeting." not in second
    assert not ls.active()


async def test_stop_twice_in_a_row_asks_no_model_at_all(settings, tmp_path):
    brain, rec, ls = hearing(settings, tmp_path, ["We decided to move the launch."])
    await ls.start_meeting()
    await say_into(ls, SYSTEM, 0)
    await ls.stop_meeting()
    await ls.stop_meeting()  # at once: the writing had not even begun
    card = await notes_card(rec)
    assert card["title"] == "Transcript saved, no notes yet" and brain._listen_llm.asked == []
    assert len(card["files"]) == 1 and Path(card["files"][0]).is_file()


# ------------------------------------------------------------------ the offers
async def offers(settings, tmp_path, llm=None):
    brain, rec, ls = hearing(settings, tmp_path, ["We decided to move the launch."], llm=llm)
    g = brain.connectors.google
    g.token_path.parent.mkdir(parents=True, exist_ok=True)
    g.token_path.write_text("{}", encoding="utf-8")
    posted: list[dict[str, Any]] = []

    async def api(method: str, url: str, **kw: Any) -> Any:
        if url.endswith("/settings/timezone"):
            return {"value": "UTC"}
        if method == "POST" and url.endswith("/events"):
            posted.append(kw["json"])
            return {"id": f"e{len(posted)}"}
        if method == "GET" and "/events/" in url:
            e = posted[-1]
            return {"summary": e["summary"], "start": e["start"], "end": e["end"], "htmlLink": "https://calendar.example.com/e"}
        raise AssertionError(f"unexpected {method} {url}")

    g._api = api  # type: ignore[method-assign]
    await ls.start_meeting()
    await say_into(ls, SYSTEM, 0)
    await ls.stop_meeting()
    await notes_card(rec)
    cards = await until(rec, "confirm", 2)
    return brain, rec, posted, {c["action"]: c for c in cards}


async def test_the_email_and_the_calendar_are_only_offered(settings, tmp_path):
    brain, rec, posted, cards = await offers(settings, tmp_path)
    assert set(cards) == {"Draft a follow-up email for this meeting", "Put 1 action item in your calendar"}
    assert "Subject: Launch plan" in cards["Draft a follow-up email for this meeting"]["reason"]
    assert "Nothing is sent until you allow that card" in cards["Draft a follow-up email for this meeting"]["reason"]
    cal = cards["Put 1 action item in your calendar"]
    assert "Priya: Send the revised plan" in cal["reason"] and "Book the venue" not in cal["reason"]  # no date, no entry
    for card in cards.values():
        brain.hub.resolve(card["request_id"], False)  # no to both
    await until(rec, "request_closed", 2)
    await asyncio.sleep(0.05)
    assert posted == [] and len(rec.of("user")) == 0  # nothing added, no email started


async def test_allow_puts_the_dated_action_item_in_the_calendar(settings, tmp_path):
    brain, rec, posted, cards = await offers(settings, tmp_path)
    brain.hub.resolve(cards["Draft a follow-up email for this meeting"]["request_id"], False)
    brain.hub.resolve(cards["Put 1 action item in your calendar"]["request_id"], True)
    for _ in range(200):
        if any(r["title"].startswith("Added 1 action item") for r in rec.of("job_ready")):
            break
        await asyncio.sleep(0.02)
    assert [e["summary"] for e in posted] == ["Priya: Send the revised plan"]
    assert posted[0]["start"]["dateTime"].startswith(f"{FRIDAY}T09:00") and "attendees" not in posted[0]


async def test_allow_hands_the_follow_up_to_the_mail_tool_which_asks_again(settings, tmp_path):
    # The chat model, asked to prepare the email, has nobody's address: it asks.
    llm = Script([("say", {"text": "Who should get the follow-up?"})])
    brain, rec, posted, cards = await offers(settings, tmp_path, llm=llm)
    brain.hub.resolve(cards["Put 1 action item in your calendar"]["request_id"], False)
    brain.hub.resolve(cards["Draft a follow-up email for this meeting"]["request_id"], True)
    shown = await until(rec, "user")
    assert shown and shown[0]["text"] == "Follow-up email: Launch planning"
    for _ in range(200):
        if llm.prompts:
            break
        await asyncio.sleep(0.02)
    assert "gmail_send" in llm.prompts[0] and "Priya" in llm.prompts[0] and "Thanks for today." in llm.prompts[0]
    assert posted == []  # nothing was sent or scheduled by the offer itself


async def test_without_google_the_card_says_what_it_would_take(settings, tmp_path):
    _brain, rec, ls = hearing(settings, tmp_path, ["We decided to move the launch."])
    await ls.start_meeting()
    await say_into(ls, SYSTEM, 0)
    await ls.stop_meeting()
    card = await notes_card(rec)
    assert "Connect Google in Settings" in card["body"]
    await asyncio.sleep(0.05)
    assert rec.of("confirm") == []


def test_the_small_model_is_used_where_it_can_be_reached(settings, monkeypatch):
    made: list[str] = []
    monkeypatch.setattr("pyxi.llm.make_llm", lambda s, model=None: made.append(model) or object())

    def small(model: str, key: bool = False, local: bool = False) -> Any:
        brain, _rec = make_brain(settings, llm=Script())
        monkeypatch.setattr(settings, "model", model)
        monkeypatch.setattr(settings, "local_mode", local)
        if key:
            monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-for-this-test")
        else:
            monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        made.clear()
        return brain, listening.listen_llm(brain)

    # OpenRouter (and the beta): Haiku, with the chat model behind it if it can't be reached.
    brain, llm = small("anthropic/claude-sonnet-5.5", key=True)
    assert made == ["anthropic/claude-haiku-5.5"] and isinstance(llm, listening._OrChat)
    assert listening.listen_llm(brain) is llm and made == ["anthropic/claude-haiku-5.5"]  # made once
    # A key of their own with Anthropic: its Haiku.
    _, llm = small("claude-opus-5-5")
    assert made == ["claude-haiku-5-5"] and isinstance(llm, listening._OrChat)
    # Already the small model, another provider, or local mode: the chat model itself.
    for model, kw in [("anthropic/claude-haiku-5.5", {"key": True}), ("gpt-5", {}), ("claude-opus-5-5", {"local": True})]:
        brain, llm = small(model, **kw)
        assert made == [] and llm is brain.llm, model


async def test_the_chat_model_takes_over_when_the_small_one_cannot_be_reached():
    class Down:
        async def complete(self, **kw: Any) -> Any:
            raise RuntimeError("model not found")

    class Up:
        calls = 0

        async def complete(self, **kw: Any) -> str:
            Up.calls += 1
            return "ok"

    llm = listening._OrChat(Down(), Up())
    assert await llm.complete(system="s", messages=[]) == "ok" and llm.failed
    assert await llm.complete(system="s", messages=[]) == "ok" and Up.calls == 2


def test_the_apps_own_buttons_reach_the_brain_and_no_other_program_does(settings, tmp_path):
    from fastapi.testclient import TestClient

    from pyxi.server import create_app

    brain, _rec, ls = hearing(settings, tmp_path, ["x"])
    app = create_app(settings, demo=True, brain=brain, key="k")
    with TestClient(app) as client:
        assert client.post("/api/listen/captions").status_code == 401  # no launch key
        assert not ls.active()
        keyed = {"x-pyxi-key": "k"}
        assert client.post("/api/listen/everything", headers=keyed).status_code == 404
        r = client.post("/api/listen/captions", headers=keyed)  # the tray: on
        assert r.status_code == 200 and r.json()["captions"]["source"] == "pyxi"
        # A window that opens while Pyxi listens is told at once, so it shows the sign.
        with client.websocket_connect("/ws?key=k") as ws:
            assert ws.receive_json()["type"] == "hello"
            sign = ws.receive_json()
            assert sign["type"] == "listening" and sign["captions"] is not None
            ws.send_json({"type": "listen", "what": "captions", "action": "stop"})  # the bar's Stop
            for _ in range(20):
                e = ws.receive_json()
                if e["type"] == "listening":
                    break
            assert e["captions"] is None and e["meeting"] is None
    assert not ls.active() and all(c.stopped for c in FakeCapture.made)


@pytest.mark.parametrize("what", ["meeting", "captions"])
async def test_the_tray_menu_turns_it_on_and_off(settings, tmp_path, what, monkeypatch):
    import time

    from pyxi import hands

    _brain, rec, ls = hearing(settings, tmp_path, ["x"])
    # A press made while Pyxi's own hands had the mouse may be its own: it starts nothing.
    monkeypatch.setattr(hands, "_last_input", time.monotonic())
    await ls.on_message({"type": "listen", "what": what, "action": "toggle"})
    assert not ls.active() and FakeCapture.made == []
    monkeypatch.setattr(hands, "_last_input", 0.0)
    await ls.on_message({"type": "listen", "what": what, "action": "toggle"})
    assert ls.active() and rec.of("listening")[-1][what] is not None
    monkeypatch.setattr(hands, "_last_input", time.monotonic())  # Stop always counts
    await ls.on_message({"type": "listen", "what": what, "action": "toggle"})
    monkeypatch.setattr(hands, "_last_input", 0.0)
    if what == "meeting":
        await notes_card(rec)
    assert not ls.active()
