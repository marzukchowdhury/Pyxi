"""Alarms and timers, understood straight from the words: no model, no mouse, about a second.

"Set an alarm for 7am", "wake me up at 6:30 tomorrow", "set a timer for 10 minutes". An alarm
is one of Pyxi's routines (kind "alarm"), so it survives a restart; a timer is a short wait in
the brain. Both ring through Pyxi: a chime, a spoken line and a card. Like Windows' own Clock
alarms, they ring only while the PC is awake.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

_LEAD = (
    r"^\W*(?:(?:hey|ok|okay|so|um|uh|now|and|just|alright|please)[,.]?\s+)*(?:pyxi[,.]?\s+)?"
    r"(?:(?:can|could|would|will) you\s+|please\s+|i (?:want|need) you to\s+|go ahead and\s+)?"
    r"(?:please\s+|just\s+)?"
)
_TIME = r"(?P<h>\d{1,2})(?:[:.](?P<m>\d{2}))?\s*(?P<ampm>a\.?\s?m\.?|p\.?\s?m\.?)?"
_WHEN = (
    r"(?:\s+(?P<day>tomorrow|today|tonight)(?:\s+(?:morning|evening|night))?)?"
    r"(?:\s+(?:in the\s+)?(?P<part>morning|evening|night|afternoon))?"
)
_ALARM = re.compile(
    _LEAD
    + r"(?:(?:set|make|create|put|add|start)\s+(?:up\s+)?(?:an?|my|the)?\s*alarm|alarm|wake me(?: up)?)"
    + r"\s+(?:for|at|to|on)?\s*(?:exactly\s+)?(?P<pre>tomorrow\s+(?:at\s+)?)?"
    + _TIME
    + r"(?:\s+exactly)?"
    + _WHEN
    + r"(?:\s+(?:for|to)\s+(?P<label>[^.?!]{2,60}))?"
    + r"(?:\s+(?:please|for me))?[\s.!?]*$",
    re.I,
)
_TIMER = re.compile(
    _LEAD
    + r"(?:(?:set|start|make|put)\s+(?:a|an|the)?\s*)?"
    + r"(?:timer\s+(?:for\s+)?(?P<n>\d+(?:\.\d+)?|an?|half an?)\s*(?P<unit>seconds?|secs?|minutes?|mins?|hours?|hrs?)"
    + r"|(?P<n2>\d+)[\s-]*(?P<unit2>second|minute|min|hour)s?[\s-]+timer)"
    + r"(?:\s+(?:for|to)\s+(?P<label>[^.?!]{2,60}))?"
    + r"(?:\s+(?:please|for me))?[\s.!?]*$",
    re.I,
)


@dataclass(frozen=True)
class Alarm:
    at: datetime
    label: str = ""

    def line(self, now: datetime) -> str:
        day = "today" if self.at.date() == now.date() else "tomorrow" if self.at.date() == (now + timedelta(days=1)).date() else self.at.strftime("%A")
        return f"Alarm set for {spoken_time(self.at)} {day}."


@dataclass(frozen=True)
class Timer:
    seconds: int
    label: str = ""

    def line(self) -> str:
        return f"Timer set: {spoken_span(self.seconds)}."


def spoken_time(at: datetime) -> str:
    return at.strftime("%I:%M %p").lstrip("0")


def spoken_span(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds} second{'s' if seconds != 1 else ''}"
    mins, secs = divmod(seconds, 60)
    hours, mins = divmod(mins, 60)
    parts = []
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if mins:
        parts.append(f"{mins} minute{'s' if mins != 1 else ''}")
    if secs and not hours:
        parts.append(f"{secs} second{'s' if secs != 1 else ''}")
    return " and ".join(parts)


def alarm_request(text: str, now: datetime | None = None) -> Alarm | None:
    """When to ring for "set an alarm for 7am tomorrow", else None (anything more goes to the model)."""
    now = now or datetime.now()
    m = _ALARM.match(" ".join(text.split()))
    if not m:
        return None
    h, mins = int(m.group("h")), int(m.group("m") or 0)
    ampm = (m.group("ampm") or "").replace(".", "").replace(" ", "").lower()
    part = (m.group("part") or "").lower()
    day = (m.group("day") or ("tomorrow" if m.group("pre") else "")).lower()
    if day == "tonight" and not ampm:
        ampm = "pm"
    if not ampm and part:
        ampm = "am" if part == "morning" else "pm"
    if not (0 <= h <= 23 and 0 <= mins <= 59) or (ampm and not 1 <= h <= 12):
        return None
    if ampm == "pm" and h < 12:
        h += 12
    elif ampm == "am" and h == 12:
        h = 0
    choices = [h] if ampm or h > 12 or h == 0 else [h, h + 12 if h < 12 else h - 12]
    base = now.date() + timedelta(days=1) if day == "tomorrow" else now.date()
    candidates = []
    for hour in choices:
        at = datetime.combine(base, datetime.min.time()).replace(hour=hour % 24, minute=mins)
        if day != "tomorrow" and at <= now:
            at += timedelta(days=1)
        candidates.append(at)
    # "Alarm for 7" with no am/pm: the next 7 o'clock, morning first when it's tomorrow.
    at = min(candidates) if day != "tomorrow" else candidates[0]
    return Alarm(at=at, label=(m.group("label") or "").strip())


def timer_request(text: str) -> Timer | None:
    m = _TIMER.match(" ".join(text.split()))
    if not m:
        return None
    raw = (m.group("n") or m.group("n2") or "").lower()
    unit = (m.group("unit") or m.group("unit2") or "").lower()
    n = 0.5 if raw.startswith("half") else 1.0 if raw in ("a", "an") else float(raw)
    scale = 1 if unit.startswith("s") else 3600 if unit.startswith("h") else 60
    seconds = int(n * scale)
    if not 1 <= seconds <= 24 * 3600:
        return None
    return Timer(seconds=seconds, label=(m.group("label") or "").strip())
