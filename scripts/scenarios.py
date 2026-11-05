"""Replay everyday requests on real recorded screens and check Wisp behaves, every time.

Rebuilds the screen Wisp saw in a recorded turn (UI elements, OCR lines, the screenshot), runs
a request through the real chat model, and checks the rules: explanations get a full cue
card that ends with an offer, 3D only when asked, account actions become errands, research
goes to Scout, small talk stays short. Errands, browser jobs and 3D builds are intercepted,
so nothing touches the user's accounts or opens a browser. Speech is off (tts=none).

usage: python scripts/scenarios.py [repeats]
"""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

from otto.config import load_dotenv
from PIL import Image

from wisp.companion.brain import Brain
from wisp.config import WispSettings
from wisp.hub import Hub
from wisp.screen.context import ScreenContext, UIElement, WindowInfo
from wisp.screen.ocr import TextLine

DB = ".wisp/wisp.db"
EL = re.compile(r'^\s*\[(\d+)\] (\w+)(?: "(.*)")? @\((-?\d+),(-?\d+),(\d+)x(\d+)\)')
OCR = re.compile(r'^\[(\d+)\] "(.*)" @\((-?\d+),(-?\d+),(\d+)x(\d+)\)')


def screen_from(turn_id: str) -> ScreenContext:
    c = sqlite3.connect(DB)
    text, app, window, image = c.execute(
        "select screen_text, app, window, image_path from turns where id=?", (turn_id,)
    ).fetchone()
    lines = text.splitlines()
    size = re.search(r"Screen: (\d+)x(\d+)", text)
    w, h = (int(size.group(1)), int(size.group(2))) if size else (1920, 1080)
    shot = Image.open(image).convert("RGB").resize((w, h))
    elements, ocr, in_ocr = [], [], False
    for line in lines:
        if line.startswith("Text read from the pixels"):
            in_ocr = True
            continue
        if in_ocr and (m := OCR.match(line)):
            i, t, x, y, ww, hh = m.groups()
            rect = (int(x), int(y), int(ww), int(hh))
            # Word boxes aren't recorded; spread the line's width over its words.
            words, cx = [], int(x)
            per = max(1, int(ww) // max(1, len(t)))
            for word in t.split():
                words.append((word, (cx, int(y), per * len(word), int(hh))))
                cx += per * (len(word) + 1)
            ocr.append(TextLine(t, rect, words, int(i)))
        elif not in_ocr and (m := EL.match(line)):
            i, role, name, x, y, ww, hh = m.groups()
            elements.append(UIElement(int(i), name or "", role, (int(x), int(y), int(ww), int(hh))))
    ctx = ScreenContext(
        time.time(), WindowInfo(window or "", f"{app}.exe", (0, 0, w, h)), elements, shot
    )
    ctx.text = ocr
    return ctx


class Replay:
    def __init__(self, ctx: ScreenContext) -> None:
        self.ctx = ctx
        self.paused = False

    async def capture(self, pixels: bool = True) -> ScreenContext:
        self.ctx.captured_at = time.time()
        return self.ctx

    def is_excluded(self, window: WindowInfo) -> str | None:
        return None


SCENARIOS = [
    (
        "heart explain",
        "turn_480a31d9",
        "Can you explain this to me? How does the heart actually work?",
        "card",
    ),
    ("heart 3D", "turn_480a31d9", "Show me in 3D how the heart pumps blood.", "3d"),
    (
        "anthropic loss",
        "turn_ac9f763c",
        "How can a company this big lose 80 billion dollars? Explain this post.",
        "card",
    ),
    ("altman post", "turn_49b75541", "What exactly is this post about?", "card"),
    (
        "jet hover",
        "turn_35fba1a3",
        "Can this jet really hover in midair like a helicopter?",
        "visual",
    ),
    ("small talk", "turn_49b75541", "Hey Wisp, how's your day going?", "chat"),
    (
        "meeting",
        "turn_49b75541",
        "Add a meeting with test.person@example.com tomorrow at 3pm called Catch up.",
        "errand",
    ),
    (
        "research",
        "turn_49b75541",
        "Can you research whether Sam Altman really posted that, and tell me?",
        "delegate",
    ),
    ("nba finals", "turn_49b75541", "Who won the NBA Finals last year?", "web"),
    ("bitcoin", "turn_49b75541", "What's the price of bitcoin right now?", "web"),
    (
        "binance hack",
        "turn_f0a93d40",
        "Do you know about the hacking that took place a few days ago, the 6.3 billion dollar one by a North Korean hacker?",
        "news",
    ),
]


async def run_one(name: str, turn_id: str, question: str, expect: str, tmp: Path) -> dict:
    settings = WispSettings(data_dir=tmp / name.replace(" ", "_"))
    settings.voice.tts = "none"
    hub = Hub()
    events: list[dict] = []

    async def record(text: str) -> None:
        events.append(json.loads(text))

    hub.connect(record)
    brain = Brain(settings, hub, screen=Replay(screen_from(turn_id)))
    background: list[str] = []

    async def fake_errand(task: str, user_text: str) -> None:
        background.append(f"errand: {task[:70]}")

    async def fake_build(args: dict) -> None:
        background.append(f"3d: {args.get('subject')}")

    async def fake_start(agent, task, **kw):
        background.append(f"delegate {agent}: {task[:60]}")

        class J:
            id, agent_name = "job_x", str(agent).title()

        return J()

    brain._run_errand = fake_errand  # type: ignore[method-assign]
    # Pretend an account is connected (without touching the real one), so errands can start.
    from otto.llm.base import ToolSpec

    from wisp.connectors import Tool

    async def _noop(args):
        return "ok"

    dummy = Tool(
        ToolSpec("calendar_list", "List calendar events.", {"type": "object", "properties": {}}),
        _noop,
        info=True,
    )
    brain.connectors.tools = lambda: [dummy]  # type: ignore[method-assign]
    brain._build_from_brief = fake_build  # type: ignore[method-assign]
    brain.jobs.start = fake_start  # type: ignore[method-assign]
    started = time.monotonic()
    await brain.turn(question)
    await asyncio.sleep(0.3)
    took = time.monotonic() - started
    tools = [c["tool"] for c in brain._calls]
    said = " ".join(e["text"] for e in events if e["type"] == "say")
    cards = [e["card"] for e in events if e["type"] == "notes"]
    card = cards[-1] if cards else None  # the finished card (it fills in point by point)
    problems = []
    three_d = any(t in tools for t in ("build_3d_model", "explain_in_3d", "edit_3d_model"))
    if not said.strip():
        problems.append("said nothing")
    if re.search(r"there you go|i have done this|\{\s*\"", said, re.I):
        problems.append("filler or raw text")
    if expect == "card":
        if card is None:
            problems.append("no cue card")
        else:
            n = len(card["items"])
            unaimed = sum(1 for it in card["items"] if it["mark"] != "none" and not it["rect"])
            if n < 4:
                problems.append(f"thin card ({n})")
            if not card.get("closing"):
                problems.append("no closing")
            if unaimed:
                problems.append(f"{unaimed} marks unaimed")
            if any(not it.get("say") for it in card["items"]):
                problems.append("point without speech")
    if expect != "3d" and three_d:
        problems.append("3D not asked for")
    if expect == "3d" and not any(b.startswith("3d") for b in background):
        problems.append("no 3D build")
    if expect == "visual" and not ({"sketch_diagram", "explain_on_screen"} & set(tools)):
        problems.append("no visual")
    if expect == "chat" and (len(said) > 260 or set(tools) - {"say"}):
        problems.append(f"chat not short ({len(said)} chars, {set(tools) - {'say'}})")
    if expect == "errand":
        if not any(b.startswith("errand") for b in background):
            problems.append("no errand")
        if "calendar_add" in tools:
            problems.append("booked directly")
    if (
        expect == "delegate"
        and not any(b.startswith("delegate") or b.startswith("web") for b in background)
        and "web_search" not in tools
    ):
        problems.append("not researched")
    if expect == "web":
        # Looking it up, or knowing it for sure (a settled fact like last year's champion), are both fine.
        if "web_search" not in tools and not (name.startswith("nba") and "Thunder" in said):
            problems.append("didn't look it up")
        if re.search(r"can.?t (browse|search|access the internet)", said, re.I):
            problems.append("said it can't browse")
    if expect == "news":
        if "web_search" not in tools:
            problems.append("didn't look it up")
        if not re.search(r"FBI|North Korea|Lazarus|Chainalysis", said):
            problems.append("didn't find the story")
        if re.search(r"can.?t (confirm|verify|find)", said, re.I):
            problems.append("said it couldn't confirm")
    from wisp.companion.brain import drop_false_promises

    if drop_false_promises(said.split(". ")[-1])[1]:
        problems.append("ends promising a search")
    if re.search(r"M4|model is finished", said):
        problems.append("mentioned an old job")
    if "807" in said:
        problems.append("OCR misread repeated")
    return {
        "name": name,
        "ok": not problems,
        "problems": problems,
        "tools": tools,
        "secs": round(took, 1),
        "cost": round(brain.turns.spent_today(), 4),
        "said": said[:220],
        "background": background,
    }


async def main() -> None:
    load_dotenv()
    repeats = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    tmp = Path(tempfile.mkdtemp(prefix="wisp_scen_"))
    results = []
    for r in range(repeats):
        for name, turn_id, q, expect in SCENARIOS:
            res = await run_one(f"{name} {r}", turn_id, q, expect, tmp)
            results.append(res)
            mark = "PASS" if res["ok"] else "FAIL"
            print(
                f"{mark} {res['name']:18s} {res['secs']:5.1f}s ${res['cost']:.4f} {res['tools']} {res['problems']}"
            )
            if not res["ok"]:
                print("      said:", res["said"])
                print("      background:", res["background"])
    passed = sum(r["ok"] for r in results)
    print(f"\n{passed}/{len(results)} passed, total ${sum(r['cost'] for r in results):.3f}")


if __name__ == "__main__":
    asyncio.run(main())
