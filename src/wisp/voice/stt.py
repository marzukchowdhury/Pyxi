"""Push-to-talk transcription with faster-whisper, fully local.

The overlay records with MediaRecorder (webm/opus) and sends the clip; faster-whisper
decodes it with its bundled PyAV/FFmpeg, so no system FFmpeg install is needed. The model
loads lazily on first use and stays warm afterwards.

English uses the small, fast English-only model. Any other language, or "Automatic", uses a
multilingual model (downloaded once, on first use) that also reports which language you spoke,
so Wisp can answer in it.
"""

from __future__ import annotations

import asyncio
import io
import logging
import time

from wisp.config import VoiceConfig

log = logging.getLogger(__name__)


class Transcriber:
    def __init__(self, config: VoiceConfig) -> None:
        self.config = config
        self._models: dict[str, object] = {}
        self._lock = asyncio.Lock()

    def _load(self, name: str):
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

    def model_for(self, language: str) -> str:
        english_only = self.config.stt_model.endswith(".en")
        return (
            self.config.stt_model
            if language == "en" or not english_only
            else self.config.stt_multilingual_model
        )

    async def warm_up(self, language: str = "en"):
        name = self.model_for(language)
        async with self._lock:
            if name not in self._models:
                self._models[name] = await asyncio.to_thread(self._load, name)
        return self._models[name]

    async def transcribe(self, audio: bytes, language: str = "en") -> tuple[str, float, str]:
        """Return (text, seconds taken, language spoken). ``language`` may be "auto"."""
        model = await self.warm_up(language)
        started = time.monotonic()
        name = self.model_for(language)

        def run() -> tuple[str, str]:
            lang = "en" if name.endswith(".en") else (None if language == "auto" else language)
            segments, info = model.transcribe(
                io.BytesIO(audio), language=lang, vad_filter=True, beam_size=1
            )
            text = " ".join(s.text.strip() for s in segments).strip()
            return text, lang or getattr(info, "language", "en") or "en"

        text, spoken = await asyncio.to_thread(run)
        return text, time.monotonic() - started, spoken
