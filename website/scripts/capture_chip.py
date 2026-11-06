"""The site's 3D example: "Hey Pyxi, how is an AI chip built? Show me in 3D."

A hand-built model in Pyxi's own ObjectScene format (the same format the model writes and the
same way the README's 3D shots are made), rendered by Pyxi's real 3D renderer in the overlay
preview, then cropped into public/img.

  1. cd desktop && npx vite --config vite.web.config.ts        (the overlay preview, port 5199)
  2. ..\\.venv\\Scripts\\python scripts\\capture_chip.py [beat ...]   (from website/)

Nothing here is part of the app; it only stages a picture.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from PIL import Image
from playwright.async_api import async_playwright

from pyxi.scenes.objects import Beat, Key, Motion, ObjectScene, Part

URL = "http://localhost:5199"
GL = ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"]
SITE = Path(__file__).resolve().parents[1]
OUT = SITE / "public" / "img"
TMP = SITE / ".shots"

# Heights (y is up). The package is about 6.4 units across.
SUB_Y, INT_Y, DIE_Y, HBM_Y = 0.0, 0.2, 0.33, 0.29
LAYER = 0.055  # one DRAM die
STACKS = [(-1.72, -0.66), (-1.72, 0.66), (1.72, -0.66), (1.72, 0.66)]


def k(t: float, **kw) -> Key:
    return Key(t=t, **kw)


def ai_chip() -> ObjectScene:
    parts: list[Part] = [
        Part(id="substrate", shape="rounded_box", size=[6.4, 0.24, 6.4], position=[0, SUB_Y, 0],
             material="painted", color="#1c3428", label="Package substrate"),
        Part(id="interposer", shape="box", size=[4.7, 0.1, 3.3], position=[0, INT_Y, 0],
             material="metal", color="#55606f", label="Silicon interposer"),
        Part(id="die", shape="box", size=[2.3, 0.12, 2.7], position=[0, DIE_Y, 0],
             material="dark_metal", color="#232833", label="Compute die"),
        Part(id="lid", shape="rounded_box", size=[5.3, 0.2, 4.1], position=[0, 0.66, 0],
             material="brushed_metal", color="#c4c9d1", casing=True, label="Heat spreader"),
    ]
    # Solder balls underneath: they connect the package to the motherboard.
    for i, x in enumerate((-2.6, -1.3, 0.0, 1.3, 2.6)):
        for j, z in enumerate((-2.6, -1.3, 0.0, 1.3, 2.6)):
            parts.append(Part(id=f"ball_{i}{j}", shape="sphere", size=[0.32, 0.32, 0.32],
                              position=[x, -0.2, z], material="metal", color="#cfd4db",
                              label="Solder balls" if (i, j) == (4, 4) else None))
    # Capacitors along the edge of the substrate.
    for n, (x, z) in enumerate([(-2.7, -2.3), (-2.3, -2.7), (2.7, 2.3), (2.3, 2.7), (2.7, -2.3), (-2.7, 2.3), (2.3, -2.7), (-2.3, 2.7)]):
        parts.append(Part(id=f"cap_{n}", shape="box", size=[0.34, 0.16, 0.2], position=[x, 0.2, z],
                          rotation=[0, 45 if n % 2 else -45, 0], material="ceramic", color="#9b7448"))
    # The compute die's grid of cores, lit when they work.
    for i, x in enumerate((-0.81, -0.27, 0.27, 0.81)):
        for j, z in enumerate((-0.96, -0.32, 0.32, 0.96)):
            parts.append(Part(id=f"core_{i}{j}", parent="die", shape="box", size=[0.46, 0.012, 0.56],
                              position=[x, 0.066, z], material="emissive", color="#6fa8ff", opacity=0.28))
    # Four HBM stacks: a base logic die with DRAM dies piled on top.
    for s, (x, z) in enumerate(STACKS):
        parts.append(Part(id=f"hbm{s}_base", shape="box", size=[0.92, 0.07, 1.12], position=[x, HBM_Y, z],
                          material="dark_metal", color="#2a303b",
                          ))
        for layer in range(4):
            top = layer == 3
            parts.append(Part(
                id=f"hbm{s}_d{layer}", shape="box", size=[0.92, LAYER - 0.008, 1.12],
                position=[x, HBM_Y + 0.062 + layer * LAYER, z],
                material="dark_metal" if not top else "metal", color="#3a414e" if not top else "#5d6675",
                label=("HBM memory stack" if top else "DRAM die" if layer == 1 else None) if s == 3 else None,
            ))
    # Through-silicon vias: copper columns through one stack, shown (already raised) in the close-up.
    x, z = STACKS[3]
    for n, dz in enumerate((-0.3, 0.0, 0.3)):
        parts.append(Part(id=f"tsv_{n}", shape="cylinder", axis="y", size=[0.05, 1.5, 0.05],
                          position=[x * 1.12 + 0.25, 2.45, z * 1.3 + dz], material="gold", color="#e0a55a",
                          opacity=0, label="Through-silicon vias" if n == 1 else None))
    # Data on its way from memory to the cores.
    parts += [
        Part(id="flow_l", shape="arrow", path=[[-1.5, 0.5, 0.0], [-0.6, 0.5, 0.0]], radius=0.05, opacity=0),
        Part(id="flow_r", shape="arrow", path=[[1.5, 0.5, 0.0], [0.6, 0.5, 0.0]], radius=0.05, opacity=0),
    ]

    motions: list[Motion] = [
        # The lid lifts away to show what's underneath.
        Motion(part="lid", ease="in_out", keys=[k(2.0, position=[0, 0.66, 0], opacity=1), k(3.4, position=[0, 3.6, 0], opacity=0)]),
        # Exploded view: every layer rises, the memory dies spread apart.
        Motion(part="interposer", keys=[k(4.0, position=[0, INT_Y, 0]), k(5.6, position=[0, 1.0, 0]), k(8.6, position=[0, 1.0, 0]), k(10.0, position=[0, INT_Y, 0])]),
        Motion(part="die", keys=[k(4.0, position=[0, DIE_Y, 0]), k(5.8, position=[0, 2.1, 0]), k(8.6, position=[0, 2.1, 0]), k(10.0, position=[0, DIE_Y, 0])]),
    ]
    for s, (x, z) in enumerate(STACKS):
        motions.append(Motion(part=f"hbm{s}_base", keys=[
            k(4.0, position=[x, HBM_Y, z]), k(5.8, position=[x * 1.12, 1.8, z * 1.3]),
            k(8.6, position=[x * 1.12, 1.8, z * 1.3]), k(10.0, position=[x, HBM_Y, z])]))
        for layer in range(4):
            y0 = HBM_Y + 0.062 + layer * LAYER
            y1 = 2.05 + layer * 0.32
            motions.append(Motion(part=f"hbm{s}_d{layer}", keys=[
                k(4.0, position=[x, y0, z]), k(5.9 + layer * 0.08, position=[x * 1.12, y1, z * 1.3]),
                k(8.6, position=[x * 1.12, y1, z * 1.3]), k(10.0, position=[x, y0, z])]))
    motions += [Motion(part=f"tsv_{n}", keys=[k(6.4, opacity=0), k(7.0, opacity=1), k(8.4, opacity=1), k(8.8, opacity=0)]) for n in range(3)]
    motions += [
        Motion(part="flow_l", keys=[k(10.4, opacity=0), k(10.9, opacity=1)]),
        Motion(part="flow_r", keys=[k(10.4, opacity=0), k(10.9, opacity=1)]),
        Motion(part="flow_l", kind="pulse", period=0.9, start=10.9),
        Motion(part="flow_r", kind="pulse", period=0.9, start=10.9, phase=0.5),
    ]
    motions += [Motion(part=f"core_{i}{j}", kind="pulse", period=1.1, start=10.6, phase=((i + j) % 4) / 4)
                for i in range(4) for j in range(4)][:12]

    return ObjectScene(
        title="How an AI chip is built",
        subtitle="A compute die and stacked memory, bonded onto one package",
        style="studio",
        parts=parts,
        motions=motions,
        beats=[
            Beat(at=0, title="The finished chip", camera="iso",
                 narration="This is an AI accelerator: one big compute chip with stacks of fast memory beside it, all on one package."),
            Beat(at=2.0, title="Under the lid", camera="iso", focus=["die", "hbm3_d3"],
                 narration="Lift the metal lid and there's the compute die in the middle, with memory stacks on either side."),
            Beat(at=4.0, title="Built in layers", camera="iso",
                 narration="It's built like a sandwich. Solder balls meet the motherboard, the substrate spreads the wiring out, and a thin silicon interposer links the chips."),
            Beat(at=6.4, title="Memory, stacked up", camera="close", focus=["hbm3_d0", "hbm3_d1", "hbm3_d2", "hbm3_d3", "hbm3_base", "tsv_1"],
                 narration="Each memory stack is several DRAM chips piled on a logic die, wired straight through by copper columns called through-silicon vias."),
            Beat(at=8.6, title="Bonded back together", camera="iso",
                 narration="Tiny solder bumps bond it all back down, so the memory ends up just millimetres from the compute."),
            Beat(at=10.4, title="Why it's built this way", camera="iso", focus=["die", "flow_l", "flow_r"],
                 narration="That short trip is the point: the cores can pull data from memory fast enough to keep up, which is what AI models need."),
        ],
    )


async def capture(beats: list[int]) -> None:
    TMP.mkdir(exist_ok=True)
    model = ai_chip().model_dump(mode="json")
    async with async_playwright() as p:
        b = None
        for kw in ({}, {"channel": "msedge"}, {"channel": "chrome"}):
            try:
                b = await p.chromium.launch(args=GL, **kw)
                break
            except Exception:  # noqa: BLE001
                continue
        assert b, "no browser"
        for beat in beats:
            pg = await b.new_page(viewport={"width": 1440, "height": 900})
            await pg.goto(f"{URL}/overlay.html?backdrop=1&live=1")
            await pg.wait_for_timeout(1300)
            await pg.mouse.move(1010, 470)
            ev = {"type": "object", "scene": model, "audio_key": "chip:site", "restart": True, "place": None}
            await pg.evaluate("(e) => window.__pyxi.inject(e)", ev)
            await pg.wait_for_timeout(2500)
            if beat > 1:
                await pg.get_by_role("button", name=f"Beat {beat}").click()
            await pg.mouse.move(1010, 470)
            await pg.wait_for_timeout(3800)
            raw = TMP / f"chip-beat{beat}.png"
            await pg.screenshot(path=str(raw))
            print("shot", raw)
            await pg.close()
        await b.close()


def publish(beat: int, box: tuple[int, int, int, int]) -> None:
    im = Image.open(TMP / f"chip-beat{beat}.png").convert("RGB").crop(box)
    im.save(OUT / "ai-chip.webp", "WEBP", quality=88, method=6)
    im.resize((600, round(im.height * 600 / im.width)), Image.LANCZOS).save(OUT / "ai-chip-600.webp", "WEBP", quality=86, method=6)
    print("published beat", beat, im.size)


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["publish"]:
        publish(int(args[1]), tuple(int(v) for v in args[2:6]))  # type: ignore[arg-type]
    else:
        asyncio.run(capture([int(a) for a in args] or [1, 2, 3, 4, 5, 6]))
