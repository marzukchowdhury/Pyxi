"""Quill, after an audit of what it does to real documents.

The founder's resume lost its format once, so the first half of this file is the Word fix: a
grammar fix changes the words that changed and nothing else. Bold stays where it was, a link is
not written twice, and a picture, a tab or a page break in the same paragraph is still there.
The rest: a file's name is not an instruction, a scan with no words is not "fixed", a file
Quill can't open is not answered with a made-up draft, and what the model sends back is checked
before anything is saved.
"""

from __future__ import annotations

import io
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from docx import Document
from docx.enum.text import WD_BREAK
from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import nsdecls, qn
from docx.shared import Inches
from PIL import Image

from pyxi import docs
from pyxi.docs import document_mode, fix_document, summarize_document
from test_brain_and_server import make_brain
from test_connectors_and_docs import Script


# ---------------------------------------------------------------------------- helpers
class Model:
    """Quill's model, played back: each reply is a dict (sent as JSON) or raw text. When the
    replies run out it has nothing more to change."""

    def __init__(self, *replies) -> None:
        self.replies = list(replies)
        self.asked: list[str] = []

    async def complete(self, system, messages, max_tokens=0, **_):
        self.asked.append(getattr(messages[-1].parts[0], "text", ""))
        reply = self.replies.pop(0) if self.replies else {"edits": []}
        text = reply if isinstance(reply, str) else json.dumps(reply)
        return SimpleNamespace(text=text, usage=SimpleNamespace(cost_usd=0.0))


def edit(i: int, text: str, **more) -> dict:
    return {"edits": [{"i": i, "text": text}], "changes": [], "notes": [], **more}


def link(paragraph, url: str, text: str) -> None:
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"
    h = OxmlElement("w:hyperlink")
    h.set(qn("r:id"), paragraph.part.relate_to(url, rel, is_external=True))
    r = OxmlElement("w:r")
    t = OxmlElement("w:t")
    t.text = text
    r.append(t)
    h.append(r)
    paragraph._p.append(h)


def picture() -> io.BytesIO:
    buf = io.BytesIO()
    Image.new("RGB", (60, 60), (200, 30, 30)).save(buf, "PNG")
    buf.seek(0)
    return buf


async def fixed(tmp_path: Path, build, text: str, i: int = 0):
    """Build a one-off Word file, let the model change paragraph ``i`` to ``text``, and open
    the copy Quill saved."""
    doc = Document()
    build(doc)
    src = tmp_path / "doc.docx"
    doc.save(str(src))
    result = await fix_document(src, "fix the grammar", Model(edit(i, text)))
    return result, Document(str(result.path))


def runs(paragraph, what: str = "bold") -> list[tuple[str, object]]:
    return [(r.text, getattr(r, what)) for r in paragraph.runs]


# ---------------------------------------------------------------------------- the Word fix
async def test_a_bold_label_stays_bold_and_the_rest_stays_plain(tmp_path):
    """Live shape of a resume line: "Skills:" in bold, the list after it not. One spelling fix
    used to put the whole line into the first run, so all of it came back bold."""

    def build(doc):
        p = doc.add_paragraph()
        p.add_run("Skills: ").bold = True
        p.add_run("python, exel and comunication.")

    result, doc = await fixed(tmp_path, build, "Skills: Python, Excel and communication.")
    assert result.changed == 1 and result.path.name == "doc (fixed by Pyxi).docx"
    assert runs(doc.paragraphs[0]) == [("Skills: ", True), ("Python, Excel and communication.", None)]


async def test_italics_in_the_middle_of_a_sentence_stay_where_they_were(tmp_path):
    def build(doc):
        p = doc.add_paragraph()
        p.add_run("I has read ")
        p.add_run("War and Peace").italic = True
        p.add_run(" twice.")

    _, doc = await fixed(tmp_path, build, "I have read War and Peace twice.")
    assert runs(doc.paragraphs[0], "italic") == [
        ("I have read ", None),
        ("War and Peace", True),
        (" twice.", None),
    ]


async def test_a_link_in_the_middle_is_not_written_twice(tmp_path):
    """The link's words are part of the paragraph's text but not of its runs: the fixed line
    ended "...thank you.a@example.com"."""

    def build(doc):
        p = doc.add_paragraph("Email me at ")
        link(p, "mailto:a@example.com", "a@example.com")
        p.add_run(" for a quote, thankyou.")

    _, doc = await fixed(tmp_path, build, "Email me at a@example.com for a quote, thank you.")
    p = doc.paragraphs[0]
    assert p.text == "Email me at a@example.com for a quote, thank you."
    assert [h.text for h in p.hyperlinks] == ["a@example.com"]  # still a link, still whole
    assert [r.text for r in p.runs] == ["Email me at ", " for a quote, thank you."]


async def test_a_line_that_is_only_a_link_is_fixed_inside_the_link(tmp_path):
    def build(doc):
        link(doc.add_paragraph(), "https://example.com", "Visit our web site")

    _, doc = await fixed(tmp_path, build, "Visit our website")
    p = doc.paragraphs[0]
    assert p.text == "Visit our website"
    assert [h.text for h in p.hyperlinks] == ["Visit our website"] and p.runs == []


async def test_a_change_that_runs_across_a_links_edge_leaves_the_line_and_says_so(tmp_path):
    def build(doc):
        p = doc.add_paragraph("Email me at ")
        link(p, "mailto:a@example.com", "a@example.com")
        p.add_run(" today.")

    result, doc = await fixed(tmp_path, build, "Email me today.")
    assert doc.paragraphs[0].text == "Email me at a@example.com today."
    assert result.changed == 0
    assert any("Left as it was" in n and "link" in n for n in result.notes)


@pytest.mark.parametrize("first", [False, True])
async def test_a_picture_in_the_line_is_still_there(tmp_path, first):
    """The picture's run was thrown away with every run but the first, or emptied when it was
    the first."""

    def build(doc):
        p = doc.add_paragraph()
        if first:
            p.add_run().add_picture(picture(), width=Inches(1))
            p.add_run(" Our logo are red.")
        else:
            p.add_run("Figure 1: ")
            p.add_run().add_picture(picture(), width=Inches(1))
            p.add_run(" the sales has grown.")

    want = "Our logo is red." if first else "Figure 1: the sales have grown."
    _, doc = await fixed(tmp_path, build, want)
    assert len(doc.inline_shapes) == 1
    assert doc.paragraphs[0].text.strip() == want


async def test_a_page_break_in_the_line_is_still_there(tmp_path):
    def build(doc):
        p = doc.add_paragraph("End of chapter one, it were long.")
        p.add_run().add_break(WD_BREAK.PAGE)
        doc.add_paragraph("Chapter two")

    _, doc = await fixed(tmp_path, build, "End of chapter one, it was long.")
    assert doc.paragraphs[0].text == "End of chapter one, it was long."
    assert len(doc.element.body.xpath(".//w:br[@w:type='page']")) == 1


@pytest.mark.parametrize("typed", ["Video Editor\t2020 to 2023", "Video Editor    2020 to 2023"])
async def test_a_tab_between_a_job_and_its_dates_stays_a_tab(tmp_path, typed):
    """Whether the model keeps the tab or types spaces for it."""

    def build(doc):
        doc.add_paragraph("Video Editer\t2020 to 2023")

    _, doc = await fixed(tmp_path, build, typed)
    assert doc.paragraphs[0].text == "Video Editor\t2020 to 2023"
    assert len(doc.element.body.xpath(".//w:tab")) == 1


async def test_an_edit_at_the_very_start_and_at_the_very_end(tmp_path):
    def start(doc):
        p = doc.add_paragraph()
        p.add_run("their").bold = True
        p.add_run(" going to the store.")

    _, doc = await fixed(tmp_path, start, "They're going to the store.")
    assert runs(doc.paragraphs[0]) == [("They're", True), (" going to the store.", None)]

    def end(doc):
        p = doc.add_paragraph()
        p.add_run("We will meet on ")
        p.add_run("monday").italic = True

    _, doc = await fixed(tmp_path, end, "We will meet on Monday.")
    assert runs(doc.paragraphs[0], "italic") == [("We will meet on ", None), ("Monday.", True)]


async def test_an_added_word_goes_with_the_words_it_belongs_to(tmp_path):
    def inside(doc):
        p = doc.add_paragraph()
        p.add_run("Note:").bold = True
        p.add_run(" send report by Friday")

    _, doc = await fixed(tmp_path, inside, "Note: send the report by Friday")
    assert runs(doc.paragraphs[0]) == [("Note:", True), (" send the report by Friday", None)]

    def after_the_label(doc):  # right where bold ends: the new word is not part of the label
        p = doc.add_paragraph()
        p.add_run("Skills: ").bold = True
        p.add_run("python")

    _, doc = await fixed(tmp_path, after_the_label, "Skills: Advanced python")
    assert runs(doc.paragraphs[0]) == [("Skills: ", True), ("Advanced python", None)]


async def test_a_dropped_word_takes_nothing_else_with_it(tmp_path):
    def build(doc):
        p = doc.add_paragraph()
        p.add_run("This is is a ")
        p.add_run("test").bold = True
        p.add_run(" of the the system.")

    _, doc = await fixed(tmp_path, build, "This is a test of the system.")
    assert runs(doc.paragraphs[0]) == [("This is a ", None), ("test", True), (" of the system.", None)]


async def test_an_indent_typed_as_spaces_and_a_heading_are_kept(tmp_path):
    def build(doc):
        doc.add_heading("Experiance", 1)
        doc.add_paragraph("    Indented line with a mistak.")

    doc = Document()
    build(doc)
    src = tmp_path / "doc.docx"
    doc.save(str(src))
    model = Model({"edits": [{"i": 0, "text": "Experience"}, {"i": 1, "text": "Indented line with a mistake."}]})
    result = await fix_document(src, "fix", model)
    out = Document(str(result.path))
    assert out.paragraphs[0].style.name == "Heading 1" and out.paragraphs[0].text == "Experience"
    assert out.paragraphs[1].text == "    Indented line with a mistake."
    assert Document(str(src)).paragraphs[0].text == "Experiance"  # the original is untouched


async def test_quill_reads_what_is_in_boxes_tables_in_tables_headers_and_footers(tmp_path):
    """Word's resume templates are made of content controls, and layouts of tables in tables.
    None of that was shown to the model, so it came back "fixed" with its mistakes in."""
    doc = Document()
    doc.add_paragraph("Body paragraph.")
    box = parse_xml(
        f"<w:sdt {nsdecls('w')}><w:sdtContent><w:p><w:r><w:t>I has ten year of experience."
        "</w:t></w:r></w:p></w:sdtContent></w:sdt>"
    )
    doc.element.body.insert(1, box)
    outer = doc.add_table(rows=1, cols=2)
    outer.cell(0, 0).merge(outer.cell(0, 1)).paragraphs[0].text = "Totel sales"
    outer.cell(0, 0).add_table(rows=1, cols=1).cell(0, 0).text = "They was late."
    doc.sections[0].header.paragraphs[0].text = "Curriculm Vitae"
    doc.sections[0].footer.paragraphs[0].text = "Page fotter"
    src = tmp_path / "template.docx"
    doc.save(str(src))

    texts, _ = docs._read(src)
    wrong = {
        "I has ten year of experience.": "I have ten years of experience.",
        "Totel sales": "Total sales",
        "They was late.": "They were late.",
        "Curriculm Vitae": "Curriculum Vitae",
        "Page fotter": "Page footer",
    }
    assert all(texts.count(t) == 1 for t in wrong)  # each once: a merged cell is not shown twice
    model = Model({"edits": [{"i": texts.index(a), "text": b} for a, b in wrong.items()]})
    result = await fix_document(src, "fix", model)
    out = Document(str(result.path))
    assert result.changed == 5
    body = out.element.xml
    assert "I have ten years of experience." in body and "They were late." in body
    assert "Total sales" in body
    assert out.sections[0].header.paragraphs[0].text == "Curriculum Vitae"
    assert out.sections[0].footer.paragraphs[0].text == "Page footer"
    # A document with no header of its own does not gain an empty one by being read.
    plain = Document()
    plain.add_paragraph("Hello.")
    plain.save(str(tmp_path / "plain.docx"))
    docs._read(tmp_path / "plain.docx")
    assert Document(str(tmp_path / "plain.docx")).sections[0].header.is_linked_to_previous


def test_ledgers_find_and_replace_keeps_the_formatting_too():
    """Ledger uses the same helper to put new text in a paragraph."""
    doc = Document()
    p = doc.add_paragraph()
    p.add_run("Total: ").bold = True
    p.add_run("due in 2024")
    docs._set_text(p, "Total: due in 2025")
    assert runs(p) == [("Total: ", True), ("due in 2025", None)]
    # Where the change can't be placed run by run, the text is still exactly what was asked.
    q = doc.add_paragraph("See ")
    link(q, "https://example.com", "our site")
    q.add_run(" now")
    docs._set_text(q, "Look here now")
    assert q.text == "Look here now"


# ---------------------------------------------------------------------------- what the task asks
@pytest.mark.parametrize(
    ("task", "mode"),
    [
        # A file's name is not an instruction.
        ("Fix the grammar in Meeting Summary.docx", "fix"),
        ("Fix the typos in Company Overview.pdf", "fix"),
        ("Fix the grammar in Spanish Homework.docx", "fix"),
        ("Fix grammar in Tone Guide.docx", "fix"),
        ("Fix the grammar in 'Key Points.docx'", "fix"),
        ('Fix the spelling in "Overview of French Tone.docx"', "fix"),
        (r"Fix the grammar in C:\Users\me\Documents\My French Summary.docx", "fix"),
        # A noun beside a request to proofread is what to proofread, not another job.
        ("Proofread the professional summary on my resume, fix typos only", "fix"),
        ("Check the grammar of my letter, it is written in French", "fix"),
        ("Correct the grammar and the tone is fine", "fix"),
        ("fix grammar; the gist should stay", "fix"),
        ("make sure the summary section has no typos", "fix"),
        ("check the grammar, fix only, don't rewrite", "fix"),
        ("Fix spelling mistakes. Keep it in English.", "fix"),
        # Asked for outright, it is still done, whatever the file is called.
        ("Summarize Meeting Summary.docx", "summarize"),
        (r"Please summarize C:\Users\me\Documents\Tone Guide.docx", "summarize"),
        ("Give me the gist of Report.pdf", "summarize"),
        ("write a short summary of this", "summarize"),
        ("What are the key points of this contract?", "summarize"),
        ("a quick overview please", "summarize"),
        ("tl;dr", "summarize"),
        ("Translate Spanish Homework.docx into English", "translate"),
        ("Fix the grammar and translate it to Spanish", "translate"),
        ("I need this in French", "translate"),
        ("Put it in Spanish", "translate"),
        ("fix the tone", "rewrite"),
        ("Rewrite Meeting Summary.docx so it is shorter", "rewrite"),
        # An instruction in quotes is the instruction, not a file's name to take out.
        ('The user said: "make it more formal"', "rewrite"),
        ("User asked: 'summarize this for me'", "summarize"),
        ("Apply this to resume.docx: “translate into French”", "translate"),
        ('Translate the document into "French"', "translate"),
        ('Fix the grammar in "Meeting Summary"', "fix"),
        # A summary asked for by the noun, where a sentence starts.
        ("write summaries of each section", "summarize"),
        ("summary of the errors in this report", "summarize"),
        ("an overview and fix typos", "summarize"),
        ("fix typos, then the gist in two lines", "summarize"),
        ("check spelling; overview at the top please", "summarize"),
        ("what are the main points, and is the math correct?", "summarize"),
        ("Give Me A Summary Of Report.pdf", "summarize"),
        ("what does this say", "summarize"),
        ("what is this document about", "summarize"),
        ("give me the highlights", "summarize"),
        ("what's in this file", "summarize"),
        ("Fix the typos in the summary section", "fix"),
        ("Fix typos in Translation Of Poem.docx", "fix"),
        # A translation asked for without the word.
        ("French version of my cover letter", "translate"),
        ("convert to Spanish", "translate"),
        ("English to French", "translate"),
        ("can you make this German", "translate"),
        ("proofread, and I also need it in French", "translate"),
        ("correct my French homework", "fix"),
        # A rewrite asked for in other words.
        ("make it shorter", "rewrite"),
        ("cut this down to one page", "rewrite"),
        ("condense this", "rewrite"),
        ("paraphrase this paragraph", "rewrite"),
        ("correct the tone", "rewrite"),
        ("fix the tone, it's too casual", "rewrite"),
        ("don't make it shorter, just fix the typos", "fix"),
        # What already worked.
        ("fix the typos, keep the tone", "fix"),
        ("proofread it without shortening anything", "fix"),
        ("fix the grammar only, no rewording", "fix"),
        ("make it more formal", "rewrite"),
        ("shorten it and fix the grammar", "rewrite"),
        ("Translate into French, don't summarize", "translate"),
        ("Summarize the key dates", "summarize"),
    ],
)
def test_a_files_name_and_a_noun_do_not_choose_the_job(task, mode):
    """Live in the audit: "Fix the grammar in Meeting Summary.docx" wrote a summary and fixed
    nothing; "Spanish Homework.docx" was sent to be translated."""
    assert document_mode(task) == mode


async def test_a_grammar_fix_of_a_file_called_summary_fixes_it(settings, tmp_path):
    doc = Document()
    doc.add_paragraph("Their going to the store tomorow.")
    src = tmp_path / "Meeting Summary.docx"
    doc.save(str(src))
    reply = json.dumps(edit(0, "They're going to the store tomorrow."))
    brain, _ = make_brain(settings, llm=Script(reply, '{"edits": []}'))
    job = await brain.jobs.wait(
        (await brain.jobs.start("quill", "Fix the grammar in Meeting Summary.docx", files=[str(src)])).id
    )
    await brain.jobs.wait_all()
    assert job.status == "done", job.summary
    assert [Path(o).name for o in job.outputs] == ["Meeting Summary (fixed by Pyxi).docx"]


# ---------------------------------------------------------------------------- files it can't read
async def test_a_file_quill_cannot_open_is_said_not_answered_with_a_draft(settings, tmp_path):
    """An old .doc sent for a grammar fix: Quill wrote a draft from the task alone, "Your
    draft is ready", and the file was never read."""
    old = tmp_path / "Resume.doc"
    old.write_bytes(b"\xd0\xcf\x11\xe0 an old Word file")
    llm = Script("Dear Hiring Manager, I am a made-up person.")
    brain, rec = make_brain(settings, llm=llm)
    job = await brain.jobs.wait(
        (await brain.jobs.start("quill", "Fix the grammar in my resume", files=[str(old)])).id
    )
    await brain.jobs.wait_all()
    assert job.status == "failed" and not job.outputs
    assert "not Resume.doc" in job.summary and ".docx" in job.summary and ".pdf" in job.summary
    assert llm.prompts == []  # nothing was made up
    assert rec.of("job_ready")[-1]["ok"] is False
    # A file that has gone is said too.
    gone = await brain.jobs.wait(
        (await brain.jobs.start("quill", "Fix the grammar", files=[str(tmp_path / "Gone.docx")])).id
    )
    assert gone.status == "failed" and "can't find Gone.docx" in gone.summary


# ---------------------------------------------------------------------------- encodings
async def test_a_notepad_file_keeps_its_accents(tmp_path):
    """Notepad's "ANSI" is Windows-1252 and its "Unicode" is UTF-16. Read as UTF-8, a café came
    back as "caf\ufffd" in the fixed copy and a UTF-16 note went to the model as empty bytes."""
    ansi = tmp_path / "note.txt"
    ansi.write_bytes("Le café est fermé, désolé.\r\nSee you tomorow.\r\n".encode("cp1252"))
    result = await fix_document(ansi, "fix", Model(edit(1, "See you tomorrow.")))
    saved = result.path.read_text(encoding="utf-8")
    assert saved.splitlines()[:2] == ["Le café est fermé, désolé.", "See you tomorrow."]
    assert "\ufffd" not in saved

    wide = tmp_path / "wide.txt"
    wide.write_bytes("Hello wrold.\r\nBye.\r\n".encode("utf-16"))
    model = Model(edit(0, "Hello world."))
    result = await fix_document(wide, "fix", model)
    assert "[0] Hello wrold." in model.asked[0] and "\x00" not in model.asked[0]
    assert result.path.read_text(encoding="utf-8").startswith("Hello world.")


# ---------------------------------------------------------------------------- scans
def _scan(path: Path, pages: int = 1) -> Path:
    blank = [Image.new("RGB", (600, 800), "white") for _ in range(pages)]
    blank[0].save(path, "PDF", save_all=True, append_images=blank[1:])
    return path


async def test_a_scan_nothing_can_be_read_from_is_not_fixed(settings, tmp_path, monkeypatch):
    """A scanned PDF has no words until it is read with OCR. Quill read it plainly, found
    nothing, called no model, and the card said "Your document is fixed: 0 fixes"."""
    monkeypatch.setattr("pyxi.agents.pixel_pdf._ocr_image", lambda img: [])
    scan = _scan(tmp_path / "Contract scan.pdf")
    model = Model()
    with pytest.raises(ValueError, match="couldn't find any text in Contract scan"):
        await fix_document(scan, "fix the grammar", model)
    with pytest.raises(ValueError, match="couldn't find any text"):
        await fix_document(scan, "translate into French", model, mode="translate")
    assert model.asked == [] and [p.name for p in tmp_path.iterdir()] == ["Contract scan.pdf"]

    llm = Script()
    brain, rec = make_brain(settings, llm=llm)
    job = await brain.jobs.wait((await brain.jobs.start("quill", "Fix the grammar", files=[str(scan)])).id)
    await brain.jobs.wait_all()
    assert job.status == "failed" and "couldn't find any text" in job.summary
    assert rec.of("job_ready")[-1]["title"] != "Your document is fixed"


async def test_a_scanned_page_is_read_before_it_is_checked(tmp_path, monkeypatch):
    line = SimpleNamespace(text="Their going to the store tomorow, and it are far away.")
    monkeypatch.setattr("pyxi.agents.pixel_pdf._ocr_image", lambda img: [line])
    scan = _scan(tmp_path / "Letter.pdf")
    before = scan.read_bytes()
    model = Model(
        {"edits": [], "changes": [{"from": "Their going", "to": "They're going", "why": "grammar"}]}
    )
    result = await fix_document(scan, "fix the grammar", model)
    assert "[0] Their going to the store tomorow" in model.asked[0]
    assert result.listed and result.changed == 1
    assert "1. 'Their going' → 'They're going' (grammar)" in result.path.read_text(encoding="utf-8")
    assert scan.read_bytes() == before


# ---------------------------------------------------------------------------- the model's answer
def _two_lines(tmp_path: Path) -> Path:
    doc = Document()
    doc.add_paragraph("Bonjour le monde.")
    doc.add_paragraph("Merci beaucoup.")
    src = tmp_path / "note.docx"
    doc.save(str(src))
    return src


async def test_an_answer_cut_off_is_asked_again_in_halves_then_it_is_a_failure(tmp_path):
    """An answer cut off mid-JSON read as "no edits": the job was done, with a copy called
    "(translated by Pyxi)" still in the first language."""
    src = _two_lines(tmp_path)
    cut = '{"edits": [{"i": 0, "text": "Hello world."}, {"i": 1, "text": "Thank y'
    with pytest.raises(RuntimeError, match="cut off, so nothing was saved"):
        await fix_document(src, "translate into English", Model(cut, cut, cut), mode="translate")
    assert [p.name for p in tmp_path.iterdir()] == ["note.docx"]
    # Each half fits where the whole didn't.
    model = Model(cut, edit(0, "Hello world."), edit(1, "Thank you very much."))
    result = await fix_document(src, "translate into English", model, mode="translate")
    assert len(model.asked) == 3 and "[1]" not in model.asked[1] and "[0]" not in model.asked[2]
    assert [p.text for p in Document(str(result.path)).paragraphs] == [
        "Hello world.",
        "Thank you very much.",
    ]
    # A review pass that can't be read leaves the first pass's fix standing.
    doc = Document()
    doc.add_paragraph("Their going.")
    fixme = tmp_path / "fixme.docx"
    doc.save(str(fixme))
    result = await fix_document(fixme, "fix", Model(edit(0, "They're going."), "not json at all"))
    assert Document(str(result.path)).paragraphs[0].text == "They're going."


async def test_an_answer_in_words_is_nothing_to_change_not_a_failure(tmp_path):
    """A clean file, and the model says so in a sentence instead of JSON: "came back cut off,
    so nothing was saved". An answer with no JSON in it at all is no edits."""
    doc = Document()
    doc.add_paragraph("This sentence is fine.")
    doc.add_paragraph("So is this one.")
    src = tmp_path / "clean.docx"
    doc.save(str(src))
    model = Model("Everything here is already correct.", "No changes needed.")
    result = await fix_document(src, "proofread this", model)
    assert result.changed == 0 and result.notes == [] and len(model.asked) == 2
    assert [p.text for p in Document(str(result.path)).paragraphs] == [
        "This sentence is fine.",
        "So is this one.",
    ]


async def test_one_part_that_cannot_be_read_does_not_lose_the_rest(tmp_path):
    """A 40-paragraph file, one chunk of four cut off twice: everything was thrown away after
    six calls. The other chunks are saved, and a note says which part was left as it was."""
    doc = Document()
    for i in range(40):
        doc.add_paragraph(f"Paragraph {i} have a error in it. " + "A filler sentence to fill the chunk. " * 16)
    src = tmp_path / "long.docx"
    doc.save(str(src))
    texts, _ = docs._read(src)
    chunks = docs._chunks(texts)
    assert len(chunks) >= 3
    bad = set(chunks[1])

    class Flaky:
        calls = 0

        async def complete(self, system, messages, max_tokens=0, **_):
            Flaky.calls += 1
            ask = messages[-1].parts[0].text
            ids = [int(x) for x in re.findall(r"^\[(\d+)\]", ask, re.M)]
            if ask.startswith("Review this"):
                reply = '{"edits": []}'
            elif bad & set(ids):
                reply = '{"edits": [{"i": ' + str(ids[0]) + ', "text": "Paragraph has an err'
            else:
                fixes = [{"i": i, "text": texts[i].replace("have a error", "has an error")} for i in ids]
                reply = json.dumps({"edits": fixes})
            return SimpleNamespace(text=reply, usage=SimpleNamespace(cost_usd=0.0))

    result = await fix_document(src, "fix the grammar", Flaky())
    saved = [p.text for p in Document(str(result.path)).paragraphs]
    assert result.changed == 40 - len(bad)
    assert all(("has an error" in saved[i]) == (i not in bad) for i in range(40))
    [note] = [n for n in result.notes if "couldn't be read" in n]
    first, last = min(bad), max(bad)
    assert note.startswith(f"{len(bad)} paragraphs were left as they were")
    assert f"'Paragraph {first} have a error" in note and f"to 'Paragraph {last} have a error" in note

    # Nothing readable at all is still a failure, with nothing saved.
    cut = '{"edits": [{"i": 0, "text": "Paragr'
    short = Document()
    short.add_paragraph("Their going.")
    short.add_paragraph("It are far.")
    short.save(str(tmp_path / "short.docx"))
    with pytest.raises(RuntimeError, match="cut off, so nothing was saved"):
        await fix_document(tmp_path / "short.docx", "fix", Model(cut, cut, cut))
    assert not list(tmp_path.glob("short (*"))


async def test_a_paragraph_the_model_only_retyped_is_not_counted_as_changed(tmp_path):
    """The model sends back lines it didn't change (a tab typed as spaces, or word for word):
    "fixed 3 of 3 paragraphs" when one word in one of them had changed."""
    doc = Document()
    p = doc.add_paragraph()
    p.add_run("Software Developer")
    p.add_run().add_tab()
    p.add_run("Jan 2019 – Mar 2021")
    doc.add_paragraph("Managed a team of 5 people.")
    doc.add_paragraph("He go to school.")
    src = tmp_path / "retyped.docx"
    doc.save(str(src))
    reply = {
        "edits": [
            {"i": 0, "text": "Software Developer    Jan 2019 – Mar 2021"},
            {"i": 1, "text": "Managed a team of 5 people."},
            {"i": 2, "text": "He goes to school."},
        ]
    }
    result = await fix_document(src, "fix the grammar", Model(reply))
    assert result.changed == 1 and result.total == 3
    assert result.changes == ["'go' → 'goes'"]
    assert Document(str(result.path)).paragraphs[0].text == "Software Developer\tJan 2019 – Mar 2021"


def _linked_notice(tmp_path: Path) -> Path:
    doc = Document()
    p = doc.add_paragraph()
    p.add_run("For more information, please visit ")
    link(p, "https://example.com", "our website")
    p.add_run(" or contact us by email.")
    q = doc.add_paragraph()
    q.add_run("Deadline: ").bold = True
    q.add_run("applications close on Friday.")
    q.add_run().add_picture(picture(), width=Inches(1))
    src = tmp_path / "notice.docx"
    doc.save(str(src))
    return src


async def test_a_translated_line_with_a_link_is_never_left_in_the_first_language(tmp_path):
    """Chinese has no spaces to line words up by, so the change "ran across the link": the
    paragraph stayed in English in a file called "(translated by Pyxi)", and the job said
    "translated 1 of 2". In a translation every word changes: the text goes in whole."""
    src = _linked_notice(tmp_path)
    chinese = ["欲了解更多信息，请访问我们的网站或通过电子邮件联系我们。", "截止日期：申请于周五截止。"]
    model = Model({"edits": [{"i": i, "text": t} for i, t in enumerate(chinese)]})
    result = await fix_document(src, "translate into Chinese", model, mode="translate")
    out = Document(str(result.path))
    assert [p.text for p in out.paragraphs] == chinese and result.changed == 2
    [url] = out.paragraphs[0].hyperlinks  # still a link, on some of the words
    assert url.text and url.text in chinese[0] and url.address == "https://example.com"
    assert any("link" in n and "check it" in n for n in result.notes)
    # The bold label is still a bold start, not the whole line; the picture is still there.
    assert runs(out.paragraphs[1])[:2] == [("截止日期：", True), ("申请于周五截止。", None)]
    assert len(out.inline_shapes) == 1

    # A language with spaces: the words are placed one by one, and the link is still a link.
    german = ["Weitere Informationen finden Sie auf unserer Website oder per E-Mail.", "Frist: Bewerbungen enden am Freitag."]
    model = Model({"edits": [{"i": i, "text": t} for i, t in enumerate(german)]})
    result = await fix_document(src, "translate into German", model, mode="translate")
    out = Document(str(result.path))
    assert [p.text for p in out.paragraphs] == german
    assert out.paragraphs[0].hyperlinks[0].text == "unserer Website"
    assert runs(out.paragraphs[1])[0] == ("Frist: ", True)
    # A grammar fix is still careful: a change across the link's edge is not made.
    result = await fix_document(src, "fix", Model(edit(0, "For more information, please contact us by email.")))
    assert Document(str(result.path)).paragraphs[0].text.startswith("For more information, please visit our website")


async def test_a_translation_that_skips_a_paragraph_is_asked_for_it_then_fails(tmp_path):
    src = _two_lines(tmp_path)
    model = Model(edit(0, "Hello world."), edit(1, "Thank you very much."))
    result = await fix_document(src, "translate into English", model, mode="translate")
    assert len(model.asked) == 2 and "[0]" not in model.asked[1]  # asked again, for the one missed
    assert Document(str(result.path)).paragraphs[1].text == "Thank you very much."
    with pytest.raises(RuntimeError, match="1 paragraph missing, so nothing was saved"):
        await fix_document(
            src, "translate into English", Model(edit(0, "Hello world.")), mode="translate"
        )
    assert not list(tmp_path.glob("*translated by Pyxi) (2)*"))


def _resume_pdf(folder: Path, line: str, monkeypatch) -> Path:
    """A scanned resume: the one kind of PDF whose fixes are still a list (a typed PDF is
    fixed in a Word copy, see test_quill_keeps_the_form.py). The text reader reads ``line``."""
    monkeypatch.setattr("pyxi.agents.pixel_pdf._ocr_image", lambda img: [SimpleNamespace(text=line)])
    return _scan(folder / "Resume.pdf")


async def test_a_pdfs_corrections_are_worked_out_when_the_model_does_not_list_them(
    tmp_path, monkeypatch
):
    """The model fixed the paragraph but left "changes" empty, and the corrections file said
    "Nothing to correct."."""
    pdf = _resume_pdf(
        tmp_path, "Video Editor2020 to 2023. Managed a team of five people's.", monkeypatch
    )
    model = Model(edit(0, "Video Editor 2020 to 2023. Managed a team of five."))
    result = await fix_document(pdf, "fix", model)
    said = result.path.read_text(encoding="utf-8")
    assert result.listed and result.changed == 2
    assert "1. 'Editor2020' → 'Editor 2020'" in said
    assert "2. 'five people's.' → 'five.'" in said
    assert "Nothing to correct" not in said


@pytest.mark.parametrize(
    ("was", "asked", "saved", "left"),
    [
        # The same number, written another way: the fix is made.
        ("This is my 2nd job since 2019.", "This is my second job since 2019.", None, ""),
        ("The budget is 1,200 dollar.", "The budget is 1200 dollars.", None, ""),
        ("It was a 12 % cut for 3 years experience.", "It was a 12% cut for three years of experience.", None, ""),
        ("I worked their for 3 years and lead 2 team.", "I worked there for three years and led two teams.", None, ""),
        ("The price are $1,200.00 for each units.", "The price is $1,200 for each unit.", None, ""),
        ("Call me at 204 555 1234 anytime you wants.", "Call me at 204-555-1234 anytime you want.", None, ""),
        ("On 05/03/2024 we was done.", "On 5/3/2024 we were done.", None, ""),
        ("We has twenty-one staffs.", "We have 21 staff.", None, ""),
        # A date in another form loses a digit ("3" becomes "March"): that one edit is left
        # out, and the rest of the line is still fixed.
        (
            "We meet on 3/5/2024 at 10am.",
            "We met on March 5, 2024 at 10 a.m.",
            "We met on 3/5/2024 at 10 a.m.",
            "Left '3/5/2024' as it was",
        ),
        # A number really changed is never made; the grammar beside it is.
        (
            "Mr. Rahman owe $1,200 by March 3, 2026.",
            "Mr. Rahman owes $1,300 by March 5, 2026.",
            "Mr. Rahman owes $1,200 by March 3, 2026.",
            "Left '$1,200' as it was",
        ),
        (
            "Their are 3 team working since 2019.",
            "There are 4 teams working since 2019.",
            "There are 3 teams working since 2019.",
            "Left '3' as it was",
        ),
        # Nor is one brought in that wasn't there.
        (
            "The fee are 50 dollars.",
            "The fee is 50 dollars, due in 2027.",
            "The fee is 50 dollars.",
            "'dollars, due in 2027.' would change a number",
        ),
        ("We was late again.", "We were late in 2024 again.", "We were late again.", "Did not add 'in 2024'"),
    ],
)
async def test_a_number_fix_drops_only_the_edit_that_changes_the_number(
    tmp_path, was, asked, saved, left
):
    """One changed number used to throw away the whole paragraph's fixes: "worked their for 3
    years and lead 2 team" to "there ... three ... led two teams" was left untouched, their
    and lead still wrong, and so was "2nd" to "second" and "$1,200.00" to "$1,200"."""
    doc = Document()
    doc.add_paragraph(was)
    src = tmp_path / "numbers.docx"
    doc.save(str(src))
    result = await fix_document(src, "fix the grammar", Model(edit(0, asked)))
    assert Document(str(result.path)).paragraphs[0].text == (saved or asked)
    assert result.changed == 1
    if left:
        assert any(left in n for n in result.notes), result.notes
    else:
        assert result.notes == []


def test_numbers_are_compared_by_what_they_are_worth():
    same = docs._same_numbers
    assert same("05/03/2024", "5/3/2024") and same("$1,200.00", "1200") and same("10am", "10 a.m.")
    assert same("2nd", "second") and same("3 years", "three years") and same("one of them", "ones")
    assert not same("$1,200", "$1,300") and not same("March 3", "March 5")
    assert not same("3/5/2024", "March 5, 2024") and not same("3 years", "four years")


async def test_a_grammar_fix_that_changes_a_number_is_not_made(tmp_path, monkeypatch):
    """A fix keeps amounts and dates. An edit that changes one is left out, with a note; the
    grammar in the same paragraph, and the other paragraphs, are still fixed."""
    doc = Document()
    doc.add_paragraph("Mr. Rahman owe $1,200 by March 3, 2026.")
    doc.add_paragraph("He have paid $1,200.00 before.")
    doc.add_paragraph("Call 555 0100 tomorow.")
    src = tmp_path / "invoice.docx"
    doc.save(str(src))
    model = Model(
        {
            "edits": [
                {"i": 0, "text": "Mr. Rahman owes $1,300 by March 5, 2026."},
                {"i": 1, "text": "He has paid $1,200.00 before."},
                {"i": 2, "text": "Call 555-0100 tomorrow."},
            ],
            "changes": [
                {"from": "owe $1,200", "to": "owes $1,300", "why": "grammar"},
                {"from": "He have", "to": "He has", "why": "grammar"},
            ],
        }
    )
    result = await fix_document(src, "fix the grammar only", model)
    out = [p.text for p in Document(str(result.path)).paragraphs]
    assert out == [
        "Mr. Rahman owes $1,200 by March 3, 2026.",  # the verb fixed, the amount and date kept
        "He has paid $1,200.00 before.",
        "Call 555-0100 tomorrow.",  # the same digits, written differently: fine
    ]
    assert result.changed == 3
    assert any("Left '$1,200' as it was" in n for n in result.notes)
    assert any("Left '3,' as it was" in n for n in result.notes)
    assert not any("1,300" in c for c in result.changes)
    assert "'owe' → 'owes'" in result.changes and "'have' → 'has'" in result.changes
    # A rewrite may be asked to do more: this guard is the grammar fix's.
    result = await fix_document(src, "make it formal", Model(edit(2, "Call 555 0199.")), mode="rewrite")
    assert Document(str(result.path)).paragraphs[2].text == "Call 555 0199."

    pdf = _resume_pdf(
        tmp_path, "Mr. Rahman owe $1,200 by March 3, 2026, as agreed before.", monkeypatch
    )
    reply = {"edits": [], "changes": [{"from": "$1,200", "to": "$1,300"}, {"from": "owe", "to": "owes"}]}
    listed = await fix_document(pdf, "fix", Model(reply))
    said = listed.path.read_text(encoding="utf-8")
    assert "1. 'owe' → 'owes'" in said and "'$1,200' → '$1,300'" not in said.split("Worth a look")[0]
    assert "would change a number" in said


# ---------------------------------------------------------------------------- summaries
async def test_a_summary_of_a_long_document_says_how_much_it_read(tmp_path):
    """Only the first 60,000 characters go to the model. The summary didn't say so, and the
    amount due on the last page was simply not in it."""
    long = tmp_path / "report.txt"
    long.write_bytes(b"The quick brown fox jumps over the lazy dog.\n" * 5000 + b"Due: $9,999.\n")
    model = Model("It is about a fox.")
    result = await summarize_document(long, "summarize", model)
    assert "Due: $9,999" not in model.asked[0]
    assert "only the first 27%" in result.text and "(60,000 of 225,013 characters)" in result.text
    assert "was not read" in result.text
    assert result.path.read_text(encoding="utf-8") == result.text
    short = tmp_path / "short.txt"
    short.write_text("A short note.", encoding="utf-8")
    assert (await summarize_document(short, "summarize", Model("A note."))).text == "A note."
