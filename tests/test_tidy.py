"""Tidying a folder: only moves, never overwrites, never touches folders, and undoes exactly."""

from blip import tidy


def mess(tmp_path):
    for name in [
        "Screenshot 2026-11-01.png",
        "Screenshot 2026-11-02.png",
        "resume.pdf",
        "notes.docx",
        "setup.exe",
    ]:
        (tmp_path / name).write_text(name)
    (tmp_path / "desktop.ini").write_text("system")
    (tmp_path / "my-project").mkdir()
    (tmp_path / "Documents").mkdir()
    (tmp_path / "Documents" / "resume.pdf").write_text("an older resume")  # name already taken
    return tmp_path


GROUPS = [
    {"folder": "Screenshots", "files": ["Screenshot 2026-11-01.png", "Screenshot 2026-11-02.png"]},
    {"folder": "Documents", "files": ["resume.pdf", "notes.docx"]},
    {"folder": "Installers", "files": ["setup.exe", "my-project", "desktop.ini", "ghost.txt"]},
    {"folder": "../escape", "files": ["notes.docx"]},
]


def test_the_listing_shows_files_and_folders_but_not_system_files(tmp_path):
    text = tidy.listing(mess(tmp_path))
    assert "5 loose files" in text and "my-project" in text and "desktop.ini" not in text


def test_a_plan_only_moves_real_loose_files(tmp_path):
    plan = tidy.make_plan(mess(tmp_path), GROUPS)
    moved = {name for name, _ in plan.moves}
    assert moved == {
        "Screenshot 2026-11-01.png",
        "Screenshot 2026-11-02.png",
        "resume.pdf",
        "notes.docx",
        "setup.exe",
    }
    assert any("bad folder name" in p for p in plan.problems)  # no escaping the folder
    assert plan.summary().startswith("Move 5 files into 3 folders")


def test_moving_never_overwrites_and_undo_puts_everything_back(tmp_path):
    folder = mess(tmp_path)
    before = sorted(p.name for p in folder.iterdir())
    plan = tidy.make_plan(folder, GROUPS)
    moved, failed, _ = tidy.apply(plan, tmp_path / ".logs")
    assert moved == 5 and not failed
    assert (folder / "Documents" / "resume (2).pdf").read_text() == "resume.pdf"
    assert (folder / "Documents" / "resume.pdf").read_text() == "an older resume"  # untouched
    assert (folder / "my-project").is_dir() and (folder / "desktop.ini").exists()
    print(tidy.undo_last(tmp_path / ".logs"))
    after = sorted(p.name for p in folder.iterdir() if p.name != ".logs")
    assert after == before  # Screenshots and Installers were made by the tidy, so they're gone
    assert (folder / "Documents" / "resume.pdf").read_text() == "an older resume"
    assert tidy.undo_last(tmp_path / ".logs").startswith("There's nothing")
