"""What would Wisp do with "my desktop is a mess, organise it"? Runs the real brain and model,
stops at the plan (nothing is moved), and prints it. usage: python scripts/tidy_dry_run.py"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

from otto.config import load_dotenv

from wisp.companion.brain import Brain
from wisp.config import WispSettings
from wisp.hub import Hub

sys.path.insert(0, str(Path(__file__).parent))
from scenarios import Replay, screen_from


async def main() -> None:
    load_dotenv()
    settings = WispSettings(data_dir=Path(tempfile.mkdtemp()))
    settings.voice.tts = "none"
    hub = Hub()
    said: list[str] = []

    async def record(text: str) -> None:
        e = json.loads(text)
        if e["type"] == "say":
            said.append(e["text"])

    hub.connect(record)
    brain = Brain(
        settings,
        hub,
        screen=Replay(screen_from(sys.argv[1] if len(sys.argv) > 1 else "turn_49b75541")),
    )
    plans = []

    async def caught(plan, log_dir):  # stop here: never apply in a dry run
        plans.append(plan)

    brain._tidy_approved = caught  # type: ignore[method-assign]
    await brain.turn("My desktop is so messed up, can you organise it for me?")
    await asyncio.sleep(0.3)
    print("SAID:", " ".join(said))
    print("TOOLS:", [c["tool"] for c in brain._calls], f"${brain._usage.cost_usd:.4f}")
    for plan in plans:
        # Saved, so what gets approved is exactly what moves (scripts/tidy_apply.py).
        Path(".wisp/tidy_plan.json").write_text(
            json.dumps({"folder": str(plan.folder), "moves": plan.moves}, indent=1)
        )
        print(plan.summary())
        groups: dict[str, list[str]] = {}
        for name, into in plan.moves:
            groups.setdefault(into, []).append(name)
        for into, names in groups.items():
            print(
                f"  {into}/ ({len(names)}): {', '.join(names)}"
            )
        if plan.problems:
            print("  problems:", plan.problems[:5])


if __name__ == "__main__":
    asyncio.run(main())
