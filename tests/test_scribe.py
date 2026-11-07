"""Scribe, the meetings teammate: a recording in, notes, decisions, action items and a follow-up
email out, sent only after the approval card.

Most tests use a recorded transcript (a fake Whisper) and a scripted model, so they're fast
and never touch the network. One test speaks a short meeting with Kokoro and transcribes it
with the real faster-whisper, when both models are on this PC.
"""

from __future__ import annotations

import json
import wave
from pathlib import Path
from typing import Any

import pytest

from pyxi.agents import scribe
from pyxi.companion import specialists
from test_connectors_and_docs import Script
from test_mailwatch import connected, until

STANDUP = [
    (0.0, 6.2, "Okay, morning everyone, quick standup for the beta launch."),
    (
        6.5,
        14.0,
        "Sam here. The installer is down to forty megabytes and installs in under a minute.",
    ),
    (14.3, 22.8, "Great. Then we ship the beta on Friday. Everyone agree? Yes, Friday it is."),
    (23.0, 31.5, "Priya, can you write the release notes by Thursday?"),
    (31.8, 36.0, "Sure, I'll have them by Thursday noon."),
    (36.2, 44.0, "And someone needs to email the twenty testers. Let's figure out who later."),
]

NOTES = {
    "title": "Beta launch standup",
    "summary": ["The installer is ready.", "The beta ships on Friday."],
    "decisions": ["Ship the beta on Friday."],
    "actions": [
        {"who": "Priya", "what": "Write the release notes", "when": "Thursday noon"},
        {"who": "", "what": "Email the twenty testers", "when": ""},
    ],
    "follow_up": {
        "subject": "Beta launch: notes from standup",
        "body": "Hi all,\n\nThanks for the quick standup — here's what we agreed.\n"
        "We ship the beta on Friday. Priya writes the release notes by Thursday noon.\n"
        "We still need someone to email the testers.\n\nThanks!",
    },
}


class FakeModel:
    def __init__(self, segments):
        self.segments = segments
        self.audio_seconds = 0.0

    def transcribe(self, audio, **kw):
        self.audio_seconds = len(audio) / 16000

        class S:
            def __init__(self, start, end, text):
                self.start, self.end, self.text = start, end, text

        return (S(*s) for s in self.segments), None


class FakeSTT:
    def __init__(self, segments=STANDUP):
        self.model = FakeModel(segments)

    async def warm_up(self, language="en"):
        return self.model

    def model_for(self, language):
        return "base.en"


def recording(path: Path, seconds: float = 1.0) -> Path:
    """A real (silent) WAV, so decoding goes through PyAV like a real recording."""
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * int(16000 * seconds))
    return path


def test_notes_are_read_strictly_and_never_show_dashes():
    notes = scribe.parse_notes("Sure! " + json.dumps(NOTES))
    assert notes.title == "Beta launch standup" and len(notes.actions) == 2
    assert "—" not in notes.body and "Thanks for the quick standup, here's" in notes.body
    assert (
        notes.action_line(notes.actions[0]) == "Priya: Write the release notes (by Thursday noon)"
    )
    assert notes.action_line(notes.actions[1]) == "No owner yet: Email the twenty testers"
    md = notes.markdown("standup.m4a", 1)
    assert "## Decisions\n\n- Ship the beta on Friday." in md and "- [ ] Priya" in md
    assert "(draft, not sent)" in md
    assert scribe.parse_notes("no json here") is None
    assert scribe.parse_notes('{"actions": [{"who": "x"}]}').actions == []  # no task, no action


def test_long_meetings_are_split_at_line_boundaries():
    lines = [f"[00:00:{i:02d}] " + "word " * 50 for i in range(60)]
    chunks = scribe.parts(lines, size=4000)
    assert len(chunks) > 3 and all(len(c) <= 4300 for c in chunks)
    assert "\n".join(chunks).splitlines() == lines


async def test_a_recording_becomes_notes_and_files(settings, tmp_path, monkeypatch):
    monkeypatch.setattr(scribe, "out_dir", lambda: tmp_path / "Meetings")
    rec_path = recording(tmp_path / "standup.wav", 2.0)
    brain, rec, fake = connected(settings, [], llm=Script(json.dumps(NOTES)))
    brain.stt = FakeSTT()
    out = await specialists.handle(
        brain, "meeting_notes", {"file": str(rec_path), "about": "beta launch"}
    )
    assert out.startswith("Started job_") and "Scribe" in out
    [ready] = await until(rec, "job_ready")
    assert ready["agent"] == "Scribe" and ready["ok"], ready
    assert ready["title"] == "Notes: Beta launch standup"
    assert ready["body"].startswith("1 min. 1 decision, 2 action items.")
    assert "nothing is sent unless you allow it" in ready["body"]
    assert ready["changes"][0].startswith("Priya: Write the release notes")
    notes, script = (Path(f) for f in ready["files"])
    assert notes.name == "standup notes.md" and script.name == "standup transcript.txt"
    assert "[00:00:23] Priya, can you write the release notes" in script.read_text(encoding="utf-8")
    assert brain.stt.model.audio_seconds == pytest.approx(2.0, abs=0.05)  # really decoded
    assert brain.llm.prompts and "beta launch" in brain.llm.prompts[0]
    assert "[00:00:14] Great. Then we ship" in brain.llm.prompts[0]
    await brain.jobs.wait_all()
    assert not rec.of("confirm") and fake.sent == []  # no `to`: no card, nothing sent


async def test_the_follow_up_goes_only_through_the_card(settings, tmp_path, monkeypatch):
    monkeypatch.setattr(scribe, "out_dir", lambda: tmp_path / "Meetings")
    rec_path = recording(tmp_path / "call.m4a.wav", 1.0)
    brain, rec, fake = connected(settings, [], llm=Script(json.dumps(NOTES), json.dumps(NOTES)))
    brain.stt = FakeSTT()
    sent: list[Any] = []

    async def api(method, url, **kw):
        if method == "POST":
            sent.append(kw["json"])
            return {"id": "sent1"}
        return await fake(method, url, **kw)

    brain.connectors.google._api = api  # type: ignore[method-assign]
    args = {"file": str(rec_path), "to": ["priya@example.com", "sam@example.com"]}
    await specialists.handle(brain, "meeting_notes", args)
    [card] = await until(rec, "confirm")
    assert card["agent"] == "Scribe" and "priya@example.com" in card["action"]
    assert "Subject: Beta launch: notes from standup" in card["reason"]
    brain.hub.resolve(card["request_id"], False)  # they said no
    await until(rec, "request_closed")
    assert sent == []
    # Again, and this time they allow it.
    await specialists.handle(brain, "meeting_notes", args)
    cards = await until(rec, "confirm", 2)
    brain.hub.resolve(cards[1]["request_id"], True)
    for _ in range(100):
        if any(r["title"] == "Follow-up sent" for r in rec.of("job_ready")):
            break
        await __import__("asyncio").sleep(0.02)
    assert len(sent) == 1 and any(r["title"] == "Follow-up sent" for r in rec.of("job_ready"))


async def test_missing_or_wrong_files_answer_straight_away(settings, tmp_path):
    brain, _rec, _ = connected(settings, [])
    out = await specialists.handle(brain, "meeting_notes", {"file": str(tmp_path / "nope.m4a")})
    assert "no recording at" in out and not brain.jobs.jobs
    doc = tmp_path / "agenda.docx"
    doc.write_text("x")
    out = await specialists.handle(brain, "meeting_notes", {"file": str(doc)})
    assert "isn't an audio or video file" in out and not brain.jobs.jobs


async def test_silence_says_so(settings, tmp_path, monkeypatch):
    monkeypatch.setattr(scribe, "out_dir", lambda: tmp_path / "Meetings")
    rec_path = recording(tmp_path / "empty.wav", 1.0)
    brain, rec, _ = connected(settings, [])
    brain.stt = FakeSTT(segments=[])
    await specialists.handle(brain, "meeting_notes", {"file": str(rec_path)})
    [ready] = await until(rec, "job_ready")
    assert not ready["ok"] and ready["title"] == "No speech in that recording"


MODELS = Path(".pyxi/models/kokoro-v1.0.onnx")
WHISPER = Path.home() / ".cache/huggingface/hub/models--Systran--faster-whisper-base.en"


@pytest.mark.skipif(not (MODELS.exists() and WHISPER.exists()), reason="Kokoro or Whisper not here")
async def test_a_spoken_meeting_is_really_transcribed(tmp_path):
    from pyxi.config import VoiceConfig
    from pyxi.voice.stt import Transcriber
    from pyxi.voice.tts import KokoroTTS

    speech = await KokoroTTS(VoiceConfig(), Path(".pyxi/models")).synthesize(
        "We ship the beta on Friday. Priya will write the release notes by Thursday."
    )
    path = tmp_path / "standup.wav"
    path.write_bytes(speech.wav)
    stt = Transcriber(VoiceConfig(stt_device="cpu"))
    segments = await scribe.transcribe(stt, path, "en")
    text = " ".join(s.text for s in segments).lower()
    assert "beta" in text and "friday" in text and "release notes" in text
    assert segments[0].line().startswith("[00:00:0")


# ---------------------------------------------------------------- found in the beta QA pass
@pytest.mark.parametrize("data", [b"", b"ID3\x03\x00 not audio at all" * 40])
async def test_a_damaged_recording_is_said_plainly(settings, tmp_path, monkeypatch, data):
    monkeypatch.setattr(scribe, "out_dir", lambda: tmp_path / "Meetings")
    bad = tmp_path / "call.mp3"
    bad.write_bytes(data)
    brain, rec, _ = connected(settings, [], llm=Script(json.dumps(NOTES)))
    brain.stt = FakeSTT()
    await specialists.handle(brain, "meeting_notes", {"file": str(bad)})
    [ready] = await until(rec, "job_ready")
    assert not ready["ok"] and ready["body"] == (
        "I couldn't read the sound in call.mp3. It may be damaged, or have no audio."
    )


async def test_the_summary_has_no_double_full_stops(settings, tmp_path, monkeypatch):
    monkeypatch.setattr(scribe, "out_dir", lambda: tmp_path / "Meetings")
    brain, rec, _ = connected(settings, [], llm=Script(json.dumps(NOTES)))
    brain.stt = FakeSTT()
    await specialists.handle(brain, "meeting_notes", {"file": str(recording(tmp_path / "a.wav"))})
    await until(rec, "job_ready")
    job = next(iter(brain.jobs.jobs.values()))
    assert "Decisions: Ship the beta on Friday." in job.summary and ".." not in job.summary
