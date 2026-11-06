"""The ambient tracker that quietly builds the Memory Palace.

Every ``focus_poll_s`` it reads the focused element (cheap). When the foreground window
changes, or every ``snapshot_interval_s``, it records a full spatial snapshot (UI metadata,
plus a thumbnail only if the user opted in). Private apps and the pause switch are always
respected: nothing about them is recorded at all.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from pyxi.config import ScreenConfig
from pyxi.memory import SpatialMemory
from pyxi.palace.activity import ActivityLog
from pyxi.screen import ScreenReader, WindowInfo, uia

log = logging.getLogger(__name__)


class PalaceTracker:
    def __init__(
        self,
        config: ScreenConfig,
        screen: ScreenReader,
        spatial: SpatialMemory,
        activity: ActivityLog,
        *,
        is_busy: Callable[[], bool],
        on_window: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        self.config, self.screen, self.spatial, self.activity = config, screen, spatial, activity
        self.is_busy = is_busy
        self.on_window = on_window
        self.last_snapshot: int | None = None
        self._last_window: tuple[str, str] | None = None
        self._last_full = 0.0

    async def run(self) -> None:
        while True:
            await asyncio.sleep(self.config.focus_poll_s)
            if not self.config.ambient_memory or self.screen.paused or self.is_busy():
                continue
            try:
                await self.tick()
            except Exception as exc:  # the tracker must never die
                log.debug("palace tick failed: %s", exc)

    async def tick(self) -> None:
        focus = await asyncio.to_thread(uia.read_focus)
        if focus is None:
            return
        window = WindowInfo(focus.window_title, focus.process)
        if self.screen.is_excluded(window):
            return
        key = (window.app, window.title)
        now = time.time()
        if key != self._last_window or now - self._last_full > self.config.snapshot_interval_s:
            ctx = await self.screen.capture(pixels=self.config.keep_screenshots)
            if not ctx.excluded:
                sid = self.spatial.record(ctx)
                self.last_snapshot = sid or self.last_snapshot
                self._last_full = now
                if key != self._last_window:
                    self.activity.window_changed(
                        ctx.window.app, ctx.window.title, self.last_snapshot
                    )
                    self._last_window = key
                    if self.on_window:
                        await self.on_window(ctx.window.app)
        self.activity.focus_changed(
            window.app, window.title, focus.name, focus.role, focus.rect, self.last_snapshot
        )
