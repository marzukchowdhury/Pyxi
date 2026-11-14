"""Ditto, after the audit of the five specialists: what it says and when, the names it gives
files, what it keeps of what was typed, and the plans it refuses before a card.

Everything here is temp folders, a scripted model and a fake focus reader. Nothing is renamed
outside tmp_path, and no browser or mouse is touched.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from pyxi.agents import ditto
from pyxi.agents.jobs import Job
from pyxi.companion import answers, specialists
from pyxi.companion.brain import Brain
from test_answer_card import settle
from test_connectors_and_docs import Script
from test_mailwatch import connected, until


@pytest.fixture(autouse=True)
def no_wait_for_silence(monkeypatch):
    monkeypatch.setattr(Brain, "QUIET_BEFORE_SPEAKING_S", 0.0)


def said(rec) -> list[str]:
    return [e["text"] for e in rec.of("say")]


def watched(folder: Path | str, rec_dir: Path, name: str = "watch_x") -> Path:
    """A saved recording of one step, as `watch_me stop` leaves it."""
    recording = ditto.Recording(name, "rename invoices", str(folder), 0.0)
    recording.steps = [ditto.Step(0.0, "window", "explorer.exe", "Invoices")]
    return recording.save(rec_dir)


PLAN = {
    "summary": "Rename the remaining invoices like the first one.",
    "route": "files",
    "items": [{"item": "a.pdf", "to": "2026 A"}, {"item": "b.pdf", "to": "2026 B"}],
}


# ------------------------------------------------------------------ what is said, and when
def test_a_plan_waiting_on_a_card_is_not_called_done():
    """Audit: "Ditto's done." was said the moment the plan was ready, with the card still
    waiting and nothing renamed."""
    line = answers.spoken_line(
        "Ditto",
        ok=True,
        title="Ready to do 3 more",
        summary="Rename the invoices. (3 items, route files; waiting for their OK)",
        on_card=False,
        role="automation",
    )
    assert line == "Ready to do 3 more."


async def test_the_renaming_is_said_when_it_has_happened(settings, tmp_path):
    """Audit: after Allow, "Done 2 more" was a ten second notice and nothing else. The same
    happened live with another teammate: "I didn't see anything"."""
    folder = tmp_path / "Invoices"
    folder.mkdir()
    for n in ("a.pdf", "b.pdf"):
        (folder / n).write_bytes(b"%PDF")
    brain, rec, _ = connected(settings, [], llm=Script(json.dumps(PLAN)))
    await brain.jobs.start(
        "ditto", "Repeat", params={"recording": str(watched(folder, tmp_path / "watch"))}
    )
    [card] = await until(rec, "confirm")
    await settle(brain, rounds=15)
    assert said(rec) == ["Ready to do 2 more."]  # not "Ditto's done."
    assert sorted(p.name for p in folder.iterdir()) == ["a.pdf", "b.pdf"]
    brain.hub.resolve(card["request_id"], True)
    for _ in range(150):
        if "Done 2 more." in said(rec):
            break
        await asyncio.sleep(0.02)
    assert said(rec) == ["Ready to do 2 more.", "Done 2 more."]
    assert sorted(p.name for p in folder.iterdir()) == ["2026 A.pdf", "2026 B.pdf"]


# ------------------------------------------------------------------ the names it gives
def test_a_new_name_with_a_dot_in_it_keeps_the_extension(tmp_path):
    """Audit: "2026.10.09 Contoso Ltd" and "Acme Inc. invoice" were taken to have an
    extension already, and the files lost their .pdf."""
    folder = tmp_path / "Invoices"
    folder.mkdir()
    for n in ("invoice-1183.pdf", "scan 7.pdf", "c.pdf", "d.PDF"):
        (folder / n).write_bytes(b"%PDF")
    plan = ditto.Plan(
        "Rename",
        "files",
        [
            {"item": "invoice-1183.pdf", "to": "2026.10.09 Contoso Ltd"},
            {"item": "scan 7.pdf", "to": "Acme Inc. invoice"},
            {"item": "c.pdf", "to": "Already named.pdf"},  # theirs is kept, not doubled
            {"item": "d.PDF", "to": "Loud.pdf"},  # the same extension, whatever its case
        ],
    )
    moves = ditto.check_files(plan, folder)
    assert [d.name for _, d in moves] == [
        "2026.10.09 Contoso Ltd.pdf",
        "Acme Inc. invoice.pdf",
        "Already named.pdf",
        "Loud.pdf",
    ]


def test_each_batch_has_its_own_undo_log(tmp_path):
    """Audit: two batches in the same second wrote the same log, so the first could no
    longer be undone."""
    f = tmp_path / "f"
    f.mkdir()
    (f / "a.txt").write_text("a")
    (f / "b.txt").write_text("b")
    _, first = ditto.do_files([(f / "a.txt", f / "a2.txt")], tmp_path / "undo")
    _, second = ditto.do_files([(f / "b.txt", f / "b2.txt")], tmp_path / "undo")
    assert first != second and sorted([first, second]) == [first, second]  # newest sorts last
    assert ditto.undo_files(second) == 1 and ditto.undo_files(first) == 1
    assert sorted(p.name for p in f.iterdir()) == ["a.txt", "b.txt"]


def test_after_a_conversion_the_rest_are_the_files_it_was_made_from(tmp_path):
    """Audit: they saved a.docx as a.pdf once. The items offered were the other PDFs in the
    folder ("old scan.pdf"); b.docx and c.docx never were."""
    folder = tmp_path / "Letters"
    folder.mkdir()
    for n in ("a.docx", "b.docx", "c.docx", "d.docx"):
        (folder / n).write_bytes(b"PK")
    (folder / "old scan.pdf").write_bytes(b"%PDF")
    (folder / "d.pdf").write_bytes(b"%PDF")  # this one already has its PDF
    before = ditto.listing(folder)
    (folder / "a.pdf").write_bytes(b"%PDF")  # what they did once
    rec = ditto.Recording("w", "save each letter as a PDF", str(folder), 0.0)
    rec.changes = ditto.folder_changes(before, ditto.listing(folder))
    assert rec.changes == [{"op": "created", "to": "a.pdf"}]
    assert ditto.remaining(rec) == ["b.docx", "c.docx"]


# ------------------------------------------------------------------ what it keeps
async def test_a_card_number_typed_while_watching_is_never_kept(tmp_path):
    """Audit: a checkout shown once put the card number and the security code in the saved
    recording, the activity log and the prompt (they are ordinary fields, not password ones)."""
    seen = iter(
        [
            ditto.Focus("chrome.exe", "Checkout", "Full name", "Edit", "Alex Morgan"),
            ditto.Focus("chrome.exe", "Checkout", "Card number", "Edit", "4111 1111 1111 1111"),
            ditto.Focus("chrome.exe", "Checkout", "Security code", "Edit", "737"),
            ditto.Focus("chrome.exe", "Checkout", "Reference", "Edit", "4111111111111111"),
            ditto.Focus("chrome.exe", "Checkout", "Pay now", "Button"),
        ]
    )
    logged: list[ditto.Step] = []
    r = ditto.Recorder(
        "fill the form",
        "",
        out_dir=tmp_path / "watch",
        reader=lambda: next(seen, None),
        log=logged.append,
    )
    for _ in range(5):
        await r.tick()
    recording = await r.stop()
    saved = (tmp_path / "watch" / f"{recording.id}.json").read_text(encoding="utf-8")
    everywhere = saved + recording.text() + " ".join(s.line() for s in logged)
    # (Clock readings are numbers too: "1794555521.6737866" has 737 in it, and the test failed
    # one run in a dozen. They are taken out before looking for what was typed.)
    import re

    everywhere = re.sub(r"\d{9,}\.\d+", "", everywhere)
    assert "4111" not in everywhere and "737" not in everywhere
    assert 'typed "Alex Morgan" into edit "Full name"' in recording.text()  # the rest is kept
    assert 'typed something private (not kept) into edit "Card number"' in recording.text()
    assert ditto.private("Reference", "4111111111111111")  # by what was typed, not the name
    assert not ditto.private("Name", "2026-10-03 Northwind Supplies.pdf")


async def test_a_field_that_only_mentions_a_card_or_a_pin_is_still_learned(tmp_path):
    """Review: any field with "card" or "pin" in its name had its text hidden, so a Trello
    card's title or a Pinterest board's name, the very thing being shown, was never seen."""
    learned = [
        "Enter a title for this card",
        "Card title",
        "Add a card",
        "Pinterest board name",
        "Pin title",
        "Shipping address",
        "Business card holder",
        "Spinner size",
        "Name on card",
        "Single sign-on name",
    ]
    hidden = [
        "Card number",
        "Credit card",
        "Debit card",
        "Card no.",
        "CVV",
        "CVC2",
        "Security code",
        "Expiry date",
        "Expiration (MM/YY)",
        "PIN",
        " pin ",
        "PIN code",
        "PIN number",
        "SSN",
        "Social Security number",
        "SIN",
        "Password",
        "Confirm password",
        "Passcode",
    ]
    assert [n for n in learned if ditto.private(n, "Q3 launch plan")] == []
    assert [n for n in hidden if not ditto.private(n, "x")] == []
    # What was typed is still checked, whatever the field is called.
    assert ditto.private("Enter a title for this card", "4111 1111 1111 1111")
    seen = iter(
        [
            ditto.Focus("chrome.exe", "Board | Trello", "Enter a title for this card", "Edit", "Q3 launch plan"),
            ditto.Focus("chrome.exe", "Board | Trello", "Add card", "Button"),
        ]
    )  # fmt: skip
    r = ditto.Recorder("add a card per row", "", out_dir=tmp_path / "watch", reader=lambda: next(seen, None))
    for _ in range(2):
        await r.tick()
    recording = await r.stop()
    assert 'typed "Q3 launch plan" into edit "Enter a title for this card"' in recording.text()


# ------------------------------------------------------------------ plans it refuses
async def test_a_job_with_no_recording_says_what_it_needs(settings):
    """Audit: handed a task directly (delegate), Ditto failed with a KeyError and Pyxi said
    "Ditto couldn't finish that. 'recording'." """
    brain, rec, _ = connected(settings, [])
    job = await brain.jobs.start("ditto", "Rename my invoices like the first one")
    await brain.jobs.wait(job.id)
    [ready] = await until(rec, "job_ready")
    assert not ready["ok"] and ready["title"] == "Show me once first"
    assert "watch you do it once" in ready["body"] and "recording" not in ready["body"]
    assert not rec.of("confirm")


@pytest.mark.parametrize(
    ("plan", "title"),
    [
        (
            {"summary": "Rename it.", "route": "files", "items": [{"item": "x.pdf", "to": "y"}]},
            "Which folder are they in?",
        ),
        (
            {"summary": "Fill the form.", "route": "web", "items": [{"item": "x.pdf"}]},
            "I didn't get the steps",
        ),
        (
            {"summary": "Type it in.", "route": "mouse", "items": [{"item": "x.pdf"}], "steps": []},
            "I didn't get the steps",
        ),
    ],
)
async def test_a_plan_that_cannot_be_done_never_reaches_the_card(
    settings, tmp_path, monkeypatch, plan, title
):
    """Audit: with no folder, an approved rename was done in Pyxi's own working folder; a web
    or mouse plan with no steps sent Scout, or the hands, off with nothing to do."""
    cwd = tmp_path / "pyxi_install_dir"
    cwd.mkdir()
    (cwd / "x.pdf").write_bytes(b"%PDF")
    monkeypatch.chdir(cwd)
    drives: list[str] = []
    brain, rec, _ = connected(settings, [], llm=Script(json.dumps(plan)))

    async def fake_drive(task, window, come_back=False):
        drives.append(task)

    brain._drive = fake_drive
    job = await brain.jobs.start(
        "ditto",
        "Repeat",
        params={"recording": str(watched("", tmp_path / "watch")), "items": ["x.pdf"]},
    )
    await brain.jobs.wait(job.id)
    [ready] = await until(rec, "job_ready")
    await settle(brain)
    assert not ready["ok"] and ready["title"] == title
    assert not rec.of("confirm") and drives == []
    assert [p.name for p in cwd.iterdir()] == ["x.pdf"]  # nothing in Pyxi's folder moved
    assert [j.agent_name for j in brain.jobs.jobs.values()] == ["Ditto"]  # no Scout started


async def test_a_conversion_pixel_cannot_do_is_refused_before_the_card(settings, tmp_path):
    folder = tmp_path / "Letters"
    folder.mkdir()
    (folder / "a.docx").write_bytes(b"PK")
    plan = {
        "summary": "Turn each into a hologram.",
        "route": "convert",
        "operation": "to_hologram",
        "items": [{"item": "a.docx"}],
    }
    brain, rec, _ = connected(settings, [], llm=Script(json.dumps(plan)))
    job = await brain.jobs.start(
        "ditto", "Repeat", params={"recording": str(watched(folder, tmp_path / "watch"))}
    )
    await brain.jobs.wait(job.id)
    [ready] = await until(rec, "job_ready")
    assert ready["title"] == "I can't repeat that conversion" and not rec.of("confirm")


async def test_a_plan_from_elsewhere_with_no_folder_gets_no_card(settings, tmp_path, monkeypatch):
    """The same check where the plan is carried out: a job's data can come from a hand-off."""
    cwd = tmp_path / "pyxi_install_dir"
    cwd.mkdir()
    (cwd / "report.pdf").write_bytes(b"%PDF")
    monkeypatch.chdir(cwd)
    brain, rec, _ = connected(settings, [])
    plan = ditto.Plan("Rename it.", "files", [{"item": "report.pdf", "to": "2026 report.pdf"}])
    job = Job(agent_id="ditto", agent_name="Ditto", role="automation", task="Repeat", status="done")
    job.data = {"plan": plan.__dict__, "folder": ""}
    assert await specialists.after_repeat(brain, job) == "bad plan"
    [ready] = rec.of("job_ready")
    assert ready["title"] == "I couldn't repeat that" and "which folder" in ready["body"]
    assert not rec.of("confirm") and [p.name for p in cwd.iterdir()] == ["report.pdf"]


async def test_scout_does_the_items_three_at_a_time(settings):
    """Audit: a web plan of sixty items started sixty browsers at once."""
    brain, _rec, _ = connected(settings, [])
    running, most, started = 0, 0, []

    class Started:
        def __init__(self, n: int) -> None:
            self.id, self.status = f"job_{n}", "done"

    async def fake_start(agent, task, **kw):
        nonlocal running, most
        started.append((agent, task, kw.get("start_url")))
        running += 1
        most = max(most, running)
        return Started(len(started))

    async def fake_wait(job_id):
        nonlocal running
        await asyncio.sleep(0.01)
        running -= 1
        return Started(0)

    brain.jobs.start, brain.jobs.wait = fake_start, fake_wait
    tasks = [f"Repeat for row {i}" for i in range(20)]
    assert await specialists.scouts_in_turn(brain, tasks, "https://forms.example/new") == 20
    assert most == ditto.SCOUTS_AT_ONCE == 3
    assert [t for _, t, _ in started] == tasks and started[0][2] == "https://forms.example/new"


async def test_stopping_one_scout_stops_the_ones_still_waiting(settings):
    brain, _rec, _ = connected(settings, [])
    started = []

    class Stopped:
        id, status = "job_s", "stopped"

    async def fake_start(agent, task, **kw):
        started.append(task)
        return Stopped()

    async def fake_wait(job_id):
        await asyncio.sleep(0.01)
        return Stopped()

    brain.jobs.start, brain.jobs.wait = fake_start, fake_wait
    await specialists.scouts_in_turn(brain, [f"row {i}" for i in range(20)], None)
    assert len(started) == ditto.SCOUTS_AT_ONCE  # the first three, and no more
