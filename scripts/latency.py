"""Where does the time go in a voice turn? Measures each stage, end to end, for real.

A spoken question (made with Pyxi's own voice engine) goes through the real pipeline:
speech-to-text, the screen (a recorded one, for repeatable runs), the chat model (streamed),
and the voice. Prints when each stage finished, counted from the moment the user let go of
the talk key. usage: python scripts/latency.py [runs]
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path

from otto.config import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
from scenarios import Replay, screen_from

from pyxi.companion.brain import Brain
from pyxi.config import PyxiSettings
from pyxi.hub import Hub

QUESTIONS_ALL = [
    ("explain post", "turn_ac9f763c", "Can you explain what this post is about?"),
    ("small talk", "turn_ac9f763c", "Hey Pyxi, how is your day going?"),
]
_args = [a for a in sys.argv[1:] if not a.startswith("--")]
QUESTIONS = [q for q in QUESTIONS_ALL if len(_args) < 2 or q[0].startswith(_args[1])]


async def spoken(brain: Brain, text: str) -> bytes:
    """The question as 16-bit WAV, the way the microphone would deliver it."""
    speech = await brain.tts.synthesize(text)
    if speech.wav:
        return speech.wav
    raise SystemExit("no TTS audio")


async def run(name: str, turn_id: str, question: str, tmp: Path) -> dict[str, float]:
    class Settings(PyxiSettings):
        @property
        def models_dir(self) -> Path:  # Pyxi's real voice models; everything else stays temporary
            return Path(".pyxi/models").resolve()

    settings = Settings(data_dir=tmp / name.replace(" ", "_"))
    settings.voice.voice = "am_puck"  # a different voice from Pyxi's, like a real user
    hub = Hub()
    marks: dict[str, float] = {}
    t0 = 0.0

    async def record(text: str) -> None:
        e = json.loads(text)
        now = time.monotonic() - t0
        if e["type"] == "transcript":
            marks.setdefault("heard", now)
        elif e["type"] == "state" and e.get("state") == "thinking":
            marks.setdefault("thinking", now)
        elif e["type"] == "say":
            marks.setdefault("first voice ready", now)
            marks["last voice ready"] = now
        elif e["type"] == "notes":
            marks.setdefault("cue card", now)

    hub.connect(record)
    brain = Brain(settings, hub, screen=Replay(screen_from(turn_id)))
    if "--trace" in sys.argv:
        real_speak = brain._speak

        def traced(text, **kw):
            print(f"    speak at {time.monotonic() - t0:.2f}s: {text[:70]!r}", flush=True)
            return real_speak(text, **kw)

        brain._speak = traced  # type: ignore[method-assign]
        real_act = brain._act

        async def traced_act(name, args):
            print(f"    tool {name} at {time.monotonic() - t0:.2f}s", flush=True)
            return await real_act(name, args)

        brain._act = traced_act  # type: ignore[method-assign]
    audio = await spoken(brain, question)
    settings.voice.voice = "af_heart"
    await brain.stt.warm_up("en")  # warm, as in the running app
    t0 = time.monotonic()
    await brain.hear(audio)
    await asyncio.sleep(0.2)
    marks["turn done"] = time.monotonic() - t0
    return marks


async def main() -> None:
    load_dotenv()
    runs = int(_args[0]) if _args else 2
    tmp = Path(tempfile.mkdtemp(prefix="pyxi_lat_"))
    for name, turn_id, q in QUESTIONS:
        for r in range(runs):
            m = await asyncio.wait_for(run(f"{name} {r}", turn_id, q, tmp), 120)
            print(f"{name:13s} " + "  ".join(f"{k}={v:.2f}s" for k, v in m.items()), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
