"""Logs without personal data: brain.log is for support, so what's written to it is scrubbed.

Email addresses, phone numbers, the user's name in paths (``C:\\Users\\<name>``) and anything that
looks like a key or token (screen/secrets.py's patterns, plus bearer tokens and Pyxi's own beta and
launch keys) are replaced as each line is written. :func:`install` wraps stdout and stderr, which
is where every log line, warning and traceback of the brain goes (the desktop app points them at
``.pyxi/brain.log``).
"""

from __future__ import annotations

import io
import os
import re
import sys
from typing import TextIO

from pyxi.screen import secrets

_EMAIL = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(
    r"(?<![\w.:/-])(?:\+\d{1,3}[ .-]?)?(?:\(\d{2,4}\)[ .-]?|\d{2,4}[ .-])\d{3,4}[ .-]\d{3,4}(?![\w.:/-])"
)
_HOME = re.compile(r"(?i)\b([a-z]:[\\/]+(?:users|documents and settings)[\\/]+)([^\\/\s\"'<>|:*?]+)")
_UNIX_HOME = re.compile(r"(?<![\w.])(/(?:home|Users)/)([^/\s\"'<>]+)")
_BEARER = re.compile(r"(?i)\b(bearer|token|key)([\s:=\"']+)([A-Za-z0-9._~+/=-]{16,})")


def scrub(text: str) -> str:
    if not text:
        return text
    text = secrets.PATTERN.sub("[hidden]", text)
    text = _BEARER.sub(r"\1\2[hidden]", text)
    launch = os.environ.get("PYXI_BRAIN_KEY", "")
    if len(launch) >= 16:
        text = text.replace(launch, "[hidden]")
    text = _EMAIL.sub("<email>", text)
    text = _PHONE.sub("<phone>", text)
    text = _HOME.sub(r"\1<user>", text)
    return _UNIX_HOME.sub(r"\1<user>", text)


class ScrubbingStream(io.TextIOBase):
    """A text stream that scrubs whole lines before passing them on."""

    def __init__(self, inner: TextIO) -> None:
        self.inner = inner
        self._buf = ""

    def write(self, s: str) -> int:
        self._buf += s
        if "\n" in self._buf:
            done, self._buf = self._buf.rsplit("\n", 1)
            self.inner.write(scrub(done) + "\n")
        return len(s)

    def flush(self) -> None:
        if self._buf:
            self.inner.write(scrub(self._buf))
            self._buf = ""
        self.inner.flush()

    def isatty(self) -> bool:
        return bool(getattr(self.inner, "isatty", lambda: False)())

    @property
    def encoding(self) -> str:  # type: ignore[override]
        return getattr(self.inner, "encoding", "utf-8")

    def fileno(self) -> int:
        return self.inner.fileno()

    def reconfigure(self, **kw: object) -> None:
        if hasattr(self.inner, "reconfigure"):
            self.inner.reconfigure(**kw)  # type: ignore[attr-defined]


def install() -> None:
    """Scrub everything the brain writes to stdout and stderr from now on."""
    if not isinstance(sys.stdout, ScrubbingStream) and sys.stdout is not None:
        sys.stdout = ScrubbingStream(sys.stdout)  # type: ignore[assignment]
    if not isinstance(sys.stderr, ScrubbingStream) and sys.stderr is not None:
        sys.stderr = ScrubbingStream(sys.stderr)  # type: ignore[assignment]
