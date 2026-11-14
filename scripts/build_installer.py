"""Build Pyxi's one-click Windows installer.

1. A private Python for the brain, in desktop/build/python-full: a copy of this machine's CPython
   (no tests, docs or tkinter) with Otto and Pyxi pip-installed into it as ordinary packages, so
   the installed app needs nothing else on the user's PC.
2. A slim copy of it in desktop/build/python, which is what ships:
   - the standard library and every pure-Python package are compiled and packed into two zips
     (python312.zip, python-packages.zip), so a few thousand files install instead of 23,000;
   - pip, tests, docs, type stubs and C headers are left out;
   - Playwright's own Node (90 MB) is left out: Pyxi.exe is Electron, which is Node too, so the
     driver runs on it (ELECTRON_RUN_AS_NODE, set only for the driver);
   - the speech libraries (faster-whisper, CTranslate2, PyAV, onnxruntime, Kokoro and the
     packages only they use) become the "voice pack", desktop/dist/pyxi-voice-pack-<hash>.zip,
     which the installed brain downloads once on first use (scripts/bundle_runtime/
     sitecustomize.py). release.py uploads it next to the installer.
3. electron-vite builds the app, then electron-builder wraps both into one NSIS installer,
   desktop/dist/Pyxi-Setup.exe (with its blockmap and latest.yml for automatic updates).

Voice and speech models aren't bundled either: the brain fetches them on first run.

usage: python scripts/build_installer.py [--skip-python | --update-pyxi]
  --skip-python: reuse the slim bundle as it is (only the Electron side is rebuilt)
  --update-pyxi: keep the bundled Python, reinstall only Pyxi's own code into it (fast)
"""

from __future__ import annotations

import hashlib
import importlib.metadata as md
import importlib.util
import json
import marshal
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
import zipfile
from pathlib import Path, PurePosixPath

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "desktop"
DIST = DESKTOP / "dist"
FULL = DESKTOP / "build" / "python-full"  # pip-built, never shipped
BUNDLE = DESKTOP / "build" / "python"  # the slim copy that ships
OTTO = ROOT.parent / "otto"
RUNTIME = Path(__file__).resolve().parent / "bundle_runtime"
REPO = "marzukchowdhury/Pyxi"
PY = f"python{sys.version_info.major}{sys.version_info.minor}"
SKIP = {"test", "tests", "idlelib", "tkinter", "turtledemo", "lib2to3", "site-packages"}
SKIP_DLLS = {"_tkinter.pyd", "tcl86t.dll", "tk86t.dll"}

# Not needed to run: installing tools, and stdlib parts only those use.
DROP_DISTS = {"pip", "setuptools", "wheel"}
DROP_STDLIB = {"ensurepip", "venv", "pydoc_data"}
# The speech stack: downloaded on first use. Everything only these need goes with them.
# RapidOCR (Pixel's fallback for reading scans) rides along: it runs on the same onnxruntime, and
# its OpenCV is big, so it arrives with the pack instead of growing the installer.
VOICE_ROOTS = [
    "faster-whisper",
    "kokoro-onnx",
    "soundfile",
    "av",
    "ctranslate2",
    "onnxruntime",
    "rapidocr-onnxruntime",
]
# Pure Python, but they open their own files by path or write into their own folder
# (comtypes generates modules in comtypes/gen), so they stay as plain files.
STAY_ON_DISK = {
    "pyxi-buddy",  # readable tracebacks in brain.log, and --update-pyxi swaps it
    "otto-browser-buddy",
    "comtypes",
    "certifi",
    "python-docx",
    "pycparser",
}
NATIVE = {".pyd", ".dll", ".exe", ".so"}
JUNK_SUFFIXES = {".pyi", ".pxd", ".pyx", ".h", ".hpp", ".c", ".cpp", ".lib", ".pdb", ".a"}
JUNK_DIRS = {"tests", "test", "__pycache__"}
JUNK_META = {"RECORD", "INSTALLER", "REQUESTED", "direct_url.json"}
FIXED_TIME = (2026, 1, 1, 0, 0, 0)  # zips are byte-identical when nothing changed


def base_python() -> Path:
    """The CPython this venv was made from (the real install, not the venv)."""
    return Path(sys.base_prefix)


# ---------------------------------------------------------------- 1. the full, pip-built Python


def copy_python() -> None:
    src = base_python()
    if FULL.exists():
        shutil.rmtree(FULL)
    FULL.mkdir(parents=True)
    for f in src.glob("*"):
        if f.is_file() and f.suffix.lower() in (".exe", ".dll") and f.name.lower() != "pythonw.exe":
            shutil.copy2(f, FULL / f.name)
    shutil.copytree(
        src / "DLLs",
        FULL / "DLLs",
        ignore=lambda _d, names: [n for n in names if n in SKIP_DLLS or n.startswith("_test")],
    )
    lib = FULL / "Lib"
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


LOCK = ROOT / "requirements" / "installer.txt"  # exact versions and hashes (lock_installer_deps.py)


def check_lock() -> None:
    """The lock must cover what Pyxi and Otto ask for today: a dependency added to either
    pyproject.toml without re-locking stops the build rather than going in unpinned."""
    if not LOCK.exists():
        raise SystemExit("STOP: no requirements/installer.txt. Run: python scripts/lock_installer_deps.py")
    otto = tomllib.loads((OTTO / "pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]
    locked_in = (LOCK.with_suffix(".in")).read_text(encoding="utf-8").splitlines()
    missing = [d for d in [*requirements(), *otto] if d not in locked_in]
    if missing:
        raise SystemExit(
            f"STOP: {missing} aren't in the lock yet. Run: python scripts/lock_installer_deps.py"
        )


def pip(*args: str) -> None:
    py = str(FULL / "python.exe")
    run(
        [
            py,
            "-m",
            "pip",
            "--disable-pip-version-check",
            "install",
            "--no-warn-script-location",
            *args,
        ]
    )


def install_packages() -> None:
    check_lock()
    run([str(FULL / "python.exe"), "-m", "ensurepip", "--default-pip"])
    # Every third-party package (and pip itself) at its locked version, each file checked against
    # its sha256: a package that changed on PyPI, or one nobody pinned, stops the build.
    pip("--require-hashes", "-r", str(LOCK))
    pip("--no-deps", str(OTTO))  # the local checkouts, their dependencies already locked above
    pip("--no-deps", str(ROOT))


def adopt_old_bundle() -> None:
    """Builds before the slim step kept the pip-built Python in build/python: reuse it."""
    if not FULL.exists() and (BUNDLE / "Lib" / "site-packages" / "pip").is_dir():
        print(f"reusing the pip-built Python in {BUNDLE} as {FULL}")
        BUNDLE.rename(FULL)


# ---------------------------------------------------------------- 2. the slim copy that ships


def dists(site: Path) -> list[md.Distribution]:
    return sorted(md.distributions(path=[str(site)]), key=lambda d: canonicalize_name(d.name))


def voice_only(all_dists: list[md.Distribution]) -> set[str]:
    """The speech roots plus whatever only they depend on (not needed by the rest of Pyxi)."""
    env = default_environment() | {"extra": ""}
    by_name = {canonicalize_name(d.name): d for d in all_dists}

    def deps(name: str) -> list[str]:
        d = by_name.get(name)
        out = []
        for r in (d.requires or []) if d else []:
            req = Requirement(r)
            if req.marker is None or req.marker.evaluate(env):
                out.append(canonicalize_name(req.name))
        return out

    def closure(roots, stop=frozenset()) -> set[str]:
        seen: set[str] = set()
        todo = list(roots)
        while todo:
            n = todo.pop()
            if n not in seen and n not in stop:
                seen.add(n)
                todo += deps(n)
        return seen

    roots = {canonicalize_name(r) for r in VOICE_ROOTS}
    rest_roots = [n for n in deps("pyxi-buddy") if n not in roots] + ["otto-browser-buddy"]
    return closure(roots) - closure(rest_roots, stop=roots)


def wanted(rel: PurePosixPath) -> bool:
    if rel.parts[0] == ".." or any(p in JUNK_DIRS for p in rel.parts[:-1]):
        return False
    if rel.suffix.lower() in JUNK_SUFFIXES:
        return False
    return not (rel.parts[0].endswith(".dist-info") and rel.name in JUNK_META)


def pyc(source: bytes, name: str) -> bytes | None:
    """A sourceless, hash-based .pyc for zipimport (None if it doesn't compile)."""
    try:
        code = compile(source, name, "exec", dont_inherit=True)
    except (SyntaxError, ValueError):
        return None
    flags = (1).to_bytes(4, "little")  # hash-based, unchecked: never compared to a source
    return (
        importlib.util.MAGIC_NUMBER
        + flags
        + importlib.util.source_hash(source)
        + marshal.dumps(code)
    )


class Zip:
    """A deterministic zip: sorted entries, fixed dates. Stored (not compressed) by default:
    the installer compresses everything with LZMA, which does far better on raw .pyc files
    than on deflated ones (45 MB of packages: 19 MB deflated, 10 MB stored then LZMA)."""

    def __init__(self, path: Path, method: int = zipfile.ZIP_STORED) -> None:
        self.path, self.method, self.entries = path, method, {}

    def add(self, arc: str, data: bytes) -> None:
        self.entries[arc] = data

    def add_module(self, arc: str, source: bytes, label: str) -> None:
        compiled = pyc(source, f"{label}/{arc}") if arc.endswith(".py") else None
        if compiled is None:
            self.add(arc, source)
        else:
            self.add(arc[:-3] + ".pyc", compiled)

    def write(self) -> None:
        # Folder entries too: zipimport finds namespace packages (no __init__.py, like
        # "opentelemetry" or "google") only through them.
        dirs = {
            "/".join(arc.split("/")[:i]) + "/"
            for arc in self.entries
            for i in range(1, arc.count("/") + 1)
        }
        level = 9 if self.method == zipfile.ZIP_DEFLATED else None
        with zipfile.ZipFile(self.path, "w") as z:
            for arc in sorted(dirs | set(self.entries)):
                info = zipfile.ZipInfo(arc, FIXED_TIME)
                if arc.endswith("/"):
                    info.external_attr = (0o40755 << 16) | 0x10
                    z.writestr(info, b"")
                    continue
                info.compress_type = self.method
                info.external_attr = 0o644 << 16
                z.writestr(info, self.entries[arc], compresslevel=level)


def patch_playwright(site: Path) -> None:
    """Run Playwright's driver on Pyxi.exe (Electron as Node) instead of its own node.exe."""
    driver = site / "playwright" / "_impl" / "_driver.py"
    text = driver.read_text(encoding="utf-8")
    old_exe = 'os.getenv("PLAYWRIGHT_NODEJS_PATH", str(driver_path / "node.exe")),'
    new_exe = 'os.getenv("PLAYWRIGHT_NODEJS_PATH", str(_pyxi_node(driver_path))),'
    old_env = 'env["PW_LANG_NAME"] = "python"'
    new_env = (
        'env["ELECTRON_RUN_AS_NODE"] = "1"  # Pyxi.exe is Electron: be plain Node\n    ' + old_env
    )
    helper = '''

def _pyxi_node(driver_path: Path) -> Path:
    """Pyxi's installer leaves Playwright's node.exe out: Pyxi.exe (Electron) is Node too.
    The bundled Python lives in <app>/resources/python, so Pyxi.exe is two folders up."""
    node = driver_path / "node.exe"
    return node if node.exists() else Path(sys.executable).resolve().parents[2] / "Pyxi.exe"
'''
    if "_pyxi_node" in text:
        return
    if old_exe not in text or old_env not in text:
        raise SystemExit(f"STOP: Playwright's driver changed; update patch_playwright ({driver})")
    text = text.replace(old_exe, new_exe).replace(old_env, new_env)
    text = text.replace(
        "\n\ndef compute_driver_executable", helper + "\n\ndef compute_driver_executable", 1
    )
    driver.write_text(text, encoding="utf-8")
    (site / "playwright" / "driver" / "node.exe").unlink(missing_ok=True)


def slim(version: str) -> None:
    started = time.monotonic()
    if BUNDLE.exists():
        shutil.rmtree(BUNDLE)
    BUNDLE.mkdir(parents=True)
    for f in FULL.glob("*"):
        if f.is_file() and f.suffix.lower() in (".exe", ".dll"):
            shutil.copy2(f, BUNDLE / f.name)
    shutil.copytree(FULL / "DLLs", BUNDLE / "DLLs")

    # The standard library, compiled, in pythonXY.zip (as in Python's own embeddable build).
    stdlib = Zip(BUNDLE / f"{PY}.zip")
    lib = FULL / "Lib"
    for f in lib.rglob("*"):
        rel = PurePosixPath(f.relative_to(lib).as_posix())
        if f.is_dir() or rel.parts[0] in DROP_STDLIB | {"site-packages"} or not wanted(rel):
            continue
        stdlib.add_module(str(rel), f.read_bytes(), PY)
    stdlib.write()

    full_site = lib / "site-packages"
    site = BUNDLE / "Lib" / "site-packages"
    site.mkdir(parents=True)
    packages = Zip(BUNDLE / "python-packages.zip")
    pack_dir = DESKTOP / "build" / "packs" / "voice"
    shutil.rmtree(pack_dir.parent, ignore_errors=True)
    pack_dir.mkdir(parents=True)

    all_dists = dists(full_site)
    voice = voice_only(all_dists)
    owned: set[str] = set()
    counts = {"disk": 0, "zip": 0, "voice": 0}
    plan: dict[str, tuple[str, list[PurePosixPath]]] = {}
    for d in all_dists:
        name = canonicalize_name(d.name)
        files = [PurePosixPath(str(f)) for f in d.files or []]
        owned.update(str(f) for f in files)
        if name in DROP_DISTS:
            continue
        files = [f for f in files if wanted(f) and (full_site / f).is_file()]
        native = any(f.suffix.lower() in NATIVE for f in files)
        # winrt 4 reads each namespace's projection table (_table.pywinrt) from a real folder:
        # zipped, Windows OCR fails to load and cue cards lose their underlines and lines.
        stay = name in STAY_ON_DISK or name.startswith("winrt-")
        where = "voice" if name in voice else "disk" if native or stay else "zip"
        plan[name] = (where, files)

    # A regular package (one with __init__.py) can't be split between the zip and a folder:
    # winrt-Windows.* add modules inside winrt-runtime's native "winrt" package, for example.
    def regular_tops(files: list[PurePosixPath]) -> set[str]:
        return {
            f.parts[0]
            for f in files
            if len(f.parts) > 1 and (full_site / f.parts[0] / "__init__.py").exists()
        }

    on_disk = set().union(*(regular_tops(f) for w, f in plan.values() if w == "disk"))
    for name, (where, files) in plan.items():
        if where == "zip" and regular_tops(files) & on_disk:
            plan[name] = ("disk", files)

    for where, files in plan.values():
        counts[where] += 1
        for f in files:
            src = full_site / f
            if where == "zip":
                packages.add_module(str(f), src.read_bytes(), "python-packages.zip")
            else:
                dest = (pack_dir if where == "voice" else site) / f
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
    packages.write()
    strays = sorted(
        p.relative_to(full_site).as_posix()
        for p in full_site.iterdir()
        if p.name != "__pycache__"
        and not any(o == p.name or o.startswith(p.name + "/") for o in owned)
    )
    if strays:
        print("note: in site-packages but owned by no package (left out):", strays)
    patch_playwright(site)
    shutil.copy2(RUNTIME / "sitecustomize.py", BUNDLE / "sitecustomize.py")
    # Python's search path, fixed: no PYTHONPATH, no user site, no current folder.
    (BUNDLE / f"{PY}._pth").write_text(
        f"{PY}.zip\nDLLs\nLib\\site-packages\npython-packages.zip\n.\nimport site\n",
        encoding="ascii",
    )
    write_voice_pack(pack_dir, version, {p.name for p in site.iterdir()})
    n = sum(1 for p in BUNDLE.rglob("*") if p.is_file())
    mb = sum(p.stat().st_size for p in BUNDLE.rglob("*") if p.is_file()) / 1e6
    print(
        f"slim bundle: {n} files, {mb:.0f} MB ({counts['disk']} packages as files, "
        f"{counts['zip']} zipped, {counts['voice']} in the voice pack) in "
        f"{time.monotonic() - started:.0f} s"
    )


def write_voice_pack(pack_dir: Path, version: str, base_names: set[str]) -> None:
    for old in DIST.glob("pyxi-*-pack-*.zip"):
        old.unlink()
    DIST.mkdir(parents=True, exist_ok=True)
    tmp = DIST / "voice-pack.tmp"
    z = Zip(tmp, zipfile.ZIP_LZMA)
    for f in sorted(pack_dir.rglob("*")):
        if f.is_file():
            z.add(f.relative_to(pack_dir).as_posix(), f.read_bytes())
    z.write()
    data = tmp.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    name = f"pyxi-voice-pack-{sha[:12]}.zip"
    tmp.replace(DIST / name)
    # What triggers the download: the pack's own top-level modules (not shared namespaces like
    # "google", which base packages use too, and not metadata folders).
    modules = sorted(
        p.stem if p.suffix == ".py" else p.name
        for p in pack_dir.iterdir()
        if p.name not in base_names
        and not p.name.endswith((".dist-info", ".libs"))
        and (p.is_dir() or p.suffix in (".py", ".pyd"))
        and p.name not in {"docs", "licensing", "google"}
    )
    manifest = [
        {
            "name": "voice",
            "file": name,
            "sha256": sha,
            "size": len(data),
            "url": f"https://github.com/{REPO}/releases/download/v{version}/{name}",
            "modules": modules,
        }
    ]
    (BUNDLE / "pyxi_packs.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    print(f"voice pack: {name} ({len(data) / 1e6:.0f} MB), loads on first import of {modules}")


# ---------------------------------------------------------------- checks


CHECK = r"""
import importlib, importlib.metadata as md, sys, zipfile, zoneinfo
from pathlib import Path
assert not any("site-packages" in p and "python-full" in p for p in sys.path), sys.path
import pyxi.server, pyxi.companion.brain, otto, uiautomation, pycaw
import faster_whisper, kokoro_onnx, ctranslate2, av, onnxruntime, soundfile
from playwright._impl._driver import compute_driver_executable, get_driver_env
assert compute_driver_executable()[0].endswith("Pyxi.exe"), compute_driver_executable()
assert get_driver_env()["ELECTRON_RUN_AS_NODE"] == "1"
zoneinfo.ZoneInfo("Europe/London")
import pyxi.screen.ocr  # Windows OCR: how cue cards find the quoted words to underline
assert pyxi.screen.ocr.available(), "Windows OCR doesn't load in the bundle"
md.version("pydantic"), md.version("langgraph"), md.version("anthropic")
here = Path(sys.executable).parent
tops = {n.split("/")[0].removesuffix(".pyc") for n in zipfile.ZipFile(here / "python-packages.zip").namelist()}
bad = []
for t in sorted(tops):
    if t.endswith(".dist-info") or t.startswith("_") or "." in t:
        continue
    try:
        importlib.import_module(t)
    except Exception as e:
        bad.append(f"{t}: {type(e).__name__}: {e}")
print("zipped packages that don't import:", bad or "none")
print("bundle ok:", len(tops), "zipped top-level names", flush=True)
# Leave the way the brain does (os._exit): with uiautomation's COM and winrt's both loaded,
# Python's own shutdown crashes tearing them down, after everything above has passed.
import os; os._exit(0)
"""


def check_bundle() -> None:
    """Prove it: the slim bundle imports the brain, every zipped package and (fetched from the
    fresh pack in desktop/dist, as an installed app would) the speech libraries."""
    with tempfile.TemporaryDirectory() as packs:
        env = {
            k: v for k, v in os.environ.items() if not k.startswith(("PYTHON", "VIRTUAL_ENV"))
        } | {"PYXI_PACK_SOURCE": str(DIST), "PYXI_PACKS_DIR": packs}
        subprocess.run([str(BUNDLE / "python.exe"), "-c", CHECK], check=True, cwd=packs, env=env)


# Run in the bundle: makes the wrappers Windows' automation and file search are used through.
WRAPPERS = """
import comtypes.client
comtypes.client.GetModule("UIAutomationCore.dll")  # UI Automation: how the screen is read
try:
    comtypes.client.CreateObject("ADODB.Connection")  # Windows Search: how files are found
    comtypes.client.CreateObject("ADODB.Recordset")
except Exception as exc:  # no such component on this PC: made on first use instead
    print("note: no file-search wrappers:", exc)
import os; os._exit(0)
"""


_STAMP = re.compile(r"^_check_version\('([^']+)', [0-9.]+\)\s*$", re.M)


def any_pc(module: Path) -> bool:
    """A shipped wrapper ends with the date of the Windows file it was made from, on the PC
    that built the installer. On any other PC that date is different, so comtypes threw the
    wrapper away and made it again at the first look at the screen: slow, and with several
    threads at it at once the first tester's came out broken and the screen could not be
    read at all. The wrapper now checks against the date of the file on the PC it runs on."""
    text = module.read_text(encoding="utf-8")
    new, n = _STAMP.subn(
        "def _made_from():\n"
        "    try:\n"
        "        return __import__('os').stat(typelib_path).st_mtime\n"
        "    except OSError:\n"
        "        return 0.0\n"
        "\n\n"
        r"_check_version('\1', _made_from())" "\n",
        text,
    )
    if n:
        module.write_text(new, encoding="utf-8", newline="\n")
    return bool(n)


def make_wrappers() -> None:
    """comtypes writes a Python module for each Windows component the first time it is used,
    into comtypes/gen inside the app's own folder. An update replaces that folder, so the
    first look at the screen after every install made them again: 28 seconds before the first
    answer, live, every time. They are made here once and shipped."""
    gen = BUNDLE / "Lib" / "site-packages" / "comtypes" / "gen"
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTHON", "VIRTUAL_ENV"))}
    with tempfile.TemporaryDirectory() as cwd:
        subprocess.run([str(BUNDLE / "python.exe"), "-c", WRAPPERS], check=True, cwd=cwd, env=env)
    for cache in gen.rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)
    for module in gen.glob("*.py"):
        any_pc(module)
    made = sorted(p.name for p in gen.glob("*.py") if p.name != "__init__.py")
    if "UIAutomationClient.py" not in made:
        raise SystemExit(f"STOP: the automation wrappers weren't made in {gen} (found {made}).")
    print(f"automation wrappers shipped: {len(made)} modules")


def ship_google_client() -> None:
    """Beta testers connect Gmail, Calendar and Docs with Pyxi's own Google OAuth client, so they
    only press Connect. The client JSON lives in desktop/build-secrets (git-ignored) and goes in
    beside the bundled Python (pyxi.connectors.google.bundled_client)."""
    src = DESKTOP / "build-secrets" / "google_client.json"
    dest = BUNDLE / "google_client.json"
    if src.is_file():
        shutil.copy2(src, dest)
        print("google: shipping Pyxi's OAuth client")
    else:
        dest.unlink(missing_ok=True)
        print("google: no desktop/build-secrets/google_client.json; testers would need their own")


def run(cmd: list[str], check: bool = True) -> None:
    print("+", " ".join(cmd[:6]), "..." if len(cmd) > 6 else "")
    subprocess.run(cmd, check=check, cwd=ROOT)


def ship_browser_extension(out: Path | None = None) -> Path:
    """Both builds of the browser extension, for the installer to carry
    (resources/browser-extension, see "extraResources" in desktop/package.json): chrome/ is
    what Chrome and Edge load unpacked, firefox/ is for a temporary install while no signed
    add-on is published. Pyxi copies them to a folder whose path never changes
    (pyxi/companion/browsersetup.py)."""
    sys.path.insert(0, str(ROOT / "extension"))
    try:
        from build import build
    finally:
        sys.path.remove(str(ROOT / "extension"))
    out = out or DESKTOP / "build" / "browser-extension"
    for target in ("chrome", "firefox"):
        build(target, out / target)
    return out


def main() -> None:
    version = json.loads((DESKTOP / "package.json").read_text(encoding="utf-8"))["version"]
    adopt_old_bundle()
    if "--update-pyxi" in sys.argv:
        pip("--no-deps", "--force-reinstall", str(ROOT))
    elif "--skip-python" not in sys.argv:
        copy_python()
        install_packages()
    if "--skip-python" not in sys.argv:
        slim(version)
        check_bundle()
        make_wrappers()
    ship_google_client()
    ship_browser_extension()
    npm = shutil.which("npm") or "npm"
    subprocess.run([npm, "run", "build"], check=True, cwd=DESKTOP, shell=sys.platform == "win32")
    subprocess.run([npm, "run", "dist"], check=True, cwd=DESKTOP, shell=sys.platform == "win32")
    for f in sorted(DIST.glob("Pyxi-Setup*.exe")) + sorted(DIST.glob("pyxi-*-pack-*.zip")):
        print(f"{f.name}: {f.stat().st_size / 1e6:.0f} MB")


if __name__ == "__main__":
    main()
