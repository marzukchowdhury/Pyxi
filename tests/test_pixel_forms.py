"""Filling a PDF form whose fields have no names worth reading (agents/pixel_forms.py): the
page laid out with its fields, the model's answer checked, and the job end to end. The forms
here are made up; scripts/ has nothing to do with them and no model is called."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from pypdf import PdfReader

from pyxi import booking
from pyxi.agents import pixel, pixel_forms, pixel_pdf
from pyxi.agents.pixel import PixelError
from pyxi.companion import filework
from test_brain_and_server import make_brain
from test_connectors_and_docs import Script
from test_pixel_documents import form as named_form

DETAILS = {"first_name": "Ana", "last_name": "Mock", "email": "ana@example.com", "phone": "604-555-0100"}


def unnamed_form(path: Path) -> Path:
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
    text(150, 730, value="Already Here")  # untitled1
    c.drawString(320, 730, "Last name")
    text(390, 730)  # untitled2
    c.drawString(72, 700, "Email")
    text(150, 700)  # untitled3
    c.drawString(320, 700, "SIN")
    text(390, 700)  # untitled4
    c.drawString(72, 670, "Pets:")
    box(110, 670)  # untitled5
    c.drawString(126, 670, "Yes")
    box(160, 670)  # untitled6
    c.drawString(176, 670, "No")
    c.drawString(72, 640, "B. LANDLORD")
    c.drawString(72, 610, "Name")
    c.drawString(250, 610, "Phone number")
    text(72, 585)  # untitled7: under "Name"
    text(250, 585)  # untitled8: under "Phone number"
    c.drawString(72, 550, "Monthly rent: $")
    text(160, 550, 80)  # untitled9
    c.save()
    return path


def values(path) -> dict[str, str]:
    return {k: v.get("/V") for k, v in PdfReader(str(path)).get_fields().items()}


def test_the_page_is_laid_out_with_each_field_where_it_sits(tmp_path):
    f = pixel_forms.read(unnamed_form(tmp_path / "rental.pdf"))
    assert len(f.fields) == 9 and f.unnamed()
    text = f.outline()
    assert "First name [f1 filled] Last name [f2]" in text
    assert "Email [f3] SIN [f4]" in text
    assert "Pets: [f5 box] Yes [f6 box] No" in text
    assert "Monthly rent: $ [f9]" in text
    assert "Already Here" not in text  # what is typed in the form is never shown
    by = {x.id: x for x in f.fields}
    assert by["f2"].label == "Last name" and by["f4"].label == "SIN"
    assert by["f5"].label == "Yes" and by["f6"].label == "No"  # a box's words follow it
    assert by["f7"].label == "Name" and by["f8"].label == "Phone number"  # the heading above
    assert by["f9"].label == "Monthly rent: $" and by["f1"].filled and not by["f2"].filled
    assert by["f5"].kind == "box" and by["f5"].state.startswith("/")
    assert text.index("B. LANDLORD") < text.index("[f7]")  # sections stay in reading order


def test_which_forms_need_the_page_read(tmp_path):
    odd = pixel_forms.read(unnamed_form(tmp_path / "a.pdf"))
    named = pixel_forms.read(named_form(tmp_path / "b.pdf"))
    assert pixel_forms.needs_model(odd, {}) and not named.unnamed()
    assert not pixel_forms.needs_model(named, {})
    assert not pixel_forms.needs_model(named, {"Move-in date": "2026-12-01", "has pets": "no"})
    assert pixel_forms.needs_model(named, {"When they move in": "2026-12-01"})
    assert not pixel_forms.needs_model(pixel_forms.Form(), {"x": 1})
    for junk in ("untitled17", "Text3", "Check Box 12", "field_4", "form1.page2.TextField7", "f1_02", "3"):
        assert pixel_forms.junk_name(junk), junk
    for real in ("First Name", "email", "Has pets", "SIN", "DOB"):
        assert not pixel_forms.junk_name(real), real


def test_every_part_of_the_models_answer_is_checked(tmp_path):
    f = pixel_forms.read(unnamed_form(tmp_path / "rental.pdf"))
    answer = json.dumps(
        {
            "fields": {
                "f2": {"detail": "last_name"},
                "[f3]": {"detail": "email"},
                "f4": "123 456 789",  # an ID number: never
                "f5": "yes",  # a box takes true or false, nothing else
                "f6": True,
                "f7": "Sam Example",
                "f8": {"detail": "bank_pin"},  # no such saved detail
                "f9": 1800,
                "f99": "nowhere",
                "f1": ["a", "list"],
            },
            "unplaced": ["Favourite colour: green"],
        }
    )
    placed, unplaced = pixel_forms.place("Here you go:\n" + answer, f, DETAILS, pixel_pdf.SECRET)
    assert placed == {
        "untitled2": "Mock",
        "untitled3": "ana@example.com",
        "untitled6": f.by_id()["f6"].state,
        "untitled7": "Sam Example",
        "untitled9": "1800",
    }
    assert unplaced == ["Favourite colour: green"]
    full, _ = pixel_forms.place('{"fields": {"f7": {"detail": "full_name"}}}', f, DETAILS, pixel_pdf.SECRET)
    assert full == {"untitled7": "Ana Mock"}
    long, _ = pixel_forms.place(json.dumps({"fields": {"f7": "x" * 5000}}), f, DETAILS, pixel_pdf.SECRET)
    assert len(long["untitled7"]) == pixel_forms.MAX_VALUE
    off, _ = pixel_forms.place('{"fields": {"f5": false}}', f, DETAILS, pixel_pdf.SECRET)
    assert off == {"untitled5": "/Off"}
    for nonsense in ("", "no json here", "{broken", '{"fields": ["f1"]}', "[1, 2]"):
        assert pixel_forms.place(nonsense, f, DETAILS, pixel_pdf.SECRET) == ({}, [])


def test_the_model_is_told_the_names_of_saved_details_never_their_values(tmp_path):
    f = pixel_forms.read(unnamed_form(tmp_path / "rental.pdf"))
    text = pixel_forms.prompt(f, {"Monthly rent": "1800"}, {**DETAILS, "preferences": "mornings"})
    assert "email, first_name, last_name, phone" in text and '"Monthly rent": "1800"' in text
    for private in ("Ana", "Mock", "ana@example.com", "604-555-0100", "mornings", "Already Here"):
        assert private not in text
    assert "ID" in pixel_forms.SYSTEM and "—" not in pixel_forms.SYSTEM


def test_placed_values_are_written_and_the_rest_is_left_alone(tmp_path):
    src = unnamed_form(tmp_path / "rental.pdf")
    f = pixel_forms.read(src)
    placed = {"untitled2": "Mock", "untitled6": f.by_id()["f6"].state, "untitled9": "1800", "untitled4": "123"}
    r = pixel_pdf.fill_form([src], DETAILS, {}, None, placed, f.labels())
    got = values(r.outputs[0])
    assert got["untitled2"] == "Mock" and got["untitled9"] == "1800"
    assert got["untitled6"] == f.by_id()["f6"].state and got["untitled5"] in (None, "/Off")
    assert got["untitled1"] == "Already Here" and not got.get("untitled4")  # SIN, by its label
    assert r.summary.startswith("Filled 3 fields in rental (filled).pdf.")
    assert "Still empty: Email, Name, Phone number" in r.summary and "untitled" not in r.summary
    assert values(src)["untitled2"] in (None, "")  # the original is as it was


async def _fill(settings, src, reply, options=None, llm=None):
    booking.save(settings.data_dir, DETAILS)
    llm = llm or Script(json.dumps(reply))
    brain, _ = make_brain(settings, llm=llm)
    await filework.handle(
        brain,
        "convert_files",
        {"operation": "fill_form", "files": [str(src)], "options": options or {}},
    )
    await brain.jobs.wait_all()
    [job] = list(brain.jobs.jobs.values())
    return job, llm


async def test_a_job_fills_an_unnamed_form_from_the_words_on_the_page(settings, tmp_path):
    reply = {
        "fields": {"f2": {"detail": "last_name"}, "f3": {"detail": "email"}, "f6": True, "f9": "1800"},
        "unplaced": ["Parking spot: 12"],
    }
    job, llm = await _fill(
        settings,
        unnamed_form(tmp_path / "rental.pdf"),
        reply,
        {"values": {"Monthly rent": "1800", "Pets": "no", "Parking spot": "12"},
         "placed": {"untitled4": "999"}, "labels": {"untitled4": "Name"}},  # not theirs to pass
    )  # fmt: skip
    assert job.status == "done", job.summary
    got = values(job.outputs[0])
    assert got["untitled2"] == "Mock" and got["untitled3"] == "ana@example.com"
    assert got["untitled9"] == "1800" and got["untitled1"] == "Already Here"
    assert not got.get("untitled4")
    assert "Filled 4 fields" in job.summary and "I found no place for: Parking spot: 12." in job.summary
    [prompt] = llm.prompts
    assert "Monthly rent: $ [f9]" in prompt and "Mock" not in prompt and "ana@example.com" not in prompt
    assert "Mock" not in json.dumps(job.params) and "placed" not in json.dumps(job.params)


async def test_a_form_with_real_names_is_filled_without_the_model(settings, tmp_path):
    job, llm = await _fill(settings, named_form(tmp_path / "application.pdf"), {})
    assert job.status == "done" and llm.prompts == []
    assert values(job.outputs[0])["First Name"] == "Ana"


async def test_an_answer_that_cannot_be_read_ends_plainly(settings, tmp_path):
    job, _ = await _fill(settings, unnamed_form(tmp_path / "rental.pdf"), {}, llm=Script("I am not sure."))
    assert job.status == "failed" and "matched" in job.summary and job.outputs == []

    class Down:
        async def complete(self, **_):
            raise RuntimeError("the model is away")

    job, _ = await _fill(settings, unnamed_form(tmp_path / "again.pdf"), {}, llm=Down())
    assert job.status == "failed" and "the model is away" not in job.summary


def test_a_pdf_with_no_fields_says_so(tmp_path):
    from reportlab.pdfgen import canvas

    plain = tmp_path / "plain.pdf"
    c = canvas.Canvas(str(plain))
    c.drawString(72, 700, "Nothing to fill in here")
    c.save()
    assert pixel_forms.read(plain).fields == []
    with pytest.raises(PixelError, match="no fillable fields"):
        pixel_forms.check(plain)
    with pytest.raises(PixelError, match="no fillable fields"):
        pixel.run("fill_form", [plain], {"details": DETAILS})
    assert re.search(r"what the form asks for", str(next(
        t for t in __import__("pyxi.companion.tools", fromlist=["x"]).COMPANION_TOOLS if t.name == "convert_files"
    ).parameters))
