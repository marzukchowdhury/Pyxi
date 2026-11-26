"""File and Office work that fails says which file, why in plain words, and what to do. No
"[Errno 13] Permission denied: 'C:\\Users\\...'", no "File is not a zip file", no class names.
"""

from __future__ import annotations

import json
import re
import zipfile

import pytest
from PIL import Image

from pyxi import audit as audit_mod
from pyxi import plainwords as pw
from pyxi import tidy
from pyxi.agents import deck_build, ledger, ledger_live, libreoffice, pixel, pixel_pdf
from pyxi.agents.jobs import describe_failure
from pyxi.agents.ledger_common import LedgerError
from pyxi.agents.pixel_common import PixelError
from pyxi.companion import filework

PYTHON = re.compile(r"Errno|WinError|Error\b|Exception|Traceback|not a zip|\('|\{'|C:\\\\|C:\\Users")


def clean(line: str) -> str:
    assert line and not PYTHON.search(line), line
    return line


# ---------------------------------------------------------------- Ledger (Excel, Word, PowerPoint)
def test_a_damaged_or_password_locked_office_file(tmp_path):
    """A workbook locked with a password is not a zip inside: the job said "File is not a
    zip file" and stopped there."""
    bad = tmp_path / "Budget 2026.xlsx"
    bad.write_bytes(b"\xd0\xcf\x11\xe0 not a zip")
    with pytest.raises(Exception) as err:
        ledger.open_local(bad)
    line = clean(describe_failure(err.value))
    assert line == pw.FILE_DAMAGED and "locked with a password" in line and "save a fresh copy" in line


def test_one_edit_that_breaks_is_said_without_pythons_words():
    line = clean(ledger._didnt_work("insert picture", OSError(r"cannot identify image file 'C:\\Users\\sam\\logo.png'")))
    assert line == f"The insert picture didn't work. {pw.PICTURE_DAMAGED}"
    line = clean(ledger._didnt_work("totals row", IndexError("list index out of range")))
    assert line == "The totals row didn't work, so that part is unchanged. Ask me for it again, worded another way."


def test_an_office_app_that_gives_no_reason_gets_a_next_step():
    class COMError(Exception):  # the name comtypes gives it
        pass

    exc = COMError(-2147352567, "Exception occurred.", (None, None, None, 0, None))
    said = ledger_live._plain(exc, "Excel")
    assert isinstance(said, LedgerError)
    assert str(said).startswith("Excel refused that edit.") and "then ask again" in str(said)
    assert "Exception occurred" not in str(said)
    # The app's own reason is still passed on when it gave one.
    exc = COMError(-2147352567, "Exception occurred.", ("The cell is protected.", "Excel", None, 0, None))
    assert str(ledger_live._plain(exc, "Excel")) == "Excel said no: The cell is protected."


# ---------------------------------------------------------------- decks
def test_a_source_the_deck_builder_cannot_read(tmp_path):
    bad = tmp_path / "Report.docx"
    bad.write_bytes(b"not a real document")
    with pytest.raises(deck_build.DeckError) as err:
        deck_build.read_source([bad])
    assert clean(str(err.value)) == f"I couldn't read Report.docx. {pw.FILE_DAMAGED}"


def test_a_pdf_the_deck_builder_cannot_read_says_what_to_do(tmp_path, monkeypatch):
    from types import SimpleNamespace

    pdf = tmp_path / "Plan.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    monkeypatch.setattr(pixel_pdf, "read_layout", lambda path, *a, **kw: None)
    with pytest.raises(deck_build.DeckError) as err:
        deck_build.read_source([pdf])
    assert str(err.value).startswith("I couldn't read Plan.pdf. It may be damaged") and "save a fresh copy" in str(err.value)
    # A scan with no reader for scans on this PC: said as that, not "ask Pixel to make it
    # searchable first", which needs the same reader.
    missing = "I can't read scanned pages on this PC right now. Reinstalling Pyxi usually fixes it."
    scan = SimpleNamespace(words=[], blocks=[], tables=[], no_reader=missing)
    monkeypatch.setattr(pixel_pdf, "read_layout", lambda path, *a, **kw: scan)
    with pytest.raises(deck_build.DeckError) as err:
        deck_build.read_source([pdf])
    assert str(err.value) == missing


# ---------------------------------------------------------------- Pixel
def test_a_pdf_that_is_not_one_says_what_to_do(tmp_path):
    bad = tmp_path / "Scan.pdf"
    bad.write_bytes(b"not a pdf")
    with pytest.raises(PixelError) as err:
        pixel_pdf._reader(bad)
    line = clean(str(err.value))
    assert line.startswith("Scan.pdf doesn't open as a PDF.") and "save a fresh copy" in line


def test_a_damaged_picture_among_several_is_named(tmp_path):
    good = tmp_path / "a.png"
    Image.new("RGB", (40, 30), (200, 20, 20)).save(good)
    bad = tmp_path / "b.png"
    bad.write_bytes(b"this is not a picture")
    with pytest.raises(PixelError) as err:
        pixel.run("convert_image", [str(good), str(bad)], {"format": "jpg"})
    line = clean(str(err.value))
    assert line.startswith("b.png won't open as a picture.") and "Try another copy" in line
    assert str(tmp_path) not in line


def test_one_file_that_broke_names_the_file_once():
    line = clean(pixel._one_failed(r"C:\Users\sam\Pictures\b.png", zipfile.BadZipFile("File is not a zip file")))
    assert line == f"b.png: {pw.FILE_DAMAGED}"
    locked = PermissionError(13, "Permission denied", r"C:\Users\sam\Pictures\b.png")
    locked.winerror = 32
    assert pixel._one_failed(r"C:\Users\sam\Pictures\b.png", locked) == "b.png is open in another app. Close it there, then ask again."


# ---------------------------------------------------------------- reading a file in a turn
def test_a_file_that_cannot_be_read_in_a_turn(tmp_path):
    path = tmp_path / "CV.docx"
    locked = PermissionError(13, "Permission denied", str(path))
    note = filework._unreadable("read_file", path, locked)
    assert note.startswith("read_file: couldn't read CV.docx. Windows won't let me open or save CV.docx.")
    assert "Errno" not in note and str(tmp_path) not in note and "don't guess" in note
    old = filework._unreadable("fill_form", tmp_path / "CV.doc", ValueError("I can't read .doc files."))
    assert "save it as .docx" in old and "PDF copy" in old and old.startswith("fill_form: CV.doc is an older kind")
    odd = filework._unreadable("read_file", path, KeyError("word/document.xml"))
    assert "word/document.xml" not in odd and "It may be damaged, or open in another app." in odd


# ---------------------------------------------------------------- undo
def test_an_undo_that_fails_says_why_and_can_be_tried_again(tmp_path, monkeypatch):
    src, dst = tmp_path / "in" / "a.pdf", tmp_path / "out" / "a.pdf"
    dst.parent.mkdir()
    src.parent.mkdir()
    dst.write_bytes(b"x")
    log_path = tmp_path / "tidy.json"
    log_path.write_text(json.dumps({"moves": [[str(src), str(dst)]], "made": []}), encoding="utf-8")
    real_move = tidy.shutil.move

    def in_use(a, b):
        raise PermissionError(13, "The process cannot access the file because it is being used by another process", str(a))

    monkeypatch.setattr(tidy.shutil, "move", in_use)
    said = tidy.undo_log(log_path)
    assert "1 couldn't move back (a.pdf)" in said and "Close them, then undo again." in said
    assert "process cannot access" not in said
    assert log_path.exists()  # still there: the same Undo finishes it
    monkeypatch.setattr(tidy.shutil, "move", real_move)
    assert tidy.undo_log(log_path) == "Put 1 files back where they were."
    assert src.exists() and not log_path.exists()


def test_the_activity_logs_undo_never_shows_the_raw_error(tmp_path, monkeypatch):
    def broken(how):
        raise PermissionError(13, "Permission denied", r"C:\Users\sam\Desktop\a.txt")

    monkeypatch.setattr(audit_mod, "_undo", broken)
    log = audit_mod.AuditLog(tmp_path / "audit.jsonl")
    entry = log.add("moved", "Renamed 1 file", agent="Ditto", undo={"type": "ditto", "log": "x"})
    said = log.undo(entry["id"])
    assert said.startswith("I couldn't undo that. Windows won't let me open or save a.txt.")
    clean(said)
    assert log.recent()[0]["can_undo"]  # nothing changed, so Undo is still offered

    # A tidy whose files were open stays undoable: the same button finishes it later.
    monkeypatch.setattr(audit_mod, "_undo", lambda how: "Put 2 files back where they were. 1 couldn't move back (a.pdf): they may be open in another app. Close them, then undo again.")
    assert "Close them, then undo again." in log.undo(entry["id"])
    assert log.recent()[0]["can_undo"]


# ---------------------------------------------------------------- a job that finished
async def test_a_finished_job_is_still_told_when_its_files_cannot_be_noted(settings, tmp_path, monkeypatch):
    """With the disk full, noting the new file for the app raised after the work was done:
    the job stayed "running" in the tray and was never announced."""
    from pyxi import made
    from test_answer_card import make_brain

    def full(*a, **kw):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(made, "record", full)
    brain, _ = make_brain(settings)
    src = tmp_path / "a.png"
    Image.new("RGB", (40, 30), (200, 20, 20)).save(src)
    job = await brain.jobs.start("pixel", "to jpg", files=[str(src)], params={"operation": "convert_image", "options": {"format": "jpg"}})
    job = await brain.jobs.wait(job.id)
    assert job.status == "done" and job.outputs and job.ended_at


# ---------------------------------------------------------------- LibreOffice
def test_libreoffice_set_up_failures_say_what_to_do():
    assert "Is the internet connection up?" in libreoffice._plain(RuntimeError("connect failed"))
    odd = libreoffice._plain(KeyError("soffice"))
    assert odd == "LibreOffice didn't install. Try again from Settings, under Your files."
    kept = "Windows couldn't unpack LibreOffice. If another installer or a Windows update is running, let it finish, then try again from Settings, under Your files."
    assert libreoffice._plain(RuntimeError(kept)) == kept
