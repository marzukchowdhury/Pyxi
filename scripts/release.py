"""Ship a new Pyxi version with one command.

    python scripts/release.py 0.1.5 "What's new, in a sentence or two."  [--full] [--skip-tests]
        [--build-only]       build and check everything, publish nothing (then install and try it)
        [--publish-built]    publish what --build-only built, without rebuilding
    python scripts/release.py --checklist      (the pre-release security checklist, nothing else)

1. Sets the version everywhere (pyproject.toml, src/pyxi/__init__.py, desktop/package.json).
2. Runs the tests: Python, the desktop app's typecheck and unit tests, and the beta server's
   (unless --skip-tests). See docs/RELEASE_CHECKLIST.md.
3. Builds the installer (only Pyxi's own code is refreshed in the bundled Python; --full rebuilds
   that Python too, for new dependencies).
4. Checks the build holds no keys, tokens, invite codes or personal data, and that nothing it
   ships has a known high or critical vulnerability (scripts/security_check.py).
5. Publishes GitHub release v<version> with Pyxi-Setup.exe, its blockmap, latest.yml and the
   voice pack (pyxi-voice-pack-<hash>.zip: the speech libraries, which installed copies download
   from this release on first use).
   The website's download button points at GitHub's "latest release" link, so it serves the new
   version straight away, and installed copies of Pyxi find it, download it in the background
   and offer "Restart to update".
6. Checks the latest link really gives the new version, then commits the version bump.

GitHub access uses the sign-in Git already has on this PC; the token is never printed.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
REPO = "marzukchowdhury/Pyxi"
DIST = ROOT / "desktop" / "dist"
ASSETS = ["Pyxi-Setup.exe", "Pyxi-Setup.exe.blockmap", "latest.yml"]
PACKS = "pyxi-*-pack-*.zip"  # first-use downloads the installer points at (build_installer.py)
AUTHOR = [
    "-c",
    "user.name=Marzuk Haider Chowdhury",
    "-c",
    "user.email=342144728+marzukchowdhury@users.noreply.github.com",
]
# Things that must never ship: real keys and tokens, and files with someone's own data.
SECRET = re.compile(
    rb"sk-or-v1-[0-9a-f]{20,}|sk-ant-[A-Za-z0-9_-]{20,}|AIza[0-9A-Za-z_-]{30,}|ghp_[A-Za-z0-9]{30,}|pyxi_[0-9a-f]{40,}"
)
PRIVATE_FILES = {
    ".env",
    "cloud.json",
    "profile.json",
    "beta_invite_codes.txt",
    "beta_invite_codes.csv",
    "google.json",
    "linkedin.json",
    "brain.key",
}


CHECKLIST = ROOT / "docs" / "RELEASE_CHECKLIST.md"


def manual_checklist() -> str:
    """The by-hand part of docs/RELEASE_CHECKLIST.md, to read before anything is published."""
    text = CHECKLIST.read_text(encoding="utf-8")
    part = text.split("## By hand", 1)[1] if "## By hand" in text else text
    return "Before you publish, by hand (docs/RELEASE_CHECKLIST.md):\n" + part.strip()


def automated_checks() -> None:
    """The tests of everything that ships: Python, the desktop app and the beta server."""
    import shutil

    npm = shutil.which("npm") or shutil.which("npm.cmd") or "npm"
    sh(sys.executable, "-m", "pytest", "-q")
    sh(npm, "run", "typecheck", cwd=ROOT / "desktop")
    sh(npm, "test", cwd=ROOT / "desktop")
    sh(npm, "test", cwd=ROOT / "cloud")


def sh(*cmd: str, **kw) -> subprocess.CompletedProcess:
    print("+", " ".join(cmd[:6]), "..." if len(cmd) > 6 else "")
    kw.setdefault("cwd", ROOT)
    return subprocess.run(cmd, check=True, **kw)


def set_version(v: str) -> None:
    edits = [
        (ROOT / "pyproject.toml", r'^version = "[^"]+"', f'version = "{v}"'),
        (ROOT / "src/pyxi/__init__.py", r'^__version__ = "[^"]+"', f'__version__ = "{v}"'),
    ]
    for path, pattern, new in edits:
        text = path.read_text(encoding="utf-8")
        path.write_text(re.sub(pattern, new, text, count=1, flags=re.M), encoding="utf-8")
    pkg = ROOT / "desktop/package.json"
    data = json.loads(pkg.read_text(encoding="utf-8"))
    data["version"] = v
    pkg.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def check_clean_build() -> None:
    """Scan everything that ships (the app archive, the bundled Python and the packs, zips
    included)."""
    resources = DIST / "win-unpacked" / "resources"
    text_like = {".py", ".json", ".txt", ".asar", ".env", ".yml"}
    for path in [resources / "app.asar", *(resources / "python").rglob("*"), *DIST.glob(PACKS)]:
        if not path.is_file():
            continue
        if path.name in PRIVATE_FILES:
            raise SystemExit(f"STOP: {path} must not ship.")
        if path.suffix == ".zip":
            with zipfile.ZipFile(path) as z:
                for member in z.namelist():
                    name = member.rsplit("/", 1)[-1]
                    if name in PRIVATE_FILES:
                        raise SystemExit(f"STOP: {path}/{member} must not ship.")
                    if Path(name).suffix in text_like | {".pyc"} and SECRET.search(z.read(member)):
                        raise SystemExit(
                            f"STOP: something that looks like a key is in {path}/{member}."
                        )
            continue
        if (
            path.suffix in text_like
            and path.stat().st_size < 200_000_000
            and SECRET.search(path.read_bytes())
        ):
            raise SystemExit(f"STOP: something that looks like a key is inside {path}.")
    print("build is clean: no keys, tokens or personal files")


def check_app_packages() -> None:
    """Every package the app needs at runtime, its dependencies' dependencies included, must be in
    the built app. 0.1.6 shipped without node-gyp-build (uiohook-napi needs it) and crashed on
    start, because desktop/node_modules was a link the builder didn't follow all the way."""
    import shutil

    npm = shutil.which("npm") or shutil.which("npm.cmd") or "npm"
    tree = json.loads(
        sh(npm, "ls", "--omit=dev", "--all", "--json", cwd=ROOT / "desktop", capture_output=True, text=True).stdout
    )
    needed: set[str] = set()

    def walk(deps: dict) -> None:
        for name, info in (deps or {}).items():
            needed.add(name)
            walk(info.get("dependencies", {}))

    walk(tree.get("dependencies", {}))
    resources = DIST / "win-unpacked" / "resources"
    listing = sh(
        "node",
        "-e",
        "for (const p of require('@electron/asar').listPackage(process.argv[1])) console.log(p)",
        str(resources / "app.asar"),
        cwd=ROOT / "desktop",
        capture_output=True,
        text=True,
    ).stdout
    shipped = set()
    for line in listing.splitlines():
        parts = re.split(r"[\\/]", line)
        if len(parts) > 2 and parts[1] == "node_modules" and parts[2]:
            shipped.add(parts[2] + "/" + parts[3] if parts[2].startswith("@") and len(parts) > 3 else parts[2])
    unpacked = resources / "app.asar.unpacked" / "node_modules"
    if unpacked.is_dir():
        shipped |= {p.name for p in unpacked.iterdir()}
    missing = sorted(n for n in needed if n not in shipped)
    if missing:
        raise SystemExit(f"STOP: the app would crash on start, these packages aren't in it: {missing}")
    print(f"app has all {len(needed)} runtime packages")


def github() -> httpx.Client:
    cred = sh(
        "git",
        "credential",
        "fill",
        input="protocol=https\nhost=github.com\n\n",
        capture_output=True,
        text=True,
    ).stdout
    token = next(
        line.split("=", 1)[1] for line in cred.splitlines() if line.startswith("password=")
    )
    return httpx.Client(
        timeout=httpx.Timeout(1800, connect=30),
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
    )


def publish(v: str, notes: str) -> None:
    with github() as gh:
        r = gh.post(
            f"https://api.github.com/repos/{REPO}/releases",
            json={
                "tag_name": f"v{v}",
                "target_commitish": "main",
                "name": f"Pyxi {v} beta",
                "body": notes,
                "prerelease": False,
                "make_latest": "true",
            },
        )
        if r.status_code == 422:
            raise SystemExit(f"STOP: release v{v} already exists. Pick a new version number.")
        r.raise_for_status()
        rel = r.json()
        for name in ASSETS + [p.name for p in sorted(DIST.glob(PACKS))]:
            f = DIST / name
            print(f"uploading {name} ({f.stat().st_size / 2**20:.0f} MB)")
            with f.open("rb") as body:
                up = gh.post(
                    f"https://uploads.github.com/repos/{REPO}/releases/{rel['id']}/assets",
                    params={"name": name},
                    headers={
                        "Content-Type": "application/octet-stream",
                        "Content-Length": str(f.stat().st_size),
                    },
                    content=body,
                )
            if up.status_code >= 300:
                raise SystemExit(f"STOP: uploading {name} failed: {up.status_code} {up.text[:200]}")
        print("release:", rel["html_url"])


def verify(v: str) -> None:
    latest = f"https://github.com/{REPO}/releases/latest/download"
    yml = httpx.get(f"{latest}/latest.yml", follow_redirects=True, timeout=60).text
    if f"version: {v}" not in yml:
        raise SystemExit(f"STOP: the latest release doesn't say {v} yet:\n{yml[:200]}")
    size = (DIST / "Pyxi-Setup.exe").stat().st_size
    r = httpx.get(
        f"{latest}/Pyxi-Setup.exe",
        headers={"Range": "bytes=0-0"},
        follow_redirects=True,
        timeout=60,
    )
    total = int(r.headers.get("content-range", "/0").rsplit("/", 1)[-1] or 0)
    if total != size:
        raise SystemExit(f"STOP: the latest download is {total} bytes, the build is {size}.")
    print(f"verified: the website's download link now gives Pyxi {v} ({size / 2**20:.0f} MB)")


def main() -> None:
    if "--checklist" in sys.argv:
        print(manual_checklist())
        return
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args or not re.fullmatch(r"\d+\.\d+\.\d+", args[0]):
        raise SystemExit(__doc__)
    v = args[0]
    notes = args[1] if len(args) > 1 else "Fixes and improvements."
    notes = (
        f"{notes}\n\nInstall: run Pyxi-Setup.exe. During the beta Windows may warn about an unknown "
        "publisher: choose More info, then Run anyway. Already have Pyxi? It updates itself: look for "
        '"Restart to update" in the notch.'
    )
    set_version(v)
    # --publish-built: publish the build an earlier --build-only run made (and that was installed
    # and tried by hand), without rebuilding it.
    if "--publish-built" not in sys.argv:
        sh(sys.executable, "-m", "pip", "install", "-e", ".", "-q", "--no-deps")
        if "--skip-tests" not in sys.argv:
            automated_checks()
        else:
            print("WARNING: --skip-tests: no tests run. Only for re-publishing a build that passed them.")
        for name in ASSETS:  # never upload a leftover from an earlier build
            (DIST / name).unlink(missing_ok=True)
        for pack in DIST.glob(PACKS):
            pack.unlink()
        built = ROOT / "desktop/build"
        full = "--full" in sys.argv or not (
            (built / "python-full/python.exe").exists()
            or (built / "python/Lib/site-packages/pip").is_dir()
        )
        sh(sys.executable, "scripts/build_installer.py", *([] if full else ["--update-pyxi"]))
    missing = [n for n in ASSETS if not (DIST / n).exists()] + (
        [] if any(DIST.glob(PACKS)) else [PACKS]
    )
    if missing:
        raise SystemExit(f"STOP: the build didn't produce {missing}.")
    if f"version: {v}" not in (DIST / "latest.yml").read_text(encoding="utf-8"):
        raise SystemExit(f"STOP: the build in {DIST} isn't {v}.")
    check_app_packages()
    check_clean_build()
    sh(sys.executable, "scripts/security_check.py")  # stops on high or critical
    print(manual_checklist())
    if "--build-only" in sys.argv:
        print(f"built {v}, not published: install {DIST / 'Pyxi-Setup.exe'} and try it, then run "
              f"release.py {v} \"notes\" --publish-built")
        return
    publish(v, notes)
    verify(v)
    sh("git", "add", "pyproject.toml", "src/pyxi/__init__.py", "desktop/package.json")
    sh(
        "git",
        *AUTHOR,
        "commit",
        "-q",
        "-m",
        f"Pyxi {v}\n\n{args[1] if len(args) > 1 else ''}".strip(),
    )
    print(f"done: Pyxi {v} is out.")


if __name__ == "__main__":
    main()
