"""Stand-ins for the Apple frameworks (pyobjc's Quartz, AppKit, ApplicationServices, Vision,
Foundation, Security), so the Mac code paths in ``pyxi.macos`` run on any machine.

``FakeMac`` is a tiny desktop: windows in the Quartz window list, running apps, Accessibility
elements, screens, the mouse and the Keychain. ``install(monkeypatch)`` puts its modules in
``sys.modules`` for one test. They follow pyobjc's calling conventions: the AX copy calls return
``(error, value)``, AXValueGetValue returns ``(ok, struct)``, Security returns ``(status, data)``.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from types import ModuleType, SimpleNamespace
from typing import Any

# The real modules, imported here as this machine sees them, before any test pretends to be a
# Mac: a first import under a pretend Mac would swap in the Mac functions for the whole run.
import pyxi.agents.ditto
import pyxi.companion.actions
import pyxi.companion.operator
import pyxi.files_access
import pyxi.hands
import pyxi.media
import pyxi.screen.ocr
import pyxi.screen.uia
import pyxi.secretstore
import pyxi.windows  # noqa: F401

AX_ERROR = -25205  # kAXErrorAttributeUnsupported


def pt(x: float, y: float) -> SimpleNamespace:
    return SimpleNamespace(x=x, y=y)


def sz(w: float, h: float) -> SimpleNamespace:
    return SimpleNamespace(width=w, height=h)


def rect(x: float, y: float, w: float, h: float) -> SimpleNamespace:
    return SimpleNamespace(origin=pt(x, y), size=sz(w, h))


@dataclass
class AXValue:
    kind: str
    value: Any


class El:
    """An Accessibility element."""

    def __init__(self, pid: int = 0, role: str = "", **attrs: Any) -> None:
        self.pid = pid
        self.attrs: dict[str, Any] = {"AXRole": role, **attrs} if role else dict(attrs)
        self.actions: list[str] = []

    def place(self, x: int, y: int, w: int, h: int) -> El:
        self.attrs["AXPosition"] = AXValue("point", pt(x, y))
        self.attrs["AXSize"] = AXValue("size", sz(w, h))
        return self

    def kids(self, *children: El) -> El:
        self.attrs["AXChildren"] = list(children)
        return self


@dataclass
class App:
    pid: int
    name: str
    bundle: str
    exe: str = ""
    ax: El = field(default_factory=El)
    activated: int = 0


def window(number: int, pid: int, owner: str, name: str, x: int, y: int, w: int, h: int, *,
           layer: int = 0, onscreen: bool = True, alpha: float = 1.0) -> dict:  # fmt: skip
    return {
        "kCGWindowNumber": number,
        "kCGWindowOwnerPID": pid,
        "kCGWindowOwnerName": owner,
        "kCGWindowName": name,
        "kCGWindowBounds": {"X": x, "Y": y, "Width": w, "Height": h},
        "kCGWindowLayer": layer,
        "kCGWindowIsOnscreen": onscreen,
        "kCGWindowAlpha": alpha,
    }


class FakeMac:
    def __init__(self) -> None:
        self.windows: list[dict] = []  # front to back
        self.apps: dict[int, App] = {}
        self.screens: list[tuple[SimpleNamespace, SimpleNamespace]] = []  # (frame, visibleFrame)
        self.frontmost = 0
        self.cursor = (0.0, 0.0)
        self.trusted = True
        self.screen_ok = True
        self.focused: El | None = None
        self.at_point: El | None = None  # what Accessibility hit-tests at any point
        self.posted: list[dict] = []
        self.esc_down = False
        self.idle_s = 10.0
        self.keychain: dict[tuple[str, str], bytes] = {}
        self.ocr_results: list[Any] = []

    # ------------------------------------------------------------------ building the desktop
    def add_app(self, pid: int, name: str, bundle: str | None = None) -> App:
        bundle = bundle or f"/Applications/{name}.app"
        app = App(pid, name, bundle, f"{bundle}/Contents/MacOS/{name}", El(pid, "AXApplication"))
        app.ax.attrs["AXWindows"] = []
        self.apps[pid] = app
        return app

    def add_window(self, number: int, pid: int, name: str, x: int, y: int, w: int, h: int,
                   **kw: Any) -> El:  # fmt: skip
        owner = self.apps[pid].name if pid in self.apps else "?"
        self.windows.append(window(number, pid, owner, name, x, y, w, h, **kw))
        el = El(pid, "AXWindow", AXTitle=name).place(x, y, w, h)
        if pid in self.apps:
            self.apps[pid].ax.attrs["AXWindows"].append(el)
        return el

    # ------------------------------------------------------------------ the modules
    def modules(self) -> dict[str, ModuleType]:
        return {
            "Quartz": self._quartz(),
            "AppKit": self._appkit(),
            "ApplicationServices": self._ax(),
            "Vision": self._vision(),
            "Foundation": self._foundation(),
            "Security": self._security(),
        }

    def _quartz(self) -> ModuleType:
        m = ModuleType("Quartz")
        mac = self
        m.kCGWindowListOptionAll = 0
        m.kCGWindowListOptionOnScreenOnly = 1
        m.kCGWindowListOptionIncludingWindow = 8
        m.kCGWindowListExcludeDesktopElements = 16
        m.kCGNullWindowID = 0

        def copy(opts: int, wid: int):
            if opts & 8:
                return [w for w in mac.windows if w["kCGWindowNumber"] == wid]
            if opts & 1:
                return [w for w in mac.windows if w["kCGWindowIsOnscreen"]]
            return list(mac.windows)

        m.CGWindowListCopyWindowInfo = copy
        m.CGPointMake = pt
        m.CGSizeMake = sz
        m.CGEventCreate = lambda _src: "now"
        m.CGEventGetLocation = lambda _ev: pt(*mac.cursor)
        events = (
            "kCGEventMouseMoved", "kCGEventLeftMouseDragged", "kCGEventLeftMouseDown",
            "kCGEventLeftMouseUp", "kCGEventRightMouseDown", "kCGEventRightMouseUp",
        )  # fmt: skip
        for name in events:
            setattr(m, name, name)
        m.kCGMouseButtonLeft, m.kCGMouseButtonRight = 0, 1
        m.kCGMouseEventClickState = "clickState"
        m.kCGScrollEventUnitLine = "line"
        m.kCGHIDEventTap = "hid"
        m.kCGEventSourceStateHIDSystemState = "hidstate"
        m.kCGAnyInputEventType = 0xFFFFFFFF

        def mouse(_src, kind, point, button):
            return {"type": kind, "x": point.x, "y": point.y, "button": button, "fields": {}}

        m.CGEventCreateMouseEvent = mouse
        m.CGEventSetIntegerValueField = lambda ev, f, v: ev["fields"].__setitem__(f, v)
        m.CGEventCreateKeyboardEvent = lambda _src, code, down: {
            "type": "key", "code": code, "down": down, "flags": 0, "text": None,
        }  # fmt: skip
        m.CGEventSetFlags = lambda ev, flags: ev.__setitem__("flags", flags)
        m.CGEventKeyboardSetUnicodeString = lambda ev, n, s: ev.__setitem__("text", s)
        m.CGEventCreateScrollWheelEvent = lambda _src, unit, n, dy, dx: {
            "type": "scroll", "dy": dy, "dx": dx,
        }  # fmt: skip

        def post(_tap, ev):
            mac.posted.append(ev)
            if isinstance(ev, dict) and ev.get("type") in ("kCGEventMouseMoved", "kCGEventLeftMouseDragged"):
                mac.cursor = (ev["x"], ev["y"])

        m.CGEventPost = post
        m.CGEventSourceKeyState = lambda _state, code: mac.esc_down and code == 0x35
        m.CGEventSourceSecondsSinceLastEventType = lambda _state, _kind: mac.idle_s
        m.CGPreflightScreenCaptureAccess = lambda: mac.screen_ok
        m.CGImageSourceCreateWithData = lambda data, _opts: ("source", data)
        m.CGImageSourceCreateImageAtIndex = lambda src, _i, _opts: ("image", src)
        return m

    def _appkit(self) -> ModuleType:
        m = ModuleType("AppKit")
        mac = self

        class Screen:
            def __init__(self, frame, visible) -> None:
                self._f, self._v = frame, visible

            def frame(self):
                return self._f

            def visibleFrame(self):
                return self._v

        m.NSScreen = SimpleNamespace(screens=lambda: [Screen(f, v) for f, v in mac.screens])

        class Running:
            def __init__(self, app: App) -> None:
                self.app = app

            def executableURL(self):
                return SimpleNamespace(path=lambda: self.app.exe)

            def bundleURL(self):
                return SimpleNamespace(path=lambda: self.app.bundle)

            def localizedName(self):
                return self.app.name

            def processIdentifier(self):
                return self.app.pid

            def activateWithOptions_(self, _flags):
                self.app.activated += 1
                mac.frontmost = self.app.pid
                return True

        def by_pid(pid: int):
            return Running(mac.apps[pid]) if pid in mac.apps else None

        m.NSRunningApplication = SimpleNamespace(runningApplicationWithProcessIdentifier_=by_pid)
        m.NSWorkspace = SimpleNamespace(
            sharedWorkspace=lambda: SimpleNamespace(
                frontmostApplication=lambda: by_pid(mac.frontmost)
            )
        )
        m.NSApplicationActivateIgnoringOtherApps = 2

        def other_event(*args):
            data1 = args[7]
            return SimpleNamespace(CGEvent=lambda: {"type": "media", "data1": data1})

        m.NSEvent = SimpleNamespace(
            otherEventWithType_location_modifierFlags_timestamp_windowNumber_context_subtype_data1_data2_=other_event
        )
        return m

    def _ax(self) -> ModuleType:
        m = ModuleType("ApplicationServices")
        mac = self
        m.kAXValueCGPointType, m.kAXValueCGSizeType = 1, 2
        system = El(0, "AXSystemWide")

        def copy_attr(el, name, _out):
            if el is system and name == "AXFocusedUIElement":
                return (0, mac.focused) if mac.focused is not None else (AX_ERROR, None)
            if name in el.attrs and el.attrs[name] is not None:
                return 0, el.attrs[name]
            return AX_ERROR, None

        def set_attr(el, name, value):
            el.attrs[name] = value
            if name in ("AXPosition", "AXSize"):  # the window list follows, as on a real Mac
                for w in mac.windows:
                    if w["kCGWindowOwnerPID"] == el.pid and w["kCGWindowName"] == el.attrs.get("AXTitle"):
                        b = w["kCGWindowBounds"]
                        if name == "AXPosition":
                            b["X"], b["Y"] = value.value.x, value.value.y
                        else:
                            b["Width"], b["Height"] = value.value.width, value.value.height
            return 0

        m.AXUIElementCopyAttributeValue = copy_attr
        m.AXUIElementSetAttributeValue = set_attr
        m.AXUIElementPerformAction = lambda el, action: el.actions.append(action) or 0
        m.AXValueGetValue = lambda v, code, _out: (True, v.value) if isinstance(v, AXValue) else (False, None)
        m.AXValueCreate = lambda code, s: AXValue("point" if code == 1 else "size", s)
        m.AXUIElementCreateApplication = lambda pid: mac.apps[pid].ax if pid in mac.apps else El(pid)
        m.AXUIElementSetMessagingTimeout = lambda el, t: 0
        m.AXUIElementCreateSystemWide = lambda: system
        m.AXUIElementGetPid = lambda el, _out: (0, el.pid)
        m.AXIsProcessTrusted = lambda: mac.trusted

        def at(_sys, x, y, _out):
            return (0, mac.at_point) if mac.at_point is not None else (AX_ERROR, None)

        m.AXUIElementCopyElementAtPosition = at
        return m

    def _vision(self) -> ModuleType:
        m = ModuleType("Vision")
        mac = self

        class Request:
            def __init__(self) -> None:
                self.level = None

            @classmethod
            def alloc(cls):
                return cls()

            def init(self):
                return self

            def setRecognitionLevel_(self, level):
                self.level = level

            def setUsesLanguageCorrection_(self, _on):
                pass

            def results(self):
                return mac.ocr_results

        class Handler:
            @classmethod
            def alloc(cls):
                return cls()

            def initWithCGImage_options_(self, image, _opts):
                self.image = image
                return self

            def performRequests_error_(self, _reqs, _err):
                return True, None

        m.VNRecognizeTextRequest = Request
        m.VNImageRequestHandler = Handler
        return m

    def _foundation(self) -> ModuleType:
        m = ModuleType("Foundation")
        m.NSData = SimpleNamespace(dataWithBytes_length_=lambda b, n: bytes(b[:n]))
        return m

    def _security(self) -> ModuleType:
        m = ModuleType("Security")
        mac = self
        for name in (
            "kSecClass", "kSecClassGenericPassword", "kSecAttrService", "kSecAttrAccount",
            "kSecReturnData", "kSecMatchLimit", "kSecMatchLimitOne", "kSecAttrLabel",
            "kSecValueData", "kSecAttrAccessible", "kSecAttrAccessibleWhenUnlockedThisDeviceOnly",
        ):  # fmt: skip
            setattr(m, name, name)

        def find(query, _out):
            key = (query["kSecAttrService"], query["kSecAttrAccount"])
            return (0, mac.keychain[key]) if key in mac.keychain else (-25300, None)

        def add(item, _out):
            key = (item["kSecAttrService"], item["kSecAttrAccount"])
            if key in mac.keychain:
                return -25299, None  # errSecDuplicateItem
            mac.keychain[key] = bytes(item["kSecValueData"])
            return 0, None

        m.SecItemCopyMatching = find
        m.SecItemAdd = add
        return m


class OCRCandidate:
    """A Vision text candidate: its string and a box per character range."""

    def __init__(self, text: str, boxes: dict[tuple[int, int], SimpleNamespace]) -> None:
        self.text, self.boxes = text, boxes

    def string(self):
        return self.text

    def boundingBoxForRange_error_(self, rng, _err):
        box = self.boxes.get(tuple(rng))
        return (SimpleNamespace(boundingBox=lambda: box), None) if box else (None, None)


class OCRObservation:
    def __init__(self, candidate: OCRCandidate, box: SimpleNamespace) -> None:
        self.candidate, self.box = candidate, box

    def topCandidates_(self, _n):
        return [self.candidate]

    def boundingBox(self):
        return self.box


def install(monkeypatch, mac: FakeMac | None = None) -> FakeMac:
    """Put a fake Mac's frameworks in sys.modules for this test (and clear Pyxi's caches)."""
    mac = mac or FakeMac()
    for name, mod in mac.modules().items():
        monkeypatch.setitem(sys.modules, name, mod)
    from pyxi.macos import _ax, secretstore

    monkeypatch.setattr(_ax, "_exe_cache", {})
    monkeypatch.setattr(secretstore, "_key", None)
    monkeypatch.setattr(secretstore, "_unavailable", False)
    return mac
