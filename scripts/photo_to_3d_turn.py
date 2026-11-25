"""One whole turn with the real models: "make a 3D model of this" said at a made-up screen (a
plain shop page with one picture file on it), or with the picture in the chat box. Speech is
off. Prints what the chat model called and said, and when each thing reached the overlay.

usage: python scripts/photo_to_3d_turn.py picture.jpg ["what they say"] [--chat-box] [--no-picture]

Live model calls (the chat model once or twice, the 3D builder three times): about ten cents.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path

from otto.config import load_dotenv
from PIL import Image, ImageDraw

from pyxi.companion.brain import Brain
from pyxi.config import PyxiSettings
from pyxi.hub import Hub
from pyxi.scenes import photo
from pyxi.screen import ScreenContext, UIElement, WindowInfo


class Still:
    """A screen that is always the same made-up page."""

    def __init__(self, ctx: ScreenContext) -> None:
        self.ctx, self.paused = ctx, False

    async def capture(self, pixels: bool = True, **_) -> ScreenContext:
        self.ctx.captured_at = time.time()
        return self.ctx

    def is_excluded(self, window: WindowInfo) -> str | None:
        return None


def shop_page(picture: Path | None) -> ScreenContext:
    shot = Image.new("RGB", (1600, 900), (250, 250, 252))
    d = ImageDraw.Draw(shot)
    d.rectangle([0, 0, 1600, 64], fill=(38, 42, 54))
    d.text((24, 24), "Corner Shop  |  Home  |  Kitchen  |  Garden", fill=(235, 235, 240))
    els = [UIElement(1, "Corner Shop - Browser", "Pane", (0, 0, 1600, 900), 0)]
    if picture is not None:
        img = Image.open(picture).convert("RGB")
        img.thumbnail((640, 560))
        shot.paste(img, (120, 160))
        els.append(UIElement(2, "Product photo", "Image", (120, 160, img.width, img.height), 1, 1))
        d.text((860, 200), "A thing for sale", fill=(20, 20, 30))
        d.text((860, 240), "In stock. Free delivery over 30.", fill=(90, 90, 100))
        els.append(UIElement(3, "Add to basket", "Button", (860, 320, 220, 52), 1, 1))
        d.rectangle([860, 320, 1080, 372], fill=(40, 110, 220))
    else:
        d.text((120, 200), "Your basket is empty.", fill=(20, 20, 30))
    return ScreenContext(time.time(), WindowInfo("Corner Shop - Browser", "browser.exe", (0, 0, 1600, 900)), els, shot)


async def main() -> None:
    load_dotenv()  # the same .env the app uses (the key is never printed)
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    in_chat, none = "--chat-box" in sys.argv, "--no-picture" in sys.argv
    file = Path(args[0])
    words = args[1] if len(args) > 1 else "Make a 3D model of this."
    settings = PyxiSettings(data_dir=Path(tempfile.mkdtemp()))
    settings.voice.tts = "none"
    hub = Hub()
    started = time.monotonic()
    events: list[tuple[float, dict]] = []

    async def record(text: str) -> None:
        events.append((time.monotonic() - started, json.loads(text)))

    hub.connect(record)
    brain = Brain(settings, hub, screen=Still(shop_page(None if (in_chat or none) else file)))
    attached = photo.load_picture(file, kind="attached") if in_chat else None
    await brain.turn(words, picture=attached)
    for _ in range(900):  # the build goes on after the turn has answered
        await asyncio.sleep(0.1)
        done = [e for _, e in events if e["type"] == "object" and e.get("restart")]
        if done or (time.monotonic() - started > 12 and not brain._bg):
            break
    await asyncio.sleep(0.5)
    for t, e in events:
        if e["type"] == "say":
            print(f"{t:5.1f}s say: {e['text'][:160]}")
        elif e["type"] == "object":
            s = e["scene"]
            print(f"{t:5.1f}s model: {s['title']!r} {len(s['parts'])} parts, photo beside it: {bool(s.get('photo'))}, plays: {e['restart']}")
        elif e["type"] == "job_event" and e.get("event") == "status":
            print(f"{t:5.1f}s notch: {e['data'].get('message')}")
    print("tools:", [(c["tool"], {k: v for k, v in (c.get("args") or {}).items() if k != "text"}) for c in brain._calls])
    print(f"spent this turn: ${brain.turns.spent_today():.3f}")


asyncio.run(main())
