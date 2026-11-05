"""Run one real browser job through Wisp's job runner (hidden browser) and report every step.

usage: python scripts/agent_check.py [agent] "<task>" [start_url]
Prints each step, the result, the cost and the time. Never prints the API key.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time

from otto.config import load_dotenv

from wisp.companion.brain import Brain
from wisp.config import WispSettings
from wisp.hub import Hub


async def main() -> None:
    load_dotenv()
    agent = sys.argv[1] if len(sys.argv) > 1 else "scout"
    task = (
        sys.argv[2]
        if len(sys.argv) > 2
        else "Find the main heading on the page and its first sentence."
    )
    start_url = sys.argv[3] if len(sys.argv) > 3 else "https://example.com"
    hub = Hub()

    async def show(text: str) -> None:
        import json

        e = json.loads(text)
        if e["type"] == "job_event":
            d = e.get("data", {})
            if e["event"] == "step_finished":
                s = d.get("step")
                print(f"  step {s}: ${d.get('budget', {}).get('cost_usd', 0):.4f}")
            elif e["event"] in ("action", "tool_call", "thought", "status"):
                print(f"  {e['event']}: {str(d)[:160]}")
        elif e["type"] in ("confirm", "ask"):
            print("  NEEDS YOU:", e.get("action") or e.get("question"))

    hub.connect(show)
    brain = Brain(WispSettings(), hub)
    brain.jobs.headed = bool(
        os.getenv("HEADED")
    )  # hidden unless asked: don't pop windows on screen
    print("agents:", [(a.name, a.role) for a in brain.registry.all()])
    started = time.monotonic()
    job = await brain.jobs.start(agent, task, start_url=start_url)
    job = await brain.jobs.wait(job.id)
    print(
        f"status={job.status} steps={job.steps} cost=${job.cost_usd:.4f} time={time.monotonic() - started:.1f}s"
    )
    print("summary:", job.summary)
    if job.data:
        print("data:", str(job.data)[:400])


asyncio.run(main())
