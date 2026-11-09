r"""Build Pyxi for macOS: the bundled Python, then the .dmg. Runs on a Mac only.

1. A private Python for the brain in desktop/build/python (what electron-builder ships as
   Contents/Resources/python): python-build-standalone's relocatable CPython for the chosen
   architecture, checked against its published SHA-256, with Pyxi's and Otto's dependencies, the
   pyobjc frameworks, Otto (from ../otto) and Pyxi pip-installed into it.
2. electron-vite builds the app, then electron-builder makes desktop/dist/Pyxi-<version>-<arch>.dmg.

The two architectures need two Pythons, so each is built in turn (arm64 on Apple silicon; x64
under Rosetta with ``arch -x86_64``). Unlike the Windows build nothing is slimmed or split into
a voice pack yet: the bundle is bigger, and simpler.

Signing and notarizing (both need an Apple Developer ID, which Pyxi doesn't have yet):
* no identity: the app is ad-hoc signed ("-"), which Apple silicon needs to run it at all.
  Gatekeeper still warns ("unidentified developer"): right-click > Open the first time.
* CSC_LINK (+ CSC_KEY_PASSWORD), or CSC_NAME for a certificate in the keychain: electron-builder
  signs with that Developer ID Application certificate, hardened runtime on.
* and APPLE_TEAM_ID with either APPLE_API_KEY + APPLE_API_KEY_ID + APPLE_API_ISSUER, or
  APPLE_ID + APPLE_APP_SPECIFIC_PASSWORD: it's also notarized and stapled.
docs/MAC-PORT.md has the details.

usage: python3 scripts/build_mac.py [--arch arm64|x64|both] [--skip-python | --python-only]
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "desktop"
BUNDLE = DESKTOP / "build" / "python"  # what ships (desktop/package.json extraResources)
CACHE = DESKTOP / "build" / "cache"
OTTO = ROOT.parent / "otto"
REQS = ROOT / "requirements" / "mac.in"
LOCK = ROOT / "requirements" / "mac.txt"  # hash-locked, once made on a Mac (see mac.in)

# python-build-standalone 20261026, CPython 3.12.16 (the Windows bundle is 3.12 too). The
# SHA-256s are the ones GitHub lists for these release assets.
PBS = "https://github.com/astral-sh/python-build-standalone/releases/download/20261026/"
PYTHONS = {
    "arm64": (
        "cpython-3.12.16+20261026-aarch64-apple-darwin-install_only_stripped.tar.gz",
        "353d5f1b4fd2715e7053f12f068435d5bbad16b91e057006b5d01e0925709a7e",
    ),
    "x64": (
        "cpython-3.12.16+20261026-x86_64-apple-darwin-install_only_stripped.tar.gz",
        "37c38fda76fec1fbf310adcbafb7f0d96af13b5511bf8ea954c3aa8eec185734",
    ),
}
# Never needed to run the brain.
DROP = ("test", "idlelib", "tkinter", "turtledemo", "ensurepip", "lib2to3", "pydoc_data")

# What the finished bundle must import, Mac frameworks included.
CHECK = (
    "import pyxi.server, pyxi.macos.windows, pyxi.macos.uia, pyxi.macos.hands, otto; "
    "import Quartz, AppKit, ApplicationServices, Vision, Security, Foundation; "
    "import faster_whisper, kokoro_onnx, playwright, cryptography, mss; "
    "from pyxi import windows; assert windows.screens.__module__ == 'pyxi.macos.windows'; "
    "print('bundle ok')"
)


def host_arch() -> str:
    return "arm64" if platform.machine() in ("arm64", "aarch64") else "x64"


def prefix(arch: str) -> list[str]:
    """Run x64 tools under Rosetta on Apple silicon (and the other way round never)."""
    return ["arch", "-x86_64"] if arch == "x64" and host_arch() == "arm64" else []


def run(cmd: list[str], **kw) -> None:
    print("+", " ".join(cmd[:7]), "..." if len(cmd) > 7 else "")
    subprocess.run(cmd, check=True, cwd=kw.pop("cwd", ROOT), **kw)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch_python(arch: str) -> Path:
    name, want = PYTHONS[arch]
    CACHE.mkdir(parents=True, exist_ok=True)
    tarball = CACHE / name
    if not tarball.exists() or sha256(tarball) != want:
        print(f"downloading {name}")
        tmp = tarball.with_suffix(".part")
        urllib.request.urlretrieve(PBS + name.replace("+", "%2B"), tmp)  # a fixed https URL
        tmp.replace(tarball)
    got = sha256(tarball)
    if got != want:
        tarball.unlink(missing_ok=True)
        raise SystemExit(f"STOP: {name} has SHA-256 {got}, expected {want}")
    return tarball


def unpack(tarball: Path) -> None:
    if BUNDLE.exists():
        shutil.rmtree(BUNDLE)
    BUNDLE.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tarball) as tar:
        # The archive holds one folder, "python/": extracted as desktop/build/python.
        tar.extractall(BUNDLE.parent, filter="data")


def python(arch: str) -> list[str]:
    return [*prefix(arch), str(BUNDLE / "bin" / "python3")]


def install_packages(arch: str) -> None:
    pip = [*python(arch), "-m", "pip", "--disable-pip-version-check", "install", "--no-input"]
    if LOCK.exists():
        run([*pip, "--require-hashes", "-r", str(LOCK)])
    else:
        print("WARNING: no requirements/mac.txt lock yet; installing from mac.in (versions float)")
        run([*pip, "-r", str(REQS)])
    run([*pip, "--no-deps", str(OTTO)])
    run([*pip, "--no-deps", str(ROOT)])


def trim() -> None:
    lib = next((BUNDLE / "lib").glob("python3.*"))
    for name in DROP:
        shutil.rmtree(lib / name, ignore_errors=True)
    for tk in (BUNDLE / "lib").glob("t[ck]l*"):
        shutil.rmtree(tk, ignore_errors=True)
    for cache in BUNDLE.rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)


def check_bundle(arch: str) -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTHON", "VIRTUAL_ENV"))}
    run([*python(arch), "-c", CHECK], env=env, cwd=BUNDLE)


def ship_google_client() -> None:
    """As on Windows: Pyxi's own OAuth client, from the git-ignored desktop/build-secrets."""
    src = DESKTOP / "build-secrets" / "google_client.json"
    dest = BUNDLE / "google_client.json"
    if src.is_file():
        shutil.copy2(src, dest)
        print("google: shipping Pyxi's OAuth client")
    else:
        dest.unlink(missing_ok=True)
        print("google: no desktop/build-secrets/google_client.json; testers would need their own")


def builder_flags(env: dict[str, str] | None = None) -> list[str]:
    """electron-builder overrides from the signing environment (see the module docstring)."""
    env = dict(os.environ) if env is None else env
    flags = []
    if not (env.get("CSC_LINK") or env.get("CSC_NAME")):
        flags.append("-c.mac.identity=-")  # ad-hoc: runs on Apple silicon, Gatekeeper warns
    team = env.get("APPLE_TEAM_ID")
    api_key = env.get("APPLE_API_KEY") and env.get("APPLE_API_KEY_ID") and env.get("APPLE_API_ISSUER")
    apple_id = env.get("APPLE_ID") and env.get("APPLE_APP_SPECIFIC_PASSWORD")
    if team and (api_key or apple_id) and (env.get("CSC_LINK") or env.get("CSC_NAME")):
        flags.append("-c.mac.notarize=true")
    return flags


def build_app(arch: str) -> None:
    npm = shutil.which("npm") or "npm"
    run([npm, "run", "build"], cwd=DESKTOP)
    npx = shutil.which("npx") or "npx"
    # The zip is what electron-updater installs from, once Mac builds are signed (updates.ts).
    run([npx, "electron-builder", "--mac", "dmg", "zip", f"--{arch}", "--publish", "never", *builder_flags()], cwd=DESKTOP)


def main() -> None:
    if sys.platform != "darwin":
        raise SystemExit("build_mac.py builds the Mac app on a Mac (see docs/MAC-PORT.md).")
    args = sys.argv[1:]
    arch = args[args.index("--arch") + 1] if "--arch" in args else host_arch()
    arches = ["arm64", "x64"] if arch == "both" else [arch]
    version = json.loads((DESKTOP / "package.json").read_text(encoding="utf-8"))["version"]
    for a in arches:
        if a not in PYTHONS:
            raise SystemExit(f"unknown --arch {a!r}: arm64, x64 or both")
        if "--skip-python" not in args:
            unpack(fetch_python(a))
            install_packages(a)
            trim()
            check_bundle(a)
        ship_google_client()
        if "--python-only" in args:
            continue
        build_app(a)
        for f in sorted((DESKTOP / "dist").glob(f"Pyxi-{version}-{a}.dmg")):
            print(f"{f.name}: {f.stat().st_size / 1e6:.0f} MB")


if __name__ == "__main__":
    main()
