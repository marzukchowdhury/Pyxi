"""Installing the browser extension without a store: the signed Firefox add-on (signing,
its update file, the release step), the folder Chrome and Edge load it from, the guided setup,
and the offer Pyxi makes when a browser would help and none is connected.

Nothing here reaches Mozilla, GitHub or a real browser: the network is a fake, the browser in
front is whatever the test says it is, and the folder the extension is copied to is a temporary
one (see conftest.py). The real-browser checks are in scripts/browser_setup_check.py.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import io
import json
import re
import sys
import time
import zipfile
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from otto.types import LLMResponse, ToolCall, Usage

from conftest import fake_context
from pyxi.companion import browsersetup, tabwork
from pyxi.server import create_app
from test_browserlink import KEY, LOCAL, Cards, PretendBrowser, _brain

ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "extension"
sys.path.insert(0, str(EXTENSION))
sys.path.insert(0, str(ROOT / "scripts"))

import build as extension_build  # noqa: E402
import sign_firefox  # noqa: E402

ID = "pyxi-in-your-browser@pyxi.local"
LATEST = "https://github.com/marzukchowdhury/Pyxi/releases/latest/download"
CHROME = (browsersetup.BROWSERS["chrome.exe"], r"C:\Program Files\Google\Chrome\Application\chrome.exe")
EDGE = (browsersetup.BROWSERS["msedge.exe"], r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
FIREFOX = (browsersetup.BROWSERS["firefox.exe"], r"C:\Program Files\Mozilla Firefox\firefox.exe")


# ====================================================================== the Firefox build, ready to be signed
def test_the_firefox_manifest_is_ready_for_unlisted_signing():
    spec = extension_build.manifest("firefox")
    gecko = spec["browser_specific_settings"]["gecko"]
    assert gecko["id"] == ID == browsersetup.ADDON_ID  # the same id, always: that is what updates go by
    assert re.fullmatch(r"[a-zA-Z0-9-._]*@[a-zA-Z0-9-._]+", gecko["id"]) and len(gecko["id"]) <= 80
    # Where installed copies look for a new version: the release's update file, at an address
    # that doesn't change (and the one the app reads to know whether there is an add-on).
    assert gecko["update_url"] == f"{LATEST}/pyxi-firefox-updates.json" == browsersetup.UPDATES_URL
    # What leaves the browser, as Mozilla asks every new add-on to say: page text and which
    # pages, to Pyxi on the same PC. The question is asked by Firefox itself from version 140.
    assert gecko["data_collection_permissions"] == {"required": ["websiteContent", "browsingActivity"]}
    assert gecko["strict_min_version"] == "140.0"
    # None of that leaks into the Chrome and Edge manifest.
    chrome = extension_build.manifest("chrome")
    assert "browser_specific_settings" not in chrome and chrome["version"] == spec["version"]


def test_the_package_is_the_build_as_one_file_and_the_same_every_time(tmp_path):
    one = extension_build.package("firefox", tmp_path / "a.zip").read_bytes()
    two = extension_build.package("firefox", tmp_path / "b.zip").read_bytes()
    assert one == two  # same code, same bytes
    with zipfile.ZipFile(io.BytesIO(one)) as z:
        names = set(z.namelist())
        inside = json.loads(z.read("manifest.json"))
    assert {"manifest.json", "background.js", "content.js", "popup.html", "popup.js", "popup.css"} <= names
    assert any(n.startswith("icons/") for n in names) and not any("sign_firefox" in n or "README" in n for n in names)
    assert inside == extension_build.manifest("firefox")


def test_the_update_file_names_the_version_the_address_and_the_hash():
    xpi = b"signed bytes"
    version = extension_build.manifest("firefox")["version"]
    assert sign_firefox.updates(version, xpi) == {
        "addons": {
            ID: {
                "updates": [
                    {
                        "version": version,
                        "update_link": f"{LATEST}/pyxi-firefox.xpi",
                        "update_hash": "sha256:" + hashlib.sha256(xpi).hexdigest(),
                        "applications": {"gecko": {"strict_min_version": "140.0"}},
                    }
                ]
            }
        }
    }


# ====================================================================== signing, with Mozilla played by a fake
SECRETS = {"WEB_EXT_API_KEY": "user:12345:67", "WEB_EXT_API_SECRET": "s3cret-0f-the-founder-9f8e7d6c5b4a"}


def signed_xpi(version: str | None = None, addon_id: str = ID, sign: bool = True) -> bytes:
    spec = extension_build.manifest("firefox")
    spec["version"] = version or spec["version"]
    spec["browser_specific_settings"]["gecko"]["id"] = addon_id
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("manifest.json", json.dumps(spec))
        if sign:
            z.writestr("META-INF/mozilla.rsa", b"signature")
    return out.getvalue()


class Mozilla:
    """addons.mozilla.org's signing API, as far as the script uses it."""

    def __init__(self, xpi: bytes, *, valid: bool = True, status: str = "public", taken: bool = False) -> None:
        self.xpi, self.valid, self.status, self.taken = xpi, valid, status, taken
        self.calls: list[httpx.Request] = []
        self.polls = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        path = request.url.path
        if not request.headers.get("authorization", "").startswith("JWT "):
            return httpx.Response(401, json={"detail": "no token"})
        if request.method == "POST" and path == "/api/v5/addons/upload/":
            return httpx.Response(201, json={"uuid": "u-1", "processed": False})
        if path == "/api/v5/addons/upload/u-1/":
            self.polls += 1
            if self.polls < 2:
                return httpx.Response(200, json={"uuid": "u-1", "processed": False})
            bad = {"messages": [{"type": "error", "message": "The manifest is wrong."}, {"type": "notice", "message": "fine"}]}
            return httpx.Response(200, json={"processed": True, "valid": self.valid, "validation": {} if self.valid else bad})
        if request.method == "PUT" and path == f"/api/v5/addons/addon/{ID}/":
            if self.taken:
                return httpx.Response(409, json={"version": ["Version 0.2.0 already exists."]})
            assert json.loads(request.content) == {"version": {"upload": "u-1"}}
            return httpx.Response(200, json={"guid": ID, "version": {"id": 77}})
        if path == f"/api/v5/addons/addon/{ID}/versions/77/":
            self.polls += 1
            status = "unreviewed" if self.polls < 4 and self.status == "public" else self.status
            return httpx.Response(200, json={"file": {"status": status, "url": "https://addons.mozilla.org/firefox/downloads/file/9/pyxi.xpi"}})
        if path == "/firefox/downloads/file/9/pyxi.xpi":
            return httpx.Response(200, content=self.xpi)
        return httpx.Response(404, json={"detail": path})


def run_sign(tmp_path, mozilla: Mozilla, env: dict | None = None, argv: list[str] | None = None) -> int:
    client = httpx.Client(transport=httpx.MockTransport(mozilla))
    return sign_firefox.main(["--out", str(tmp_path / "signed"), *(argv or [])], env=SECRETS if env is None else env, client=client, sleep=lambda s: None)


def test_signing_sends_the_package_to_mozilla_and_keeps_the_signed_file_and_its_update_file(tmp_path, capsys):
    xpi = signed_xpi()
    mozilla = Mozilla(xpi)
    assert run_sign(tmp_path, mozilla) == 0
    out = tmp_path / "signed"
    assert sorted(p.name for p in out.iterdir()) == ["pyxi-firefox-updates.json", "pyxi-firefox.xpi"]
    assert (out / "pyxi-firefox.xpi").read_bytes() == xpi
    version = extension_build.manifest("firefox")["version"]
    assert json.loads((out / "pyxi-firefox-updates.json").read_text(encoding="utf-8")) == sign_firefox.updates(version, xpi)

    # What went to Mozilla: the package, for the unlisted channel, then this add-on's version.
    upload = mozilla.calls[0]
    assert upload.url == "https://addons.mozilla.org/api/v5/addons/upload/"
    body = upload.read()
    assert b'name="channel"\r\n\r\nunlisted' in body and b'name="upload"' in body and b"PK\x03\x04" in body
    assert [c.method for c in mozilla.calls].count("PUT") == 1
    # Every request carries a fresh, short-lived token signed with the secret, and nothing else of it.
    seen = set()
    for call in mozilla.calls:
        head, claims, mac = call.headers["authorization"].removeprefix("JWT ").split(".")
        payload = json.loads(base64.urlsafe_b64decode(claims + "=="))
        assert payload["iss"] == SECRETS["WEB_EXT_API_KEY"] and 0 < payload["exp"] - payload["iat"] <= 300
        want = hmac.new(SECRETS["WEB_EXT_API_SECRET"].encode(), f"{head}.{claims}".encode(), hashlib.sha256).digest()
        assert base64.urlsafe_b64decode(mac + "==") == want
        seen.add(payload["jti"])
        assert SECRETS["WEB_EXT_API_SECRET"] not in str(call.headers) + str(call.url)
    assert len(seen) == len(mozilla.calls)
    # Neither value is ever printed, or written anywhere.
    said = capsys.readouterr()
    for value in SECRETS.values():
        assert value not in said.out + said.err
        for path in tmp_path.rglob("*"):
            assert not path.is_file() or value.encode() not in path.read_bytes()
    assert "signed:" in said.out and "commit them" in said.out


@pytest.mark.parametrize(
    ("env", "names"),
    [
        ({}, "WEB_EXT_API_KEY and WEB_EXT_API_SECRET are not set"),
        ({"WEB_EXT_API_KEY": "user:1:2"}, "WEB_EXT_API_SECRET is not set"),
        ({"WEB_EXT_API_SECRET": "x", "WEB_EXT_API_KEY": "  "}, "WEB_EXT_API_KEY is not set"),
    ],
)
def test_signing_stops_clearly_when_the_credentials_are_missing(tmp_path, env, names):
    mozilla = Mozilla(signed_xpi())
    with pytest.raises(SystemExit) as stop:
        run_sign(tmp_path, mozilla, env=env)
    assert names in str(stop.value) and "addons.mozilla.org" in str(stop.value)
    assert "x" not in str(stop.value).split("not set")[0].replace("WEB_EXT", "")  # names, never values
    assert mozilla.calls == [] and not (tmp_path / "signed").exists()  # nothing sent, nothing written


def test_check_builds_and_checks_the_credentials_without_contacting_mozilla(tmp_path, capsys):
    mozilla = Mozilla(signed_xpi())
    assert run_sign(tmp_path, mozilla, argv=["--check"]) == 0
    assert mozilla.calls == [] and not (tmp_path / "signed").exists()
    assert "Nothing was sent" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        run_sign(tmp_path, mozilla, env={}, argv=["--check"])


@pytest.mark.parametrize(
    ("mozilla", "says"),
    [
        (Mozilla(signed_xpi(), valid=False), "Mozilla's check found problems:\n  The manifest is wrong."),
        (Mozilla(signed_xpi(), taken=True), 'Mozilla already has this version. A version is signed once: raise "version"'),
        (Mozilla(signed_xpi(), status="disabled"), "Mozilla's review turned this version down"),
        (Mozilla(signed_xpi(sign=False)), "the file Mozilla sent back isn't signed"),
        (Mozilla(signed_xpi(addon_id="someone@else")), "the signed file isn't this add-on and version"),
        (Mozilla(b"<html>not a zip</html>"), "what Mozilla sent back isn't an add-on file"),
    ],
)
def test_signing_says_plainly_what_went_wrong_and_leaves_nothing_behind(tmp_path, mozilla, says):
    with pytest.raises(SystemExit) as stop:
        run_sign(tmp_path, mozilla)
    assert says in str(stop.value)
    assert not (tmp_path / "signed").exists()
    for value in SECRETS.values():
        assert value not in str(stop.value)


def test_signing_gives_up_politely_when_mozilla_takes_too_long_or_refuses_the_key(tmp_path):
    with pytest.raises(SystemExit) as stop:
        run_sign(tmp_path, Mozilla(signed_xpi()), argv=["--timeout", "0"])
    assert "Mozilla is still checking the package" in str(stop.value)
    slow = Mozilla(signed_xpi(), status="unreviewed")
    slow.polls = 1  # its check of the package is quick; the signature is what never comes
    with pytest.raises(SystemExit) as stop:
        run_sign(tmp_path, slow, argv=["--timeout", "0"])
    assert "Mozilla hasn't signed it yet" in str(stop.value) and not (tmp_path / "signed").exists()

    def refuse(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "bad signature"})

    with pytest.raises(SystemExit) as stop:
        sign_firefox.main(["--out", str(tmp_path / "s")], env=SECRETS, client=httpx.Client(transport=httpx.MockTransport(refuse)), sleep=lambda s: None)
    assert "Mozilla refused the API key (401)" in str(stop.value) and SECRETS["WEB_EXT_API_SECRET"] not in str(stop.value)


# ====================================================================== the release carries them
def _signed_folder(tmp_path: Path, xpi: bytes | None = None, version: str | None = None) -> Path:
    folder = tmp_path / "signed"
    folder.mkdir()
    xpi = xpi or signed_xpi()
    (folder / "pyxi-firefox.xpi").write_bytes(xpi)
    version = version or extension_build.manifest("firefox")["version"]
    (folder / "pyxi-firefox-updates.json").write_text(json.dumps(sign_firefox.updates(version, xpi)), encoding="utf-8")
    return folder


def test_a_release_takes_the_signed_add_on_and_its_update_file_when_they_exist(tmp_path, monkeypatch, capsys):
    import release

    monkeypatch.setattr(release, "SIGNED", tmp_path / "nothing")
    assert release.firefox_assets() == [] and "no signed Firefox add-on" in capsys.readouterr().out

    folder = _signed_folder(tmp_path)
    monkeypatch.setattr(release, "SIGNED", folder)
    assert release.firefox_assets() == [
        (folder / "pyxi-firefox.xpi", "application/x-xpinstall"),  # so Firefox installs it, not saves it
        (folder / "pyxi-firefox-updates.json", "application/json"),
    ]
    # The names are the ones the add-on's manifest and the app look for at the latest link.
    assert sign_firefox.XPI_NAME in release.FIREFOX and sign_firefox.UPDATES_NAME in release.FIREFOX
    assert browsersetup.UPDATES_URL.endswith("/latest/download/" + sign_firefox.UPDATES_NAME)

    # An add-on signed for an older version of the extension still goes up, with a warning.
    (folder / "pyxi-firefox-updates.json").write_text(json.dumps(sign_firefox.updates("0.0.9", signed_xpi())), encoding="utf-8")
    assert len(release.firefox_assets()) == 2 and "WARNING: the signed Firefox add-on is 0.0.9" in capsys.readouterr().out

    # Two files that don't belong together, or only one of them, stop the release before it starts.
    (folder / "pyxi-firefox.xpi").write_bytes(b"something else")
    with pytest.raises(SystemExit, match="don't belong together"):
        release.firefox_assets()
    (folder / "pyxi-firefox.xpi").unlink()
    with pytest.raises(SystemExit, match="has only one of"):
        release.firefox_assets()


def test_publishing_uploads_them_with_the_release_as_what_they_are(tmp_path, monkeypatch):
    import release

    dist = tmp_path / "dist"
    dist.mkdir()
    for name in [*release.ASSETS, "pyxi-voice-pack-abc.zip"]:
        (dist / name).write_bytes(b"x" * 10)
    folder = _signed_folder(tmp_path)
    monkeypatch.setattr(release, "DIST", dist)
    monkeypatch.setattr(release, "SIGNED", folder)
    uploads: list[tuple[str, str, int]] = []

    def github(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.github.com":
            assert json.loads(request.content)["tag_name"] == "v9.9.9"
            return httpx.Response(201, json={"id": 5, "html_url": "https://github.com/x/releases/v9.9.9"})
        uploads.append((request.url.params["name"], request.headers["content-type"], len(request.read())))
        return httpx.Response(201, json={})

    monkeypatch.setattr(release, "github", lambda: httpx.Client(transport=httpx.MockTransport(github)))
    release.publish("9.9.9", "Notes.", release.firefox_assets())
    assert [(n, k) for n, k, _ in uploads] == [
        ("Pyxi-Setup.exe", "application/octet-stream"),
        ("Pyxi-Setup.exe.blockmap", "application/octet-stream"),
        ("latest.yml", "application/octet-stream"),
        ("pyxi-voice-pack-abc.zip", "application/octet-stream"),
        ("pyxi-firefox.xpi", "application/x-xpinstall"),
        ("pyxi-firefox-updates.json", "application/json"),
    ]
    assert uploads[4][2] == (folder / "pyxi-firefox.xpi").stat().st_size

    uploads.clear()
    release.publish("9.9.9", "Notes.")  # no signed add-on: the release is what it always was
    assert [n for n, _, _ in uploads] == ["Pyxi-Setup.exe", "Pyxi-Setup.exe.blockmap", "latest.yml", "pyxi-voice-pack-abc.zip"]


def test_after_publishing_the_latest_link_is_checked_for_them(tmp_path, monkeypatch):
    import release

    folder = _signed_folder(tmp_path)
    extra = [(folder / n, k) for n, k in release.FIREFOX.items()]
    served = {"type": "application/x-xpinstall", "updates": json.loads((folder / "pyxi-firefox-updates.json").read_text())}

    def get(url: str, **kw: Any) -> httpx.Response:
        if url.endswith("pyxi-firefox-updates.json"):
            return httpx.Response(200, json=served["updates"])
        return httpx.Response(206, content=b"P", headers={"content-type": served["type"]})

    monkeypatch.setattr(release.httpx, "get", get)
    release.verify_firefox(extra)
    release.verify_firefox([])  # nothing uploaded: nothing to check
    served["type"] = "application/octet-stream"
    with pytest.raises(SystemExit, match="Firefox would save it instead of installing it"):
        release.verify_firefox(extra)
    served.update(type="application/x-xpinstall", updates={"addons": {}})
    with pytest.raises(SystemExit, match="doesn't give the Firefox update file"):
        release.verify_firefox(extra)


# ====================================================================== the installer carries the extension
def test_the_installer_carries_both_builds_of_the_extension(tmp_path):
    import build_installer

    out = build_installer.ship_browser_extension(tmp_path / "browser-extension")
    chrome = json.loads((out / "chrome" / "manifest.json").read_text(encoding="utf-8"))
    firefox = json.loads((out / "firefox" / "manifest.json").read_text(encoding="utf-8"))
    assert chrome["background"] == {"service_worker": "background.js"} and "host_permissions" not in chrome
    assert firefox["browser_specific_settings"]["gecko"]["id"] == ID
    assert (out / "chrome" / "content.js").read_bytes() == (EXTENSION / "content.js").read_bytes()
    package = json.loads((ROOT / "desktop" / "package.json").read_text(encoding="utf-8"))
    assert {"from": "build/browser-extension", "to": "browser-extension", "filter": ["**/*"]} in package["build"]["extraResources"]
    source = (ROOT / "scripts" / "build_installer.py").read_text(encoding="utf-8")
    assert source.index("ship_browser_extension()\n    npm") < source.index('[npm, "run", "dist"]')  # before the installer is made


# ====================================================================== the folder that keeps its path
@pytest.fixture
def bundle(tmp_path, monkeypatch) -> Path:
    """This Pyxi's own copy of the extension, as the installer lays it out."""
    import build_installer

    out = build_installer.ship_browser_extension(tmp_path / "resources" / "browser-extension")
    monkeypatch.setenv(browsersetup.BUNDLE_ENV, str(out))
    return out


def test_the_extension_is_copied_to_a_folder_whose_path_never_changes(bundle, tmp_path):
    where = browsersetup.ensure_folder("chromium")
    assert where == tmp_path / "extension-home" / "browser-extension" == browsersetup.folder("chromium")
    assert json.loads((where / "manifest.json").read_text(encoding="utf-8"))["name"] == "Pyxi in your browser"
    assert (where / "icons" / "pyxi-48.png").is_file()
    fox = browsersetup.ensure_folder("firefox")
    assert fox == tmp_path / "extension-home" / "browser-extension-firefox" and fox != where
    assert "gecko" in json.loads((fox / "manifest.json").read_text(encoding="utf-8"))["browser_specific_settings"]
    # On the real PC it is under the user's own local app data, whatever version is installed.
    assert browsersetup.folder("chromium").name == "browser-extension"


def test_updating_pyxi_updates_that_folder_in_place(bundle, tmp_path):
    where = browsersetup.ensure_folder("chromium")
    theirs = where / "kept.txt"
    theirs.write_text("not ours to remove")
    before = where.stat().st_ino if hasattr(where.stat(), "st_ino") else None
    # A newer Pyxi brings a changed script, a new version number and a new file.
    (bundle / "chrome" / "content.js").write_text("// the new one\n", encoding="utf-8")
    spec = json.loads((bundle / "chrome" / "manifest.json").read_text(encoding="utf-8"))
    (bundle / "chrome" / "manifest.json").write_text(json.dumps({**spec, "version": "9.9.9"}), encoding="utf-8")
    (bundle / "chrome" / "extra.js").write_text("// new file\n", encoding="utf-8")
    assert not (browsersetup.folder("firefox") / "manifest.json").exists()
    browsersetup.refresh()  # what the brain does when it starts
    assert browsersetup.folder("chromium") == where and where.is_dir()  # the same folder
    assert before is None or where.stat().st_ino == before
    assert (where / "content.js").read_text(encoding="utf-8") == "// the new one\n"
    assert json.loads((where / "manifest.json").read_text(encoding="utf-8"))["version"] == "9.9.9"
    assert (where / "extra.js").is_file() and theirs.read_text() == "not ours to remove"
    # A folder that was never made (no browser was set up) isn't made by an update.
    assert not browsersetup.folder("firefox").exists()
    assert browsersetup.sync(bundle / "chrome", where) == 0  # nothing to write the second time


def test_a_checkout_builds_the_folder_from_the_source_and_a_copy_without_it_says_so(tmp_path, monkeypatch):
    monkeypatch.delenv(browsersetup.BUNDLE_ENV, raising=False)
    monkeypatch.setattr(browsersetup, "bundled", lambda kind: None)
    where = browsersetup.ensure_folder("chromium")  # from extension/ in this checkout
    assert (where / "background.js").read_bytes() == (EXTENSION / "background.js").read_bytes()
    assert json.loads((where / "manifest.json").read_text(encoding="utf-8")) == extension_build.manifest("chrome")
    monkeypatch.setattr(browsersetup, "_repo_extension", lambda: None)
    with pytest.raises(browsersetup.SetupError, match="doesn't carry the browser extension"):
        browsersetup.ensure_folder("chromium")


# ====================================================================== which browser
def test_the_browser_in_front_is_found_by_its_program(monkeypatch):
    monkeypatch.undo()  # the real front(): windows are passed in here
    windows = [
        (1, "Untitled - Notepad", r"C:\Windows\System32\notepad.exe"),
        (2, "Inbox - Microsoft Edge", EDGE[1]),
        (3, "Docs - Google Chrome", CHROME[1]),
        (4, "Mozilla Firefox", FIREFOX[1]),
    ]
    assert browsersetup.front(open_windows=lambda: windows) == EDGE  # nearest the front
    assert browsersetup.front("firefox", open_windows=lambda: windows) == FIREFOX
    assert browsersetup.front("chrome", open_windows=lambda: windows) == CHROME
    assert browsersetup.front(open_windows=lambda: windows[:1]) is None
    kinds = {b.key: (b.kind, b.page, b.checked) for b in browsersetup.BROWSERS.values()}
    assert kinds["chrome"] == ("chromium", "chrome://extensions", True)
    assert kinds["edge"] == ("chromium", "edge://extensions", True)
    assert kinds["firefox"][0] == "firefox"
    # Other Chromium browsers get the same steps, and are said not to have been checked.
    for key in ("brave", "vivaldi", "opera"):
        assert kinds[key][0] == "chromium" and kinds[key][1] == f"{key}://extensions" and kinds[key][2] is False
    assert browsersetup.browser_of("C:/x/MSEDGE.EXE").name == "Edge" and browsersetup.browser_of("explorer.exe") is None


# ====================================================================== Firefox: only a link that works
def _updates_server(status: int = 200, body: Any = None):
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(status, json=body) if body is not None else httpx.Response(status)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), calls


async def test_add_to_firefox_is_only_offered_when_a_signed_add_on_is_published():
    good = sign_firefox.updates("0.2.0", b"xpi")
    client, calls = _updates_server(200, good)
    assert browsersetup.addon_known() is None
    assert await browsersetup.firefox_addon(client) == f"{LATEST}/pyxi-firefox.xpi"
    assert calls == [browsersetup.UPDATES_URL] and browsersetup.addon_known() is True
    assert await browsersetup.firefox_addon(client) and len(calls) == 1  # remembered for a while

    for status, body in (
        (404, None),  # no release carries it yet
        (200, {"addons": {}}),
        (200, {"addons": {"someone@else": good["addons"][ID]}}),
        (200, {"addons": {ID: {"updates": [{"version": "1", "update_link": "https://evil.example/pyxi.xpi"}]}}}),
        (200, {"addons": {ID: {"updates": [{"version": "1", "update_link": "https://github.com/marzukchowdhury/Pyxi/releases/x.exe"}]}}}),
        (200, "not json at all"),
    ):
        client, _ = _updates_server(status, body)
        assert await browsersetup.firefox_addon(client, again=True) == "", (status, body)
        assert browsersetup.addon_known() is False

    class Offline:
        async def get(self, url: str) -> httpx.Response:
            raise httpx.ConnectError("no network")

    assert await browsersetup.firefox_addon(Offline(), again=True) == ""


# ====================================================================== the guided setup
class Guide:
    """A chat model that does what the setup asks of it: points at one thing per step with the
    guide tool, then says it's done."""

    def __init__(self, *steps: dict | None) -> None:
        self.steps = list(steps)
        self.asked: list[str] = []

    async def complete(self, *, system, messages, tools=None, **_):
        text = "\n".join(getattr(p, "text", "") for m in messages for p in m.parts)
        self.asked.append(text.rsplit("# The user says", 1)[-1])
        step = self.steps.pop(0) if self.steps else None
        calls = [("say", {"text": "Okay."})] if step is None else [("guide", step)]
        return LLMResponse(
            tool_calls=[ToolCall(id=str(i), name=n, arguments=a) for i, (n, a) in enumerate(calls)],
            usage=Usage(),
            stop_reason="tool_use",
        )


def point(element: int, label: str, **more: Any) -> dict:
    return {"goal": "set up the browser extension", "element_id": element, "label": label, **more}


DONE = {"goal": "set up the browser extension", "done": True}


@pytest.fixture
def fast(monkeypatch):
    real = asyncio.sleep

    async def quick(seconds: float, *a: Any) -> None:
        await real(min(seconds, 0.01))

    monkeypatch.setattr(asyncio, "sleep", quick)


async def _until(test: Any, tries: int = 400) -> None:
    for _ in range(tries):
        if test():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("it never happened")


async def test_set_it_up_for_me_in_chrome_points_at_each_step_and_then_pairs(settings, bundle, monkeypatch, fast):
    model = Guide(
        point(3, "Developer mode"),
        point(4, "Load unpacked"),
        point(1, "Folder box", action="type", text="Ctrl+V, then Select Folder"),
        DONE,
        point(4, "Extensions"),
        point(3, "Pyxi in your browser"),
    )
    brain = _brain(settings, model)
    started: list[tuple[str, ...]] = []
    monkeypatch.setattr(browsersetup, "front", lambda key="", open_windows=None: CHROME)
    monkeypatch.setattr(browsersetup, "show_page", lambda browser, exe: started.append((exe, browser.page)) or True)
    cards = Cards(brain.hub)
    flow = asyncio.ensure_future(browsersetup.setup(brain, "chrome"))

    await _until(lambda: brain._guide is not None and brain._guide.label == "Developer mode")
    where = browsersetup.folder("chromium")
    assert (where / "manifest.json").is_file()  # the folder is there before the browser is sent to it
    assert started == [(CHROME[1], "chrome://extensions")]  # Chrome's own page: not a web link
    assert {"type": "action", "kind": "copy", "value": str(where)}.items() <= next(e for e in cards.events if e["type"] == "action").items()
    notice = next(e["message"] for e in cards.events if e["type"] == "notice")
    assert notice == "Leave Developer mode on in Chrome: the extension only works while it is on. Chrome may mention developer mode extensions when it starts, which is expected for one that isn't from the store."
    assert "three clicks" in brain.said[0]
    assert "its extensions page is open in front of me" in model.asked[0]
    assert "Turn on the 'Developer mode' switch" in model.asked[0] and "Load unpacked" in model.asked[0]
    assert "top right of the page" in model.asked[0] and "three lines" not in model.asked[0]  # Chrome's layout, not Edge's

    # It waits for each real click: nothing moves on until the user has done the step.
    await asyncio.sleep(0.05)
    assert brain._guide.label == "Developer mode" and len(model.asked) == 1
    await brain.guide_clicked("clicked")
    await _until(lambda: brain._guide is not None and brain._guide.label == "Load unpacked")
    await brain.guide_clicked("clicked")
    await _until(lambda: brain._guide is not None and brain._guide.action == "type")
    await brain.guide_clicked("done")

    # Loaded: now the pairing code, on the pointer and in Settings.
    await _until(lambda: any(e["type"] == "browser" and e.get("code") for e in cards.events))
    code = [e["code"] for e in cards.events if e["type"] == "browser" and e.get("code")][-1]
    await _until(lambda: brain._guide is not None and brain._guide.label == "Extensions")
    assert f"text '{code}'" in model.asked[-1] and "puzzle piece" in model.asked[-1]
    await brain.guide_clicked("clicked")
    await _until(lambda: brain._guide is not None and brain._guide.label == "Pyxi in your browser")

    PretendBrowser(brain.browser)  # the extension pairs and connects
    await asyncio.wait_for(flow, 10)
    assert brain.said[-1] == "Chrome is connected. Ask me to do something in a tab."
    assert brain._guide is None and not brain._browser_setup
    assert [e for e in cards.events if e["type"] == "guide"][-1]["finished"] is True
    last = [e for e in cards.events if e["type"] == "browser"][-1]
    assert last["connected"] and last["setup"]["running"] is False


async def test_edge_gets_its_own_page_and_a_model_that_cant_point_gets_the_steps_in_words(settings, bundle, monkeypatch, fast):
    brain = _brain(settings, Guide())  # says "Okay." and points at nothing
    started: list[tuple[str, ...]] = []
    monkeypatch.setattr(browsersetup, "front", lambda key="", open_windows=None: EDGE)
    monkeypatch.setattr(browsersetup, "show_page", lambda browser, exe: started.append((exe, browser.page)) or True)
    monkeypatch.setattr(browsersetup, "PAIR_WAIT_S", 0.2)
    cards = Cards(brain.hub)
    await asyncio.wait_for(browsersetup.setup(brain), 20)
    assert started == [(EDGE[1], "edge://extensions")]
    notices = [e["message"] for e in cards.events if e["type"] == "notice"]
    # What Edge itself asks at every start, and which button keeps the extension working.
    assert notices[0] == 'Each time Edge starts it asks "Turn off developer mode?". Press Not now: the extension only works while Developer mode is on.'
    assert notices[1].startswith(
        "I couldn't point at the steps, so here they are: 1. Type edge://extensions into Edge's address bar and press Enter. "
        "2. Turn on Developer mode (at the bottom of the menu on the left; in a narrow window, press the three lines at the top left first). "
        "3. Press Load unpacked (in a narrow window it is the small folder button on the right) and choose this folder: "
    )
    asked = brain.llm.asked[0]  # Edge keeps the two controls somewhere else: the model is told where
    assert "bottom of the menu on the left" in asked and "three lines at the top left" in asked and "small button with a folder" in asked
    assert str(browsersetup.folder("chromium")) in notices[1]
    assert notices[-1].startswith("The pairing code ran out.")  # nobody typed it
    assert not brain._browser_setup and not brain.browser.connected


async def test_when_the_page_cant_be_opened_the_guide_starts_at_the_address_bar(settings, bundle, monkeypatch, fast):
    model = Guide(point(2, "Address bar", action="type", text="chrome://extensions, then Enter"))
    brain = _brain(settings, model)
    monkeypatch.setattr(browsersetup, "front", lambda key="", open_windows=None: CHROME)
    monkeypatch.setattr(browsersetup, "show_page", lambda browser, exe: False)
    monkeypatch.setattr(browsersetup, "LOAD_WAIT_S", 0.3)
    monkeypatch.setattr(browsersetup, "PAIR_WAIT_S", 0.2)
    flow = asyncio.ensure_future(browsersetup.setup(brain, "chrome"))
    await _until(lambda: brain._guide is not None and brain._guide.label == "Address bar")
    assert brain.said[0] == "I've copied the folder's address. First open Chrome's extensions page: follow the pointer."
    assert "its extensions page is not open yet" in model.asked[0]
    assert "type chrome://extensions and press Enter" in model.asked[0]
    await asyncio.wait_for(flow, 20)


class Desk:
    """A pretend desktop for show_page: windows, which one is in front, and the keys pressed."""

    def __init__(self, monkeypatch, *, opens: bool = True, steals: int = -1, deaf: int = 0) -> None:
        from pyxi import hands, windows

        self.windows = [(11, "Inbox - Google Chrome", CHROME[1]), (12, "Notes", r"C:\Windows\notepad.exe")]
        self.front = 12
        self.keys: list[str] = []
        self.started: list[tuple[str, ...]] = []
        desk = self

        def launch(exe: str, *args: str) -> None:
            desk.started.append((exe, *args))
            if opens:
                desk.windows.insert(0, (21, "New Tab - Google Chrome", exe))
                desk.front = 21

        class Keys:
            def press(self, combo: str) -> None:
                desk.note(f"<{combo}>")

            def type(self, text: str) -> None:
                desk.note(text)

        monkeypatch.setattr(browsersetup, "launch", launch)
        monkeypatch.setattr(browsersetup, "PAGE_WAIT_S", 0.5)
        monkeypatch.setattr(browsersetup, "PAGE_SETTLE_S", 0.0)
        monkeypatch.setattr(browsersetup, "PAGE_CHANGE_S", 0.1)
        self.deaf = deaf  # how many times the new window drops what is typed (still starting)
        monkeypatch.setattr(windows, "open_windows", lambda minimized=False: list(self.windows))
        monkeypatch.setattr(windows, "foreground", lambda: self.front)
        monkeypatch.setattr(windows, "focus", lambda hwnd: setattr(self, "front", hwnd) or True)
        monkeypatch.setattr(hands, "Hands", Keys)
        self.steals = steals

    def note(self, what: str) -> None:
        assert self.front == 21, f"typed {what!r} into window {self.front}"
        self.keys.append(what)
        if len(self.keys) == self.steals:
            self.front = 12  # the user clicks into another window
        if what == "<enter>":
            if self.deaf:
                self.deaf -= 1
            else:  # the page opens: the window is named after it
                self.windows[0] = (21, "Extensions - Google Chrome", self.windows[0][2])


def test_the_extensions_page_is_opened_by_typing_its_address_into_a_new_window(monkeypatch):
    chrome = browsersetup.BROWSERS["chrome.exe"]
    desk = Desk(monkeypatch)
    assert browsersetup.show_page(chrome, CHROME[1]) is True
    # Not handed to Chrome as an address: it ignores chrome:// ones there. A new window, then typed.
    assert desk.started == [(CHROME[1], "--new-window")]
    assert desk.keys[0] == "<ctrl+l>" and desk.keys[-2:] == ["<delete>", "<enter>"]
    assert "".join(desk.keys[1:-2]) == "chrome://extensions"


def test_a_window_that_drops_the_keys_is_typed_into_once_more_and_then_left(monkeypatch):
    chrome = browsersetup.BROWSERS["chrome.exe"]
    desk = Desk(monkeypatch, deaf=1)  # still starting: the first try goes nowhere
    assert browsersetup.show_page(chrome, CHROME[1]) is True
    assert desk.keys.count("<enter>") == 2 and desk.keys.count("<ctrl+l>") == 2  # the bar is cleared first
    desk = Desk(monkeypatch, deaf=5)  # it never takes it: say so, so the guide starts at the address bar
    assert browsersetup.show_page(chrome, CHROME[1]) is False
    assert desk.keys.count("<enter>") == 2


def test_nothing_is_typed_when_no_new_window_comes_or_another_window_takes_the_front(monkeypatch):
    chrome = browsersetup.BROWSERS["chrome.exe"]
    desk = Desk(monkeypatch, opens=False)
    assert browsersetup.show_page(chrome, CHROME[1]) is False and desk.keys == []
    desk = Desk(monkeypatch, steals=4)  # the user clicks elsewhere after the first few letters
    assert browsersetup.show_page(chrome, CHROME[1]) is False
    assert desk.keys == ["<ctrl+l>", "c", "h", "r"] and "<enter>" not in desk.keys  # not one key more


async def test_add_to_firefox_opens_the_signed_add_on_in_firefox_then_pairs(settings, bundle, monkeypatch, fast):
    model = Guide(point(4, "Add"), DONE, point(4, "Extensions"))
    brain = _brain(settings, model)
    started: list[tuple[str, ...]] = []
    monkeypatch.setattr(browsersetup, "front", lambda key="", open_windows=None: FIREFOX)
    monkeypatch.setattr(browsersetup, "launch", lambda exe, *args: started.append((exe, *args)))

    async def published(client: Any = None, *, again: bool = False) -> str:
        return f"{LATEST}/pyxi-firefox.xpi"

    monkeypatch.setattr(browsersetup, "firefox_addon", published)
    cards = Cards(brain.hub)
    flow = asyncio.ensure_future(browsersetup.setup(brain, "firefox"))
    await _until(lambda: brain._guide is not None and brain._guide.label == "Add")
    assert started == [(FIREFOX[1], f"{LATEST}/pyxi-firefox.xpi")]  # in Firefox, whatever the default browser is
    assert brain.said[0] == "Firefox is asking whether to add Pyxi. Press Add."
    assert not browsersetup.folder("firefox").exists()  # nothing to load by hand
    await brain.guide_clicked("clicked")
    await _until(lambda: brain._guide is not None and brain._guide.label == "Extensions")
    PretendBrowser(brain.browser, name="Firefox")
    await asyncio.wait_for(flow, 10)
    assert brain.said[-1] == "Firefox is connected. Ask me to do something in a tab."
    assert not any("developer mode" in e.get("message", "") for e in cards.events)  # not Firefox's business


async def test_with_no_signed_add_on_published_firefox_gets_the_by_hand_way_not_a_dead_link(settings, bundle, monkeypatch, fast):
    brain = _brain(settings, Guide())
    started: list[tuple[str, ...]] = []
    monkeypatch.setattr(browsersetup, "front", lambda key="", open_windows=None: FIREFOX)
    monkeypatch.setattr(browsersetup, "launch", lambda exe, *args: started.append((exe, *args)))
    monkeypatch.setattr(browsersetup, "PAIR_WAIT_S", 0.2)
    client, _ = _updates_server(404)

    real = browsersetup.firefox_addon

    async def not_published(c: Any = None, *, again: bool = False) -> str:
        return await real(client, again=True)

    monkeypatch.setattr(browsersetup, "firefox_addon", not_published)
    cards = Cards(brain.hub)
    await asyncio.wait_for(browsersetup.setup(brain, "firefox"), 20)
    assert started == [(FIREFOX[1], "about:debugging#/runtime/this-firefox")]
    assert not any(".xpi" in " ".join(s) for s in started)
    manifest = browsersetup.folder("firefox") / "manifest.json"
    assert manifest.is_file()
    assert [e["value"] for e in cards.events if e["type"] == "action"] == [str(manifest)]
    said = next(e["message"] for e in cards.events if e["type"] == "notice")
    assert "isn't published yet" in said and "until Firefox closes" in said
    shown = browsersetup.public(brain)["setup"]
    assert shown["addon"] is False and "about:debugging" in shown["steps"][0]


async def test_setup_says_so_when_no_browser_is_open_or_one_is_already_connected(settings, bundle, monkeypatch, fast):
    brain = _brain(settings, Guide())
    cards = Cards(brain.hub)
    await browsersetup.setup(brain, "edge")  # front() finds nothing (conftest)
    assert [e["message"] for e in cards.events if e["type"] == "notice"] == ["Open Edge first, then ask me again."]
    PretendBrowser(brain.browser)
    monkeypatch.setattr(browsersetup, "front", lambda key="", open_windows=None: CHROME)
    await browsersetup.setup(brain)
    assert brain.said == ["Your browser is already connected."]


# ====================================================================== when they ask, and Settings
async def test_connect_my_browser_is_a_tool_only_while_none_is_connected(settings, bundle, monkeypatch):
    brain = _brain(settings)
    assert [t.name for t in tabwork.tools(brain)] == ["connect_browser"]
    asked: list[str] = []

    async def setup(b: Any, key: str = "") -> None:
        asked.append(key)

    monkeypatch.setattr(browsersetup, "setup", setup)
    note = await tabwork.handle(brain, "connect_browser", {})
    assert note.startswith("connect_browser: no browser Pyxi can set up is open")
    monkeypatch.setattr(browsersetup, "front", lambda key="", open_windows=None: EDGE if key in ("", "edge") else None)
    assert await tabwork.handle(brain, "connect_browser", {"browser": "Microsoft Edge"}) is None
    await asyncio.sleep(0.05)
    assert asked == ["edge"]
    assert (await tabwork.handle(brain, "connect_browser", {"browser": "chrome"})).startswith("connect_browser: no browser")
    PretendBrowser(brain.browser)
    assert "connect_browser" not in {t.name for t in tabwork.tools(brain)}
    assert (await tabwork.handle(brain, "connect_browser", {})).startswith("connect_browser: their browser is already connected")


def test_settings_is_told_which_browser_and_how_and_can_start_it(settings, bundle, monkeypatch):
    brain = _brain(settings)
    started: list[str] = []

    async def setup(b: Any, key: str = "") -> None:
        started.append(key)

    async def nothing(b: Any) -> None:
        return None

    monkeypatch.setattr(browsersetup, "setup", setup)
    monkeypatch.setattr(browsersetup, "look_up_addon", nothing)
    monkeypatch.setattr(browsersetup, "front", lambda key="", open_windows=None: EDGE)
    app = create_app(settings, demo=True, brain=brain, key=KEY)
    with TestClient(app, client=LOCAL) as client, client.websocket_connect(f"/ws?key={KEY}") as panel:
        assert json.loads(panel.receive_text())["type"] == "hello"
        panel.send_text('{"type":"browser_get"}')
        shown = json.loads(panel.receive_text())
        while shown["type"] != "browser":
            shown = json.loads(panel.receive_text())
        setup_info = shown["setup"]
        assert (setup_info["browser"], setup_info["key"], setup_info["kind"], setup_info["checked"]) == ("Edge", "edge", "chromium", True)
        assert setup_info["notice"].startswith('Each time Edge starts it asks "Turn off developer mode?". Press Not now')
        assert setup_info["steps"][0] == "Type edge://extensions into Edge's address bar and press Enter." and len(setup_info["steps"]) == 4
        assert setup_info["running"] is False and setup_info["addon"] is None
        panel.send_text(json.dumps({"type": "browser_setup", "browser": "edge"}))
        panel.send_text('{"type":"browser_get"}')
        while json.loads(panel.receive_text())["type"] != "browser":
            pass
    assert started == ["edge"]
    monkeypatch.setattr(browsersetup, "front", lambda key="", open_windows=None: None)
    assert browsersetup.public(brain) == {"setup": {"browser": "", "key": "", "kind": "", "running": False}}
    monkeypatch.setattr(browsersetup, "front", lambda key="", open_windows=None: (browsersetup.BROWSERS["brave.exe"], "brave.exe"))
    assert browsersetup.public(brain)["setup"]["checked"] is False  # said, in Settings


# ====================================================================== the offer
def _in(brain: Any, app: str, said: str, monkeypatch: Any, front: Any = CHROME) -> None:
    brain._ctx = fake_context(app=app, title="Cart - Shop")
    brain._user_text = said
    monkeypatch.setattr(browsersetup, "front", lambda key="", open_windows=None: front)


@pytest.fixture
def setups(monkeypatch) -> list[str]:
    started: list[str] = []

    async def setup(b: Any, key: str = "") -> None:
        started.append(key)

    monkeypatch.setattr(browsersetup, "setup", setup)
    return started


CLICK = {"task": "On the page in front, press the Add to cart button and stop at the cart."}


async def test_a_task_for_their_own_tab_gets_one_line_and_a_card_and_yes_sets_it_up(settings, monkeypatch, setups):
    brain = _brain(settings)
    _in(brain, "chrome.exe", "add this to my cart", monkeypatch)
    cards = Cards(brain.hub, answer=True)
    note = await tabwork.before_fetch(brain, "use_computer", CLICK)
    await asyncio.sleep(0.05)
    assert brain.said == ["I could do that inside your Chrome tab, without taking your mouse, if your browser were connected."]
    assert cards.asked == ["Set up Pyxi in Chrome, so it can work in your own tabs?"]
    reason = cards.card()["reason"]
    assert "about a minute" in reason and "developer mode extensions" in reason and "won't ask again" in reason
    assert setups == ["chrome"]
    assert note.startswith("use_computer: they chose to connect their browser first")  # the mouse isn't taken meanwhile


async def test_firefox_is_only_offered_once_a_signed_add_on_is_published(settings, monkeypatch, setups):
    brain = _brain(settings)
    _in(brain, "firefox.exe", "add this to my cart", monkeypatch, front=FIREFOX)
    cards = Cards(brain.hub, answer=True)
    looked: list[bool] = []

    async def look(client: Any = None, *, again: bool = False) -> str:
        looked.append(True)
        browsersetup._addon = (time.monotonic(), "")  # looked up: nothing is published
        return ""

    monkeypatch.setattr(browsersetup, "firefox_addon", look)
    # Not known yet: no card, the usual way goes ahead, and it is looked up for next time.
    assert await tabwork.before_fetch(brain, "use_computer", CLICK) is None
    await asyncio.sleep(0.05)
    assert looked == [True] and cards.asked == [] and brain.said == []
    # Known, and nothing is published: what there is lasts until Firefox closes, so no offer.
    assert await tabwork.before_fetch(brain, "use_computer", CLICK) is None
    assert looked == [True] and cards.asked == [] and setups == []
    # Published: offered, and the card says what Firefox will do.
    monkeypatch.setattr(browsersetup, "_addon", (time.monotonic(), browsersetup.XPI_HOME + "latest/download/pyxi-firefox.xpi"))
    note = await tabwork.before_fetch(brain, "use_computer", CLICK)
    await asyncio.sleep(0.05)
    assert cards.asked == ["Set up Pyxi in Firefox, so it can work in your own tabs?"]
    assert "Firefox asks you to add it, then you type a code." in cards.card()["reason"]
    assert setups == ["firefox"] and note.startswith("use_computer: they chose to connect")


async def test_dont_carries_on_the_usual_way_and_it_is_not_offered_again(settings, monkeypatch, setups):
    brain = _brain(settings)
    _in(brain, "msedge.exe", "add this to my cart", monkeypatch, front=EDGE)
    cards = Cards(brain.hub, answer=False)
    assert await tabwork.before_fetch(brain, "use_computer", CLICK) is None  # carries on as it does today
    assert cards.asked == ["Set up Pyxi in Edge, so it can work in your own tabs?"] and setups == []
    assert await tabwork.before_fetch(brain, "use_computer", CLICK) is None
    assert len(cards.asked) == 1 and len(brain.said) == 1  # no second line, no second card

    # Not after a restart of Pyxi either: the no is kept.
    again = _brain(settings)
    _in(again, "msedge.exe", "add this to my cart", monkeypatch, front=EDGE)
    later = Cards(again.hub, answer=True)
    assert await tabwork.before_fetch(again, "use_computer", CLICK) is None
    assert later.asked == [] and again.said == []

    # Unless they ask: then it's set up, and the no is forgotten.
    assert await tabwork.handle(again, "connect_browser", {}) is None
    await asyncio.sleep(0.05)
    assert setups == [""]


async def test_a_card_nobody_answers_is_offered_once_more_and_then_left_alone(settings, monkeypatch, setups):
    for expected in (1, 1, 0):
        brain = _brain(settings)  # each a new run of Pyxi
        _in(brain, "chrome.exe", "add this to my cart", monkeypatch)
        cards = Cards(brain.hub, answer=None)
        task = asyncio.ensure_future(tabwork.before_fetch(brain, "use_computer", CLICK))
        await asyncio.sleep(0.05)
        brain.hub.cancel_all()  # the card goes unanswered
        assert await task is None and len(cards.asked) == expected


@pytest.mark.parametrize(
    ("app", "said", "name", "args"),
    [
        # The mouse is headed somewhere that isn't a web page.
        ("explorer.exe", "rename these files", "use_computer", {"task": "Rename the three files in this folder."}),
        ("chrome.exe", "play some jazz", "use_computer", {"task": "Open Spotify and play jazz.", "app": "Spotify"}),
        ("chrome.exe", "open my downloads", "use_computer", {"task": "Open File Explorer and go to Downloads."}),
        # Scout doing what Scout does well: research across sites, nothing of theirs.
        ("chrome.exe", "find the cheapest flight to Dhaka", "delegate", {"agent": "scout", "task": "Find the cheapest flight to Dhaka in March."}),
        ("chrome.exe", "write a reply about this page", "delegate", {"agent": "quill", "task": "Draft a reply."}),
        # Routes that already work without the user's tab are never asked about.
        ("chrome.exe", "what's the weather", "web_search", {"query": "weather vancouver"}),
    ],
)
async def test_it_does_not_offer_for_what_the_existing_routes_handle(settings, monkeypatch, setups, app, said, name, args):
    brain = _brain(settings)
    _in(brain, app, said, monkeypatch)
    cards = Cards(brain.hub, answer=True)
    assert await tabwork.before_fetch(brain, name, args) is None
    assert cards.asked == [] and brain.said == [] and setups == []


async def test_scout_being_sent_to_their_own_tab_or_account_is_offered_too(settings, monkeypatch, setups):
    for said in ("check my orders and tell me when the lamp ships", "fill in the form on this page", "I'm signed in, grab the invoice"):
        brain = _brain(settings)
        _in(brain, "firefox.exe", said, monkeypatch, front=FIREFOX)
        monkeypatch.setattr(browsersetup, "_addon", (time.monotonic(), browsersetup.XPI_HOME + "latest/download/pyxi-firefox.xpi"))  # published
        cards = Cards(brain.hub, answer=True)
        note = await tabwork.before_fetch(brain, "delegate", {"agent": "Scout", "task": "Do it."})
        await asyncio.sleep(0.02)
        assert cards.asked == ["Set up Pyxi in Firefox, so it can work in your own tabs?"], said
        assert "developer mode" not in cards.card()["reason"] and "Firefox asks you to add it" in cards.card()["reason"]
        assert note.startswith("delegate: they chose to connect their browser first")
        (settings.data_dir / browsersetup.STATE).unlink(missing_ok=True)
    assert setups == ["firefox"] * 3


async def test_no_offer_while_a_browser_is_connected_or_none_is_open(settings, monkeypatch, setups):
    brain = _brain(settings)
    _in(brain, "chrome.exe", "add this to my cart", monkeypatch, front=None)  # the window list has no browser
    cards = Cards(brain.hub, answer=True)
    assert await tabwork.before_fetch(brain, "use_computer", CLICK) is None and cards.asked == []
    _in(brain, "chrome.exe", "add this to my cart", monkeypatch)
    PretendBrowser(brain.browser)
    assert await tabwork.before_fetch(brain, "use_computer", CLICK) is None and cards.asked == []
    # The brain asks before these routes and no others (and the two fetches, as before).
    assert {"read_page", "web_search", "use_computer", "delegate"} == tabwork.FETCHES


def test_nothing_the_user_reads_here_has_em_dashes_or_emoji():
    shown = "".join(
        (ROOT / path).read_text(encoding="utf-8")
        for path in (
            "src/pyxi/companion/browsersetup.py",
            "extension/sign_firefox.py",
            "extension/README.md",
            "desktop/src/renderer/src/panel/BrowserConnect.tsx",
        )
    )
    assert "—" not in shown and "–" not in shown
    assert not re.search("[\U0001f300-\U0001faff\u2600-\u27bf]", shown)
