"""Make the site's images from the real product screenshots in docs/images.

Run from website/:  python scripts/images.py
Writes WebP files (two widths each) into public/img and the icons into public/.
"""

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "docs" / "images"
OUT = Path(__file__).resolve().parents[1] / "public" / "img"
PUBLIC = OUT.parent
OUT.mkdir(parents=True, exist_ok=True)

# name -> widths to export
SHOTS = {
    "targeting": (1440, 800),
    "explanation": (1440, 800),
    "neural-net": (1440, 800),
    "exploded": (1440, 800),
    "memory-palace": (1440, 800),
    "walkthrough": (1440, 800),
    "field-notes": (1000, 640),
    "notch": (480,),
}

# The panel shots were captured without a key, so they carry a "demo mode" banner a beta user
# never sees once they've joined. Drop that strip (and only that strip) and keep the rest as is.
PANELS = ("panel-team", "panel-chat", "panel-memory", "voice-studio")
BANNER = (112, 276)  # rows to cut: from under the header to just above the tabs
PANEL_HEIGHT = 1300


def save(im: Image.Image, name: str, widths: tuple[int, ...]) -> None:
    for w in widths:
        out = im if im.width == w else im.resize((w, round(im.height * w / im.width)), Image.LANCZOS)
        suffix = "" if w == widths[0] else f"-{w}"
        out.save(OUT / f"{name}{suffix}.webp", "WEBP", quality=86, method=6)
        print(f"{name}{suffix}.webp", out.size)


for name, widths in SHOTS.items():
    save(Image.open(SRC / f"{name}.png").convert("RGB"), name, widths)

for name in PANELS:
    src = Image.open(SRC / f"{name}.png").convert("RGB")
    top = src.crop((0, 0, src.width, BANNER[0]))
    rest = src.crop((0, BANNER[1], src.width, BANNER[1] + PANEL_HEIGHT - BANNER[0]))
    panel = Image.new("RGB", (src.width, PANEL_HEIGHT))
    panel.paste(top, (0, 0))
    panel.paste(rest, (0, BANNER[0]))
    save(panel, name, (880, 440))

icon = Image.open(ROOT / "desktop" / "resources" / "icon.png").convert("RGBA")
for size, fname in ((180, "apple-touch-icon.png"), (512, "icon-512.png"), (192, "icon-192.png"), (64, "favicon-64.png")):
    icon.resize((size, size), Image.LANCZOS).save(PUBLIC / fname, optimize=True)
icon.resize((48, 48), Image.LANCZOS).save(PUBLIC / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])
print("icons ok")
