"""The voice studio: which engine and voice Pyxi speaks with, switchable live from the panel.

Engines:

* **kokoro** (default, free): Kokoro-82M on this PC. No account, works offline.
* **elevenlabs**: the most human-sounding option, with your own ElevenLabs key
  (``ELEVENLABS_API_KEY``). Uses ``eleven_v4_turbo``, ElevenLabs' expressive real-time model.
* **gemini**: Google's Gemini TTS voices (``GEMINI_API_KEY``), with a delivery style ("warm,
  quick, lightly amused") and inline sounds like ``<laugh>``.

Pyxi's own lines use the chosen voice; teammates keep their own Kokoro voices so you can always
tell who's talking. If a cloud voice fails (no credit left, offline), that line falls back to
Kokoro and the panel says why, instead of Pyxi going silent. Preferences persist in
``.pyxi/voice.json``.

Bengali: Kokoro can't speak it, so any line written in Bengali script is read by Mitali, the
on-device Bengali voice (voice/bengali.py), whoever says it and whatever engine is chosen. A
cloud engine that can speak Bengali (ElevenLabs v4, Gemini) still speaks Pyxi's own lines when
chosen, and falls back to Mitali, never to an English voice reading Bengali.
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import wave
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from pyxi.config import VoiceConfig
from pyxi.voice.bengali import VOICE_ID as BENGALI_VOICE
from pyxi.voice.bengali import BengaliVoice, is_bengali
from pyxi.voice.tts import TAG, KokoroTTS, Speech, strip_tags

log = logging.getLogger(__name__)

Engine = Literal["kokoro", "elevenlabs", "gemini"]
Tone = Literal["neutral", "playful", "warm", "excited", "calm", "dry", "whisper"]
TONE_TAG = re.compile(r"^\s*\{(neutral|playful|warm|excited|calm|dry|whisper)\}\s*", re.IGNORECASE)

ELEVEN_URL = "https://api.elevenlabs.io/v1"
ELEVEN_MODEL = "eleven_v4_turbo"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
GEMINI_MODEL = "gemini-3.8-flash-tts"

# Spoken delivery for each tone. Gemini takes these as a style; the others use them for pacing.
STYLE: dict[str, str] = {
    "neutral": "natural and conversational, like a clever friend sitting next to you",
    "playful": "playful and lightly amused, with a smile in the voice",
    "warm": "warm, kind and unhurried",
    "excited": "genuinely excited, quick and bright",
    "calm": "calm, steady and reassuring",
    "dry": "deadpan and dry, understated comic timing",
    "whisper": "a soft conspiratorial half-whisper",
}
SPEED_BY_TONE = {"excited": 1.06, "calm": 0.94, "whisper": 0.92, "dry": 0.97}


class VoiceInfo(BaseModel):
    id: str
    name: str
    vibe: str
    engine: Engine
    accent: str = ""
    lang: str = "en-us"
    gender: str = ""
    sample: str = ""


# Kokoro's strongest voices (by the model card's own quality grades), each with a personality.
KOKORO_CATALOG: list[VoiceInfo] = [
    VoiceInfo(
        id="af_heart",
        name="Heart",
        vibe="Warm",
        engine="kokoro",
        accent="American",
        gender="f",
        sample="Hi, I'm Pyxi. I live on your screen, I don't pay rent, and I'm weirdly good at spreadsheets.",
    ),
    VoiceInfo(
        id="af_bella",
        name="Bella",
        vibe="Bright",
        engine="kokoro",
        accent="American",
        gender="f",
        sample="Okay, this one's fun. Give me two seconds and I'll draw it for you.",
    ),
    VoiceInfo(
        id="af_nicole",
        name="Nicole",
        vibe="Hushed",
        engine="kokoro",
        accent="American",
        gender="f",
        sample="Lean in. I'm about to explain this so gently your coffee won't notice.",
    ),
    VoiceInfo(
        id="af_aoede",
        name="Aoede",
        vibe="Breezy",
        engine="kokoro",
        accent="American",
        gender="f",
        sample="No stress. We'll take it one click at a time.",
    ),
    VoiceInfo(
        id="af_kore",
        name="Kore",
        vibe="Steady",
        engine="kokoro",
        accent="American",
        gender="f",
        sample="Here's the plan. Three steps, no surprises.",
    ),
    VoiceInfo(
        id="am_michael",
        name="Michael",
        vibe="Easygoing",
        engine="kokoro",
        accent="American",
        gender="m",
        sample="Alright, let's figure out what this button actually does. Spoiler, it's not much.",
    ),
    VoiceInfo(
        id="am_puck",
        name="Puck",
        vibe="Mischievous",
        engine="kokoro",
        accent="American",
        gender="m",
        sample="I could just tell you. But a tiny animated diagram is so much funnier.",
    ),
    VoiceInfo(
        id="am_fenrir",
        name="Fenrir",
        vibe="Bold",
        engine="kokoro",
        accent="American",
        gender="m",
        sample="Big idea, small words. Let's go.",
    ),
    VoiceInfo(
        id="bf_emma",
        name="Emma",
        vibe="Polished",
        engine="kokoro",
        accent="British",
        gender="f",
        sample="Right then. Let's make this look effortless, shall we?",
    ),
    VoiceInfo(
        id="bf_isabella",
        name="Isabella",
        vibe="Crisp",
        engine="kokoro",
        accent="British",
        gender="f",
        sample="Two things matter here, and one of them is hiding behind that menu.",
    ),
    VoiceInfo(
        id="bm_george",
        name="George",
        vibe="Gentle",
        engine="kokoro",
        accent="British",
        gender="m",
        sample="Take your time. I'll point at the bit that matters.",
    ),
    VoiceInfo(
        id="bm_fable",
        name="Fable",
        vibe="Storyteller",
        engine="kokoro",
        accent="British",
        gender="m",
        sample="Once upon a time, there was a settings page. It had far too many toggles.",
    ),
    VoiceInfo(
        id=BENGALI_VOICE,
        name="Mitali",
        vibe="Friendly",
        engine="kokoro",
        accent="Bangla",
        lang="bn",
        gender="f",
        sample="হাই, আমি পিক্সি। তোমার স্ক্রিনেই থাকি, আর এক ক্লিক করে করে সব দেখিয়ে দিই।",
    ),
]

# Google's prebuilt voices with the personalities Google gives them.
GEMINI_CATALOG: list[VoiceInfo] = [
    VoiceInfo(id=n, name=n, vibe=v, engine="gemini")
    for n, v in [
        ("Puck", "Upbeat"),
        ("Zephyr", "Bright"),
        ("Aoede", "Breezy"),
        ("Fenrir", "Excitable"),
        ("Leda", "Youthful"),
        ("Kore", "Firm"),
        ("Charon", "Informative"),
        ("Sulafat", "Warm"),
        ("Achird", "Friendly"),
        ("Sadachbia", "Lively"),
        ("Callirrhoe", "Easy-going"),
        ("Umbriel", "Easy-going"),
        ("Algieba", "Smooth"),
        ("Despina", "Smooth"),
        ("Enceladus", "Breathy"),
        ("Achernar", "Soft"),
        ("Vindemiatrix", "Gentle"),
        ("Zubenelgenubi", "Casual"),
        ("Gacrux", "Mature"),
        ("Algenib", "Gravelly"),
        ("Iapetus", "Clear"),
        ("Orus", "Firm"),
        ("Sadaltager", "Knowledgeable"),
        ("Laomedeia", "Upbeat"),
    ]
]

LANGUAGES: dict[str, str] = {
    "auto": "Automatic",
    "en": "English",
    "es": "Spanish",
    "fr": "French",
    "hi": "Hindi",
    "it": "Italian",
    "pt": "Portuguese",
    "ja": "Japanese",
    "zh": "Chinese",
    "bn": "Bengali",
}
# Kokoro speaks these. Bengali has its own on-device voice (Mitali); anything else needs a cloud voice.
KOKORO_LANG = {
    "en": "en-us",
    "es": "es",
    "fr": "fr-fr",
    "hi": "hi",
    "it": "it",
    "pt": "pt-br",
    "ja": "ja",
    "zh": "cmn",
}
# Kokoro voice ids start with their language: a/b English, e Spanish, f French, h Hindi...
KOKORO_PREFIX = {
    "en": "ab",
    "es": "e",
    "fr": "f",
    "hi": "h",
    "it": "i",
    "pt": "p",
    "ja": "j",
    "zh": "z",
}
KOKORO_VOICE_FOR = {
    "es": "ef_dora",
    "fr": "ff_siwis",
    "hi": "hf_alpha",
    "it": "if_sara",
    "pt": "pf_dora",
    "ja": "jf_alpha",
    "zh": "zf_xiaobei",
}


class VoicePrefs(BaseModel):
    engine: Engine = "kokoro"
    voice: str = "af_heart"
    speed: float = Field(1.0, ge=0.5, le=1.6)
    language: str = "en"  # Automatic is opt-in: it downloads a bigger multilingual model
    captions: bool = True


def _key(*names: str) -> str | None:
    for n in names:
        if v := os.environ.get(n):
            return v
    return None


def _wav_duration(data: bytes) -> float:
    try:
        with wave.open(io.BytesIO(data)) as w:
            return w.getnframes() / float(w.getframerate())
    except (wave.Error, EOFError):
        return 0.0


def split_tone(text: str) -> tuple[Tone, str]:
    """'{playful} Ha, nice.' -> ('playful', 'Ha, nice.')"""
    m = TONE_TAG.match(text)
    if not m:
        return "neutral", text
    return m.group(1).lower(), text[m.end() :]  # type: ignore[return-value]


class VoiceStudio:
    """A TTS that speaks Pyxi's lines with the chosen engine and voice."""

    def __init__(self, config: VoiceConfig, models_dir: Path, prefs_path: Path) -> None:
        self.config = config
        self.kokoro = KokoroTTS(config, models_dir)
        self.prefs_path = prefs_path
        self.prefs = self._load()
        self.bengali = BengaliVoice(models_dir)
        self.last_error: str | None = None
        self._detected = "en"
        self._prep: Any = None
        self._eleven_voices: list[VoiceInfo] | None = None
        self.name = "studio"

    # ------------------------------------------------------------------ language
    @property
    def detected_language(self) -> str:
        return self._detected

    @detected_language.setter
    def detected_language(self, code: str) -> None:
        self._detected = code or "en"
        if self._detected == "bn":
            self.prepare_bengali()  # first Bengali heard in Automatic: fetch the voice now

    def wants_bengali(self) -> bool:
        lang = self.prefs.language
        return lang == "bn" or (lang == "auto" and self._detected == "bn")

    def prepare_bengali(self) -> None:
        """Download and load the Bengali voice in the background, so the first answer is quick."""
        import asyncio

        if self.bengali._session is not None or (self._prep and not self._prep.done()):
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # no event loop (a script or test): the first Bengali line loads it instead

        async def run() -> None:
            try:
                await self.bengali.ensure()
            except RuntimeError as exc:
                log.warning("Bengali voice not ready: %s", exc)

        self._prep = loop.create_task(run())

    def warm(self) -> None:
        """A question is coming: load the voice that will answer it, in the background."""
        if self.wants_bengali():
            self.prepare_bengali()
        elif self.prefs.engine == "kokoro" or not self.available().get(self.prefs.engine):
            self.kokoro.warm()

    def release_idle(self, idle_s: float) -> list[str]:
        """Let go of voices that have been quiet for ``idle_s`` seconds."""
        gone = []
        if self.kokoro.release_idle(idle_s):
            gone.append("kokoro")
        if self.bengali.release_idle(idle_s):
            gone.append("bengali")
        return gone

    async def _bengali(self, text: str, speed: float) -> Speech:
        """Mitali reads it. If she can't (no download yet, offline), the words still show as a
        caption and the panel says why: never an English voice mangling Bengali."""
        try:
            return await self.bengali.synthesize(text, speed)
        except RuntimeError as exc:
            msg = f"{exc}. Pyxi will show its Bengali answers as text until the voice is ready."
            if msg != self.last_error:
                log.warning(msg)
            self.last_error = msg
            return Speech(text=strip_tags(text), wav=None)

    # ------------------------------------------------------------------ prefs
    def _load(self) -> VoicePrefs:
        try:
            return VoicePrefs.model_validate_json(self.prefs_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return VoicePrefs(voice=self.config.voice)

    def update(self, **changes: Any) -> VoicePrefs:
        data = {**self.prefs.model_dump(), **{k: v for k, v in changes.items() if v is not None}}
        if "engine" in changes and "voice" not in changes:
            data["voice"] = self.default_voice(data["engine"])
        self.prefs = VoicePrefs.model_validate(data)
        self.last_error = None
        if self.prefs.language == "bn":
            self.prepare_bengali()
        self.prefs_path.parent.mkdir(parents=True, exist_ok=True)
        self.prefs_path.write_text(self.prefs.model_dump_json(indent=2), encoding="utf-8")
        return self.prefs

    @staticmethod
    def default_voice(engine: str) -> str:
        return {"kokoro": "af_heart", "gemini": "Puck"}.get(engine, "")

    def available(self) -> dict[str, bool]:
        return {
            "kokoro": self.kokoro.installed(),
            "elevenlabs": bool(_key("ELEVENLABS_API_KEY", "XI_API_KEY")),
            "gemini": bool(_key("GEMINI_API_KEY", "GOOGLE_API_KEY")),
            "bengali": self.bengali.installed(),
        }

    @property
    def engine_name(self) -> str:
        return self.prefs.engine

    async def catalog(self) -> dict[str, list[dict[str, Any]]]:
        out: dict[str, list[dict[str, Any]]] = {
            "kokoro": [v.model_dump() for v in KOKORO_CATALOG],
            "gemini": [v.model_dump() for v in GEMINI_CATALOG],
            "elevenlabs": [],
        }
        if self.available()["elevenlabs"]:
            out["elevenlabs"] = [v.model_dump() for v in await self._eleven_catalog()]
        return out

    async def _eleven_catalog(self) -> list[VoiceInfo]:
        """The voices in the user's own ElevenLabs library (premade ones included)."""
        if self._eleven_voices is not None:
            return self._eleven_voices
        import httpx

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.get(
                    f"{ELEVEN_URL}/voices",
                    headers={"xi-api-key": _key("ELEVENLABS_API_KEY", "XI_API_KEY") or ""},
                )
                r.raise_for_status()
            voices = []
            for v in r.json().get("voices", [])[:40]:
                labels = v.get("labels") or {}
                vibe = (
                    labels.get("description")
                    or labels.get("descriptive")
                    or labels.get("use_case")
                    or "Natural"
                ).replace("_", " ")
                voices.append(
                    VoiceInfo(
                        id=v["voice_id"],
                        name=v.get("name", "Voice").split(" - ")[0],
                        vibe=vibe.capitalize(),
                        engine="elevenlabs",
                        accent=(labels.get("accent") or "").capitalize(),
                        gender=(labels.get("gender") or "")[:1],
                    )
                )
            self._eleven_voices = voices
        except Exception as exc:  # a bad key shouldn't break the settings page
            self.last_error = f"Couldn't load your ElevenLabs voices: {exc}"
            self._eleven_voices = []
        return self._eleven_voices

    # ------------------------------------------------------------------ speaking
    async def synthesize(self, text: str, voice: str | None = None) -> Speech:
        tone, text = split_tone(text)
        if voice:  # a teammate with its own Kokoro voice (which can't read Bengali)
            if is_bengali(text):
                return await self._bengali(text, self.prefs.speed)
            return await self.kokoro.synthesize(text, voice)
        return await self.speak_as(self.prefs.engine, self.prefs.voice, text, tone=tone)

    async def speak_as(
        self,
        engine: str,
        voice: str,
        text: str,
        *,
        tone: Tone = "neutral",
        speed: float | None = None,
    ) -> Speech:
        speed = (speed or self.prefs.speed) * SPEED_BY_TONE.get(tone, 1.0)
        lang = self.prefs.language if self.prefs.language != "auto" else self.detected_language
        bengali = is_bengali(text)
        if bengali and (engine == "kokoro" or not self.available().get(engine)):
            return await self._bengali(text, speed)
        try:
            if engine == "elevenlabs":
                return await self._eleven(voice, text, speed)
            if engine == "gemini":
                return await self._gemini(voice, text, tone, speed)
        except Exception as exc:
            msg = f"{engine} voice failed ({_short(exc)}); using the on-device voice for now."
            if msg != self.last_error:
                log.warning(msg)
            self.last_error = msg
            voice = self.kokoro.config.voice
            if bengali:
                return await self._bengali(text, speed)
        return await self._kokoro(voice if engine == "kokoro" else "af_heart", text, speed, lang)

    async def _kokoro(self, voice: str, text: str, speed: float, lang: str) -> Speech:
        if voice == BENGALI_VOICE:  # Mitali chosen, but this line isn't Bengali
            voice, lang = "af_heart", "en" if lang == "bn" else lang
        code = KOKORO_LANG.get(lang, "en-us")
        if voice[:1] not in KOKORO_PREFIX.get(lang, "ab"):
            voice = KOKORO_VOICE_FOR.get(
                lang, voice
            )  # an English voice can't do a Spanish accent justice
        if voice.startswith("b"):
            code = "en-gb" if code == "en-us" else code
        return await self.kokoro.synthesize_with(text, voice=voice, speed=speed, lang=code)

    async def _eleven(self, voice: str, text: str, speed: float) -> Speech:
        import httpx

        key = _key("ELEVENLABS_API_KEY", "XI_API_KEY")
        if not key:
            raise RuntimeError("no ELEVENLABS_API_KEY")
        clean = strip_tags(text)
        body = {
            "text": clean,
            "model_id": ELEVEN_MODEL,
            "voice_settings": {
                "stability": 0.42,
                "similarity_boost": 0.8,
                "style": 0.3,
                "use_speaker_boost": True,
                "speed": max(0.7, min(1.2, speed)),
            },
        }
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                f"{ELEVEN_URL}/text-to-speech/{voice}",
                params={"output_format": "mp3_44100_128"},
                headers={"xi-api-key": key},
                json=body,
            )
            if r.status_code >= 400:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:160]}")
        audio = r.content
        return Speech(text=clean, wav=audio, duration_s=len(audio) / 16000)  # 128 kbps mp3

    async def _gemini(self, voice: str, text: str, tone: Tone, speed: float) -> Speech:
        import httpx

        key = _key("GEMINI_API_KEY", "GOOGLE_API_KEY")
        if not key:
            raise RuntimeError("no GEMINI_API_KEY")
        clean = strip_tags(text)
        spoken = TAG.sub(
            lambda m: f"<{m.group(1).lower()}>", text
        ).strip()  # Gemini performs <laugh>, <sigh>
        style = STYLE[tone] + (
            ", a little faster than usual"
            if speed > 1.08
            else ", a little slower than usual"
            if speed < 0.92
            else ""
        )
        body = {
            "model": GEMINI_MODEL,
            "input": [
                {
                    "type": "user_input",
                    "content": [
                        {
                            "type": "text",
                            "text": spoken,
                            "annotations": [{"type": "speech_metadata", "style": style}],
                        }
                    ],
                }
            ],
            "response_format": {"type": "audio"},
            "generation_config": {"speech_config": [{"voice": voice or "Puck"}]},
        }
        async with httpx.AsyncClient(timeout=40) as client:
            r = await client.post(GEMINI_URL, headers={"x-goog-api-key": key}, json=body)
            if r.status_code >= 400:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:160]}")
        import base64

        audio = None
        for step in r.json().get("steps", []):
            if step.get("type") == "model_output":
                for c in step.get("content", []):
                    if c.get("type") == "audio" and c.get("data"):
                        audio = base64.b64decode(c["data"])
        if not audio:
            raise RuntimeError("no audio in the response")
        return Speech(text=clean, wav=audio, duration_s=_wav_duration(audio))

    async def preview(self, engine: str, voice: str, speed: float | None = None) -> Speech:
        sample = next(
            (v.sample for v in [*KOKORO_CATALOG, *GEMINI_CATALOG] if v.id == voice and v.sample), ""
        )
        line = sample or "Hey, it's me. Same brain, new voice. How do I sound?"
        return await self.speak_as(engine, voice, line, tone="playful", speed=speed)

    def public(self) -> dict[str, Any]:
        return {
            **self.prefs.model_dump(),
            "available": self.available(),
            "languages": LANGUAGES,
            "error": self.last_error,
        }


def _short(exc: Exception) -> str:
    s = str(exc) or type(exc).__name__
    try:
        detail = json.loads(s.split(": ", 1)[1]).get("detail")
        if isinstance(detail, dict):
            s = detail.get("message", s)
    except (IndexError, ValueError, AttributeError):
        pass
    return s[:140]
