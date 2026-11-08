"""Instant routes: the simplest requests, done in about a second without asking the model.

"Open a blank spreadsheet", "open Gmail", "pause the music" don't need 3-10 seconds of thinking
over a screenshot. Each pattern matches the WHOLE request, so anything more ("open Sheets and
make a pivot", "open the cheapest flight") still goes to the model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pyxi.companion.actions import APPS


@dataclass(frozen=True)
class Instant:
    action: str  # a quick_action: open_url, open_app or media
    value: str
    line: str  # what Pyxi says, after the acknowledgement


_LEAD = (
    r"^\W*(?:(?:hey|ok|okay|so|yeah|yes|um|uh|now|and|just|alright)[,.]?\s+)*(?:pyxi[,.]?\s+)?"
    r"(?:(?:can|could|would|will) you\s+|please\s+|i (?:want|need) you to\s+|go ahead and\s+)?"
    r"(?:please\s+|just\s+)?"
)
_TAIL = r"(?:\s+(?:for me|please|now|real quick|quickly))*(?:\s+(?:in|on) a new tab)?[\s.!?]*$"
_OPEN = r"(?:open(?: up)?|start|create|make|bring up|pull up|give me|launch|new)"

_BLANK = {
    "spreadsheet": "https://sheets.new",
    "sheet": "https://sheets.new",
    "google sheet": "https://sheets.new",
    "doc": "https://docs.new",
    "document": "https://docs.new",
    "google doc": "https://docs.new",
    "slides": "https://slides.new",
    "slide deck": "https://slides.new",
    "presentation": "https://slides.new",
    "form": "https://forms.new",
    "google form": "https://forms.new",
}
_SITES = {
    "google sheets": "https://docs.google.com/spreadsheets",
    "sheets": "https://docs.google.com/spreadsheets",
    "google docs": "https://docs.google.com/document",
    "google slides": "https://docs.google.com/presentation",
    "google drive": "https://drive.google.com",
    "drive": "https://drive.google.com",
    "gmail": "https://mail.google.com",
    "my email": "https://mail.google.com",
    "my inbox": "https://mail.google.com",
    "google calendar": "https://calendar.google.com",
    "my calendar": "https://calendar.google.com",
    "google maps": "https://maps.google.com",
    "maps": "https://maps.google.com",
    "youtube": "https://www.youtube.com",
    "youtube music": "https://music.youtube.com",
    "google": "https://www.google.com",
    "facebook": "https://www.facebook.com",
    "instagram": "https://www.instagram.com",
    "linkedin": "https://www.linkedin.com",
    "netflix": "https://www.netflix.com",
    "chatgpt": "https://chatgpt.com",
    "whatsapp": "https://web.whatsapp.com",
    "amazon": "https://www.amazon.com",
    "reddit": "https://www.reddit.com",
    "github": "https://github.com",
    "x": "https://x.com",
    "twitter": "https://x.com",
}
_MEDIA = {
    "pause": "playpause",
    "pause it": "playpause",
    "pause the music": "playpause",
    "pause the song": "playpause",
    "pause the video": "playpause",
    "stop the music": "playpause",
    "resume": "playpause",
    "resume the music": "playpause",
    "play it again": "playpause",
    "unpause": "playpause",
    "next": "next",
    "next song": "next",
    "next track": "next",
    "skip": "next",
    "skip it": "next",
    "skip this song": "next",
    "skip this": "next",
    "previous song": "previous",
    "previous track": "previous",
    "last song": "previous",
    "go back a song": "previous",
}


def _alts(words: list[str]) -> str:
    return "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))


_BLANK_RE = re.compile(
    _LEAD
    + rf"(?:{_OPEN})\s+(?:me\s+)?(?:up\s+)?(?:a\s+|an\s+|the\s+)?(?:new|blank|empty|fresh|bank|plank)"
    + rf"(?:\s+(?:new|blank|empty))?\s+({_alts(list(_BLANK))})s?"
    + _TAIL,
    re.I,
)
_SITE_RE = re.compile(
    _LEAD
    + rf"(?:open(?: up)?|go to|pull up|bring up|launch)\s+(?:up\s+)?({_alts(list(_SITES))})"
    + _TAIL,
    re.I,
)
_APP_RE = re.compile(
    _LEAD
    + r"(?:open(?: up)?|start|launch|bring up|pull up)\s+(?:up\s+)?(?:the\s+)?"
    + rf"({_alts([k for k in APPS if k not in ('apps', 'files')])})(?:\s+app)?"
    + _TAIL,
    re.I,
)
_MEDIA_RE = re.compile(_LEAD + rf"({_alts(list(_MEDIA))})" + r"[\s.!?]*$", re.I)


def match(text: str) -> Instant | None:
    """The instant route for this request, or None when it needs the model."""
    t = " ".join(text.split())
    if not t or len(t) > 80:
        return None
    if m := _BLANK_RE.match(t):
        what = m.group(1).lower()
        name = {
            "https://sheets.new": "spreadsheet",
            "https://docs.new": "doc",
            "https://slides.new": "slide deck",
            "https://forms.new": "form",
        }[_BLANK[what]]
        return Instant("open_url", _BLANK[what], f"a blank {name}, coming up.")
    if m := _SITE_RE.match(t):
        what = m.group(1).lower()
        return Instant("open_url", _SITES[what], "")
    if m := _APP_RE.match(t):
        return Instant("open_app", m.group(1).lower(), "")
    if m := _MEDIA_RE.match(t):
        return Instant("media", _MEDIA[m.group(1).lower()], "")
    return None
