"""This PC's own time zone as an IANA name (America/Vancouver), from Windows' setting. Used when the
Google Calendar time zone can't be read (the scope wasn't granted), so "is it going to rain?" still
knows the city and "3pm" still means local time."""

from __future__ import annotations

import ctypes
import sys
from datetime import datetime

# Windows zone names -> the IANA zone CLDR lists first for them (the common ones).
WINDOWS_TO_IANA = {
    "Pacific Standard Time": "America/Vancouver",
    "British Columbia Standard Time": "America/Vancouver",
    "Yukon Standard Time": "America/Whitehorse",
    "Mountain Standard Time": "America/Edmonton",
    "US Mountain Standard Time": "America/Phoenix",
    "Central Standard Time": "America/Chicago",
    "Canada Central Standard Time": "America/Regina",
    "Eastern Standard Time": "America/Toronto",
    "Atlantic Standard Time": "America/Halifax",
    "Newfoundland Standard Time": "America/St_Johns",
    "Alaskan Standard Time": "America/Anchorage",
    "Hawaiian Standard Time": "Pacific/Honolulu",
    "GMT Standard Time": "Europe/London",
    "Greenwich Standard Time": "Atlantic/Reykjavik",
    "W. Europe Standard Time": "Europe/Berlin",
    "Romance Standard Time": "Europe/Paris",
    "Central Europe Standard Time": "Europe/Budapest",
    "Central European Standard Time": "Europe/Warsaw",
    "E. Europe Standard Time": "Europe/Chisinau",
    "FLE Standard Time": "Europe/Kiev",
    "GTB Standard Time": "Europe/Bucharest",
    "Russian Standard Time": "Europe/Moscow",
    "Turkey Standard Time": "Europe/Istanbul",
    "Arabian Standard Time": "Asia/Dubai",
    "Arab Standard Time": "Asia/Riyadh",
    "Egypt Standard Time": "Africa/Cairo",
    "South Africa Standard Time": "Africa/Johannesburg",
    "W. Central Africa Standard Time": "Africa/Lagos",
    "E. Africa Standard Time": "Africa/Nairobi",
    "Pakistan Standard Time": "Asia/Karachi",
    "India Standard Time": "Asia/Kolkata",
    "Nepal Standard Time": "Asia/Kathmandu",
    "Bangladesh Standard Time": "Asia/Dhaka",
    "SE Asia Standard Time": "Asia/Bangkok",
    "Singapore Standard Time": "Asia/Singapore",
    "China Standard Time": "Asia/Shanghai",
    "Taipei Standard Time": "Asia/Taipei",
    "Tokyo Standard Time": "Asia/Tokyo",
    "Korea Standard Time": "Asia/Seoul",
    "AUS Eastern Standard Time": "Australia/Sydney",
    "E. Australia Standard Time": "Australia/Brisbane",
    "W. Australia Standard Time": "Australia/Perth",
    "New Zealand Standard Time": "Pacific/Auckland",
    "SA Pacific Standard Time": "America/Bogota",
    "Pacific SA Standard Time": "America/Santiago",
    "E. South America Standard Time": "America/Sao_Paulo",
    "Argentina Standard Time": "America/Buenos_Aires",
    "Central America Standard Time": "America/Guatemala",
    "Central Standard Time (Mexico)": "America/Mexico_City",
}


class _SystemTime(ctypes.Structure):
    _fields_ = [(n, ctypes.c_ushort) for n in ("y", "mo", "dow", "d", "h", "mi", "s", "ms")]


class _DynamicTZ(ctypes.Structure):
    _fields_ = [
        ("Bias", ctypes.c_long),
        ("StandardName", ctypes.c_wchar * 32),
        ("StandardDate", _SystemTime),
        ("StandardBias", ctypes.c_long),
        ("DaylightName", ctypes.c_wchar * 32),
        ("DaylightDate", _SystemTime),
        ("DaylightBias", ctypes.c_long),
        ("TimeZoneKeyName", ctypes.c_wchar * 128),
        ("DynamicDaylightTimeDisabled", ctypes.c_ubyte),
    ]


def windows_zone() -> str:
    """'Pacific Standard Time' (Windows' own name for the zone), or '' off Windows."""
    if sys.platform != "win32":
        return ""
    info = _DynamicTZ()
    if ctypes.windll.kernel32.GetDynamicTimeZoneInformation(ctypes.byref(info)) == 0xFFFFFFFF:  # type: ignore[attr-defined]
        return ""
    return info.TimeZoneKeyName or info.StandardName


def local_zone() -> str:
    """'America/Vancouver' for this PC, or '' if it can't be told."""
    name = WINDOWS_TO_IANA.get(windows_zone(), "")
    if name:
        return name
    tz = datetime.now().astimezone().tzinfo
    key = getattr(tz, "key", "")
    return key if "/" in key else ""
