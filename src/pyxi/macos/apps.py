"""The Mac side of quick actions: which apps "open Notes" can mean, and starting them.

A target is one of:
* an app's name ("Safari"), started with ``open -a``;
* a link with a scheme ("x-apple.systempreferences:...", "spotify:"), started with ``open``;
* a folder ("~/Downloads"), shown in Finder with ``open``.

Settings links only open a System Settings page for the user to look at. Pyxi never changes a
setting itself.
"""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
from pathlib import Path

__all__ = ["APPS", "app_target", "open_app", "start"]

_SETTINGS = "x-apple.systempreferences:"
_PRIVACY = _SETTINGS + "com.apple.preference.security?"

APPS: dict[str, str] = {
    # Apple's own
    "safari": "Safari",
    "notes": "Notes",
    "calendar": "Calendar",
    "finder": "Finder",
    "file explorer": "Finder",
    "explorer": "Finder",
    "files": "Finder",
    "music": "Music",
    "apple music": "Music",
    "mail": "Mail",
    "messages": "Messages",
    "facetime": "FaceTime",
    "maps": "Maps",
    "photos": "Photos",
    "preview": "Preview",
    "reminders": "Reminders",
    "contacts": "Contacts",
    "podcasts": "Podcasts",
    "tv": "TV",
    "books": "Books",
    "weather": "Weather",
    "clock": "Clock",
    "textedit": "TextEdit",
    "notepad": "TextEdit",  # what Windows people mean
    "calculator": "Calculator",
    "calc": "Calculator",
    "terminal": "Terminal",
    "command prompt": "Terminal",
    "activity monitor": "Activity Monitor",
    "task manager": "Activity Monitor",
    "app store": "App Store",
    "microsoft store": "App Store",
    "camera": "Photo Booth",
    "photo booth": "Photo Booth",
    "screenshot": "Screenshot",
    "snipping tool": "Screenshot",
    "keynote": "Keynote",
    "pages": "Pages",
    "numbers": "Numbers",
    # Everyone else's
    "chrome": "Google Chrome",
    "google chrome": "Google Chrome",
    "firefox": "Firefox",
    "edge": "Microsoft Edge",
    "brave": "Brave Browser",
    "arc": "Arc",
    "word": "Microsoft Word",
    "excel": "Microsoft Excel",
    "powerpoint": "Microsoft PowerPoint",
    "outlook": "Microsoft Outlook",
    "teams": "Microsoft Teams",
    "spotify": "spotify:",
    "discord": "Discord",
    "slack": "Slack",
    "zoom": "zoom.us",
    "whatsapp": "WhatsApp",
    "steam": "Steam",
    "vs code": "Visual Studio Code",
    "vscode": "Visual Studio Code",
    "visual studio code": "Visual Studio Code",
    "code": "Visual Studio Code",
    # System Settings pages (opened for the user to look at; never changed by Pyxi)
    "settings": _SETTINGS,
    "system settings": _SETTINGS,
    "system preferences": _SETTINGS,
    "control panel": _SETTINGS,
    "sound settings": _SETTINGS + "com.apple.Sound-Settings.extension",
    "display settings": _SETTINGS + "com.apple.Displays-Settings.extension",
    "bluetooth settings": _SETTINGS + "com.apple.BluetoothSettings",
    "wifi settings": _SETTINGS + "com.apple.wifi-settings-extension",
    "microphone privacy": _PRIVACY + "Privacy_Microphone",
    "camera privacy": _PRIVACY + "Privacy_Camera",
    "screen recording privacy": _PRIVACY + "Privacy_ScreenCapture",
    "accessibility privacy": _PRIVACY + "Privacy_Accessibility",
    "software update": _SETTINGS + "com.apple.Software-Update-Settings.extension",
    "windows update": _SETTINGS + "com.apple.Software-Update-Settings.extension",
    "apps": "/Applications",
    "applications": "/Applications",
    # Their own folders, shown in Finder ("open my Downloads folder").
    "downloads": "~/Downloads",
    "documents": "~/Documents",
    "desktop": "~/Desktop",
    "pictures": "~/Pictures",
    "videos": "~/Movies",
    "movies": "~/Movies",
    "trash": "~/.Trash",
    "recycle bin": "~/.Trash",
    "home": "~",
}
_FOLDERS = {"music": "~/Music"}  # "my music folder" is the folder, "music" alone the app


def app_target(name: str) -> str | None:
    raw = re.sub(r"\s+", " ", name.lower().strip())
    key = raw.removesuffix(".app").removesuffix(".exe")
    key = key.removeprefix("my ").removeprefix("the ").removesuffix(" folder").removesuffix(" app")
    if raw.endswith(" folder") and key in _FOLDERS:
        return _FOLDERS[key]
    return APPS.get(key)


def _kind(target: str) -> str:
    if target.startswith(("/", "~")):
        return "path"
    if re.match(r"^[a-z][a-z0-9.+-]*:", target, re.I):
        return "link"
    return "app"


def command(target: str) -> list[str]:
    """The ``open`` command for a target (see the module docstring)."""
    kind = _kind(target)
    if kind == "path":
        return ["open", os.path.expanduser(target)]
    if kind == "link":
        return ["open", target]
    return ["open", "-a", target]


def start(target: str) -> None:
    """Open a target without waiting for it. Raises OSError when macOS says it can't."""
    result = subprocess.run(command(target), capture_output=True, text=True, timeout=15)
    if result.returncode != 0:
        raise OSError((result.stderr or result.stdout or "open failed").strip()[:200])


def spotify_installed() -> bool:
    return any(
        Path(p).is_dir()
        for p in ("/Applications/Spotify.app", os.path.expanduser("~/Applications/Spotify.app"))
    )


async def open_app(name: str) -> str:
    target = app_target(name)
    if target == "spotify:":
        if not spotify_installed():
            import webbrowser

            await asyncio.to_thread(webbrowser.open, "https://open.spotify.com", 2)
            return "Opened Spotify in the browser (the app isn't installed)."
        target = "Spotify"
    if target is None:
        known = ", ".join(sorted({k for k in APPS if " " not in k})[:14])
        return f"open_app: I can only open apps I know, like {known}."
    try:
        await asyncio.to_thread(start, target)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"open_app: macOS couldn't open {name} ({exc})."
    return f"Opened {name}."
