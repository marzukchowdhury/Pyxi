"""Record one take for the film: ask the running Pyxi the way a person does, and film the screen.

    .venv/Scripts/python.exe website/tools/film_take.py <name> "<request>" [seconds] [--click x,y@t ...]

Pyxi must be running with PYXI_SHOW_OVERLAY_IN_CAPTURE=1 so its overlay is filmed. The request is
typed into Pyxi's own "Ask Pyxi anything" box: its hotkey (Ctrl+Alt+W) opens the box, the words are
typed, Enter sends them. "--click 960,540@12" clicks that point 12 seconds after asking (to press
Allow on a card). Nothing here talks to Pyxi behind its back: it only presses keys and clicks.
Writes website/.shots/film2/takes/<name>/: the frames and when each was taken (git-ignored).
"""

from __future__ import annotations

import ctypes
import json
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

import mss
from PIL import Image

FILM = Path(__file__).resolve().parents[1] / ".shots" / "film2"
name, request = sys.argv[1], sys.argv[2]
seconds = float(sys.argv[3]) if len(sys.argv) > 3 and not sys.argv[3].startswith("--") else 30.0
clicks = []
for i, a in enumerate(sys.argv):
    if a == "--click":
        xy, t = sys.argv[i + 1].split("@")
        clicks.append((float(t), *[int(v) for v in xy.split(",")]))
out = FILM / "takes" / name
out.mkdir(parents=True, exist_ok=True)
for old in out.glob("*.jpg"):
    old.unlink()

user32 = ctypes.windll.user32
KEYUP, UNICODE = 0x0002, 0x0004
ULONG_PTR = ctypes.POINTER(ctypes.c_ulong)


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]

    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


def key(vk=0, scan=0, flags=0):
    inp = INPUT(type=1)
    inp.ki = KEYBDINPUT(vk, scan, flags, 0, None)
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def chord(*vks):
    for vk in vks:
        key(vk)
        time.sleep(0.03)
    for vk in reversed(vks):
        key(vk, flags=KEYUP)
        time.sleep(0.03)


def type_text(text, per_char=0.035):
    for ch in text:
        key(scan=ord(ch), flags=UNICODE)
        key(scan=ord(ch), flags=UNICODE | KEYUP)
        time.sleep(per_char)


def click(x, y):
    """Glide the pointer there the way a hand does, then press."""
    pt = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    for k in range(1, 31):
        u = 1 - (1 - k / 30) ** 3
        user32.SetCursorPos(int(pt.x + (x - pt.x) * u), int(pt.y + (y - pt.y) * u))
        time.sleep(0.02)
    # Pyxi's windows let the mouse through until they feel it move over them: a small wiggle
    for dx in (2, -2, 1, -1):
        inp = INPUT(type=0)
        inp.mi = MOUSEINPUT(dx, 0, 0, 0x0001, 0, None)
        user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
        time.sleep(0.08)
    time.sleep(0.35)
    for flag in (0x0002, 0x0004):
        inp = INPUT(type=0)
        inp.mi = MOUSEINPUT(0, 0, 0, flag, 0, None)
        user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
        time.sleep(0.06)


stop = threading.Event()
stamps: list[float] = []


def film():
    """The main screen, as fast as it comes, as JPEGs."""
    with mss.MSS() as grab:
        mon = next((m for m in grab.monitors[1:] if m.get("is_primary")), grab.monitors[1])
        nxt = time.time()
        while not stop.is_set():
            shot = grab.grab(mon)
            stamps.append(time.time())
            Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX").save(out / f"{len(stamps) - 1:06d}.jpg", quality=90)
            nxt += 1 / 30
            time.sleep(max(0, nxt - time.time()))


def bring_front(title_part):
    """Put the first visible window whose title contains this in front (e.g. the browser)."""
    found = []

    def each(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(300)
            user32.GetWindowTextW(hwnd, buf, 300)
            if title_part.lower() in buf.value.lower():
                found.append(hwnd)
        return True

    user32.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)(each), 0)
    if not found:
        raise SystemExit(f"no window with {title_part!r} in its title")
    key(0x12)  # a tap of Alt: Windows only lets a program that just got input change the front window
    key(0x12, flags=KEYUP)
    user32.ShowWindow(found[0], 3)  # maximised
    user32.SetForegroundWindow(found[0])
    time.sleep(1.2)


front = sys.argv[sys.argv.index("--front") + 1] if "--front" in sys.argv else None
if front:
    bring_front(front)
PANEL_CLOSE = (1168, 26)  # the panel's X; with no panel open this is empty space in the browser's tab strip
click(*PANEL_CLOSE)  # start from a known state: the panel is away
time.sleep(0.6)
t0 = time.time()
cam = threading.Thread(target=film, daemon=True)
cam.start()
time.sleep(1.5)
chord(0x11, 0x12, 0x57)  # Ctrl+Alt+W: Pyxi's own hotkey, opens its panel with the ask box
time.sleep(1.1)
click(900, 634)  # the "Ask Pyxi anything" box at the foot of the panel
time.sleep(0.3)
type_text(request)
time.sleep(0.35)
chord(0x0D)
asked = time.time()
time.sleep(0.5)
click(*PANEL_CLOSE)  # put the panel away again, so the answer shows on the screen itself
for t, x, y in sorted(clicks):
    time.sleep(max(0, asked + t - time.time()))
    click(x, y)
time.sleep(max(0, asked + seconds - time.time()))
stop.set()
cam.join()
(out / "take.json").write_text(json.dumps({"request": request, "asked": asked - t0, "stamps": [s - t0 for s in stamps]}), encoding="utf-8")
print(f"{name}: {len(stamps)} frames in {stamps[-1] - stamps[0]:.1f} s ({len(stamps) / (stamps[-1] - stamps[0]):.0f} a second), asked at {asked - t0:.1f} s")
