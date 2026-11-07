"""Pin the installer's Python dependencies, with hashes.

    python scripts/lock_installer_deps.py            # same versions as the last full build
    python scripts/lock_installer_deps.py --upgrade  # newest versions that satisfy pyproject

Writes requirements/installer.in (what Pyxi and Otto ask for, from their pyproject.toml files)
and requirements/installer.txt (every package, exact version and sha256 hashes, compiled with
pip-tools). scripts/build_installer.py installs the lock with ``--require-hashes``: a package
that changed on PyPI, or one nobody pinned, stops the build.

By default the versions are the ones in desktop/build/python-full (the last full build, which was
tested), so locking changes nothing that ships. pip itself is pinned too, at a current version,
because it runs at build time. Dependabot (.github/dependabot.yml) proposes updates to the lock.
pip-tools runs from .venv-audit (the same tool venv as scripts/security_check.py), never Pyxi's.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OTTO = ROOT.parent / "otto"
OUT = ROOT / "requirements"
FULL = ROOT / "desktop" / "build" / "python-full" / "python.exe"
TOOLS = ROOT / ".venv-audit"
PIP_MIN = "pip>=26.2"  # pip runs at build time; older ones have known advisories
LOCAL = ("pyxi-buddy", "otto-browser-buddy")
# Not held at the built version: the build's was yanked on PyPI (5.15.0; 5.15.1 has a fix).
REFRESH = ("pypdfium2",)


def deps(pyproject: Path) -> list[str]:
    return tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["dependencies"]


def tool(name: str) -> Path:
    exe = TOOLS / ("Scripts" if os.name == "nt" else "bin") / (name + (".exe" if os.name == "nt" else ""))
    if not exe.exists():
        if not TOOLS.exists():
            subprocess.run([sys.executable, "-m", "venv", str(TOOLS)], check=True)
        py = TOOLS / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        subprocess.run([str(py), "-m", "pip", "install", "-q", "pip-tools"], check=True)
    return exe


def main() -> None:
    upgrade = "--upgrade" in sys.argv
    wanted = [d for d in deps(ROOT / "pyproject.toml") if not d.startswith(LOCAL)]
    wanted += [d for d in deps(OTTO / "pyproject.toml") if d not in wanted]
    OUT.mkdir(exist_ok=True)
    src = OUT / "installer.in"
    src.write_text(
        "# What the installer's Python needs: Pyxi's and Otto's dependencies (their pyproject.toml),\n"
        "# Otto and Pyxi themselves come from the local checkouts. Lock: scripts/lock_installer_deps.py\n"
        + "\n".join([*wanted, PIP_MIN])
        + "\n",
        encoding="utf-8",
    )
    args = [str(tool("pip-compile")), "--generate-hashes", "--allow-unsafe", "--strip-extras", "--quiet",
            "--no-header", "--output-file", str(OUT / "installer.txt"), str(src)]  # fmt: skip
    with tempfile.TemporaryDirectory() as tmp:
        if not upgrade and FULL.exists():
            frozen = subprocess.run(
                [str(FULL), "-m", "pip", "freeze", "--all"], capture_output=True, text=True, check=True
            ).stdout.splitlines()
            skip = (*LOCAL, "pip==", *(f"{p}==" for p in REFRESH))
            keep = [ln for ln in frozen if "==" in ln and not ln.lower().startswith(skip)]
            pins = Path(tmp) / "built.txt"
            pins.write_text("\n".join(keep) + "\n", encoding="utf-8")
            args[1:1] = ["--constraint", str(pins), *(f"--upgrade-package={p}" for p in REFRESH)]
        else:
            args.insert(1, "--upgrade")
        subprocess.run(args, check=True, cwd=ROOT)
    # pip-compile notes where each pin came from; the build's temporary file (a path with the
    # Windows user name in it) is just "the last full build", and the .in file is named plainly.
    path = OUT / "installer.txt"
    lines = [
        "    #   -c (the last full build)"
        if ln.lstrip().startswith("#   -c ")
        else "    #   -r requirements/installer.in"
        if ln.lstrip().startswith("#   -r ")
        else "    # via -r requirements/installer.in"
        if ln.lstrip().startswith("# via -r ")
        else ln
        for ln in path.read_text(encoding="utf-8").splitlines()
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    lock = path.read_text(encoding="utf-8")
    print(f"requirements/installer.txt: {lock.count('==')} packages, {lock.count('--hash=')} hashes")


if __name__ == "__main__":
    main()
