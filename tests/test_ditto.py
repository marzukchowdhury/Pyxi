"""Ditto, "watch me once, then do the rest": the recording (focus, fields, windows, a folder
before and after), the plan for the remaining items, and doing them only after the card.

The sample is a folder of real PDF invoices (reportlab); the user renames one in Explorer
while the fake UI Automation reader reports what a real one would. The model is scripted.
"""

from __future__ import annotations

import json
from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from pyxi.agents import ditto
from pyxi.companion import specialists
from test_connectors_and_docs import Script
from test_mailwatch import connected, until

INVOICES = {
    "invoice-1182.pdf": ("Northwind Supplies", "2026-10-03", "1,175.20"),
    "invoice-1183.pdf": ("Contoso Ltd", "2026-10-09", "412.00"),
    "invoice-1184.pdf": ("Fabrikam Inc", "2026-10-15", "89.50"),
    "invoice-1185.pdf": ("Litware", "2026-10-21", "2,040.00"),
}


def invoices(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    for name, (vendor, day, total) in INVOICES.items():
        c = canvas.Canvas(str(folder / name), pagesize=letter)
        c.drawString(72, 720, f"INVOICE {name[8:12]}")
        c.drawString(72, 700, f"From: {vendor}")
        c.drawString(72, 680, f"Date: {day}")
        c.drawString(72, 660, f"Total due: ${total}")
        c.save()
    (folder / "notes.txt").write_text("not an invoice", encoding="utf-8")
    return folder


def explorer(name: str, role: str, value: str = "", password: bool = False) -> ditto.Focus:
    return ditto.Focus("explorer.exe", "Invoices - File Explorer", name, role, value, password)


PLAN = {
    "summary": "Rename each invoice to its date and who it's from, like the first one.",
    "route": "files",
    "items": [
        {"item": "invoice-1183.pdf", "to": "2026-10-09 Contoso Ltd.pdf"},
        {"item": "invoice-1184.pdf", "to": "2026-10-15 Fabrikam Inc.pdf"},
        {"item": "invoice-1185.pdf", "to": "2026-10-21 Litware"},  # it keeps the .pdf itself
        {"item": "invoice-9999.pdf", "to": "made up.pdf"},  # not in the folder: dropped
    ],
    "why": "They renamed a file; the rest are files in the same folder.",
}


async def test_watching_records_fields_windows_and_the_rename(tmp_path):
    folder = invoices(tmp_path / "Invoices")
    seen = iter(
        [
            explorer("invoice-1182.pdf", "ListItem"),
            explorer("Name", "Edit", "invoice-1182.pdf"),
            explorer("Name", "Edit", "2026-10-03 Northwind Supplies.pdf"),
            explorer("2026-10-03 Northwind Supplies.pdf", "ListItem"),
            ditto.Focus("pyxi.exe", "Pyxi", "Send", "Button"),  # Pyxi's own overlay: ignored
            ditto.Focus("1password.exe", "1Password", "Vault", "Edit", "secret"),  # excluded app
            ditto.Focus("msedge.exe", "Sign in", "Password", "Edit", "hunter2", password=True),
            ditto.Focus("msedge.exe", "Sign in", "Next", "Button"),
        ]
    )
    logged = []
    rec = ditto.Recorder(
        "rename the invoices by date and vendor",
        str(folder),
        out_dir=tmp_path / "watch",
        reader=lambda: next(seen),
        log=logged.append,
        excluded=lambda app, title: "1password" in app,
    )
    for i in range(8):
        await rec.tick()
        if i == 2:  # Explorer renames the file when they press Enter
            (folder / "invoice-1182.pdf").rename(folder / "2026-10-03 Northwind Supplies.pdf")
    recording = await rec.stop()
    lines = [s.line() for s in recording.steps]
    assert lines[0] == "switched to explorer.exe: Invoices - File Explorer"
    assert 'clicked listitem "invoice-1182.pdf"' in lines
    assert 'typed "2026-10-03 Northwind Supplies.pdf" into edit "Name"' in lines
    assert recording.changes == [
        {"op": "renamed", "from": "invoice-1182.pdf", "to": "2026-10-03 Northwind Supplies.pdf"}
    ]
    assert lines[-1] == "file renamed invoice-1182.pdf -> 2026-10-03 Northwind Supplies.pdf"
    text = json.dumps([s.__dict__ for s in recording.steps])
    assert "hunter2" not in text and "secret" not in text and "pyxi.exe" not in text
    assert len(logged) == len(recording.steps)  # every step went to the activity log
    again = ditto.Recording.load(tmp_path / "watch" / f"{recording.id}.json")
    assert again.steps[2].line() == lines[2]
    assert ditto.remaining(again) == ["invoice-1183.pdf", "invoice-1184.pdf", "invoice-1185.pdf"]


def test_folder_changes_tell_renames_from_moves(tmp_path):
    folder = tmp_path / "f"
    (folder / "done").mkdir(parents=True)
    for n in ("a.pdf", "b.pdf", "c.pdf"):
        (folder / n).write_text(n)
    before = ditto.listing(folder)
    (folder / "a.pdf").rename(folder / "A renamed.pdf")
    (folder / "b.pdf").rename(folder / "done" / "b.pdf")
    (folder / "c.pdf").unlink()
    (folder / "new.txt").write_text("x")
    ops = sorted(
        (c["op"], c.get("from", ""), c.get("to", ""))
        for c in ditto.folder_changes(before, ditto.listing(folder))
    )
    assert ops == [
        ("created", "", "new.txt"),
        ("deleted", "c.pdf", ""),
        ("moved", "b.pdf", str(Path("done/b.pdf"))),
        ("renamed", "a.pdf", "A renamed.pdf"),
    ]


def test_a_plan_only_names_real_items_and_safe_paths(tmp_path):
    folder = invoices(tmp_path / "Invoices")
    allowed = ["invoice-1183.pdf", "invoice-1184.pdf", "invoice-1185.pdf"]
    plan = ditto.parse_plan("Plan: " + json.dumps(PLAN), allowed)
    assert [it["item"] for it in plan.items] == allowed
    assert ditto.parse_plan(json.dumps({**PLAN, "route": "teleport"}), allowed) is None
    plan.items += [
        {"item": "invoice-1183.pdf", "to": "../outside.pdf"},
        {"item": "invoice-1184.pdf", "to": "a:b.pdf"},
    ]
    moves = ditto.check_files(plan, folder)
    assert [(s.name, d.name) for s, d in moves] == [
        ("invoice-1183.pdf", "2026-10-09 Contoso Ltd.pdf"),
        ("invoice-1184.pdf", "2026-10-15 Fabrikam Inc.pdf"),
        ("invoice-1185.pdf", "2026-10-21 Litware.pdf"),
    ]
    done, undo = ditto.do_files(moves, tmp_path / "undo")
    assert done == 3 and (folder / "2026-10-21 Litware.pdf").is_file()
    assert ditto.undo_files(undo) == 3 and (folder / "invoice-1185.pdf").is_file()


async def test_pyxi_watches_once_then_does_the_rest_after_the_card(settings, tmp_path, monkeypatch):
    folder = invoices(tmp_path / "Invoices")
    seen = iter(
        [
            explorer("invoice-1182.pdf", "ListItem"),
            explorer("Name", "Edit", "2026-10-03 Northwind Supplies.pdf"),
            explorer("2026-10-03 Northwind Supplies.pdf", "ListItem"),
        ]
    )
    monkeypatch.setattr(ditto, "read_focus", lambda: next(seen, None))
    llm = Script(json.dumps(PLAN))
    brain, rec, _ = connected(settings, [], llm=llm)
    out = await specialists.handle(
        brain,
        "watch_me",
        {"action": "start", "task": "rename these invoices", "folder": str(folder)},
    )
    assert out.startswith("Watching now.")
    recorder = brain.ditto_recorder
    recorder._task.cancel()  # tick by hand instead of on a timer
    recorder.snap = None
    for i in range(3):
        await recorder.tick()
        if i == 1:
            (folder / "invoice-1182.pdf").rename(folder / "2026-10-03 Northwind Supplies.pdf")
    out = await specialists.handle(brain, "watch_me", {"action": "stop"})
    assert "Started job_" in out and "Ditto" in out
    [ready] = await until(rec, "job_ready")
    assert ready["agent"] == "Ditto" and ready["title"] == "Ready to do 3 more"
    prompt = llm.prompts[0]
    assert "file renamed invoice-1182.pdf -> 2026-10-03 Northwind Supplies.pdf" in prompt
    assert "From: Northwind Supplies" in prompt  # what's inside the one they did
    assert "- invoice-1184.pdf: " in prompt and "Fabrikam Inc" in prompt  # and the rest
    assert "notes.txt" not in prompt.split("Items still to do:")[1].split("What's in them")[0]
    [card] = await until(rec, "confirm")
    assert card["action"] == "Do the other 3 the same way?"
    assert "invoice-1183.pdf  ->  2026-10-09 Contoso Ltd.pdf" in card["reason"]
    assert "Renames and moves the files right here" in card["reason"]
    brain.hub.resolve(card["request_id"], True)
    for _ in range(100):
        if any(r["title"] == "Done 3 more" for r in rec.of("job_ready")):
            break
        await __import__("asyncio").sleep(0.02)
    names = sorted(p.name for p in folder.iterdir())
    assert names == [
        "2026-10-03 Northwind Supplies.pdf",
        "2026-10-09 Contoso Ltd.pdf",
        "2026-10-15 Fabrikam Inc.pdf",
        "2026-10-21 Litware.pdf",
        "notes.txt",
    ]
    undo = await specialists.handle(brain, "watch_me", {"action": "undo"})
    assert "put 3 files back" in undo and (folder / "invoice-1183.pdf").is_file()
    assert (folder / "2026-10-03 Northwind Supplies.pdf").is_file()  # theirs stays as they did it


async def test_the_mouse_is_the_last_resort_and_asks_first(settings, tmp_path):
    rec_dir = tmp_path / "watch"
    recording = ditto.Recording("watch_x", "copy rows into the old app", "", 0.0)
    recording.steps = [ditto.Step(0.0, "window", "legacy.exe", "Orders")]
    recording.save(rec_dir)
    plan = {
        "summary": "Type each order into the old app.",
        "route": "mouse",
        "items": [{"item": "A-1"}, {"item": "A-2"}],
        "steps": ["click New", "type {item} in Order", "click Save"],
    }
    brain, rec, _ = connected(settings, [], llm=Script(json.dumps(plan)))
    drives = []

    async def fake_drive(task, window, come_back=False):
        drives.append(task)

    brain._drive = fake_drive
    job = await brain.jobs.start(
        "ditto",
        "Repeat",
        params={"recording": str(rec_dir / "watch_x.json"), "items": ["A-1", "A-2"]},
    )
    [card] = await until(rec, "confirm")
    assert "needs your mouse and keyboard" in card["reason"]
    brain.hub.resolve(card["request_id"], False)
    await until(rec, "request_closed")
    await brain.jobs.wait(job.id)
    assert drives == []  # said no: the mouse is never touched


async def test_nothing_to_watch_or_repeat(settings, tmp_path):
    brain, _rec, _ = connected(settings, [])
    assert "I wasn't watching" in await specialists.handle(brain, "watch_me", {"action": "stop"})
    assert "nothing is being watched" in await specialists.handle(
        brain, "watch_me", {"action": "cancel"}
    )
    assert "nothing of mine to undo" in await specialists.handle(
        brain, "watch_me", {"action": "undo"}
    )
    assert "no folder" in await specialists.handle(
        brain, "watch_me", {"action": "start", "folder": "Nowhere Land"}
    )
