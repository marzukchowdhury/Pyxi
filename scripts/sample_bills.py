"""Realistic sample bills, drawn as a screen showing a PDF viewer, for testing "explain this bill".

Three made-up documents, each with something a careful friend would catch:

* ``phone``: a Maplewave Mobile bill. Roaming charged twice, the plan went up $10 since last
  month, and a $2.50 paper bill fee.
* ``insurance``: a Cedar Health explanation of benefits. "This is not a bill", the same physio
  visit listed twice, and an out-of-network charge.
* ``tax``: a National Revenue Service notice of assessment. A balance owing with a due date,
  arrears interest, and a late-filing penalty.

Each comes back as the screenshot plus its text lines with exact boxes (what OCR would read),
so scenarios.py can replay "what does this bill mean?" on it without a real screen.

usage: python scripts/sample_bills.py <folder>   (saves each as a PNG and a PDF there)
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 1920, 1080
PAGE_X, PAGE_W = 560, 800

# (text, style) per line; styles: h1, h2, b (bold), r (right-aligned amount on the same row as
# the previous line), muted, rule, gap.
BILLS: dict[str, dict] = {
    "phone": {
        "file": "Maplewave-Mobile-bill-Nov-2026.pdf",
        "lines": [
            ("Maplewave Mobile", "h1"),
            ("Account 4471 2093 . Bill date Nov 4, 2026 . Alex Tester", "muted"),
            ("", "gap"),
            ("Amount due", "h2"),
            ("$107.80", "big"),
            ("Please pay by Nov 28, 2026", "b"),
            ("", "rule"),
            ("Previous balance", ""),
            ("$76.18", "r"),
            ("Payment received Oct 22 - thank you", ""),
            ("-$76.18", "r"),
            ("", "rule"),
            ("Monthly charges (Nov 4 - Dec 3)", "h2"),
            ("Unlimited 60GB plan", ""),
            ("$75.00", "r"),
            ("Your plan price changed from $65.00 to $75.00 on Oct 15", "muted"),
            ("Device payment: Pixel 9 (month 14 of 24)", ""),
            ("$18.75", "r"),
            ("Usage charges", "h2"),
            ("US Roam Pass, Oct 18 (1 day)", ""),
            ("$15.00", "r"),
            ("US Roam Pass, Oct 18 (1 day)", ""),
            ("$15.00", "r"),
            ("Other charges", "h2"),
            ("Paper bill fee", ""),
            ("$2.50", "r"),
            ("Promotional credit: Loyalty discount", ""),
            ("-$30.00", "r"),
            ("Taxes (GST 5%, PST 7%)", ""),
            ("$11.55", "r"),
            ("", "rule"),
            ("Total new charges", "b"),
            ("$107.80", "r"),
        ],
    },
    "insurance": {
        "file": "Cedar-Health-EOB-Oct-2026.pdf",
        "lines": [
            ("Cedar Health Insurance", "h1"),
            ("Explanation of Benefits . Member Alex Tester . ID CH-88231-04", "muted"),
            ("THIS IS NOT A BILL", "b"),
            ("", "gap"),
            ("Claims processed Oct 1 - Oct 31, 2026", "h2"),
            ("Oct 3  Physiotherapy visit, Harbourside Physio", ""),
            ("Billed $120.00 . Plan paid $96.00 . You owe $24.00", "r2"),
            ("Oct 3  Physiotherapy visit, Harbourside Physio", ""),
            ("Billed $120.00 . Plan paid $96.00 . You owe $24.00", "r2"),
            ("Oct 17  Dental cleaning, Maple Grove Dental", ""),
            ("Billed $145.00 . Plan paid $116.00 . You owe $29.00", "r2"),
            ("Oct 24  MRI, knee, Westside Imaging (out of network)", ""),
            ("Billed $850.00 . Plan paid $340.00 . You owe $510.00", "r2"),
            ("", "rule"),
            ("Your total responsibility", "b"),
            ("$587.00", "r"),
            ("Providers will bill you directly for amounts you owe.", "muted"),
            ("Out-of-network services are covered at 40%. See your plan booklet, section 4.", "muted"),
            ("", "gap"),
            ("Plan year so far", "h2"),
            ("Deductible met: $250.00 of $250.00", ""),
            ("Physiotherapy used: $1,240 of $1,500 annual maximum", ""),
            ("Questions? Call 1-800-555-0144 within 90 days to appeal a claim.", "muted"),
        ],
    },
    "tax": {
        "file": "NRS-Notice-of-Assessment-2025.pdf",
        "lines": [
            ("National Revenue Service", "h1"),
            ("Notice of Assessment . Tax year 2025 . Date issued Nov 2, 2026", "muted"),
            ("Alex Tester . Account ending 4417", "muted"),
            ("", "gap"),
            ("Balance owing", "h2"),
            ("$312.40", "big"),
            ("Pay by Dec 2, 2026 to avoid more interest", "b"),
            ("", "rule"),
            ("Summary", "h2"),
            ("Total income", ""),
            ("$58,420.00", "r"),
            ("Total tax payable", ""),
            ("$8,964.30", "r"),
            ("Tax deducted by your employer", ""),
            ("-$8,710.00", "r"),
            ("Balance from this assessment", ""),
            ("$254.30", "r"),
            ("Late-filing penalty (return received May 21, 2026)", ""),
            ("$50.86", "r"),
            ("Arrears interest to Nov 2, 2026", ""),
            ("$7.24", "r"),
            ("", "rule"),
            ("Balance owing", "b"),
            ("$312.40", "r"),
            ("We changed your return: tuition amount claimed $4,800, allowed $3,200.", ""),
            ("If you disagree, you can object within 90 days of this notice.", "muted"),
        ],
    },
}


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    names = ["segoeuib.ttf", "arialbd.ttf"] if bold else ["segoeui.ttf", "arial.ttf"]
    for name in names:
        for folder in (Path("C:/Windows/Fonts"), Path("/usr/share/fonts/truetype/dejavu")):
            try:
                return ImageFont.truetype(str(folder / name), size)
            except OSError:
                continue
    try:
        return ImageFont.truetype("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf", size)
    except OSError:
        return ImageFont.load_default()


STYLES = {
    "h1": (34, True, "#0f2a44"),
    "h2": (22, True, "#0f2a44"),
    "big": (40, True, "#0f2a44"),
    "b": (19, True, "#1d232c"),
    "muted": (16, False, "#5b6573"),
    "": (19, False, "#1d232c"),
    "r": (19, False, "#1d232c"),
    "r2": (17, False, "#3b4450"),
}

Line = tuple[str, tuple[int, int, int, int], list[tuple[str, tuple[int, int, int, int]]]]


def render(name: str) -> tuple[Image.Image, list[Line], str]:
    """The screen (a PDF viewer showing the bill), its text lines with boxes, and the window
    title."""
    bill = BILLS[name]
    img = Image.new("RGB", (W, H), "#3c3f44")
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, W, 44), fill="#202124")
    title = f"{bill['file']} - Microsoft Edge"
    d.text((16, 12), title, font=_font(16), fill="#e8eaed")
    d.rectangle((PAGE_X, 64, PAGE_X + PAGE_W, H + 20), fill="white")
    left, right = PAGE_X + 56, PAGE_X + PAGE_W - 56
    y = 96
    lines: list[Line] = []
    last_row_y = y
    for text, style in bill["lines"]:
        if style == "gap":
            y += 12
            continue
        if style == "rule":
            d.line((left, y + 6, right, y + 6), fill="#d6dae0", width=2)
            y += 18
            continue
        size, bold, color = STYLES[style]
        font = _font(size, bold)
        width = int(d.textlength(text, font=font))
        if style == "r":  # an amount on the right of the row above
            x, ty = right - width, last_row_y
        elif style == "r2":
            x, ty = left + 24, y
        else:
            x, ty = left, y
        d.text((x, ty), text, font=font, fill=color)
        box = d.textbbox((x, ty), text, font=font)
        rect = (box[0], box[1], box[2] - box[0], box[3] - box[1])
        words, cx = [], x
        for word in text.split(" "):
            ww = int(d.textlength(word, font=font))
            if word:
                words.append((word, (cx, rect[1], ww, rect[3])))
            cx += ww + int(d.textlength(" ", font=font))
        lines.append((text, rect, words))
        if style != "r":
            last_row_y = ty
            y += int(size * 1.55)
    return img, lines, title


def save_all(folder: Path) -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    out = []
    for name, bill in BILLS.items():
        img, _, _ = render(name)
        page = img.crop((PAGE_X, 64, PAGE_X + PAGE_W, H))
        png = folder / f"{name}-bill-on-screen.png"
        img.save(png)
        pdf = folder / bill["file"]
        page.save(pdf, "PDF", resolution=110)
        out += [png, pdf]
    return out


if __name__ == "__main__":
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "sample-bills")
    for p in save_all(target):
        print(p)
