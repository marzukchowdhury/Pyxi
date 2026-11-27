from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from pyxi.config import PyxiSettings
from pyxi.hub import Hub
from pyxi.screen import ScreenContext, UIElement, WindowInfo


def fake_context(app: str = "app.exe", title: str = "Settings - App") -> ScreenContext:
    img = Image.new("RGB", (1600, 900), (240, 240, 250))
    els = [
        UIElement(1, "Settings", "Pane", (100, 100, 800, 600), 0),
        UIElement(2, "General", "Group", (120, 140, 760, 200), 1, 1),
        UIElement(3, "Dark mode", "CheckBox", (140, 170, 200, 30), 2, 2),
        UIElement(4, "Export", "Button", (700, 650, 120, 40), 1, 1),
        UIElement(5, "", "Edit", (140, 400, 300, 30), 2, 1, is_password=True),
    ]
    return ScreenContext(time.time(), WindowInfo(title, app, (0, 0, 1600, 900)), els, img)


class FakeScreen:
    """Stands in for ScreenReader so tests never capture the real desktop."""

    def __init__(self) -> None:
        self.paused = False
        self.ctx = fake_context()

    async def capture(self, pixels: bool = True) -> ScreenContext:
        ctx = self.ctx
        ctx.captured_at = time.time()
        return ctx

    def is_excluded(self, window: WindowInfo) -> str | None:
        return None


class Recorder:
    """A hub client that records every event."""

    def __init__(self, hub: Hub) -> None:
        self.events: list[dict[str, Any]] = []
        hub.connect(self.send)

    async def send(self, text: str) -> None:
        self.events.append(json.loads(text))

    def of(self, kind: str) -> list[dict[str, Any]]:
        return [e for e in self.events if e["type"] == kind]


@pytest.fixture(autouse=True)
def _no_real_browser(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No test looks at the windows that are really open, writes the browser extension into
    the folder a real browser loads it from, or reads or changes the real clipboard
    (pyxi/companion/browsersetup.py). The pretend clipboard is ``browsersetup.CLIP``."""
    from pyxi.companion import browsersetup

    monkeypatch.setenv(browsersetup.HOME_ENV, str(tmp_path / "extension-home"))
    monkeypatch.setattr(browsersetup, "front", lambda key="", open_windows=None: None)
    # No test sends sound to the cloud to be written down: the key that makes the cloud
    # reachable is taken out (a test that means to listen there sets one of its own).
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    # Nor believes it is in the beta because an earlier test joined it (cloud.apply sets this
    # for the whole process): what a refused key is called depends on it (plainwords.py).
    monkeypatch.delenv("PYXI_BETA", raising=False)
    # No test opens an address in a real browser: links go out as the app's own "action".
    from pyxi import windows

    def no_browser(words, like=None):
        return ""

    no_browser.real = windows.browser_for  # for the test of the real one
    monkeypatch.setattr(windows, "browser_for", no_browser)
    def no_open(exe, url, window=None):
        return None

    no_open.real = windows.open_in  # for the test of the real one
    monkeypatch.setattr(windows, "open_in", no_open)
    # No test asks the internet whether a site exists (the test of that gives its own answers).
    from pyxi.companion import actions as actions_mod

    async def every_site_exists(host):
        return True

    every_site_exists.real = actions_mod._resolves  # for the test of the real one
    monkeypatch.setattr(actions_mod, "_resolves", every_site_exists)
    from pyxi.companion import answers as answers_mod

    async def no_shop_page(url, limit):  # a shop's results are never fetched from the real web
        return b"", ""

    monkeypatch.setattr(answers_mod, "_shop_page", no_shop_page)
    monkeypatch.setattr(browsersetup, "_addon", None)
    monkeypatch.setattr(browsersetup, "last_fault", None)
    clip = {"text": "what I had copied"}
    monkeypatch.setattr(browsersetup, "CLIP", clip, raising=False)
    monkeypatch.setattr(browsersetup, "clip_get", lambda: clip["text"])
    monkeypatch.setattr(browsersetup, "clip_set", lambda text: clip.__setitem__("text", text) or True)


@pytest.fixture(autouse=True)
def _no_real_account_server(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test reaches the real Pyxi server, Google's sign-in or a browser for the account
    (pyxi/account.py): every request finds no internet (a test of the account gives its own
    pretend server and browser)."""
    import httpx

    from pyxi.account import Account

    def no_internet(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("tests never reach the real server", request=request)

    monkeypatch.setattr(Account, "transport", httpx.MockTransport(no_internet))
    monkeypatch.delenv("PYXI_ACCOUNT", raising=False)


@pytest.fixture(autouse=True)
def _no_real_windows_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test reads the other windows that are really on this PC's screen: a look at the
    screen finds none besides the one it is given (a test of several gives its own)."""
    from pyxi.screen import uia

    monkeypatch.setattr(uia, "other_windows", lambda screen, main=0, limit=3: [])
    # Nor asks Windows which control has the keyboard: a dictation finds a text box in front
    # (a test of the other cases gives its own answer).
    from pyxi import dictation

    def a_text_box() -> dictation.Target:
        return dictation.Target("field", 0)

    a_text_box.real = dictation.where_to_type  # type: ignore[attr-defined]
    monkeypatch.setattr(dictation, "where_to_type", a_text_box)


@pytest.fixture(autouse=True)
def _no_real_listening(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test listens to this PC's speakers or microphone, starts Windows Live Captions or
    asks Windows what is playing: the real capture refuses to open (tests of the listening
    give LiveEars a pretend one)."""
    from pyxi.companion import listening
    from pyxi.listen import capture, wincaptions

    def refuse(self: Any, wait_s: float = 3.0) -> bool:
        self.problem = "Tests never listen to the real PC."
        return False

    refuse.real = capture.Capture.start  # for the test of the capture's own thread (fake device)
    monkeypatch.setattr(capture.Capture, "start", refuse)
    monkeypatch.setattr(capture, "muted", lambda: False)
    monkeypatch.setattr(wincaptions, "installed", lambda: False)
    monkeypatch.setattr(wincaptions, "window", lambda: 0)
    monkeypatch.setattr(listening, "sound_playing", lambda: False)
    monkeypatch.setattr(listening, "sound_screen", lambda: None)


@pytest.fixture(autouse=True)
def _addresses_are_right(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test waits on the "is this the address you meant?" card (companion/addresscheck.py):
    it is shown, and answered at once with "Yes, that's it". Tests of the card itself give
    their own answers (tests/test_address_check.py)."""
    real = Hub.request

    async def request(self: Hub, type: str, timeout: float = 300, **data: Any) -> Any:
        if type != "check_address":
            return await real(self, type, timeout=timeout, **data)
        await self.emit(type, request_id="req_test", **data)
        return {"addresses": [a["address"] for a in data["addresses"]]}

    monkeypatch.setattr(Hub, "request", request)


@pytest.fixture(autouse=True)
def _no_real_libreoffice_on_save(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test starts the real LibreOffice to work a workbook's formulas out as Ledger saves
    it (agents/ledger.py, Workbook._keep_results): that is seconds a save, on a PC that has
    it. A test that gives its own stand-in (``_worked_out``, or ``_soffice`` and
    ``_convert``) is still asked."""
    from pyxi.agents import ledger

    real = (ledger._worked_out, ledger._soffice, ledger._convert)

    def for_save(wb: Any, suffix: str) -> Any:
        if (ledger._worked_out, ledger._soffice, ledger._convert) == real:
            return None
        return ledger._worked_out(wb, suffix)

    monkeypatch.setattr(ledger, "_worked_out_for_save", for_save)


@pytest.fixture
def settings(tmp_path: Path) -> PyxiSettings:
    return PyxiSettings(
        _env_file=None,
        data_dir=tmp_path / ".pyxi",
        voice={"tts": "client"},
        # tab_fallback: off here whatever the default is, so no test reaches for real windows.
        screen={"ambient_memory": False, "tab_fallback": False},
    )
