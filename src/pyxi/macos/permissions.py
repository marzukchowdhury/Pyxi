"""What Pyxi has been allowed to do on this Mac, and where the user can allow the rest.

Pyxi only ever reads these. Granting is the user's own click in System Settings; the desktop
app's first-run card links to the right page (``SETTINGS``). Microphone access is asked for by
the desktop app itself (macOS shows its own prompt), so it isn't checked here.
"""

from __future__ import annotations

import importlib
import sys

_PRIVACY = "x-apple.systempreferences:com.apple.preference.security?"
SETTINGS = {
    "accessibility": _PRIVACY + "Privacy_Accessibility",
    "screen": _PRIVACY + "Privacy_ScreenCapture",
    "microphone": _PRIVACY + "Privacy_Microphone",
    "automation": _PRIVACY + "Privacy_Automation",
}


def accessibility() -> bool:
    """Reading other apps' controls, moving windows, and Pyxi's hands."""
    from pyxi.macos._ax import trusted

    return trusted()


def screen_recording() -> bool:
    """Screenshots of other apps and their window titles. Checked without prompting."""
    try:
        Q = importlib.import_module("Quartz")
        return bool(Q.CGPreflightScreenCaptureAccess())
    except Exception:
        return False


def status() -> dict[str, bool]:
    if sys.platform != "darwin":
        return {}
    return {"accessibility": accessibility(), "screen": screen_recording()}
