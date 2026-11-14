"""A plan Quill wrote can be made a PDF: by the teammate after Quill, or when asked afterwards.

Live, first tester, 13 November: "Can you plan the whole thing and make it a PDF file?" Quill
wrote the plan, Pixel was handed no file and failed with no reason, and "Just convert it to
PDF" was answered "I can't find the Word plan without file search".
"""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

from pyxi.agents import jobs as jobs_mod
from pyxi.agents.jobs import JobManager, draft_title, named_draft
from pyxi.companion import filework


def _draft_job(tmp_path: Path, text: str = "# Whistler weekend plan\n\nSaturday: ski.\n", **kw):
    out = tmp_path / "outputs"
    out.mkdir(exist_ok=True)
    path = out / "j1-quill.md"
    path.write_text(text, encoding="utf-8")
    base = dict(
        id="j1", role="writer", files=[], outputs=[str(path)], task="Plan the trip",
        status="done", ended_at=time.time(), then=SimpleNamespace(agent="pixel", task="make it a PDF"),
        data={}, agent_name="Quill",
    )
    return SimpleNamespace(**{**base, **kw})


def test_a_draft_is_named_after_its_heading() -> None:
    assert draft_title("# Whistler weekend plan\n\ntext") == "Whistler weekend plan"
    assert draft_title("", "Plan the trip: day by day") == "Plan the trip day by day"
    assert draft_title("", "") == "Draft"
    assert "/" not in draft_title('## A/B: "plan"?')


def test_the_draft_goes_to_pixel_as_a_file(tmp_path: Path) -> None:
    job = _draft_job(tmp_path)
    manager = SimpleNamespace(registry=SimpleNamespace(get=lambda _n: SimpleNamespace(role="files")))
    files, params = JobManager._handed_on(manager, job)
    assert [Path(f).name for f in files] == ["Whistler weekend plan.md"]
    assert Path(files[0]).read_text(encoding="utf-8").startswith("# Whistler")
    assert params["save_in"].endswith("Pyxi")  # Documents\Pyxi, not Pyxi's own hidden folder


def test_a_document_quill_fixed_is_not_renamed(tmp_path: Path) -> None:
    job = _draft_job(tmp_path, files=["C:/x/letter.docx"])
    assert named_draft(job) is None


def test_convert_it_finds_what_pyxi_just_made(tmp_path: Path, monkeypatch) -> None:
    job = _draft_job(tmp_path, then=None)
    brain = SimpleNamespace(jobs=SimpleNamespace(recent=lambda _n=12: [job]))
    files, save_in = filework._just_made(brain)
    assert [Path(f).name for f in files] == ["Whistler weekend plan.md"]
    assert save_in.endswith("Pyxi")
    job.ended_at = time.time() - 3 * 3600  # hours ago: not "it" any more
    assert filework._just_made(brain) == ([], "")
    assert jobs_mod  # imported for the helpers above
