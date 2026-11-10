"""scripts/release.py runs the automated security checks and prints the manual ones (without
running a release here: its steps are called one by one)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import release  # noqa: E402


def test_the_manual_checklist_is_printed_from_the_doc():
    text = release.manual_checklist()
    assert text.startswith("Before you publish, by hand")
    assert "e2e/security.mjs" in text and "injection_scenarios.py" in text
    assert "## Automated" not in text  # only the part a person does


def test_the_automated_checks_cover_python_desktop_and_cloud(monkeypatch):
    ran: list[tuple[tuple[str, ...], Path]] = []
    monkeypatch.setattr(release, "sh", lambda *cmd, **kw: ran.append((cmd, Path(kw.get("cwd", ROOT)))))
    release.automated_checks()
    assert ran[0][0][1:] == ("-m", "pytest", "-q")
    rest = [(c[1:], d.name) for c, d in ran[1:]]
    assert rest == [(("run", "typecheck"), "desktop"), (("test",), "desktop"), (("test",), "cloud")]


def test_checklist_flag_only_prints(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["release.py", "--checklist"])
    monkeypatch.setattr(release, "set_version", lambda v: (_ for _ in ()).throw(AssertionError("no release")))
    release.main()
    assert "Before you publish" in capsys.readouterr().out


def test_the_key_scan_and_audit_run_before_publishing():
    src = (ROOT / "scripts" / "release.py").read_text(encoding="utf-8")
    body = src[src.index("def main()") :]
    steps = ("automated_checks()", "check_clean_build()", "security_check.py", "manual_checklist()", "publish(v, notes, firefox)")
    order = [body.rindex(s) for s in steps]  # the last mention: the release run itself
    assert order == sorted(order)
