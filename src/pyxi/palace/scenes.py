"""Memory Palace scenes: rebuild a past interface in 3D, and lay out how one thing led to another."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image

from pyxi.palace.activity import Event
from pyxi.scenes.spec import SceneEdge, SceneNode, SceneSpec, SceneStep

MAX_GHOSTS = 36


def _when(ts: float) -> str:
    dt = datetime.fromtimestamp(ts)
    today = datetime.now().date()
    day = "today" if dt.date() == today else dt.strftime("%A %d %b")
    return f"{day} at {dt.strftime('%H:%M')}"


class Thumb:
    """Cuts element crops out of a stored snapshot thumbnail (if thumbnails are enabled)."""

    def __init__(self, path: str | None) -> None:
        self.img: Image.Image | None = None
        self.origin, self.scale = (0, 0), 1.0
        if path and Path(path).exists():
            self.img = Image.open(path)
            meta = Path(path).with_suffix(".json")
            if meta.exists():
                m = json.loads(meta.read_text())
                self.origin, self.scale = tuple(m["origin"]), float(m["scale"])

    def crop(self, rect: tuple[int, int, int, int], out_dir: Path) -> str | None:
        if self.img is None:
            return None
        x, y, w, h = rect
        box = [
            int((x - self.origin[0]) * self.scale),
            int((y - self.origin[1]) * self.scale),
            int((x - self.origin[0] + w) * self.scale),
            int((y - self.origin[1] + h) * self.scale),
        ]
        box = [
            max(0, box[0]),
            max(0, box[1]),
            min(self.img.width, box[2]),
            min(self.img.height, box[3]),
        ]
        if box[2] - box[0] < 3 or box[3] - box[1] < 3:
            return None
        out_dir.mkdir(parents=True, exist_ok=True)
        name = f"{uuid.uuid4().hex[:12]}.png"
        self.img.crop(tuple(box)).save(out_dir / name)
        return name


def snapshot_scene(
    snapshot: dict[str, Any], crops_dir: Path, *, highlight: str | None = None
) -> SceneSpec:
    """A past interface as a hologram of ghost panels at their real positions, peeled apart by depth."""
    thumb = Thumb(snapshot.get("thumb_path"))
    elements = sorted(snapshot["elements"], key=lambda e: (e["depth"], -(e["w"] * e["h"])))[
        :MAX_GHOSTS
    ]
    keep = {e["id"] for e in elements}
    base = min((e["depth"] for e in elements), default=0)
    nodes = []
    needle = (highlight or "").lower()
    for e in elements:
        rect = (e["x"], e["y"], e["w"], e["h"])
        nodes.append(
            SceneNode(
                id=f"g{e['id']}",
                label=(e["name"] or e["role"])[:36],
                detail=e["role"],
                shape="screen",
                parent=f"g{e['parent_id']}" if e["parent_id"] in keep else None,
                screen_rect=list(rect),
                image=thumb.crop(rect, crops_dir),
                depth=e["depth"] - base,
                glow=bool(
                    needle
                    and e["name"]
                    and any(w in e["name"].lower() for w in needle.split() if len(w) > 2)
                ),
            )
        )
    glowing = [n.id for n in nodes if n.glow][:4]
    when = _when(snapshot["ts"])
    steps = [
        SceneStep(
            title=f"{snapshot['app']} · {when}",
            narration=f"Here's {snapshot['app']} as it was {when}.",
            camera="front",
        )
    ]
    if glowing:
        steps.append(
            SceneStep(
                title="What you were looking for",
                narration="And here's the bit you were after.",
                focus=glowing,
            )
        )
    steps.append(
        SceneStep(
            title="Layer by layer",
            narration="Fly around it, or scrub the timeline to move through time.",
            camera="side",
        )
    )
    return SceneSpec(
        title=f"{snapshot['app']}: {snapshot['title'][:60]}",
        subtitle=when,
        layout="exploded",
        mode="hologram",
        theme="blueprint",
        nodes=nodes or [SceneNode(id="empty", label="Nothing recorded")],
        steps=steps,
        auto_play=False,
    )


def causal_scene(
    events: list[Event],
    links: list[dict[str, Any]],
    *,
    title: str,
    summary: str,
    what_ifs: list[dict[str, Any]] | None,
    crops_dir: Path,
    snapshots: dict[int, dict[str, Any]],
) -> SceneSpec:
    """The chain of events that led here: action nodes in time order, 'led to' threads, what-if branches."""
    nodes: list[SceneNode] = []
    for i, ev in enumerate(events):
        image = None
        snap = snapshots.get(ev.snapshot_id or -1)
        if snap and ev.rect:
            image = Thumb(snap.get("thumb_path")).crop(ev.rect, crops_dir)
        nodes.append(
            SceneNode(
                id=f"ev{ev.id}",
                label=ev.headline()[:40],
                detail=f"{ev.app} · {datetime.fromtimestamp(ev.ts).strftime('%H:%M:%S')}",
                shape="screen" if image else "card",
                image=image,
                group="chain",
                depth=i,
                glow=i == len(events) - 1,
            )
        )
    edges = []
    why_by_target: dict[str, str] = {}
    for link in links:
        a, b = f"ev{link.get('from')}", f"ev{link.get('to')}"
        why = str(link.get("why", ""))[:60]
        edges.append(SceneEdge(source=a, target=b, label=why or "led to", kind="flow"))
        why_by_target[b] = why
    if not links:  # default: each event led to the next
        edges = [
            SceneEdge(source=nodes[i].id, target=nodes[i + 1].id, label="then", kind="flow")
            for i in range(len(nodes) - 1)
        ]
    for j, w in enumerate(what_ifs or []):
        nid = f"whatif{j}"
        nodes.append(
            SceneNode(
                id=nid,
                label=f"What if: {str(w.get('action', ''))[:30]}",
                detail=str(w.get("outcome", ""))[:60],
                shape="chip",
                color="#f472b6",
                group="whatif",
            )
        )
        edges.append(
            SceneEdge(
                source=f"ev{w.get('after_event_id')}", target=nid, label="instead…", kind="dashed"
            )
        )
    steps = [SceneStep(title=title, narration=summary, camera="overview")]
    for n in nodes:
        if n.id.startswith("ev") and n.id in why_by_target and why_by_target[n.id]:
            steps.append(
                SceneStep(
                    title=n.label,
                    narration=f"{n.label}, because {why_by_target[n.id]}.",
                    focus=[n.id],
                )
            )
    for j, w in enumerate(what_ifs or []):
        steps.append(
            SceneStep(
                title="A different path",
                narration=f"If you had {w.get('action')}, {w.get('outcome')}.",
                focus=[f"whatif{j}"],
            )
        )
    return SceneSpec(
        title=title,
        subtitle="How you got here",
        layout="timeline",
        mode="card",
        theme="aurora",
        nodes=nodes,
        edges=edges,
        steps=steps[:12],
    )
