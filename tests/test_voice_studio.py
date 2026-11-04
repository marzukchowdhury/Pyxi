"""The voice studio: preferences persist, tone tags are performed not read, cloud voices fall back."""

from __future__ import annotations

import pytest

from wisp.config import VoiceConfig
from wisp.voice.studio import VoiceStudio, split_tone
from wisp.voice.tts import Speech, split_sentences, strip_tags


@pytest.fixture
def studio(tmp_path, monkeypatch):
    s = VoiceStudio(VoiceConfig(), tmp_path / "models", tmp_path / "voice.json")
    calls: list[dict] = []

    async def fake_kokoro(text, *, voice, speed, lang):
        calls.append({"text": text, "voice": voice, "speed": speed, "lang": lang})
        return Speech(text=text, wav=b"RIFF", duration_s=1.0)

    monkeypatch.setattr(s.kokoro, "synthesize_with", fake_kokoro)
    s.calls = calls  # type: ignore[attr-defined]
    return s


def test_prefs_persist_and_switching_engine_picks_a_voice(studio, tmp_path):
    studio.update(engine="gemini")
    assert studio.prefs.voice == "Puck"
    studio.update(speed=1.25, language="es", captions=False)
    again = VoiceStudio(VoiceConfig(), tmp_path / "models", tmp_path / "voice.json")
    assert (again.prefs.engine, again.prefs.speed, again.prefs.language, again.prefs.captions) == (
        "gemini",
        1.25,
        "es",
        False,
    )


def test_tone_tags_are_performed_not_spoken():
    assert split_tone("{Playful} Ha, nice.") == ("playful", "Ha, nice.")
    assert strip_tags("{dry} Nine settings pages [laugh].") == "Nine settings pages ."
    assert split_sentences("Sure. {dry} Nine pages.") == ["Sure.", "{dry} Nine pages."]


async def test_teammates_keep_their_own_kokoro_voice(studio):
    studio.update(engine="gemini")
    await studio.synthesize("Found it.", voice="am_puck")
    assert studio.calls[-1]["voice"] == "am_puck"


async def test_a_failing_cloud_voice_falls_back_and_says_why(studio, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    studio.update(engine="elevenlabs", voice="abc")

    async def boom(*a, **k):
        raise RuntimeError("HTTP 401: quota_exceeded")

    monkeypatch.setattr(studio, "_eleven", boom)
    speech = await studio.synthesize("{excited} It works!")
    assert speech.wav == b"RIFF" and studio.calls[-1]["voice"] == "af_heart"
    assert studio.calls[-1]["speed"] == pytest.approx(1.06)  # excited reads a touch quicker
    assert "elevenlabs voice failed" in (studio.last_error or "")


async def test_spanish_gets_a_spanish_voice(studio):
    studio.update(language="es")
    await studio.synthesize("Hola, soy Wisp.")
    assert studio.calls[-1]["lang"] == "es" and studio.calls[-1]["voice"] == "ef_dora"
