"""The library's models: where each comes from, its licence, and how it splits into parts.

Part rules (first match wins; ``rest`` takes whatever is left):
    match.node / match.not_node   regex on the node path ("root/child/mesh")
    match.material                regex on the material name
    match.region                  [x0, y0, z0, x1, y1, z1] box in stage units (after the model
                                  is upright, centred and ``size`` units on its longest side)
``about`` tells the explaining model what it's looking at: which way is front, what's where.
"""

from __future__ import annotations

NASA = "https://raw.githubusercontent.com/nasa/NASA-3D-Resources/master/3D%20Models/"
NASA_CREDIT = "NASA 3D Resources"
NASA_LICENSE = "Public domain (NASA media guidelines)"


def nasa(name: str) -> str:
    q = name.replace(" ", "%20").replace("(", "%28").replace(")", "%29")
    return f"{NASA}{q}/{q}.glb"


RECIPES: list[dict] = []


def _nasa(rid: str, name: str, title: str, keywords: list[str], parts: list[dict] | None = None, **kw) -> dict:
    return {
        "id": rid,
        "title": title,
        "url": nasa(name),
        "credit": f"{name}, {NASA_CREDIT}",
        "license": NASA_LICENSE,
        "source": f"https://github.com/nasa/NASA-3D-Resources/tree/master/3D%20Models/{name.replace(' ', '%20')}",
        "keywords": keywords,
        "parts": parts or [{"id": "body", "rest": True}],
        **kw,
    }


RECIPES += [
    _nasa(
        "saturn_v",
        "Saturn V",
        "Saturn V rocket",
        ["saturn v", "apollo rocket", "moon rocket", "rocket stages", "staging"],
        about="standing upright, y up: first stage at the bottom, the Apollo spacecraft and escape tower on top",
        parts=[
            {"id": "spacecraft", "label": "Apollo spacecraft", "match": {"region": [-9, 2.7, -9, 9, 9, 9]}},
            {"id": "third_stage", "label": "Third stage", "match": {"region": [-9, 1.1, -9, 9, 2.7, 9]}},
            {"id": "second_stage", "label": "Second stage", "match": {"region": [-9, -1.2, -9, 9, 1.1, 9]}},
            {"id": "first_stage", "label": "First stage", "rest": True},
        ],
    ),
    _nasa(
        "space_shuttle",
        "Space Shuttle (A)",
        "Space Shuttle at launch",
        ["space shuttle", "orbiter", "shuttle launch", "solid rocket boosters", "external tank"],
        about="lying along z: nose toward -z, engines toward +z; tank and boosters under the orbiter (-y)",
        parts=[
            {"id": "main_engines", "label": "Main engines", "match": {"node": "group13"}},
            {"id": "external_tank", "label": "External tank", "match": {"material": "^shuttlesrbsande6$"}},
            {"id": "boosters", "label": "Rocket boosters", "match": {"material": "^shuttlesrbsande|^blinn2SG$"}},
            {"id": "orbiter", "label": "Orbiter", "rest": True},
        ],
    ),
    _nasa(
        "perseverance",
        "Mars 2020 Perseverance Rover",
        "Perseverance Mars rover",
        ["perseverance", "mars rover", "rover"],
        about="y up, front toward +z; six wheels on rocker-bogie legs, camera mast at the front",
        parts=[
            {"id": "wheels", "label": "Six wheels", "match": {"node": "Wheels_objs"}},
            {"id": "mast", "label": "Camera mast", "match": {"node": "^Scene/Cylinder"}},
            {"id": "body", "label": "Rover body", "rest": True},
        ],
    ),
    _nasa(
        "ingenuity",
        "Ingenuity Mars Helicopter",
        "Ingenuity Mars helicopter",
        ["ingenuity", "mars helicopter", "helicopter", "coaxial rotor"],
        about="y up; two stacked rotors spin in opposite directions about the vertical axis",
        parts=[
            {"id": "upper_rotor", "label": "Upper rotor", "match": {"node": "rotors_01"}},
            {"id": "lower_rotor", "label": "Lower rotor", "match": {"node": "rotors_02"}},
            {"id": "solar_panel", "label": "Solar panel", "match": {"node": "SOLAR_PANEL|solar_panel"}},
            {"id": "legs", "label": "Landing legs", "match": {"node": "leg_|STOWED_ROTOR"}},
            {"id": "body", "label": "Body", "rest": True},
        ],
    ),
    _nasa(
        "hubble",
        "Hubble Space Telescope (A)",
        "Hubble Space Telescope",
        ["hubble", "space telescope", "telescope"],
        about="tube upright, y up: the aperture (where light comes in) at the top, solar arrays on its sides",
        parts=[
            {"id": "solar_arrays", "label": "Solar arrays", "match": {"material": "^hbltel_3$"}},
            {"id": "telescope", "label": "Telescope tube", "rest": True},
        ],
    ),
    _nasa(
        "apollo_lm",
        "Apollo Lunar Module",
        "Apollo Lunar Module",
        ["lunar module", "apollo lander", "moon landing", "eagle"],
        about="upright, y up: the gold descent stage with legs below, the crew's ascent stage on top",
        parts=[
            {"id": "ascent_stage", "label": "Ascent stage", "match": {"region": [-9, 0.15, -9, 9, 9, 9]}},
            {"id": "descent_stage", "label": "Descent stage", "rest": True},
        ],
    ),
    _nasa("global_hawk", "Global Hawk", "Global Hawk drone", ["global hawk", "drone", "uav", "surveillance aircraft"]),
    _nasa(
        "voyager",
        "Voyager Probe (A)",
        "Voyager space probe",
        ["voyager", "space probe", "golden record"],
        parts=[
            {"id": "dish", "label": "High-gain antenna", "match": {"material": "VoyagerDish|VoyagerDiskEmblem"}},
            {"id": "probe", "label": "Probe body", "rest": True},
        ],
    ),
    _nasa("astronaut", "Astronaut", "Astronaut in a spacesuit", ["astronaut", "spacesuit", "space suit", "spacewalk"]),
]


from recipes_more import MORE  # noqa: E402

RECIPES += MORE


# Models added with scripts/library/add.py (downloaded, owned, or generated once and shared).
_EXTRA = __import__("pathlib").Path(__file__).with_name("extra.json")
if _EXTRA.exists():
    RECIPES += __import__("json").loads(_EXTRA.read_text(encoding="utf-8"))
