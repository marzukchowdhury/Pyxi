"""Ship a new Pyxi version with one command.

    python scripts/release.py 0.1.5 "What's new, in a sentence or two."  [--full] [--skip-tests]
        [--build-only]       build and check everything, publish nothing (then install and try it)
        [--publish-built]    publish what --build-only built, without rebuilding
        [--without-firefox]  publish with no Firefox add-on though the last release had one
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
   If extension/sign_firefox.py has left a signed Firefox add-on in extension/signed/, it goes
   up too, with its update file: pyxi-firefox.xpi and pyxi-firefox-updates.json. The add-on's
   manifest points at .../releases/latest/download/pyxi-firefox-updates.json, so publishing
   the release is what puts the update file at its address; "Add to Firefox" in the app opens
   .../releases/latest/download/pyxi-firefox.xpi. They go up with every release, new or not,
   so that the latest link always has them. extension/signed/ belongs to one checkout, so a
   release run from a checkout without it would take the add-on off the latest link: when the
   release that is out now carries the add-on and this one would not, it stops at the very
   start, before the version is set or anything is built (copy extension/signed/ over, or
   pass --without-firefox). A --build-only run publishes nothing, so it is told, not stopped.
6. Checks the latest link really gives the new version, then commits the version bump. The
   Firefox add-on at the latest link is checked after that commit: the release is public by
   then, so a fault there is reported loudly and nothing is left half done.

GitHub access uses the sign-in Git already has on this PC; the token is never printed.
"""

from __future__ import annotations

import hashlib
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
# The signed Firefox add-on and its update file (extension/sign_firefox.py), with the content
# types they must be served with: Firefox only offers to install a file sent as an add-on.
SIGNED = ROOT / "extension" / "signed"
FIREFOX = {"pyxi-firefox.xpi": "application/x-xpinstall", "pyxi-firefox-updates.json": "application/json"}
# What the app looks for before it offers "Add to Firefox" (pyxi/companion/browsersetup.py):
# this add-on's id in the update file, and a link to the add-on at the latest release.
ADDON_ID = "pyxi-in-your-browser@pyxi.local"
LATEST = f"https://github.com/{REPO}/releases/latest/download"
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


def latest_has_firefox() -> bool | None:
    """Does the release that is out now carry the Firefox add-on? None when that couldn't be
    found out."""
    try:
        r = httpx.get(
            f"{LATEST}/pyxi-firefox.xpi", headers={"Range": "bytes=0-0"}, follow_redirects=True, timeout=60
        )
    except httpx.HTTPError:
        return None
    if r.status_code in (200, 206):
        return True
    return False if r.status_code == 404 else None


def firefox_assets(build_only: bool = False) -> list[tuple[Path, str]]:
    """The signed Firefox add-on and its update file, as (file, content type), if they have
    been made. Checked at the very start, before the build: the two must belong together, be
    what the app looks for, and not go missing from a release when the last one had them.
    ``build_only``: nothing is published by this run, so a missing add-on is a note, not a stop."""
    xpi, updates = (SIGNED / name for name in FIREFOX)
    if not xpi.exists() and not updates.exists():
        if "--without-firefox" in sys.argv:
            print("WARNING: --without-firefox: this release carries no Firefox add-on.")
            return []
        had = latest_has_firefox()
        if had is False:
            print("note: no signed Firefox add-on in extension/signed (extension/sign_firefox.py makes it)")
            return []
        what = (
            "the release that is out now carries the Firefox add-on, and this checkout has "
            "none in extension/signed."
            if had
            else "couldn't find out whether the release that is out now carries the Firefox "
            "add-on (no connection to GitHub?), and this checkout has none in extension/signed."
        )
        fix = (
            "Copy extension/signed/ here from the checkout that has it, or run "
            "extension/sign_firefox.py (it picks up the version Mozilla already signed). To "
            "publish without it on purpose, add --without-firefox."
        )
        if build_only:  # nothing goes out now: build, and say what publishing will need
            print(f"note: {what} Publishing this build will stop on that. {fix}")
            return []
        raise SystemExit(
            f"STOP: {what} Publishing without it takes it off the latest link: Add to Firefox "
            "goes away in Pyxi, and add-ons that are installed find no update file. " + fix
        )
    if not (xpi.exists() and updates.exists()):
        raise SystemExit(f"STOP: {SIGNED} has only one of {list(FIREFOX)}. Run extension/sign_firefox.py again.")
    try:
        newest = json.loads(updates.read_text(encoding="utf-8"))["addons"][ADDON_ID]["updates"][-1]
    except (ValueError, KeyError, IndexError, TypeError):
        raise SystemExit(
            f"STOP: {updates} isn't the update file of {ADDON_ID}. Run extension/sign_firefox.py again."
        ) from None
    if newest.get("update_hash") != "sha256:" + hashlib.sha256(xpi.read_bytes()).hexdigest():
        raise SystemExit(
            "STOP: the signed add-on and its update file don't belong together (the hash differs). "
            "Run extension/sign_firefox.py again."
        )
    if newest.get("update_link") != f"{LATEST}/pyxi-firefox.xpi":
        raise SystemExit(
            f"STOP: the update file sends Firefox to {newest.get('update_link')!r}, not to "
            f"{LATEST}/pyxi-firefox.xpi. Pyxi would not offer it and installed add-ons would not "
            "update. Run extension/sign_firefox.py again."
        )
    gecko = json.loads((ROOT / "extension" / "manifest.firefox.json").read_text(encoding="utf-8"))
    if gecko["browser_specific_settings"]["gecko"]["id"] != ADDON_ID:
        raise SystemExit(
            f"STOP: the extension's Firefox id is no longer {ADDON_ID}: the app, this script and "
            "the signed add-on must all name the same one."
        )
    current = json.loads((ROOT / "extension" / "manifest.json").read_text(encoding="utf-8"))["version"]
    if newest.get("version") != current:
        print(
            f"WARNING: the signed Firefox add-on is {newest.get('version')}, the extension is now "
            f"{current}. Firefox users keep the old one until extension/sign_firefox.py is run."
        )
    return [(SIGNED / name, kind) for name, kind in FIREFOX.items()]


def github() -> httpx.Client:
    # The account is named (the repository's owner): with two GitHub accounts signed in on
    # this PC and none named, Windows asks "Select an account" every single time.
    cred = sh(
        "git",
        "credential",
        "fill",
        input=f"protocol=https\nhost=github.com\nusername={REPO.split('/')[0]}\n\n",
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


def publish(v: str, notes: str, extra: list[tuple[Path, str]] | None = None) -> None:
    """``extra``: more files for the release, each with the content type to serve it as."""
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
        files = [
            (DIST / name, "application/octet-stream")
            for name in ASSETS + [p.name for p in sorted(DIST.glob(PACKS))]
        ] + list(extra or [])
        for f, kind in files:
            name = f.name
            print(f"uploading {name} ({f.stat().st_size / 2**20:.0f} MB)")
            with f.open("rb") as body:
                up = gh.post(
                    f"https://uploads.github.com/repos/{REPO}/releases/{rel['id']}/assets",
                    params={"name": name},
                    headers={
                        "Content-Type": kind,
                        "Content-Length": str(f.stat().st_size),
                    },
                    content=body,
                )
            if up.status_code >= 300:
                raise SystemExit(f"STOP: uploading {name} failed: {up.status_code} {up.text[:200]}")
        print("release:", rel["html_url"])


def verify_firefox(extra: list[tuple[Path, str]]) -> None:
    """The latest link gives the update file that was just uploaded, and the add-on as an
    add-on (so that Firefox offers to install it instead of saving it). Raises SystemExit
    saying what is wrong. Run after the release is public: see ``finish``."""
    if not extra:
        return
    by_name = {f.name: f for f, _ in extra}
    updates = httpx.get(f"{LATEST}/pyxi-firefox-updates.json", follow_redirects=True, timeout=60)
    if updates.status_code != 200 or updates.json() != json.loads(
        by_name["pyxi-firefox-updates.json"].read_text(encoding="utf-8")
    ):
        raise SystemExit("the latest link doesn't give the Firefox update file that was uploaded.")
    xpi = httpx.get(
        f"{LATEST}/pyxi-firefox.xpi", headers={"Range": "bytes=0-0"}, follow_redirects=True, timeout=60
    )
    if xpi.status_code not in (200, 206):
        raise SystemExit(f"the latest link doesn't give the Firefox add-on ({xpi.status_code}).")
    if "x-xpinstall" not in xpi.headers.get("content-type", ""):
        raise SystemExit(
            f"the Firefox add-on is served as {xpi.headers.get('content-type')!r}, so "
            "Firefox would save it instead of installing it."
        )
    print("verified: the Firefox add-on and its update file are at the latest link")


def finish(v: str, message: str, firefox: list[tuple[Path, str]]) -> None:
    """After publishing: commit the version bump, whatever else happens, and only then look at
    the Firefox add-on at the latest link. The release is public by now, so a fault there
    can't stop anything any more: it is said loudly, with the commit already made."""
    sh("git", "add", "pyproject.toml", "src/pyxi/__init__.py", "desktop/package.json")
    sh("git", *AUTHOR, "commit", "-q", "-m", message)
    try:
        verify_firefox(firefox)
    except (SystemExit, httpx.HTTPError, ValueError) as fault:
        raise SystemExit(
            f"\n!!! Pyxi {v} IS OUT: the release is public and the version is committed.\n"
            f"!!! But its Firefox add-on is not right: {fault}\n"
            "!!! Until that is fixed, Add to Firefox doesn't show in Pyxi and installed add-ons "
            "don't update.\n"
            f"!!! To fix it: on the release page of v{v}, replace pyxi-firefox.xpi and "
            "pyxi-firefox-updates.json with the ones in extension/signed (the add-on must be "
            "uploaded as application/x-xpinstall), or publish the next version."
        ) from None
    print(f"done: Pyxi {v} is out.")


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
    # First of all, before the version is touched or anything is built: would this release
    # lose the Firefox add-on, and do the signed files belong together? (--build-only is told,
    # not stopped: it publishes nothing.)
    firefox = firefox_assets(build_only="--build-only" in sys.argv)
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
    publish(v, notes, firefox)
    verify(v)
    finish(v, f"Pyxi {v}\n\n{args[1] if len(args) > 1 else ''}".strip(), firefox)


if __name__ == "__main__":
    main()
