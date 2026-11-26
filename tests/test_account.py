"""The Pyxi account (pyxi/account.py, pyxi/account_signin.py): sign in with Google, the trial,
plans and the server's gates. Google, the Pyxi server and the browser are all pretend here."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import threading
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
import pytest

from conftest import Recorder
from pyxi import account, account_signin, cloud, onboarding
from pyxi.account import GATES, SENTENCES, SIGNED_OUT, Account, gate_of
from pyxi.account_signin import SignInError
from pyxi.agents.jobs import Job, describe_failure
from pyxi.connectors.signin_page import page
from pyxi.hub import Hub
from test_answer_card import make_brain, settle

SERVER = "https://pyxi.example"
CLIENT_ID = "1234-test.apps.googleusercontent.com"
TOKEN = "pyxi_" + "t" * 40
RESETS = "2026-12-01T00:00:00Z"


def an_account(**changes: Any) -> dict[str, Any]:
    base = {
        "email": "sam@example.com",
        "plan": "trial",
        "status": "trial",
        "trial_ends_at": "2026-11-29T12:00:00Z",
        "days_left": 2,
        "renews_at": None,
        "cancels": False,
        "allowance": {"used_usd": 0.1, "limit_usd": 1.5, "resets_at": None},
    }
    return {**base, **changes}


def an_id_token(nonce: str) -> str:
    """Shaped like Google's (three parts, the nonce in the middle one); signed by nobody."""
    body = base64.urlsafe_b64encode(
        json.dumps({"sub": "1", "email": "sam@example.com", "nonce": nonce}).encode()
    )
    return "head." + body.decode().rstrip("=") + ".sig"


class FakeGoogle:
    """Google's two ends: the page in the browser (``open``) and the token address
    (``token``). It checks PKCE the way Google does."""

    def __init__(self, answer: str = "ok") -> None:
        self.answer = answer  # ok, wrong_state, refused, nothing, stray_first
        self.asked: dict[str, str] = {}
        self.exchanged: list[dict[str, str]] = []
        self.pages: list[str] = []
        self.threads: list[threading.Thread] = []

    def _get(self, url: str) -> None:
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                self.pages.append(r.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            self.pages.append(f"HTTP {exc.code}")
        except OSError:
            self.pages.append("closed")

    def open(self, url: str) -> bool:
        self.asked = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        back = self.asked["redirect_uri"]
        state = self.asked["state"]

        def land() -> None:
            if self.answer == "nothing":
                return
            if self.answer == "stray_first":
                self._get(back + "/favicon.ico")
                self._get(back + "/")
            query = {"state": state, "code": "one-time-code"}
            if self.answer == "wrong_state":
                query["state"] = "someone-elses"
            if self.answer == "refused":
                query = {"state": state, "error": "access_denied"}
            self._get(back + "/?" + urlencode(query))

        thread = threading.Thread(target=land)
        self.threads.append(thread)
        thread.start()
        return True

    def token(self, request: httpx.Request) -> httpx.Response:
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        self.exchanged.append(form)
        digest = hashlib.sha256(form.get("code_verifier", "").encode()).digest()
        challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
        if (
            form.get("code") != "one-time-code"
            or challenge != self.asked.get("code_challenge")
            or form.get("redirect_uri") != self.asked.get("redirect_uri")
            or form.get("client_id") != CLIENT_ID
        ):
            return httpx.Response(400, json={"error": "invalid_grant"})
        nonce = "not-ours" if self.answer == "wrong_nonce" else self.asked["nonce"]
        return httpx.Response(200, json={"id_token": an_id_token(nonce)})

    def join(self) -> None:
        for t in self.threads:
            t.join(5)


def sign_in_with(google: FakeGoogle, **kw: Any) -> list[str]:
    delivered: list[str] = []
    http = httpx.Client(transport=httpx.MockTransport(google.token))
    try:
        account_signin.run(
            CLIENT_ID, delivered.append, open_browser=google.open, http=http, timeout=5, **kw
        )
    finally:
        google.join()
        http.close()
    return delivered


# ------------------------------------------------------------------ Google's side of signing in
def test_the_sign_in_asks_only_who_they_are_and_checks_state_and_pkce():
    google = FakeGoogle()
    delivered = sign_in_with(google)
    assert google.asked["scope"] == "openid email profile"  # never mail, calendar or files
    assert google.asked["response_type"] == "code"
    assert google.asked["code_challenge_method"] == "S256" and len(google.asked["state"]) >= 24
    assert google.asked["redirect_uri"].startswith("http://127.0.0.1:")  # this PC only
    assert len(google.exchanged) == 1 and "client_secret" not in google.exchanged[0]
    assert len(google.exchanged[0]["code_verifier"]) >= 43
    assert delivered == [an_id_token(google.asked["nonce"])]
    assert "You're signed in to Pyxi" in google.pages[-1]
    assert "one-time-code" not in google.pages[-1]


def test_an_answer_with_someone_elses_state_is_refused():
    google = FakeGoogle("wrong_state")
    with pytest.raises(SignInError) as failed:
        sign_in_with(google)
    assert failed.value.why == "state"
    assert google.exchanged == []  # the code was never used
    assert "That didn't work" in google.pages[-1] and "signed in to Pyxi" not in google.pages[-1]


def test_a_token_that_answers_another_sign_in_is_refused():
    google = FakeGoogle("wrong_nonce")
    with pytest.raises(SignInError) as failed:
        sign_in_with(google)
    assert failed.value.why == "state"


def test_saying_no_on_googles_page_ends_it_plainly():
    google = FakeGoogle("refused")
    with pytest.raises(SignInError) as failed:
        sign_in_with(google)
    assert failed.value.why == "refused" and google.exchanged == []
    assert "Google said: access denied" in google.pages[-1]


def test_cancel_half_way_and_a_page_left_open_both_end():
    cancel = threading.Event()
    threading.Timer(0.3, cancel.set).start()
    with pytest.raises(SignInError) as failed:
        sign_in_with(FakeGoogle("nothing"), cancel=cancel)
    assert failed.value.why == "cancelled"
    http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    with pytest.raises(SignInError) as failed:
        account_signin.run(CLIENT_ID, lambda t: None, open_browser=lambda u: True, http=http, timeout=0.4)
    assert failed.value.why == "timeout"


def test_other_requests_on_the_port_are_not_the_answer():
    google = FakeGoogle("stray_first")
    assert len(sign_in_with(google)) == 1
    assert google.pages[:2] == ["HTTP 404", "HTTP 404"]


def test_the_closing_page_for_pyxi_is_not_the_google_apps_one():
    mine, apps = page(True, account=True).decode(), page(True).decode()
    assert "You're signed in to Pyxi" in mine and "can't read your mail" in mine
    assert "Pyxi is connected to Google" in apps and "signed in to Pyxi" not in apps
    assert "Press the sign-in button" in page(False, account=True).decode()


# ------------------------------------------------------------------ a pretend Pyxi server
class FakeServer:
    """The routes of the contract, answering what a test tells it to."""

    def __init__(self, google: FakeGoogle | None = None) -> None:
        self.google = google
        self.config: Any = {"sign_in": True, "google_client_id": CLIENT_ID, "invite_codes": True}
        self.account: Any = an_account()
        self.me_status = 200
        self.auth: tuple[int, Any] | None = None  # None: yes, here is your token
        self.billing: tuple[int, Any] = (200, {"url": "https://checkout.stripe.com/c/pay/test_1"})
        self.billing_at: dict[str, tuple[int, Any]] = {}  # one route's own answer
        self.down = False
        self.calls: list[tuple[str, str, Any, str]] = []
        self.id_tokens: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com" and self.google is not None:
            return self.google.token(request)
        if self.down:
            raise httpx.ConnectError("no internet", request=request)
        path = request.url.path
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, path, body, request.headers.get("authorization", "")))
        assert str(request.url).startswith(SERVER)
        if path == "/api/config":
            if self.config is None:  # a server from before accounts
                return httpx.Response(404, text="Not found")
            return httpx.Response(200, json=self.config)
        if path == "/api/auth/google":
            self.id_tokens.append(body["id_token"])
            if self.auth is not None:
                return httpx.Response(self.auth[0], json=self.auth[1])
            return httpx.Response(200, json={"token": TOKEN, "account": self.account})
        if path == "/api/auth/signout":
            return httpx.Response(200, json={"ok": True})
        if path == "/api/me":
            if self.me_status != 200:
                return httpx.Response(self.me_status, json={"error": {"code": "x", "message": "x"}})
            return httpx.Response(200, json={"left_today_usd": 0.4, "account": self.account})
        if path in ("/api/billing/checkout", "/api/billing/portal"):
            status, answer = self.billing_at.get(path, self.billing)
            return httpx.Response(status, json=answer)
        return httpx.Response(404)


@pytest.fixture
def world(tmp_path, monkeypatch):
    """An Account on an empty data folder, a pretend server and Google, and what it emitted."""
    for var in ("PYXI_BETA", "PYXI_ACCOUNT", "OPENROUTER_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("PYXI_OWN_KEY", "0")
    monkeypatch.setenv("PYXI_CLOUD_URL", SERVER)
    google = FakeGoogle()
    server = FakeServer(google)
    hub = Hub()
    rec = Recorder(hub)
    acct = Account(tmp_path, hub)
    acct.transport = httpx.MockTransport(server)
    acct.open_url = google.open
    acct.sign_in_timeout = 5
    acct.poll_s, acct.poll_for_s = 0.01, 2
    restarts: list[int] = []
    acct.restart = lambda: restarts.append(1)

    class World:
        pass

    w = World()
    w.account, w.server, w.google, w.rec, w.restarts, w.dir = acct, server, google, rec, restarts, tmp_path
    yield w
    google.join()


def signed_in(w: Any, **info: Any) -> None:
    """As after a sign-in and the restart: the token is saved and the brain runs on it."""
    from pyxi import secretstore

    secretstore.write_json(w.dir / cloud.FILE, {"url": SERVER, "token": TOKEN, "account": True})
    w.server.account = an_account(**info)


def cards(rec: Recorder) -> list[dict[str, Any]]:
    return rec.of("job_ready")


def never_a_token(w: Any) -> None:
    """No token of any kind in anything a window was sent."""
    sent = json.dumps(w.rec.events)
    assert "pyxi_" not in sent and TOKEN not in sent
    for id_token in w.server.id_tokens:
        assert id_token not in sent


# ------------------------------------------------------------------ what the first step offers
async def test_the_tour_opens_with_sign_in_when_the_server_has_it_on(world):
    class ABrain:
        account = world.account

        def warm_voice(self):
            pass

    tour = onboarding.Tour(world.dir, None, needs_code=lambda: True)
    await onboarding.handle(tour, ABrain(), world.account.hub, {"action": "start", "code": True})
    start = world.rec.of("onboarding")[-1]
    assert start["code"] is True and start["only"] is False
    assert (start["sign_in"], start["invite_codes"], start["offline"]) == (True, True, False)
    assert world.server.calls == [("GET", "/api/config", None, "")]  # asked without any token


@pytest.mark.parametrize(
    "config",
    [
        {"sign_in": False, "google_client_id": None, "invite_codes": True},
        {"sign_in": True, "google_client_id": None, "invite_codes": True},  # nothing to sign in with
        None,  # a server from before accounts has no such route
    ],
)
async def test_with_sign_in_off_the_invite_code_step_shows_as_before(world, config):
    world.server.config = config
    assert await onboarding.first_step(type("B", (), {"account": world.account})()) == {
        "sign_in": False,
        "invite_codes": True,
        "offline": False,
    }


async def test_no_internet_at_the_first_step_is_said_not_guessed(world):
    world.server.down = True
    step = await onboarding.first_step(type("B", (), {"account": world.account})())
    assert step == {"sign_in": False, "invite_codes": True, "offline": True}
    world.server.down = False  # "Try again"
    await world.account.on_message({"action": "config"})
    again = world.rec.of("account")[-1]
    assert again["what"] == "config" and again["ok"] is True and again["account"]["offered"] is True


async def test_someone_who_needs_no_code_is_never_asked_to_sign_in(world):
    class ABrain:
        account = world.account

        def warm_voice(self):
            pass

    tour = onboarding.Tour(world.dir, None, needs_code=lambda: False)
    await onboarding.handle(tour, ABrain(), world.account.hub, {"action": "start", "code": True})
    start = world.rec.of("onboarding")[-1]
    assert start["code"] is False and "sign_in" not in start
    assert world.server.calls == []


async def test_signing_back_in_is_only_the_sign_in_step_and_leaves_the_marker(world):
    class ABrain:
        account = world.account

        def warm_voice(self):
            pass

    tour = onboarding.Tour(world.dir, None, needs_code=lambda: True)
    assert tour.start() is True
    tour.finish("finished")
    before = onboarding.load(world.dir)
    await onboarding.handle(tour, ABrain(), world.account.hub, {"action": "signin"})
    start = world.rec.of("onboarding")[-1]
    assert start["only"] is True and start["code"] is True and start["sign_in"] is True
    tour.finish("skipped")
    assert onboarding.load(world.dir) == before


# ------------------------------------------------------------------ signing in and out
async def test_signing_in_saves_the_pyxi_token_and_keeps_no_google_token(world):
    google_apps = world.dir / "google.json"  # the saved Google apps connection
    google_apps.write_bytes(b"the apps connection, untouched")
    assert await world.account.sign_in() is True
    saved = cloud.saved(world.dir)
    assert saved == {"url": SERVER, "token": TOKEN, "account": True}
    assert world.server.id_tokens == [an_id_token(world.google.asked["nonce"])]
    assert world.google.asked["client_id"] == CLIENT_ID
    assert world.google.asked["scope"] == "openid email profile"
    assert google_apps.read_bytes() == b"the apps connection, untouched"
    # The ID token went to the server once and is in no file; the Pyxi token only in cloud.json.
    for path in world.dir.iterdir():
        text = path.read_bytes()
        assert world.server.id_tokens[0].encode() not in text
        if path.name != cloud.FILE:
            assert TOKEN.encode() not in text
    done = world.rec.of("account")[-1]
    assert (done["what"], done["ok"]) == ("sign_in", True)
    assert done["message"] == "Your 3-day trial has started. No card needed."
    assert done["account"]["signed_in"] is True and done["account"]["email"] == "sam@example.com"
    assert done["account"]["busy"] == "" and world.restarts == [1]
    never_a_token(world)
    assert cloud.apply(world.dir) and account.signed_in_here()  # the brain that starts again


async def test_a_sign_in_the_server_turns_down_saves_nothing(world):
    world.server.auth = (401, {"error": {"code": "bad_sign_in", "message": SENTENCES["bad_sign_in"]}})
    assert await world.account.sign_in() is False
    failed = world.rec.of("account")[-1]
    assert failed["ok"] is False and failed["message"] == "That Google sign-in didn't work. Try again."
    assert failed["retry"] is True and failed["account"]["signed_in"] is False
    assert cloud.saved(world.dir) is None and world.restarts == []
    assert "That didn't work" in world.google.pages[-1]


@pytest.mark.parametrize(
    ("answer", "said"),
    [
        ("wrong_state", "That Google sign-in didn't work. Try again."),
        ("refused", "That Google sign-in didn't work. Try again."),
    ],
)
async def test_a_sign_in_that_goes_wrong_says_so_in_plain_words(world, answer, said):
    world.google.answer = answer
    assert await world.account.sign_in() is False
    failed = world.rec.of("account")[-1]
    assert failed["message"] == said and failed["retry"] is True
    assert cloud.saved(world.dir) is None and world.server.id_tokens == []


async def test_cancelling_the_sign_in_half_way_says_nothing_and_frees_the_button(world):
    world.google.answer = "nothing"
    task = asyncio.create_task(world.account.sign_in())
    for _ in range(100):
        await asyncio.sleep(0.02)
        if world.account._cancel is not None:
            break
    assert world.rec.of("account")[-1]["account"]["busy"] == "sign_in"
    await world.account.on_message({"action": "cancel_sign_in"})
    assert await task is False
    ended = world.rec.of("account")[-1]
    assert ended["cancelled"] is True and ended["message"] == "" and ended["account"]["busy"] == ""
    assert cloud.saved(world.dir) is None


async def test_a_sign_in_page_left_open_times_out(world):
    world.google.answer = "nothing"
    world.account.sign_in_timeout = 0.3
    assert await world.account.sign_in() is False
    assert world.rec.of("account")[-1]["message"] == "The sign-in took too long. Try again."


async def test_no_internet_at_sign_in_is_plain_words_and_try_again(world):
    world.server.down = True
    assert await world.account.sign_in() is False
    failed = world.rec.of("account")[-1]
    assert failed["message"] == "I can't reach Pyxi right now. Check your internet and try again."
    assert failed["retry"] is True and world.google.asked == {}  # no browser was opened


async def test_signing_out_ends_the_token_and_keeps_everything_else(world, monkeypatch):
    signed_in(world)
    (world.dir / "profile.json").write_text('{"first_name": "Sam"}')
    (world.dir / "google.json").write_bytes(b"the apps connection")
    await world.account.sign_out()
    assert ("POST", "/api/auth/signout", None, f"Bearer {TOKEN}") in world.server.calls
    assert cloud.saved(world.dir) is None and world.restarts == [1]
    assert (world.dir / "profile.json").read_text() == '{"first_name": "Sam"}'
    assert (world.dir / "google.json").read_bytes() == b"the apps connection"
    out = world.rec.of("account")[-1]
    assert out["what"] == "sign_out" and out["account"]["signed_in"] is False
    assert out["account"]["ask_sign_in"] is True  # the sign-in step comes back by itself
    never_a_token(world)
    # Signed in again: it is not asked for any more.
    assert await world.account.sign_in() is True
    assert world.rec.of("account")[-1]["account"]["ask_sign_in"] is False


# ------------------------------------------------------------------ the gates
def model_error(status: int, code: str, message: str) -> Exception:
    """The way a refusal from the server reaches the brain: the model client's own words."""
    inner = Exception(f"Error code: {status} - {{'error': {{'code': '{code}', 'message': {message!r}}}}}")
    outer = RuntimeError(f"OpenRouter API error: {inner}")
    outer.__cause__ = inner
    return outer


ALLOWANCE_SAID = "You've used this month's allowance on your plan. It resets on 1 December, or you can move up a plan."
GATE_CASES = [
    ("trial_ended", 402, SENTENCES["trial_ended"], "choose_plan"),
    ("subscription_ended", 402, SENTENCES["subscription_ended"], "choose_plan"),
    ("payment_failed", 402, SENTENCES["payment_failed"], "update_card"),
    ("allowance_used", 429, ALLOWANCE_SAID, "see_plans"),
    # A trial whose allowance is used: the server's own sentence, and no month to wait for.
    ("allowance_used", 429, account.TRIAL_ALLOWANCE_USED, "choose_plan"),
    ("billing_off", 503, SENTENCES["billing_off"], None),
    # Whatever the server says for a gate is what is shown, word for word.
    ("trial_ended", 402, "Your trial is over. Pick a plan to keep going.", "choose_plan"),
]


def test_the_contracts_sentences_are_word_for_word():
    assert SENTENCES == {
        "trial_ended": "Your 3-day trial has ended. Choose a plan to keep going.",
        "subscription_ended": "Your Pyxi plan has ended. Choose a plan to carry on.",
        "payment_failed": "Your last payment didn't go through. Update your card to keep Pyxi working.",
        "allowance_used": "You've used this month's allowance on your plan. It resets on {date}, or you can move up a plan.",
        "billing_off": "Plans aren't open yet. Your trial keeps working for now.",
        "bad_sign_in": "That Google sign-in didn't work. Try again.",
    }
    # Only when the server's own sentence did not come through: the same words, built here.
    assert account.sentence("allowance_used", RESETS) == ALLOWANCE_SAID
    assert account.sentence("allowance_used", None, trial=True) == account.TRIAL_ALLOWANCE_USED
    assert gate_of(model_error(402, "trial_ended", "")) == ("trial_ended", "")
    for words in [*SENTENCES.values(), SIGNED_OUT, account.OFFLINE, account.TOO_SLOW]:
        assert "—" not in words and not any(w in words.lower() for w in ("token", "oauth", "webhook"))


@pytest.mark.parametrize(("code", "status", "said", "button"), GATE_CASES)
def test_each_gate_is_read_out_of_the_models_error(code, status, said, button):
    found = gate_of(model_error(status, code, said))
    assert found == (code, said)  # the server's own sentence, with its date
    assert describe_failure(model_error(status, code, said)) == said  # what a teammate keeps
    assert gate_of(said) is not None and gate_of(said)[0] == code  # and from that text alone


def test_a_signed_out_account_is_told_apart_from_an_invite_code(monkeypatch):
    refused = model_error(401, "unauthorized", "Sign in first.")
    assert gate_of(refused, account_device=True) == ("signed_out", "")
    assert gate_of(refused, account_device=False) is None  # an invite-code device: as before
    assert gate_of(RuntimeError("Google said 401: not allowed"), account_device=True) is None
    monkeypatch.setenv("PYXI_BETA", "1")
    monkeypatch.setenv("PYXI_ACCOUNT", "1")
    assert describe_failure(refused) == SIGNED_OUT
    monkeypatch.setenv("PYXI_ACCOUNT", "0")
    assert describe_failure(refused) == "The AI model turned the key down. Check the model settings, then try again."


def test_what_the_beta_already_said_is_not_a_gate():
    old = "OpenRouter API error: Error code: 429 - You've used today's Pyxi beta allowance. It resets at midnight UTC."
    assert gate_of(Exception(old), account_device=False) is None
    assert gate_of("Scout couldn't finish: the site took too long to respond.") is None


class Refusing:
    """A model whose every call the server turns down."""

    model = "refusing"

    def __init__(self, error: Exception) -> None:
        self.error = error

    async def complete(self, **kw: Any) -> Any:
        raise self.error


@pytest.mark.parametrize(("code", "status", "said", "button"), GATE_CASES)
async def test_a_turn_the_server_turns_down_shows_the_gates_card(settings, code, status, said, button):
    brain, rec = make_brain(settings, llm=Refusing(model_error(status, code, said)))
    await brain.turn("What does this setting do?")
    await settle(brain)
    spoken = " ".join(e["text"] for e in rec.of("say"))
    assert spoken == said and "hiccup" not in spoken and "Error code" not in spoken
    (card,) = cards(rec)
    assert card["body"] == said and card["ok"] is False
    trial = said == account.TRIAL_ALLOWANCE_USED
    assert card["title"] == ("Your trial's allowance is used" if trial else GATES[code][0])
    assert card["actions"] == ([button] if button else [])
    assert "Error code" not in json.dumps(rec.events) and "OpenRouter" not in json.dumps(rec.of("job_ready"))


async def test_a_turn_on_a_sign_in_that_ran_out_offers_sign_in(settings, monkeypatch):
    monkeypatch.setenv("PYXI_BETA", "1")
    monkeypatch.setenv("PYXI_ACCOUNT", "1")
    brain, rec = make_brain(settings, llm=Refusing(model_error(401, "unauthorized", "Sign in first.")))
    await brain.turn("What does this setting do?")
    await settle(brain)
    (card,) = cards(rec)
    assert card["body"] == SIGNED_OUT and card["actions"] == ["pyxi_sign_in"]
    assert " ".join(e["text"] for e in rec.of("say")) == SIGNED_OUT


async def test_any_other_failed_turn_is_said_as_before(settings):
    brain, rec = make_brain(settings, llm=Refusing(RuntimeError("OpenRouter API error: boom")))
    await brain.turn("What does this setting do?")
    await settle(brain)
    assert "hiccupped" in " ".join(e["text"] for e in rec.of("say")) and cards(rec) == []


@pytest.mark.parametrize(("code", "status", "said", "button"), GATE_CASES)
async def test_a_background_job_the_server_turns_down_shows_the_same_card(settings, code, status, said, button):
    brain, rec = make_brain(settings)
    job = Job(
        agent_id="scout",
        agent_name="Scout",
        role="browser",
        task="Find the opening hours of the library.",
        status="failed",
        summary=describe_failure(model_error(status, code, said)),
    )
    brain.jobs.jobs[job.id] = job
    await brain._job_done(job)
    await settle(brain)
    (card,) = cards(rec)
    assert card["job_id"] == f"account-{code}" and card["body"] == said
    assert card["actions"] == ([button] if button else [])
    assert "couldn't finish" not in json.dumps(rec.events)


async def test_a_job_that_failed_for_its_own_reasons_is_told_as_before(settings):
    brain, rec = make_brain(settings)
    job = Job(agent_id="scout", agent_name="Scout", role="browser", task="Find it.", status="failed", summary="That site wouldn't load.")
    brain.jobs.jobs[job.id] = job
    await brain._job_done(job)
    await settle(brain)
    (card,) = cards(rec)
    assert card["title"] == "Scout couldn't finish" and card["actions"] == []


# ------------------------------------------------------------------ the plan, the warnings
async def test_settings_gets_the_plan_and_allowance_and_no_token(world):
    signed_in(world, days_left=1)
    await world.account.on_message({"action": "get"})
    state = world.rec.of("account")[-1]["account"]
    assert state["signed_in"] is True and state["plan"] == "trial" and state["days_left"] == 1
    assert state["allowance"] == {"used_usd": 0.1, "limit_usd": 1.5, "resets_at": None}
    assert ("GET", "/api/me", None, f"Bearer {TOKEN}") in world.server.calls
    assert account.trial_line(world.account.info) == "3-day trial, 1 day left"
    assert cards(world.rec) == []
    never_a_token(world)


async def test_the_allowance_warning_is_shown_once_at_80_percent(world):
    signed_in(world, plan="pro", status="active", days_left=None, renews_at="2026-12-20T00:00:00Z",
              allowance={"used_usd": 6.0, "limit_usd": 9.0, "resets_at": RESETS})
    await world.account.refresh()
    assert cards(world.rec) == []  # 67 percent
    world.server.account["allowance"]["used_usd"] = 7.4
    await world.account.refresh()
    await world.account.refresh()
    (card,) = cards(world.rec)
    assert card["body"] == "You've used 80 percent of this month's allowance. It resets on 1 December."
    assert card["actions"] == ["see_plans"] and card["ok"] is True
    # Not again after a restart, in the same month; again the month after.
    later = Account(world.dir, world.account.hub)
    later.transport = world.account.transport
    await later.refresh()
    assert len(cards(world.rec)) == 1
    world.server.account["allowance"]["resets_at"] = "2027-01-01T00:00:00Z"
    await later.refresh()
    assert len(cards(world.rec)) == 2


async def test_the_last_day_of_the_trial_is_said_once(world):
    signed_in(world, days_left=1)
    await world.account.refresh()
    assert cards(world.rec) == []
    world.server.account["days_left"] = 0
    await world.account.refresh()
    await world.account.refresh()
    (card,) = cards(world.rec)
    assert card["title"] == "Your trial ends today" and card["actions"] == ["choose_plan"]


async def test_a_trial_that_has_ended_shows_its_card_once_a_run(world):
    signed_in(world, plan="none", status="ended", days_left=None)
    await world.account.refresh()
    await world.account.refresh()
    (card,) = cards(world.rec)
    assert card["body"] == SENTENCES["trial_ended"] and card["actions"] == ["choose_plan"]
    assert world.rec.of("account")[-1]["account"]["gate"] == "trial_ended"


async def test_the_plan_unlocks_after_checkout_without_a_restart(world):
    signed_in(world, plan="none", status="ended", days_left=None)
    await world.account.refresh()
    assert world.account.gate == "trial_ended"
    opened: list[str] = []
    world.account.open_url = opened.append
    await world.account.on_message({"action": "checkout", "plan": "pro", "yearly": True})
    assert ("POST", "/api/billing/checkout", {"plan": "pro", "yearly": True}, f"Bearer {TOKEN}") in world.server.calls
    assert opened == ["https://checkout.stripe.com/c/pay/test_1"]
    said = world.rec.of("account")[-1]
    assert (said["what"], said["ok"]) == ("checkout", True) and "Checkout is open" in said["message"]
    await asyncio.sleep(0.08)  # still paying: asked a few times, nothing said
    assert world.rec.of("notice") == []
    world.server.account = an_account(plan="pro", status="active", days_left=None, renews_at="2027-11-26T00:00:00Z",
                                      allowance={"used_usd": 0, "limit_usd": 9, "resets_at": RESETS})
    for _ in range(100):
        await asyncio.sleep(0.02)
        if world.rec.of("notice"):
            break
    assert world.rec.of("notice")[-1]["message"] == "You're on the Pro plan now. Pyxi is ready."
    unlocked = [e for e in world.rec.of("account") if e.get("what") == "plan"][-1]
    assert unlocked["account"]["plan"] == "pro" and unlocked["account"]["gate"] is None
    assert world.restarts == []
    asked = len(world.server.calls)
    await asyncio.sleep(0.1)
    assert len(world.server.calls) == asked  # it has stopped asking
    never_a_token(world)


async def test_coming_back_to_the_window_unlocks_the_plan_at_once(world):
    signed_in(world)
    await world.account.refresh()
    world.account.open_url = lambda url: True
    world.account.poll_s = 30  # the clock would not get there in this test
    await world.account.checkout("starter", False)
    world.server.account = an_account(plan="starter", status="active", days_left=None)
    await world.account.on_message({"action": "refresh"})  # the window got the focus
    assert world.rec.of("notice")[-1]["message"] == "You're on the Starter plan now. Pyxi is ready."
    await asyncio.sleep(0)
    assert world.account._watch is None or world.account._watch.done() or world.account._watch.cancelled()


async def test_checkout_before_plans_are_open_and_with_no_internet(world):
    signed_in(world)
    opened: list[str] = []
    world.account.open_url = opened.append
    world.server.billing = (503, {"error": {"code": "billing_off", "message": SENTENCES["billing_off"]}})
    await world.account.checkout("pro", False)
    off = world.rec.of("account")[-1]
    assert off["message"] == "Plans aren't open yet. Your trial keeps working for now."
    assert off["ok"] is False and not off.get("retry") and off["account"]["gate"] is None
    world.server.down = True
    await world.account.checkout("pro", False)
    offline = world.rec.of("account")[-1]
    assert offline["message"] == account.OFFLINE and offline["retry"] is True
    assert opened == [] and world.account.busy == ""
    await world.account.checkout("everything", False)  # not a plan: nothing is asked
    assert world.rec.of("account")[-1] is offline


async def test_the_subscription_page_and_having_no_plan_to_manage(world):
    signed_in(world, plan="pro", status="past_due", days_left=None, renews_at=RESETS)
    opened: list[str] = []
    world.account.open_url = opened.append
    world.server.billing = (200, {"url": "https://billing.stripe.com/p/session/test_2"})
    await world.account.on_message({"action": "portal"})
    assert opened == ["https://billing.stripe.com/p/session/test_2"]
    assert ("POST", "/api/billing/portal", None, f"Bearer {TOKEN}") in world.server.calls
    world.account._watch.cancel()
    world.server.billing = (409, {"error": {"code": "no_subscription", "message": "You don't have a plan yet. Choose one first."}})
    await world.account.portal()
    none = world.rec.of("account")[-1]
    assert none["pick"] is True and none["message"] == "You don't have a plan yet. Choose one first."
    world.server.billing = (200, {"url": "http://not-secure.example/pay"})
    await world.account.portal()
    assert len(opened) == 1  # only a secure address is ever opened


async def test_a_sign_in_that_ran_out_is_noticed_and_mended(world):
    signed_in(world)
    world.server.me_status = 401
    await world.account.refresh()
    (card,) = cards(world.rec)
    assert card["body"] == SIGNED_OUT and card["actions"] == ["pyxi_sign_in"]
    state = world.rec.of("account")[-1]["account"]
    assert state["gate"] == "signed_out" and state["signed_in"] is False
    world.server.me_status = 200
    assert await world.account.sign_in() is True
    assert world.rec.of("account")[-1]["account"]["gate"] is None


# ------------------------------------------------------------------ what else the server may say
def refusal(status: int, code: str, message: str) -> tuple[int, Any]:
    return status, {"error": {"code": code, "message": message}}


@pytest.mark.parametrize(
    ("status", "code", "said"),
    [
        (503, "sign_in_off", "Signing in with Google isn't open yet."),
        (429, "too_many_tries", "Too many tries. Wait a while, then try again."),
        (403, "account_closed", "This Pyxi account has been switched off."),
    ],
)
async def test_a_sign_in_the_server_will_not_take_says_its_own_words(world, status, code, said):
    world.server.auth = refusal(status, code, said)
    assert await world.account.sign_in() is False
    failed = world.rec.of("account")[-1]
    assert failed["message"] == said and not failed.get("retry")  # another try now would not help
    assert cloud.saved(world.dir) is None and world.restarts == []
    assert "That didn't work" in world.google.pages[-1]


async def test_choosing_a_plan_when_they_already_have_one_opens_its_own_page(world):
    signed_in(world, plan="pro", status="active", days_left=None, trial_ends_at=None, renews_at=RESETS)
    opened: list[str] = []
    world.account.open_url = opened.append
    world.server.billing_at["/api/billing/checkout"] = refusal(
        409, "already_subscribed", "You already have a Pyxi plan. Open your plan page to change it."
    )
    world.server.billing_at["/api/billing/portal"] = (200, {"url": "https://billing.stripe.com/p/session/test_3"})
    await world.account.checkout("max", False)
    assert opened == ["https://billing.stripe.com/p/session/test_3"]
    paths = [c[1] for c in world.server.calls if c[1].startswith("/api/billing")]
    assert paths == ["/api/billing/checkout", "/api/billing/portal"]
    assert world.rec.of("account")[-1]["what"] == "portal" and world.account.busy == ""
    world.account._watch.cancel()


@pytest.mark.parametrize(
    ("status", "code", "said", "retry"),
    [
        (502, "billing_trouble", "The payment page didn't open. Try again in a moment.", True),
        (400, "bad_plan", "Choose Starter, Pro or Max.", False),
        (429, "too_many_tries", "Too many tries. Wait a while, then try again.", False),
        (403, "account_closed", "This Pyxi account has been switched off.", False),
    ],
)
async def test_a_checkout_that_does_not_open_says_why_in_the_servers_words(world, status, code, said, retry):
    signed_in(world)
    opened: list[str] = []
    world.account.open_url = opened.append
    world.server.billing = refusal(status, code, said)
    await world.account.checkout("pro", False)
    failed = world.rec.of("account")[-1]
    assert failed["message"] == said and bool(failed.get("retry")) is retry
    assert opened == [] and world.account.busy == "" and cards(world.rec) == []


async def test_a_plan_button_on_an_invite_code_offers_the_google_sign_in(world):
    from pyxi import secretstore

    secretstore.write_json(world.dir / cloud.FILE, {"url": SERVER, "token": TOKEN})  # no account
    world.server.billing = refusal(403, "sign_in_needed", "Sign in with Google to choose a plan.")
    await world.account.checkout("pro", False)
    assert world.rec.of("account")[-1]["message"] == "Sign in with Google to choose a plan."
    (card,) = cards(world.rec)
    assert card["body"] == "Sign in with Google to choose a plan." and card["actions"] == ["pyxi_sign_in"]
    assert world.account.gate is None  # nothing is wrong with what they have


async def test_a_billing_button_after_the_sign_in_ran_out_offers_sign_in(world):
    signed_in(world)
    world.server.billing = refusal(401, "not_signed_in", "Sign in to Pyxi first.")
    await world.account.portal()
    (card,) = cards(world.rec)
    assert card["actions"] == ["pyxi_sign_in"] and world.account.gate == "signed_out"


async def test_a_gate_met_at_checkout_is_the_same_card(world):
    signed_in(world, plan="pro", status="past_due", days_left=None, trial_ends_at=None)
    world.server.billing = refusal(402, "payment_failed", SENTENCES["payment_failed"])
    await world.account.checkout("max", True)
    (card,) = cards(world.rec)
    assert card["body"] == SENTENCES["payment_failed"] and card["actions"] == ["update_card"]


async def test_a_plan_that_ended_is_told_apart_from_a_trial_that_ended(world):
    # The server drops the trial's end date once someone has paid.
    signed_in(world, plan="none", status="ended", days_left=None, trial_ends_at=None)
    await world.account.refresh()
    (card,) = cards(world.rec)
    assert card["body"] == SENTENCES["subscription_ended"] and card["actions"] == ["choose_plan"]


async def test_a_trial_out_of_allowance_gets_choose_a_plan(world):
    signed_in(world)
    await world.account.refresh()
    said = await world.account.gate_from(model_error(429, "allowance_used", account.TRIAL_ALLOWANCE_USED))
    assert said == "You've used your trial's allowance. Choose a plan to keep going."
    (card,) = cards(world.rec)
    assert card["actions"] == ["choose_plan"] and card["body"] == said
    # Without the server's sentence, the same words are built here from what is known.
    world.rec.events.clear()
    await world.account.gate_from(model_error(429, "allowance_used", ""))
    assert cards(world.rec)[0]["body"] == said


def test_the_sign_in_uses_the_shipped_desktop_clients_secret_only_for_that_client(monkeypatch):
    monkeypatch.delenv("PYXI_SIGNIN_CLIENT_SECRET", raising=False)

    class Shipped:
        def client_config(self):
            return {"installed": {"client_id": CLIENT_ID, "client_secret": "made-up-desktop-secret"}}

    class Nothing:
        def client_config(self):
            return None

    assert account.desktop_client_secret(Shipped(), CLIENT_ID) == "made-up-desktop-secret"
    assert account.desktop_client_secret(Shipped(), "another-client.apps.googleusercontent.com") == ""
    assert account.desktop_client_secret(Nothing(), CLIENT_ID) == ""
    google = FakeGoogle()
    delivered = sign_in_with(google, client_secret="made-up-desktop-secret")
    assert google.exchanged[0]["client_secret"] == "made-up-desktop-secret" and len(delivered) == 1


# ------------------------------------------------------------------ invite-code testers
async def test_an_invite_code_tester_sees_no_account_and_no_cards(world):
    from pyxi import secretstore

    secretstore.write_json(world.dir / cloud.FILE, {"url": SERVER, "token": TOKEN})
    world.server.account = None  # the contract: null for an invite-code device
    await world.account.on_message({"action": "get"})
    await world.account.refresh()
    state = world.rec.of("account")[-1]["account"]
    assert state["signed_in"] is False and state["plan"] is None and state["gate"] is None
    assert cards(world.rec) == [] and world.rec.of("notice") == []
    assert cloud.saved(world.dir) == {"url": SERVER, "token": TOKEN}  # their token is as it was
    world.server.me_status = 401
    await world.account.refresh()
    assert cards(world.rec) == []  # never "signed out": there was no sign-in
    await world.account.sign_out()
    assert cloud.saved(world.dir) is not None and world.restarts == []  # nothing to sign out of


def test_the_hello_carries_the_account_and_no_token(settings, monkeypatch):
    from fastapi.testclient import TestClient

    from pyxi import secretstore
    from pyxi.server import create_app

    monkeypatch.delenv("PYXI_BETA", raising=False)
    monkeypatch.setenv("PYXI_OWN_KEY", "0")
    brain, _ = make_brain(settings)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    secretstore.write_json(settings.data_dir / cloud.FILE, {"url": SERVER, "token": TOKEN, "account": True})
    app = create_app(settings, demo=True, brain=brain, key="k")
    with TestClient(app) as client, client.websocket_connect("/ws?key=k") as ws:
        raw = ws.receive_text()
        hello = json.loads(raw)
        assert hello["account"]["signed_in"] is True and hello["account"]["busy"] == ""
        assert "pyxi_" not in raw
        ws.send_text(json.dumps({"type": "account", "action": "get"}))  # no internet in tests
        for _ in range(40):
            event = json.loads(ws.receive_text())
            if event["type"] == "account":
                break
        assert event["account"]["signed_in"] is True and "pyxi_" not in json.dumps(event)
