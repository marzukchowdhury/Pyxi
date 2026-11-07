"""Tokens at rest: encrypted with DPAPI, old plain files migrate, a bad file means sign in again."""

from __future__ import annotations

import json
import sys

import pytest

from pyxi import cloud, secretstore
from pyxi.connectors.google import GoogleConnector
from pyxi.connectors.linkedin import LinkedInConnector

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows only")

TOKEN = "pyxi_" + "a1b2c3" * 6
GOOGLE = {
    "token": "ya29.secret-access",
    "refresh_token": "1//secret-refresh",
    "client_id": "cid.apps.googleusercontent.com",
    "client_secret": "csecret",
    "token_uri": "https://oauth2.googleapis.com/token",
    "scopes": ["https://www.googleapis.com/auth/gmail.modify"],
    "pyxi_email": "me@example.com",
}


def test_written_secrets_are_encrypted_and_read_back(tmp_path):
    path = tmp_path / "cloud.json"
    secretstore.write_json(path, {"url": "https://beta.example", "token": TOKEN})
    body = path.read_bytes()
    assert body.startswith(secretstore.MAGIC)
    assert TOKEN.encode() not in body and b"beta.example" not in body
    assert secretstore.read_json(path) == {"url": "https://beta.example", "token": TOKEN}
    assert not (tmp_path / "cloud.json.tmp").exists()


def test_another_program_without_pyxis_entropy_cant_decrypt(tmp_path):
    path = tmp_path / "google.json"
    secretstore.write_json(path, GOOGLE)
    blob = path.read_bytes()[len(secretstore.MAGIC):]
    entropy = secretstore._ENTROPY
    try:
        secretstore._ENTROPY = b""
        with pytest.raises(secretstore.SecretError):
            secretstore.unprotect(blob)
    finally:
        secretstore._ENTROPY = entropy


def test_an_old_plain_file_is_encrypted_on_first_read(tmp_path):
    path = tmp_path / "google.json"
    path.write_text(json.dumps(GOOGLE), encoding="utf-8")
    assert secretstore.read_json(path) == GOOGLE
    assert secretstore.encrypted(path) and b"secret-refresh" not in path.read_bytes()
    assert secretstore.read_json(path) == GOOGLE


@pytest.mark.parametrize(
    "body",
    [
        secretstore.MAGIC + b"\x00garbage, or a file copied from another PC",
        secretstore.MAGIC,
        b"{not json",
        b"[1, 2]",
        b"\xff\xfe\x00",
    ],
)
def test_an_unreadable_file_means_sign_in_again_never_a_crash(tmp_path, body):
    path = tmp_path / "cloud.json"
    path.write_bytes(body)
    assert secretstore.read_json(path) is None
    assert not path.exists()  # dropped, so the next sign-in starts clean
    assert secretstore.read_json(tmp_path / "missing.json") is None


def test_beta_token_is_stored_encrypted_and_old_files_migrate(tmp_path, monkeypatch):
    for var in ("PYXI_BETA", "OPENROUTER_API_KEY", "PYXI_OPENROUTER_URL", "PYXI_MODEL", "PYXI_OWN_KEY"):
        monkeypatch.delenv(var, raising=False)
    (tmp_path / cloud.FILE).write_text(json.dumps({"url": "https://b.example", "token": TOKEN}))
    assert cloud.apply(tmp_path) and cloud.in_beta()
    assert secretstore.encrypted(tmp_path / cloud.FILE)
    assert TOKEN.encode() not in (tmp_path / cloud.FILE).read_bytes()
    assert cloud.saved(tmp_path)["token"] == TOKEN
    assert cloud.leave(tmp_path) and cloud.saved(tmp_path) is None


def test_a_corrupt_beta_file_just_leaves_the_beta(tmp_path, monkeypatch):
    monkeypatch.delenv("PYXI_BETA", raising=False)
    (tmp_path / cloud.FILE).write_bytes(secretstore.MAGIC + b"broken")
    assert cloud.apply(tmp_path) is False and not cloud.in_beta()


async def test_beta_join_saves_the_token_encrypted(tmp_path, monkeypatch):
    import httpx

    def reply(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"token": TOKEN})

    real = httpx.AsyncClient
    monkeypatch.setattr(
        cloud.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(reply), **kw)
    )
    assert await cloud.join(tmp_path, "INVITE") == "ok"
    assert secretstore.encrypted(tmp_path / cloud.FILE)
    assert cloud.saved(tmp_path)["token"] == TOKEN


async def test_google_tokens_encrypted_migrated_and_a_bad_file_asks_to_connect(tmp_path):
    g = GoogleConnector(tmp_path / "connectors", tmp_path / "downloads")
    g.token_path.parent.mkdir(parents=True)
    g.token_path.write_text(json.dumps(GOOGLE), encoding="utf-8")  # from an older Pyxi
    assert g.connected() and g.status().detail.startswith("me@example.com")
    assert secretstore.encrypted(g.token_path) and b"ya29" not in g.token_path.read_bytes()
    assert g.granted_scopes() == set(GOOGLE["scopes"])

    g.token_path.write_bytes(secretstore.MAGIC + b"corrupt")
    g._creds = None
    assert not g.connected() and not g.status().connected
    with pytest.raises(RuntimeError, match="press Connect"):
        await g._token()


def test_linkedin_token_encrypted_and_old_files_migrate(tmp_path):
    import time

    li = LinkedInConnector(tmp_path)
    li.token_path.write_text(json.dumps({"access_token": "AQX-secret", "expires_at": time.time() + 3600}))
    assert li.connected()
    assert secretstore.encrypted(li.token_path) and b"AQX-secret" not in li.token_path.read_bytes()
    li.token_path.write_bytes(b"\x00\x01")
    assert not li.connected()
