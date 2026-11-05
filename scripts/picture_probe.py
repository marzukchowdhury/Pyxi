"""Explain a recorded picture the way Blip would, and draw where its tags landed.

Replays a real screen through the real brain and models (speech off), times when the opener,
the survey and each tag arrive, and saves the screenshot with the tags, leaders and routes
drawn on, to .blip/crops/picture_<turn>.png.

usage: python scripts/picture_probe.py [turn_id] [question]
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path

from otto.config import load_dotenv
from PIL import ImageDraw

from blip.companion.brain import Brain
from blip.config import BlipSettings
from blip.hub import Hub

sys.path.insert(0, str(Path(__file__).parent))
from scenarios import Replay, screen_from


async def main() -> None:
    load_dotenv()
    turn_id = sys.argv[1] if len(sys.argv) > 1 else "turn_815fdfc6"
    question = (
        sys.argv[2] if len(sys.argv) > 2 else "Hey Blip, can you explain this diagram of the heart?"
    )
    tmp = Path(tempfile.mkdtemp())
    settings = BlipSettings(data_dir=tmp)
    settings.voice.tts = "none"
    hub = Hub()
    t0 = time.monotonic()
    events: list[tuple[float, dict]] = []

    async def record(text: str) -> None:
        events.append((time.monotonic() - t0, json.loads(text)))

    hub.connect(record)
    ctx = screen_from(turn_id)
    brain = Brain(settings, hub, screen=Replay(ctx))
    await brain.turn(question)
    await asyncio.sleep(0.3)
    for t, e in events:
        if e["type"] == "say":
            print(f"{t:5.1f}s say: {e['text'][:110]}")
        elif e["type"] == "notes":
            c = e["card"]
            print(f"{t:5.1f}s card ({c.get('style')}): {[it['text'] for it in c['items']]}")
    print("tools:", [c["tool"] for c in brain._calls], f"| cost ${brain._usage.cost_usd:.4f}")
    cards = [e["card"] for _, e in events if e["type"] == "notes"]
    if not cards:
        return
    card = cards[-1]
    print("closing:", card.get("closing"))
    img = ctx.screenshot.copy()
    d = ImageDraw.Draw(img)
    for it in card["items"]:
        for a, b in zip(it.get("trace") or [], (it.get("trace") or [])[1:], strict=False):
            d.line([tuple(a), tuple(b)], fill=(255, 60, 60), width=4)
        if it.get("at") and it.get("tag"):
            d.line([tuple(it["tag"]), tuple(it["at"])], fill=(255, 60, 60), width=2)
            x, y = it["at"]
            d.ellipse([x - 5, y - 5, x + 5, y + 5], fill=(255, 60, 60), outline="white")
            tx, ty = it["tag"]
            w = len(it["text"]) * 7 + 22
            d.rounded_rectangle([tx - w / 2, ty - 12, tx + w / 2, ty + 12], 6, fill=(229, 56, 59))
            d.text((tx - w / 2 + 10, ty - 7), it["text"], fill="white")
        print(f"  {it['n']}. {it['text']!r:24} at {it.get('at')} | {it.get('say')}")
    Path(f".blip/crops/picture_{turn_id}.json").write_text(json.dumps(card))
    out = Path(f".blip/crops/picture_{turn_id}.png")
    img.save(out)
    print("saved", out)


if __name__ == "__main__":
    asyncio.run(main())
