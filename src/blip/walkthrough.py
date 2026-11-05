"""Ghost Walkthroughs: turn an agent's run into a tutorial you can follow yourself.

Otto records, for every step, a clean screenshot, the element each action touched (name
and bounding box) and the agent's own one-line narration. A walkthrough replays that as
"theater": a ghost cursor glides to each element, circles it, and Blip narrates why. The
agent doesn't just do the task, it teaches you how.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from otto.storage import TrajectoryStore
from PIL import Image
from pydantic import BaseModel, Field

VISIBLE_TOOLS = {"click", "type_text", "select_option", "set_checkbox", "hover", "upload_file"}


class GhostStep(BaseModel):
    index: int
    narration: str
    url: str = ""
    image: str | None = Field(None, description="Path to the clean screenshot for this step.")
    image_size: tuple[int, int] | None = None
    action: str = ""
    detail: str = ""
    target_name: str | None = None
    target_box: tuple[float, float, float, float] | None = Field(
        None, description="x, y, w, h in image px."
    )


class Walkthrough(BaseModel):
    id: str
    title: str
    summary: str = ""
    steps: list[GhostStep] = Field(default_factory=list)


def _describe(action: dict[str, Any]) -> str:
    args = action.get("arguments") or {}
    tool = action.get("tool", "")
    target = (action.get("target") or {}).get("name") or "this"
    if tool == "click":
        return f'Click "{target}"'
    if tool == "type_text":
        return f'Type "{str(args.get("text", ""))[:60]}" into "{target}"'
    if tool == "select_option":
        return f'Choose "{args.get("option")}" in "{target}"'
    if tool == "set_checkbox":
        return f'{"Tick" if args.get("checked") else "Untick"} "{target}"'
    if tool == "navigate":
        return f"Open {args.get('url')}"
    return tool.replace("_", " ").capitalize()


def build_walkthrough(store: TrajectoryStore, run_id: str) -> Walkthrough:
    run = store.get_run(run_id)
    if run is None:
        raise KeyError(f"No run {run_id!r}")
    steps: list[GhostStep] = []
    for s in run["steps_detail"]:
        shot = s.get("screenshot_path")
        clean = None
        if shot:
            p = Path(shot)
            candidate = p.with_name(p.stem + "_clean.jpg")
            clean = candidate if candidate.exists() else (p if p.exists() else None)
        size = Image.open(clean).size if clean else None
        for a in s.get("actions", []):
            if not a.get("ok") or a.get("tool") in {
                "done",
                "(batch)",
                "save_finding",
                "update_plan",
                "remember",
                "write_file",
            }:
                continue
            target = a.get("target") or {}
            bbox = target.get("bbox")
            box = (
                (bbox["x"], bbox["y"], bbox["width"], bbox["height"])
                if bbox and a.get("tool") in VISIBLE_TOOLS
                else None
            )
            steps.append(
                GhostStep(
                    index=len(steps) + 1,
                    narration=(a.get("arguments") or {}).get("status")
                    or s.get("status")
                    or _describe(a),
                    url=s.get("url") or "",
                    image=str(clean) if clean else None,
                    image_size=size,
                    action=a["tool"],
                    detail=_describe(a),
                    target_name=target.get("name"),
                    target_box=box,
                )
            )
    return Walkthrough(
        id=f"walk_{run['id']}",
        title=f"How to: {run['task'].splitlines()[0][:90]}",
        summary=run.get("summary") or "",
        steps=steps,
    )
