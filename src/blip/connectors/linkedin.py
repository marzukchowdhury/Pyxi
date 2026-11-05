"""LinkedIn: post to your own profile with LinkedIn's official "Share on LinkedIn" API.

LinkedIn doesn't let apps read your feed or messages, so for anything beyond posting Blip asks
Scout to do it in a browser you can watch.

Setup (once): linkedin.com/developers > Create app > Products: add "Sign In with LinkedIn using
OpenID Connect" and "Share on LinkedIn". Auth tab: add the redirect URL
``http://localhost:8767/linkedin/callback``. Put ``LINKEDIN_CLIENT_ID`` and
``LINKEDIN_CLIENT_SECRET`` in ``blip/.env``, then ``blip connect linkedin``.
"""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from blip.connectors import Connector, Outgoing, Result, Status, Tool, tool

REDIRECT = "http://localhost:8767/linkedin/callback"
SCOPES = "openid profile w_member_social"


class LinkedInConnector(Connector):
    id = "linkedin"
    name = "LinkedIn"

    def __init__(self, store: Path) -> None:
        self.token_path = store / "linkedin.json"

    def _saved(self) -> dict[str, Any] | None:
        try:
            data = json.loads(self.token_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if data.get("expires_at", 0) > time.time() else None

    def connected(self) -> bool:
        return self._saved() is not None

    def status(self) -> Status:
        saved = self._saved()
        if saved:
            days = int((saved["expires_at"] - time.time()) // 86400)
            return Status(
                self.id,
                self.name,
                True,
                f"Posting as {saved.get('name', 'you')} (reconnect in {days} days)",
            )
        ready = bool(
            os.environ.get("LINKEDIN_CLIENT_ID") and os.environ.get("LINKEDIN_CLIENT_SECRET")
        )
        return Status(
            self.id,
            self.name,
            False,
            "Ready to sign in" if ready else "Needs a LinkedIn developer app",
            [
                "linkedin.com/developers > Create app (any company page you manage).",
                "Products: add 'Sign In with LinkedIn using OpenID Connect' and 'Share on LinkedIn'.",
                f"Auth: add the redirect URL {REDIRECT}",
                "Add LINKEDIN_CLIENT_ID and LINKEDIN_CLIENT_SECRET to blip/.env, then press Connect.",
            ],
        )

    def sign_in(self, timeout: float = 300) -> str:
        """Open LinkedIn's sign-in and catch the redirect on localhost (blocking)."""
        cid, secret = os.environ.get("LINKEDIN_CLIENT_ID"), os.environ.get("LINKEDIN_CLIENT_SECRET")
        if not (cid and secret):
            raise RuntimeError(
                "Add LINKEDIN_CLIENT_ID and LINKEDIN_CLIENT_SECRET to blip/.env first."
            )
        state = secrets.token_urlsafe(16)
        got: dict[str, str] = {}

        class Catch(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                q = parse_qs(urlparse(self.path).query)
                if q.get("state", [""])[0] == state:
                    got.update({k: v[0] for k, v in q.items()})
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(
                    b"<p style='font-family:sans-serif'>Blip is connected to LinkedIn. You can close this tab.</p>"
                )

            def log_message(self, *a: Any) -> None:
                pass

        server = HTTPServer(("localhost", 8767), Catch)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            webbrowser.open(
                "https://www.linkedin.com/oauth/v2/authorization?"
                + urlencode(
                    {
                        "response_type": "code",
                        "client_id": cid,
                        "redirect_uri": REDIRECT,
                        "state": state,
                        "scope": SCOPES,
                    }
                )
            )
            deadline = time.time() + timeout
            while "code" not in got and "error" not in got and time.time() < deadline:
                time.sleep(0.3)
        finally:
            server.shutdown()
        if "code" not in got:
            raise RuntimeError(got.get("error_description") or "LinkedIn sign-in didn't finish.")
        tok = httpx.post(
            "https://www.linkedin.com/oauth/v2/accessToken",
            data={
                "grant_type": "authorization_code",
                "code": got["code"],
                "redirect_uri": REDIRECT,
                "client_id": cid,
                "client_secret": secret,
            },
            timeout=20,
        ).json()
        if "access_token" not in tok:
            raise RuntimeError(f"LinkedIn didn't give a token: {tok}")
        me = httpx.get(
            "https://api.linkedin.com/v2/userinfo",
            headers={"Authorization": f"Bearer {tok['access_token']}"},
            timeout=20,
        ).json()
        data = {
            "access_token": tok["access_token"],
            "expires_at": time.time() + int(tok.get("expires_in", 0)),
            "sub": me.get("sub"),
            "name": me.get("name"),
        }
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        self.token_path.write_text(json.dumps(data), encoding="utf-8")
        return data.get("name") or "you"

    def disconnect(self) -> None:
        self.token_path.unlink(missing_ok=True)

    async def post(self, args: dict[str, Any]) -> Result:
        saved = self._saved()
        text = str(args.get("text", "")).strip()
        if not saved or not text:
            return "linkedin_post: nothing to post, or LinkedIn isn't connected."
        visibility = "CONNECTIONS" if args.get("connections_only") else "PUBLIC"

        async def publish() -> str:
            body = {
                "author": f"urn:li:person:{saved['sub']}",
                "lifecycleState": "PUBLISHED",
                "specificContent": {
                    "com.linkedin.ugc.ShareContent": {
                        "shareCommentary": {"text": text[:3000]},
                        "shareMediaCategory": "NONE",
                    }
                },
                "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": visibility},
            }
            async with httpx.AsyncClient(timeout=20) as client:
                r = await client.post(
                    "https://api.linkedin.com/v2/ugcPosts",
                    json=body,
                    headers={
                        "Authorization": f"Bearer {saved['access_token']}",
                        "X-Restli-Protocol-Version": "2.0.0",
                    },
                )
            if r.status_code >= 400:
                raise RuntimeError(f"LinkedIn said {r.status_code}: {r.text[:200]}")
            return "Posted on LinkedIn."

        return Outgoing(
            action=f"Post on LinkedIn ({visibility.lower()})", preview=text[:900], run=publish
        )

    def tools(self) -> list[Tool]:
        return [
            Tool(
                tool(
                    "linkedin_post",
                    "Post on the user's LinkedIn profile (they approve the exact text first). For reading LinkedIn, delegate to Scout.",
                    {"text": {"type": "string"}, "connections_only": {"type": "boolean"}},
                    ["text"],
                ),
                self.post,
            ),
        ]
