from __future__ import annotations

import io
import json

import pytest
from PIL import Image

from conftest import fake_context
from pyxi.config import ScreenConfig
from pyxi.scenes import GALLERY, build_exploded_scene, parse_scene, tool_schema
from pyxi.screen import ScreenReader, WindowInfo
from pyxi.screen.context import pick_monitor


# ---------------------------------------------------------------- scenes
@pytest.mark.parametrize("name", list(GALLERY))
def test_gallery_scenes_are_valid_and_clean(name):
    scene = GALLERY[name]()
    assert scene.nodes and scene.steps and not scene.notes
    ids = {n.id for n in scene.nodes}
    assert all(e.source in ids and e.target in ids for e in scene.edges)
    assert all(f in ids for s in scene.steps for f in s.focus)


def test_bad_model_output_is_repaired_not_rejected():
    scene = parse_scene(
        {
            "title": "t",
            "layout": "flow",
            "nodes": [
                {"id": "a b", "label": "A", "color": "red"},
                {"id": "a b", "label": "B", "parent": "ghost"},
            ],
            "edges": [
                {"from": "a_b", "to": "nope"},
                {"from": "a_b", "to": "a_b_1", "kind": "flow"},
            ],
            "steps": [
                {
                    "title": "s",
                    "narration": "n",
                    "focus": ["a_b", "ghost"],
                    "edges": ["a_b->a_b_1", "x->y"],
                }
            ],
        }
    )
    assert [n.id for n in scene.nodes] == ["a_b", "a_b_1"]
    assert scene.nodes[0].color is None  # not a hex colour
    assert scene.nodes[1].parent is None and len(scene.edges) == 1
    assert scene.steps[0].focus == ["a_b"] and scene.steps[0].edges == ["a_b->a_b_1"]
    assert len(scene.notes) == 3


def test_tool_schema_is_self_contained():
    schema = tool_schema()
    text = json.dumps(schema)
    assert "$ref" not in text and "$defs" not in text
    assert {"title", "layout", "nodes", "steps"} <= set(schema["required"])
    assert (
        "image" not in schema["properties"]["nodes"]["items"]["properties"]
    )  # internal fields hidden


def test_exploded_view_uses_real_crops(tmp_path):
    ctx = fake_context()
    scene = build_exploded_scene(ctx, 1, tmp_path)
    by_id = {n.id: n for n in scene.nodes}
    assert scene.layout == "exploded" and scene.mode == "hologram"
    assert by_id["e3"].parent == "e2" and by_id["e3"].depth == 2 and by_id["e1"].glow
    assert "e5" not in by_id, "password fields never become crops"
    assert all((tmp_path / n.image).exists() for n in scene.nodes if n.image)


# ---------------------------------------------------------------- screen context
def test_render_and_model_image_masks_passwords():
    ctx = fake_context()
    ctx.screenshot.paste(
        (255, 0, 0), (140, 400, 440, 430)
    )  # pretend the password field shows red text
    text = ctx.render()
    assert '[4] Button "Export"' in text and "(password field)" in text
    jpeg = ctx.model_image(800, marks=False)
    img = Image.open(io.BytesIO(jpeg))
    assert img.width == 800 and ctx.model_scale == pytest.approx(2.0)
    r, g, b = img.getpixel((145, 207))  # centre of the masked field, at half scale
    assert r < 60 and g < 60 and b < 60
    assert ctx.to_screen(350, 325) == (700, 650)


def test_excluded_apps_and_monitor_choice():
    reader = ScreenReader(ScreenConfig())
    assert reader.is_excluded(WindowInfo("Vault - Bitwarden", "Bitwarden.exe"))
    assert reader.is_excluded(WindowInfo("Online Banking", "chrome.exe"))
    assert reader.is_excluded(WindowInfo("Pricing - Figma", "Figma.exe")) is None
    mons = [
        {"left": 1920, "top": 0, "width": 1920, "height": 1080},
        {"left": 0, "top": 0, "width": 1920, "height": 1080},
    ]
    assert pick_monitor(mons, (100, 100, 800, 600))["left"] == 0
    assert pick_monitor(mons, (2500, 100, 800, 600))["left"] == 1920
