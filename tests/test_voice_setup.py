"""Getting a voice working from the Voice tab, with no settings file and no command: the voice
pack downloads (or is fetched afresh) with progress and a plain line when it fails, and a
cloud voice's key is pasted in, kept in the protected store and never sent back."""

from __future__ import annotations

import asyncio
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from conftest import FakeScreen
from pyxi import plainwords, secretstore
from pyxi.companion.brain import Brain
from pyxi.config import VoiceConfig
from pyxi.demo import DemoLLM
from pyxi.hub import Hub
from pyxi.server import create_app
from pyxi.voice import make_tts
from pyxi.voice import tts as voice_tts
from pyxi.voice.studio import KEY_BAD_SHAPE, VoiceInfo, VoiceStudio

KEY = "test-brain-key"
PASTED = "made-up-voice-key-0123456789"
DEV_WORDS = (".env", "setup-voice", "pyxi/", "terminal", "command", "API_KEY")


# ---------------------------------------------------------------- the download
def _transport(bodies: dict[str, bytes], *, cut: str = "") -> httpx.MockTransport:
    def answer(request: httpx.Request) -> httpx.Response:
        name = request.url.path.rsplit("/", 1)[-1]
        body = bodies[name]
        if name == cut:  # the server promises more than arrives: the connection dropped
            return httpx.Response(200, headers={"content-length": str(len(body) + 50)}, content=body)
        return httpx.Response(200, content=body)

    return httpx.MockTransport(answer)


BODIES = {"kokoro-v1.0.onnx": b"m" * 5000, "voices-v1.0.bin": b"v" * 1000}


async def test_the_voice_pack_downloads_and_says_how_far(tmp_path):
    seen: list[tuple[int, int]] = []
    async with httpx.AsyncClient(transport=_transport(BODIES)) as client:
        out = await voice_tts.download_kokoro(
            tmp_path, progress=lambda done, total: seen.append((done, total)), client=client
        )
    assert [p.name for p in out] == list(BODIES)
    assert (tmp_path / "kokoro-v1.0.onnx").read_bytes() == BODIES["kokoro-v1.0.onnx"]
    assert seen and seen[-1] == (6000, 6000)  # ends at all of it
    assert [done for done, _ in seen] == sorted(done for done, _ in seen)  # never backwards
    assert not list(tmp_path.glob("*.part"))


async def test_a_repair_replaces_only_once_the_new_file_is_whole(tmp_path):
    for name in BODIES:
        (tmp_path / name).write_bytes(b"old")
    # Not asked to repair: what is here is kept and nothing is fetched.
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500))) as client:
        await voice_tts.download_kokoro(tmp_path, client=client)
    assert (tmp_path / "voices-v1.0.bin").read_bytes() == b"old"
    # A repair that breaks off part-way leaves the working voice as it was.
    async with httpx.AsyncClient(transport=_transport(BODIES, cut="kokoro-v1.0.onnx")) as client:
        with pytest.raises(OSError, match="broke off") as failed:
            await voice_tts.download_kokoro(tmp_path, force=True, client=client)
    assert (tmp_path / "kokoro-v1.0.onnx").read_bytes() == b"old"
    assert not list(tmp_path.glob("*.part"))
    assert plainwords.voice_pack_failure(failed.value) == plainwords.VOICE_PACK_OFFLINE
    # And one that finishes replaces both.
    async with httpx.AsyncClient(transport=_transport(BODIES)) as client:
        await voice_tts.download_kokoro(tmp_path, force=True, client=client)
    assert (tmp_path / "voices-v1.0.bin").read_bytes() == BODIES["voices-v1.0.bin"]


def test_every_way_the_download_fails_says_what_to_do():
    lines = {
        plainwords.voice_pack_failure(httpx.ConnectError("getaddrinfo failed")),
        plainwords.voice_pack_failure(OSError(28, "No space left on device")),
        plainwords.voice_pack_failure(PermissionError(13, "Permission denied")),
        plainwords.voice_pack_failure(ValueError("KeyError: 'x'")),
        plainwords.VOICE_PACK_LATER,
    }
    assert len(lines) == 5
    for line in lines:
        assert "Download" in line and not any(w in line for w in DEV_WORDS)
        assert "Error" not in line and "—" not in line


# ---------------------------------------------------------------- the keys
@pytest.fixture
def studio(tmp_path, monkeypatch):
    for name in ("ELEVENLABS_API_KEY", "XI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    return VoiceStudio(VoiceConfig(), tmp_path / "models", tmp_path / "voice.json")


async def test_a_pasted_key_is_kept_protected_and_never_handed_back(studio, tmp_path, caplog):
    assert studio.available()["gemini"] is False
    with caplog.at_level("DEBUG"):
        ok, said = await studio.save_key("gemini", f"  {PASTED}\n")
    assert ok and said.startswith("Saved on this PC.")
    assert studio.available()["gemini"] is True and studio.keys.get("gemini") == PASTED
    public = studio.public()
    assert public["own_keys"] == {"elevenlabs": False, "gemini": True}
    assert PASTED not in json.dumps(public) and PASTED not in said and PASTED not in caplog.text
    saved = tmp_path / "voice_keys.json"
    if secretstore.available():  # Windows: encrypted for this user, as the Google sign-in is
        assert secretstore.encrypted(saved) and PASTED.encode() not in saved.read_bytes()
    # It is still there after a restart, and gone once removed.
    again = VoiceStudio(VoiceConfig(), tmp_path / "models", tmp_path / "voice.json")
    assert again.keys.get("gemini") == PASTED
    again.update(engine="gemini")
    assert "removed from this PC" in again.forget_key("gemini")
    assert not saved.exists() and again.available()["gemini"] is False
    assert again.prefs.engine == "kokoro"  # back to the voice on this PC


async def test_something_that_is_not_a_key_is_turned_away(studio, tmp_path):
    for bad in ("", "short", "two words here ok", "x" * 500):
        assert await studio.save_key("gemini", bad) == (False, KEY_BAD_SHAPE)
    assert (await studio.save_key("nonsense", PASTED))[0] is False
    assert not (tmp_path / "voice_keys.json").exists()
    assert not any(w in KEY_BAD_SHAPE for w in DEV_WORDS)


async def test_an_elevenlabs_key_is_tried_once_and_dropped_if_turned_down(studio, monkeypatch):
    async def refused():
        studio.last_error = "Couldn't load your ElevenLabs voices: its key was turned down. Check the ElevenLabs key."
        return []

    monkeypatch.setattr(studio, "_eleven_catalog", refused)
    ok, said = await studio.save_key("elevenlabs", PASTED)
    assert not ok and said.startswith("ElevenLabs turned that key down.")
    assert studio.keys.get("elevenlabs") is None and PASTED not in said

    async def fine():
        return [VoiceInfo(id="v1", name="Robin", vibe="Warm", engine="elevenlabs")]

    monkeypatch.setattr(studio, "_eleven_catalog", fine)
    assert (await studio.save_key("elevenlabs", PASTED))[0] is True
    assert studio.available()["elevenlabs"] is True


def test_a_key_in_the_environment_still_wins(studio, monkeypatch):
    studio.keys.save("gemini", PASTED)
    monkeypatch.setenv("GEMINI_API_KEY", "made-up-key-from-the-environment")
    assert studio.keys.get("gemini") == "made-up-key-from-the-environment"


# ---------------------------------------------------------------- end to end, over the socket
def _until(ws, want, seconds: float = 15) -> list[dict]:
    seen, deadline = [], time.time() + seconds
    while time.time() < deadline:
        seen.append(json.loads(ws.receive_text()))
        if want(seen[-1]):
            return seen
    raise AssertionError(f"never came: {[e['type'] for e in seen]}")


def _app(settings, tmp_path):
    hub = Hub()
    tts = make_tts(VoiceConfig(tts="kokoro"), settings.models_dir, tmp_path / "voice.json")
    brain = Brain(settings, hub, llm=DemoLLM(), screen=FakeScreen(), tts=tts)
    return create_app(settings, demo=True, brain=brain, key=KEY), brain


def test_the_button_downloads_the_pack_with_progress(settings, tmp_path, monkeypatch):
    calls: list[bool] = []

    async def fake(models, *, force=False, progress=None, client=None):
        calls.append(force)
        models.mkdir(parents=True, exist_ok=True)
        for step in (10, 55, 100):
            progress(step, 100)
            await asyncio.sleep(0.3)
        for name in BODIES:
            (models / name).write_bytes(b"x")
        return []

    monkeypatch.setattr(voice_tts, "download_kokoro", fake)
    app, brain = _app(settings, tmp_path)
    brain.tts.failed = "the voice was missing a moment ago"
    with TestClient(app) as client, client.websocket_connect(f"/ws?key={KEY}") as ws:
        json.loads(ws.receive_text())
        ws.send_text(json.dumps({"type": "voice_pack"}))
        seen = _until(ws, lambda e: e["type"] == "voice_catalog")
    pack = [e for e in seen if e["type"] == "voice_pack"]
    # In order: how far, then ready, and nothing after "ready" that says it is still going.
    assert pack[0]["state"] == "downloading" and pack[-1]["state"] == "ready"
    assert [e["state"] for e in pack].count("ready") == 1
    assert {10, 55} <= {e["percent"] for e in pack} and pack[-1]["percent"] == 100
    assert seen[-1]["voice"]["available"]["kokoro"] is True
    assert calls == [False] and brain.tts.failed is None  # Pyxi's own voice talks again


def test_a_failed_download_says_one_plain_line(settings, tmp_path, monkeypatch):
    async def fake(models, *, force=False, progress=None, client=None):
        raise httpx.ConnectError("[Errno 11001] getaddrinfo failed")

    monkeypatch.setattr(voice_tts, "download_kokoro", fake)
    app, _ = _app(settings, tmp_path)
    with TestClient(app) as client, client.websocket_connect(f"/ws?key={KEY}") as ws:
        json.loads(ws.receive_text())
        ws.send_text(json.dumps({"type": "voice_pack", "repair": True}))
        seen = _until(ws, lambda e: e["type"] == "voice_pack" and e["state"] == "failed")
    assert seen[-1]["message"] == plainwords.VOICE_PACK_OFFLINE
    assert "Errno" not in json.dumps(seen)


def test_a_key_saved_from_the_tab_never_comes_back_over_the_socket(settings, tmp_path, monkeypatch):
    for name in ("ELEVENLABS_API_KEY", "XI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    app, brain = _app(settings, tmp_path)
    with TestClient(app) as client, client.websocket_connect(f"/ws?key={KEY}") as ws:
        json.loads(ws.receive_text())
        ws.send_text(json.dumps({"type": "voice_key", "engine": "gemini", "key": PASTED}))
        seen = _until(ws, lambda e: e["type"] == "voice_catalog")
        said = next(e for e in seen if e["type"] == "voice_key")
        assert said["ok"] and said["engine"] == "gemini"
        assert seen[-1]["voice"]["available"]["gemini"] and seen[-1]["voice"]["own_keys"]["gemini"]
        assert PASTED not in json.dumps(seen)
        ws.send_text(json.dumps({"type": "voice_key", "engine": "gemini", "forget": True}))
        seen = _until(ws, lambda e: e["type"] == "voice_catalog")
        assert seen[-1]["voice"]["available"]["gemini"] is False
    assert brain.studio.keys.get("gemini") is None
