"""The rest of the Mac port, on a fake Mac: apps, media, OCR, the Keychain, time zone, Spotlight,
screenshots in points, and the switch that swaps each Windows module's functions for the Mac's."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest
from PIL import Image

from macfakes import FakeMac, OCRCandidate, OCRObservation, install, rect

SRC = Path(__file__).resolve().parents[1] / "src" / "pyxi"


@pytest.fixture
def mac(monkeypatch) -> FakeMac:
    monkeypatch.setattr(sys, "platform", "darwin")
    return install(monkeypatch)


class Ran:
    def __init__(self, stdout: str = "", returncode: int = 0, stderr: str = "") -> None:
        self.stdout, self.returncode, self.stderr = stdout, returncode, stderr


# ---------------------------------------------------------------------- the switch
def _load_as_mac(monkeypatch, rel: str):
    """A fresh copy of a Windows module, imported as it would be on a Mac."""
    monkeypatch.setattr(sys, "platform", "darwin")
    name = "pyxi_mac_copy_" + rel.replace("/", "_").removesuffix(".py")
    spec = importlib.util.spec_from_file_location(name, SRC / rel)
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, mod)  # dataclasses look their module up
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize(
    ("rel", "mac_module", "names"),
    [
        ("windows.py", "pyxi.macos.windows", ["screens", "open_windows", "snap", "focus", "rect_of", "foreground", "available"]),
        ("screen/uia.py", "pyxi.macos.uia", ["read_foreground", "read_focus", "read_taskbar", "window_at", "cursor_pos", "browser_url"]),
        ("hands.py", "pyxi.macos.hands", ["Hands", "parse_keys", "is_own", "available"]),
        ("media.py", "pyxi.macos.media", ["volume", "set_volume", "play", "play_spotify", "spotify_installed"]),
        ("screen/ocr.py", "pyxi.macos.ocr", ["available", "read_lines"]),
        ("secretstore.py", "pyxi.macos.secretstore", ["MAGIC", "available", "protect", "unprotect"]),
        ("files_access.py", "pyxi.macos.files", ["search_index"]),
        ("companion/actions.py", "pyxi.macos.apps", ["APPS", "app_target", "open_app"]),
    ],
)
def test_on_a_mac_each_module_uses_the_mac_functions(monkeypatch, rel, mac_module, names):
    mod = _load_as_mac(monkeypatch, rel)
    mac = importlib.import_module(mac_module)
    for name in names:
        assert getattr(mod, name) is getattr(mac, name), f"{rel}: {name}"


@pytest.mark.skipif(sys.platform == "darwin", reason="on a Mac they are swapped")
def test_on_windows_nothing_is_swapped():
    from pyxi import hands, windows
    from pyxi.companion import actions
    from pyxi.screen import uia

    assert windows.screens.__module__ == "pyxi.windows"
    assert uia.read_foreground.__module__ == "pyxi.screen.uia"
    assert hands.Hands.__module__ == "pyxi.hands"
    assert actions.APPS["notepad"] == "notepad.exe"


def test_window_helpers_run_on_the_mac_list(monkeypatch, mac):
    """pyxi.windows' own logic (naming screens, the summary for the model) on Mac data."""
    win = _load_as_mac(monkeypatch, "windows.py")
    mac.screens = [(rect(0, 0, 1440, 900), rect(0, 70, 1440, 805)), (rect(1440, 0, 1920, 1080), rect(1440, 0, 1920, 1080))]
    mac.add_app(1, "Safari")
    mac.add_app(2, "Music")
    mac.add_window(10, 1, "Apple", 0, 25, 1000, 700)
    mac.add_window(11, 2, "Music", 1500, 100, 900, 700)
    assert win.screen_named("right")["rect"] == (1440, -180, 1920, 1080)
    assert win.screen_named("main")["name"] == "left"
    summary = win.screens_summary(mouse=(1600, 200))
    assert "Left screen: Apple" in summary and "Right screen (THE SCREENSHOT IS OF THIS ONE): Music" in summary
    assert win.named_window("pause the music", win.open_windows()) == 11


# ---------------------------------------------------------------------- apps and folders
def test_mac_apps_table():
    from pyxi.macos import apps

    assert apps.app_target("Notes") == "Notes"
    assert apps.app_target("open the calendar app".removeprefix("open ")) == "Calendar"
    assert apps.app_target("music") == "Music"
    assert apps.app_target("my music folder") == "~/Music"
    assert apps.app_target("Downloads folder") == "~/Downloads"
    assert apps.app_target("notepad.exe") == "TextEdit"
    assert apps.app_target("Spotify") == "spotify:"
    assert apps.app_target("a thing nobody has") is None
    assert apps.app_target("microphone privacy").startswith("x-apple.systempreferences:")


def test_open_commands():
    from pyxi.macos import apps

    assert apps.command("Safari") == ["open", "-a", "Safari"]
    assert apps.command("x-apple.systempreferences:com.apple.Sound-Settings.extension")[0:1] == ["open"]
    path = apps.command("~/Downloads")
    assert path[0] == "open" and path[1].endswith("Downloads") and "~" not in path[1]


def test_open_app_runs_open_and_reports_failures(monkeypatch):
    from pyxi.macos import apps

    ran = []
    monkeypatch.setattr(apps.subprocess, "run", lambda cmd, **_kw: ran.append(cmd) or Ran())
    assert asyncio.run(apps.open_app("notes")) == "Opened notes."
    assert ran == [["open", "-a", "Notes"]]
    monkeypatch.setattr(apps.subprocess, "run", lambda cmd, **_kw: Ran(returncode=1, stderr="Unable to find application named 'Steam'"))
    note = asyncio.run(apps.open_app("steam"))
    assert note.startswith("open_app: macOS couldn't open steam") and "Unable to find" in note
    assert asyncio.run(apps.open_app("zzz")).startswith("open_app: I can only open apps I know")


def test_spotify_without_the_app_opens_the_web_player(monkeypatch):
    import webbrowser

    from pyxi.macos import apps

    monkeypatch.setattr(apps, "spotify_installed", lambda: False)
    opened = []
    monkeypatch.setattr(webbrowser, "open", lambda url, new=0: opened.append(url))
    assert "browser" in asyncio.run(apps.open_app("spotify"))
    assert opened == ["https://open.spotify.com"]


# ---------------------------------------------------------------------- media
def test_volume_through_applescript(monkeypatch):
    from pyxi.macos import media

    scripts = []

    def run(cmd, **_kw):
        scripts.append(cmd[2])
        return Ran("40") if "get volume" in cmd[2] else Ran()

    monkeypatch.setattr(media.subprocess, "run", run)
    assert media.volume() == 0.4
    assert media.set_volume("70%") == "Volume 40% -> 70%."
    assert scripts[-1] == "set volume output volume 70"
    assert "without output muted" in scripts[-2]
    assert media.set_volume("banana").startswith("volume: I can set")


def test_play_opens_youtube_in_the_background(monkeypatch):
    import pyxi.media as shared
    from pyxi.macos import media

    async def found(_q):
        return "dQw4w9WgXcQ", "A song"

    monkeypatch.setattr(shared, "find_video", found)
    ran = []
    monkeypatch.setattr(media.subprocess, "run", lambda cmd, **_kw: ran.append(cmd) or Ran())
    played = asyncio.run(media.play("a song", music=True))
    assert played.ok and played.title == "A song"
    assert ran == [["open", "-g", "https://music.youtube.com/watch?v=dQw4w9WgXcQ"]]


def test_spotify_plays_over_applescript(monkeypatch):
    import pyxi.media as shared
    from pyxi.macos import media

    async def track(_q):
        return "4uLU6hMCjMI75M1A2tKUQC"

    async def no_sleep(_s):
        return None

    monkeypatch.setattr(shared, "_spotify_track", track)
    monkeypatch.setattr(media, "spotify_installed", lambda: True)
    monkeypatch.setattr(media.asyncio, "sleep", no_sleep)
    scripts = []

    def run(cmd, **_kw):
        scripts.append(cmd[-1])
        return Ran("playing") if "player state" in cmd[-1] else Ran()

    monkeypatch.setattr(media.subprocess, "run", run)
    played = asyncio.run(media.play_spotify("never gonna"))
    assert played.ok and played.url.endswith("4uLU6hMCjMI75M1A2tKUQC")
    assert scripts[0] == 'tell application "Spotify" to play track "spotify:track:4uLU6hMCjMI75M1A2tKUQC"'


# ---------------------------------------------------------------------- OCR (Vision)
def test_vision_lines_become_screen_boxes(mac):
    from pyxi.macos import ocr

    # A 1000x500 picture taken at (100, 50). Vision: 0-1, origin at the bottom left.
    words = {
        (0, 5): rect(0.1, 0.8, 0.1, 0.05),
        (6, 5): rect(0.25, 0.8, 0.1, 0.05),
    }
    line = OCRObservation(OCRCandidate("Hello world", words), rect(0.1, 0.8, 0.25, 0.05))
    crumb = OCRObservation(OCRCandidate("x", {}), rect(0, 0, 0.01, 0.01))
    mac.ocr_results = [line, crumb]
    found = ocr.read_lines(Image.new("RGB", (1000, 500)), origin=(100, 50))
    assert len(found) == 1
    assert found[0].text == "Hello world"
    assert found[0].words[0] == ("Hello", (200, 125, 100, 25))  # y: (1 - 0.8 - 0.05) * 500 + 50
    assert found[0].rect == (200, 125, 250, 25)


def test_no_vision_means_no_ocr(monkeypatch):
    from pyxi.macos import ocr

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setitem(sys.modules, "Vision", None)  # import fails
    monkeypatch.setattr(ocr, "_failed", False)
    assert ocr.read_lines(Image.new("RGB", (10, 10))) == []


# ---------------------------------------------------------------------- the Keychain
def _mac_store(monkeypatch):
    """pyxi.secretstore as it is on a Mac (its Keychain functions swapped in)."""
    from pyxi import secretstore
    from pyxi.macos import secretstore as mac_store

    for name in ("MAGIC", "available", "protect", "unprotect"):
        monkeypatch.setattr(secretstore, name, getattr(mac_store, name))
    return secretstore


def test_secrets_are_sealed_with_a_keychain_key(tmp_path, mac, monkeypatch):
    secretstore = _mac_store(monkeypatch)
    path = tmp_path / "google_token.json"
    secretstore.write_json(path, {"token": "abc"})
    body = path.read_bytes()
    assert body.startswith(b"PYXI-KEYCHAIN-1\n") and b"abc" not in body
    assert len(mac.keychain[("app.pyxi.desktop", "secret-store-key")]) == 32
    assert secretstore.read_json(path) == {"token": "abc"}


def test_a_file_from_another_mac_is_dropped(tmp_path, mac, monkeypatch):
    from pyxi.macos import secretstore as mac_store

    secretstore = _mac_store(monkeypatch)
    path = tmp_path / "t.json"
    secretstore.write_json(path, {"token": "abc"})
    mac.keychain[("app.pyxi.desktop", "secret-store-key")] = b"\x01" * 32  # another Mac's key
    monkeypatch.setattr(mac_store, "_key", None)
    assert secretstore.read_json(path) is None and not path.exists()


def test_old_plain_files_are_migrated(tmp_path, mac, monkeypatch):
    secretstore = _mac_store(monkeypatch)
    path = tmp_path / "t.json"
    path.write_text(json.dumps({"token": "old"}), encoding="utf-8")
    assert secretstore.read_json(path) == {"token": "old"}
    assert secretstore.encrypted(path)


def test_without_a_keychain_files_stay_plain(tmp_path, monkeypatch):
    from pyxi.macos import secretstore as mac_store

    monkeypatch.setitem(sys.modules, "Security", None)
    monkeypatch.setattr(mac_store, "_key", None)
    monkeypatch.setattr(mac_store, "_unavailable", False)
    secretstore = _mac_store(monkeypatch)
    path = tmp_path / "t.json"
    secretstore.write_json(path, {"token": "abc"})
    assert json.loads(path.read_text(encoding="utf-8")) == {"token": "abc"}


# ---------------------------------------------------------------------- the rest
def test_mac_time_zone_from_etc_localtime(monkeypatch):
    from pyxi import localzone

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(os, "readlink", lambda _p: "/var/db/timezone/zoneinfo/America/Vancouver")
    assert localzone.mac_zone() == "America/Vancouver"
    assert localzone.local_zone() == "America/Vancouver"
    monkeypatch.setattr(os, "readlink", lambda _p: "/usr/share/zoneinfo/UTC")
    assert localzone.mac_zone() == ""


def test_mac_zone_is_empty_off_a_mac(monkeypatch):
    from pyxi import localzone

    monkeypatch.setattr(sys, "platform", "win32")
    assert localzone.mac_zone() == ""


def test_the_brain_quits_when_the_app_is_gone(monkeypatch):
    from pyxi import cli

    assert cli._alive(os.getpid())
    exits = []
    monkeypatch.setattr(cli, "_alive", lambda pid: False)
    monkeypatch.setattr(cli.os, "_exit", lambda code: exits.append(code))
    cli._quit_with_app_mac(12345)
    assert exits and exits[0] == 0


def test_spotlight_search(monkeypatch, tmp_path):
    from pyxi.macos import files

    (tmp_path / "Resume 2026.pdf").write_bytes(b"x")
    calls = []

    def run(cmd, **_kw):
        calls.append(cmd)
        return Ran(str(tmp_path / "Resume 2026.pdf") + "\n/gone/file.txt\n")

    monkeypatch.setattr(files.subprocess, "run", run)
    hits = files.search_index("my resume", [str(tmp_path)])
    assert [Path(h.path).name for h in hits] == ["Resume 2026.pdf"]
    assert calls[0][:3] == ["mdfind", "-onlyin", str(tmp_path)]
    assert files.spotlight_query(["resume"]) == '(kMDItemFSName == "*resume*"cd || kMDItemTextContent == "resume*"cdw)'


def test_retina_screenshots_are_scaled_to_points(monkeypatch):
    from pyxi.screen import context

    big = Image.new("RGB", (2880, 1800))
    mon = {"left": 0, "top": 0, "width": 1440, "height": 900}
    monkeypatch.setattr(sys, "platform", "darwin")
    assert context.to_points(big, mon).size == (1440, 900)
    monkeypatch.setattr(sys, "platform", "win32")
    assert context.to_points(big, mon) is big  # Windows is untouched


def test_operator_knows_its_on_a_mac_and_cmd_w_closes():
    from pyxi.companion import operator

    assert "This computer is a Mac" in operator.MAC_KNOWHOW
    assert {"cmd+w", "cmd+q", "ctrl+w", "alt+f4"} <= operator.CLOSING_KEYS
    if sys.platform != "darwin":
        assert "win+w" not in operator.CLOSING_KEYS  # Windows' widgets key isn't closing


def test_permissions_status(mac):
    from pyxi.macos import permissions

    mac.trusted, mac.screen_ok = True, False
    assert permissions.status() == {"accessibility": True, "screen": False}
    assert permissions.SETTINGS["screen"].endswith("Privacy_ScreenCapture")


def test_libreoffice_on_a_mac_uses_the_users_copy(tmp_path, monkeypatch):
    from pyxi.agents import libreoffice

    soffice = tmp_path / "LibreOffice.app" / "Contents" / "MacOS" / "soffice"
    soffice.parent.mkdir(parents=True)
    soffice.write_bytes(b"")
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(libreoffice, "MAC_APPS", (Path("/nope/soffice"), soffice))
    assert libreoffice.installed() == soffice
    with pytest.raises(RuntimeError, match=r"libreoffice\.org"):
        asyncio.run(libreoffice.Installer(root=tmp_path / "x")._install())


def test_ditto_reads_the_focused_field_on_a_mac(monkeypatch):
    from pyxi.agents import ditto
    from pyxi.screen import uia

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(uia, "available", lambda: True)
    field = {"process": "Notes", "title": "List", "role": "Edit", "name": "Body", "password": False, "value": "milk", "rect": (0, 0, 1, 1)}
    monkeypatch.setattr(uia, "focused_field", lambda: field, raising=False)
    f = ditto.read_focus()
    assert (f.app, f.title, f.role, f.value) == ("Notes", "List", "Edit", "milk")
    field["password"] = True
    f = ditto.read_focus()
    assert f.password and f.value == "" and f.name == "(password field)"


def test_paperwork_opens_files_with_open(tmp_path, monkeypatch):
    import subprocess

    from pyxi.companion import paperwork

    opened = []
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(subprocess, "Popen", lambda cmd: opened.append(cmd))
    paperwork.open_path(tmp_path / "form.pdf")
    assert opened == [["open", str(tmp_path / "form.pdf")]]


def test_pricecheck_reads_the_address_through_the_mac_reader(monkeypatch):
    from pyxi import windows
    from pyxi.companion import pricecheck
    from pyxi.screen import uia

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(windows, "foreground", lambda: 7)
    monkeypatch.setattr(uia, "browser_url", lambda hwnd, budget_s=0.5: f"https://shop.example/{hwnd}")
    assert pricecheck.browser_url() == "https://shop.example/7"
