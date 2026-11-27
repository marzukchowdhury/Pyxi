"""Ledger making a deck (agents/deck_build.py and deck_design.py): the plan, the slides, the
check, and the way a deck is asked for through edit_document.

The model is a script here: it plans and writes what each test needs, sometimes badly on
purpose. No network. Every name and figure is made up.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from docx import Document
from openpyxl import Workbook
from pptx import Presentation

from pyxi.agents import deck_build as build
from pyxi.agents import deck_design as design
from pyxi.agents.ledger import run_ledger
from pyxi.agents.ledger_common import LedgerError
from pyxi.companion import filework

NOTES = "Say the point in a breath. Then give the one example that proves it."
BODY = {
    "statement": {"statement": "Every lantern is sold before it is built.", "support": "Demand leads supply."},
    "bullets": {"bullets": ["A second kiln came on line", "Orders from the north doubled", "Delivery fell to five days"]},
    "two_columns": {"left": {"heading": "Open a shop", "bullets": ["Closer to buyers", "Needs staff"]}, "right": {"heading": "Sell online", "bullets": ["Reaches everyone", "Needs packing space"]}},
    "comparison": {"left": {"heading": "Shop", "points": ["High fixed cost", "Slow to start"]}, "right": {"heading": "Online", "points": ["Low fixed cost", "Fast to start"]}, "verdict": "Online first."},
    "timeline": {"steps": [{"label": "March", "text": "Survey the wall"}, {"label": "April", "text": "Order fittings"}, {"label": "June", "text": "Paint the room"}]},
    "big_number": {"number": "120", "label": "kettles sold in the north", "context": "The best line."},
    "closing": {"takeaways": ["Approve the store", "Hold the shop"], "next": "A decision today."},
}


class Model:
    """A scripted model: ``layouts`` are the body slides it plans, ``write`` what it puts on a
    layout (given the layout and whether this is the second asking)."""

    def __init__(self, layouts=("statement", "bullets", "comparison"), write=None, theme="forest", title="Harbour lanterns"):
        self.layouts, self.theme, self.title = list(layouts), theme, title
        self.custom = write or (lambda layout, again, prompt: None)
        self.plans, self.writes, self.prompts, self.systems = 0, 0, [], []

    async def __call__(self, system: str, prompt: str):
        self.prompts.append(prompt)
        self.systems.append(system)
        if "planning a slide deck" in system:
            self.plans += 1
            slides = [{"layout": "title", "title": "Opening", "point": "What this is"}]
            slides += [{"layout": lay, "title": f"Point {k} said plainly", "point": f"The one thing slide {k + 1} says."} for k, lay in enumerate(self.layouts, 1)]
            slides.append({"layout": "closing", "title": "What to remember", "point": "Two things."})
            return json.dumps({"title": self.title, "subtitle": "A made-up review", "theme": self.theme, "slides": slides}), 0.001
        self.writes += 1
        want = [int(x) for x in re.search(r"Write slides ([\d, ]+) now", prompt).group(1).split(",")]
        plan = dict(re.findall(r"(?m)^(\d+)\. \[(\w+)\]", prompt))
        again = "not in the source" in prompt.split("Write slides")[-1]
        out = []
        for n in want:
            layout = plan[str(n)]
            spec = self.custom(layout, again, prompt) or BODY.get(layout, BODY["bullets"])
            out.append({"n": n, "notes": NOTES, **spec})
        return json.dumps({"slides": out}), 0.002


async def make(tmp_path, task, model, files=(), title="Lanterns"):
    return await run_ledger(task, files=[str(f) for f in files], ask=model, new=[{"kind": "pptx", "title": title}], new_dir=tmp_path / "out")


def sales_book(path: Path, with_total: bool = True) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "Sales"
    ws.append(["Region", "Product", "Units", "Revenue"])
    for k in range(8):
        ws.append([("North", "South", "East", "West")[k % 4], f"Item {k}", 10 + k, 100.5 * (k + 1)])
    if with_total:
        ws.append(["Total", "", 108, 3618.0])
    wb.save(path)
    return path


def report(path: Path) -> Path:
    doc = Document()
    doc.add_heading("Spring review", 1)
    doc.add_paragraph("The north sold 120 kettles and the south sold 64. Revenue came to 7,286 dollars.")
    t = doc.add_table(rows=3, cols=2)
    for r, row in enumerate((["Region", "Revenue"], ["North", "3,610.00"], ["South", "1,300.00"])):
        for c, text in enumerate(row):
            t.cell(r, c).text = text
    doc.save(str(path))
    return path


def texts_of(path) -> list[list[str]]:
    return [[sh.text_frame.text for sh in s.shapes if sh.has_text_frame and sh.text_frame.text] for s in Presentation(str(path)).slides]


# ---------------------------------------------------------------------------- a deck from a topic
async def test_a_deck_is_planned_then_written_then_saved_as_a_real_file(tmp_path):
    model = Model()
    r = await make(tmp_path, "Make a presentation about harbour lanterns for the board", model)
    [out] = r.outputs
    assert Path(out).name == "Harbour lanterns.pptx" and r.made == [out] and r.kind == "deck"
    assert model.plans == 1 and model.writes == 2  # four slides to write, three to a call
    prs = Presentation(out)
    assert len(prs.slides) == 5 and round(prs.slide_width / prs.slide_height, 3) == round(16 / 9, 3)
    assert [s.shapes.title.text_frame.text for s in prs.slides] == [
        "Harbour lanterns",
        "Every lantern is sold before it is built.",
        "Point 2 said plainly",
        "Point 3 said plainly",
        "What to remember",
    ]
    assert all(s.notes_slide.notes_text_frame.text.strip() for s in prs.slides)  # notes on every slide
    assert "5-slide deck in the Forest theme" in r.headline and "speaker notes on every slide" in r.headline
    assert r.problems == [] and r.built["done"][0] == "Harbour lanterns"
    assert r.cost_usd == pytest.approx(0.005)


async def test_every_layout_is_drawn_in_every_theme_and_passes_the_check(tmp_path):
    slides = [
        {"layout": "title", "title": "The Harbour Lantern Project", "subtitle": "A plan"},
        {"layout": "section", "number": 1, "title": "Where we are", "point": "Three shops"},
        *({"layout": k, "title": f"A slide of {k}", **v} for k, v in BODY.items()),
        {"layout": "chart", "title": "Revenue", "chart_type": "pie", "data": {"categories": ["North", "South"], "series": [("Revenue", [3610.0, 1300.0])]}, "takeaway": "The north leads."},
    ]
    from PIL import Image

    Image.new("RGB", (600, 400), "#336699").save(tmp_path / "p.png")
    slides.append({"layout": "picture", "title": "The workshop", "image": (tmp_path / "p.png").read_bytes(), "caption": "The second kiln."})
    for theme in design.THEMES.values():
        w = design.DeckWriter(theme, "The Harbour Lantern Project")
        for s in slides:
            w.add({**s, "notes": NOTES})
        path = w.save(tmp_path / f"{theme.key}.pptx")
        found = design.audit(path, theme, w.placed, w.charts)
        assert (found.slides, found.charts, found.problems, w.problems) == (len(slides), 1, [], []), theme.key
    assert len(design.THEMES) >= 4 and len(design.LAYOUTS) >= 10


async def test_text_too_long_for_its_box_is_made_smaller_then_cut_and_said(tmp_path):
    long = " ".join(["word"] * 400)
    w = design.DeckWriter(design.THEMES["paper"], "Long")
    w.add({"layout": "bullets", "title": "A title " * 90, "bullets": [long, "short"], "notes": NOTES})
    path = w.save(tmp_path / "long.pptx")
    assert any("too long for its box" in p for p in w.problems)
    assert design.audit(path, w.theme, w.placed, w.charts).problems == []  # what is on the slide fits
    title = next(p for p in w.placed if p.what == "title")
    assert title.size == 22 and title.cut and title.paras[0].endswith("…")


def test_the_check_catches_what_is_wrong_with_a_saved_deck(tmp_path):
    w = design.DeckWriter(design.THEMES["paper"], "Check")
    w.add({"layout": "chart", "title": "Revenue", "data": {"categories": ["North", "South"], "series": [("Revenue", [1.0, 2.0])]}, "notes": NOTES})
    w.add({"layout": "bullets", "title": "Points", "bullets": ["One point here", "Another point here"], "notes": NOTES})
    path = w.save(tmp_path / "check.pptx")
    prs = Presentation(str(path))
    run = next(sh for sh in prs.slides[1].shapes if sh.has_text_frame and "One point" in sh.text_frame.text).text_frame.paragraphs[0].runs[0]
    run.font.name = "Comic Sans MS"
    run.font.color.rgb = design._rgb("FF00FF")
    prs.slides[1].shapes.title.text_frame.text = ""
    prs.slides[1].notes_slide.notes_text_frame.text = ""
    prs.save(str(path))
    wrong = {1: {"categories": ["North", "South"], "series": [("Revenue", [1.0, 3.0])]}}
    found = " ".join(design.audit(path, w.theme, w.placed, wrong).problems)
    for said in ("no title", "empty placeholder", "not the theme's (Comic Sans MS)", "#FF00FF", "no speaker notes", "figures are not the ones from the source"):
        assert said in found, said
    grown = [design.Placed(2, "points", ["word " * 300], "Segoe UI", 20, 3.0, 1.0)]
    assert "does not fit its box" in " ".join(design.audit(path, w.theme, grown, {}).problems)


def test_the_theme_is_the_one_named_then_the_planners_then_the_subjects():
    pick = design.pick_theme
    assert pick("a deck about tea in the dusk theme", "paper").key == "dusk"
    assert pick("a dark theme deck about tea", "paper").key == "slate"
    assert pick("a deck about tea", "forest").key == "forest"
    assert pick("our investor pitch for the startup launch", "nope").key == "signal"
    assert pick("a deck about something", "").key == design.DEFAULT_THEME


# ---------------------------------------------------------------------------- a deck from a file
async def test_a_chart_takes_its_figures_from_the_source_never_from_the_model(tmp_path):
    def write(layout, again, prompt):
        if layout == "chart":
            return {"chart": {"type": "column", "table": "Sales", "labels": "Region", "values": ["Revenue"]}, "takeaway": "The west leads."}

    model = Model(layouts=["chart", "bullets"], write=write)
    r = await make(tmp_path, "make a deck from this spreadsheet", model, [sales_book(tmp_path / "sales.xlsx")])
    assert Path(r.outputs[0]).parent == tmp_path / "out" and "with 1 chart drawn from its figures" in r.headline
    assert "from sales.xlsx" in r.headline and r.problems == []
    chart = next(sh.chart for sh in Presentation(r.outputs[0]).slides[1].shapes if getattr(sh, "has_chart", False) and sh.has_chart)
    plot = chart.plots[0]
    # Summed by region from the eight rows; the sheet's own Total row is not a region.
    assert list(plot.categories) == ["North", "South", "East", "West"]
    assert list(plot.series[0].values) == [603.0, 804.0, 1005.0, 1206.0] and plot.series[0].name == "Revenue"
    # The model was shown the table and its totals worked out here, without the Total row twice.
    assert 'Table "Sales" (9 rows)' in model.prompts[0] and "Revenue: total 3,618" in model.prompts[0]


async def test_a_chart_that_names_no_table_or_a_column_of_words_is_not_drawn(tmp_path):
    def write(layout, again, prompt):
        if layout == "chart":
            return {"chart": {"table": "Sales", "labels": "Region", "values": ["Product"]}, "takeaway": "The north leads."}

    r = await make(tmp_path, "a deck from this", Model(layouts=["chart", "bullets"], write=write), [sales_book(tmp_path / "s.xlsx")])
    prs = Presentation(r.outputs[0])
    assert not any(getattr(sh, "has_chart", False) and sh.has_chart for s in prs.slides for sh in s.shapes)
    assert any("no chart (the column it named holds no figures)" in p for p in r.problems)
    assert prs.slides[1].shapes.title.text_frame.text == "The north leads."  # its one line, as a statement


async def test_a_figure_that_is_not_in_the_source_goes_back_once_then_comes_out(tmp_path):
    def write(layout, again, prompt):
        if layout == "bullets":
            return {"bullets": ["The north sold 120 kettles", "Revenue came to 7,286 dollars" if again else "Revenue grew 73.5% to 9,999,123", "Three shops are open"]}
        if layout == "big_number":
            return {"number": "424242", "label": "lanterns nobody counted", "context": "A figure from nowhere."}

    model = Model(layouts=["bullets", "big_number", "statement"], write=write)
    r = await make(tmp_path, "turn this report into slides", model, [report(tmp_path / "report.docx")])
    assert model.writes == 2 + 2  # two calls for four slides, and one more for each slide with a stray figure
    assert "holds figures that are not in the source: 73.5%, 9,999,123" in model.prompts[-2]
    words = " ".join(t for slide in texts_of(r.outputs[0]) for t in slide)
    assert "7,286" in words and "120 kettles" in words  # the source's own figures stay
    assert "9,999,123" not in words and "73.5" not in words and "424242" not in words
    assert any("left out 1 figure that is not in the source (424242)" in p for p in r.problems)
    assert Path(r.outputs[0]).parent == tmp_path / "out"


def test_a_figure_is_the_sources_as_written_rounded_or_in_thousands():
    have = [1240000.0, 12.37, 0.46, 7286.0]
    k = build.known
    assert k(7286, 0, have) and k(12.4, 1, have) and k(12, 0, have) and k(1.24, 2, have) and k(46, 0, have)
    assert not k(7300, 0, have) and not k(13, 0, have) and not k(1.3, 1, have)
    assert [v for _, v, _ in build.numbers_in("Up 12.5% to $1,240,000 in Q3 2026")] == [12.5, 1240000.0, 2026.0]


async def test_a_deck_from_a_pdf_reads_its_tables_as_tables(tmp_path):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Table, TableStyle

    rows = [["Region", "Units", "Revenue"], ["North", "205", "3,610.00"], ["South", "374", "1,300.00"], ["East", "57", "2,376.00"]]
    t = Table(rows, colWidths=[90, 70, 90])
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.black)]))
    pdf = tmp_path / "Sales.pdf"
    SimpleDocTemplate(str(pdf), pagesize=A4).build([Paragraph("Sales by region for the spring quarter, in made-up dollars.", getSampleStyleSheet()["Normal"]), t])

    def write(layout, again, prompt):
        if layout == "chart":
            return {"chart": {"type": "bar", "table": "Table 1", "labels": "Region", "values": ["Revenue", "Units"]}, "takeaway": "The north leads on revenue."}

    model = Model(layouts=["chart", "statement"], write=write)
    r = await run_ledger("a deck from this PDF", files=[str(pdf)], ask=model, new=[{"kind": "pptx", "title": "Sales"}])
    out = Path(r.outputs[0])
    assert out.parent == tmp_path  # beside the file it was made from
    plot = next(sh.chart for sh in Presentation(str(out)).slides[1].shapes if getattr(sh, "has_chart", False) and sh.has_chart).plots[0]
    assert list(plot.categories) == ["North", "South", "East"]
    assert [list(s.values) for s in plot.series] == [[3610.0, 1300.0, 2376.0], [205.0, 374.0, 57.0]]
    assert "| North | 205 | 3,610.00 |" in model.prompts[0] and "from nothing else" in model.systems[0]


async def test_a_picture_slide_uses_a_picture_of_the_source_and_no_other(tmp_path):
    from docx.shared import Inches
    from PIL import Image

    Image.new("RGB", (640, 420), "#2E7D4F").save(tmp_path / "kiln.png")
    doc = Document()
    doc.add_paragraph("The second kiln came on line in April and doubled what the workshop can fire.")
    doc.add_picture(str(tmp_path / "kiln.png"), width=Inches(4))
    doc.save(str(tmp_path / "report.docx"))

    def write(layout, again, prompt):
        if layout == "picture":
            return {"picture": "P1" if "Point 1" in prompt.split("Write slides")[0] and not seen else "P9", "caption": "The second kiln, on line since April."}

    seen: list[int] = []
    model = Model(layouts=["picture"], write=write)
    r = await make(tmp_path, "turn this report into slides", model, [tmp_path / "report.docx"])
    assert "Pictures in the source: P1 (from the document)" in model.prompts[0] and "- picture:" in model.systems[0]
    slide = Presentation(r.outputs[0]).slides[1]
    assert [sh.shape_type for sh in slide.shapes].count(13) == 1 and r.problems == []  # 13: a picture
    seen.append(1)  # the next deck names a picture the source doesn't have
    r = await make(tmp_path, "turn this report into slides", Model(layouts=["picture"], write=write), [tmp_path / "report.docx"])
    slide = Presentation(r.outputs[0]).slides[1]
    assert [sh.shape_type for sh in slide.shapes].count(13) == 0
    assert any("no picture (it named none of the source's)" in p for p in r.problems)
    assert slide.shapes.title.text_frame.text == "The second kiln, on line since April."


async def test_a_long_source_says_how_much_of_it_the_deck_covers(tmp_path):
    (tmp_path / "long.txt").write_bytes(("The harbour wall was surveyed in March. " * 2500).encode())
    model = Model()
    r = await make(tmp_path, "turn this into slides", model, [tmp_path / "long.txt"])
    assert any("long.txt is long: the slides are made from the first 24% of it" in p for p in r.problems)
    assert len(model.prompts[0]) < build.PLAN_CHARS + 2000 and "the source goes on: 99,999 characters" in model.prompts[0]


async def test_a_spreadsheet_with_no_numbers_gets_no_chart_layout(tmp_path):
    wb = Workbook()
    wb.active.append(["Name", "Team"])
    for row in (["Ana Example", "Sales"], ["Ben Sample", "Warehouse"]):
        wb.active.append(row)
    wb.save(tmp_path / "people.xlsx")
    model = Model(layouts=["chart", "bullets"])
    r = await make(tmp_path, "a deck from this", model, [tmp_path / "people.xlsx"])
    assert "- chart:" not in model.systems[0] and "- bullets:" in model.systems[0]  # not offered
    assert len(Presentation(r.outputs[0]).slides) == 4 and "chart" not in r.headline


@pytest.mark.parametrize("kind", ["empty.txt", "blank.docx", "notes.zip"])
async def test_a_file_with_nothing_in_it_makes_no_deck(tmp_path, kind):
    path = tmp_path / kind
    if kind.endswith(".docx"):
        Document().save(str(path))
    else:
        path.write_bytes(b"")
    model = Model()
    with pytest.raises(LedgerError, match=r"nothing in it to make slides from|not notes\.zip"):
        await make(tmp_path, "turn this into slides", model, [path])
    assert model.plans == 0 and not (tmp_path / "out").exists()  # nothing asked, nothing saved


# ---------------------------------------------------------------------------- the plan put right
async def test_the_number_of_slides_asked_for_is_kept_and_forty_is_the_most(tmp_path):
    model = Model(layouts=["bullets", "statement", "comparison", "timeline"] * 12)
    r = await make(tmp_path, "a 6-slide deck about lanterns", model)
    assert len(Presentation(r.outputs[0]).slides) == 6 and "Plan exactly 6 slides" in model.systems[0]
    big = Model(layouts=["bullets", "statement", "comparison", "timeline"] * 12)
    r = await make(tmp_path, "make a 90 slide deck about lanterns", big)
    assert len(Presentation(r.outputs[0]).slides) == 40 and big.writes == 13
    assert "You asked for 90 slides; the most I make in one deck is 40." in r.headline
    assert build.slides_asked("turn this 30-page report into slides") is None
    assert build.slides_asked("make ten slides") == 10


async def test_the_same_deck_asked_for_twice_gets_its_own_name(tmp_path):
    first = await make(tmp_path, "a deck about lanterns", Model())
    held = Path(first.outputs[0])
    with held.open("ab"):  # open in PowerPoint, say: the name is taken and the file is held
        second = await make(tmp_path, "a deck about lanterns", Model())
    assert Path(second.outputs[0]).name == "Harbour lanterns (2).pptx"
    assert held.stat().st_size and Presentation(str(held)).slides  # the first is untouched


async def test_a_plan_that_breaks_the_rules_is_put_right(tmp_path):
    b = build.Builder("a deck about lanterns", "Lanterns", ask=Model(), folder=tmp_path)
    slides = b.put_right(
        [
            {"layout": "closing", "title": "The end comes first", "point": "x"},
            {"layout": "chart", "title": "A chart with no figures", "point": "y"},
            {"layout": "section", "title": "A divider in a short deck", "point": "z"},
            {"layout": "Two-Column", "title": "Two", "point": "w"},
            "not a slide",
            {"layout": "bullets"},
        ],
        None,
    )
    assert [s.layout for s in slides] == ["title", "bullets", "statement", "two_columns", "closing"]
    assert [s.n for s in slides] == [1, 2, 3, 4, 5] and slides[-1].title == "The end comes first"


async def test_a_slide_nothing_came_back_for_uses_the_plans_own_words(tmp_path):
    class Half(Model):
        async def __call__(self, system, prompt):
            text, cost = await super().__call__(system, prompt)
            if "planning" not in system:
                data = json.loads(text)
                data["slides"] = data["slides"][:1]
                return json.dumps(data), cost
            return text, cost

    r = await make(tmp_path, "a deck about lanterns", Half(layouts=["bullets", "bullets", "bullets"]))
    titles = [s.shapes.title.text_frame.text for s in Presentation(r.outputs[0]).slides]
    assert "The one thing slide 3 says." in titles and any("written from the plan alone" in p for p in r.problems)


async def test_a_plan_that_cannot_be_read_makes_no_file(tmp_path):
    async def junk(system, prompt):
        return "I would be glad to help with your deck!", 0.001

    with pytest.raises(LedgerError, match="couldn't plan the deck"):
        await make(tmp_path, "a deck about lanterns", junk)
    assert not (tmp_path / "out").exists()

    async def down(system, prompt):
        raise RuntimeError("401 authentication failed")

    with pytest.raises(RuntimeError, match="401"):  # said as the model's trouble, not a bad plan
        await make(tmp_path, "a deck about lanterns", down)


# ---------------------------------------------------------------------------- asked for through edit_document
class Jobs:
    def __init__(self):
        self.started = []

    async def start(self, agent, task, **kw):
        self.started.append((agent, task, kw))
        return SimpleNamespace(id="job-1", agent_name="Ledger")

    def recent(self, n):
        return []


def a_brain():
    brain = SimpleNamespace(jobs=Jobs(), spawned=[], registry=None)
    brain._spawn = lambda coro: (brain.spawned.append(coro), coro.close())
    return brain


async def test_edit_document_starts_a_deck_from_a_topic(tmp_path):
    brain = a_brain()
    said = await filework.handle(brain, "edit_document", {"task": "Make a 10-slide pitch deck about harbour lanterns", "files": ["new pptx: Harbour lanterns"]})
    [(agent, _, kw)] = brain.jobs.started
    assert agent == "ledger" and kw["files"] == [] and kw["params"] == {"new": [{"kind": "pptx", "title": "Harbour lanterns"}]}
    assert "building Harbour lanterns.pptx" in said and "opens on their screen" in said
    assert len(brain.spawned) == 1  # it is opened when it is ready


async def test_edit_document_starts_a_deck_from_a_file(tmp_path):
    brain = a_brain()
    src = report(tmp_path / "report.docx")
    said = await filework.handle(brain, "edit_document", {"task": "turn this report into slides", "files": [str(src), "New Presentation: Spring review"]})
    [(_, _, kw)] = brain.jobs.started
    assert kw["files"] == [str(src)] and kw["params"]["new"][0] == {"kind": "pptx", "title": "Spring review"}
    assert "from report.docx" in said


async def test_a_deck_with_no_subject_is_asked_about_first(tmp_path):
    brain = a_brain()
    for task in ("", "make me a presentation", "a 10 slide deck please"):
        said = await filework.handle(brain, "edit_document", {"task": task, "files": ["new pptx: Presentation"]})
        assert "what the deck is about" in said and "not started" in said
    assert brain.jobs.started == []
    odd = tmp_path / "song.mp3"
    odd.write_bytes(b"x")
    said = await filework.handle(brain, "edit_document", {"task": "slides from this", "files": [str(odd), "new deck: Song"]})
    assert "not song.mp3" in said and brain.jobs.started == []
    said = await filework.handle(brain, "edit_document", {"task": "a deck about tea", "files": ["new pptx: Tea", str(tmp_path / "gone.pdf")]})
    assert "can't find gone.pdf" in said and brain.jobs.started == []


async def test_the_whole_job_as_the_app_runs_it(settings, tmp_path, monkeypatch):
    """Asked for through edit_document on a real brain: Ledger builds the deck, the card says
    so, the file is in Documents\\Pyxi (a temp folder here) and it is opened when it's ready."""
    import asyncio

    from pyxi.agents import pixel_common
    from test_brain_and_server import make_brain

    opened: list[Path] = []
    monkeypatch.setattr(filework, "open_path", opened.append)
    monkeypatch.setattr(pixel_common, "pyxi_folder", lambda: tmp_path / "Documents" / "Pyxi")
    model = Model()

    class AsLLM:
        async def complete(self, *, system, messages, **_):
            text, cost = await model(system, messages[-1].parts[0].text)
            return SimpleNamespace(text=text, usage=SimpleNamespace(cost_usd=cost))

    brain, rec = make_brain(settings, llm=AsLLM())
    said = await filework.handle(brain, "edit_document", {"task": "Make a presentation about harbour lanterns", "files": ["new pptx: Harbour lanterns"]})
    assert said.startswith("Started ")
    await brain.jobs.wait_all()
    for _ in range(40):
        await asyncio.sleep(0.01)
    [job] = brain.jobs.recent(5)
    assert job.status == "done", job.summary
    [out] = job.outputs
    assert Path(out) == tmp_path / "Documents" / "Pyxi" / "Harbour lanterns.pptx" and opened == [Path(out)]
    assert job.summary.startswith("Built Harbour lanterns, a 5-slide deck in the Forest theme")
    assert "It's a new file, Harbour lanterns.pptx" in job.summary and "didn't work" not in job.summary
    assert job.data["slides"][0] == "Harbour lanterns" and len(job.data["slides"]) == 5
    [ready] = rec.of("job_ready")
    assert ready["title"] == "Your deck is ready" and ready["files"] == [out] and "open" in ready["actions"]
    assert any(e["data"].get("message") == "Planning the slides" for e in rec.of("job_event") if e["event"] == "status")


async def test_a_new_workbook_is_still_a_workbook():
    brain = a_brain()
    await filework.handle(brain, "edit_document", {"task": "a budget with twelve months", "files": ["new xlsx: Budget"]})
    assert brain.jobs.started[0][2]["params"] == {"new": [{"kind": "xlsx", "title": "Budget"}]}
    assert filework._new_kind("new slides: Tea") == "pptx" and filework._new_kind("new workbook: Tea") == "xlsx"


def test_the_model_is_told_how_a_deck_is_asked_for():
    from pyxi.companion import prompts, tools

    text = json.dumps(tools.__dict__, default=str)
    assert "new pptx: <title>" in text
    assert any("new pptx: <title>" in str(v) for v in vars(prompts).values() if isinstance(v, str))
