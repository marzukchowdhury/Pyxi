"""An expired Google sign-in is said in plain words, never as the raw 'invalid_grant' line."""

from pyxi.agents.jobs import GOOGLE_EXPIRED, describe_failure
from pyxi.companion.brain import friendly_error

RAW = (
    "('invalid_grant: Token has been expired or revoked.', "
    "{'error': 'invalid_grant', 'error_description': 'Token has been expired or revoked.'})"
)


class RefreshError(Exception):
    pass


def test_a_job_says_to_connect_google_again():
    assert describe_failure(RuntimeError(RAW)) == GOOGLE_EXPIRED
    assert describe_failure(RefreshError("anything")) == GOOGLE_EXPIRED


def test_a_turn_says_the_same():
    assert friendly_error(RuntimeError(RAW)) == GOOGLE_EXPIRED
    assert "invalid_grant" not in GOOGLE_EXPIRED and "Settings" in GOOGLE_EXPIRED
