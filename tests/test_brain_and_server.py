"""The brain end to end with the demo model, a fake screen and the client voice (no audio)."""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient
from otto.llm.base import TextPart
from otto.types import LLMResponse, ToolCall, Usage

from conftest import FakeScreen, Recorder
from wisp.companion.brain import Brain
from wisp.demo import DemoLLM
from wisp.hub import Hub
from wisp.server import create_app
from wisp.voice import make_tts


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
        and turn["tool_calls"][1]["tool"] == "show_scene"
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
    assert any(s["agent"] == "Quill" for s in rec.of("say"))

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
    app = create_app(settings, demo=True, brain=brain)
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        hello = json.loads(ws.receive_text())
        assert hello["type"] == "hello" and hello["demo"] and "https" in hello["gallery"]
        ws.send_text(json.dumps({"type": "text", "text": "how do you work?"}))
        seen, deadline = [], time.time() + 20
        while time.time() < deadline:
            seen.append(json.loads(ws.receive_text()))
            if seen[-1]["type"] == "turn_done":
                break
        assert any(e["type"] == "scene" and e["scene"]["title"] == "How Wisp works" for e in seen)
        assert client.get("/file", params={"path": "C:/Windows/win.ini"}).status_code == 404
        assert client.get("/crops/..%2F..%2Fsecret.png").status_code == 404


def test_openrouter_models_need_the_openrouter_key(settings, monkeypatch):
    from wisp.server import has_key, key_env

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    s = settings.model_copy(update={"model": "anthropic/claude-haiku-5.5"})
    assert key_env(s.model) == "OPENROUTER_API_KEY" and not has_key(s)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    assert has_key(s)
    assert key_env("claude-opus-5-5") == "ANTHROPIC_API_KEY"
