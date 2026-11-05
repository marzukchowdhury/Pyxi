"""Routines: schedules, persistence, and running due ones as ordinary turns."""

from __future__ import annotations

from datetime import datetime

from test_brain_and_server import make_brain
from test_notes_and_guides import Scripted
from blip.routines import RoutineStore, parse_time


def test_times_and_next_runs(tmp_path):
    assert [parse_time(t) for t in ("9", "9:30", "6pm", "12am", "18:05", "25:00")] == [
        "09:00",
        "09:30",
        "18:00",
        "00:00",
        "18:05",
        None,
    ]
    store = RoutineStore(tmp_path / "r.json")
    fri_night = datetime(2026, 11, 6, 22, 0)  # a Friday
    r = store.add("check my email", days=["weekdays"], time="9am", now=fri_night)
    assert datetime.fromtimestamp(r.next_run) == datetime(2026, 11, 9, 9, 0)  # skips the weekend
    every = store.add("stretch", every_minutes=5, now=fri_night)
    assert every.every_minutes == 15  # never more often than every 15 minutes
    assert RoutineStore(tmp_path / "r.json").items[0].task == "check my email"  # persisted
    assert store.remove("stretch") and len(store.items) == 1


async def test_due_routines_run_as_turns(settings):
    llm = Scripted(
        [
            (
                "schedule_routine",
                {"task": "Tell me a fun fact", "days": ["every day"], "time": "08:00"},
            ),
            ("say", {"text": "Every morning at 8."}),
        ],
        [("say", {"text": "Octopuses have three hearts."})],
    )
    brain, rec = make_brain(settings, llm=llm)
    await brain.turn("every morning at 8 tell me a fun fact")
    [r] = brain.routines.items
    assert (
        r.time == "08:00" and rec.of("routines")[-1]["routines"][0]["task"] == "Tell me a fun fact"
    )
    assert await brain.run_due_routines(datetime.fromtimestamp(r.next_run - 60)) == 0  # not yet
    ran = await brain.run_due_routines(datetime.fromtimestamp(r.next_run + 1))
    assert ran == 1 and rec.of("user")[-1]["text"] == "Routine: Tell me a fun fact"
    assert "(Routine: Tell me a fun fact)" in llm.prompts[-1]
    assert rec.of("say")[-1]["text"] == "Octopuses have three hearts."
    assert r.next_run > r.last_run  # scheduled again for tomorrow
