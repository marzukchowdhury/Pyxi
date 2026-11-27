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
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

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
    (re.compile(r"(\d)\s*°\s*[CF]\b"), r"\1 degrees"),  # "12°C" (the city says which scale)
    (re.compile(r"(\d)\s*°"), r"\1 degrees"),
    (re.compile(r"\b(\d+)\s*[x×]\s*(\d+)\b"), r"\1 by \2"),  # "5x5 table"
    (re.compile(r"(\d)\s*km\b"), r"\1 kilometres"),
    (re.compile(r"(\d)\s*kg\b"), r"\1 kilos"),
    (re.compile(r"(\d)\s*mins?\b"), r"\1 minutes"),
    (re.compile(r"(\d)\s*hrs?\b"), r"\1 hours"),
    (re.compile(r"\b([A-Z]{1,2}\d{1,5}):([A-Z]{1,2}\d{1,5})\b"), r"\1 to \2"),  # "A1:C13"
]


# A web address that ends in a country's two letters: "amazon.ca" was read as "amazon ka".
# The letters are said one by one, and each dot as "dot": "amazon dot C A".
_COUNTRY_SITE = re.compile(
    r"(?<![\w@.-])((?:[A-Za-z0-9-]+\.)+)"
    r"(ca|uk|us|au|nz|ie|in|bd|pk|de|fr|es|it|nl|se|no|ch|jp|cn|sg|ae|za|br|mx|io|ai|eu)\b(?![\w-])",
    re.I,
)

def _site_aloud(m: re.Match[str]) -> str:
    names = [part for part in m.group(1).split(".") if part]
    return " dot ".join([*names, " ".join(m.group(2).upper())])


def speakable(text: str) -> str:
    """Written shorthand reads badly aloud ('e.g.' -> 'for example', '->' -> ', then')."""
    for pattern, repl in _SPOKEN:
        text = pattern.sub(repl, text)
    return _COUNTRY_SITE.sub(_site_aloud, text)


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
        self._used = 0.0  # monotonic time of the last line spoken
        self._busy = 0
        self._warming: asyncio.Task | None = None

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
                        "Pyxi's own voice isn't on this PC yet. Download it in the Voice tab (about 350 MB)."
                    )
                from kokoro_onnx import Kokoro

                model, voices = self.model_paths
                self._engine = await asyncio.to_thread(Kokoro, str(model), str(voices))
            self._used = time.monotonic()
        return self._engine

    def warm(self) -> None:
        """Load the voice in the background (a question is coming). Never blocks."""
        self._used = time.monotonic()
        if self._engine is not None or not self.installed():
            return
        if self._warming and not self._warming.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        async def run() -> None:
            try:
                await self._ensure()
            except Exception as exc:  # the first real line reports it
                log.debug("voice warm-up failed: %s", exc)

        self._warming = loop.create_task(run())

    def release_idle(self, idle_s: float) -> bool:
        """Let the voice model go (about 400 MB) after ``idle_s`` quiet seconds."""
        if self._engine is None or self._busy or time.monotonic() - self._used < idle_s:
            return False
        self._engine = None
        return True

    async def synthesize(self, text: str, voice: str | None = None) -> Speech:
        voice = voice or self.config.voice
        lang = "en-gb" if voice.startswith("b") else "en-us"
        return await self.synthesize_with(text, voice=voice, speed=self.config.speed, lang=lang)

    async def synthesize_with(self, text: str, *, voice: str, speed: float, lang: str) -> Speech:
        engine = await self._ensure()
        clean = strip_tags(text)
        self._busy += 1
        try:
            samples, rate = await asyncio.to_thread(
                engine.create, speakable(clean), voice=voice, speed=speed, lang=lang
            )
        finally:
            self._busy -= 1
            self._used = time.monotonic()
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

    def warm(self) -> None:
        if self.failed is None and hasattr(self.primary, "warm"):
            self.primary.warm()

    def release_idle(self, idle_s: float) -> list[str]:
        release = getattr(self.primary, "release_idle", None)
        if release is None:
            return []
        out = release(idle_s)
        return out if isinstance(out, list) else ([self.primary.name] if out else [])

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


# About how big each file is, for a progress figure before the server says (bytes).
KOKORO_SIZES = {"kokoro-v1.0.onnx": 326_000_000, "voices-v1.0.bin": 28_000_000}


async def download_kokoro(
    models_dir: Path,
    *,
    force: bool = False,
    progress: Any = None,
    client: Any = None,
) -> list[Path]:
    """Fetch the Kokoro ONNX model and voice pack from the kokoro-onnx release.

    ``force``: fetch both again even if they are here (a repair). Each file arrives beside the
    old one and replaces it only once it is whole, so a download that breaks off leaves the
    working voice as it was. ``progress(done, total)`` is called with bytes as they arrive."""
    import httpx

    models_dir.mkdir(parents=True, exist_ok=True)
    urls = (KOKORO_MODEL, KOKORO_VOICES)
    names = [u.rsplit("/", 1)[-1] for u in urls]
    sizes = {n: KOKORO_SIZES.get(n, 1) for n in names}
    got = dict.fromkeys(names, 0)

    def tell() -> None:
        if progress is not None:
            progress(sum(got.values()), max(1, sum(sizes.values())))

    out = []
    own = client is None
    client = client or httpx.AsyncClient(follow_redirects=True, timeout=None)
    try:
        for url, name in zip(urls, names, strict=True):
            target = models_dir / name
            if target.exists() and not force:
                got[name] = sizes[name] = target.stat().st_size
                tell()
            else:
                tmp = target.with_suffix(".part")
                async with client.stream("GET", url) as r:
                    r.raise_for_status()
                    length = int(r.headers.get("content-length") or 0)
                    if length:
                        sizes[name] = length
                    with tmp.open("wb") as f:
                        async for chunk in r.aiter_bytes(1 << 20):
                            f.write(chunk)
                            got[name] += len(chunk)
                            tell()
                if length and got[name] != length:  # cut off part-way: never a half voice
                    tmp.unlink(missing_ok=True)
                    raise OSError(f"the download of {name} broke off")
                sizes[name] = got[name]
                os.replace(tmp, target)
            out.append(target)
    finally:
        if own:
            await client.aclose()
    return out
