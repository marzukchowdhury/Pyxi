"""Pyxi's hands on a Mac: Quartz events, Windows shortcuts pressed the Mac way, and the guards."""

from __future__ import annotations

import sys

import pytest

from macfakes import El, FakeMac, install
from pyxi import hands as shared
from pyxi.macos import hands as mac_hands


@pytest.fixture
def mac(monkeypatch) -> FakeMac:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(mac_hands.time, "sleep", lambda _s: None)
    monkeypatch.setenv("PYXI_APP_PID", "303")
    m = install(monkeypatch)
    m.add_app(101, "TextEdit")
    m.add_app(303, "Pyxi")
    m.add_window(10, 101, "Untitled", 0, 25, 800, 600)
    m.frontmost = 101
    m.cursor = (10, 30)
    m.at_point = El(101, "AXTextArea")
    return m


@pytest.mark.parametrize(
    ("windows", "mac"),
    [
        ("ctrl+c", "cmd+c"),
        ("ctrl+shift+s", "cmd+shift+s"),
        ("ctrl+tab", "control+tab"),  # browsers keep tab switching on Control
        ("ctrl+shift+tab", "control+shift+tab"),
        ("control+c", "control+c"),  # spelled out: really Control (Terminal's interrupt)
        ("alt+tab", "cmd+tab"),
        ("alt+f4", "cmd+q"),
        ("ctrl+f4", "cmd+w"),
        ("alt+left", "cmd+["),
        ("win", "cmd"),
        ("cmd+space", "cmd+space"),
        ("alt+e", "option+e"),
        ("ctrl+win+c", "cmd+c"),
        ("Ctrl + L", "cmd+l"),
    ],
)
def test_windows_shortcuts_become_mac_shortcuts(windows, mac):
    assert mac_hands.mac_combo(windows) == mac


def test_parse_keys_uses_mac_key_codes():
    assert mac_hands.parse_keys("ctrl+s") == [0x37, 0x01]  # Cmd, S
    assert mac_hands.parse_keys("enter") == [0x24]
    assert mac_hands.parse_keys("backspace") == [0x33] and mac_hands.parse_keys("delete") == [0x75]
    assert mac_hands.parse_keys("playpause") == [mac_hands.MEDIA + 16]
    with pytest.raises(ValueError):
        mac_hands.parse_keys("menu")
    with pytest.raises(ValueError):
        mac_hands.parse_keys("")


def test_press_holds_modifiers_with_their_flags(mac):
    mac_hands.Hands().press("ctrl+shift+s")
    keys = [(e["code"], e["down"], e["flags"]) for e in mac.posted]
    cmd, shift = 0x100000, 0x20000
    assert keys == [
        (0x37, True, cmd),
        (0x38, True, cmd | shift),
        (0x01, True, cmd | shift),
        (0x01, False, cmd | shift),
        (0x38, False, cmd),
        (0x37, False, 0),
    ]
    assert shared.recently_acted()


def test_media_keys_are_system_defined_events(mac):
    mac_hands.Hands().press("next")
    assert [e["type"] for e in mac.posted] == ["media", "media"]
    assert mac.posted[0]["data1"] == (17 << 16) | (0xA << 8)  # NX_KEYTYPE_NEXT, key down
    assert mac.posted[1]["data1"] == (17 << 16) | (0xB << 8)


def test_type_sends_each_character_and_returns_for_newlines(mac):
    mac_hands.Hands().type("hé\n")
    texts = [e["text"] for e in mac.posted if e["type"] == "key" and e["text"]]
    assert texts == ["h", "h", "é", "é"]
    assert [e["code"] for e in mac.posted if e["type"] == "key" and not e["text"]] == [0x24, 0x24]


def test_click_glides_then_clicks_with_a_click_count(mac):
    mac_hands.Hands(glide_s=0.05).click(400, 300, count=2)
    moves = [e for e in mac.posted if e["type"] == "kCGEventMouseMoved"]
    assert moves and (moves[-1]["x"], moves[-1]["y"]) == (400, 300)
    clicks = [(e["type"], e["fields"]["clickState"]) for e in mac.posted if "Mouse" in e["type"] and "Moved" not in e["type"]]
    assert clicks == [
        ("kCGEventLeftMouseDown", 1),
        ("kCGEventLeftMouseUp", 1),
        ("kCGEventLeftMouseDown", 2),
        ("kCGEventLeftMouseUp", 2),
    ]


def test_drag_moves_with_the_button_held(mac):
    mac_hands.Hands(glide_s=0.05).drag(100, 100, 300, 120)
    kinds = [e["type"] for e in mac.posted]
    down, up = kinds.index("kCGEventLeftMouseDown"), kinds.index("kCGEventLeftMouseUp")
    assert down < up and set(kinds[down + 1 : up]) == {"kCGEventLeftMouseDragged"}


def test_scroll_down_is_negative_lines(mac):
    mac_hands.Hands(glide_s=0.02).scroll(200, 200, "down", notches=2)
    scrolls = [e for e in mac.posted if e["type"] == "scroll"]
    assert [(e["dy"], e["dx"]) for e in scrolls] == [(-3, 0), (-3, 0)]


def test_never_clicks_or_types_into_pyxi(mac):
    h = mac_hands.Hands(glide_s=0.02)
    mac.at_point = El(303, "AXButton", AXTitle="Allow")  # a Pyxi card under the mouse
    with pytest.raises(shared.OwnWindow):
        h.click(200, 200)
    assert not [e for e in mac.posted if e["type"] == "kCGEventLeftMouseDown"]
    mac.frontmost = 303  # the notch's text box has the keyboard
    with pytest.raises(shared.OwnWindow):
        h.press("enter")
    with pytest.raises(shared.OwnWindow):
        h.type("hi")


def test_the_user_grabbing_the_mouse_or_holding_esc_stops_it(mac):
    h = mac_hands.Hands(glide_s=0.02)
    h.move(100, 100)
    mac.cursor = (700, 500)  # they moved it
    assert h.interrupted() and "mouse at" in h.reason
    with pytest.raises(shared.Interrupted):
        h.check()
    h2 = mac_hands.Hands()
    mac.esc_down = True
    assert h2.interrupted() and h2.reason == "esc"
    assert not h2.wait_idle(timeout_s=0.01)


def test_idle_and_wait_idle(mac):
    mac.idle_s = 5.0
    h = mac_hands.Hands()
    assert h.idle_seconds() == 5.0
    assert h.wait_idle(quiet_s=3.0, timeout_s=0.5)


def test_is_own_and_window_at(mac):
    mac.add_window(20, 303, "Pyxi", 900, 25, 300, 400)
    assert mac_hands.is_own(20) and not mac_hands.is_own(10) and not mac_hands.is_own(0)
    assert mac_hands.window_at(950, 100) == 20
    assert mac_hands.foreground_window() == 10
