"""Exploded views: peel a real piece of the user's screen apart into a 3D hierarchy.

Given a container element (a settings panel, a toolbar, a dialog), Wisp walks its UI
Automation subtree, crops each element out of the screenshot, and builds a ``hologram``
scene whose nodes start exactly where they are on screen and then fly apart in depth by
hierarchy level. Because the overlay's camera maps the z=0 plane 1:1 onto screen pixels,
the pieces appear to lift straight off the real UI.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from wisp.scenes.spec import SceneNode, SceneSpec, SceneStep
from wisp.screen import ScreenContext, UIElement

MAX_PIECES = 28


def _label(e: UIElement) -> str:
    return (e.name or e.role)[:36]


def build_exploded_scene(
    ctx: ScreenContext,
    root_id: int,
    crops_dir: Path,
    *,
    title: str | None = None,
    steps: list[dict] | None = None,
) -> SceneSpec:
    root = ctx.element(root_id)
    if root is None:
        raise ValueError(f"No element [{root_id}] on screen.")
    pieces = [e for e in ctx.subtree(root_id, max_depth=3) if not e.is_password][:MAX_PIECES]
    crops_dir.mkdir(parents=True, exist_ok=True)
    nodes: list[SceneNode] = []
    kept = {e.id for e in pieces}
    for e in pieces:
        image_id = None
        crop = ctx.crop(e.rect)
        if crop is not None and crop.width >= 4 and crop.height >= 4:
            image_id = f"{uuid.uuid4().hex[:12]}.png"
            crop.save(crops_dir / image_id)
        parent = e.parent if e.parent in kept else None
        nodes.append(
            SceneNode(
                id=f"e{e.id}",
                label=_label(e),
                detail=e.role,
                shape="screen",
                parent=f"e{parent}" if parent else None,
                screen_rect=list(e.rect),
                image=image_id,
                depth=max(0, e.depth - root.depth),
                glow=e.id == root_id,
            )
        )

    scene_steps: list[SceneStep] = []
    for s in steps or []:
        focus = [f"e{i}" for i in s.get("focus_element_ids", []) if i in kept]
        scene_steps.append(
            SceneStep(
                title=s.get("title", ""),
                narration=s.get("narration", ""),
                focus=focus,
                camera="focus",
            )
        )
    if not scene_steps:
        top = [n.id for n in nodes if n.parent == f"e{root_id}"][:6]
        scene_steps = [
            SceneStep(
                title="Lifting it apart",
                narration=f"Here's how {_label(root)} is put together.",
                camera="overview",
            ),
            SceneStep(
                title="The main sections",
                narration="Each layer back is one level deeper in the hierarchy.",
                focus=top,
                camera="side",
            ),
        ]
    return SceneSpec(
        title=title or f"Inside {_label(root)}",
        layout="exploded",
        mode="hologram",
        theme="blueprint",
        nodes=nodes,
        steps=scene_steps,
    )
