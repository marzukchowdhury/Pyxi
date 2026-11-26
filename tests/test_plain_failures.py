"""No failure is a dead end: each one says what happened and what to do next, in plain words,
and nothing of Python's, no path with the Windows user name and nothing like a key is ever
shown (pyxi/plainwords.py, the funnel under describe_failure and friendly_error)."""

from __future__ import annotations

import json
import re
import zipfile

import httpx
import pytest

from pyxi import plainwords as pw
from pyxi.agents.jobs import describe_failure
from pyxi.companion.brain import friendly_error
from pyxi.connectors.google import GOOGLE_EXPIRED, google_error

JARGON = re.compile(
    r"(?i)invalid_grant|oauth|\bscopes?\b|\btoken\b|exception|traceback|errno|winerror|"
    r"error code|\bjson\b|\b40[0-9]\b|\b5[0-9][0-9]\b|[{}\[\]]|—"
)


def both(exc: BaseException) -> str:
    """A job and a turn say the same thing."""
    assert describe_failure(exc) == friendly_error(exc)
    return describe_failure(exc)


def plain(line: str) -> str:
    assert line and not JARGON.search(line), line
    assert line[0].isupper() and line.rstrip()[-1] in ".?", line
    return line


# ---------------------------------------------------------------- Google
def said(status: int, message: str) -> RuntimeError:
    return RuntimeError(google_error(status, json.dumps({"error": {"message": message}})))


@pytest.mark.parametrize(
    "message",
    [
        "Request had insufficient authentication scopes.",  # Docs, Sheets, Slides, Drive, Calendar
        "Insufficient Permission",  # Gmail
    ],
)
def test_a_missing_google_permission_says_which_button_to_press(message):
    line = plain(both(said(403, message)))
    assert line == pw.GOOGLE_PERMISSION
    assert "Press Sign in to Google" in line and "tick every box" in line
    assert pw.action_for(line) == "google"  # the card carries the button


@pytest.mark.parametrize(
    "status,message",
    [
        (403, "The caller does not have permission"),  # a sheet in another account
        (403, "The user does not have sufficient permissions for this file."),
        (404, "Requested entity was not found."),
        (404, "File not found: 1AbC."),
    ],
)
def test_a_file_in_another_account_says_so(status, message):
    line = plain(both(said(status, message)))
    assert "another Google account" in line and "share it" in line


def test_google_rate_limit_and_outage():
    assert plain(both(said(429, "Quota exceeded for quota metric 'Read requests'"))) == pw.GOOGLE_BUSY
    assert both(said(403, "Rate Limit Exceeded")) == pw.GOOGLE_BUSY
    assert "Try again in a minute" in pw.GOOGLE_BUSY
    assert plain(both(said(503, "The service is currently unavailable."))) == pw.GOOGLE_DOWN


def test_google_turning_the_sign_in_down_is_the_expired_card():
    assert both(said(401, "Invalid Credentials")) == GOOGLE_EXPIRED
    assert pw.action_for(GOOGLE_EXPIRED) == "google"


def test_no_internet_is_said_as_no_internet():
    for exc in (
        httpx.ConnectError("[Errno 11001] getaddrinfo failed"),
        httpx.ConnectTimeout(""),
        OSError(11001, "getaddrinfo failed"),
        RuntimeError("Cannot connect to host oauth2.googleapis.com:443 ssl:default [getaddrinfo failed]"),
    ):
        line = plain(both(exc))
        assert "internet connection" in line and "ask me again" in line, exc


# ---------------------------------------------------------------- LinkedIn and Discord
def test_linkedin_and_discord_refusals_name_the_account_and_the_way_back():
    line = plain(both(RuntimeError('LinkedIn said 401: {"message":"The token used in the request has expired"}')))
    assert line.startswith("Your LinkedIn sign-in has run out.") and "press Connect next to LinkedIn" in line
    line = plain(both(RuntimeError('Discord said 401: {"message": "401: Unauthorized", "code": 0}')))
    assert "Discord turned down the bot key" in line and "restart Pyxi" in line
    line = plain(both(RuntimeError('Discord said 403: {"message": "Missing Access", "code": 50001}')))
    assert "Add the bot to the server" in line
    assert plain(both(RuntimeError("LinkedIn said 429: slow down"))).startswith("LinkedIn is asking me to slow down")


def test_a_sign_in_that_did_not_finish_says_to_press_connect_again():
    class AccessDeniedError(Exception):
        pass

    line = plain(pw.connect_failure("Google", AccessDeniedError("(access_denied) The user denied the request")))
    assert "cancelled" in line and "Press Connect to try again" in line
    # LinkedIn's own failure used to print the whole reply, key and all.
    raw = RuntimeError("LinkedIn didn't give a token: {'error': 'invalid_request', 'access_token': 'AQX" + "k" * 40 + "'}")
    line = plain(pw.connect_failure("LinkedIn", raw))
    assert line == "The LinkedIn sign-in didn't go through. Press Connect to try again."
    assert "internet" in pw.connect_failure("Google", httpx.ConnectError("getaddrinfo failed"))
    # A sentence of Pyxi's own is kept.
    kept = "Google didn't give the Sheets permission. Press Allow Sheets and tick the Google Sheets box when Google asks"
    assert pw.connect_failure("Google", RuntimeError(kept)) == kept


# ---------------------------------------------------------------- the AI model
def model_error(code: int, message: str) -> Exception:
    class LLMError(Exception):
        pass

    return LLMError(f"OpenRouter API error: Error code: {code} - {{'error': {{'message': \"{message}\", 'code': {code}}}}}")


def test_the_beta_allowance_is_not_called_a_busy_model():
    """A background job said "The AI model is busy right now. Try again in a minute." when the
    day's allowance was gone: trying again in a minute could never work."""
    exc = model_error(429, "You've used today's Pyxi beta allowance. It resets at midnight UTC.")
    assert both(exc) == "You've used today's Pyxi beta allowance. It resets at midnight UTC."
    exc = model_error(429, "The Pyxi beta is at its limit for this month. Back soon.")
    assert both(exc) == "The Pyxi beta is at its limit for this month. Back soon."
    exc = model_error(401, "Sign in to the Pyxi beta first (Settings, invite code).")
    assert both(exc) == "Sign in to the Pyxi beta first (Settings, invite code)."
    # What the server says instead once Google sign-in is on (cloud/lib/beta.ts, notSignedIn).
    exc = model_error(401, "Sign in to Pyxi to carry on.")
    assert both(exc) == "Sign in to Pyxi to carry on."


def test_model_failures_each_have_their_own_line(monkeypatch):
    monkeypatch.delenv("PYXI_BETA", raising=False)
    assert both(model_error(401, "No auth credentials found")) == pw.MODEL_KEY
    assert both(model_error(402, "Insufficient credits")) == pw.MODEL_CREDIT
    assert both(model_error(429, "Rate limit exceeded")) == pw.MODEL_BUSY
    assert both(model_error(529, "Overloaded")) == pw.MODEL_BUSY
    assert both(model_error(503, "The beta server has no model key set.")) == pw.BETA_DOWN
    assert both(model_error(502, "Bad gateway")) == pw.MODEL_DOWN
    assert both(model_error(404, "No endpoints found for some/model.")) == pw.MODEL_MISSING
    assert both(model_error(400, "This endpoint's maximum context length is 200000 tokens.")) == pw.MODEL_TOO_MUCH
    monkeypatch.setenv("PYXI_BETA", "1")  # in the beta there is no key of theirs to check
    assert both(model_error(401, "No auth credentials found")) == pw.MODEL_KEY_BETA
    assert "invite code" in pw.MODEL_KEY_BETA
    for line in (pw.MODEL_KEY, pw.MODEL_KEY_BETA, pw.MODEL_CREDIT, pw.MODEL_BUSY, pw.BETA_DOWN,
                 pw.MODEL_DOWN, pw.MODEL_MISSING, pw.MODEL_TOO_MUCH, pw.MODEL_OFFLINE, pw.MODEL_SLOW):  # fmt: skip
        plain(line)


def test_the_model_out_of_reach_or_too_slow():
    class APIConnectionError(Exception):
        pass

    class APITimeoutError(APIConnectionError):
        pass

    assert both(APIConnectionError("Connection error.")) == pw.MODEL_OFFLINE
    assert both(APITimeoutError("Request timed out.")) == pw.MODEL_SLOW
    assert "internet connection" in pw.MODEL_OFFLINE and "Ask me again" in pw.MODEL_SLOW
    # Through the teammates' library, which wraps it.
    class LLMError(Exception):
        pass

    assert both(LLMError("OpenRouter API error: Connection error.")) == pw.MODEL_OFFLINE
    assert both(LLMError("OpenRouter API error: Request timed out.")) == pw.MODEL_SLOW


# ---------------------------------------------------------------- the browser
def test_a_browser_window_closed_half_way():
    class TargetClosedError(Exception):
        pass

    line = plain(both(TargetClosedError("Page.goto: Target page, context or browser has been closed")))
    assert "closed before I finished" in line and "leave it open" in line
    assert both(RuntimeError("BrowserType.launch: Executable doesn't exist at C:\\Users\\sam\\x")) == pw.BROWSER_START
    assert both(RuntimeError("Page.goto: net::ERR_NAME_NOT_RESOLVED at https://example.com")) == pw.SITE_UNREACHABLE
    assert both(RuntimeError("Page.goto: Timeout 30000ms exceeded.")) == pw.SITE_SLOW


# ---------------------------------------------------------------- files
def winerror(code: int, text: str, path: str) -> OSError:
    exc = PermissionError(13, text, path) if code in (5, 32) else OSError(22, text, path)
    exc.winerror = code
    return exc


def test_a_file_open_elsewhere_names_the_file_and_not_its_folders():
    path = r"C:\Users\sam.rivera\Documents\Budget 2026.xlsx"
    line = plain(both(winerror(32, "The process cannot access the file because it is being used by another process", path)))
    assert line == "Budget 2026.xlsx is open in another app. Close it there, then ask again."
    line = plain(both(winerror(5, "Access is denied", path)))
    assert line.startswith("Windows won't let me open or save Budget 2026.xlsx.") and "pick another folder" in line
    assert "sam.rivera" not in line
    # As text only (a library that wrapped it).
    line = both(RuntimeError(f"[Errno 13] Permission denied: '{path}'".replace("\\", "\\\\")))
    assert "Budget 2026.xlsx" in line and "sam.rivera" not in line and "Errno" not in line


def test_missing_file_full_disk_and_damaged_files():
    line = plain(both(FileNotFoundError(2, "No such file or directory", r"C:\Users\sam\Desktop\CV.docx")))
    assert line == "I can't find CV.docx. It may have been moved or renamed. Tell me where it is now."
    assert plain(both(OSError(28, "No space left on device"))) == pw.DISK_FULL
    full = OSError(22, "There is not enough space on the disk")
    full.winerror = 112
    assert both(full) == pw.DISK_FULL and "Free up some space" in pw.DISK_FULL
    assert plain(both(zipfile.BadZipFile("File is not a zip file"))) == pw.FILE_DAMAGED
    assert both(RuntimeError(r"Package not found at 'C:\Users\sam\Report.docx'")) == pw.FILE_DAMAGED
    assert "locked with a password" in pw.FILE_DAMAGED
    assert plain(both(RuntimeError(r"cannot identify image file 'C:\\Users\\sam\\logo.png'"))) == pw.PICTURE_DAMAGED
    assert plain(both(RuntimeError("EOF marker not found"))) == pw.PDF_DAMAGED
    assert plain(both(RuntimeError("File has not been decrypted"))) == pw.PDF_LOCKED


def test_office_apps_that_refuse_or_are_not_there():
    refused = RuntimeError("(-2147352567, 'Exception occurred.', (0, None, None, None, 0, -2146827284), None)")
    assert plain(both(refused)) == pw.OFFICE_REFUSED
    missing = RuntimeError("(-2147221005, 'Invalid class string', None, None)")
    assert plain(both(missing)) == pw.OFFICE_MISSING
    assert "Excel, Word or PowerPoint" in pw.OFFICE_MISSING


# ---------------------------------------------------------------- the last resort
UNSAFE = [
    "('invalid_request: Bad thing.', {'error': 'invalid_request'})",  # a tuple
    "{'error': {'message': 'nope', 'code': 418}}",  # dict text
    "KeyError: 'id'",
    "'id'",
    "'NoneType' object has no attribute 'get'",
    "list index out of range",
    'Traceback (most recent call last):\n  File "x.py", line 3',
    r"Could not write C:\Users\sam.rivera\AppData\Roaming\Pyxi\made.json",
    "Could not read /home/sam/notes.txt",
    "Fetching https://example.com/cb?access_token=abc123def456 did not work",
    "The key sk-or-v1-0123456789abcdef0123456789abcdef was refused",
    "Bad value 4f9a8b7c6d5e4f3a2b1c0d9e8f7a6b5c4d3e2f1a",
    "ValueError",
    "",
    "Unexpected status_code 418",
    "<Response [418 I'm a teapot]>",
]


@pytest.mark.parametrize("raw", UNSAFE)
def test_nothing_of_pythons_a_path_or_a_key_is_ever_shown(raw):
    for line in (describe_failure(RuntimeError(raw)), friendly_error(RuntimeError(raw))):
        assert line == pw.UNKNOWN, (raw, line)
    assert "Ask me again" in pw.UNKNOWN and "restart Pyxi" in pw.UNKNOWN
    plain(pw.UNKNOWN)


def test_a_sentence_of_pyxis_own_is_kept():
    for own in (
        "Google only lets Pyxi rename files it made itself. Rename this one by clicking its title at the top.",
        "Gmail isn't connected. Connect Google in Settings and ask again.",
        "I couldn't find a tab like that.",
    ):
        assert describe_failure(RuntimeError(own)) == own
    # A quoted path loses its folders, and the sentence stays.
    assert pw.safe_line(r"I saved a copy as 'C:\Users\sam\Documents\Pyxi\Notes.docx' for you.") == (
        "I saved a copy as Notes.docx for you."
    )
    long = "This sentence is fine. " * 20
    assert len(pw.safe_line(long)) <= 160 and pw.safe_line(long).endswith(".")
