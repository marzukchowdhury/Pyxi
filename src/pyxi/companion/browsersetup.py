"""Getting Pyxi's browser extension into the user's browser, without a store.

What browsers allow (checked against their documentation, November 2026):

* Firefox installs an add-on from anywhere once Mozilla has signed it, listed or not. The
  signed file is published with Pyxi's releases (extension/sign_firefox.py, scripts/release.py)
  and "Add to Firefox" opens it in Firefox, which asks the user to confirm. It then stays
  installed and updates itself. Until a signed file is published there is nothing to link to,
  and the user gets the by-hand steps instead (a temporary add-on, gone when Firefox closes).
* Chrome on Windows only installs from its store unless the PC is managed, and Edge follows
  it. The one store-free way is "Load unpacked" with Developer mode on. So Pyxi does the fiddly
  parts: it keeps the extension in a folder whose path never changes (an unpacked extension's
  id comes from its path), opens the browser's extensions page, puts the folder's address on
  the clipboard, and points at each control with its on-screen guide, waiting for each real
  click. (The page can't be opened by starting the browser with its address: Chrome and Edge
  drop chrome:// addresses given on the command line and open a new tab instead. So Pyxi opens
  a new window of the browser and types the address into it, and if that doesn't work the
  guide's first step is the address bar.) The extension stays loaded across restarts for as
  long as Developer mode stays on
  (Chrome 134 and later switch it off when Developer mode is switched off, and on again when
  it is switched back on). Edge asks "Turn off developer mode?" each time it starts, with the
  buttons "Turn off" and "Not now". The user is told so, and to press Not now.

Checked in real browsers by scripts/browser_setup_check.py (Chromium 156, Edge 157).

Also here: the offer. When the user asks for something that would be done in their own tab
and no browser is connected, Pyxi says one line and shows a card, once. "Don't" is remembered
and it isn't offered again unless they ask.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from otto.llm.base import ToolSpec

if TYPE_CHECKING:
    from pyxi.companion.brain import Brain

log = logging.getLogger(__name__)

BUNDLE_ENV = "PYXI_BROWSER_EXTENSION_DIR"  # where the shipped builds are, if not beside Python
HOME_ENV = "PYXI_BROWSER_EXTENSION_HOME"  # where the copies with a fixed path go
STATE = "browser_setup.json"  # in the data folder: whether the offer was turned down

# The signed Firefox add-on, as extension/manifest.firefox.json and sign_firefox.py name it.
ADDON_ID = "pyxi-in-your-browser@pyxi.local"
UPDATES_URL = (
    "https://github.com/marzukchowdhury/Pyxi/releases/latest/download/pyxi-firefox-updates.json"
)
XPI_HOME = "https://github.com/marzukchowdhury/Pyxi/releases/"
ADDON_CACHE_S = 600.0

LOAD_WAIT_S = 420.0  # how long the "Load unpacked" steps may take before Pyxi stops waiting
PAIR_WAIT_S = 300.0  # a pairing code lasts five minutes

def dev_notice(browser: Browser) -> str:
    """The one honest line about loading an extension this way."""
    if browser.key == "edge":  # read from Edge 157 itself: the question and its two buttons
        return (
            'Each time Edge starts it asks "Turn off developer mode?". Press Not now: the '
            "extension only works while Developer mode is on."
        )
    return (
        f"Leave Developer mode on in {browser.name}: the extension only works while it is on. "
        f"{browser.name} may mention developer mode extensions when it starts, which is "
        "expected for one that isn't from the store."
    )


class SetupError(Exception):
    """Setup can't go on; the message is for the user."""


@dataclass(frozen=True)
class Browser:
    key: str  # what Settings and the model call it: "chrome", "edge", "firefox"...
    name: str
    kind: str  # "chromium" (Load unpacked) or "firefox" (a signed add-on)
    page: str  # its extensions page: only opens from the browser's own address bar
    checked: bool  # the steps were checked in this browser; the others share its page


# By the program's file name. Brave, Vivaldi and Opera are Chromium with the same extensions
# page, so they get the same steps, but only Chrome and Edge were checked.
BROWSERS: dict[str, Browser] = {
    "chrome.exe": Browser("chrome", "Chrome", "chromium", "chrome://extensions", True),
    "msedge.exe": Browser("edge", "Edge", "chromium", "edge://extensions", True),
    "brave.exe": Browser("brave", "Brave", "chromium", "brave://extensions", False),
    "vivaldi.exe": Browser("vivaldi", "Vivaldi", "chromium", "vivaldi://extensions", False),
    "opera.exe": Browser("opera", "Opera", "chromium", "opera://extensions", False),
    "firefox.exe": Browser("firefox", "Firefox", "firefox", "about:addons", True),
}


def browser_of(app: str) -> Browser | None:
    """The browser a program is, by its file name or path ("C:\\...\\msedge.exe")."""
    return BROWSERS.get(Path(str(app or "").replace("\\", "/")).name.lower())


def front(key: str = "", open_windows: Any = None) -> tuple[Browser, str] | None:
    """The browser window nearest the front, and its program's path: (browser, exe). With
    ``key`` ("edge"), the nearest window of that browser."""
    if open_windows is None:
        from pyxi import windows

        open_windows = windows.open_windows
    for _hwnd, _title, exe in open_windows():
        browser = browser_of(exe)
        if browser is not None and (not key or browser.key == key.lower()):
            return browser, exe
    return None


def launch(exe: str, *args: str) -> None:
    """Start a browser, with an address or a switch."""
    subprocess.Popen([exe, *args], close_fds=True)


PAGE_WAIT_S = 8.0  # how long a new browser window may take to come up
PAGE_SETTLE_S = 1.0  # and how long after that before it takes typing
PAGE_CHANGE_S = 3.0  # how long the typed address may take to open


def show_page(browser: Browser, exe: str, *more: str) -> bool:
    """Open the browser's extensions page. The address is the browser's own (not a web link),
    and Chrome and Edge ignore it on the command line, so: a new window of the browser, then
    the address typed into that window's address bar. Typed only while that new window is the
    one in front, so nothing lands anywhere else. True once the window shows another page than
    the one it opened with (its title changes), False when that couldn't be done."""
    from pyxi import hands, windows

    def ours() -> dict[int, str]:
        return {h: title for h, title, e in windows.open_windows() if browser_of(e) is browser}

    def no(why: str) -> bool:
        log.info("couldn't open %s myself: %s", browser.page, why)
        return False

    try:
        before = set(ours())
        launch(exe, "--new-window", *more)
        end = time.monotonic() + PAGE_WAIT_S
        new: set[int] = set()
        while not new and time.monotonic() < end:
            time.sleep(0.2)
            new = set(ours()) - before
        if not new:
            return no("no new window came up")
        time.sleep(PAGE_SETTLE_S)  # the window is there before its address bar is ready
        hwnd = next((h for h in new if h == windows.foreground()), next(iter(new)))
        for _try in range(3):  # Windows doesn't always let a window be brought forward at once
            if windows.foreground() == hwnd or windows.focus(hwnd):
                break
            time.sleep(0.4)
        if windows.foreground() != hwnd:
            return no("the new window wouldn't come to the front")
        opened_with = ours().get(hwnd, "")
        keys = hands.Hands()
        for _attempt in range(2):  # a window that is still starting can drop the first keys
            for part in ("ctrl+l", *browser.page):  # looked at before every key
                if windows.foreground() != hwnd:
                    return no("another window came to the front")  # stop typing
                if part == "ctrl+l":
                    keys.press(part)  # the address bar, whatever had the keyboard in the window
                    time.sleep(0.25)
                else:
                    keys.type(part)
            if windows.foreground() != hwnd:
                return no("another window came to the front")
            keys.press("delete")  # anything the address bar suggested after what was typed
            keys.press("enter")
            end = time.monotonic() + PAGE_CHANGE_S
            while time.monotonic() < end:
                time.sleep(0.05)
                if ours().get(hwnd, opened_with) != opened_with:
                    return True
        return no("the window stayed on the page it opened with")
    except Exception as exc:  # no keyboard to use, Esc held, a window of Pyxi's in front
        return no(type(exc).__name__)


# ---------------------------------------------------------------------- the folder that keeps its path
def home() -> Path:
    given = os.environ.get(HOME_ENV, "").strip()
    if given:
        return Path(given)
    return Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Pyxi"


def folder(kind: str = "chromium") -> Path:
    """Where the extension lives for the browser to load: the same path whatever version of
    Pyxi is installed, because Chrome and Edge make an unpacked extension's id from it."""
    return home() / ("browser-extension-firefox" if kind == "firefox" else "browser-extension")


def _target(kind: str) -> str:
    return "firefox" if kind == "firefox" else "chrome"


def bundled(kind: str) -> Path | None:
    """The build of the extension that came with this Pyxi (resources/browser-extension)."""
    roots = [os.environ.get(BUNDLE_ENV, "").strip(), str(Path(sys.executable).resolve().parents[1] / "browser-extension")]
    for root in roots:
        if root and (Path(root) / _target(kind) / "manifest.json").is_file():
            return Path(root) / _target(kind)
    return None


def _repo_extension() -> Path | None:
    """Run from a checkout: the extension's own folder, with its build script."""
    here = Path(__file__).resolve().parents[3] / "extension"
    return here if (here / "build.py").is_file() and (here / "manifest.json").is_file() else None


def sync(src: Path, dst: Path) -> int:
    """Make ``dst`` hold what ``src`` holds, in place: files that differ are rewritten, the
    folder itself stays where it is. Returns how many files were written. Files that are no
    longer shipped are left alone (nothing loads them)."""
    written = 0
    dst.mkdir(parents=True, exist_ok=True)
    for path in sorted(src.rglob("*")):
        if not path.is_file():
            continue
        to = dst / path.relative_to(src)
        data = path.read_bytes()
        if not to.is_file() or to.read_bytes() != data:
            to.parent.mkdir(parents=True, exist_ok=True)
            to.write_bytes(data)
            written += 1
    return written


def ensure_folder(kind: str = "chromium") -> Path:
    """The folder for the browser to load, filled from this Pyxi's build of the extension."""
    dst = folder(kind)
    src = bundled(kind)
    if src is not None:
        sync(src, dst)
        return dst
    repo = _repo_extension()
    if repo is None:
        raise SetupError("This copy of Pyxi doesn't carry the browser extension.")
    import importlib.util

    spec = importlib.util.spec_from_file_location("pyxi_extension_build", repo / "build.py")
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    with tempfile.TemporaryDirectory(prefix="pyxi-extension-") as tmp:
        sync(module.build(_target(kind), Path(tmp) / _target(kind)), dst)
    return dst


def refresh() -> None:
    """After an update of Pyxi: bring the folders the browser already loads up to date, in
    place. Folders that were never made are not made here."""
    for kind in ("chromium", "firefox"):
        try:
            if (folder(kind) / "manifest.json").is_file():
                ensure_folder(kind)
        except Exception as exc:
            log.warning("couldn't bring the browser extension folder up to date: %s", exc)


# ---------------------------------------------------------------------- Firefox: is a signed add-on published?
_addon: tuple[float, str] | None = None  # (when it was looked up, its address or "")


async def firefox_addon(client: Any = None, *, again: bool = False) -> str:
    """The address of the signed add-on, if one is published with Pyxi's releases, else "".
    Read from the add-on's own update file, so a link is only ever offered when it works."""
    global _addon
    if _addon is not None and not again and time.monotonic() - _addon[0] < ADDON_CACHE_S:
        return _addon[1]
    import httpx

    link = ""
    try:
        if client is None:
            async with httpx.AsyncClient(timeout=8, follow_redirects=True) as own:
                r = await own.get(UPDATES_URL)
        else:
            r = await client.get(UPDATES_URL)
        if r.status_code == 200:
            newest = r.json()["addons"][ADDON_ID]["updates"][-1]
            found = str(newest["update_link"])
            if found.startswith(XPI_HOME) and found.endswith(".xpi"):
                link = found
    except Exception as exc:  # offline, no release with it yet, a file that isn't one
        log.info("no signed Firefox add-on to offer (%s)", type(exc).__name__)
    _addon = (time.monotonic(), link)
    return link


def addon_known() -> bool | None:
    """True or False once it has been looked up, None before."""
    return None if _addon is None else bool(_addon[1])


# ---------------------------------------------------------------------- what Settings shows
def steps(browser: Browser, addon: bool = False) -> list[str]:
    """The same thing by hand, in words."""
    pair = (
        f"Click the Pyxi icon in {browser.name} (under the puzzle piece), type the code Pyxi "
        "shows, and press Connect."
    )
    if browser.kind == "firefox" and addon:
        return ["Press Add to Firefox. Firefox asks if you want to add it: press Add.", pair]
    if browser.kind == "firefox":
        return [
            "Open about:debugging in Firefox and choose This Firefox.",
            f"Press Load Temporary Add-on and pick manifest.json in {folder('firefox')}",
            pair,
            "This way it stays until Firefox closes. A signed add-on that stays is on its way.",
        ]
    edge = browser.key == "edge"
    return [
        f"Type {browser.page} into {browser.name}'s address bar and press Enter.",
        "Turn on Developer mode"
        + (
            " (at the bottom of the menu on the left; in a narrow window, press the three "
            "lines at the top left first)."
            if edge
            else " (top right of the page)."
        ),
        "Press Load unpacked"
        + (" (in a narrow window it is the small folder button on the right)" if edge else "")
        + f" and choose this folder: {folder('chromium')}",
        pair,
    ]


def public(brain: Brain) -> dict[str, Any]:
    """For Settings, with every "browser" event: which browser would be set up, and how."""
    try:
        found = front()
    except Exception:
        found = None
    if found is None:
        return {"setup": {"browser": "", "key": "", "kind": "", "running": False}}
    browser, _ = found
    addon = addon_known() if browser.kind == "firefox" else None
    return {
        "setup": {
            "browser": browser.name,
            "key": browser.key,
            "kind": browser.kind,
            "checked": browser.checked,
            "addon": addon,  # Firefox: is "Add to Firefox" a real link? (None: not known yet)
            "notice": dev_notice(browser) if browser.kind == "chromium" else "",
            "steps": steps(browser, bool(addon)),
            "running": bool(getattr(brain, "_browser_setup", False)),
        }
    }


async def announce(brain: Brain, **extra: Any) -> None:
    await brain.hub.emit("browser", **brain.browser.public(), **public(brain), **extra)


async def look_up_addon(brain: Brain) -> None:
    """Settings is open with Firefox in front: find out whether there is an add-on to link to."""
    found = front()
    if found is not None and found[0].kind == "firefox" and addon_known() is None:
        await firefox_addon()
        await announce(brain)


def attach(brain: Brain) -> None:
    """When the brain starts: an updated Pyxi brings the loaded extension's folder up to date."""
    brain._browser_setup = False
    threading.Thread(target=refresh, name="pyxi-extension-refresh", daemon=True).start()


# ---------------------------------------------------------------------- the guided setup
_OPENED = "its extensions page is open in front of me."
_NOT_OPENED = (
    "its extensions page is not open yet. First have me click the address bar of {name}, type "
    "{page} and press Enter (a guide step with action 'type' and text '{page}, then Enter')."
)
# Where the two controls are, as seen in the browsers themselves (browser_setup_check.py).
_WHERE_CHROME = "It is at the top right of the page."
_WHERE_EDGE = (
    "In Edge it is at the bottom of the menu on the left of the page. In a narrow window that "
    "menu is folded away: then first have me click the button with three lines at the top "
    "left of the page, and the switch after it."
)
_LOAD_EDGE = (
    "In a narrow Edge window it has no words: it is the small button with a folder on it, the "
    "middle one of three at the right, level with 'Developer options'. "
)
_LOAD = (
    "(Set up Pyxi's browser extension in {name}: {start} "
    "Guide me through it one step at a time with the guide tool, waiting for me to do each one. "
    "1. Turn on the 'Developer mode' switch; skip this step if it is already on. {where} "
    "2. Click 'Load unpacked'. {load}3. A folder window opens: the folder's address is already "
    "on my clipboard, so have me paste it into the 'Folder:' box at the bottom and press "
    "'Select Folder' (a guide step with action 'type' and text 'Ctrl+V, then Select Folder'). "
    "It is done when a card named 'Pyxi in your browser' is on "
    "the extensions page: then call guide with done=true. Say at most one short sentence per "
    "step, and don't explain what the extension is.)"
)
_ADD = (
    "(Firefox is asking whether to add 'Pyxi in your browser'. Guide me with the guide tool: "
    "point at the 'Add' button in Firefox's question, then at 'OK' if it shows one. When the "
    "add-on is added, call guide with done=true. One short sentence per step.)"
)
_PAIR = (
    "(Now pair the extension with you. Guide me with the guide tool, one step at a time: "
    "1. click the Extensions button in {name}'s toolbar (the puzzle piece); 2. click 'Pyxi in "
    "your browser' in the list; 3. type this code into the box (a guide step with action "
    "'type' and text '{code}'); 4. press Connect. When the little window says Connected, call "
    "guide with done=true. One short sentence per step.)"
)


async def _guide(brain: Brain, text: str, shown: str, timeout: float) -> bool:
    """One stretch of the on-screen guide: the model points, the user clicks, until it says
    done (or the browser connects, or time runs out). False if nothing was ever pointed at."""
    await brain.turn(text, shown=shown, synthetic=True)
    pointed = brain._guide is not None
    end = time.monotonic() + timeout
    while time.monotonic() < end and not brain.browser.connected:
        if brain._guide is None and not brain._lock.locked():
            await asyncio.sleep(0.3)  # the click's own turn may be about to start
            if brain._guide is None and not brain._lock.locked():
                break
        pointed = pointed or brain._guide is not None
        await asyncio.sleep(0.3)
    return pointed


async def _end_guide(brain: Brain, finished: bool) -> None:
    if brain._guide is not None:
        brain._guide = None
        await brain.hub.emit("guide", active=False, finished=finished, at=None)


async def setup(brain: Brain, key: str = "") -> None:
    """ "Set it up for me": from Settings, from the offer's card, or when they ask."""
    if getattr(brain, "_browser_setup", False):
        return
    brain._browser_setup = True
    try:
        await _setup(brain, key)
    except SetupError as exc:
        await brain.hub.emit("notice", message=str(exc))
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("browser setup failed")
        await brain.hub.emit(
            "notice",
            message="Setting up the browser extension hit a snag. The steps are in Settings, "
            "under Connect your browser.",
        )
    finally:
        brain._browser_setup = False
        await _end_guide(brain, finished=brain.browser.connected)
        await announce(brain)


async def _setup(brain: Brain, key: str) -> None:
    link = brain.browser
    if link.connected:
        await brain.say_line("Your browser is already connected.")
        return
    found = front(key)
    if found is None:
        raise SetupError(
            f"Open {key.capitalize() if key else 'Chrome, Edge or Firefox'} first, then ask me again."
        )
    browser, exe = found
    _remember(brain, declined=0, ignored=0)  # they want it now: the offer may come back later
    await announce(brain)
    if browser.kind == "firefox":
        await _firefox(brain, browser, exe)
    else:
        await _chromium(brain, browser, exe)
    if link.connected:
        return
    await _pair(brain, browser)


async def _chromium(brain: Brain, browser: Browser, exe: str) -> None:
    where = await asyncio.to_thread(ensure_folder, "chromium")
    await brain.hub.emit("action", kind="copy", value=str(where))  # one paste into the folder box
    opened = await asyncio.to_thread(show_page, browser, exe)
    await brain.hub.emit("notice", message=dev_notice(browser))
    await brain.say_line(
        f"I've opened {browser.name}'s extensions page and copied the folder's address. "
        "Follow the pointer: it's three clicks."
        if opened
        else f"I've copied the folder's address. First open {browser.name}'s extensions page: "
        "follow the pointer."
    )
    await asyncio.sleep(1.5)  # let the page come up before it's looked at
    start = _OPENED if opened else _NOT_OPENED.format(name=browser.name, page=browser.page)
    edge = browser.key == "edge"
    pointed = await _guide(
        brain,
        _LOAD.format(
            name=browser.name,
            start=start,
            where=_WHERE_EDGE if edge else _WHERE_CHROME,
            load=_LOAD_EDGE if edge else "",
        ),
        "Setting up the browser extension",
        LOAD_WAIT_S,
    )
    if not pointed:
        await brain.hub.emit(
            "notice",
            message="I couldn't point at the steps, so here they are: "
            + " ".join(f"{n}. {s}" for n, s in enumerate(steps(browser)[:3], 1)),
        )


async def _firefox(brain: Brain, browser: Browser, exe: str) -> None:
    addon = await firefox_addon(again=True)
    if addon:
        await asyncio.to_thread(launch, exe, addon)
        await brain.say_line("Firefox is asking whether to add Pyxi. Press Add.")
        await asyncio.sleep(1.5)
        await _guide(brain, _ADD, "Adding Pyxi to Firefox", LOAD_WAIT_S)
        return
    # Nothing signed is published yet: no dead link, the by-hand way instead.
    where = await asyncio.to_thread(ensure_folder, "firefox")
    await brain.hub.emit("action", kind="copy", value=str(where / "manifest.json"))
    await asyncio.to_thread(launch, exe, "about:debugging#/runtime/this-firefox")
    await brain.hub.emit(
        "notice",
        message="The Firefox add-on that stays installed isn't published yet. For now: press "
        "Load Temporary Add-on on the page I opened and paste the address I copied. It stays "
        "until Firefox closes.",
    )
    await brain.say_line(
        "The add-on that stays isn't out yet, so this one lasts until Firefox closes. "
        "Press Load Temporary Add-on and paste what I copied."
    )


async def _pair(brain: Brain, browser: Browser) -> None:
    link = brain.browser
    code = link.new_code()["code"]
    await announce(brain, code=code, expires_in=int(PAIR_WAIT_S))  # Settings shows it too
    end = time.monotonic() + PAIR_WAIT_S  # the code is good that long, pointer or no pointer
    await _guide(
        brain, _PAIR.format(name=browser.name, code=code), "Pairing the browser", PAIR_WAIT_S
    )
    while not link.connected and time.monotonic() < end:
        await asyncio.sleep(0.3)
    if link.connected:
        await _end_guide(brain, finished=True)
        await brain.say_line(f"{browser.name} is connected. Ask me to do something in a tab.")
    else:
        await brain.hub.emit(
            "notice",
            message="The pairing code ran out. Press Connect a browser in Settings for a new "
            "one, and type it into the Pyxi icon in your browser.",
        )


# ---------------------------------------------------------------------- when they ask
CONNECT = ToolSpec(
    "connect_browser",
    "Set up Pyxi's browser extension in the browser they have open (Chrome, Edge or Firefox), "
    "so that Pyxi can read and work in their own tabs without their mouse: Pyxi opens the "
    "right page, points at each step and pairs. Only when they ask for it ('connect my "
    "browser', 'set up the extension', 'yes, set that up'). Say one short line.",
    {
        "type": "object",
        "properties": {
            "browser": {
                "type": "string",
                "description": "chrome, edge or firefox, if they said which.",
            }
        },
        "required": [],
    },
)


async def connect(brain: Brain, args: dict[str, Any]) -> str | None:
    if brain.browser.connected:
        return "connect_browser: their browser is already connected. Tell them so."
    if getattr(brain, "_browser_setup", False):
        return "connect_browser: that's already under way. Tell them to follow the pointer."
    key = str(args.get("browser") or "").strip().lower()
    key = {"microsoft edge": "edge", "google chrome": "chrome", "msedge": "edge"}.get(key, key)
    if front(key if key in {b.key for b in BROWSERS.values()} else "") is None:
        return (
            "connect_browser: no browser Pyxi can set up is open. Tell them to open Chrome, "
            "Edge or Firefox and ask again."
        )
    brain._spawn(setup(brain, key if key in {b.key for b in BROWSERS.values()} else ""))
    return None


# ---------------------------------------------------------------------- the offer
OFFERS = {"use_computer", "delegate"}  # the routes that stand in for the user's own tab today
# Their words say it is about the tab or the account in front of them.
_OWN_TAB = re.compile(
    r"\b(this|that|the|my|current|open) (tab|page|site|website)\b|\bin my (browser|tab)\b|"
    r"\b(signed|logged) in\b|\bmy (account|cart|basket|orders?|inbox|profile|dashboard|feed)\b",
    re.I,
)
# A mouse-and-keyboard task that isn't about the page, though a browser is in front.
_ELSEWHERE = re.compile(
    r"\b(taskbar|start menu|file explorer|explorer|settings app|notepad|spotify|desktop|"
    r"folder|another app|other app|new window|control panel)\b",
    re.I,
)


def _state(brain: Brain) -> dict[str, Any]:
    try:
        data = json.loads((brain.settings.data_dir / STATE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    return data if isinstance(data, dict) else {}


def _remember(brain: Brain, **changes: Any) -> None:
    data = {**_state(brain), **changes}
    path = brain.settings.data_dir / STATE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def wants_own_tab(brain: Brain, name: str, args: dict[str, Any]) -> bool:
    """Is this one of the requests the extension exists for: something in the page they have
    open, or that needs them signed in?"""
    ctx = getattr(brain, "_ctx", None)
    in_front = browser_of(ctx.window.process) if ctx is not None and ctx.window else None
    if name == "use_computer":
        if in_front is None:
            return False  # the mouse is headed for some other app
        target = str(args.get("app") or "").lower()
        if target and in_front.name.lower() not in target and "browser" not in target:
            return False
        return not _ELSEWHERE.search(str(args.get("task") or ""))
    if name == "delegate":
        agent = brain.registry.get(str(args.get("agent") or "").strip().lower())
        if agent is None or agent.role != "browser":
            return False
        return bool(_OWN_TAB.search(str(getattr(brain, "_user_text", "") or "")))
    return False


async def offer(brain: Brain, name: str, args: dict[str, Any]) -> str | None:
    """Before the mouse (or Scout) does what the user's own tab would do better: say so once
    and offer to set the extension up. Returns a note that stops the tool when they say yes;
    None to carry on as always."""
    link = getattr(brain, "browser", None)
    if link is None or link.connected or getattr(brain, "_browser_setup", False):
        return None
    if getattr(brain, "_setup_offered", False) or not wants_own_tab(brain, name, args):
        return None
    state = _state(brain)
    if state.get("declined") or int(state.get("ignored") or 0) >= 2:
        return None  # they said no (or looked away twice): only if they ask
    try:
        found = front()
    except Exception:
        found = None
    if found is None:
        return None
    browser, _ = found
    if browser.kind == "firefox" and not addon_known():
        # Nothing signed is published (or it isn't known yet: find out for next time). What
        # there is for Firefox until then is gone when Firefox closes, so it isn't offered
        # unasked. Settings and "connect my browser" still do it.
        if addon_known() is None:
            brain._spawn(firefox_addon())
        return None
    brain._setup_offered = True  # once while Pyxi runs, whatever the answer
    brain._speak(
        f"I could do that inside your {browser.name} tab, without taking your mouse, if your "
        "browser were connected."
    )
    how = (
        "Firefox asks you to add it, then you type a code."
        if browser.kind == "firefox"
        else "Three clicks and a code. " + dev_notice(browser)
    )
    ok = await brain.hub.request(
        "confirm",
        timeout=45,
        job_id="",
        agent="Pyxi",
        action=f"Set up Pyxi in {browser.name}, so it can work in your own tabs?",
        reason=f"It takes about a minute: Pyxi opens the page and points at each step. {how}\n"
        "Choose Don't and Pyxi carries on the usual way, and won't ask again.",
    )
    if ok:
        brain._spawn(setup(brain, browser.key))
        return (
            f"{name}: they chose to connect their browser first, and Pyxi is walking them "
            "through that now. Don't start this task and don't call another tool: say at most "
            "one short line, that they can ask again once it's connected."
        )
    if ok is None:
        _remember(brain, ignored=int(state.get("ignored") or 0) + 1)
    else:
        _remember(brain, declined=time.time())
    return None
