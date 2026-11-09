"""Pyxi on a Mac: the macOS side of every module that talks to the operating system.

Each Windows module (``pyxi.windows``, ``pyxi.screen.uia``, ``pyxi.hands``, ...) stays the Windows
implementation. At its very end it has a short block that, on a Mac only, swaps in the functions
from the matching module here, with the same names and the same arguments. Callers never change,
and on Windows nothing here is ever imported.

Coordinates on a Mac are points (what AppKit, Accessibility and Quartz events all use, with the
origin at the top left of the main screen), not Retina pixels. Screenshots are scaled down to
points too, so one number means the same spot everywhere, as physical pixels do on Windows.

The Apple frameworks come from pyobjc (``Quartz``, ``AppKit``, ``ApplicationServices``,
``Vision``, ``Security``). They're imported lazily, through the helpers below, so the tests can put
stand-ins in ``sys.modules`` and run on Windows.
"""

from __future__ import annotations

import sys
from types import ModuleType

IS_MAC = sys.platform == "darwin"


def _load(name: str) -> ModuleType:
    import importlib

    return importlib.import_module(name)


def quartz() -> ModuleType:
    """CoreGraphics: the window list, events, display info."""
    return _load("Quartz")


def appkit() -> ModuleType:
    """NSWorkspace, NSScreen, NSRunningApplication."""
    return _load("AppKit")


def axapi() -> ModuleType:
    """Accessibility (AXUIElement...), part of ApplicationServices."""
    return _load("ApplicationServices")
