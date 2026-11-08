"""Library models from Khronos's glTF samples (product-render quality, CC0 or CC-BY 4.0, read
from each model's metadata) and Poly Haven (CC0 scans and models of real objects)."""

from __future__ import annotations

KHRONOS_RAW = "https://raw.githubusercontent.com/KhronosGroup/glTF-Sample-Assets/main/Models/"


def _ph(rid: str, ph: str, title: str, keywords: list[str] | None = None, **kw) -> dict:
    return {
        "id": rid,
        "title": title,
        "polyhaven": ph,
        "credit": f"{title}, Poly Haven (polyhaven.com/a/{ph})",
        "license": "CC0-1.0",
        "source": f"https://polyhaven.com/a/{ph}",
        "keywords": keywords or [],
        "parts": kw.pop("parts", None) or [{"id": "body", "rest": True}],
        **kw,
    }


def _kh(rid: str, name: str, title: str, keywords: list[str] | None = None, **kw) -> dict:
    return {
        "id": rid,
        "title": title,
        "khronos": name,
        "url": f"{KHRONOS_RAW}{name}/glTF-Binary/{name}.glb",
        "credit": "",
        "license": "",
        "source": f"https://github.com/KhronosGroup/glTF-Sample-Assets/tree/main/Models/{name}",
        "keywords": keywords or [],
        "parts": kw.pop("parts", None) or [{"id": "body", "rest": True}],
        **kw,
    }


MORE: list[dict] = [
    # Khronos showcase models.
    _kh(
        "concept_car",
        "CarConcept",
        "Sports car",
        ["car", "sports car", "supercar", "sedan", "coupe", "bmw", "mercedes", "ferrari", "porsche", "audi", "toyota"],
        version=2,  # split into wheels, doors, lights after library-v1 shipped it in one piece
        about="y up, front of the car towards +z (headlights at +z, tail lights at -z); wheels turn "
        "about the x axis; the doors hinge at their front edge",
        parts=[
            {"id": "front_left_wheel", "label": "Front wheel", "match": {"node": "WheelFrontL"}},
            {"id": "front_right_wheel", "label": "Front wheel", "match": {"node": "WheelFrontR"}},
            {"id": "rear_left_wheel", "label": "Rear wheel", "match": {"node": "WheelRearL"}},
            {"id": "rear_right_wheel", "label": "Rear wheel", "match": {"node": "WheelRearR"}},
            {"id": "left_door", "label": "Door", "match": {"node": "BodyDoorL"}},
            {"id": "right_door", "label": "Door", "match": {"node": "BodyDoorR"}},
            {"id": "headlights", "label": "Headlights", "match": {"node": "BodyHeadlights"}},
            {"id": "tail_lights", "label": "Tail lights", "match": {"node": "BodyTaillights$|BodyTurnsignalsRear"}},
            {"id": "grille", "label": "Bonnet vent", "note": "an air vent on top of the bonnet; this car has no front grille", "match": {"node": "BodyHoodTopgrill"}},
            {"id": "bonnet", "label": "Bonnet", "match": {"node": "BodyHood$|BodyHoodUnder"}},
            {"id": "cabin", "label": "Cabin", "match": {"node": "Interior"}},
            {"id": "body", "label": "Body", "rest": True},
        ],
    ),
    _kh("chronograph_watch", "ChronographWatch", "Chronograph wristwatch", ["watch", "chronograph"]),
    _kh("antique_camera", "AntiqueCamera", "Antique bellows camera", ["camera", "photography"]),
    _kh("sunglasses", "SunglassesKhronos", "Sunglasses", ["sunglasses", "lenses"]),
    _kh("water_bottle", "WaterBottle", "Water bottle", ["bottle"]),
    _kh("refrigerator", "CommercialRefrigerator", "Refrigerator", ["fridge", "refrigerator"]),
    _kh("barn_lamp", "AnisotropyBarnLamp", "Barn lamp", ["lamp"]),
    _kh("shoe", "MaterialsVariantsShoe", "Sneaker", ["shoe", "sneaker"]),
    _kh("sofa_velvet", "GlamVelvetSofa", "Velvet sofa", ["sofa"]),
    _kh("chair_sheen", "SheenChair", "Armchair (fabric)", ["chair"]),
    _kh("teacup", "DiffuseTransmissionTeacup", "Teacup", ["teacup"]),
    _kh("mosquito_amber", "MosquitoInAmber", "Mosquito in amber", ["amber", "fossil"]),
    _kh("fish", "BarramundiFish", "Fish (barramundi)", ["fish"]),
    _kh("traffic_cone", "TrafficCone", "Traffic cone", ["cone"]),
    _kh("vase_flowers", "GlassVaseFlowers", "Flowers in a glass vase", ["flowers"]),
    _kh("plant_kh", "DiffuseTransmissionPlant", "House plant", ["plant", "photosynthesis"]),
    # Electronics and appliances.
    _ph("circuit_board", "circuit_board", "Circuit board", ["pcb", "electronics", "chip"]),
    _ph("laptop", "classic_laptop", "Laptop", ["computer"]),
    _ph("game_console", "gaming_console", "Game console", ["console", "gaming"]),
    _ph("gamepad", "gamepad", "Game controller", ["controller"]),
    _ph("camera", "Camera_01", "Camera", ["camera"]),
    _ph("television", "Television_01", "Old television (CRT)", ["tv", "crt"]),
    _ph("television_2", "television_02", "Television", ["tv"]),
    _ph("cassette_player", "portable_cassette_player", "Portable cassette player", ["walkman"]),
    _ph("radio", "vintage_radio_transceiver", "Radio transceiver", ["radio"]),
    _ph("video_camera", "vintage_video_camera", "Video camera", ["camcorder"]),
    _ph("microwave", "vintage_microwave", "Microwave oven", ["microwave"]),
    _ph("multimeter", "retro_multimeter", "Multimeter", ["electricity"]),
    _ph("security_camera", "security_camera_01", "Security camera", ["cctv"]),
    _ph("lightbulb", "lightbulb_01", "Light bulb", ["filament"]),
    _ph("led_bulb", "lightbulb_led", "LED bulb", ["led"]),
    _ph("digital_watch", "digital_wrist_watch", "Digital watch", ["watch"]),
    _ph("alarm_clock", "alarm_clock_01", "Alarm clock", ["clock"]),
    _ph("kettle", "vintage_electric_kettle", "Electric kettle", ["kettle"]),
    _ph("stove", "electric_stove", "Electric stove", ["stove"]),
    _ph("ceiling_fan", "ceiling_fan", "Ceiling fan", ["fan"]),
    _ph("air_conditioner", "exterior_aircon_unit", "Air conditioner unit", ["ac", "heat pump"]),
    _ph("generator", "portable_generator", "Portable generator", ["generator", "engine"]),
    _ph("microscope", "industrial_microscope", "Microscope", ["microscope"]),
    _ph("old_microscope", "vintage_microscope", "Vintage microscope", ["microscope"]),
    _ph("binoculars", "binoculars", "Binoculars", ["binoculars"]),
    _ph("magnifier", "magnifying_glass_01", "Magnifying glass", ["lens"]),
    _ph("projector", "filmstrip_projector_8mm", "Film projector", ["projector"]),
    _ph("desk_lamp", "desk_lamp_arm_01", "Desk lamp", ["lamp"]),
    _ph("megaphone", "Megaphone_01", "Megaphone", ["sound"]),
    _ph("smoke_alarm", "fire_alarm", "Fire alarm", ["smoke detector"]),
    _ph("flashlight", "signal_flashlight", "Flashlight", ["torch"]),
    _ph("telephone", "vintage_telephone_wall_clock", "Vintage telephone", ["phone"]),
    _ph("boombox", "boombox", "Boombox", ["speaker"]),
    _ph("spacecraft_instrument", "vintage_spacecraft_instrument", "Spacecraft instrument", ["spacecraft"]),
    # Food.
    _ph("apple", "food_apple_01", "Apple", ["fruit"]),
    _ph("avocado", "food_avocado_01", "Avocado", ["fruit"]),
    _ph("kiwi", "food_kiwi_01", "Kiwi fruit", ["fruit"]),
    _ph("lime", "food_lime_01", "Lime", ["citrus"]),
    _ph("lemon", "lemon", "Lemon", ["citrus"]),
    _ph("lychee", "food_lychee_01", "Lychee", ["fruit"]),
    _ph("pear", "food_pears_asian_01", "Asian pear", ["fruit"]),
    _ph("pomegranate", "food_pomegranate_01", "Pomegranate", ["fruit"]),
    _ph("bananas", "bananas", "Bananas", ["fruit"]),
    _ph("ginger", "food_ginger_01", "Ginger root", ["root"]),
    _ph("sweet_potato", "sweet_potato", "Sweet potato", ["vegetable"]),
    _ph("onion", "yellow_onion", "Onion", ["vegetable"]),
    _ph("croissant", "croissant", "Croissant", ["pastry"]),
    _ph("burger_buns", "hamburger_buns", "Burger buns", ["bread"]),
    _ph("carrot_cake", "carrot_cake", "Carrot cake", ["cake"]),
    _ph("chocolate_cake", "strawberry_chocolate_cake", "Chocolate cake", ["cake"]),
    # Tools.
    _ph("drill", "Drill_01", "Power drill", ["power tool"]),
    _ph("drill_press", "drill_press_01", "Drill press", ["drill"]),
    _ph("hand_drill", "vintage_hand_drill", "Hand drill", ["gears"]),
    _ph("wrench", "adjustable_wrench", "Adjustable wrench", ["spanner"]),
    _ph("ratchet", "ratchet_wrench", "Ratchet wrench", ["ratchet"]),
    _ph("hammer", "cross_pein_hammer", "Hammer", ["hammer"]),
    _ph("screwdriver", "screwdriver", "Screwdriver", ["screw"]),
    _ph("pliers", "pliers", "Pliers", ["lever"]),
    _ph("saw", "handsaw_wood", "Handsaw", ["saw"]),
    _ph("axe", "hatchet", "Hatchet", ["axe"]),
    _ph("tape_measure", "measuring_tape_01", "Tape measure", ["measure"]),
    _ph("vice", "bench_vice_01", "Bench vice", ["clamp", "screw thread"]),
    _ph("bolt_cutters", "bolt_cutters_01", "Bolt cutters", ["leverage"]),
    _ph("tire_pump", "tire_pump", "Tyre pump", ["air pressure"]),
    _ph("blowtorch", "brass_blowtorch", "Blowtorch", ["flame"]),
    _ph("hand_plane", "hand_plane_no4", "Hand plane", ["woodwork"]),
    _ph("hand_truck", "hand_truck", "Hand truck", ["lever"]),
    # Around the house and town.
    _ph("fire_hydrant", "fire_hydrant", "Fire hydrant", ["water pressure"]),
    _ph("fire_extinguisher", "korean_fire_extinguisher_01", "Fire extinguisher", ["fire"]),
    _ph("street_lamp", "street_lamp_01", "Street lamp", ["street light"]),
    _ph("grandfather_clock", "vintage_grandfather_clock_01", "Grandfather clock", ["pendulum"]),
    _ph("mantel_clock", "mantel_clock_01", "Mantel clock", ["clock"]),
    _ph("pocket_watch", "vintage_pocket_watch", "Pocket watch", ["gears"]),
    _ph("compass", "seadogs_compass", "Compass", ["magnet", "navigation"]),
    _ph("oil_lamp", "vintage_oil_lamp", "Oil lamp", ["lamp"]),
    _ph("gas_mask", "old_gas_mask", "Gas mask", ["filter"]),
    _ph("wheelchair", "wheelchair_01", "Wheelchair", ["wheelchair"]),
    _ph("life_jacket", "life_jacket", "Life jacket", ["buoyancy"]),
    _ph("rubber_duck", "rubber_duck_toy", "Rubber duck", ["float"]),
    _ph("suitcase", "vintage_suitcase", "Suitcase", ["luggage"]),
    _ph("spectacles", "round_spectacles", "Glasses", ["lens"]),
    _ph("armchair", "ArmChair_01", "Armchair", ["chair"]),
    _ph("lounge_chair", "mid_century_lounge_chair", "Lounge chair", ["chair"]),
    _ph("sofa", "Sofa_01", "Sofa", ["couch"]),
    _ph("dining_table", "dining_table", "Dining table", ["table"]),
    _ph("treasure_chest", "treasure_chest", "Treasure chest", ["chest"]),
    _ph("barrel", "wine_barrel_01", "Wine barrel", ["barrel"]),
    # Ships and history.
    _ph("sailing_ship", "dutch_ship_large_01", "Sailing ship", ["galleon", "sails"]),
    _ph("cannon", "cannon_01", "Cannon", ["artillery"]),
    _ph("katana", "antique_katana_01", "Katana", ["sword"]),
    _ph("shield", "kite_shield", "Kite shield", ["medieval"]),
    # Sport, games, music, science.
    _ph("football", "football", "Football", ["soccer"]),
    _ph("american_football", "american_football", "American football", ["football"]),
    _ph("baseball", "baseball_01", "Baseball", ["baseball"]),
    _ph("dartboard", "dartboard", "Dartboard", ["darts"]),
    _ph("chess_set", "chess_set", "Chess set (wooden)", ["chess"]),
    _ph("ukulele", "Ukulele_01", "Ukulele", ["strings"]),
    _ph("bunsen_burner", "bunsen_burner", "Bunsen burner", ["lab"]),
    _ph("chemistry_set", "chemistry_set", "Chemistry set", ["lab"]),
    _ph("marble_bust", "marble_bust_01", "Marble bust", ["sculpture"]),
    _ph("spinning_wheel", "spinning_wheel_01", "Spinning wheel", ["wheel"]),
    _ph("moon_rock", "moon_rock_01", "Moon rock", ["lunar"]),
]


def _si(rid: str, title: str, package: str, file: str, keywords: list[str] | None = None, **kw) -> dict:
    """A Smithsonian 3D scan whose Open Access record marks it CC0 (scripts/library/smithsonian.py)."""
    return {
        "id": rid,
        "title": title,
        "url": f"https://3d-api.si.edu/content/document/3d_package:{package}/{file}",
        "credit": f"{title}, Smithsonian Institution (Open Access, CC0)",
        "license": "CC0-1.0",
        "source": f"https://3d.si.edu/object/3d/{package}",
        "keywords": keywords or [],
        "parts": kw.pop("parts", None) or [{"id": "body", "rest": True}],
        **kw,
    }


MORE += [
    _si("bell_x1", "Bell X-1 rocket plane", "6c69a6bb-55e6-4356-8725-120ff7f8d652", "p0-Part_01-100k-2048.glb",
        ["bell x-1", "sound barrier", "supersonic", "rocket plane", "chuck yeager"]),
]
