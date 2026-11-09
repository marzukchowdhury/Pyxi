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
    line: str  # what Pyxi says ("Notepad, coming up."); empty for media keys


_LEAD = (
    r"^\W*(?:(?:hey|ok|okay|so|yeah|yes|um|uh|now|and|just|alright)[,.]?\s+)*(?:(?:pyxi|pixie|pixi|pixy|pixey|pixig)[,.]?\s+)?"
    r"(?:(?:can|could|would|will) you\s+|please\s+|i (?:want|need) you to\s+|go ahead and\s+)?"
    r"(?:please\s+|just\s+)?"
)
_TAIL = r"(?:\s+(?:for me|please|now|real quick|quickly))*(?:\s+(?:in|on) a new (?:tab|chat|tap|tub|tad|job))?[\s.!?]*$"
_OPEN = r"(?:open(?: up)?|start|create|make|bring up|pull up|give me|launch|new)"

_BLANK = {
    "spreadsheet": "https://sheets.new",
    "sheet": "https://sheets.new",
    "google sheet": "https://sheets.new",
    "doc": "https://docs.new",
    "dot": "https://docs.new",  # what speech recognition makes of "a new doc"
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
    + rf"(?:{_OPEN})\s+(?:me\s+)?(?:up\s+)?(?:a\s+|an\s+|the\s+)?(?:[a-z]+\s+)?(?:new|blank|empty|fresh|bank|plank)"
    + rf"(?:\s+(?:new|blank|empty))?\s+({_alts(list(_BLANK))})s?"
    + _TAIL,
    re.I,
)
_SITE_RE = re.compile(
    _LEAD
    + rf"(?:open(?: up)?|go to|pull up|bring up|launch)\s+(?:up\s+)?"
    + r"(?:(?:a\s+)?new\s+(?:tab|window)\s+(?:with|to|for|on|of)\s+)?"  # "open a new tab with Google Maps"
    + rf"({_alts(list(_SITES))})"
    + _TAIL,
    re.I,
)
_APP_RE = re.compile(
    _LEAD
    + r"(?:open(?: up)?|start|launch|bring up|pull up|show me)\s+(?:up\s+)?(?:the\s+|my\s+)?"
    + rf"({_alts([k for k in APPS if k not in ('apps', 'files')])})(?:\s+(?:app|folder))?"
    + _TAIL,
    re.I,
)
_MEDIA_RE = re.compile(_LEAD + rf"({_alts(list(_MEDIA))})" + r"[\s.!?]*$", re.I)


# What speech recognition makes of "Gmail" after "open": nobody asks Pyxi to open OpenGL.
_GMAIL_MISHEARD = re.compile(
    r"^(\W*(?:(?:no,?\s+)?i said\s+)?)(?:open\s+)?(?:open ?gl|open g ?means?|open g ?mail|open gee ?mail)\b",
    re.I,
)


_PRETTY = {
    "gmail": "Gmail", "my email": "your inbox", "my inbox": "your inbox", "youtube": "YouTube",
    "youtube music": "YouTube Music", "chatgpt": "ChatGPT", "whatsapp": "WhatsApp",
    "linkedin": "LinkedIn", "github": "GitHub", "x": "X", "twitter": "X", "my calendar": "your calendar",
    "notepad": "Notepad", "calculator": "the calculator",
}
_OPENING = ["{0}, coming up.", "Opening {0}.", "Here comes {0}.", "{0}, on its way."]


def opening_line(what: str, pick=None) -> str:
    """'Notepad, coming up.' A short line that names what's opening, worded a little differently
    each time, instead of a stock 'On it'."""
    import random

    name = _PRETTY.get(what) or (what if what[:1].isupper() else what.title() if len(what) > 3 else what.upper())
    if name.startswith(("the ", "your ")):
        templates = [t for t in _OPENING if not t.startswith("{0}")]
    else:
        templates = _OPENING
    line = (pick or random.choice)(templates).format(name)
    return line[0].upper() + line[1:]


# "Search YouTube for lo-fi study music", "google cheap flights to Toronto": a search page.
_SEARCHES = {
    "youtube": "https://www.youtube.com/results?search_query={q}",
    "google": "https://www.google.com/search?q={q}",
    "amazon": "https://www.amazon.ca/s?k={q}",
    "maps": "https://www.google.com/maps/search/{q}",
    "google maps": "https://www.google.com/maps/search/{q}",
    "wikipedia": "https://en.wikipedia.org/w/index.php?search={q}",
    "reddit": "https://www.reddit.com/search/?q={q}",
}
_SEARCH_RE = re.compile(
    _LEAD
    + r"(?:(?:search|look up|find)\s+(?:on\s+)?(?P<site>youtube|google maps|google|amazon|maps|wikipedia|reddit)\s+(?:for\s+)?(?P<q>.{2,80}?)"
    + r"|(?:search|look up|find)\s+(?P<q2>.{2,80}?)\s+on\s+(?P<site2>youtube|google maps|google|amazon|maps|wikipedia|reddit)"
    + r"|google\s+(?P<q3>.{2,80}?))"
    + r"(?:\s+(?:for me|please))?[\s.!?]*$",
    re.I,
)


_CLEAR = re.compile(
    _LEAD
    + r"(?:close|hide|dismiss|clear|remove|get rid of|put away|take away)\s+"
    + r"(?:(?:the|that|this|those|these|all|everything|it|of it)\s*)*"
    + r"(?:3d|three[- ]d)?\s*(?:model|models|drawings?|cards?|notes?|thing|stuff|that|it|screen|bicycle|heart|engine)?"
    + r"(?:\s+(?:on|off) (?:the |my )?screen)?(?:\s+(?:please|now))?[\s.!?]*$",
    re.I,
)


def match(text: str) -> Instant | None:
    """The instant route for this request, or None when it needs the model."""
    hit = _match(text)
    if hit is None:
        # "I need to check my email, open Gmail": a plain command after a reason. Only the last
        # clause, and only an open or search command (never "..., and then do X").
        parts = re.split(r"[,.;:]\s+|\s+so\s+", " ".join(text.split()))
        last = parts[-1] if len(parts) > 1 else ""
        if last and re.match(r"(?i)^\W*(?:please\s+|just\s+|can you\s+)?(?:open|go to|pull up|bring up|launch|start|search|google)\b", last):
            hit = _match(last)
    return hit


def _match(text: str) -> Instant | None:
    t = _GMAIL_MISHEARD.sub(r"\1open gmail", " ".join(text.split()))
    t = re.sub(r"(?i)^\W*(?:no,?\s+)?i said\s+", "", t)  # "No, I said open Gmail"
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
        return Instant("open_url", _BLANK[what], f"A blank {name}, coming up.")
    if m := _SITE_RE.match(t):
        what = m.group(1).lower()
        return Instant("open_url", _SITES[what], opening_line(what))
    if m := _APP_RE.match(t):
        what = m.group(1).lower()
        return Instant("open_app", what, opening_line(what))
    if m := _SEARCH_RE.match(t):
        from urllib.parse import quote_plus

        site = (m.group("site") or m.group("site2") or "google").lower()
        q = (m.group("q") or m.group("q2") or m.group("q3") or "").strip()
        if q:
            return Instant("open_url", _SEARCHES[site].format(q=quote_plus(q)), f"Searching {site.title().replace('Youtube', 'YouTube')} for {q}.")
    if _CLEAR.match(t) and re.search(r"(?i)\b(close|hide|dismiss|clear|remove|rid|away)\b", t):
        return Instant("clear", "all", "Cleared away.")
    if m := _MEDIA_RE.match(t):
        return Instant("media", _MEDIA[m.group(1).lower()], "")
    return None


# "Play Blinding Lights by The Weeknd", "can you play some lofi on Spotify": straight to the
# player. Anything with more to it ("play it a bit quieter and then...") goes to the model.
_PLAY = re.compile(
    _LEAD
    + r"play\s+(?:me\s+)?(?P<q>.+?)(?:\s+(?:on|in)\s+(?:my\s+)?(?P<app>youtube music|youtube|spotify)(?:\s+app)?)?"
    + r"(?:\s+(?:for me|please|now))*[\s.!?]*$",
    re.I,
)
_NOT_A_SONG = re.compile(
    r"^(?:it|it again|that|this|the music|music|something|again|a game|the video|the next one)$"
    r"|\b(?:and|then|volume|quieter|louder|lower|higher|game|games|with me|with us|along)\b"
    # "play the song on my middle screen", "just click play": something already on screen
    r"|\b(?:screen|monitor|window|tab|page|click|the song|this song|that song|this video|that video)\b",
    re.I,
)


def play_request(text: str) -> tuple[str, str] | None:
    """(what to play, app) for a plain "play X", else None."""
    m = _PLAY.match(" ".join(text.split()))
    if not m:
        return None
    q = m.group("q").strip(" .,!?")
    if not q or _NOT_A_SONG.search(q):
        return None
    app = (m.group("app") or "").lower().replace(" ", "_") or "youtube_music"
    return q, app


# "Stop.", "okay stop stop", "cancel that", "never mind": the same as the Stop everything hotkey,
# at once. A longer sentence ("stop the music", "stop sharing my screen") goes elsewhere.
_STOP = re.compile(
    r"^\W*(?:(?:ok|okay|no|hey|pyxi|wait|hold on|please)[,.!]?\s+)*"
    r"(?:stop(?:[,.!]?\s+(?:it|that|now|everything|please|stop|right now))*"
    r"|cancel(?:\s+(?:it|that|everything))?|never\s*mind|that'?s enough|enough)"
    r"(?:[,.!]?\s+(?:please|pyxi))?[\s.!]*$",
    re.I,
)


def is_stop(text: str) -> bool:
    return bool(_STOP.match(" ".join(text.split())))
