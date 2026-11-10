from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from pyxi.config import PyxiSettings
from pyxi.hub import Hub
from pyxi.screen import ScreenContext, UIElement, WindowInfo


def fake_context(app: str = "app.exe", title: str = "Settings - App") -> ScreenContext:
    img = Image.new("RGB", (1600, 900), (240, 240, 250))
    els = [
        UIElement(1, "Settings", "Pane", (100, 100, 800, 600), 0),
        UIElement(2, "General", "Group", (120, 140, 760, 200), 1, 1),
        UIElement(3, "Dark mode", "CheckBox", (140, 170, 200, 30), 2, 2),
        UIElement(4, "Export", "Button", (700, 650, 120, 40), 1, 1),
        UIElement(5, "", "Edit", (140, 400, 300, 30), 2, 1, is_password=True),
    ]
    return ScreenContext(time.time(), WindowInfo(title, app, (0, 0, 1600, 900)), els, img)


class FakeScreen:
    """Stands in for ScreenReader so tests never capture the real desktop."""

    def __init__(self) -> None:
        self.paused = False
        self.ctx = fake_context()

    async def capture(self, pixels: bool = True) -> ScreenContext:
        ctx = self.ctx
        ctx.captured_at = time.time()
        return ctx

    def is_excluded(self, window: WindowInfo) -> str | None:
        return None


class Recorder:
    """A hub client that records every event."""

    def __init__(self, hub: Hub) -> None:
        self.events: list[dict[str, Any]] = []
        hub.connect(self.send)

    async def send(self, text: str) -> None:
        self.events.append(json.loads(text))

    def of(self, kind: str) -> list[dict[str, Any]]:
        return [e for e in self.events if e["type"] == kind]


@pytest.fixture
def settings(tmp_path: Path) -> PyxiSettings:
    return PyxiSettings(
        _env_file=None,
        data_dir=tmp_path / ".pyxi",
        voice={"tts": "client"},
        # tab_fallback: off here whatever the default is, so no test reaches for real windows.
        screen={"ambient_memory": False, "tab_fallback": False},
    )
