"""The beta: an invite code becomes a token, and models then go through the beta server."""

import json

import httpx
import pytest

from pyxi import cloud


@pytest.fixture
def clean_env(monkeypatch):
    for k in (
        "OPENROUTER_API_KEY",
        "ANTHROPIC_API_KEY",
        "PYXI_OPENROUTER_URL",
        "PYXI_MODEL",
        "PYXI_BETA",
    ):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("PYXI_CLOUD_URL", "https://beta.example")


async def test_joining_saves_a_token_and_routes_the_models(tmp_path, clean_env, monkeypatch):
    def reply(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://beta.example/api/join"
        code = json.loads(request.content)["code"]
        if code != "FIREFLY":
            return httpx.Response(403, json={"error": "That invite code isn't valid."})
        return httpx.Response(200, json={"token": "pyxi_" + "a" * 48})

    real = httpx.AsyncClient
    monkeypatch.setattr(
        cloud.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(reply), **kw)
    )
    assert await cloud.join(tmp_path, "nope") == "That invite code isn't valid."
    assert not cloud.apply(tmp_path)
    assert await cloud.join(tmp_path, " FIREFLY ") == "ok"
    assert cloud.apply(tmp_path)
    import os

    assert os.environ["OPENROUTER_API_KEY"].startswith("pyxi_")
    assert os.environ["PYXI_OPENROUTER_URL"] == "https://beta.example/api/v1"
    assert os.environ["PYXI_MODEL"] == cloud.BETA_MODEL and cloud.in_beta()


def test_without_joining_your_own_key_is_used(tmp_path, clean_env, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-mine")
    assert not cloud.apply(tmp_path)
    import os

    assert "PYXI_OPENROUTER_URL" not in os.environ and not cloud.in_beta()
    assert cloud.had_own_key()


def test_an_invite_code_wins_over_a_key_already_on_the_pc(tmp_path, clean_env, monkeypatch):
    """Developers often have ANTHROPIC_API_KEY set for other tools; joining must still work."""
    import os

    for var in cloud.MODEL_VARS:
        monkeypatch.delenv(var, raising=False)
    (tmp_path / cloud.FILE).write_text(
        json.dumps({"url": "https://beta.example", "token": "pyxi_x"})
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-mine")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-mine")
    monkeypatch.setenv("PYXI_MODEL", "claude-opus-5-5")  # a native Anthropic model: their key
    monkeypatch.setenv("PYXI_MODEL_3D", "claude-opus-5-5")
    monkeypatch.setenv("PYXI_MODEL_AIM", "anthropic/claude-sonnet-5.5")  # the beta allows this
    assert cloud.apply(tmp_path) and cloud.in_beta()
    assert os.environ["OPENROUTER_API_KEY"] == "pyxi_x"
    assert os.environ["PYXI_OPENROUTER_URL"] == "https://beta.example/api/v1"
    assert os.environ["PYXI_MODEL"] == cloud.BETA_MODEL
    assert os.environ["PYXI_MODEL_3D"] == ""  # falls back to the chat model, not their key
    assert os.environ["PYXI_MODEL_AIM"] == "anthropic/claude-sonnet-5.5"
    assert cloud.had_own_key()  # so Settings can offer "Use my own key instead"


def test_leaving_the_beta_goes_back_to_your_own_key(tmp_path, clean_env, monkeypatch):
    (tmp_path / cloud.FILE).write_text(json.dumps({"token": "pyxi_x"}))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-mine")
    assert cloud.leave(tmp_path) and not (tmp_path / cloud.FILE).exists()
    assert not cloud.leave(tmp_path)
    assert not cloud.apply(tmp_path)


def test_old_conversations_and_their_screenshots_are_forgotten(tmp_path):
    import sqlite3
    import time

    from pyxi.companion.turns import TurnStore

    store = TurnStore(tmp_path / "t.db", tmp_path / "turns")
    old_img = store.save_image("old", b"jpg")
    new_img = store.save_image("new", b"jpg")
    with sqlite3.connect(tmp_path / "t.db") as c:
        c.execute(
            "INSERT INTO turns (id, ts, image_path) VALUES ('old', ?, ?)",
            (time.time() - 40 * 86400, old_img),
        )
        c.execute(
            "INSERT INTO turns (id, ts, image_path) VALUES ('new', ?, ?)", (time.time(), new_img)
        )
    assert store.prune(30) == 1
    from pathlib import Path

    assert not Path(old_img).exists() and Path(new_img).exists()


def test_a_used_up_allowance_reads_plainly():
    from pyxi.companion.brain import friendly_error

    raw = "OpenRouter API error: Error code: 429 - You've used today's Pyxi beta allowance. It resets at midnight UTC."
    assert friendly_error(Exception(raw)) == "You've used today's Pyxi beta allowance. It resets at midnight UTC."
    raw = "Error code: 429 - {'error': {'message': 'The Pyxi beta is at its limit for this month. Back soon.'}}"
    assert friendly_error(Exception(raw)) == "The Pyxi beta is at its limit for this month. Back soon."


def test_testers_use_the_google_client_the_installer_ships(tmp_path, monkeypatch):
    """No client of their own: the one beside the bundled Python is used, so Connect just works."""
    import sys

    from pyxi.connectors.google import GoogleConnector

    for k in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"):
        monkeypatch.delenv(k, raising=False)
    store = tmp_path / ".pyxi" / "connectors"
    store.mkdir(parents=True)
    g = GoogleConnector(store, tmp_path)
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "python"))
    assert g.client_config() is None
    (tmp_path / "python").mkdir()
    (tmp_path / "python" / "google_client.json").write_text(json.dumps({"installed": {"client_id": "pyxi"}}))
    assert g.client_config()["installed"]["client_id"] == "pyxi"
    (store.parent / "google_client.json").write_text(json.dumps({"installed": {"client_id": "mine"}}))
    assert g.client_config()["installed"]["client_id"] == "mine"  # their own still wins


def test_the_brain_quits_when_its_app_does():
    """After an update an old brain must never be left running on its own."""
    import subprocess
    import sys

    if sys.platform != "win32":
        return
    app = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(1.5)"])
    brain = subprocess.Popen(
        [sys.executable, "-c", "import sys, time; from pyxi.cli import _quit_with_app; _quit_with_app(sys.argv[1]); time.sleep(60)", str(app.pid)]
    )
    assert brain.wait(timeout=20) == 0  # well before its 60 s sleep: it followed the app out
    app.wait()
