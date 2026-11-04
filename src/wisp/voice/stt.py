"""Push-to-talk transcription with faster-whisper, fully local.

The overlay records with MediaRecorder (webm/opus) and sends the clip; faster-whisper
decodes it with its bundled PyAV/FFmpeg, so no system FFmpeg install is needed. The model
loads lazily on first use and stays warm afterwards.
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
        self._model = None
        self._lock = asyncio.Lock()

    def _load(self):
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
                self.config.stt_model,
                device=device,
                compute_type="float16" if device == "cuda" else "int8",
            )
        except Exception as exc:  # CUDA libs missing etc: CPU always works
            log.warning("Whisper on %s failed (%s); using CPU.", device, exc)
            return WhisperModel(self.config.stt_model, device="cpu", compute_type="int8")

    async def warm_up(self) -> None:
        async with self._lock:
            if self._model is None:
                self._model = await asyncio.to_thread(self._load)

    async def transcribe(self, audio: bytes) -> tuple[str, float]:
        """Return (text, seconds taken)."""
        await self.warm_up()
        started = time.monotonic()

        def run() -> str:
            segments, _ = self._model.transcribe(
                io.BytesIO(audio), language="en", vad_filter=True, beam_size=1
            )
            return " ".join(s.text.strip() for s in segments).strip()

        text = await asyncio.to_thread(run)
        return text, time.monotonic() - started
