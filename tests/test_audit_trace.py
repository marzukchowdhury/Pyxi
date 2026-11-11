"""Trace, after the audit of the five specialists: a screen only counts when what they named
was on it, a file found by what's inside it is kept, one window is one hit, and "nothing
found" says how far back the screen history goes.

Real Memory Palace snapshots with past timestamps, files in a temp folder, a stand-in for the
Windows Search index. No model, no network.
"""

from __future__ import annotations

from pyxi import files_access
from pyxi.agents import trace
from pyxi.companion import specialists
from test_mailwatch import connected, until
from test_trace import NOW, at, history, mails, screen


async def test_one_ordinary_word_on_a_screen_is_not_found_it(settings, monkeypatch):
    """Audit: "the Zephyr invoice pricing page", with only Wikipedia's Main Page in the
    history, was "Found it: Wikipedia, the free encyclopedia" (it has the word "page")."""
    monkeypatch.setattr(trace, "clock", lambda: NOW)
    brain, rec, _ = connected(settings, [])
    brain.spatial.record(
        screen(
            "Wikipedia, the free encyclopedia - Google Chrome",
            "chrome.exe",
            ["Main Page", "Random article", "Log in"],
            at(11, 5, 9),
        )
    )
    await specialists.handle(brain, "find_again", {"query": "the Zephyr invoice pricing page"})
    [ready] = await until(rec, "job_ready")
    assert not ready["ok"] and ready["title"] == "I couldn't find it"


def test_what_a_screen_must_have_on_it(settings, tmp_path):
    """The words for what kind of thing it was don't have to be on it; the rest do: all of
    one or two, at least half of more."""
    assert trace.named(["pricing", "page"]) == ["pricing"]
    assert trace.named(["page"]) == ["page"]  # nothing else to go by
    assert trace.named(["ab", "李雷"]) == ["李雷"]
    assert [trace.enough(w, h) for w, h in ((1, 1), (2, 1), (2, 2), (3, 1), (3, 2), (4, 2))] == [
        True,
        False,
        True,
        False,
        True,
        True,
    ]
    from pyxi.memory.spatial import SpatialMemory

    spatial = SpatialMemory(tmp_path / "s.db")
    history(spatial)
    # "that pricing page": the Acme page says Pricing and nowhere says "page", and is found.
    found = trace.from_screen(spatial, ["pricing", "page"], None)
    assert [h.title for h in found] == ["Pricing page v2", "Pricing | Acme Analytics"]
    # Two of the three words are in what was on the screen (not all in its title).
    [hit] = trace.from_screen(spatial, ["acme", "starter", "zephyr"], None)
    assert hit.title == "Pricing | Acme Analytics"
    assert trace.from_screen(spatial, ["acme", "zephyr"], None) == []  # one of two is not it


async def test_a_window_looked_at_five_times_is_one_hit(settings, monkeypatch):
    """Audit: the same pricing page, visited five times, was the top hit and all four "also"
    lines, and the mail about it was pushed off the list."""
    monkeypatch.setattr(trace, "clock", lambda: NOW)
    brain, rec, _ = connected(settings, mails())
    for i in range(5):  # back and forth between the page and their mail
        brain.spatial.record(
            screen(
                "Pricing | Acme Analytics - Google Chrome",
                "chrome.exe",
                ["Pricing", "Starter"],
                at(11, 5, 9, i * 10),
            )
        )
        brain.spatial.record(screen("Inbox - Outlook", "olk.exe", ["Inbox"], at(11, 5, 9, i * 10 + 5)))
    await specialists.handle(brain, "find_again", {"query": "Acme pricing"})
    [ready] = await until(rec, "job_ready")
    job = next(iter(brain.jobs.jobs.values()))
    on_screen = next(s for s in job.data["card"]["sections"] if s["heading"] == "On screen")
    assert on_screen["lines"] == [
        'On screen in your browser: "Pricing | Acme Analytics", Thu 5 Nov at 09:40'  # the last time
    ]
    assert ready["title"] == "Found it: Pricing | Acme Analytics"
    assert any("Acme pricing for your team" in c for c in ready["changes"])  # the mail is back


async def test_a_file_found_by_what_is_inside_it_is_kept(settings, tmp_path, monkeypatch):
    """Audit: Windows Search returned "Q3 board deck.pdf" because Zephyr is in its text. Its
    name doesn't say so, it scored 0.2, and Trace said "I couldn't find it"."""
    monkeypatch.setattr(trace, "clock", lambda: NOW)
    folder = tmp_path / "Documents"
    folder.mkdir()
    doc = folder / "Q3 board deck.pdf"
    doc.write_bytes(b"%PDF")
    outside = tmp_path / "Private" / "Zephyr.pdf"  # not in a folder they allowed
    outside.parent.mkdir()
    outside.write_bytes(b"%PDF")
    found = [files_access.Hit(str(doc), 4, at(11, 1, 9)), files_access.Hit(str(outside), 4, 0.0)]
    monkeypatch.setattr(files_access, "search_index", lambda *a, **k: found)
    files_access.save(settings.data_dir, files_access.FileAccess(True, [str(folder)], True))
    brain, rec, _ = connected(settings, [])
    await specialists.handle(brain, "find_again", {"query": "Zephyr"})
    [ready] = await until(rec, "job_ready")
    assert ready["ok"] and ready["title"] == "Found it: Q3 board deck.pdf"
    assert ready["files"] == [str(doc)]
    # A name with one word of three, found by walking the folders, is scored as before.
    by_name = trace.from_files(found[:1], ["zephyr"], None)
    assert by_name[0].score == 0.2 and trace.from_files(found[:1], ["zephyr"], None, True)[0].score == 0.5


async def test_nothing_found_says_how_far_back_the_screen_history_goes(settings, monkeypatch):
    """Audit: "where did I see it last week" can never be in three days of screen history,
    and "nothing in your screen history" read as if all of it had been looked through."""
    monkeypatch.setattr(trace, "clock", lambda: NOW)
    brain, rec, _ = connected(settings, [])
    await specialists.handle(
        brain, "find_again", {"query": "the giraffe spreadsheet", "when": "last week"}
    )
    [ready] = await until(rec, "job_ready")
    assert not ready["ok"] and ready["body"].endswith("Screen history goes back 3 days.")
    assert [trace.how_long(h) for h in (72, 24, 6, 720)] == ["3 days", "a day", "6 hours", "30 days"]
    # Not said when the screen history wasn't one of the places looked in.
    rep = trace.report([], "giraffe", ["files"], 72)
    assert "goes back" not in rep["body"] and "goes back" not in rep["summary"]
