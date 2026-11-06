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
}


def safe_url(value: str) -> str | None:
    value = value.strip()
    if not re.match(r"^https?://", value, re.I):
        if re.match(r"^[\w-]+(\.[\w-]+)+(/.*)?$", value):
            value = "https://" + value
        else:
            return None
    parsed = urlparse(value)
    return value if parsed.scheme in ("http", "https") and parsed.netloc else None


def app_target(name: str) -> str | None:
    key = re.sub(r"\s+", " ", name.lower().strip()).removesuffix(".exe")
    return APPS.get(key)


async def open_app(name: str) -> str:
    target = app_target(name)
    if target is None:
        known = ", ".join(sorted({k for k in APPS if " " not in k})[:14])
        return f"open_app: I can only open apps I know, like {known}."
    try:
        await asyncio.to_thread(os.startfile, target)  # type: ignore[attr-defined]
    except (OSError, AttributeError) as exc:
        return f"open_app: Windows couldn't open {name} ({exc})."
    return f"Opened {name}."
