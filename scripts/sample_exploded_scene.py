import json


def n(i, label, rect, depth, parent=None, glow=False, role="Pane"):
    return {
        "id": f"e{i}",
        "label": label,
        "detail": role,
        "shape": "screen",
        "size": 1,
        "parent": parent,
        "screen_rect": rect,
        "depth": depth,
        "glow": glow,
        "image": None,
    }


nodes = [
    n(1, "Settings - App", [310, 240, 820, 420], 0, glow=True, role="Window"),
    n(2, "Sidebar", [310, 279, 190, 381], 1, "e1", role="List"),
    n(3, "Appearance page", [500, 279, 630, 381], 1, "e1", role="Pane"),
    n(4, "General", [325, 300, 160, 26], 2, "e2", role="ListItem"),
    n(5, "Appearance", [325, 334, 160, 28], 2, "e2", role="ListItem"),
    n(6, "Privacy", [325, 370, 160, 26], 2, "e2", role="ListItem"),
    n(7, "Dark mode", [533, 338, 104, 22], 2, "e3", role="CheckBox"),
    n(8, "Reduce motion", [533, 373, 124, 22], 2, "e3", role="CheckBox"),
    n(9, "Export", [530, 409, 76, 33], 2, "e3", role="Button"),
]
scene = {
    "id": "scene_explode",
    "title": "How this window is built",
    "subtitle": "Settings - App",
    "layout": "exploded",
    "mode": "hologram",
    "theme": "blueprint",
    "anchor": None,
    "nodes": nodes,
    "edges": [],
    "auto_play": False,
    "steps": [
        {
            "title": "Lifting it apart",
            "narration": "Here's how Settings is put together.",
            "focus": [],
            "camera": "overview",
        },
        {
            "title": "The main sections",
            "narration": "Each layer back is one level deeper in the hierarchy.",
            "focus": ["e2", "e3"],
            "camera": "side",
        },
        {
            "title": "The controls",
            "narration": "And these are the controls you actually touch.",
            "focus": ["e7", "e8", "e9"],
            "camera": "focus",
        },
    ],
}
print(json.dumps(scene))
