"""Where does the time go before a 3D model is on screen? One real turn per kind of 3D request.

Typed questions go through the real brain (chat model streamed, the 3D builder, the voice), with
a recorded screen. For each request it prints, counted from the moment the question was asked:

    args_start   the 3D tool call's first argument bytes arrive (the model has decided)
    args_done    the tool call has fully arrived (the long JSON)
    built        the builder's model is back (build_3d_model briefs only)
    shown        the model or scene is sent to the screen
plus how long validation took, the output tokens, and the cost. The first frame on screen is
measured separately in the browser (scripts/first_frame_3d.py).

usage: python scripts/latency_3d.py [build edit explain gallery]   (costs a few cents a run)
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

import pyxi.companion.brain as brain_mod
from pyxi.companion.brain import Brain
from pyxi.config import PyxiSettings
from pyxi.hub import Hub

SCREEN = "turn_ac9f763c"
REQUESTS = [
    ("build", "build_3d_model", "Show me a jet engine in 3D, how the air goes through it."),
    ("edit", "edit_3d_model", "Show the inside of it and make the fan blades gold."),
    ("explain", "explain_in_3d", "Explain in 3D how a DNS lookup works, step by step."),
    ("gallery", "show_scene", "Play your ejection seat 3D model from the gallery."),
    # A big edit: new parts and a new explanation (the kind that came back as broken JSON).
    # A subject the real-model library has: should be explained on the real Saturn V.
    ("library", "build_3d_model", "Show me in 3D how the Saturn V's stages separate on the way to the Moon."),
    ("bigedit", "edit_3d_model", "Change the model on screen: add a second seat behind the pilot for a back-seater, paint the jet "
     "dark blue, and replace the explanation with four steps on how the parachute is packed and opens."),
]


async def main() -> None:
    load_dotenv()
    import logging

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    wanted = set(sys.argv[1:])
    tmp = Path(tempfile.mkdtemp(prefix="pyxi_3d_lat_"))

    class Settings(PyxiSettings):
        @property
        def models_dir(self) -> Path:
            return Path(".pyxi/models").resolve()

    settings = Settings(data_dir=tmp)
    hub = Hub()
    marks: dict[str, float] = {}
    info: dict[str, object] = {}
    t0 = 0.0

    def mark(key: str) -> None:
        marks.setdefault(key, time.monotonic() - t0)

    async def record(text: str) -> None:
        e = json.loads(text)
        if e["type"] in ("object", "scene"):
            mark("shown")
            marks["shown_last"] = time.monotonic() - t0
            info["parts"] = len(e["scene"].get("parts", e["scene"].get("nodes", [])))
            info.setdefault("first_parts", info["parts"])
            if e["type"] == "object":
                (tmp / "last_object.json").write_text(json.dumps(e["scene"]), encoding="utf-8")
            info["emits"] = int(info.get("emits", 0)) + 1

    hub.connect(record)
    brain = Brain(settings, hub, screen=Replay(screen_from(SCREEN)))

    # The chat model's stream: when each 3D tool's arguments start and finish arriving.
    real_complete = brain.llm.complete

    async def complete(**kw):
        on_partial = kw.get("on_partial")
        target = info.get("tool")

        async def partial(i, name, raw):
            if name == target:
                mark("args_start")
                info["arg_chars"] = len(raw)
            if on_partial:
                await on_partial(i, name, raw)

        if on_partial is not None:
            kw["on_partial"] = partial
        r = await real_complete(**kw)
        if any(c.name == target for c in r.tool_calls):
            mark("args_done")
        info["chat_out_tokens"] = int(info.get("chat_out_tokens", 0)) + r.usage.output_tokens
        return r

    brain.llm.complete = complete  # type: ignore[method-assign]

    # Keep the raw arguments of any call that doesn't parse, to see how it broke.
    import pyxi.stream_llm as sl

    real_parse = sl._parse

    def parse(slot, i):
        call = real_parse(slot, i)
        if call is not None and call.arguments.get("__unparseable__"):
            out = tmp / f"unparseable_{slot['name']}_{i}.txt"
            out.write_text(slot["args"], encoding="utf-8")
            print(f"    raw arguments kept in {out}", flush=True)
        return call

    sl._parse = parse

    real_build = brain_mod.build_model

    async def build(llm, brief, context="", on_parts=None):
        s = time.monotonic()
        real_llm_complete = llm.complete

        async def timed(*, on_partial=None, **kw):
            inner = on_partial

            async def partial(i, name, raw):
                mark("builder_json_start")
                info["builder_chars"] = len(raw)
                if '"parts"' in raw:
                    mark("builder_parts_start")
                if inner:
                    await inner(i, name, raw)

            r = await real_llm_complete(**kw, on_partial=partial if inner else None)
            info["builder_out_tokens"] = r.usage.output_tokens
            return r

        llm.complete = timed
        r = await real_build(llm, brief, context, on_parts=on_parts)
        llm.complete = real_llm_complete
        mark("built")
        info["builder_s"] = round(time.monotonic() - s, 2)
        info["builder_cost"] = round(r.cost_usd, 4)
        info["builder_attempts"] = r.attempts
        return r

    brain_mod.build_model = build

    for name, tool, question in REQUESTS:
        if wanted and name not in wanted:
            continue
        marks.clear()
        info.clear()
        info["tool"] = tool
        t0 = time.monotonic()
        await brain.turn(question)
        # A brief's model is built in the background after the turn: wait for it.
        for _ in range(900):
            done = "built" in marks if tool == "build_3d_model" else "shown" in marks
            if done:
                break
            await asyncio.sleep(0.1)
        await asyncio.sleep(0.3)
        tools = [c["tool"] for c in brain._calls]
        cost = round(brain._usage.cost_usd, 4)
        line = "  ".join(f"{k}={v:.2f}s" for k, v in sorted(marks.items(), key=lambda kv: kv[1]))
        extra = {k: v for k, v in info.items() if k != "tool"}
        if (tmp / "last_object.json").exists():
            print(f"         model: {tmp / 'last_object.json'}")
        print(f"{name:8s} tools={tools} chat_cost=${cost}\n         {line}\n         {extra}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
