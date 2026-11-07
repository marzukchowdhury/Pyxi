"""Private apps: never captured in front, blacked out anywhere else, and testers add their own."""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from pyxi import windows
from pyxi.config import ScreenConfig
from pyxi.screen import context, private_apps, uia
from pyxi.screen.context import ScreenReader, WindowInfo


@pytest.fixture
def reader(monkeypatch):
    monkeypatch.setattr(context, "enable_dpi_awareness", lambda: None)
    monkeypatch.setattr(uia, "read_taskbar", lambda: [])
    return ScreenReader(ScreenConfig(ocr=False))


@pytest.mark.parametrize(
    ("title", "process"),
    [
        ("Bitwarden", "Bitwarden.exe"),
        ("1Password", "1Password.exe"),
        ("Vault - KeePassXC", "KeePassXC.exe"),
        ("Dashlane", "Dashlane.exe"),
        ("LastPass", "chrome.exe"),
        ("Accounts - Big Bank - Edge", "msedge.exe"),
        ("Windows Default Lock Screen", "LockApp.exe"),
        ("", "LogonUI.exe"),
        ("User Account Control", "consent.exe"),
        ("Windows Security", "CredentialUIBroker.exe"),
    ],
)
def test_the_built_in_list_covers_password_managers_banking_lock_screen_and_uac(
    reader, title, process
):
    assert reader.is_excluded(WindowInfo(title, process))


def test_ordinary_windows_are_seen(reader):
    for title, process in [("Budget.xlsx - Excel", "EXCEL.EXE"), ("Bankruptcy law - Wikipedia", "msedge.exe")]:
        assert reader.is_excluded(WindowInfo(title, process)) is None


def test_testers_add_their_own_apps_as_plain_names(reader, tmp_path):
    assert private_apps.save(tmp_path, ["  Quicken ", "quicken", "my-vpn.exe", "x", 7]) == [
        "Quicken",
        "my-vpn.exe",
    ]  # trimmed, no repeats, nothing too short to mean anything
    reader.set_private_apps(private_apps.load(tmp_path))
    assert reader.is_excluded(WindowInfo("Quicken 2026 - Home", "qw.exe"))
    assert reader.is_excluded(WindowInfo("Connected", "my-vpn.exe"))
    assert reader.is_excluded(WindowInfo("Bitwarden", "Bitwarden.exe"))  # built-ins stay
    assert reader.is_excluded(WindowInfo("Quickenings", "notepad.exe")) is None  # whole words
    assert private_apps.load(tmp_path / "nowhere") == []


def test_a_private_window_in_front_is_never_captured(reader, monkeypatch):
    raw = uia.RawWindow("Vault - KeePassXC", "KeePassXC.exe", (0, 0, 800, 600), 11)
    monkeypatch.setattr(uia, "read_foreground", lambda **kw: (raw, []))

    def no_pixels(*a, **k):
        raise AssertionError("a private window must not be photographed")

    monkeypatch.setattr(ScreenReader, "_grab", staticmethod(no_pixels))
    ctx = reader._capture_sync(True)
    assert ctx.excluded and ctx.screenshot is None and not ctx.elements
    assert "cannot see it" in ctx.render()


def test_a_private_window_elsewhere_on_screen_is_blacked_out(reader, monkeypatch):
    raw = uia.RawWindow("Notes - Notepad", "notepad.exe", (0, 0, 300, 300), 1)
    monkeypatch.setattr(uia, "read_foreground", lambda **kw: (raw, []))
    monkeypatch.setattr(
        ScreenReader,
        "_grab",
        staticmethod(lambda rect, follow=True: (Image.new("RGB", (800, 600), "white"), (100, 50))),
    )
    monkeypatch.setattr(
        windows,
        "open_windows",
        lambda minimized=False: [(1, "Notes - Notepad", r"C:\Windows\notepad.exe"), (2, "Bitwarden", r"C:\Apps\Bitwarden.exe")],
    )
    monkeypatch.setattr(windows, "rect_of", lambda hwnd: {2: (500, 250, 200, 100)}[hwnd])
    ctx = reader._capture_sync(True)
    shot = ctx.screenshot
    assert shot.getpixel((450, 250)) == (0, 0, 0)  # inside Bitwarden (screen 550,300)
    assert shot.getpixel((400, 200)) == (0, 0, 0) and shot.getpixel((599, 299)) == (0, 0, 0)
    assert shot.getpixel((399, 199)) == (255, 255, 255) and shot.getpixel((601, 301)) == (255, 255, 255)
    assert shot.getpixel((50, 50)) == (255, 255, 255)  # Notepad, the window asked about


def test_settings_round_trip_and_the_brain_says_it_cant_see(settings):
    from conftest import FakeScreen, Recorder
    from pyxi.companion.brain import Brain
    from pyxi.demo import DemoLLM
    from pyxi.hub import Hub
    from pyxi.server import create_app
    from pyxi.voice import make_tts

    class Screen(FakeScreen):
        def __init__(self):
            super().__init__()
            self.names: list[str] = []

        def set_private_apps(self, names):
            self.names = names

    hub = Hub()
    screen = Screen()
    brain = Brain(settings, hub, llm=DemoLLM(), screen=screen, tts=make_tts(settings.voice, settings.models_dir))
    app = create_app(settings, demo=True, brain=brain, key="k")
    with TestClient(app) as client, client.websocket_connect("/ws?key=k") as ws:
        ws.receive_text()
        ws.send_text(json.dumps({"type": "private_apps_save", "apps": ["Quicken"]}))
        got = json.loads(ws.receive_text())
        assert got["type"] == "private_apps" and got["apps"] == ["Quicken"]
        assert any("lock screen" in b for b in got["built_in"]) and screen.names == ["Quicken"]
    assert private_apps.load(settings.data_dir) == ["Quicken"]

    rec = Recorder(hub)
    screen.ctx = context.ScreenContext(time.time(), WindowInfo("Bitwarden", "Bitwarden.exe"), excluded="Bitwarden")

    async def turn():
        await brain.turn("what's my github password?")

    import asyncio

    asyncio.run(turn())
    notes = [e["message"] for e in rec.events if e["type"] == "notice"]
    assert notes == ["Bitwarden is private, so I can't see that window."]
