"""Record one take for the film: type a request to the running Pyxi and film the real screen.

    .venv/Scripts/python.exe website/tools/film_take.py <name> "<request>" [seconds] [--answer allow]

Pyxi must be running from this checkout with a launch key we know (the key file is in
website/.shots/film2/, git-ignored) and PYXI_SHOW_OVERLAY_IN_CAPTURE=1 so its overlay is filmed.
Writes website/.shots/film2/takes/<name>/: the frames, when each was taken, and everything Pyxi
sent while it worked (what it said, cards, requests for an OK), so the cut can be checked.
The request is typed, not spoken. "--answer allow" presses Allow on the first card that asks.
"""

from __future__ import annotations

import asyncio
import base64
import json
import sys
import threading
import time
from pathlib import Path

import mss
import mss.tools
import websockets
from PIL import Image

FILM = Path(__file__).resolve().parents[1] / ".shots" / "film2"
name, request = sys.argv[1], sys.argv[2]
seconds = float(sys.argv[3]) if len(sys.argv) > 3 and not sys.argv[3].startswith("--") else 30.0
answer = sys.argv[sys.argv.index("--answer") + 1] if "--answer" in sys.argv else None
out = FILM / "takes" / name
out.mkdir(parents=True, exist_ok=True)
for old in out.glob("*.jpg"):
    old.unlink()

stop = threading.Event()
stamps: list[float] = []


def film():
    """The primary monitor, as fast as it comes, as JPEGs."""
    with mss.MSS() as grab:
        # the main screen (monitors[1] is only the first one listed, which may be a side screen)
        mon = next((m for m in grab.monitors[1:] if m.get("is_primary")), grab.monitors[1])
        nxt = time.time()
        while not stop.is_set():
            shot = grab.grab(mon)
            stamps.append(time.time())
            Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX").save(out / f"{len(stamps) - 1:06d}.jpg", quality=90)
            nxt += 1 / 30
            time.sleep(max(0, nxt - time.time()))


async def main():
    key = (FILM / "launch.key").read_text().strip()
    events = []
    t0 = time.time()
    cam = threading.Thread(target=film, daemon=True)
    cam.start()
    async with websockets.connect(f"ws://127.0.0.1:8766/ws?key={key}", max_size=None) as ws:
        await asyncio.sleep(1.5)
        asked = time.time()
        await ws.send(json.dumps({"type": "text", "text": request}))
        answered = False
        while time.time() - asked < seconds:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            msg = json.loads(raw)
            kind = msg.get("type")
            # sound comes as base64: keep it as a file, not in the log
            for field in ("data", "audio", "wav"):
                if isinstance(msg.get(field), str) and len(msg[field]) > 2000:
                    (out / f"audio-{len(events):04d}.bin").write_bytes(base64.b64decode(msg[field]))
                    msg[field] = f"<{len(msg[field])} chars saved>"
            events.append({"t": round(time.time() - t0, 2), **msg})
            if answer and not answered and kind in ("request", "approval", "ask") and msg.get("id"):
                await asyncio.sleep(2.2)  # long enough for the card to be read on film
                await ws.send(json.dumps({"type": "answer", "id": msg["id"], "answer": answer}))
                answered = True
    stop.set()
    cam.join()
    (out / "take.json").write_text(json.dumps({"request": request, "t0": t0, "asked": asked - t0, "stamps": [s - t0 for s in stamps], "events": events}, default=str), encoding="utf-8")
    kinds = {}
    for e in events:
        kinds[e.get("type")] = kinds.get(e.get("type"), 0) + 1
    print(f"{name}: {len(stamps)} frames in {stamps[-1] - stamps[0]:.1f} s ({len(stamps) / (stamps[-1] - stamps[0]):.0f} a second); events {kinds}")
    for e in events:
        text = e.get("text") or e.get("say") or e.get("title")
        if text and e.get("type") not in ("hello",):
            print(f"  {e['t']:6.1f} {e.get('type'):<14} {str(text)[:110]}")


asyncio.run(main())
