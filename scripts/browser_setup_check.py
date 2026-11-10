"""Installing the browser extension without a store, checked in real browsers.

What Pyxi's "Set it up for me" relies on, and what it tells the user, tried in a throwaway
profile of Playwright's Chromium and of the installed Edge and Firefox (never a real profile):

  Chromium and Edge
  - the extensions page does not open when its address is given on the command line (the
    browser opens a new tab instead), which is why Pyxi doesn't do it that way;
  - it does open the way Pyxi does it: a new window, and the address typed into it. This
    step uses a window on the screen and the real keyboard for about two seconds (keys go
    only while that throwaway window is in front); --unseen skips it;
  - the page has the two controls the guide points at, under the names it uses: a
    "Developer mode" switch and, once that is on, "Load unpacked";
  - "Load unpacked" opens Windows' folder window, with the "Folder:" box and the "Select
    Folder" button the guide names; given the folder Pyxi keeps the extension in, the browser
    loads it (the box is filled and the button pressed through Windows' accessibility
    interface, the way a screen reader would: no mouse, no keyboard);
  - after Pyxi updates that folder in place and the browser is restarted it is still loaded,
    under the same id, in its new version;
  - what the browser does with it when Developer mode is switched off again;
  - what the browser says about it when it starts (read from a window on the screen, through
    Windows' accessibility interface; --unseen skips it).
  Firefox
  - given about:debugging on the command line, Firefox opens it (the by-hand way Pyxi falls
    back to while no signed add-on is published);
  - handed the address of an .xpi served the way a release serves it, the running Firefox
    starts installing an add-on (it doesn't just download a file). The package here isn't
    signed, so Firefox then refuses it as unverified, which is what it should do;
  - the package that extension/sign_firefox.py sends to Mozilla installs (as a temporary
    add-on, since it isn't signed here) and Firefox reads its id, its version and its update
    address from it.

    python scripts/browser_setup_check.py [--browser chromium|msedge|firefox|all] [--shown] [--unseen]

Chromium and Edge run in real windows (an extension can't be loaded by hand without one),
placed off the screen unless --shown is given.

What this can't do: answer Firefox's own "Add?" question for a signed add-on (there is no
signed file to try it with), follow Pyxi's on-screen pointer with a real mouse, or say what
branded Chrome does differently from Chromium (Chrome was not driven here). Those were checked
by reading, and by the unit tests with a pretend screen (tests/test_browsersetup.py).
"""

from __future__ import annotations

import argparse
import asyncio
import http.server
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "extension"))

from browser_e2e import Marionette, check, chromium_copy, free_port, until  # noqa: E402

from pyxi.companion import browsersetup  # noqa: E402

EDGE = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
FIREFOX = Path(r"C:\Program Files\Mozilla Firefox\firefox.exe")


SHOWN = False  # --shown: put the windows on the screen


def start(exe: Path, profile: Path, port: int, *more: str) -> subprocess.Popen:
    """The browser in a window, with what a check needs: its own profile and a port to look
    in through."""
    args = [
        str(exe),
        f"--user-data-dir={profile}",
        f"--remote-debugging-port={port}",
        "--no-first-run",
        "--no-default-browser-check",
        "--window-size=1000,800",
        *([] if SHOWN else ["--window-position=-32000,-32000"]),
        *more,
    ]
    return subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


async def close(pw: Any, port: int, proc: subprocess.Popen) -> None:
    """Close the browser the way its own Exit does, so it writes down what it has loaded."""
    try:
        browser = await pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        await (await browser.new_browser_cdp_session()).send("Browser.close")
    except Exception:
        pass
    try:
        await asyncio.to_thread(proc.wait, 15)
    except subprocess.TimeoutExpired:
        stop(proc)


def pick_folder(pid: int, path: Path, wait_s: float = 15.0) -> str:
    """Windows' folder window that "Load unpacked" opens: put the folder in its "Folder:" box
    and press "Select Folder", through the accessibility interface. "" when done, else what
    was missing."""
    import uiautomation as auto

    with auto.UIAutomationInitializerInThread():
        end = time.monotonic() + wait_s
        dialog = None
        while dialog is None and time.monotonic() < end:
            for window in auto.GetRootControl().GetChildren():
                if window.ProcessId != pid:
                    continue
                for inner in [window, *window.GetChildren()]:
                    if inner.ClassName == "#32770":
                        dialog = inner
            if dialog is None:
                time.sleep(0.3)
        if dialog is None:
            return "no folder window opened"
        box = dialog.EditControl(Name="Folder:", searchDepth=6)
        button = dialog.ButtonControl(Name="Select Folder", searchDepth=6)
        if not box.Exists(3, 0.3):
            return f"the window '{dialog.Name}' has no 'Folder:' box"
        if not button.Exists(3, 0.3):
            return f"the window '{dialog.Name}' has no 'Select Folder' button"
        box.GetValuePattern().SetValue(str(path))
        time.sleep(0.3)
        button.GetInvokePattern().Invoke()
    return ""


def stop(proc: subprocess.Popen) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()


async def attached(pw: Any, port: int) -> Any:
    def up() -> bool:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1).read()
            return True
        except OSError:
            return False

    if not await until(up, 30):
        raise SystemExit("the browser didn't start")
    return await pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")


def page_addresses(port: int) -> list[str]:
    try:
        raw = urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=2).read()
    except OSError:
        return []
    return [t["url"] for t in json.loads(raw) if t["type"] == "page"]


async def extensions_page(browser: Any, address: str) -> Any:
    """A tab on the extensions page. A check can't type into a browser nobody sees, so here
    the tab is sent there through the debugging connection."""
    pages = [p for c in browser.contexts for p in c.pages]
    page = next((p for p in pages if p.url.startswith(address)), None)
    if page is None:
        page = next((p for p in pages if "dialog" not in p.url), None) or await browser.contexts[0].new_page()
        await page.goto(address)
    await page.wait_for_load_state()
    return page


async def close_by_port(pw: Any, port: int) -> None:
    try:
        browser = await pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        await (await browser.new_browser_cdp_session()).send("Browser.close")
    except Exception as exc:
        print(f"      (couldn't close a throwaway window by itself: {type(exc).__name__})")


async def opening(pw: Any, name: str, exe: Path, address: str, tmp: Path, keys: bool) -> None:
    tag = f"{name}: "
    # Given on the command line, the address is dropped.
    port = free_port()
    proc = start(exe, tmp / f"{name.lower()}-args", port, address)
    try:
        await attached(pw, port)
        await asyncio.sleep(2)
        seen = page_addresses(port)
        check(
            tag + f"given {address} on the command line, it does not open it (so Pyxi can't open it that way)",
            bool(seen) and not any(u.startswith(address) for u in seen),
            str(seen),
        )
    finally:
        stop(proc)
    if not keys:
        print(f"      skipped ({name}): opening the page the way Pyxi does, with the real keyboard")
        return
    # The way Pyxi does it: a new window, the address typed into it.
    which = browsersetup.BROWSERS["msedge.exe" if name == "Edge" else "chrome.exe"]
    for attempt in (1, 2):  # someone using this PC can take the front mid-way: Pyxi then stops typing
        port = free_port()
        profile = tmp / f"{name.lower()}-typed-{attempt}"
        typed = await asyncio.to_thread(
            browsersetup.show_page,
            which,
            str(exe),
            f"--user-data-dir={profile}",
            f"--remote-debugging-port={port}",
            "--no-first-run",
            "--no-default-browser-check",
            "--window-size=900,700",
        )
        if typed or attempt == 2:
            break
        print(f"      ({name}: another window took the front while typing, so it stopped; once more)")
        await close_by_port(pw, port)
        await asyncio.sleep(3)
    try:
        await until(lambda: any(u.startswith(address) for u in page_addresses(port)), 8)
        seen = page_addresses(port)
        check(
            tag + "a new window with the address typed into it does open the extensions page",
            typed and any(u.startswith(address) for u in seen),
            f"typed={typed} pages={seen}",
        )
    finally:
        await close_by_port(pw, port)


def startup_notice(pid: int) -> list[str]:
    """What the browser's own window says about developer mode (not the page: the browser)."""
    import uiautomation as auto

    found: list[str] = []

    def everything(node: Any) -> None:
        for child in node.GetChildren():
            if child.Name and child.ControlTypeName in ("TextControl", "ButtonControl"):
                found.append(f"{child.ControlTypeName[:-7]}: {child.Name}")
            everything(child)

    def walk(node: Any, depth: int) -> None:
        for child in node.GetChildren():
            if child.ControlTypeName == "PaneControl" and "developer" in (child.Name or "").lower():
                everything(child)
            elif child.ControlTypeName != "DocumentControl" and depth < 16:
                walk(child, depth + 1)

    with auto.UIAutomationInitializerInThread():
        for window in auto.GetRootControl().GetChildren():
            if window.ProcessId == pid:
                walk(window, 1)
    return list(dict.fromkeys(found))


async def seen(page: Any, *choices: Any) -> Any:
    """The first of these that is on the page and can be seen, or None."""
    for choice in choices:
        for i in range(await choice.count()):
            if await choice.nth(i).is_visible():
                return choice.nth(i)
    return None


async def dev_switch(page: Any) -> Any:
    return await seen(
        page,
        page.get_by_role("switch", name="Developer mode"),
        page.locator("#devMode, #dev-switch, [aria-label='Developer mode']"),
    )


async def load_button(page: Any) -> Any:
    return await seen(
        page,
        page.get_by_role("button", name="Load unpacked"),
        page.get_by_text("Load unpacked", exact=True),
        # Edge in a narrow window: a small picture of a folder with no words, named for readers
        page.locator("[aria-label='Load unpacked'], [title='Load unpacked']"),
    )


async def our_extension(page: Any) -> dict[str, Any] | None:
    """What the extensions page says about "Pyxi in your browser", asked of the page's own
    management call (the page is the browser's, so it may)."""
    try:
        items = await page.evaluate(
            "new Promise((done) => chrome.developerPrivate.getExtensionsInfo({}, (all) => done("
            "all.map((e) => ({ id: e.id, name: e.name, version: e.version, state: e.state, "
            "location: e.location, off_because: Object.keys(e.disableReasons || {}).filter((k) => e.disableReasons[k]) })))))"
        )
    except Exception as exc:
        return {"error": str(exc)[:200]}
    return next((e for e in items if e["name"].startswith("Pyxi in your browser")), None)


async def chromium_family(pw: Any, name: str, exe: Path, address: str, tmp: Path, keys: bool) -> None:
    tag = f"{name}: "
    await opening(pw, name, exe, address, tmp, keys)
    profile = tmp / f"{name.lower()}-profile"
    home = tmp / f"{name.lower()}-home"
    os.environ[browsersetup.HOME_ENV] = str(home)
    folder = browsersetup.ensure_folder("chromium")  # the folder Pyxi keeps for the browser
    port = free_port()

    # 1. The extensions page.
    proc = start(exe, profile, port)
    first_id = first_version = None
    try:
        browser = await attached(pw, port)
        page = await extensions_page(browser, address)

        # 2. The controls the guide points at, by the names it uses.
        switch = await dev_switch(page)
        if switch is None:
            # Edge keeps the switch in its left-hand menu, and folds that menu away behind the
            # button with three lines (top left) when the window is narrow.
            await page.mouse.click(28, 41)
            await asyncio.sleep(1)
            switch = await dev_switch(page)
            check(
                tag + "in a narrow window the 'Developer mode' switch is behind the menu button at the top left",
                switch is not None,
            )
        check(tag + "the page has a 'Developer mode' switch", switch is not None)
        if switch is None:
            return
        before = await load_button(page)
        await switch.click()
        await asyncio.sleep(1)
        if name == "Edge":
            await page.keyboard.press("Escape")  # fold the menu away again, as a click beside it would
            await asyncio.sleep(0.5)
        load = await load_button(page)
        check(
            tag + "'Load unpacked' is there once Developer mode is on (and not before)",
            before is None and load is not None,
            f"before={before is not None} after={load is not None}",
        )
        if load is None:
            return

        # 3. "Load unpacked", the folder window, the folder Pyxi keeps.
        await load.click()
        missing = await asyncio.to_thread(pick_folder, proc.pid, folder)
        check(
            tag + "'Load unpacked' opens a folder window with a 'Folder:' box and a 'Select Folder' button",
            not missing,
            missing,
        )
        mine = None
        for _ in range(40):
            mine = await our_extension(page)
            if mine and "error" not in mine:
                break
            await asyncio.sleep(0.25)
        check(
            tag + "given the folder Pyxi keeps, it loads 'Pyxi in your browser' and switches it on",
            bool(mine) and mine.get("state") == "ENABLED" and mine.get("location") == "UNPACKED",
            str(mine),
        )
        first_id, first_version = (mine or {}).get("id"), (mine or {}).get("version")
        await asyncio.sleep(1)
    finally:
        await close(pw, port, proc)
    if not first_id:
        return

    # 4. Pyxi is updated: the folder changes in place, the browser restarts.
    newer = tmp / f"{name.lower()}-newer"
    browsersetup.sync(folder, newer)
    spec = json.loads((newer / "manifest.json").read_text(encoding="utf-8"))
    (newer / "manifest.json").write_text(json.dumps({**spec, "version": "9.9.9"}), encoding="utf-8")
    browsersetup.sync(newer, folder)
    port = free_port()
    proc = start(exe, profile, port)
    try:
        browser = await attached(pw, port)
        page = await extensions_page(browser, address)
        mine = await our_extension(page)
        check(
            tag + "after a restart it is still loaded, under the same id, in the updated version",
            bool(mine) and mine.get("id") == first_id and mine.get("version") == "9.9.9" != first_version and mine.get("state") == "ENABLED",
            str(mine),
        )
        workers = [w.url for c in browser.contexts for w in c.service_workers]
        check(tag + "and its background script is running", any(first_id in w for w in workers), str(workers))

        # 5. Developer mode switched off again.
        async def flip() -> None:
            switch = await dev_switch(page)
            if switch is None:
                await page.mouse.click(28, 41)
                await asyncio.sleep(1)
                switch = await dev_switch(page)
            await switch.click()
            await asyncio.sleep(1.5)
            await page.reload()
            await page.wait_for_load_state()
            await asyncio.sleep(0.5)

        await flip()
        off = await our_extension(page)
        print(f"      with Developer mode off, {name} reports: {off}")
        check(
            tag + "with Developer mode switched off, the extension is switched off (so the user is told to leave it on)",
            bool(off) and off.get("state") != "ENABLED",
            str(off),
        )
        # And back on: it comes back by itself.
        await flip()
        back = await our_extension(page)
        check(
            tag + "and with Developer mode switched on again it is back on",
            bool(back) and back.get("state") == "ENABLED",
            str(back),
        )
    finally:
        await close(pw, port, proc)
    if not keys:
        print(f"      skipped ({name}): reading what it says when it starts")
        return

    # 6. What the browser says when it starts with the extension loaded (a window on the screen).
    global SHOWN
    was, SHOWN = SHOWN, True
    port = free_port()
    proc = start(exe, profile, port)
    try:
        await attached(pw, port)
        await asyncio.sleep(7)
        says = await asyncio.to_thread(startup_notice, proc.pid)
        print(f"      when it starts, {name} says: {says or 'nothing about developer mode'}")
        which = browsersetup.BROWSERS["msedge.exe" if name == "Edge" else "chrome.exe"]
        told = browsersetup.dev_notice(which)
        if name == "Edge":
            check(
                tag + "what Pyxi tells the user is what Edge asks: 'Turn off developer mode?', with a 'Not now' button",
                "Text: Turn off developer mode?" in says and "Button: Not now" in says and "Button: Turn off" in says
                and '"Turn off developer mode?"' in told and "Not now" in told,
                told,
            )
        else:
            check(tag + "Pyxi doesn't promise a notice this browser doesn't show", "may mention" in told, told)
    finally:
        SHOWN = was
        await close(pw, port, proc)


async def firefox(tmp: Path) -> None:
    from build import manifest, package

    spec = manifest("firefox")
    gecko = spec["browser_specific_settings"]["gecko"]
    xpi = package("firefox", tmp / "pyxi-firefox-unsigned.xpi")  # what sign_firefox.py sends
    profile = tmp / "firefox-profile"
    profile.mkdir()
    port = free_port()
    (profile / "user.js").write_text(
        f'user_pref("marionette.port", {port});\n'
        'user_pref("browser.shell.checkDefaultBrowser", false);\n'
        'user_pref("browser.aboutwelcome.enabled", false);\n'
        'user_pref("datareporting.policy.dataSubmissionEnabled", false);\n'
        'user_pref("app.update.auto", false);\n',
        encoding="utf-8",
    )
    by_hand = "about:debugging#/runtime/this-firefox"
    args = [str(FIREFOX), "-profile", str(profile), "-marionette", by_hand]
    if not SHOWN:
        args.insert(1, "-headless")
    env = {**os.environ, "MOZ_REMOTE_ALLOW_SYSTEM_ACCESS": "1"}
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
    m = Marionette()
    served = xpi.read_bytes()

    class Release(http.server.BaseHTTPRequestHandler):
        """Stands in for the release download: the file, as what a release says it is."""

        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "application/x-xpinstall")
            self.send_header("Content-Length", str(len(served)))
            self.end_headers()
            self.wfile.write(served)

        def log_message(self, *_: Any) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Release)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    tabs = (
        "arguments[arguments.length - 1](Services.wm.getMostRecentWindow('navigator:browser')"
        ".gBrowser.tabs.map((t) => t.linkedBrowser.currentURI.spec));"
    )
    try:
        await m.connect(port)
        await asyncio.sleep(2)
        opened = await m.chrome_js(tabs)
        check("FIREFOX: given about:debugging on the command line, it opens it", by_hand in opened, str(opened))

        # "Add to Firefox": the add-on's address, handed to the Firefox that is running.
        await m.chrome_js(
            "const done = arguments[arguments.length - 1];"
            "const { AddonManager } = ChromeUtils.importESModule('resource://gre/modules/AddonManager.sys.mjs');"
            "const w = Services.wm.getMostRecentWindow('navigator:browser'); w.__pyxi = [];"
            "const note = (what) => (i) => { w.__pyxi.push([what, i.error || 0]); };"
            "AddonManager.addInstallListener({ onNewInstall: note('new'), onDownloadStarted: note('download'),"
            " onDownloadEnded: note('downloaded'), onDownloadFailed: note('refused'), onInstallStarted: note('install'),"
            " onInstallEnded: note('installed'), onInstallFailed: note('refused') });"
            "done(true);"
        )
        address = f"http://127.0.0.1:{server.server_address[1]}/pyxi-firefox.xpi"
        browsersetup.launch(str(FIREFOX), "-profile", str(profile), address)  # as Pyxi does, plus the profile
        events: list[list[Any]] = []
        for _ in range(30):
            await asyncio.sleep(0.5)
            events = await m.chrome_js(
                "arguments[arguments.length - 1](Services.wm.getMostRecentWindow('navigator:browser').__pyxi);"
            )
            if any(e[0] in ("refused", "installed", "downloaded") for e in events):
                break
        check(
            "FIREFOX: handed the add-on's address, it starts installing an add-on (not a file download)",
            [e[0] for e in events][:2] == ["new", "download"],
            str(events),
        )
        said = await m.chrome_js(
            "const w = Services.wm.getMostRecentWindow('navigator:browser'); const out = [];"
            "for (const c of w.PopupNotifications.panel.children) out.push(c.getAttribute('label'));"
            "arguments[arguments.length - 1](out);"
        )
        print(f"      Firefox says: {said}")
        check(
            "FIREFOX: and it refuses this one because it isn't signed (the signed file is the founder's step)",
            ["refused", -5] in events and any("unverified" in str(line) for line in said),
            str(events),
        )
        try:
            added = await m.send("Addon:Install", {"path": str(xpi), "temporary": True})
            addon_id = added["value"] if isinstance(added, dict) else added
        except RuntimeError as exc:
            check("FIREFOX: the package sign_firefox.py sends installs", False, str(exc))
            return
        check("FIREFOX: the package sign_firefox.py sends installs, under the add-on's id", addon_id == gecko["id"], str(addon_id))
        seen = await m.chrome_js(
            "const done = arguments[arguments.length - 1];"
            "const { AddonManager } = ChromeUtils.importESModule('resource://gre/modules/AddonManager.sys.mjs');"
            "AddonManager.getAddonByID(arguments[0]).then((a) => done({ version: a.version, update: a.updateURL, on: a.isActive, name: a.name }),"
            " (err) => done({ error: String(err) }));",
            addon_id,
        )
        check(
            "FIREFOX: it reads the version and the update address the manifest gives",
            seen.get("version") == spec["version"] and seen.get("update") == gecko["update_url"] and seen.get("on") is True,
            str(seen),
        )
        version = await m.chrome_js("arguments[arguments.length - 1](Services.appinfo.version);")
        check(
            f"FIREFOX: this Firefox ({version}) is new enough for it (140 or later)",
            int(str(version).split(".")[0]) >= int(gecko["strict_min_version"].split(".")[0]),
        )
    finally:
        server.shutdown()
        try:
            await asyncio.wait_for(m.send("Marionette:Quit", {"flags": ["eForceQuit"]}), 10)
        except Exception:
            pass
        try:
            proc.wait(timeout=15)
        except Exception:
            proc.kill()


async def main(opts: argparse.Namespace) -> int:
    from browser_e2e import RESULTS
    from playwright.async_api import async_playwright

    tmp = Path(tempfile.mkdtemp(prefix="pyxi-setup-check-"))
    want = ["chromium", "msedge", "firefox"] if opts.browser == "all" else [opts.browser]
    async with async_playwright() as pw:
        if "chromium" in want:
            exe = chromium_copy(Path(pw.chromium.executable_path))
            await chromium_family(pw, "Chromium", exe, "chrome://extensions", tmp, not opts.unseen)
        if "msedge" in want:
            await chromium_family(pw, "Edge", EDGE, "edge://extensions", tmp, not opts.unseen)
    if "firefox" in want:
        await firefox(tmp)
    failed = [n for n, ok, _ in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)} of {len(RESULTS)} checks passed" + (f"; FAILED: {failed}" if failed else ""))
    print(f"(temporary files are in {tmp})")
    return 1 if failed else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="The store-free install, in real browsers.")
    parser.add_argument("--browser", default="all", choices=["all", "chromium", "msedge", "firefox"])
    parser.add_argument("--shown", action="store_true", help="put the browser windows on the screen")
    parser.add_argument("--unseen", action="store_true", help="skip the two steps that put a window on the screen (one types an address)")
    options = parser.parse_args()
    import logging

    logging.basicConfig(level=logging.WARNING)
    logging.getLogger("pyxi.companion.browsersetup").setLevel(logging.INFO)  # why a page didn't open
    SHOWN = options.shown
    sys.exit(asyncio.run(main(options)))
