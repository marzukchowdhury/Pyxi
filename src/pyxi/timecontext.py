"""What time it is, said precisely enough that "Friday at 3" can't be misread.

Models are bad at calendar arithmetic and don't know today's date at all unless told, so
every turn gets the exact date, time and time zone, plus the next two weeks with their
weekdays spelled out.
"""

from __future__ import annotations

from datetime import datetime, timedelta, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def zone(name: str | None) -> tzinfo:
    if name:
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            pass
    return datetime.now().astimezone().tzinfo  # the PC's own zone


# The zones people name when they give a time, as their own time zone data has them today.
ELSEWHERE = (
    ("US Pacific (Los Angeles, Seattle)", "America/Los_Angeles"),
    ("US Mountain (Denver)", "America/Denver"),
    ("Central (Chicago)", "America/Chicago"),
    ("Eastern (New York, Toronto)", "America/New_York"),
    ("UK", "Europe/London"),
    ("Central Europe (Paris, Berlin)", "Europe/Paris"),
    ("Gulf (Dubai)", "Asia/Dubai"),
    ("India", "Asia/Kolkata"),
    ("Bangladesh", "Asia/Dhaka"),
    ("Singapore", "Asia/Singapore"),
    ("Japan", "Asia/Tokyo"),
    ("Sydney", "Australia/Sydney"),
)


def elsewhere(now: datetime) -> str:
    """How far each of those zones is from the user's own, right now, from the zone data on
    this PC. Live: the model said 10 AM Eastern was 7 AM in Vancouver "since Vancouver is
    three hours behind Eastern in November"; British Columbia had stopped changing its clocks
    that year, the answer was 8 AM, and it was about the user's interview. A model's memory of
    zone rules is older than the rules."""
    here = now.utcoffset() or timedelta(0)
    bits = []
    for label, name in ELSEWHERE:
        try:
            there = now.astimezone(ZoneInfo(name)).utcoffset() or timedelta(0)
        except (ZoneInfoNotFoundError, ValueError):
            continue
        minutes = int((there - here).total_seconds() // 60)
        if minutes == 0:
            bits.append(f"{label} the same time")
            continue
        h, m = divmod(abs(minutes), 60)
        size = f"{h}h{m:02d}" if m else f"{h}h"
        bits.append(f"{label} {size} {'ahead' if minutes > 0 else 'behind'}")
    return "; ".join(bits)


def now_block(tz_name: str | None = None, now: datetime | None = None) -> str:
    tz = zone(tz_name)
    now = (now or datetime.now(tz)).astimezone(tz)
    offset = now.strftime("%z")
    offset = f"UTC{offset[:3]}:{offset[3:]}" if offset else "local time"
    label = tz_name or now.tzname() or "local"
    days = [now + timedelta(days=d) for d in range(1, 15)]
    upcoming = ", ".join(f"{d:%a} {d.day} {d:%b}" for d in days)
    return (
        f"Now: {now:%A} {now.day} {now:%B %Y}, {now:%I:%M %p}".replace(" 0", " ")
        + f" ({label}, {offset}).\n"
        f"Today is {now:%a} {now.day} {now:%b}; tomorrow is {days[0]:%a} {days[0].day} {days[0]:%b}.\n"
        f"Next 14 days: {upcoming}.\n"
        f"Other time zones today, measured from THEIR clock: {elsewhere(now)}. To turn a time "
        "in another zone into their time, use these differences and never your own memory of "
        "how far apart zones are: the rules change, and theirs may not be what its name "
        "suggests."
    )
