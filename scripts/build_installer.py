"""Build Pyxi's one-click Windows installer.

1. A private Python for the brain, in desktop/build/python: a copy of this machine's CPython
   (no tests, docs or tkinter) with Otto and Pyxi installed into it as ordinary packages, so the
   installed app needs nothing else on the user's PC.
2. electron-vite builds the app, then electron-builder wraps both into one NSIS installer in
   desktop/dist ("Pyxi Setup <version>.exe").

Voice and speech models aren't bundled: the brain fetches them on first run.

usage: python scripts/build_installer.py [--skip-python | --update-pyxi]
  --update-pyxi: keep the bundled Python, reinstall only Pyxi's own code into it (fast)
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "desktop"
BUNDLE = DESKTOP / "build" / "python"
OTTO = ROOT.parent / "otto"
SKIP = {"test", "tests", "idlelib", "tkinter", "turtledemo", "lib2to3", "site-packages"}
SKIP_DLLS = {"_tkinter.pyd", "tcl86t.dll", "tk86t.dll"}


def base_python() -> Path:
    """The CPython this venv was made from (the real install, not the venv)."""
    return Path(sys.base_prefix)


def copy_python() -> None:
    src = base_python()
    if BUNDLE.exists():
        shutil.rmtree(BUNDLE)
    BUNDLE.mkdir(parents=True)
    for f in src.glob("*"):
        if f.is_file() and f.suffix.lower() in (".exe", ".dll") and f.name.lower() != "pythonw.exe":
            shutil.copy2(f, BUNDLE / f.name)
    shutil.copytree(
        src / "DLLs",
        BUNDLE / "DLLs",
        ignore=lambda _d, names: [n for n in names if n in SKIP_DLLS or n.startswith("_test")],
    )
    lib = BUNDLE / "Lib"
    lib.mkdir()
    for item in (src / "Lib").iterdir():
        if item.name in SKIP or item.name == "__pycache__":
            continue
        if item.is_dir():
            shutil.copytree(
                item, lib / item.name, ignore=shutil.ignore_patterns("__pycache__", "test", "tests")
            )
        else:
            shutil.copy2(item, lib / item.name)
    (lib / "site-packages").mkdir()


def requirements() -> list[str]:
    """Pyxi's dependencies, minus Otto (installed from the local checkout instead)."""
    deps = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "dependencies"
    ]
    return [d for d in deps if not d.startswith("otto-browser-buddy")]


def install_packages() -> None:
    py = str(BUNDLE / "python.exe")
    run([py, "-m", "ensurepip", "--default-pip"])
    pip = [py, "-m", "pip", "--disable-pip-version-check", "install", "--no-warn-script-location"]
    run([*pip, *requirements()])
    run([*pip, "--no-deps", str(OTTO)])
    otto_deps = tomllib.loads((OTTO / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "dependencies"
    ]
    run([*pip, *otto_deps])
    run([*pip, "--no-deps", str(ROOT)])
    # Prove it: the bundle imports the brain with no help from this machine's packages.
    run(
        [
            py,
            "-I",
            "-c",
            "import pyxi.server, pyxi.companion.brain, otto, uiautomation, pycaw; print('bundle ok')",
        ]
    )


def run(cmd: list[str], check: bool = True) -> None:
    print("+", " ".join(cmd[:6]), "..." if len(cmd) > 6 else "")
    subprocess.run(cmd, check=check, cwd=ROOT)


def main() -> None:
    if "--update-pyxi" in sys.argv:
        py = str(BUNDLE / "python.exe")
        run(
            [
                py,
                "-m",
                "pip",
                "--disable-pip-version-check",
                "install",
                "--no-warn-script-location",
                "--no-deps",
                "--force-reinstall",
                str(ROOT),
            ]
        )
        run([py, "-I", "-c", "import pyxi.cloud, pyxi.server; print('bundle ok')"])
    elif "--skip-python" not in sys.argv:
        copy_python()
        install_packages()
    npm = shutil.which("npm") or "npm"
    subprocess.run([npm, "run", "build"], check=True, cwd=DESKTOP, shell=sys.platform == "win32")
    subprocess.run([npm, "run", "dist"], check=True, cwd=DESKTOP, shell=sys.platform == "win32")
    for f in sorted((DESKTOP / "dist").glob("*.exe")):
        print(f"installer: {f} ({f.stat().st_size / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
