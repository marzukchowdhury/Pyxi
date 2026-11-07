"""scripts/security_check.py: what stops a release (no network: canned audit reports)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import security_check as sc

PIP_REPORT = {
    "dependencies": [
        {"name": "pip", "version": "25.0.1", "vulns": [
            {"id": "PYSEC-1", "aliases": ["GHSA-aaaa"], "fix_versions": ["26.1"]},
            {"id": "PYSEC-1", "aliases": ["GHSA-aaaa"], "fix_versions": ["26.1"]},
        ]},
        {"name": "Pillow", "version": "10.0.0", "vulns": [
            {"id": "PYSEC-2", "aliases": ["GHSA-bbbb", "CVE-2026-1"], "fix_versions": ["10.3.0"]},
        ]},
        {"name": "httpx", "version": "0.28.1", "vulns": []},
        {"name": "otto-browser-buddy", "skip_reason": "Dependency not found on PyPI"},
    ]
}  # fmt: skip


def test_python_findings_dedupe_and_skip_what_doesnt_ship():
    rating = {"PYSEC-1": "critical", "PYSEC-2": "moderate"}
    found = sc.pip_findings(PIP_REPORT, lambda i, a: rating[i])
    assert [(f.package, f.id, f.severity, f.ships) for f in found] == [
        ("pip", "PYSEC-1", "critical", False),  # the slim bundle drops pip
        ("Pillow", "PYSEC-2", "moderate", True),
    ]
    assert sc.judge(found, set()) == []


def test_high_critical_or_unrated_in_what_ships_stops_the_release():
    found = sc.pip_findings(PIP_REPORT, lambda i, a: "high" if i == "PYSEC-2" else "low")
    assert [f.id for f in sc.judge(found, set())] == ["PYSEC-2"]
    assert sc.judge(found, {"PYSEC-2"}) == []  # judged and accepted
    unrated = sc.pip_findings(PIP_REPORT, lambda i, a: "unknown")
    assert [f.id for f in sc.judge(unrated, set())] == ["PYSEC-2"]


def test_npm_findings_and_electron_only_filter():
    report = {
        "vulnerabilities": {
            "electron": {"severity": "high", "range": "<45.3.0", "fixAvailable": True,
                         "via": [{"source": 1, "url": "https://github.com/advisories/GHSA-elec"}]},
            "minimatch": {"severity": "moderate", "range": "<9", "fixAvailable": {"name": "x", "version": "2"},
                          "via": ["glob"]},
        }
    }  # fmt: skip
    every = sc.npm_findings(report, "desktop")
    assert {(f.package, f.severity, f.id) for f in every} == {
        ("electron", "high", "GHSA-elec"),
        ("minimatch", "moderate", "-"),
    }
    only = sc.npm_findings(report, "desktop", only={"electron"})
    assert [f.package for f in only] == ["electron"] and sc.judge(only, set()) == only
    assert sc.judge(only, {"GHSA-elec"}) == []
