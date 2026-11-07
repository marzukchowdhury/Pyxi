"""LibreOffice on demand and the OCR fallback. The download is served by a fake TDF (no network),
and unpacking is stubbed, so nothing is installed on this PC."""

from __future__ import annotations

import asyncio
import hashlib
import subprocess
from pathlib import Path

import httpx
import pytest
from docx import Document
from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfReader

from pyxi.agents import libreoffice, pixel, pixel_ocr, pixel_render
from pyxi.agents.ledger import LedgerError, open_local
from pyxi.agents.libreoffice import Choice, Installer
from pyxi.companion import filework
from test_brain_and_server import make_brain

MSI = b"MZ fake LibreOffice installer " * 1000
LISTING = (
    '<a href="25.8.7/">25.8.7/</a> <a href="26.8.2/">26.8.2/</a> <a href="26.2.7/">26.2.7/</a>'
)


def fake_tdf(msi: bytes = MSI, sha: str | None = None) -> httpx.MockTransport:
    digest = sha or hashlib.sha256(msi).hexdigest()

    def handle(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == libreoffice.STABLE:
            return httpx.Response(200, text=LISTING)
        if url.endswith("LibreOffice_26.8.2_Win_x86-64.msi.sha256"):
            return httpx.Response(200, text=f"{digest}  LibreOffice_26.8.2_Win_x86-64.msi")
        if url.endswith("LibreOffice_26.8.2_Win_x86-64.msi"):
            return httpx.Response(200, content=msi, headers={"content-length": str(len(msi))})
        return httpx.Response(404)

    return httpx.MockTransport(handle)


def installer(tmp_path: Path, transport: httpx.MockTransport) -> Installer:
    return Installer(
        root=tmp_path / "LO",
        client_factory=lambda: httpx.AsyncClient(transport=transport),
        staging=tmp_path / "staging",
    )


@pytest.fixture
def stub_setup(monkeypatch):
    """A valid signature, and an unpack that lays out program\\soffice.exe like the real one."""
    monkeypatch.setattr(
        libreoffice,
        "signer_of",
        lambda path: "CN=The Document Foundation, O=The Document Foundation",
    )
    unpacked: list[Path] = []

    def unpack(msi: Path, target: Path) -> Path:
        assert msi.read_bytes() == MSI
        program = target / "LibreOffice" / "program"
        program.mkdir(parents=True)
        (program / "soffice.exe").write_bytes(b"exe")
        unpacked.append(target)
        return program / "soffice.exe"

    monkeypatch.setattr(libreoffice, "unpack", unpack)
    monkeypatch.setattr(
        libreoffice, "warm_up", lambda soffice: unpacked.append(Path(soffice)) or True
    )
    return unpacked


def test_picking_the_newest_release():
    assert libreoffice.pick_version(LISTING) == "26.8.2"
    msi, sha = libreoffice.release_urls("26.8.2")
    assert (
        msi.endswith("/26.8.2/win/x86_64/LibreOffice_26.8.2_Win_x86-64.msi")
        and sha == msi + ".sha256"
    )
    assert libreoffice.parse_sha256("AB" * 32 + "  file.msi") == "ab" * 32
    with pytest.raises(RuntimeError):
        libreoffice.pick_version("<html>nothing</html>")


async def test_download_check_and_setup(tmp_path, stub_setup):
    lo = installer(tmp_path, fake_tdf())
    seen: list[str] = []
    warmed = stub_setup

    async def listen(status):
        seen.append(status.state)

    lo.listen(listen)
    soffice = await lo.wait()
    assert soffice == tmp_path / "LO" / "26.8.2" / "LibreOffice" / "program" / "soffice.exe"
    assert libreoffice.installed(tmp_path / "LO") == soffice
    assert seen[0] == "downloading" and seen[-4:] == ["checking", "unpacking", "unpacking", "ready"]
    assert (
        warmed[-1] == soffice
    )  # LibreOffice's first start happens during setup, not on their file
    assert lo.status.version == "26.8.2"
    assert not (tmp_path / "staging").exists()  # the 380 MB installer is cleaned up
    assert await lo.wait() == soffice and len(stub_setup) == 2  # set up (and warmed) once


async def test_a_tampered_download_is_deleted(tmp_path, stub_setup):
    lo = installer(tmp_path, fake_tdf(sha="0" * 64))
    assert await lo.wait() is None
    assert lo.status.state == "failed" and "checksum" in lo.status.message
    assert not list((tmp_path / "staging").rglob("*.msi")) and not stub_setup


async def test_an_unsigned_download_is_deleted(tmp_path, stub_setup, monkeypatch):
    monkeypatch.setattr(libreoffice, "signer_of", lambda path: "CN=Someone Else")
    lo = installer(tmp_path, fake_tdf())
    assert await lo.wait() is None and "signed by The Document Foundation" in lo.status.message
    assert not stub_setup


async def test_no_internet_says_so(tmp_path):
    def offline(request):
        raise httpx.ConnectError("no route")

    lo = installer(tmp_path, httpx.MockTransport(offline))
    assert await lo.wait() is None and "internet" in lo.status.message


def test_pixel_uses_pyxis_own_copy(tmp_path, monkeypatch):
    root = tmp_path / "LO"
    program = root / "26.8.2" / "program"
    program.mkdir(parents=True)
    (program / "soffice.exe").write_bytes(b"exe")
    (root / libreoffice.MARKER).write_text(
        f'{{"soffice": "{(program / "soffice.exe").as_posix()}"}}'
    )
    monkeypatch.setattr(libreoffice, "home", lambda: root)
    assert Path(pixel.libreoffice()) == program / "soffice.exe"


# ---------------------------------------------------------------------------- in a Pixel job
def word(path: Path) -> Path:
    doc = Document()
    doc.add_heading("Offer", 1)
    doc.add_paragraph("Start date: 1 December.")
    doc.save(path)
    return path


async def _answer(brain, rec, value):
    for _ in range(300):
        for card in rec.of("confirm"):
            if "LibreOffice" in card["action"] and brain.hub.resolve(card["request_id"], value):
                return card
        await asyncio.sleep(0.01)
    raise AssertionError("no LibreOffice card")


@pytest.fixture
def no_system_libreoffice(monkeypatch, tmp_path):
    monkeypatch.setattr(libreoffice, "home", lambda: tmp_path / "nowhere")
    monkeypatch.setattr(pixel.shutil, "which", lambda name: None)
    monkeypatch.setenv("PROGRAMFILES", str(tmp_path / "pf"))
    monkeypatch.setenv("PROGRAMFILES(X86)", str(tmp_path / "pf86"))


async def test_first_office_pdf_asks_then_converts_now(
    settings, tmp_path, stub_setup, no_system_libreoffice
):
    brain, rec = make_brain(settings)
    brain.jobs._converter = installer(tmp_path, fake_tdf())
    src = word(tmp_path / "offer.docx")
    await filework.handle(brain, "convert_files", {"operation": "to_pdf", "files": [str(src)]})
    card = await _answer(brain, rec, True)
    assert "380 MB" in card["action"] and "no admin rights" in card["reason"]
    await brain.jobs.wait_all()
    [job] = brain.jobs.jobs.values()
    assert (
        job.status == "done" and "Start date" in PdfReader(job.outputs[0]).pages[0].extract_text()
    )
    assert "LibreOffice is downloading" in job.summary and "Install LibreOffice" not in job.summary
    await brain.jobs._converter.wait()
    assert brain.jobs._converter.status.state == "ready"
    assert libreoffice.load_choice(settings.data_dir) == Choice(True, True)
    assert rec.of("libreoffice")[-1]["state"] == "ready"  # Settings hears the progress


async def test_an_old_doc_waits_for_the_converter(
    settings, tmp_path, stub_setup, no_system_libreoffice, monkeypatch
):
    libreoffice.save_choice(settings.data_dir, Choice(True, True))
    brain, rec = make_brain(settings)
    lo = installer(tmp_path, fake_tdf())
    brain.jobs._converter = lo
    converted: list[str] = []

    def soffice_pdf(soffice: str, src: Path, target: Path) -> None:
        converted.append(soffice)
        pixel_render.to_pdf(word(tmp_path / "stand-in.docx"), target)

    monkeypatch.setattr(pixel, "_soffice_pdf", soffice_pdf)
    monkeypatch.setattr(pixel, "libreoffice", lambda: str(lo.soffice()) if lo.soffice() else None)
    (tmp_path / "old letter.doc").write_bytes(b"\xd0\xcf\x11\xe0 old word file")
    await filework.handle(
        brain, "convert_files", {"operation": "to_pdf", "files": [str(tmp_path / "old letter.doc")]}
    )
    await brain.jobs.wait_all()
    [job] = [j for j in brain.jobs.jobs.values() if "old letter" in j.task]
    assert job.status == "done", job.summary
    assert converted and converted[0].endswith("soffice.exe")
    assert Path(job.outputs[0]).name == "old letter.pdf"
    statuses = [e["data"]["message"] for e in rec.of("job_event") if e["job_id"] == job.id]
    assert any("Downloading LibreOffice" in m for m in statuses)
    assert not rec.of("confirm")  # they'd already said yes


async def test_declined_means_old_files_explain_themselves(
    settings, tmp_path, no_system_libreoffice
):
    libreoffice.save_choice(settings.data_dir, Choice(True, False))
    brain, rec = make_brain(settings)
    (tmp_path / "old.xls").write_bytes(b"\xd0\xcf old")
    await filework.handle(
        brain, "convert_files", {"operation": "to_pdf", "files": [str(tmp_path / "old.xls")]}
    )
    await brain.jobs.wait_all()
    [job] = brain.jobs.jobs.values()
    assert job.status == "failed" and "Settings" in job.summary and not rec.of("confirm")
    # New formats still work with the built-in renderer, no card, no download.
    await filework.handle(
        brain, "convert_files", {"operation": "to_pdf", "files": [str(word(tmp_path / "a.docx"))]}
    )
    await brain.jobs.wait_all()
    assert sorted(j.status for j in brain.jobs.jobs.values()) == ["done", "failed"]


def test_ledger_opens_old_files_through_libreoffice(tmp_path, monkeypatch):
    (tmp_path / "old.doc").write_bytes(b"\xd0\xcf old")
    monkeypatch.setattr(pixel, "libreoffice", lambda: None)
    with pytest.raises(LedgerError, match="free LibreOffice converter"):
        open_local(tmp_path / "old.doc")
    monkeypatch.setattr(pixel, "libreoffice", lambda: "soffice.exe")

    def fake_run(cmd, **kw):
        out = Path(cmd[cmd.index("--outdir") + 1])
        word(out / "old.docx")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    doc = open_local(tmp_path / "old.doc")
    doc.apply({"op": "add_heading", "text": "Updated", "level": 2})
    saved = doc.save()
    assert saved == tmp_path / "old (edited by Pyxi).docx"
    assert [p.text for p in Document(str(saved)).paragraphs][-1] == "Updated"


# ---------------------------------------------------------------------------- OCR fallback
def scan(path: Path, text: str) -> Path:
    img = Image.new("RGB", (1600, 900), "white")
    font = ImageFont.truetype(r"C:\Windows\Fonts\arial.ttf", 56)
    ImageDraw.Draw(img).text((150, 300), text, fill="black", font=font)
    img.save(path)
    return path


@pytest.mark.skipif(pixel_ocr.rapid_engine() is None, reason="RapidOCR isn't installed")
def test_rapidocr_reads_when_windows_cant(tmp_path, monkeypatch):
    monkeypatch.setattr(pixel_ocr, "windows_available", lambda: False)
    lines, engine = pixel_ocr.read_lines(
        Image.open(scan(tmp_path / "s.png", "Loyer mensuel: 1450, payé le 1er"))
    )
    assert engine == "rapid" and "payé le 1er" in lines[0].text
    pdf = pixel.run("images_to_pdf", [tmp_path / "s.png"]).outputs[0]
    out = pixel.run("make_searchable", [pdf]).outputs[0]
    assert "Loyer mensuel" in PdfReader(str(out)).pages[0].extract_text()


@pytest.mark.skipif(pixel_ocr.rapid_engine() is None, reason="RapidOCR isn't installed")
def test_windows_ocr_with_nothing_falls_back(tmp_path, monkeypatch):
    from pyxi.screen import ocr

    monkeypatch.setattr(pixel_ocr, "windows_available", lambda: True)
    monkeypatch.setattr(ocr, "read_lines", lambda img: [])  # e.g. a language it doesn't have
    lines, engine = pixel_ocr.read_lines(Image.open(scan(tmp_path / "s.png", "Invoice total 1175")))
    assert engine == "rapid" and "1175" in lines[0].text


def test_no_ocr_at_all_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(pixel_ocr, "available", lambda: False)
    with pytest.raises(pixel.PixelError, match="text reader"):
        pixel.run("extract_text", [scan(tmp_path / "s.png", "x")])


def test_rapidocr_load_is_retried_after_a_failure(monkeypatch):
    """Offline at first (no voice pack yet): try again a minute later, not never."""
    import builtins
    import sys

    monkeypatch.setattr(pixel_ocr, "_rapid", None)
    monkeypatch.setattr(pixel_ocr, "_rapid_failed_at", None)
    real_import = builtins.__import__

    def offline_import(name, *args, **kwargs):
        if name.startswith("rapidocr_onnxruntime"):
            raise ImportError("no voice pack yet")
        return real_import(name, *args, **kwargs)

    monkeypatch.delitem(sys.modules, "rapidocr_onnxruntime", raising=False)
    monkeypatch.setattr(builtins, "__import__", offline_import)
    clock = [1000.0]
    monkeypatch.setattr(pixel_ocr.time, "monotonic", lambda: clock[0])
    assert pixel_ocr.rapid_engine() is None
    monkeypatch.setattr(builtins, "__import__", real_import)  # the pack has arrived
    clock[0] += 10
    assert pixel_ocr.rapid_engine() is None  # too soon to try again
    clock[0] += 60
    if pytest.importorskip("rapidocr_onnxruntime"):
        assert pixel_ocr.rapid_engine() is not None


def test_pack_pending_reads_the_import_hook_without_triggering_it(monkeypatch):
    import sys

    class Pack:
        ready = False

    class Finder:
        by_module = {"onnxruntime": Pack(), "faster_whisper": Pack()}

        def find_spec(self, *a, **k):
            raise AssertionError("must not trigger a download")

    assert pixel_ocr.pack_pending() is False  # no hook: a dev install, or the pack is in
    monkeypatch.setattr(sys, "meta_path", [Finder(), *sys.meta_path])
    assert pixel_ocr.pack_pending() is True
    Finder.by_module["onnxruntime"].ready = True
    assert pixel_ocr.pack_pending() is False


async def test_a_scan_job_says_it_is_getting_the_reader(settings, tmp_path, monkeypatch):
    monkeypatch.setattr(pixel_ocr, "windows_available", lambda: False)
    monkeypatch.setattr(pixel_ocr, "pack_pending", lambda: True)
    brain, rec = make_brain(settings)
    pdf = pixel.run("images_to_pdf", [scan(tmp_path / "s.png", "Rent 1450")]).outputs[0]
    await filework.handle(
        brain, "convert_files", {"operation": "extract_text", "files": [str(pdf)]}
    )
    await brain.jobs.wait_all()
    said = [e["data"]["message"] for e in rec.of("job_event")]
    assert "Getting the text reader first (about 75 MB, once)" in said
