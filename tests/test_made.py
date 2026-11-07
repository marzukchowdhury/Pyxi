"""The list of files Pyxi made (.pyxi/made.json): what the desktop app checks before it opens,
reveals or drags a file out of a result card."""

from __future__ import annotations

import json

from PIL import Image

from pyxi import made
from pyxi.companion import filework
from test_brain_and_server import make_brain


def test_record_keeps_existing_files_newest_last(tmp_path):
    a, b = tmp_path / "a.pdf", tmp_path / "b.pdf"
    a.write_bytes(b"%PDF")
    b.write_bytes(b"%PDF")
    made.record(tmp_path / "data", [a, tmp_path / "gone.pdf"])
    made.record(tmp_path / "data", [b, a])  # a again: moves to the end, not twice
    assert made.load(tmp_path / "data") == [str(b.resolve()), str(a.resolve())]
    assert json.loads((tmp_path / "data" / "made.json").read_text()) == made.load(tmp_path / "data")


def test_the_list_stays_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(made, "MAX", 3)
    files = []
    for i in range(5):
        f = tmp_path / f"{i}.txt"
        f.write_text("x")
        files.append(f)
        made.record(tmp_path, [f])
    assert made.load(tmp_path) == [str(f.resolve()) for f in files[-3:]]


def test_a_broken_list_is_an_empty_list(tmp_path):
    (tmp_path / "made.json").write_text("{not json")
    assert made.load(tmp_path) == []


async def test_a_finished_pixel_job_records_its_file(settings, tmp_path):
    brain, rec = make_brain(settings)
    Image.new("RGB", (400, 300), "white").save(tmp_path / "receipt.jpg")
    await filework.handle(
        brain,
        "convert_files",
        {"operation": "images_to_pdf", "files": [str(tmp_path / "receipt.jpg")]},
    )
    await brain.jobs.wait_all()
    assert made.load(settings.data_dir) == [str((tmp_path / "receipt.pdf").resolve())]
    [ready] = rec.of("job_ready")
    assert ready["files"] == [str(tmp_path / "receipt.pdf")]  # the card gets the same path


async def test_a_failed_job_records_nothing(settings, tmp_path):
    brain, _ = make_brain(settings)
    (tmp_path / "a.pdf").write_bytes(b"%PDF")
    job = await brain.jobs.start(
        "pixel", "merge", files=[str(tmp_path / "a.pdf")], params={"operation": "merge_pdfs"}
    )
    await brain.jobs.wait(job.id)
    assert made.load(settings.data_dir) == []
