"""Window snapping: halves of the work area, with no gap between two snapped windows."""

import pytest

from blip import windows


@pytest.fixture
def placed(monkeypatch):
    calls = []
    monkeypatch.setattr(windows, "work_area", lambda hwnd: (0, 0, 1921, 1032))
    monkeypatch.setattr(windows, "place", lambda hwnd, *rect: calls.append(rect))
    return calls


def test_left_and_right_halves_meet_exactly(placed):
    windows.snap(1, "left")
    windows.snap(2, "right")
    left, right = placed
    assert left == (0, 0, 960, 1032)
    assert right == (960, 0, 961, 1032)  # an odd pixel goes to the right half, no gap
    assert left[0] + left[2] == right[0]


def test_top_and_bottom(placed):
    windows.snap(1, "top")
    windows.snap(2, "bottom")
    top, bottom = placed
    assert top[1] + top[3] == bottom[1]
    assert top[3] + bottom[3] == 1032


def test_browser_flags_cover_the_common_browsers():
    for exe in ("firefox.exe", "chrome.exe", "msedge.exe", "brave.exe"):
        assert windows.BROWSER_FLAGS[exe].endswith("new-window")


def test_named_window_finds_the_app_the_request_names():
    from blip.windows import named_window

    open_now = [
        (11, "Blip overlay", r"C:\x\electron.exe"),
        (12, "Claude", r"C:\x\claude.exe"),
        (13, "*notes - Notepad", r"C:\Windows\notepad.exe"),
        (14, "Vercel - Google Chrome", r"C:\x\chrome.exe"),
        (15, "main.py - wisp - Visual Studio Code", r"C:\x\Code.exe"),
    ]
    assert named_window("Guide me to turn on word wrap in Notepad?", open_now) == 13
    assert named_window("walk me through this in google chrome", open_now) == 14
    assert named_window("show me how to use visual studio code", open_now) == 15
    # Ordinary words that happen to be program names don't count.
    assert named_window("explain this code to me", open_now) is None
    assert named_window("what's on my screen?", open_now) is None
