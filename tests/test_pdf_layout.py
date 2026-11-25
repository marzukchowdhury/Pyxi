"""Reading a PDF in reading order with its tables kept (agents/pdf_layout.py), and what Pixel
does with that: text, Word tables, and a PDF's tables as the sheets of a workbook.

Every PDF here is made in the test from made-up words. No model, no network.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from docx import Document
from openpyxl import load_workbook
from pypdf import PdfReader, PdfWriter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from pyxi import files_access
from pyxi.agents import pdf_layout, pixel, pixel_pdf
from pyxi.agents.pixel_common import PixelError

W, H = A4
S = getSampleStyleSheet()
WORDS = ["river", "stone", "market", "harbour", "lantern", "orchard", "copper", "meadow", "winter", "signal", "garden", "bridge"]

SALES = [
    ["Region", "Product", "Units", "Unit price", "Revenue", "Note"],
    ["North", "Copper kettle", "120", "14.50", "1,740.00", "steady"],
    ["North", "Garden lantern", "85", "22.00", "1,870.00", ""],
    ["South", "Paper ribbon", "310", "1.20", "372.00", "new shop"],
    ["West", "Paper ribbon", "", "1.20", "0.00", "none sold"],
    ["Total", "", "515", "", "3,982.00", ""],
]


def sentence(n: int, tag: str) -> str:
    return f"{tag}{n:02d} the " + " ".join(WORDS[(n * 5 + k * 3) % len(WORDS)] for k in range(9)) + " ends here."


def ruled(path: Path, rows=SALES) -> Path:
    t = Table(rows, colWidths=[55, 110, 45, 65, 70, 80][: len(rows[0])])
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.6, colors.black)]))
    SimpleDocTemplate(str(path), pagesize=A4).build(
        [Paragraph("Sales by region", S["Heading1"]), Paragraph("B01 Before the table.", S["Normal"]), Spacer(1, 10), t, Spacer(1, 10), Paragraph("B02 After the table.", S["Normal"])]
    )
    return path


def unruled(path: Path) -> Path:
    t = Table(SALES, colWidths=[55, 110, 45, 65, 70, 80])
    t.setStyle(TableStyle([("ALIGN", (2, 0), (4, -1), "RIGHT")]))
    SimpleDocTemplate(str(path), pagesize=A4).build(
        [Paragraph("Sales", S["Heading1"]), Paragraph("U01 A table with no lines at all, only space between its columns.", S["Normal"]), Spacer(1, 10), t, Spacer(1, 10), Paragraph("U02 After the table.", S["Normal"])]
    )
    return path


def two_columns_written_a_row_at_a_time(path: Path) -> tuple[Path, list[str]]:
    """Two columns whose lines were put in the file a row at a time, the right column's first:
    the file's own order is not the reading order."""
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setFont("Helvetica-Bold", 18)
    c.drawCentredString(W / 2, H - 25 * mm, "The Orchard Bridge Report")
    c.setFont("Helvetica", 10)
    cols: list[list[str]] = [[], []]
    order = []
    for n in range(1, 21):
        col = 0 if n <= 10 else 1
        order.append(f"K{n:02d}")
        cols[col] += [*textwrap.wrap(sentence(n, "K"), 46), ""]
    for k in range(max(len(cols[0]), len(cols[1]))):
        for col in (1, 0):
            if k < len(cols[col]) and cols[col][k]:
                c.drawString((20 if col == 0 else 110) * mm, H - 40 * mm - k * 12, cols[col][k])
    c.showPage()
    c.save()
    return path, order


def in_order(text: str, marks: list[str]) -> bool:
    places = [text.find(m) for m in marks]
    return -1 not in places and places == sorted(places)


# ---------------------------------------------------------------------------- tables
def test_a_ruled_table_comes_out_with_every_cell_in_its_place(tmp_path):
    layout = pdf_layout.read(ruled(tmp_path / "ruled.pdf"))
    [table] = layout.tables
    assert table.rows == SALES and table.ruled  # the empty cells too
    text = layout.markdown()
    assert "| North | Copper kettle | 120 | 14.50 | 1,740.00 | steady |" in text
    assert in_order(text, ["B01", "| Region |", "| Total |", "B02"])
    assert "# Sales by region" in text


def test_a_table_with_no_lines_is_found_by_the_gaps_between_its_columns(tmp_path):
    layout = pdf_layout.read(unruled(tmp_path / "unruled.pdf"))
    [table] = layout.tables
    assert table.rows == SALES and not table.ruled
    assert in_order(layout.markdown(), ["U01", "| Region |", "U02"])


def test_a_table_that_runs_over_two_pages_is_one_table_with_one_heading_row(tmp_path):
    head = ["Invoice", "Customer", "Amount"]
    rows = [head] + [[f"INV-{1000 + n}", f"{WORDS[n % 12].title()} Ltd", f"{n * 137.35:,.2f}"] for n in range(1, 61)]
    t = Table(rows, colWidths=[90, 200, 90], repeatRows=1)
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.grey)]))
    path = tmp_path / "long.pdf"
    SimpleDocTemplate(str(path), pagesize=A4).build([Paragraph("P01 Sixty invoices.", S["Normal"]), t])
    assert len(PdfReader(str(path)).pages) == 2
    layout = pdf_layout.read(path)
    [table] = layout.tables
    assert table.rows == rows and table.pages == [1, 2]  # the repeated heading row is there once


def test_a_table_with_wrapped_cells_and_a_first_column_left_blank(tmp_path):
    """No lines, a description that wraps, and an area named only where it changes: the
    wrapped line belongs to its row, and a row with a blank first cell is still its own row."""
    rows = [
        ["Area", "Item", "Description", "Qty", "Cost"],
        ["Kitchen", "Kettle", "Copper kettle with a wooden handle, two litres", "2", "48.00"],
        ["", "Lantern", "Garden lantern for the back door", "1", "22.50"],
        ["Hall", "Ribbon", "Paper ribbon in three colours for the winter market stall", "12", "14.40"],
        ["Garden", "Bridge kit", "Small wooden bridge for the orchard stream, flat packed", "1", "310.00"],
    ]
    path = tmp_path / "wrapped.pdf"
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setFont("Helvetica", 10)
    c.drawString(25 * mm, H - 30 * mm, "V01 What each room still needs.")
    xs = [25 * mm, 50 * mm, 80 * mm, 150 * mm, 170 * mm]
    y = H - 42 * mm
    for row in rows:
        lines = textwrap.wrap(row[2], 34)
        for k, x in enumerate(xs):
            if k == 2:
                for j, ln in enumerate(lines):
                    c.drawString(x, y - j * 11.5, ln)
            elif k >= 3:
                c.drawRightString(x + 12 * mm, y, row[k])
            else:
                c.drawString(x, y, row[k])
        y -= 11.5 * len(lines) + 7
    c.drawString(25 * mm, y - 8, "V02 Prices include delivery.")
    c.save()
    layout = pdf_layout.read(path)
    assert [t.rows for t in layout.tables] == [rows]
    assert "V02 Prices include delivery." in layout.markdown().splitlines()[-1]


def test_a_numbered_list_is_not_a_table(tmp_path):
    path = tmp_path / "list.pdf"
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setFont("Helvetica", 11)
    for k in range(6):
        c.drawString(25 * mm, H - 30 * mm - k * 16, f"{k + 1}.")
        c.drawString(40 * mm, H - 30 * mm - k * 16, sentence(k, "N"))
    c.save()
    assert pdf_layout.read(path).tables == []


# ---------------------------------------------------------------------------- reading order
def test_two_columns_are_read_one_after_the_other(tmp_path):
    path, order = two_columns_written_a_row_at_a_time(tmp_path / "columns.pdf")
    plain, _ = pixel_pdf._page_texts_plain(path)
    assert not in_order(plain[0], order)  # the file's own order shuffles the columns
    layout = pdf_layout.read(path)
    assert in_order(layout.markdown(), order) and layout.tables == []
    assert layout.blocks[0].kind == "h1" and layout.blocks[0].text == "The Orchard Bridge Report"


def test_two_columns_of_text_are_not_taken_for_a_table(tmp_path):
    path, _ = two_columns_written_a_row_at_a_time(tmp_path / "columns.pdf")
    with pytest.raises(PixelError, match="couldn't find a table"):
        pixel.run("pdf_to_excel", [path])


def test_running_headers_and_footers_are_left_out_and_a_cut_paragraph_is_joined(tmp_path):
    path = tmp_path / "report.pdf"

    def deco(c, doc) -> None:
        c.setFont("Helvetica", 9)
        c.drawString(20 * mm, H - 12 * mm, "Northwind Example Co.  Annual review")
        c.drawCentredString(W / 2, 10 * mm, f"Page {doc.page} of 4")

    story = [Paragraph(sentence(n, "F") + " " + sentence(n + 50, "x"), S["Normal"]) for n in range(1, 90)]
    SimpleDocTemplate(str(path), pagesize=A4, topMargin=22 * mm, bottomMargin=20 * mm).build(story, onFirstPage=deco, onLaterPages=deco)
    pages = len(PdfReader(str(path)).pages)
    assert pages >= 3
    plain = "\n".join(pixel_pdf._page_texts_plain(path)[0])
    assert plain.count("Northwind Example Co.") == pages
    layout = pdf_layout.read(path)
    text = layout.markdown()
    assert "Northwind Example Co." not in text and "Page 2 of" not in text
    assert "Northwind Example Co. Annual review" in layout.dropped
    assert in_order(text, [f"F{n:02d}" for n in range(1, 90)])
    # No paragraph was cut by the page break: every one still ends as it was written.
    assert all(b.text.endswith("ends here.") for b in layout.blocks)


def test_a_two_page_pdf_keeps_its_first_and_last_lines(tmp_path):
    """With only two pages nothing can be called a running header: nothing is dropped."""
    path = tmp_path / "two.pdf"
    c = canvas.Canvas(str(path), pagesize=A4)
    for page in range(2):
        c.setFont("Helvetica", 10)
        c.drawString(20 * mm, H - 12 * mm, "Membership form")
        c.drawString(20 * mm, H - 40 * mm, sentence(page, "T"))
        c.showPage()
    c.save()
    layout = pdf_layout.read(path)
    assert layout.markdown().count("Membership form") == 2 and layout.dropped == []


# ---------------------------------------------------------------------------- forms, scans, odd files
def test_what_is_typed_into_a_form_is_read_beside_its_label(tmp_path):
    path = tmp_path / "form.pdf"
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setFont("Helvetica", 11)
    y = H - 40 * mm
    for label, value in (("Full name", "Ana Example"), ("Email address", "ana@example.com"), ("City", "Springfield")):
        c.drawString(25 * mm, y + 2 * mm, label)
        c.acroForm.textfield(name=label.replace(" ", "_"), x=70 * mm, y=y, width=90 * mm, height=7 * mm, value=value)
        y -= 13 * mm
    c.save()
    plain = "\n".join(pixel_pdf._page_texts_plain(path, ocr=False)[0])
    assert "Ana Example" not in plain  # an answer is not page text
    text = pdf_layout.read(path, ocr=False).markdown()
    assert "| Full name | Ana Example |" in text and "| Email address | ana@example.com |" in text


def test_a_scanned_table_is_read_through_its_ruling_lines(tmp_path):
    """A page that is only a picture: the lines in the picture are the table's grid, and each
    word OCR reads goes in the cell it sits in. (OCR itself is stood in for here.)"""
    import pypdfium2 as pdfium
    from reportlab.lib.utils import ImageReader

    from pyxi.screen.ocr import TextLine

    src = ruled(tmp_path / "typed.pdf", [r[:5] for r in SALES[:4]])
    doc = pdfium.PdfDocument(str(src))
    page = doc[0]
    img = page.render(scale=200 / 72).to_pil().convert("RGB")
    height = page.get_height()
    words = pdf_layout._page_words(page.get_textpage(), height)
    doc.close()
    scan = tmp_path / "scan.pdf"
    c = canvas.Canvas(str(scan), pagesize=A4)
    c.drawImage(ImageReader(img), 0, 0, W, H)
    c.save()
    k = 200 / 72
    lines = [
        TextLine(w.text, (int(w.x0 * k), int(w.y0 * k), int((w.x1 - w.x0) * k), int((w.y1 - w.y0) * k)), [(w.text, (int(w.x0 * k), int(w.y0 * k), int((w.x1 - w.x0) * k), int((w.y1 - w.y0) * k)))])
        for w in words
    ]
    seen = {}

    def reader(picture):
        seen["size"] = picture.size
        return lines

    layout = pdf_layout.read(scan, ocr_image=reader)
    assert layout.scanned == [1] and seen["size"] == img.size
    [table] = layout.tables
    assert table.ruled and table.rows == [r[:5] for r in SALES[:4]]


def test_ruling_lines_are_wiped_from_the_picture_before_it_is_read(tmp_path):
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (900, 600), "white")
    d = ImageDraw.Draw(img)
    for y in (100, 200, 300):
        d.line((100, y, 800, y), fill="black", width=3)
    for x in (100, 450, 800):
        d.line((x, 100, x, 300), fill="black", width=3)
    d.rectangle((150, 130, 170, 170), fill="black")  # a letter-sized blot: not a line
    hs, vs, clean = pdf_layout.picture_lines(img)
    [(xs, ys, _, _)] = pdf_layout._ruled_tables(hs, vs)
    assert len(xs) == 3 and len(ys) == 3
    assert clean.getpixel((300, 200)) == 255 and clean.getpixel((160, 150)) == 0


def test_figures_ocr_misreads_in_a_scanned_table_are_put_right():
    assert pdf_layout._ocr_number("1 ,870.oo") == "1,870.00"
    assert pdf_layout._ocr_number("Oolong") == "Oolong" and pdf_layout._ocr_number("no 4") == "no 4"


def test_a_locked_pdf_says_so_everywhere(tmp_path):
    w = PdfWriter(clone_from=PdfReader(str(ruled(tmp_path / "open.pdf"))))
    w.encrypt("made-up-test-pass")
    locked = tmp_path / "locked.pdf"
    with locked.open("wb") as fh:
        w.write(fh)
    for op in ("extract_text", "pdf_to_word", "pdf_to_excel"):
        with pytest.raises(PixelError, match="password-protected"):
            pixel.run(op, [locked])
    with pytest.raises(pdf_layout.PdfLocked):
        pdf_layout.read(locked)
    with pytest.raises(ValueError, match="password-protected"):
        files_access.read_text(locked)
    assert [p.name for p in tmp_path.iterdir() if p.suffix != ".pdf"] == []


def test_right_to_left_lines_come_out_in_reading_order(tmp_path):
    arial = Path(r"C:\Windows\Fonts\arial.ttf")
    if not arial.is_file():
        pytest.skip("needs a font with Hebrew letters")
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    pdfmetrics.registerFont(TTFont("ArialTest", str(arial)))
    lines = ["מספר ההזמנה הוא 2041 בלבד", "שלום עולם זה מסמך בדיקה"]
    path = tmp_path / "rtl.pdf"
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setFont("ArialTest", 14)
    for k, line in enumerate(lines):
        # A PDF stores what is seen: the words right to left, a number left to right among them.
        seen = " ".join(w if w.isdigit() else w[::-1] for w in reversed(line.split()))
        c.drawRightString(W - 25 * mm, H - 30 * mm - 22 * k, seen)
    c.save()
    assert pdf_layout.read(path, ocr=False).markdown() == " ".join(lines)


def test_a_page_with_one_short_typed_line_keeps_its_own_words(tmp_path):
    """Fewer than 25 letters sends a page to OCR, in case it is a scan. What OCR makes of it
    replaces the typed words only when the picture plainly holds far more."""
    path = tmp_path / "cover.pdf"
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setFont("Helvetica", 14)
    c.drawString(72, 700, "Annual review")
    c.save()
    junk = [type("L", (), {"text": "Annua1 rev1ew", "rect": (200, 250, 300, 40), "words": []})()]
    layout = pdf_layout.read(path, ocr_image=lambda img: junk)
    assert layout.markdown() == "Annual review" and layout.scanned == []


def test_a_page_the_layout_reader_cannot_lay_out_is_still_read(tmp_path, monkeypatch):
    path = ruled(tmp_path / "ruled.pdf")
    monkeypatch.setattr(pdf_layout, "_lay_out", lambda *a, **k: 1 / 0)
    layout = pdf_layout.read(path)
    assert layout.plain == [1] and "Copper kettle" in layout.markdown()
    monkeypatch.setattr(pdf_layout, "read", lambda *a, **k: 1 / 0)
    texts, _ = pixel_pdf.page_texts(path)  # the reader itself failing: the plain one is used
    assert "Copper kettle" in texts[0]


# ---------------------------------------------------------------------------- what Pixel makes of it
def test_pdf_tables_become_the_sheets_of_a_workbook(tmp_path):
    r = pixel.run("pdf_to_excel", [ruled(tmp_path / "Sales.pdf")])
    [out] = r.outputs
    assert out.name == "Sales (tables).xlsx" and "1 table" in r.summary
    ws = load_workbook(out).worksheets[0]
    assert ws.title == "Table 1 (p1)" and ws.freeze_panes == "A2" and ws["A1"].font.bold
    assert [c.value for c in ws[1]] == SALES[0]
    assert [c.value for c in ws[2]] == ["North", "Copper kettle", 120, 14.5, 1740, "steady"]
    assert ws["E2"].number_format == "#,##0.00" and ws["C5"].value is None  # an empty cell stays empty
    again = pixel.run("pdf_to_excel", [tmp_path / "Sales.pdf"])
    assert again.outputs[0].name == "Sales (tables) (2).xlsx"  # never over the first


def test_a_cell_is_a_number_only_when_it_plainly_is_one():
    v = pdf_layout.cell_value
    assert (v("1,740.00"), v("120"), v("(250)"), v("12%"), v("$4.50")) == (1740.0, 120, -250, 0.12, 4.5)
    assert (v("007"), v("2026-03-01"), v("INV-1001"), v("1234567890123456"), v("")) == ("007", "2026-03-01", "INV-1001", "1234567890123456", "")


def test_pdf_to_word_keeps_a_table_as_a_table(tmp_path):
    r = pixel.run("pdf_to_word", [ruled(tmp_path / "Sales.pdf")])
    doc = Document(str(r.outputs[0]))
    [table] = doc.tables
    assert [[c.text for c in row.cells] for row in table.rows] == SALES
    assert "1 table came through" in r.summary
    paras = [p.text for p in doc.paragraphs if p.text]
    assert paras == ["Sales by region", "B01 Before the table.", "B02 After the table."]
    assert doc.paragraphs[0].style.name == "Heading 1"


def test_the_text_of_a_pdf_and_a_quick_read_show_the_table(tmp_path):
    path = ruled(tmp_path / "Sales.pdf")
    r = pixel.run("extract_text", [path])
    assert "| North | Garden lantern | 85 | 22.00 | 1,870.00 |  |" in r.outputs[0].read_text(encoding="utf-8")
    quick = files_access.read_text(path)
    assert quick.startswith("PDF, 1 pages.\n[page 1]\n# Sales by region") and "| Region | Product |" in quick
    # Compared with its Word copy, a table reads the same row for row.
    word = pixel.run("pdf_to_word", [path]).outputs[0]
    assert "North | Copper kettle | 120 | 14.50 | 1,740.00 | steady" in pixel_pdf.document_lines(path)
    assert sorted(pixel_pdf.document_lines(path)) == sorted(pixel_pdf.document_lines(word))


def test_pixel_is_asked_for_tables_in_plain_words():
    g = pixel.guess_operation
    assert g("pull the table on page 3 into excel", ["a.pdf"]) == "pdf_to_excel"
    assert g("turn this pdf into a spreadsheet", ["a.pdf"]) == "pdf_to_excel"
    assert g("extract page 3", ["a.pdf"]) == "extract_pages"
    assert g("convert it to word with the tables", ["a.pdf"]) == "pdf_to_word"
    assert "pdf_to_excel" in pixel.OPERATIONS and "pdf_to_excel" in pixel.ONE_FILE
