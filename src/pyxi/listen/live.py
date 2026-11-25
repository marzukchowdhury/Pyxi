"""Sound into words while it plays: the captures, the pauses and Pyxi's own speech model.

One :class:`LiveEars` serves everyone who wants to hear (meeting notes, captions): the PC's
sound is captured and transcribed once, however many are listening, and a capture stops the
moment its last listener leaves. Transcription is done on this PC with the faster-whisper
model push-to-talk already uses (:mod:`pyxi.voice.stt`); no sound is sent anywhere and none is
written to disk.

Words reach a listener as :class:`Heard`: the stretch so far while someone is talking
(``final`` False, same ``id``), then the finished stretch.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from pyxi.listen import capture as cap
from pyxi.listen.segment import Piece, Segmenter

log = logging.getLogger(__name__)

SYSTEM, MIC = cap.SYSTEM, cap.MIC
PARTIAL_EVERY_S = 0.5
CAPTION_PACE = (0.4, 6.0)  # a pause this long ends a stretch; no stretch is longer than this
NOTES_PACE = (0.6, 12.0)

# What Whisper writes for silence, music and noise. A stretch that is only this is dropped.
_NOT_SPEECH = re.compile(
    r"^[\s\W]*(?:thanks? (?:you )?for watching[\s\W]*|thank you[\s\W]*|you|bye[\s\W]*|"
    r"\[[^\]]*\]|\([^)]*\)|♪.*|subtitles by.*|please subscribe.*)?[\s\W]*$",
    re.I,
)


@dataclass
class Heard:
    source: str  # "system" (what the PC plays) or "mic"
    id: int  # the stretch's number: partial updates and the final carry the same one
    text: str
    final: bool
    at: float  # when it was said (Unix time of the stretch's start)
    seconds: float  # how long the stretch is
    lag_s: float  # from its last sound to these words being ready
    language: str = "en"
    english: str = ""  # the same words in English, when asked for and they weren't English


Listener = Callable[[Heard], Awaitable[None] | None]


@dataclass
class _Sub:
    listener: Listener
    sources: set[str]
    partials: bool


@dataclass
class _Channel:
    source: str
    capture: Any
    segmenter: Segmenter
    queue: asyncio.Queue[Piece | None]
    task: asyncio.Task[None] | None = None
    recent: list[str] = field(default_factory=list)


def clean(text: str) -> str:
    """One stretch's words as shown: spaces folded, and nothing at all for the stock lines
    Whisper writes over silence or music."""
    text = " ".join(str(text or "").split())
    return "" if _NOT_SPEECH.match(text) else text


def run_model(
    model: Any, samples: Any, language: str | None, task: str = "transcribe"
) -> tuple[str, str, float]:
    """One stretch through faster-whisper: (text, language, how sure of the language).
    ``task`` "translate" gives the words in English instead (Whisper's own translation)."""
    segments, info = model.transcribe(
        samples,
        language=language,
        task=task,
        beam_size=1,
        vad_filter=False,
        condition_on_previous_text=False,
        without_timestamps=True,
    )
    words = []
    for s in segments:
        # Whisper's own doubt: "there is no speech here" and unsure of every word.
        if getattr(s, "no_speech_prob", 0.0) > 0.6 and getattr(s, "avg_logprob", 0.0) < -0.7:
            continue
        words.append(str(s.text).strip())
    spoken = language or getattr(info, "language", "en") or "en"
    return " ".join(w for w in words if w), spoken, float(getattr(info, "language_probability", 1.0))


class LiveEars:
    """``stt`` is the brain's :class:`pyxi.voice.stt.Transcriber`. ``gate(source)`` says when a
    source must not be heard right now (Pyxi's own voice is playing; they are holding the talk
    key): that sound is replaced by silence before anything sees it."""

    def __init__(
        self,
        stt: Any,
        *,
        gate: Callable[[str], bool] | None = None,
        make_capture: Callable[[str, Any], Any] | None = None,
        transcribe: Callable[..., tuple[str, str, float]] = run_model,
    ) -> None:
        self.stt = stt
        self.gate = gate
        self.make_capture = make_capture or cap.Capture
        self.transcribe = transcribe
        # Also give finished stretches in English (Whisper's own translation, on this PC).
        self.english = False
        self.language = "en"  # "en", another code, or "auto"
        self.spoken = ""  # with "auto": the language it settled on
        self._subs: list[_Sub] = []
        self._channels: dict[str, _Channel] = {}
        self._hold: Any = None
        self._model: Any = None
        self._model_lang = ""
        self._lock = asyncio.Lock()
        self.lags: list[float] = []  # recent processing lags of partials, for the report

    # ------------------------------------------------------------------ who is listening
    def active(self, source: str | None = None) -> bool:
        return bool(self._channels) if source is None else source in self._channels

    def problem(self, source: str) -> str:
        ch = self._channels.get(source)
        return str(ch.capture.problem) if ch else ""

    def level(self, source: str) -> float:
        ch = self._channels.get(source)
        return float(getattr(ch.capture, "level", 0.0)) if ch else 0.0

    def quiet_for(self, source: str) -> float:
        """Seconds since real sound last arrived from this source (0 when it never started)."""
        ch = self._channels.get(source)
        heard = float(getattr(ch.capture, "heard_at", 0.0)) if ch else 0.0
        return time.monotonic() - heard if heard else float("inf")

    async def add(
        self, listener: Listener, sources: set[str], *, partials: bool = False, language: str = ""
    ) -> _Sub:
        """Start hearing ``sources`` for ``listener``. Raises RuntimeError (a plain sentence)
        when the PC's sound can't be opened; a missing microphone is not fatal for a caller
        that also listens to the PC (see :meth:`problem`)."""
        async with self._lock:
            if language and language != self.language:
                await self._set_language(language)
            await self._ensure_model()
            sub = _Sub(listener, set(sources), partials)
            self._subs.append(sub)
            try:
                for source in sorted(sources):
                    await self._open(source)
            except Exception:
                self._subs.remove(sub)
                await self._prune()
                raise
            self._retune()
            return sub

    async def remove(self, sub: _Sub | None) -> None:
        async with self._lock:
            if sub in self._subs:
                # A capture only this listener wanted is closed while it still listens, so
                # the words that were being said reach it.
                others = [s for s in self._subs if s is not sub]
                await self._prune(lambda source: any(source in s.sources for s in others))
                self._subs.remove(sub)
            await self._prune()
            self._retune()

    async def close(self) -> None:
        async with self._lock:
            self._subs.clear()
            await self._prune()

    async def set_language(self, language: str) -> None:
        """Hear another language from now on (swaps the model when it has to)."""
        async with self._lock:
            if language and language != self.language:
                await self._set_language(language)
                if self._channels:
                    await self._ensure_model()

    # ------------------------------------------------------------------ inside
    async def _set_language(self, language: str) -> None:
        self.language, self.spoken = language, ""
        if self._hold is not None and self.stt.model_for(language) != self._model_lang:
            await self._release_model()

    async def _ensure_model(self) -> None:
        if self._model is not None:
            return
        name = self.stt.model_for(self.language)
        hold = getattr(self.stt, "hold", None)
        if hold is None:
            self._model = await self.stt.warm_up(self.language)
        else:
            self._hold = hold(self.language)  # stays loaded for as long as anyone listens
            self._model = await self._hold.__aenter__()
        self._model_lang = name

    async def _release_model(self) -> None:
        hold, self._hold, self._model = self._hold, None, None
        if hold is not None:
            with contextlib.suppress(Exception):
                await hold.__aexit__(None, None, None)

    def _wants(self, source: str, *, partials: bool = False) -> bool:
        return any(source in s.sources and (s.partials or not partials) for s in self._subs)

    def _retune(self) -> None:
        for source, ch in self._channels.items():
            live = self._wants(source, partials=True)
            ch.segmenter.partials = live
            # Captions: short stretches (a breath ends one, and nobody talks more than a few
            # seconds without being cut at a quiet moment), so the finished sentence, and
            # its translation, is never far behind. Notes alone: whole sentences.
            ch.segmenter.tune(*(CAPTION_PACE if live else NOTES_PACE))

    async def _open(self, source: str) -> None:
        if source in self._channels:
            return
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[Piece | None] = asyncio.Queue()
        segmenter = Segmenter(partials=False, partial_every_s=PARTIAL_EVERY_S)
        gate = self.gate

        def sink(samples: Any, t: float) -> None:  # on the capture's own thread
            if gate is not None and gate(source):
                samples = samples * 0.0
            for piece in segmenter.feed(samples, t):
                loop.call_soon_threadsafe(queue.put_nowait, piece)

        capture = self.make_capture(source, sink)
        ok = await asyncio.to_thread(capture.start)
        if not ok:
            problem = capture.problem or "I couldn't listen to that."
            with contextlib.suppress(Exception):
                capture.stop()
            raise RuntimeError(problem)
        ch = _Channel(source, capture, segmenter, queue)
        ch.task = asyncio.create_task(self._work(ch))
        self._channels[source] = ch

    async def _prune(self, wanted: Callable[[str], bool] | None = None) -> None:
        wanted = wanted or self._wants
        for source in [s for s in self._channels if not wanted(s)]:
            ch = self._channels.pop(source)
            await asyncio.to_thread(ch.capture.stop)
            await asyncio.sleep(0)  # pieces its thread handed over just now come first
            await asyncio.sleep(0)
            for piece in ch.segmenter.flush():  # what was being said is not lost
                ch.queue.put_nowait(piece)
            ch.queue.put_nowait(None)
            if ch.task is not None:
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(ch.task, 20)
        if not self._channels:
            await self._release_model()

    async def flush(self, source: str) -> None:
        """Wait until everything heard from ``source`` so far has reached the listeners."""
        ch = self._channels.get(source)
        if ch is None:
            return
        for _ in range(400):
            if ch.queue.empty() and not getattr(ch, "busy", False):
                return
            await asyncio.sleep(0.05)

    async def _work(self, ch: _Channel) -> None:
        while True:
            piece = await ch.queue.get()
            if piece is None:
                return
            # A newer look at the same stretch (or its end) is already waiting, or nobody shows
            # partial words: this one would only make everything later.
            if not piece.final and (
                not self._wants(ch.source, partials=True) or not ch.queue.empty()
            ):
                continue
            ch.busy = True  # type: ignore[attr-defined]
            try:
                heard = await self._hear(ch, piece)
            except Exception:
                log.exception("live transcription failed")
                heard = None
            finally:
                ch.busy = False  # type: ignore[attr-defined]
            if heard is None:
                continue
            for sub in list(self._subs):
                if ch.source not in sub.sources or (not heard.final and not sub.partials):
                    continue
                try:
                    result = sub.listener(heard)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception:
                    log.exception("a listener failed")

    async def _hear(self, ch: _Channel, piece: Piece) -> Heard | None:
        model = self._model
        for _ in range(900):  # being swapped for another language's model: a moment
            if model is not None or not self._channels:
                break
            await asyncio.sleep(0.05)
            model = self._model
        if model is None:
            return None
        english_only = self._model_lang.endswith(".en")
        language: str | None = (
            "en" if english_only else (self.spoken or None) if self.language == "auto" else self.language
        )
        began = time.monotonic()
        text, spoken, sure = await asyncio.to_thread(self.transcribe, model, piece.samples, language)
        seconds = len(piece.samples) / 16000
        if not piece.final:
            # Words so far are asked for as often as this PC can keep up with: twice a second
            # on a fast one, down to every two seconds on a slow one.
            took = time.monotonic() - began
            ch.segmenter.every = round(min(2.0, max(PARTIAL_EVERY_S, took * 3)) * 50)
        if self.language == "auto" and not self.spoken and piece.final and seconds >= 2.0 and sure >= 0.7:
            self.spoken = spoken  # settled: a short "OK" later can't flip the language
        text = clean(text)
        lag = time.monotonic() - piece.end
        if not piece.final:
            self.lags = [*self.lags[-199:], lag]
        if not text and not piece.final:
            return None
        if not text:
            # A final with no words still closes any partial line that was showing.
            return Heard(ch.source, piece.id, "", True, _wall(piece.start), seconds, lag, spoken)
        english = ""
        if piece.final and self.english and ch.source == SYSTEM and not english_only and spoken != "en":
            try:
                english, _, _ = await asyncio.to_thread(
                    self.transcribe, model, piece.samples, spoken, "translate"
                )
                english = clean(english)
            except Exception:
                log.exception("translating a stretch into English failed")
        return Heard(
            ch.source, piece.id, text, piece.final, _wall(piece.start), seconds, lag, spoken, english
        )


def _wall(monotonic: float) -> float:
    return time.time() - (time.monotonic() - monotonic)
