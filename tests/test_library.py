"""The real-model library: catalog, licences, scenes, downloads, tag snapping, the tool menu,
the parts kit, and explaining on a real model in a turn."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import struct
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from pyxi.scenes import library
from pyxi.scenes.builder import BuildResult
from pyxi.scenes.objects import ObjectScene, Part, build_tool_schema, edit_tool_schema

ALLOWED = {"CC0-1.0", "CC-BY-4.0", "CC-BY-4.0 + CC0-1.0", "Public domain (NASA media guidelines)"}


def test_the_catalog_is_complete_and_only_takes_licences_we_can_ship():
    models = library.models()
    assert len(models) >= 100
    for m in models.values():
        assert m.file in (f"{m.id}.glb", *(f"{m.id}_v{v}.glb" for v in range(2, 10))) and m.title and m.parts
        assert m.license in ALLOWED, (m.id, m.license)
        assert m.credit and m.source.startswith("https://")
        for p in m.parts:
            assert len(p.center) == 3 and all(v >= 0 for v in p.size)


def test_a_library_model_becomes_a_scene_of_real_parts():
    m = library.get("perseverance")
    scene = m.scene()
    assert scene.credit == m.credit
    assert all(p.shape == "asset" and p.asset == "perseverance" and p.node == p.id for p in scene.parts)
    assert "Parts (id: centre, size)" in m.summary()
    assert "perseverance: Perseverance Mars rover" in library.menu()


def test_the_model_never_sees_library_fields_but_does_see_the_kit():
    build = json.dumps(build_tool_schema())
    edit = json.dumps(edit_tool_schema())
    for schema in (build, edit):
        assert '"asset"' not in schema and '"credit"' not in schema
        assert '"marker"' in schema and '"ram_stick"' in schema


def test_repairs_for_kit_asset_and_marker_parts():
    scene = ObjectScene(
        title="t",
        parts=[
            Part(id="a", shape="kit"),
            Part(id="b", shape="asset"),
            Part(id="c", shape="kit", kit="gpu"),
            Part(id="d", shape="marker", label="here"),
        ],
    )
    shapes = {p.id: p.shape for p in scene.parts}
    assert shapes == {"a": "box", "b": "box", "c": "kit", "d": "marker"}


def _glb(path: Path, points: list[list[float]], translation: list[float]) -> None:
    """A minimal .glb laid out like the packer's: one part node with one triangle."""
    data = struct.pack("<9f", *[v for p in points for v in p])
    doc = {
        "asset": {"version": "2.0"},
        "nodes": [{"name": "part_body", "mesh": 0, "translation": translation}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
        "accessors": [{"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3"}],
        "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": len(data)}],
        "buffers": [{"byteLength": len(data)}],
    }
    js = json.dumps(doc).encode()
    js += b" " * (-len(js) % 4)
    body = data + b"\0" * (-len(data) % 4)
    blob = struct.pack("<4sII", b"glTF", 2, 12 + 8 + len(js) + 8 + len(body))
    blob += struct.pack("<I4s", len(js), b"JSON") + js + struct.pack("<I4s", len(body), b"BIN\x00") + body
    path.write_bytes(blob)


def _as_catalogued(monkeypatch, model_id: str, path: Path):
    """Make the catalog's size match a test file, the way a real download matches it."""
    m = dataclasses.replace(library.get(model_id), bytes=path.stat().st_size)
    monkeypatch.setitem(library.models(), model_id, m)
    return m


def test_markers_are_pinned_onto_the_real_surface(tmp_path):
    m = library.get("perseverance")
    _glb(tmp_path / m.file, [[0, 0, 0], [1, 0, 0], [0, 1, 0]], [2, 0, 0])
    library._surface.cache_clear()
    scene = m.scene()
    scene.parts.append(Part(id="tag", shape="marker", position=[5, 5, 5], label="here"))
    scene.parts.append(Part(id="kid", shape="marker", parent="tag", position=[9, 9, 9]))
    library.snap_markers(scene, tmp_path)
    tag = next(p for p in scene.parts if p.id == "tag")
    kid = next(p for p in scene.parts if p.id == "kid")
    assert tag.position == [2.0, 1.0, 0.0]  # the nearest point of the model (vertex + node offset)
    assert kid.position == [9, 9, 9]  # attached markers move with their parent instead


def test_a_model_downloads_once_and_a_missing_one_falls_back(tmp_path, monkeypatch):
    m = library.get("apple")
    served = tmp_path / "served"
    served.mkdir()
    _glb(served / m.file, [[0, 0, 0], [1, 0, 0], [0, 1, 0]], [0, 0, 0])
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(served)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        m = _as_catalogued(monkeypatch, "apple", served / m.file)
        base = f"http://127.0.0.1:{server.server_address[1]}"
        dest = tmp_path / "library"
        assert library.ensure(dest, m, base) == dest / m.file
        assert (dest / m.file).read_bytes()[:4] == b"glTF"
        assert library.ensure(dest, m, None) == dest / m.file  # already here: no download needed
        assert library.ensure(tmp_path / "other", library.get("kiwi"), base) is None  # not on the server
        assert library.ensure(tmp_path / "other", m, None) is None
    finally:
        server.shutdown()


def test_the_3d_tool_offers_the_library():
    from pyxi.companion.tools import BUILD_BRIEF

    lib = BUILD_BRIEF.parameters["properties"]["library"]
    assert set(lib["enum"]) == set(library.models())
    assert "space_shuttle: Space Shuttle" in lib["description"]


async def test_a_turn_explains_on_the_real_model(settings, monkeypatch):
    import pyxi.companion.brain as brain_mod
    from test_brain_and_server import make_brain

    brain, rec = make_brain(settings)
    m = library.get("ingenuity")
    path = settings.library_dir / m.file
    path.parent.mkdir(parents=True, exist_ok=True)
    _glb(path, [[0, 0, 0], [1, 0, 0], [0, 1, 0]], [0, 0, 0])
    m = _as_catalogued(monkeypatch, "ingenuity", path)

    async def explain(llm, model, summary, brief, context=""):
        assert "Ingenuity" in summary and "rotor" in brief
        out = model.model_copy(deep=True)
        out.parts[0].label = "Body"
        out.beats[0].narration = "This little helicopter flew on Mars."
        return BuildResult(model=out, cost_usd=0.01)

    monkeypatch.setattr(brain_mod, "explain_library_model", explain)
    await brain._build_from_brief({"subject": "Ingenuity", "show": "how its rotors fly in thin air", "library": "ingenuity"})
    objects = rec.of("object")
    assert len(objects) == 2
    at_rest, explained = objects
    assert at_rest["scene"]["id"] == explained["scene"]["id"]  # one model: the view keeps its camera
    assert not at_rest["restart"] and explained["restart"]
    assert at_rest["scene"]["auto_play"] is False and explained["scene"]["auto_play"] is True
    assert at_rest["scene"]["parts"][0]["shape"] == "asset" and explained["scene"]["credit"] == m.credit
    assert explained["scene"]["parts"][0]["label"] == "Body"


async def test_without_the_file_it_builds_one_instead(settings, monkeypatch):
    import pyxi.companion.brain as brain_mod
    from test_brain_and_server import make_brain

    brain, rec = make_brain(settings)
    settings.library_url = None
    built = []

    async def build(llm, brief, context="", on_parts=None):
        built.append(brief)
        return BuildResult(model=None)

    async def explain(*a, **k):
        await asyncio.sleep(1)
        raise AssertionError("not reached")

    monkeypatch.setattr(brain_mod, "build_model", build)
    monkeypatch.setattr(brain_mod, "explain_library_model", explain)
    await brain._build_from_brief({"subject": "Ingenuity", "show": "rotors", "library": "ingenuity"})
    assert built and not rec.of("object")


@pytest.mark.parametrize("name", ["../x.glb", "a.png", "UPPER.glb"])
def test_the_server_only_serves_library_files(name):
    from pyxi.server import _LIBRARY_NAME

    assert not _LIBRARY_NAME.match(name)
    assert _LIBRARY_NAME.match("saturn_v.glb")


def test_an_old_copy_of_a_rebuilt_model_is_fetched_again(tmp_path, monkeypatch):
    m = library.get("apple")
    served, dest = tmp_path / "served", tmp_path / "library"
    served.mkdir()
    dest.mkdir()
    _glb(served / m.file, [[0, 0, 0], [1, 0, 0], [0, 1, 0]], [0, 0, 0])
    m = _as_catalogued(monkeypatch, "apple", served / m.file)
    (dest / m.file).write_bytes(b"glTF old one-piece version")
    assert library.ensure(dest, m, None) is None  # the wrong size is never used as it is
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(served)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        assert library.ensure(dest, m, f"http://127.0.0.1:{server.server_address[1]}") == dest / m.file
        assert (dest / m.file).stat().st_size == m.bytes
    finally:
        server.shutdown()


def test_the_rebuilt_car_has_moving_parts_under_a_new_file_name():
    car = library.get("concept_car")
    assert car.file == "concept_car_v2.glb"  # library-v1 has the old one-piece car as concept_car.glb
    ids = {p.id for p in car.parts}
    assert {"front_left_wheel", "rear_right_wheel", "left_door", "headlights", "body"} <= ids


def test_a_car_shows_a_few_different_tags_and_the_story_s_come_first():
    from pyxi.scenes.objects import Beat

    at_rest = library.get("concept_car").scene()
    tags = [p.label for p in at_rest.parts if p.label]
    assert len(tags) <= library.MOST_LABELS and len(set(tags)) == len(tags)  # no two "Door"s

    scene = library.get("concept_car").scene()
    for p in scene.parts:
        p.label = p.id
    scene.beats = [Beat(title="Lights", narration="x", focus=["tail_lights", "cabin"])]
    library.few_labels(scene, most=3)
    assert {p.id for p in scene.parts if p.label} == {"tail_lights", "cabin", "front_left_wheel"}


def test_the_models_see_title_fields_in_their_tools():
    beat = edit_tool_schema()["properties"]["beats"]["items"]["properties"]
    assert "title" in beat and "title" in build_tool_schema()["properties"]


def test_an_explanation_never_fakes_parts_onto_a_real_model():
    from pyxi.scenes.builder import _effects_only

    car = library.get("concept_car").scene()  # 10 long, no spoiler, no red calipers
    added = [
        Part(id="spoiler", shape="box", position=[0, 1.4, -4.6], size=[3, 0.1, 0.6], label="Spoiler"),
        Part(id="caliper", shape="box", parent="front_left_wheel", position=[0.2, 0.2, 0], size=[0.2, 0.4, 0.3]),
        Part(id="air", shape="arrow", position=[0, 2, 0], size=[14, 0.6, 0.6], label="Airflow"),
        Part(id="flame", shape="cone", material="emissive", position=[0, 0, -5], size=[0.5, 1, 0.5]),
        Part(id="person", shape="kit", kit="person", position=[5, 0, 0], size=[0.5, 1.8, 0.4]),
        Part(id="stand", shape="cylinder", position=[0, -4, 0], size=[3, 0.3, 3]),
    ]
    kept = {p.id: p for p in _effects_only(car, added)}
    assert set(kept) == {"air", "flame", "person", "stand"}  # no blocks posing as car parts
    assert max(kept["air"].size) <= 10 / 3 + 1e-6  # and no beam bigger than the car
