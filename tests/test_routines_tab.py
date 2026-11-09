"""The Routines tab: set one up from the form, switch it off and on, run it now, delete it."""

import json

from fastapi.testclient import TestClient

from pyxi.companion.brain import Brain
from pyxi.hub import Hub
from pyxi.demo import DemoLLM
from pyxi.server import create_app
from conftest import FakeScreen


def _routines(ws) -> list[dict]:
    while True:
        got = json.loads(ws.receive_text())
        if got["type"] == "routines":
            return got["routines"]


def test_the_routines_tab_sets_up_pauses_and_deletes(settings):
    brain = Brain(settings, Hub(), llm=DemoLLM(), screen=FakeScreen())
    app = create_app(settings, demo=True, brain=brain, key="k")
    with TestClient(app) as client, client.websocket_connect("/ws?key=k") as ws:
        ws.receive_text()
        ws.send_text(json.dumps({"type": "routine_add", "task": "Tell me what's on my calendar", "days": ["weekdays", "nonsense"], "time": "08:30", "every_minutes": None}))
        [r] = _routines(ws)
        assert r["task"] == "Tell me what's on my calendar" and r["enabled"]
        assert "weekdays" in r["when"] and "08:30" in r["when"]

        ws.send_text(json.dumps({"type": "routine_toggle", "id": r["id"], "enabled": False}))
        assert _routines(ws)[0]["enabled"] is False
        assert not brain.routines.due()  # a paused routine never runs
        ws.send_text(json.dumps({"type": "routine_toggle", "id": r["id"], "enabled": True}))
        assert _routines(ws)[0]["enabled"] is True

        ws.send_text(json.dumps({"type": "routine_add", "task": "Remind me to drink water", "every_minutes": 60}))
        assert {x["when"] for x in _routines(ws)} >= {"every hour"}

        ws.send_text(json.dumps({"type": "routine_delete", "id": r["id"]}))
        assert [x["task"] for x in _routines(ws)] == ["Remind me to drink water"]
