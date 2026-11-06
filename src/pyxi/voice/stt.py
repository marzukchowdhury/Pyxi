"""Push-to-talk transcription with faster-whisper, fully local.

The overlay records with MediaRecorder (webm/opus) and sends the clip; faster-whisper
decodes it with its bundled PyAV/FFmpeg, so no system FFmpeg install is needed. The model
loads lazily on first use and stays warm afterwards.

English uses the small, fast English-only model. Any other language, or "Automatic", uses a
multilingual model (downloaded once, on first use) that also reports which language you spoke,
so Pyxi can answer in it.
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
import time

from pyxi.config import VoiceConfig

log = logging.getLogger(__name__)


def decode_audio(data: bytes, rate: int = 16000):
    """webm/opus (or any container FFmpeg reads) -> 16 kHz mono float32 samples.

    Decoded here with PyAV's current API rather than inside faster-whisper, whose decoder
    breaks on newer PyAV releases (they removed an argument it passes).
    """
    import av
    import numpy as np

    chunks = []
    with av.open(io.BytesIO(data), mode="r") as container:
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


class Transcriber:
    def __init__(self, config: VoiceConfig) -> None:
        self.config = config
        self._models: dict[str, object] = {}
        self._lock = asyncio.Lock()

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

    async def self_test(self, language: str = "en") -> None:
        """Run half a second of silence through the model at startup, so a GPU that can't
        actually run (missing CUDA libraries) is found now, not on the user's first question."""
        import numpy as np

        model = await self.warm_up(language)

        def run() -> None:
            segments, _ = model.transcribe(
                np.zeros(8000, dtype=np.float32),
                language="en" if self.model_for(language).endswith(".en") else None,
                beam_size=1,
            )
            list(segments)

        try:
            await asyncio.to_thread(run)
        except RuntimeError as exc:
            if (
                not re.search(r"cublas|cudnn|cuda", str(exc), re.I)
                or self.config.stt_device == "cpu"
            ):
                raise
            log.warning("Whisper on the GPU failed (%s); switching to the CPU.", exc)
            self.config.stt_device = "cpu"
            self._models.clear()
            await self.warm_up(language)

    async def transcribe(self, audio: bytes, language: str = "en") -> tuple[str, float, str]:
        """Return (text, seconds taken, language spoken). ``language`` may be "auto"."""
        model = await self.warm_up(language)
        started = time.monotonic()
        name = self.model_for(language)

        def run() -> tuple[str, str]:
            nonlocal model
            lang = "en" if name.endswith(".en") else (None if language == "auto" else language)
            segments, info = model.transcribe(
                decode_audio(audio),
                language=lang,
                vad_filter=True,
                beam_size=1,
                hotwords="Pyxi",  # "Hi Pyxi" was heard as "Hi Wes"
            )
            text = " ".join(s.text.strip() for s in segments).strip()
            return text, lang or getattr(info, "language", "en") or "en"

        try:
            text, spoken = await asyncio.to_thread(run)
        except RuntimeError as exc:
            # A GPU without its CUDA libraries (cuBLAS/cuDNN) loads fine and fails on first use.
            if (
                not re.search(r"cublas|cudnn|cuda", str(exc), re.I)
                or self.config.stt_device == "cpu"
            ):
                raise
            log.warning("Whisper on the GPU failed (%s); switching to the CPU.", exc)
            self.config.stt_device = "cpu"
            self._models.clear()
            model = await self.warm_up(language)
            text, spoken = await asyncio.to_thread(run)
        return text, time.monotonic() - started, spoken
