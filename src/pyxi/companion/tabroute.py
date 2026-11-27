"""Email, calendar and Google files without the Google connection: in their own browser tab.

Pyxi's connection to Google (connectors/google.py) asks Google for permissions that Google
only opens to the public once it has verified the app. Until then most people cannot connect
it, and "connect Google in Settings" is a dead end for them. Their own browser is already
signed in to Gmail, though, and Pyxi can work in a tab of it through the extension
(companion/tabwork.py), with no permission from Google at all: it reads and clicks the page
the way they would.

So when a request needs Gmail, Calendar, Sheets, Docs or Slides and Google is not connected,
the model is told to do it in their tab instead (``use_tab``), or, when Pyxi is not in their
browser yet, to say what the two ways forward are. It is slower than the direct connection,
and every send still asks for their OK in the tab's own card.
"""

from __future__ import annotations

import re

# What the request is about, most specific first, and where that lives.
_PLACES: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"(?i)\b(calendar|meeting|appointment|event|invite|schedule|reschedul\w*|availab\w+)\b"), "Google Calendar", "https://calendar.google.com/"),
    (re.compile(r"(?i)\b(google sheets?|spreadsheets?|sheets?)\b"), "Google Sheets", "https://docs.google.com/spreadsheets/"),
    (re.compile(r"(?i)\b(google slides?|slides?|presentation|deck)\b"), "Google Slides", "https://docs.google.com/presentation/"),
    (re.compile(r"(?i)\b(google docs?|docs?|document)\b"), "Google Docs", "https://docs.google.com/document/"),
    (re.compile(r"(?i)\b(e-?mails?|mails?|gmail|inbox|message|reply|repl(?:y|ies|ied)|forward\w*|draft\w*|unsubscrib\w*|attachment|sent (?:me|you|us)|send (?:it|this|that|them|him|her) )\b"), "Gmail", "https://mail.google.com/mail/"),
]

# How to get about in each, for the model that will work the page (short: the page itself
# is in front of it at every step).
_HOW = {
    "Gmail": "In Gmail, find mail with the search box at the top (type the words and press Enter) and open one by clicking its subject. Reply opens a box under the message; Compose opens a new one.",
    "Google Calendar": "In Google Calendar, Create (top left) starts a new event; clicking a day or an event opens it.",
}


def place_for(task: str) -> tuple[str, str] | None:
    """(what it is called, its address) for a task that is about one of Google's apps."""
    for pattern, name, url in _PLACES:
        if pattern.search(task or ""):
            return name, url
    return None


def note(task: str, in_browser: bool) -> str | None:
    """What the model is told in place of starting an errand, when Google is not connected and
    the task is about one of Google's apps. None for any other task."""
    place = place_for(task)
    if place is None:
        return None
    name, url = place
    if in_browser:
        how = _HOW.get(name, "")
        return (
            f"errand: not started, because Google isn't connected to Pyxi. Do it in their own "
            f"browser instead, now: call use_tab with url \"{url}\" and this whole task. "
            f"{how} Reading and finding are fine to do straight away. Nothing is sent, deleted "
            "or accepted without their OK: the tab asks them itself at that moment, so write "
            "the task to stop and show them the text before Send. Say one short line first: "
            f"that you're doing it in their {name} tab. Don't tell them to connect Google "
            "before you start. When it's done you may add, once, that connecting Google in "
            "Settings makes this instant."
        ).replace("  ", " ")
    return (
        "errand: not started. Google isn't connected to Pyxi, and Pyxi isn't in their browser "
        f"yet, so there is no way into their {name} from here. Say so in two short sentences "
        f"and give both ways forward: Pyxi can do this in their own {name} tab once it is in "
        'their browser (they can say "connect my browser", or use Settings, Connect your '
        "browser), or instantly once Google is connected in Settings, Connected accounts. "
        "Claim nothing was done."
    )
