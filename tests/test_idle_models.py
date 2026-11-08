"""Speech and voice models load when a question starts and leave memory when Pyxi is idle."""

from __future__ import annotations

import asyncio
import time

import pytest

from pyxi.config import VoiceConfig
from pyxi.voice import stt as stt_mod
from pyxi.voice.tts import KokoroTTS


class FakeWhisper:
    def __init__(self, name: str) -> None:
        self.name = name

    def transcribe(self, audio, **kw):
        seg = type("S", (), {"text": f" heard by {self.name} "})()
        return iter([seg]), type("I", (), {"language": "en"})()


def _transcriber(monkeypatch) -> tuple[stt_mod.Transcriber, list[str]]:
    t = stt_mod.Transcriber(VoiceConfig(stt_model="base.en", stt_device="cpu"))
    loads: list[str] = []

    def load(name: str):
        loads.append(name)
        return FakeWhisper(name)

    monkeypatch.setattr(t, "_load", load)
    monkeypatch.setattr(stt_mod, "decode_audio", lambda data: data)
    return t, loads


async def test_the_talk_key_loads_the_model_before_the_audio_arrives(monkeypatch):
    t, loads = _transcriber(monkeypatch)
    assert t.loaded() == []  # nothing at start-up
    t.warm("en")
    await asyncio.sleep(0.05)
    assert loads == ["base.en"] and t.loaded() == ["base.en"]
    text, _, _ = await t.transcribe(b"x", "en")
    assert text == "heard by base.en" and loads == ["base.en"]  # not loaded twice


async def test_idle_models_are_let_go_and_come_back(monkeypatch):
    t, loads = _transcriber(monkeypatch)
    await t.transcribe(b"x", "en")
    assert t.release_idle(60) == []  # just used
    t._used["base.en"] = time.monotonic() - 120
    assert t.release_idle(60) == ["base.en"] and t.loaded() == []
    await t.transcribe(b"x", "en")
    assert loads == ["base.en", "base.en"]


async def test_one_speech_model_at_a_time(monkeypatch):
    t, loads = _transcriber(monkeypatch)
    await t.transcribe(b"x", "en")
    await t.transcribe(b"x", "fr")  # multilingual model: swaps, doesn't add
    assert loads == ["base.en", "small"] and t.loaded() == ["small"]


async def test_a_long_recording_keeps_its_model(monkeypatch):
    t, _ = _transcriber(monkeypatch)
    async with t.hold("en") as model:
        t._used["base.en"] = time.monotonic() - 999
        assert t.release_idle(1) == []  # in use: stays
        assert model.name == "base.en"
    t._used["base.en"] = time.monotonic() - 999
    assert t.release_idle(1) == ["base.en"]


def test_no_gpu_libraries_means_the_cpu_without_trying_cuda(monkeypatch):
    import sys
    import types

    fake = types.SimpleNamespace(get_cuda_device_count=lambda: 1)
    monkeypatch.setitem(sys.modules, "ctranslate2", fake)
    if sys.platform == "win32":
        import ctypes

        def missing(name):
            raise OSError(f"{name} not found")

        monkeypatch.setattr(ctypes, "WinDLL", missing)
        assert stt_mod._cuda_usable() is False
    fake.get_cuda_device_count = lambda: 0
    assert stt_mod._cuda_usable() is False


async def test_kokoro_lets_go_when_quiet(tmp_path, monkeypatch):
    k = KokoroTTS(VoiceConfig(), tmp_path)
    k._engine = object()
    k._used = time.monotonic()
    assert k.release_idle(60) is False
    k._used = time.monotonic() - 120
    k._busy = 1  # mid-sentence: never
    assert k.release_idle(60) is False
    k._busy = 0
    assert k.release_idle(60) is True and k._engine is None


async def test_warming_kokoro_without_its_files_does_nothing(tmp_path):
    k = KokoroTTS(VoiceConfig(), tmp_path)
    k.warm()
    assert k._warming is None


@pytest.mark.parametrize("state", ["idle", "speaking"])
async def test_the_brain_only_lets_go_when_idle(tmp_path, state):
    from pyxi.companion.brain import Brain
    from pyxi.config import PyxiSettings
    from pyxi.hub import Hub
    from pyxi.voice.tts import ClientTTS, FallbackTTS

    settings = PyxiSettings(data_dir=tmp_path)
    brain = Brain(settings, Hub(), tts=FallbackTTS(ClientTTS()))
    brain.stt._models["base.en"] = FakeWhisper("base.en")
    brain.stt._used["base.en"] = time.monotonic() - 999
    brain.state = state
    gone = brain.release_idle(300)
    assert gone == (["ears base.en"] if state == "idle" else [])
    assert brain.release_idle(0) == []  # 0 keeps everything loaded
