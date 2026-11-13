"""The made-up files the "Just ask" film is recorded with. Nothing here is real: every name,
number and letter is invented for the film, so nothing personal can end up in a take.

    .venv/Scripts/python.exe website/tools/film_kit.py

Writes website/.shots/film2/kit/ (git-ignored):
  desktop/     60 files for "organise my desktop": screenshots, documents, pictures, installers
  sales.xlsx   a small sales table for the pivot scene, and sales-answer.txt with the right totals
  letter/      the letter "Sam" sends (three planted mistakes) and what the fixed one should say
  quick/       three photos for "turn these photos into one PDF", and slides to be quizzed on
"""

from __future__ import annotations

import random
from pathlib import Path

from docx import Document
from openpyxl import Workbook
from PIL import Image, ImageDraw

KIT = Path(__file__).resolve().parents[1] / ".shots" / "film2" / "kit"
rng = random.Random(11)


def picture(path: Path, size=(1280, 800), text: str | None = None, fmt: str | None = None):
    """A soft two-colour picture, different every time, optionally with a line of text on it."""
    a = tuple(rng.randint(30, 220) for _ in range(3))
    b = tuple(rng.randint(30, 220) for _ in range(3))
    img = Image.new("RGB", size, a)
    d = ImageDraw.Draw(img)
    for y in range(size[1]):
        k = y / size[1]
        d.line([(0, y), (size[0], y)], fill=tuple(int(a[i] + (b[i] - a[i]) * k) for i in range(3)))
    for _ in range(5):
        x, y, r = rng.randint(0, size[0]), rng.randint(0, size[1]), rng.randint(60, 260)
        d.ellipse([x - r, y - r, x + r, y + r], fill=tuple(min(255, c + 30) for c in b))
    if text:
        d.rectangle([60, 60, size[0] - 60, 150], fill=(250, 250, 250))
        d.text((84, 92), text, fill=(20, 20, 20))
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, fmt)


def desktop():
    out = KIT / "desktop"
    for i in range(22):
        day, h, m = rng.randint(1, 28), rng.randint(8, 22), rng.randint(0, 59)
        picture(out / f"Screenshot 2026-10-{day:02d} {h:02d}{m:02d}{i:02d}.png", text="A screenshot made for the film")
    photos = ["beach walk", "garden in june", "old bicycle", "market stall", "rooftop", "lake at dawn", "kitchen shelf",
              "paper lanterns", "street corner", "ferry", "bookshop window", "hill path", "orange cat", "rainy bus stop",
              "picnic", "snow on the fence"]
    for name in photos:
        picture(out / f"{name}.jpg", size=(1600, 1066))
    docs = ["Lease summary", "Trip budget", "Reading list", "Recipe ideas", "Course notes week 3", "Packing list",
            "Meeting notes 14 Oct", "Cover letter draft", "Warranty kettle", "Bus timetable"]
    for name in docs[:6]:
        picture(out / f"{name}.pdf", size=(827, 1169), text=name, fmt="PDF")
    for name in docs[6:8]:
        d = Document()
        d.add_heading(name, 1)
        d.add_paragraph("A made-up document for the film.")
        d.save(out / f"{name}.docx")
    for name in docs[8:]:
        (out / f"{name}.txt").write_text("A made-up note for the film.\n", encoding="utf-8")
    # stand-ins with installer names: empty files, so there is nothing in them to run
    for name in ["PhotoTool-Setup-4.2.exe", "NoteBird-Installer.exe", "TinyZip-1.9-x64.msi", "ColorPicker-Setup.exe",
                 "ScreenRuler-2.0.msi", "FontBox-Setup.exe", "ClipStack-Installer.exe", "MapViewer-3.1-x64.msi",
                 "SoundTrim-Setup.exe", "PdfJoin-1.4.msi", "WeatherTray-Setup.exe", "KeyPeek-Installer.exe"]:
        (out / name).write_bytes(b"")
    print(f"desktop: {len(list(out.iterdir()))} files")


def sales():
    regions, products = ["North", "South", "East", "West"], ["Lamp", "Desk", "Chair", "Shelf"]
    rows = [(f"2026-{rng.randint(7, 9):02d}-{rng.randint(1, 28):02d}", rng.choice(regions), rng.choice(products),
             rng.randint(1, 9), rng.choice([19, 45, 80, 120])) for _ in range(28)]
    wb = Workbook()
    ws = wb.active
    ws.title = "Sales"
    ws.append(["Date", "Region", "Product", "Units", "Unit price", "Sales"])
    totals = dict.fromkeys(regions, 0)
    for date, region, product, units, price in sorted(rows):
        ws.append([date, region, product, units, price, units * price])
        totals[region] += units * price
    wb.save(KIT / "sales.xlsx")
    lines = [f"{r}: {totals[r]}" for r in regions] + [f"Total: {sum(totals.values())}"]
    (KIT / "sales-answer.txt").write_text("Sales by region (check the take against these)\n" + "\n".join(lines) + "\n", encoding="utf-8")
    print("sales:", ", ".join(lines))


LETTER = [
    "Sam Rivera",
    "14 Harbour Lane, Eastfield",
    "{date}",
    "To whom it may concern,",
    "I am writing to recommend {name} for the junior designer role at your studio.",
    "{name} worked with me for two years on the Eastfield library project. In that time she led the signage "
    "redesign, ran weekly reviews with the builders, and {delivered}every stage on schedule.",
    "She is careful, quick to learn and generous with her time. I recommend her without reservation.",
    "Yours faithfully,",
    "Sam Rivera",
]


def letter():
    out = KIT / "letter"
    out.mkdir(parents=True, exist_ok=True)

    def write(path, date, names, delivered):
        d = Document()
        it = iter(names)
        for line in LETTER:
            text = line.replace("{date}", date).replace("{delivered}", delivered)
            while "{name}" in text:
                text = text.replace("{name}", next(it), 1)
            d.add_paragraph(text)
        d.save(path)

    # three planted mistakes: a date that does not exist, the name misspelt once, a missing word
    write(out / "Recommendation letter.docx", "31 November 2026", ["Dana Lee", "Dnaa Lee"], "")
    write(out / "expected - fixed letter.docx", "30 November 2026", ["Dana Lee", "Dana Lee"], "delivered ")
    (out / "what is wrong.txt").write_text(
        "The three planted mistakes in 'Recommendation letter.docx':\n"
        "1. The date reads 31 November 2026 (November has 30 days).\n"
        "2. The name is spelt 'Dnaa Lee' the second time.\n"
        "3. A word is missing: 'and every stage on schedule' should read 'and delivered every stage on schedule'.\n"
        "Send it from a second account of your own, with the sender name set to Sam.\n",
        encoding="utf-8",
    )
    print("letter: written, with its three mistakes listed beside it")


def quick():
    out = KIT / "quick"
    for name in ["photo 1", "photo 2", "photo 3"]:
        picture(out / f"{name}.jpg", size=(1600, 1066))
    slides = ["The water cycle", "Evaporation: water turns to vapour", "Condensation: vapour forms clouds",
              "Precipitation: rain and snow fall", "Collection: rivers carry it back to the sea"]
    pages = []
    for s in slides:
        p = KIT / "_slide.png"
        picture(p, size=(1280, 720), text=s)
        pages.append(Image.open(p).convert("RGB"))
    pages[0].save(out / "Water cycle slides.pdf", save_all=True, append_images=pages[1:])
    (KIT / "_slide.png").unlink()
    print("quick: 3 photos and a 5-page slide deck")


if __name__ == "__main__":
    KIT.mkdir(parents=True, exist_ok=True)
    desktop()
    sales()
    letter()
    quick()
