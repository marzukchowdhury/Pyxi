"""Push-to-talk transcription with faster-whisper, on this PC (Bengali excepted, see below).

The overlay records with MediaRecorder (webm/opus) and sends the clip; faster-whisper
decodes it with its bundled PyAV/FFmpeg, so no system FFmpeg install is needed. The model
loads lazily on first use and stays warm afterwards.

English uses the small, fast English-only model. Any other language, or "Automatic", uses a
multilingual model (downloaded once, on first use) that also reports which language you spoke,
so Pyxi can answer in it.

Bengali is different: every stock Whisper model tested on real Bengali speech either looped or
misheard most words (small: 168% character errors, large-v3: 35%). So Bengali goes to a cloud
model that hears it well (Gemini 3.1 Flash-Lite via OpenRouter: 14%, about $0.001 a minute),
through the beta server in the beta. Offline, a Bengali fine-tuned Whisper on the PC takes over
(18%, 1.5 GB, `pyxi setup-voice --bengali`). In Automatic, the local model first decides whether
you spoke English: it is very sure about English (0.92 and up on English, 0.14 or less on
Bengali) even when it can't tell which other language it heard, so English stays on the PC and
free, and only speech it can't place goes to the cloud.
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import os
import re
import time
from pathlib import Path

from pyxi.config import VoiceConfig

log = logging.getLogger(__name__)


def decode_audio(data: bytes | str | Path, rate: int = 16000):
    """webm/opus (or any container FFmpeg reads), as bytes or a file's path -> 16 kHz mono
    float32 samples.

    Decoded here with PyAV's current API rather than inside faster-whisper, whose decoder
    breaks on newer PyAV releases (they removed an argument it passes).
    """
    import av
    import numpy as np

    chunks = []
    source = io.BytesIO(data) if isinstance(data, bytes | bytearray) else str(data)
    with av.open(source, mode="r") as container:
        stream = next(s for s in container.streams if s.type == "audio")
        resampler = av.AudioResampler(format="s16", layout="mono", rate=rate)
        for frame in container.decode(stream):
            for out in resampler.resample(frame):
                chunks.append(out.to_ndarray().reshape(-1))
        for out in resampler.resample(None):  # flush
            chunks.append(out.to_ndarray().reshape(-1))
    if not chunks:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(chunks).astype(np.float32) / 32768.0


_NVIDIA_READY = False


def _find_nvidia_libraries() -> None:
    """Let the GPU path find cuBLAS and cuDNN installed as pip wheels (nvidia-cublas-cu12,
    nvidia-cudnn-cu12): they live in site-packages/nvidia/*/bin, which Windows doesn't search.
    Without them Whisper runs on the CPU, about five times slower."""
    global _NVIDIA_READY
    if _NVIDIA_READY:
        return
    _NVIDIA_READY = True
    import os
    import sys
    from pathlib import Path

    if sys.platform != "win32":
        return
    try:
        import nvidia  # the namespace package the wheels install into
    except ImportError:
        return
    for root in getattr(nvidia, "__path__", []):
        for bin_dir in Path(root).glob("*/bin"):
            if any(bin_dir.glob("*.dll")):
                os.add_dll_directory(str(bin_dir))
                os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")


# Languages the local multilingual model hears well (the ones offered in the voice settings).
LOCAL_LANGUAGES = {"en", "es", "fr", "hi", "it", "pt", "ja", "zh"}
OPENROUTER_URL = "https://openrouter.ai/api/v1"
MAX_CLOUD_SECONDS = 80  # a push-to-talk question is far shorter; keeps every request small


def _bengali_script(text: str) -> bool:
    from pyxi.voice.bengali import is_bengali

    return is_bengali(text)


def _cached(repo: str) -> bool:
    """Whether a Hugging Face model is already on this PC (never downloads)."""
    if Path(repo).exists():
        return True
    try:
        from huggingface_hub import try_to_load_from_cache

        return isinstance(try_to_load_from_cache(repo, "model.bin"), str)
    except Exception:
        return False


def _wav(samples, rate: int = 16000) -> bytes:
    import soundfile as sf

    buf = io.BytesIO()
    sf.write(buf, samples, rate, format="WAV", subtype="PCM_16")
    return buf.getvalue()


_NO_SPEECH = re.compile(r"^[\[(]?\s*(no speech|silence|inaudible|no audio)[^)\]]*[)\]]?\.?$", re.I)


def _tidy(text: str) -> str:
    text = text.strip().strip('"“”').strip()
    return "" if _NO_SPEECH.match(text) else text


class CloudEars:
    """Speech to text with a model that listens (Gemini on OpenRouter). In the beta, the beta
    server stands in for OpenRouter, so testers need no key and it counts against their
    allowance like any other model call."""

    def __init__(self, model: str) -> None:
        self.model = model

    @staticmethod
    def available() -> bool:
        return bool(os.environ.get("OPENROUTER_API_KEY"))

    @staticmethod
    def prompt(language: str) -> str:
        who = (
            "Someone is talking to Pyxi (written পিক্সি in Bengali), an assistant on their computer"
        )
        if language == "bn":
            return (
                f"{who}, in Bengali (Bangla), maybe with some English words. Transcribe exactly "
                "what they say: Bengali in Bengali script, English words in English. Output only "
                "the transcript. If there is no speech, output nothing."
            )
        return (
            f"{who}. Transcribe exactly what they say, in the language and script they speak "
            "(Bengali in Bengali script). Output only the transcript. If there is no speech, "
            "output nothing."
        )

    async def transcribe(self, samples, language: str) -> str:
        import httpx

        samples = samples[: MAX_CLOUD_SECONDS * 16000]
        audio = base64.b64encode(await asyncio.to_thread(_wav, samples)).decode()
        body = {
            "model": self.model,
            "temperature": 0,
            "max_tokens": 600,
            "reasoning": {"enabled": False},
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": self.prompt(language)},
                        {"type": "input_audio", "input_audio": {"data": audio, "format": "wav"}},
                    ],
                }
            ],
        }
        base = os.environ.get("PYXI_OPENROUTER_URL", OPENROUTER_URL).rstrip("/")
        async with httpx.AsyncClient(timeout=httpx.Timeout(25, connect=8)) as client:
            r = await client.post(
                base + "/chat/completions",
                json=body,
                headers={"Authorization": f"Bearer {os.environ.get('OPENROUTER_API_KEY', '')}"},
            )
        if r.status_code >= 400:
            try:
                detail = r.json().get("error", {}).get("message", "")
            except (ValueError, AttributeError):
                detail = r.text
            raise RuntimeError(f"HTTP {r.status_code}: {str(detail)[:160]}")
        data = r.json()
        if cost := (data.get("usage") or {}).get("cost"):
            log.info("cloud transcription: %.1fs of audio, $%.5f", len(samples) / 16000, cost)
        text = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        return _tidy(text)


class Transcriber:
    def __init__(self, config: VoiceConfig) -> None:
        self.config = config
        self._models: dict[str, object] = {}
        self._lock = asyncio.Lock()
        self.ears = CloudEars(config.stt_cloud_model)

    def _load(self, name: str):
        _find_nvidia_libraries()
        from faster_whisper import WhisperModel

        device = self.config.stt_device
        if device == "auto":
            try:
                import ctranslate2

                device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
            except Exception:
                device = "cpu"
        try:
            return WhisperModel(
                name,
                device=device,
                compute_type="float16" if device == "cuda" else "int8",
            )
        except Exception as exc:  # CUDA libs missing etc: CPU always works
            log.warning("Whisper on %s failed (%s); using CPU.", device, exc)
            return WhisperModel(name, device="cpu", compute_type="int8")

    def bengali_offline(self) -> bool:
        return _cached(self.config.stt_bengali_model)

    def model_for(self, language: str) -> str:
        if language == "bn" and self.bengali_offline():
            return self.config.stt_bengali_model
        english_only = self.config.stt_model.endswith(".en")
        return (
            self.config.stt_model
            if language == "en" or not english_only
            else self.config.stt_multilingual_model
        )

    async def _get(self, name: str):
        async with self._lock:
            if name not in self._models:
                self._models[name] = await asyncio.to_thread(self._load, name)
        return self._models[name]

    async def warm_up(self, language: str = "en"):
        return await self._get(self.model_for(language))

    def _gpu_failed(self, exc: RuntimeError) -> bool:
        """A GPU without its CUDA libraries (cuBLAS/cuDNN) loads fine and fails on first use:
        switch to the CPU for good. Returns whether that was the problem."""
        if not re.search(r"cublas|cudnn|cuda", str(exc), re.I) or self.config.stt_device == "cpu":
            return False
        log.warning("Whisper on the GPU failed (%s); switching to the CPU.", exc)
        self.config.stt_device = "cpu"
        self._models.clear()
        return True

    async def self_test(self, language: str = "en") -> None:
        """Run half a second of silence through the model at startup, so a GPU that can't
        actually run (missing CUDA libraries) is found now, not on the user's first question."""
        import numpy as np

        if language == "bn" and (self.ears.available() or not self.bengali_offline()):
            return  # Bengali is heard in the cloud: nothing to load on this PC
        name = self.model_for(language)
        model = await self._get(name)

        def run() -> None:
            segments, _ = model.transcribe(
                np.zeros(8000, dtype=np.float32),
                language="en" if name.endswith(".en") else None,
                beam_size=1,
            )
            list(segments)

        try:
            await asyncio.to_thread(run)
        except RuntimeError as exc:
            if not self._gpu_failed(exc):
                raise
            await self._get(name)

    async def _local(self, name: str, samples, language: str | None) -> tuple[str, str]:
        """Transcribe on this PC. Returns (text, language spoken)."""
        for attempt in (1, 2):
            model = await self._get(name)

            def run(model=model) -> tuple[str, str]:
                segments, info = model.transcribe(
                    samples,
                    language=language,
                    vad_filter=True,
                    beam_size=1,
                    hotwords="Pyxi",  # "Hi Pyxi" was heard as "Hi Wes"
                )
                text = " ".join(s.text.strip() for s in segments).strip()
                return text, language or getattr(info, "language", "en") or "en"

            try:
                return await asyncio.to_thread(run)
            except RuntimeError as exc:
                if attempt == 2 or not self._gpu_failed(exc):
                    raise
        raise AssertionError("unreachable")

    async def _bengali(self, samples) -> str:
        """Bengali: the cloud if it can be reached, else the Bengali model on this PC."""
        problem = ""
        if self.ears.available():
            try:
                return await self.ears.transcribe(samples, "bn")
            except Exception as exc:  # offline, allowance used up, server down
                problem = str(exc) or exc.__class__.__name__
                log.warning("cloud transcription failed (%s); trying this PC", problem)
        if self.bengali_offline():
            text, _ = await self._local(self.config.stt_bengali_model, samples, "bn")
            return text
        raise RuntimeError(
            "Bengali listening needs the internet (the Pyxi beta or an OpenRouter key), or "
            "`pyxi setup-voice --bengali` for offline Bengali."
            + (f" ({problem})" if problem else "")
        )

    async def _automatic(self, samples) -> tuple[str, str]:
        """English stays on the PC; speech the local model can't place goes to the cloud."""
        name = self.model_for("auto")
        if name.endswith(".en"):
            return await self._local(name, samples, "en")
        model = await self._get(name)
        try:
            guess, prob, probs = await asyncio.to_thread(model.detect_language, samples)
        except Exception as exc:  # very short or silent audio
            log.debug("language detection failed: %s", exc)
            return await self._local(name, samples, None)
        if dict(probs).get("en", 0.0) >= 0.5:
            return await self._local(name, samples, "en")
        if guess in LOCAL_LANGUAGES and prob >= 0.6:
            return await self._local(name, samples, guess)
        if self.ears.available():
            try:
                text = await self.ears.transcribe(samples, "auto")
                return text, "bn" if _bengali_script(text) else guess
            except Exception as exc:
                log.warning("cloud transcription failed (%s); using this PC", exc)
        if guess == "bn" and self.bengali_offline():
            text, _ = await self._local(self.config.stt_bengali_model, samples, "bn")
            return text, "bn"
        return await self._local(name, samples, None)

    async def transcribe(self, audio: bytes, language: str = "en") -> tuple[str, float, str]:
        """Return (text, seconds taken, language spoken). ``language`` may be "auto"."""
        started = time.monotonic()
        if language in ("bn", "auto"):
            samples = await asyncio.to_thread(decode_audio, audio)
            if language == "bn":
                text, spoken = await self._bengali(samples), "bn"
            else:
                text, spoken = await self._automatic(samples)
            return text, time.monotonic() - started, spoken
        name = self.model_for(language)
        await self._get(name)
        samples = await asyncio.to_thread(decode_audio, audio)
        lang = "en" if name.endswith(".en") else language
        text, spoken = await self._local(name, samples, lang)
        return text, time.monotonic() - started, spoken
