"""Music and volume in a second or two, without driving the mouse.

"Play Billie Jean on YouTube, a bit quieter" used to be ten looks of clicking search boxes and a
volume slider. Here it's: find the video on YouTube's own search page, open it in a window of the
browser they use, slip that window behind the one they're in, make sure it's actually playing,
and set the Windows volume directly.

Windows only (Core Audio through pycaw, window calls through pyxi.windows).
"""

from __future__ import annotations

import asyncio
import logging
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote_plus

import httpx

from pyxi import hands, windows

log = logging.getLogger(__name__)

BROWSERS = ("firefox.exe", "chrome.exe", "msedge.exe", "brave.exe", "opera.exe", "vivaldi.exe")
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0 Safari/537.36"
)
_VIDEO = re.compile(r'"videoRenderer":\{"videoId":"([\w-]{11})"')
_TITLE = r'"videoRenderer":\{{"videoId":"{id}".*?"title":\{{"runs":\[\{{"text":"(.*?)"'


@dataclass
class Played:
    ok: bool
    title: str = ""
    url: str = ""
    note: str = ""


async def find_video(query: str) -> tuple[str, str] | None:
    """The top YouTube result for ``query``: (video id, title). None if YouTube won't say."""
    try:
        async with httpx.AsyncClient(timeout=8, follow_redirects=True) as client:
            r = await client.get(
                "https://www.youtube.com/results",
                params={"search_query": query},
                headers={"User-Agent": _UA, "Accept-Language": "en-US,en;q=0.9"},
            )
    except httpx.HTTPError as exc:
        log.warning("YouTube search failed: %s", exc)
        return None
    m = _VIDEO.search(r.text)
    if not m:
        return None
    t = re.search(_TITLE.format(id=re.escape(m.group(1))), r.text)
    title = t.group(1).encode().decode("unicode_escape", "ignore") if t else query
    return m.group(1), title


# ---------------------------------------------------------------- volume and who's making sound
def _com() -> None:
    """Core Audio is COM, and these run on worker threads, which need COM set up first."""
    import comtypes

    try:
        comtypes.CoInitialize()
    except OSError:
        pass  # already set up on this thread


def _speakers():
    from pycaw.pycaw import AudioUtilities

    _com()
    return AudioUtilities.GetSpeakers().EndpointVolume


def volume() -> float:
    """Windows' master volume, 0 to 1."""
    return float(_speakers().GetMasterVolumeLevelScalar())


def parse_volume(value: str, now: float) -> float | None:
    """'30' or '30%' -> 0.30 · '-15' / '+10' -> relative · 'lower', 'a bit lower', 'quieter' ->
    down a fifth of the range · 'higher', 'louder' -> up · 'low' -> 0.2 · 'mute' -> 0."""
    v = value.strip().lower().replace("%", "")
    if re.fullmatch(r"[+-]\d{1,3}", v):
        return min(1.0, max(0.0, now + int(v) / 100))
    if re.fullmatch(r"\d{1,3}", v):
        return min(1.0, int(v) / 100)
    if v in ("mute", "silent", "off"):
        return 0.0
    if v in ("low", "quiet", "soft"):
        return 0.2
    if v in ("medium", "half"):
        return 0.5
    if v in ("high", "loud", "max", "full"):
        return 1.0 if v in ("max", "full") else 0.8
    if any(w in v for w in ("lower", "quieter", "down", "softer", "less")):
        return max(0.05, now - 0.2 if "bit" not in v and "little" not in v else now - 0.12)
    if any(w in v for w in ("higher", "louder", "up", "more")):
        return min(1.0, now + 0.2 if "bit" not in v and "little" not in v else now + 0.12)
    return None


def set_volume(value: str) -> str:
    """Set the master volume from words or a number; says what it did."""
    now = volume()
    level = parse_volume(value, now)
    if level is None:
        return f"volume: I can set a number (30%), or lower, higher, low, mute; not {value!r}."
    spk = _speakers()
    if level > 0:
        spk.SetMute(0, None)
    spk.SetMasterVolumeLevelScalar(level, None)
    return f"Volume {round(now * 100)}% -> {round(level * 100)}%."


def loudness(exe: str) -> float:
    """How loud a program is right now (its peak meter, 0 to 1), across all its audio sessions."""
    from pycaw.pycaw import AudioUtilities, IAudioMeterInformation

    _com()
    peak = 0.0
    for s in AudioUtilities.GetAllSessions():
        if s.Process and s.Process.name().lower() == exe:
            meter = s._ctl.QueryInterface(IAudioMeterInformation)
            peak = max(peak, float(meter.GetPeakValue()))
    return peak


async def is_sounding(exe: str, seconds: float = 0.6) -> bool:
    """Peaks drop to zero between beats; listen a little while rather than once."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if await asyncio.to_thread(loudness, exe) > 0.003:
            return True
        await asyncio.sleep(0.1)
    return False


# ---------------------------------------------------------------- which browser
def _default_browser() -> str | None:
    if sys.platform != "win32":
        return None
    import winreg

    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice",
        )
        prog = winreg.QueryValueEx(key, "ProgId")[0]
        cmd = winreg.QueryValueEx(
            winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, rf"{prog}\shell\open\command"), ""
        )[0]
    except OSError:
        return None
    m = re.match(r'"([^"]+)"|(\S+)', cmd)
    return (m.group(1) or m.group(2)) if m else None


def browser_for(hwnd: int | None) -> str | None:
    """The browser they use: the one they're in, else the topmost browser window, else the
    default browser."""
    candidates = [hwnd] if hwnd else []
    candidates += [h for h, _t, _e in windows.open_windows()]
    for h in candidates:
        exe = windows.exe_of(h) if h else ""
        if Path(exe).name.lower() in BROWSERS:
            return exe
    return _default_browser()


# ---------------------------------------------------------------- play
# ---------------------------------------------------------------- Pyxi's own player
# Their browser may refuse to start sound on its own (Firefox blocks it by default, and its
# taskbar "YouTube Music" app is Firefox too), so a song opened there sat silent while Pyxi
# pressed keys at it for 26 s. Pyxi plays music in its own small Edge (or Chrome) window instead:
# its own profile (never theirs), allowed to start sound, opened off to the side and minimised,
# one song at a time. Media keys (pause, next, previous) reach it like any player.
_PLAYERS = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
)
_player_windows: set[int] = set()
SW_SHOWMINNOACTIVE = 7
WM_CLOSE = 0x0010


def player_exe() -> str | None:
    import os

    local = os.environ.get("LOCALAPPDATA", "")
    extra = (rf"{local}\Google\Chrome\Application\chrome.exe",) if local else ()
    return next((p for p in (*_PLAYERS, *extra) if Path(p).exists()), None)


def _player_dir() -> Path:
    import os

    d = Path(os.environ.get("PYXI_DATA_DIR", ".pyxi")).resolve() / "player"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _close_player() -> None:
    """The last song's window: one song at a time, so two never talk over each other."""
    import ctypes

    for h in list(_player_windows):
        ctypes.windll.user32.PostMessageW(h, WM_CLOSE, 0, 0)  # type: ignore[attr-defined]
        _player_windows.discard(h)


async def _play_in_player(exe: str, url: str, title: str, back_to: int | None) -> Played:
    import ctypes

    name = Path(exe).name.lower()
    _close_player()
    before = {h for h, _t, _e in windows.open_windows(minimized=True)}
    sw, sh = ctypes.windll.user32.GetSystemMetrics(0), ctypes.windll.user32.GetSystemMetrics(1)  # type: ignore[attr-defined]
    subprocess.Popen(
        [
            exe,
            f"--user-data-dir={_player_dir()}",
            "--autoplay-policy=no-user-gesture-required",
            # Keep playing once it's minimised: no pausing of pages nobody is looking at.
            "--disable-background-media-suspend",
            "--disable-renderer-backgrounding",
            "--disable-backgrounding-occluded-windows",
            "--disable-features=CalculateNativeWinOcclusion",
            "--no-first-run",
            "--no-default-browser-check",
            # Small, in the bottom-right corner, until the song starts (a page that was never
            # shown doesn't start its video), then minimised.
            "--window-size=360,260",
            f"--window-position={max(0, sw - 380)},{max(0, sh - 320)}",
            f"--app={url}",
        ]
    )
    want = _norm(title)[:24]
    hwnd, end = None, time.monotonic() + 10.0
    while time.monotonic() < end and hwnd is None:
        await asyncio.sleep(0.15)
        fresh = [
            (h, t)
            for h, t, e in windows.open_windows(minimized=True)
            if h not in before and Path(e).name.lower() == name
        ]
        if fresh:
            hwnd = next((h for h, t in fresh if want and want in _norm(t)), fresh[0][0])
    if hwnd:
        _player_windows.add(hwnd)
    if back_to:
        windows.focus(back_to)  # they carry on where they were
    started = await _wait_for_sound(name, 12.0)
    if hwnd:  # playing (or given its chance): out of the way, on the taskbar
        ctypes.windll.user32.ShowWindow(hwnd, SW_SHOWMINNOACTIVE)  # type: ignore[attr-defined]
        if back_to:
            windows.focus(back_to)
    log.info("play: player %s window %s, sounding=%s, %r", name, hwnd, started, title)
    return Played(
        started,
        title=title,
        url=url,
        note=""
        if started
        else "It's open in Pyxi's player (minimised on the taskbar), but I can't hear it yet.",
    )


def spotify_installed() -> bool:
    """The Spotify app registers spotify: links (the Microsoft Store version too)."""
    if sys.platform != "win32":
        return False
    import winreg

    try:
        winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"spotify\shell\open\command")
        return True
    except OSError:
        return False


async def _spotify_track(query: str) -> str | None:
    """A Spotify track id for the query, from a web search for its open.spotify.com page."""
    from pyxi import web

    try:
        text = await web.search_text(f"{query} site:open.spotify.com/track")
    except Exception:
        return None
    m = re.search(r"open\.spotify\.com/(?:intl-\w+/)?track/([A-Za-z0-9]{22})", text)
    return m.group(1) if m else None


async def play_spotify(query: str, *, back_to: int | None = None) -> Played | None:
    """In their Spotify app, without taking their screen. None when Spotify isn't installed or
    the song can't be found there (the caller plays it in Pyxi's player instead)."""
    if not spotify_installed():
        return None
    track = await _spotify_track(query)
    if track is None:
        return None
    import os

    os.startfile(f"spotify:track:{track}")  # type: ignore[attr-defined]  # opens it in the app
    await asyncio.sleep(1.5)
    started = await _wait_for_sound("spotify.exe", 2.0)
    if not started:  # the app shows the song but waits for play: Enter plays what it shows
        app = next(
            (
                h
                for h, _t, e in windows.open_windows(minimized=True)
                if Path(e).name.lower() == "spotify.exe"
            ),
            None,
        )
        if app:
            windows.focus(app)
            await asyncio.sleep(0.4)
            await asyncio.to_thread(hands.Hands().press, "enter")
        started = await _wait_for_sound("spotify.exe", 4.0)
    for h, _t, e in windows.open_windows():
        if Path(e).name.lower() == "spotify.exe":
            import ctypes

            ctypes.windll.user32.ShowWindow(h, SW_SHOWMINNOACTIVE)  # type: ignore[attr-defined]
    if back_to:
        windows.focus(back_to)
    return Played(
        started,
        title=query,
        url=f"https://open.spotify.com/track/{track}",
        note="" if started else "Spotify has it open, but I can't hear it yet.",
    )


async def play(query: str, *, music: bool = False, back_to: int | None = None) -> Played:
    """Play the top YouTube result for ``query``: in Pyxi's own minimised player when there's
    Edge or Chrome, else in a window of their browser behind the one they're in.

    ``music``: open it in YouTube Music (same videos, the music player) instead of YouTube."""
    found = await find_video(query)
    if found is None:
        return Played(False, note="YouTube didn't return a result for that.")
    vid, title = found
    url = (
        f"https://music.youtube.com/watch?v={vid}"
        if music
        else f"https://www.youtube.com/watch?v={vid}"
    )
    theirs = browser_for(back_to)
    their_name = Path(theirs).name.lower() if theirs else ""
    # Something already playing in their browser (a video, another song) would talk over it.
    if their_name and await is_sounding(their_name, 0.5):
        await asyncio.to_thread(hands.Hands().press, "playpause")
        await asyncio.sleep(0.4)
    if (player := player_exe()) is not None:
        return await _play_in_player(player, url, title, back_to)
    exe = theirs
    name = Path(exe).name.lower() if exe else "msedge.exe"
    before = windows._visible_windows()
    flag = windows.BROWSER_FLAGS.get(name, "--new-window")
    if exe:
        subprocess.Popen([exe, flag, url])
    else:
        subprocess.Popen(["cmd", "/c", "start", "", "msedge", "--new-window", url])
    # Its window, by the video's title in the window title (that also means the page has loaded).
    # Another window can appear meanwhile (a browser popup), so "the newest window" isn't enough.
    new = await _video_window(title, before, name, 10.0)
    log.info("play: %s window %s for %r", name, new, title)
    if back_to:
        windows.focus(back_to)  # back to what they were doing; the song plays behind it
    # Browsers may block sound that starts on its own (Firefox does by default). Still silent
    # once it's loaded: press play in that window, once.
    # The title shows before YouTube's player is ready, so a press too early is lost: try a few
    # times, listening in between. Only ever pressed while it's silent, so it can't pause it.
    started = await _wait_for_sound(name, 1.5)
    for _ in range(5):
        if started or not new:
            break
        focused = windows.focus(new)
        await asyncio.sleep(0.3)
        await asyncio.to_thread(hands.Hands().press, "k")  # YouTube's play/pause key
        log.info("play: pressed play (focused=%s, front=%s)", focused, windows.foreground())
        if back_to:
            await asyncio.sleep(0.3)
            windows.focus(back_to)
        started = await _wait_for_sound(name, 1.6)
    return Played(
        started,
        title=title,
        url=url,
        note="" if started else "It's open but I can't hear it playing yet.",
    )


def _norm(text: str) -> str:
    return "".join(c for c in text.lower() if c.isalnum())


async def _video_window(title: str, before: set[int], exe: str, seconds: float) -> int | None:
    """The new browser window showing ``title``; failing that, the browser's newest window."""
    want = _norm(title)[:24]
    end = time.monotonic() + seconds
    fallback = None
    while time.monotonic() < end:
        await asyncio.sleep(0.25)
        for hwnd, wtitle, wexe in windows.open_windows():
            if Path(wexe).name.lower() != exe:
                continue
            if want and want in _norm(wtitle):
                return hwnd
            if hwnd not in before and fallback is None:
                fallback = hwnd
    return fallback


async def _wait_for_sound(exe: str, seconds: float) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if await is_sounding(exe, 0.4):
            return True
    return False


def play_url(query: str, music: bool = False) -> str:
    """A search page, for when the video can't be looked up."""
    base = (
        "https://music.youtube.com/search?q="
        if music
        else "https://www.youtube.com/results?search_query="
    )
    return base + quote_plus(query)


if sys.platform == "darwin":  # AppleScript volume, Spotify and the browser (pyxi/macos/media.py)
    from pyxi.macos.media import *  # noqa: F403
