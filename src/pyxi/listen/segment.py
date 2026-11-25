"""Where one stretch of speech ends and the next begins, in a stream that never stops.

Sound comes in as small chunks. This cuts it into stretches ("utterances") at the pauses, and
while someone is still talking hands out the stretch so far every second or so, so words can
be shown before the sentence ends. No model here: loudness against the room's own quiet.

* A pause of ``pause_s`` ends a stretch.
* Someone who never pauses (very fast speech, a film with music under it) is cut at the
  quietest moment of the last few seconds once the stretch is ``max_s`` long, so no word is
  ever more than ``max_s`` from being final and nothing is dropped.
* A click or a cough shorter than ``min_speech_s`` is not a stretch.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

RATE = 16000
FRAME = 320  # 20 ms


@dataclass
class Piece:
    """A stretch of speech, or (``final`` False) the part of one heard so far."""

    id: int
    final: bool
    samples: Any  # float32 at 16 kHz
    start: float  # the clock's time of its first sample
    end: float  # ... and of its last


class Segmenter:
    def __init__(
        self,
        *,
        pause_s: float = 0.6,
        max_s: float = 12.0,
        partial_every_s: float = 1.0,
        min_speech_s: float = 0.2,
        floor: float = 0.004,
        partials: bool = True,
    ) -> None:
        self.tune(pause_s, max_s)
        self.every = max(1, round(partial_every_s * RATE / FRAME))
        self.min_speech = max(1, round(min_speech_s * RATE / FRAME))
        self.floor = floor
        self.partials = partials
        self.noise = floor / 2  # the room's quiet, followed slowly (a fan, a hum)
        self._left: Any = None  # samples that didn't fill a frame yet
        self._frames: list[Any] = []  # the stretch being heard
        self._loud: list[float] = []
        self._before: list[Any] = []  # the last few quiet frames, so a first sound isn't clipped
        self._speech = 0  # loud frames in this stretch
        self._quiet = 0  # quiet frames at its end
        self._since_partial = 0
        self._clock = 0.0  # the time of the end of everything fed so far
        self._id = 0

    def tune(self, pause_s: float, max_s: float) -> None:
        """Change how soon a stretch ends (while it runs: captions want short stretches, so a
        translation is never far behind; notes want whole sentences)."""
        self.pause = max(1, round(pause_s * RATE / FRAME))
        self.max = max(self.pause + 1, round(max_s * RATE / FRAME))

    # ------------------------------------------------------------------ in
    def feed(self, samples: Any, end: float) -> list[Piece]:
        """Give it the next chunk (``end``: the time of its last sample). Returns what is
        ready: at most a few pieces, finals in order."""
        import numpy as np

        data = samples if self._left is None else np.concatenate([self._left, samples])
        whole = len(data) // FRAME * FRAME
        self._left = data[whole:] if whole < len(data) else None
        self._clock = end - (len(data) - whole) / RATE
        if not whole:
            return []
        frames = data[:whole].reshape(-1, FRAME)
        loud = np.sqrt((frames * frames).mean(axis=1))
        out: list[Piece] = []
        n = len(frames)
        for i in range(n):
            after = (n - 1 - i) * FRAME / RATE  # how far this frame's end is behind the clock
            piece = self._frame(frames[i], float(loud[i]), self._clock - after)
            if piece is not None:
                out.append(piece)
        return out

    def flush(self) -> list[Piece]:
        """The stream ended: whatever was being said is final."""
        if not self._frames or self._speech < self.min_speech:
            self._reset()
            return []
        return [self._cut(len(self._frames), self._clock)]

    @property
    def speaking(self) -> bool:
        return bool(self._frames)

    # ------------------------------------------------------------------ one frame
    def _frame(self, frame: Any, loud: float, now: float) -> Piece | None:
        threshold = max(self.floor, self.noise * 3.0)
        speech = loud > threshold
        if not speech:  # the quiet between words is the room's own level
            self.noise += (min(loud, self.floor * 4) - self.noise) * 0.02
        if not self._frames:
            if not speech:
                self._before = [*self._before[-9:], frame]  # 200 ms of lead-in
                return None
            self._frames = [*self._before, frame]
            self._loud = [0.0] * len(self._before) + [loud]
            self._before = []
            self._speech, self._quiet, self._since_partial = 1, 0, 0
            return None
        self._frames.append(frame)
        self._loud.append(loud)
        self._since_partial += 1
        if speech:
            self._speech += 1
            self._quiet = 0
        else:
            self._quiet += 1
        if self._quiet >= self.pause:
            if self._speech < self.min_speech:  # a click, a cough: not speech
                self._reset()
                return None
            keep = len(self._frames) - self._quiet + min(self._quiet, 8)  # 160 ms of tail
            piece = self._cut(keep, now - (len(self._frames) - keep) * FRAME / RATE)
            self._reset()
            return piece
        if len(self._frames) >= self.max:
            # They haven't paused: cut where it was quietest in the last 3 seconds.
            look = min(len(self._loud) - 1, round(3.0 * RATE / FRAME))
            tail = self._loud[-look:]
            at = len(self._loud) - look + min(range(len(tail)), key=tail.__getitem__) + 1
            rest_frames, rest_loud = self._frames[at:], self._loud[at:]
            piece = self._cut(at, now - len(rest_frames) * FRAME / RATE)
            self._frames, self._loud = rest_frames, rest_loud
            self._speech = sum(1 for v in rest_loud if v > threshold)
            self._quiet, self._since_partial = 0, 0
            if not self._frames:
                self._reset()
            return piece
        if (
            self.partials
            and self._since_partial >= self.every
            and self._speech >= self.min_speech
            and not self._quiet
        ):
            self._since_partial = 0
            return self._piece(len(self._frames), now, final=False)
        return None

    def _piece(self, frames: int, end: float, *, final: bool) -> Piece:
        import numpy as np

        samples = np.concatenate(self._frames[:frames])
        return Piece(self._id, final, samples, end - len(samples) / RATE, end)

    def _cut(self, frames: int, end: float) -> Piece:
        piece = self._piece(frames, end, final=True)
        self._id += 1
        return piece

    def _reset(self) -> None:
        self._frames, self._loud = [], []
        self._speech = self._quiet = self._since_partial = 0
