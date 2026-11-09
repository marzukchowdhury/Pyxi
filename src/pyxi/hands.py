"""Pyxi's hands: the mouse and keyboard, used only after the user allows it.

Everything goes through Windows' own input calls (SendInput), the same way a real mouse and
keyboard do, so it works in any app: browsers, Excel, CapCut, File Explorer. The cursor glides
to where it's going rather than teleporting, so the user can see what Pyxi is doing, and the
moment they touch the mouse or press Esc, ``Hands`` notices and stops.

Never types into password fields: the caller checks the focused field first.

Never touches Pyxi's own windows (the overlay, its Allow cards, the panel, the notch): a click,
drag or scroll that would land on one, or a key press while one is in front, is refused
(:class:`OwnWindow`). And every input Pyxi sends is timed (:func:`recently_acted`), so the brain
can refuse a card answer that arrives while Pyxi's own hands are moving: Pyxi never approves
its own cards.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import sys
import time
from collections import deque

INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
MOUSEEVENTF_MOVE, MOUSEEVENTF_ABSOLUTE, MOUSEEVENTF_VIRTUALDESK = 0x0001, 0x8000, 0x4000
MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0002, 0x0004
MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP = 0x0008, 0x0010
MOUSEEVENTF_WHEEL, MOUSEEVENTF_HWHEEL = 0x0800, 0x1000
KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 0x0002, 0x0004
VK_ESCAPE = 0x1B

# Key names the model uses -> virtual-key codes.
VK = {
    "ctrl": 0x11,
    "control": 0x11,
    "shift": 0x10,
    "alt": 0x12,
    "win": 0x5B,
    "windows": 0x5B,
    "enter": 0x0D,
    "return": 0x0D,
    "tab": 0x09,
    "esc": 0x1B,
    "escape": 0x1B,
    "space": 0x20,
    "backspace": 0x08,
    "delete": 0x2E,
    "del": 0x2E,
    "insert": 0x2D,
    "home": 0x24,
    "end": 0x23,
    "pageup": 0x21,
    "pagedown": 0x22,
    "up": 0x26,
    "down": 0x28,
    "left": 0x25,
    "right": 0x27,
    "capslock": 0x14,
    "printscreen": 0x2C,
    "menu": 0x5D,
    "apps": 0x5D,
    # Media keys work whatever app is in front: "pause the music", "next song", "louder".
    "playpause": 0xB3,
    "play": 0xB3,
    "pause": 0xB3,
    "nexttrack": 0xB0,
    "next": 0xB0,
    "prevtrack": 0xB1,
    "previous": 0xB1,
    "stop": 0xB2,
    "volumeup": 0xAF,
    "volumedown": 0xAE,
    "mute": 0xAD,
    **{f"f{i}": 0x6F + i for i in range(1, 13)},
    **{chr(c): c - 32 for c in range(ord("a"), ord("z") + 1)},
    **{str(d): 0x30 + d for d in range(10)},
    "-": 0xBD,
    "=": 0xBB,
    "[": 0xDB,
    "]": 0xDD,
    ";": 0xBA,
    "'": 0xDE,
    ",": 0xBC,
    ".": 0xBE,
    "/": 0xBF,
    "\\": 0xDC,
    "`": 0xC0,
}
EXTENDED = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E, 0x5B, 0x5D}


class _MouseInput(ctypes.Structure):
    _fields_ = [
        ("dx", wt.LONG),
        ("dy", wt.LONG),
        ("mouseData", wt.DWORD),
        ("dwFlags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", ctypes.POINTER(wt.ULONG)),
    ]


class _KeybdInput(ctypes.Structure):
    _fields_ = [
        ("wVk", wt.WORD),
        ("wScan", wt.WORD),
        ("dwFlags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", ctypes.POINTER(wt.ULONG)),
    ]


class _Union(ctypes.Union):
    _fields_ = [("mi", _MouseInput), ("ki", _KeybdInput), ("pad", ctypes.c_byte * 32)]


class _Input(ctypes.Structure):
    _fields_ = [("type", wt.DWORD), ("u", _Union)]


class Interrupted(Exception):
    """The user moved the mouse or pressed Esc: they're taking over."""


class OwnWindow(Exception):
    """The action would land on one of Pyxi's own windows (an Allow card, the panel)."""

    def __init__(self) -> None:
        super().__init__(
            "that's one of Pyxi's own windows (a card or the panel): Pyxi never clicks or types "
            "there. The user answers cards themselves"
        )


_last_input = 0.0  # time.monotonic() of the last input Pyxi sent


def recently_acted(within_s: float = 1.5) -> bool:
    """Did Pyxi's own hands send any input in the last ``within_s`` seconds?"""
    return _last_input > 0 and time.monotonic() - _last_input < within_s


def own_pids() -> set[int]:
    """The processes whose windows are Pyxi's: the desktop app (PYXI_APP_PID) and the brain."""
    pids = {os.getpid()}
    app = os.environ.get("PYXI_APP_PID", "")
    if app.isdigit():
        pids.add(int(app))
    return pids


def _pid_of(hwnd: int) -> int:
    pid = wt.DWORD()
    ctypes.windll.user32.GetWindowThreadProcessId(wt.HWND(hwnd), ctypes.byref(pid))  # type: ignore[attr-defined]
    return pid.value


def _exe_of(pid: int) -> str:
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wt.DWORD(1024)
        ok = kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size))
        return os.path.basename(buf.value).lower() if ok else ""
    finally:
        kernel32.CloseHandle(handle)


def is_own(hwnd: int) -> bool:
    """Is this top-level window one of Pyxi's?"""
    if not hwnd:
        return False
    pid = _pid_of(hwnd)
    return pid in own_pids() or _exe_of(pid) == "pyxi.exe"


def window_at(x: int, y: int) -> int:
    """The top-level window under a screen point (click-through layers don't count)."""
    u = ctypes.windll.user32  # type: ignore[attr-defined]
    u.WindowFromPoint.restype = wt.HWND
    u.WindowFromPoint.argtypes = [wt.POINT]
    u.GetAncestor.restype = wt.HWND
    u.GetAncestor.argtypes = [wt.HWND, wt.UINT]
    hwnd = u.WindowFromPoint(wt.POINT(x, y))
    return int(u.GetAncestor(hwnd, 2) or 0) if hwnd else 0  # GA_ROOT


def foreground_window() -> int:
    u = ctypes.windll.user32  # type: ignore[attr-defined]
    u.GetForegroundWindow.restype = wt.HWND
    return int(u.GetForegroundWindow() or 0)


def available() -> bool:
    return sys.platform == "win32"


def parse_keys(combo: str) -> list[int]:
    """'ctrl+shift+s' -> virtual keys, in press order. Raises ValueError for unknown keys."""
    keys = []
    for part in combo.lower().replace(" ", "").split("+"):
        if not part:
            continue
        if part not in VK:
            raise ValueError(f"unknown key {part!r}")
        keys.append(VK[part])
    if not keys:
        raise ValueError("no keys")
    return keys


class Hands:
    """One task's worth of mouse and keyboard. Check ``interrupted()`` between actions; every
    action also raises ``Interrupted`` if the user has grabbed the mouse or pressed Esc."""

    def __init__(self, glide_s: float = 0.28) -> None:
        self.glide_s = glide_s
        self._u = ctypes.windll.user32  # type: ignore[attr-defined]
        # Where Pyxi last put the cursor. Windows applies moves a moment late, so the cursor
        # trails a step or two behind mid-glide: it only counts as the user's hand when it's
        # far from all of the recent spots.
        self._trail: deque[tuple[int, int]] = deque(maxlen=8)
        self.reason = ""  # why it stopped, for the logs
        self._u.GetAsyncKeyState(VK_ESCAPE)  # clear the "pressed since last call" bit

    # ----------------------------------------------------------------- watching the user
    def cursor(self) -> tuple[int, int]:
        p = wt.POINT()
        self._u.GetCursorPos(ctypes.byref(p))
        return p.x, p.y

    def interrupted(self) -> bool:
        if self._u.GetAsyncKeyState(VK_ESCAPE) & 0x8001:
            self.reason = "esc"
            return True
        if self._trail:
            x, y = self.cursor()
            if all(abs(x - ex) > 24 or abs(y - ey) > 24 for ex, ey in self._trail):
                self.reason = f"mouse at {(x, y)}, expected near {self._trail[-1]}"
                return True
        return False

    def check(self) -> None:
        if self.interrupted():
            raise Interrupted

    def idle_seconds(self) -> float:
        """How long since the user last touched the mouse or keyboard (Windows' own count)."""

        class _LastInput(ctypes.Structure):
            _fields_ = [("cbSize", wt.UINT), ("dwTime", wt.DWORD)]

        info = _LastInput(ctypes.sizeof(_LastInput), 0)
        if not self._u.GetLastInputInfo(ctypes.byref(info)):
            return 0.0
        return max(0, ctypes.windll.kernel32.GetTickCount() - info.dwTime) / 1000  # type: ignore[attr-defined]

    def wait_idle(self, quiet_s: float = 3.0, timeout_s: float = 300.0) -> bool:
        """Wait until the user has left the mouse and keyboard alone for ``quiet_s``. False if
        they pressed Esc (stop for good) or it never got quiet."""
        import time

        end = time.monotonic() + timeout_s
        self._u.GetAsyncKeyState(VK_ESCAPE)
        while time.monotonic() < end:
            if self._u.GetAsyncKeyState(VK_ESCAPE) & 0x8001:
                self.reason = "esc"
                return False
            if self.idle_seconds() >= quiet_s:
                self._trail.clear()  # where they left the mouse isn't a new grab
                return True
            time.sleep(0.2)
        return False

    # ----------------------------------------------------------------- Pyxi's own windows
    def guard_point(self, x: int, y: int) -> None:
        """Refuse to press a button over one of Pyxi's own windows."""
        if is_own(window_at(x, y)):
            raise OwnWindow

    def guard_keys(self) -> None:
        """Refuse to type or press keys while one of Pyxi's own windows has the keyboard."""
        if is_own(foreground_window()):
            raise OwnWindow

    # ----------------------------------------------------------------- the mouse
    def _send(self, *inputs: _Input) -> None:
        global _last_input
        _last_input = time.monotonic()
        arr = (_Input * len(inputs))(*inputs)
        self._u.SendInput(len(inputs), arr, ctypes.sizeof(_Input))

    def _mouse(self, flags: int, x: int = 0, y: int = 0, data: int = 0) -> _Input:
        return _Input(INPUT_MOUSE, _Union(mi=_MouseInput(x, y, data & 0xFFFFFFFF, flags, 0, None)))

    def _absolute(self, x: int, y: int) -> tuple[int, int]:
        # Absolute coordinates span the whole virtual desktop (every monitor) as 0..65535.
        vx, vy = self._u.GetSystemMetrics(76), self._u.GetSystemMetrics(77)
        vw, vh = self._u.GetSystemMetrics(78), self._u.GetSystemMetrics(79)
        return round((x - vx) * 65535 / max(1, vw - 1)), round((y - vy) * 65535 / max(1, vh - 1))

    def move(self, x: int, y: int) -> None:
        """Glide there (eased), so the user sees where Pyxi is going."""
        self.check()
        sx, sy = self.cursor()
        steps = max(1, int(self.glide_s / 0.012))
        for i in range(1, steps + 1):
            t = i / steps
            e = t * t * (3 - 2 * t)
            px, py = round(sx + (x - sx) * e), round(sy + (y - sy) * e)
            ax, ay = self._absolute(px, py)
            self._send(
                self._mouse(
                    MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK, ax, ay
                )
            )
            self._trail.append((px, py))
            time.sleep(self.glide_s / steps)
            if i % 4 == 0:
                self.check()
        time.sleep(0.02)  # let the last move land before anything reads the cursor

    def click(self, x: int, y: int, button: str = "left", count: int = 1) -> None:
        self.move(x, y)
        down, up = (
            (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP)
            if button == "right"
            else (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP)
        )
        time.sleep(0.06)  # rest on it first: menus only arm an item once the pointer is over it
        self.guard_point(x, y)  # checked last: a card that just became clickable counts too
        for i in range(count):
            self._send(self._mouse(down))
            time.sleep(0.045)  # a real click lasts a few hundredths of a second
            self._send(self._mouse(up))
            if i + 1 < count:
                time.sleep(0.06)

    def drag(self, x0: int, y0: int, x1: int, y1: int) -> None:
        self.guard_point(x1, y1)
        self.move(x0, y0)
        self.guard_point(x0, y0)
        self._send(self._mouse(MOUSEEVENTF_LEFTDOWN))
        try:
            time.sleep(0.15)
            # Pull a little first: apps only start a drag once the pointer passes a few pixels
            # with the button held (a tab, a file), and a glide that starts slow can miss that.
            dx = 1 if x1 >= x0 else -1
            for i in range(1, 4):
                self._nudge(x0 + dx * 6 * i, y0)
                time.sleep(0.03)
            time.sleep(0.15)
            old, self.glide_s = self.glide_s, max(0.7, self.glide_s * 2)  # slower, so apps keep up
            try:
                self.move(x1, y1)
            finally:
                self.glide_s = old
            # Hover over the drop spot a moment: another window only takes the drop once it
            # has seen the pointer arrive (a tab row shows where the tab will land).
            for ox in (3, -3, 0):
                self._nudge(x1 + ox, y1)
                time.sleep(0.12)
        finally:
            self._send(self._mouse(MOUSEEVENTF_LEFTUP))

    def _nudge(self, x: int, y: int) -> None:
        ax, ay = self._absolute(x, y)
        self._send(
            self._mouse(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK, ax, ay)
        )
        self._trail.append((x, y))

    def scroll(self, x: int, y: int, direction: str = "down", notches: int = 5) -> None:
        self.move(x, y)
        self.guard_point(x, y)
        horizontal = direction in ("left", "right")
        sign = 1 if direction in ("up", "right") else -1
        for _ in range(max(1, min(notches, 30))):
            self.check()
            self._send(
                self._mouse(
                    MOUSEEVENTF_HWHEEL if horizontal else MOUSEEVENTF_WHEEL, data=sign * 120
                )
            )
            time.sleep(0.03)

    # ----------------------------------------------------------------- the keyboard
    def _key(self, vk: int, up: bool) -> _Input:
        flags = (KEYEVENTF_KEYUP if up else 0) | (0x0001 if vk in EXTENDED else 0)
        return _Input(INPUT_KEYBOARD, _Union(ki=_KeybdInput(vk, 0, flags, 0, None)))

    def press(self, combo: str) -> None:
        """A key or chord: 'enter', 'ctrl+s', 'alt+tab', 'ctrl+shift+esc'."""
        self.check()
        self.guard_keys()
        keys = parse_keys(combo)
        self._send(
            *[self._key(k, False) for k in keys], *[self._key(k, True) for k in reversed(keys)]
        )
        time.sleep(0.05)
        if VK_ESCAPE in keys:
            self._u.GetAsyncKeyState(VK_ESCAPE)  # Pyxi's own Esc isn't the user saying stop

    def type(self, text: str, per_char_s: float = 0.014, settle_s: float = 0.16) -> None:
        """Type text the way a keyboard does: each character as its real key (with Shift where
        needed), so web apps that draw their own text (Google Sheets, Docs, Figma) get proper
        key presses. Only characters with no key on this layout (emoji, ✓) go as Unicode.
        Newlines and tabs are Enter and Tab, followed by a pause: Sheets saves the cell on Tab
        or Enter and drops whatever is typed while it's still doing that."""
        for i, ch in enumerate(text):
            if i % 8 == 0:
                self.check()
                self.guard_keys()
            if ch in "\n\t":
                self.press("enter" if ch == "\n" else "tab")
                time.sleep(settle_s)
                continue
            scan = (self._u.VkKeyScanW(ord(ch)) if ord(ch) < 0x10000 else -1) & 0xFFFF
            if scan != 0xFFFF and not (scan >> 8) & 0b110:  # a real key, maybe with Shift
                vk, shift = scan & 0xFF, bool((scan >> 8) & 1)
                keys = ([self._key(0x10, False)] if shift else []) + [
                    self._key(vk, False),
                    self._key(vk, True),
                ]
                self._send(*keys, *([self._key(0x10, True)] if shift else []))
                time.sleep(per_char_s)
                continue
            for unit in _utf16(ch):
                self._send(
                    _Input(
                        INPUT_KEYBOARD, _Union(ki=_KeybdInput(0, unit, KEYEVENTF_UNICODE, 0, None))
                    ),
                    _Input(
                        INPUT_KEYBOARD,
                        _Union(
                            ki=_KeybdInput(0, unit, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0, None)
                        ),
                    ),
                )
            time.sleep(per_char_s)


def _utf16(ch: str) -> list[int]:
    b = ch.encode("utf-16-le")
    return [int.from_bytes(b[i : i + 2], "little") for i in range(0, len(b), 2)]


if sys.platform == "darwin":  # Quartz events instead of SendInput (pyxi/macos/hands.py)
    from pyxi.macos.hands import *  # noqa: F403
