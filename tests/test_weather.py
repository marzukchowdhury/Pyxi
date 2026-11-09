"""Weather from a forecast service: a plain question answered in a second, with numbers."""

from datetime import date

from pyxi import weather
from pyxi.weather import Day, Forecast


def sample(country: str = "CA") -> Forecast:
    return Forecast(
        "Vancouver, British Columbia, Canada",
        country,
        [Day(date(2026, 11, 8), 12.2, 6.1, 64, "drizzle"), Day(date(2026, 11, 9), 11.0, 6.0, 100, "rain")],
        10.0,
        "cloudy",
    )


def test_plain_weather_questions_are_recognised():
    q = weather.simple_question
    assert q("What's the weather in Vancouver tomorrow?") == ("Vancouver", "tomorrow")
    assert q("what is the forecast for tomorrow in Toronto?") == ("Toronto", "tomorrow")
    assert q("What's the weather like?") == ("", "today")
    assert q("how's the weather right now") == ("", "now")
    assert q("what's the weather going to be like for my trip next week, should I pack a jacket") is None


def test_the_spoken_line_has_the_numbers():
    f = sample()
    assert f.say("tomorrow") == "Tomorrow in Vancouver: rain, high of 11°C, low of 6°C, 100% chance of rain."
    assert f.say("now").startswith("Right now in Vancouver it's 10°C and cloudy")
    assert "54°F" in sample("US").say("today")  # Fahrenheit where people use it
    assert weather.city_of("America/Vancouver") == "Vancouver" and weather.city_of(None) == ""


async def test_a_weather_question_skips_the_model(settings, monkeypatch):
    from test_brain_and_server import make_brain
    from test_connectors_and_docs import Script

    async def fake(place):
        assert place == "Vancouver"
        return sample()

    monkeypatch.setattr(weather, "forecast", fake)
    llm = Script()
    brain, rec = make_brain(settings, llm=llm)
    await brain.turn("What's the weather in Vancouver tomorrow?")
    assert not llm.prompts  # no model, no web search
    assert "high of 11°C" in " ".join(s["text"] for s in rec.of("say"))


def test_weekends_weekdays_weeks_and_rain_questions_are_quick() -> None:
    from pyxi.weather import simple_question

    assert simple_question("What's the weather in Dhaka this weekend?") == ("Dhaka", "weekend")
    assert simple_question("weather in London on Saturday") == ("London", "saturday")
    assert simple_question("will it snow this week in Toronto") == ("Toronto", "week")
    assert simple_question("Is it going to rain today?") == ("", "today")
    assert simple_question("Is it going to rain in Paris tomorrow?") == ("Paris", "tomorrow")


def test_weekend_and_week_lines() -> None:
    from datetime import date, timedelta

    from pyxi.weather import Day, Forecast

    start = date(2026, 11, 9)  # a Monday
    days = [Day(start + timedelta(days=i), 20 + i, 10, 60 if i == 5 else 0, "clear") for i in range(7)]
    f = Forecast("Dhaka, Bangladesh", "BD", days, None, "")
    assert f.say("weekend").startswith("This weekend in Dhaka: Saturday clear, 25°C, 60% chance of rain; Sunday clear, 26°C")
    assert "Rain likely on Saturday" in f.say("week")
    assert f.say("saturday").startswith("Saturday in Dhaka: clear, high of 25°C")
