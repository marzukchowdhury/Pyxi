"""Pixel's document toolkit on real sample files, checking what's inside each result: pages,
text, rotation, passwords, form values, OCR, and Quill's summaries and translations."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from docx import Document
from openpyxl import Workbook
from PIL import Image, ImageDraw, ImageFont
from pptx import Presentation
from pypdf import PdfReader

from pyxi import booking
from pyxi.agents import pixel, pixel_ocr
from pyxi.agents.pixel import PixelError
from pyxi.companion import filework
from pyxi.docs import document_mode, fix_document, summarize_document
from pyxi.screen import ocr
from test_brain_and_server import make_brain
from test_connectors_and_docs import Script

ARIAL = Path(r"C:\Windows\Fonts\arial.ttf")
needs_ocr = pytest.mark.skipif(not ocr.available(), reason="Windows OCR isn't available here")
ENGINES = [
    pytest.param("windows", marks=needs_ocr),
    pytest.param(
        "rapid", marks=pytest.mark.skipif(pixel_ocr.rapid_engine() is None, reason="no RapidOCR")
    ),
]


@pytest.fixture(autouse=True)
def no_libreoffice(monkeypatch):
    monkeypatch.setattr(pixel, "libreoffice", lambda: None)  # the built-in renderer, every time


def text_of(pdf: Path, page: int | None = None) -> str:
    pages = PdfReader(str(pdf)).pages
    return "\n".join((p.extract_text() or "") for p in (pages if page is None else [pages[page]]))


def scan(path: Path, lines: list[str]) -> Path:
    """A page as a camera or scanner sees it: a picture of text, with no text layer."""
    img = Image.new("RGB", (1700, 2200), "white")
    font = ImageFont.truetype(str(ARIAL), 56) if ARIAL.exists() else ImageFont.load_default()
    draw = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        draw.text((160, 220 + i * 110), line, fill="black", font=font)
    img.save(path)
    return path


def text_pdf(path: Path, pages: list[str]) -> Path:
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(path))
    for text in pages:
        c.setFont("Helvetica", 14)
        c.drawString(72, 700, text)
        c.showPage()
    c.save()
    return path


def letter(path: Path, greeting: str = "Dear hiring manager,", extra: str = "") -> Path:
    doc = Document()
    doc.add_heading("Cover letter", 1)
    doc.add_paragraph(greeting)
    doc.add_paragraph("I led a team of five.", style="List Bullet")
    doc.add_paragraph("I shipped the app in May.", style="List Number")
    if extra:
        doc.add_paragraph(extra)
    doc.save(path)
    return path


# ---------------------------------------------------------------------------- the two bugs
def test_split_by_ranges_makes_one_pdf_per_range(tmp_path):
    src = text_pdf(tmp_path / "three.pdf", ["First", "Second", "Third"])
    r = pixel.run("split_pdf", [src], {"ranges": "1-2, 3"})
    assert [p.name for p in r.outputs] == ["three (pages 1-2).pdf", "three (page 3).pdf"]
    assert [len(PdfReader(str(p)).pages) for p in r.outputs] == [2, 1]
    assert "Third" in text_of(r.outputs[1]) and "First" in text_of(r.outputs[0])
    assert len(pixel.run("split_pdf", [src], {"pages": ["1", "2-3"]}).outputs) == 2


def test_word_bullets_are_real_bullets(tmp_path):
    r = pixel.run("word_to_pdf", [letter(tmp_path / "letter.docx")])
    text = text_of(r.outputs[0])
    assert "\x7f" not in text
    assert "\u2022 I led a team of five." in text and "1. I shipped the app in May." in text
    assert text.index("Cover letter") < text.index("Dear hiring manager,")


# ---------------------------------------------------------------------------- pages
def test_rotate_reorder_delete_and_extract(tmp_path):
    src = text_pdf(tmp_path / "doc.pdf", ["One", "Two", "Three", "Four"])
    rotated = pixel.run("rotate_pages", [src], {"pages": "2", "angle": "left"}).outputs[0]
    assert [p.rotation for p in PdfReader(str(rotated)).pages] == [0, 270, 0, 0]
    flipped = pixel.run("rotate_pages", [src], {"angle": 180}).outputs[0]
    assert {p.rotation for p in PdfReader(str(flipped)).pages} == {180}
    reordered = pixel.run("reorder_pages", [src], {"order": "4, 1"})
    assert [text_of(reordered.outputs[0], i).strip() for i in range(4)] == [
        "Four",
        "One",
        "Two",
        "Three",
    ]
    fewer = pixel.run("delete_pages", [src], {"pages": "2, last"}).outputs[0]
    assert [text_of(fewer, i).strip() for i in range(2)] == ["One", "Three"]
    some = pixel.run("extract_pages", [src], {"pages": "3-4"}).outputs[0]
    assert some.name == "doc (pages 3-4).pdf" and text_of(some).split() == ["Three", "Four"]
    with pytest.raises(PixelError, match="every page"):
        pixel.run("delete_pages", [src], {"pages": "1-4"})
    with pytest.raises(PixelError, match="90, 180 or 270"):
        pixel.run("rotate_pages", [src], {"angle": 45})
    assert len(PdfReader(str(src)).pages) == 4  # the original never changes


def test_page_numbers_and_watermark(tmp_path):
    src = text_pdf(tmp_path / "report.pdf", ["Intro", "Body", "End"])
    numbered = pixel.run(
        "add_page_numbers", [src], {"format": "Page {n} of {total}", "position": "bottom right"}
    )
    assert "Page 2 of 3" in text_of(numbered.outputs[0], 1) and "Body" in text_of(
        numbered.outputs[0], 1
    )
    marked = pixel.run("add_watermark", [src], {"text": "CONFIDENTIAL"}).outputs[0]
    assert all("CONFIDENTIAL" in text_of(marked, i) for i in range(3))
    with pytest.raises(PixelError, match="needs \\{n\\}"):
        pixel.run("add_page_numbers", [src], {"format": "Page"})


def test_passwords_lock_and_unlock(tmp_path):
    src = text_pdf(tmp_path / "invoice.pdf", ["Total $1,175.20"])
    locked = pixel.run("protect_pdf", [src], {"password": "tulip-42"}).outputs[0]
    reader = PdfReader(str(locked))
    assert reader.is_encrypted and reader.decrypt("wrong") == 0
    with pytest.raises(PixelError, match="password-protected"):
        pixel.run("rotate_pages", [locked])
    with pytest.raises(PixelError, match="didn't open it"):
        pixel.run("unlock_pdf", [locked], {"password": "nope"})
    opened = pixel.run("unlock_pdf", [locked], {"password": "tulip-42"}).outputs[0]
    assert not PdfReader(str(opened)).is_encrypted and "1,175.20" in text_of(opened)
    assert not PdfReader(str(src)).is_encrypted
    with pytest.raises(PixelError, match="No password"):
        pixel.run("protect_pdf", [src], {})


def form(path: Path) -> Path:
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(path))
    for i, name in enumerate(
        ["First Name", "Last Name", "Email Address", "Phone", "SIN", "Move-in date"]
    ):
        c.drawString(72, 700 - i * 40, name)
        c.acroForm.textfield(name=name, x=220, y=690 - i * 40, width=200, height=20)
    c.acroForm.checkbox(name="Has pets", x=220, y=440, size=14)
    c.save()
    return path


def test_fill_form_from_saved_details_never_ids(tmp_path):
    details = {
        "first_name": "Mina",
        "last_name": "Rahman",
        "email": "mina@example.com",
        "phone": "604 555 0101",
    }
    r = pixel.run(
        "fill_form",
        [form(tmp_path / "rental application.pdf")],
        {
            "details": details,
            "values": {"Move-in date": "2026-12-01", "Has pets": "no", "SIN": "123"},
        },
    )
    fields = {k: v.get("/V") for k, v in PdfReader(str(r.outputs[0])).get_fields().items()}
    assert fields["First Name"] == "Mina" and fields["Last Name"] == "Rahman"
    assert fields["Email Address"] == "mina@example.com" and fields["Phone"] == "604 555 0101"
    assert fields["Move-in date"] == "2026-12-01"
    assert not fields.get("SIN")  # ID numbers are left for them, even if the model passes one
    assert "SIN" in r.summary and "Check it" in r.summary
    with pytest.raises(PixelError, match="no fillable fields"):
        pixel.run("fill_form", [text_pdf(tmp_path / "plain.pdf", ["x"])], {"details": details})


# ---------------------------------------------------------------------------- reading
@pytest.mark.parametrize("engine", ENGINES)
def test_text_out_of_scans_and_photos(tmp_path, monkeypatch, engine):
    monkeypatch.setenv("PYXI_OCR", engine)
    page = scan(tmp_path / "lease.png", ["Monthly rent 1450 dollars", "Due on the first"])
    src = pixel.run("images_to_pdf", [page], {"name": "lease scan"}).outputs[0]
    assert not text_of(src).strip()  # a scan: no text layer at all
    r = pixel.run("extract_text", [src])
    body = r.outputs[0].read_text(encoding="utf-8")
    assert "Monthly rent 1450" in body and "OCR" in r.summary
    photo = pixel.run("extract_text", [page]).outputs[0].read_text(encoding="utf-8")
    assert "Due on the first" in photo


@pytest.mark.parametrize("engine", ENGINES)
def test_a_scan_becomes_searchable(tmp_path, monkeypatch, engine):
    monkeypatch.setenv("PYXI_OCR", engine)
    src = pixel.run("images_to_pdf", [scan(tmp_path / "s.png", ["Signed by Mina Rahman"])]).outputs[
        0
    ]
    out = pixel.run("make_searchable", [src]).outputs[0]
    assert "Signed by Mina Rahman" in text_of(out)
    assert PdfReader(str(out)).pages[0].images  # the picture is still the page
    text_pdf(tmp_path / "typed.pdf", ["Already has plenty of text on this page"])
    with pytest.raises(PixelError, match="already has text"):
        pixel.run("make_searchable", [tmp_path / "typed.pdf"])


def test_pdf_to_word_keeps_headings_and_lists(tmp_path):
    pdf = pixel.run("to_pdf", [letter(tmp_path / "letter.docx")]).outputs[0]
    out = pixel.run("pdf_to_word", [pdf]).outputs[0]
    assert out.name == "letter (2).docx"  # letter.docx is taken
    paras = [(p.style.name, p.text) for p in Document(str(out)).paragraphs if p.text]
    assert paras == [
        ("Heading 1", "Cover letter"),
        ("Normal", "Dear hiring manager,"),
        ("List Bullet", "I led a team of five."),
        ("List Number", "I shipped the app in May."),
    ]


# ---------------------------------------------------------------------------- to PDF
def test_excel_powerpoint_markdown_html_and_text_to_pdf(tmp_path):
    wb = Workbook()
    ws = wb.active
    ws.append(["Item", "Amount"])
    ws.append(["Rent", 1450.5])
    wb.save(tmp_path / "budget.xlsx")
    assert "1,450.5" in text_of(pixel.run("to_pdf", [tmp_path / "budget.xlsx"]).outputs[0])

    prs = Presentation()
    s = prs.slides.add_slide(prs.slide_layouts[1])
    s.shapes.title.text = "Q3 review"
    s.placeholders[1].text_frame.text = "Revenue up 12%"
    s2 = prs.slides.add_slide(prs.slide_layouts[1])
    s2.shapes.title.text = "Next steps"
    prs.save(tmp_path / "deck.pptx")
    deck = pixel.run("to_pdf", [tmp_path / "deck.pptx"]).outputs[0]
    assert len(PdfReader(str(deck)).pages) == 2  # a page per slide
    assert "\u2022 Revenue up 12%" in text_of(deck, 0) and "Next steps" in text_of(deck, 1)
    assert (
        PdfReader(str(deck)).pages[0].mediabox.width > PdfReader(str(deck)).pages[0].mediabox.height
    )

    (tmp_path / "notes.md").write_text(
        "# Trip notes\n\n- **Flight** at 9\n- Hotel: café nearby\n\n| Day | Plan |\n|---|---|\n| Mon | Museum |\n",
        encoding="utf-8",
    )
    md = text_of(pixel.run("to_pdf", [tmp_path / "notes.md"]).outputs[0])
    assert "Trip notes" in md and "\u2022 Flight at 9" in md and "café" in md and "Museum" in md
    assert "**" not in md and "|" not in md

    (tmp_path / "page.html").write_text(
        "<html><head><style>p{color:red}</style><script>alert(1)</script></head><body>"
        "<h1>Menu</h1><p>Soup <b>and</b> bread</p><ol><li>Tea</li><li>Cake</li></ol></body></html>",
        encoding="utf-8",
    )
    page = text_of(pixel.run("to_pdf", [tmp_path / "page.html"]).outputs[0])
    assert "Menu" in page and "Soup and bread" in page and "1. Tea" in page and "2. Cake" in page
    assert "alert" not in page and "color" not in page

    (tmp_path / "todo.txt").write_text("Buy milk\nCall Sam\n\nPay rent", encoding="utf-8")
    txt = text_of(pixel.run("to_pdf", [tmp_path / "todo.txt"]).outputs[0])
    assert "Buy milk" in txt and "Pay rent" in txt
    with pytest.raises(PixelError, match="LibreOffice"):
        (tmp_path / "old.doc").write_bytes(b"x")
        pixel.run("to_pdf", [tmp_path / "old.doc"])


# ---------------------------------------------------------------------------- images
def test_heic_to_jpg(tmp_path):
    pillow_heif = pytest.importorskip("pillow_heif")
    pillow_heif.register_heif_opener()
    Image.new("RGB", (640, 480), (30, 160, 90)).save(tmp_path / "IMG_2043.HEIC")
    r = pixel.run("convert_image", [tmp_path / "IMG_2043.HEIC"], {"format": "jpg"})
    out = Image.open(r.outputs[0])
    assert r.outputs[0].name == "IMG_2043.jpg" and out.format == "JPEG" and out.size == (640, 480)
    assert out.getpixel((10, 10))[1] > 140  # still the same green photo
    assert pixel.guess_operation("convert to jpg", ["IMG_2043.HEIC"]) == "convert_image"


def test_compress_images(tmp_path):
    Image.effect_noise((3200, 2400), 50).convert("RGB").save(tmp_path / "big.jpg", quality=98)
    Image.new("RGBA", (800, 800), (255, 0, 0, 128)).save(tmp_path / "logo.png")
    r = pixel.run("compress_image", [tmp_path / "big.jpg", tmp_path / "logo.png"])
    small = {p.name: p for p in r.outputs}
    assert Image.open(small["big (smaller).jpg"]).size == (2560, 1920)
    assert small["big (smaller).jpg"].stat().st_size < (tmp_path / "big.jpg").stat().st_size / 2
    assert "%" in r.summary
    tiny = Image.new("RGB", (40, 40), "white")
    tiny.save(tmp_path / "tiny.jpg", quality=50)
    assert pixel.run("compress_image", [tmp_path / "tiny.jpg"]).outputs == []


# ---------------------------------------------------------------------------- compare
def test_compare_two_versions(tmp_path):
    old = letter(tmp_path / "Resume v1.docx", extra="Salary expectation: $70,000.")
    new = letter(tmp_path / "Resume v2.docx", "Dear hiring team,", "Salary expectation: $78,000.")
    r = pixel.run("compare_documents", [old, new])
    assert r.details["counts"] == {"changed": 2, "added": 0, "removed": 0}
    assert "'manager,' -> 'team,'" in r.details["changes"]
    assert "'$70,000.' -> '$78,000.'" in r.details["changes"]
    report = Document(str(r.outputs[0]))
    assert report.paragraphs[0].text == "What changed: Resume v1.docx to Resume v2.docx"
    struck = [run.text.strip() for p in report.paragraphs for run in p.runs if run.font.strike]
    assert "$70,000." in struck
    same = pixel.run("compare_documents", [old, old])
    assert same.outputs == [] and "No differences" in same.summary
    pdf_old = pixel.run("to_pdf", [old]).outputs[0]  # a PDF against its Word original
    assert pixel.run("compare_documents", [pdf_old, new]).details["counts"]["changed"] >= 2
    with pytest.raises(PixelError, match="exactly two"):
        pixel.run("compare_documents", [old])


def test_guessing_new_operations():
    g = pixel.guess_operation
    assert g("rotate page 2", ["a.pdf"]) == "rotate_pages"
    assert g("add page numbers", ["a.pdf"]) == "add_page_numbers"
    assert g("password protect it", ["a.pdf"]) == "protect_pdf"
    assert g("remove the password", ["a.pdf"]) == "unlock_pdf"
    assert g("fill in this form", ["a.pdf"]) == "fill_form"
    assert g("make it searchable", ["a.pdf"]) == "make_searchable"
    assert g("turn it into Word", ["a.pdf"]) == "pdf_to_word"
    assert g("slides to PDF", ["a.pptx"]) == "to_pdf"
    assert g("what changed", ["a.docx", "b.docx"]) == "compare_documents"
    assert g("delete page 3", ["a.pdf"]) == "delete_pages"


# ---------------------------------------------------------------------------- jobs
async def _answer(rec, brain, kind: str, value, **match):
    for _ in range(200):
        for card in rec.of(kind):
            if all(card.get(k) == v for k, v in match.items()) and brain.hub.resolve(
                card["request_id"], value
            ):
                return card
        await asyncio.sleep(0.01)
    raise AssertionError(f"no {kind} card")


async def test_the_password_comes_from_a_masked_card_not_the_model(settings, tmp_path):
    brain, rec = make_brain(settings)
    src = text_pdf(tmp_path / "invoice.pdf", ["Total $1,175.20"])
    note = await filework.handle(
        brain,
        "convert_files",
        {"operation": "protect_pdf", "files": [str(src)], "options": {"password": "from-model"}},
    )
    assert "card" in note
    card = await _answer(rec, brain, "ask", "typed-by-user", secret=True)
    assert "lock invoice.pdf" in card["question"]
    await brain.jobs.wait_all()
    [job] = [j for j in brain.jobs.jobs.values() if j.agent_id == "pixel"]
    assert job.status == "done"
    locked = Path(job.outputs[0])
    assert PdfReader(str(locked)).decrypt("typed-by-user") != 0
    assert PdfReader(str(locked)).decrypt("from-model") == 0
    # Neither password is kept on the job or sent to any screen.
    record = json.dumps(job.model_dump(mode="json")) + json.dumps(rec.events)
    assert "typed-by-user" not in record and "from-model" not in record


async def test_unlocking_with_the_wrong_password_says_so(settings, tmp_path):
    brain, rec = make_brain(settings)
    locked = pixel.run(
        "protect_pdf", [text_pdf(tmp_path / "a.pdf", ["x"])], {"password": "right-one"}
    ).outputs[0]
    await filework.handle(
        brain, "convert_files", {"operation": "unlock_pdf", "files": [str(locked)]}
    )
    await _answer(rec, brain, "ask", "wrong-one", secret=True)
    await brain.jobs.wait_all()
    [job] = [j for j in brain.jobs.jobs.values() if j.agent_id == "pixel"]
    assert job.status == "failed" and "didn't open it" in job.summary
    await filework.handle(
        brain, "convert_files", {"operation": "unlock_pdf", "files": [str(locked)]}
    )
    await _answer(rec, brain, "ask", None, secret=True)  # they skipped the card
    await brain.jobs.wait_all()
    assert sorted(j.status for j in brain.jobs.jobs.values()) == ["failed", "stopped"]


async def test_fill_form_job_uses_details_saved_on_this_pc(settings, tmp_path):
    booking.save(
        settings.data_dir,
        {"first_name": "Mina", "last_name": "Rahman", "email": "mina@example.com"},
    )
    brain, _ = make_brain(settings)

    async def press_fill_it_in(kind, timeout=300, **data):
        # Pixel's card: the saved details are typed in already, and they press the button.
        return {"answers": {f["id"]: f["value"] for f in data["fields"]}, "remember": False}

    brain.hub.request = press_fill_it_in  # type: ignore[method-assign]
    src = form(tmp_path / "application.pdf")
    await filework.handle(
        brain,
        "convert_files",
        {
            "operation": "fill_form",
            "files": [str(src)],
            "options": {"details": {"first_name": "Evil"}},
        },
    )
    await brain.jobs.wait_all()
    [job] = list(brain.jobs.jobs.values())
    fields = {k: v.get("/V") for k, v in PdfReader(job.outputs[0]).get_fields().items()}
    assert fields["First Name"] == "Mina" and fields["Email Address"] == "mina@example.com"
    assert "Mina" not in json.dumps(job.params)


# ---------------------------------------------------------------------------- Quill
def test_document_modes():
    assert document_mode("Summarize the key dates") == "summarize"
    assert document_mode("Translate into French") == "translate"
    assert document_mode("make it more formal") == "rewrite"
    assert document_mode("fix the grammar") == "fix"


async def test_quill_summarizes_a_pdf(tmp_path):
    src = text_pdf(tmp_path / "tax return 2025.pdf", ["Refund owed: $1,240", "File by April 30"])
    llm = Script("**Your 2025 return.** You get a $1,240 refund.\n- File by April 30")
    r = await summarize_document(src, "Summarize in two bullets", llm)
    assert r.path.name == "tax return 2025 (summary).md" and "$1,240" in r.path.read_text(
        encoding="utf-8"
    )
    assert "Refund owed: $1,240" in llm.prompts[0] and "[page 2]" in llm.prompts[0]


async def test_quill_translates_a_word_doc_keeping_styles(tmp_path):
    src = letter(tmp_path / "Cover letter PwC.docx")
    reply = json.dumps(
        {
            "edits": [
                {"i": 0, "text": "Lettre de motivation"},
                {"i": 1, "text": "Madame, Monsieur,"},
                {"i": 2, "text": "J'ai dirigé une équipe de cinq personnes."},
                {"i": 3, "text": "J'ai lancé l'application en mai."},
            ]
        }
    )
    llm = Script(reply)
    r = await fix_document(src, "Translate into French", llm, mode="translate")
    assert len(llm.prompts) == 1  # one pass, no "second editor" review
    out = Document(str(r.path))
    assert r.path.name == "Cover letter PwC (translated by Pyxi).docx"
    assert [(p.style.name, p.text) for p in out.paragraphs][:3] == [
        ("Heading 1", "Lettre de motivation"),
        ("Normal", "Madame, Monsieur,"),
        ("List Bullet", "J'ai dirigé une équipe de cinq personnes."),
    ]


async def test_quill_job_routes_by_the_task(settings, tmp_path):
    src = text_pdf(tmp_path / "lease.pdf", ["Rent is $1,450 a month"])
    brain, rec = make_brain(settings, llm=Script("Rent is $1,450 a month, due on the 1st."))
    job = await brain.jobs.start("quill", "Summarize this lease", files=[str(src)])
    job = await brain.jobs.wait(job.id)
    assert job.status == "done" and job.outputs == [str(tmp_path / "lease (summary).md")]
    assert "$1,450" in job.summary
    [ready] = rec.of("job_ready")
    assert ready["title"] == "Your draft is ready"  # not "Your document is fixed"


# ---------------------------------------------------------------- found in the beta QA pass
def test_several_pdfs_for_a_one_file_operation_are_each_done(tmp_path):
    a = text_pdf(tmp_path / "a.pdf", ["one", "two"])
    b = text_pdf(tmp_path / "b.pdf", ["three"])
    r = pixel.run("add_page_numbers", [a, b])
    assert [p.name for p in r.outputs] == ["a (numbered).pdf", "b (numbered).pdf"]
    with pytest.raises(PixelError, match=r"can't find nope.pdf"):
        pixel.run("compress_pdf", [a, tmp_path / "nope.pdf"])  # said before any work is done


def test_page_ranges_quote_what_was_said(tmp_path):
    src = text_pdf(tmp_path / "a.pdf", ["1", "2", "3"])
    with pytest.raises(PixelError, match="'the last one'"):
        pixel.run("extract_pages", [src], {"pages": "the last one"})
    r = pixel.run("extract_pages", [src], {"pages": "first and last page"})
    assert len(PdfReader(str(r.outputs[0])).pages) == 2


def test_bad_sizes_folders_and_empty_csvs_say_so(tmp_path):
    img = tmp_path / "p.png"
    Image.new("RGB", (40, 30), "white").save(img)
    with pytest.raises(PixelError, match="bigger than zero"):
        pixel.run("resize_image", [img], {"percent": -5})
    with pytest.raises(PixelError, match="too big"):
        pixel.run("resize_image", [img], {"percent": 10_000_000})
    with pytest.raises(PixelError, match="is a folder"):
        pixel.run("compress_pdf", [tmp_path])
    (tmp_path / "empty.csv").write_text("", encoding="utf-8")
    with pytest.raises(PixelError, match="empty"):
        pixel.run("csv_to_excel", [tmp_path / "empty.csv"])
    assert not (tmp_path / "empty.xlsx").exists()


def test_a_libreoffice_failure_is_said_plainly(tmp_path, monkeypatch):
    import subprocess

    src = tmp_path / "a.docx"
    Document().save(src)

    def fail(*a, **k):
        raise subprocess.CalledProcessError(1, ["soffice.exe", "--convert-to", "pdf"])

    monkeypatch.setattr(pixel, "libreoffice", lambda: "soffice.exe")
    monkeypatch.setattr(subprocess, "run", fail)
    with pytest.raises(PixelError) as err:
        pixel.run("to_pdf", [src])
    assert "couldn't convert a.docx" in str(err.value) and "soffice" not in str(err.value)


@pytest.mark.skipif(not Path(r"C:\Windows\Fonts\msyh.ttc").is_file(), reason="no YaHei font")
def test_chinese_names_and_watermarks_are_not_empty_boxes(tmp_path):
    src = tmp_path / "contacts.csv"
    src.write_text("Name,Email\nZoë Martín,zoe@example.com\n李雷,lilei@example.com\n", "utf-8")
    [pdf] = pixel.run("to_pdf", [src]).outputs
    text = PdfReader(str(pdf)).pages[0].extract_text()
    assert "李雷" in text and "Zoë Martín" in text
    [marked] = pixel.run("add_watermark", [pdf], {"text": "機密"}).outputs
    assert "機密" in PdfReader(str(marked)).pages[0].extract_text()
