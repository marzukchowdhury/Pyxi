"""The teammates that work on documents, as the founder found them on 13 November.

1. Quill, given a PDF to fix, handed back a .txt list of corrections after 102 seconds.
2. "Turn it into Word, then fix the grammar": Pixel did its half and no Quill job ever ran.
3. Ledger could not build a 15-sheet workbook: one reply for all of it was cut off, twice.
4. That workbook, asked for "on GenOffice", went to the mouse and keyboard.
5. Pixel could not aim at a size: "at least 20 KB less" got 8% off and no word about the 20.

Every model here is scripted, and every file is made in the test's own temp folder.
"""

from __future__ import annotations

import asyncio
import io
import json
import re
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from docx import Document
from openpyxl import load_workbook
from PIL import Image
from pypdf import PdfReader
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from pyxi import docs
from pyxi.agents import ledger_build, pixel, pixel_shrink
from pyxi.agents.jobs import Handoff
from pyxi.agents.ledger import run_ledger
from pyxi.agents.pixel import PixelError
from pyxi.companion import filework, tools
from pyxi.docs import fix_document
from test_brain_and_server import make_brain
from test_connectors_and_docs import Script
from test_files_agents import budget


# ============================================================================ sample files
def text_pdf(path: Path, pages: int = 3, title: str = "Research proposal") -> Path:
    """A typed PDF: a heading and a few paragraphs a page, the way Word exports one."""
    c = canvas.Canvas(str(path), pagesize=letter)
    for page in range(pages):
        c.setFont("Helvetica-Bold", 20)
        c.drawString(72, 720, f"{title} {page + 1}" if page else title)
        c.setFont("Helvetica", 11)
        c.drawString(72, 690, "This proposal describe a study of river water in three town.")
        c.drawString(72, 660, f"The budget for year {page + 1} are 4,500 dollars and it were approved.")
        c.showPage()
    c.save()
    return path


# The two lines under the heading, as Pixel's converter reads them (one paragraph), corrected.
FIXED = (
    "This proposal describes a study of river water in three towns. "
    "The budget for year 1 is 4,500 dollars and it was approved."
)


def scan_pdf(path: Path) -> Path:
    Image.new("RGB", (600, 800), "white").save(path, "PDF")
    return path


def photo_pdf(path: Path, side: int = 2000) -> Path:
    """A page with one big noisy photo: what a phone scan or a brochure is made of."""
    img = Image.effect_noise((side, side * 3 // 4), 90).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=95)
    buf.seek(0)
    c = canvas.Canvas(str(path), pagesize=letter)
    c.setFont("Helvetica", 12)
    c.drawString(72, 740, "A page with a photo on it.")
    c.drawImage(ImageReader(buf), 72, 300, 468, 351)
    c.save()
    return path


def edits(*pairs: tuple[int, str]) -> str:
    return json.dumps({"edits": [{"i": i, "text": t} for i, t in pairs]})


async def settle(brain) -> None:
    await brain.jobs.wait_all()
    for _ in range(20):
        await asyncio.sleep(0.01)


# ============================================================================ 1. Quill and a PDF
async def test_quill_returns_a_fixed_word_file_for_a_pdf(settings, tmp_path):
    """The whole job, as the app runs it: the result is a .docx beside the PDF, the card
    calls it fixed, and the list of changes is a note on it, not the thing handed back."""
    pdf = text_pdf(tmp_path / "Proposal.pdf", pages=1)
    before = pdf.read_bytes()
    llm = Script(edits((1, FIXED)), '{"edits": []}')
    brain, rec = make_brain(settings, llm=llm)
    job = await brain.jobs.start("quill", "Fix the grammar", files=[str(pdf)])
    job = await brain.jobs.wait(job.id)
    await settle(brain)
    assert job.status == "done", job.summary
    [out] = job.outputs
    assert Path(out).name == "Proposal (fixed by Pyxi).docx" and Path(out).parent == tmp_path
    fixed = Document(out)
    assert [(p.style.name, p.text) for p in fixed.paragraphs] == [
        ("Heading 1", "Research proposal"),  # still a heading: Pixel's converter kept it
        ("Normal", FIXED),
    ]
    assert pdf.read_bytes() == before and not list(tmp_path.glob("*.txt"))
    assert "Word copy" in job.summary and "untouched" in job.summary
    assert job.data["fixed"] == [out] and job.data["from_pdf"] and job.data["count"] == 1
    assert any("describe" in c and "describes" in c for c in job.data["changes"])
    [ready] = rec.of("job_ready")
    assert ready["title"] == "Your document is fixed" and ready["files"] == [out]
    assert "a Word copy of your PDF" in ready["body"] and "send_back" in ready["actions"]
    # The model was shown the PDF's own paragraphs, numbered as the Word copy has them.
    assert "[1] This proposal describe a study" in llm.prompts[0]
    assert any("Word" in e["data"]["message"] for e in rec.of("job_event") if e["event"] == "status")


async def test_a_scanned_pdf_says_it_is_a_scan_and_offers_the_list(settings, tmp_path, monkeypatch):
    line = SimpleNamespace(text="Their going to the store tomorow, and it are far away.")
    monkeypatch.setattr("pyxi.agents.pixel_pdf._ocr_image", lambda img: [line])
    scan = scan_pdf(tmp_path / "Letter.pdf")
    reply = {"edits": [], "changes": [{"from": "Their going", "to": "They're going", "why": "grammar"}]}
    brain, rec = make_brain(settings, llm=Script(json.dumps(reply), '{"edits": []}'))
    job = await brain.jobs.wait((await brain.jobs.start("quill", "Fix the grammar", files=[str(scan)])).id)
    await settle(brain)
    assert job.status == "done", job.summary
    assert "is a scan" in job.summary and "no text in it" in job.summary
    [out] = job.outputs
    assert Path(out).name == "Letter (corrections by Pyxi).txt"
    said = Path(out).read_text(encoding="utf-8")
    assert "1. 'Their going' → 'They're going' (grammar)" in said and "is a scan" in said
    [ready] = rec.of("job_ready")
    assert ready["title"] == "Corrections for your PDF" and "scan" in ready["body"]
    assert not list(tmp_path.glob("*.docx"))  # no Word file made of what a text reader guessed


async def test_a_locked_pdf_is_said_in_a_sentence(tmp_path):
    from pypdf import PdfWriter

    src = text_pdf(tmp_path / "plain.pdf", pages=1)
    w = PdfWriter(clone_from=str(src))
    w.encrypt("secret")
    locked = tmp_path / "locked.pdf"
    with locked.open("wb") as fh:
        w.write(fh)
    with pytest.raises(PixelError, match="password-protected"):
        await fix_document(locked, "fix the grammar", Script())
    assert sorted(p.name for p in tmp_path.iterdir()) == ["locked.pdf", "plain.pdf"]


class SlowEditor:
    """A model that takes a moment over each part, and counts how many it has at once."""

    def __init__(self, delay: float = 0.15) -> None:
        self.delay = delay
        self.now = 0
        self.most = 0
        self.calls = 0

    async def complete(self, *, system, messages, **_):
        self.calls += 1
        self.now += 1
        self.most = max(self.most, self.now)
        await asyncio.sleep(self.delay)
        self.now -= 1
        text = messages[-1].parts[0].text
        if text.startswith("Review"):
            return SimpleNamespace(text='{"edits": []}', usage=SimpleNamespace(cost_usd=0.001))
        found = [
            {"i": int(m.group(1)), "text": m.group(2).replace(" have a error", " has an error")}
            for m in re.finditer(r"(?m)^\[(\d+)\] (.*have a error.*)$", text)
        ]
        return SimpleNamespace(text=json.dumps({"edits": found}), usage=SimpleNamespace(cost_usd=0.001))


async def test_a_long_document_is_edited_in_parts_side_by_side(tmp_path):
    """102 seconds for one document, nearly all of it one model call waiting for the last.
    The parts go out together, a few at a time, and come back in the document's order."""
    doc = Document()
    for i in range(40):
        doc.add_paragraph(f"Paragraph {i} have a error in it. " + "A filler sentence to fill the part. " * 16)
    src = tmp_path / "long.docx"
    doc.save(str(src))
    parts = len(docs._chunks(docs._read(src)[0]))
    assert parts >= 4
    model = SlowEditor()
    began = time.perf_counter()
    result = await fix_document(src, "fix the grammar", model)
    took = time.perf_counter() - began
    assert model.calls == parts * 2  # an edit and a review of each part, as before
    assert 2 <= model.most <= docs.PARALLEL  # side by side, and never more than a few
    assert took < model.calls * model.delay * 0.75  # one after another takes calls x delay
    assert result.changed == 40
    fixed = [p.text for p in Document(str(result.path)).paragraphs]
    assert all(t.startswith(f"Paragraph {i} has an error") for i, t in enumerate(fixed))
    # The changes are listed in the document's order, whichever part came back first.
    assert result.changes[0] == "'have a' → 'has an'" and len(result.changes) == 12


async def test_one_part_that_fails_stops_the_others(tmp_path):
    doc = Document()
    for i in range(40):
        doc.add_paragraph(f"Paragraph {i}. " + "A filler sentence to fill the part. " * 16)
    src = tmp_path / "long.docx"
    doc.save(str(src))

    class Broken(SlowEditor):
        async def complete(self, **kw):
            if self.calls == 1:
                self.calls += 1
                raise RuntimeError("401 invalid api key")
            return await super().complete(**kw)

    model = Broken(delay=0.05)
    with pytest.raises(RuntimeError, match="401"):
        await fix_document(src, "fix", model)
    seen = model.calls
    await asyncio.sleep(0.2)
    assert model.calls == seen and not list(tmp_path.glob("*fixed by Pyxi*"))  # nobody kept asking


# ============================================================================ 2. one job, then another
NAMES = ["Scout", "Quill", "Sketch", "Pixel", "Ledger", "Atlas"]


@pytest.mark.parametrize(
    ("line", "starting", "who"),
    [
        ("Pixel's turning your statement into Word first, then Quill gets the grammar.", "Pixel", "Quill"),
        ("Pixel converts it, and then Quill fixes the grammar.", "Pixel", "Quill"),
        ("Converting it now. After that Quill will tidy the wording.", "Pixel", "Quill"),
        ("Pixel makes the Word file and hands it to Quill.", "Pixel", "Quill"),
        ("Scout's looking it up, Quill writes the note afterwards.", "Scout", "Quill"),
        ("Ledger's building it, then Pixel turns it into a PDF.", "Ledger", "Pixel"),
        ("Once that's done, Ledger adds the totals.", "Pixel", "Ledger"),
        # one teammate, or none promised
        ("Pixel's turning it into Word.", "Pixel", ""),
        ("Quill's fixing the grammar and will save it beside the original.", "Quill", ""),
        ("On it. Then you can open it in Word.", "Pixel", ""),
        ("Quill's on the grammar.", "Pixel", ""),
    ],
)
def test_a_line_that_promises_a_second_teammate(line, starting, who):
    assert filework.promised_next(line, starting, NAMES) == who


def test_the_tools_offer_the_second_step():
    by_name = {t.name: t for t in tools.COMPANION_TOOLS}
    for name in ("delegate", "convert_files", "edit_document"):
        props = by_name[name].parameters["properties"]
        assert props["then_agent"]["description"] and props["then_task"]["description"], name
        assert "then_agent" in by_name[name].description
    # A PDF's grammar is one job for Quill, and the tools say so where the choice is made.
    assert "ONE delegate to Quill" in by_name["delegate"].description
    assert "single delegate to Quill" in by_name["convert_files"].description


async def test_pixel_then_quill_runs_both_and_hands_over_the_file(settings, tmp_path):
    pdf = text_pdf(tmp_path / "Statement.pdf", pages=1)
    llm = Script(
        edits((1, FIXED)),
        '{"edits": []}',
    )
    brain, rec = make_brain(settings, llm=llm)
    note = await filework.handle(
        brain,
        "convert_files",
        {
            "operation": "pdf_to_word",
            "files": [str(pdf)],
            "then_agent": "Quill",
            "then_task": "Fix the grammar and spelling. Keep the meaning.",
        },
    )
    assert "goes straight to Quill" in note
    await settle(brain)
    first, second = sorted(brain.jobs.jobs.values(), key=lambda j: j.created_at)
    assert (first.agent_name, first.status) == ("Pixel", "done")
    assert (second.agent_name, second.status, second.parent) == ("Quill", "done", first.id)
    # Quill was handed the Word file itself, and fixed it: not a draft about a conversion.
    assert second.files == [str(tmp_path / "Statement.docx")]
    [out] = second.outputs
    assert Path(out).name == "Statement (fixed by Pyxi).docx"
    assert "describes a study" in Document(out).paragraphs[1].text
    # One notice, for the finished chain; the hand-off is shown on the way.
    [ready] = rec.of("job_ready")
    assert ready["agent"] == "Quill" and ready["title"] == "Your document is fixed"
    assert [(h["source"], h["target"]) for h in rec.of("handoff")] == [("Pixel", "Quill")]


async def test_a_promised_second_step_with_no_then_is_sent_back_once(settings, tmp_path):
    pdf = text_pdf(tmp_path / "Statement.pdf", pages=1)
    brain, _ = make_brain(settings)
    brain._round_say = "Pixel's turning your statement into Word first, then Quill gets the grammar."
    call = {"operation": "pdf_to_word", "files": [str(pdf)]}
    note = await filework.handle(brain, "convert_files", call)
    assert note.startswith("convert_files: not started") and 'then_agent "Quill"' in note
    assert not brain.jobs.jobs  # nothing ran behind a promise that would not be kept
    # Once: the same call again goes ahead (the model has been told, and chose).
    again = await filework.handle(brain, "convert_files", call)
    assert again.startswith("Started")
    await settle(brain)
    # A teammate that doesn't exist is said, not dropped without a word.
    brain2, _ = make_brain(settings)
    bad = await filework.handle(brain2, "convert_files", {**call, "then_agent": "Quilt"})
    assert bad.startswith("convert_files: there's no teammate called 'Quilt'") and "Quill" in bad
    bad = await brain2._act("delegate", {"agent": "scout", "task": "x", "then_agent": "Quilt"})
    assert bad.startswith("delegate: there's no teammate called 'Quilt'")
    assert not brain2.jobs.jobs


async def test_a_delegate_that_promises_more_is_sent_back_too(settings):
    brain, _ = make_brain(settings)
    brain._round_say = "Quill's drafting it, then Pixel makes it a PDF."
    note = await brain._act("delegate", {"agent": "quill", "task": "Draft a thank-you note"})
    assert note.startswith("delegate: not started") and 'then_agent "Pixel"' in note
    assert not brain.jobs.jobs
    # Already started in this turn: the promise is being kept, and nothing is sent back.
    brain, _ = make_brain(settings, llm=Script("A short note."))
    brain._round_say = "Scout's looking it up, then Quill writes it."
    brain._calls.append({"tool": "delegate", "arguments": {"agent": "Quill", "task": "write"}})
    then, note = filework.handoff(brain, "delegate", "Scout", {"agent": "scout", "task": "x"})
    assert then is None and note is None


async def test_the_whole_turn_convert_then_fix(settings, tmp_path):
    """As it went live, with the model's first call missing the second step: it is sent
    back, the model passes then_agent, and both jobs run."""
    pdf = text_pdf(tmp_path / "Statement.pdf", pages=1)
    call = {"operation": "pdf_to_word", "files": [str(pdf)]}
    llm = Script(
        [
            ("say", {"text": "Pixel's turning your statement into Word first, then Quill gets the grammar."}),
            ("convert_files", call),
        ],
        [("convert_files", {**call, "then_agent": "quill", "then_task": "Fix the grammar."})],
        edits((1, FIXED)),
        '{"edits": []}',
    )
    brain, _ = make_brain(settings, llm=llm)
    await brain.turn("the PDF I have open, turn it into Word, fix the grammar and save it beside the original")
    await settle(brain)
    jobs = sorted(brain.jobs.jobs.values(), key=lambda j: j.created_at)
    assert [(j.agent_name, j.status) for j in jobs] == [("Pixel", "done"), ("Quill", "done")]
    assert Path(jobs[1].outputs[0]).name == "Statement (fixed by Pyxi).docx"
    assert "not started yet" in llm.prompts[1]  # the model was told why its call came back


async def test_a_hand_off_that_cannot_start_is_said(settings, tmp_path):
    """The second teammate never started, and with ``then`` still on the job the brain said
    nothing at all (it was waiting for the next one to speak)."""
    pdf = text_pdf(tmp_path / "Statement.pdf", pages=1)
    brain, rec = make_brain(settings)
    job = await brain.jobs.start(
        "pixel",
        "pdf to word",
        files=[str(pdf)],
        params={"operation": "pdf_to_word"},
        then=Handoff(agent="nobody", task="Fix the grammar."),
    )
    job = await brain.jobs.wait(job.id)
    await settle(brain)
    assert job.status == "done" and job.then is None
    assert "couldn't hand it on to nobody" in job.summary
    [ready] = rec.of("job_ready")
    assert ready["agent"] == "Pixel" and ready["files"] == [str(tmp_path / "Statement.docx")]


async def test_what_scout_found_is_still_context_not_a_file(settings, tmp_path):
    """Only document work passes its files on: a writer after a browser job writes from
    what was found, as it always did."""
    brain, _ = make_brain(settings)
    made = tmp_path / "notes.md"
    made.write_text("found things", encoding="utf-8")
    from pyxi.agents.jobs import Job

    scout = Job(agent_id="scout", agent_name="Scout", role="browser", task="look", outputs=[str(made)])
    scout.then = Handoff(agent="quill", task="write it up")
    assert brain.jobs._handed_on(scout) == ([], {})
    pixel_job = Job(agent_id="pixel", agent_name="Pixel", role="files", task="x", outputs=[str(made)])
    pixel_job.then = Handoff(agent="quill", task="shorten it")
    assert brain.jobs._handed_on(pixel_job) == ([str(made)], {})
    ledger_job = Job(agent_id="ledger", agent_name="Ledger", role="documents", task="x")
    ledger_job.data = {"links": ["https://docs.google.com/spreadsheets/d/abc/edit"]}
    ledger_job.then = Handoff(agent="ledger", task="add a chart")
    assert brain.jobs._handed_on(ledger_job) == (
        [],
        {"google": ["https://docs.google.com/spreadsheets/d/abc/edit"]},
    )


# ============================================================================ 3. a big workbook
SHEETS = [
    "Assumptions", "Revenue", "COGS", "Payroll", "Opex", "Capex", "Depreciation", "Debt",
    "Tax", "Income Statement", "Balance Sheet", "Cash Flow", "KPIs", "Scenarios", "Dashboard",
]  # fmt: skip
BRIEF = (
    "Build NEXORA, a 15-sheet financial model: " + ", ".join(SHEETS) + ". Every sheet has a "
    "header row and its own figures; Revenue reads its growth rate from Assumptions, and the "
    "Dashboard totals revenue."
)


def plan_reply(names: list[str] = SHEETS) -> str:
    """The plan: where everything is on every sheet, so each can be written without seeing
    the others. The first sheet holds the inputs, the second reads it, the last reads the
    second."""
    return json.dumps(
        {
            "sheets": [
                {
                    "name": n,
                    "purpose": f"The {n.lower()} figures",
                    "columns": (
                        ["Input", "Value"] if n == names[0] and len(names) > 2 else ["Item", "Year 1", "Year 2"]
                    ),
                    "each_row": "one line",
                    "rows": 1,
                    "needs": [names[0]] if [n] == names[1:2] else names[1:2] if n == names[-1] else [],
                }
                for n in names
            ]
        }
    )


def sheet_reply(name: str, names: list[str] = SHEETS) -> str:
    """One sheet, in the compact edits a build's reply may use: a block of rows, and a
    formula filled down a column. No "sheet" on them, and nothing for the header's look."""
    second = names[1] if len(names) > 2 else ""
    edits: list[dict[str, Any]] = [
        {"op": "set_cells", "start": "A1", "rows": [["Item", "Year 1", "Year 2"], [f"{name} line", 100]]},
        {
            "op": "fill",
            "range": "C2:C2",
            "formula": f"=B{{r}}*(1+'{names[0]}'!B2)" if name == second else "=B{r}*1.1",
        },
    ]
    if name == names[0] and second:
        edits = [{"op": "set_cells", "start": "A1", "rows": [["Input", "Value"], ["Growth rate", 0.2]]}]
    if name == names[-1] and second:
        edits.append(
            {"op": "set_cells", "cells": {"A4": "Total revenue", "B4": f"=SUM('{second}'!B2:C2)"}}
        )
    return json.dumps({"edits": edits, "more": False})


class Planner:
    """Ledger's model, scripted by what it is asked: the plan, one sheet, or the last step.
    ``cut`` names sheets whose reply comes back cut off (the first time, or every time)."""

    def __init__(self, names: list[str] = SHEETS, cut: dict[str, str] | None = None) -> None:
        self.names = names
        self.cut = cut or {}
        self.asked: list[str] = []  # "plan", a sheet's name, "part:<sheet>", "mend:<sheet>", "whole"
        self.sizes: list[int] = []
        self.prompts: list[str] = []
        self.flying = self.most = 0  # calls in flight now, and the most there ever were

    async def __call__(self, system: str, prompt: str) -> tuple[str, float]:
        self.prompts.append(prompt)
        self.flying += 1
        self.most = max(self.most, self.flying)
        try:
            await asyncio.sleep(0.01)
            reply = self.reply(system, prompt)
        finally:
            self.flying -= 1
        self.sizes.append(len(reply))
        return reply, 0.001

    def sheet(self, name: str) -> str:
        return sheet_reply(name, self.names)

    def mend(self, name: str, prompt: str) -> str:
        return json.dumps({"edits": []})

    def reply(self, system: str, prompt: str) -> str:
        if "plan it first" in system:
            self.asked.append("plan")
            return plan_reply(self.names)
        if m := re.search(r"This is the last step, for the sheet '([^']+)' only", prompt):
            self.asked.append(f"mend:{m.group(1)}")
            return self.mend(m.group(1), prompt)
        if m := re.search(r"The sheet '([^']+)' \(number \d+ of \d+\) is too long", prompt):
            name = m.group(1)
            self.asked.append(f"part:{name}")
            if self.cut.get(name) == "always":
                return '{"edits": [{"op": "set_cells", "sheet": "' + name + '", "cells": {"A1": "It'
            done = sum(1 for a in self.asked if a == f"part:{name}")
            row = done  # one row a part: the header, then two rows of figures
            cells = (
                {"A1": "Item", "B1": "Year 1", "C1": "Year 2"}
                if row == 1
                else {f"A{row}": f"{name} line {row - 1}", f"B{row}": 100 * row, f"C{row}": f"=B{row}*1.1"}
            )
            return json.dumps(
                {"edits": [{"op": "set_cells", "sheet": name, "cells": cells}], "more": done < 3}
            )
        if m := re.search(r"Now write ONLY the sheet '([^']+)'", prompt):
            name = m.group(1)
            self.asked.append(name)
            if name in self.cut:  # cut off at the length limit, mid-cell
                return self.sheet(name)[:60]
            return self.sheet(name)
        self.asked.append("whole")
        return json.dumps({"edits": [{"file": 0, "op": "totals_row"}], "summary": "Totals added."})


@pytest.mark.parametrize(
    ("task", "big"),
    [
        (BRIEF, True),
        ("Make me a 15 sheet financial workbook for a bakery", True),
        ("a workbook with twelve tabs, one per month", True),
        ("Build a tracker with six worksheets", True),
        ("Sheets:\n1. Inputs\n2. Sales\n3. Costs\n4. Staff\n5. Tax\n6. Summary", True),
        ("x" * 1600, True),
        ("Make a pivot of total sales by region", False),
        ("Add a totals row under the Amount column", False),
        ("Copy the 3 tabs' headers onto the Summary sheet", False),
        ("add a sheet called Summary with the totals of the other two sheets", False),
        ("Sort by date, then make a chart of sales by month on a new tab", False),
    ],
)
def test_only_a_big_build_is_planned_first(task, big):
    assert ledger_build.looks_big(task) is big


async def test_fifteen_sheets_are_built_all_at_the_same_time(tmp_path):
    """Live, 13 November: the same workbook took 172.8 s (and 285 s the next time) with the
    sheets written one after another, each shown the ones it reads. Written against the
    plan, none waits for another: about 20 s."""
    model = Planner()
    said: list[str] = []

    async def progress(line: str) -> None:
        said.append(line)

    r = await run_ledger(
        BRIEF,
        files=[],
        new=[{"kind": "xlsx", "title": "NEXORA"}],
        new_dir=tmp_path,
        ask=model,
        progress=progress,
    )
    assert r.outputs == [str(tmp_path / "NEXORA.xlsx")] and r.made == r.outputs
    wb = load_workbook(r.outputs[0])
    assert wb.sheetnames == SHEETS  # all fifteen, in order, and no leftover "Sheet"
    for name in SHEETS[1:-1]:
        # The header's look is done with no model: bold, and frozen above the data.
        assert wb[name]["A1"].value == "Item" and wb[name]["A1"].font.bold, name
        assert wb[name].freeze_panes == "A2" and wb[name].column_dimensions["B"].width > 12
        assert wb[name]["A2"].value == f"{name} line"
    assert wb["Assumptions"]["B2"].value == 0.2
    assert wb["Revenue"]["C2"].value == "=B2*(1+'Assumptions'!B2)"  # a live formula across sheets
    assert wb["Dashboard"]["B4"].value == "=SUM('Revenue'!B2:C2)"
    # One small call for the plan and one a sheet, none of them big. Every formula between
    # sheets reads a cell that is there, so nothing went back to the model after that.
    assert model.asked[0] == "plan" and sorted(model.asked[1:]) == sorted(SHEETS)
    assert max(model.sizes) < 2500
    # No sheet waited for another: all fifteen were with the model at once.
    assert model.most == 15
    # A sheet that reads another is told where that sheet's cells are by the plan, not shown them.
    revenue = next(p for p in model.prompts if "ONLY the sheet 'Revenue'" in p)
    assert "1. Assumptions: The assumptions figures. Row 1 (headers): A=Input | B=Value. Data: rows 2 to 2" in revenue
    assert "It takes its figures from Assumptions with formulas" in revenue
    assert "B2=0.2" not in revenue
    # The part every sheet's prompt shares comes first and is the same in all of them.
    shared = revenue[: revenue.index("Now write ONLY")]
    assert all(p.startswith(shared) for p in model.prompts[1:])
    # Which way the figures run: Assumptions is read by Revenue, and through it by Dashboard.
    first = next(p for p in model.prompts if "ONLY the sheet 'Assumptions'" in p)
    assert "no formula here may read them (that would go round in a circle): Revenue, Dashboard." in first
    assert r.headline.startswith("Built all 15 sheets: Assumptions, Revenue")
    assert r.built == {"done": SHEETS, "not_done": []} and not r.problems
    assert "Built the Cash Flow sheet" in r.changes and round(r.cost_usd, 3) == 0.016
    assert "planning the sheets" in said and "building 15 sheets" in said
    assert any(line.startswith("built ") and "of 15)" in line for line in said)
    assert "checking the formulas between sheets" in said


class Shifted(Planner):
    """A model whose Revenue sheet doesn't keep to the plan: a title in row 1, so its header
    and everything under it is one row lower than the other sheets were told."""

    def sheet(self, name: str) -> str:
        if name != "Revenue":
            return super().sheet(name)
        rows = [["NEXORA revenue"], ["Item", "Year 1", "Year 2"], ["Revenue line", 100, "=B3*(1+'Assumptions'!B2)"]]
        return json.dumps({"edits": [{"op": "set_cells", "start": "A1", "rows": rows}], "more": False})


async def test_a_sheet_that_left_its_plan_has_its_readers_moved_with_no_model(tmp_path):
    """Dashboard was written against the plan: Revenue's figures in B2:C2. Revenue came out
    with a title above its header, so they are in B3:C3. Found and put right here."""
    model = Shifted()
    r = await run_ledger(
        BRIEF, files=[], new=[{"kind": "xlsx", "title": "NEXORA"}], new_dir=tmp_path, ask=model
    )
    wb = load_workbook(r.outputs[0])
    assert wb["Revenue"]["B3"].value == 100 and wb["Revenue"]["A1"].value == "NEXORA revenue"
    assert wb["Dashboard"]["B4"].value == "=SUM('Revenue'!B3:C3)"
    assert not [a for a in model.asked if a.startswith("mend:")] and not r.problems


class Astray(Planner):
    """A model whose Dashboard reads a cell of Revenue that nothing is in, and a name that
    nobody defined. Asked again with the real cells in front of it, it reads the right one."""

    def sheet(self, name: str) -> str:
        if name != "Dashboard":
            return super().sheet(name)
        cells = {"A1": "Item", "B1": "Year 1", "A2": "Revenue", "B2": "='Revenue'!F9", "B3": "=Growth_Placeholder"}
        return json.dumps({"edits": [{"op": "set_cells", "cells": cells}], "more": False})

    def mend(self, name: str, prompt: str) -> str:
        cells = {"B2": "='Revenue'!C2", "B3": "='Assumptions'!B2"}
        return json.dumps({"edits": [{"op": "set_cells", "cells": cells}]})


async def test_a_formula_that_reads_nothing_goes_back_with_the_real_cells(tmp_path):
    model = Astray()
    r = await run_ledger(
        BRIEF, files=[], new=[{"kind": "xlsx", "title": "NEXORA"}], new_dir=tmp_path, ask=model
    )
    wb = load_workbook(r.outputs[0])
    assert wb["Dashboard"]["B2"].value == "='Revenue'!C2" and wb["Dashboard"]["B3"].value == "='Assumptions'!B2"
    # Only the sheet with the trouble went back, once, and was shown what is really there.
    assert [a for a in model.asked if a.startswith("mend:")] == ["mend:Dashboard"]
    mend = model.prompts[-1]
    assert "- B2: ='Revenue'!F9  ('Revenue'!F9 is empty)" in mend
    assert "- B3: =Growth_Placeholder  (nothing is called Growth_Placeholder)" in mend
    assert "C2==B2*(1+'Assumptions'!B2)" in mend  # Revenue as it really is
    assert r.headline.startswith("Built all 15 sheets") and not r.problems

    class Stubborn(Astray):
        def mend(self, name: str, prompt: str) -> str:
            return json.dumps({"edits": []})

    r = await run_ledger(
        BRIEF, files=[], new=[{"kind": "xlsx", "title": "NEXORA 2"}], new_dir=tmp_path, ask=Stubborn()
    )
    assert r.headline.startswith("Built all 15 sheets")  # it is built; what is still wrong is said
    assert r.problems == ["2 formulas on Dashboard still read a cell with nothing in it (B2, B3)."]


async def test_a_call_turned_away_is_made_again_not_written_in_parts(tmp_path, monkeypatch):
    """Fifteen calls at once can be too many for the service. The one it turns away waits a
    moment and goes again; it is not taken for a reply that was cut off."""
    monkeypatch.setattr(ledger_build, "BACKOFF", 0.01)

    class Busy(Exception):
        status_code = 429

    class Crowded(Planner):
        turned = 0

        def sheet(self, name: str) -> str:
            if name == "Opex" and self.turned < 2:
                self.turned += 1
                raise Busy("Too many requests")
            return super().sheet(name)

    model = Crowded()
    r = await run_ledger(
        BRIEF, files=[], new=[{"kind": "xlsx", "title": "NEXORA"}], new_dir=tmp_path, ask=model
    )
    assert model.asked.count("Opex") == 3 and not [a for a in model.asked if a.startswith("part:")]
    assert r.headline.startswith("Built all 15 sheets") and not r.problems
    assert load_workbook(r.outputs[0])["Opex"]["A2"].value == "Opex line"


async def test_only_what_needs_thought_goes_to_the_model_that_thinks(tmp_path):
    """Left to think, a call takes as long as it likes (live: a plan that thought for 30 s
    and hit its length limit; 285 s for the workbook). So when the caller has the same
    model with thinking off (``ask_quick``), the plan and the sheets of typed-in rows go
    there, and only a sheet that reads other sheets is given time to think."""
    usual, quick = Planner(), Planner()
    r = await run_ledger(
        BRIEF, files=[], new=[{"kind": "xlsx", "title": "NEXORA"}], new_dir=tmp_path,
        ask=usual, ask_quick=quick,
    )  # fmt: skip
    assert sorted(usual.asked) == ["Dashboard", "Revenue"]  # the two that read another sheet
    assert quick.asked[0] == "plan" and len(quick.asked) == 14 and usual.most == 2
    assert r.headline.startswith("Built all 15 sheets") and not r.problems
    # A small edit never asks the quick one.
    usual, quick = Planner(), Planner()
    await run_ledger("add a totals row", files=[str(budget(tmp_path / "b.xlsx"))], ask=usual, ask_quick=quick)
    assert usual.asked == ["whole"] and quick.asked == []


async def test_a_sheet_whose_thinking_ate_its_reply_is_asked_again_whole(tmp_path):
    """A reply that thought until its length limit has no cells in it at all. That is not a
    sheet too long for one reply: it is asked for once more, straight away and whole, and
    is not written a few rows at a time."""
    usual = Planner(cut={"Revenue": "once"})
    quick = Planner()
    r = await run_ledger(
        BRIEF, files=[], new=[{"kind": "xlsx", "title": "NEXORA"}], new_dir=tmp_path,
        ask=usual, ask_quick=quick,
    )  # fmt: skip
    assert usual.asked.count("Revenue") == 1 and quick.asked.count("Revenue") == 1
    assert sum("Your last reply could not be read" in p for p in quick.prompts) == 1
    assert not [a for a in usual.asked + quick.asked if a.startswith("part:")]
    assert load_workbook(r.outputs[0])["Revenue"]["C2"].value == "=B2*(1+'Assumptions'!B2)"
    assert r.headline.startswith("Built all 15 sheets") and not r.problems


def test_the_quick_model_is_the_same_model_with_thinking_off():
    from pyxi.agents.jobs import _answers_at_once
    from pyxi.stream_llm import StreamingChat

    quick = _answers_at_once(StreamingChat("anthropic/claude-haiku-5.5"))
    assert quick.model == "anthropic/claude-haiku-5.5" and quick.reasoning == {"enabled": False}
    # A model with a setting of its own keeps it, and a model that isn't this kind has none.
    assert _answers_at_once(StreamingChat("x", reasoning={"effort": "low"})) is None
    assert _answers_at_once(SimpleNamespace(model="x")) is None


async def test_a_model_that_cannot_be_asked_without_thinking_builds_the_usual_way(
    settings, tmp_path, monkeypatch
):
    monkeypatch.setattr("pyxi.agents.pixel_common.pyxi_folder", lambda: tmp_path)
    monkeypatch.setattr(filework, "open_path", lambda path: None)
    model = Planner()
    refused: list[str] = []

    class Refused(Exception):
        status_code = 400

    class NoSuchSetting:
        async def complete(self, *, system, messages, **_):
            refused.append(system[:20])
            raise Refused("reasoning.enabled is not supported by this model")

    class AsLLM:
        async def complete(self, *, system, messages, **_):
            text, cost = await model(system, messages[-1].parts[0].text)
            return SimpleNamespace(text=text, usage=SimpleNamespace(cost_usd=cost))

    monkeypatch.setattr("pyxi.agents.jobs._answers_at_once", lambda llm: NoSuchSetting())
    brain, _ = make_brain(settings, llm=AsLLM())
    job = await brain.jobs.start("ledger", BRIEF, params={"new": [{"kind": "xlsx", "title": "NEXORA"}]})
    job = await brain.jobs.wait(job.id)
    await settle(brain)
    assert job.status == "done" and job.summary.startswith("Built all 15 sheets"), job.summary
    assert len(refused) == 1  # asked that way once, refused, and never again
    assert model.asked[0] == "plan" and sorted(model.asked[1:]) == sorted(SHEETS)


def test_compact_edits_become_plain_set_cells():
    block = {"op": "set_cells", "sheet": "Tax", "start": "B2", "rows": [[1, None, "x"], ["=A1"]]}
    assert ledger_build.expand(block) == {
        "op": "set_cells", "sheet": "Tax", "cells": {"B2": 1, "D2": "x", "B3": "=A1"}
    }  # fmt: skip
    fill = {"op": "fill", "range": "H3:H5", "formula": "=H{r-1}+F{r}-G{r}"}
    assert ledger_build.expand(fill)["cells"] == {
        "H3": "=H2+F3-G3", "H4": "=H3+F4-G4", "H5": "=H4+F5-G5"
    }  # fmt: skip
    # Over several columns the letters move along, as a formula dragged to the right does.
    # Live, F2:G25 filled with ='General Journal'!F{r} put the debits in the credits column
    # too, and the trial balance came to nothing on both sides.
    across = {"op": "fill", "range": "F2:G3", "formula": "='Q1 2026'!F{r}+$A{r}+LOG10(F$1)"}
    assert ledger_build.expand(across)["cells"] == {
        "F2": "='Q1 2026'!F2+$A2+LOG10(F$1)", "G2": "='Q1 2026'!G2+$A2+LOG10(G$1)",
        "F3": "='Q1 2026'!F3+$A3+LOG10(F$1)", "G3": "='Q1 2026'!G3+$A3+LOG10(G$1)",
    }  # fmt: skip
    plain = {"op": "format", "range": "A1:C1", "bold": True}
    assert ledger_build.expand(plain) is plain
    for bad in (
        {"op": "fill", "range": "H2:H9", "formula": "=IF(A{r}=A{p},H{p},0)"},  # live: {p} went in as typed
        {"op": "fill", "range": "nowhere", "formula": "=A{r}"},
        {"op": "fill", "range": "A1:ZZ9999", "formula": "=1"},
        {"op": "set_cells", "start": "A1", "rows": [[None]]},
    ):
        with pytest.raises(ValueError):
            ledger_build.expand(bad)


def test_one_bracket_too_many_is_still_a_reply_and_a_cut_off_one_is_not():
    # Live, with thinking off: two replies in fifteen ended like these, whole but for a bracket.
    whole = {"edits": [{"op": "fill", "range": "B2:B9", "formula": '=IF(A{r}="(x)]","",1)'}], "more": False}
    text = json.dumps(whole)
    assert ledger_build.read_json(text.replace('], "more"', ']), "more"')) == whole
    assert ledger_build.read_json(text.replace('], "more"', ']], "more"')) == whole
    assert ledger_build.read_json(text[:70]) is None  # cut off: nothing is made up to close it


def test_where_a_sheet_really_is_and_the_formulas_that_follow():
    S = ledger_build.Sheet
    names = {"general journal": "General Journal", "trial balance": "Trial Balance"}
    # A journal planned at 12 rows that took 24: its Total is on row 26, not 14.
    journal = S("General Journal", columns=["No", "Date", "Code", "Debit", "Credit"], rows=12, total=True)
    grid: dict[tuple[int, int], Any] = {(1, c): h for c, h in enumerate(journal.columns, 1)}
    for row in range(2, 26):
        grid[(row, 1)], grid[(row, 4 if row % 2 == 0 else 5)] = row, 100
    grid[(26, 3)], grid[(26, 4)] = "Total", "=SUM(D2:D13)"
    moved = ledger_build.moves(journal, grid)
    assert moved.rows == {14: 26} and moved.end == (13, 25) and not moved.shift
    reads = "=SUMIFS('General Journal'!D2:D13,'General Journal'!C2:C13,A2)+'General Journal'!D14"
    assert ledger_build.remap(reads, "Trial Balance", names, {"General Journal": moved}) == (
        "=SUMIFS('General Journal'!D2:D25,'General Journal'!C2:C25,A2)+'General Journal'!D26"
    )
    assert ledger_build.to_the_end("=SUM(D2:D13)", moved.end) == "=SUM(D2:D25)"
    # A sheet as planned moves nothing, and a formula with nothing to move is left alone.
    assert not ledger_build.moves(S("Tax", columns=["Item", "Amount"], rows=1), {(1, 1): "Item", (1, 2): "Amount", (2, 1): "VAT"})
    assert ledger_build.remap("='Trial Balance'!C5*2", "Tax", names, {}) == "='Trial Balance'!C5*2"
    # A sheet called by a name that is nearly its own (live: 'General Journal' for Journal,
    # 144 formulas) is the sheet that was meant, when only one could be.
    short = {"journal": "Journal", "ledger": "Ledger", "tax": "Tax"}
    assert ledger_build.remap("=SUM('General Journal'!F:F)+'general ledger'!B2+Taxes!A1", "Tax", short, {}) == (
        "=SUM('Journal'!F:F)+'Ledger'!B2+Taxes!A1"
    )
    # A named line that sits one row lower than planned is found by its name.
    tb = S("Trial Balance", columns=["Code", "Debit"], lines=["1000", "1100", "1200"])
    lower = {(1, 1): "Code", (1, 2): "Debit", (2, 1): 1000, (3, 1): "Assets", (4, 1): 1100, (5, 1): 1200}
    assert ledger_build.moves(tb, lower).rows == {3: 4, 4: 5}
    # What a formula reads that isn't there; a blank inside a table of records is not that.
    cells = {"General Journal": grid, "Trial Balance": lower}
    bad = ledger_build.broken(
        "='General Journal'!E2+'General Journal'!Z9+Nope!A1+'Trial Balance'!B3+SUM('General Journal'!H2:H9)",
        "Tax", names, cells, frozenset({"General Journal"}),
    )  # fmt: skip
    assert bad == [
        "there is no sheet Nope", "'General Journal'!Z9 is empty", "'Trial Balance'!B3 is empty",
        "'General Journal'!H2:H9 has nothing in it",
    ]  # fmt: skip
    assert ledger_build.broken('=IF(A1="Done!","x",1)', "Tax", names, cells) == []
    assert ledger_build.reads_itself("=SUM(B2:B4)", 4, 2) and not ledger_build.reads_itself("='Tax'!B4+B3", 4, 2)
    # Counting rows reads none of them (live: 24 such formulas went back to the model for 18 s).
    assert not ledger_build.reads_itself("=INDEX('Tax'!$C$2:$C$25,ROWS($B$2:B4))", 4, 2)
    assert ledger_build.made_up_names("=Cash_Flow_Placeholder+SUM(B4:B6)*TRUE") == ["Cash_Flow_Placeholder"]
    assert ledger_build.made_up_names("=IFERROR(VLOOKUP(A2,'Chart of Accounts'!$A$2:$B$23,2,FALSE),\"n/a\")") == []


def test_the_plan_fixes_where_everything_is():
    plan = ledger_build.read_plan(
        {
            "sheets": [
                {"name": "Chart of Accounts", "columns": ["Code", "Name"], "lines": ["1000", "1100"], "needs": ["Trial Balance"]},
                {"name": "General Journal", "columns": ["No", "Code", "Debit", "Credit"], "each_row": "one debit or credit line", "rows": 24, "total": True},
                {"name": "Trial Balance", "columns": ["Code", "Debit", "Credit"], "lines": ["same as Chart of Accounts"], "total": True, "needs": ["Chart of Accounts", "General Journal"]},
            ]
        },
        "xlsx",
    )
    chart, journal, tb = plan
    assert tb.lines == ["1000", "1100"] and (tb.last, tb.total_row) == (3, 4)
    assert (journal.last, journal.total_row) == (25, 26)
    assert journal.line(2) == (
        "2. General Journal: . Row 1 (headers): A=No | B=Code | C=Debit | D=Credit. "
        "Data: rows 2 to 25, each row one debit or credit line. Total: row 26 (A26 says Total)."
    )
    assert "Column A: A2=1000 | A3=1100." in tb.line(3)
    # Two sheets that read each other: the earlier one's need is dropped, so figures run one way.
    assert chart.needs == [] and tb.needs == ["Chart of Accounts", "General Journal"]
    assert chart.read_by == ["Trial Balance"] and tb.read_by == []


async def test_a_cut_off_sheet_is_asked_for_again_in_parts_not_the_whole_job(tmp_path):
    model = Planner(cut={"Payroll": "once"})
    r = await run_ledger(
        BRIEF, files=[], new=[{"kind": "xlsx", "title": "NEXORA"}], new_dir=tmp_path, ask=model
    )
    wb = load_workbook(r.outputs[0])
    assert wb.sheetnames == SHEETS
    # Payroll came in three parts, each put in the file before the next was asked for.
    assert [a for a in model.asked if a.startswith("part:")] == ["part:Payroll"] * 3
    assert [wb["Payroll"].cell(r_, 1).value for r_ in (1, 2, 3)] == ["Item", "Payroll line 1", "Payroll line 2"]
    second = [p for p in model.prompts if "'Payroll' (number 4 of 15) is too long" in p][1]
    assert "What is on it so far" in second and "A1='Item'" in second
    # Nobody else was asked twice: the plan once, every other sheet once.
    assert model.asked.count("plan") == 1
    assert all(model.asked.count(n) == 1 for n in SHEETS)
    assert r.headline.startswith("Built all 15 sheets") and not r.problems


async def test_a_sheet_that_cannot_be_had_is_named_and_the_rest_is_kept(settings, tmp_path, monkeypatch):
    monkeypatch.setattr("pyxi.agents.pixel_common.pyxi_folder", lambda: tmp_path)
    monkeypatch.setattr(filework, "open_path", lambda path: None)
    model = Planner(cut={"Tax": "always"})

    class AsLLM:
        async def complete(self, *, system, messages, **_):
            text, cost = await model(system, messages[-1].parts[0].text)
            return SimpleNamespace(text=text, usage=SimpleNamespace(cost_usd=cost))

    brain, rec = make_brain(settings, llm=AsLLM())
    job = await brain.jobs.start("ledger", BRIEF, params={"new": [{"kind": "xlsx", "title": "NEXORA"}]})
    job = await brain.jobs.wait(job.id)
    await settle(brain)
    assert job.status == "done", job.summary
    assert job.summary.startswith("Built 14 of 15 sheets.")
    assert "Not built: Tax." in job.summary and "came back cut off, even in small parts" in job.summary
    done = job.summary.split("Done: ")[1].split(".")[0].split(", ")
    assert done == [s for s in SHEETS if s != "Tax"]  # exactly which are done, by name
    assert job.data["sheets"] == {"done": done, "not_done": ["Tax"]}
    assert f"It's a new file, NEXORA.xlsx, in {tmp_path}." in job.summary
    wb = load_workbook(job.outputs[0])
    assert wb.sheetnames == SHEETS and wb["Tax"].max_row == 1  # its tab is there, empty
    assert wb["Opex"]["A2"].value == "Opex line"  # what was built is kept
    # Tax was tried again smaller (twice), then left: three calls for it, not a new job.
    assert model.asked.count("Tax") == 1 and model.asked.count("part:Tax") == 2
    assert any("built " in e["data"]["message"].lower() for e in rec.of("job_event") if e["event"] == "status")
    [ready] = rec.of("job_ready")
    assert ready["ok"] and ready["files"] == job.outputs and "open" in ready["actions"]


async def test_nothing_built_is_a_failure_with_no_file(tmp_path):
    class Never(Planner):
        def reply(self, system, prompt):
            if "plan it first" in system:
                return plan_reply(self.names)
            return '{"edits": [{"op": "set_cells", "cells": {"A1": "cut'

    r = await run_ledger(
        BRIEF, files=[], new=[{"kind": "xlsx", "title": "NEXORA"}], new_dir=tmp_path, ask=Never()
    )
    assert r.changes == [] and r.outputs == [] and not list(tmp_path.iterdir())
    assert "came back cut off" in r.summary


class Sheets:
    """A Google Sheet kept in memory: the tabs and cells Ledger's calls leave behind."""

    def __init__(self) -> None:
        self.tabs: dict[str, dict[str, Any]] = {"Sheet1": {"id": 0, "cells": {}}}
        self.calls = 0
        self.formats = 0

    def connected(self) -> bool:
        return True

    async def open_for_edit(self, ref: str):
        from pyxi.agents.ledger import GoogleSheet

        return GoogleSheet(self, "sheet123")

    async def sheets_meta(self, sid):
        return {
            "properties": {"title": "NEXORA"},
            "sheets": [
                {"properties": {"title": t, "sheetId": v["id"], "gridProperties": {"rowCount": 1000, "columnCount": 26}}}
                for t, v in self.tabs.items()
            ],
        }

    def _grid(self, a1: str) -> list[list[Any]]:
        from pyxi.agents.ledger_common import cell_rc, split_tab

        tab, _ = split_tab(a1)
        cells = self.tabs[tab]["cells"]
        if not cells:
            return []
        rows = max(cell_rc(ref)[0] for ref in cells) + 1
        cols = max(cell_rc(ref)[1] for ref in cells) + 1
        grid = [["" for _ in range(cols)] for _ in range(rows)]
        for ref, v in cells.items():
            r, c = cell_rc(ref)
            grid[r][c] = v
        return grid

    async def sheets_values(self, sid, a1, formulas=False):
        self.calls += 1
        return self._grid(a1)

    async def sheets_write(self, sid, data):
        from pyxi.agents.ledger_common import split_tab

        self.calls += 1
        for item in data:
            tab, ref = split_tab(item["range"])
            assert len(item["values"]) == 1 and len(item["values"][0]) == 1
            self.tabs[tab]["cells"][ref] = item["values"][0][0]

    async def sheets_batch(self, sid, requests):
        self.calls += 1
        replies = []
        for req in requests:
            if "addSheet" in req:
                title = req["addSheet"]["properties"]["title"]
                self.tabs[title] = {"id": 100 + len(self.tabs), "cells": {}}
                replies.append({"addSheet": {"properties": {"title": title, "sheetId": self.tabs[title]["id"]}}})
            elif "updateSheetProperties" in req:
                props = req["updateSheetProperties"]["properties"]
                old = next(t for t, v in self.tabs.items() if v["id"] == props["sheetId"])
                self.tabs = {(props["title"] if t == old else t): v for t, v in self.tabs.items()}
                replies.append({})
            else:
                self.formats += 1
                replies.append({})
        return {"replies": replies}


async def test_a_google_sheet_is_built_the_same_way():
    names = ["Inputs", "Sales", "Costs", "Staff", "Summary"]
    g = Sheets()
    model = Planner(names=names, cut={"Costs": "once"})
    brief = "Build a five-tab plan: " + ", ".join(names) + ". Each with a header row and figures."
    r = await run_ledger(brief, files=[], google=g, google_refs=["new sheet: NEXORA"], ask=model)
    assert list(g.tabs) == names  # Sheet1 became the first, the rest were added in order
    assert g.tabs["Sales"]["cells"]["A2"] == "Sales line" and g.tabs["Staff"]["cells"]["C2"] == "=B2*1.1"
    assert g.tabs["Costs"]["cells"]["A3"] == "Costs line 2"  # the cut-off sheet, in parts
    assert g.tabs["Summary"]["cells"]["B4"] == "=SUM('Sales'!B2:C2)"
    assert g.tabs["Sales"]["cells"]["C2"] == "=B2*(1+'Inputs'!B2)" and not r.problems
    assert g.formats >= 4
    assert r.links == ["https://docs.google.com/spreadsheets/d/sheet123/edit"]
    assert r.headline.startswith("Built all 5 sheets: Inputs, Sales, Costs, Staff, Summary.")
    assert model.asked.count("plan") == 1 and model.asked.count("part:Costs") == 3


async def test_a_small_edit_is_still_one_call(tmp_path):
    model = Planner()
    r = await run_ledger("add a totals row", files=[str(budget(tmp_path / "b.xlsx"))], ask=model)
    assert model.asked == ["whole"] and r.changes and not r.headline
    assert Path(r.outputs[0]).name == "b (edited by Pyxi).xlsx"


async def test_a_plan_of_one_or_two_sheets_goes_the_usual_way(tmp_path):
    """A long brief for a small job: the plan says so, and it is one ordinary call after it."""
    model = Planner(names=["Budget"])
    task = "Add a totals row. " + "Keep every figure exactly as it is. " * 60
    r = await run_ledger(task, files=[str(budget(tmp_path / "b.xlsx"))], ask=model)
    assert model.asked == ["plan", "whole"] and r.changes and not r.headline
    assert load_workbook(r.outputs[0]).sheetnames == ["Budget"]  # the plan touched nothing


async def test_a_plan_cut_off_twice_is_built_in_parts_instead(tmp_path):
    """The old failure, for a job whose words didn't look big: "my plan came out unreadable,
    twice. Nothing in the file was touched." Now it is planned and written in parts."""

    class CutWhole(Planner):
        def reply(self, system, prompt):
            if "plan it first" not in system and "Now write ONLY" not in prompt and "last step" not in prompt:
                self.asked.append("whole")
                return '{"edits": [{"file": 0, "op": "set_cells", "cells": {"A1": "Mon'
            return super().reply(system, prompt)

    model = CutWhole(names=["Budget", "Summary"])
    r = await run_ledger("fill in the year", files=[str(budget(tmp_path / "b.xlsx"))], ask=model)
    assert model.asked[:3] == ["whole", "whole", "plan"]
    assert r.headline.startswith("Built all 2 sheets: Budget, Summary.")
    wb = load_workbook(r.outputs[0])
    assert wb.sheetnames == ["Budget", "Summary"] and wb["Summary"]["A2"].value == "Summary line"

    class Hopeless(Planner):
        def reply(self, system, prompt):
            self.asked.append("x")
            return "{"

    r = await run_ledger("fill in the year", files=[str(budget(tmp_path / "b.xlsx"))], ask=Hopeless())
    assert not r.changes and "unreadable, twice" in r.summary and not r.outputs


# ============================================================================ 4. a big build, in an app
def test_edit_document_says_a_big_build_is_ledgers_as_a_file():
    by_name = {t.name: t for t in tools.COMPANION_TOOLS}
    text = by_name["edit_document"].description
    assert "never use_computer" in text and "GenOffice" in text
    assert "new xlsx: <title>" in text and "new sheet: <title>" in text
    assert "opened for them" in text
    assert "new xlsx" in by_name["edit_document"].parameters["properties"]["files"]["description"]


async def test_a_new_workbook_is_built_saved_and_opened(settings, tmp_path, monkeypatch):
    monkeypatch.setattr("pyxi.agents.pixel_common.pyxi_folder", lambda: tmp_path / "Pyxi")
    opened: list[Path] = []
    monkeypatch.setattr(filework, "open_path", opened.append)
    reply = json.dumps(
        {
            "edits": [
                {"file": 0, "op": "set_cells", "cells": {"A1": "Month", "B1": "Rent", "A2": "January", "B2": 1200}},
                {"file": 0, "op": "totals_row"},
            ],
            "summary": "A rent table.",
        }
    )
    llm = Script(reply)
    brain, rec = make_brain(settings, llm=llm)
    note = await filework.handle(
        brain,
        "edit_document",
        {"task": "A rent table for January", "files": ["new xlsx: Rent 2026"]},
    )
    assert note.startswith("Started") and "Rent 2026.xlsx" in note and "opens on their screen" in note
    await settle(brain)
    [job] = brain.jobs.jobs.values()
    made = tmp_path / "Pyxi" / "Rent 2026.xlsx"
    assert job.status == "done" and job.outputs == [str(made)], job.summary
    assert f"It's a new file, Rent 2026.xlsx, in {made.parent}." in job.summary
    ws = load_workbook(made).active
    assert ws["A2"].value == "January" and ws["B3"].value == "=SUM(B2:B2)"
    assert opened == [made]  # put on their screen when it is ready
    assert "Excel workbook Rent 2026.xlsx" in llm.prompts[0]
    [ready] = rec.of("job_ready")
    assert ready["files"] == [str(made)] and "open" in ready["actions"]
    # Asked for again, the first one is not written over.
    brain.llm = Script(reply)
    await filework.handle(brain, "edit_document", {"task": "again", "files": ["new xlsx: Rent 2026"]})
    await settle(brain)
    assert (tmp_path / "Pyxi" / "Rent 2026 (2).xlsx").is_file()
    # A new workbook is made on its own.
    both = await filework.handle(
        brain, "edit_document", {"task": "x", "files": ["new xlsx: A", str(made)]}
    )
    assert both.startswith("edit_document: a new workbook is made on its own")


# ============================================================================ 5. Pixel aims at a size
def test_a_pdf_is_squeezed_until_it_is_under_the_size_asked_for(tmp_path):
    src = photo_pdf(tmp_path / "brochure.pdf")
    before = src.stat().st_size
    plain = pixel.run("compress_pdf", [src])
    usual = plain.details["after"]
    assert usual < before * 0.7 and "target" not in plain.details
    # A size the usual squeeze doesn't reach: it goes on, a step at a time, and stops there.
    want_kb = usual * 0.6 / 1024
    r = pixel.run("compress_pdf", [src], {"target_kb": want_kb})
    [out] = r.outputs
    assert out.stat().st_size == r.details["after"] <= int(want_kb * 1024) < usual
    assert r.details["reached"] and "under the" in r.summary and "you asked for" in r.summary
    assert len(PdfReader(str(out)).pages) == 1
    assert "A page with a photo on it." in PdfReader(str(out)).pages[0].extract_text()  # text is text
    # A size the usual squeeze already reaches is not squeezed any harder than usual.
    easy = pixel.run("compress_pdf", [src], {"target_kb": usual / 1024 + 20})
    assert easy.details["after"] == usual and easy.details["reached"]
    # "At least this much less" is the same aim, said the other way round.
    less = pixel.run("compress_pdf", [src], {"reduce_by_kb": (before - usual * 0.6) / 1024})
    assert less.details["reached"] and less.details["after"] <= usual * 0.6 + 1024
    assert src.stat().st_size == before  # the original is never changed


def test_a_size_it_cannot_reach_is_said_the_first_time(tmp_path):
    """Live: 99 KB, "at least 20 KB less", came back 90.2 KB with no word about the 20; only a
    second ask got "already about as small as it gets"."""
    src = text_pdf(tmp_path / "statement.pdf", pages=12)
    before = src.stat().st_size
    r = pixel.run("compress_pdf", [src], {"target_kb": 1})
    assert r.details["reached"] is False and r.details["target"] == 1024
    assert "without hurting how it looks" in r.summary and "1.0 KB" in r.summary
    assert "text and fonts" in r.summary  # and why: there are no pictures to squeeze
    for out in r.outputs:  # the smallest it reached is still handed over
        assert out.stat().st_size == r.details["after"] < before
        assert len(PdfReader(str(out)).pages) == 12
    photo = photo_pdf(tmp_path / "photo.pdf", side=1200)
    r = pixel.run("compress_pdf", [photo], {"target_kb": 2})
    assert not r.details["reached"] and r.outputs and "didn't reach the 2.0 KB" in r.summary
    assert "as low as they go before they look bad" in r.summary
    # Already under it: said, and nothing made.
    r = pixel.run("compress_pdf", [src], {"target_kb": 5000})
    assert r.outputs == [] and "already" in r.summary and "under the" in r.summary
    with pytest.raises(PixelError, match="more than there is to take off"):
        pixel.run("compress_pdf", [src], {"reduce_by_kb": 5000})
    with pytest.raises(PixelError, match="should be a size"):
        pixel.run("compress_pdf", [src], {"target_kb": "small"})


def test_a_text_pdf_gets_smaller_and_reads_the_same(tmp_path):
    """Written the compact way (objects packed together, as most PDFs are), a PDF that is
    all text shrinks too; before, only pictures could be made smaller."""
    src = text_pdf(tmp_path / "statement.pdf", pages=12)
    r = pixel.run("compress_pdf", [src])
    [out] = r.outputs
    data = out.read_bytes()
    assert b"/ObjStm" in data and len(data) < src.stat().st_size * 0.9
    was, now = PdfReader(str(src)), PdfReader(str(out))
    assert [p.extract_text() for p in now.pages] == [p.extract_text() for p in was.pages]
    import pypdfium2 as pdfium

    a, b = pdfium.PdfDocument(str(src)), pdfium.PdfDocument(str(out))
    try:
        assert len(a) == len(b) == 12
        for n in (0, 11):  # drawn the same, by the reader Edge and Chrome use
            assert a[n].render(scale=0.5).to_pil().tobytes() == b[n].render(scale=0.5).to_pil().tobytes()
    finally:
        a.close()
        b.close()


def test_a_compact_file_that_does_not_read_back_is_not_used(tmp_path, monkeypatch):
    src = text_pdf(tmp_path / "statement.pdf", pages=3)
    with monkeypatch.context() as m:  # it reads back differently: not used
        m.setattr(pixel_shrink, "_same", lambda plain, compact: False)
        r = pixel.run("compress_pdf", [src], {"target_kb": 1})
        [out] = r.outputs
        assert b"/ObjStm" not in out.read_bytes() and len(PdfReader(str(out)).pages) == 3
    with monkeypatch.context() as m:  # it is not a PDF at all: the plain file stands
        m.setattr(pixel_shrink, "_compact", lambda writer: b"%PDF-1.5\nnot a pdf at all")
        r = pixel.run("compress_pdf", [src], {"target_kb": 1})
        [out] = r.outputs
        assert len(PdfReader(str(out)).pages) == 3
    r = pixel.run("compress_pdf", [src], {"target_kb": 1})
    assert b"/ObjStm" in r.outputs[0].read_bytes()


@pytest.mark.parametrize(
    ("given", "size"),
    [(80, 80 * 1024), ("80", 80 * 1024), ("80 KB", 80 * 1024), ("1.5 MB", 1572864), ("79kb", 79 * 1024), ("2000 bytes", 2000), (None, None), ("", None)],
)
def test_sizes_as_people_say_them(given, size):
    assert pixel_shrink.size_bytes(given) == size


def test_a_size_said_in_words():
    assert pixel_shrink.aim_from_words("make it under 80 KB") == {"target": 80 * 1024}
    assert pixel_shrink.aim_from_words("reduce this PDF, I want at least 20 KB less") == {"less": 20 * 1024}
    assert pixel_shrink.aim_from_words("shrink it to 1.5 MB") == {"target": 1572864}
    assert pixel_shrink.aim_from_words("make this PDF smaller") == {}
    assert pixel_shrink.aim_from_words("compress pages 2 to 80") == {}


async def test_the_size_reaches_pixel_from_the_tool_and_from_words(settings, tmp_path):
    src = photo_pdf(tmp_path / "brochure.pdf")
    (tmp_path / "scratch").mkdir()
    usual = pixel.run("compress_pdf", [src], {"out_dir": str(tmp_path / "scratch")}).details["after"]
    want = int(usual * 0.6 / 1024)
    brain, _ = make_brain(settings)
    note = await filework.handle(
        brain, "convert_files", {"operation": "compress_pdf", "files": [str(src)], "options": {"target_kb": want}}
    )
    assert note.startswith("Started")
    await settle(brain)
    [job] = brain.jobs.jobs.values()
    assert job.status == "done" and job.data["reached"] and job.data["after"] <= want * 1024
    assert "under the" in job.summary and "you asked for" in job.summary
    # Handed over in words, with no operation or options: the size is still aimed at.
    job = await brain.jobs.start("pixel", f"Shrink this PDF to under {want} KB", files=[str(src)])
    job = await brain.jobs.wait(job.id)
    assert job.status == "done" and job.data["target"] == want * 1024 and job.data["reached"]
    by_name = {t.name: t for t in tools.COMPANION_TOOLS}
    assert "target_kb" in by_name["convert_files"].parameters["properties"]["options"]["description"]
    assert "target_kb 80" in by_name["convert_files"].description
