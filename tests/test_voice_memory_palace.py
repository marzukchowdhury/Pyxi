from __future__ import annotations

import time
from datetime import datetime

import pytest

from conftest import fake_context
from pyxi.agents import AgentRegistry
from pyxi.companion.annotations import split_markers
from pyxi.memory import SpatialMemory
from pyxi.palace import ActivityLog, causal_scene, parse_when, snapshot_scene, strip_when
from pyxi.voice import split_sentences, strip_tags
from pyxi.voice.tts import FallbackTTS, Speech


# ---------------------------------------------------------------- voice
def test_sentence_splitting_and_tags():
    text = (
        "Hi there! I'm Pyxi [laugh]. This is a really long sentence, with lots of clauses, "
        + "and more words, " * 20
        + "done."
    )
    parts = split_sentences(text)
    assert parts[0] == "Hi there!" and all(len(p) <= 220 for p in parts)
    assert strip_tags("Ha [laugh] okay [Chuckle]") == "Ha okay"
    assert split_markers("Hit [point:12] Export, then [circle:3] PNG.") == (
        "Hit Export, then PNG.",
        [("point", 12), ("circle", 3)],
    )


class _Flaky:
    name = "flaky"

    def __init__(self, exc: Exception) -> None:
        self.exc, self.calls = exc, 0

    async def synthesize(self, text, voice=None):
        self.calls += 1
        raise self.exc


async def test_tts_fallback_distinguishes_broken_from_unlucky():
    unlucky = FallbackTTS(_Flaky(ValueError("odd phonemes")))
    assert (await unlucky.synthesize("one")).wav is None
    await unlucky.synthesize("two")
    assert unlucky.primary.calls == 2 and unlucky.failed is None  # still trying the real voice

    broken = FallbackTTS(_Flaky(RuntimeError("model files missing")))
    await broken.synthesize("one")
    await broken.synthesize("two")
    assert broken.primary.calls == 1 and broken.failed  # switched off after it couldn't load
    assert (await broken.synthesize("[laugh]")) == Speech(text="", wav=None)


# ---------------------------------------------------------------- time words
def test_parse_when():
    now = datetime(2026, 11, 4, 15, 30)  # a Wednesday
    start, end = parse_when("last tuesday afternoon", now)
    assert datetime.fromtimestamp(start) == datetime(2026, 11, 3, 12) and datetime.fromtimestamp(
        end
    ) == datetime(2026, 11, 3, 17)
    start, end = parse_when("20 minutes ago", now)
    assert start < datetime(2026, 11, 4, 15, 10).timestamp() < end
    start, end = parse_when("this morning", now)
    assert datetime.fromtimestamp(start).hour == 5 and datetime.fromtimestamp(end).hour == 12
    assert parse_when("yesterday", now)[0] == datetime(2026, 11, 3).timestamp()
    assert parse_when("the pricing page", now) is None
    assert strip_when("the pricing page last Tuesday afternoon") == "the pricing page"


# ---------------------------------------------------------------- spatial memory + palace
def test_spatial_memory_recall_and_time_travel(tmp_path):
    mem = SpatialMemory(tmp_path / "w.db", thumbs_dir=tmp_path / "palace")
    ctx = fake_context(app="Figma", title="Pricing page")
    sid = mem.record(ctx)
    assert mem.record(ctx) is None, "identical screens are deduplicated"
    hits = mem.recall("where was export")
    assert hits[0].name == "Export" and hits[0].rect == (700, 650, 120, 40)
    assert "Figma" in hits[0].describe()

    found = mem.find_snapshots("pricing", start=time.time() - 60)
    assert found[0]["id"] == sid
    snap = mem.snapshot(sid)
    assert snap["thumb_path"] and all(
        e["name"] != "" or e["w"] * e["h"] > 40_000 for e in snap["elements"]
    )
    scene = snapshot_scene(snap, tmp_path / "crops", highlight="export")
    assert scene.layout == "exploded" and any(n.glow and n.label == "Export" for n in scene.nodes)
    assert all(n.image for n in scene.nodes), "thumbnails give every ghost real pixels"
    assert [t["id"] for t in mem.neighbours(sid)] == [sid]


def test_activity_log_and_causal_scene(tmp_path):
    log = ActivityLog(tmp_path / "w.db")
    assert log.window_changed("Figma", "Pricing", None)
    assert log.window_changed("Figma", "Pricing", None) is None
    a = log.focus_changed("Figma", "Pricing", "Auto layout", "Button", (10, 10, 50, 20), None)
    assert (
        log.focus_changed("Figma", "Pricing", "Auto layout", "Button", (10, 10, 50, 20), None)
        is None
    )
    b = log.focus_changed("Figma", "Pricing", "Delete frame", "MenuItem", (10, 40, 90, 20), None)
    events = log.recent(5)
    assert [e.kind for e in events] == ["window", "focus", "focus"]
    assert events[1].headline() == 'Clicked "Auto layout"' and events[2].headline().startswith(
        "Opened menu"
    )
    scene = causal_scene(
        log.get([a, b]),
        [{"from": a, "to": b, "why": "the layout collapsed"}],
        title="Why the frame vanished",
        summary="Two clicks did it.",
        what_ifs=[
            {"after_event_id": a, "action": "undone it", "outcome": "the frame would be back"}
        ],
        crops_dir=tmp_path,
        snapshots={},
    )
    assert scene.layout == "timeline" and any(n.id.startswith("whatif") for n in scene.nodes)
    assert any(e.kind == "dashed" for e in scene.edges) and len(scene.steps) == 3
    pin = log.add_pin("Figma", "Export", "Button", "Use 2x for retina")
    assert log.pins_for("figma")[0].note == "Use 2x for retina"
    log.remove_pin(pin.id)
    assert log.pins_for("Figma") == []


def test_agent_registry(tmp_path):
    reg = AgentRegistry(tmp_path / "w.db")
    assert {a.id for a in reg.all()} >= {"pyxi", "scout", "quill", "sketch"}
    penny = reg.create("Penny", "explainer", "Loves diagrams.")
    again = reg.create("Penny", "writer", "Another one.")
    assert penny.id == "penny" and again.id == "penny-2"
    assert reg.get("PENNY").id == "penny" and not reg.delete("scout") and reg.delete("penny")


@pytest.mark.skipif(
    not __import__("pathlib").Path(".pyxi/models/kokoro-v1.0.onnx").exists(),
    reason="Kokoro not downloaded",
)
async def test_kokoro_speaks():
    from pathlib import Path

    from pyxi.config import VoiceConfig
    from pyxi.voice.tts import KokoroTTS

    speech = await KokoroTTS(VoiceConfig(), Path(".pyxi/models")).synthesize("Hello from Pyxi.")
    assert speech.wav[:4] == b"RIFF" and 0.5 < speech.duration_s < 4
