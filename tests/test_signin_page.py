"""The page a Google sign-in ends on is Pyxi's own, through the real sign-in library's server."""

import threading
import urllib.request
import wsgiref.simple_server

import google_auth_oauthlib.flow as oauth_flow

from pyxi.connectors.signin_page import ClosingPage, page


def _get(query: str) -> tuple[str, dict[str, str], ClosingPage]:
    app = ClosingPage("ignored")
    server = wsgiref.simple_server.make_server(
        "127.0.0.1", 0, app, handler_class=oauth_flow._WSGIRequestHandler
    )
    thread = threading.Thread(target=server.handle_request)
    thread.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/?{query}") as r:
            body, headers = r.read().decode("utf-8"), dict(r.headers)
    finally:
        thread.join(5)
        server.server_close()
    return body, headers, app


def test_connected_page_is_html_and_keeps_the_request_for_the_library():
    body, headers, app = _get("state=abc&code=4/xyz")
    assert headers["Content-type"].startswith("text/html")
    assert headers["Cache-Control"] == "no-store"
    assert "Pyxi is connected to Google" in body and "close this tab" in body
    assert "history.replaceState" in body  # the one-time code leaves the address bar
    assert app.last_request_uri and "code=4/xyz" in app.last_request_uri
    assert "4/xyz" not in body


def test_refused_sign_in_says_so_and_what_to_do():
    body, _, app = _get("error=access_denied&state=abc")
    assert "That didn't connect" in body and "try again" in body
    assert "access denied" in body
    assert "error=access_denied" in app.last_request_uri


def test_the_reason_is_escaped_and_the_copy_is_plain():
    out = page(False, "<script>alert(1)</script>").decode("utf-8")
    assert "<script>alert" not in out
    for text in (page(True).decode("utf-8"), out):
        assert "—" not in text and "http://" not in text and "https://" not in text


def test_the_library_still_has_the_part_that_is_swapped():
    assert hasattr(oauth_flow, "_RedirectWSGIApp")
