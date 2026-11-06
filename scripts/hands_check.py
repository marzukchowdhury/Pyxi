"""Run one real desktop task through Pyxi's hands, on this PC, and report each step.

Uses the real screen reader, the real model and real mouse and keyboard input, exactly as the
app does after the user presses Allow. Risky steps (send, buy, delete) are always declined
here, so a test can never do anything irreversible. Moving the mouse or pressing Esc stops it.

usage: python scripts/hands_check.py "Open Notepad and type hello"
"""

from __future__ import annotations

import asyncio
import ctypes
import sys
import time

from otto.config import load_dotenv

from pyxi.companion.operator import Operator
from pyxi.config import PyxiSettings
from pyxi.screen import ScreenReader
from pyxi.screen.context import enable_dpi_awareness
from pyxi.stream_llm import StreamingChat


async def main() -> None:
    load_dotenv()
    import logging

    logging.basicConfig(level=logging.WARNING)
    logging.getLogger("pyxi.companion.operator").setLevel(logging.INFO)
    enable_dpi_awareness()
    sys.argv = [a for a in sys.argv if a != "--come-back"] + (
        ["--come-back"] if "--come-back" in sys.argv else []
    )
    task = sys.argv[1]
    settings = PyxiSettings()
    reader = ScreenReader(settings.screen)
    model = (
        sys.argv[2]
        if len(sys.argv) > 2 and sys.argv[2] != "--come-back"
        else settings.model_hands or "anthropic/claude-haiku-5.5"
    )
    t0 = time.monotonic()

    async def confirm(action: str, reason: str) -> bool:
        print(f"  [{time.monotonic() - t0:5.1f}s] ASKED (declined in tests): {action}")
        return False

    async def report(doing: str, step: int) -> None:
        print(f"  [{time.monotonic() - t0:5.1f}s] step {step}: {doing}")

    op = Operator(
        StreamingChat(model),
        lambda: reader.capture(pixels=True, follow_mouse=False),
        confirm,
        report,
        image_width=settings.screen.model_image_width,
        strong_llm=StreamingChat(settings.model_hands_strong)
        if settings.model_hands_strong
        else None,
    )
    window = ctypes.windll.user32.GetForegroundWindow()
    out = await op.run(task, window, come_back="--come-back" in sys.argv)
    took = time.monotonic() - t0
    for s in out.steps:
        print(f"  {s.n}. {s.actions}" + (f"  -> {s.note}" if s.note else ""))
    cost = op.usage.cost_usd if op.usage else 0
    print(
        f"{'OK' if out.ok else 'NOT DONE'} in {took:.1f}s, {len(out.steps)} looks, ${cost:.4f}: {out.summary}"
    )


if __name__ == "__main__":
    asyncio.run(main())
