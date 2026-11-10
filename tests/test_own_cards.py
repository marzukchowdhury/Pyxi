"""Pyxi never approves its own cards: its hands refuse Pyxi's windows, and an answer that
arrives while its own mouse or keyboard is moving doesn't count.

The real windows are checked in desktop/e2e/security.mjs (the panel and a clickable card are
refused; ordinary apps under the click-through overlay are not)."""

from __future__ import annotations

import json
import sys

import pytest
from fastapi.testclient import TestClient

from pyxi import hands

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows input")


@pytest.fixture
def h(monkeypatch):
    """Real Hands that send nothing: inputs are recorded, the glide is skipped."""
    hh = hands.Hands(glide_s=0)
    hh.sent = []
    monkeypatch.setattr(hh, "_send", lambda *inputs: hh.sent.extend(inputs))
    monkeypatch.setattr(hh, "move", lambda x, y: None)
    monkeypatch.setattr(hh, "check", lambda: None)
    return hh


def test_a_click_on_pyxis_own_window_is_refused_before_any_button_goes_down(h, monkeypatch):
    monkeypatch.setattr(hands, "window_at", lambda x, y: 4242)
    monkeypatch.setattr(hands, "is_own", lambda hwnd: hwnd == 4242)
    for act in (lambda: h.click(100, 100), lambda: h.drag(0, 0, 100, 100), lambda: h.scroll(5, 5)):
        with pytest.raises(hands.OwnWindow, match="never clicks or types there"):
            act()
    assert h.sent == []
    monkeypatch.setattr(hands, "is_own", lambda hwnd: False)
    h.click(100, 100)
    assert len(h.sent) == 2  # down and up, on someone else's window


def test_no_keys_while_pyxi_has_the_keyboard(h, monkeypatch):
    monkeypatch.setattr(hands, "foreground_window", lambda: 7)
    monkeypatch.setattr(hands, "is_own", lambda hwnd: hwnd == 7)
    with pytest.raises(hands.OwnWindow):
        h.press("enter")  # Enter on a focused Allow button would approve it
    with pytest.raises(hands.OwnWindow):
        h.type("yes")
    assert h.sent == []


def test_own_windows_are_the_apps_process_or_pyxi_exe(monkeypatch):
    monkeypatch.setenv("PYXI_APP_PID", "31337")
    assert 31337 in hands.own_pids()
    monkeypatch.setattr(hands, "_pid_of", lambda hwnd: {1: 31337, 2: 555, 3: 556}[hwnd])
    monkeypatch.setattr(hands, "_exe_of", lambda pid: "pyxi.exe" if pid == 556 else "notepad.exe")
    assert hands.is_own(1) and not hands.is_own(2) and hands.is_own(3) and not hands.is_own(0)


def test_input_pyxi_sends_is_timed(h, monkeypatch):
    monkeypatch.setattr(hands, "_last_input", 0.0)
    assert not hands.recently_acted()
    hands.Hands._send(h, *[])  # the real _send: marks the time even for an empty batch
    assert hands.recently_acted() and not hands.recently_acted(within_s=0)


def test_a_card_answer_during_pyxis_own_input_is_ignored(settings, monkeypatch):
    from conftest import FakeScreen
    from pyxi.companion.brain import Brain
    from pyxi.demo import DemoLLM
    from pyxi.hub import Hub
    from pyxi.server import create_app
    from pyxi.voice import make_tts

    hub = Hub()
    brain = Brain(settings, hub, llm=DemoLLM(), screen=FakeScreen(), tts=make_tts(settings.voice, settings.models_dir))
    resolved: list[tuple[str, object]] = []
    hub.resolve = lambda rid, v: resolved.append((rid, v)) or True  # type: ignore[method-assign]
    app = create_app(settings, demo=True, brain=brain, key="k")
    with TestClient(app) as client, client.websocket_connect("/ws?key=k") as ws:
        ws.receive_text()  # hello
        monkeypatch.setattr(hands, "recently_acted", lambda within_s=1.5: True)
        ws.send_text(json.dumps({"type": "answer", "request_id": "r1", "value": True}))
        note = json.loads(ws.receive_text())
        assert note["type"] == "error" and "didn't count" in note["message"]
        monkeypatch.setattr(hands, "recently_acted", lambda within_s=1.5: False)
        ws.send_text(json.dumps({"type": "answer", "request_id": "r1", "value": True}))
        ws.send_text(json.dumps({"type": "routines"}))
        assert json.loads(ws.receive_text())["type"] == "routines"
    assert resolved == [("r1", True)]


def test_a_drag_pulls_first_and_waits_over_the_drop_spot(h, monkeypatch):
    # Firefox only picks a tab up once the pointer moves a few pixels with the button held,
    # and another window only takes the drop once it has seen the pointer arrive.
    monkeypatch.setattr(hands, "window_at", lambda x, y: 1)
    monkeypatch.setattr(hands, "is_own", lambda hwnd: False)
    monkeypatch.setattr(hands.time, "sleep", lambda s: None)
    h.drag(100, 50, 900, 60)
    flags = [i.u.mi.dwFlags for i in h.sent]
    down = flags.index(hands.MOUSEEVENTF_LEFTDOWN)
    up = flags.index(hands.MOUSEEVENTF_LEFTUP)
    moves = [f for f in flags[down + 1 : up] if f & hands.MOUSEEVENTF_MOVE]
    assert down < up and len(moves) >= 6  # a pull of three, then three over the drop spot


def test_looking_up_a_window_leaves_the_shared_user32_alone():
    """hands.window_at set argument types on ctypes.windll.user32, which every module in the
    process shares. uiautomation calls the same GetAncestor with a c_int, so after the first
    click its call threw and Pyxi could no longer read the screen's text."""
    import ctypes

    import uiautomation as auto

    shared = ctypes.windll.user32
    before = shared.GetAncestor.argtypes
    hands.window_at(10, 10)
    assert shared.GetAncestor.argtypes == before
    desktop = shared.GetDesktopWindow()
    auto.GetAncestor(desktop, 2)  # threw "argument 2: TypeError: 'c_long' object..."
