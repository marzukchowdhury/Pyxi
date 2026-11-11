"""LibreOffice on demand: the free converter that gives Word, Excel and PowerPoint files their exact
layout as PDFs, and opens old .doc, .xls and .ppt files, without an Office license.

It isn't in the installer (it's about 380 MB). The first time Pixel needs it, the user sees one
card; if they allow it, Pyxi downloads the official installer from The Document Foundation in the
background, checks it (the SHA-256 TDF publishes, and on Windows the code signature, which must be
The Document Foundation's), and unpacks it with ``msiexec /a`` into Pyxi's own folder,
``%LOCALAPPDATA%\\Pyxi\\LibreOffice``. That needs no admin rights, adds nothing to the Start menu
and changes no file associations. Until it's ready, Pixel keeps using its built-in renderer.

The user's choice lives in the data dir as ``libreoffice.json``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger(__name__)

STABLE = "https://download.documentfoundation.org/libreoffice/stable/"
CHOICE = "libreoffice.json"
MARKER = "pyxi-libreoffice.json"
SIGNER = "The Document Foundation"
APPROX_MB = 380

Progress = Callable[["Status"], Awaitable[None]]

# Held for each run of soffice. Two at once share Pyxi's one profile, the second hands its file
# to the first and exits before the PDF exists, and that job reads as "couldn't convert".
CONVERTING = threading.Lock()


def home() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "Pyxi" / "LibreOffice"


def profile_dir() -> Path:
    """Pyxi's own LibreOffice profile: never the user's, so a LibreOffice they have open (or their
    settings) can't collide with Pixel's conversions."""
    return home() / "profile"


def command(soffice: str | Path, *args: str) -> list[str]:
    """A headless soffice command line with Pyxi's private profile."""
    return [
        str(soffice),
        f"-env:UserInstallation={profile_dir().as_uri()}",
        "--headless",
        "--norestore",
        "--nologo",
        "--nodefault",
        *args,
    ]


def warm_up(soffice: str | Path) -> bool:
    """Convert one tiny Word file now, so LibreOffice builds its profile and first loads Writer
    and the PDF exporter during setup (about 20 s), not during the user's first conversion."""
    import tempfile as _tempfile

    try:
        from docx import Document

        with _tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "warm-up.docx"
            doc = Document()
            doc.add_paragraph("Pyxi")
            doc.save(str(src))
            with CONVERTING:
                subprocess.run(
                    command(soffice, "--convert-to", "pdf", "--outdir", tmp, str(src)),
                    capture_output=True,
                    timeout=180,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            return (Path(tmp) / "warm-up.pdf").is_file()
    except (OSError, subprocess.SubprocessError, ImportError):
        return False


def installed(root: Path | None = None) -> Path | None:
    """soffice.exe from Pyxi's own copy, if it's been set up."""
    root = root or home()
    try:
        info = json.loads((root / MARKER).read_text(encoding="utf-8"))
        path = Path(info["soffice"])
        return path if path.is_file() else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


# ---------------------------------------------------------------------------- the user's choice
@dataclass
class Choice:
    asked: bool = False
    allowed: bool = False


def load_choice(data_dir: Path) -> Choice:
    try:
        raw = json.loads((Path(data_dir) / CHOICE).read_text(encoding="utf-8"))
        return Choice(bool(raw.get("asked")), bool(raw.get("allowed")))
    except (OSError, ValueError):
        return Choice()


def save_choice(data_dir: Path, choice: Choice) -> Choice:
    Path(data_dir).mkdir(parents=True, exist_ok=True)
    (Path(data_dir) / CHOICE).write_text(json.dumps(asdict(choice)), encoding="utf-8")
    return choice


# ---------------------------------------------------------------------------- finding the release
def _version_key(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split("."))


def pick_version(listing_html: str) -> str:
    versions = set(re.findall(r'href="(\d+\.\d+\.\d+)/"', listing_html))
    if not versions:
        raise RuntimeError("Couldn't find a LibreOffice release to download.")
    return max(versions, key=_version_key)


def release_urls(version: str) -> tuple[str, str]:
    name = f"LibreOffice_{version}_Win_x86-64.msi"
    base = f"{STABLE}{version}/win/x86_64/{name}"
    return base, base + ".sha256"


def parse_sha256(text: str) -> str:
    m = re.search(r"\b([0-9a-fA-F]{64})\b", text)
    if not m:
        raise RuntimeError("The download's checksum file didn't have a checksum in it.")
    return m.group(1).lower()


# ---------------------------------------------------------------------------- checks
def signer_of(path: Path) -> str | None:
    """The Authenticode signer of a Windows file, if its signature is valid (else None)."""
    if sys.platform != "win32":
        return SIGNER  # nothing to check against off Windows; the SHA-256 still has to match
    script = (
        f"$s = Get-AuthenticodeSignature -LiteralPath '{str(path).replace(chr(39), chr(39) * 2)}'; "
        "if ($s.Status -eq 'Valid') { $s.SignerCertificate.Subject }"
    )
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return out or None


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def unpack(msi: Path, target: Path) -> Path:
    """An administrative install: the files only, into target. Returns soffice.exe."""
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True)
    logfile = target.parent / "unpack.log"
    result = subprocess.run(
        ["msiexec", "/a", str(msi), "/qn", f"TARGETDIR={target}", "/L*", str(logfile)],
        capture_output=True,
        timeout=1800,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode not in (0, 3010):
        raise RuntimeError(f"Unpacking LibreOffice failed (msiexec {result.returncode}).")
    found = find_soffice(target)
    if found is None:
        raise RuntimeError("LibreOffice unpacked, but soffice.exe wasn't in it.")
    return found


def find_soffice(root: Path) -> Path | None:
    for candidate in root.rglob("soffice.exe"):
        if candidate.parent.name.lower() == "program":
            return candidate
    return None


# ---------------------------------------------------------------------------- the installer
@dataclass
class Status:
    state: str = "missing"  # missing, downloading, checking, unpacking, ready, failed
    progress: float = 0.0  # 0..1 while downloading
    version: str = ""
    message: str = ""

    def public(self) -> dict[str, Any]:
        return asdict(self)


class Installer:
    """One download at a time per process; everyone who needs it waits on the same task."""

    def __init__(
        self,
        root: Path | None = None,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
        staging: Path | None = None,
    ) -> None:
        self.root = root or home()
        # The download and the unpack happen in TEMP, then the finished copy moves into root: the
        # Windows Installer service can be refused files in some app folders (their permissions),
        # and a failed setup leaves nothing half-done in Pyxi's folder.
        self.staging = staging or Path(tempfile.gettempdir()) / "pyxi-libreoffice"
        self.client_factory = client_factory or (
            lambda: httpx.AsyncClient(timeout=60, follow_redirects=True)
        )
        self._task: asyncio.Task[Path | None] | None = None
        self._listeners: list[Progress] = []
        self.status = Status("ready" if installed(self.root) else "missing")
        if found := installed(self.root):
            self.status.version = found.parent.parent.name

    def soffice(self) -> Path | None:
        return installed(self.root)

    def listen(self, fn: Progress) -> None:
        if fn not in self._listeners:
            self._listeners.append(fn)

    def unlisten(self, fn: Progress) -> None:
        if fn in self._listeners:
            self._listeners.remove(fn)

    async def _tell(self) -> None:
        for fn in list(self._listeners):
            try:
                await fn(self.status)
            except Exception:
                log.exception("libreoffice progress listener failed")

    def start(self) -> asyncio.Task[Path | None]:
        """Begin (or join) the download and setup."""
        if self.soffice():
            loop = asyncio.get_running_loop()
            done: asyncio.Future[Path | None] = loop.create_future()
            done.set_result(self.soffice())
            return asyncio.ensure_future(done)
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())
        return self._task

    async def wait(self) -> Path | None:
        return await asyncio.shield(self.start())

    async def _run(self) -> Path | None:
        try:
            return await self._install()
        except Exception as exc:
            log.exception("LibreOffice setup failed")
            self.status = Status("failed", 0.0, self.status.version, _plain(exc))
            await self._tell()
            return None

    async def _install(self) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        async with self.client_factory() as client:
            listing = await client.get(STABLE)
            listing.raise_for_status()
            version = pick_version(listing.text)
            msi_url, sha_url = release_urls(version)
            self.status = Status("downloading", 0.0, version, f"Downloading LibreOffice {version}")
            await self._tell()
            sha = await client.get(sha_url)
            sha.raise_for_status()
            expected = parse_sha256(sha.text)
            if self.staging.exists():
                shutil.rmtree(self.staging, ignore_errors=True)
            msi = self.staging / "download" / Path(msi_url).name
            msi.parent.mkdir(parents=True, exist_ok=True)
            part = msi.with_suffix(".part")
            last = 0.0
            async with client.stream("GET", msi_url) as r:
                r.raise_for_status()
                total = int(r.headers.get("content-length") or APPROX_MB * 1_000_000)
                got = 0
                with part.open("wb") as fh:
                    async for chunk in r.aiter_bytes(1 << 20):
                        fh.write(chunk)
                        got += len(chunk)
                        now = time.monotonic()
                        if now - last > 1.0:
                            last = now
                            self.status.progress = min(got / total, 0.999)
                            await self._tell()
            part.replace(msi)
        self.status = Status("checking", 1.0, version, "Checking the download")
        await self._tell()
        actual = await asyncio.to_thread(sha256_of, msi)
        if actual != expected:
            msi.unlink(missing_ok=True)
            raise RuntimeError(
                "The download didn't match LibreOffice's published checksum, so I deleted it."
            )
        signer = await asyncio.to_thread(signer_of, msi)
        if not signer or SIGNER not in signer:
            msi.unlink(missing_ok=True)
            raise RuntimeError(
                "The download isn't signed by The Document Foundation, so I deleted it."
            )
        self.status = Status("unpacking", 1.0, version, "Setting it up")
        await self._tell()
        try:
            staged = await asyncio.to_thread(unpack, msi, self.staging / version)
            target = self.root / version
            if target.exists():
                await asyncio.to_thread(shutil.rmtree, target, True)
            await asyncio.to_thread(shutil.move, str(self.staging / version), str(target))
            soffice = target / staged.relative_to(self.staging / version)
        finally:
            await asyncio.to_thread(shutil.rmtree, self.staging, True)  # the 380 MB download too
        if not soffice.is_file():
            raise RuntimeError("LibreOffice was set up, but soffice.exe went missing.")
        self.status = Status("unpacking", 1.0, version, "Getting it ready")
        await self._tell()
        await asyncio.to_thread(warm_up, soffice)
        (self.root / MARKER).write_text(
            json.dumps({"version": version, "soffice": str(soffice), "installed_at": time.time()}),
            encoding="utf-8",
        )
        for old in self.root.iterdir():  # older versions Pyxi set up before
            if (
                old.is_dir()
                and old.name not in (version, "profile")
                and re.fullmatch(r"\d+\.\d+\.\d+", old.name)
            ):
                shutil.rmtree(old, ignore_errors=True)
        self.status = Status("ready", 1.0, version, f"LibreOffice {version} is ready")
        await self._tell()
        return soffice


def _plain(exc: BaseException) -> str:
    text = str(exc).strip()
    low = text.lower()
    if isinstance(exc, httpx.HTTPError) or "connect" in low or "timed out" in low:
        return "I couldn't download LibreOffice. Is the internet connection up?"
    if "space" in low or "errno 28" in low:
        return "There isn't enough free disk space for LibreOffice (it needs about 1.5 GB)."
    return text.splitlines()[0][:200] if text else type(exc).__name__
