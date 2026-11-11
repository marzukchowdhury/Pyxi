"""Pixel, after an audit of what it does to real files.

Each test is something that went wrong with a small file made here: a conversion that said
"Made it" over a file of nonsense, pages that were not the ones asked for, text and numbers
that did not survive, and failures that showed the user a line of Python.
"""

from __future__ import annotations

import datetime as dt
import io
import re
import threading
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from docx import Document
from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import nsdecls, qn
from openpyxl import Workbook, load_workbook
from PIL import Image
from pypdf import PdfReader, PdfWriter
from pypdf.generic import RectangleObject
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from pyxi.agents import libreoffice, pixel, pixel_pdf, pixel_render
from pyxi.agents.pixel import PixelError
from pyxi.agents.pixel_common import parse_ranges, unique_path
from test_brain_and_server import make_brain


# ---------------------------------------------------------------------------- sample files
def pdf(path: Path, pages: int) -> Path:
    c = canvas.Canvas(str(path), pagesize=letter)
    for i in range(1, pages + 1):
        c.drawString(72, 700, f"This is page number {i} of the test document, long enough.")
        c.showPage()
    c.save()
    return path


def page_numbers(path: Path) -> list[int]:
    return [
        int(re.search(r"page number (\d+)", p.extract_text()).group(1))
        for p in PdfReader(str(path)).pages
    ]


def pdf_text(path: Path) -> str:
    return "\n".join(p.extract_text() for p in PdfReader(str(path)).pages)


def blank_pdf(path: Path, pages: int = 3) -> Path:
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(612, 792)
    with path.open("wb") as fh:
        w.write(fh)
    return path


@pytest.fixture
def no_libreoffice(monkeypatch):
    """The built-in renderer: what every PC has before LibreOffice is downloaded."""
    monkeypatch.setattr(pixel, "libreoffice", lambda: None)


# ---------------------------------------------------------------------------- page ranges
@pytest.mark.parametrize(
    ("spec", "want"),
    [
        ("page 2 to page 4", [(2, 4)]),
        ("page 2-page 4", [(2, 4)]),
        ("pages 2 to 4", [(2, 4)]),
        ("2 to the last", [(2, 10)]),
        ("the last page", [(10, 10)]),
        ("pages 1 and 3", [(1, 1), (3, 3)]),
        ("2—4", [(2, 4)]),
        ("2 through 4", [(2, 4)]),
        ("1-3, 5, 7-", [(1, 3), (5, 5), (7, 10)]),
        ("-3", [(1, 3)]),
    ],
)
def test_page_ranges_said_in_words(spec, want):
    assert parse_ranges(spec, 10) == want


def test_page_2_to_page_4_is_three_pages_not_nine(tmp_path):
    """The word "page" was turned into a comma, so "page 2 to page 4" read as "2 to the end"
    and "4": deleting those pages left one page of ten, and said "Removed pages 2-10"."""
    src = pdf(tmp_path / "ten.pdf", 10)
    r = pixel.run("delete_pages", [src], {"pages": "page 2 to page 4"})
    assert page_numbers(r.outputs[0]) == [1, 5, 6, 7, 8, 9, 10] and "Removed pages 2-4" in r.summary
    r = pixel.run("extract_pages", [src], {"pages": "page 2 to page 4"})
    assert page_numbers(r.outputs[0]) == [2, 3, 4]
    with pytest.raises(PixelError, match="don't understand"):
        parse_ranges("-", 10)  # a dash alone is not "every page"
    assert page_numbers(pixel.run("merge_pdfs", [pdf(tmp_path / "b.pdf", 2), src]).outputs[0]) == [
        1, 2, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10,
    ]  # fmt: skip


# ---------------------------------------------------------------------------- encodings
def test_a_csv_from_excel_with_an_accent_is_not_read_as_chinese(tmp_path):
    """Excel's "CSV (Comma delimited)" is Windows-1252. With an even number of bytes it passed
    for UTF-16, and the workbook was one cell of CJK characters under "Made even.xlsx"."""
    src = tmp_path / "people.csv"
    src.write_bytes("Name,City\r\nRené,Montréal\r\nZoë,Köln\r\n".encode("cp1252"))
    assert len(src.read_bytes()) % 2 == 0
    r = pixel.run("csv_to_excel", [src])
    rows = [list(row) for row in load_workbook(r.outputs[0]).active.iter_rows(values_only=True)]
    assert rows == [["Name", "City"], ["René", "Montréal"], ["Zoë", "Köln"]]
    # UTF-16 is still read: with its mark (Notepad's "Unicode"), and without one.
    for name, raw in (
        ("marked.txt", "Zoë, Köln".encode("utf-16")),
        ("bare.txt", "Zoe lives in Cologne now".encode("utf-16-le")),
        ("utf8.txt", "Zoë, Köln".encode("utf-8-sig")),
    ):
        (tmp_path / name).write_bytes(raw)
        assert pixel_render.read_any_text(tmp_path / name).startswith("Zo")
        assert "\x00" not in pixel_render.read_any_text(tmp_path / name)


# ---------------------------------------------------------------------------- compress
def _noise(size: int) -> Image.Image:
    return Image.effect_noise((size, size), 60).convert("RGB")


def _colour_at(path: Path, x: int, y: int) -> tuple[int, int, int]:
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(path))
    try:
        return doc[0].render(scale=1).to_pil().convert("RGB").getpixel((x, y))
    finally:
        doc.close()


def test_compressing_a_pdf_keeps_a_logo_see_through(tmp_path):
    """Every image was re-saved as a JPEG, which has no transparency: a logo on a blue page
    came back in a white box."""
    logo = _noise(900).convert("RGBA")
    logo.putalpha(Image.new("L", logo.size, 0))  # all of it see-through
    logo.save(tmp_path / "logo.png")
    photo = io.BytesIO()
    _noise(1800).save(photo, "PNG")
    photo.seek(0)
    src = tmp_path / "brochure.pdf"
    c = canvas.Canvas(str(src), pagesize=letter)
    c.setFillColorRGB(0.05, 0.2, 0.6)
    c.rect(0, 0, 612, 792, fill=1, stroke=0)
    c.drawImage(ImageReader(str(tmp_path / "logo.png")), 100, 500, 200, 200, mask="auto")
    c.drawImage(ImageReader(photo), 100, 100, 300, 300)
    c.save()
    blue = _colour_at(src, 200, 792 - 600)
    r = pixel.run("compress_pdf", [src])
    assert r.outputs and "smaller" in r.summary  # the photo was still worth shrinking
    assert _colour_at(r.outputs[0], 200, 792 - 600) == blue  # and the page shows through the logo


# ---------------------------------------------------------------------------- scans
def test_a_scan_nothing_can_be_read_from_is_not_text_a_word_file_or_searchable(
    tmp_path, monkeypatch
):
    """Three pages no text reader made anything of: "Pulled 12 words" (the page markers), a
    Word file with nothing in it, and a PDF said to be searchable."""
    monkeypatch.setattr(pixel_pdf, "_ocr_image", lambda img: [])
    src = blank_pdf(tmp_path / "scan.pdf")
    for op in ("extract_text", "pdf_to_word", "make_searchable"):
        with pytest.raises(PixelError, match="any text"):
            pixel.run(op, [src])
    assert [p.name for p in tmp_path.iterdir()] == ["scan.pdf"]


def test_the_word_count_is_of_the_words_not_the_page_markers(tmp_path):
    src = pdf(tmp_path / "three.pdf", 3)
    r = pixel.run("extract_text", [src])
    assert "Pulled 33 words" in r.summary  # eleven a page; "--- Page 1 ---" is not four more
    assert "--- Page 3 ---" in r.outputs[0].read_text(encoding="utf-8")


def test_a_typed_pdf_with_a_blank_page_is_read_without_a_text_reader(tmp_path, monkeypatch):
    """A page with next to nothing on it is sent to OCR. On a PC with no text reader that
    failed the whole document, typed pages and all."""

    def none(img):
        raise PixelError("Reading scanned pages needs a text reader.")

    monkeypatch.setattr(pixel_pdf, "_ocr_image", none)
    w = PdfWriter(clone_from=PdfReader(str(pdf(tmp_path / "typed.pdf", 2))))
    w.add_blank_page(612, 792)
    with (tmp_path / "mixed.pdf").open("wb") as fh:
        w.write(fh)
    texts, scanned = pixel_pdf.page_texts(tmp_path / "mixed.pdf")
    assert [bool(t) for t in texts] == [True, True, False] and scanned == []
    assert "from 3 pages" in pixel.run("pdf_to_word", [tmp_path / "mixed.pdf"]).summary
    with pytest.raises(PixelError, match="needs a text reader"):  # nothing typed at all: say why
        pixel_pdf.page_texts(blank_pdf(tmp_path / "scan.pdf"))


# ---------------------------------------------------------------------------- Word to PDF
def _link(paragraph, url: str, text: str) -> None:
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"
    h = OxmlElement("w:hyperlink")
    h.set(qn("r:id"), paragraph.part.relate_to(url, rel, is_external=True))
    r = OxmlElement("w:r")
    t = OxmlElement("w:t")
    t.text = text
    r.append(t)
    h.append(r)
    paragraph._p.append(h)


def _list_item(doc, text: str, kind: str):
    """A list made with Word's own buttons: the style "List Paragraph" plus a numbering id."""
    numbering = doc.part.numbering_part.element
    for num in numbering.xpath("./w:num"):
        base = num.xpath("./w:abstractNumId/@w:val")[0]
        fmt = numbering.xpath(
            f"./w:abstractNum[@w:abstractNumId='{base}']/w:lvl[@w:ilvl='0']/w:numFmt/@w:val"
        )
        if fmt and fmt[0] == kind:
            p = doc.add_paragraph(text, style="List Paragraph")
            p._p.get_or_add_pPr().append(
                parse_xml(
                    f"<w:numPr {nsdecls('w')}><w:ilvl w:val='0'/>"
                    f"<w:numId w:val='{num.get(qn('w:numId'))}'/></w:numPr>"
                )
            )
            return p
    raise AssertionError(f"no {kind} list in the default template")


def test_word_to_pdf_keeps_links_boxes_and_numbered_lists(tmp_path, no_libreoffice):
    """Without LibreOffice a resume's "Email: jane@example.com" came out as "Email:" (a link's
    words are not among the paragraph's runs), anything in a content control was left out, and
    a list numbered with Word's button came out as bullets. Each time: "Made Jane.pdf"."""
    doc = Document()
    doc.add_heading("Jane Doe", 0)
    p = doc.add_paragraph("Email: ")
    _link(p, "mailto:jane@example.com", "jane@example.com")
    p.add_run(" | Phone: 555-0100")
    doc.element.body.append(
        parse_xml(
            f"<w:sdt {nsdecls('w')}><w:sdtContent><w:p><w:r><w:t>Objective: a senior role."
            "</w:t></w:r></w:p></w:sdtContent></w:sdt>"
        )
    )
    doc.add_paragraph("Step zero", style="List Number")  # python-docx's own numbered style
    _list_item(doc, "Preheat the oven", "decimal")
    _list_item(doc, "Mix the batter", "decimal")
    _list_item(doc, "Flour", "bullet")
    outer = doc.add_table(rows=1, cols=1)
    outer.cell(0, 0).text = "Outer cell"
    outer.cell(0, 0).add_table(rows=1, cols=1).cell(0, 0).text = "Inner table words"
    src = tmp_path / "Jane.docx"
    doc.save(str(src))
    blocks, _ = pixel_render.docx_blocks(src)
    items = [(b.text, b.number) for b in blocks if b.kind == "li"]
    assert items == [  # each list counts from 1, and a bulleted one stays bullets
        ("Step zero", 1),
        ("Preheat the oven", 1),
        ("Mix the batter", 2),
        ("Flour", None),
    ]
    text = pdf_text(pixel.run("to_pdf", [src]).outputs[0])
    assert "Email: jane@example.com | Phone: 555-0100" in text
    assert "Objective: a senior role." in text and "Inner table words" in text
    assert "1. Preheat the oven" in text.replace("\n", " ")


def test_a_resume_laid_out_as_one_tall_table_still_becomes_a_pdf(tmp_path, no_libreoffice):
    """A two-column layout table with a row taller than a page: the card showed reportlab's
    "Flowable <Table@0x...> too large on page 2"."""
    doc = Document()
    t = doc.add_table(rows=1, cols=2)
    t.cell(0, 0).text = "Skills\n" + "\n".join(f"Skill {i}" for i in range(20))
    t.cell(0, 1).text = "\n".join(f"2019-2024 Did thing number {i} at a company." for i in range(120))
    src = tmp_path / "Two column resume.docx"
    doc.save(str(src))
    r = pixel.run("to_pdf", [src])
    out = PdfReader(str(r.outputs[0]))
    text = pdf_text(r.outputs[0])
    assert len(out.pages) >= 2
    assert "Skill 19" in text and "thing number 0 " in text and "thing number 119 " in text


# ---------------------------------------------------------------------------- spreadsheets
def test_excel_to_pdf_shows_a_rate_as_a_rate(tmp_path, no_libreoffice):
    """Every number was rounded to two decimals: a 3.25% rate printed as 0.03 and a 0.004
    unit price as 0."""
    wb = Workbook()
    wb.active.title = "Empty"  # an empty first sheet used to cost a blank first page
    ws = wb.create_sheet("Loan")
    ws.append(["Item", "Value"])
    ws.append(["Interest rate", 0.0325])
    ws["B2"].number_format = "0.00%"
    ws.append(["Unit price", 0.004])
    ws.append(["Exchange rate", 1.08765])
    ws.append(["Balance", 1234.5])
    ws["B5"].number_format = '"$"#,##0.00'
    ws.append(["Paid", 1200.5])
    ws.append(["Due", dt.datetime(2026, 3, 5)])
    src = tmp_path / "loan.xlsx"
    wb.save(src)
    r = pixel.run("to_pdf", [src])
    pages = [p.extract_text() for p in PdfReader(str(r.outputs[0])).pages]
    assert len(pages) == 1
    cells = [c.strip() for c in pages[0].split("\n") if c.strip()]
    for label, shown in (
        ("Interest rate", "3.25%"),
        ("Unit price", "0.004"),
        ("Exchange rate", "1.08765"),
        ("Balance", "$1,234.50"),
        ("Paid", "1,200.5"),
        ("Due", "2026-03-05"),
    ):
        assert cells[cells.index(label) + 1] == shown


def _one_cell_dimension(path: Path) -> Path:
    """A workbook whose sheet claims to be one cell big, the way streaming exporters write it."""
    wb = Workbook()
    for i in range(5):
        wb.active.append([f"name{i}", i, i * 2])
    plain = path.with_name("plain.xlsx")
    wb.save(plain)
    with zipfile.ZipFile(plain) as zin, zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "xl/worksheets/sheet1.xml":
                data = re.sub(rb"<dimension[^>]*/>", b'<dimension ref="A1"/>', data)
                assert b'ref="A1"' in data
            zout.writestr(item, data)
    plain.unlink()
    return path


def test_a_sheet_that_says_it_is_one_cell_big_is_read_whole(tmp_path, no_libreoffice):
    """Believed, the CSV and the PDF had one cell of fifteen, under "Made export.csv"."""
    src = _one_cell_dimension(tmp_path / "export.xlsx")
    rows = pixel.run("excel_to_csv", [src]).outputs[0].read_text(encoding="utf-8-sig").splitlines()
    assert rows == [f"name{i},{i},{i * 2}" for i in range(5)]
    text = pdf_text(pixel.run("to_pdf", [src]).outputs[0])
    assert "name0" in text and "name4" in text
    assert len(pixel_pdf.document_lines(src)) == 5


def test_excel_to_csv_writes_a_date_as_a_date(tmp_path):
    wb = Workbook()
    ws = wb.active
    ws.append(["When", "Amount"])
    ws.append([dt.datetime(2026, 3, 5), 0.1 + 0.2])
    ws.append([dt.datetime(2026, 3, 6, 14, 30), 2])
    src = tmp_path / "sales.xlsx"
    wb.save(src)
    rows = pixel.run("excel_to_csv", [src]).outputs[0].read_text(encoding="utf-8-sig").splitlines()
    assert rows == ["When,Amount", "2026-03-05,0.3", "2026-03-06 14:30:00,2"]


def test_csv_to_excel_keeps_every_digit_of_a_long_number(tmp_path):
    """A 22-digit tracking number was stored as 9.400111899223345e+21, and Excel shows a
    16-digit card number with its last digit turned to zero."""
    src = tmp_path / "orders.csv"
    src.write_text(
        "Order,Card,Tracking,Zip,Amount\n"
        '1,4111111111111111,9400111899223344556677,02134,"1,234.50"\n',
        encoding="utf-8",
    )
    row = list(load_workbook(pixel.run("csv_to_excel", [src]).outputs[0]).active.iter_rows(values_only=True))[1]
    assert row == (1, "4111111111111111", "9400111899223344556677", "02134", 1234.5)


# ---------------------------------------------------------------------------- LibreOffice
def test_one_libreoffice_conversion_at_a_time(tmp_path, monkeypatch):
    """Two Word-to-PDF jobs started together share Pyxi's one LibreOffice profile: the second
    handed its file to the first and exited with no PDF, three times out of three."""
    running, most = [], []

    def fake_run(cmd, **kw):
        assert libreoffice.CONVERTING.locked()
        running.append(1)
        most.append(len(running))
        time.sleep(0.05)
        out = Path(cmd[cmd.index("--outdir") + 1])
        (out / (Path(cmd[-1]).stem + ".pdf")).write_bytes(b"%PDF-1.4")
        running.pop()
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(pixel.subprocess, "run", fake_run)
    targets = [tmp_path / f"out{i}.pdf" for i in range(3)]
    threads = [
        threading.Thread(target=pixel._soffice_pdf, args=("soffice", tmp_path / f"in{i}.docx", t))
        for i, t in enumerate(targets)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert most == [1, 1, 1] and all(t.is_file() for t in targets)
    assert not libreoffice.CONVERTING.locked()


def test_an_old_file_without_libreoffice_is_told_a_real_format(tmp_path, no_libreoffice):
    """The advice was the extension plus an "x": right for .doc, but "save it as .rtfx"."""
    for name, newer in (("letter.rtf", ".docx"), ("sheet.ods", ".xlsx"), ("old.doc", ".docx")):
        (tmp_path / name).write_bytes(b"x")
        with pytest.raises(PixelError, match=rf"save {name} as \{newer}, or install LibreOffice"):
            pixel.run("to_pdf", [tmp_path / name])


# ---------------------------------------------------------------------------- images
def test_every_page_of_a_fax_tiff_goes_into_the_pdf(tmp_path):
    """A scanner's TIFF holds several pages. Only the first went in: "Made fax.pdf from 1
    image", and two pages gone."""
    frames = [Image.new("RGB", (200, 280), c) for c in ("white", "silver", "gray")]
    frames[0].save(tmp_path / "fax.tif", save_all=True, append_images=frames[1:])
    r = pixel.run("images_to_pdf", [tmp_path / "fax.tif"])
    assert len(PdfReader(str(r.outputs[0])).pages) == 3
    assert r.summary == "Made fax.pdf from 1 image (3 pages)."
    Image.new("RGB", (200, 280), "white").save(tmp_path / "one.png")
    assert pixel.run("images_to_pdf", [tmp_path / "one.png"]).summary == "Made one.pdf from 1 image."


# ---------------------------------------------------------------------------- names
def test_a_name_given_with_its_extension_is_not_given_two(tmp_path):
    a, b = pdf(tmp_path / "a.pdf", 1), pdf(tmp_path / "b.pdf", 1)
    assert pixel.run("merge_pdfs", [a, b], {"name": "Combined.pdf"}).outputs[0].name == "Combined.pdf"
    Image.new("RGB", (50, 50), "white").save(tmp_path / "scan.png")
    made = pixel.run("images_to_pdf", [tmp_path / "scan.png"], {"name": "My scan.PDF"})
    assert made.outputs[0].name == "My scan.pdf"
    assert pixel.run("merge_pdfs", [a, b], {"name": "Q3 v1.2"}).outputs[0].name == "Q3 v1.2.pdf"


def test_a_long_list_of_pages_is_not_the_files_name(tmp_path):
    """"Delete every other page" of a 300-page scan put all 150 numbers in the new file's
    name, which Windows turned down: the card read "[Errno 22] Invalid argument: 'C:\\..."."""
    src = pdf(tmp_path / "duplex scan.pdf", 300)
    every_other = ", ".join(str(i) for i in range(2, 301, 2))
    r = pixel.run("delete_pages", [src], {"pages": every_other})
    assert r.outputs[0].name == "duplex scan (without 150 pages).pdf"
    assert len(PdfReader(str(r.outputs[0])).pages) == 150 and len(r.summary) < 120
    r = pixel.run("extract_pages", [src], {"pages": every_other})
    assert r.outputs[0].name == "duplex scan (150 pages).pdf"
    # Whatever the name, it is one Windows will take.
    long = unique_path(tmp_path, "x" * 400 + " (without pages 1, 2).pdf")
    assert len(str(long)) < 250 and long.suffix == ".pdf"
    long.write_bytes(b"")
    # And a dot in the name is not the start of an extension.
    (tmp_path / "Report v1.2 (split)").mkdir()
    assert unique_path(tmp_path, "Report v1.2 (split)").name == "Report v1.2 (split) (2)"


# ---------------------------------------------------------------------------- several files
def test_one_bad_file_of_three_does_not_hide_the_two_that_were_made(tmp_path, no_libreoffice):
    """The job said only "failed", with two finished files on the disk it never mentioned
    (and a second try made them again as "(2)")."""
    one, three = pdf(tmp_path / "one.pdf", 2), pdf(tmp_path / "three.pdf", 2)
    two = tmp_path / "two.pdf"
    two.write_bytes(b"%PDF-1.4 not really")
    r = pixel.run("rotate_pages", [one, two, three], {"angle": 90})
    assert [o.name for o in r.outputs] == ["one (rotated).pdf", "three (rotated).pdf"]
    assert "But two.pdf doesn't open as a PDF" in r.summary and len(r.details["failed"]) == 1
    with pytest.raises(PixelError, match="doesn't open as a PDF"):
        pixel.run("rotate_pages", [two, two], {"angle": 90})  # nothing made: that is a failure
    (tmp_path / "note.txt").write_text("Hello there.", encoding="utf-8")
    (tmp_path / "old.doc").write_bytes(b"x")
    r = pixel.run("to_pdf", [tmp_path / "note.txt", tmp_path / "old.doc"])
    assert [o.name for o in r.outputs] == ["note.pdf"]
    assert "Made note.pdf (1 page)." in r.summary and "But .doc files need LibreOffice" in r.summary


async def test_a_job_with_one_bad_file_ends_with_what_it_made(settings, tmp_path):
    one = pdf(tmp_path / "one.pdf", 2)
    two = tmp_path / "two.pdf"
    two.write_bytes(b"%PDF-1.4 not really")
    brain, rec = make_brain(settings)
    job = await brain.jobs.start(
        "pixel", "rotate", files=[str(one), str(two)], params={"operation": "rotate_pages"}
    )
    job = await brain.jobs.wait(job.id)
    await brain.jobs.wait_all()
    assert job.status == "done" and job.outputs == [str(tmp_path / "one (rotated).pdf")]
    assert "two.pdf doesn't open as a PDF" in job.summary
    assert rec.of("job_ready")[-1]["files"] == job.outputs


# ---------------------------------------------------------------------------- page numbers
def _ink_rows(path: Path) -> tuple[int, int, int]:
    """(page height, first row with ink, last row with ink) of page 1 as it is shown."""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(path))
    try:
        im = doc[0].render(scale=1).to_pil().convert("L")
    finally:
        doc.close()
    rows = [y for y in range(im.height) if im.crop((0, y, im.width, y + 1)).getextrema()[0] < 128]
    return im.height, rows[0], rows[-1]


def test_page_numbers_land_inside_a_cropped_page(tmp_path):
    """Placed by the full sheet, the number of a cropped PDF fell outside the part that is
    shown: "Numbered the pages", and no numbers to see."""
    w = PdfWriter(clone_from=PdfReader(str(pdf(tmp_path / "plain.pdf", 2))))
    for page in w.pages:
        page.cropbox = RectangleObject([36, 100, 576, 742])
    with (tmp_path / "cropped.pdf").open("wb") as fh:
        w.write(fh)
    _, _, last_before = _ink_rows(tmp_path / "cropped.pdf")
    r = pixel.run("add_page_numbers", [tmp_path / "cropped.pdf"], {"format": "Page {n} of {total}"})
    height, _, last = _ink_rows(r.outputs[0])
    assert last > last_before and height - 40 < last < height  # near the bottom of what's shown
    assert "Page 1 of 2" in PdfReader(str(r.outputs[0])).pages[0].extract_text()


# ---------------------------------------------------------------------------- guessing
def test_a_format_is_not_a_form_and_turning_into_is_not_turning_round():
    """"Convert this PDF to Word format" went to the form filler (the letters f-o-r-m), and
    "turn each page into a JPG" rotated every page and said so."""
    g = pixel.guess_operation
    assert g("convert this pdf to word format", ["a.pdf"]) == "pdf_to_word"
    assert g("reformat this as a Word document", ["a.pdf"]) == "pdf_to_word"
    assert g("save the information in this pdf as a docx", ["a.pdf"]) == "pdf_to_word"
    assert g("turn each page of this pdf into a jpg", ["a.pdf"]) == "pdf_to_images"
    assert g("turn these pages into images", ["a.pdf"]) == "pdf_to_images"
    assert g("unblock the printing on this pdf", ["a.pdf"]) != "protect_pdf"
    # What they were for still works.
    assert g("fill in this form", ["a.pdf"]) == "fill_form"
    assert g("fill out the forms", ["a.pdf"]) == "fill_form"
    assert g("turn page 2 sideways", ["a.pdf"]) == "rotate_pages"
    assert g("turn the pages the right way up", ["a.pdf"]) == "rotate_pages"
    assert g("rotate it", ["a.pdf"]) == "rotate_pages"
    assert g("lock this pdf", ["a.pdf"]) == "protect_pdf"
