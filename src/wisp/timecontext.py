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
        f"Next 14 days: {upcoming}."
    )
