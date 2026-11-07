"""Scout's browser never uses the user's own Chrome or Edge profile, cookies or saved passwords:
it runs in a profile of its own in Pyxi's data folder (Otto's ``profile_dir``)."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from otto.browser.session import BrowserSession, check_profile_dir
from otto.core.guardrails import Guardrails

from pyxi.llm import otto_settings


def test_pyxi_gives_scout_its_own_profile_and_never_attaches(settings, monkeypatch):
    monkeypatch.setenv("OTTO_CDP_URL", "http://127.0.0.1:9222")  # a developer's .env, say
    s = otto_settings(settings)
    assert s.profile_dir == settings.data_dir / "browser"
    assert s.cdp_url is None  # never the user's running browser


@pytest.mark.parametrize(
    "everyday",
    [
        r"Google\Chrome\User Data",
        r"Google\Chrome\User Data\Default",
        r"Microsoft\Edge\User Data",
        r"BraveSoftware\Brave-Browser\User Data\Profile 1",
    ],
)
def test_a_browsers_everyday_profile_is_refused(tmp_path, monkeypatch, everyday):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    with pytest.raises(ValueError, match="a browser's own profile"):
        check_profile_dir(tmp_path / everyday)
    check_profile_dir(tmp_path / "Pyxi" / ".pyxi" / "browser")  # its own folder is fine


class _CookieSite:
    def __init__(self) -> None:
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                if self.path == "/set":
                    self.send_header("Set-Cookie", "signed_in=yes; Max-Age=3600; Path=/")
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(b"<title>t</title><p>ok</p>")

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


@pytest.mark.browser
async def test_scouts_profile_lives_in_pyxis_folder_and_keeps_its_own_cookies(settings):
    site = _CookieSite()
    s = otto_settings(settings, headless=True)
    try:
        async with BrowserSession(s, Guardrails(s.safety)) as browser:
            await browser.navigate(site.url + "/set")
        profile = Path(s.profile_dir)
        assert (profile / "Default").is_dir()  # a real profile, in Pyxi's data folder
        prefs = json.loads((profile / "Default" / "Preferences").read_text(encoding="utf-8"))
        assert prefs["credentials_enable_service"] is False  # never offers to save a password
        assert prefs["profile"]["password_manager_enabled"] is False
        async with BrowserSession(s, Guardrails(s.safety)) as browser:
            await browser.navigate(site.url + "/check")
            cookies = await browser._context.cookies(site.url)
        assert any(c["name"] == "signed_in" for c in cookies)  # kept between runs, in its own profile
    finally:
        site.server.shutdown()
