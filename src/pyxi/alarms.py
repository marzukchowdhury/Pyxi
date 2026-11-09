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
    r"^\W*(?:(?:hey|ok|okay|so|um|uh|now|and|just|alright|please)[,.]?\s+)*(?:(?:pyxi|pixie|pixi|pixy|pixey|pixig)[,.]?\s+)?"
    r"(?:(?:can|could|would|will) you\s+|please\s+|i (?:want|need) you to\s+|go ahead and\s+)?"
    r"(?:please\s+|just\s+)?"
)
_TIME = r"(?P<h>\d{1,2})(?:[:.](?P<m>\d{2}))?\s*(?P<ampm>a\.?\s?m\.?|p\.?\s?m\.?)?"
_WHEN = (
    r"(?:\s+(?P<day>tomorrow|today|tonight)(?:\s+(?:morning|evening|night))?)?"
    r"(?:\s+(?:in the\s+)?(?P<part>morning|evening|night|afternoon))?"
    r"(?:\s+(?P<day2>tomorrow|today|tonight))?"  # "7am in the morning tomorrow"
)
_ALARM = re.compile(
    _LEAD
    # "sit on alarm", "said an alarm": how speech recognition often hears "set an alarm"
    + r"(?:(?:set|sit|sat|said|make|create|put|add|start)\s+(?:up\s+)?(?:an?|on|my|the)?\s*alarm|alarm|wake me(?: up)?)"
    + r"\s+(?:for|at|to|on)?\s*(?:exactly\s+)?(?P<pre>tomorrow\s+(?:at\s+)?)?"
    + _TIME
    + r"(?:\s+exactly)?"
    + _WHEN
    + r"(?:\s+(?:for|to)\s+(?P<label>[^.?!]{2,60}))?"
    + r"(?:\s+(?:please|for me))?[\s.!?]*$",
    re.I,
)
_NUM_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "twelve": 12, "fifteen": 15, "twenty": 20, "twenty-five": 25, "thirty": 30,
    "forty": 40, "forty-five": 45, "fifty": 50, "sixty": 60, "ninety": 90,
}
_NUMS = "|".join(sorted(_NUM_WORDS, key=len, reverse=True))
_TIMER = re.compile(
    _LEAD
    + r"(?:(?:set|start|make|put)\s+(?:a|an|the)?\s*)?"
    + r"(?:timer\s+(?:for\s+)?(?P<n>\d+(?:\.\d+)?|an?|half an?|" + _NUMS + r")\s*(?P<unit>seconds?|secs?|minutes?|mins?|hours?|hrs?)"
    + r"|(?P<n2>\d+|" + _NUMS + r")[\s-]*(?P<unit2>second|minute|min|hour)s?[\s-]+timer)"
    + r"(?:\s+(?:for|to)\s+(?P<label>[^.?!]{2,60}))?"
    + r"(?:\s+(?:please|for me))?[\s.!?]*$",
    re.I,
)


@dataclass(frozen=True)
class Alarm:
    at: datetime
    label: str = ""
    passed_today: bool = False  # they said today, but that time had gone: it's tomorrow
    small_hours: bool = False  # "tomorrow" said after midnight: the morning that's coming

    def line(self, now: datetime) -> str:
        if self.passed_today:
            return f"{spoken_time(self.at)} has already gone today, so it's set for tomorrow."
        if self.small_hours:
            hours = round((self.at - now).total_seconds() / 3600)
            return f"Alarm set for {spoken_time(self.at)} this morning, in about {hours} hour{'s' if hours != 1 else ''}."
        day = "today" if self.at.date() == now.date() else "tomorrow" if self.at.date() == (now + timedelta(days=1)).date() else self.at.strftime("%A")
        return f"Alarm set for {spoken_time(self.at)} {day}."


@dataclass(frozen=True)
class Timer:
    seconds: int
    label: str = ""
    reminder: bool = False  # "remind me in 10 minutes to stretch"

    def line(self) -> str:
        if self.reminder:
            return f"I'll remind you in {spoken_span(self.seconds)}."
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
    day = (m.group("day") or m.group("day2") or ("tomorrow" if m.group("pre") else "")).lower()
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
    # Past midnight, "tomorrow at 6:30" means the morning that's coming, not the day after.
    small_hours = day == "tomorrow" and now.hour < 5 and (ampm == "am" or (not ampm and h < 12))
    if small_hours:
        day = "today"
    base = now.date() + timedelta(days=1) if day == "tomorrow" else now.date()
    candidates = []
    for hour in choices:
        at = datetime.combine(base, datetime.min.time()).replace(hour=hour % 24, minute=mins)
        if day != "tomorrow" and at <= now:
            at += timedelta(days=1)
        candidates.append(at)
    # "Alarm for 7" with no am/pm: the next 7 o'clock, morning first when it's tomorrow.
    at = min(candidates) if day != "tomorrow" else candidates[0]
    passed = day == "today" and at.date() != now.date() and not small_hours
    if small_hours and at.date() != now.date():
        small_hours = False  # that time has already gone this morning too: it really is tomorrow
    return Alarm(at=at, label=(m.group("label") or "").strip(), passed_today=passed, small_hours=small_hours)


_SPAN = r"(?P<n>\d+(?:\.\d+)?|an?|half an?|" + _NUMS + r")\s*(?P<unit>seconds?|secs?|minutes?|mins?|hours?|hrs?)"
_WORDS = _NUM_WORDS
_REMIND_IN = re.compile(
    _LEAD
    + r"remind me\s+(?:(?:in|after)\s+" + _SPAN + r"\s+(?:to|that|about)\s+(?P<label>[^?!]{2,80}?)"
    + r"|(?:to|that|about)\s+(?P<label2>[^?!]{2,80}?)\s+(?:in|after)\s+" + _SPAN.replace("?P<n>", "?P<n2>").replace("?P<unit>", "?P<unit2>") + r")"
    + r"[\s.!?]*$",
    re.I,
)
_REMIND_AT = re.compile(
    _LEAD + r"remind me\s+(?:at\s+)?(?P<when>.+?)\s+(?:to|that|about)\s+(?P<label>[^?!]{2,80}?)[\s.!?]*$", re.I
)


def _seconds(raw: str, unit: str) -> int:
    raw, unit = raw.lower(), unit.lower()
    n = 0.5 if raw.startswith("half") else 1.0 if raw in ("a", "an") else float(_WORDS.get(raw, raw))
    return int(n * (1 if unit.startswith("s") else 3600 if unit.startswith("h") else 60))


def reminder_request(text: str, now: datetime | None = None) -> Timer | Alarm | None:
    """'Remind me in 10 minutes to stretch' -> a Timer; 'remind me at 5pm to call mum' -> an
    Alarm with that label. Each rings once."""
    t = " ".join(text.split())
    if m := _REMIND_IN.match(t):
        raw, unit = (m.group("n"), m.group("unit")) if m.group("n") else (m.group("n2"), m.group("unit2"))
        seconds = _seconds(raw, unit)
        label = (m.group("label") or m.group("label2") or "").strip()
        if 1 <= seconds <= 24 * 3600 and label:
            return Timer(seconds=seconds, label=label, reminder=True)
        return None
    if m := _REMIND_AT.match(t):
        a = alarm_request(f"set an alarm for {m.group('when')}", now)
        if a is not None:
            return Alarm(at=a.at, label=m.group("label").strip(), passed_today=a.passed_today)
    return None


_CANCEL = re.compile(
    _LEAD + r"(?:cancel|delete|remove|turn off|stop|get rid of)\s+(?:my\s+|the\s+)?" + _TIME
    + r"\s+alarm(?:\s+(?:for\s+)?(?:tomorrow|today|this morning))?[\s.!?]*$",
    re.I,
)


_LIST = re.compile(
    _LEAD + r"(?:what|which|list|show me|tell me)?\s*(?:are\s+)?(?:my\s+)?(?:alarms|timers)(?:\s+(?:do i have|have i got|are set|are on))?"
    r"|(?:do i have|have i got|are there) (?:any )?(?:alarms|timers)(?: set| on)?" + r"[\s.!?]*$",
    re.I,
)


def list_request(text: str) -> bool:
    """'What alarms do I have?', 'list my alarms', 'do I have any timers?'"""
    t = " ".join(text.split())
    return bool(_LIST.match(t)) or bool(re.match(r"(?i)^\W*(?:pyxi,?\s+)?what alarms do i have\b", t))


def cancel_request(text: str) -> str | None:
    """'Cancel my 6:30 alarm' -> '06:30' (24-hour, or '6:30' with no am/pm: either half of the day)."""
    m = _CANCEL.match(" ".join(text.split()))
    if not m:
        return None
    h, mins = int(m.group("h")), int(m.group("m") or 0)
    ampm = (m.group("ampm") or "").replace(".", "").replace(" ", "").lower()
    if ampm == "pm" and h < 12:
        h += 12
    elif ampm == "am" and h == 12:
        h = 0
    if not (0 <= h <= 23 and 0 <= mins <= 59):
        return None
    return f"{h:02d}:{mins:02d}" if ampm or h > 12 else f"{h}:{mins:02d}"


def timer_request(text: str) -> Timer | None:
    m = _TIMER.match(" ".join(text.split()))
    if not m:
        return None
    raw = (m.group("n") or m.group("n2") or "").lower()
    unit = (m.group("unit") or m.group("unit2") or "").lower()
    n = 0.5 if raw.startswith("half") else 1.0 if raw in ("a", "an") else float(_NUM_WORDS.get(raw, raw))
    scale = 1 if unit.startswith("s") else 3600 if unit.startswith("h") else 60
    seconds = int(n * scale)
    if not 1 <= seconds <= 24 * 3600:
        return None
    return Timer(seconds=seconds, label=(m.group("label") or "").strip())
