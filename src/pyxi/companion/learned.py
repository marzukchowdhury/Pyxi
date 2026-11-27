"""What Pyxi's hands learned about an app or a site, kept for the next task there.

Every task in an app started from nothing: where the pivot dialog hides its "existing sheet"
choice, that this menu only opens on a second click, that the search box is behind the
magnifier. Found once, by trial, and found again the next day. After a task the hands write
down at most two short notes about how the place works; before the next task there, they read
them.

The notes are about the app, never about the person: what they typed, their files, names and
numbers stay out (the writer is told so, and a note that looks like any of those is dropped
here whatever the writer did). They live in one small file in Pyxi's data folder, which the
person can read, edit or delete.

A note earns its place. One that was on hand when a task went well counts as having helped;
one the writer says was wrong, or that was on hand for two failures more than successes, goes.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

from pyxi.screen.secrets import looks_secret

log = logging.getLogger(__name__)

PER_PLACE = 6  # notes kept for one app or site
MAX_PLACES = 200
MAX_WORDS, MAX_CHARS = 32, 220
BROWSERS = {"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "vivaldi.exe", "arc.exe"}

# What a note about an app never needs, and a note about a person usually has.
_PERSONAL = re.compile(
    r"@|\bhttps?://|\bwww\.|\d{5,}|[A-Za-z]:\\|\\\\|/Users/|\b\S+\.(?:docx?|xlsx?|pptx?|pdf|csv|txt|png|jpe?g)\b",
    re.I,
)
_WORD = re.compile(r"[a-z0-9]+")


def place_of(process: str, url: str | None = None) -> str:
    """The key notes are filed under: a site when the window is a browser on one, else the app."""
    process = (process or "").strip().lower()
    if process in BROWSERS and url:
        host = (urlparse(url if "//" in url else f"https://{url}").hostname or "").lower()
        host = host.removeprefix("www.")
        if host and "." in host:
            return f"site:{host}"
    return f"app:{process}" if process else ""


_SMALL = {"the", "a", "an", "is", "are", "it", "its", "on", "in", "at", "of", "to", "from", "and", "or", "for", "with", "by", "be", "this", "that"}


def _words(text: str) -> set[str]:
    return set(_WORD.findall(text.lower())) - _SMALL


def alike(a: str, b: str) -> bool:
    """The same note again, give or take a word. Deliberately strict: two notes built alike
    about two different controls ("the X panel opens from the X menu") are two notes. Saying
    the same thing in quite other words is left to the writer, who is shown what is kept."""
    wa, wb = _words(a), _words(b)
    if not wa or not wb:
        return False
    return len(wa & wb) / len(wa | wb) >= 0.75


def fit_to_keep(text: str, task: str = "") -> str | None:
    """A note tidied for keeping, or None when it should not be kept: too long to be a note,
    or it carries something of the person's (an address, a file, a long number, a secret, or a
    phrase lifted from what they asked for in quotes)."""
    note = " ".join(str(text or "").split()).strip(" -*")
    if not note or len(note) > MAX_CHARS or len(note.split()) > MAX_WORDS or len(note.split()) < 3:
        return None
    if _PERSONAL.search(note) or looks_secret(note):
        return None
    for quoted in re.findall(r"[\"'“‘]([^\"'”’]{4,})[\"'”’]", task):
        if quoted.lower() in note.lower():
            return None  # their own words, typed or named in the request
    return note


class Learned:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self._data: dict[str, list[dict]] | None = None

    # ------------------------------------------------------------------ file
    def _load(self) -> dict[str, list[dict]]:
        if self._data is None:
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                self._data = {k: [n for n in v if isinstance(n, dict) and n.get("text")] for k, v in raw.items() if isinstance(v, list)}
            except (OSError, ValueError, AttributeError):
                self._data = {}
        return self._data

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, indent=1, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.path)
        except OSError as exc:
            log.warning("could not save what was learned: %s", exc)

    # ------------------------------------------------------------------ reading
    def recall(self, place: str) -> list[str]:
        """The notes for a place, the most proven first."""
        if not place:
            return []
        with self._lock:
            notes = sorted(self._load().get(place, []), key=lambda n: (n.get("failed", 0) - n.get("helped", 0), -n.get("seen", 1)))
            return [n["text"] for n in notes]

    # ------------------------------------------------------------------ writing
    def add(self, place: str, texts: list[str], task: str = "") -> list[str]:
        """Keep new notes for a place. One that says what a kept note says counts as that note
        seen again. Returns what was newly kept."""
        if not place:
            return []
        kept: list[str] = []
        with self._lock:
            data = self._load()
            notes = data.setdefault(place, [])
            for text in texts[:3]:
                note = fit_to_keep(text, task)
                if note is None:
                    continue
                same = next((n for n in notes if alike(n["text"], note)), None)
                if same is not None:
                    same["seen"] = same.get("seen", 1) + 1
                    same["when"] = time.time()
                    continue
                notes.append({"text": note, "seen": 1, "helped": 0, "failed": 0, "when": time.time()})
                kept.append(note)
            # Room for the new: the least proven, then the oldest, make way.
            notes.sort(key=lambda n: (n.get("helped", 0) - n.get("failed", 0), n.get("seen", 1), n.get("when", 0)), reverse=True)
            del notes[PER_PLACE:]
            if not notes:
                data.pop(place, None)
            if len(data) > MAX_PLACES:
                for old in sorted(data, key=lambda k: max((n.get("when", 0) for n in data[k]), default=0))[: len(data) - MAX_PLACES]:
                    del data[old]
            self._save()
        return [n for n in kept if any(n == m["text"] for m in self._load().get(place, []))]

    def used(self, place: str, shown: list[str], ok: bool) -> None:
        """The notes that were on hand for a task that went well, or did not."""
        if not place or not shown:
            return
        with self._lock:
            data = self._load()
            notes = data.get(place, [])
            for n in notes:
                if n["text"] in shown:
                    n["helped" if ok else "failed"] = n.get("helped" if ok else "failed", 0) + 1
            data[place] = [n for n in notes if n.get("failed", 0) < n.get("helped", 0) + 2]
            if not data[place]:
                del data[place]
            self._save()

    def drop(self, place: str, wrong: list[str]) -> int:
        """Notes found to be wrong."""
        if not place or not wrong:
            return 0
        with self._lock:
            data = self._load()
            notes = data.get(place, [])
            keep = [n for n in notes if not any(n["text"] == w or alike(n["text"], w) for w in wrong)]
            gone = len(notes) - len(keep)
            if gone:
                if keep:
                    data[place] = keep
                else:
                    data.pop(place, None)
                self._save()
            return gone

    def forget(self, place: str | None = None) -> None:
        """Everything learned about one place, or everything."""
        with self._lock:
            data = self._load()
            if place is None:
                data.clear()
            else:
                data.pop(place, None)
            self._save()

    def places(self) -> dict[str, int]:
        with self._lock:
            return {k: len(v) for k, v in self._load().items()}
