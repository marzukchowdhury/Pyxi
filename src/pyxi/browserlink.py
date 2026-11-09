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
3. Paired with this PC's Pyxi. Any program on the PC can send whatever Origin it likes, so the
   extension must also prove it was paired: the user asks Pyxi for a pairing code (Settings,
   "Connect your browser"), which is shown once, lasts five minutes and takes five wrong tries,
   and types it into the extension. The brain answers with a long random token that the
   extension keeps; only its hash is stored here (``browser.json`` in the data folder). Every
   later connection sends the token, and it only works from the extension that paired.

The launch key (``pyxi.brainkey``) is not used on this route: it changes at every launch and
an extension has no way to learn it. The pairing token stands in for it, and it opens this
route only, never the app's own ``/ws`` or ``/api``.

Also kept here: the sites where the user has let Pyxi act (``sites`` in the same file), each
for the same 30 days as other standing permissions (``pyxi.grants``).
"""

from __future__ import annotations

import asyncio
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
PROTOCOL = 1
EXTENSION_ORIGINS = ("chrome-extension://", "moz-extension://")
LOOPBACK = frozenset({"127.0.0.1", "::1"})
CODE_TTL_S = 300.0
CODE_TRIES = 5
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I: it's read off a screen and typed
HELLO_TIMEOUT_S = 10.0
MAX_BROWSERS = 6
_BROWSER_NAME = re.compile(r"^[A-Za-z][A-Za-z ]{0,23}$")
_HOST = re.compile(r"^[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?(:\d{1,5})?$")


class BrowserError(Exception):
    """The extension couldn't do it (or isn't there). ``code`` says why in one word:
    ``not_connected``, ``no_access`` (the browser hasn't let the extension into that site),
    ``browser_page`` (a settings page, the web store), ``sensitive`` (a password, card or ID
    field), ``gone`` (the element isn't on the page any more), ``stopped`` (Esc in the tab, or
    Stop everything), ``timeout``, ``failed``."""

    def __init__(self, code: str, message: str, host: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.host = host


def host_of(url: str) -> str:
    """The site a page is on, as consent is kept: ``mail.google.com``, ``localhost:3000``."""
    try:
        parts = urlsplit(url or "")
    except ValueError:
        return ""
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return ""
    host = parts.hostname.lower()
    default = {"http": 80, "https": 443}[parts.scheme]
    try:
        port = parts.port
    except ValueError:
        return ""
    return host if port in (None, default) else f"{host}:{port}"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _grant_days() -> float:
    from pyxi import grants

    return grants.days()


@dataclass
class _Conn:
    id: str  # the paired browser's id in browser.json
    browser: str
    send: Callable[[str], Awaitable[None]]
    since: float = field(default_factory=time.time)
    active: float = field(default_factory=time.time)  # last time the user was in this browser
    pending: dict[str, asyncio.Future[Any]] = field(default_factory=dict)


class BrowserLink:
    def __init__(self, data_dir: Path) -> None:
        self.path = Path(data_dir) / FILE
        self._conns: dict[str, _Conn] = {}
        self._code: tuple[str, float, int] | None = None  # (code, expires, wrong tries)
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

    def _take_code(self, given: str, now: float | None = None) -> bool:
        """True once for the live code; a wrong guess counts against it."""
        now = time.time() if now is None else now
        if self._code is None:
            return False
        code, expires, tries = self._code
        if now > expires:
            self._code = None
            return False
        cleaned = re.sub(r"[^A-Za-z0-9]", "", str(given or "")).upper()
        if hmac.compare_digest(cleaned, code):
            self._code = None  # one use
            return True
        tries += 1
        self._code = None if tries >= CODE_TRIES else (code, expires, tries)
        return False

    def _pair(self, browser: str, origin: str) -> tuple[str, str]:
        """Remember a newly paired browser; returns (its id, the token it keeps)."""
        token = secrets.token_urlsafe(32)
        data = self._load()
        browsers = data["browsers"]
        # The same extension pairing again replaces its old pairing.
        for old in [k for k, v in browsers.items() if v.get("origin") == origin]:
            browsers.pop(old)
        while len(browsers) >= MAX_BROWSERS:  # the oldest goes
            browsers.pop(min(browsers, key=lambda k: browsers[k].get("seen", 0)))
        bid = "br_" + secrets.token_hex(4)
        now = time.time()
        browsers[bid] = {
            "hash": _hash(token),
            "browser": browser,
            "origin": origin,
            "paired": now,
            "seen": now,
        }
        self._save(data)
        return bid, token

    def _known(self, token: str, origin: str) -> str | None:
        """The paired browser this token belongs to, if it comes from the extension that paired."""
        if not token:
            return None
        digest = _hash(str(token))
        data = self._load()
        for bid, rec in data["browsers"].items():
            if hmac.compare_digest(str(rec.get("hash", "")), digest):
                if rec.get("origin") != origin:
                    log.warning("browser link: a pairing token came from another extension")
                    return None
                rec["seen"] = time.time()
                self._save(data)
                return bid
        return None

    def forget(self, browser_id: str = "") -> int:
        """Unpair one browser (or all of them): its token stops working at once."""
        data = self._load()
        gone = [b for b in data["browsers"] if not browser_id or b == browser_id]
        for bid in gone:
            data["browsers"].pop(bid)
        self._save(data)
        for bid in gone:
            if conn := self._conns.pop(bid, None):
                self._fail(conn, "not_connected", "That browser was disconnected from Pyxi.")
                task = asyncio.ensure_future(self._goodbye(conn))
                self._farewells.add(task)
                task.add_done_callback(self._farewells.discard)
        return len(gone)

    async def _goodbye(self, conn: _Conn) -> None:
        try:
            await conn.send(json.dumps({"type": "refused", "reason": "unpaired"}))
        except Exception:
            pass

    # ------------------------------------------------------------------ sites Pyxi may act on
    def site_allowed(self, host: str, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        rec = self._load()["sites"].get(host)
        return bool(host) and isinstance(rec, dict) and float(rec.get("expires", 0)) > now

    def allow_site(self, host: str, now: float | None = None) -> None:
        if not _HOST.match(host or ""):
            raise ValueError(f"not a site: {host!r}")
        now = time.time() if now is None else now
        data = self._load()
        data["sites"][host] = {"granted": now, "expires": now + _grant_days() * 86400}
        self._save(data)

    def revoke_site(self, host: str) -> bool:
        data = self._load()
        gone = data["sites"].pop(host, None) is not None
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
            try:
                first = json.loads(
                    await asyncio.wait_for(socket.receive_text(), HELLO_TIMEOUT_S)
                )
            except (TimeoutError, ValueError):
                first = None
            if not isinstance(first, dict):
                await self._refuse(socket, "expected hello or pair")
                return
            name = str(first.get("browser") or "Browser").strip()
            browser = name if _BROWSER_NAME.match(name) else "Browser"
            token = ""
            if first.get("type") == "pair":
                if not self._take_code(str(first.get("code") or "")):
                    log.warning("browser link: a wrong or expired pairing code")
                    await self._refuse(socket, "wrong_code")
                    return
                bid, token = self._pair(browser, origin)
            elif first.get("type") == "hello":
                found = self._known(str(first.get("token") or ""), origin)
                if found is None:
                    await self._refuse(socket, "unpaired")
                    return
                bid = found
            else:
                await self._refuse(socket, "expected hello or pair")
                return
            conn = _Conn(bid, browser, socket.send_text)
            if old := self._conns.get(bid):  # the extension restarted: the new socket wins
                self._fail(old, "not_connected", "The browser reconnected.")
            self._conns[bid] = conn
            await socket.send_text(
                json.dumps(
                    {"type": "paired", "token": token, "v": PROTOCOL}
                    if token
                    else {"type": "welcome", "v": PROTOCOL}
                )
            )
            log.info("browser link: %s connected", browser)
            await self._changed()
            while True:
                try:
                    msg = json.loads(await socket.receive_text())
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
                future.set_exception(BrowserError(code, message))
                future.exception()  # seen: a caller that was cancelled meanwhile won't collect it
        conn.pending.clear()

    # ------------------------------------------------------------------ asking the extension
    def _pick(self, prefer: str = "") -> _Conn | None:
        """The browser to ask: the one whose window is in front if it's connected, else the one
        the user was in most recently."""
        conns = list(self._conns.values())
        if not conns:
            return None
        want = prefer.lower()
        for conn in conns:
            name = conn.browser.lower()
            if want and (name in want or (name == "edge" and "msedge" in want)):
                return conn
        return max(conns, key=lambda c: c.active)

    async def call(
        self,
        name: str,
        args: dict[str, Any] | None = None,
        *,
        run: str = "",
        timeout: float = 20.0,
        prefer: str = "",
    ) -> dict[str, Any]:
        """Ask the extension to do one thing; its answer, or :class:`BrowserError`."""
        conn = self._pick(prefer)
        if conn is None:
            raise BrowserError("not_connected", "The browser extension isn't connected.")
        cid = "c_" + secrets.token_hex(4)
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        conn.pending[cid] = future
        try:
            await conn.send(
                json.dumps({"type": "cmd", "id": cid, "name": name, "args": args or {}, "run": run})
            )
            reply = await asyncio.wait_for(future, timeout)
        except TimeoutError:
            raise BrowserError("timeout", "The browser didn't answer in time.") from None
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
