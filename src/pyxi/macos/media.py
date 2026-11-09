"""The Mac side of ``pyxi.media``: volume, Spotify and YouTube.

* Volume is the system output volume, through AppleScript (``set volume``), which needs no
  permission.
* Spotify is asked directly over AppleScript ("play track ..."), which also says whether it's
  playing. macOS asks the user once whether Pyxi may control Spotify.
* YouTube opens in the default browser, behind what they're doing. A Mac has no per-app sound
  meter Pyxi can read without extra system software, so "is it playing yet?" isn't checked: the
  song is reported as opened, and the play key is never pressed blind (that could pause it).
"""

from __future__ import annotations

import asyncio
import logging
import subprocess

from pyxi.macos.apps import spotify_installed

log = logging.getLogger(__name__)

__all__ = [
    "_default_browser",
    "browser_for",
    "is_sounding",
    "loudness",
    "play",
    "play_spotify",
    "player_exe",
    "set_volume",
    "spotify_installed",
    "volume",
]


def _osascript(script: str, timeout: float = 4.0) -> str:
    out = subprocess.run(
        ["osascript", "-e", script], capture_output=True, text=True, timeout=timeout
    )
    if out.returncode != 0:
        raise OSError((out.stderr or "osascript failed").strip()[:200])
    return out.stdout.strip()


def volume() -> float:
    """The Mac's output volume, 0 to 1."""
    try:
        return int(_osascript("output volume of (get volume settings)")) / 100
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0.5


def set_volume(value: str) -> str:
    from pyxi.media import parse_volume

    now = volume()
    level = parse_volume(value, now)
    if level is None:
        return f"volume: I can set a number (30%), or lower, higher, low, mute; not {value!r}."
    try:
        if level > 0:
            _osascript("set volume without output muted")
        _osascript(f"set volume output volume {round(level * 100)}")
    except (OSError, subprocess.SubprocessError) as exc:
        return f"volume: macOS wouldn't change it ({exc})."
    return f"Volume {round(now * 100)}% -> {round(level * 100)}%."


def loudness(exe: str) -> float:
    return 0.0  # no per-app meter on a Mac (see the module docstring)


async def is_sounding(exe: str, seconds: float = 0.6) -> bool:
    return False


def _default_browser() -> str | None:
    return None


def browser_for(hwnd: int | None) -> str | None:
    return None


def player_exe() -> str | None:
    return None  # Pyxi's own Edge player window is a Windows thing


async def play_spotify(query: str, *, back_to: int | None = None):
    """Play it in their Spotify app. None when Spotify isn't installed or the song can't be
    found (the caller opens YouTube instead)."""
    from pyxi import windows
    from pyxi.media import Played, _spotify_track

    if not spotify_installed():
        return None
    track = await _spotify_track(query)
    if track is None:
        return None
    uri = f"spotify:track:{track}"
    started = False
    try:
        await asyncio.to_thread(_osascript, f'tell application "Spotify" to play track "{uri}"', 10)
        for _ in range(8):
            await asyncio.sleep(0.4)
            state = await asyncio.to_thread(_osascript, 'tell application "Spotify" to player state as string')
            if state == "playing":
                started = True
                break
    except (OSError, subprocess.SubprocessError) as exc:
        log.info("Spotify over AppleScript failed (%s); opening the link", exc)
        await asyncio.to_thread(subprocess.run, ["open", "-g", uri], timeout=10)
    if back_to:
        windows.focus(back_to)
    return Played(
        started,
        title=query,
        url=f"https://open.spotify.com/track/{track}",
        note="" if started else "Spotify has it open, but it isn't playing yet.",
    )


async def play(query: str, *, music: bool = False, back_to: int | None = None):
    """Open the top YouTube result in the default browser without taking their screen (``open
    -g`` keeps the browser in the background)."""
    from pyxi import windows
    from pyxi.media import Played, find_video

    found = await find_video(query)
    if found is None:
        return Played(False, note="YouTube didn't return a result for that.")
    vid, title = found
    host = "music.youtube.com" if music else "www.youtube.com"
    url = f"https://{host}/watch?v={vid}"
    await asyncio.to_thread(subprocess.run, ["open", "-g", url], timeout=10)
    if back_to:
        await asyncio.sleep(0.6)
        windows.focus(back_to)
    return Played(
        True,
        title=title,
        url=url,
        note="It's open in your browser. If it doesn't start by itself, press play there.",
    )
