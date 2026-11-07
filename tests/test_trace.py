"""Trace, the memory teammate: "where did I see that pricing page last week?" across the Memory
Palace's screen history, the files they allowed, and a fake Gmail, with the exact place.

The screen history is real Memory Palace snapshots (recorded with past timestamps), the files
are real files in a temp folder (Windows Search is switched off so only that folder counts),
and the mail is a fake Gmail. No model call: Trace works in code.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from pyxi import files_access
from pyxi.agents import trace
from pyxi.companion import specialists
from pyxi.screen.context import ScreenContext, UIElement, WindowInfo
from test_mailwatch import connected, until

TZ = ZoneInfo("America/Vancouver")
NOW = datetime(2026, 11, 7, 10, 0)  # a Saturday: "last week" is Mon 26 Oct to Sun 1 Nov


def at(month: int, day: int, hour: int, minute: int = 0) -> float:
    return datetime(2026, month, day, hour, minute).timestamp()


def screen(title: str, app: str, names: list[str], ts: float) -> ScreenContext:
    elements = [
        UIElement(i + 1, n, "Text", (100, 100 + 40 * i, 400, 30)) for i, n in enumerate(names)
    ]
    return ScreenContext(ts, WindowInfo(title, app, (0, 0, 1920, 1080)), elements)


def history(spatial: Any) -> None:
    spatial.record(
        screen(
            "Pricing | Acme Analytics - Google Chrome",
            "chrome.exe",
            ["Pricing", "Starter $29/month", "Team $99/month", "Talk to sales"],
            at(10, 29, 14, 20),
        )
    )
    spatial.record(
        screen("Inbox - Outlook", "olk.exe", ["Inbox", "Focused", "Other"], at(10, 30, 9))
    )
    spatial.record(
        screen(
            "Pricing page v2 - Figma", "figma.exe", ["Pricing page v2", "Frame 12"], at(10, 2, 11)
        )
    )  # a month before
    spatial.record(
        screen(
            "Lease renewal.pdf - Adobe Acrobat",
            "acrobat.exe",
            ["Lease renewal", "Monthly rent $2,150"],
            at(11, 3, 16, 5),
        )
    )


def mails() -> list[dict[str, Any]]:
    return [
        {
            "from": "Dana Lee <dana@acme.example>",
            "subject": "Acme pricing for your team",
            "body": "Hi Alex, here is our new pricing for teams of 10. Starter is $29 a month.",
            "html": '<a href="https://acme.example/pricing">See the pricing page</a>',
            "date": datetime(2026, 10, 28, 9, 0, tzinfo=TZ),
        },
        {
            "from": "Landlord <rent@homes.example>",
            "subject": "Your lease renewal",
            "body": "Attached is the lease renewal for 2027.",
            "date": datetime(2026, 11, 2, 9, 0, tzinfo=TZ),
        },
    ]


def files(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    for name, when in (
        ("Acme pricing 2026.pdf", at(10, 28, 10)),
        ("Lease renewal.pdf", at(11, 3, 16)),
        ("notes.txt", at(9, 1, 9)),
    ):
        p = folder / name
        p.write_bytes(b"%PDF-1.4 sample")
        os.utime(p, (when, when))
    return folder


def test_the_question_becomes_words_and_a_week():
    assert trace.words("where did I see that pricing page last week?") == ["pricing", "page"]
    start, end = trace.window("that pricing page", "last week", NOW)
    assert datetime.fromtimestamp(start) == datetime(2026, 10, 26) and datetime.fromtimestamp(
        end
    ) == datetime(2026, 11, 2)
    start, _ = trace.window("the lease pdf", "on Tuesday", NOW)
    assert datetime.fromtimestamp(start).date().isoformat() == "2026-11-03"
    assert trace.window("the lease", "", NOW) is None
    assert trace.short("Plans and pricing - Personal - Microsoft​ Edge") == "Plans and pricing"


async def test_the_screen_moment_wins_and_the_others_are_listed(settings, tmp_path, monkeypatch):
    monkeypatch.setattr(trace, "clock", lambda: NOW)
    monkeypatch.setattr(files_access, "search_index", lambda *a, **k: None)
    folder = files(tmp_path / "Documents")
    files_access.save(settings.data_dir, files_access.FileAccess(True, [str(folder)], True))
    brain, rec, fake = connected(settings, mails())
    history(brain.spatial)
    out = await specialists.handle(
        brain, "find_again", {"query": "that pricing page", "when": "last week"}
    )
    assert out.startswith("Started job_") and "Trace" in out and "file search is off" not in out
    [ready] = await until(rec, "job_ready")
    assert ready["agent"] == "Trace" and ready["ok"]
    assert ready["title"] == "Found it: Pricing | Acme Analytics"
    assert ready["body"].startswith(
        'On screen in your browser: "Pricing | Acme Analytics", Thu 29 Oct at 14:20.'
    )
    assert 'say "show me"'.lower() in ready["body"].lower()
    others = " ".join(ready["changes"])
    assert "Acme pricing for your team" in others and "https://acme.example/pricing" in others
    assert "Acme pricing 2026.pdf" in others and "Figma" not in ready["title"]
    assert "Lease" not in others and "notes.txt" not in others
    job = next(iter(brain.jobs.jobs.values()))
    sections = {s["heading"] for s in job.data["card"]["sections"]}
    assert sections == {"On screen", "Files", "Mail"}
    assert "snapshot" in job.summary  # the model can bring it back with time_travel
    assert fake.sent == []


async def test_a_file_answer_opens_and_a_mail_answer_links(settings, tmp_path, monkeypatch):
    monkeypatch.setattr(trace, "clock", lambda: NOW)
    monkeypatch.setattr(files_access, "search_index", lambda *a, **k: None)
    folder = files(tmp_path / "Documents")
    files_access.save(settings.data_dir, files_access.FileAccess(True, [str(folder)], True))
    brain, rec, _ = connected(settings, mails())
    history(brain.spatial)
    await specialists.handle(
        brain, "find_again", {"query": "lease renewal pdf", "when": "on Tuesday"}
    )
    [ready] = await until(rec, "job_ready")
    assert ready["files"] and Path(ready["files"][0]).name == "Lease renewal.pdf"
    assert "Adobe Acrobat" not in ready["title"] and "acrobat" in ready["body"].lower()
    assert "open" in ready["actions"] and ready["title"].startswith("Found it: Lease renewal")
    # Mail only (no screen history, no files): the email's own link opens it.
    brain2, rec2, _ = connected(settings.model_copy(update={"data_dir": tmp_path / "d2"}), mails())
    await specialists.handle(brain2, "find_again", {"query": "Acme pricing email"})
    [r2] = await until(rec2, "job_ready")
    assert (
        r2["title"] == "Found it: Acme pricing for your team"
        and r2["link"] == "https://acme.example/pricing"
    )


async def test_nothing_found_says_where_it_looked(settings, monkeypatch):
    monkeypatch.setattr(trace, "clock", lambda: NOW)
    brain, rec, _ = connected(settings, [])
    out = await specialists.handle(brain, "find_again", {"query": "the giraffe spreadsheet"})
    assert "file search is off" in out
    [ready] = await until(rec, "job_ready")
    assert not ready["ok"] and ready["title"] == "I couldn't find it"
    assert "screen history and mail" in ready["body"]
    assert "find_again: what are they" in await specialists.handle(
        brain, "find_again", {"query": " "}
    )


@pytest.mark.parametrize("q", ["where did I see it", "that thing from last week"])
async def test_no_words_to_look_for(settings, q):
    brain, rec, _ = connected(settings, [])
    await specialists.handle(brain, "find_again", {"query": q})
    [ready] = await until(rec, "job_ready")
    assert ready["title"] == "What should I look for?"
