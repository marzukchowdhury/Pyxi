"""A grammar check is never a rewrite, and a PDF comes back as a document, not one block of text.

Live, in the founder's trial: a resume (a PDF) sent for a grammar check came back as one block
of text in a Word file, its layout gone. Twice: the second time he had said "keep the exact
form". Two faults. The task named what it was told not to do ("don't rewrite anything"), and
that word alone chose the rewrite. And a PDF's words can't be changed where they are, so a
"fixed" PDF was the page typed out again, every line a plain paragraph.

For a while after that a PDF got only a list of corrections, as a .txt. That was no better:
"why is it a txt file that she gave me?" Now Pixel's own converter turns the PDF into Word
(headings, lists, bold and pictures come through) and the corrections are made in that copy.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pyxi.docs import document_mode, fix_document


@pytest.mark.parametrize(
    ("task", "mode"),
    [
        (
            "Check this resume for grammar, spelling and punctuation mistakes. Fix them only. Keep "
            "the layout, facts, names, dates and contact details exactly as they are, and do not "
            "rewrite or rephrase anything.",
            "fix",
        ),
        ("Redo the grammar check, working from the original PDF. Keep the exact form.", "fix"),
        ("fix the typos, keep the tone", "fix"),
        ("proofread it without shortening anything", "fix"),
        ("fix the grammar only, no rewording", "fix"),
        ("fix the grammar", "fix"),
        # Asked for, it is still done.
        ("make it more formal", "rewrite"),
        ("rewrite this to sound friendlier", "rewrite"),
        ("shorten it and fix the grammar", "rewrite"),
        ("Translate into French, don't summarize", "translate"),
        ("Summarize the key dates", "summarize"),
    ],
)
def test_what_a_task_rules_out_is_not_what_it_asks_for(task, mode):
    assert document_mode(task) == mode


class _Editor:
    """A copy editor that finds two mistakes."""

    at = 0  # the number of the paragraph the mistakes are in

    def __init__(self) -> None:
        self.asked: list[str] = []

    async def complete(self, system, messages, max_tokens=0, **_):
        from types import SimpleNamespace

        self.asked.append(system)
        first = len(self.asked) == 1
        body = {
            "edits": [{"i": self.at, "text": "Video Editor 2020 to 2023. Managed a team of five."}] if first else [],
            "changes": [
                {"from": "Video Editor2020", "to": "Video Editor 2020", "why": "missing space"},
                {"from": "a team of five people's", "to": "a team of five", "why": "grammar"},
            ]
            if first
            else [],
            "notes": ["The phone number has nine digits."] if first else [],
        }
        return SimpleNamespace(text=json.dumps(body), usage=SimpleNamespace(cost_usd=0.001))


def _resume_pdf(folder: Path) -> Path:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    path = folder / "Resume.pdf"
    c = canvas.Canvas(str(path), pagesize=letter)
    c.setFont("Helvetica-Bold", 20)
    c.drawString(72, 720, "A. Person")
    c.setFont("Helvetica", 11)
    c.drawString(72, 690, "Video Editor2020 to 2023. Managed a team of five people's.")
    c.save()
    return path


async def test_a_pdf_sent_for_a_grammar_check_comes_back_as_a_fixed_word_file(tmp_path, monkeypatch):
    """13 November: a research proposal (a PDF) came back as a .txt list of 28 corrections.
    "Why is it a txt file that she gave me?" Pixel turns a PDF into Word in seconds, headings
    and all, so Quill uses that and fixes the Word copy: a file that can be sent."""
    pytest.importorskip("reportlab")
    from docx import Document

    monkeypatch.setattr("pyxi.agents.pixel_common.out_dir_for", lambda path: tmp_path)
    pdf = _resume_pdf(tmp_path)
    before = pdf.read_bytes()
    # Pixel's converter reads the name as a heading and the line under it as a paragraph.
    editor = _Editor()
    editor.at = 1
    result = await fix_document(pdf, "Fix the grammar only. Keep the layout.", editor, mode="fix")
    assert result.converted and not result.listed and result.changed == 1
    assert result.path.name == "Resume (fixed by Pyxi).docx"
    fixed = Document(str(result.path))
    assert [(p.style.name, p.text) for p in fixed.paragraphs] == [
        ("Heading 1", "A. Person"),  # still a heading, not one block of text
        ("Normal", "Video Editor 2020 to 2023. Managed a team of five."),
    ]
    assert "'Video Editor2020' → 'Video Editor 2020' (missing space)" in result.changes
    assert "The phone number has nine digits." in result.notes
    # One new file beside the PDF: no list, no stray Word copy, and the PDF is as it was.
    assert sorted(p.name for p in tmp_path.iterdir()) == ["Resume (fixed by Pyxi).docx", "Resume.pdf"]
    assert pdf.read_bytes() == before
    # Asked again, the first copy is not written over.
    again = _Editor()
    again.at = 1
    second = await fix_document(pdf, "Fix the grammar.", again, mode="fix")
    assert second.path.name == "Resume (fixed by Pyxi) (2).docx"


async def test_a_word_file_is_still_fixed_inside_itself(tmp_path, monkeypatch):
    from docx import Document

    monkeypatch.setattr("pyxi.agents.pixel_common.out_dir_for", lambda path: tmp_path)
    doc = Document()
    doc.add_heading("A. Person", 1)
    doc.add_paragraph("Video Editor2020 to 2023. Managed a team of five people's.")
    source = tmp_path / "Resume.docx"
    doc.save(str(source))

    class Editor(_Editor):
        async def complete(self, system, messages, max_tokens=0, **_):
            from types import SimpleNamespace

            self.asked.append(system)
            edits = [{"i": 1, "text": "Video Editor 2020 to 2023. Managed a team of five."}] if len(self.asked) == 1 else []
            return SimpleNamespace(text=json.dumps({"edits": edits, "changes": [], "notes": []}), usage=SimpleNamespace(cost_usd=0.0))

    result = await fix_document(source, "fix the grammar", Editor(), mode="fix")
    assert not result.listed and result.path.name == "Resume (fixed by Pyxi).docx"
    fixed = Document(str(result.path))
    assert fixed.paragraphs[0].style.name.startswith("Heading")  # the heading is still a heading
    assert fixed.paragraphs[1].text == "Video Editor 2020 to 2023. Managed a team of five."
