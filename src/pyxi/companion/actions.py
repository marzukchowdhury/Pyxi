"""Quick actions: small, safe things Pyxi can do for you on the spot.

* open a website (http/https only) or one of a known set of apps,
* copy exact text to the clipboard,
* drop a drafted message into the text field you're typing in (clipboard + paste, then your
  old clipboard comes back), but only if you're still in the same app you asked from.

Anything bigger (sending, buying, deleting) goes through a teammate with an approval card.
"""

from __future__ import annotations

import asyncio
import os
import re
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


def app_target(name: str) -> str | None:
    key = re.sub(r"\s+", " ", name.lower().strip()).removesuffix(".exe")
    key = key.removeprefix("my ").removeprefix("the ").removesuffix(" folder").removesuffix(" app")
    return APPS.get(key)


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
        known = ", ".join(sorted({k for k in APPS if " " not in k})[:14])
        return f"open_app: I can only open apps I know, like {known}."
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
