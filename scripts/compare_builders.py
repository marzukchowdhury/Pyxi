"""Build the same 3D model with several builder models and compare time, cost and validity.

usage: python scripts/compare_builders.py "<brief>" model1 model2 ...
Writes each model to .wisp/builds/<model>.json for viewing in the overlay preview.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from otto.llm import make_provider

from wisp.config import WispSettings
from wisp.llm import otto_settings
from wisp.scenes.builder import build_model


async def main() -> None:
    from otto.config import load_dotenv

    load_dotenv()  # the same .env the app uses (the key is never printed)
    brief, models = sys.argv[1], sys.argv[2:]
    settings = WispSettings()
    out = Path(".wisp/builds")
    out.mkdir(parents=True, exist_ok=True)

    async def one(model: str):
        name, _, effort = model.partition("@")
        model = name
        llm = make_provider(otto_settings(settings, model=model, effort=effort or "medium"))
        r = await build_model(llm, brief)
        name = model.split("/")[-1] + (f"@{effort}" if effort else "")
        if r.model:
            (out / f"{name}.json").write_text(json.dumps(r.model.model_dump(mode="json")))
        parts = len(r.model.parts) if r.model else 0
        extrudes = sum(p.shape == "extrude" for p in r.model.parts) if r.model else 0
        print(
            f"{name}: ok={bool(r.model)} parts={parts} extrudes={extrudes} "
            f"cost=${r.cost_usd:.4f} time={r.seconds:.1f}s attempts={r.attempts} {r.errors[:1]}"
        )

    await asyncio.gather(*(one(m) for m in models))


asyncio.run(main())
