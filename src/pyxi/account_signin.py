"""Signing in to Pyxi with Google: who they are, and nothing else.

This is not the Google apps connection (connectors/google.py), which asks for mail, calendar
and files and keeps a token for them. This one asks Google for the basic identity only
(``openid email profile``), on Google's own page in their browser, and ends on a small server
on this PC (a loopback sign-in, with PKCE and a one-time ``state``). What comes back is an ID
token: it is handed to ``deliver`` once (the Pyxi server checks it and answers with a Pyxi
token) and is never written anywhere. The saved Google apps connection is not read or changed.

Everything here blocks, so it runs in a thread (pyxi/account.py); ``cancel`` ends it early.

One more step, only when asked for (``onward``): someone may have pressed a plan on the
website's pricing page before they downloaded Pyxi. That choice lives in their browser, so
after a good sign-in the browser is sent on to the website's /signed-in page with this PC's
port and a one-time key (in the address's fragment, which is never sent to the website's
server). That page sends the browser straight back here, to ``/pick``, with the plan or
"none", and the usual closing page is shown. The website learns nothing about who signed in.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import threading
import time
import webbrowser
import wsgiref.simple_server
from collections.abc import Callable, Iterable
from typing import Any
from urllib.parse import parse_qs, urlencode

import httpx

from pyxi.connectors.signin_page import page

log = logging.getLogger(__name__)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPES = ("openid", "email", "profile")  # who they are. Never mail, calendar or files.
TIMEOUT_S = 300.0  # a sign-in page left open is not waited for all day
PICK_WAIT_S = 8.0  # how long the website's page has to come back with the plan they pressed
PICKS = ("starter", "pro", "max")


class SignInError(Exception):
    """The sign-in did not finish. ``why``: cancelled, timeout, refused (they said no on
    Google's page), state (an answer that was not for this sign-in), offline, failed."""

    def __init__(self, why: str, detail: str = "") -> None:
        super().__init__(detail or why)
        self.why = why


def pkce() -> tuple[str, str]:
    """A one-time secret and its SHA-256 challenge (RFC 7636)."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def auth_address(
    client_id: str, redirect_uri: str, state: str, challenge: str, nonce: str, base: str = AUTH_URL
) -> str:
    return (
        base
        + "?"
        + urlencode(
            {
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": " ".join(SCOPES),
                "state": state,
                "nonce": nonce,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "prompt": "select_account",
            }
        )
    )


def _nonce_of(id_token: str) -> str | None:
    """The nonce inside an ID token (read, not verified: the Pyxi server verifies the token)."""
    try:
        body = id_token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        return str(claims.get("nonce") or "")
    except (IndexError, ValueError, AttributeError):
        return None


class _Quiet(wsgiref.simple_server.WSGIRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:  # the address holds a one-time code
        return


class _Landing:
    """The page Google sends the browser back to. It takes one answer: the one that carries
    this sign-in's ``state``. Anything else on the port (a favicon, another program) is not it."""

    def __init__(
        self,
        state: str,
        finish: Callable[[str], None],
        onward: Callable[[], str] | None = None,
        on_pick: Callable[[str, bool], None] | None = None,
    ) -> None:
        self.state = state
        self.finish = finish
        self.onward = onward
        self.on_pick = on_pick
        self.key = secrets.token_urlsafe(24)  # the website's page must bring this back
        self.port = 0
        self.done = False
        self.waiting = False  # the browser went on to the website and is expected back
        self.error: SignInError | None = None

    def _closing(self, start_response: Callable[..., Any], reason: str = "") -> Iterable[bytes]:
        start_response(
            "200 OK",
            [
                ("Content-type", "text/html; charset=utf-8"),
                ("Cache-Control", "no-store"),
                ("Referrer-Policy", "no-referrer"),
                ("X-Content-Type-Options", "nosniff"),
            ],
        )
        return [page(self.error is None, reason, account=True)]

    def _pick(self, query: dict[str, list[str]], start_response: Callable[..., Any]) -> Iterable[bytes]:
        """The browser is back from the website with the plan they pressed there (or "none")."""
        got = {k: (query.get(k) or [""])[0] for k in ("plan", "yearly", "k")}
        if not hmac.compare_digest(got["k"].encode(), self.key.encode()):
            start_response("404 Not Found", [("Content-type", "text/plain; charset=utf-8")])
            return [b"Not found"]
        self.waiting = False
        if got["plan"] in PICKS and self.on_pick is not None:
            try:
                self.on_pick(got["plan"], got["yearly"] == "1")
            except Exception as exc:  # a pick that can't be kept never spoils the sign-in
                log.warning("the picked plan was not kept: %s", type(exc).__name__)
        return self._closing(start_response)

    def __call__(
        self, environ: dict[str, Any], start_response: Callable[..., Any]
    ) -> Iterable[bytes]:
        query = parse_qs(str(environ.get("QUERY_STRING") or ""))
        got = {k: (query.get(k) or [""])[0] for k in ("state", "code", "error")}
        if self.waiting and str(environ.get("PATH_INFO") or "/") == "/pick":
            return self._pick(query, start_response)
        if self.done or str(environ.get("PATH_INFO") or "/") != "/" or not any(got.values()):
            start_response("404 Not Found", [("Content-type", "text/plain; charset=utf-8")])
            return [b"Not found"]
        self.done = True
        reason = ""
        if not hmac.compare_digest(got["state"].encode(), self.state.encode()):
            self.error = SignInError("state")  # not the answer to the question Pyxi asked
        elif got["error"] or not got["code"]:
            reason = got["error"].replace("_", " ")
            self.error = SignInError("refused", got["error"])
        else:
            try:
                self.finish(got["code"])
            except SignInError as exc:
                self.error = exc
            except httpx.HTTPError as exc:
                self.error = SignInError("offline", type(exc).__name__)
            except Exception as exc:  # never a stack on their screen
                log.warning("the Pyxi sign-in failed: %s", type(exc).__name__)
                self.error = SignInError("failed", type(exc).__name__)
        site = ""
        if self.error is None and self.onward is not None:
            try:
                site = self.onward().rstrip("/")
            except Exception:
                site = ""
        if site.startswith(("https://", "http://127.0.0.1", "http://localhost")):
            # Signed in. On to the website's page, which knows the plan they pressed there and
            # comes straight back with it. The port and key ride in the fragment: the browser
            # keeps that to itself, so the website's server never sees them.
            self.waiting = True
            start_response(
                "303 See Other",
                [
                    ("Location", f"{site}/signed-in#p={self.port}&k={self.key}"),
                    ("Cache-Control", "no-store"),
                    ("Referrer-Policy", "no-referrer"),
                ],
            )
            return [b""]
        return self._closing(start_response, reason)


def run(
    client_id: str,
    deliver: Callable[[str], None],
    *,
    client_secret: str = "",
    open_browser: Callable[[str], Any] = webbrowser.open,
    cancel: threading.Event | None = None,
    timeout: float = TIMEOUT_S,
    http: httpx.Client | None = None,
    auth_url: str = AUTH_URL,
    token_url: str = TOKEN_URL,
    onward: Callable[[], str] | None = None,
    on_pick: Callable[[str, bool], None] | None = None,
    pick_wait: float = PICK_WAIT_S,
) -> None:
    """Open Google's sign-in page and wait for it to come back to this PC, then swap the
    one-time code for an ID token and hand that to ``deliver``. Raises :class:`SignInError`
    when it does not finish. The closing page says "signed in" only once ``deliver`` returned.

    ``client_secret``: Google asks for one from a "Desktop app" client even with PKCE (it is
    not a secret there: it ships inside every installed app). Empty: none is sent.

    ``onward``: asked after a good sign-in; when it answers with the website's address, the
    browser goes there to fetch the plan pressed on the pricing page and ``on_pick(plan,
    yearly)`` is called with it. The wait for that is short (``pick_wait``) and never fails
    the sign-in: they are signed in either way."""
    if not client_id:
        raise SignInError("failed", "no client id")
    cancel = cancel or threading.Event()
    state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    verifier, challenge = pkce()
    redirect: list[str] = []

    def finish(code: str) -> None:
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "code_verifier": verifier,
            "redirect_uri": redirect[0],
        }
        if client_secret:
            form["client_secret"] = client_secret
        client = http or httpx.Client(timeout=15)
        try:
            r = client.post(token_url, data=form)
        finally:
            if http is None:
                client.close()
        try:
            id_token = str(r.json().get("id_token") or "") if r.status_code == 200 else ""
        except ValueError:
            id_token = ""
        if not id_token:
            raise SignInError("failed", f"Google answered {r.status_code}")
        if _nonce_of(id_token) != nonce:
            raise SignInError("state", "nonce")
        deliver(id_token)  # used once, kept nowhere

    landing = _Landing(state, finish, onward, on_pick)
    server = wsgiref.simple_server.make_server("127.0.0.1", 0, landing, handler_class=_Quiet)
    try:
        landing.port = server.server_port
        redirect.append(f"http://127.0.0.1:{server.server_port}")
        server.timeout = 0.25
        open_browser(auth_address(client_id, redirect[0], state, challenge, nonce, auth_url))
        deadline = time.monotonic() + timeout
        while not landing.done:
            if cancel.is_set():
                raise SignInError("cancelled")
            if time.monotonic() >= deadline:
                raise SignInError("timeout")
            server.handle_request()
        # Signed in. The website's page may still be on its way back with the plan they pressed.
        back_by = time.monotonic() + pick_wait
        while landing.waiting and time.monotonic() < back_by and not cancel.is_set():
            server.handle_request()
    finally:
        server.server_close()
    if landing.error is not None:
        raise landing.error
