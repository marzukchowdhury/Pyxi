"""Filling a PDF form by asking (agents/pixel_forms.py reads the page, agents/pixel_ask.py
makes the questions, the card and the fill). The forms here are made up, the model is a
script, and the card is answered by the test."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pypdf import PdfReader

from pyxi import booking
from pyxi.agents import pixel, pixel_ask, pixel_forms, pixel_pdf
from pyxi.agents.pixel import PixelError
from pyxi.companion import filework, tools
from test_brain_and_server import make_brain
from test_connectors_and_docs import Script
from test_pixel_documents import form as named_form

DETAILS = {"first_name": "Ana", "last_name": "Mock", "email": "ana@example.com", "phone": "604-555-0100"}
SECRET = pixel_pdf.SECRET

# What the model writes for the made-up form below: its questions, a line at a time. The last
# seven lines are wrong in one way each and must not survive.
MAP = """# A. Applicant
T f1 | First name | first_name
T f2 | Last name | last_name
T [f3] | Email | email
T f4 | SIN
C Pets | Yes=f5; No=f6
# B. Landlord
T f7 | Landlord name
T f8 | Landlord phone number
# Rent
T f9 | Monthly rent ($)
T f10 | Home address | address
T f11 | Address you are moving to | address
T f9 | Monthly rent, again
T f77 | A field the form doesn't have
T f5 | A tick box as a place to write
C Smoker | Maybe=f12
C Pets again | Yes=f5
T f12 | Favourite colour
Here is some chatter that is not a line.
"""


def unnamed_form(path: Path, first: str = "Already Here") -> Path:
    """A rental form the way many real ones are made: every field is 'untitledN', and what
    each is for is only printed beside it."""
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(path))
    c.drawString(72, 760, "A. APPLICANT")
    n = 0

    def text(x: float, y: float, width: float = 150, value: str = "") -> None:
        nonlocal n
        n += 1
        c.acroForm.textfield(name=f"untitled{n}", x=x, y=y - 5, width=width, height=18, value=value)

    def box(x: float, y: float) -> None:
        nonlocal n
        n += 1
        c.acroForm.checkbox(name=f"untitled{n}", x=x, y=y - 3, size=12, buttonStyle="check")

    c.drawString(72, 730, "First name")
    text(150, 730, value=first)  # f1
    c.drawString(320, 730, "Last name")
    text(390, 730)  # f2
    c.drawString(72, 700, "Email")
    text(150, 700)  # f3
    c.drawString(320, 700, "SIN")
    text(390, 700)  # f4
    c.drawString(72, 670, "Pets:")
    box(110, 670)  # f5
    c.drawString(126, 670, "Yes")
    box(160, 670)  # f6
    c.drawString(176, 670, "No")
    c.drawString(72, 640, "B. LANDLORD")
    c.drawString(72, 610, "Name")
    c.drawString(250, 610, "Phone number")
    text(72, 585)  # f7: under "Name"
    text(250, 585)  # f8: under "Phone number"
    c.drawString(72, 550, "Monthly rent: $")
    text(160, 550, 80)  # f9
    c.drawString(72, 520, "Home address")
    text(160, 520, 300)  # f10
    c.drawString(72, 490, "Address you are moving to")
    text(230, 490, 250)  # f11
    c.drawString(72, 460, "Parking:")
    box(130, 460)  # f12
    c.drawString(146, 460, "Wanted")
    c.save()
    return path


def values(path) -> dict[str, str]:
    return {k: v.get("/V") for k, v in PdfReader(str(path)).get_fields().items()}


def mapped(tmp_path, **kw):
    form = pixel_forms.read(unnamed_form(tmp_path / "rental.pdf", **kw))
    questions, dropped = pixel_ask.read_map(MAP, form)
    return form, questions, dropped


# ---------------------------------------------------------------------------- reading the page
def test_the_page_is_laid_out_with_each_field_where_it_sits(tmp_path):
    f = pixel_forms.read(unnamed_form(tmp_path / "rental.pdf"))
    assert len(f.fields) == 12 and f.unnamed()
    text = f.outline()
    assert "First name [f1 filled] Last name [f2]" in text
    assert "Email [f3] SIN [f4]" in text
    assert "Pets: [f5 box] Yes [f6 box] No" in text
    assert "Monthly rent: $ [f9]" in text
    assert "Already Here" not in text  # what is typed in the form is never part of the outline
    by = f.by_id()
    assert by["f1"].value == "Already Here" and by["f1"].filled and not by["f2"].filled
    assert by["f2"].label == "Last name" and by["f4"].label == "SIN"
    assert by["f5"].label == "Yes" and by["f6"].label == "No"  # a box's words follow it
    assert by["f7"].label == "Name" and by["f8"].label == "Phone number"  # the heading above
    assert by["f5"].kind == "box" and by["f5"].state.startswith("/")
    assert text.index("B. LANDLORD") < text.index("[f7]")  # sections stay in reading order
    assert not pixel_forms.read(named_form(tmp_path / "named.pdf")).unnamed()
    for junk in ("untitled17", "Text3", "Check Box 12", "field_4", "form1.page2.TextField7", "f1_02", "3"):
        assert pixel_forms.junk_name(junk), junk
    for real in ("First Name", "email", "Has pets", "SIN", "DOB"):
        assert not pixel_forms.junk_name(real), real


# ---------------------------------------------------------------------------- the map
def test_every_line_of_the_map_is_checked_against_the_page(tmp_path):
    form, questions, dropped = mapped(tmp_path)
    assert [(q.id, q.kind, q.field, q.text) for q in questions if q.kind == "text"] == [
        ("q1", "text", "f1", "First name"),
        ("q2", "text", "f2", "Last name"),
        ("q3", "text", "f3", "Email"),
        ("q4", "text", "f4", "SIN"),
        ("q6", "text", "f7", "Landlord name"),
        ("q7", "text", "f8", "Landlord phone number"),
        ("q8", "text", "f9", "Monthly rent ($)"),
        ("q9", "text", "f10", "Home address"),
        ("q10", "text", "f11", "Address you are moving to"),
    ]
    pets = questions[4]
    assert (pets.kind, pets.text, pets.options) == ("choice", "Pets", [["Yes", "f5"], ["No", "f6"]])
    assert pets.section == "A. Applicant" and questions[5].section == "B. Landlord"
    assert [q.detail for q in questions[:3]] == ["first_name", "last_name", "email"]
    # a field used twice, one that isn't there, a box as text, words that aren't beside the
    # box, a box used twice, a question that shares no word with the page, and chatter
    assert dropped == 7
    assert pixel_ask.read_map("", form) == ([], 0)
    assert pixel_ask.read_map("I can't read this form, sorry.", form) == ([], 1)


def signed_form(path: Path) -> Path:
    """Two boxes side by side, each named only by the words under it; a row of money boxes
    named by the row and by the column over each; and a note several lines long."""
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(path))
    c.drawString(72, 760, "I agree to the terms of this made-up form.")
    c.acroForm.textfield(name="untitled1", x=72, y=715, width=200, height=18)  # f1
    c.acroForm.textfield(name="untitled2", x=320, y=715, width=150, height=18)  # f2
    c.drawString(72, 703, "Applicant signature")
    c.drawString(320, 703, "Date (day/month/year)")
    c.drawString(250, 660, "Applicant:")
    c.drawString(400, 660, "Other earners:")
    c.drawString(72, 635, "Full time employment: $")
    c.acroForm.textfield(name="untitled3", x=250, y=630, width=90, height=18)  # f3
    c.drawString(385, 635, "$")
    c.acroForm.textfield(name="untitled4", x=400, y=630, width=90, height=18)  # f4
    c.drawString(72, 600, "Position:")
    c.acroForm.textfield(name="untitled5", x=130, y=595, width=120, height=18)  # f5
    c.drawString(270, 600, "Hours:")
    c.acroForm.textfield(name="untitled6", x=315, y=595, width=60, height=18)  # f6
    c.drawString(72, 560, "Other:")
    c.acroForm.textfield(name="untitled7", x=120, y=555, width=200, height=18)  # f7
    c.drawString(72, 520, "Notes")
    c.acroForm.textfield(
        name="untitled8", x=120, y=470, width=300, height=60, value="A first line\nand a second   line", fieldFlags="multiline"
    )  # f8
    c.save()
    return path


def test_boxes_side_by_side_are_told_apart_by_the_words_over_or_under_each(tmp_path):
    form = pixel_forms.read(signed_form(tmp_path / "signed.pdf"))
    by = form.by_id()
    assert by["f1"].below == "Applicant signature" and by["f2"].below == "Date (day/month/year)"
    assert by["f1"].mates == ["f2"] and by["f4"].above == "Other earners:"
    right = (
        "T f2 | Applicant signature date (day/month/year)\n"
        "T f3 | Full time employment income: applicant ($)\n"
        "T f4 | Full time employment income: other earners ($)\n"
        "T f5 | Position\nT f6 | Hours\nT f7 | Other (specify)\nT f8 | Notes"
    )
    questions, dropped = pixel_ask.read_map(right, form)
    assert [q.field for q in questions] == ["f2", "f3", "f4", "f5", "f6", "f7", "f8"] and dropped == 0
    # the same lines pinned to the neighbour: every one is dropped, none is believed
    wrong = (
        "T f1 | Applicant signature date (day/month/year)\n"
        "T f4 | Full time employment income: applicant ($)\n"
        "T f3 | Full time employment income: other earners ($)\n"
        "T f6 | Position\nT f5 | Hours"
    )
    assert pixel_ask.read_map(wrong, form) == ([], 5)
    assert pixel_ask.coverage(questions, form) == 7 / 8 and pixel_ask.coverage([], form) == 0.0


def test_an_answer_left_as_it_was_is_not_written_again(tmp_path):
    src = signed_form(tmp_path / "signed.pdf")
    form = pixel_forms.read(src)
    questions, _ = pixel_ask.read_map("T f5 | Position\nT f8 | Notes", form)
    card = pixel_ask.card(questions, form, {}, {}, SECRET)
    shown = {c["label"]: c["value"] for c in card}
    assert shown["Notes"] == "A first line\nand a second   line"
    one_line = "A first line and a second   line"  # what a one-line box on the card hands back
    assert pixel_ask.plan(questions, form, {"q1": "Baker", "q2": one_line}, SECRET) == {"untitled5": "Baker"}
    r = pixel_pdf.fill_form([src], {}, {}, None, {"untitled5": "Baker"}, form.labels())
    assert values(r.outputs[0])["untitled8"] == "A first line\nand a second   line"
    assert pixel_ask.plan(questions, form, {"q2": "A new note"}, SECRET) == {"untitled8": "A new note"}


def test_the_model_is_shown_the_blank_form(tmp_path):
    form = pixel_forms.read(unnamed_form(tmp_path / "rental.pdf"))
    text = pixel_ask.prompt(form)
    assert "First name [f1] Last name [f2]" in text and "filled" not in text
    assert "Already Here" not in text
    assert "f12" not in pixel_ask.SYSTEM  # an id in the instructions was copied onto a real form


def test_a_lone_box_and_boxes_where_several_apply(tmp_path):
    form = pixel_forms.read(unnamed_form(tmp_path / "rental.pdf"))
    questions, dropped = pixel_ask.read_map(
        "C Parking wanted | Yes=f12\nM Pets | Yes=f5; No=f6\nC Nothing printed here | Yes=f5", form
    )
    assert dropped == 1
    assert (questions[0].kind, questions[0].options) == ("choice", [["Yes", "f12"]])
    assert questions[1].kind == "multi"


def test_the_same_blank_form_has_one_print_whatever_is_typed_in_it(tmp_path):
    a = pixel_forms.read(unnamed_form(tmp_path / "a.pdf", first="Ana"))
    b = pixel_forms.read(unnamed_form(tmp_path / "b.pdf", first=""))
    other = pixel_forms.read(named_form(tmp_path / "c.pdf"))
    assert pixel_ask.fingerprint(a) == pixel_ask.fingerprint(b) != pixel_ask.fingerprint(other)


def test_the_map_is_kept_and_holds_no_answers(tmp_path):
    form, questions, _ = mapped(tmp_path)
    key = pixel_ask.fingerprint(form)
    assert pixel_ask.load_map(tmp_path, key) is None
    pixel_ask.save_map(tmp_path, key, questions)
    assert pixel_ask.load_map(tmp_path, key) == questions
    kept = (tmp_path / "forms" / f"{key}.json").read_text()
    assert "Already Here" not in kept and "Landlord name" in kept
    (tmp_path / "forms" / f"{key}.json").write_text(json.dumps({"version": 1, "questions": []}))
    assert pixel_ask.load_map(tmp_path, key) is None  # an older reader's map is made again
    (tmp_path / "forms" / f"{key}.json").write_text("{not json")
    assert pixel_ask.load_map(tmp_path, key) is None


def test_a_map_without_the_model_asks_by_the_words_beside_each_box(tmp_path):
    plain = pixel_ask.plain_map(pixel_forms.read(unnamed_form(tmp_path / "a.pdf")))
    words = {q.text: q for q in plain}
    assert words["Last name"].field == "f2" and words["Last name"].detail == "last_name"
    assert words["Monthly rent"].field == "f9" and words["Email"].detail == "email"
    named = pixel_ask.plain_map(pixel_forms.read(named_form(tmp_path / "b.pdf")))
    by = {q.text: q for q in named}
    assert by["First Name"].detail == "first_name" and by["Has pets"].options[0][0] == "Yes"
    assert by["SIN"].detail == ""


# ---------------------------------------------------------------------------- the card
def test_the_card_shows_what_is_known_and_never_an_id_number(tmp_path):
    form, questions, _ = mapped(tmp_path)
    details = {**DETAILS, "address": "12 Example Street"}
    card = pixel_ask.card(questions, form, details, {"q8": "1800", "q5": "No"}, SECRET)
    by = {c["label"]: c for c in card}
    assert "SIN" not in by and len(card) == len(questions) - 1
    assert (by["First name"]["value"], by["First name"]["from"]) == ("Already Here", "form")
    assert (by["Last name"]["value"], by["Last name"]["from"]) == ("Mock", "saved")
    assert (by["Monthly rent ($)"]["value"], by["Monthly rent ($)"]["from"]) == ("1800", "said")
    assert by["Landlord phone number"]["value"] == ""  # their own phone is not their landlord's
    assert by["Home address"]["value"] == "12 Example Street"
    assert by["Address you are moving to"]["value"] == ""  # a saved detail answers one question
    assert by["Pets"] == {
        "id": "q5", "label": "Pets", "section": "A. Applicant", "kind": "choice", "page": 1,
        "options": ["Yes", "No"], "value": "No", "from": "said",
    }  # fmt: skip
    assert [c["section"] for c in card][:2] == ["A. Applicant", "A. Applicant"]


def test_a_phone_number_goes_to_the_mobile_line():
    f = [
        pixel_forms.Field("f1", "a", "text", 0, (0, 0, 1, 1)),
        pixel_forms.Field("f2", "b", "text", 0, (0, 0, 1, 1)),
    ]
    form = pixel_forms.Form(f, [""])
    qs = [
        pixel_ask.Question("q1", "Home phone", "text", "", "f1", [], "phone"),
        pixel_ask.Question("q2", "Cellphone", "text", "", "f2", [], "phone"),
    ]
    card = pixel_ask.card(qs, form, DETAILS, {}, SECRET)
    assert [c["value"] for c in card] == ["", "604-555-0100"]


def test_answers_go_to_the_field_their_question_was_checked_against(tmp_path):
    form, questions, _ = mapped(tmp_path)
    yes, no = form.by_id()["f5"].state, "/Off"
    placed = pixel_ask.plan(
        questions,
        form,
        {
            "q2": "  Mock  ", "q4": "123 456 789", "q5": "no", "q6": "Sam Example", "q8": 1800,
            "q9": "x" * 5000, "q1": "", "q7": ["a", "list"], "q99": "nowhere", "q3": {"a": 1},
        },
        SECRET,
    )  # fmt: skip
    assert placed == {
        "untitled2": "Mock",
        "untitled1": "",  # cleared on the card: cleared in the form
        "untitled5": no,
        "untitled6": form.by_id()["f6"].state,  # one choice: one box ticked, the other cleared
        "untitled7": "Sam Example",
        "untitled9": "1800",
        "untitled10": "x" * pixel_ask.MAX_VALUE,
    }
    assert pixel_ask.plan(questions, form, {"q5": "Yes"}, SECRET) == {"untitled5": yes, "untitled6": no}
    assert pixel_ask.plan(questions, form, {"q5": ""}, SECRET) == {"untitled5": no, "untitled6": no}
    assert pixel_ask.plan(questions, form, {"q5": "Perhaps"}, SECRET) == {}  # not one of its options
    many, _ = pixel_ask.read_map("M Pets | Yes=f5; No=f6", form)
    assert pixel_ask.plan(many, form, {"q1": ["Yes", "No", "Other"]}, SECRET) == {
        "untitled5": yes, "untitled6": form.by_id()["f6"].state,
    }  # fmt: skip


def test_what_was_typed_is_written_and_nothing_else(tmp_path):
    src = unnamed_form(tmp_path / "rental.pdf")
    form, questions, _ = mapped(tmp_path)
    placed = pixel_ask.plan(questions, form, {"q2": "Mock", "q5": "No", "q8": "1800"}, SECRET)
    placed |= {"untitled4": "123", "untitled99": "nowhere"}
    r = pixel_pdf.fill_form([src], {}, {}, None, placed, form.labels())
    got = values(r.outputs[0])
    assert got["untitled2"] == "Mock" and got["untitled9"] == "1800"
    assert got["untitled6"] == form.by_id()["f6"].state and got["untitled5"] in (None, "/Off")
    assert got["untitled1"] == "Already Here" and not got.get("untitled4")  # SIN, by its label
    assert r.summary == (
        "Filled in 3 answers in rental (filled).pdf. 6 places to write are still empty. "
        "Check it before you send it."
    )
    assert values(src)["untitled2"] in (None, "")  # the original is as it was
    with pytest.raises(PixelError, match="Nothing was changed"):
        pixel_pdf.fill_form([src], {}, {}, None, {"untitled1": "Already Here"}, form.labels())


def test_what_is_remembered_is_an_address_and_blanks_in_saved_details(tmp_path):
    _, questions, _ = mapped(tmp_path)
    answers = {"q1": "Someone Else", "q3": "new@example.com", "q9": "12 Example Street", "q6": "Sam Example"}
    new, kept = pixel_ask.remember(tmp_path, questions, answers, {"first_name": "Ana"})
    assert new == {"email": "new@example.com"}  # the saved first name is not replaced by a form
    assert kept == {"address": "12 Example Street"} == pixel_ask.remembered(tmp_path)
    assert "Sam Example" not in (tmp_path / pixel_ask.MEMORY_FILE).read_text()
    assert pixel_ask.remembered(tmp_path / "nowhere") == {}


# ---------------------------------------------------------------------------- the job
def answering(brain, change=None, cancel=False):
    """Answer Pixel's card the way a person would: keep what is typed in, change some answers
    (by the question's words), press the button. Returns the cards that were shown."""
    shown: list[dict] = []

    async def request(kind, timeout=300, **data):
        shown.append({"type": kind, **data})
        if kind != "ask" or not data.get("fields") or cancel:
            return None
        answers = {f["id"]: f["value"] for f in data["fields"]}
        for words, value in (change or {}).items():
            answers[next(f["id"] for f in data["fields"] if f["label"] == words)] = value
        return {"answers": answers, "remember": True}

    brain.hub.request = request  # type: ignore[method-assign]
    return shown


async def fill(brain, src, options=None):
    await filework.handle(
        brain, "convert_files", {"operation": "fill_form", "files": [str(src)], "options": options or {}}
    )
    await brain.jobs.wait_all()
    return list(brain.jobs.jobs.values())[-1]


async def test_a_job_reads_the_form_once_asks_and_fills_what_was_typed(settings, tmp_path):
    booking.save(settings.data_dir, DETAILS)
    llm = Script(MAP)
    brain, _ = make_brain(settings, llm=llm)
    shown = answering(brain, {"Monthly rent ($)": "1800", "Pets": "No", "Home address": "12 Example Street"})
    src = unnamed_form(tmp_path / "rental.pdf")
    job = await fill(brain, src, {"placed": {"untitled4": "999"}, "labels": {"untitled4": "x"}})
    assert job.status == "done", job.summary
    got = values(job.outputs[0])
    assert got["untitled2"] == "Mock" and got["untitled3"] == "ana@example.com"  # saved details
    assert got["untitled9"] == "1800" and got["untitled10"] == "12 Example Street"
    assert got["untitled1"] == "Already Here" and not got.get("untitled4")
    assert got["untitled6"] == pixel_forms.read(src).by_id()["f6"].state
    assert job.summary.startswith("Filled in 5 answers in rental (filled).pdf.")
    [card] = [s for s in shown if s["type"] == "ask"]
    assert card["agent"] == "Pixel" and card["question"] == "What goes in rental.pdf?"
    # the model saw the form's printed words, and nothing about the person
    [prompt] = llm.prompts
    assert "Monthly rent: $ [f9]" in prompt
    for private in ("Mock", "ana@example.com", "604-555-0100", "Already Here", "1800"):
        assert private not in prompt
    assert "Mock" not in json.dumps(job.params) and "1800" not in json.dumps(job.params)
    assert pixel_ask.remembered(settings.data_dir) == {"address": "12 Example Street"}

    # the same form again: no model call at all, and the address is typed in already
    shown.clear()
    again = await fill(brain, unnamed_form(tmp_path / "second copy.pdf", first=""))
    assert again.status == "done" and len(llm.prompts) == 1
    by = {f["label"]: f for f in shown[-1]["fields"]}
    assert (by["Home address"]["value"], by["Home address"]["from"]) == ("12 Example Street", "saved")
    assert by["First name"]["value"] == "Ana"


async def test_what_they_said_in_chat_is_typed_onto_the_card(settings, tmp_path):
    llm = Script(MAP, "q8 = 1,800\nq5 = No\nq99 = nowhere\nq6 =\nnot a line")
    brain, _ = make_brain(settings, llm=llm)
    shown = answering(brain)
    job = await fill(brain, unnamed_form(tmp_path / "rental.pdf"), {"values": {"rent": "1,800", "pets": "none"}})
    assert job.status == "done", job.summary
    by = {f["label"]: f for f in shown[-1]["fields"]}
    assert by["Monthly rent ($)"]["value"] == "1,800" and by["Pets"]["value"] == "No"
    assert values(job.outputs[0])["untitled9"] == "1,800"
    assert '"rent": "1,800"' in llm.prompts[1] and "Already Here" not in llm.prompts[1]


async def test_closing_the_card_fills_nothing(settings, tmp_path):
    brain, _ = make_brain(settings, llm=Script(MAP))
    answering(brain, cancel=True)
    job = await fill(brain, unnamed_form(tmp_path / "rental.pdf"))
    assert job.status == "stopped" and job.outputs == []
    assert job.summary == "The card was closed, so nothing was filled in."
    assert not list(tmp_path.glob("*filled*"))


async def test_with_no_model_the_form_is_still_asked_about_and_filled(settings, tmp_path):
    class Down:
        async def complete(self, **_):
            raise RuntimeError("the model is away")

    booking.save(settings.data_dir, DETAILS)
    brain, _ = make_brain(settings, llm=Down())
    shown = answering(brain, {"Monthly rent": "1800"})
    job = await fill(brain, unnamed_form(tmp_path / "rental.pdf"))
    assert job.status == "done", job.summary
    got = values(job.outputs[0])
    assert got["untitled9"] == "1800" and got["untitled2"] == "Mock"
    assert "SIN" not in {f["label"] for f in shown[-1]["fields"]}
    assert not (settings.data_dir / "forms").exists()  # a map made without the model is not kept


async def test_a_form_with_real_names_is_asked_about_too(settings, tmp_path):
    booking.save(settings.data_dir, DETAILS)
    brain, _ = make_brain(settings, llm=Script("no lines at all"))
    shown = answering(brain, {"Move-in date": "2026-12-01"})
    job = await fill(brain, named_form(tmp_path / "application.pdf"))
    assert job.status == "done", job.summary
    got = values(job.outputs[0])
    assert got["First Name"] == "Ana" and got["Move-in date"] == "2026-12-01" and not got.get("SIN")
    assert len([s for s in shown if s["type"] == "ask"]) == 1


async def test_a_pdf_with_no_fields_says_so(settings, tmp_path):
    from reportlab.pdfgen import canvas

    plain = tmp_path / "plain.pdf"
    c = canvas.Canvas(str(plain))
    c.drawString(72, 700, "Nothing to fill in here")
    c.save()
    assert pixel_forms.read(plain).fields == []
    with pytest.raises(PixelError, match="no fillable fields"):
        pixel.run("fill_form", [plain], {"details": DETAILS})
    brain, _ = make_brain(settings)
    shown = answering(brain)
    job = await fill(brain, plain)
    assert job.status == "failed" and "no fillable fields" in job.summary and shown == []


def test_the_chat_model_is_told_how_forms_are_filled():
    spec = next(t for t in tools.COMPANION_TOOLS if t.name == "convert_files")
    assert "what the form asks for" in json.dumps(spec.parameters)
    assert "card" in pixel.OPERATIONS["fill_form"]
    for text in (pixel_ask.SYSTEM, pixel_ask.SAID_SYSTEM, pixel.OPERATIONS["fill_form"]):
        assert "—" not in text
