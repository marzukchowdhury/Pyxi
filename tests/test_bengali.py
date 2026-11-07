"""Bengali: numbers read as words, Bengali lines always get a Bengali voice, and listening goes to
the cloud (or the offline model) instead of a Whisper model that can't hear Bengali."""

from __future__ import annotations

import numpy as np
import pytest

from pyxi.companion.prompts import turn_prompt
from pyxi.config import VoiceConfig
from pyxi.voice import stt as stt_mod
from pyxi.voice.bengali import is_bengali, number_words, speakable_bengali
from pyxi.voice.studio import VoiceStudio
from pyxi.voice.tts import Speech, split_sentences


def test_numbers_are_read_as_bengali_words():
    assert number_words(1275) == "এক হাজার দুইশো পঁচাত্তর"
    assert number_words(150000) == "এক লাখ পঞ্চাশ হাজার"
    assert number_words(12_500_000) == "এক কোটি পঁচিশ লাখ"
    assert speakable_bengali("মোট ১,২৭৫ টাকা") == "মোট এক হাজার দুইশো পঁচাত্তর টাকা"
    assert speakable_bengali("৳1,275 দিতে হবে") == "এক হাজার দুইশো পঁচাত্তর টাকা দিতে হবে"
    assert speakable_bengali("৭৫% শেষ") == "পঁচাত্তর শতাংশ শেষ"
    assert speakable_bengali("বিকেল ৪:৩০টায়") == "বিকেল চারটা ত্রিশ মিনিটে"
    assert speakable_bengali("৪টায় আসো") == "চারটায় আসো"
    # A phone number is said digit by digit, an acronym by its letters.
    assert speakable_bengali("01712 নম্বরে") == "শূন্য এক সাত এক দুই নম্বরে"
    assert speakable_bengali("PDFটা খুলি?") == "পিডিএফটা খুলি?"


def test_bengali_sentences_split_at_the_dari():
    text = "ইমেইলটা পড়ে দেখলাম। ওরা জানতে চাইছে তুমি আসবে কি না? আমি জানিয়ে দেব।"
    assert split_sentences(text) == [
        "ইমেইলটা পড়ে দেখলাম।",
        "ওরা জানতে চাইছে তুমি আসবে কি না?",
        "আমি জানিয়ে দেব।",
    ]
    assert split_sentences("Sure. {dry} Nine pages.") == ["Sure.", "{dry} Nine pages."]


def test_script_detection():
    assert is_bengali("আমি এক্সেল খুলছি")
    assert is_bengali("আমি Excel খুলছি")
    assert not is_bengali("Opening Excel now.")
    assert not is_bengali("")


@pytest.fixture
def studio(tmp_path, monkeypatch):
    s = VoiceStudio(VoiceConfig(), tmp_path / "models", tmp_path / "voice.json")
    calls: list[tuple[str, str]] = []

    async def fake_kokoro(text, *, voice, speed, lang):
        calls.append(("kokoro", voice))
        return Speech(text=text, wav=b"RIFF-en", duration_s=1.0)

    async def fake_bengali(text, speed=1.0):
        calls.append(("bengali", text))
        return Speech(text=text, wav=b"RIFF-bn", duration_s=1.0)

    monkeypatch.setattr(s.kokoro, "synthesize_with", fake_kokoro)
    monkeypatch.setattr(s.bengali, "synthesize", fake_bengali)
    s.calls = calls  # type: ignore[attr-defined]
    return s


async def test_bengali_lines_get_the_bengali_voice_whoever_says_them(studio):
    studio.update(language="bn")
    assert (await studio.synthesize("{warm} হাই, আমি পিক্সি।")).wav == b"RIFF-bn"
    # A teammate's Kokoro voice can't read Bengali, so Mitali reads its Bengali lines...
    assert (await studio.synthesize("পেয়ে গেছি।", voice="am_puck")).wav == b"RIFF-bn"
    # ...and its English lines stay in its own voice, so you can still tell who's talking.
    await studio.synthesize("Found it.", voice="am_puck")
    assert studio.calls[-1] == ("kokoro", "am_puck")


async def test_choosing_mitali_still_speaks_english_lines_in_english(studio):
    studio.update(voice="bn_mitali")
    await studio.synthesize("Hi, I'm Pyxi.")
    assert studio.calls[-1] == ("kokoro", "af_heart")


async def test_a_failing_cloud_voice_falls_back_to_mitali_not_to_english(studio, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    studio.update(engine="gemini", language="bn")

    async def boom(*a, **k):
        raise RuntimeError("HTTP 429: quota")

    monkeypatch.setattr(studio, "_gemini", boom)
    speech = await studio.synthesize("আজ বিকেল চারটায় মিটিং আছে।")
    assert speech.wav == b"RIFF-bn" and "gemini voice failed" in (studio.last_error or "")


async def test_no_bengali_voice_yet_shows_the_words_instead_of_an_english_voice(
    tmp_path, monkeypatch
):
    s = VoiceStudio(VoiceConfig(), tmp_path / "models", tmp_path / "voice.json")

    async def offline(*a, **k):
        raise OSError("no internet")

    monkeypatch.setattr("pyxi.voice.bengali.download_bengali_voice", offline)
    speech = await s.synthesize("হাই, আমি পিক্সি।")
    assert speech.wav is None and speech.text == "হাই, আমি পিক্সি।"
    assert "Bengali voice" in (s.last_error or "")


def test_the_picker_offers_a_bengali_voice(studio):
    import asyncio

    catalog = asyncio.run(studio.catalog())
    mitali = next(v for v in catalog["kokoro"] if v["id"] == "bn_mitali")
    assert mitali["lang"] == "bn" and is_bengali(mitali["sample"])


def test_hearing_bengali_in_automatic_fetches_the_voice(studio, monkeypatch):
    wanted: list[bool] = []
    monkeypatch.setattr(studio, "prepare_bengali", lambda: wanted.append(True))
    studio.update(language="auto")
    studio.detected_language = "en"
    assert not wanted and not studio.wants_bengali()
    studio.detected_language = "bn"
    assert wanted and studio.wants_bengali()


def test_bengali_replies_are_asked_for_in_bengali_script():
    prompt = turn_prompt(
        user_text="আমার ডাউনলোডস ফোল্ডারটা খোলো",
        history=[],
        screen="(no screen)",
        jobs="",
        memories="",
        notes=[],
        budget_line="",
        language="Bengali",
    )
    assert "Bengali script" in prompt and "Latin letters" in prompt


# ------------------------------------------------------------------ hearing
class FakeModel:
    def __init__(self, probs: dict[str, float]) -> None:
        self.probs = probs
        self.calls: list[str | None] = []

    def detect_language(self, samples):
        best = max(self.probs, key=self.probs.get)
        return best, self.probs[best], list(self.probs.items())

    def transcribe(self, samples, language=None, **kw):
        self.calls.append(language)

        class Seg:
            text = "local words"

        class Info:
            pass

        Info.language = language or "en"
        return [Seg()], Info()


def _transcriber(monkeypatch, probs, *, cloud: str | Exception | None, offline=False):
    t = stt_mod.Transcriber(VoiceConfig(stt_model="base.en"))
    model = FakeModel(probs)
    t._models = {"small": model, t.config.stt_bengali_model: model}
    monkeypatch.setattr(stt_mod, "decode_audio", lambda audio: np.zeros(16000, np.float32))
    monkeypatch.setattr(t, "bengali_offline", lambda: offline)
    sent: list[str] = []

    async def ears(samples, language):
        sent.append(language)
        if isinstance(cloud, Exception):
            raise cloud
        return cloud or ""

    if cloud is None:
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    else:
        monkeypatch.setenv("OPENROUTER_API_KEY", "pyxi_test")
    monkeypatch.setattr(t.ears, "transcribe", ears)
    return t, model, sent


async def test_bengali_is_heard_in_the_cloud(monkeypatch):
    t, model, sent = _transcriber(monkeypatch, {"bn": 0.5}, cloud="আমার ফোল্ডারটা খোলো")
    text, _, spoken = await t.transcribe(b"clip", "bn")
    assert (text, spoken, sent, model.calls) == ("আমার ফোল্ডারটা খোলো", "bn", ["bn"], [])


async def test_bengali_offline_falls_back_to_the_model_on_this_pc(monkeypatch):
    t, model, _ = _transcriber(
        monkeypatch, {"bn": 0.5}, cloud=RuntimeError("offline"), offline=True
    )
    text, _, spoken = await t.transcribe(b"clip", "bn")
    assert (text, spoken, model.calls) == ("local words", "bn", ["bn"])


async def test_bengali_with_no_way_to_listen_says_how_to_fix_it(monkeypatch):
    t, _, _ = _transcriber(monkeypatch, {"bn": 0.5}, cloud=None)
    with pytest.raises(RuntimeError, match="setup-voice --bengali"):
        await t.transcribe(b"clip", "bn")


async def test_automatic_keeps_english_on_this_pc(monkeypatch):
    t, model, sent = _transcriber(monkeypatch, {"en": 0.93, "bn": 0.0}, cloud="x")
    text, _, spoken = await t.transcribe(b"clip", "auto")
    assert (text, spoken, sent, model.calls) == ("local words", "en", [], ["en"])


async def test_automatic_sends_speech_it_cant_place_to_the_cloud(monkeypatch):
    # Real Bengali clips were guessed as Polish (0.74) or Greek (0.12) by the small model.
    t, model, sent = _transcriber(monkeypatch, {"pl": 0.74, "en": 0.04}, cloud="অশোক স্যার, বলেন না।")
    text, _, spoken = await t.transcribe(b"clip", "auto")
    assert text and (spoken, sent, model.calls) == ("bn", ["auto"], [])


async def test_automatic_still_hears_spanish_locally(monkeypatch):
    t, model, sent = _transcriber(monkeypatch, {"es": 0.9, "en": 0.05}, cloud="x")
    _, _, spoken = await t.transcribe(b"clip", "auto")
    assert (spoken, sent, model.calls) == ("es", [], ["es"])


def test_no_speech_replies_become_empty():
    assert stt_mod._tidy("(no speech)") == ""
    assert stt_mod._tidy('"হাই পিক্সি"') == "হাই পিক্সি"
