"""Known vulnerabilities in what Pyxi ships. The release script runs this; it fails on high or
critical.

    python scripts/security_check.py [--skip-pip] [--skip-npm] [--ignore ID ...]

- Python: pip-audit on the bundled Python (desktop/build/python-full, the pip-built copy the slim
  bundle is made from; without a build, the Python running this). Packages the slim bundle drops
  (pip, setuptools, wheel) are listed but don't count. Severity comes from the GitHub advisory
  (via osv.dev); a finding with no known severity counts as high, so nothing slips through.
- npm: ``npm audit --omit=dev`` in desktop/ and cloud/, plus Electron itself in desktop/ (it ships
  in the installer, though npm lists it as a dev dependency).

pip-audit runs from its own virtualenv (.venv-audit, made on first use), never Pyxi's.
``--ignore ID`` accepts a finding (PYSEC-, GHSA-, CVE- or GHSA from npm) after you've judged it:
say why in the release notes.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
FULL = ROOT / "desktop" / "build" / "python-full"
AUDIT_VENV = ROOT / ".venv-audit"
BLOCKING = {"high", "critical"}
try:  # stay in step with what the slim bundle leaves out
    sys.path.insert(0, str(Path(__file__).parent))
    from build_installer import DROP_DISTS as NOT_SHIPPED
except Exception:
    NOT_SHIPPED = {"pip", "setuptools", "wheel"}


@dataclass
class Finding:
    where: str
    package: str
    version: str
    id: str
    severity: str  # low, moderate, high, critical, unknown
    fix: str
    ships: bool = True

    @property
    def blocking(self) -> bool:
        return self.ships and (self.severity in BLOCKING or self.severity == "unknown")

    def line(self) -> str:
        tag = "" if self.ships else "  (not shipped)"
        return f"  [{self.severity:8}] {self.where}: {self.package} {self.version} {self.id} fix: {self.fix or 'none yet'}{tag}"


# ---------------------------------------------------------------- Python
def pip_audit_exe() -> Path:
    exe = AUDIT_VENV / ("Scripts/pip-audit.exe" if os.name == "nt" else "bin/pip-audit")
    if not exe.exists():
        print(f"setting up pip-audit in {AUDIT_VENV.name} (once)")
        subprocess.run([sys.executable, "-m", "venv", str(AUDIT_VENV)], check=True)
        py = AUDIT_VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        subprocess.run(
            [str(py), "-m", "pip", "install", "-q", "--disable-pip-version-check", "pip-audit"],
            check=True,
        )
    return exe


def site_packages() -> Path:
    built = FULL / "Lib" / "site-packages"
    if built.is_dir():
        return built
    print("no installer build yet: auditing the Python running this instead")
    import sysconfig

    return Path(sysconfig.get_paths()["purelib"])


def osv_severity(vuln_id: str, aliases: Iterable[str], client: httpx.Client) -> str:
    """The GitHub advisory's rating (low/moderate/high/critical), or 'unknown'."""
    for vid in [*[a for a in aliases if a.startswith("GHSA-")], vuln_id]:
        try:
            r = client.get(f"https://api.osv.dev/v1/vulns/{vid}")
        except httpx.HTTPError:
            continue
        if r.status_code != 200:
            continue
        rating = str((r.json().get("database_specific") or {}).get("severity") or "").lower()
        if rating:
            return "moderate" if rating == "medium" else rating
    return "unknown"


def pip_findings(report: dict, severity: Callable[[str, list[str]], str]) -> list[Finding]:
    out: dict[tuple[str, str], Finding] = {}
    for dep in report.get("dependencies", []):
        for v in dep.get("vulns", []):
            key = (dep["name"].lower(), v["id"])
            if key in out:  # pip-audit can list one advisory twice
                continue
            out[key] = Finding(
                "python",
                dep["name"],
                dep["version"],
                v["id"],
                severity(v["id"], v.get("aliases") or []),
                ", ".join(v.get("fix_versions") or []),
                ships=dep["name"].lower() not in NOT_SHIPPED,
            )
    return list(out.values())


def audit_python() -> tuple[list[Finding], list[str]]:
    target = site_packages()
    print(f"pip-audit: {target}")
    run = subprocess.run(
        [str(pip_audit_exe()), "--path", str(target), "-f", "json", "--progress-spinner", "off"],
        capture_output=True,
        text=True,
    )
    if run.returncode not in (0, 1) or not run.stdout.strip():
        raise SystemExit(f"STOP: pip-audit failed: {run.stderr.strip()[-400:]}")
    report = json.loads(run.stdout)
    skipped = [
        f"{d['name']}: not on PyPI, audited through its dependencies"
        for d in report.get("dependencies", [])
        if d.get("skip_reason")
    ]
    with httpx.Client(timeout=20) as client:
        findings = pip_findings(report, lambda i, a: osv_severity(i, a, client))
    return findings, skipped


# ---------------------------------------------------------------- npm
def npm_findings(report: dict, where: str, only: set[str] | None = None) -> list[Finding]:
    out = []
    for name, v in (report.get("vulnerabilities") or {}).items():
        if only is not None and name not in only:
            continue
        ids = [
            str(s.get("url", "")).rsplit("/", 1)[-1] or str(s.get("source"))
            for s in v.get("via", [])
            if isinstance(s, dict)
        ]
        fix = v.get("fixAvailable")
        fix_text = (
            f"{fix.get('name')} {fix.get('version')}"
            if isinstance(fix, dict)
            else ("yes" if fix else "")
        )
        out.append(
            Finding(
                where, name, str(v.get("range", "")), ",".join(ids) or "-", v["severity"], fix_text
            )
        )
    return out


def npm_audit(folder: Path, *extra: str) -> dict:
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if npm is None:
        raise SystemExit("STOP: npm not found")
    run = subprocess.run(
        [npm, "audit", "--json", *extra], cwd=folder, capture_output=True, text=True
    )
    try:
        return json.loads(run.stdout)
    except ValueError:
        raise SystemExit(
            f"STOP: npm audit failed in {folder.name}: {run.stderr.strip()[-400:]}"
        ) from None


def audit_npm() -> list[Finding]:
    findings = []
    for folder in ("desktop", "cloud"):
        print(f"npm audit --omit=dev: {folder}/")
        findings += npm_findings(npm_audit(ROOT / folder, "--omit=dev"), folder)
    print("npm audit: Electron (ships in the installer)")
    findings += npm_findings(npm_audit(ROOT / "desktop"), "desktop", only={"electron"})
    return findings


# ---------------------------------------------------------------- report
def judge(findings: list[Finding], ignore: set[str]) -> list[Finding]:
    """The findings that stop a release."""
    return [f for f in findings if f.blocking and not ({f.id, *f.id.split(",")} & ignore)]


def main(argv: list[str]) -> int:
    ignore = {argv[i + 1] for i, a in enumerate(argv[:-1]) if a == "--ignore"}
    findings: list[Finding] = []
    notes: list[str] = []
    if "--skip-pip" not in argv:
        py, notes = audit_python()
        findings += py
    if "--skip-npm" not in argv:
        findings += audit_npm()
    order = {"critical": 0, "high": 1, "unknown": 2, "moderate": 3, "low": 4}
    findings.sort(key=lambda f: (not f.ships, order.get(f.severity, 5), f.package))
    for note in notes:
        print(f"  note: {note}")
    if findings:
        print(f"{len(findings)} known vulnerabilities:")
        for f in findings:
            print(f.line())
    stop = judge(findings, ignore)
    if stop:
        print(
            f"STOP: {len(stop)} high or critical in what ships. Update them, or --ignore ID with a reason."
        )
        return 1
    print("security check passed: nothing high or critical in what ships")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
