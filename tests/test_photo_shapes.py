"""The shapes a photo proved were missing (a body from its cross-sections, a hollow vessel, an
outline with soft edges, a ring of copies, a wall of windows), and the plain picture of a model
that the brain draws for itself to hold against a photo (scenes/raster.py)."""

from __future__ import annotations

import math

import numpy as np
import pytest

from pyxi.scenes import raster
from pyxi.scenes.builder import SYSTEM
from pyxi.scenes.check import floating, tidy
from pyxi.scenes.model_examples import PHOTO_EXAMPLES
from pyxi.scenes.objects import ObjectScene, Part, build_tool_schema, loft_rings

CAR = [[-4, 3.2, 1.2, 0, 0.4], [-2, 3.6, 1.5, 0.1, 0.4], [0, 3.6, 2.6, 0.6, 0.5], [2.4, 3.6, 2.4, 0.5, 0.5], [4, 3.3, 1.4, 0.1, 0.4]]


def scene(*parts: dict, **more) -> ObjectScene:
    return ObjectScene.model_validate({"title": "T", "parts": list(parts), **more})


# ------------------------------------------------------------------ loft
def test_a_loft_goes_through_every_section_the_same_way_the_renderer_does_it():
    rings = loft_rings(CAR)
    assert len(rings) == (len(CAR) - 1) * 8 + 1
    for i, s in enumerate(CAR):
        assert rings[i * 8] == pytest.approx(s)
    assert all(b[0] >= a[0] for a, b in zip(rings, rings[1:]))  # never doubles back
    # Between two sections it is between them: no bulge, no pinch.
    mid = rings[4]
    assert -4 < mid[0] < -2 and 3.2 < mid[1] < 3.6 and 1.2 < mid[2] < 1.5


def test_a_lofts_box_is_worked_out_from_its_sections_and_it_is_centred_on_it():
    [p] = scene({"id": "body", "shape": "loft", "axis": "x", "sections": CAR, "position": [1, 2, 3]}).parts
    assert p.size[0] == pytest.approx(8.0) and p.size[2] == pytest.approx(3.6, abs=0.1)
    lo, hi = min(r[3] - r[2] / 2 for r in loft_rings(CAR)), max(r[3] + r[2] / 2 for r in loft_rings(CAR))
    assert p.size[1] == pytest.approx(hi - lo)
    assert p.position == pytest.approx([1, 2 + (lo + hi) / 2, 3])  # moved to the middle of its box
    assert min(r[3] - r[2] / 2 for r in loft_rings(p.sections)) == pytest.approx(-p.size[1] / 2)


def test_careless_sections_are_repaired_and_a_loft_without_any_is_still_a_shape():
    [p] = scene({"id": "b", "shape": "loft", "sections": [[3, -2, 1], [0, 1, 1, 0.5, 9], "x", [1], [1, 2, 0.5, 0, 0]]}).parts
    assert [s[0] for s in p.sections] == sorted(s[0] for s in p.sections)  # in order along the body
    assert all(s[1] > 0 and s[2] > 0 and 0.15 <= s[4] <= 1 for s in p.sections)
    bare = scene({"id": "b", "shape": "loft"})
    assert bare.parts[0].shape == "rounded_box" and any("loft without sections" in n for n in bare.notes)


# ------------------------------------------------------------------ a ring of copies
def test_around_makes_a_ring_of_the_part_each_turned_to_face_out():
    made = scene(
        {"id": "seat", "shape": "cylinder", "size": [4, 0.4, 4], "position": [0, 2, 0]},
        {"id": "leg", "shape": "cylinder", "size": [0.3, 4, 0.3], "position": [1.5, 0, 0], "rotation": [0, 0, 12], "around": 4, "label": "Leg"},
    )
    legs = [p for p in made.parts if p.id.startswith("leg")]
    assert [p.id for p in legs] == ["leg", "leg_2", "leg_3", "leg_4"]
    places = sorted((round(p.position[0], 3), round(p.position[2], 3)) for p in legs)
    assert places == [(-1.5, 0.0), (0.0, -1.5), (0.0, 1.5), (1.5, 0.0)]
    assert all(p.position[1] == 0 for p in legs) and all(p.around is None for p in made.parts)
    assert [p.label for p in legs] == ["Leg", None, None, None]  # one tag, not four
    # Each leg leans the same way relative to the middle: its top is nearer the axis.
    where = raster.placements(made)
    for p in legs:
        turn, at = where[p.id]
        top = turn @ np.array([0, 2.0, 0]) + at
        assert math.hypot(top[0], top[2]) < math.hypot(at[0], at[2]) - 0.3


def test_a_part_on_the_middle_line_is_not_copied_onto_itself_and_the_count_is_kept_sane():
    assert len(scene({"id": "pole", "shape": "cylinder", "position": [0, 1, 0], "around": 6}).parts) == 1
    assert len(scene({"id": "petal", "shape": "sphere", "position": [1, 0, 0], "around": 500}).parts) == 24
    twelve = scene({"id": "leaf", "shape": "sphere", "position": [0, 0, 2], "around": 3, "mirror": True})
    assert len(twelve.parts) == 3  # a ring is not also mirrored


# ------------------------------------------------------------------ hollow, soft-edged, windows
def test_new_options_are_kept_in_range_and_reach_the_builder():
    [p] = scene({"id": "mug", "shape": "lathe", "profile": [[-1, 0.8], [0, 0.9], [1, 0.9]], "wall": 99, "bevel": 7}).parts
    assert p.wall == 5 and p.bevel == 0.5
    [w] = scene({"id": "wall", "shape": "box", "surface": "windows"}).parts
    assert w.surface == "windows"
    props = build_tool_schema()["properties"]["parts"]["items"]["properties"]
    assert {"wall", "bevel", "sections", "around"} <= set(props)
    assert "loft" in props["shape"]["enum"] and "windows" in props["surface"]["enum"]
    # What only Pyxi sets is not offered to the model.
    assert "photo" not in build_tool_schema()["properties"] and "photo_view" not in build_tool_schema()["properties"]
    for word in ("`loft`", "`wall`", "`bevel`", "`around`", "windows"):
        assert word in SYSTEM


def test_a_models_photo_is_a_small_picture_held_in_it_never_an_address():
    part = {"id": "a", "shape": "box"}
    ok = "data:image/jpeg;base64,/9j/4AAQSkZJRg=="
    assert scene(part, photo=ok, photo_view=[40, 20]).photo == ok
    for bad in ("https://example.com/x.jpg", "data:image/svg+xml;base64,PHN2Zz4=", "data:image/jpeg;base64," + "A" * 500_000, 7, ""):
        assert scene(part, photo=bad).photo is None
    assert scene(part, photo_view=[400, 120]).photo_view == [360, 89]
    assert scene(part, photo_view=[1]).photo_view is None and scene(part, photo_view="front").photo_view is None
    # It stays with the model through an edit.
    from pyxi.scenes.objects import ObjectEdit, apply_edit

    edited = apply_edit(scene(part, photo=ok, photo_view=[40, 20]), ObjectEdit(title="Renamed"))
    assert edited.photo == ok and edited.photo_view == [40, 20]


def test_what_is_in_an_open_pot_does_not_turn_the_pot_into_a_ghost():
    soil = {"id": "soil", "shape": "cylinder", "size": [1, 0.3, 1], "position": [0, 0.2, 0], "label": "Soil"}
    profile = [[-1.5, 1.6], [0, 1.6], [1.5, 1.6]]
    hollow = scene({"id": "pot", "shape": "lathe", "profile": profile, "wall": 0.1, "size": [3.2, 3, 3.2]}, soil)
    solid = scene({"id": "pot", "shape": "lathe", "profile": profile, "size": [3.2, 3, 3.2]}, soil)
    assert tidy(hollow)[1] == [] and not tidy(hollow)[0].parts[0].casing
    assert tidy(solid)[0].parts[0].casing  # a solid one still does, as before
    assert not tidy(solid, open_up=False)[0].parts[0].casing  # unless told the model is an outside


def test_the_photo_examples_are_whole_models_with_nothing_hanging_in_the_air():
    for raw in PHOTO_EXAMPLES:
        made = ObjectScene.model_validate(raw)
        assert made.notes == [] and floating(made) == [] and tidy(made)[1] == []
        shapes = {p.shape for p in made.parts}
        assert 10 <= len(made.parts) <= 20
        shot = raster.render(made, 35, 20, 96)
        assert 0.08 < shot.mask.mean() < 0.7  # it draws, and it is one thing in the frame
    assert "loft" in {p.shape for p in ObjectScene.model_validate(PHOTO_EXAMPLES[0]).parts}
    assert shapes  # the last one too


# ------------------------------------------------------------------ the plain picture
def aspect(mask: np.ndarray) -> float:
    ys, xs = np.nonzero(mask)
    return (xs.max() - xs.min() + 1) / (ys.max() - ys.min() + 1)


def filled(mask: np.ndarray) -> float:
    ys, xs = np.nonzero(mask)
    return mask.sum() / ((xs.max() - xs.min() + 1) * (ys.max() - ys.min() + 1))


def test_a_box_from_the_front_is_its_front_and_from_the_side_its_side():
    made = scene({"id": "b", "shape": "box", "size": [4, 2, 1]})
    front, side, top = (raster.render(made, az, el, 128) for az, el in ((0, 0), (90, 0), (0, 89)))
    assert aspect(front.mask) == pytest.approx(2.0, abs=0.08) and filled(front.mask) > 0.97
    assert aspect(side.mask) == pytest.approx(0.5, abs=0.04)
    assert aspect(top.mask) == pytest.approx(4.0, abs=0.25)


def test_round_things_are_round_and_a_cone_points_up():
    ball = raster.render(scene({"id": "s", "shape": "sphere", "size": [3, 3, 3]}), 30, 20, 128).mask
    assert aspect(ball) == pytest.approx(1.0, abs=0.04) and filled(ball) == pytest.approx(math.pi / 4, abs=0.03)
    cone = raster.render(scene({"id": "c", "shape": "cone", "size": [2, 4, 2]}), 0, 0, 128).mask
    ys, xs = np.nonzero(cone)
    top_row = xs[ys == ys.min()]
    bottom_row = xs[ys == ys.max()]
    assert np.ptp(top_row) < 0.2 * np.ptp(bottom_row)  # a point at the top, the base at the bottom
    assert filled(cone) == pytest.approx(0.5, abs=0.06)


def test_a_part_is_turned_and_carried_by_its_parent_as_in_the_viewer():
    made = scene(
        {"id": "arm", "shape": "box", "size": [4, 0.4, 0.4], "position": [0, 0, 0], "rotation": [0, 0, 90]},
        {"id": "hand", "shape": "box", "size": [0.6, 0.6, 0.6], "position": [2.3, 0, 0], "parent": "arm"},
    )
    turn, at = raster.placements(made)["hand"]
    assert at == pytest.approx([0, 2.3, 0], abs=1e-9)  # the arm stands up, and the hand is on top of it
    shot = raster.render(made, 0, 0, 128)
    assert aspect(shot.mask) < 0.2  # tall and thin, not lying down
    ys, _ = np.nonzero(shot.part == 1)
    assert ys.max() < np.nonzero(shot.part == 0)[0].mean()  # the hand is above the arm's middle


def test_what_the_viewer_does_not_draw_is_not_drawn():
    made = scene(
        {"id": "a", "shape": "box", "size": [2, 2, 2]},
        {"id": "gone", "shape": "box", "size": [9, 9, 9], "hidden": True},
        {"id": "faint", "shape": "box", "size": [9, 9, 9], "opacity": 0},
        {"id": "flow", "shape": "arrow", "path": [[0, 0, 0], [9, 0, 0]]},
        {"id": "floor", "shape": "plane", "size": [30, 0.1, 30]},
        {"id": "tag", "shape": "marker", "position": [9, 9, 9]},
    )
    shot = raster.render(made, 0, 0, 96)
    assert set(np.unique(shot.part)) == {-1, 0} and aspect(shot.mask) == pytest.approx(1.0, abs=0.05)
    empty = raster.render(scene({"id": "flow", "shape": "arrow", "path": [[0, 0, 0], [1, 0, 0]]}), 0, 0, 64)
    assert not empty.mask.any()


def test_a_hollow_vessel_shows_its_inside_from_above_and_a_solid_one_its_lid():
    profile = [[-1.5, 1.2], [0, 1.2], [1.5, 1.2]]
    inner ={"id": "tea", "shape": "cylinder", "size": [2.0, 0.2, 2.0], "position": [0, -1.0, 0], "color": "#ff0000"}
    cup = scene({"id": "cup", "shape": "lathe", "profile": profile, "wall": 0.1, "color": "#ffffff"}, inner)
    block = scene({"id": "cup", "shape": "lathe", "profile": profile, "color": "#ffffff"}, inner)
    assert (raster.render(cup, 0, 80, 128).part == 1).sum() > 500  # the tea shows down in the cup
    assert (raster.render(block, 0, 80, 128).part == 1).sum() < 40  # sealed in (a stray pixel or two aside)


def test_colours_are_the_parts_own_and_a_material_gives_one_when_the_part_does_not():
    made = scene(
        {"id": "red", "shape": "box", "size": [2, 2, 2], "position": [-2, 0, 0], "color": "#c81e1e"},
        {"id": "wood", "shape": "box", "size": [2, 2, 2], "position": [2, 0, 0], "material": "wood"},
    )
    shot = raster.render(made, 0, 0, 128)
    red, wood = (shot.rgb[shot.part == i].mean(axis=0) for i in (0, 1))
    assert red[0] > 2.5 * red[1] and red[0] > 2.5 * red[2]
    assert wood[0] > wood[1] > wood[2]  # brown
    assert raster.part_rgb(Part(id="x", material="gold")) == (224, 178, 90)
    assert raster.hex_rgb("#nothex") == (221, 225, 231)


def test_every_shape_draws_something_of_about_its_own_size():
    shapes = [
        {"shape": "rounded_box"}, {"shape": "cylinder", "taper": 0.5}, {"shape": "capsule", "size": [1, 3, 1]}, {"shape": "torus", "size": [3, 0.6, 3]},
        {"shape": "ring", "size": [3, 0.4, 3]}, {"shape": "wedge"}, {"shape": "gear", "teeth": 12}, {"shape": "spring", "size": [1, 3, 1]},
        {"shape": "tube", "path": [[0, 0, 0], [1, 1, 0], [2, 0, 0]], "radius": 0.2},
        {"shape": "limb", "path": [[0, 0, 0], [0, 1, 0], [0, 2, 0.5]], "radii": [0.4, 0.3, 0.1]},
        {"shape": "blob", "lumps": [[0, 0, 0, 1, 1, 1], [1, 0.5, 0, 0.7, 0.7, 0.7]]},
        {"shape": "extrude", "axis": "z", "profile": [[-1, -1], [1, -1], [0, 1]], "size": [2, 2, 0.5]},
        {"shape": "lathe", "profile": [[-1, 0.5], [0, 1], [1, 0.3]]},
        {"shape": "loft", "axis": "x", "sections": CAR}, {"shape": "kit", "kit": "wheel", "axis": "x", "size": [0.5, 1.5, 1.5]},
        {"shape": "kit", "kit": "house", "size": [3, 3, 3]},
    ]
    for raw in shapes:
        made = scene({"id": "p", **raw})
        cloud = raster.points(made, detail=96)
        assert len(cloud.at) > 200 and np.isfinite(cloud.at).all() and np.isfinite(cloud.normal).all(), raw
        assert np.abs(np.linalg.norm(cloud.normal, axis=1) - 1).max() < 1e-3, raw
        # Within the part's own box, give or take the roundness of ends and a tube's width.
        half = np.array(made.parts[0].size) / 2 + 0.75
        centre = cloud.at - np.array(made.parts[0].position)
        if raw["shape"] not in ("tube", "limb", "blob", "lathe", "extrude"):
            assert (np.abs(centre) <= half).all(), raw
        assert raster.shoot(cloud, 30, 20, 96).mask.mean() > 0.02, raw


def test_the_same_angle_words_mean_the_same_place_as_in_the_viewer():
    right, up, toward = raster.view_axes(0, 0)
    assert toward == pytest.approx([0, 0, 1]) and right == pytest.approx([1, 0, 0]) and up == pytest.approx([0, 1, 0])
    _, _, from_right = raster.view_axes(90, 0)
    assert from_right == pytest.approx([1, 0, 0], abs=1e-9)  # 90 is from the model's +x side
    _, _, above = raster.view_axes(0, 30)
    assert above[1] == pytest.approx(0.5) and above[2] > 0


def test_a_whole_model_is_drawn_in_well_under_a_second():
    import time

    made = ObjectScene.model_validate(PHOTO_EXAMPLES[1])
    started = time.perf_counter()
    cloud = raster.points(made, detail=192)
    for az in (0, 40, 80, 120):
        raster.shoot(cloud, az, 15, 192)
    assert time.perf_counter() - started < 3.0  # generous: about 0.3 s on a laptop
