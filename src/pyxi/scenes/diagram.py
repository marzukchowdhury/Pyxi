"""Typed diagrams: Pyxi chooses a type and gives the content, and the board lays it out.

A freehand sketchboard (``sketch.py``) suits a physical mechanism: the model draws the jet and
the air under it. A structured idea is different. A process, a decision, an order of events, a
comparison, a hierarchy or a cycle has a known good shape, and a model placing every box by hand
gets it uneven. So here the model only names the type and writes the content: short labels, the
links between them, the one or two items that matter, and which spoken step draws each. The
renderer (``desktop/src/renderer/src/scene/diagramLayout.ts``) places everything, so nothing
can overlap.

This file holds the limits that make that promise keepable. A request inside them always fits;
one outside them is turned away with a note the model can act on, never drawn cramped.

The type catalogue and the taste (at most nine items, one accent on at most two of them, thin
lines, nothing drawn that carries no meaning) follow the diagram-design skill by Cathryn Lavery
(MIT, github.com/cathrynlavery/diagram-design), written again for Pyxi's own board.
"""

from __future__ import annotations

import re
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field

from pyxi.scenes.sketch import MAX_STEPS, NARRATION, SketchAnchor, Step

DiagramType = Literal["steps", "flowchart", "timeline", "comparison", "tree", "loop"]
TYPES: tuple[str, ...] = ("steps", "flowchart", "timeline", "comparison", "tree", "loop")
# What the model sometimes calls them.
ALIASES = {
    "process": "steps",
    "sequence": "steps",
    "decision": "flowchart",
    "flow": "flowchart",
    "compare": "comparison",
    "quadrant": "comparison",
    "matrix": "comparison",
    "hierarchy": "tree",
    "cycle": "loop",
}

# The layout is tested against exactly these (diagramLayout.test.ts): change them together.
MAX_ITEMS = 9
MAX_FOCUS = 2
MAX_LABEL = 28  # characters: 1 to 4 words
MAX_COLUMN_LABEL = 44  # a line in a comparison's column: a short phrase
MAX_WORD = 14
MAX_LINKS = 12
MAX_LINK_LABEL = 10  # "yes", "no", "over $100"
MAX_WHEN = 14  # "12 March 2024"
MAX_NAME = 16  # a column's name
MAX_AXIS_WORD = 12
MAX_CENTER = 20
MAX_LOOP_ITEMS = 8  # a ninth station leaves no room for the arcs between them
FLOW_LEVELS = 5
FLOW_ABREAST = 3
FLOW_EXITS = 3
TREE_LEVELS = 4
PER_COLUMN = 5
PER_QUARTER = 4


class DiagramError(ValueError):
    """The content does not fit the type. ``problems`` are sentences the model can act on."""

    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


class Item(BaseModel):
    id: str
    label: str
    step: int = 0
    focus: bool = False
    side: Literal["left", "right"] | None = None
    when: str | None = None
    x: float | None = None
    y: float | None = None


class Link(BaseModel):
    source: str = Field(serialization_alias="from")
    to: str
    label: str | None = None


class Axes(BaseModel):
    x: tuple[str, str]
    y: tuple[str, str]


class Diagram(BaseModel):
    id: str = Field(default_factory=lambda: "sketch_" + uuid.uuid4().hex[:8])
    type: DiagramType
    title: str
    subtitle: str | None = None
    items: list[Item]
    links: list[Link] = Field(default_factory=list)
    columns: tuple[str, str] | None = None
    axes: Axes | None = None
    center: str | None = None
    steps: list[Step]
    anchor: SketchAnchor | None = None
    notes: list[str] = Field(default_factory=list)

    def as_board(self) -> dict[str, Any]:
        """What the overlay gets: a sketchboard with no shapes and the content to lay out."""
        content = self.model_dump(
            mode="json",
            by_alias=True,
            include={"type", "items", "links", "columns", "axes", "center"},
        )
        return {
            "id": self.id,
            "title": self.title,
            "subtitle": self.subtitle,
            "shapes": [],
            "steps": [s.model_dump(mode="json") for s in self.steps],
            "anchor": self.anchor.model_dump(mode="json") if self.anchor else None,
            "diagram": content,
        }


def _text(v: Any) -> str:
    return re.sub(r"\s+", " ", str(v if v is not None else "")).strip()


def _label(v: Any) -> str:
    return _text(v).rstrip(".;:,").strip()


def _number(v: Any) -> float | None:
    try:
        return min(100.0, max(0.0, float(v)))
    except (TypeError, ValueError):
        return None


def _pair(v: Any) -> tuple[str, str] | None:
    if isinstance(v, (list, tuple)) and len(v) == 2 and all(_label(x) for x in v):
        return (_label(v[0]), _label(v[1]))
    return None


def _levels(n: int, links: list[tuple[int, int]]) -> list[int] | None:
    """How far down each item sits (its longest way from a start), or None if the links loop."""
    level = [0] * n
    waiting = [0] * n
    for _, v in links:
        waiting[v] += 1
    queue = [i for i in range(n) if not waiting[i]]
    for u in queue:  # grows as it is read
        for a, v in links:
            if a == u:
                level[v] = max(level[v], level[u] + 1)
                waiting[v] -= 1
                if not waiting[v]:
                    queue.append(v)
    return level if len(queue) == n else None


def parse_diagram(args: dict[str, Any]) -> Diagram:
    """The model's ``draw_diagram`` call as a diagram that is known to fit, or a DiagramError
    listing everything to change. Small slips are repaired and noted, not refused."""
    problems: list[str] = []
    notes: list[str] = []
    kind = _text(args.get("type")).lower()
    kind = ALIASES.get(kind, kind)
    if kind not in TYPES:
        raise DiagramError([f"type must be one of {', '.join(TYPES)} (got {kind or 'nothing'!r})"])

    items: list[Item] = []
    seen: set[str] = set()
    raw_items = args.get("items") if isinstance(args.get("items"), list) else []
    for i, raw in enumerate(raw_items):
        if not isinstance(raw, dict) or not _label(raw.get("label")):
            continue
        ident = _text(raw.get("id")) or str(i + 1)
        if ident in seen:
            problems.append(f"two items share the id {ident!r}: give each its own")
            continue
        seen.add(ident)
        try:
            step = max(0, int(raw.get("step") or 0))
        except (TypeError, ValueError):
            step = 0
        side = _text(raw.get("side")).lower()
        items.append(
            Item(
                id=ident,
                label=_label(raw.get("label")),
                step=step,
                focus=raw.get("focus") is True,
                side=side if side in ("left", "right") else None,  # type: ignore[arg-type]
                when=_label(raw.get("when")) or None,
                x=_number(raw.get("x")),
                y=_number(raw.get("y")),
            )
        )
    limit = MAX_LOOP_ITEMS if kind == "loop" else MAX_ITEMS
    if len(items) < 2:
        problems.append("a diagram needs at least 2 items, each with a label")
    if len(items) > limit:
        problems.append(
            f"{len(items)} items: the limit is {limit}. Merge items that always go together "
            "or leave out the least important"
        )
    # A column of a comparison is nearly half the board wide: its lines may be short phrases.
    in_columns = kind == "comparison" and not isinstance(args.get("axes"), dict)
    most = MAX_COLUMN_LABEL if in_columns else MAX_LABEL
    long = [
        it.label
        for it in items
        if len(it.label) > most or max(map(len, it.label.split())) > MAX_WORD
    ]
    if long:
        problems.append(
            f"{len(long)} label{'s' if len(long) > 1 else ''} too long (limit {most} "
            f"characters, no word over {MAX_WORD}): "
            + ", ".join(repr(x) for x in long[:3])
            + (". Say each in a few words" if in_columns else ". Say each in 1 to 4 words")
        )
    late = [it.when for it in items if it.when and len(it.when) > MAX_WHEN]
    if late:
        problems.append(f"`when` too long: {late[0]!r} (limit {MAX_WHEN} characters)")
    focal = [it for it in items if it.focus]
    for it in focal[MAX_FOCUS:]:
        it.focus = False
    if len(focal) > MAX_FOCUS:
        notes.append(f"kept the accent on the first {MAX_FOCUS} of {len(focal)} focus items")

    # Links: only a flowchart and a tree have them (the other types are joined by their order).
    at = {it.id: i for i, it in enumerate(items)}
    links: list[Link] = []
    pairs: list[tuple[int, int]] = []
    raw_links = args.get("links") if isinstance(args.get("links"), list) else []
    if kind in ("flowchart", "tree"):
        for raw in raw_links:
            if not isinstance(raw, dict):
                continue
            a, b = _text(raw.get("from")), _text(raw.get("to"))
            label = _label(raw.get("label")) or None
            missing = [x for x in (a, b) if x not in at]
            if missing:
                problems.append(f"link {a or '?'} -> {b or '?'}: no item has the id {missing[0]!r}")
                continue
            if a == b or (at[a], at[b]) in pairs:
                continue
            if label and len(label) > MAX_LINK_LABEL:
                problems.append(
                    f"link label too long: {label!r} (limit {MAX_LINK_LABEL} characters, "
                    "e.g. 'yes', 'no')"
                )
            pairs.append((at[a], at[b]))
            links.append(Link(source=a, to=b, label=label if kind == "flowchart" else None))
        if len(links) > MAX_LINKS:
            problems.append(f"{len(links)} links: the limit is {MAX_LINKS}")
    if kind == "flowchart" and len(items) >= 2:
        problems += _check_flowchart(items, pairs)
    if kind == "tree" and len(items) >= 2:
        problems += _check_tree(items, pairs)

    columns = axes = None
    if kind == "comparison":
        columns = _pair(args.get("columns"))
        raw_axes = args.get("axes") if isinstance(args.get("axes"), dict) else None
        if raw_axes:
            x, y = _pair(raw_axes.get("x")), _pair(raw_axes.get("y"))
            if not x or not y:
                problems.append(
                    "axes needs x: [left word, right word] and y: [bottom word, top word]"
                )
            else:
                axes = Axes(x=x, y=y)
                wordy = [w for w in (*x, *y) if len(w) > MAX_AXIS_WORD]
                if wordy:
                    problems.append(
                        f"axis word too long: {wordy[0]!r} (limit {MAX_AXIS_WORD} characters)"
                    )
                problems += _check_axes(items)
        elif columns:
            wordy = [c for c in columns if len(c) > MAX_NAME]
            if wordy:
                problems.append(f"column name too long: {wordy[0]!r} (limit {MAX_NAME} characters)")
            unsided = [it.label for it in items if it.side is None]
            if unsided:
                problems.append(
                    f"every item needs side 'left' or 'right' (missing on {unsided[0]!r})"
                )
            for side in ("left", "right"):
                count = sum(1 for it in items if it.side == side)
                if count > PER_COLUMN:
                    problems.append(
                        f"{count} items on the {side}: the limit is {PER_COLUMN} a side"
                    )
        else:
            problems.append(
                "a comparison needs `columns` (the two names, with `side` on every item) "
                "or `axes` (with x and y on every item)"
            )
    center = _label(args.get("center")) or None if kind == "loop" else None
    if center and len(center) > MAX_CENTER:
        problems.append(f"`center` too long: {center!r} (limit {MAX_CENTER} characters)")

    steps = [
        s
        for s in (Step.model_validate(r) for r in (args.get("steps") or []) if isinstance(r, dict))
        if s.narration
    ][:MAX_STEPS]
    title = _text(args.get("title"))[:48]
    if not title:
        problems.append("give it a title: the idea in 2 to 5 words")
    if problems:
        raise DiagramError(problems)
    steps = steps or [Step(narration=title)]
    for it in items:
        it.step = min(it.step, len(steps) - 1)
    anchor = args.get("anchor") if isinstance(args.get("anchor"), dict) else None
    return Diagram(
        type=kind,  # type: ignore[arg-type]
        title=title,
        subtitle=_text(args.get("subtitle"))[:48] or None,
        items=items,
        links=links,
        columns=columns if not axes else None,
        axes=axes,
        center=center,
        steps=steps,
        anchor=SketchAnchor.model_validate(anchor) if anchor else None,
        notes=notes,
    )


def _check_flowchart(items: list[Item], pairs: list[tuple[int, int]]) -> list[str]:
    if not pairs:
        return ["a flowchart needs `links` (from, to); for a plain sequence use type steps"]
    problems: list[str] = []
    level = _levels(len(items), pairs)
    if level is None:
        return [
            "the links loop back on themselves: a flowchart runs one way. Use type loop for "
            "a cycle, or end the branch at an item like 'Try again'"
        ]
    deep = max(level) + 1
    if deep > FLOW_LEVELS:
        problems.append(
            f"{deep} levels deep: the limit is {FLOW_LEVELS}. Merge two steps that always "
            "follow each other"
        )
    for lv in range(deep):
        abreast = [it.label for it, at in zip(items, level, strict=True) if at == lv]
        if len(abreast) > FLOW_ABREAST:
            problems.append(
                f"{len(abreast)} items side by side ({', '.join(abreast)}): the limit is "
                f"{FLOW_ABREAST}"
            )
    for i, it in enumerate(items):
        exits = sum(1 for a, _ in pairs if a == i)
        if exits > FLOW_EXITS:
            problems.append(f"{exits} ways out of {it.label!r}: the limit is {FLOW_EXITS}")
    return problems


def _check_tree(items: list[Item], pairs: list[tuple[int, int]]) -> list[str]:
    if not pairs:
        return ["a tree needs `links` from each parent to its children"]
    parents: dict[int, int] = {}
    for a, b in pairs:
        if b in parents:
            return [f"{items[b].label!r} has two parents: in a tree each item has one"]
        parents[b] = a
    roots = [it.label for i, it in enumerate(items) if i not in parents]
    if len(roots) != 1:
        return [
            f"a tree has one item at the top; {len(roots)} have no parent"
            + (f" ({', '.join(roots[:4])})" if roots else " (the links go round in a circle)")
        ]
    for i in range(len(items)):
        depth, at = 1, i
        while at in parents:
            depth, at = depth + 1, parents[at]
            if depth > len(items):
                return ["the links go round in a circle: a tree only branches downward"]
        if depth > TREE_LEVELS:
            return [f"{depth} levels deep: the limit is {TREE_LEVELS}. Flatten or split it"]
    return []


def _check_axes(items: list[Item]) -> list[str]:
    unplaced = [it.label for it in items if it.x is None or it.y is None]
    if unplaced:
        return [f"every item needs x and y from 0 to 100 (missing on {unplaced[0]!r})"]
    counts: dict[tuple[bool, bool], int] = {}
    for it in items:
        quarter = (it.x >= 50, it.y >= 50)  # type: ignore[operator]
        counts[quarter] = counts.get(quarter, 0) + 1
    if max(counts.values(), default=0) > PER_QUARTER:
        return [f"more than {PER_QUARTER} items in one quarter: spread them or leave some out"]
    return []


DIAGRAM_TOOL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "type": {
            "type": "string",
            "enum": list(TYPES),
            "description": "steps: a process in order. flowchart: a decision with branches. "
            "timeline: events in order. comparison: two things side by side, or items on two "
            "axes. tree: a hierarchy. loop: a cycle that feeds itself.",
        },
        "title": {"type": "string", "description": "The idea in 2-5 words."},
        "items": {
            "type": "array",
            "description": "2-9 items in the order you tell them (a loop: at most 8).",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Short handle for links, e.g. 'a'."},
                    "label": {
                        "type": "string",
                        "description": "1-4 words, at most 28 characters (a line in a "
                        "comparison's column: a short phrase, at most 44).",
                    },
                    "step": {"type": "integer", "description": "0-based step that draws it."},
                    "focus": {
                        "type": "boolean",
                        "description": "True on the one or two items that matter most.",
                    },
                    "side": {
                        "type": "string",
                        "enum": ["left", "right"],
                        "description": "comparison with columns: which column.",
                    },
                    "when": {
                        "type": "string",
                        "description": "timeline: the date or moment, e.g. '1969'.",
                    },
                    "x": {
                        "type": "number",
                        "description": "comparison on axes: 0 (left) to 100 (right).",
                    },
                    "y": {
                        "type": "number",
                        "description": "comparison on axes: 0 (bottom) to 100 (top).",
                    },
                },
                "required": ["id", "label", "step"],
            },
        },
        "links": {
            "type": "array",
            "description": "flowchart: from each item to what follows it, with 'yes'/'no' on "
            "the ways out of a decision; at most 5 levels deep and 3 items side by side. "
            "tree: from each parent to its children; at most 4 levels. Leave out otherwise.",
            "items": {
                "type": "object",
                "properties": {
                    "from": {"type": "string"},
                    "to": {"type": "string"},
                    "label": {"type": "string", "description": "At most 10 characters."},
                },
                "required": ["from", "to"],
            },
        },
        "columns": {
            "type": "array",
            "items": {"type": "string"},
            "description": "comparison: the two things compared, e.g. ['Renting', 'Buying']; "
            "at most 5 items a side.",
        },
        "axes": {
            "type": "object",
            "description": "comparison on two axes instead of columns: one word for each end.",
            "properties": {
                "x": {"type": "array", "items": {"type": "string"}, "description": "[left, right]"},
                "y": {"type": "array", "items": {"type": "string"}, "description": "[bottom, top]"},
            },
        },
        "center": {
            "type": "string",
            "description": "loop: what the cycle builds up, shown in the middle. Optional.",
        },
        "steps": {
            "type": "array",
            "description": "2-5 steps. Each draws its items while Pyxi says the narration.",
            "items": {
                "type": "object",
                "properties": {"narration": {"type": "string", "description": NARRATION}},
                "required": ["narration"],
            },
        },
        "anchor": {
            "type": "object",
            "description": "What on screen this is about, if anything. A thread joins them.",
            "properties": {"element_id": {"type": "integer"}, "quote": {"type": "string"}},
        },
    },
    "required": ["type", "title", "items", "steps"],
}
