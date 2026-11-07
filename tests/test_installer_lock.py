"""The installer's Python is built from a hash-pinned lock (requirements/installer.txt)."""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "requirements" / "installer.txt"
sys.path.insert(0, str(ROOT / "scripts"))


def test_every_locked_package_has_an_exact_version_and_hashes():
    text = LOCK.read_text(encoding="utf-8")
    blocks = re.split(r"\n(?=[A-Za-z0-9])", text.strip())
    assert len(blocks) > 100
    for block in blocks:
        head = block.splitlines()[0]
        assert re.match(r"^[A-Za-z0-9_.-]+==\S+( ; [^\\]+)? \\$", head), head  # markers allowed
        assert "--hash=sha256:" in block, head
    assert re.search(r"^pip==(\d+)", text, re.M) and int(re.search(r"^pip==(\d+)", text, re.M).group(1)) >= 26
    assert "Users/" not in text and "Users\\" not in text  # no one's home folder in the repo


def test_the_lock_covers_what_pyxi_and_otto_ask_for():
    import build_installer

    if not (build_installer.OTTO / "pyproject.toml").exists():
        pytest.skip("Otto's checkout isn't beside this one")
    build_installer.check_lock()  # raises SystemExit if a dependency isn't locked
    deps = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]
    locked_in = (LOCK.with_suffix(".in")).read_text(encoding="utf-8").splitlines()
    assert all(d in locked_in for d in deps if not d.startswith("otto-browser-buddy"))


def test_the_build_installs_with_require_hashes(monkeypatch):
    import build_installer

    calls: list[list[str]] = []
    monkeypatch.setattr(build_installer, "check_lock", lambda: None)
    monkeypatch.setattr(build_installer, "run", lambda cmd, check=True: calls.append(cmd))
    build_installer.install_packages()
    installs = [c[c.index("install") + 1 :] for c in calls if "install" in c]
    assert installs[0][-3:] == ["--require-hashes", "-r", str(build_installer.LOCK)]
    assert all("--no-deps" in c for c in installs[1:])  # only the local checkouts, nothing unpinned


def test_a_dependency_added_without_relocking_stops_the_build(monkeypatch):
    import build_installer

    if not (build_installer.OTTO / "pyproject.toml").exists():
        pytest.skip("Otto's checkout isn't beside this one")
    monkeypatch.setattr(build_installer, "requirements", lambda: ["left-pad>=1.0"])
    with pytest.raises(SystemExit, match="aren't in the lock"):
        build_installer.check_lock()
