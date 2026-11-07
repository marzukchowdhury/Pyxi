"""Patch, the coding teammate: the failing line found from a real error, a small fix checked
here, and the file changed only after the approval card (with a backup).

The Python error is real: the sample script is run and its traceback read. The model is
scripted, so nothing leaves the PC.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from pyxi.agents import patch
from pyxi.companion import specialists
from test_connectors_and_docs import Script
from test_mailwatch import connected, until

SCRIPT = """import json


def load(path):
    with open(path) as f:
        return json.load(f)


def total(rows):
    return sum(r["amount"] for r in rows)


rows = [{"amount": 12.5}, {"amount": "7"}]
print("Total:", total(rows))
"""

FIX = {
    "explain": 'One of the amounts is the text "7", not a number, so Python can\'t add it to 12.5.',
    "line": 10,
    "find": '    return sum(r["amount"] for r in rows)',
    "replace": '    return sum(float(r["amount"]) for r in rows)',
    "why": "Turns each amount into a number before adding it up.",
}


def broken(tmp_path: Path) -> tuple[Path, str]:
    path = tmp_path / "My Scripts" / "report.py"
    path.parent.mkdir()
    path.write_text(SCRIPT, encoding="utf-8")
    run = subprocess.run([sys.executable, str(path)], capture_output=True, text=True, timeout=60)
    assert run.returncode != 0
    return path, run.stderr


def test_the_deepest_line_in_their_own_file_comes_first(tmp_path):
    _path, err = broken(tmp_path)
    assert "TypeError" in err
    found = patch.locations(err)
    assert [(Path(f.path).name, f.line) for f in found] == [("report.py", 10), ("report.py", 14)]
    # Library frames are never "their" line.
    lib = 'Traceback (most recent call last):\n  File "C:\\Python312\\Lib\\site-packages\\requests\\api.py", line 9, in get\n'
    assert patch.locations(lib + err)[0].line == 10


def test_node_typescript_and_compiler_formats(tmp_path):
    app = tmp_path / "My Scripts" / "app.js"
    app.parent.mkdir()
    app.write_text("const x = 1;\nconsole.log(y);\n", encoding="utf-8")
    node = f"ReferenceError: y is not defined\n    at Object.<anonymous> ({app}:2:13)\n    at node:internal/main:1:1"
    assert [(Path(f.path).name, f.line) for f in patch.locations(node)] == [("app.js", 2)]
    ts = tmp_path / "src" / "util.ts"
    ts.parent.mkdir()
    ts.write_text("let a: number = 'x';\n", encoding="utf-8")
    tsc = "src/util.ts(1,5): error TS2322: Type 'string' is not assignable to type 'number'."
    assert [(Path(f.path).name, f.line) for f in patch.locations(tsc, cwd=tmp_path)] == [
        ("util.ts", 1)
    ]
    assert patch.locations("Something went wrong") == []
    assert patch.locations(f"{tmp_path / 'missing.py'}:3") == []


def test_a_fix_is_checked_before_anyone_sees_it(tmp_path):
    path = tmp_path / "a.py"
    text = "x = 1\nx = 1\ny = 2\n"
    assert patch.check(path, text, "x = 1", "x = 2") == "the text to replace appears more than once"
    assert patch.check(path, text, "z = 3", "z = 4") == "the text to replace isn't in the file"
    assert patch.check(path, text, "y = 2", "y = (2").startswith("the fixed file wouldn't run")
    assert patch.check(path, text, "y = 2", "y = 2") == "the fix doesn't change anything"
    assert patch.check(path, text, "", "") == "no fix offered"
    big = "\n".join(f"v{i} = {i}" for i in range(40))
    assert patch.check(path, big, big, big.replace("=", "==")) == "that's more than a small fix"
    assert patch.check(path, text, "y = 2", "y = 3") is None
    assert (
        patch.check(tmp_path / "c.json", '{"a": 1}', '"a": 1', '"a": 1,')
        == "the fixed file isn't valid JSON"
    )
    # The model dropped the file's trailing spaces: still the same lines.
    assert (
        patch.normalise("def f():   \n    return 1\n", "def f():\n    return 1")
        == "def f():   \n    return 1"
    )


def test_applying_keeps_a_backup_and_the_line_endings(tmp_path):
    path = tmp_path / "w.py"
    path.write_bytes(b"a = 1\r\nb = 'x' + 1\r\n")
    fix = patch.Fix(str(path), 2, "", "b = 'x' + 1", "b = 'x' + str(1)")
    backup = patch.apply(fix)
    assert path.read_bytes() == b"a = 1\r\nb = 'x' + str(1)\r\n"
    assert backup.name == "w.py.bak" and backup.read_bytes() == b"a = 1\r\nb = 'x' + 1\r\n"
    with pytest.raises(RuntimeError, match="changed since"):
        patch.apply(fix)  # it's already been applied: the old text is gone


async def test_pyxi_finds_the_line_and_fixes_it_only_after_the_card(settings, tmp_path):
    path, err = broken(tmp_path)
    brain, rec, _ = connected(settings, [], llm=Script(json.dumps(FIX), json.dumps(FIX)))
    out = await specialists.handle(brain, "fix_code", {"error": err})
    assert out.startswith("Started job_") and "Patch" in out and "report.py" in out
    [ready] = await until(rec, "job_ready")
    assert ready["agent"] == "Patch" and ready["title"] == "Found it: line 10 of report.py"
    assert "nothing changes until you allow it" in ready["body"]
    prompt = brain.llm.prompts[0]
    assert "TypeError" in prompt and '   10|     return sum(r["amount"] for r in rows)' in prompt
    [card] = await until(rec, "confirm")
    assert card["action"] == "Apply this fix to report.py?"
    assert '-    return sum(r["amount"] for r in rows)' in card["reason"]
    assert '+    return sum(float(r["amount"]) for r in rows)' in card["reason"]
    brain.hub.resolve(card["request_id"], False)  # no: nothing changes
    await until(rec, "request_closed")
    assert path.read_text(encoding="utf-8") == SCRIPT and not list(path.parent.glob("*.bak"))
    # Again, and this time they allow it.
    await specialists.handle(brain, "fix_code", {"error": err})
    cards = await until(rec, "confirm", 2)
    brain.hub.resolve(cards[1]["request_id"], True)
    done = []
    for _ in range(100):
        done = [r for r in rec.of("job_ready") if r["title"] == "Fixed report.py"]
        if done:
            break
        await __import__("asyncio").sleep(0.02)
    assert done and "report.py.bak" in done[0]["body"]
    run = subprocess.run([sys.executable, str(path)], capture_output=True, text=True, timeout=60)
    assert run.returncode == 0 and run.stdout.strip() == "Total: 19.5"
    assert (path.parent / "report.py.bak").read_text(encoding="utf-8") == SCRIPT


async def test_no_fix_offered_still_explains(settings, tmp_path):
    path, err = broken(tmp_path)
    reply = {
        **FIX,
        "find": "",
        "explain": "The data file has text where numbers belong; fix the data.",
    }
    brain, rec, _ = connected(settings, [], llm=Script(json.dumps(reply)))
    await specialists.handle(brain, "fix_code", {"error": err})
    [ready] = await until(rec, "job_ready")
    assert (
        ready["title"] == "The error is at line 10 of report.py" and "fix the data" in ready["body"]
    )
    await brain.jobs.wait_all()
    assert not rec.of("confirm") and path.read_text(encoding="utf-8") == SCRIPT


async def test_unknown_files_answer_straight_away(settings, tmp_path):
    brain, _rec, _ = connected(settings, [])
    assert "doesn't name a file" in await specialists.handle(
        brain, "fix_code", {"error": "Segmentation fault"}
    )
    assert "no file at" in await specialists.handle(
        brain, "fix_code", {"error": "x", "file": str(tmp_path / "no.py")}
    )
    pic = tmp_path / "shot.png"
    pic.write_bytes(b"x")
    assert "isn't a script" in await specialists.handle(
        brain, "fix_code", {"error": "x", "file": str(pic)}
    )
    assert not brain.jobs.jobs
