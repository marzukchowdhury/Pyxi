"""The 3D explanation layer's data model: scene spec, exploded views, gallery."""

from blip.scenes.explode import build_exploded_scene
from blip.scenes.gallery import GALLERY, TITLES, get_scene
from blip.scenes.spec import SceneEdge, SceneNode, SceneSpec, SceneStep, parse_scene, tool_schema

__all__ = [
    "GALLERY",
    "TITLES",
    "SceneEdge",
    "SceneNode",
    "SceneSpec",
    "SceneStep",
    "build_exploded_scene",
    "get_scene",
    "parse_scene",
    "tool_schema",
]
