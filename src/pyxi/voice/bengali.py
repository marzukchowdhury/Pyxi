"""Bengali (Bangla) speech: an on-device Bengali voice, and text made ready to read aloud.

Kokoro has no Bengali, and cloud voices tested for Bengali either need a new account (Azure) or
mispronounce common words (OpenAI's gpt-audio says বৃহস্পতিবার so it is heard as "the 25th").
So Pyxi speaks Bengali on the PC with a VITS voice trained on Bangladeshi speech
(mobassir94's comprehensive-bangla-tts, Apache 2.0, as converted to ONNX by sherpa-onnx). It runs
on ONNX Runtime, which Pyxi already ships for Kokoro, about six times faster than real time on a
CPU. The model (108 MB) downloads once, the first time Bengali is needed.

The voice reads Bengali letters only: no digits beyond a few, no Latin letters. So numbers are
spelled out as Bengali words first (১,২৭৫ or 1,275 -> এক হাজার দুইশো পঁচাত্তর), and short
English acronyms are spelled by their letter names (PDF -> পিডিএফ). The reply prompt asks the
model to write loanwords in Bengali script, so very little else needs help.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import tarfile
import time
from pathlib import Path

import numpy as np

from pyxi.voice.tts import Speech, _breathe, _wav_bytes, strip_tags

log = logging.getLogger(__name__)

VOICE_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/"
    "vits-coqui-bn-custom_female.tar.bz2"
)
VOICE_SHA256 = "a03292d7da03650e892bb1989b40dc2c62574c0d6c34c8bef185fbb3151417a1"
VOICE_DIR = "bengali-mitali"
VOICE_ID = "bn_mitali"
VOICE_MB = 108

_BENGALI = re.compile(r"[ঀ-৿]")
_LATIN = re.compile(r"[A-Za-z]")


def is_bengali(text: str) -> bool:
    """Whether a line is mostly Bengali script (so a Bengali voice must read it)."""
    bn = len(_BENGALI.findall(text))
    return bn > 0 and bn >= len(_LATIN.findall(text))


# ------------------------------------------------------------------ numbers as words
_UNITS = [
    "শূন্য",
    "এক",
    "দুই",
    "তিন",
    "চার",
    "পাঁচ",
    "ছয়",
    "সাত",
    "আট",
    "নয়",
    "দশ",
    "এগারো",
    "বারো",
    "তেরো",
    "চৌদ্দ",
    "পনেরো",
    "ষোলো",
    "সতেরো",
    "আঠারো",
    "উনিশ",
    "বিশ",
    "একুশ",
    "বাইশ",
    "তেইশ",
    "চব্বিশ",
    "পঁচিশ",
    "ছাব্বিশ",
    "সাতাশ",
    "আটাশ",
    "উনত্রিশ",
    "ত্রিশ",
    "একত্রিশ",
    "বত্রিশ",
    "তেত্রিশ",
    "চৌত্রিশ",
    "পঁয়ত্রিশ",
    "ছত্রিশ",
    "সাঁইত্রিশ",
    "আটত্রিশ",
    "উনচল্লিশ",
    "চল্লিশ",
    "একচল্লিশ",
    "বিয়াল্লিশ",
    "তেতাল্লিশ",
    "চুয়াল্লিশ",
    "পঁয়তাল্লিশ",
    "ছেচল্লিশ",
    "সাতচল্লিশ",
    "আটচল্লিশ",
    "উনপঞ্চাশ",
    "পঞ্চাশ",
    "একান্ন",
    "বাহান্ন",
    "তিপ্পান্ন",
    "চুয়ান্ন",
    "পঞ্চান্ন",
    "ছাপ্পান্ন",
    "সাতান্ন",
    "আটান্ন",
    "উনষাট",
    "ষাট",
    "একষট্টি",
    "বাষট্টি",
    "তেষট্টি",
    "চৌষট্টি",
    "পঁয়ষট্টি",
    "ছেষট্টি",
    "সাতষট্টি",
    "আটষট্টি",
    "উনসত্তর",
    "সত্তর",
    "একাত্তর",
    "বাহাত্তর",
    "তিয়াত্তর",
    "চুয়াত্তর",
    "পঁচাত্তর",
    "ছিয়াত্তর",
    "সাতাত্তর",
    "আটাত্তর",
    "উনআশি",
    "আশি",
    "একাশি",
    "বিরাশি",
    "তিরাশি",
    "চুরাশি",
    "পঁচাশি",
    "ছিয়াশি",
    "সাতাশি",
    "আটাশি",
    "উননব্বই",
    "নব্বই",
    "একানব্বই",
    "বিরানব্বই",
    "তিরানব্বই",
    "চুরানব্বই",
    "পঁচানব্বই",
    "ছিয়ানব্বই",
    "সাতানব্বই",
    "আটানব্বই",
    "নিরানব্বই",
]
assert len(_UNITS) == 100

_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")


def number_words(n: int) -> str:
    """1275 -> এক হাজার দুইশো পঁচাত্তর, in the lakh and crore system used in Bangladesh."""
    if n < 100:
        return _UNITS[n]
    parts: list[str] = []
    for size, word in ((10_000_000, "কোটি"), (100_000, "লাখ"), (1000, "হাজার")):
        if n >= size:
            parts.append(f"{number_words(n // size)} {word}")
            n %= size
    if n >= 100:
        parts.append(f"{_UNITS[n // 100]}শো")
        n %= 100
    if n:
        parts.append(_UNITS[n])
    return " ".join(parts)


def _digit_by_digit(s: str) -> str:
    return " ".join(_UNITS[int(d)] for d in s)


_NUMBER = re.compile(
    r"(?P<cur>[৳$€£]\s?)?(?P<num>[0-9০-৯]+(?:,[0-9০-৯]{2,3})*(?:\.[0-9০-৯]+)?)(?P<pct>\s?%)?"
)
_TIME = re.compile(r"(?<![0-9০-৯])([0-9০-৯]{1,2}):([0-9০-৯]{2})(?![0-9০-৯])(টায়|টায়|টার|টা)?")
_CURRENCY = {"৳": "টাকা", "$": "ডলার", "€": "ইউরো", "£": "পাউন্ড"}


def _say_number(m: re.Match[str]) -> str:
    raw = m.group("num").translate(_DIGITS)
    whole, _, frac = raw.replace(",", "").partition(".")
    if len(whole) > 9 or (len(whole) > 4 and whole.startswith("0") and "," not in raw):
        words = _digit_by_digit(whole)  # a phone or account number: say each digit
    else:
        words = number_words(int(whole))
    if frac:
        words += " দশমিক " + _digit_by_digit(frac)
    if m.group("pct"):
        words += " শতাংশ"
    if cur := m.group("cur"):
        words += " " + _CURRENCY[cur.strip()]
    return words


def _say_time(m: re.Match[str]) -> str:
    """4:30টায় -> চারটা ত্রিশ মিনিটে; 4:00টায় -> চারটায় (Bengali digits too)."""
    h, mm = int(m.group(1).translate(_DIGITS)), int(m.group(2).translate(_DIGITS))
    suffix = m.group(3) or ""
    if not mm:
        return f"{number_words(h)}{suffix or 'টা'}"
    ending = {"টায়": "ে", "টায়": "ে", "টার": "ের"}.get(suffix, "")
    return f"{number_words(h)}টা {number_words(mm)} মিনিট{ending}"


_LETTERS = dict(
    zip(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
        [
            "এ",
            "বি",
            "সি",
            "ডি",
            "ই",
            "এফ",
            "জি",
            "এইচ",
            "আই",
            "জে",
            "কে",
            "এল",
            "এম",
            "এন",
            "ও",
            "পি",
            "কিউ",
            "আর",
            "এস",
            "টি",
            "ইউ",
            "ভি",
            "ডাবলিউ",
            "এক্স",
            "ওয়াই",
            "জেড",
        ],
        strict=True,
    )
)
_ACRONYM = re.compile(r"(?<![A-Za-z])[A-Z]{2,5}(?![A-Za-z])")
_SYMBOLS = [
    (re.compile(r"\s*&\s*"), " এবং "),
    (re.compile(r"\s*\+\s*"), " যোগ "),
    (re.compile(r"[*_`#]+"), ""),
]


def speakable_bengali(text: str) -> str:
    """Bengali text the voice can read: numbers as words, acronyms by letter name."""
    text = _TIME.sub(_say_time, text)
    text = _NUMBER.sub(_say_number, text)
    text = _ACRONYM.sub(lambda m: "".join(_LETTERS[c] for c in m.group(0)), text)
    for pattern, repl in _SYMBOLS:
        text = pattern.sub(repl, text)
    return re.sub(r"\s{2,}", " ", text).strip()


# ------------------------------------------------------------------ the voice
_CLAUSE = re.compile(r"(?<=[,;:।?!.])\s+")


class BengaliVoice:
    """Mitali, Pyxi's on-device Bengali voice (VITS on ONNX Runtime)."""

    name = "bengali"
    rate = 22050

    def __init__(self, models_dir: Path) -> None:
        self.dir = models_dir / VOICE_DIR
        self._session = None
        self._tokens: dict[str, int] = {}
        self._lock = asyncio.Lock()
        self._failed_at = 0.0
        self.error: str | None = None
        self._used = 0.0
        self._busy = 0

    def installed(self) -> bool:
        return (self.dir / "model.onnx").exists() and (self.dir / "tokens.txt").exists()

    async def ensure(self) -> None:
        """Download (once) and load the voice. Raises RuntimeError if it can't."""
        async with self._lock:
            if self._session is not None:
                return
            if not self.installed():
                if (
                    time.monotonic() - self._failed_at < 60
                ):  # don't retry a failing download per line
                    raise RuntimeError(self.error or "the Bengali voice isn't downloaded yet")
                try:
                    log.info("downloading the Bengali voice (%s MB) to %s", VOICE_MB, self.dir)
                    await download_bengali_voice(self.dir.parent)
                except Exception as exc:
                    self._failed_at = time.monotonic()
                    self.error = f"Couldn't download the Bengali voice ({exc.__class__.__name__})"
                    raise RuntimeError(self.error) from exc
            self._session, self._tokens = await asyncio.to_thread(self._load)
            self.error = None
            self._used = time.monotonic()

    async def fetch(self) -> None:
        """Download the voice (once) without loading it. Raises RuntimeError if it can't."""
        async with self._lock:
            if self.installed():
                return
            try:
                log.info("downloading the Bengali voice (%s MB) to %s", VOICE_MB, self.dir)
                await download_bengali_voice(self.dir.parent)
            except Exception as exc:
                self._failed_at = time.monotonic()
                self.error = f"Couldn't download the Bengali voice ({exc.__class__.__name__})"
                raise RuntimeError(self.error) from exc

    def release_idle(self, idle_s: float) -> bool:
        if self._session is None or self._busy or time.monotonic() - self._used < idle_s:
            return False
        self._session = None
        return True

    def _load(self):
        import onnxruntime as ort

        tokens: dict[str, int] = {}
        for line in (self.dir / "tokens.txt").read_text(encoding="utf-8").splitlines():
            sym, _, idx = line.rpartition(" ")
            if idx.isdigit():
                tokens[sym or " "] = int(idx)
        opts = ort.SessionOptions()
        opts.log_severity_level = 3
        session = ort.InferenceSession(
            str(self.dir / "model.onnx"), opts, providers=["CPUExecutionProvider"]
        )
        return session, tokens

    def _ids(self, text: str) -> list[int]:
        # Characters the voice knows, with a blank between each (the model was trained so).
        blank = self._tokens.get("<BLNK>", 3)
        ids = [blank]
        for ch in text:
            if (i := self._tokens.get(ch)) is not None:
                ids += [i, blank]
        return ids

    def _run(self, text: str, speed: float) -> np.ndarray:
        ids = self._ids(text)
        if len(ids) < 3:
            return np.zeros(0, dtype=np.float32)
        x = np.array([ids], dtype=np.int64)
        out = self._session.run(  # type: ignore[union-attr]
            None,
            {
                "input": x,
                "input_lengths": np.array([x.shape[1]], dtype=np.int64),
                "scales": np.array([0.667, 1.0 / max(0.5, min(1.6, speed)), 0.8], dtype=np.float32),
            },
        )[0]
        return out.reshape(-1).astype(np.float32)

    def _render(self, text: str, speed: float) -> np.ndarray:
        """Clause by clause, with short pauses at commas and full stops (the model on its own
        runs straight through punctuation)."""
        parts: list[np.ndarray] = []
        for clause in _CLAUSE.split(text):
            audio = self._run(clause, speed)
            if not audio.size:
                continue
            level = np.abs(audio)
            loud = np.flatnonzero(level > 0.01)
            if loud.size:
                audio = audio[max(0, loud[0] - 220) : loud[-1] + 660]
            pause = 0.24 if clause.rstrip()[-1:] in "।?!." else 0.12
            parts += [audio, np.zeros(int(pause * self.rate), dtype=np.float32)]
        if not parts:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(parts[:-1])

    async def synthesize(self, text: str, speed: float = 1.0) -> Speech:
        await self.ensure()
        clean = strip_tags(text)
        self._busy += 1
        try:
            samples = await asyncio.to_thread(self._render, speakable_bengali(clean), speed)
        finally:
            self._busy -= 1
            self._used = time.monotonic()
        if not samples.size:
            return Speech(text=clean, wav=None)
        peak = float(np.max(np.abs(samples))) or 1.0
        samples = _breathe(samples * min(1.0, 0.9 / peak), self.rate)
        return Speech(
            text=clean, wav=_wav_bytes(samples, self.rate), duration_s=len(samples) / self.rate
        )


async def download_bengali_voice(models_dir: Path) -> Path:
    """Fetch the Bengali voice, check it is the exact file we tested, and keep only the model."""
    import httpx

    target = models_dir / VOICE_DIR
    if (target / "model.onnx").exists() and (target / "tokens.txt").exists():
        return target
    models_dir.mkdir(parents=True, exist_ok=True)
    archive = models_dir / "bengali-voice.tar.bz2.part"
    digest = hashlib.sha256()
    timeout = httpx.Timeout(30, read=120)
    async with (
        httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client,
        client.stream("GET", VOICE_URL) as r,
    ):
        r.raise_for_status()
        with archive.open("wb") as f:
            async for chunk in r.aiter_bytes(1 << 20):
                digest.update(chunk)
                f.write(chunk)
    try:
        if digest.hexdigest() != VOICE_SHA256:
            raise RuntimeError("the Bengali voice download didn't match the expected file")
        await asyncio.to_thread(_extract, archive, target)
    finally:
        archive.unlink(missing_ok=True)
    return target


def _extract(archive: Path, target: Path) -> None:
    tmp = target.with_name(target.name + ".part")
    tmp.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:bz2") as tar:
        for member in tar.getmembers():
            name = Path(member.name).name
            if member.isfile() and name in ("model.onnx", "tokens.txt"):
                src = tar.extractfile(member)
                if src is not None:
                    (tmp / name).write_bytes(src.read())
    if not (tmp / "model.onnx").exists():
        raise RuntimeError("the Bengali voice archive had no model in it")
    if target.exists():
        for p in target.iterdir():
            p.unlink()
        target.rmdir()
    tmp.rename(target)
