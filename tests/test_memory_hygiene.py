"""Memory hygiene: no passwords, card numbers, ID numbers or keys in memory (Pyxi's or the
teammates'); "Forget this" on a memory; "Wipe everything" after a card."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from otto.storage import MemoryStore

from pyxi import memory_guard
from pyxi.memory_guard import SensitiveMemory, why_sensitive


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("my Gmail password is Hunter2!", "a password or code"),
        ("wifi pwd: tr0ub4dor", "a password or code"),
        ("Visa 4111 1111 1111 1111 exp 12/29", "a card number"),
        ("card 5500-0000-0000-0004", "a card number"),
        ("SSN 078-05-1120", "a Social Security number"),
        ("SIN 046 454 286", "a Social Insurance number"),
        ("passport number GA1234567", "an ID number"),
        ("health card 9876 543 210", "an ID number"),
        ("OPENROUTER_API_KEY=sk-or-v1-" + "a" * 40, "an API key or token"),
        ("token ghp_" + "b" * 36, "an API key or token"),
    ],
)
def test_secrets_are_recognised(text, kind):
    assert why_sensitive(text) == kind


@pytest.mark.parametrize(
    "text",
    [
        "Prefers window seats and oat milk",
        "Their dentist is Maple Grove Dental, 604-555-0199",
        "Order #702-8812345-1234567 arrived",  # an order number isn't a card (no check digit)
        "Lives in Vancouver, works at Northwind",
        "Birthday is 12 March",
    ],
)
def test_ordinary_memories_are_fine(text):
    assert why_sensitive(text) is None


def test_the_guard_covers_every_memory_store(tmp_path):
    memory_guard.install()
    store = MemoryStore(tmp_path / "otto.db")  # a teammate's store, as Scout's runs use
    with pytest.raises(SensitiveMemory, match="never saves passwords"):
        store.remember("scout", "login", "the password is S3cret!!")
    store.remember("scout", "seat", "aisle")
    assert [m.key for m in store.all()] == ["seat"]


async def test_pyxi_says_it_wont_remember_a_secret(settings):
    from conftest import FakeScreen
    from pyxi.companion.brain import Brain
    from pyxi.demo import DemoLLM
    from pyxi.hub import Hub

    brain = Brain(settings, Hub(), llm=DemoLLM(), screen=FakeScreen())
    note = await brain._act("remember", {"key": "bank", "content": "PIN is 4321"})
    assert note.startswith("remember: That looks like a password or code") and not brain.memory.all()
    assert await brain._act("remember", {"key": "coffee", "content": "flat white"}) is None


def test_forget_this_and_wipe_everything_after_a_card(settings):
    from conftest import FakeScreen
    from pyxi.companion.brain import Brain
    from pyxi.demo import DemoLLM
    from pyxi.hub import Hub
    from pyxi.server import create_app

    hub = Hub()
    brain = Brain(settings, hub, llm=DemoLLM(), screen=FakeScreen())
    brain.memory.remember("agent:pyxi", "coffee", "flat white")
    brain.memory.remember("agent:pyxi", "city", "Vancouver")
    (settings.data_dir / "otto").mkdir(parents=True, exist_ok=True)
    MemoryStore(settings.data_dir / "otto" / "otto.db").remember("scout", "seat", "aisle")
    app = create_app(settings, demo=True, brain=brain, key="k")
    with TestClient(app) as client, client.websocket_connect("/ws?key=k") as ws:
        ws.receive_text()
        ws.send_text(json.dumps({"type": "memories_get"}))
        listed = json.loads(ws.receive_text())["memories"]
        assert {(m["store"], m["key"]) for m in listed} == {("pyxi", "coffee"), ("pyxi", "city"), ("teammates", "seat")}
        ws.send_text(json.dumps({"type": "memory_forget", "store": "pyxi", "scope": "agent:pyxi", "key": "coffee"}))
        assert {m["key"] for m in json.loads(ws.receive_text())["memories"]} == {"city", "seat"}
        # Wipe everything: a card first, and Don't keeps it all.
        ws.send_text(json.dumps({"type": "memories_wipe"}))
        events = [json.loads(ws.receive_text()) for _ in range(2)]
        card = next(e for e in events if e["type"] == "confirm")
        assert card["action"] == "Wipe everything Pyxi remembers?" and "2 memories" in card["reason"]
        ws.send_text(json.dumps({"type": "answer", "request_id": card["request_id"], "value": False}))
        while (e := json.loads(ws.receive_text()))["type"] != "notice":
            pass
        assert e["message"] == "Okay, nothing was wiped." and len(brain.memories_public()) == 2
        ws.send_text(json.dumps({"type": "memories_wipe"}))
        while (e := json.loads(ws.receive_text()))["type"] != "confirm":
            pass
        ws.send_text(json.dumps({"type": "answer", "request_id": e["request_id"], "value": True}))
        while (e := json.loads(ws.receive_text()))["type"] != "notice":
            pass
        assert e["message"].startswith("Done: Pyxi has forgotten everything")
    assert brain.memories_public() == []
