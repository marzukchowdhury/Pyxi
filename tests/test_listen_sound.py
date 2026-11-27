"""Hearing what the PC plays: the sound itself, the pauses, and the words while it plays.

No test here opens a real device or loads a speech model: sound is made up with numpy, the
capture is a pretend one the test feeds, and "the model" answers from a script.
"""

from __future__ import annotations

import asyncio
import itertools
from typing import Any

import numpy as np
import pytest

from pyxi.listen import capture, wincaptions
from pyxi.listen.live import MIC, SYSTEM, Heard, LiveEars, clean
from pyxi.listen.segment import FRAME, RATE, Segmenter


def talk(seconds: float, level: float = 0.1, seed: int = 1) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return (rng.standard_normal(int(seconds * RATE)) * level).astype(np.float32)


def hush(seconds: float) -> np.ndarray:
    return np.zeros(int(seconds * RATE), dtype=np.float32)


def feed(seg: Segmenter, audio: np.ndarray, start: float = 0.0, chunk: int = 480) -> list:
    """Feed it the way a capture does: 30 ms at a time, each with the time of its end."""
    out, t = [], start
    for i in range(0, len(audio), chunk):
        part = audio[i : i + chunk]
        t += len(part) / RATE
        out += seg.feed(part, t)
    return out


# ------------------------------------------------------------------ the sound itself
def test_sound_at_the_speakers_rate_becomes_16k_without_losing_time():
    r = capture.Resampler(48000)
    t = np.arange(48000 * 2, dtype=np.float32) / 48000
    tone = np.sin(2 * np.pi * 440 * t).astype(np.float32) * 0.5
    out = np.concatenate([r.feed(tone[i : i + 480]) for i in range(0, len(tone), 480)])
    assert abs(len(out) - 2 * RATE) < 200  # two seconds in, two seconds out
    # Still a 440 Hz tone of the same loudness (the middle of it, clear of the edges).
    mid = out[4000:28000]
    assert 0.3 < float(np.sqrt((mid**2).mean())) < 0.4
    spectrum = np.abs(np.fft.rfft(mid))
    assert abs(np.argmax(spectrum) * RATE / len(mid) - 440) < 3


def test_a_44k_device_and_one_already_at_16k():
    r = capture.Resampler(44100)
    out = np.concatenate([r.feed(talk(1.0, seed=i)[:441]) for i in range(300)])  # 10 ms each
    assert abs(len(out) - 300 * 441 * RATE / 44100) < 200
    same = capture.Resampler(16000)
    chunk = talk(0.1)
    assert same.feed(chunk) is chunk


def test_the_devices_own_formats_are_read():
    class Fmt:
        nChannels, nSamplesPerSec, nBlockAlign, cbSize = 2, 48000, 4, 0

        def __init__(self, tag: int, bits: int) -> None:
            self.wFormatTag, self.wBitsPerSample = tag, bits

    stereo16 = np.array([[16384, -16384], [8192, 8192]], dtype=np.int16)
    got = capture._Format(Fmt(1, 16)).mono(stereo16.tobytes())
    assert np.allclose(got, [0.0, 0.25])  # the two channels are averaged
    floats = np.array([[0.5, 0.25], [-1.0, 0.0]], dtype=np.float32)
    assert np.allclose(capture._Format(Fmt(3, 32)).mono(floats.tobytes()), [0.375, -0.5])
    three = bytes([0x00, 0x00, 0x40, 0x00, 0x00, 0xC0])  # +0.5 and -0.5 as 24-bit
    assert np.allclose(capture._Format(Fmt(1, 24)).mono(three), [0.0])
    with pytest.raises(ValueError):
        capture._Format(Fmt(1, 8)).mono(b"\x00\x00")


class FakeDevice:
    """Stands in for Windows' capture client: hands out the packets it was given."""

    def __init__(self, packets: list[np.ndarray], fail_after: int | None = None) -> None:
        self.packets, self.fail_after, self.given = list(packets), fail_after, 0
        self.stopped = False
        self._held: Any = None

    def GetNextPacketSize(self) -> int:
        if self.fail_after is not None and self.given >= self.fail_after:
            raise OSError("AUDCLNT_E_DEVICE_INVALIDATED")
        return len(self.packets[0]) if self.packets else 0

    def GetBuffer(self, *_: Any) -> tuple[Any, int, int]:
        import ctypes

        packet = self.packets.pop(0)
        self.given += 1
        self._held = ctypes.create_string_buffer(packet.astype(np.float32).tobytes())
        return self._held, len(packet), 0

    def ReleaseBuffer(self, frames: int) -> None:
        self._held = None

    def Stop(self) -> None:
        self.stopped = True


@pytest.mark.skipif(not capture.available(), reason="Windows' sound interfaces only")
def test_headphones_plugged_in_half_way_and_nothing_playing():
    import time

    class Fmt:
        rate, block = 16000, 4

        @staticmethod
        def mono(raw: bytes) -> np.ndarray:
            return np.frombuffer(raw, dtype=np.float32)

    speakers = FakeDevice([np.full(1600, 0.25, dtype=np.float32)] * 3)
    headphones = FakeDevice([np.full(1600, 0.5, dtype=np.float32)] * 3)
    state = {"default": "speakers", "opened": []}

    class Cap(capture.Capture):
        def _open(self):
            name = state["default"]
            state["opened"].append(name)
            self.device = name
            device = speakers if name == "speakers" else headphones
            return device, device, Fmt, capture.Resampler(16000)

        def _current_device(self) -> str:
            return state["default"]

    got: list[np.ndarray] = []
    cap = Cap(capture.SYSTEM, lambda samples, t: got.append(samples.copy()))
    old = capture._DEVICE_CHECK_S
    capture._DEVICE_CHECK_S = 0.05
    try:
        assert capture.Capture.start.real(cap) and cap.running
        time.sleep(0.25)
        state["default"] = "headphones"  # plugged in: Windows makes them the default
        time.sleep(0.5)
    finally:
        capture._DEVICE_CHECK_S = old
        cap.stop()
    assert state["opened"] == ["speakers", "headphones"] and cap.reopened == 1
    assert speakers.stopped and not cap.problem
    heard = np.concatenate(got)
    assert (heard == 0.25).sum() == 4800 and (heard == 0.5).sum() == 4800  # both, in full
    # With nothing playing, Windows sends nothing: the gaps are filled by the clock.
    assert 0.3 * 16000 < (heard == 0.0).sum() < 0.9 * 16000
    assert list(heard[heard != 0.0][:1]) == [0.25] and heard[heard != 0.0][-1] == 0.5


@pytest.mark.skipif(not capture.available(), reason="Windows' sound interfaces only")
def test_no_speakers_at_all_is_a_plain_sentence():
    class Cap(capture.Capture):
        def _open(self):
            raise OSError(-2147023728, "Element not found.")

    cap = Cap(capture.SYSTEM, lambda samples, t: None)
    assert capture.Capture.start.real(cap) is False
    assert cap.problem.startswith("I couldn't listen to this PC's sound") and "Error" not in cap.problem
    mic = Cap(capture.MIC, lambda samples, t: None)
    assert capture.Capture.start.real(mic) is False and "microphone" in mic.problem
    cap.stop()
    mic.stop()


# ------------------------------------------------------------------ the pauses
def test_a_pause_ends_a_stretch_and_words_come_while_it_is_said():
    seg = Segmenter(pause_s=0.6, partial_every_s=0.5)
    pieces = feed(seg, np.concatenate([hush(0.5), talk(2.0), hush(1.0), talk(1.0, seed=2), hush(1.0)]))
    finals = [p for p in pieces if p.final]
    assert [p.id for p in finals] == [0, 1]
    assert 1.9 < len(finals[0].samples) / RATE < 2.6  # the talk, a little lead-in and tail
    partials = [p for p in pieces if not p.final and p.id == 0]
    assert len(partials) >= 3 and all(len(a.samples) < len(b.samples) for a, b in itertools.pairwise(partials))
    # Times are the clock's: the first stretch began about half a second in.
    assert 0.2 < finals[0].start < 0.6 and 2.4 < finals[0].end < 2.9


def test_someone_who_never_pauses_is_cut_at_a_quiet_moment_and_nothing_is_lost():
    seg = Segmenter(max_s=4.0, partials=False)
    # Ten seconds without a pause; a soft spot at 3.4 s.
    audio = talk(10.0)
    audio[int(3.4 * RATE) : int(3.5 * RATE)] *= 0.08
    pieces = feed(seg, audio) + seg.flush()
    assert all(p.final for p in pieces) and len(pieces) >= 3
    assert len(pieces[0].samples) / RATE == pytest.approx(3.45, abs=0.15)  # cut in the soft spot
    assert sum(len(p.samples) for p in pieces) == len(audio) // FRAME * FRAME  # every sample kept
    assert max(len(p.samples) for p in pieces) <= 4.0 * RATE


def test_a_click_is_not_speech_and_silence_is_nothing():
    seg = Segmenter()
    assert feed(seg, hush(3.0)) == []
    assert feed(seg, np.concatenate([talk(0.06), hush(1.5)])) == []
    assert not seg.speaking


def test_a_steady_hum_becomes_the_rooms_quiet():
    seg = Segmenter()
    hum = talk(6.0, level=0.006, seed=5)  # a fan: steady and soft, just above the floor
    feed(seg, hum)
    pieces = feed(seg, np.concatenate([talk(1.5, level=0.2), hum[: RATE * 2]]), start=6.0)
    assert [p.final for p in pieces if p.final] == [True]  # the talk over the hum is heard


# ------------------------------------------------------------------ Windows' own captions
def test_windows_captions_text_becomes_lines():
    s = wincaptions.Splitter(settle_s=1.5)
    assert s.feed("Good morning", 0.0) == [(0, "Good morning", False)]
    assert s.feed("Good morning every", 0.2) == [(0, "Good morning every", False)]
    assert s.feed("Good morning everyone. Let us", 0.5) == [
        (0, "Good morning everyone.", True),
        (1, "Let us", False),
    ]
    assert s.feed("Good morning everyone. Let us", 0.7) == []
    assert s.feed("Good morning everyone. Let us start with the budget.", 1.0) == [
        (1, "Let us start with the budget.", False)
    ]
    # It stopped changing: it was the end of the sentence.
    assert s.feed("Good morning everyone. Let us start with the budget.", 2.6) == [
        (1, "Let us start with the budget.", True)
    ]
    # The block scrolled: the first sentence is gone from it. Nothing is said twice.
    assert s.feed("Let us start with the budget. We decided", 3.0) == [(2, "We decided", False)]


# ------------------------------------------------------------------ words while it plays
class FakeCapture:
    made: list[FakeCapture] = []

    def __init__(self, source: str, sink: Any) -> None:
        self.source, self.sink = source, sink
        self.problem, self.level, self.heard_at = "", 0.0, 0.0
        self.started = self.stopped = False
        self.t = 100.0
        FakeCapture.made.append(self)

    def start(self) -> bool:
        self.started = True
        return not self.problem

    def stop(self) -> None:
        self.stopped = True

    def play(self, audio: np.ndarray, chunk: int = 480) -> None:
        for i in range(0, len(audio), chunk):
            part = audio[i : i + chunk]
            self.t += len(part) / RATE
            self.sink(part, self.t)


class FakeSTT:
    def __init__(self) -> None:
        self.loads: list[str] = []

    def model_for(self, language: str) -> str:
        return "base.en" if language == "en" else "small"

    async def warm_up(self, language: str = "en") -> str:
        self.loads.append(language)
        return f"model:{self.model_for(language)}"


def said(texts: list[str]):
    """A pretend model: the nth finished stretch is the nth text; partials are its start."""
    calls: list[tuple[int, str | None, str]] = []

    def transcribe(model: Any, samples: Any, language: str | None, task: str = "transcribe"):
        calls.append((len(samples), language, task))
        if not float(np.abs(samples).max()):
            return "", language or "en", 1.0
        text = texts[min(len(texts) - 1, transcribe.finals)]
        if task == "translate":
            return f"(in English) {text}", "es", 0.9
        return text, language or "es", 0.9

    transcribe.finals = 0  # type: ignore[attr-defined]
    transcribe.calls = calls  # type: ignore[attr-defined]
    return transcribe


def ears(texts: list[str], **kw: Any) -> tuple[LiveEars, Any]:
    FakeCapture.made = []
    t = said(texts)
    e = LiveEars(FakeSTT(), make_capture=FakeCapture, transcribe=t, **kw)
    return e, t


async def drain(e: LiveEars, source: str = SYSTEM) -> None:
    await asyncio.sleep(0.05)
    await e.flush(source)
    await asyncio.sleep(0.05)


async def test_one_capture_serves_everyone_and_stops_with_the_last_listener():
    e, _t = ears(["Hello there."])
    notes: list[Heard] = []
    caps: list[Heard] = []
    a = await e.add(notes.append, {SYSTEM})
    b = await e.add(caps.append, {SYSTEM}, partials=True)
    assert len(FakeCapture.made) == 1  # heard once, however many are listening
    cap = FakeCapture.made[0]
    voice = talk(1.6)
    for i in range(0, len(voice), 4800):  # as it plays: a little at a time
        cap.play(voice[i : i + 4800])
        await asyncio.sleep(0.02)
    cap.play(hush(1.0))
    await drain(e)
    assert [h.text for h in notes] == ["Hello there."] and notes[0].final
    assert any(not h.final for h in caps) and caps[-1].final  # words while it was said
    assert caps[-1].id == notes[0].id == 0 and notes[0].seconds == pytest.approx(1.8, abs=0.4)
    await e.remove(b)
    assert not cap.stopped  # the notes still listen
    await e.remove(a)
    assert cap.stopped and not e.active()


async def test_pyxis_own_voice_and_the_talk_key_are_heard_as_silence():
    gated = {SYSTEM: True, MIC: False}
    e, t = ears(["Pyxi's own words."], gate=lambda source: gated[source])
    heard: list[Heard] = []
    await e.add(heard.append, {SYSTEM}, partials=True)
    cap = FakeCapture.made[0]
    cap.play(np.concatenate([talk(1.5), hush(1.0)]))  # Pyxi is speaking: none of it is heard
    await drain(e)
    assert heard == [] and t.calls == []
    gated[SYSTEM] = False
    cap.play(np.concatenate([talk(1.5), hush(1.0)]))
    await drain(e)
    assert [h.text for h in heard if h.final] == ["Pyxi's own words."]
    await e.close()


async def test_no_speakers_to_listen_to_says_so_and_leaves_nothing_running():
    e, _ = ears(["x"])

    def broken(source: str, sink: Any) -> FakeCapture:
        c = FakeCapture(source, sink)
        c.problem = "I couldn't listen to this PC's sound."
        return c

    e.make_capture = broken
    with pytest.raises(RuntimeError, match="couldn't listen"):
        await e.add(lambda h: None, {SYSTEM})
    assert not e.active() and e._subs == []


async def test_another_language_is_worked_out_once_and_given_in_english_too():
    e, t = ears(["Hola a todos.", "Empecemos."])
    e.english = True
    heard: list[Heard] = []
    await e.add(heard.append, {SYSTEM}, language="auto")
    assert e.stt.loads == ["auto"] and e._model_lang == "small"
    cap = FakeCapture.made[0]
    cap.play(np.concatenate([talk(2.4), hush(1.0)]))
    await drain(e)
    t.finals = 1
    cap.play(np.concatenate([talk(1.2, seed=3), hush(1.0)]))
    await drain(e)
    assert [(h.text, h.language, h.english) for h in heard] == [
        ("Hola a todos.", "es", "(in English) Hola a todos."),
        ("Empecemos.", "es", "(in English) Empecemos."),
    ]
    # The first stretch was asked "which language?"; from then on it is settled.
    asked = [lang for _, lang, task in t.calls if task == "transcribe"]
    assert asked[0] is None and asked[-1] == "es" and e.spoken == "es"
    await e.close()


async def test_what_was_being_said_when_it_stops_is_not_lost():
    e, _ = ears(["Cut off mid sentence"])
    heard: list[Heard] = []
    sub = await e.add(heard.append, {SYSTEM})
    FakeCapture.made[0].play(talk(1.5))  # no pause yet
    await e.remove(sub)
    assert [h.text for h in heard] == ["Cut off mid sentence"] and heard[0].final


def test_the_stock_lines_a_model_writes_over_silence_are_dropped():
    for junk in ["Thank you.", " Thanks for watching!", "you", "[Music]", "(applause)", "...", ""]:
        assert clean(junk) == "", junk
    assert clean("  Thank you   for the update, Sam. ") == "Thank you for the update, Sam."
