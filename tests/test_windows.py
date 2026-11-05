"""Window snapping: halves of the work area, with no gap between two snapped windows."""

import pytest

from wisp import windows


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
