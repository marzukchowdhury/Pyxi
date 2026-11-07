"""'Always allow' grants expire after 30 days, Settings lists them, and they can be revoked."""

from __future__ import annotations

import json
import time

from fastapi.testclient import TestClient

from pyxi import grants

DAY = 86400


def test_a_grant_lasts_thirty_days_then_asks_again(tmp_path):
    now = 1_800_000_000.0
    g = grants.grant(tmp_path, "hands", now=now)
    assert g["expires"] - g["granted"] == 30 * DAY
    assert grants.active(tmp_path, "hands", now=now + 29 * DAY)
    assert not grants.active(tmp_path, "hands", now=now + 30 * DAY + 1)
    assert grants.public(tmp_path, now=now + 31 * DAY) == []  # Settings drops expired ones
    assert not grants.active(tmp_path, "hands", now=now)  # and they're gone for good


def test_the_length_is_configurable(tmp_path, monkeypatch):
    monkeypatch.setenv("PYXI_GRANT_DAYS", "7")
    g = grants.grant(tmp_path, "hands", now=0)
    assert g["expires"] == 7 * DAY


def test_revoke_and_the_settings_list(tmp_path):
    grants.grant(tmp_path, "hands")
    [g] = grants.public(tmp_path)
    assert g["kind"] == "hands" and "mouse and keyboard" in g["label"]
    assert grants.revoke(tmp_path, "hands") and not grants.active(tmp_path, "hands")
    assert not grants.revoke(tmp_path, "hands")


def test_an_old_forever_grant_becomes_a_thirty_day_one(tmp_path):
    (tmp_path / "hands.json").write_text(json.dumps({"always": True}))
    assert grants.active(tmp_path, "hands")
    [g] = grants.public(tmp_path)
    assert 29 * DAY < g["expires"] - time.time() <= 30 * DAY
    assert not (tmp_path / "hands.json").exists()


def test_settings_lists_and_revokes_over_the_socket(settings):
    from conftest import FakeScreen
    from pyxi.companion.brain import Brain
    from pyxi.demo import DemoLLM
    from pyxi.hub import Hub
    from pyxi.server import create_app

    brain = Brain(settings, Hub(), llm=DemoLLM(), screen=FakeScreen())
    grants.grant(settings.data_dir, "hands")
    assert brain._hands_always()
    app = create_app(settings, demo=True, brain=brain, key="k")
    with TestClient(app) as client, client.websocket_connect("/ws?key=k") as ws:
        ws.receive_text()
        ws.send_text(json.dumps({"type": "grants_get"}))
        listed = json.loads(ws.receive_text())
        assert listed["type"] == "grants" and [g["kind"] for g in listed["grants"]] == ["hands"]
        ws.send_text(json.dumps({"type": "grant_revoke", "kind": "hands"}))
        assert json.loads(ws.receive_text())["grants"] == []
    assert not brain._hands_always()


async def test_choosing_always_allow_saves_a_grant(settings):
    from conftest import FakeScreen
    from pyxi.companion.brain import Brain
    from pyxi.demo import DemoLLM
    from pyxi.hub import Hub

    brain = Brain(settings, Hub(), llm=DemoLLM(), screen=FakeScreen())

    async def request(kind, timeout=300, **data):
        return "always"

    brain.hub.request = request  # type: ignore[method-assign]

    class Started(Exception):
        pass

    async def track(*a, **k):
        raise Started

    brain.jobs.track = track  # type: ignore[method-assign]
    try:
        await brain._drive("tidy the desktop", None)
    except Started:
        pass
    assert grants.active(settings.data_dir, "hands")
