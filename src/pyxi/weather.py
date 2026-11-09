"""The weather, from Open-Meteo (free, no key): today, tomorrow and the week, with real numbers.

A web search answered "what's the weather tomorrow?" in 9.5 s with "clear and mild, a few degrees
above average" from a news site. This answers in about a second with the high, the low and the
chance of rain.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

import httpx

from pyxi.httpshared import shared

GEO = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST = "https://api.open-meteo.com/v1/forecast"
TIMEOUT = httpx.Timeout(6.0, connect=3.0)

# WMO weather codes, in words people say.
SKY = {
    0: "clear",
    1: "mostly clear",
    2: "partly cloudy",
    3: "cloudy",
    45: "foggy",
    48: "foggy",
    51: "light drizzle",
    53: "drizzle",
    55: "heavy drizzle",
    56: "freezing drizzle",
    57: "freezing drizzle",
    61: "light rain",
    63: "rain",
    65: "heavy rain",
    66: "freezing rain",
    67: "freezing rain",
    71: "light snow",
    73: "snow",
    75: "heavy snow",
    77: "snow grains",
    80: "showers",
    81: "showers",
    82: "heavy showers",
    85: "snow showers",
    86: "heavy snow showers",
    95: "thunderstorms",
    96: "thunderstorms with hail",
    99: "thunderstorms with hail",
}
# Countries that think in Fahrenheit.
FAHRENHEIT = {"US", "LR", "MM", "BS", "BZ", "KY", "PW", "FM", "MH"}


@dataclass
class Day:
    day: date
    high: float
    low: float
    rain_chance: int
    sky: str


@dataclass
class Forecast:
    place: str
    country: str
    days: list[Day]
    now_temp: float | None
    now_sky: str

    @property
    def fahrenheit(self) -> bool:
        return self.country in FAHRENHEIT

    def temp(self, c: float) -> str:
        return f"{round(c * 9 / 5 + 32)}°F" if self.fahrenheit else f"{round(c)}°C"

    def describe(self) -> str:
        """All of it, for the model to answer from."""
        lines = [f"Weather for {self.place} (Open-Meteo):"]
        if self.now_temp is not None:
            lines.append(f"Now: {self.temp(self.now_temp)}, {self.now_sky}.")
        for d in self.days:
            lines.append(
                f"{d.day:%a %d %b}: high {self.temp(d.high)}, low {self.temp(d.low)}, "
                f"{d.sky}, {d.rain_chance}% chance of rain."
            )
        return "\n".join(lines)

    def say(self, which: str) -> str:
        """One short spoken line: 'Tomorrow in Vancouver: light rain, 11°C, low of 7, 80% chance of rain.'"""
        if which == "now" and self.now_temp is not None:
            d = self.days[0]
            return (
                f"Right now in {self.place.split(',')[0]} it's {self.temp(self.now_temp)} and "
                f"{self.now_sky}, with a high of {self.temp(d.high)} today."
            )
        city = self.place.split(",")[0]
        if which in ("weekend", "week"):
            days = [d for d in self.days if d.day.weekday() >= 5] if which == "weekend" else self.days
            if not days:
                days = self.days[-2:]
            parts = [
                f"{d.day:%A} {d.sky}, {self.temp(d.high)}"
                + (f", {d.rain_chance}% chance of rain" if d.rain_chance >= 30 else "")
                for d in days[: 2 if which == "weekend" else 7]
            ]
            if which == "week":
                wet = [f"{d.day:%A}" for d in days if d.rain_chance >= 50]
                hi, lo = max(d.high for d in days), min(d.low for d in days)
                rain = f" Rain likely on {', '.join(wet)}." if wet else " No real rain in sight."
                return f"This week in {city}: highs up to {self.temp(hi)}, lows down to {self.temp(lo)}.{rain}"
            return f"This weekend in {city}: " + "; ".join(parts) + "."
        names = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
        if which in names:
            match = [d for d in self.days if d.day.weekday() == names.index(which)]
            index = self.days.index(match[0]) if match else 0
        else:
            index = {"today": 0, "tomorrow": 1}.get(which, 0)
        d = self.days[min(index, len(self.days) - 1)]
        when = "Tomorrow" if index == 1 else "Today" if index == 0 else f"{d.day:%A}"
        rain = f", {d.rain_chance}% chance of rain" if d.rain_chance >= 20 else ""
        return (
            f"{when} in {self.place.split(',')[0]}: {d.sky}, high of {self.temp(d.high)}, "
            f"low of {self.temp(d.low)}{rain}."
        )


async def forecast(place: str) -> Forecast | None:
    """None when the place can't be found or Open-Meteo doesn't answer."""
    place = place.strip()
    if not place:
        return None
    async with shared("weather", timeout=TIMEOUT) as c:
        try:
            geo = await c.get(
                GEO, params={"name": place.split(",")[0], "count": 1, "language": "en"}
            )
            hits = geo.json().get("results") or []
            if not hits:
                return None
            g = hits[0]
            r = await c.get(
                FORECAST,
                params={
                    "latitude": g["latitude"],
                    "longitude": g["longitude"],
                    "timezone": "auto",
                    "forecast_days": 7,
                    "current": "temperature_2m,weather_code",
                    "daily": "temperature_2m_max,temperature_2m_min,"
                    "precipitation_probability_max,weather_code",
                },
            )
            data = r.json()
        except (httpx.HTTPError, ValueError, KeyError):
            return None
    daily = data.get("daily") or {}
    days = [
        Day(
            datetime.strptime(t, "%Y-%m-%d").date(),
            float(hi),
            float(lo),
            int(p or 0),
            SKY.get(int(code or 0), "mixed"),
        )
        for t, hi, lo, p, code in zip(
            daily.get("time", []),
            daily.get("temperature_2m_max", []),
            daily.get("temperature_2m_min", []),
            daily.get("precipitation_probability_max", []),
            daily.get("weather_code", []),
            strict=False,
        )
        if hi is not None and lo is not None
    ]
    if not days:
        return None
    now = data.get("current") or {}
    name = ", ".join(x for x in (g.get("name"), g.get("admin1"), g.get("country")) if x)
    return Forecast(
        name,
        str(g.get("country_code") or ""),
        days,
        float(now["temperature_2m"]) if now.get("temperature_2m") is not None else None,
        SKY.get(int(now.get("weather_code") or 0), "mixed"),
    )


def city_of(tz_name: str | None) -> str:
    """Where they probably are, from their time zone (America/Vancouver -> Vancouver)."""
    name = str(tz_name or "")
    return name.rsplit("/", 1)[-1].replace("_", " ") if "/" in name else ""


# "What's the weather in Vancouver tomorrow?", "weather today", "is it going to rain tomorrow?"
_ASK = re.compile(
    r"^\W*(?:hey,?\s+)?(?:pyxi,?\s+)?(?:what'?s|what is|how'?s|how is|tell me)?\s*(?:the\s+)?"
    r"(?:weather|forecast)(?:\s+(?:like|going to be|gonna be))?"
    r"(?:\s+(?:in|for|at)\s+(?P<place>[a-z .'-]+?))?"
    r"(?:\s+(?:for\s+)?(?P<when>today|tomorrow|right now|now|tonight|this weekend|the weekend|weekend|this week|the week|(?:on |this )?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)))?"
    r"(?:\s+(?:in|for|at)\s+(?P<place2>[a-z .'-]+?))?[\s?.!]*$",
    re.I,
)


_RAIN = re.compile(
    r"^\W*(?:hey,?\s+)?(?:pyxi,?\s+)?(?:is it|will it|is it going to|is it gonna|does it look like it'?s? going to)\s+"
    r"(?:be\s+)?(?P<what>rain|raining|rainy|snow|snowing)"
    r"(?:\s+(?:in|at)\s+(?P<place>[a-z .'-]+?))?"
    r"(?:\s+(?P<when>today|tomorrow|right now|now|tonight|this weekend|the weekend|weekend|this week|the week|(?:on |this )?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)))?"
    r"(?:\s+(?:in|at)\s+(?P<place2>[a-z .'-]+?))?[\s?.!]*$",
    re.I,
)


def _when(word: str) -> str:
    w = word.lower().removeprefix("on ").removeprefix("this ").removeprefix("the ").strip()
    return {"right now": "now", "tonight": "today", "": "today", "week": "week"}.get(w, w)


def simple_question(text: str) -> tuple[str, str] | None:
    """(place or "", today|tomorrow|now|weekend|week|monday..sunday) for a plain weather
    question, else None."""
    text = " ".join(text.split())
    m = _ASK.match(text) or _RAIN.match(text)
    if not m:
        return None
    when = (m.group("when") or "").lower()
    place = (m.group("place") or "").strip()
    if place.lower() in ("today", "tomorrow", "now", "right now", "tonight"):  # "for tomorrow in X"
        when, place = when or place.lower(), ""
    place = place or (m.group("place2") or "").strip()
    if place.lower() in ("here", "my area", "my city"):
        place = ""
    return place, _when(when)
