"""The Pyxi account: sign in with Google, a 3-day trial, then a plan.

The brain's half of accounts and subscriptions (the server's half is ``cloud/``; both are
built to ``.pyxi/ACCOUNTS-CONTRACT.md``). One :class:`Account` per brain keeps what is known
about the signed-in account and tells every window in one event, ``account``.

* **Signing in** opens Google's page for the basic identity only (pyxi/account_signin.py),
  swaps the ID token for a Pyxi token at the server, and saves that token the way the beta
  token is saved (``cloud.json``, protected with Windows DPAPI). The brain then restarts, so
  every model call goes through the server, as after an invite code.
* **Plans** are bought on Stripe's own pages in their browser: Pyxi asks the server for the
  address, opens it, and then asks ``/api/me`` every few seconds for a while, so the plan is
  unlocked here without a restart.
* **Gates**: when the server says the trial or plan has ended, a payment failed or the month's
  allowance is used, the user sees the contract's own sentence on a card with the one button
  that fixes it. Never the raw error.

The Pyxi token and Google's ID token never go into an event, a log line or a window.
An invite-code device has no account: nothing here changes for it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import time
import webbrowser
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from pyxi import account_signin, cloud, secretstore
from pyxi.account_signin import SignInError

log = logging.getLogger(__name__)

FILE = "account.json"  # what this PC remembers that is not secret: warnings shown, signed out
PLANS = ("starter", "pro", "max")
PLAN_NAMES = {"trial": "Trial", "starter": "Starter", "pro": "Pro", "max": "Max", "none": "No plan"}

# The words both sides use, exactly as the contract has them.
SENTENCES = {
    "trial_ended": "Your 3-day trial has ended. Choose a plan to keep going.",
    "subscription_ended": "Your Pyxi plan has ended. Choose a plan to carry on.",
    "payment_failed": "Your last payment didn't go through. Update your card to keep Pyxi working.",
    "allowance_used": "You've used this month's allowance on your plan. It resets on {date}, or you can move up a plan.",
    "billing_off": "Plans aren't open yet. Your trial keeps working for now.",
    "bad_sign_in": "That Google sign-in didn't work. Try again.",
}
# The server's sentence for a trial whose allowance is used (there is no month to wait for).
TRIAL_ALLOWANCE_USED = "You've used your trial's allowance. Choose a plan to keep going."
SIGNED_OUT = "You've been signed out of Pyxi. Sign in to carry on."
OFFLINE = "I can't reach Pyxi right now. Check your internet and try again."
TOO_SLOW = "The sign-in took too long. Try again."
NOT_OPEN = "Signing in with Google isn't open yet."
NO_PLAN_YET = "You don't have a plan yet. Choose one first."
SIGN_IN_FOR_PLANS = "Sign in with Google to choose a plan."
DIDNT_OPEN = "That page didn't open. Try again in a moment."
CHECKOUT_OPEN = "Checkout is open in your browser. Come back here when you're done."
PORTAL_OPEN = "Your subscription page is open in your browser."

# Each gate's card: its title and the one button that fixes it (None: nothing to press).
GATES: dict[str, tuple[str, str | None]] = {
    "trial_ended": ("Your trial has ended", "choose_plan"),
    "subscription_ended": ("Your plan has ended", "choose_plan"),
    "payment_failed": ("Your payment didn't go through", "update_card"),
    "allowance_used": ("This month's allowance is used", "see_plans"),
    "billing_off": ("Plans aren't open yet", None),
    "signed_out": ("Sign in to Pyxi", "pyxi_sign_in"),
}

WARN_AT = 0.8  # of the month's allowance: said once
POLL_S = 3.0  # after checkout opens: ask the server this often...
POLL_FOR_S = 600.0  # ...for this long
REFRESH_EVERY_S = 30.0  # an unforced refresh is not repeated sooner than this

_CODE = re.compile(
    r"""['"]code['"]\s*:\s*['"](trial_ended|subscription_ended|payment_failed|allowance_used|billing_off)['"]"""
)
# The server's own sentence, as the model client prints it (a Python or JSON string).
_MESSAGE = re.compile(r"""['"]message['"]\s*:\s*(?:"((?:[^"\\]|\\.){1,300})"|'((?:[^'\\]|\\.){1,300})')""")
_UNAUTHORIZED = re.compile(r"\bAPI error: Error code: 401\b")
# How each gate's sentence begins, for a failure of which only the words are left.
_OPENINGS = {
    "trial_ended": "Your 3-day trial has ended.",
    "subscription_ended": "Your Pyxi plan has ended.",
    "payment_failed": "Your last payment didn't go through.",
    "allowance_used": "You've used this month's allowance on your plan.",
    "billing_off": "Plans aren't open yet.",
}
# The server's sentences seen in this run, and the gate each one was for: a teammate keeps
# only the words of what went wrong, and the card still needs to know which gate it was.
_SAID: dict[str, str] = {TRIAL_ALLOWANCE_USED: "allowance_used"}


def friendly_date(iso: str | None) -> str:
    """'2026-12-01T00:00:00Z' as '1 December', the way the server writes dates ('' for none)."""
    if not iso:
        return ""
    try:
        when = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return ""
    return f"{when.day} {when.strftime('%B')}"


def sentence(code: str, resets_at: str | None = None, *, trial: bool = False) -> str:
    """The contract's sentence for a gate, for when the server's own did not come through."""
    if code == "signed_out":
        return SIGNED_OUT
    if code == "allowance_used" and trial:
        return TRIAL_ALLOWANCE_USED
    text = SENTENCES[code]
    if "{date}" in text:
        text = text.replace("{date}", friendly_date(resets_at) or "the 1st of next month")
    return text


def _server_error(body: Any) -> tuple[str, str]:
    """``(code, message)`` from the server's ``{"error": {"code", "message"}}`` (or from the
    inner part alone, which is what the model client keeps)."""
    if isinstance(body, dict):
        error = body.get("error") if isinstance(body.get("error"), dict) else body
        code, message = error.get("code"), error.get("message")
        if isinstance(code, str) and isinstance(message, str):
            return code, " ".join(message.split())[:300]
        if isinstance(code, str):
            return code, ""
    return "", ""


def gate_of(error: BaseException | str, *, account_device: bool = False) -> tuple[str, str] | None:
    """Is this failure one of the server's gates? ``(code, the server's own sentence or '')``.

    The model client wraps the server's answer in its own words ("Error code: 402 -
    {'error': {'code': 'trial_ended', 'message': ...}}"), and a teammate may only keep the
    text, so this reads the code and the sentence out of whatever is left. The server's own
    sentence is the one shown (it has the date in it). ``account_device``: this PC is signed
    in to an account, so a 401 means the sign-in no longer holds."""
    texts: list[str] = []
    code, said = "", ""
    seen: Any = error
    while isinstance(seen, BaseException) and len(texts) < 8:
        texts.append(str(seen))
        if not code:
            found = _server_error(getattr(seen, "body", None))
            if found[0] in _OPENINGS:
                code, said = found
        seen = seen.__cause__ or seen.__context__
    if isinstance(error, str):
        texts.append(error)
    text = "\n".join(texts)
    if not code:
        m = _CODE.search(text)
        if m:
            code = m.group(1)
            words = _MESSAGE.search(text, m.end()) or _MESSAGE.search(text)
            raw = (words.group(1) or words.group(2) or "") if words else ""
            said = " ".join(re.sub(r"\\(.)", r"\1", raw).split())
    if not code:  # only the words are left
        for words, known in _SAID.items():
            if words in text:
                code, said = known, words
                break
    if not code:
        for known, opening in _OPENINGS.items():
            if opening in text:
                code = known
                end = text.find(".", text.find(opening) + len(opening))
                # "...on your plan. It resets on 1 December, or you can move up a plan."
                said = text[text.find(opening) : end + 1] if known == "allowance_used" and end > 0 else ""
                break
    if code:
        if said:
            if len(_SAID) > 40:
                _SAID.pop(next(iter(_SAID)))
            _SAID[said] = code
        return code, said
    # The model client's own words for a token the server no longer takes. (Only its words:
    # a 401 from some other service a teammate used is not Pyxi's sign-in.)
    if SIGNED_OUT in text or (account_device and _UNAUTHORIZED.search(text)):
        return "signed_out", ""
    return None


def signed_in_here() -> bool:
    """Is this brain running on an account's token (not an invite code, not their own key)?"""
    return cloud.in_beta() and os.environ.get("PYXI_ACCOUNT") == "1"


def _clean(raw: Any) -> dict[str, Any] | None:
    """The server's Account, with only the fields the contract names."""
    if not isinstance(raw, dict):
        return None
    allowance = raw.get("allowance") if isinstance(raw.get("allowance"), dict) else {}

    def number(value: Any) -> float:
        try:
            return max(0.0, float(value))
        except (TypeError, ValueError):
            return 0.0

    days = raw.get("days_left")
    return {
        "email": str(raw.get("email") or "")[:200],
        "plan": str(raw.get("plan") or "none"),
        "status": str(raw.get("status") or "ended"),
        "trial_ends_at": raw.get("trial_ends_at") or None,
        "days_left": int(days) if isinstance(days, (int, float)) else None,
        "renews_at": raw.get("renews_at") or None,
        "cancels": bool(raw.get("cancels")),
        "allowance": {
            "used_usd": number(allowance.get("used_usd")),
            "limit_usd": number(allowance.get("limit_usd")),
            "resets_at": allowance.get("resets_at") or None,
        },
    }


def trial_line(info: dict[str, Any] | None) -> str:
    """'3-day trial, 2 days left' (the tour says it once; Settings always shows it)."""
    if not info or info.get("status") != "trial":
        return ""
    days = info.get("days_left")
    if days is None:
        return "3-day trial"
    return "3-day trial, last day" if days <= 0 else f"3-day trial, {days} day{'' if days == 1 else 's'} left"


def welcome(info: dict[str, Any] | None) -> str:
    """Said once, quietly, when they sign in: where the trial or the plan stands."""
    if not info:
        return "You're signed in."
    if info["status"] == "trial":
        # The server counts whole days still to come (0 on the last day), so a trial that has
        # just begun reads 2: "has started" says it better than a number would.
        days = info["days_left"]
        if days is None or days >= 2:
            return "Your 3-day trial has started. No card needed."
        if days <= 0:
            return "Today is the last day of your 3-day trial."
        return "Your 3-day trial has 1 day left after today."
    if info["status"] in ("active", "past_due") and info["plan"] in PLANS:
        return f"You're on the {PLAN_NAMES[info['plan']]} plan."
    return "You're signed in."


def _error(r: httpx.Response) -> tuple[str, str]:
    """The server's ``(code, message)`` for an answer that was not a yes."""
    try:
        return _server_error(r.json())
    except ValueError:
        return "", ""


def desktop_client_secret(connector: Any, client_id: str) -> str:
    """Google asks a "Desktop app" client for its client secret when the one-time code is
    swapped, even with PKCE (it is not a secret there: it ships inside every installed app).
    Pyxi already ships that client for the Google apps connection. When the server names the
    same client for signing in, its secret is used: read here only to be sent to Google with
    the code. The saved connection (its token) is not read or changed."""
    env = os.environ.get("PYXI_SIGNIN_CLIENT_SECRET", "")
    if env:
        return env
    try:
        config = connector.client_config() or {}
    except Exception:
        return ""
    client = config.get("installed") or config.get("web") or {}
    return str(client.get("client_secret") or "") if client.get("client_id") == client_id else ""


class Account:
    """What this brain knows about the signed-in account, and everything that changes it."""

    # How it reaches the server and Google's token page. None: the real network. Tests give
    # a stand-in (tests/conftest.py gives every test one that reaches nothing).
    transport: Any = None

    def __init__(self, data_dir: Path, hub: Any) -> None:
        self.data_dir = Path(data_dir)
        self.hub = hub
        self.offered: bool | None = None  # does the server offer sign-in? None: not known yet
        self.invite_codes = True
        self.info: dict[str, Any] | None = None  # the server's Account, cleaned
        self.busy = ""  # "sign_in", "checkout", "portal", "sign_out"
        self.gate: str | None = None
        self._client_id = ""
        self._client_secret = ""
        self._cancel: threading.Event | None = None
        self._refreshed = 0.0
        self._shown: set[str] = set()  # gates the server's Account gave away, said once a run
        self._before: tuple[Any, ...] | None = None  # the plan when checkout opened
        self._watch: asyncio.Task[None] | None = None
        self._tasks: set[asyncio.Task[Any]] = set()
        # Swapped by tests (with ``transport``): no network, no browser, no restart.
        self.open_url: Callable[[str], Any] = webbrowser.open
        self.restart: Callable[[], None] = lambda: None
        # The desktop client's secret Google asks for with the code (see desktop_client_secret).
        self.secret_for: Callable[[str], str] = lambda client_id: os.environ.get(
            "PYXI_SIGNIN_CLIENT_SECRET", ""
        )
        self.sign_in_timeout = account_signin.TIMEOUT_S
        self.poll_s, self.poll_for_s = POLL_S, POLL_FOR_S

    # ------------------------------------------------------------------ what is saved
    def _saved(self) -> dict[str, Any] | None:
        """The saved token, when it is an account's (an invite-code device has none)."""
        saved = cloud.saved(self.data_dir)
        return saved if saved is not None and saved.get("account") else None

    def has_account(self) -> bool:
        return self._saved() is not None

    @property
    def signed_in(self) -> bool:
        return self.has_account() and self.gate != "signed_out"

    def _flags(self) -> dict[str, Any]:
        try:
            raw = json.loads((self.data_dir / FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return raw if isinstance(raw, dict) else {}

    def _flag(self, **changes: Any) -> None:
        flags = {**self._flags(), **changes}
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / FILE).write_bytes(json.dumps(flags, indent=2).encode("utf-8"))

    def public(self) -> dict[str, Any]:
        """Everything a window may know. Never a token."""
        info = self.info if self.signed_in else None
        return {
            "offered": self.offered,
            "invite_codes": self.invite_codes,
            "signed_in": self.signed_in,
            # They signed out (or the sign-in ran out): the sign-in step comes up by itself.
            "ask_sign_in": bool(self._flags().get("signed_out"))
            and not self.has_account()
            and not cloud.in_beta()
            and not cloud.had_own_key(),
            "email": info["email"] if info else "",
            "plan": info["plan"] if info else None,
            "status": info["status"] if info else None,
            "trial_ends_at": info["trial_ends_at"] if info else None,
            "days_left": info["days_left"] if info else None,
            "renews_at": info["renews_at"] if info else None,
            "cancels": bool(info and info["cancels"]),
            "allowance": info["allowance"] if info else None,
            "busy": self.busy,
            "gate": self.gate,
        }

    async def _emit(self, **extra: Any) -> None:
        await self.hub.emit("account", account=self.public(), **extra)

    def _spawn(self, coro: Any) -> asyncio.Task[Any]:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    # ------------------------------------------------------------------ the server
    def _url(self) -> str:
        saved = cloud.saved(self.data_dir)
        return str((saved or {}).get("url") or cloud.server_url()).rstrip("/")

    async def _call(
        self, method: str, path: str, *, token: bool = True, body: dict[str, Any] | None = None, timeout: float = 12
    ) -> httpx.Response:
        headers = {}
        if token:
            saved = cloud.saved(self.data_dir)
            if saved is None:
                raise httpx.ConnectError("not signed in")
            headers["Authorization"] = f"Bearer {saved['token']}"
        kw: dict[str, Any] = {"timeout": timeout}
        if self.transport is not None:
            kw["transport"] = self.transport
        async with httpx.AsyncClient(**kw) as client:
            return await client.request(method, self._url() + path, headers=headers, json=body)

    async def config(self) -> bool:
        """Ask the server whether sign-in is on (and with which Google client). False: the
        server could not be reached. A server from before accounts (no such route) answers
        "no sign-in", so the invite code is asked for, as before."""
        try:
            r = await self._call("GET", "/api/config", token=False, timeout=6)
        except httpx.HTTPError:
            return False
        if r.status_code >= 500:
            return False
        try:
            body = r.json() if r.status_code == 200 else {}
        except ValueError:
            body = {}
        body = body if isinstance(body, dict) else {}
        self._client_id = str(body.get("google_client_id") or "")
        # Not in the contract: a server that also sends the desktop client's secret is used.
        self._client_secret = str(body.get("google_client_secret") or "")
        self.offered = bool(body.get("sign_in")) and bool(self._client_id)
        self.invite_codes = bool(body.get("invite_codes", True))
        return True

    async def refresh(self, *, force: bool = True) -> None:
        """Ask the server about this account and tell every window."""
        if cloud.saved(self.data_dir) is None:
            await self._emit()
            return
        if not force and self._watch is None and time.monotonic() - self._refreshed < REFRESH_EVERY_S:
            return
        self._refreshed = time.monotonic()
        try:
            r = await self._call("GET", "/api/me", timeout=8)
        except httpx.HTTPError:
            await self._emit()
            return
        if r.status_code == 401 and self.has_account():
            await self.show_gate("signed_out")
            return
        try:
            body = r.json() if r.status_code == 200 else {}
        except ValueError:
            body = {}
        info = _clean(body.get("account")) if isinstance(body, dict) else None
        if info is None:  # an invite-code device, or an answer that could not be read
            await self._emit()
            return
        self.info = info
        self._clear_gate(info)
        unlocked = self._unlocked(info)
        await self._emit(**({"what": "plan", "ok": True, "message": unlocked} if unlocked else {}))
        if unlocked:
            await self.hub.emit("notice", message=unlocked)
        await self._warn(info)

    def _clear_gate(self, info: dict[str, Any]) -> None:
        working = info["status"] in ("trial", "active", "past_due")
        allowance = info["allowance"]
        room = allowance["limit_usd"] <= 0 or allowance["used_usd"] < allowance["limit_usd"]
        mended = {
            "trial_ended": working,
            "subscription_ended": working,
            "payment_failed": working,
            "allowance_used": room,
            "signed_out": True,  # the server just answered for this token
        }
        if self.gate is not None and mended.get(self.gate):
            self.gate = None

    @staticmethod
    def _key(info: dict[str, Any] | None) -> tuple[Any, ...]:
        info = info or {}
        return (info.get("plan"), info.get("status"), info.get("cancels"), info.get("renews_at"))

    def _unlocked(self, info: dict[str, Any]) -> str:
        """After checkout or the subscription page: has the plan changed? The line to say."""
        if self._before is None or self._key(info) == self._before:
            return ""
        was_plan, was_status = self._before[0], self._before[1]
        self._before = None
        if self._watch is not None and self._watch is not asyncio.current_task():
            self._watch.cancel()
        self._watch = None
        if info["status"] == "active" and (info["plan"], info["status"]) != (was_plan, was_status):
            return f"You're on the {PLAN_NAMES.get(info['plan'], info['plan'])} plan now. Pyxi is ready."
        return "Your subscription is up to date."

    async def _warn(self, info: dict[str, Any]) -> None:
        """Said once each: 80 percent of the allowance, and the last day of the trial. Also the
        card for a trial or plan that has already ended, once a run."""
        warned = dict(self._flags().get("warned") or {})
        allowance = info["allowance"]
        limit, used = allowance["limit_usd"], allowance["used_usd"]
        month = str(allowance["resets_at"] or info.get("trial_ends_at") or "now")
        if info["status"] == "ended":
            # The server drops the trial's end date once someone has paid: with it still
            # there, what ended is the trial; without it, a plan.
            code = "trial_ended" if info["trial_ends_at"] else "subscription_ended"
            if code not in self._shown:
                await self.show_gate(code)
            return
        if info["status"] == "trial" and info["days_left"] is not None and info["days_left"] <= 0:
            ends = str(info["trial_ends_at"] or "trial")
            if warned.get("trial") != ends:
                self._flag(warned={**warned, "trial": ends})
                warned["trial"] = ends
                await self._card(
                    "account-trial-warning",
                    "Your trial ends today",
                    "Today is the last day of your 3-day trial. Choose a plan to keep going.",
                    "choose_plan",
                    ok=True,
                )
        if limit > 0 and WARN_AT * limit <= used < limit and warned.get("allowance") != month:
            self._flag(warned={**warned, "allowance": month})
            resets = friendly_date(allowance["resets_at"])
            trial = info["status"] == "trial"
            body = (
                "You've used 80 percent of your trial allowance."
                if trial
                else "You've used 80 percent of this month's allowance."
                + (f" It resets on {resets}." if resets else "")
            )
            await self._card(
                "account-allowance-warning",
                "Allowance nearly used",
                body,
                "choose_plan" if trial else "see_plans",
                ok=True,
            )

    # ------------------------------------------------------------------ the cards
    async def _card(self, job_id: str, title: str, body: str, action: str | None, *, ok: bool = False) -> None:
        await self.hub.emit(
            "job_ready",
            job_id=job_id,
            agent="Pyxi",
            ok=ok,
            title=title,
            body=body,
            files=[],
            changes=[],
            notes=[],
            actions=[action] if action else [],
            link=None,
            calendar=None,
        )

    async def show_gate(self, code: str, said: str = "") -> str:
        """The card for one of the server's gates: its sentence and the button that fixes it.
        Returns the sentence."""
        title, action = GATES[code]
        resets = (self.info or {}).get("allowance", {}).get("resets_at")
        trial = (self.info or {}).get("status") == "trial" or said == TRIAL_ALLOWANCE_USED
        # The server's own sentence (it has the date in it); the contract's when there is none.
        body = said or sentence(code, resets, trial=trial)
        if code == "allowance_used" and trial:
            # A trial has no next month to wait for and no plan to move up from.
            title, action = "Your trial's allowance is used", "choose_plan"
        if code != "billing_off":  # plans not being open is not something wrong with the account
            self.gate = code
        self._shown.add(code)
        if code == "signed_out":
            self._flag(signed_out=True)
        await self._emit()
        await self._card(f"account-{code}", title, body, action)
        return body

    async def gate_from(self, error: BaseException | str) -> str | None:
        """A turn or a job failed: if the server's gate is why, show its card and return its
        sentence (else None, and the caller says what it always said)."""
        found = gate_of(error, account_device=signed_in_here())
        if found is None:
            return None
        return await self.show_gate(*found)

    # ------------------------------------------------------------------ signing in and out
    def _deliver(self, url: str, got: dict[str, Any]) -> Callable[[str], None]:
        def deliver(id_token: str) -> None:
            kw: dict[str, Any] = {"timeout": 15}
            if self.transport is not None:
                kw["transport"] = self.transport
            with httpx.Client(**kw) as client:
                r = client.post(url + "/api/auth/google", json={"id_token": id_token})
            try:
                body = r.json()
            except ValueError:
                body = {}
            token = str(body.get("token") or "") if isinstance(body, dict) else ""
            if r.status_code != 200 or not token.startswith("pyxi_"):
                # Why, in the server's own words: the sign-in wasn't right, signing in isn't
                # open, too many tries, the account was switched off.
                got["refused"] = _server_error(body)
                raise SignInError("failed", f"the server answered {r.status_code}")
            secretstore.write_json(
                self.data_dir / cloud.FILE, {"url": url, "token": token, "account": True}
            )
            got["account"] = _clean(body.get("account"))

        return deliver

    async def sign_in(self) -> bool:
        """Continue with Google. True when signed in (the brain then restarts)."""
        if self.busy:
            return False
        self.busy = "sign_in"
        await self._emit()
        try:
            if not self._client_id and not await self.config():
                return await self._failed("sign_in", OFFLINE, retry=True)
            if not self.offered:
                return await self._failed("sign_in", NOT_OPEN)
            self._cancel = threading.Event()
            got: dict[str, Any] = {}
            http = httpx.Client(transport=self.transport, timeout=15) if self.transport else None
            try:
                await asyncio.to_thread(
                    account_signin.run,
                    self._client_id,
                    self._deliver(cloud.server_url(), got),
                    client_secret=self._client_secret or self.secret_for(self._client_id),
                    open_browser=self.open_url,
                    cancel=self._cancel,
                    timeout=self.sign_in_timeout,
                    http=http,
                )
            except SignInError as exc:
                log.info("the Pyxi sign-in did not finish: %s", exc.why)
                if exc.why == "cancelled":
                    return await self._failed("sign_in", "", cancelled=True)
                code, said = got.get("refused") or ("", "")
                if code and code != "bad_sign_in":
                    # Trying again at once would not help: it is not open, there were too
                    # many tries, or the account was switched off.
                    return await self._failed("sign_in", said or SENTENCES["bad_sign_in"])
                words = {"timeout": TOO_SLOW, "offline": OFFLINE}.get(
                    exc.why, said or SENTENCES["bad_sign_in"]
                )
                return await self._failed("sign_in", words, retry=True)
            finally:
                if http is not None:
                    http.close()
        finally:
            self._cancel = None
            self.busy = ""
        self.info, self.gate = got.get("account"), None
        self._flag(signed_out=False)
        await self._emit(what="sign_in", ok=True, message=welcome(self.info))
        self.restart()  # so every model call goes through the server, as after an invite code
        return True

    async def _failed(self, what: str, message: str, **extra: Any) -> bool:
        self.busy = ""
        await self._emit(what=what, ok=False, message=message, **extra)
        return False

    def cancel_sign_in(self) -> None:
        if self._cancel is not None:
            self._cancel.set()

    async def sign_out(self) -> None:
        """This device's token stops working and is forgotten. Settings, memories and the
        connected Google apps stay as they are."""
        if self.busy or not self.has_account():
            return
        self.busy = "sign_out"
        try:
            try:
                await self._call("POST", "/api/auth/signout", timeout=8)
            except httpx.HTTPError:
                pass  # offline: forgotten here all the same
            cloud.leave(self.data_dir)
            self.info, self.gate = None, None
            self._flag(signed_out=True)
        finally:
            self.busy = ""
        await self._emit(what="sign_out", ok=True, message="You're signed out. Pyxi is restarting.")
        self.restart()

    # ------------------------------------------------------------------ plans
    async def _billing(self, what: str, path: str, body: dict[str, Any] | None, opened: str) -> None:
        if self.busy:
            return
        self.busy = what
        again = ""
        await self._emit()
        try:
            try:
                r = await self._call("POST", path, body=body)
            except httpx.HTTPError:
                await self._failed(what, OFFLINE, retry=True)
                return
            code, said = _error(r)
            if r.status_code == 401 and self.has_account():
                self.busy = ""
                await self.show_gate("signed_out")  # "Sign in"
                return
            if r.status_code == 401 or code == "sign_in_needed":
                # On an invite code (or not signed in at all): plans need a Google sign-in.
                words = said if code == "sign_in_needed" and said else SIGN_IN_FOR_PLANS
                await self._failed(what, words)
                await self._card("account-sign-in-needed", "Sign in to choose a plan", words, "pyxi_sign_in")
                return
            if code == "already_subscribed" and what == "checkout":
                again = "portal"  # they have a plan: its own page is where it is changed
            elif code == "no_subscription":
                await self._failed(what, said or NO_PLAN_YET, pick=True)  # the plans open
                return
            elif code == "billing_off":
                await self._failed(what, said or SENTENCES["billing_off"])
                return
            elif code in GATES:  # the trial or the plan ended meanwhile, say
                self.busy = ""
                await self.show_gate(code, said)
                return
            elif r.status_code != 200:
                # billing_trouble and any other 5xx may work in a moment; bad_plan,
                # too_many_tries and account_closed will not, so no "Try again" for those.
                await self._failed(what, said or DIDNT_OPEN, retry=r.status_code >= 500)
                return
            else:
                try:
                    url = str(r.json().get("url") or "")
                except (ValueError, AttributeError):
                    url = ""
                if not url.startswith("https://"):
                    await self._failed(what, DIDNT_OPEN, retry=True)
                    return
                await asyncio.to_thread(self.open_url, url)
        finally:
            self.busy = ""
        if again:
            await self.portal()
            return
        await self._emit(what=what, ok=True, message=opened)
        self._before = self._key(self.info)
        if self._watch is not None:
            self._watch.cancel()
        self._watch = self._spawn(self._poll())

    async def checkout(self, plan: str, yearly: bool) -> None:
        """Open Stripe Checkout for a plan in their browser."""
        if plan in PLANS:
            await self._billing(
                "checkout", "/api/billing/checkout", {"plan": plan, "yearly": bool(yearly)}, CHECKOUT_OPEN
            )

    async def portal(self) -> None:
        """Open Stripe's own page: change plan, update the card, cancel, receipts."""
        await self._billing("portal", "/api/billing/portal", None, PORTAL_OPEN)

    async def _poll(self) -> None:
        """They are paying in the browser: ask the server until the plan changes."""
        deadline = time.monotonic() + self.poll_for_s
        try:
            while self._before is not None and time.monotonic() < deadline:
                await asyncio.sleep(self.poll_s)
                await self.refresh()
        finally:
            if self._watch is asyncio.current_task():
                self._watch = None

    # ------------------------------------------------------------------ the windows' messages
    async def on_message(self, msg: dict[str, Any]) -> None:
        action = msg.get("action")
        if action == "get":
            reached = True
            if not self.has_account() and self.offered is None:
                reached = await self.config()
            if cloud.saved(self.data_dir) is not None:
                await self.refresh(force=False)
            await self._emit(what="config", ok=reached)
        elif action == "config":
            await self._emit(what="config", ok=await self.config())
        elif action == "refresh":
            await self.refresh(force=bool(msg.get("force")) or self._before is not None)
        elif action == "sign_in":
            await self.sign_in()
        elif action == "cancel_sign_in":
            self.cancel_sign_in()
        elif action == "sign_out":
            await self.sign_out()
        elif action == "checkout":
            await self.checkout(str(msg.get("plan") or ""), bool(msg.get("yearly")))
        elif action == "portal":
            await self.portal()
