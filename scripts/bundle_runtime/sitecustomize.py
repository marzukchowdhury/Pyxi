"""Pyxi's bundled Python: big optional parts arrive on first use.

The installer leaves out the speech libraries (faster-whisper, CTranslate2, PyAV, onnxruntime,
Kokoro and friends, about 75 MB to download) so it stays small. They're one "pack", a zip
published next to the installer on the GitHub release. The first time the brain imports one of
them (the speech model warm-up does that in a background thread a moment after start), this
downloads the pack once into the brain's data folder (.pyxi/packs) and the import carries on. Until then nothing else
waits: the brain, the panel and the agents all work without it.

The pack list is pyxi_packs.json, written beside this file by scripts/build_installer.py.
PYXI_PACK_SOURCE (a folder or a base URL) overrides where packs come from, for testing a build
before its release exists.
"""

from __future__ import annotations

import hashlib
import importlib.machinery
import json
import logging
import os
import shutil
import sys
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_MANIFEST = _HERE / "pyxi_packs.json"
_RETRY_AFTER_S = 60.0
log = logging.getLogger("pyxi.packs")


def _home() -> Path:
    """Beside the voice models, in the brain's data folder (pyxi.config: PYXI_DATA_DIR, by
    default .pyxi in the folder the app starts the brain in, its user-data folder)."""
    if os.environ.get("PYXI_PACKS_DIR"):
        return Path(os.environ["PYXI_PACKS_DIR"])
    return (Path(os.environ.get("PYXI_DATA_DIR") or ".pyxi") / "packs").resolve()


class _Pack:
    def __init__(self, spec: dict) -> None:
        self.name: str = spec["name"]
        self.file: str = spec["file"]
        self.sha256: str = spec["sha256"]
        self.size: int = spec["size"]
        self.url: str = spec["url"]
        self.modules: set[str] = set(spec["modules"])
        self.dir = _home() / f"{self.name}-{self.sha256[:12]}"
        self._lock = threading.Lock()
        self._failed_at = 0.0

    @property
    def ready(self) -> bool:
        return (self.dir / ".complete").exists()

    def activate(self) -> None:
        path = str(self.dir)
        if path not in sys.path:
            sys.path.append(path)
            importlib.invalidate_caches()

    def ensure(self) -> bool:
        """Download and unpack once. False (and quietly retried later) when offline."""
        with self._lock:
            if self.ready:
                self.activate()
                return True
            if time.monotonic() - self._failed_at < _RETRY_AFTER_S:
                return False
            try:
                self._install()
            except Exception as exc:
                self._failed_at = time.monotonic()
                log.warning("couldn't get Pyxi's %s pack yet: %s", self.name, exc)
                return False
            self.activate()
            return True

    def _source(self) -> str:
        override = os.environ.get("PYXI_PACK_SOURCE")
        if override:
            return override.rstrip("/\\") + "/" + self.file
        return self.url

    def _install(self) -> None:
        home = _home()
        home.mkdir(parents=True, exist_ok=True)
        part = home / f"{self.file}.part"
        src = self._source()
        log.info("downloading Pyxi's %s pack (%d MB) from %s", self.name, self.size >> 20, src)
        started = time.monotonic()
        digest = hashlib.sha256()
        if "://" not in src:
            src = Path(src).resolve().as_uri()
        req = urllib.request.Request(src, headers={"User-Agent": "Pyxi"})
        with urllib.request.urlopen(req, timeout=60) as resp, part.open("wb") as out:
            while chunk := resp.read(1 << 20):
                digest.update(chunk)
                out.write(chunk)
        if digest.hexdigest() != self.sha256:
            part.unlink(missing_ok=True)
            raise RuntimeError("the download was damaged (checksum mismatch)")
        staging = home / f"{self.dir.name}.unpacking"
        shutil.rmtree(staging, ignore_errors=True)
        with zipfile.ZipFile(part) as z:
            z.extractall(staging)
        part.unlink(missing_ok=True)
        (staging / ".complete").write_text(self.sha256, encoding="ascii")
        shutil.rmtree(self.dir, ignore_errors=True)
        staging.rename(self.dir)
        for old in home.glob(f"{self.name}-*"):  # an earlier version's copy
            if old != self.dir and old.is_dir():
                shutil.rmtree(old, ignore_errors=True)
        log.info("%s pack ready in %.0f s", self.name, time.monotonic() - started)


class _PackFinder:
    """Finds nothing itself: on the first import of a module that lives in a pack that isn't
    here yet, it fetches the pack, then lets the normal import go ahead."""

    def __init__(self, packs: list[_Pack]) -> None:
        self.by_module = {m: p for p in packs for m in p.modules}

    def find_spec(self, fullname, path=None, target=None):
        if path is not None:
            return None
        pack = self.by_module.get(fullname.partition(".")[0])
        if pack is None or not pack.ensure():
            return None
        return importlib.machinery.PathFinder.find_spec(fullname, [str(pack.dir)])


def _install_hook() -> None:
    if not _MANIFEST.is_file() or os.environ.get("PYXI_PACKS") == "off":
        return
    packs = [_Pack(s) for s in json.loads(_MANIFEST.read_text(encoding="utf-8"))]
    waiting = []
    for pack in packs:
        if pack.ready:
            pack.activate()
        else:
            waiting.append(pack)
    if waiting:
        sys.meta_path.insert(0, _PackFinder(waiting))


_install_hook()
