"""Alarms and timers: set in about a second from the words, rung by Pyxi itself."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime

from conftest import FakeScreen
from pyxi.alarms import alarm_request, timer_request
from pyxi.companion.brain import Brain
from pyxi.demo import DemoLLM
from pyxi.hub import Hub

NIGHT = datetime(2026, 11, 8, 22, 15)


def test_alarm_times_from_how_people_say_them() -> None:
    said = {
        "Okay, can you just set an alarm at 7am exactly tomorrow morning?": (9, 7, 0),
        "set an alarm for 7": (9, 7, 0),  # no am/pm: the next 7 o'clock
        "wake me up at 6:30": (9, 6, 30),
        "Set an alarm for 7:30 a.m. tomorrow": (9, 7, 30),
        "set an alarm for 11 pm": (8, 23, 0),  # still today
    }
    for text, (day, h, m) in said.items():
        a = alarm_request(text, NIGHT)
        assert a is not None, text
        assert (a.at.day, a.at.hour, a.at.minute) == (day, h, m), text
    assert alarm_request("set an alarm for 6 pm to call mom", NIGHT).label == "call mom"
    assert alarm_request("set an alarm for 7am", NIGHT).line(NIGHT) == "Alarm set for 7:00 AM tomorrow."
    # repeating ones go to the model (schedule_routine alarm=true)
    assert alarm_request("set an alarm every weekday at 7", NIGHT) is None


def test_timers() -> None:
    assert timer_request("Set a timer for 10 minutes.").seconds == 600
    assert timer_request("set a 5 minute timer").seconds == 300
    assert timer_request("start a timer for an hour").seconds == 3600
    t = timer_request("set a timer for half an hour for the pasta")
    assert t.seconds == 1800 and t.label == "the pasta"
    assert timer_request("what's a good timer app?") is None


async def test_setting_an_alarm_is_instant_and_it_rings(settings) -> None:
    hub = Hub()
    events: list[dict] = []

    async def record(text: str) -> None:
        events.append(json.loads(text))

    hub.connect(record)

    class NoModel(DemoLLM):
        async def complete(self, *a, **k):  # pragma: no cover - must never run
            raise AssertionError("an alarm called the model")

    brain = Brain(settings, hub, llm=NoModel(), screen=FakeScreen())
    said: list[str] = []
    brain._speak = lambda text, **kw: said.append(text)  # type: ignore[method-assign]
    await brain.turn("set an alarm for 7am tomorrow")
    assert said[0].startswith("Alarm set for 7:00 AM") and [c["tool"] for c in brain._calls] == ["alarm"]
    (r,) = [r for r in brain.routines.items if r.kind == "alarm"]
    # at 7:00 the next morning, Pyxi rings: a chime event, a card and a line
    ran = await brain.run_due_routines(datetime.fromtimestamp(r.next_run + 5))
    assert ran == 1
    assert any(e["type"] == "alarm" for e in events)
    assert any(e["type"] == "job_ready" and e["title"].startswith("Alarm") for e in events)
    assert said[-1].startswith("It's ")
    assert not [r for r in brain.routines.items if r.kind == "alarm"]  # a one-off goes away


async def test_a_timer_rings_and_stop_cancels_one(settings) -> None:
    brain = Brain(settings, Hub(), llm=DemoLLM(), screen=FakeScreen())
    said: list[str] = []
    brain._speak = lambda text, **kw: said.append(text)  # type: ignore[method-assign]
    await brain.turn("set a timer for 1 second")
    assert said == ["Timer set: 1 second."]
    await asyncio.sleep(1.3)
    assert said[-1] == "Time's up: 1 second timer."
    await brain.turn("set a timer for 10 minutes")
    await brain.stop_everything()
    await asyncio.sleep(0.05)
    assert all(j.status != "running" for j in brain.jobs.jobs.values())


def test_misheard_and_reordered() -> None:
    a = alarm_request("Hey Pixie, can you sit on alarm for 7 a.m. in the morning tomorrow?", NIGHT)
    assert a is not None and (a.at.day, a.at.hour) == (9, 7)
    assert alarm_request("said an alarm for 6:45", NIGHT).at.hour == 6


async def test_an_alarm_the_model_sets_without_days_rings_once(settings) -> None:
    brain = Brain(settings, Hub(), llm=DemoLLM(), screen=FakeScreen())
    await brain._act("schedule_routine", {"task": "Alarm", "time": "07:00", "alarm": True})
    (r,) = brain.routines.items
    assert r.kind == "alarm" and r.on  # a date, so it goes away after ringing
    await brain._act("schedule_routine", {"task": "Gym", "time": "06:30", "alarm": True, "days": ["weekdays"]})
    assert brain.routines.items[-1].on is None and brain.routines.items[-1].days == ["weekdays"]


def test_cancelling_a_routine_goes_back_to_the_model() -> None:
    from pyxi.companion.tools import INFO_TOOLS

    assert "cancel_routine" in INFO_TOOLS  # so "make it one-time" can set the new one after


async def test_a_label_that_only_says_alarm_rings_as_time_to_get_up(settings) -> None:
    brain = Brain(settings, Hub(), llm=DemoLLM(), screen=FakeScreen())
    said: list[str] = []
    brain._speak = lambda text, **kw: said.append(text)  # type: ignore[method-assign]
    await brain._act("schedule_routine", {"task": "Alarm: tomorrow, Monday 9 November", "time": "07:00", "alarm": True})
    (r,) = brain.routines.items
    await brain.run_due_routines(datetime.fromtimestamp(r.next_run + 5))
    assert said[-1].endswith("Time to get up.") and "November" not in said[-1]
