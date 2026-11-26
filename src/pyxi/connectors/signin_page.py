"""The page the browser lands on when a Google sign-in ends.

Google sends the browser back to a small server on this PC. The sign-in library answers with a
line of plain text on an empty page, which is the last thing a new user sees of connecting an
account, and it looked unfinished. This is Pyxi's own page instead: it says what happened and
what to do next, and it takes the one-time code out of the address bar.

Nothing is loaded from the internet: the page is one self-contained document.
"""

from __future__ import annotations

import html
import wsgiref.util
from collections.abc import Callable, Iterable
from typing import Any
from urllib.parse import parse_qs

_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<title>__TITLE__</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  html, body { height: 100%; margin: 0; }
  body {
    display: grid; place-items: center; padding: 24px;
    background: radial-gradient(1200px 600px at 50% -10%, #1b1e2b 0%, #0c0d12 60%);
    color: #eceef4; font: 16px/1.5 "Segoe UI", system-ui, -apple-system, sans-serif;
  }
  main {
    width: min(440px, 100%); padding: 36px 32px 30px; text-align: center;
    background: rgba(255, 255, 255, 0.04); border: 1px solid rgba(255, 255, 255, 0.09);
    border-radius: 20px; box-shadow: 0 24px 60px rgba(0, 0, 0, 0.45);
  }
  .orb {
    width: 54px; height: 54px; margin: 0 auto 22px; border-radius: 50%;
    background: radial-gradient(circle at 35% 30%, #fff3c4, __ORB__ 55%, __ORB_DARK__);
    box-shadow: 0 0 34px __GLOW__;
  }
  h1 { margin: 0 0 10px; font-size: 22px; font-weight: 600; letter-spacing: -0.01em; }
  p { margin: 0 0 8px; color: #b9bdcb; }
  .who { color: #eceef4; }
  ul { margin: 18px 0 0; padding: 14px 18px; list-style: none; text-align: left;
       background: rgba(255, 255, 255, 0.04); border-radius: 12px; color: #b9bdcb; font-size: 14px; }
  li + li { margin-top: 6px; }
  small { display: block; margin-top: 20px; color: #7d8296; font-size: 13px; }
</style>
</head>
<body>
<main>
  <div class="orb" aria-hidden="true"></div>
  <h1>__HEADING__</h1>
  __BODY__
  <small>__FOOT__</small>
</main>
<script>
  // The address holds a one-time code from Google. It has been used; take it off the screen.
  try { history.replaceState(null, "", "/"); } catch (e) {}
</script>
</body>
</html>
"""

_DONE = (
    "<p>You can close this tab and go back to Pyxi.</p>"
    "<ul>"
    "<li>Ask again for what you wanted. Pyxi can pick it up now.</li>"
    "<li>Reading is instant. Sending, posting and invites always show you a card to approve first.</li>"
    "<li>You can disconnect at any time in Pyxi's Settings, under Connected accounts.</li>"
    "</ul>"
)
_REFUSED = (
    "<p>Google didn't finish the sign-in, so nothing has changed.</p>"
    "<ul>"
    "<li>Close this tab and go back to Pyxi.</li>"
    "<li>Open Settings, find Google under Connected accounts and press the button to try again.</li>"
    "<li>When Google lists what Pyxi may use, leave the boxes ticked for the apps you want help with.</li>"
    "</ul>"
)


def page(ok: bool, reason: str = "") -> bytes:
    """The closing page: connected, or not (with Google's own short reason when it gave one)."""
    fill = {
        "__TITLE__": "Pyxi is connected to Google" if ok else "Pyxi couldn't connect to Google",
        "__HEADING__": "Pyxi is connected to Google" if ok else "That didn't connect",
        "__BODY__": _DONE if ok else _REFUSED,
        "__FOOT__": (
            "Pyxi never sees your Google password."
            if ok
            else (f"Google said: {html.escape(reason[:80])}" if reason else "Nothing was saved.")
        ),
        "__ORB__": "#f5b942" if ok else "#8c93a8",
        "__ORB_DARK__": "#b9731a" if ok else "#4b5063",
        "__GLOW__": "rgba(245, 185, 66, 0.45)" if ok else "rgba(140, 147, 168, 0.25)",
    }
    out = _PAGE
    for key, value in fill.items():
        out = out.replace(key, value)
    return out.encode("utf-8")


class ClosingPage:
    """Stands in for the sign-in library's own redirect app (same contract: it is called once
    with the request Google sent the browser back with, and keeps that request's address)."""

    def __init__(self, success_message: str = "") -> None:
        self.last_request_uri: str | None = None

    def __call__(
        self, environ: dict[str, Any], start_response: Callable[..., Any]
    ) -> Iterable[bytes]:
        self.last_request_uri = wsgiref.util.request_uri(environ)
        query = parse_qs(str(environ.get("QUERY_STRING") or ""))
        reason = (query.get("error") or [""])[0]
        ok = not reason and bool(query.get("code"))
        start_response(
            "200 OK",
            [
                ("Content-type", "text/html; charset=utf-8"),
                ("Cache-Control", "no-store"),
                ("Referrer-Policy", "no-referrer"),
                ("X-Content-Type-Options", "nosniff"),
            ],
        )
        return [page(ok, reason.replace("_", " "))]
