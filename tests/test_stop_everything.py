"""'Stop everything': every teammate job, the hands, open cards and anything queued, at once."""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from conftest import FakeScreen
from pyxi.companion.brain import Brain
from pyxi.demo import DemoLLM
from pyxi.hub import Hub


def _brain(settings) -> Brain:
    return Brain(settings, Hub(), llm=DemoLLM(), screen=FakeScreen())


async def test_everything_stops_at_once(settings):
    brain = _brain(settings)
    job = await brain.jobs.track("Pyxi", "Email the landlord")
    gone: list[str] = []

    async def slow_job():
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            gone.append("job task")
            raise

    brain.jobs._tasks[job.id] = asyncio.create_task(slow_job())
    sent: list[str] = []

    async def errand_waiting_on_a_card():
        if await brain.hub.request("confirm", action="Send an email to x"):
            sent.append("email")

    brain._spawn(errand_waiting_on_a_card())

    class Hands:
        cancelled = False

    brain._operator = Hands()
    await asyncio.sleep(0.05)
    message = await brain.stop_everything()
    await asyncio.sleep(0.05)
    assert brain.jobs.jobs[job.id].status == "stopped" and gone == ["job task"]
    assert sent == [] and not brain.hub._pending  # the card closed, nothing went out
    assert brain._operator.cancelled and not brain._bg
    assert message == "Stopped everything: 1 teammate job, the mouse and keyboard, 1 waiting action."
    assert brain.audit.recent()[0]["what"] == message


async def test_nothing_running_says_so(settings):
    assert await _brain(settings).stop_everything() == "Stopped everything. Nothing was running."


def test_the_desktop_app_stops_it_with_the_launch_key(settings):
    from pyxi.server import create_app

    app = create_app(settings, demo=True, brain=_brain(settings), key="k")
    with TestClient(app) as client:
        assert client.post("/api/stop").status_code == 401  # no other program can
        r = client.post("/api/stop", headers={"x-pyxi-key": "k"})
        assert r.status_code == 200 and r.json()["message"].startswith("Stopped everything")


async def test_saying_stop_stops_for_real(settings):
    brain = _brain(settings)
    job = await brain.jobs.track("Scout", "Book the dentist")
    brain.jobs._tasks[job.id] = asyncio.create_task(asyncio.sleep(60))
    said: list[str] = []
    brain._speak = lambda text, **kw: said.append(text)  # type: ignore[method-assign]
    await brain.turn("Okay stop stop")
    await asyncio.sleep(0.05)
    assert brain.jobs.jobs[job.id].status == "stopped"
    assert said == ["Stopped."]
    assert [c["tool"] for c in brain._calls] == ["stop_everything"]


def test_only_a_plain_stop_counts() -> None:
    from pyxi.companion.instant import is_stop

    assert is_stop("Stop.") and is_stop("cancel that") and is_stop("Hey Pyxi, stop!")
    assert not is_stop("stop the music") and not is_stop("stop sharing my screen")


async def test_a_turn_stopped_midway_is_still_in_the_history(settings):
    # The Stop button cancels the running turn: it used to vanish from the history, its spend too.
    brain = _brain(settings)
    reached = asyncio.Event()

    class Stuck:
        async def ainvoke(self, state):
            brain._usage.cost_usd = 0.002
            brain._said.append("On it.")
            reached.set()
            await asyncio.sleep(60)

    brain._graph = Stuck()
    brain._spawn(brain.turn("Open a new tab and go to my sheet"))
    await asyncio.wait_for(reached.wait(), 5)
    await brain.stop_everything()
    await asyncio.sleep(0.05)
    last = brain.turns.recent(1)[0]
    assert last["user_text"] == "Open a new tab and go to my sheet"
    assert last["error"] == "stopped" and last["said"] == "On it."
    assert brain.turns.spent_today() == 0.002
    assert brain.history[-1] == ("Open a new tab and go to my sheet", "On it.")
    assert not brain._lock.locked()  # the next request isn't left waiting
