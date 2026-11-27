"""An expired Google sign-in is said in plain words with a way to mend it, never as the raw
'invalid_grant' line, and Settings stops calling the account connected and fine."""

import asyncio

import pytest

from pyxi.agents.jobs import describe_failure
from pyxi.companion.brain import friendly_error
from pyxi.connectors import google as g
from pyxi.connectors.google import GOOGLE_EXPIRED, GoogleConnector, GoogleExpired

RAW = (
    "('invalid_grant: Token has been expired or revoked.', "
    "{'error': 'invalid_grant', 'error_description': 'Token has been expired or revoked.'})"
)


class RefreshError(Exception):
    pass


def test_a_job_says_to_sign_in_again():
    assert describe_failure(RuntimeError(RAW)) == GOOGLE_EXPIRED
    assert describe_failure(RefreshError("anything")) == GOOGLE_EXPIRED
    assert describe_failure(GoogleExpired(GOOGLE_EXPIRED)) == GOOGLE_EXPIRED


def test_a_turn_says_the_same():
    assert friendly_error(RuntimeError(RAW)) == GOOGLE_EXPIRED
    assert friendly_error(GoogleExpired(GOOGLE_EXPIRED)) == GOOGLE_EXPIRED
    assert "invalid_grant" not in GOOGLE_EXPIRED and "Settings" in GOOGLE_EXPIRED


class _Dead:
    valid = False
    token = "t"

    def refresh(self, request):
        raise RefreshError(RAW)


def _connector(tmp_path, monkeypatch) -> GoogleConnector:
    c = GoogleConnector.__new__(GoogleConnector)
    c.id, c.name = "google", "Google"
    c.token_path = tmp_path / "token.json"
    c.token_path.write_bytes(b"x")
    c.expired, c._creds, c._tz = False, _Dead(), None
    monkeypatch.setattr(c, "_saved", lambda: {"pyxi_email": "sam@example.com", "scopes": g.SCOPES})
    return c


def test_a_refused_refresh_marks_the_sign_in_expired(tmp_path, monkeypatch):
    c = _connector(tmp_path, monkeypatch)
    assert c.status().needs == ""
    with pytest.raises(GoogleExpired):
        asyncio.run(c._token())
    assert c.expired
    status = c.status()
    assert status.connected and status.needs == "Sign in again"
    assert "run out" in status.detail and "sam@example.com" in status.detail


def test_disconnect_clears_it(tmp_path, monkeypatch):
    c = _connector(tmp_path, monkeypatch)
    c.expired = True
    c.disconnect()
    assert not c.expired
