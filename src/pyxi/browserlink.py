"""Pyxi in your browser: the brain's side of the browser extension (``extension/``).

The extension keeps one WebSocket to the brain (``/ws/browser``). Through it the brain can read
a tab the user has open and click, type and scroll inside it, in their own signed-in browser,
without moving their mouse. This module is the bridge only: who may connect, what is asked of
the extension and what comes back. What Pyxi does with a tab is in ``companion/tabwork.py``.

Who may connect, checked in this order:

1. Loopback only. The brain listens on 127.0.0.1, and a connection from anywhere else is
   refused even if the brain is ever started on another address.
2. A browser extension only. The ``Origin`` header must be ``chrome-extension://`` (Chrome,
   Edge) or ``moz-extension://`` (Firefox). A web page can open a WebSocket to 127.0.0.1, but a
   browser never lets a page fake its Origin, so pages are refused here.
3. Paired with this PC's Pyxi, proven both ways. Any program on the PC can send whatever
   Origin it likes, and any program can listen on the brain's port when Pyxi isn't running, so
   neither side takes the other's word:

   - Pairing. The user asks Pyxi for a code (Settings, "Connect your browser": shown once,
     five minutes, five tries) and types it into the extension. The code never crosses the
     socket. The two sides do a key exchange (ECDH, P-256) and each proves, with a MAC keyed by
     the code (stretched with PBKDF2), that it saw the same exchange. The brain proves first,
     so an impostor on the port learns nothing from the extension. Out of the exchange comes
     the link key, which is never sent either.
   - Every later connection. The extension sends its id and a nonce; the brain answers with
     its own nonce and a MAC over both, keyed by the link key; only when that checks out does
     the extension answer with its MAC and start taking requests. No token is ever sent.
   - After that every message, both ways, carries a counter and a MAC under a key made for
     that connection, so nothing in between can add, change or replay one.

   The link key is kept in ``browser.json`` in the data folder, encrypted for this Windows user
   (``pyxi.secretstore``), and it only works from the extension that paired.

The launch key (``pyxi.brainkey``) is not used on this route: it changes at every launch and
an extension has no way to learn it. The pairing stands in for it, and it opens this route
only, never the app's own ``/ws`` or ``/api``.

Also kept here: the sites where the user chose "Always allow on this site" (``sites`` in the
same file), each for the same 30 days as other standing permissions (``pyxi.grants``).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import re
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

FILE = "browser.json"
PROTOCOL = 2
EXTENSION_ORIGINS = ("chrome-extension://", "moz-extension://")
LOOPBACK = frozenset({"127.0.0.1", "::1"})
CODE_TTL_S = 300.0
CODE_TRIES = 5
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I: it's read off a screen and typed
CODE_ROUNDS = 150_000  # PBKDF2 over the code: a recorded pairing can't be guessed at cheaply
HELLO_TIMEOUT_S = 10.0
MAX_BROWSERS = 6
_BROWSER_NAME = re.compile(r"^[A-Za-z][A-Za-z ]{0,23}$")
_NONCE = re.compile(r"^[A-Za-z0-9+/=_-]{16,128}$")
# A site as consent is kept: "shop.example.com", "localhost:3000", "[::1]:8080", and
# "http://..." in front for a page that isn't https (http and https don't share a yes).
_SITE = re.compile(
    r"^(http://)?(\[[0-9a-f:.]{2,45}\]|[a-z0-9_]([a-z0-9._-]{0,251}[a-z0-9_])?)(:\d{1,5})?$"
)


class BrowserError(Exception):
    """The extension couldn't do it (or isn't there). ``code`` says why in one word:
    ``not_connected``, ``no_access`` (the browser hasn't let the extension into that site),
    ``browser_page`` (a settings page, the web store), ``sensitive`` (a password, card or ID
    field), ``gone`` (the element or the page isn't the one that was read), ``stopped`` (Esc
    in the tab, or Stop everything), ``timeout``, ``failed``.

    ``unanswered`` is True when the request was sent and no answer came back (a timeout, a
    stop, the browser going away): it may have happened in the page all the same."""

    def __init__(self, code: str, message: str, host: str = "", unanswered: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.host = host
        self.unanswered = unanswered


def host_of(url: str) -> str:
    """The host a page is on, the way a browser writes it: ``mail.google.com``,
    ``localhost:3000``, ``[::1]:8080``. "" for anything that isn't http or https."""
    try:
        parts = urlsplit(url or "")
        port = parts.port
    except ValueError:
        return ""
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return ""
    host = parts.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    default = {"http": 80, "https": 443}[parts.scheme]
    return host if port in (None, default) else f"{host}:{port}"


def site_of(url: str) -> str:
    """The site a page is on, as consent is kept and as cards name it: the host, with
    ``http://`` in front when the page isn't https."""
    host = host_of(url)
    if not host:
        return ""
    return host if urlsplit(url).scheme == "https" else f"http://{host}"


# ---------------------------------------------------------------------- the handshake's sums
def _bytes(part: bytes | str) -> bytes:
    return part if isinstance(part, bytes) else str(part).encode("utf-8")


def mac(key: bytes, *parts: bytes | str) -> bytes:
    return hmac.new(key, b"".join(_bytes(p) for p in parts), hashlib.sha256).digest()


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def unb64(text: str) -> bytes:
    return base64.b64decode(str(text), validate=True)


def clean_code(code: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", str(code or "")).upper()


def transcript(origin: str, ne: str, nb: str, pub_e: str, pub_b: str) -> bytes:
    """Everything both sides saw of a pairing, as one hash."""
    return hashlib.sha256(f"pyxi-pair-v2|{origin}|{ne}|{nb}|{pub_e}|{pub_b}".encode()).digest()


def code_key(code: str, seen: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", clean_code(code).encode(), seen, CODE_ROUNDS, 32)


def pair_proof(who: str, key: bytes, seen: bytes, shared: bytes) -> bytes:
    """``who`` ("brain" or "ext") saw this exchange and knows the code."""
    return mac(key, who, seen, shared)


def link_key(shared: bytes, seen: bytes) -> bytes:
    return mac(shared, "pyxi-link-key", seen)


def hello_proof(who: str, key: bytes, browser_id: str, origin: str, ne: str, nb: str) -> bytes:
    return mac(key, f"{who}|{browser_id}|{origin}|{ne}|{nb}")


def session_key(key: bytes, ne: str, nb: str) -> bytes:
    return mac(key, f"session|{ne}|{nb}")


def seal(key: bytes, way: str, n: int, body: str) -> str:
    """One message on an authenticated link: its counter, its text and a MAC over both.
    ``way`` is "B" from the brain and "E" from the extension, so neither can be played back
    at its sender."""
    return json.dumps({"s": n, "b": body, "m": b64(mac(key, f"{way}|{n}|{body}"))})


def unseal(key: bytes, way: str, after: int, raw: str) -> tuple[int, str] | None:
    """(counter, text) of a sealed message, or None if it wasn't made with this key, was
    changed, or isn't newer than the last one."""
    try:
        frame = json.loads(raw)
        n, body, tag = int(frame["s"]), str(frame["b"]), unb64(frame["m"])
    except (ValueError, KeyError, TypeError):
        return None
    if n <= after or not hmac.compare_digest(tag, mac(key, f"{way}|{n}|{body}")):
        return None
    return n, body


def _protect(key: bytes) -> str:
    from pyxi import secretstore

    return b64(secretstore.protect(key))


def _unprotect(stored: str) -> bytes | None:
    from pyxi import secretstore

    try:
        return secretstore.unprotect(unb64(stored))
    except Exception:  # copied from another PC or user, or damaged: that browser pairs again
        return None


def _grant_days() -> float:
    from pyxi import grants

    return grants.days()


async def _nothing() -> None:
    return None


@dataclass
class _Conn:
    id: str  # the paired browser's id in browser.json
    browser: str
    send: Callable[[str], Awaitable[None]]
    close: Callable[[], Awaitable[None]] = _nothing
    since: float = field(default_factory=time.time)
    active: float = field(default_factory=time.time)  # last time the user was in this browser
    pending: dict[str, asyncio.Future[Any]] = field(default_factory=dict)


class _Channel:
    """The socket once both sides have proven themselves: every message sealed."""

    def __init__(self, socket: Any, key: bytes) -> None:
        self.socket, self.key = socket, key
        self.sent = 0
        self.seen = 0
        self._lock = asyncio.Lock()

    async def send(self, text: str) -> None:
        async with self._lock:  # counters must reach the other side in order
            self.sent += 1
            await self.socket.send_text(seal(self.key, "B", self.sent, text))

    def open(self, raw: str) -> str | None:
        got = unseal(self.key, "E", self.seen, raw)
        if got is None:
            return None
        self.seen, body = got
        return body

    async def close(self) -> None:
        try:
            await self.socket.close(code=1008)
        except Exception:
            pass


class BrowserLink:
    def __init__(self, data_dir: Path) -> None:
        self.path = Path(data_dir) / FILE
        self._conns: dict[str, _Conn] = {}
        self._code: tuple[str, float, int] | None = None  # (code, expires, pairings started)
        self._farewells: set[asyncio.Future[None]] = set()  # "you're unpaired" being sent
        # Told when something changes (connected, paired, a site allowed): Settings redraws.
        self.on_change: Callable[[], Awaitable[None]] | None = None
        # Told when the user stopped Pyxi in a tab (Esc, or the marker's Stop): (run id, how).
        self.on_stopped: list[Callable[[str, str], None]] = []

    # ------------------------------------------------------------------ the file
    def _load(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
        browsers = raw.get("browsers")
        sites = raw.get("sites")
        return {
            "browsers": browsers if isinstance(browsers, dict) else {},
            "sites": sites if isinstance(sites, dict) else {},
        }

    def _save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=1), encoding="utf-8")
        try:  # owner only (on Windows a user's AppData is already private to them)
            self.path.chmod(0o600)
        except OSError:
            pass

    async def _changed(self) -> None:
        if self.on_change is not None:
            try:
                await self.on_change()
            except Exception:
                log.exception("browser link: telling Settings failed")

    # ------------------------------------------------------------------ pairing
    def new_code(self, now: float | None = None) -> dict[str, Any]:
        """A fresh pairing code for the user to type into the extension. One at a time: asking
        again replaces the last one."""
        now = time.time() if now is None else now
        raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
        self._code = (raw, now + CODE_TTL_S, 0)
        return {"code": f"{raw[:4]}-{raw[4:]}", "expires_in": int(CODE_TTL_S)}

    def _begin_pairing(self, now: float | None = None) -> str | None:
        """The live code, for one pairing attempt. Each attempt counts as a try whether or not
        it gets anywhere: the brain's proof gives a guesser something to test guesses against,
        so only five of them are ever made per code."""
        now = time.time() if now is None else now
        if self._code is None:
            return None
        code, expires, tries = self._code
        if now > expires or tries >= CODE_TRIES:
            self._code = None
            return None
        self._code = (code, expires, tries + 1)
        return code

    def _remember(self, browser: str, origin: str, key: bytes) -> str:
        """A newly paired browser; returns its id. The same extension in the same browser
        pairing again replaces its old pairing, and the oldest goes when there are too many:
        either way that pairing's live connection goes with it."""
        data = self._load()
        browsers = data["browsers"]
        gone = [
            k
            for k, v in browsers.items()
            if v.get("origin") == origin and v.get("browser") == browser
        ]
        kept = [k for k in browsers if k not in gone]
        kept.sort(key=lambda k: browsers[k].get("seen", 0))
        gone += kept[: max(0, len(kept) - (MAX_BROWSERS - 1))]
        for old in gone:
            browsers.pop(old)
        bid = "br_" + secrets.token_hex(4)
        now = time.time()
        browsers[bid] = {
            "key": _protect(key),
            "browser": browser,
            "origin": origin,
            "paired": now,
            "seen": now,
        }
        self._save(data)
        self._cut(gone)
        return bid

    def _key_of(self, browser_id: str, origin: str) -> bytes | None:
        """The link key of a paired browser, if the connection comes from the extension that
        paired."""
        data = self._load()
        rec = data["browsers"].get(str(browser_id))
        if not isinstance(rec, dict):
            return None
        if rec.get("origin") != origin:
            log.warning("browser link: a paired browser's id came from another extension")
            return None
        key = _unprotect(str(rec.get("key") or ""))
        if key is None or len(key) != 32:
            return None
        rec["seen"] = time.time()
        self._save(data)
        return key

    def forget(self, browser_id: str = "") -> int:
        """Unpair one browser (or all of them): it is cut off at once."""
        data = self._load()
        gone = [b for b in data["browsers"] if not browser_id or b == browser_id]
        for bid in gone:
            data["browsers"].pop(bid)
        self._save(data)
        self._cut(gone)
        return len(gone)

    def _cut(self, browser_ids: list[str]) -> None:
        """Drop the live connection of each of these (no longer paired) browsers."""
        for bid in browser_ids:
            if conn := self._conns.pop(bid, None):
                self._fail(conn, "not_connected", "That browser was disconnected from Pyxi.")
                task = asyncio.ensure_future(self._goodbye(conn))
                self._farewells.add(task)
                task.add_done_callback(self._farewells.discard)

    async def _goodbye(self, conn: _Conn) -> None:
        try:
            await conn.send(json.dumps({"type": "unpaired"}))
            await conn.close()
        except Exception:
            pass

    # ------------------------------------------------------------------ sites Pyxi may act on
    def site_allowed(self, site: str, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        rec = self._load()["sites"].get(site)
        return bool(site) and isinstance(rec, dict) and float(rec.get("expires", 0)) > now

    def allow_site(self, site: str, now: float | None = None) -> None:
        if not _SITE.match(site or ""):
            raise ValueError(f"not a site: {site!r}")
        now = time.time() if now is None else now
        data = self._load()
        data["sites"][site] = {"granted": now, "expires": now + _grant_days() * 86400}
        self._save(data)

    def revoke_site(self, site: str) -> bool:
        data = self._load()
        gone = data["sites"].pop(site, None) is not None
        self._save(data)
        return gone

    def sites(self, now: float | None = None) -> list[dict[str, Any]]:
        now = time.time() if now is None else now
        data = self._load()
        live = {
            h: v
            for h, v in data["sites"].items()
            if isinstance(v, dict) and float(v.get("expires", 0)) > now
        }
        if live != data["sites"]:
            data["sites"] = live
            self._save(data)
        return [
            {"host": h, "granted": v.get("granted", 0), "expires": v["expires"]}
            for h, v in sorted(live.items())
        ]

    # ------------------------------------------------------------------ state
    @property
    def connected(self) -> bool:
        return bool(self._conns)

    def browsers(self) -> list[str]:
        return [c.browser for c in self._conns.values()]

    def public(self) -> dict[str, Any]:
        """For Settings: which browsers are paired and which are connected right now."""
        data = self._load()
        return {
            "connected": self.connected,
            "browsers": [
                {
                    "id": bid,
                    "browser": rec.get("browser", "Browser"),
                    "connected": bid in self._conns,
                    "paired": rec.get("paired", 0),
                }
                for bid, rec in sorted(
                    data["browsers"].items(), key=lambda kv: kv[1].get("paired", 0)
                )
            ],
            "sites": self.sites(),
        }

    # ------------------------------------------------------------------ the socket
    @staticmethod
    def may_connect(client_host: str | None, origin: str | None) -> str:
        """"" when this connection may go on to the pairing check, else why not."""
        if client_host not in LOOPBACK:
            return "not from this PC"
        if not origin or not origin.startswith(EXTENSION_ORIGINS):
            return "not a browser extension"
        return ""

    async def _next(self, socket: Any) -> dict[str, Any] | None:
        try:
            msg = json.loads(await asyncio.wait_for(socket.receive_text(), HELLO_TIMEOUT_S))
        except (TimeoutError, ValueError):
            return None
        return msg if isinstance(msg, dict) else None

    async def _pairing(
        self, socket: Any, first: dict[str, Any], origin: str, browser: str
    ) -> tuple[str, bytes] | None:
        """A pairing: (the new browser's id, the connection's key), or None after refusing."""
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

        ne, pub_e = str(first.get("nonce") or ""), str(first.get("pub") or "")
        code = self._begin_pairing()
        if code is None or not _NONCE.match(ne):
            await self._refuse(socket, "wrong_code")
            return None
        try:
            theirs = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), unb64(pub_e))
        except (ValueError, TypeError):
            await self._refuse(socket, "wrong_code")
            return None
        mine = ec.generate_private_key(ec.SECP256R1())
        shared = mine.exchange(ec.ECDH(), theirs)
        nb = b64(secrets.token_bytes(16))
        pub_b = b64(mine.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint))
        seen = transcript(origin, ne, nb, pub_e, pub_b)
        key = await asyncio.to_thread(code_key, code, seen)
        await socket.send_text(
            json.dumps(
                {
                    "type": "pair_challenge",
                    "nonce": nb,
                    "pub": pub_b,
                    "proof": b64(pair_proof("brain", key, seen, shared)),
                    "v": PROTOCOL,
                }
            )
        )
        answer = await self._next(socket)
        try:
            given = unb64((answer or {}).get("proof") or "")
        except (ValueError, TypeError):
            given = b""
        if (
            not answer
            or answer.get("type") != "pair_auth"
            or not hmac.compare_digest(given, pair_proof("ext", key, seen, shared))
        ):
            log.warning("browser link: a pairing that didn't know the code")
            await self._refuse(socket, "wrong_code")
            return None
        self._code = None  # a code works once
        link = link_key(shared, seen)
        return self._remember(browser, origin, link), session_key(link, ne, nb)

    async def _greeting(
        self, socket: Any, first: dict[str, Any], origin: str
    ) -> tuple[str, bytes] | None:
        """A paired browser coming back: (its id, the connection's key), or None after
        refusing. The brain proves itself first; nothing secret is asked of the extension."""
        bid, ne = str(first.get("id") or ""), str(first.get("nonce") or "")
        key = self._key_of(bid, origin) if _NONCE.match(ne) else None
        if key is None:
            await self._refuse(socket, "unknown")
            return None
        nb = b64(secrets.token_bytes(16))
        await socket.send_text(
            json.dumps(
                {
                    "type": "challenge",
                    "nonce": nb,
                    "proof": b64(hello_proof("brain", key, bid, origin, ne, nb)),
                    "v": PROTOCOL,
                }
            )
        )
        answer = await self._next(socket)
        try:
            given = unb64((answer or {}).get("proof") or "")
        except (ValueError, TypeError):
            given = b""
        if (
            not answer
            or answer.get("type") != "auth"
            or not hmac.compare_digest(given, hello_proof("ext", key, bid, origin, ne, nb))
        ):
            log.warning("browser link: a connection that didn't have the pairing's key")
            await self._refuse(socket, "unknown")
            return None
        return bid, session_key(key, ne, nb)

    async def serve(self, socket: Any) -> None:
        """One extension's connection, from the upgrade to the hang-up (the ``/ws/browser``
        route). ``socket`` is a Starlette WebSocket."""
        from starlette.websockets import WebSocketDisconnect

        origin = socket.headers.get("origin")
        client = socket.client.host if socket.client else None
        why = self.may_connect(client, origin)
        if why:
            log.warning("browser link: refused a connection (%s)", why)
            await socket.close(code=1008)
            return
        await socket.accept()
        conn: _Conn | None = None
        try:
            first = await self._next(socket)
            if first is None or first.get("type") not in ("hello", "pair"):
                await self._refuse(socket, "expected hello or pair")
                return
            if first.get("v") != PROTOCOL:  # an extension from before the two-way proof
                await self._refuse(socket, "old_extension")
                return
            name = str(first.get("browser") or "Browser").strip()
            browser = name if _BROWSER_NAME.match(name) else "Browser"
            pairing = first["type"] == "pair"
            proven = await (
                self._pairing(socket, first, origin, browser)
                if pairing
                else self._greeting(socket, first, origin)
            )
            if proven is None:
                return
            bid, key = proven
            channel = _Channel(socket, key)
            conn = _Conn(bid, browser, channel.send, channel.close)
            if old := self._conns.get(bid):  # the extension restarted: the new socket wins
                self._fail(old, "not_connected", "The browser reconnected.")
            self._conns[bid] = conn
            await conn.send(
                json.dumps(
                    {"type": "paired", "id": bid} if pairing else {"type": "welcome"}
                )
            )
            log.info("browser link: %s connected", browser)
            await self._changed()
            while True:
                body = channel.open(await socket.receive_text())
                if body is None:
                    log.warning("browser link: a message that wasn't sealed for this connection")
                    await channel.close()
                    break
                try:
                    msg = json.loads(body)
                except ValueError:
                    continue
                if isinstance(msg, dict):
                    await self._incoming(conn, msg)
        except WebSocketDisconnect:
            pass
        finally:
            if conn is not None and self._conns.get(conn.id) is conn:
                self._conns.pop(conn.id, None)
                self._fail(conn, "not_connected", "The browser went away.")
                log.info("browser link: %s disconnected", conn.browser)
                await self._changed()

    async def _refuse(self, socket: Any, reason: str) -> None:
        try:
            await socket.send_text(json.dumps({"type": "refused", "reason": reason}))
            await socket.close(code=1008)
        except Exception:
            pass

    async def _incoming(self, conn: _Conn, msg: dict[str, Any]) -> None:
        kind = msg.get("type")
        if kind == "ping":
            await conn.send('{"type":"pong"}')
        elif kind == "result":
            future = conn.pending.get(str(msg.get("id")))
            if future is not None and not future.done():
                future.set_result(msg)
        elif kind == "event":
            name = msg.get("name")
            if name == "focus":
                conn.active = time.time()
            elif name == "stopped":
                run, how = str(msg.get("run") or ""), str(msg.get("by") or "esc")
                log.info("browser link: stopped in the tab (%s)", how)
                for heard in list(self.on_stopped):
                    try:
                        heard(run, how)
                    except Exception:
                        log.exception("browser link: a stop listener failed")

    def _fail(self, conn: _Conn, code: str, message: str) -> None:
        for future in conn.pending.values():
            if not future.done():
                # Sent, and now nobody will hear the answer: it may still happen in the page.
                future.set_exception(BrowserError(code, message, unanswered=True))
                future.exception()  # seen: a caller that was cancelled meanwhile won't collect it
        conn.pending.clear()

    # ------------------------------------------------------------------ asking the extension
    def pick(self, prefer: str = "") -> str:
        """The browser to ask (its id): the one whose window is in front if it's connected,
        else the one the user was in most recently. "" when none is connected."""
        conns = list(self._conns.values())
        if not conns:
            return ""
        want = prefer.lower()
        for conn in conns:
            name = conn.browser.lower()
            if want and (name in want or (name == "edge" and "msedge" in want)):
                return conn.id
        return max(conns, key=lambda c: c.active).id

    async def call(
        self,
        name: str,
        args: dict[str, Any] | None = None,
        *,
        run: str = "",
        timeout: float = 20.0,
        prefer: str = "",
        browser: str = "",
    ) -> dict[str, Any]:
        """Ask the extension to do one thing; its answer, or :class:`BrowserError`. A task
        names the ``browser`` (from :meth:`pick`) it started in, so that every request of the
        task goes to the same one, whatever window the user moves to meanwhile."""
        conn = self._conns.get(browser or self.pick(prefer))
        if conn is None:
            raise BrowserError(
                "not_connected",
                "That browser isn't connected any more."
                if browser
                else "The browser extension isn't connected.",
            )
        cid = "c_" + secrets.token_hex(4)
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        conn.pending[cid] = future
        try:
            await conn.send(
                json.dumps({"type": "cmd", "id": cid, "name": name, "args": args or {}, "run": run})
            )
            reply = await asyncio.wait_for(future, timeout)
        except TimeoutError:
            raise BrowserError(
                "timeout", "The browser didn't answer in time.", unanswered=True
            ) from None
        finally:
            conn.pending.pop(cid, None)
        if reply.get("ok"):
            data = reply.get("data")
            return data if isinstance(data, dict) else {}
        raise BrowserError(
            str(reply.get("code") or "failed"),
            str(reply.get("error") or "That didn't work.")[:300],
            str(reply.get("host") or ""),
        )

    async def stop(self) -> int:
        """Stop everything the extension is doing for Pyxi, in every browser: commands in
        flight fail with ``stopped`` and the markers come off the tabs. Returns how many
        commands were cut short."""
        cut = 0
        for conn in list(self._conns.values()):
            cut += sum(1 for f in conn.pending.values() if not f.done())
            self._fail(conn, "stopped", "Stopped, as you asked.")
            try:
                await conn.send('{"type":"stop"}')
            except Exception as exc:
                log.warning("browser link: couldn't send stop (%s)", exc)
        return cut
