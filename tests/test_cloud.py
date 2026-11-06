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


def test_your_own_key_always_wins(tmp_path, clean_env, monkeypatch):
    (tmp_path / cloud.FILE).write_text(
        json.dumps({"url": "https://beta.example", "token": "pyxi_x"})
    )
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-mine")
    assert not cloud.apply(tmp_path)
    import os

    assert (
        os.environ["OPENROUTER_API_KEY"] == "sk-or-mine" and "PYXI_OPENROUTER_URL" not in os.environ
    )
