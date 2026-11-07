"""The activity log: what Pyxi did, kept locally for 30 days, with undo for tidies, Ditto's
renames and Patch's fixes."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from pyxi import tidy
from pyxi.agents import ditto
from pyxi.audit import AuditLog

DAY = 86400


def test_entries_are_kept_newest_first_for_thirty_days(tmp_path):
    log = AuditLog(tmp_path / "audit.jsonl", 30)
    log.add("sent", "Send an email to sam@uni.edu", agent="Pyxi", now=1000)
    log.add("moved", "Moved 3 files", now=2000)
    assert [e["what"] for e in log.recent(now=3000)] == ["Moved 3 files", "Send an email to sam@uni.edu"]
    assert [e["what"] for e in log.recent(now=1000 + 30 * DAY + 1)] == ["Moved 3 files"]
    assert len((tmp_path / "audit.jsonl").read_text().splitlines()) == 1  # forgotten on disk too


def test_undo_a_tidy(tmp_path):
    folder = tmp_path / "Desktop"
    folder.mkdir()
    for n in ("a.pdf", "b.jpg"):
        (folder / n).write_text(n)
    plan = tidy.make_plan(folder, [{"folder": "Documents", "files": ["a.pdf"]}, {"folder": "Pictures", "files": ["b.jpg"]}])
    moved, _, tidy_log = tidy.apply(plan, tmp_path / "tidy")
    log = AuditLog(tmp_path / "audit.jsonl")
    entry = log.add("moved", f"Moved {moved} files", undo={"type": "tidy", "log": str(tidy_log)})
    assert (folder / "Documents" / "a.pdf").exists()
    assert "Put 2 files back" in log.undo(entry["id"])
    assert (folder / "a.pdf").exists() and (folder / "b.jpg").exists()
    [undid, done] = log.recent()
    assert undid["kind"] == "undid" and done["undone"] and not done["can_undo"]
    assert log.undo(entry["id"]) == "That was already undone."


def test_undo_dittos_renames(tmp_path):
    old, new = tmp_path / "invoice 1.pdf", tmp_path / "2026-11-01 Acme.pdf"
    old.write_text("x")
    done, undo_log = ditto.do_files([(old, new)], tmp_path / "undo")
    log = AuditLog(tmp_path / "audit.jsonl")
    entry = log.add("moved", "Renamed 1 file", undo={"type": "ditto", "log": str(undo_log)})
    assert done == 1 and new.exists()
    assert log.undo(entry["id"]) == "Put 1 file back as they were."
    assert old.exists() and not new.exists()


def test_undo_a_patch_fix_keeps_both_versions(tmp_path):
    script, backup = tmp_path / "report.py", tmp_path / "report.py.bak"
    script.write_text("fixed = True\n")
    backup.write_text("fixed = False\n")
    log = AuditLog(tmp_path / "audit.jsonl")
    entry = log.add("wrote", "Changed line 1 of report.py", undo={"type": "patch", "path": str(script), "backup": str(backup)})
    assert "back to how it was" in log.undo(entry["id"])
    assert script.read_text() == "fixed = False\n"
    assert (tmp_path / "report.py.pyxi-undone").read_text() == "fixed = True\n"  # nothing deleted
    assert not backup.exists()


def test_an_entry_without_undo_says_so(tmp_path):
    log = AuditLog(tmp_path / "audit.jsonl")
    e = log.add("sent", "Posted on LinkedIn")
    assert not log.recent()[0]["can_undo"] and "no undo" in log.undo(e["id"])
    assert "no longer" in log.undo("act_nope")


async def test_the_brain_logs_what_it_sends_and_moves(settings, tmp_path):
    from conftest import FakeScreen
    from pyxi.companion.brain import Brain
    from pyxi.connectors import Outgoing
    from pyxi.demo import DemoLLM
    from pyxi.hub import Hub

    brain = Brain(settings, Hub(), llm=DemoLLM(), screen=FakeScreen())

    async def allow(kind, timeout=300, **data):
        return True

    brain.hub.request = allow  # type: ignore[method-assign]

    async def run():
        return "Sent to Sam."

    await brain._approve(Outgoing("Send an email to sam@uni.edu", "To: sam@uni.edu\n\nHi", run, channel="email", payload="Hi"))
    folder = tmp_path / "Downloads"
    folder.mkdir()
    (folder / "a.pdf").write_text("x")
    plan = tidy.make_plan(folder, [{"folder": "PDFs", "files": ["a.pdf"]}])
    await brain._tidy_approved(plan, settings.data_dir / "tidy")
    kinds = [(e["kind"], e["what"], e["can_undo"]) for e in brain.audit.recent()]
    assert kinds[0][0] == "moved" and kinds[0][2] and kinds[1] == ("sent", "Send an email to sam@uni.edu", False)
    assert brain.audit.recent()[1]["detail"] == "Hi"


def test_the_panel_reads_and_undoes_over_the_socket(settings, tmp_path):
    from conftest import FakeScreen
    from pyxi.companion.brain import Brain
    from pyxi.demo import DemoLLM
    from pyxi.hub import Hub
    from pyxi.server import create_app

    brain = Brain(settings, Hub(), llm=DemoLLM(), screen=FakeScreen())
    old, new = tmp_path / "a.txt", tmp_path / "b.txt"
    old.write_text("x")
    _, undo_log = ditto.do_files([(old, new)], tmp_path / "undo")
    entry = brain.audit.add("moved", "Renamed 1 file", agent="Ditto", undo={"type": "ditto", "log": str(undo_log)})
    app = create_app(settings, demo=True, brain=brain, key="k")
    with TestClient(app) as client, client.websocket_connect("/ws?key=k") as ws:
        ws.receive_text()
        ws.send_text(json.dumps({"type": "audit_get"}))
        got = json.loads(ws.receive_text())
        assert got["type"] == "audit" and got["entries"][0]["can_undo"]
        ws.send_text(json.dumps({"type": "audit_undo", "id": entry["id"]}))
        assert json.loads(ws.receive_text())["message"] == "Put 1 file back as they were."
        after = json.loads(ws.receive_text())["entries"]
        assert after[0]["kind"] == "undid" and after[1]["undone"]
    assert old.exists()
