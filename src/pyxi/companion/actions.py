"""Quick actions: small, safe things Pyxi can do for you on the spot.

* open a website (http/https only) or one of a known set of apps,
* copy exact text to the clipboard,
* drop a drafted message into the text field you're typing in (clipboard + paste, then your
  old clipboard comes back), but only if you're still in the same app you asked from.

Anything bigger (sending, buying, deleting) goes through a teammate with an approval card.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import socket
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

# Friendly names -> what Windows can start. Settings pages use their ms-settings: URIs.
APPS: dict[str, str] = {
    "notepad": "notepad.exe",
    "calculator": "calc.exe",
    "calc": "calc.exe",
    "paint": "mspaint.exe",
    "file explorer": "explorer.exe",
    "explorer": "explorer.exe",
    "files": "explorer.exe",
    "terminal": "wt.exe",
    "command prompt": "cmd.exe",
    "task manager": "taskmgr.exe",
    "snipping tool": "ms-screenclip:",
    "edge": "msedge.exe",
    "chrome": "chrome.exe",
    "firefox": "firefox.exe",
    "word": "winword.exe",
    "excel": "excel.exe",
    "powerpoint": "powerpnt.exe",
    "outlook": "outlook.exe",
    "spotify": "spotify:",
    "discord": "discord:",
    "settings": "ms-settings:",
    "sound settings": "ms-settings:sound",
    "display settings": "ms-settings:display",
    "bluetooth settings": "ms-settings:bluetooth",
    "wifi settings": "ms-settings:network-wifi",
    "microphone privacy": "ms-settings:privacy-microphone",
    "camera privacy": "ms-settings:privacy-webcam",
    "windows update": "ms-settings:windowsupdate",
    "apps": "ms-settings:appsfeatures",
    "control panel": "control.exe",
    "clock": "ms-clock:",
    "camera": "microsoft.windows.camera:",
    "photos": "ms-photos:",
    "microsoft store": "ms-windows-store:",
    "steam": "steam:",
    # Their own folders, opened in File Explorer ("open my Downloads folder").
    "downloads": "shell:Downloads",
    "documents": "shell:Personal",
    "desktop": "shell:Desktop",
    "pictures": "shell:My Pictures",
    "music": "shell:My Music",
    "videos": "shell:My Video",
    "recycle bin": "shell:RecycleBinFolder",
}


def safe_url(value: str) -> str | None:
    value = value.strip()
    if not re.match(r"^https?://", value, re.I):
        if re.match(r"^[\w-]+(\.[\w-]+)+(/.*)?$", value):
            value = "https://" + value
        else:
            return None
    parsed = urlparse(value)
    if "\\" in value or "@" in parsed.netloc:
        # Written so that this code and a browser read different hosts out of it
        # (a backslash before an @), or with a "user@" in front of the real one.
        return None
    return value if parsed.scheme in ("http", "https") and parsed.netloc else None


# ---------------------------------------------------------------------- is there such a site
_KNOWN_SITE = "www.google.com"  # resolves whenever the internet is there at all
_NO_SUCH_NAME = {11001, getattr(socket, "EAI_NONAME", 11001)}  # Windows' and the standard code
_STUCK_WORDS = ("open", "goto", "visit", "on", "to", "in", "at", "go")


async def _resolves(host: str) -> bool | None:
    """Does this name have an address: yes, no, or can't tell (slow, or some other trouble)."""
    try:
        await asyncio.wait_for(asyncio.get_running_loop().getaddrinfo(host, 443), 2.5)
        return True
    except socket.gaierror as exc:
        # Only "there is no such name" is a no. A lookup that failed for now, a name with no
        # address of that kind, or one this PC's DNS won't answer (the browser's own secure
        # DNS, a VPN or a proxy may still reach it) can't be told, and the address is opened.
        return False if exc.errno in _NO_SUCH_NAME else None
    except (TimeoutError, OSError, UnicodeError):  # (a label too long to be a name at all)
        return None


# Names everyone knows. A name one doubled letter away from one of these ("amaazon",
# "gooogle") is that site misheard, never the look-alike address someone has registered.
_WELL_KNOWN = frozenset(
    ["amazon", "google", "youtube", "facebook", "instagram", "linkedin", "netflix", "reddit", "github", "twitter", "wikipedia", "ebay", "walmart", "bestbuy", "spotify", "whatsapp", "microsoft", "apple", "paypal", "gmail", "yahoo", "bing", "tiktok", "pinterest", "etsy", "ikea", "costco", "airbnb", "booking", "expedia", "stockx", "kijiji", "craigslist", "twitch", "discord", "zoom", "dropbox", "notion", "canva", "openai", "chatgpt", "claude", "anthropic", "outlook", "office", "adobe", "samsung", "nike", "adidas", "target", "homedepot", "lowes", "aliexpress", "temu", "shein", "uber", "lyft", "doordash"]
)


def _spellings(name: str) -> list[str]:
    """A name as it is, then with each run of a repeated letter one shorter ("amaazon" is
    amazon, "gooogle" is google), then with every such run single."""
    out = [name]
    for run in re.finditer(r"(.)\1+", name):
        out.append(name[: run.start()] + run.group(0)[1:] + name[run.end() :])
    out.append(re.sub(r"(.)\1+", r"\1", name))
    return list(dict.fromkeys(out))


def _as_well_known(host: str) -> str:
    """The host with its name put right, when the name as heard is not one everyone knows and
    one of its other spellings is; else the host as it was."""
    www = "www." if host.startswith("www.") else ""
    name, _, ending = host.removeprefix("www.").partition(".")
    if not ending or name in _WELL_KNOWN:
        return host
    known = next((n for n in _spellings(name) if n in _WELL_KNOWN), "")
    return f"{www}{known}.{ending}" if known else host


async def real_site(url: str) -> str | None:
    """The address to open, or None when there is no such site.

    Live: "on techlandbd.com, find earphones" was written down as "ontechlandbd.com", Pyxi
    opened that, and the browser said "Server Not Found". An address whose name does not exist
    is not opened: a small word stuck to its front is taken off when that makes a real site,
    and otherwise the caller says there is no such site. Offline, or when the check is slow,
    the address is opened as it is (nothing can be told then).
    """
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if not host or "." not in host or host == "localhost" or re.fullmatch(r"[\d.:]+", host):
        return url
    # "amaazon.ca" exists, and it is not Amazon: a well-known name with a letter doubled is
    # put right before anything is looked up, so a mishearing never lands on a look-alike.
    fixed = _as_well_known(host)
    if fixed != host:
        url, host = url.replace(host, fixed, 1), fixed
    if await _resolves(host) is not False:
        return url
    if await _resolves(_KNOWN_SITE) is not True:
        return url  # offline: every name fails, and that says nothing about this one
    bare = host.removeprefix("www.")
    for word in _STUCK_WORDS:
        rest = bare.removeprefix(word)
        if rest != bare and len(rest.split(".")[0]) >= 4 and await _resolves(rest) is True:
            return url.replace(host, rest, 1)
    return None


# Endings as they are misheard, and what was said.
_HEARD_ENDINGS = {"ka": "ca", "kom": "com", "con": "com", "cam": "com", "come": "com", "calm": "com", "nat": "net", "orc": "org"}


async def near_site(url: str) -> str | None:
    """A real address one mishearing away from one that does not exist, or None.

    Live: "Amazon.ca" was written down as "amaazon.ka". A letter said long comes out doubled,
    and ".ca" sounds like ".ka": the name is tried with its doubled letters single and its
    ending as it is usually said, and the first that exists is offered. It is only offered:
    the caller tells the model, which opens it when it is plainly the site they named."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").removeprefix("www.")
    name, _, ending = host.partition(".")
    if not name or not ending:
        return None
    # A spelling everyone knows first, then the name as it was heard, then its others.
    names = sorted(_spellings(name), key=lambda n: n not in _WELL_KNOWN)
    endings = [ending]
    last = ending.rsplit(".", 1)[-1]
    if last in _HEARD_ENDINGS:
        endings.insert(0, ending[: len(ending) - len(last)] + _HEARD_ENDINGS[last])
    for e in endings:
        for n in names:
            guess = f"{n}.{e}"
            if guess != host and await _resolves(guess) is True:
                return url.replace(parsed.hostname or host, guess, 1)
    return None


def app_target(name: str) -> str | None:
    key = re.sub(r"\s+", " ", name.lower().strip()).removesuffix(".exe")
    key = key.removeprefix("my ").removeprefix("the ").removesuffix(" folder").removesuffix(" app")
    return APPS.get(key)


# ---------------------------------------------------------------------- the apps on this PC
_INSTALLED: tuple[float, dict[str, tuple[str, str]]] = (0.0, {})
_INSTALLED_KEEPS_S = 300.0
_NOT_AN_APP = re.compile(r"(?i)uninstall|readme|release notes|help|manual|website|license|documentation")


def _plain(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def _start_apps() -> list[tuple[str, str]]:
    """What the Start menu lists: (name, what to start). Windows' own list, so it has classic
    programs and Store apps alike."""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "Get-StartApps | Select-Object Name, AppID | ConvertTo-Json -Compress"],
            capture_output=True, text=True, timeout=12, creationflags=0x08000000,
        ).stdout  # fmt: skip
        found = json.loads(out or "[]")
    except (OSError, ValueError, subprocess.SubprocessError):
        return []
    found = [found] if isinstance(found, dict) else found
    return [
        (str(a.get("Name") or ""), "shell:AppsFolder\\" + str(a.get("AppID") or ""))
        for a in found
        if isinstance(a, dict) and a.get("Name") and a.get("AppID")
    ]


def _desktop_shortcuts() -> list[tuple[str, str]]:
    """Shortcuts on the desktop, theirs and the shared one (a program can be there only)."""
    homes = [os.environ.get("USERPROFILE", ""), os.environ.get("OneDrive", ""), os.environ.get("PUBLIC", "")]
    out = []
    for home in filter(None, homes):
        folder = Path(home) / "Desktop"
        try:
            out += [(p.stem, str(p)) for p in folder.iterdir() if p.suffix.lower() in (".lnk", ".url", ".appref-ms")]
        except OSError:
            continue
    return out


def installed_apps() -> dict[str, tuple[str, str]]:
    """Every program this PC can start, by its name in plain letters: (its name, its target).
    Read from Windows at most every five minutes."""
    global _INSTALLED
    at, apps = _INSTALLED
    if apps and time.monotonic() - at < _INSTALLED_KEEPS_S:
        return apps
    apps = {}
    for name, target in [*_desktop_shortcuts(), *_start_apps()]:  # the Start menu's wins
        if not _NOT_AN_APP.search(name) and _plain(name):
            apps[_plain(name)] = (name, target)
    _INSTALLED = (time.monotonic(), apps)
    return apps


def installed_app(name: str) -> tuple[str, str] | None:
    """The installed program a spoken name means: "gen office" is GenOffice, "vs code" is
    Visual Studio Code. Its exact name first; else the only program whose name starts with
    it or holds it. None when nothing fits, or more than one does."""
    key = _plain(re.sub(r"(?i)^(my|the)\s+|\s+(app|program)$", "", name.strip()))
    if len(key) < 2:
        return None
    apps = installed_apps()
    if key in apps:
        return apps[key]
    # Most of the name, not a word of it: "office" is not Office Language Preferences.
    most = [(k, v) for k, v in apps.items() if len(key) * 2 >= len(k)]
    for fits in (
        [v for k, v in most if k.startswith(key)],
        [v for k, v in most if key in k and len(key) >= 4],
        [v for k, v in apps.items() if _initials(v[0]) == key and len(key) >= 2],
    ):
        if len({target for _, target in fits}) == 1:
            return fits[0]
    # Misheard by a letter or two ("Jen Office" for GenOffice): the one name that is nearly it.
    import difflib

    near = sorted(
        ((difflib.SequenceMatcher(None, key, k).ratio(), v) for k, v in apps.items() if abs(len(k) - len(key)) <= 2),
        key=lambda pair: pair[0],
        reverse=True,
    )
    if near and near[0][0] >= 0.8 and len(key) >= 5:
        if len(near) == 1 or near[1][0] < near[0][0] - 0.05 or near[1][1][1] == near[0][1][1]:
            return near[0][1]
    return None


def _initials(name: str) -> str:
    """'Visual Studio Code' -> 'vscode' (how people say it), else ''."""
    words = re.findall(r"[A-Za-z0-9]+", name)
    return (words[0][0] + words[1][0] + "".join(words[2:])).lower() if len(words) >= 3 else ""


async def open_app(name: str) -> str:
    target = app_target(name)
    if target == "spotify:":
        from pyxi import media

        if not media.spotify_installed():
            # Not installed: Windows would only offer to find an app. The web player is the app.
            import webbrowser

            await asyncio.to_thread(webbrowser.open, "https://open.spotify.com", 2)
            return "Opened Spotify in the browser (the app isn't installed)."
    if target is None:
        # Not one of the names above: any program in their Start menu or on their desktop.
        # (Live: "open GenOffice" was answered "GenOffice, coming up" and nothing opened.)
        found = await asyncio.to_thread(installed_app, name)
        if found is None:
            return (
                f"open_app: there's no program called {name!r} in the Start menu or on the "
                "desktop of this PC, so nothing was opened. Say so in one short line, and ask "
                "what it's called there if the name may have been misheard."
            )
        name, target = found
    try:
        await asyncio.to_thread(os.startfile, target)  # type: ignore[attr-defined]
    except (OSError, AttributeError) as exc:
        return f"open_app: Windows couldn't open {name} ({exc})."
    return f"Opened {name}."


_NEW_GOOGLE_FILE = {
    "sheets.new": "https://docs.google.com/spreadsheets/create",
    "docs.new": "https://docs.google.com/document/create",
    "slides.new": "https://docs.google.com/presentation/create",
    "forms.new": "https://docs.google.com/forms/create",
}
_GOOGLE_HOSTS = ("docs.google.com", "drive.google.com", "mail.google.com", "calendar.google.com")


def in_account(url: str, email: str | None) -> str:
    """Open a Google page in the account Pyxi is connected to, not whichever account the browser
    signs in first: a sheet made in another account is one Pyxi's Ledger can't edit."""
    if not email:
        return url
    parsed = urlparse(url)
    host = parsed.netloc.lower().removeprefix("www.")
    if host in _NEW_GOOGLE_FILE and parsed.path in ("", "/"):
        url, host = _NEW_GOOGLE_FILE[host], "docs.google.com"
        parsed = urlparse(url)
    if host not in _GOOGLE_HOSTS or "authuser=" in parsed.query:
        return url
    if host == "mail.google.com" and parsed.path in ("", "/"):
        url, parsed = "https://mail.google.com/mail/", urlparse("https://mail.google.com/mail/")
    from urllib.parse import quote

    sep = "&" if parsed.query else "?"
    base, _, frag = url.partition("#")
    return f"{base}{sep}authuser={quote(email)}" + (f"#{frag}" if frag else "")
