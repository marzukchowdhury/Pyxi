"""Voice failures say what is wrong and what to do. "I couldn't make out the audio. Try again?"
is for bad audio only: with no internet, no listening pack or a spent allowance, trying
again could never work."""

from __future__ import annotations

import httpx
import pytest

from pyxi import dictation
from pyxi import plainwords as pw
from test_answer_card import make_brain

BAD_AUDIO = "I couldn't make out the audio. Try again?"


class Ears:
    hints: list[str] = []
    on_this_pc = False

    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    async def transcribe(self, audio, language="en", **kw):
        raise self.exc


class LocalEntryNotFoundError(Exception):
    pass


CASES = [
    (
        RuntimeError(
            "Bengali listening needs the internet (the Pyxi beta or an OpenRouter key), or "
            "`pyxi setup-voice --bengali` for offline Bengali. (ConnectError)"
        ),
        pw.BENGALI_OFFLINE,
    ),
    (
        LocalEntryNotFoundError("Cannot find an appropriate cached snapshot folder for the specified revision"),
        pw.HEARING_OFFLINE,
    ),
    (httpx.ConnectError("[Errno 11001] getaddrinfo failed"), "I couldn't write down what you said. " + pw.OFFLINE),
    (
        RuntimeError("OpenRouter API error: Error code: 429 - {'error': {'message': \"You've used today's Pyxi beta allowance. It resets at midnight UTC.\"}}"),
        "I couldn't write down what you said. You've used today's Pyxi beta allowance. It resets at midnight UTC.",
    ),
    (ValueError("could not decode the audio stream"), BAD_AUDIO),
]


@pytest.mark.parametrize("exc,line", CASES)
async def test_a_question_that_could_not_be_heard_says_why(settings, exc, line):
    brain, rec = make_brain(settings)
    brain.stt = Ears(exc)
    await brain.hear(b"audio")
    assert [e["message"] for e in rec.of("error")] == [line]
    assert "pyxi setup-voice" not in line and "Error" not in line and "(" not in line


@pytest.mark.parametrize("exc,line", CASES)
async def test_a_dictation_that_could_not_be_heard_says_why(settings, exc, line):
    brain, rec = make_brain(settings)
    brain.stt = Ears(exc)
    assert await brain.dictate(b"audio") == ""
    [done] = rec.of("dictated")
    assert done["how"] == "failed"
    assert done["note"] == (dictation.NOTES["failed"] if line == BAD_AUDIO else line)


def test_the_next_step_is_in_each_line():
    assert "Check the connection" in pw.BENGALI_OFFLINE
    assert "Check the internet connection" in pw.HEARING_OFFLINE


# ---------------------------------------------------------------- the Voice panel
def test_a_cloud_voice_that_fails_says_why_and_what_to_check():
    from pyxi.voice import studio

    refused = RuntimeError('HTTP 401: {"detail": {"status": "invalid_api_key", "message": "Invalid API key"}}')
    assert studio._why(refused) == "its key was turned down"
    assert studio._next("ElevenLabs", refused) == "Check the ElevenLabs key."
    spent = RuntimeError('HTTP 429: {"error": {"message": "Quota exceeded"}}')
    assert studio._why(spent) == "its allowance is used up" and "pick another voice" in studio._next("Gemini", spent)
    offline = httpx.ConnectError("[Errno 11001] getaddrinfo failed")
    assert studio._why(offline) == "it can't be reached"
    assert studio._next("Gemini", offline) == "Check the internet connection."
    assert studio._why(KeyError("audio")) == "it didn't answer properly"


async def test_the_bengali_voice_download_failing_names_no_python(tmp_path, monkeypatch):
    from pyxi.voice import bengali

    async def no_net(folder):
        raise httpx.ConnectError("getaddrinfo failed")

    monkeypatch.setattr(bengali, "download_bengali_voice", no_net)
    voice = bengali.BengaliVoice(tmp_path / "bn")
    with pytest.raises(RuntimeError) as err:
        await voice.fetch()
    assert str(err.value) == "Couldn't download the Bengali voice. Check the internet connection"
    assert "ConnectError" not in str(err.value)
