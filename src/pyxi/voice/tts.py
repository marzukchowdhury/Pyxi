"""Text-to-speech, streamed sentence by sentence so Pyxi starts talking almost immediately.

Providers:

* **kokoro** (default): Kokoro-82M via ONNX Runtime. Tiny, runs faster than real time on
  CPU, natural-sounding fixed voices (af_heart, am_puck, bf_emma...). No GPU needed.
* **chatterbox**: Resemble AI's Chatterbox Turbo on the GPU. Zero-shot voice cloning from a
  ~10 s reference clip and paralinguistic tags such as ``[laugh]`` and ``[chuckle]``.
  Optional extra (``pip install -e ".[chatterbox]"``) because it pulls in PyTorch.
* **client**: no audio from the backend; the desktop app uses the OS voice.

Pyxi's persona may include tags like ``[laugh]``. Chatterbox performs them; every other
provider has them stripped so they are never read out literally.
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

from pyxi.config import VoiceConfig

log = logging.getLogger(__name__)

TAG = re.compile(
    r"\[(laugh|chuckle|sigh|gasp|cough|clear throat|sniff|groan|shush)\]", re.IGNORECASE
)
# Sentence ends: English (a capital follows), and Bengali, which ends sentences with the dari (।).
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'(\[{])|(?<=।)\s+|(?<=[!?])\s+(?=[ঀ-৿])")

KOKORO_MODEL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx"
KOKORO_VOICES = (
    "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin"
)


@dataclass
class Speech:
    text: str
    wav: bytes | None  # None means "client should speak this itself"
    duration_s: float = 0.0


class TTS(Protocol):
    name: str

    async def synthesize(self, text: str, voice: str | None = None) -> Speech: ...


TONE = re.compile(r"\{(neutral|playful|warm|excited|calm|dry|whisper)\}\s*", re.IGNORECASE)


def strip_tags(text: str) -> str:
    """Remove performance tags ([laugh], {playful}) so they are never read out literally."""
    return re.sub(r"\s{2,}", " ", TONE.sub("", TAG.sub("", text))).strip()


def split_sentences(text: str, max_chars: int = 220) -> list[str]:
    """Split into speakable chunks: sentences, with long ones broken at commas."""
    out: list[str] = []
    for sentence in _SENTENCE.split(text.strip()):
        sentence = sentence.strip()
        while len(sentence) > max_chars:
            cut = sentence.rfind(", ", 0, max_chars)
            cut = cut + 1 if cut > 40 else max_chars
            out.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip()
        if sentence:
            out.append(sentence)
    return out


_SPOKEN = [
    (re.compile(r"\be\.g\.", re.I), "for example"),
    (re.compile(r"\bi\.e\.", re.I), "that is"),
    (re.compile(r"\betc\.", re.I), "and so on"),
    (re.compile(r"\bvs\.?(?=\s)", re.I), "versus"),
    (re.compile(r"(\d)\s*%"), r"\1 percent"),
    (re.compile(r"\s*&\s*"), " and "),
    (re.compile(r"\s*->\s*"), ", then "),
    (re.compile(r"[*_`#]+"), ""),
]


def speakable(text: str) -> str:
    """Written shorthand reads badly aloud ('e.g.' -> 'for example', '->' -> ', then')."""
    for pattern, repl in _SPOKEN:
        text = pattern.sub(repl, text)
    return text


def _breathe(samples: np.ndarray, rate: int) -> np.ndarray:
    """Trim dead air at the ends, then leave a short natural pause after the sentence, so
    sentences join like speech instead of clipped clips."""
    level = np.abs(samples)
    loud = np.flatnonzero(level > 0.008)
    if loud.size:
        start = max(0, loud[0] - int(0.02 * rate))
        end = min(len(samples), loud[-1] + int(0.06 * rate))
        samples = samples[start:end]
    fade = min(len(samples), int(0.012 * rate))
    if fade:
        samples = samples.copy()
        samples[:fade] *= np.linspace(0, 1, fade)
        samples[-fade:] *= np.linspace(1, 0, fade)
    return np.concatenate([samples, np.zeros(int(0.16 * rate), dtype=samples.dtype)])


def _wav_bytes(samples: np.ndarray, sample_rate: int) -> bytes:
    import soundfile as sf

    buf = io.BytesIO()
    sf.write(buf, samples, sample_rate, format="WAV", subtype="PCM_16")
    return buf.getvalue()


class ClientTTS:
    name = "client"

    async def synthesize(self, text: str, voice: str | None = None) -> Speech:
        return Speech(text=strip_tags(text), wav=None)


class KokoroTTS:
    name = "kokoro"

    def __init__(self, config: VoiceConfig, models_dir: Path) -> None:
        self.config = config
        self.models_dir = models_dir
        self._engine = None
        self._lock = asyncio.Lock()

    @property
    def model_paths(self) -> tuple[Path, Path]:
        return self.models_dir / "kokoro-v1.0.onnx", self.models_dir / "voices-v1.0.bin"

    def installed(self) -> bool:
        return all(p.exists() for p in self.model_paths)

    async def _ensure(self):
        async with self._lock:
            if self._engine is None:
                if not self.installed():
                    raise RuntimeError(
                        "Kokoro model files are missing. Run `pyxi setup-voice` to download them (~350 MB)."
                    )
                from kokoro_onnx import Kokoro

                model, voices = self.model_paths
                self._engine = await asyncio.to_thread(Kokoro, str(model), str(voices))
        return self._engine

    async def synthesize(self, text: str, voice: str | None = None) -> Speech:
        voice = voice or self.config.voice
        lang = "en-gb" if voice.startswith("b") else "en-us"
        return await self.synthesize_with(text, voice=voice, speed=self.config.speed, lang=lang)

    async def synthesize_with(self, text: str, *, voice: str, speed: float, lang: str) -> Speech:
        engine = await self._ensure()
        clean = strip_tags(text)
        samples, rate = await asyncio.to_thread(
            engine.create, speakable(clean), voice=voice, speed=speed, lang=lang
        )
        samples = _breathe(samples, rate)
        return Speech(text=clean, wav=_wav_bytes(samples, rate), duration_s=len(samples) / rate)


class ChatterboxTTS:
    name = "chatterbox"

    def __init__(self, config: VoiceConfig) -> None:
        self.config = config
        self._model = None
        self._lock = asyncio.Lock()

    async def _ensure(self):
        async with self._lock:
            if self._model is None:
                import torch
                from chatterbox.tts_turbo import ChatterboxTurboTTS

                device = "cuda" if torch.cuda.is_available() else "cpu"
                self._model = await asyncio.to_thread(
                    ChatterboxTurboTTS.from_pretrained, device=device
                )
        return self._model

    async def synthesize(self, text: str, voice: str | None = None) -> Speech:
        model = await self._ensure()
        ref = self.config.chatterbox_reference
        kwargs = {"audio_prompt_path": str(ref)} if ref else {}
        wav = await asyncio.to_thread(
            model.generate, text, **kwargs
        )  # tags like [laugh] are performed
        samples = wav.squeeze().detach().cpu().numpy()
        return Speech(
            text=strip_tags(text),
            wav=_wav_bytes(samples, model.sr),
            duration_s=len(samples) / model.sr,
        )


class FallbackTTS:
    """Use the preferred provider, falling back to the client (OS) voice when it can't speak.

    A provider that can't even load (missing model, no GPU) is switched off for the session;
    a one-off failure on a particular line only affects that line.
    """

    def __init__(self, primary: TTS) -> None:
        self.primary = primary
        self.client = ClientTTS()
        self.failed: str | None = None
        self.name = primary.name

    async def synthesize(self, text: str, voice: str | None = None) -> Speech:
        if not strip_tags(text):
            return Speech(text="", wav=None)
        if self.failed is None:
            try:
                return await self.primary.synthesize(text, voice)
            except (RuntimeError, ImportError, OSError) as exc:
                self.failed = str(exc)
                log.warning("TTS %s unavailable, using the OS voice: %s", self.primary.name, exc)
            except Exception as exc:
                log.warning(
                    "TTS %s failed on one line (%s); using the OS voice for it.",
                    self.primary.name,
                    exc,
                )
        return await self.client.synthesize(text, voice)


def make_tts(config: VoiceConfig, models_dir: Path, prefs_path: Path | None = None) -> FallbackTTS:
    primary: TTS
    if config.tts == "kokoro" and prefs_path is not None:
        from pyxi.voice.studio import VoiceStudio

        primary = VoiceStudio(config, models_dir, prefs_path)
    elif config.tts == "kokoro":
        primary = KokoroTTS(config, models_dir)
    elif config.tts == "chatterbox":
        primary = ChatterboxTTS(config)
    else:
        primary = ClientTTS()
    return FallbackTTS(primary)


async def download_kokoro(models_dir: Path) -> list[Path]:
    """Fetch the Kokoro ONNX model and voice pack from the kokoro-onnx release."""
    import httpx

    models_dir.mkdir(parents=True, exist_ok=True)
    out = []
    async with httpx.AsyncClient(follow_redirects=True, timeout=None) as client:
        for url in (KOKORO_MODEL, KOKORO_VOICES):
            target = models_dir / url.rsplit("/", 1)[-1]
            if not target.exists():
                tmp = target.with_suffix(".part")
                async with client.stream("GET", url) as r:
                    r.raise_for_status()
                    with tmp.open("wb") as f:
                        async for chunk in r.aiter_bytes(1 << 20):
                            f.write(chunk)
                tmp.rename(target)
            out.append(target)
    return out
