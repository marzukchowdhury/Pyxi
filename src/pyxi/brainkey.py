"""The brain's launch key: only Pyxi's own windows and tools may talk to it.

The brain listens on 127.0.0.1, where any program on the PC (and any other Windows user's
program) can connect. It sees the screen, drives the mouse and answers its own Allow cards, so
every request must carry a secret that only the desktop app knows.

- The desktop app makes a random key at each launch and starts the brain with it in
  ``PYXI_BRAIN_KEY``. Its windows get it through the preload and send it as ``?key=`` (WebSocket,
  images) or the ``X-Pyxi-Key`` header.
- A brain started by hand (``pyxi serve``) with no key makes its own and writes it to
  ``<data dir>/brain.key``, readable only by this Windows user, so dev scripts and the desktop
  app in dev mode can find it. :func:`client_key` reads it for them.
"""

from __future__ import annotations

import hmac
import os
import secrets
from pathlib import Path

ENV = "PYXI_BRAIN_KEY"
HEADER = "x-pyxi-key"
QUERY = "key"
FILE = "brain.key"


def resolve(data_dir: Path) -> str:
    """The key this brain demands: the app's (from the environment), or a new one on disk."""
    given = os.environ.get(ENV, "").strip()
    if given:
        return given
    key = secrets.token_urlsafe(32)
    path = Path(data_dir) / FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(key, encoding="utf-8")
    try:  # owner only (on Windows a user's AppData is already private to them)
        path.chmod(0o600)
    except OSError:
        pass
    return key


def client_key(data_dir: Path | str | None = None) -> str:
    """For local tools: the key of the brain they're about to talk to (``PYXI_BRAIN_KEY``, else
    the file a brain started by hand wrote in its data folder)."""
    given = os.environ.get(ENV, "").strip()
    if given:
        return given
    folder = Path(data_dir or os.environ.get("PYXI_DATA_DIR") or ".pyxi")
    try:
        return (folder / FILE).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def matches(expected: str, given: str | None) -> bool:
    return bool(expected) and bool(given) and hmac.compare_digest(expected, str(given))
