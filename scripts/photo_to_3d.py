"""Make a 3D model from a picture file with the real builder model, and save what happened.

usage: python scripts/photo_to_3d.py out_dir picture [picture ...] [--want "the chair"]

For each picture, in out_dir/<name>/: model.json (the model, as the overlay takes it),
seen.json (what was read and measured), check.jpg (the photo beside the plain picture the
model was checked with), result.json (seconds and cost of each step, what the comparison
found). Prints one line a picture. Live model calls: about 5-10 cents a picture.
"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import asdict
from pathlib import Path

from otto.config import load_dotenv

from pyxi.config import PyxiSettings
from pyxi.scenes import photo
from pyxi.stream_llm import StreamingChat

BUILD: dict = {}  # "llm": a builder that thinks harder for the modelling step (--effort)


async def one(llm: StreamingChat, path: Path, out: Path, want: str) -> dict:
    folder = out / path.stem
    folder.mkdir(parents=True, exist_ok=True)
    try:
        picture = photo.load_picture(path)
    except photo.PictureError as exc:
        print(f"{path.name}: turned away: {exc.say}")
        return {"name": path.stem, "turned_away": exc.say}
    first_part = {"t": None}

    async def parts(_scene) -> None:
        if first_part["t"] is None:
            first_part["t"] = asyncio.get_event_loop().time() - started

    started = asyncio.get_event_loop().time()
    result = await photo.model_from_picture(llm, picture, want=want, on_parts=parts, build_llm=BUILD.get("llm"))
    seen = result.seen
    seen_out = {k: v for k, v in asdict(seen).items() if k != "outline"}
    seen_out["outline"] = {"trusted": seen.outline.trusted, "why": seen.outline.why, "box": [float(v) for v in seen.outline.box]} if seen.outline else None
    (folder / "seen.json").write_text(json.dumps(seen_out, indent=1, default=str), encoding="utf-8")
    summary = {
        "name": path.stem,
        "kind": seen.kind,
        "subject": seen.subject,
        "objects": seen.objects,
        "seconds": round(result.seconds, 1),
        "first_part_s": round(first_part["t"], 1) if first_part["t"] else None,
        "cost_usd": round(result.cost_usd, 4),
        "timings": {k: round(v, 1) for k, v in result.timings.items()},
        "rounds": result.rounds,
        "parts": len(result.model.parts) if result.model else 0,
        "shapes": sorted({p.shape for p in result.model.parts}) if result.model else [],
        "before": result.first_match.words() if result.first_match else None,
        "after": result.match.words() if result.match else None,
        "view": result.match.view if result.match else None,
        "checks": result.checks,
        "notes": picture.notes,
    }
    (folder / "result.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    if result.model is not None:
        (folder / "model.json").write_text(json.dumps(result.model.model_dump(mode="json")), encoding="utf-8")
        if result.shot is not None:
            (folder / "check.jpg").write_bytes(photo.sheet(picture, result.shot))
    print(
        f"{path.stem}: {seen.kind} {seen.subject!r} parts={summary['parts']} {summary['seconds']}s "
        f"${summary['cost_usd']} rounds={result.rounds} | {summary['timings']} | {summary['after']}",
        flush=True,
    )
    return summary


async def main() -> None:
    load_dotenv()  # the same .env the app uses (the key is never printed)
    args = sys.argv[1:]
    want = ""
    if "--want" in args:
        i = args.index("--want")
        want = args[i + 1]
        del args[i : i + 2]
    effort = ""
    if "--effort" in args:  # how hard the builder thinks while modelling (the app's own default if not given)
        i = args.index("--effort")
        effort = args[i + 1]
        del args[i : i + 2]
    out = Path(args[0])
    settings = PyxiSettings()
    llm = StreamingChat(settings.model_3d or settings.model, reasoning={"effort": settings.effort_3d})
    if effort:
        BUILD["llm"] = StreamingChat(settings.model_3d or settings.model, reasoning={"effort": effort})
    rows = []
    for p in args[1:]:
        try:
            rows.append(await one(llm, Path(p), out, want))
        except Exception as exc:  # one bad picture must not stop the rest
            print(f"{Path(p).name}: failed: {type(exc).__name__}: {exc}", flush=True)
    done = [r for r in rows if r.get("cost_usd")]
    if done:
        print(f"total ${sum(r['cost_usd'] for r in done):.3f} over {len(done)} pictures; mean {sum(r['seconds'] for r in done) / len(done):.1f}s")


asyncio.run(main())
