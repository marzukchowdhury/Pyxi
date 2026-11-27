"""Windows 11's own Live Captions as a source of the words.

Windows 11 (22H2 and later) has Live Captions built in: speech recognition on the PC, free,
word by word. Its window holds the text in one element, which UI Automation can read, so when
it is already there Pyxi can show (and translate) those words instead of running its own
speech model.

What was found on a real PC (Windows 11 Pro, build 26300), and what follows from it:

* It must be **set up once by the person**: the first start shows "Welcome to live
  captions... Do you want to continue and set up live captions?", which agrees to
  Microsoft's terms and downloads a language pack. Pyxi never presses that for them. A PC
  where it was never set up cannot use this source, and Pyxi's own captions are used.
* It hears **one language**, the one chosen in its own settings. Another language, or
  "work out the language", is Pyxi's own model's job.
* It puts **its own window** on screen. Pyxi does not start it by itself ("auto" uses it only
  when they already have it running); when they chose it in the settings Pyxi starts it and
  minimises the window it started, and closes only a window it started.

Reading its text while it runs could not be checked on that PC (it was never set up there),
so :class:`WindowsCaptions` is watched by its caller: if no words arrive while sound is
playing, the caller goes back to Pyxi's own captions.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger(__name__)

WINDOW_CLASS = "LiveCaptionsDesktopWindow"
TEXT_ID = "CaptionsTextBlock"
SETUP_IDS = ("SetupToContinueText", "AgreeButton")
_END = re.compile(r"(?<=[.!?。！？…])\s+")

NOT_SET_UP = (
    "Windows Live Captions isn't set up on this PC yet, so I'm using my own captions. To use "
    "Windows' own, turn Live captions on once yourself (Windows key + Ctrl + L) and say yes to "
    "its setup."
)


def installed() -> bool:
    if sys.platform != "win32":
        return False
    root = os.environ.get("SYSTEMROOT", r"C:\Windows")
    return (Path(root) / "System32" / "LiveCaptions.exe").is_file()


def window() -> int:
    """Live Captions' window, 0 when it isn't running."""
    if sys.platform != "win32":
        return 0
    import ctypes

    return int(ctypes.windll.user32.FindWindowW(WINDOW_CLASS, None) or 0)


class Splitter:
    """Live Captions shows one block of text that grows, scrolls and corrects itself. This
    turns what it reads, time after time, into lines: (id, text, final). The sentence being
    said keeps its id until it ends; a sentence that stops changing is final too."""

    def __init__(self, settle_s: float = 1.5) -> None:
        self.settle_s = settle_s
        self._done = ""  # the last sentence handed out as final
        self._open = ""  # the sentence being said, as last handed out
        self._changed = 0.0
        self._id = 0

    def feed(self, text: str, now: float) -> list[tuple[int, str, bool]]:
        text = " ".join(str(text or "").split())
        rest = text
        if self._done:
            at = text.rfind(self._done)
            if at >= 0:
                rest = text[at + len(self._done) :].strip()
            else:
                # It scrolled away or was rewritten: only its last sentence is news.
                rest = _END.split(text)[-1] if text else ""
        out: list[tuple[int, str, bool]] = []
        sentences = [s.strip() for s in _END.split(rest) if s.strip()]
        for sentence in sentences[:-1]:
            out.append((self._id, sentence, True))
            self._finish(sentence)
        last = sentences[-1] if sentences else ""
        if last != self._open:
            self._open, self._changed = last, now
            if last:
                out.append((self._id, last, False))
        elif last and now - self._changed >= self.settle_s:
            out.append((self._id, last, True))
            self._finish(last)
        return out

    def _finish(self, sentence: str) -> None:
        self._done, self._open = sentence, ""
        self._id += 1


class WindowsCaptions:
    """Reads Live Captions' words on a thread of its own. ``on_line(id, text, final)`` is
    called on that thread."""

    def __init__(self, on_line: Callable[[int, str, bool], None], poll_s: float = 0.08) -> None:
        self.on_line = on_line
        self.poll_s = poll_s
        self.problem = ""
        self.read_at = 0.0  # monotonic time new words last arrived
        self._started_it = False
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self, launch: bool, wait_s: float = 8.0) -> bool:
        """``launch`` False: only when they already have it running. Returns whether its text
        can be read (``problem`` says why not, in plain words, when it should be told)."""
        if not installed():
            return False
        if not window() and not launch:
            return False
        self._thread = threading.Thread(
            target=self._run, args=(launch,), name="pyxi-wincaptions", daemon=True
        )
        self._thread.start()
        self._ready.wait(wait_s + 2)
        return self._ready.is_set() and not self.problem and not self._stop.is_set()

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(3.0)

    # ------------------------------------------------------------------ the thread
    def _run(self, launch: bool) -> None:
        import ctypes

        import uiautomation as auto

        user32 = ctypes.windll.user32
        hwnd = 0
        try:
            with auto.UIAutomationInitializerInThread():
                hwnd = window()
                if not hwnd and launch:
                    subprocess.Popen(["LiveCaptions.exe"])  # Windows' own, from System32
                    self._started_it = True
                    for _ in range(80):
                        hwnd = window()
                        if hwnd or self._stop.is_set():
                            break
                        time.sleep(0.1)
                if not hwnd:
                    self.problem = "no window"
                    return
                box = None
                for _ in range(40):  # up to 8 s: its window fills in a moment after it opens
                    if self._stop.is_set():
                        return
                    try:
                        win = auto.ControlFromHandle(hwnd)
                        if any(
                            win.Control(AutomationId=i, searchDepth=6).Exists(0, 0)
                            for i in SETUP_IDS
                        ):
                            self.problem = NOT_SET_UP  # never agreed to on their behalf
                            return
                        found = win.TextControl(AutomationId=TEXT_ID, searchDepth=8)
                        if found.Exists(0, 0):
                            box = found
                            break
                    except Exception:
                        pass
                    time.sleep(0.2)
                if box is None:
                    self.problem = "no text"
                    return
                if self._started_it:
                    user32.ShowWindow(hwnd, 6)  # minimised: Pyxi's own bar shows the words
                self._ready.set()
                splitter, misses = Splitter(), 0
                while not self._stop.is_set():
                    try:
                        text = box.Name
                        misses = 0
                    except Exception:
                        misses += 1
                        if misses > 25 or not window():  # they closed it
                            self.problem = "closed"
                            return
                        time.sleep(self.poll_s)
                        continue
                    for line in splitter.feed(text, time.monotonic()):
                        self.read_at = time.monotonic()
                        try:
                            self.on_line(*line)
                        except Exception:
                            log.exception("a caption line failed")
                    time.sleep(self.poll_s)
        except Exception as exc:
            log.warning("reading Windows Live Captions failed: %s", exc)
            self.problem = self.problem or "failed"
        finally:
            if self._started_it and hwnd:
                try:
                    user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE: Pyxi started it
                except Exception:
                    pass
            self._ready.set()
