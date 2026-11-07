"""The brain end to end with the demo model, a fake screen and the client voice (no audio)."""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient
from otto.llm.base import TextPart
from otto.types import LLMResponse, ToolCall, Usage

from conftest import FakeScreen, Recorder
from pyxi.companion.brain import Brain
from pyxi.demo import DemoLLM
from pyxi.hub import Hub
from pyxi.server import create_app
from pyxi.voice import make_tts

KEY = "test-launch-key"  # what the desktop app would have made at launch
KEYED = {"x-pyxi-key": KEY}


def make_brain(settings, llm=None, otto_llm=None):
    hub = Hub()
    rec = Recorder(hub)
    brain = Brain(
        settings,
        hub,
        llm=llm or DemoLLM(),
        otto_llm=otto_llm,
        screen=FakeScreen(),
        tts=make_tts(settings.voice, settings.models_dir),
    )
    brain.jobs.headed = False
    return brain, rec


async def test_question_becomes_a_3d_scene_with_narration(settings):
    brain, rec = make_brain(settings)
    await brain.turn("how does https work?")
    await brain.jobs.wait_all()
    [scene] = rec.of("scene")
    assert scene["scene"]["title"] == "How HTTPS keeps you safe"
    assert rec.of("say")[0]["text"].startswith("Ooh")
    await __import__("asyncio").sleep(0.05)
    assert len(rec.of("scene_audio")) == len(scene["scene"]["steps"])
    assert [e["state"] for e in rec.of("state")][-1] == "idle"
    turn = brain.turns.recent(1)[0]
    assert (
        turn["user_text"] == "how does https work?"
        and turn["image_path"]
        and turn["tool_calls"][0]["tool"] == "show_scene"  # shown first, then spoken
    )


async def test_pointing_is_synced_to_the_sentence(settings):
    brain, rec = make_brain(settings)
    await brain.turn("where is the export button?")
    [say] = rec.of("say")
    assert say["text"] == "Right here!" and say["annotations"][0]["kind"] == "point"
    assert say["annotations"][0]["at"] == [760, 670]  # centre of the Export button, in screen px


async def test_explode_view_and_memory_palace(settings):
    brain, rec = make_brain(settings)
    await brain.turn("explode this panel")
    scene = rec.of("scene")[-1]["scene"]
    assert scene["layout"] == "exploded" and len(scene["nodes"]) >= 3

    # Causal replay: two turns already logged as activity, plus a click.
    brain.activity.focus_changed(
        "app", "Settings", "Dark mode", "CheckBox", (140, 170, 200, 30), None
    )
    await brain.turn("how did I get here?")
    causal = rec.of("scene")[-1]["scene"]
    assert causal["layout"] == "timeline" and causal["title"] == "How you got here"
    assert any("Dark mode" in n["label"] for n in causal["nodes"])

    await brain.turn("take me back to the settings")
    [palace] = rec.of("palace")
    assert palace["scene"]["mode"] == "hologram" and palace["timeline"]


async def test_daily_budget_stops_the_brain(settings):
    settings = settings.model_copy(
        update={"budget": settings.budget.model_copy(update={"daily_cost_usd": 0.01})}
    )
    brain, rec = make_brain(settings)
    brain.turns.add_spend("turn", 0.02)
    await brain.turn("how does https work?")
    assert not rec.of("scene") and "budget" in rec.of("say")[0]["text"]


class _ScriptedBrain:
    """A model that delegates to Scout, then hands off to Quill."""

    model = "scripted"

    async def complete(self, *, system, messages, tools=None, max_tokens=None):
        prompt = "\n".join(p.text for m in messages for p in m.parts if isinstance(p, TextPart))
        if tools and any(t.name == "delegate" for t in tools):
            return LLMResponse(
                usage=Usage(cost_usd=0.001),
                tool_calls=[
                    ToolCall(
                        id="1",
                        name="say",
                        arguments={"text": "Scout's on it, and Quill will write it up."},
                    ),
                    ToolCall(
                        id="2",
                        name="delegate",
                        arguments={
                            "agent": "scout",
                            "task": "Fill the form",
                            "start_url": self.url,
                            "then_agent": "quill",
                            "then_task": "Draft a two-line note about what Scout did.",
                        },
                    ),
                ],
            )
        return LLMResponse(text=f"Draft based on: {prompt[-120:]}", usage=Usage(cost_usd=0.002))


@pytest.mark.browser
async def test_scout_then_quill_handoff_and_ghost_walkthrough(settings):
    from otto.demo_site import DemoSite
    from otto.demo_site.scripted_demo import demo_provider

    with DemoSite() as site:
        model = _ScriptedBrain()
        model.url = site.url
        brain, rec = make_brain(settings, llm=model, otto_llm=demo_provider())
        await brain.turn("fill the demo form and write me a note")
        await brain.jobs.wait_all()
    jobs = {e["job"]["agent_id"]: e["job"] for e in rec.of("job")}
    assert jobs["scout"]["status"] == "done" and jobs["scout"]["steps"] >= 8
    assert jobs["quill"]["status"] == "done" and jobs["quill"]["parent"] == jobs["scout"]["id"]
    assert "All set!" in jobs["quill"]["context"], "Quill receives Scout's result"
    assert rec.of("handoff")[0]["target"] == "quill" and rec.of("job_frame")
    # Background work is quiet: teammates don't speak, the user gets a notification instead.
    assert not any(s["agent"] in ("Scout", "Quill") for s in rec.of("say"))
    ready = rec.of("job_ready")[-1]
    assert ready["agent"] == "Quill" and ready["ok"] and ready["title"] == "Your draft is ready"

    await brain.start_walkthrough()
    walk = rec.of("walkthrough")[0]["walkthrough"]
    clicks = [s for s in walk["steps"] if s["action"] == "click"]
    assert clicks and all(s["target_box"] and s["image"] for s in clicks)


def test_server_websocket_round_trip(settings):
    hub = Hub()
    brain = Brain(
        settings,
        hub,
        llm=DemoLLM(),
        screen=FakeScreen(),
        tts=make_tts(settings.voice, settings.models_dir),
    )
    app = create_app(settings, demo=True, brain=brain, key=KEY)
    with TestClient(app, headers=KEYED) as client, client.websocket_connect(f"/ws?key={KEY}") as ws:
        hello = json.loads(ws.receive_text())
        assert hello["type"] == "hello" and hello["demo"] and "https" in hello["gallery"]
        ws.send_text(json.dumps({"type": "text", "text": "how do you work?"}))
        seen, deadline = [], time.time() + 20
        while time.time() < deadline:
            seen.append(json.loads(ws.receive_text()))
            if seen[-1]["type"] == "turn_done":
                break
        assert any(e["type"] == "scene" and e["scene"]["title"] == "How Pyxi works" for e in seen)
        assert client.get("/file", params={"path": "C:/Windows/win.ini"}).status_code == 404
        assert client.get("/crops/..%2F..%2Fsecret.png").status_code == 404
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        (settings.data_dir / "cloud.json").write_text("{}")
        shot = settings.data_dir / "step_clean.jpg"
        shot.write_bytes(bytes([0xFF, 0xD8]))
        # Inside the data folder, still only pictures: never tokens, keys or databases.
        assert client.get("/file", params={"path": str(settings.data_dir / "cloud.json")}).status_code == 404
        assert client.get("/file", params={"path": str(shot)}).status_code == 200


def test_settings_offers_the_beta_to_someone_with_their_own_key(settings, monkeypatch):
    """A key already on the PC (ANTHROPIC_API_KEY for other tools) must not hide the beta card."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-mine")
    monkeypatch.setenv("PYXI_OWN_KEY", "1")
    monkeypatch.delenv("PYXI_BETA", raising=False)
    hub = Hub()
    brain = Brain(settings, hub, llm=DemoLLM(), screen=FakeScreen(), tts=make_tts(settings.voice, settings.models_dir))
    app = create_app(settings, demo=False, brain=brain, key=KEY)
    with TestClient(app) as client, client.websocket_connect(f"/ws?key={KEY}") as ws:
        hello = json.loads(ws.receive_text())
    assert hello["beta"] == {"joined": False, "can_join": True, "own_key": True}


def test_openrouter_models_need_the_openrouter_key(settings, monkeypatch):
    from pyxi.server import has_key, key_env

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    s = settings.model_copy(update={"model": "anthropic/claude-haiku-5.5"})
    assert key_env(s.model) == "OPENROUTER_API_KEY" and not has_key(s)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    assert has_key(s)
    assert key_env("claude-opus-5-5") == "ANTHROPIC_API_KEY"


def test_only_pyxis_own_windows_can_reach_the_brain(settings):
    from starlette.websockets import WebSocketDisconnect as Closed

    brain = Brain(
        settings,
        Hub(),
        llm=DemoLLM(),
        screen=FakeScreen(),
        tts=make_tts(settings.voice, settings.models_dir),
    )
    app = create_app(settings, demo=True, brain=brain, key=KEY)
    with TestClient(app) as client:
        evil = {"origin": "https://evil.example"}
        with pytest.raises(Closed), client.websocket_connect(f"/ws?key={KEY}", headers=evil) as ws:
            ws.receive_text()  # a web page is refused even if it somehow had the key
        assert client.get("/api/timeline", headers={**evil, **KEYED}).status_code == 403
        ok = client.get("/api/timeline", headers={"origin": "file://", **KEYED})
        assert ok.status_code == 200 and ok.headers["access-control-allow-origin"] == "file://"
        assert client.get("/api/timeline", headers=KEYED).status_code == 200  # local scripts
        with client.websocket_connect(
            f"/ws?key={KEY}", headers={"origin": "http://localhost:5173"}
        ) as ws:
            assert json.loads(ws.receive_text())["type"] == "hello"


def test_a_program_without_the_launch_key_is_refused(settings, monkeypatch):
    """Any program on the PC can reach 127.0.0.1 and send no Origin. Without the key the desktop
    app made at launch it can't read anything, send requests, or answer an Allow card."""
    from starlette.websockets import WebSocketDisconnect as Closed

    hub = Hub()
    brain = Brain(
        settings, hub, llm=DemoLLM(), screen=FakeScreen(), tts=make_tts(settings.voice, settings.models_dir)
    )
    app = create_app(settings, demo=True, brain=brain, key=KEY)
    answered: list[object] = []
    hub.resolve = lambda rid, value: answered.append((rid, value))  # type: ignore[method-assign]
    with TestClient(app) as client:
        for url in ("/ws", "/ws?key=", "/ws?key=guess", f"/ws?key={KEY}x"):
            with pytest.raises(Closed), client.websocket_connect(url) as ws:
                ws.send_text(json.dumps({"type": "answer", "request_id": "r1", "value": True}))
                ws.receive_text()
        with pytest.raises(Closed), client.websocket_connect(
            "/ws", headers={"origin": "file://", "x-pyxi-key": "nope"}
        ) as ws:
            ws.receive_text()
        for path in ("/api/state", "/api/timeline", "/api/turns", "/api/beta", "/api/activity"):
            assert client.get(path).status_code == 401, path
            assert client.get(path, headers={"x-pyxi-key": "nope"}).status_code == 401, path
        assert client.post("/api/quit").status_code == 401  # can't shut Pyxi down either
        assert client.get("/file", params={"path": str(settings.data_dir / "x.png")}).status_code == 401
        # Health stays open (the starting app asks "is a brain on this port?"), but says whose.
        assert client.get("/api/health").json()["authorized"] is False
        assert client.get("/api/health", headers=KEYED).json()["authorized"] is True
        # The real app's way in: the key as a header (fetch) or ?key= (WebSocket, <img>).
        assert client.get(f"/api/timeline?key={KEY}", headers={"origin": "file://"}).status_code == 200
        with client.websocket_connect(f"/ws?key={KEY}", headers={"origin": "file://"}) as ws:
            assert json.loads(ws.receive_text())["type"] == "hello"
            ws.send_text(json.dumps({"type": "answer", "request_id": "r1", "value": True}))
            ws.send_text(json.dumps({"type": "routines"}))
            assert json.loads(ws.receive_text())["type"] == "routines"
    assert answered == [("r1", True)]  # only the keyed window's answer reached the card


def test_a_brain_started_by_hand_writes_its_key_for_local_tools(settings, monkeypatch, tmp_path):
    from pyxi import brainkey

    monkeypatch.delenv(brainkey.ENV, raising=False)
    first = brainkey.resolve(tmp_path)
    assert len(first) >= 40 and (tmp_path / "brain.key").read_text() == first
    assert brainkey.client_key(tmp_path) == first
    assert brainkey.resolve(tmp_path) != first  # a new key every start
    monkeypatch.setenv(brainkey.ENV, "from-the-app")
    (tmp_path / "brain.key").unlink()
    assert brainkey.resolve(tmp_path) == "from-the-app" and not (tmp_path / "brain.key").exists()
    assert brainkey.client_key(tmp_path) == "from-the-app"
    assert not brainkey.matches("", "") and not brainkey.matches("a", None)
