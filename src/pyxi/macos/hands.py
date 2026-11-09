"""The Mac side of ``pyxi.hands``: the mouse and keyboard through Quartz events.

Same rules as on Windows: the cursor glides, the user grabbing the mouse or holding Esc stops it,
Pyxi's own windows are never clicked or typed into, and every input is timed so Pyxi never
approves its own cards (``pyxi.hands.recently_acted``).

Shortcuts: the model (and the user) mostly speak Windows ("ctrl+c", "alt+tab", "win"). On a Mac
those become what a Mac person presses: ctrl+<key> is Cmd+<key> (except ctrl+tab, which browsers
keep on Control), alt is Option, win is Cmd, alt+tab is Cmd+Tab and alt+f4 is Cmd+Q. "cmd",
"command" and "option" work as written.

Needs the Accessibility permission: without it macOS drops every event Pyxi posts.
"""

from __future__ import annotations

import sys
import time
from collections import deque

from pyxi import hands as _shared
from pyxi.hands import Interrupted, OwnWindow, own_pids
from pyxi.macos import _ax, appkit, axapi, quartz

__all__ = [
    "EXTENDED",
    "VK",
    "Hands",
    "available",
    "foreground_window",
    "is_own",
    "parse_keys",
    "window_at",
]

# Mac virtual key codes (Carbon's kVK_*), by the names the model uses.
VK: dict[str, int] = {
    "cmd": 0x37,
    "command": 0x37,
    "shift": 0x38,
    "capslock": 0x39,
    "option": 0x3A,
    "control": 0x3B,
    "fn": 0x3F,
    "enter": 0x24,
    "return": 0x24,
    "tab": 0x30,
    "space": 0x31,
    "backspace": 0x33,  # the Mac's "delete" key
    "esc": 0x35,
    "escape": 0x35,
    "delete": 0x75,  # forward delete, what Windows calls Delete
    "del": 0x75,
    "insert": 0x72,  # Help on old keyboards; nothing on new ones
    "home": 0x73,
    "end": 0x77,
    "pageup": 0x74,
    "pagedown": 0x79,
    "left": 0x7B,
    "right": 0x7C,
    "down": 0x7D,
    "up": 0x7E,
    "f1": 0x7A, "f2": 0x78, "f3": 0x63, "f4": 0x76, "f5": 0x60, "f6": 0x61,
    "f7": 0x62, "f8": 0x64, "f9": 0x65, "f10": 0x6D, "f11": 0x67, "f12": 0x6F,
    "a": 0x00, "s": 0x01, "d": 0x02, "f": 0x03, "h": 0x04, "g": 0x05, "z": 0x06, "x": 0x07,
    "c": 0x08, "v": 0x09, "b": 0x0B, "q": 0x0C, "w": 0x0D, "e": 0x0E, "r": 0x0F, "y": 0x10,
    "t": 0x11, "o": 0x1F, "u": 0x20, "i": 0x22, "p": 0x23, "l": 0x25, "j": 0x26, "k": 0x28,
    "n": 0x2D, "m": 0x2E,
    "1": 0x12, "2": 0x13, "3": 0x14, "4": 0x15, "5": 0x17, "6": 0x16, "7": 0x1A, "8": 0x1C,
    "9": 0x19, "0": 0x1D,
    "=": 0x18, "-": 0x1B, "]": 0x1E, "[": 0x21, "'": 0x27, ";": 0x29, "\\": 0x2A, ",": 0x2B,
    "/": 0x2C, ".": 0x2F, "`": 0x32,
}  # fmt: skip
CMD, SHIFT, OPTION, CONTROL, FN = 0x37, 0x38, 0x3A, 0x3B, 0x3F
MODIFIERS = {CMD, SHIFT, OPTION, CONTROL, FN}
# Event flags each modifier sets (kCGEventFlagMask*).
FLAGS = {CMD: 0x100000, SHIFT: 0x20000, OPTION: 0x80000, CONTROL: 0x40000, FN: 0x800000}
# Media keys aren't key codes on a Mac: they're "system defined" events (NX_KEYTYPE_*). Encoded
# above every real key code.
MEDIA = 0x1000
_MEDIA_KEYS = {
    "playpause": 16, "play": 16, "pause": 16, "nexttrack": 17, "next": 17, "prevtrack": 18,
    "previous": 18, "stop": 16, "volumeup": 0, "volumedown": 1, "mute": 7,
}  # fmt: skip
EXTENDED: set[int] = set()  # a Windows notion; kept so the names line up
_ESC = 0x35
# Windows names for modifiers -> Mac keys.
_ALIASES = {
    "ctrl": "control",
    "alt": "option",
    "opt": "option",
    "win": "cmd",
    "windows": "cmd",
    "meta": "cmd",
    "super": "cmd",
}
# Whole Windows shortcuts with a different Mac shape.
_SHORTCUTS = {
    "alt+tab": "cmd+tab",
    "alt+shift+tab": "cmd+shift+tab",
    "alt+f4": "cmd+q",
    "ctrl+f4": "cmd+w",
    "win+d": "f11",  # show the desktop
    "win+e": "cmd+option+space",  # a Finder window
    "win+l": "control+cmd+q",  # lock the screen
    "alt+left": "cmd+[",  # back, in browsers and Finder
    "alt+right": "cmd+]",
    "ctrl+home": "cmd+up",
    "ctrl+end": "cmd+down",
}
_CONTROL_STAYS = {"tab"}  # ctrl+tab / ctrl+shift+tab switch tabs on a Mac too


def available() -> bool:
    return sys.platform == "darwin"


def mac_combo(combo: str) -> str:
    """'ctrl+shift+s' -> 'cmd+shift+s', 'alt+tab' -> 'cmd+tab', 'win' -> 'cmd'."""
    parts = [p for p in combo.lower().replace(" ", "").split("+") if p]
    plain = "+".join(parts)
    if plain in _SHORTCUTS:
        return _SHORTCUTS[plain]
    keys = [k for k in parts if _ALIASES.get(k, k) not in ("control", "option", "shift", "cmd", "fn")]
    out = []
    for p in parts:
        name = _ALIASES.get(p, p)
        # Windows' ctrl+<key> is the Mac's Cmd+<key>. "control" spelled out stays Control.
        if p == "ctrl" and keys and not set(keys) & _CONTROL_STAYS:
            name = "cmd"
        out.append(name)
    return "+".join(dict.fromkeys(out))  # ctrl+win+c: one Cmd, not two


def parse_keys(combo: str) -> list[int]:
    """A combo -> Mac key codes in press order (media keys as ``MEDIA + NX code``). Raises
    ValueError for keys a Mac doesn't have (menu, printscreen)."""
    keys = []
    for part in mac_combo(combo).split("+"):
        if not part:
            continue
        if part in _MEDIA_KEYS:
            keys.append(MEDIA + _MEDIA_KEYS[part])
        elif part in VK:
            keys.append(VK[part])
        else:
            raise ValueError(f"unknown key {part!r}")
    if not keys:
        raise ValueError("no keys")
    return keys


def window_at(x: int, y: int) -> int:
    """The window under a point, Pyxi's own included, for the guards below."""
    for info in _ax.cg_windows():
        if int(info.get(_ax.LAYER, 0)) < 0 or float(info.get(_ax.ALPHA, 1.0)) <= 0:
            continue
        if info.get(_ax.OWNER) in ("Window Server",):
            continue
        if _ax.contains(_ax.bounds(info), x, y) and int(info.get(_ax.LAYER, 0)) == 0:
            return int(info[_ax.NUMBER])
    return 0


def foreground_window() -> int:
    from pyxi.macos.windows import foreground

    return foreground()


def _is_own_pid(pid: int) -> bool:
    return bool(pid) and (pid in own_pids() or _ax.app_name(pid) == "Pyxi")


def is_own(hwnd: int) -> bool:
    if not hwnd:
        return False
    info = _ax.cg_info(hwnd)
    return bool(info) and _is_own_pid(int(info.get(_ax.PID, 0)))


def _pid_at(x: int, y: int) -> int:
    """The app that would get a click at this point. Asked of Accessibility, which hit-tests the
    way the window server does: Pyxi's click-through overlay doesn't count, its cards do."""
    try:
        AX = axapi()
        res = AX.AXUIElementCopyElementAtPosition(AX.AXUIElementCreateSystemWide(), float(x), float(y), None)
    except Exception:
        return 0
    el = res[1] if isinstance(res, tuple) and res[0] == 0 else None if isinstance(res, tuple) else res
    return _ax.pid_of_element(el) if el is not None else 0


class Hands:
    """One task's worth of mouse and keyboard, as on Windows."""

    def __init__(self, glide_s: float = 0.28) -> None:
        self.glide_s = glide_s
        self._q = quartz()
        self._trail: deque[tuple[int, int]] = deque(maxlen=8)
        self.reason = ""
        self._held = False  # the left button is down (a drag): moves are drags

    # ----------------------------------------------------------------- watching the user
    def cursor(self) -> tuple[int, int]:
        p = self._q.CGEventGetLocation(self._q.CGEventCreate(None))
        return int(p.x), int(p.y)

    def _esc_down(self) -> bool:
        try:
            return bool(self._q.CGEventSourceKeyState(self._q.kCGEventSourceStateHIDSystemState, _ESC))
        except Exception:
            return False

    def interrupted(self) -> bool:
        if self._esc_down():
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
        """Seconds since the user last touched a real mouse or keyboard."""
        try:
            any_input = getattr(self._q, "kCGAnyInputEventType", 0xFFFFFFFF)
            return float(
                self._q.CGEventSourceSecondsSinceLastEventType(
                    self._q.kCGEventSourceStateHIDSystemState, any_input
                )
            )
        except Exception:
            return 0.0

    def wait_idle(self, quiet_s: float = 3.0, timeout_s: float = 300.0) -> bool:
        end = time.monotonic() + timeout_s
        while time.monotonic() < end:
            if self._esc_down():
                self.reason = "esc"
                return False
            if self.idle_seconds() >= quiet_s:
                self._trail.clear()
                return True
            time.sleep(0.2)
        return False

    # ----------------------------------------------------------------- Pyxi's own windows
    def guard_point(self, x: int, y: int) -> None:
        if _is_own_pid(_pid_at(x, y)) or is_own(window_at(x, y)):
            raise OwnWindow

    def guard_keys(self) -> None:
        if _is_own_pid(_ax.frontmost_pid()):
            raise OwnWindow

    # ----------------------------------------------------------------- posting events
    def _post(self, event) -> None:
        _shared._last_input = time.monotonic()
        self._q.CGEventPost(self._q.kCGHIDEventTap, event)

    def _mouse_event(self, kind: str, x: int, y: int, button: str = "left"):
        Q = self._q
        types = {
            ("move", "left"): Q.kCGEventMouseMoved,
            ("drag", "left"): Q.kCGEventLeftMouseDragged,
            ("down", "left"): Q.kCGEventLeftMouseDown,
            ("up", "left"): Q.kCGEventLeftMouseUp,
            ("down", "right"): Q.kCGEventRightMouseDown,
            ("up", "right"): Q.kCGEventRightMouseUp,
        }
        btn = Q.kCGMouseButtonRight if button == "right" else Q.kCGMouseButtonLeft
        return Q.CGEventCreateMouseEvent(None, types[(kind, button)], Q.CGPointMake(x, y), btn)

    def _goto(self, x: int, y: int) -> None:
        self._post(self._mouse_event("drag" if self._held else "move", x, y))
        self._trail.append((x, y))

    # ----------------------------------------------------------------- the mouse
    def move(self, x: int, y: int) -> None:
        self.check()
        sx, sy = self.cursor()
        steps = max(1, int(self.glide_s / 0.012))
        for i in range(1, steps + 1):
            t = i / steps
            e = t * t * (3 - 2 * t)
            self._goto(round(sx + (x - sx) * e), round(sy + (y - sy) * e))
            time.sleep(self.glide_s / steps)
            if i % 4 == 0:
                self.check()
        time.sleep(0.02)

    def click(self, x: int, y: int, button: str = "left", count: int = 1) -> None:
        self.move(x, y)
        time.sleep(0.06)
        self.guard_point(x, y)
        Q = self._q
        for i in range(count):
            down = self._mouse_event("down", x, y, button)
            up = self._mouse_event("up", x, y, button)
            for ev in (down, up):  # the click count makes a double-click a double-click
                Q.CGEventSetIntegerValueField(ev, Q.kCGMouseEventClickState, i + 1)
            self._post(down)
            time.sleep(0.045)
            self._post(up)
            if i + 1 < count:
                time.sleep(0.06)

    def drag(self, x0: int, y0: int, x1: int, y1: int) -> None:
        self.guard_point(x1, y1)
        self.move(x0, y0)
        self.guard_point(x0, y0)
        self._post(self._mouse_event("down", x0, y0))
        self._held = True
        try:
            time.sleep(0.15)
            dx = 1 if x1 >= x0 else -1
            for i in range(1, 4):
                self._goto(x0 + dx * 6 * i, y0)
                time.sleep(0.03)
            time.sleep(0.15)
            old, self.glide_s = self.glide_s, max(0.7, self.glide_s * 2)
            try:
                self.move(x1, y1)
            finally:
                self.glide_s = old
            for ox in (3, -3, 0):
                self._goto(x1 + ox, y1)
                time.sleep(0.12)
        finally:
            self._held = False
            self._post(self._mouse_event("up", x1, y1))

    def scroll(self, x: int, y: int, direction: str = "down", notches: int = 5) -> None:
        self.move(x, y)
        self.guard_point(x, y)
        Q = self._q
        sign = 1 if direction in ("up", "left") else -1
        horizontal = direction in ("left", "right")
        for _ in range(max(1, min(notches, 30))):
            self.check()
            # One wheel notch on Windows scrolls about three lines.
            dy, dx = (0, sign * 3) if horizontal else (sign * 3, 0)
            self._post(Q.CGEventCreateScrollWheelEvent(None, Q.kCGScrollEventUnitLine, 2, dy, dx))
            time.sleep(0.03)

    # ----------------------------------------------------------------- the keyboard
    def _media(self, code: int) -> None:
        """A media key (play/pause, next, volume), the way the keyboard's own keys send it."""
        AppKit = appkit()
        for down in (True, False):
            flags = 0xA00 if down else 0xB00
            ev = AppKit.NSEvent.otherEventWithType_location_modifierFlags_timestamp_windowNumber_context_subtype_data1_data2_(
                14, (0, 0), flags, 0, 0, None, 8, (code << 16) | ((0xA if down else 0xB) << 8), -1
            )
            self._post(ev.CGEvent())
            time.sleep(0.02)

    def _key(self, code: int, down: bool, flags: int):
        ev = self._q.CGEventCreateKeyboardEvent(None, code, down)
        self._q.CGEventSetFlags(ev, flags)
        return ev

    def press(self, combo: str) -> None:
        """A key or chord: 'enter', 'cmd+s', 'ctrl+s' (pressed as Cmd+S), 'cmd+tab'."""
        self.check()
        self.guard_keys()
        keys = parse_keys(combo)
        if len(keys) == 1 and keys[0] >= MEDIA:
            self._media(keys[0] - MEDIA)
            return
        flags = 0
        for k in keys:  # modifiers first, each adding its flag, then the key
            flags |= FLAGS.get(k, 0)
            self._post(self._key(k, True, flags))
        for k in reversed(keys):
            flags &= ~FLAGS.get(k, 0)
            self._post(self._key(k, False, flags))
        time.sleep(0.05)

    def type(self, text: str, per_char_s: float = 0.014, settle_s: float = 0.16) -> None:
        """Type text: each character as a key event carrying that character (so any layout and
        any symbol works); newlines and tabs are Return and Tab, with a pause after."""
        Q = self._q
        for i, ch in enumerate(text):
            if i % 8 == 0:
                self.check()
                self.guard_keys()
            if ch in "\n\t":
                self.press("enter" if ch == "\n" else "tab")
                time.sleep(settle_s)
                continue
            for down in (True, False):
                ev = Q.CGEventCreateKeyboardEvent(None, 0, down)
                Q.CGEventKeyboardSetUnicodeString(ev, len(ch.encode("utf-16-le")) // 2, ch)
                self._post(ev)
            time.sleep(per_char_s)
